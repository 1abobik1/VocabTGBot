"""Вечерняя практика: написать слова в обе стороны."""


from .. import cards, srs
from .. import practice as pr
from .. import words as w
from ..cards import escape
from ..keys import known_key, practice_key, practice_log_key, queue_key, session_key
from ..storage import normalize_username

# Сколько записей практики хранить (больше нужного на неделю — с запасом).
PRACTICE_LOG_LIMIT = 400


class PracticeFlow:
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
        # Практика идёт небольшими порциями, чтобы утренняя сессия была на пару минут.
        batch = waiting[: self.schedule.practice_batch] if self.schedule else waiting
        session = {"ids": [x["id"] for x in batch], "round": pr.RU_EN, "results": {}}
        await self.repo.put(session_key(username), session)
        await self._send_practice_round(chat_id, batch, pr.RU_EN)
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
        for (word, _, verdict, _extras) in graded:
            session["results"].setdefault(word["id"], {})[round_name] = verdict
        feedback = [
            (word, answer, verdict, exp, extras)
            for (word, answer, verdict, extras), exp in zip(graded, expected)
        ]
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
        queue = [srs.prepare(x, self._now()) for x in await self.repo.get(queue_key(username), [])]
        known = await self.repo.get(known_key(username), [])
        waiting = await self.repo.get(practice_key(username), [])
        by_id = {x["id"]: x for x in words}
        log = await self.repo.get(practice_log_key(username), [])
        log += [
            {"at": self._now().isoformat(), "en": by_id[word_id]["en"], **verdicts}
            for word_id, verdicts in session["results"].items()
            if word_id in by_id
        ]
        await self.repo.put(practice_log_key(username), log[-PRACTICE_LOG_LIMIT:])
        outcome = pr.apply_results(queue, known, waiting, session["results"], self.schedule, self._now())
        await self.repo.put(queue_key(username), queue)
        await self.repo.put(known_key(username), known)
        await self.repo.put(practice_key(username), waiting)
        await self.repo.put(session_key(username), {})
        cheer = cards.practice_cheer(len(outcome[pr.OK]))
        await self.send(chat_id, cards.render_practice_summary(outcome, cheer), cards.main_keyboard())
        await self._catch_up(username, chat_id)

    async def _practice_command(self, username, chat_id):
        if not await self.start_practice(username, chat_id):
            await self.send(chat_id, "Для практики пока нет слов: они появятся после «Знаю» в обе стороны.")

    async def _skip_sentences(self, username, chat_id):
        """Кнопка «Пропустить» на необязательном шаге с предложениями."""
        session = await self.repo.get(session_key(username), None)
        if session and session.get("round") == "sentences":
            await self._finish_practice(username, chat_id, session)

    async def _cron_practice(self, now):
        if not self.schedule.is_practice_time(now):
            return []
        started = []
        for user in await self._users_with_chat():
            if await self.start_practice(normalize_username(user["username"]), user["chat_id"], quiet=True):
                started.append(user["username"])
        return started
