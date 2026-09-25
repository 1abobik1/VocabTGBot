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


def review(*verdicts):
    return {"response": {"sentences": [
        {"sentence": f"s{i}", "verdict": v, "corrected": f"Fixed sentence {i}.",
         "comment_ru": "Пропущен артикль." if v != "correct" else ""} for i, v in enumerate(verdicts)]}}


USAGE = {"response": {
    "tip_ru": "Ещё говорят «chill out» — расслабиться, и «chill» — спокойный.",
    "past": {"en": "We chilled out after the exam.", "ru": "Мы расслабились после экзамена."},
    "present": {"en": "Just chill out, it's fine.", "ru": "Расслабься, всё нормально."},
    "future": {"en": "We'll chill at the beach tomorrow.", "ru": "Завтра отдохнём на пляже."}}}


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
                                       spoken=True)
        system, user = prompt["messages"][0]["content"], prompt["messages"][1]["content"]
        self.assertIn("the word or phrase 'chill' (Russian: 'отдыхать')", system)
        self.assertIn("CEFR level B1", system)
        self.assertIn("transcribed from the learner's speech", system)
        self.assertEqual(user, "1. I chill.\n2. We chill.")
        self.assertNotIn("speech", compose.build_request(WORD, ["a b"], "B1")["messages"][0]["content"])

    def test_parse_and_verdict(self):
        items = compose.parse(review("correct", "grammar", "wrong"), ["a b", "c d", "e f"])
        self.assertEqual([x["sentence"] for x in items], ["a b", "c d", "e f"])  # предложения — свои, не из ответа
        self.assertEqual(items[0]["comment"], "")
        self.assertEqual(compose.good_count(items), 2)
        self.assertTrue(compose.passed(items))
        self.assertTrue(compose.passed(compose.parse(review("wrong", "grammar"), ["a b", "c d"])))  # хватает одного
        self.assertFalse(compose.passed(compose.parse(review("wrong", "wrong"), ["a b", "c d"])))
        self.assertIsNone(compose.parse(review("correct"), ["a b", "c d"]))           # разобрано не всё
        self.assertIsNone(compose.parse(review("correct", "maybe"), ["a b", "c d"]))  # непонятный вердикт
        bad = review("correct")
        bad["response"]["sentences"][0]["corrected"] = "Я отдыхаю."
        self.assertEqual(compose.parse(bad, ["a b"])[0]["corrected"], "a b")

    def test_usage(self):
        prompt = compose.build_usage_request(WORD, ["I chill at home."], "B1")
        self.assertIn("typical constructions and collocations", prompt["messages"][0]["content"])
        self.assertEqual(prompt["messages"][1]["content"], "The learner's sentences:\n- I chill at home.")
        self.assertIn("'past', 'present', 'future'", prompt["messages"][0]["content"])
        tip, examples = compose.parse_usage(USAGE)
        self.assertIn("chill out", tip)
        self.assertEqual([(e["tense"], e["en"]) for e in examples], [
            ("past", "We chilled out after the exam."), ("present", "Just chill out, it's fine."),
            ("future", "We'll chill at the beach tomorrow.")])
        broken = {"response": dict(USAGE["response"], present={"en": "Chill.", "ru": "Chill."})}
        self.assertEqual([e["tense"] for e in compose.parse_usage(broken)[1]], ["past", "future"])  # плохой пример выпал
        broken = {"response": {"tip_ru": "Use chill out.", "past": {"en": "a", "ru": "a"}, "present": {"en": "b", "ru": "b"},
                               "future": {"en": "c", "ru": "c"}}}
        self.assertIsNone(compose.parse_usage(broken))                                # ничего годного

    def test_punctuation_only_fix_is_not_shown(self):
        item = {"sentence": "my car is reliable it never breaks", "corrected": "My car is reliable; it never breaks.",
                "verdict": "correct", "comment": ""}
        self.assertFalse(compose.changed(item))
        self.assertNotIn("→", cards.render_compose_review([item, dict(item)]))
        self.assertTrue(compose.changed(dict(item, corrected="My car is reliable; it never breaks down.")))

    def test_quotes_around_english_are_dropped(self):
        tip = ("Помни: после фразы идёт -ing. В письме: 'I look forward to your reply' (Жду ответа), "
               "а ещё ‘chill out’ и \"reliable\", и 'it's easy to take something for granted'. Don't — не трогаем.")
        text = cards.render_compose_result(WORD, [], (tip, []))
        self.assertIn("В письме: <b>I look forward to your reply</b> (Жду ответа), а ещё <b>chill out</b> "
                      "и <b>reliable</b>, и <b>it's easy to take something for granted</b>. Don't — не трогаем.", text)

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
        self.ai.responses.append(review("wrong"))
        self.msg("The soup is chill.")
        text = self.tg.last_text()
        self.assertIn("1. ❌ The soup is chill.\n→ <b>Fixed sentence 0.</b>\n<i>Пропущен артикль.</i>", text)
        self.assertIn("Верно: 0 из 1. Пока слово не употреблено верно — пришли ещё предложение", text)
        self.assertEqual(self.buttons(), ["sd", "sc"])
        self.assertIn("sent_at", self.word())                                        # карточка ещё ждёт ответа
        dropped = [p["message_id"] for m, p in self.tg.calls if m == "editMessageReplyMarkup"]
        self.assertIn(prompt_message, dropped)                                      # «Отмена» у задания убрана

        for n in range(5):  # голосовых сколько угодно, лимита нет
            self.ai.responses += [{"text": f"We chill on day {n}."}, review("correct")]
            self.msg(voice={"file_id": f"F{n}", "duration": 4})
        text = self.tg.last_text()
        self.assertIn("🎙 Услышал: <i>We chill on day 4.</i>\n\n6. ✅ We chill on day 4.", text)
        self.assertIn("Верно: 5 из 6. Уже засчитывается", text)
        self.assertEqual(self.word()["stage"], 0)                                  # пока не нажал «Готово»

        self.ai.responses.append(USAGE)
        self.press("sd")
        usage_prompt = self.ai.calls[-1][1]["messages"]
        self.assertIn("typical constructions", usage_prompt[0]["content"])
        self.assertIn("- Fixed sentence 0.", usage_prompt[1]["content"])            # исправленные предложения
        result = self.tg.sent()[-2]["text"]
        self.assertIn("🏁 <b>Итог</b> · chill\n\nВерно 5 из 6 — засчитано как «Знаю».", result)
        self.assertIn("💡 Ещё говорят <b>chill out</b> — расслабиться, и <b>chill</b> — спокойный.", result)
        self.assertIn("<b>Ещё так говорят:</b>\n• прошлое: We chilled out after the exam.\n  <i>Мы расслабились после "
                      "экзамена.</i>\n• настоящее: Just chill out, it's fine.", result)
        self.assertIn("• будущее: We'll chill at the beach tomorrow.", result)
        self.assertEqual(self.word()["stage"], 1)                                  # как «Знаю» на новом слове
        self.assertTrue(self.tg.last_text().startswith("Chill"))
        self.assertEqual(self.store.json(f"compose:{KEY}"), {})
        dropped = [p["message_id"] for m, p in self.tg.calls if m == "editMessageReplyMarkup"]
        self.assertIn(self.card_message, dropped)                                   # второй раз не ответить

    def test_long_message_is_cut_and_weak_result_is_unknown(self):
        self.start()
        self.ai.responses.append(review("wrong", "wrong", "wrong", "wrong", "wrong"))
        self.msg("I chill. You chill. The soup is chill. It's chill outside. We chill a lot. They chill too.")
        self.assertEqual(self.ai.calls[-1][1]["messages"][1]["content"].count("\n"), 4)  # 5 из 6
        self.assertIn("Ещё 1 не разобрал: в одном сообщении — до 5.", self.tg.last_text())
        self.ai.responses.append(RuntimeError("down"))                              # совет не пришёл — итог всё равно
        self.press("sd")
        self.assertIn("Верно 0 из 5 — считаю как «Не знаю»", self.tg.last_text())
        self.assertNotIn("💡", self.tg.last_text())
        word = self.word()
        self.assertEqual((word["stage"], word["lapses_in_a_row"]), (0, 1))
        self.assertNotIn("sent_at", word)

    def test_one_good_sentence_is_enough(self):
        self.start()
        self.ai.responses += [review("correct"), USAGE]
        self.msg("I chill at home on Sundays.")
        self.assertIn("Уже засчитывается", self.tg.last_text())
        self.press("sd")
        self.assertIn("Верно 1 из 1 — засчитано как «Знаю».", self.tg.sent()[-2]["text"])
        self.assertEqual(self.word()["stage"], 1)

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

    def test_know_on_a_finished_card_brings_advice(self):
        word = self.word()
        self.press(f"k:{word['id']}:0")                       # первая сторона нового слова — только разворот
        self.assertEqual(self.ai.calls, [])
        self.assertTrue(self.tg.last_text().startswith("Chill"))
        deferred = []
        self.bot.defer = deferred.append
        self.ai.responses.append(USAGE)
        self.press(f"k:{word['id']}:1")                       # вторая сторона — слово отвечено полностью
        self.assertEqual(len(deferred), 1)                    # совет ждёт ИИ в фоне, кнопка отвечает сразу
        asyncio.run(deferred[0])
        prompt = self.ai.calls[-1][1]["messages"]
        self.assertIn("recalled on a flashcard the word or phrase 'chill'", prompt[0]["content"])
        self.assertEqual(prompt[1]["content"], "Word: chill\nRussian translation: отдыхать")
        text = self.tg.last_text()
        self.assertTrue(text.startswith("📘 <b>chill</b> — отдыхать\n\n💡 Ещё говорят <b>chill out</b>"))
        self.assertIn("<b>Ещё так говорят:</b>\n• прошлое: We chilled out after the exam.", text)

    def test_no_advice_when_ai_fails(self):
        word = self.word()
        self.press(f"k:{word['id']}:0")
        before = len(self.tg.sent())
        self.ai.responses += [RuntimeError("down")]
        self.press(f"k:{word['id']}:1")
        self.assertEqual(len(self.tg.sent()), before)          # без совета, без ошибок пользователю
        self.assertEqual(self.word()["box"], 1)

    def test_stale_compose_button(self):
        word = self.word()
        self.press(f"k:{word['id']}:0")
        self.press(f"sx:{word['id']}:0")  # кнопка со старой копии карточки
        self.assertIn("уже неактуальна", self.tg.last_text())


if __name__ == "__main__":
    unittest.main()
