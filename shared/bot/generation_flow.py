"""Уровни, генерация карточек ИИ и входящие на проверку."""


from .. import cards, generator, srs
from .. import curriculum as cur
from .. import exercises as ex
from .. import words as w
from ..cards import escape
from ..keys import (
    inbox_key,
    known_key,
    mistake_log_key,
    queue_key,
    settings_key,
    state_key,
)
from ..storage import normalize_username


class GenerationFlow:
    async def autogenerate(self, now=None):
        """За 20 минут до слота — новое слово от ИИ на случайную тему, если в очереди не осталось
        новых слов (или она пуста), дневная квота новых ещё не выбрана и прошлое предложение
        уже разобрано. Слово, как всегда, сначала приходит на проверку."""
        now = now or self._now()
        today = self._today(now)
        generated = []
        for user in await self._users_with_chat():
            name = normalize_username(user["username"])
            if await self.repo.get(inbox_key(name), []):
                continue
            if any(srs.is_new(x) for x in await self.repo.get(queue_key(name), [])):
                continue
            if (await self._day_state(name, today))["new"] >= self._new_quota():
                continue
            if await self.generate_to_inbox(name, user["chat_id"], 1, auto=True):
                generated.append(user["username"])
        return generated

    async def _global_level(self, username):
        """Общий уровень: по нему ИИ делает всё — слова, примеры, упражнения."""
        settings = await self.repo.get(settings_key(username), {})
        return cur.clamp_level(settings.get("level"))

    async def _level(self, username):
        """Уровень для генерации слов: свой, если выбран, иначе общий."""
        settings = await self.repo.get(settings_key(username), {})
        return cur.clamp_level(settings.get("gen_level") or settings.get("level"))

    async def _grammar_focus(self, username, limit=2):
        """Грамматика, которую стоит подтянуть: слабые правила, затем изучаемые на рабочем уровне.
        Примеры к новым словам строятся на ней, чтобы слова и грамматика учились вместе."""
        log = await self.repo.get(mistake_log_key(username), [])
        level = cur.working_level(log, await self._global_level(username))
        rules = [cur.BY_ID[s["rule"]] for s in ex.weak_spots(log, self._now()) if s["rule"] in cur.BY_ID]
        rules += [r for r in cur.progress(log, level)[-1]["learning"] if r not in rules]
        return [r.en for r in rules if r.id != cur.VOCAB.id][:limit]

    async def _generation_menu(self, username):
        settings = await self.repo.get(settings_key(username), {})
        own = bool(settings.get("gen_level"))
        level = await self._level(username)
        return cards.render_generate_menu(level, own), cards.generate_keyboard(level, own)

    async def _set_level(self, username, chat_id, value, menu_message_id=None, scope="global"):
        """scope="global" — общий уровень; scope="gen" — только для генерации ("auto" — снова как общий)."""
        settings = await self.repo.get(settings_key(username), {})
        if scope == "gen" and value == "auto":
            settings.pop("gen_level", None)
        else:
            level = cur.normalize_level(value)
            if level is None:
                await self.send(chat_id, "Уровень: A1, A2, B1 или B2. Например: /level B2")
                return
            settings["gen_level" if scope == "gen" else "level"] = level
        await self.repo.put(settings_key(username), settings)
        if scope != "gen":
            await self._reset_pool(username)
        if scope == "gen":
            text, keyboard = await self._generation_menu(username)
        else:
            level = await self._global_level(username)
            text, keyboard = cards.render_level_menu(level), cards.level_keyboard(level)
        if menu_message_id is not None:
            await self._edit_menu(chat_id, menu_message_id, text, keyboard)
        else:
            await self.send(chat_id, text, keyboard)

    async def _gen_command(self, username, chat_id, text):
        """/gen [count] [level] [topic in free words]"""
        args = text.split()[1:]
        if not args:
            text, keyboard = await self._generation_menu(username)
            await self.send(chat_id, text, keyboard)
            return
        count = 5
        if args and args[0].isdigit():
            count = int(args.pop(0))
        level = None
        if args and cur.normalize_level(args[0]):
            level = cur.normalize_level(args.pop(0))
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
        existing = await self._seen(username)
        try:
            new_cards = await generator.generate(
                self.ai, level, count, topic, existing, model=self.ai_model, grammar=await self._grammar_focus(username)
            )
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
        await self._remember_seen(username, [c["en"] for c in new_cards])
        inbox = await self.repo.get(inbox_key(username), [])
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

    async def _inbox_approve(self, username, chat_id, word_id, bare=False):
        """Move a reviewed card into the queue; `bare` drops the examples and synonyms."""
        inbox, word = await self._take_from_inbox(username, chat_id, word_id)
        if word is None:
            return
        if bare:
            word["examples"], word["synonyms"] = [], []
        queue = await self.repo.get(queue_key(username), [])
        known = await self.repo.get(known_key(username), [])
        if w.find_duplicate(word["en"], word["ru"], queue, known):
            await self.send(chat_id, f"«{escape(word['en'])}» уже есть в очереди или архиве — пропускаю.")
        else:
            for field in ("level", "topic", "source"):
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

    async def _level_command(self, username, chat_id, text):
        """/level B2 — сразу поставить уровень; /level или кнопка — меню уровней."""
        parts = text.split()
        if text.startswith("/") and len(parts) > 1:
            await self._set_level(username, chat_id, parts[1])
            return
        level = await self._global_level(username)
        await self.send(chat_id, cards.render_level_menu(level), cards.level_keyboard(level))
