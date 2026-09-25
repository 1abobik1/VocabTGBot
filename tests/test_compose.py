import asyncio
import base64
import unittest
from datetime import datetime, timedelta, timezone

from shared import cards, compose
from shared.bot import Bot
from shared.schedule import Schedule
from tests.test_bot import CHAT, OWNER, FakeStore, FakeTelegram, use_clock
from tests.test_generator import FakeAI

MSK = timezone(timedelta(hours=3))
NOW = datetime(2026, 9, 24, 12, 0, tzinfo=MSK)
KEY = OWNER.lower()
WORD = {"en": "chill", "ru": "отдыхать"}


def review(*verdicts, example=("Let's just chill tonight.", "Давай просто отдохнём сегодня.")):
    sentences = [{"sentence": f"s{i}", "verdict": v, "corrected": f"Fixed sentence {i}.",
                  "comment_ru": "Пропущен артикль." if v != "correct" else ""} for i, v in enumerate(verdicts)]
    en, ru = example or ("", "")
    return {"response": {"sentences": sentences, "example_en": en, "example_ru": ru}}


class ComposeLogicTest(unittest.TestCase):
    def test_split_sentences(self):
        text = "1. I chill at home. He chills a lot!\n2) we chilled yesterday\n- ok\n• Let's chill tonight?"
        self.assertEqual(compose.split_sentences(text),
                         ["I chill at home.", "He chills a lot!", "we chilled yesterday", "Let's chill tonight?"])

    def test_check_input(self):
        self.assertIsNone(compose.check_input(["I chill."]))                         # одного предложения хватает
        self.assertIn("на английском", compose.check_input(["I chill.", "Я отдыхаю дома."]))
        self.assertIn("Не нашёл предложения", compose.check_input([]))

    def test_request(self):
        prompt = compose.build_request({"en": "chill [tʃɪl]", "ru": "отдыхать"}, ["I chill.", "We chill."], "B1",
                                       spoken=True, with_example=True)
        system, user = prompt["messages"][0]["content"], prompt["messages"][1]["content"]
        self.assertIn("the word or phrase 'chill' (Russian: 'отдыхать')", system)
        self.assertIn("CEFR level B1", system)
        self.assertIn("transcribed from the learner's speech", system)
        self.assertIn("'example_en': one short, common sentence", system)
        self.assertEqual(user, "1. I chill.\n2. We chill.")
        plain = compose.build_request(WORD, ["a b"], "B1")["messages"][0]["content"]
        self.assertNotIn("speech", plain)
        self.assertIn("Leave 'example_en' and 'example_ru' empty.", plain)

    def test_parse_and_verdict(self):
        items, example = compose.parse(review("correct", "grammar", "wrong"), ["a b", "c d", "e f"])
        self.assertEqual([x["sentence"] for x in items], ["a b", "c d", "e f"])  # предложения — свои, не из ответа
        self.assertEqual(items[0]["comment"], "")
        self.assertEqual(example, {"en": "Let's just chill tonight.", "ru": "Давай просто отдохнём сегодня."})
        self.assertEqual(compose.good_count(items), 2)
        self.assertTrue(compose.passed(items))
        self.assertFalse(compose.passed(compose.parse(review("correct", "wrong"), ["a b", "c d"])[0]))
        self.assertIsNone(compose.parse(review("correct"), ["a b", "c d"]))           # разобрано не всё
        self.assertIsNone(compose.parse(review("correct", "maybe"), ["a b", "c d"]))  # непонятный вердикт
        bad = review("correct", example=("Давай отдохнём.", "Давай отдохнём."))
        bad["response"]["sentences"][0]["corrected"] = "Я отдыхаю."
        items, example = compose.parse(bad, ["a b"])
        self.assertEqual((items[0]["corrected"], example), ("a b", None))
        self.assertIsNone(compose.parse(review("correct", example=None), ["a b"])[1])

    def test_punctuation_only_fix_is_not_shown(self):
        item = {"sentence": "my car is reliable it never breaks", "corrected": "My car is reliable; it never breaks.",
                "verdict": "correct", "comment": ""}
        self.assertFalse(compose.changed(item))
        self.assertNotIn("→", cards.render_compose_review([item, dict(item)]))
        self.assertTrue(compose.changed(dict(item, corrected="My car is reliable; it never breaks down.")))

    def test_transcribe_request(self):
        payload = compose.transcribe_request(b"OggS")
        self.assertEqual(base64.b64decode(payload["audio"]), b"OggS")
        self.assertEqual(payload["language"], "en")
        self.assertNotIn("initial_prompt", payload)  # ломает распознавание, см. compose.transcribe_request
        self.assertEqual(compose.parse_transcript({"text": "  I chill.  We chill. "}), "I chill. We chill.")
        self.assertEqual(compose.parse_transcript({"error": 1}), "")


class ComposeFlowTest(unittest.TestCase):
    def setUp(self):
        self.store, self.tg, self.ai = FakeStore(), FakeTelegram(), FakeAI()
        self.bot = Bot(self.store, self.tg, owner=OWNER, schedule=Schedule(), ai=self.ai)
        self.clock = use_clock(self, self.bot, NOW)
        self.n = 0
        self.msg("/start")
        self.msg("chill - отдыхать\nI chill on Sundays. - Я отдыхаю по воскресеньям.")
        self.msg(cards.NEXT_BUTTON)
        self.card_message = len(self.tg.calls)  # FakeTelegram отдаёт номер вызова как message_id

    def msg(self, text=None, voice=None):
        self.n += 1
        message = {"message_id": self.n, "from": {"id": CHAT, "username": OWNER}, "chat": {"id": CHAT, "type": "private"}}
        if voice:
            message["voice"] = voice
        else:
            message["text"] = text
        asyncio.run(self.bot.handle_update({"message": message}))

    def press(self, data, message_id=None):
        self.n += 1
        asyncio.run(self.bot.handle_update({"callback_query": {
            "id": f"q{self.n}", "from": {"id": CHAT, "username": OWNER},
            "message": {"message_id": message_id or self.card_message, "chat": {"id": CHAT, "type": "private"}},
            "data": data}}))

    def word(self):
        return self.store.json(f"queue:{KEY}")[0]

    def start(self):
        word = self.word()
        self.press(f"sx:{word['id']}:{word['stage']}")

    def test_card_has_the_compose_button(self):
        keyboard = self.tg.sent()[-1]["reply_markup"]["inline_keyboard"]
        self.assertEqual(keyboard[1][0]["text"], cards.COMPOSE_BUTTON)
        self.assertEqual(keyboard[1][0]["callback_data"], f"sx:{self.word()['id']}:0")

    def test_prompt_hides_the_english_word_on_the_first_side(self):
        self.start()
        self.assertIn("со словом «Отдыхать» — <tg-spoiler>chill</tg-spoiler>", self.tg.last_text())
        self.assertEqual(self.store.json(f"compose:{KEY}")["id"], self.word()["id"])

    def buttons(self):
        return [b["callback_data"] for row in self.tg.sent()[-1]["reply_markup"]["inline_keyboard"] for b in row]

    def test_sentences_one_by_one_then_done(self):
        self.start()
        prompt_message = len(self.tg.calls)
        self.ai.responses.append(review("grammar"))
        self.msg("She chilling with friends.")
        self.assertIn("'example_en': one short", self.ai.calls[-1][1]["messages"][0]["content"])
        text = self.tg.last_text()
        self.assertIn("1. 🟡 She chilling with friends.\n→ <b>Fixed sentence 0.</b>\n<i>Пропущен артикль.</i>", text)
        self.assertIn("📌 Так часто говорят: <b>Let's just chill tonight.</b>\nДавай просто отдохнём сегодня.", text)
        self.assertIn("Верно: 1 из 1. Нужно верных: 2. Пришли ещё предложение текстом или голосом (осталось 3).", text)
        self.assertEqual(self.buttons(), ["sd", "sc"])
        self.assertIn("sent_at", self.word())                                        # карточка ещё ждёт ответа
        dropped = [p["message_id"] for m, p in self.tg.calls if m == "editMessageReplyMarkup"]
        self.assertIn(prompt_message, dropped)                                      # «Отмена» у задания убрана

        review_message = len(self.tg.calls) - 1
        self.ai.responses += [{"text": "We chill on Fridays."}, review("correct", example=None)]
        self.msg(voice={"file_id": "F1", "duration": 4})
        self.assertIn("Leave 'example_en' and 'example_ru' empty.", self.ai.calls[-1][1]["messages"][0]["content"])
        text = self.tg.last_text()
        self.assertIn("🎙 Услышал: <i>We chill on Fridays.</i>\n\n2. ✅ We chill on Fridays.", text)
        self.assertNotIn("📌", text)                                                # частая фраза — один раз
        self.assertIn("Верно: 2 из 2. Уже засчитывается", text)
        self.assertEqual(self.word()["stage"], 0)                                  # пока не нажал «Готово»

        self.press("sd", message_id=review_message + 2)
        self.assertIn("🏁 Верно 2 из 2 — засчитано как «Знаю».", self.tg.sent()[-2]["text"])
        self.assertEqual(self.word()["stage"], 1)                                  # как «Знаю» на новом слове
        self.assertTrue(self.tg.last_text().startswith("Chill"))
        self.assertEqual(self.store.json(f"compose:{KEY}"), {})
        dropped = [p["message_id"] for m, p in self.tg.calls if m == "editMessageReplyMarkup"]
        self.assertIn(self.card_message, dropped)                                   # второй раз не ответить

    def test_fourth_sentence_finishes_and_extra_ones_are_skipped(self):
        self.start()
        self.ai.responses.append(review("correct", "wrong", "wrong", "wrong"))
        self.msg("I chill. You chill. The soup is chill. It's chill outside. We chill a lot.")
        self.assertEqual(self.ai.calls[-1][1]["messages"][1]["content"].count("\n"), 3)  # только 4 предложения
        text = self.tg.sent()[-1]["text"]
        self.assertIn("Лишние предложения (1) не учёл: максимум 4.", text)
        self.assertIn("🏁 Верно 1 из 4 — меньше 2, считаю как «Не знаю»", text)
        word = self.word()
        self.assertEqual((word["stage"], word["lapses_in_a_row"]), (0, 1))
        self.assertNotIn("sent_at", word)

    def test_done_before_any_sentence(self):
        self.start()
        self.press("sd")
        self.assertIn("Сначала пришли хотя бы одно предложение", self.tg.last_text())
        self.assertIn("sent_at", self.word())

    def test_ai_failure_keeps_waiting(self):
        self.start()
        self.ai.responses += [RuntimeError("down")]
        self.msg("I chill at home. We chill a lot.")
        self.assertIn("Не получилось проверить", self.tg.last_text())
        self.assertTrue(self.store.json(f"compose:{KEY}"))
        self.assertIn("sent_at", self.word())

    def test_voice_is_downloaded_and_transcribed(self):
        self.start()
        self.ai.responses += [{"text": "I chill at home."}, review("correct")]
        self.msg(voice={"file_id": "F1", "duration": 9})
        whisper_model, whisper = self.ai.calls[0]
        self.assertEqual(whisper_model, compose.WHISPER_MODEL)
        self.assertEqual(base64.b64decode(whisper["audio"]), b"OggS-voice")
        self.assertIn(("download", "voice/file_1.oga"), self.tg.calls)
        self.assertIn("transcribed from the learner's speech", self.ai.calls[1][1]["messages"][0]["content"])

    def test_long_or_unexpected_voice(self):
        self.msg(voice={"file_id": "F1", "duration": 5})
        self.assertIn(f"кнопка «{cards.COMPOSE_BUTTON}»", self.tg.last_text())
        self.start()
        self.msg(voice={"file_id": "F1", "duration": 200})
        self.assertIn("уложись в 60 секунд", self.tg.last_text())
        self.assertEqual(self.ai.calls, [])

    def test_cancel_and_buttons_still_work(self):
        self.start()
        self.press("sc", message_id=999)
        self.assertEqual(self.store.json(f"compose:{KEY}"), {})
        self.start()
        word = self.word()
        self.press(f"k:{word['id']}:0")  # ответил обычной кнопкой
        self.msg("cat - кот\nThe cat sleeps. - Кот спит.")  # текст снова добавляет слова
        self.assertEqual(self.store.json(f"compose:{KEY}"), {})
        self.assertIn("cat", [x["en"] for x in self.store.json(f"queue:{KEY}")])
        self.assertEqual(self.ai.calls, [])

    def test_stale_compose_button(self):
        word = self.word()
        self.press(f"k:{word['id']}:0")
        self.press(f"sx:{word['id']}:0")  # кнопка со старой копии карточки
        self.assertIn("уже неактуальна", self.tg.last_text())


if __name__ == "__main__":
    unittest.main()
