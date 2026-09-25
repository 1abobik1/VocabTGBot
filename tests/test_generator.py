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

    def test_bare_list_of_cards_is_accepted(self):
        text = "```json\n" + json.dumps([card("chill", "отдыхать")], ensure_ascii=False) + "\n```"
        parsed = generator.parse_cards({"choices": [{"message": {"content": text}}]})
        self.assertEqual([c["en"] for c in parsed], ["chill"])

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
        prompt = generator.build_input("B2", 3, "работа в офисе", ["apple", "cat"])
        system = prompt["messages"][0]["content"]
        self.assertIn("CEFR level B2", system)
        self.assertIn("Topic: работа в офисе", system)
        self.assertIn("apple, cat", system)
        self.assertEqual(prompt["messages"][1]["content"], "Generate 3 cards.")
        self.assertIn('Answer with JSON only, in this shape: {"cards":[{"en":"","ru":"","examples":', system)
        random_topic = generator.build_input("B1", 1)["messages"][0]["content"]
        self.assertTrue(any(f"Topic: {t}." in random_topic for t in generator.RANDOM_TOPICS))

    def test_generate_tops_up_dropped_cards(self):
        ai = FakeAI(
            {"response": {"cards": [card("bug", "ошибка"), card("deploy", "развернуть"), card("ex-boyfriend", "бывший boyfriend")]}},
            {"response": {"cards": [card("bug", "ошибка"), card("commit", "закоммитить")]}},
        )
        result = asyncio.run(generator.generate(ai, "B1", 3, "программирование"))
        self.assertEqual([c["en"] for c in result], ["bug", "deploy", "commit"])
        second_prompt = ai.calls[1][1]
        self.assertEqual(second_prompt["messages"][1]["content"], "Generate 1 cards.")
        self.assertIn("bug, deploy", second_prompt["messages"][0]["content"])

    def test_generate_gives_up_after_three_requests(self):
        ai = FakeAI(*[{"response": {"cards": [card("кот", "cat")]}}] * 5)
        self.assertEqual(asyncio.run(generator.generate(ai, "B1", 2)), [])
        self.assertEqual(len(ai.calls), 3)

    def test_terms_from_english_are_allowed_in_russian_examples(self):
        raw = card("script", "скрипт", examples=(
            ("I wrote a script in Python.", "Я написал скрипт на Python."),
            ("Call the API from the script.", "Вызови API из скрипта."),
            ("The app crashed.", "Приложение crashedнуло."),
            ("She is my boyfriend.", "Она мой boyfriend."),
        ))
        parsed = generator.parse_cards({"response": {"cards": [raw]}})[0]
        self.assertEqual([e["ru"] for e in parsed["examples"]], ["Я написал скрипт на Python.", "Вызови API из скрипта."])
        # the word's own translation stays strict
        self.assertEqual(generator.parse_cards({"response": {"cards": [card("python", "язык Python")]}}), [])

    def test_enrich_validates_and_retries(self):
        ai = FakeAI(
            {"response": {"examples": [{"en": "The app crashed.", "ru": "Приложение crashedнуло."}], "synonyms": []}},
            {"response": {"examples": [{"en": "I ate an apple.", "ru": "Я съел яблоко."}],
                          "synonyms": [{"en": "fruit", "ru": "фрукт"}, {"en": "", "ru": "пусто"}]}},
        )
        examples, synonyms = asyncio.run(generator.enrich(ai, "apple", "яблоко"))
        self.assertEqual(examples, [{"en": "I ate an apple.", "ru": "Я съел яблоко."}])
        self.assertEqual(synonyms, [{"en": "fruit", "ru": "фрукт"}])
        self.assertEqual(len(ai.calls), 2)

    def test_enrich_gives_up(self):
        ai = FakeAI({"response": "nope"}, {"response": "nope"})
        self.assertEqual(asyncio.run(generator.enrich(ai, "apple", "яблоко")), ([], []))

    def test_gemma_runs_without_thinking(self):
        ai = FakeAI({"response": {"cards": [card("chill", "отдыхать")]}}, {"response": {
            "examples": [{"en": "Let's chill.", "ru": "Давай отдохнём."}], "synonyms": []}})
        asyncio.run(generator.generate(ai, "B1", 1, model="@cf/google/gemma-4-26b-a4b-it"))
        asyncio.run(generator.enrich(ai, "chill", "отдыхать", model="@cf/google/gemma-4-26b-a4b-it"))
        for _, payload in ai.calls:
            self.assertEqual(payload["chat_template_kwargs"], {"enable_thinking": False})
        self.assertEqual(generator.model_options("@cf/meta/llama-4-scout-17b-16e-instruct"), {})

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
        return {b["text"]: b["callback_data"] for row in sent[-1]["reply_markup"]["inline_keyboard"] for b in row}

    def test_generate_review_edit_and_approve(self):
        self.msg("/start")
        self.msg("apple - яблоко")
        self.ai.responses.append({"response": {"cards": [card("burnout", "выгорание"), card("chill", "отдыхать"), card("apple", "яблоко")]}})
        self.msg("/gen 3 B2 работа в офисе")

        system = self.ai.calls[-1][1]["messages"][0]["content"]
        self.assertIn("CEFR level B2", system)
        self.assertIn("работа в офисе", system)
        self.assertIn("apple", system)  # known words are listed to avoid
        self.assertEqual([c["en"] for c in self.key("inbox")], ["burnout", "chill"])  # apple filtered out
        self.assertIn("🆕 Новая карточка · B2 · на проверке ещё 1", self.tg.last_text())
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

    def test_generation_follows_the_global_level_by_default(self):
        self.msg("/start")
        self.msg(cards.LEVEL_BUTTON)
        menu = self.tg.sent()[-1]
        self.assertIn("Общий уровень английского: <b>B1</b>", menu["text"])
        self.assertEqual(menu["reply_markup"], cards.level_keyboard("B1"))
        self.press("glv:B2")
        self.assertEqual(self.key("settings")["level"], "B2")
        method, payload = self.tg.calls[-1]
        self.assertEqual((method, payload["message_id"]), ("editMessageText", 50))
        self.assertIn("<b>B2</b>", payload["text"])

        self.msg(cards.GENERATE_BUTTON)
        menu = self.tg.sent()[-1]
        self.assertIn("Уровень: <b>B2</b> (как общий", menu["text"])
        self.assertEqual(menu["reply_markup"], cards.generate_keyboard("B2"))
        self.ai.responses.append({"response": {"cards": [card("chill", "отдыхать")]}})
        self.press("gen:1")
        self.assertIn("CEFR level B2", self.ai.calls[-1][1]["messages"][0]["content"])

    def test_generation_can_have_its_own_level(self):
        self.msg("/start")
        self.press("glv:B1")
        self.msg(cards.GENERATE_BUTTON)
        self.press("lv:B2")  # только для генерации
        settings = self.key("settings")
        self.assertEqual((settings["level"], settings["gen_level"]), ("B1", "B2"))
        payload = self.tg.calls[-1][1]
        self.assertIn("Уровень: <b>B2</b> (свой для генерации)", payload["text"])
        self.assertEqual(payload["reply_markup"], cards.generate_keyboard("B2", own=True))
        self.ai.responses.append({"response": {"cards": [card("chill", "отдыхать")]}})
        self.press("gen:1")
        self.assertIn("CEFR level B2", self.ai.calls[-1][1]["messages"][0]["content"])

        # смена общего уровня не трогает свой уровень генерации...
        self.press("glv:A2")
        self.assertEqual(self.key("settings")["gen_level"], "B2")
        # ...а «как общий» возвращает связь
        self.press("lv:auto")
        self.assertNotIn("gen_level", self.key("settings"))
        self.assertIn("Уровень: <b>A2</b> (как общий", self.tg.calls[-1][1]["text"])

    def test_manual_examples_follow_the_global_level(self):
        self.msg("/start")
        self.press("glv:A2")
        self.ai.responses.append(self.enrichment())
        self.msg("apple - яблоко")
        self.assertIn("CEFR level A2", self.ai.calls[-1][1]["messages"][0]["content"])

    def test_examples_use_the_grammar_with_gaps(self):
        self.msg("/start")
        now = datetime.now(timezone.utc).isoformat()
        log = [{"at": now, "rule": "a2_present_perfect", "verdict": v, "sentence": "x ___", "answer": "a", "given": "b"}
               for v in ("wrong", "wrong", "ok")]
        asyncio.run(self.bot.repo.put(f"mlog:{OWNER.lower()}", log))
        self.ai.responses.append(self.enrichment())
        self.msg("apple - яблоко")
        system = self.ai.calls[-1][1]["messages"][0]["content"]
        self.assertIn("grammar the learner is practising: present perfect for experience", system)

    def test_old_c1_level_becomes_b2(self):
        self.msg("/start")
        asyncio.run(self.bot.repo.put(f"settings:{OWNER.lower()}", {"level": "C1"}))
        self.ai.responses.append({"response": {"cards": [card("chill", "отдыхать")]}})
        self.msg("/gen 1")
        self.assertIn("CEFR level B2", self.ai.calls[-1][1]["messages"][0]["content"])

    def test_level_command_still_works(self):
        self.msg("/start")
        self.msg("/level b2")
        self.assertEqual(self.key("settings")["level"], "B2")
        self.msg("/level")
        self.assertEqual(self.tg.sent()[-1]["reply_markup"], cards.level_keyboard("B2"))
        self.msg("/level Z9")
        self.assertIn("Уровень: A1, A2, B1 или B2", self.tg.last_text())

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

    def test_partial_generation_is_reported(self):
        self.msg("/start")
        self.ai.responses += [{"response": {"cards": [card("chill", "отдыхать")]}}] + [{"response": {"cards": []}}] * 2
        self.msg("/gen 3")
        texts = [p["text"] for p in self.tg.sent()[-3:]]
        self.assertTrue(any("Получилось 1 из 3" in t for t in texts))
        self.assertEqual(len(self.key("inbox")), 1)

    def test_generated_word_is_never_offered_twice(self):
        self.msg("/start")
        self.ai.responses.append({"response": {"cards": [card("burnout", "выгорание")]}})
        self.msg("/gen 1")
        self.assertEqual(self.key("seen"), ["burnout"])

        # карточку удалили — слово всё равно считается показанным
        self.press(self.inbox_buttons()["🗑 Удалить"])
        self.assertEqual(self.key("inbox"), [])
        self.assertIn("burnout", self.key("seen"))
        self.ai.responses.append({"response": {"cards": [card("burnout", "выгорание"), card("chill", "отдыхать")]}})
        self.ai.responses.append({"response": {"cards": [card("commit", "закоммитить")]}})
        self.msg("/gen 2")
        self.assertIn("burnout", self.ai.calls[-1][1]["messages"][0]["content"])  # в промпте как запрет
        self.assertEqual([c["en"] for c in self.key("inbox")], ["chill", "commit"])  # дубль отброшен

    def test_manual_and_archived_words_are_remembered_too(self):
        self.msg("/start")
        self.msg("apple - яблоко\nI ate an apple. - Я съел яблоко.")
        self.assertEqual(self.key("seen"), ["apple"])
        asyncio.run(self.bot.repo.put(f"known:{OWNER.lower()}", [w.new_word("gone", "ушедшее")]))
        asyncio.run(self.bot.repo.put(f"queue:{OWNER.lower()}", []))
        self.ai.responses.append({"response": {"cards": [card("gone", "ушедшее"), card("chill", "отдыхать")]}})
        self.msg("/gen 1")
        self.assertEqual([c["en"] for c in self.key("inbox")], ["chill"])

    def test_generation_failure(self):
        self.msg("/start")
        self.ai.responses += [RuntimeError("boom"), RuntimeError("boom")]
        self.msg("/gen 2")
        self.assertIn("Не получилось", self.tg.last_text())
        self.assertEqual(self.key("inbox"), None)

    def enrichment(self, examples=(("I ate an apple.", "Я съел яблоко."),), synonyms=(("fruit", "фрукт"),)):
        return {"response": {
            "examples": [{"en": a, "ru": b} for a, b in examples],
            "synonyms": [{"en": a, "ru": b} for a, b in synonyms],
        }}

    def test_manual_word_without_examples_is_enriched_by_ai(self):
        self.msg("/start")
        self.ai.responses.append(self.enrichment())
        self.msg("apple - яблоко")

        prompt = self.ai.calls[-1][1]["messages"]
        self.assertIn("Word: apple", prompt[1]["content"])
        self.assertIn("Russian translation: яблоко", prompt[1]["content"])
        self.assertIsNone(self.key("queue"))  # waits for review, not in the queue yet
        card = self.key("inbox")[0]
        self.assertEqual(card["examples"], [{"en": "I ate an apple.", "ru": "Я съел яблоко."}])
        self.assertEqual(card["synonyms"], [{"en": "fruit", "ru": "фрукт"}])
        self.assertIn("🆕 Примеры от ИИ", self.tg.last_text())
        self.assertIn("⏳ Придумываю примеры", self.tg.sent()[-2]["text"])

        self.press(self.inbox_buttons()["✅ В очередь"])
        queued = self.key("queue")[0]
        self.assertEqual(queued["en"], "apple")
        self.assertEqual(len(queued["examples"]), 1)
        self.assertNotIn("source", queued)

    def test_bare_button_drops_ai_examples(self):
        self.msg("/start")
        self.ai.responses.append(self.enrichment())
        self.msg("apple - яблоко")
        self.press(self.inbox_buttons()["🚫 Без примеров"])
        queued = self.key("queue")[0]
        self.assertEqual((queued["examples"], queued["synonyms"]), ([], []))
        self.assertEqual(self.key("inbox"), [])

    def test_manual_word_with_examples_is_not_enriched(self):
        self.msg("/start")
        self.msg("apple - яблоко\nI ate an apple. - Я съел яблоко.")
        self.assertEqual(self.ai.calls, [])
        self.assertEqual(len(self.key("queue")), 1)
        self.assertIsNone(self.key("inbox"))

    def test_word_is_added_as_is_when_enrichment_fails(self):
        self.msg("/start")
        self.ai.responses += [RuntimeError("down")]
        self.msg("apple - яблоко")
        self.assertEqual([x["en"] for x in self.key("queue")], ["apple"])
        self.assertIn("➕ Добавлено", self.tg.last_text())
        # a model answering with junk is the same case
        self.ai.responses += [{"response": "sorry"}, {"response": "sorry"}]
        self.msg("cat - кот")
        self.assertEqual([x["en"] for x in self.key("queue")], ["apple", "cat"])

    def test_no_ai_means_straight_to_the_queue(self):
        bot = Bot(self.store, self.tg, owner=OWNER)
        asyncio.run(bot.handle_update({"message": {"message_id": 1, "from": {"id": CHAT, "username": OWNER},
                                                   "chat": {"id": CHAT, "type": "private"}, "text": "dog - собака"}}))
        self.assertEqual([x["en"] for x in self.key("queue")], ["dog"])

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
