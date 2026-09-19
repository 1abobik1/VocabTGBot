"""Update routing and scheduled jobs, independent of where they run.

`store` must provide `async get(key) -> str | None` and `async put(key, value: str)`.
`tg` must provide `async call(method, payload: dict) -> dict` (Telegram Bot API).
"""

import json
from datetime import timedelta

from . import cards
from . import generator
from . import practice as pr
from .cards import escape
from . import words as w

ALLOWED_USERS_KEY = "allowed_users"


def queue_key(username):
    return f"queue:{username}"


def known_key(username):
    return f"known:{username}"


def state_key(username):
    return f"state:{username}"


def inbox_key(username):
    """Generated cards waiting for review before they join the queue."""
    return f"inbox:{username}"


def settings_key(username):
    return f"settings:{username}"


def practice_key(username):
    """Words answered "Знаю" in both directions, waiting for the typed practice."""
    return f"practice:{username}"


def session_key(username):
    """The running practice: {"ids": [...], "round": "ru_en" | "en_ru" | "sentences", "results": {...}}."""
    return f"session:{username}"


def sched_key(username):
    """{"missed": n}: slots skipped while a card or the practice waited for an answer."""
    return f"sched:{username}"


# Missed slots are delivered at once after the answer, but never more than this many.
DEFAULT_CATCH_UP_LIMIT = 10


# Auto-generation runs this long before a slot when the queue is empty.
AUTOGEN_LEAD = timedelta(minutes=20)


def normalize_username(name):
    return (name or "").strip().lstrip("@").lower()


class Repo:
    def __init__(self, store):
        self.store = store

    async def get(self, key, default):
        raw = await self.store.get(key)
        if not raw:
            return default
        return json.loads(raw)

    async def put(self, key, value):
        await self.store.put(key, json.dumps(value, ensure_ascii=False))

    async def allowed_users(self, owner):
        users = await self.get(ALLOWED_USERS_KEY, None)
        if users is None:
            if not owner:
                return []  # no owner configured: nobody is allowed
            users = [{"username": owner, "role": "owner"}]
            await self.put(ALLOWED_USERS_KEY, users)
        return users


def find_user(users, username):
    key = normalize_username(username)
    for user in users:
        if normalize_username(user["username"]) == key:
            return user
    return None


class Bot:
    def __init__(self, store, tg, owner=None, schedule=None, ai=None, ai_model=None):
        """`owner` is the Telegram username that seeds allowed_users on the first message.

        `schedule` (shared.schedule.Schedule) enables sending a card right after an answer
        when a slot was skipped because the previous card was still unanswered.
        `ai` (see shared.generator) enables AI-generated cards.
        """
        self.repo = Repo(store)
        self.tg = tg
        self.owner = owner
        self.schedule = schedule
        self.ai = ai
        self.ai_model = ai_model or generator.DEFAULT_MODEL
        # Chat whose reply keyboard is outdated: the next plain reply carries the new one.
        self._keyboard_refresh_chat = self._keyboard_refresh_user = None

    # ---- sending helpers -------------------------------------------------

    async def send(self, chat_id, text, reply_markup=None):
        if reply_markup is None and chat_id == self._keyboard_refresh_chat:
            reply_markup = cards.main_keyboard()
        if reply_markup == cards.main_keyboard() and chat_id == self._keyboard_refresh_chat:
            self._keyboard_refresh_chat = None
            await self._remember_keyboard(chat_id)
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        return await self.tg.call("sendMessage", payload)

    async def _remember_keyboard(self, chat_id):
        username = self._keyboard_refresh_user
        settings = await self.repo.get(settings_key(username), {})
        settings["keyboard"] = cards.KEYBOARD_VERSION
        await self.repo.put(settings_key(username), settings)

    async def send_card(self, username, chat_id):
        """Send queue[0] to the user and bump its shown_count. Returns the word or None."""
        sent = await self.send_cards(username, chat_id, 1, include_pending=True)
        return sent[0] if sent else None

    async def send_cards(self, username, chat_id, count, include_pending=False):
        """Send up to `count` cards from the front of the queue; each one then awaits an answer.

        Cards already waiting for an answer are skipped unless `include_pending` (used by /next
        to re-send the current card).
        """
        key = queue_key(normalize_username(username))
        queue = await self.repo.get(key, [])
        chosen = [x for x in queue if include_pending or not x.get("sent_at")][:count]
        for word in chosen:
            await self.send(chat_id, cards.render_card(word), cards.card_keyboard(word))
            word["shown_count"] = word.get("shown_count", 0) + 1
            # Marks the card as awaiting an answer: no new scheduled cards until it is answered.
            word["sent_at"] = w.now_iso()
        if chosen:
            await self.repo.put(key, queue)
        return chosen

    async def send_stats(self, username, chat_id):
        name = normalize_username(username)
        known = await self.repo.get(known_key(name), [])
        queue = await self.repo.get(queue_key(name), [])
        waiting = await self.repo.get(practice_key(name), [])
        await self.send(chat_id, cards.render_stats(w.weekly_stats(known), len(queue), len(waiting)))

    async def _is_blocked(self, username, queue):
        """New cards wait while a card is unanswered or the practice is running."""
        return w.is_awaiting_answer(queue) or bool(await self.repo.get(session_key(username), None))

    def _catch_up_limit(self):
        return self.schedule.cards_per_day if self.schedule else DEFAULT_CATCH_UP_LIMIT

    async def _catch_up(self, username, chat_id):
        """After the last answer, send every card whose slot was skipped while waiting."""
        missed = (await self.repo.get(sched_key(username), {})).get("missed", 0)
        if not missed:
            return []
        queue = await self.repo.get(queue_key(username), [])
        if await self._is_blocked(username, queue):
            return []
        await self.repo.put(sched_key(username), {"missed": 0})
        sent = await self.send_cards(username, chat_id, missed)
        return sent

    # ---- scheduled jobs (Worker cron, GitHub Actions) ---------------------

    async def _users_with_chat(self):
        # Read-only: the list is seeded by the Worker on the first message.
        users = await self.repo.get(ALLOWED_USERS_KEY, [])
        return [u for u in users if u.get("chat_id")]

    async def broadcast_cards(self, count_missed=False):
        """Send the next card to every user who is not waiting on an answer.

        With `count_missed` (schedule slots) a blocked user's slot is remembered and the card
        is delivered right after the answer (see _catch_up).
        """
        sent = []
        for user in await self._users_with_chat():
            name = normalize_username(user["username"])
            queue = await self.repo.get(queue_key(name), [])
            if await self._is_blocked(name, queue):
                if count_missed:
                    missed = (await self.repo.get(sched_key(name), {})).get("missed", 0)
                    await self.repo.put(sched_key(name), {"missed": min(missed + 1, self._catch_up_limit())})
            elif queue:
                await self.send_card(user["username"], user["chat_id"])
                sent.append(user["username"])
        return sent

    async def on_cron(self, now):
        """Called by the Worker cron every minute: the weekly practice, cards on schedule slots,
        and 20 minutes before a slot an AI card for users whose queue and inbox are both empty."""
        if self.schedule is None:
            return []
        result = []
        if self.schedule.is_practice_time(now):
            for user in await self._users_with_chat():
                if await self.start_practice(normalize_username(user["username"]), user["chat_id"], quiet=True):
                    result.append(user["username"])
        if self.schedule.is_slot(now):
            result += await self.broadcast_cards(count_missed=True)
        elif self.ai is not None and self.schedule.is_slot(now + AUTOGEN_LEAD):
            result += await self.autogenerate()
        return result

    async def autogenerate(self):
        generated = []
        for user in await self._users_with_chat():
            name = normalize_username(user["username"])
            if await self.repo.get(queue_key(name), []) or await self.repo.get(inbox_key(name), []):
                continue
            if await self.generate_to_inbox(name, user["chat_id"], 1, auto=True):
                generated.append(user["username"])
        return generated

    async def broadcast_stats(self):
        users = await self._users_with_chat()
        for user in users:
            await self.send_stats(user["username"], user["chat_id"])
        return [u["username"] for u in users]

    # ---- webhook updates --------------------------------------------------

    async def handle_update(self, update):
        if "callback_query" in update:
            query = update["callback_query"]
            sender, chat = query.get("from", {}), (query.get("message") or {}).get("chat", {})
        elif "message" in update:
            message = update["message"]
            sender, chat = message.get("from", {}), message.get("chat", {})
        else:
            return
        if chat.get("type") != "private":
            return
        users = await self.repo.allowed_users(self.owner)
        user = find_user(users, sender.get("username"))
        if user is None:
            return  # not allowed: ignore silently
        if user.get("chat_id") != chat["id"]:
            user["chat_id"] = chat["id"]
            await self.repo.put(ALLOWED_USERS_KEY, users)
        username = normalize_username(user["username"])
        settings = await self.repo.get(settings_key(username), {})
        if settings.get("keyboard") != cards.KEYBOARD_VERSION:
            self._keyboard_refresh_chat, self._keyboard_refresh_user = chat["id"], username

        if "callback_query" in update:
            await self._on_callback(update["callback_query"], username, chat["id"])
        else:
            await self._on_message(update["message"], user, users, username, chat["id"])

    async def _on_callback(self, query, username, chat_id):
        data = query.get("data") or ""
        message_id = (query.get("message") or {}).get("message_id")
        if data == "cancel":
            await self.tg.call("answerCallbackQuery", {"callback_query_id": query["id"]})
            if message_id is not None:
                await self._drop_buttons(chat_id, message_id)
            await self.repo.put(state_key(username), {})
            await self.send(chat_id, "Отменено.", cards.main_keyboard())
            return
        if data == "menu":
            await self.repo.put(state_key(username), {})
            await self.tg.call("answerCallbackQuery", {"callback_query_id": query["id"]})
            await self.send(chat_id, "Режим добавления слов.\n\n" + cards.HELP_TEXT, cards.main_keyboard())
            return
        if data == "px":  # skip the optional sentences round
            await self.tg.call("answerCallbackQuery", {"callback_query_id": query["id"]})
            if message_id is not None:
                await self._drop_buttons(chat_id, message_id)
            session = await self.repo.get(session_key(username), None)
            if session and session.get("round") == "sentences":
                await self._finish_practice(username, chat_id, session)
            return
        prefix, _, value = data.partition(":")
        if prefix in ("ia", "ie", "ix", "lv", "gen"):
            await self.tg.call("answerCallbackQuery", {"callback_query_id": query["id"]})
            if prefix in ("ia", "ix") and message_id is not None:
                await self._drop_buttons(chat_id, message_id)
            if prefix == "ia":
                await self._inbox_approve(username, chat_id, value)
            elif prefix == "ix":
                await self._inbox_delete(username, chat_id, value)
            elif prefix == "ie":
                await self._inbox_edit_start(username, chat_id, value)
            elif prefix == "lv":
                await self._set_level(username, chat_id, value, menu_message_id=message_id)
            elif value.isdigit():
                await self.generate_to_inbox(username, chat_id, int(value))
            return

        # "k:<id>:<stage>" / "n:<id>:<stage>"; cards sent before the stage suffix have no ":<stage>".
        action, _, rest = data.partition(":")
        word_id, _, stage = rest.partition(":")
        if action not in ("k", "n") or not word_id or (stage and not stage.isdigit()):
            await self.tg.call("answerCallbackQuery", {"callback_query_id": query["id"]})
            return
        stage = int(stage) if stage else None

        queue = await self.repo.get(queue_key(username), [])
        if action == "k":
            waiting = await self.repo.get(practice_key(username), [])
            result, word = w.answer_known(queue, waiting, word_id, stage=stage)
        else:
            waiting = None
            result, word = w.answer_unknown(queue, word_id, stage=stage)

        notices = {
            w.FLIPPED: "👍 Теперь проверю в обратную сторону",
            w.TO_PRACTICE: "🎯 Отлично! Слово ждёт субботней практики",
            w.REQUEUED: "🔁 Слово вернётся через пару карточек",
            w.NOT_FOUND: "Эта карточка уже неактуальна",
        }
        if result != w.NOT_FOUND:
            await self.repo.put(queue_key(username), queue)
            if result == w.TO_PRACTICE:
                await self.repo.put(practice_key(username), waiting)

        await self.tg.call("answerCallbackQuery", {"callback_query_id": query["id"], "text": notices[result]})
        if message_id is not None:
            # Drop the buttons so the same card can't be answered twice.
            await self._drop_buttons(chat_id, message_id)
        if result != w.NOT_FOUND:
            await self._catch_up(username, chat_id)

    async def _drop_buttons(self, chat_id, message_id):
        await self.tg.call(
            "editMessageReplyMarkup",
            {"chat_id": chat_id, "message_id": message_id, "reply_markup": {"inline_keyboard": []}},
        )

    async def _on_message(self, message, user, users, username, chat_id):
        text = (message.get("text") or "").strip()
        if not text:
            return
        command = text.split()[0].split("@")[0].lower() if text.startswith("/") else None
        text = cards.LEGACY_BUTTONS.get(text, text)

        if command in ("/start", "/help") or text == cards.HELP_BUTTON:
            await self.send(chat_id, cards.HELP_TEXT, cards.main_keyboard())
        elif command == "/allow":
            await self._allow(text, user, users, chat_id)
        elif command == "/next" or text == cards.NEXT_BUTTON:
            if not await self.send_card(username, chat_id):
                await self.send(chat_id, "Очередь пуста — добавь новое слово.")
        elif command == "/review" or text == cards.REVIEW_BUTTON:
            await self._start_review(username, chat_id)
        elif command == "/stats" or text == cards.STATS_BUTTON:
            await self.send_stats(username, chat_id)
        elif command == "/gen" or text == cards.GENERATE_BUTTON:
            await self._gen_command(username, chat_id, text if command else "")
        elif command == "/level":
            parts = text.split()
            if len(parts) > 1:
                await self._set_level(username, chat_id, parts[1])
            else:
                await self._gen_command(username, chat_id, "")
        elif command == "/practice" or text == cards.PRACTICE_BUTTON:
            if not await self.start_practice(username, chat_id):
                await self.send(chat_id, "Для практики пока нет слов: они появятся после «Знаю» в обе стороны.")
        elif command == "/cancel":
            await self.repo.put(state_key(username), {})
            await self.send(chat_id, "Отменено.", cards.main_keyboard())
        elif command:
            await self.send(chat_id, "Неизвестная команда.\n\n" + cards.HELP_TEXT)
        else:
            state = await self.repo.get(state_key(username), {})
            numbers = w.parse_numbers(text)
            session = None if state.get("mode") == "edit" else await self.repo.get(session_key(username), None)
            if state.get("mode") == "edit":
                await self._inbox_edit_finish(username, chat_id, state, text)
            elif session:
                await self._practice_answer(username, chat_id, session, text)
            elif numbers is not None and state.get("mode") == "review":
                await self._finish_review(username, chat_id, state, numbers)
            else:
                await self._add_word(username, chat_id, text)

    async def _allow(self, text, user, users, chat_id):
        if user.get("role") != "owner":
            await self.send(chat_id, "Управлять доступом может только владелец.")
            return
        parts = text.split()
        if len(parts) != 2 or not normalize_username(parts[1]):
            await self.send(chat_id, "Формат: /allow @username")
            return
        name = parts[1].strip().lstrip("@")
        if find_user(users, name):
            await self.send(chat_id, f"@{name} уже есть в списке.")
            return
        users.append({"username": name, "role": "full"})
        await self.repo.put(ALLOWED_USERS_KEY, users)
        await self.send(chat_id, f"✅ @{name} получил доступ. Пусть напишет боту /start.")

    async def _add_word(self, username, chat_id, text):
        try:
            en, ru, examples, synonyms = w.parse_card_text(text)
        except w.ParseError as error:
            await self.send(chat_id, f"⚠️ {error}\n\n{cards.HELP_TEXT}")
            return
        queue = await self.repo.get(queue_key(username), [])
        known = await self.repo.get(known_key(username), [])
        duplicate = w.find_duplicate(en, ru, queue, known)
        if duplicate:
            where = f"уже в архиве (верни через «{cards.REVIEW_BUTTON}»)" if duplicate in known else "уже в очереди"
            pair = f"{escape(duplicate['en'])} — {escape(duplicate['ru'])}"
            await self.send(chat_id, f"Не добавил: «{pair}» {where}.")
            return
        queue.append(w.new_word(en, ru, examples, synonyms))
        await self.repo.put(queue_key(username), queue)
        await self.send(
            chat_id,
            f"➕ Добавлено: <b>{escape(en)}</b> — {escape(ru)}\n"
            f"Примеров: {len(examples)}. В очереди: {len(queue)}.",
        )

    async def _start_review(self, username, chat_id):
        known = w.known_newest_first(await self.repo.get(known_key(username), []))
        if not known:
            await self.send(
                chat_id, "Архив пока пуст — выученных слов нет.\n\n" + cards.ARCHIVE_PATH, cards.menu_inline_keyboard()
            )
            return
        await self.repo.put(state_key(username), {"mode": "review", "ids": [x["id"] for x in known]})
        chunks = cards.chunk_lines(cards.render_review_lines(known), header="📚 <b>Выученные слова</b> (новые сверху)")
        for chunk in chunks[:-1]:
            await self.send(chat_id, chunk)
        await self.send(
            chat_id,
            chunks[-1]
            + "\n\n"
            + cards.ARCHIVE_PATH
            + "\n\nПришли номера забытых слов через пробел, например: <code>2 5 7</code>",
            cards.menu_inline_keyboard(),
        )

    async def _finish_review(self, username, chat_id, state, numbers):
        ids = state.get("ids", [])
        chosen = [ids[n - 1] for n in dict.fromkeys(numbers) if 1 <= n <= len(ids)]
        bad = [str(n) for n in numbers if not 1 <= n <= len(ids)]
        queue = await self.repo.get(queue_key(username), [])
        known = await self.repo.get(known_key(username), [])
        restored = w.restore_from_known(queue, known, chosen)
        if restored:
            await self.repo.put(queue_key(username), queue)
            await self.repo.put(known_key(username), known)
        await self.repo.put(state_key(username), {})
        lines = [f"🔁 Вернул в очередь: {len(restored)}"]
        lines += [f"• {escape(x['en'])} — {escape(x['ru'])}" for x in restored]
        if bad:
            lines.append(f"Нет таких номеров: {', '.join(bad)}")
        await self.send(chat_id, "\n".join(lines), cards.main_keyboard())

    # ---- AI generation and the review inbox ---------------------------------

    async def _level(self, username):
        settings = await self.repo.get(settings_key(username), {})
        return settings.get("level") or generator.DEFAULT_LEVEL

    async def _set_level(self, username, chat_id, value, menu_message_id=None):
        """Save the level; from the generation menu the menu itself is updated in place."""
        level = generator.normalize_level(value)
        if level is None:
            await self.send(chat_id, "Уровень: A1, A2, B1, B2 или C1. Например: /level B2")
            return
        settings = await self.repo.get(settings_key(username), {})
        settings["level"] = level
        await self.repo.put(settings_key(username), settings)
        if menu_message_id is not None:
            await self.tg.call(
                "editMessageText",
                {
                    "chat_id": chat_id,
                    "message_id": menu_message_id,
                    "text": cards.render_generate_menu(level),
                    "parse_mode": "HTML",
                    "reply_markup": cards.generate_keyboard(level),
                },
            )
        else:
            await self.send(chat_id, f"Уровень для генерации: <b>{level}</b>")

    async def _gen_command(self, username, chat_id, text):
        """/gen [count] [level] [topic in free words]"""
        args = text.split()[1:]
        if not args:
            level = await self._level(username)
            await self.send(chat_id, cards.render_generate_menu(level), cards.generate_keyboard(level))
            return
        count = 5
        if args and args[0].isdigit():
            count = int(args.pop(0))
        level = None
        if args and generator.normalize_level(args[0]):
            level = generator.normalize_level(args.pop(0))
        topic = " ".join(args) or None
        if not 1 <= count <= generator.MAX_CARDS:
            await self.send(chat_id, f"Можно от 1 до {generator.MAX_CARDS} карточек за раз.")
            return
        await self.generate_to_inbox(username, chat_id, count, level=level, topic=topic)

    async def generate_to_inbox(self, username, chat_id, count, level=None, topic=None, auto=False):
        """Generate cards into the inbox and show the first one. Returns the number generated."""
        if self.ai is None:
            await self.send(chat_id, "Генерация недоступна: Workers AI не подключён.")
            return 0
        level = level or await self._level(username)
        if not auto:
            about = f", тема: {escape(topic)}" if topic else ""
            await self.send(chat_id, f"⏳ Генерирую {count} шт. (уровень {level}{about})…")
        queue = await self.repo.get(queue_key(username), [])
        known = await self.repo.get(known_key(username), [])
        inbox = await self.repo.get(inbox_key(username), [])
        existing = [x["en"] for x in known + queue + inbox]
        try:
            new_cards = await generator.generate(self.ai, level, count, topic, existing, model=self.ai_model)
        except Exception as error:  # the model or the network failed
            print(f"generation failed: {error!r}")
            new_cards = []
        if not new_cards:
            if not auto:
                await self.send(chat_id, "😕 Не получилось сгенерировать карточки. Попробуй ещё раз чуть позже.")
            return 0
        for card in new_cards:
            card["level"] = level
            if topic:
                card["topic"] = topic
        inbox += new_cards
        await self.repo.put(inbox_key(username), inbox)
        if not auto and len(new_cards) < count:
            await self.send(
                chat_id, f"Получилось {len(new_cards)} из {count}: остальные ИИ сделал с ошибками. Можно сгенерировать ещё."
            )
        if auto:
            await self.send(chat_id, "🤖 Очередь пуста — ИИ подобрал новую карточку. Проверь её:")
        await self.send_inbox_card(username, chat_id, inbox)
        return len(new_cards)

    async def send_inbox_card(self, username, chat_id, inbox=None):
        if inbox is None:
            inbox = await self.repo.get(inbox_key(username), [])
        if not inbox:
            queue = await self.repo.get(queue_key(username), [])
            await self.send(chat_id, f"✅ Все новые карточки разобраны. В очереди: {len(queue)}.")
            return
        word = inbox[0]
        await self.send(
            chat_id, cards.render_inbox_card(word, len(inbox), word.get("level")), cards.inbox_keyboard(word)
        )

    async def _take_from_inbox(self, username, chat_id, word_id):
        inbox = await self.repo.get(inbox_key(username), [])
        index = w.find_index(inbox, word_id)
        if index < 0:
            await self.send(chat_id, "Этой карточки уже нет во входящих.")
            return inbox, None
        return inbox, inbox.pop(index)

    async def _inbox_approve(self, username, chat_id, word_id):
        inbox, word = await self._take_from_inbox(username, chat_id, word_id)
        if word is None:
            return
        queue = await self.repo.get(queue_key(username), [])
        known = await self.repo.get(known_key(username), [])
        if w.find_duplicate(word["en"], word["ru"], queue, known):
            await self.send(chat_id, f"«{escape(word['en'])}» уже есть в очереди или архиве — пропускаю.")
        else:
            for field in ("level", "topic"):
                word.pop(field, None)
            word["added_at"] = w.now_iso()
            queue.append(word)
            await self.repo.put(queue_key(username), queue)
            await self.send(chat_id, f"➕ <b>{escape(word['en'])}</b> — в очереди на изучение ({len(queue)}).")
        await self.repo.put(inbox_key(username), inbox)
        await self.send_inbox_card(username, chat_id, inbox)

    async def _inbox_delete(self, username, chat_id, word_id):
        inbox, word = await self._take_from_inbox(username, chat_id, word_id)
        if word is None:
            return
        await self.repo.put(inbox_key(username), inbox)
        await self.send(chat_id, f"🗑 «{escape(word['en'])}» удалена.")
        await self.send_inbox_card(username, chat_id, inbox)

    async def _inbox_edit_start(self, username, chat_id, word_id):
        inbox = await self.repo.get(inbox_key(username), [])
        index = w.find_index(inbox, word_id)
        if index < 0:
            await self.send(chat_id, "Этой карточки уже нет во входящих.")
            return
        await self.repo.put(state_key(username), {"mode": "edit", "id": word_id})
        await self.send(
            chat_id,
            "✏️ Скопируй текст (нажми на него), исправь и пришли одним сообщением.\n\n"
            f"<code>{escape(w.card_to_text(inbox[index]))}</code>",
            cards.cancel_keyboard(),
        )

    async def _inbox_edit_finish(self, username, chat_id, state, text):
        try:
            en, ru, examples, synonyms = w.parse_card_text(text)
        except w.ParseError as error:
            await self.send(chat_id, f"⚠️ {error}\nИсправь и пришли ещё раз или /cancel.")
            return
        inbox = await self.repo.get(inbox_key(username), [])
        index = w.find_index(inbox, state.get("id"))
        await self.repo.put(state_key(username), {})
        if index < 0:
            await self.send(chat_id, "Этой карточки уже нет во входящих.")
            return
        word = inbox[index]
        word.update(en=en, ru=ru, examples=examples, synonyms=synonyms)
        await self.repo.put(inbox_key(username), inbox)
        await self.send(chat_id, "Готово, вот исправленная карточка:")
        await self.send(chat_id, cards.render_inbox_card(word, len(inbox), word.get("level")), cards.inbox_keyboard(word))

    # ---- weekly typed practice ------------------------------------------------

    async def _session_words(self, username, session):
        waiting = await self.repo.get(practice_key(username), [])
        by_id = {x["id"]: x for x in waiting}
        return [by_id[i] for i in session["ids"] if i in by_id]

    async def start_practice(self, username, chat_id, quiet=False):
        """Start (or re-send) the practice. Returns False when there is nothing to practise."""
        session = await self.repo.get(session_key(username), None)
        if session:
            words = await self._session_words(username, session)
            await self.send(chat_id, "⏰ Практика ещё не закончена.")
            await self._send_practice_round(chat_id, words, session["round"])
            return True
        waiting = await self.repo.get(practice_key(username), [])
        if not waiting:
            return False
        session = {"ids": [x["id"] for x in waiting], "round": pr.RU_EN, "results": {}}
        await self.repo.put(session_key(username), session)
        await self._send_practice_round(chat_id, waiting, pr.RU_EN)
        return True

    async def _send_practice_round(self, chat_id, words, round_name):
        if round_name == "sentences":
            await self.send(chat_id, cards.render_practice_sentences(words), cards.practice_skip_keyboard())
        else:
            for chunk in cards.chunk_lines(cards.render_practice_round(words, round_name).split("\n")):
                await self.send(chat_id, chunk)

    async def _practice_answer(self, username, chat_id, session, text):
        words = await self._session_words(username, session)
        round_name = session["round"]
        if round_name == "sentences" or not words:
            await self._finish_practice(username, chat_id, session, sentences_answer=text)
            return
        graded = pr.grade_round(words, round_name, text)
        expected = pr.expected_answers(words, round_name)
        for (word, _, verdict) in graded:
            session["results"].setdefault(word["id"], {})[round_name] = verdict
        feedback = [(word, answer, verdict, exp) for (word, answer, verdict), exp in zip(graded, expected)]
        await self.send(chat_id, cards.render_practice_feedback(feedback))
        if round_name == pr.RU_EN:
            session["round"] = pr.EN_RU
        elif any(w.current_example(x) for x in words):
            session["round"] = "sentences"
        else:
            await self._finish_practice(username, chat_id, session)
            return
        await self.repo.put(session_key(username), session)
        await self._send_practice_round(chat_id, words, session["round"])

    async def _finish_practice(self, username, chat_id, session, sentences_answer=None):
        words = await self._session_words(username, session)
        if sentences_answer is not None:
            reference = [w.current_example(x)["en"] for x in words if w.current_example(x)]
            lines = ["Сверь с эталоном:"] + [f"{i}) {escape(t)}" for i, t in enumerate(reference, start=1)]
            await self.send(chat_id, "\n".join(lines))
        queue = await self.repo.get(queue_key(username), [])
        known = await self.repo.get(known_key(username), [])
        waiting = await self.repo.get(practice_key(username), [])
        outcome = pr.apply_results(queue, known, waiting, session["results"], w.now_iso())
        await self.repo.put(queue_key(username), queue)
        await self.repo.put(known_key(username), known)
        await self.repo.put(practice_key(username), waiting)
        await self.repo.put(session_key(username), {})
        cheer = cards.practice_cheer(len(outcome[pr.OK]))
        await self.send(chat_id, cards.render_practice_summary(outcome, cheer), cards.main_keyboard())
        await self._catch_up(username, chat_id)
