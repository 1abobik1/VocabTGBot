"""Ядро бота: входящие апдейты Telegram, маршрутизация кнопок и команд, крон, отправка сообщений.

Логика фич живёт в соседних модулях (карточки, словарь, генерация, практика, упражнения);
здесь только кто кого вызывает.
"""

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone

from .. import cards, generator
from .. import words as w
from ..keys import ALLOWED_USERS_KEY, session_key, settings_key, state_key
from ..storage import Repo, find_user, normalize_username


@dataclass
class Ctx:
    """Кто и откуда прислал апдейт."""

    username: str
    chat_id: int
    user: dict
    users: list
    message_id: int | None = None
    query_id: str | None = None


# Команды и кнопки главного меню -> обработчик(bot, ctx, text).
_MESSAGE_ROUTES = {
    ("/start", "/help", cards.HELP_BUTTON): lambda bot, ctx, text: bot.send(
        ctx.chat_id, cards.HELP_TEXT, cards.main_keyboard()
    ),
    ("/allow",): lambda bot, ctx, text: bot._allow(text, ctx.user, ctx.users, ctx.chat_id),
    ("/next", cards.NEXT_BUTTON): lambda bot, ctx, text: bot._next_card(ctx.username, ctx.chat_id),
    ("/review", cards.REVIEW_BUTTON): lambda bot, ctx, text: bot._start_review(ctx.username, ctx.chat_id),
    ("/stats", cards.STATS_BUTTON): lambda bot, ctx, text: bot.send_stats(ctx.username, ctx.chat_id),
    ("/gen", cards.GENERATE_BUTTON): lambda bot, ctx, text: bot._gen_command(
        ctx.username, ctx.chat_id, text if text.startswith("/") else ""
    ),
    ("/level", cards.LEVEL_BUTTON): lambda bot, ctx, text: bot._level_command(ctx.username, ctx.chat_id, text),
    ("/ex", cards.EXERCISE_BUTTON): lambda bot, ctx, text: bot.start_exercises(ctx.username, ctx.chat_id),
    (cards.TOPICS_BUTTON,): lambda bot, ctx, text: bot._show_topics(ctx.username, ctx.chat_id),
    ("/practice", cards.PRACTICE_BUTTON): lambda bot, ctx, text: bot._practice_command(ctx.username, ctx.chat_id),
    (cards.SETTINGS_BUTTON,): lambda bot, ctx, text: bot.send(
        ctx.chat_id,
        f"{cards.SETTINGS_BUTTON}: редкие действия. «{cards.BACK_BUTTON}» — в главное меню.",
        cards.settings_keyboard(),
    ),
    (cards.BACK_BUTTON,): lambda bot, ctx, text: bot.send(ctx.chat_id, "Главное меню.", cards.main_keyboard()),
    ("/cancel",): lambda bot, ctx, text: bot._cancel(ctx),
}
MESSAGE_ROUTES = {key: handler for keys, handler in _MESSAGE_ROUTES.items() for key in keys}


@dataclass(frozen=True)
class CallbackRoute:
    handler: object  # (bot, ctx, value, data) -> awaitable
    answer: bool = True  # сразу ответить на нажатие (кнопка перестаёт «крутиться»)
    drop: bool = False  # убрать кнопки под сообщением


# Префикс callback_data ("ia:<id>" -> "ia") -> маршрут.
CALLBACK_ROUTES = {
    "cancel": CallbackRoute(lambda bot, ctx, value, data: bot._cancel(ctx), drop=True),
    "menu": CallbackRoute(lambda bot, ctx, value, data: bot._back_to_adding(ctx)),
    "px": CallbackRoute(lambda bot, ctx, value, data: bot._skip_sentences(ctx.username, ctx.chat_id), drop=True),
    # Упражнения сами убирают кнопки параллельно с разбором ответа.
    "xo": CallbackRoute(lambda bot, ctx, value, data: bot._exercise_callback(ctx.username, ctx.chat_id, data, ctx.message_id)),
    "xn": CallbackRoute(lambda bot, ctx, value, data: bot._exercise_callback(ctx.username, ctx.chat_id, data, ctx.message_id)),
    "xd": CallbackRoute(lambda bot, ctx, value, data: bot._exercise_callback(ctx.username, ctx.chat_id, data, ctx.message_id)),
    "xt": CallbackRoute(lambda bot, ctx, value, data: bot._toggle_topic(ctx.username, ctx.chat_id, value, ctx.message_id)),
    "ia": CallbackRoute(lambda bot, ctx, value, data: bot._inbox_approve(ctx.username, ctx.chat_id, value), drop=True),
    "ib": CallbackRoute(lambda bot, ctx, value, data: bot._inbox_approve(ctx.username, ctx.chat_id, value, bare=True), drop=True),
    "ix": CallbackRoute(lambda bot, ctx, value, data: bot._inbox_delete(ctx.username, ctx.chat_id, value), drop=True),
    "ie": CallbackRoute(lambda bot, ctx, value, data: bot._inbox_edit_start(ctx.username, ctx.chat_id, value)),
    "lv": CallbackRoute(lambda bot, ctx, value, data: bot._set_level(
        ctx.username, ctx.chat_id, value, menu_message_id=ctx.message_id, scope="gen")),
    "glv": CallbackRoute(lambda bot, ctx, value, data: bot._set_level(
        ctx.username, ctx.chat_id, value, menu_message_id=ctx.message_id)),
    "gen": CallbackRoute(lambda bot, ctx, value, data: bot.generate_to_inbox(ctx.username, ctx.chat_id, int(value))
                         if value.isdigit() else asyncio.sleep(0)),
    # Ответ на карточку сам отвечает на нажатие: всплывающий текст зависит от результата.
    "k": CallbackRoute(lambda bot, ctx, value, data: bot._answer_card(ctx, "k", value), answer=False),
    "n": CallbackRoute(lambda bot, ctx, value, data: bot._answer_card(ctx, "n", value), answer=False),
    "a": CallbackRoute(lambda bot, ctx, value, data: bot._answer_card(ctx, "a", value), answer=False),
    # Кнопки карточки остаются: пока предложения не прислал, можно ответить и обычным «Знаю».
    "sx": CallbackRoute(lambda bot, ctx, value, data: bot._compose_start(ctx, value)),
    "sd": CallbackRoute(lambda bot, ctx, value, data: bot._compose_done(ctx)),
    "sc": CallbackRoute(lambda bot, ctx, value, data: bot._compose_cancel(ctx), drop=True),
}


class BotCore:
    def __init__(self, store, tg, owner=None, schedule=None, ai=None, ai_model=None, clock=None, defer=None):
        """`owner` — Telegram username владельца, с него начинается список допущенных.

        `schedule` — расписание (shared.schedule.Schedule), `ai` — Workers AI (см. shared.ai).
        """
        self.repo = Repo(store)
        self.tg = tg
        self.owner = owner
        self.schedule = schedule
        self.ai = ai
        self.ai_model = ai_model or generator.DEFAULT_MODEL
        # Подменяется в тестах, чтобы проигрывать расписание без ожидания.
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        # Фоновые задачи: в воркере — ctx.waitUntil (работа продолжается после ответа Telegram),
        # в тестах и скриптах — выполняются сразу.
        self.defer = defer
        # Чат, у которого устарела нижняя клавиатура: следующий простой ответ принесёт новую.
        self._keyboard_refresh_chat = self._keyboard_refresh_user = None

    # ---- время и фон ---------------------------------------------------------

    def _now(self):
        return self.clock()

    def _today(self, now):
        return self.schedule.local(now).date().isoformat() if self.schedule else ""

    async def _background(self, coro):
        if self.defer is not None:
            self.defer(coro)
        else:
            await coro

    # ---- отправка ------------------------------------------------------------

    async def send(self, chat_id, text, reply_markup=None):
        if reply_markup is None and chat_id == self._keyboard_refresh_chat:
            reply_markup = cards.main_keyboard()
        if reply_markup == cards.main_keyboard() and chat_id == self._keyboard_refresh_chat:
            self._keyboard_refresh_chat = None
            await self._remember_keyboard()
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        return await self.tg.call("sendMessage", payload)

    async def _remember_keyboard(self):
        username = self._keyboard_refresh_user
        settings = await self.repo.get(settings_key(username), {})
        settings["keyboard"] = cards.KEYBOARD_VERSION
        await self.repo.put(settings_key(username), settings)

    async def _drop_buttons(self, chat_id, message_id):
        await self.tg.call(
            "editMessageReplyMarkup",
            {"chat_id": chat_id, "message_id": message_id, "reply_markup": {"inline_keyboard": []}},
        )

    async def _edit_menu(self, chat_id, message_id, text, keyboard):
        await self.tg.call(
            "editMessageText",
            {"chat_id": chat_id, "message_id": message_id, "text": text, "parse_mode": "HTML", "reply_markup": keyboard},
        )

    async def _users_with_chat(self):
        # Только чтение: список заводится воркером при первом сообщении.
        users = await self.repo.get(ALLOWED_USERS_KEY, [])
        return [u for u in users if u.get("chat_id")]

    # ---- крон ------------------------------------------------------------------

    async def on_cron(self, now):
        """Крон воркера раз в минуту: каждая фича сама решает, её ли это минута."""
        if self.schedule is None:
            return []
        result = []
        for job in (self._cron_practice, self._cron_exercise_pool, self._cron_exercises, self._cron_report,
                    self._cron_cards):
            result += await job(now)
        return result

    # ---- апдейты Telegram ----------------------------------------------------

    async def handle_update(self, update):
        if "callback_query" in update:
            query = update["callback_query"]
            sender, chat = query.get("from", {}), (query.get("message") or {}).get("chat", {})
        elif "message" in update:
            query, message = None, update["message"]
            sender, chat = message.get("from", {}), message.get("chat", {})
        else:
            return
        if chat.get("type") != "private":
            return
        # Список пользователей и настройки читаются параллельно: ключ настроек известен по имени отправителя.
        users, settings = await asyncio.gather(
            self.repo.allowed_users(self.owner),
            self.repo.get(settings_key(normalize_username(sender.get("username"))), {}),
        )
        user = find_user(users, sender.get("username"))
        if user is None:
            return  # чужим не отвечаем
        if user.get("chat_id") != chat["id"]:
            user["chat_id"] = chat["id"]
            await self.repo.put(ALLOWED_USERS_KEY, users)
        username = normalize_username(user["username"])
        if settings.get("keyboard") != cards.KEYBOARD_VERSION:
            self._keyboard_refresh_chat, self._keyboard_refresh_user = chat["id"], username
        ctx = Ctx(username, chat["id"], user, users)
        if query is not None:
            ctx.message_id = (query.get("message") or {}).get("message_id")
            ctx.query_id = query["id"]
            await self._on_callback(ctx, query.get("data") or "")
        else:
            await self._on_message(ctx, update["message"])

    async def _on_callback(self, ctx, data):
        prefix, _, value = data.partition(":")
        route = CALLBACK_ROUTES.get(prefix)
        first = []
        if route is None or route.answer:
            first.append(self.tg.call("answerCallbackQuery", {"callback_query_id": ctx.query_id}))
        if route is not None and route.drop and ctx.message_id is not None:
            first.append(self._drop_buttons(ctx.chat_id, ctx.message_id))
        await asyncio.gather(*first)
        if route is not None:
            await route.handler(self, ctx, value, data)

    async def _on_message(self, ctx, message):
        if message.get("voice"):
            await self._on_voice(ctx, message["voice"])
            return
        text = (message.get("text") or "").strip()
        if not text:
            return
        text = cards.LEGACY_BUTTONS.get(text, text)
        command = text.split()[0].split("@")[0].lower() if text.startswith("/") else None
        handler = MESSAGE_ROUTES.get(command or text)
        if handler is not None:
            await handler(self, ctx, text)
        elif command:
            await self.send(ctx.chat_id, "Неизвестная команда.\n\n" + cards.HELP_TEXT)
        else:
            await self._on_text(ctx, text)

    async def _on_text(self, ctx, text):
        """Обычный текст: ответ в идущем режиме (исправление, предложения со словом, практика, упражнение,
        повтор) или новое слово."""
        state = await self.repo.get(state_key(ctx.username), {})
        if state.get("mode") == "edit":
            await self._inbox_edit_finish(ctx.username, ctx.chat_id, state, text)
            return
        compose_state, word = await self._active_compose(ctx.username)
        if compose_state is not None:
            await self._compose_text(ctx, compose_state, word, text)
            return
        session = await self.repo.get(session_key(ctx.username), None)
        if session:
            await self._practice_answer(ctx.username, ctx.chat_id, session, text)
            return
        exercise = await self._typed_exercise(ctx.username)
        if exercise is not None:
            await self._exercise_answer(ctx.username, ctx.chat_id, exercise[0], exercise[1], text)
            return
        numbers = w.parse_numbers(text)
        if numbers is not None and state.get("mode") == "review":
            await self._finish_review(ctx.username, ctx.chat_id, state, numbers)
        else:
            await self._add_word(ctx.username, ctx.chat_id, text)

    async def _cancel(self, ctx):
        await self.repo.put(state_key(ctx.username), {})
        await self.send(ctx.chat_id, "Отменено.", cards.main_keyboard())

    async def _back_to_adding(self, ctx):
        await self.repo.put(state_key(ctx.username), {})
        await self.send(ctx.chat_id, "Режим добавления слов.\n\n" + cards.HELP_TEXT, cards.main_keyboard())
