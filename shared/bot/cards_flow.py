"""Карточки по расписанию: выбор слова, ответы «Знаю/Не знаю/В архив», досылка, статистика."""

import asyncio
from datetime import timedelta

from .. import cards, compose, srs
from .. import curriculum as cur
from .. import exercises as ex
from .. import practice as pr
from .. import words as w
from ..keys import (
    advice_key,
    known_key,
    mistake_log_key,
    practice_key,
    practice_log_key,
    queue_key,
    sched_key,
    session_key,
)
from ..storage import normalize_username

# Пропущенные слоты досылаются разом после ответа, но не больше стольких.
DEFAULT_CATCH_UP_LIMIT = 10
# Автогенерация слова за столько до слота, если очередь пуста.
AUTOGEN_LEAD = timedelta(minutes=20)


class CardsFlow:
    async def send_card(self, username, chat_id, now=None, force=False):
        """Карточку прямо сейчас: если есть неотвеченная, присылает её повторно.

        `force` (кнопка «Карточка сейчас») показывает ближайшее по сроку слово, даже если
        срок ещё не подошёл.
        """
        name = normalize_username(username)
        now = now or self._now()
        queue = [srs.prepare(x, now) for x in await self.repo.get(queue_key(name), [])]
        pending = next((x for x in queue if x.get("sent_at")), None)
        if pending is not None:
            await self._deliver(name, chat_id, [pending], queue, now)
            return pending
        sent = await self.send_cards(username, chat_id, 1, now=now)
        if sent or not force:
            return sent[0] if sent else None
        word = srs.earliest(queue)
        if word is None:
            return None
        await self._deliver(name, chat_id, [word], queue, now)
        return word

    async def send_cards(self, username, chat_id, count, now=None):
        """До `count` карточек: сначала новые слова в рамках дневной квоты, затем созревшие повторы."""
        name = normalize_username(username)
        now = now or self._now()
        today = self._today(now)
        key = queue_key(name)
        queue = [srs.prepare(x, now) for x in await self.repo.get(key, [])]
        day = await self._day_state(name, today)
        chosen = []
        for _ in range(count):
            word = srs.pick_next(queue, now, today, day["new"], self._new_quota(), exclude=chosen)
            if word is None:
                break
            if srs.is_new(word):
                day["new"] += 1
            chosen.append(word)
        if chosen:
            await self._deliver(name, chat_id, chosen, queue, now, today)
            await self.repo.put(sched_key(name), day)
        return chosen

    async def _deliver(self, username, chat_id, words, queue, now, today=None):
        today = today or (self._today(now))
        for word in words:
            await self.send(chat_id, cards.render_card(word), cards.card_keyboard(word))
            srs.note_shown(word, today)
            # Пока на карточку нет ответа, новые по расписанию не приходят.
            word["sent_at"] = w.now_iso()
        await self.repo.put(queue_key(username), queue)

    def _new_quota(self):
        return self.schedule.new_words_per_day if self.schedule else 6

    async def _day_state(self, username, today):
        """Счётчики за день: сколько новых слов отправлено и сколько слотов пропущено."""
        state = await self.repo.get(sched_key(username), {})
        if state.get("date") != today:
            state = {"date": today, "new": 0, "missed": state.get("missed", 0)}
        state.setdefault("new", 0)
        state.setdefault("missed", 0)
        return state

    async def _next_card(self, username, chat_id):
        """Кнопка «Карточка сейчас»: показывает слово, даже если его срок ещё не подошёл."""
        now = self._now()
        if not await self.repo.get(queue_key(username), []):
            await self.send(chat_id, "Очередь пуста — добавь новое слово или нажми «🤖 AI-Генерация».")
            return
        await self.send_card(username, chat_id, now=now, force=True)

    async def send_stats(self, username, chat_id, weekly=False):
        """Статистика; `weekly` — воскресный отчёт с новым ИИ-разбором слабых мест."""
        name = normalize_username(username)
        now = self._now()
        today = self._today(now)
        known = await self.repo.get(known_key(name), [])
        queue = [srs.prepare(x, now) for x in await self.repo.get(queue_key(name), [])]
        waiting = await self.repo.get(practice_key(name), [])
        day = await self._day_state(name, today)
        practice = pr.weekly_practice_stats(await self.repo.get(practice_log_key(name), []), now)
        mlog = await self.repo.get(mistake_log_key(name), [])
        weak = ex.weak_spots(mlog, now)
        exercise_stats = ex.weekly_stats(mlog, now)
        level = await self._global_level(name)
        progress = cur.progress(mlog)
        advice = (await self.repo.get(advice_key(name), {})).get("text")
        if weekly and self.ai is not None:
            try:
                fresh = await ex.advise(
                    self.ai, level, weak, practice["worst"], exercise_stats["counts"], progress, model=self.ai_model,
                )
            except Exception as error:
                print(f"advice failed: {error!r}")
                fresh = None
            if fresh:
                advice = fresh
                await self.repo.put(advice_key(name), {"at": now.isoformat(), "text": fresh})
        await self.send(
            chat_id,
            cards.render_stats(
                w.weekly_stats(known), srs.stats(queue, now, today), len(waiting), day["new"], self._new_quota()
            )
            + cards.render_practice_stats(practice)
            + cards.render_exercise_stats(exercise_stats, weak, advice)
            + cards.render_level_progress(progress, cur.working_level(mlog, level), level),
        )

    async def _is_blocked(self, username, queue):
        """New cards wait while a card is unanswered or the practice is running."""
        return w.is_awaiting_answer(queue) or bool(await self.repo.get(session_key(username), None))

    def _catch_up_limit(self):
        return self.schedule.cards_per_day if self.schedule else DEFAULT_CATCH_UP_LIMIT

    async def _catch_up(self, username, chat_id):
        """После ответа присылает карточки за слоты, пропущенные из-за ожидания."""
        now = self._now()
        today = self._today(now)
        day = await self._day_state(username, today)
        if not day["missed"]:
            return []
        queue = await self.repo.get(queue_key(username), [])
        if await self._is_blocked(username, queue):
            return []
        missed = day["missed"]
        day["missed"] = 0
        await self.repo.put(sched_key(username), day)
        return await self.send_cards(username, chat_id, missed, now=now)

    async def broadcast_cards(self, count_missed=False, now=None):
        """Карточка каждому, кто не ждёт ответа; занятому слот записывается в долг."""
        now = now or self._now()
        today = self._today(now)
        sent = []
        for user in await self._users_with_chat():
            name = normalize_username(user["username"])
            queue = await self.repo.get(queue_key(name), [])
            if await self._is_blocked(name, queue):
                if count_missed:
                    day = await self._day_state(name, today)
                    day["missed"] = min(day["missed"] + 1, self._catch_up_limit())
                    await self.repo.put(sched_key(name), day)
            elif await self.send_cards(user["username"], user["chat_id"], 1, now=now):
                sent.append(user["username"])
        return sent

    async def broadcast_stats(self):
        users = await self._users_with_chat()
        for user in users:
            await self.send_stats(user["username"], user["chat_id"])
        return [u["username"] for u in users]

    # Всплывающий текст после ответа на карточку.
    _NOTICES = {
        w.FLIPPED: "👍 Теперь в обратную сторону",
        w.REVIEWED: "✅ Отлично, вернусь к слову позже",
        w.TO_PRACTICE: "🎯 Интервалы пройдены — слово ждёт практики",
        w.ARCHIVED: "📥 В архиве — вот следующее",
        w.REQUEUED: "🔁 Слово вернётся сегодня же",
        w.NOT_FOUND: "Эта карточка уже неактуальна",
    }

    async def _answer_card(self, ctx, action, value):
        """«Знаю» (k), «Не знаю» (n), «В архив» (a); value = "<id>:<stage>" (у старых карточек без stage)."""
        word_id, _, stage = value.partition(":")
        if not word_id or (stage and not stage.isdigit()):
            await self.tg.call("answerCallbackQuery", {"callback_query_id": ctx.query_id})
            return
        answer = await self._apply_card_answer(ctx.username, action, word_id, int(stage) if stage else None)
        # Ответ на нажатие, снятие кнопок и запись в KV друг от друга не зависят — идут параллельно,
        # чтобы кнопка перестала «крутиться» как можно раньше.
        steps = [self.tg.call("answerCallbackQuery", {"callback_query_id": ctx.query_id,
                                                      "text": self._NOTICES[answer["result"]]})]
        if ctx.message_id is not None:
            steps.append(self._drop_buttons(ctx.chat_id, ctx.message_id))  # старую карточку нельзя ответить дважды
        await asyncio.gather(*steps, *answer["writes"])
        if action == "k" and answer["result"] in (w.REVIEWED, w.TO_PRACTICE) and self.ai is not None:
            # Совет по слову ждёт ИИ несколько секунд — в фоне, чтобы кнопка ответила сразу; следующие
            # карточки приходят после совета, чтобы он был про слово, на которое только что ответил.
            await self._background(self._advise_then_continue(ctx.username, ctx.chat_id, answer))
            return
        await self._after_card_answer(ctx.username, ctx.chat_id, answer)

    async def _advise_then_continue(self, username, chat_id, answer):
        """«Знаю» на полностью отвеченной карточке: совет, как ещё употребляют слово, затем досылка.
        Сразу после первой стороны нового слова совета нет — он выдал бы перевод до второй стороны."""
        word = answer["word"]
        try:
            usage = await compose.usage(self.ai, word, [], await self._global_level(username), model=self.ai_model)
        except Exception as error:
            print(f"word advice failed: {error!r}")
            usage = None
        if usage:
            await self.send(chat_id, cards.render_word_advice(word, usage))
        await self._after_card_answer(username, chat_id, answer)

    async def _apply_card_answer(self, username, action, word_id, stage):
        """Ответ на карточку в памяти: «k» — знаю, «n» — не знаю, «a» — в архив.
        Запись в KV — в `writes` (корутины), чтобы вызывающий мог выполнить её параллельно с другим."""
        now = self._now()
        today = self._today(now)
        queue = [srs.prepare(x, now) for x in await self.repo.get(queue_key(username), [])]
        waiting = known = None
        if action == "k":
            waiting = await self.repo.get(practice_key(username), [])
            result, word = w.answer_known(queue, waiting, word_id, self.schedule, now, today, stage=stage)
        elif action == "a":
            known = await self.repo.get(known_key(username), [])
            result, word = w.answer_archive(queue, known, word_id, now=now.isoformat(), stage=stage)
        else:
            result, word = w.answer_unknown(queue, word_id, self.schedule, now, today, stage=stage)
        writes = []
        if result != w.NOT_FOUND:
            writes.append(self.repo.put(queue_key(username), queue))
            if result == w.TO_PRACTICE:
                writes.append(self.repo.put(practice_key(username), waiting))
            elif result == w.ARCHIVED:
                writes.append(self.repo.put(known_key(username), known))
        return {"result": result, "word": word, "queue": queue, "now": now, "today": today, "writes": writes}

    async def _after_card_answer(self, username, chat_id, answer):
        if answer["result"] == w.FLIPPED:
            # Вторая сторона нового слова идёт сразу, в этом же слоте.
            await self._deliver(username, chat_id, [answer["word"]], answer["queue"], answer["now"], answer["today"])
        elif answer["result"] != w.NOT_FOUND:
            sent = await self._catch_up(username, chat_id)
            if answer["result"] == w.ARCHIVED and not sent:
                # «В архив» — слово и так знаю: сразу следующее из очереди, не дожидаясь расписания.
                await self._next_card(username, chat_id)

    async def _cron_cards(self, now):
        """Слот расписания — карточки; за 20 минут до слота — слово от ИИ, если очередь пуста."""
        if self.schedule.is_slot(now):
            return await self.broadcast_cards(count_missed=True, now=now)
        if self.ai is not None and self.schedule.is_slot(now + AUTOGEN_LEAD):
            return await self.autogenerate(now)
        return []

    async def _cron_report(self, now):
        if not self.schedule.is_report_time(now):
            return []
        users = await self._users_with_chat()
        for user in users:
            await self.send_stats(user["username"], user["chat_id"], weekly=True)
        return [u["username"] for u in users]
