"""Свои предложения со словом с карточки: текстом или голосовым, разбор от ИИ, ответ на карточку."""

import asyncio

from .. import cards, compose
from .. import words as w
from ..keys import compose_key, queue_key


class ComposeFlow:
    async def _compose_start(self, ctx, value):
        """Кнопка «Составить предложения» (sx:<id>:<stage>) под карточкой."""
        word_id, _, stage = value.partition(":")
        word = await self._pending_word(ctx.username, word_id, int(stage) if stage.isdigit() else None)
        if word is None:
            await self.send(ctx.chat_id, "Эта карточка уже неактуальна.")
            return
        if self.ai is None:
            await self.send(ctx.chat_id, "ИИ не настроен — ответь на карточку кнопками.")
            return
        sent = await self.send(ctx.chat_id, cards.render_compose_prompt(word), cards.compose_keyboard())
        await self.repo.put(compose_key(ctx.username), {
            "id": word["id"],
            "stage": word.get("stage", 0),
            "card_message_id": ctx.message_id,
            "prompt_message_id": ((sent or {}).get("result") or {}).get("message_id"),
        })

    async def _pending_word(self, username, word_id, stage):
        """Слово с карточки, которая ещё ждёт ответа; None — уже ответили или слово ушло дальше."""
        queue = await self.repo.get(queue_key(username), [])
        index = w.find_index(queue, word_id)
        if index < 0:
            return None
        word = queue[index]
        if stage is not None and word.get("stage", 0) != stage:
            return None
        return word

    async def _active_compose(self, username):
        """Идущее составление предложений; устаревшее (на карточку ответили кнопкой) сбрасывается."""
        state = await self.repo.get(compose_key(username), {})
        if not state:
            return None, None
        word = await self._pending_word(username, state["id"], state["stage"])
        if word is None:
            await self.repo.put(compose_key(username), {})
            return None, None
        return state, word

    async def _compose_cancel(self, ctx):
        await self.repo.put(compose_key(ctx.username), {})
        await self.send(ctx.chat_id, "Хорошо. На карточку можно ответить кнопками.")

    async def _compose_text(self, ctx, state, word, text, transcript=None):
        """Очередное сообщение с предложениями: разбор сразу, итог — на MAX_SENTENCES или по «Готово»."""
        items = state.get("items", [])
        found = compose.split_sentences(text)
        sentences = found[: compose.MAX_SENTENCES - len(items)]
        problem = compose.check_input(sentences)
        if problem:
            heard = f"🎙 Услышал: <i>{cards.escape(transcript)}</i>\n\n" if transcript else ""
            await self.send(ctx.chat_id, heard + problem, cards.compose_keyboard(done=bool(items)))
            return
        await self.tg.call("sendChatAction", {"chat_id": ctx.chat_id, "action": "typing"})
        try:
            result = await compose.review(
                self.ai, word, sentences, await self._global_level(ctx.username), spoken=transcript is not None,
                with_example=not state.get("example_shown"), model=self.ai_model,
            )
        except Exception as error:  # модель или сеть
            print(f"compose review failed: {error!r}")
            result = None
        if result is None:
            await self.send(ctx.chat_id, "😕 Не получилось проверить. Пришли предложение ещё раз чуть позже.",
                            cards.compose_keyboard(done=bool(items)))
            return
        fresh, example = result
        text = cards.render_compose_review(fresh, len(items) + 1, example, transcript, len(found) - len(sentences))
        items = items + fresh
        state = dict(state, items=items, example_shown=state.get("example_shown") or example is not None)
        if len(items) >= compose.MAX_SENTENCES:
            await self._compose_finish(ctx, state, word, text + "\n\n" + cards.render_compose_result(items))
            return
        # Кнопки прошлого разбора больше не нужны: «Готово» и «Отмена» — под последним.
        sent, _ = await asyncio.gather(
            self.send(ctx.chat_id, text + "\n\n" + cards.render_compose_progress(items),
                      cards.compose_keyboard(done=True)),
            self._drop_last_compose_buttons(ctx.chat_id, state),
        )
        state["last_message_id"] = ((sent or {}).get("result") or {}).get("message_id")
        await self.repo.put(compose_key(ctx.username), state)

    async def _drop_last_compose_buttons(self, chat_id, state):
        message_id = state.get("last_message_id") or state.get("prompt_message_id")
        if message_id is not None:
            await self._drop_buttons(chat_id, message_id)

    async def _compose_done(self, ctx):
        """Кнопка «Готово»."""
        state, word = await self._active_compose(ctx.username)
        if state is None:
            await self.send(ctx.chat_id, "Эта карточка уже неактуальна.")
        elif not state.get("items"):
            await self.send(ctx.chat_id, "Сначала пришли хотя бы одно предложение.", cards.compose_keyboard())
        else:
            await self._compose_finish(ctx, state, word, cards.render_compose_result(state["items"]))

    async def _compose_finish(self, ctx, state, word, text):
        """Итог и ответ на карточку: «Знаю», если слово верно хотя бы в MIN_GOOD предложениях."""
        await self.send(ctx.chat_id, text)
        ok = compose.passed(state["items"])
        answer = await self._apply_card_answer(ctx.username, "k" if ok else "n", word["id"], state["stage"])
        steps = [self.repo.put(compose_key(ctx.username), {}), *answer["writes"],
                 self._drop_last_compose_buttons(ctx.chat_id, state)]
        # Кнопки карточки больше не нужны: ответ уже засчитан.
        if state.get("card_message_id") is not None:
            steps.append(self._drop_buttons(ctx.chat_id, state["card_message_id"]))
        await asyncio.gather(*steps)
        await self._after_card_answer(ctx.username, ctx.chat_id, answer)

    async def _on_voice(self, ctx, voice):
        state, word = await self._active_compose(ctx.username)
        if state is None:
            await self.send(ctx.chat_id, f"Голосовые принимаю, когда составляешь предложения со словом: "
                                         f"кнопка «{cards.COMPOSE_BUTTON}» под карточкой.")
            return
        if (voice.get("duration") or 0) > compose.MAX_VOICE_SECONDS:
            await self.send(ctx.chat_id, f"Слишком длинное — уложись в {compose.MAX_VOICE_SECONDS} секунд.",
                            cards.compose_keyboard(done=bool(state.get("items"))))
            return
        await self.tg.call("sendChatAction", {"chat_id": ctx.chat_id, "action": "typing"})
        try:
            info = await self.tg.call("getFile", {"file_id": voice["file_id"]})
            audio = await self.tg.download(info["result"]["file_path"])
            transcript = await compose.transcribe(self.ai, audio)
        except Exception as error:
            print(f"voice failed: {error!r}")
            transcript = None
        if not transcript:
            await self.send(ctx.chat_id, "😕 Не удалось разобрать голосовое. Запиши ещё раз или напиши текстом.",
                            cards.compose_keyboard(done=bool(state.get("items"))))
            return
        await self._compose_text(ctx, state, word, transcript, transcript=transcript)


