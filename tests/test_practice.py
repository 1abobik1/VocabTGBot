import asyncio
import unittest
from datetime import datetime, timedelta, timezone

from shared import practice as pr
from shared import words as w
from shared.bot import Bot
from shared.schedule import Schedule
from tests.test_bot import CHAT, OWNER, FakeStore, FakeTelegram

MSK = timezone(timedelta(hours=3))
SATURDAY = (2026, 9, 26)
KEY = OWNER.lower()


def at(h, m, day=SATURDAY):
    return datetime(*day, h, m, tzinfo=MSK)


class GradingTest(unittest.TestCase):
    def test_separators(self):
        expected = ["loud", "silence", "hit it off", "sound"]
        for text in [
            "loud, silence, hit it off, sound",
            "loud silence hit it off sound",
            "loud\tsilence\thit it off\tsound",
            "1) loud\n2) silence\n3) hit it off\n4) sound",
            "loud; silence; hit it off; sound",
        ]:
            self.assertEqual(pr.split_answers(text, expected, True), expected, text)

    def test_missing_answers_are_empty(self):
        self.assertEqual(pr.split_answers("loud", ["loud", "sound"], True), ["loud", ""])

    def test_grade(self):
        cases = [
            ("loud", "loud", True, pr.OK),
            ("Loud!", "loud", True, pr.OK),
            ("to catch up", "catch up", True, pr.OK),
            ("lod", "loud", True, pr.NEAR),
            ("silense", "silence", True, pr.NEAR),
            ("sielnse", "silence", True, pr.NEAR),       # two letters off in a long word
            ("lud", "loud", True, pr.NEAR),
            ("ld", "loud", True, pr.WRONG),               # two letters off in a short word
            ("quiet", "silence", True, pr.WRONG),
            ("", "loud", True, pr.WRONG),
            ("-", "loud", True, pr.WRONG),
            ("наверстать", "догнать, наверстать", False, pr.OK),
            ("замужем", "женат/замужем", False, pr.OK),
            ("проводить время", "проводить время (с друзьями)", False, pr.OK),
            ("ёлка", "елка", False, pr.OK),
            ("звонкии", "звонкий", False, pr.NEAR),
            ("тихий", "звонкий", False, pr.WRONG),
        ]
        for answer, expected, english, verdict in cases:
            self.assertEqual(pr.grade(answer, expected, english), verdict, (answer, expected))

    def test_final_verdict(self):
        self.assertEqual(pr.final_verdict({pr.RU_EN: pr.OK, pr.EN_RU: pr.OK}), pr.OK)
        self.assertEqual(pr.final_verdict({pr.RU_EN: pr.NEAR, pr.EN_RU: pr.OK}), pr.NEAR)
        self.assertEqual(pr.final_verdict({pr.RU_EN: pr.NEAR, pr.EN_RU: pr.WRONG}), pr.WRONG)
        self.assertEqual(pr.final_verdict({}), pr.WRONG)

    def test_apply_results(self):
        a, b, c = w.new_word("a", "а"), w.new_word("b", "б"), w.new_word("c", "в")
        for x in (a, b, c):
            x["stage"] = 2
        queue = [w.new_word(n, n) for n in ("q1", "q2", "q3")]
        known, practice = [], [a, b, c]
        results = {
            a["id"]: {pr.RU_EN: pr.OK, pr.EN_RU: pr.OK},
            b["id"]: {pr.RU_EN: pr.NEAR, pr.EN_RU: pr.OK},
            c["id"]: {pr.RU_EN: pr.OK, pr.EN_RU: pr.WRONG},
        }
        outcome = pr.apply_results(queue, known, practice, results, "2026-09-26T09:30:00+03:00")
        self.assertEqual([x["en"] for x in known], ["a"])
        self.assertEqual(a["archived_at"], "2026-09-26T09:30:00+03:00")
        self.assertEqual([x["en"] for x in queue], ["q1", "b", "c", "q2", "q3"])
        self.assertEqual((b["stage"], c["stage"]), (1, 0))
        self.assertEqual(practice, [])
        self.assertEqual({k: [x["en"] for x in v] for k, v in outcome.items()}, {"ok": ["a"], "near": ["b"], "wrong": ["c"]})


class PracticeFlowTest(unittest.TestCase):
    def setUp(self):
        self.store, self.tg = FakeStore(), FakeTelegram()
        self.bot = Bot(self.store, self.tg, owner=OWNER, schedule=Schedule())
        self.n = 0
        self.msg("/start")

    def msg(self, text):
        self.n += 1
        asyncio.run(self.bot.handle_update({"message": {
            "message_id": self.n, "from": {"id": CHAT, "username": OWNER},
            "chat": {"id": CHAT, "type": "private"}, "text": text}}))

    def press(self, data):
        self.n += 1
        asyncio.run(self.bot.handle_update({"callback_query": {
            "id": f"q{self.n}", "from": {"id": CHAT, "username": OWNER},
            "message": {"message_id": 7, "chat": {"id": CHAT, "type": "private"}}, "data": data}}))

    def tick(self, when):
        return asyncio.run(self.bot.on_cron(when))

    def get(self, name):
        return self.store.json(f"{name}:{KEY}")

    def put(self, name, value):
        asyncio.run(self.bot.repo.put(f"{name}:{KEY}", value))

    def cards_sent(self):
        return [p for p in self.tg.sent() if p.get("reply_markup", {}).get("inline_keyboard", [[{}]])[0][0].get("callback_data", "").startswith("k:")]

    def seed_practice(self, pairs):
        words = []
        for en, ru, example in pairs:
            word = w.new_word(en, ru, [{"en": example[0], "ru": example[1]}] if example else [])
            word["stage"] = 2
            words.append(word)
        self.put("practice", words)
        return words

    def test_saturday_practice_full_flow(self):
        self.seed_practice([
            ("loud", "звонкий", ("The bell is loud.", "Звонок громкий.")),
            ("silence", "тишина", None),
            ("hit it off", "сразу поладить", None),
            ("sound", "звук", None),
        ])
        self.put("queue", [w.new_word(n, n + "_ru") for n in ("q1", "q2", "q3", "q4", "q5")])

        self.assertEqual(self.tick(at(8, 59)), [])
        self.assertEqual(self.tick(at(9, 0)), [OWNER])
        round1 = self.tg.last_text()
        self.assertIn("1/3.</b> Напиши по-английски", round1)
        self.assertIn("1) Звонкий\n2) Тишина\n3) Сразу поладить\n4) Звук", round1)

        # 10:00 and 11:18 slots are skipped while the practice waits
        self.tick(at(10, 0))
        self.tick(at(11, 18))
        self.assertEqual(self.cards_sent(), [])
        self.assertEqual(self.get("sched"), {"missed": 2})

        self.msg("loud silense hit it off sound")  # typo in "silence"
        self.assertIn("2) 🟡 silense → <b>silence</b>", self.tg.sent()[-2]["text"])
        self.assertIn("2/3.</b> Теперь по-русски", self.tg.last_text())

        self.msg("звонкий, тишина, поладить, тихий")  # 3 is wrong-ish, 4 wrong
        self.assertIn("4) ❌ тихий → <b>звук</b>", self.tg.sent()[-2]["text"])
        sentences = self.tg.last_text()
        self.assertIn("3/3.</b> Предложения", sentences)
        self.assertIn("1) Звонок громкий.\n<tg-spoiler>The bell is loud.</tg-spoiler>", sentences)

        self.msg("The bell is loud")  # optional sentence answer
        texts = [p["text"] for p in self.tg.sent()[-5:]]
        self.assertTrue(any("Сверь с эталоном:\n1) The bell is loud." in t for t in texts))
        self.assertTrue(any("В архив: loud" in t for t in texts))

        self.assertEqual([x["en"] for x in self.get("known")], ["loud"])
        self.assertEqual(self.get("practice"), [])
        self.assertEqual(self.get("session"), {})
        queue = self.get("queue")
        # silence (typo) second at stage 1; hit it off and sound (wrong) third at stage 0
        self.assertEqual([x["en"] for x in queue[:4]], ["q1", "silence", "sound", "hit it off"])
        self.assertEqual([x["stage"] for x in queue[1:4]], [1, 0, 0])

        # the two skipped slots are delivered at once
        self.assertEqual(len(self.cards_sent()), 2)
        self.assertEqual(self.get("sched"), {"missed": 0})

    def test_skip_sentences_button(self):
        self.seed_practice([("loud", "звонкий", ("The bell is loud.", "Звонок громкий."))])
        self.msg("/practice")
        self.msg("loud")
        self.msg("звонкий")
        self.press("px")
        self.assertEqual([x["en"] for x in self.get("known")], ["loud"])
        self.assertIn("Практика завершена", self.tg.last_text())

    def test_no_sentences_round_without_examples(self):
        self.seed_practice([("silence", "тишина", None)])
        self.msg("/practice")
        self.msg("silence")
        self.msg("тишина")
        self.assertEqual(self.get("session"), {})
        self.assertEqual(len(self.get("known")), 1)

    def test_practice_without_words(self):
        self.msg("/practice")
        self.assertIn("пока нет слов", self.tg.last_text())
        self.assertEqual(self.tick(at(9, 0)), [])

    def test_unfinished_practice_is_reminded_next_saturday(self):
        self.seed_practice([("silence", "тишина", None)])
        self.tick(at(9, 0))
        self.tick(at(9, 0, day=(2026, 10, 3)))
        self.assertIn("Напиши по-английски", self.tg.last_text())
        self.assertIn("ещё не закончена", self.tg.sent()[-2]["text"])

    def test_words_cannot_be_added_during_practice(self):
        self.seed_practice([("silence", "тишина", None)])
        self.msg("/practice")
        self.msg("cat - кот")  # treated as the practice answer
        self.assertIsNone(self.get("queue"))
        self.assertEqual(self.get("session")["round"], pr.EN_RU)


class CatchUpTest(unittest.TestCase):
    def setUp(self):
        self.store, self.tg = FakeStore(), FakeTelegram()
        self.bot = Bot(self.store, self.tg, owner=OWNER, schedule=Schedule())
        asyncio.run(self.bot.handle_update({"message": {"message_id": 1, "from": {"id": CHAT, "username": OWNER},
                                                        "chat": {"id": CHAT, "type": "private"}, "text": "/start"}}))
        asyncio.run(self.bot.repo.put(f"queue:{KEY}", [w.new_word(f"w{i}", f"с{i}") for i in range(15)]))

    def tick(self, h, m, day=(2026, 9, 21)):
        asyncio.run(self.bot.on_cron(datetime(*day, h, m, tzinfo=MSK)))

    def cards(self):
        return [p for p in self.tg.sent() if "reply_markup" in p and p["reply_markup"].get("inline_keyboard", [[{}]])[0][0].get("callback_data", "").startswith("k:")]

    def answer(self, card, action="n"):
        data = card["reply_markup"]["inline_keyboard"][0][0]["callback_data"].replace("k:", f"{action}:", 1)
        asyncio.run(self.bot.handle_update({"callback_query": {"id": "q", "from": {"id": CHAT, "username": OWNER},
                                                               "message": {"message_id": 3, "chat": {"id": CHAT, "type": "private"}}, "data": data}}))

    def test_whole_day_unanswered_sends_all_missed_at_once(self):
        slots = [(10, 0), (11, 18), (12, 36), (13, 54), (15, 12), (16, 30), (17, 48), (19, 6), (20, 24), (21, 42)]
        for h, m in slots:
            self.tick(h, m)
        self.assertEqual(len(self.cards()), 1)  # only the 10:00 card, the other 9 slots were skipped
        self.answer(self.cards()[0], "k")
        batch = self.cards()[1:]
        self.assertEqual(len(batch), 9)
        self.assertEqual(len({c["text"] for c in batch}), 9)  # nine different words
        # the batch is pending: the next slot is skipped again until all of them are answered
        self.tick(10, 0, day=(2026, 9, 22))
        self.assertEqual(len(self.cards()), 10)

    def test_missed_count_is_capped_at_cards_per_day(self):
        self.tick(10, 0)
        for day in (21, 22, 23):
            for h, m in [(11, 18), (12, 36), (13, 54), (15, 12), (16, 30), (17, 48), (19, 6), (20, 24), (21, 42)]:
                self.tick(h, m, day=(2026, 9, day))
        self.assertEqual(self.store.json(f"sched:{KEY}"), {"missed": 10})
        self.answer(self.cards()[0])
        self.assertEqual(len(self.cards()), 11)

    def test_batch_answered_one_by_one_then_nothing_extra(self):
        self.tick(10, 0)
        self.tick(11, 18)
        self.tick(12, 36)
        self.answer(self.cards()[0])
        self.assertEqual(len(self.cards()), 3)
        for card in self.cards()[1:]:
            self.answer(card)
        self.assertEqual(len(self.cards()), 3)  # no debt left


if __name__ == "__main__":
    unittest.main()
