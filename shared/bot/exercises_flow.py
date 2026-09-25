"""Упражнения: пул, сессия, ответы, журнал ошибок, темы."""

import asyncio
from datetime import timedelta

from .. import cards, srs
from .. import curriculum as cur
from .. import exercises as ex
from .. import practice as pr
from .. import words as w
from ..keys import (
    exercise_pool_key,
    exercise_session_key,
    known_key,
    mistake_log_key,
    practice_key,
    queue_key,
    settings_key,
)
from ..storage import normalize_username
from ..text import strip_extras

# За сколько до EXERCISE_TIME пополнять пустой пул (подстраховка к фоновой генерации).
POOL_LEAD = timedelta(minutes=20)


class ExercisesFlow:
    async def _vocab_words(self, username, limit=20):
        """Слова пользователя для заданий «вставь слово»: из очереди, практики и архива."""
        words = [
            strip_extras(x["en"])
            for key in (queue_key, practice_key, known_key)
            for x in await self.repo.get(key(username), [])
        ]
        words = [x for x in dict.fromkeys(words) if x and len(x) > 2]
        return words[-limit:]

    async def _exercise_params(self, username):
        settings = await self.repo.get(settings_key(username), {})
        vocab = await self._vocab_words(username)
        groups = cur.enabled_groups(settings, has_vocab=len(vocab) >= 3)
        return await self._global_level(username), groups, vocab

    async def _generate_exercises(self, username):
        level, groups, vocab = await self._exercise_params(username)
        try:
            return await ex.generate(
                self.ai, level, ex.BATCH, groups, vocab,
                await self.repo.get(mistake_log_key(username), []), self._now(), model=self.ai_model,
            )
        except Exception as error:
            print(f"exercise generation failed: {error!r}")
            return []

    async def refill_pool(self, username):
        """Готовит следующую пачку заранее. Вызывается в фоне после каждой пройденной пачки,
        поэтому учитывает только что сделанные ошибки."""
        if self.ai is None or (await self.repo.get(exercise_pool_key(username), {})).get("items"):
            return False
        level, groups, _ = await self._exercise_params(username)
        items = await self._generate_exercises(username)
        if items:
            await self.repo.put(
                exercise_pool_key(username),
                {"items": items, "level": level, "groups": groups, "at": self._now().isoformat()},
            )
        return bool(items)

    async def _take_pool(self, username):
        """Пачка из запаса, если она подходит под текущие уровень и темы."""
        pool = await self.repo.get(exercise_pool_key(username), {})
        if not pool.get("items"):
            return None
        level, groups, _ = await self._exercise_params(username)
        await self.repo.put(exercise_pool_key(username), {})
        if pool.get("level") != level or set(pool.get("groups", [])) != set(groups):
            return None  # уровень или темы поменялись — запас устарел
        return [ex.normalize(item) for item in pool["items"]]

    async def _reset_pool(self, username):
        """После смены уровня или тем старый запас не годится — готовим новый в фоне."""
        await self.repo.put(exercise_pool_key(username), {})
        await self._background(self.refill_pool(username))

    async def start_exercises(self, username, chat_id, auto=False):
        """Упражнения на сегодня. Незаконченные не пересоздаются, а присылаются снова."""
        session = await self._load_session(username)
        if session.get("items"):
            if auto:
                await self.send(chat_id, "⏰ Упражнения ещё ждут — продолжим:")
            await self._send_exercise(chat_id, session)
            return True
        if self.ai is None:
            if not auto:
                await self.send(chat_id, "Упражнения недоступны: Workers AI не подключён.")
            return False
        now = self._now()
        items = await self._take_pool(username)
        if items is None:
            # Запаса нет (первый раз или фоновая генерация ещё идёт) — генерируем сейчас.
            if not auto:
                await self.send(chat_id, "⏳ Готовлю упражнения…")
            items = await self._generate_exercises(username)
        if not items:
            if not auto:
                await self.send(chat_id, "😕 Не получилось подготовить упражнения. Попробуй чуть позже.")
            return False
        session = {"date": now.isoformat(), "items": items, "i": 0, "results": []}
        await self.repo.put(exercise_session_key(username), session)
        await self._send_exercise(chat_id, session)
        return True

    async def _load_session(self, username):
        """Текущие упражнения; задания, созданные до программы, приводятся к новому виду."""
        session = await self.repo.get(exercise_session_key(username), {})
        if session.get("items"):
            session["items"] = [ex.normalize(item) for item in session["items"]]
        return session

    async def _send_exercise(self, chat_id, session):
        index = session["i"]
        exercise = session["items"][index]
        await self.send(
            chat_id,
            cards.render_exercise(exercise, index, len(session["items"])),
            cards.exercise_keyboard(exercise, index),
        )

    async def _typed_exercise(self, username):
        """(сессия, номер), если сейчас ждём ответ текстом, иначе None."""
        session = await self._load_session(username)
        if not session.get("items"):
            return None
        exercise = session["items"][session["i"]]
        if ex.uses_buttons(exercise):
            return None  # это задание с кнопками
        return session, session["i"]

    async def _exercise_callback(self, username, chat_id, data, message_id):
        action, _, rest = data.partition(":")
        if action == "xt":
            await self._toggle_topic(username, chat_id, rest, message_id)
            return
        session = await self._load_session(username)
        index, _, option = rest.partition(":")
        if not session.get("items") or not index.isdigit() or int(index) != session["i"]:
            await self.send(chat_id, "Это упражнение уже неактуально.")
            return
        exercise = session["items"][session["i"]]
        if action == "xd":
            handle = self._record_exercise(username, chat_id, session, "", ex.DISPUTED)
        elif action == "xn":
            handle = self._exercise_answer(username, chat_id, session, session["i"], "", dont_know=True)
        elif option.isdigit() and int(option) < len(exercise.get("options", [])):
            handle = self._exercise_answer(username, chat_id, session, session["i"], exercise["options"][int(option)])
        else:
            return
        # Кнопки старого задания убираются параллельно с разбором ответа.
        drop = self._drop_buttons(chat_id, message_id) if message_id is not None else asyncio.sleep(0)
        await asyncio.gather(drop, handle)

    async def _exercise_answer(self, username, chat_id, session, index, given, dont_know=False):
        exercise = session["items"][index]
        verdict = pr.WRONG if dont_know else ex.grade(exercise, given)
        await self.send(chat_id, cards.render_exercise_feedback(exercise, given, verdict))
        await self._record_exercise(username, chat_id, session, given, verdict)

    async def _record_exercise(self, username, chat_id, session, given, verdict):
        session["results"].append({"verdict": verdict, "given": given})
        if verdict == ex.DISPUTED:
            await self.send(chat_id, "🤔 Убрал это упражнение, в статистику оно не пойдёт.")
        session["i"] += 1
        if session["i"] < len(session["items"]):
            await self.repo.put(exercise_session_key(username), session)
            await self._send_exercise(chat_id, session)
        else:
            await self._finish_exercises(username, chat_id, session)

    async def _finish_exercises(self, username, chat_id, session):
        now = self._now()
        log = await self.repo.get(mistake_log_key(username), [])
        rollback = []
        for exercise, result in zip(session["items"], session["results"]):
            if result["verdict"] == ex.DISPUTED:
                continue
            log.append({
                "at": now.isoformat(), "rule": exercise["rule"], "verdict": result["verdict"],
                "sentence": exercise["sentence"], "answer": exercise["answer"], "given": result["given"],
            })
            if exercise["rule"] == cur.VOCAB.id and result["verdict"] == pr.WRONG:
                rollback.append(exercise["word"])
        await self.repo.put(mistake_log_key(username), log[-ex.MISTAKE_LOG_LIMIT:])
        if rollback:
            await self._roll_back_words(username, rollback, now)
        await self.repo.put(exercise_session_key(username), {})
        await self.send(chat_id, cards.render_exercise_summary(session["results"], ex.weak_spots(log, now)))
        # Следующая пачка готовится в фоне уже с учётом ошибок из этой.
        await self._background(self.refill_pool(username))

    async def _roll_back_words(self, username, words, now):
        """Ошибка в «вставь слово» откатывает слово на один интервал и показывает его завтра."""
        targets = {w.compact(x) for x in words}
        queue = [srs.prepare(x, now) for x in await self.repo.get(queue_key(username), [])]
        changed = False
        for word in queue:
            if w.compact(strip_extras(word["en"])) in targets:
                word["box"] = max(0, word.get("box", 0) - 1)
                if self.schedule is not None:
                    srs.postpone_to_tomorrow(word, self.schedule, now)
                changed = True
        if changed:
            await self.repo.put(queue_key(username), queue)

    async def _toggle_topic(self, username, chat_id, kind, message_id):
        settings = await self.repo.get(settings_key(username), {})
        chosen = [g for g in (cur.LEGACY_GROUPS.get(t, t) for t in settings.get("ex_types") or []) if g in cur.GROUPS]
        if kind == "auto":
            chosen = []
        elif kind in cur.GROUPS:
            current = chosen or list(cur.GROUPS)
            chosen = [t for t in current if t != kind] if kind in current else current + [kind]
            if set(chosen) == set(cur.GROUPS):
                chosen = []  # всё включено — снова выбор ИИ
            if not chosen:
                chosen = [kind]  # нельзя выключить все темы разом
        settings["ex_types"] = chosen
        await self.repo.put(settings_key(username), settings)
        await self._reset_pool(username)
        if message_id is not None:
            await self._edit_menu(chat_id, message_id, cards.render_topics_menu(chosen), cards.topics_keyboard(chosen))

    async def _show_topics(self, username, chat_id):
        chosen = (await self.repo.get(settings_key(username), {})).get("ex_types") or []
        await self.send(chat_id, cards.render_topics_menu(chosen), cards.topics_keyboard(chosen))

    async def _cron_exercise_pool(self, now):
        """Страховка: за 20 минут до упражнений пополнить пустой запас."""
        if self.ai is None or not self.schedule.is_exercise_time(now + POOL_LEAD):
            return []
        for user in await self._users_with_chat():
            name = normalize_username(user["username"])
            if not (await self.repo.get(exercise_session_key(name), {})).get("items"):
                await self.refill_pool(name)
        return []

    async def _cron_exercises(self, now):
        if not self.schedule.is_exercise_time(now):
            return []
        started = []
        for user in await self._users_with_chat():
            if await self.start_exercises(normalize_username(user["username"]), user["chat_id"], auto=True):
                started.append(user["username"])
        return started
