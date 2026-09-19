"""Update routing and scheduled jobs, independent of where they run.

`store` must provide `async get(key) -> str | None` and `async put(key, value: str)`.
`tg` must provide `async call(method, payload: dict) -> dict` (Telegram Bot API).
"""

import json

from . import cards
from .cards import escape
from . import words as w

ALLOWED_USERS_KEY = "allowed_users"


def queue_key(username):
    return f"queue:{username}"


def known_key(username):
    return f"known:{username}"


def state_key(username):
    return f"state:{username}"


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
    def __init__(self, store, tg, owner=None):
        """`owner` is the Telegram username that seeds allowed_users on the first message."""
        self.repo = Repo(store)
        self.tg = tg
        self.owner = owner

    # ---- sending helpers -------------------------------------------------

    async def send(self, chat_id, text, reply_markup=None):
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        return await self.tg.call("sendMessage", payload)

    async def send_card(self, username, chat_id):
        """Send queue[0] to the user and bump its shown_count. Returns the word or None."""
        key = queue_key(normalize_username(username))
        queue = await self.repo.get(key, [])
        if not queue:
            return None
        word = queue[0]
        await self.send(chat_id, cards.render_card(word), cards.card_keyboard(word))
        word["shown_count"] = word.get("shown_count", 0) + 1
        await self.repo.put(key, queue)
        return word

    async def send_stats(self, username, chat_id):
        name = normalize_username(username)
        known = await self.repo.get(known_key(name), [])
        queue = await self.repo.get(queue_key(name), [])
        await self.send(chat_id, cards.render_stats(w.weekly_stats(known), len(queue)))

    # ---- scheduled jobs (GitHub Actions) ----------------------------------

    async def _users_with_chat(self):
        # Read-only: the list is seeded by the Worker on the first message.
        users = await self.repo.get(ALLOWED_USERS_KEY, [])
        return [u for u in users if u.get("chat_id")]

    async def broadcast_cards(self):
        sent = []
        for user in await self._users_with_chat():
            if await self.send_card(user["username"], user["chat_id"]):
                sent.append(user["username"])
        return sent

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

        if "callback_query" in update:
            await self._on_callback(update["callback_query"], username, chat["id"])
        else:
            await self._on_message(update["message"], user, users, username, chat["id"])

    async def _on_callback(self, query, username, chat_id):
        data = query.get("data") or ""
        message_id = (query.get("message") or {}).get("message_id")
        if data == "menu":
            await self.repo.put(state_key(username), {})
            await self.tg.call("answerCallbackQuery", {"callback_query_id": query["id"]})
            await self.send(chat_id, "Режим добавления слов.\n\n" + cards.HELP_TEXT, cards.main_keyboard())
            return

        action, _, word_id = data.partition(":")
        if action not in ("k", "n") or not word_id:
            await self.tg.call("answerCallbackQuery", {"callback_query_id": query["id"]})
            return

        queue = await self.repo.get(queue_key(username), [])
        if action == "k":
            known = await self.repo.get(known_key(username), [])
            result, word = w.answer_known(queue, known, word_id)
        else:
            known = None
            result, word = w.answer_unknown(queue, word_id)

        notices = {
            w.FLIPPED: "👍 Теперь проверю в обратную сторону",
            w.ARCHIVED: "✅ В архив!",
            w.REQUEUED: "🔁 Слово вернётся через пару карточек",
            w.NOT_FOUND: "Эта карточка уже неактуальна",
        }
        if result != w.NOT_FOUND:
            await self.repo.put(queue_key(username), queue)
            if result == w.ARCHIVED:
                await self.repo.put(known_key(username), known)

        await self.tg.call("answerCallbackQuery", {"callback_query_id": query["id"], "text": notices[result]})
        if message_id is not None:
            # Drop the buttons so the same card can't be answered twice.
            await self.tg.call(
                "editMessageReplyMarkup",
                {"chat_id": chat_id, "message_id": message_id, "reply_markup": {"inline_keyboard": []}},
            )
        if result == w.ARCHIVED:
            await self.send(chat_id, cards.archive_cheer(word))

    async def _on_message(self, message, user, users, username, chat_id):
        text = (message.get("text") or "").strip()
        if not text:
            return
        command = text.split()[0].split("@")[0].lower() if text.startswith("/") else None

        if command in ("/start", "/help"):
            await self.send(chat_id, cards.HELP_TEXT, cards.main_keyboard())
        elif command == "/allow":
            await self._allow(text, user, users, chat_id)
        elif command == "/next" or text == cards.NEXT_BUTTON:
            if not await self.send_card(username, chat_id):
                await self.send(chat_id, "Очередь пуста — добавь новое слово.")
        elif command == "/review" or text == cards.REVIEW_BUTTON:
            await self._start_review(username, chat_id)
        elif command == "/stats":
            await self.send_stats(username, chat_id)
        elif command:
            await self.send(chat_id, "Неизвестная команда.\n\n" + cards.HELP_TEXT)
        else:
            numbers = w.parse_numbers(text)
            state = await self.repo.get(state_key(username), {})
            if numbers is not None and state.get("mode") == "review":
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
            en, ru, examples = w.parse_add_message(text)
        except w.ParseError as error:
            await self.send(chat_id, f"⚠️ {error}\n\n{cards.HELP_TEXT}")
            return
        queue = await self.repo.get(queue_key(username), [])
        known = await self.repo.get(known_key(username), [])
        duplicate = w.find_duplicate(en, ru, queue, known)
        if duplicate:
            where = "уже в архиве (верни через «Повторить слова»)" if duplicate in known else "уже в очереди"
            pair = f"{escape(duplicate['en'])} — {escape(duplicate['ru'])}"
            await self.send(chat_id, f"Не добавил: «{pair}» {where}.")
            return
        queue.append(w.new_word(en, ru, examples))
        await self.repo.put(queue_key(username), queue)
        await self.send(
            chat_id,
            f"➕ Добавлено: <b>{escape(en)}</b> — {escape(ru)}\n"
            f"Примеров: {len(examples)}. В очереди: {len(queue)}.",
        )

    async def _start_review(self, username, chat_id):
        known = w.known_newest_first(await self.repo.get(known_key(username), []))
        if not known:
            await self.send(chat_id, "Архив пока пуст — выученных слов нет.", cards.menu_inline_keyboard())
            return
        await self.repo.put(state_key(username), {"mode": "review", "ids": [x["id"] for x in known]})
        chunks = cards.chunk_lines(cards.render_review_lines(known), header="📚 <b>Выученные слова</b> (новые сверху)")
        for chunk in chunks[:-1]:
            await self.send(chat_id, chunk)
        await self.send(
            chat_id,
            chunks[-1] + "\n\nПришли номера забытых слов через пробел, например: <code>2 5 7</code>",
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
