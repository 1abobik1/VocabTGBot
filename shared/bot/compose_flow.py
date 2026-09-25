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
        sentences = compose.split_sentences(text)[: compose.MAX_SENTENCES]
        problem = compose.check_input(sentences)
        if problem:
            heard = f"🎙 Услышал: <i>{cards.escape(transcript)}</i>\n\n" if transcript else ""
            await self.send(ctx.chat_id, heard + problem + " Или «Отмена».", cards.compose_keyboard())
            return
        await self.tg.call("sendChatAction", {"chat_id": ctx.chat_id, "action": "typing"})
        try:
            result = await compose.review(self.ai, word, sentences, await self._global_level(ctx.username),
                                          spoken=transcript is not None, model=self.ai_model)
        except Exception as error:  # модель или сеть
            print(f"compose review failed: {error!r}")
            result = None
        if result is None:
            await self.send(ctx.chat_id, "😕 Не получилось проверить. Пришли предложения ещё раз чуть позже.",
                            cards.compose_keyboard())
            return
        items, tip = result
        ok = compose.passed(items)
        await self.send(ctx.chat_id, cards.render_compose_review(word, items, tip, ok, transcript))
        answer = await self._apply_card_answer(ctx.username, "k" if ok else "n", word["id"], state["stage"])
        steps = [self.repo.put(compose_key(ctx.username), {}), *answer["writes"]]
        # Кнопки карточки и «Отмена» больше не нужны: ответ уже засчитан.
        for message_id in (state.get("card_message_id"), state.get("prompt_message_id")):
            if message_id is not None:
                steps.append(self._drop_buttons(ctx.chat_id, message_id))
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
                            cards.compose_keyboard())
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
                            cards.compose_keyboard())
            return
        await self._compose_text(ctx, state, word, transcript, transcript=transcript)


