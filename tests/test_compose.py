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


def review(*verdicts, tip="Ещё говорят chill out — расслабиться."):
    sentences = [{"sentence": f"s{i}", "verdict": v, "corrected": f"Fixed sentence {i}.",
                  "comment_ru": "Пропущен артикль." if v != "correct" else ""} for i, v in enumerate(verdicts)]
    return {"response": {"sentences": sentences, "tip_ru": tip}}


class ComposeLogicTest(unittest.TestCase):
    def test_split_sentences(self):
        text = "1. I chill at home. He chills a lot!\n2) we chilled yesterday\n- ok\n• Let's chill tonight?"
        self.assertEqual(compose.split_sentences(text),
                         ["I chill at home.", "He chills a lot!", "we chilled yesterday", "Let's chill tonight?"])

    def test_check_input(self):
        self.assertIn("хотя бы 2", compose.check_input(["I chill at home."]))
        self.assertIn("на английском", compose.check_input(["I chill.", "Я отдыхаю дома."]))
        self.assertIsNone(compose.check_input(["I chill.", "We chill."]))

    def test_request(self):
        prompt = compose.build_request({"en": "chill [tʃɪl]", "ru": "отдыхать"}, ["I chill.", "We chill."], "B1",
                                       spoken=True)
        system, user = prompt["messages"][0]["content"], prompt["messages"][1]["content"]
        self.assertIn("the word or phrase 'chill' (Russian: 'отдыхать')", system)
        self.assertIn("CEFR level B1", system)
        self.assertIn("transcribed from the learner's speech", system)
        self.assertEqual(user, "1. I chill.\n2. We chill.")
        self.assertNotIn("speech", compose.build_request(WORD, ["a b", "c d"], "B1")["messages"][0]["content"])

    def test_parse_and_verdict(self):
        items, tip = compose.parse(review("correct", "grammar", "wrong"), ["a b", "c d", "e f"])
        self.assertEqual([x["sentence"] for x in items], ["a b", "c d", "e f"])  # предложения — свои, не из ответа
        self.assertEqual(items[0]["comment"], "")
        self.assertEqual(tip, "Ещё говорят chill out — расслабиться.")
        self.assertTrue(compose.passed(items))
        self.assertFalse(compose.passed(compose.parse(review("correct", "wrong"), ["a b", "c d"])[0]))
        self.assertIsNone(compose.parse(review("correct"), ["a b", "c d"]))           # разобрано не всё
        self.assertIsNone(compose.parse(review("correct", "maybe"), ["a b", "c d"]))  # непонятный вердикт
        bad = review("correct", "correct")
        bad["response"]["sentences"][0]["corrected"] = "Я отдыхаю."
        bad["response"]["tip_ru"] = "Use chill out."
        items, tip = compose.parse(bad, ["a b", "c d"])
        self.assertEqual((items[0]["corrected"], tip), ("a b", ""))

    def test_punctuation_only_fix_is_not_shown(self):
        item = {"sentence": "my car is reliable it never breaks", "corrected": "My car is reliable; it never breaks.",
                "verdict": "correct", "comment": ""}
        self.assertFalse(compose.changed(item))
        self.assertNotIn("→", cards.render_compose_review(WORD, [item, dict(item)], "", True))
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

    def test_good_sentences_count_as_known(self):
        self.start()
        self.ai.responses.append(review("correct", "grammar", "wrong"))
        self.msg("I chill at home on Sundays.\nShe chilling with friends.\nThe soup is chill.")
        user = self.ai.calls[-1][1]["messages"][1]["content"]
        self.assertEqual(user, "1. I chill at home on Sundays.\n2. She chilling with friends.\n3. The soup is chill.")
        report = self.tg.sent()[-2]["text"]
        self.assertIn("1. ✅ I chill at home on Sundays.", report)
        self.assertIn("2. 🟡 She chilling with friends.\n→ <b>Fixed sentence 1.</b>\n<i>Пропущен артикль.</i>", report)
        self.assertIn("💡 Ещё говорят chill out", report)
        self.assertIn("Засчитано как «Знаю».", report)
        # как после «Знаю» на новом слове: сразу вторая сторона
        self.assertEqual(self.word()["stage"], 1)
        self.assertTrue(self.tg.last_text().startswith("Chill"))
        self.assertEqual(self.store.json(f"compose:{KEY}"), {})
        dropped = [p["message_id"] for m, p in self.tg.calls if m == "editMessageReplyMarkup"]
        self.assertIn(self.card_message, dropped)  # кнопки карточки убраны — второй раз не ответить

    def test_weak_sentences_count_as_unknown(self):
        self.start()
        self.ai.responses.append(review("correct", "wrong"))
        self.msg("I chill at home. The soup is chill.")
        self.assertIn("считаю как «Не знаю»", self.tg.last_text())
        word = self.word()
        self.assertEqual((word["stage"], word["lapses_in_a_row"]), (0, 1))
        self.assertNotIn("sent_at", word)

    def test_one_sentence_is_not_enough(self):
        self.start()
        self.msg("I chill.")
        self.assertIn("хотя бы 2 предложения", self.tg.last_text())
        self.assertEqual(self.ai.calls, [])
        self.assertTrue(self.store.json(f"compose:{KEY}"))

    def test_ai_failure_keeps_waiting(self):
        self.start()
        self.ai.responses += [RuntimeError("down")]
        self.msg("I chill at home. We chill a lot.")
        self.assertIn("Не получилось проверить", self.tg.last_text())
        self.assertTrue(self.store.json(f"compose:{KEY}"))
        self.assertIn("sent_at", self.word())

    def test_voice_is_transcribed_and_reviewed(self):
        self.start()
        self.ai.responses += [{"text": "I chill at home. We chill on Fridays."}, review("correct", "correct")]
        self.msg(voice={"file_id": "F1", "duration": 9})
        whisper_model, whisper = self.ai.calls[0]
        self.assertEqual(whisper_model, compose.WHISPER_MODEL)
        self.assertEqual(base64.b64decode(whisper["audio"]), b"OggS-voice")
        self.assertIn(("download", "voice/file_1.oga"), self.tg.calls)
        self.assertIn("transcribed from the learner's speech", self.ai.calls[1][1]["messages"][0]["content"])
        self.assertIn("🎙 Услышал: <i>I chill at home. We chill on Fridays.</i>", self.tg.sent()[-2]["text"])
        self.assertEqual(self.word()["stage"], 1)

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
