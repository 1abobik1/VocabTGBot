import asyncio
import json
import unittest
from datetime import datetime, timedelta, timezone

from shared import cards, generator
from shared import words as w
from shared.bot import Bot
from shared.schedule import Schedule
from tests.test_bot import CHAT, OWNER, FakeStore, FakeTelegram

MSK = timezone(timedelta(hours=3))


def card(en, ru, examples=(("I like it.", "Мне нравится."),), synonyms=(("fairly", "довольно"),)):
    return {
        "en": en,
        "ru": ru,
        "examples": [{"en": a, "ru": b} for a, b in examples],
        "synonyms": [{"en": a, "ru": b} for a, b in synonyms],
    }


class FakeAI:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []

    async def run(self, model, input):
        self.calls.append((model, input))
        response = self.responses.pop(0) if self.responses else {"response": {"cards": []}}
        if isinstance(response, Exception):
            raise response
        return response


class ParseCardsTest(unittest.TestCase):
    def test_accepts_dict_string_and_chat_completion(self):
        payload = {"cards": [card("burnout", "выгорание")]}
        for output in (
            {"response": payload},
            {"response": "Here you go:\n```json\n" + json.dumps(payload, ensure_ascii=False) + "\n```"},
            {"choices": [{"message": {"content": json.dumps(payload)}}]},
        ):
            parsed = generator.parse_cards(output)
            self.assertEqual([c["en"] for c in parsed], ["burnout"])
            self.assertEqual(parsed[0]["synonyms"], [{"en": "fairly", "ru": "довольно"}])
            self.assertEqual(parsed[0]["stage"], 0)

    def test_rejects_bad_cards(self):
        output = {"response": {"cards": [
            card("micromanage", "контролировать каждый细节"),  # stray Chinese
            card("ex-boyfriend", "бывший boyfriend"),      # English inside Russian
            card("crash", "падать", examples=(("The app crashed.", "Приложение crashedнуло."),)),
            card("выгорание", "burnout"),                  # languages swapped
            card("hang out", "тусоваться", examples=()),   # no examples
            card("", "пусто"),
            "not a card",
            card("to delegate", "делегировать"),           # leading "to" is dropped
            card("Get By", "обходиться"),                  # duplicate of an existing word
        ]}}
        parsed = generator.parse_cards(output, existing=["get by"])
        self.assertEqual([c["en"] for c in parsed], ["delegate"])

    def test_short_abbreviations_are_allowed_in_russian(self):
        parsed = generator.parse_cards({"response": {"cards": [card("bug", "баг в IT", examples=(("It's a bug.", "Это баг, ОК?"),))]}})
        self.assertEqual(len(parsed), 1)

    def test_bad_examples_and_synonyms_are_dropped_not_the_card(self):
        raw = card("chill", "отдыхать", examples=(("Let's chill.", "Давай отдохнём."), ("бред", "nonsense")),
                   synonyms=(("relax", "расслабиться"), ("", "пусто")))
        parsed = generator.parse_cards({"response": {"cards": [raw]}})[0]
        self.assertEqual(len(parsed["examples"]), 1)
        self.assertEqual(parsed["synonyms"], [{"en": "relax", "ru": "расслабиться"}])

    def test_garbage(self):
        for output in ({}, {"response": "sorry"}, {"response": "{broken"}, {"response": {"cards": "x"}}, "text"):
            self.assertEqual(generator.parse_cards(output), [])

    def test_prompt(self):
        prompt = generator.build_input("C1", 3, "работа в офисе", ["apple", "cat"])
        system = prompt["messages"][0]["content"]
        self.assertIn("CEFR level C1", system)
        self.assertIn("Topic: работа в офисе", system)
        self.assertIn("apple, cat", system)
        self.assertEqual(prompt["messages"][1]["content"], "Generate 3 cards.")
        self.assertIn("json_schema", prompt["response_format"]["type"])
        random_topic = generator.build_input("B1", 1)["messages"][0]["content"]
        self.assertTrue(any(f"Topic: {t}." in random_topic for t in generator.RANDOM_TOPICS))

    def test_generate_retries_once(self):
        ai = FakeAI({"response": "nothing"}, {"response": {"cards": [card("chill", "отдыхать")]}})
        result = asyncio.run(generator.generate(ai, "B1", 1))
        self.assertEqual([c["en"] for c in result], ["chill"])
        self.assertEqual(len(ai.calls), 2)


class CardTextTest(unittest.TestCase):
    def test_round_trip_with_synonyms(self):
        word = w.new_word("quite", "довольно", [{"en": "It's quite cold.", "ru": "Довольно холодно."}],
                          [{"en": "fairly", "ru": "довольно"}, {"en": "rather", "ru": ""}])
        text = w.card_to_text(word)
        self.assertEqual(text, "quite - довольно\nIt's quite cold. - Довольно холодно.\nСинонимы: fairly - довольно; rather")
        self.assertEqual(w.parse_card_text(text), (word["en"], word["ru"], word["examples"], word["synonyms"]))

    def test_synonym_line_variants(self):
        _, _, _, synonyms = w.parse_card_text("quite - довольно\nsyn: скорее - rather;  fairly")
        self.assertEqual(synonyms, [{"en": "rather", "ru": "скорее"}, {"en": "fairly", "ru": ""}])

    def test_card_shows_synonyms_unhidden(self):
        word = w.new_word("quite", "довольно", [], [{"en": "fairly", "ru": "довольно"}, {"en": "rather", "ru": ""}])
        self.assertTrue(cards.render_card(word).endswith("\n\n<i>Синонимы:</i> fairly — довольно; rather"))


class InboxFlowTest(unittest.TestCase):
    def setUp(self):
        self.store, self.tg = FakeStore(), FakeTelegram()
        self.ai = FakeAI()
        self.bot = Bot(self.store, self.tg, owner=OWNER, schedule=Schedule(), ai=self.ai)
        self.n = 0

    def msg(self, text):
        self.n += 1
        asyncio.run(self.bot.handle_update({"message": {
            "message_id": self.n, "from": {"id": CHAT, "username": OWNER},
            "chat": {"id": CHAT, "type": "private"}, "text": text}}))

    def press(self, data):
        self.n += 1
        asyncio.run(self.bot.handle_update({"callback_query": {
            "id": f"q{self.n}", "from": {"id": CHAT, "username": OWNER},
            "message": {"message_id": 50, "chat": {"id": CHAT, "type": "private"}}, "data": data}}))

    def key(self, name):
        return self.store.json(f"{name}:{OWNER.lower()}")

    def inbox_buttons(self):
        sent = [p for p in self.tg.sent() if p.get("reply_markup", {}).get("inline_keyboard", [[{}]])[0][0].get("callback_data", "").startswith("ia:")]
        return {b["text"]: b["callback_data"] for b in sent[-1]["reply_markup"]["inline_keyboard"][0]}

    def test_generate_review_edit_and_approve(self):
        self.msg("/start")
        self.msg("apple - яблоко")
        self.ai.responses.append({"response": {"cards": [card("burnout", "выгорание"), card("chill", "отдыхать"), card("apple", "яблоко")]}})
        self.msg("/gen 3 C1 работа в офисе")

        system = self.ai.calls[-1][1]["messages"][0]["content"]
        self.assertIn("CEFR level C1", system)
        self.assertIn("работа в офисе", system)
        self.assertIn("apple", system)  # known words are listed to avoid
        self.assertEqual([c["en"] for c in self.key("inbox")], ["burnout", "chill"])  # apple filtered out
        self.assertIn("🆕 Новая карточка · C1 · на проверке ещё 1", self.tg.last_text())
        self.assertIn("<b>Burnout</b> — выгорание", self.tg.last_text())

        # edit the first card
        self.press(self.inbox_buttons()["✏️ Исправить"])
        self.assertIn("<code>burnout - выгорание\nI like it. - Мне нравится.\nСинонимы: fairly - довольно</code>", self.tg.last_text())
        self.msg("burnout - профессиональное выгорание\nI'm close to burnout. - Я на грани выгорания.\nСинонимы: exhaustion - истощение")
        self.assertEqual(self.key("state"), {})
        edited = self.key("inbox")[0]
        self.assertEqual(edited["ru"], "профессиональное выгорание")
        self.assertEqual(edited["synonyms"], [{"en": "exhaustion", "ru": "истощение"}])
        self.assertEqual(len(self.key("queue")), 1)  # the edit did not add a word

        # approve -> queue, next inbox card is shown
        self.press(self.inbox_buttons()["✅ В очередь"])
        self.assertEqual([x["en"] for x in self.key("queue")], ["apple", "burnout"])
        self.assertNotIn("level", self.key("queue")[1])
        self.assertIn("<b>Chill</b>", self.tg.last_text())

        # delete the last one
        self.press(self.inbox_buttons()["🗑 Удалить"])
        self.assertEqual(self.key("inbox"), [])
        self.assertIn("Все новые карточки разобраны", self.tg.last_text())

        # stale buttons do nothing
        self.press("ia:" + edited["id"])
        self.assertIn("уже нет во входящих", self.tg.last_text())
        self.assertEqual(len(self.key("queue")), 2)

    def test_edit_errors_keep_edit_mode(self):
        self.msg("/start")
        self.ai.responses.append({"response": {"cards": [card("chill", "отдыхать")]}})
        self.msg("/gen 1")
        self.press(self.inbox_buttons()["✏️ Исправить"])
        self.msg("no separator here")
        self.assertIn("⚠️", self.tg.last_text())
        self.assertEqual(self.key("state")["mode"], "edit")
        self.msg("/cancel")
        self.assertEqual(self.key("state"), {})
        self.assertEqual(self.key("queue"), None)

    def test_level_is_changed_inside_the_generation_menu(self):
        self.msg("/start")
        self.msg(cards.GENERATE_BUTTON)
        menu = self.tg.sent()[-1]
        self.assertIn("Уровень: <b>B1</b>", menu["text"])
        self.assertEqual(menu["reply_markup"], cards.generate_keyboard("B1"))
        self.assertIn({"text": "• B1 •", "callback_data": "lv:B1"}, menu["reply_markup"]["inline_keyboard"][1])

        self.press("lv:C1")
        self.assertEqual(self.key("settings")["level"], "C1")
        method, payload = self.tg.calls[-1]
        self.assertEqual((method, payload["message_id"]), ("editMessageText", 50))
        self.assertIn("Уровень: <b>C1</b>", payload["text"])
        self.assertEqual(payload["reply_markup"], cards.generate_keyboard("C1"))

        self.ai.responses.append({"response": {"cards": [card("chill", "отдыхать")]}})
        self.press("gen:1")
        self.assertIn("CEFR level C1", self.ai.calls[-1][1]["messages"][0]["content"])

    def test_level_command_still_works(self):
        self.msg("/start")
        self.msg("/level b2")
        self.assertEqual(self.key("settings")["level"], "B2")
        self.msg("/level")
        self.assertEqual(self.tg.sent()[-1]["reply_markup"], cards.generate_keyboard("B2"))
        self.msg("/level Z9")
        self.assertIn("A1, A2, B1, B2 или C1", self.tg.last_text())

    def test_gen_limits(self):
        self.msg("/start")
        self.msg("/gen 50")
        self.assertIn("от 1 до 10", self.tg.last_text())
        self.assertEqual(self.ai.calls, [])

    def test_edit_cancel_button(self):
        self.msg("/start")
        self.ai.responses.append({"response": {"cards": [card("chill", "отдыхать")]}})
        self.msg("/gen 1")
        self.press(self.inbox_buttons()["✏️ Исправить"])
        self.assertEqual(self.tg.sent()[-1]["reply_markup"], cards.cancel_keyboard())
        self.press("cancel")
        self.assertEqual(self.key("state"), {})
        self.assertEqual(self.tg.last_text(), "Отменено.")

    def test_generation_failure(self):
        self.msg("/start")
        self.ai.responses += [RuntimeError("boom"), RuntimeError("boom")]
        self.msg("/gen 2")
        self.assertIn("Не получилось", self.tg.last_text())
        self.assertEqual(self.key("inbox"), None)

    def test_manual_add_keeps_synonyms(self):
        self.msg("quite - довольно\nIt's quite cold. - Довольно холодно.\nСинонимы: fairly - довольно")
        self.assertEqual(self.key("queue")[0]["synonyms"], [{"en": "fairly", "ru": "довольно"}])

    def test_no_ai_configured(self):
        bot = Bot(self.store, self.tg, owner=OWNER)
        asyncio.run(bot.handle_update({"message": {"message_id": 1, "from": {"id": CHAT, "username": OWNER},
                                                   "chat": {"id": CHAT, "type": "private"}, "text": "/gen 2"}}))
        self.assertIn("Workers AI не подключён", self.tg.last_text())


class AutogenTest(unittest.TestCase):
    def setUp(self):
        self.store, self.tg = FakeStore(), FakeTelegram()
        self.ai = FakeAI()
        self.bot = Bot(self.store, self.tg, owner=OWNER, schedule=Schedule(), ai=self.ai)
        asyncio.run(self.bot.handle_update({"message": {"message_id": 1, "from": {"id": CHAT, "username": OWNER},
                                                        "chat": {"id": CHAT, "type": "private"}, "text": "/start"}}))

    def tick(self, h, m):
        return asyncio.run(self.bot.on_cron(datetime(2026, 9, 21, h, m, tzinfo=MSK)))

    def test_autogen_20_minutes_before_slot_only_when_queue_and_inbox_empty(self):
        self.ai.responses.append({"response": {"cards": [card("chill", "отдыхать")]}})
        self.assertEqual(self.tick(9, 39), [])
        self.assertEqual(self.ai.calls, [])
        self.assertEqual(self.tick(9, 40), [OWNER])  # 20 minutes before 10:00
        self.assertEqual(len(self.ai.calls), 1)
        self.assertIn("1 cards", self.ai.calls[0][1]["messages"][1]["content"])
        self.assertIn("<b>Chill</b>", self.tg.last_text())
        # inbox not reviewed yet: no second generation before the 11:18 slot
        self.assertEqual(self.tick(10, 58), [])
        self.assertEqual(len(self.ai.calls), 1)

    def test_no_autogen_when_queue_has_words(self):
        store_queue = [w.new_word("cat", "кот")]
        asyncio.run(self.bot.repo.put(f"queue:{OWNER.lower()}", store_queue))
        self.assertEqual(self.tick(9, 40), [])
        self.assertEqual(self.ai.calls, [])

    def test_autogen_failure_is_silent(self):
        self.ai.responses += [RuntimeError("down"), RuntimeError("down")]
        sent_before = len(self.tg.sent())
        self.assertEqual(self.tick(9, 40), [])
        self.assertEqual(len(self.tg.sent()), sent_before)


if __name__ == "__main__":
    unittest.main()
