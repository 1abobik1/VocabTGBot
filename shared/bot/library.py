"""Словарь: добавление слов (с примерами от ИИ), повтор архива, доступ для друзей."""


from .. import cards, generator
from .. import words as w
from ..cards import escape
from ..keys import (
    ALLOWED_USERS_KEY,
    inbox_key,
    known_key,
    practice_key,
    queue_key,
    seen_key,
    state_key,
)
from ..storage import find_user, normalize_username

# Сколько слов помнить в списке «уже было» (защита от повторов при генерации).
SEEN_LIMIT = 2000


class LibraryFlow:
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
        if not examples and not synonyms and self.ai is not None:
            if await self._add_with_ai_examples(username, chat_id, en, ru):
                return
        queue.append(w.new_word(en, ru, examples, synonyms))
        await self.repo.put(queue_key(username), queue)
        await self._remember_seen(username, [en])
        await self.send(
            chat_id,
            f"➕ Добавлено: <b>{escape(en)}</b> — {escape(ru)}\n"
            f"Примеров: {len(examples)}. В очереди: {len(queue)}.",
        )

    async def _add_with_ai_examples(self, username, chat_id, en, ru):
        """Ask the AI for examples and synonyms and put the card in the inbox for review."""
        await self.send(chat_id, f"⏳ Придумываю примеры для <b>{escape(en)}</b>…")
        try:
            level = await self._global_level(username)
            examples, synonyms = await generator.enrich(
                self.ai, en, ru, model=self.ai_model, level=level, grammar=await self._grammar_focus(username)
            )
        except Exception as error:
            print(f"enrichment failed: {error!r}")
            examples = []
        if not examples:
            return False  # fall back to adding the bare word
        word = w.new_word(en, ru, examples, synonyms)
        word["source"] = "manual"
        await self._remember_seen(username, [en])
        inbox = await self.repo.get(inbox_key(username), [])
        inbox.append(word)
        await self.repo.put(inbox_key(username), inbox)
        await self.send_inbox_card(username, chat_id, inbox[-1:])
        return True

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
        restored = w.restore_from_known(queue, known, chosen, self.schedule, self._now())
        if restored:
            await self.repo.put(queue_key(username), queue)
            await self.repo.put(known_key(username), known)
        await self.repo.put(state_key(username), {})
        lines = [f"🔁 Вернул в очередь: {len(restored)}"]
        lines += [f"• {escape(x['en'])} — {escape(x['ru'])}" for x in restored]
        if bad:
            lines.append(f"Нет таких номеров: {', '.join(bad)}")
        await self.send(chat_id, "\n".join(lines), cards.main_keyboard())

    async def _seen(self, username):
        """Английские слова, которые уже были у пользователя: очередь, архив, практика, входящие
        и всё, что ИИ предлагал раньше, даже если карточку удалили."""
        stored = await self.repo.get(seen_key(username), [])
        current = [
            x["en"]
            for key in (queue_key, known_key, practice_key, inbox_key)
            for x in await self.repo.get(key(username), [])
        ]
        seen, result = set(), []
        for en in stored + current:
            key = w.compact(en)
            if key not in seen:
                seen.add(key)
                result.append(en)
        return result[-SEEN_LIMIT:]

    async def _remember_seen(self, username, words):
        seen = await self._seen(username)
        known = {w.compact(x) for x in seen}
        seen += [x for x in words if w.compact(x) not in known]
        await self.repo.put(seen_key(username), seen[-SEEN_LIMIT:])
