import asyncio
import unittest
from datetime import datetime, timedelta, timezone

from shared import cards
from shared import practice as pr
from shared import srs
from shared import words as w
from shared.bot import Bot
from shared.schedule import Schedule
from tests.test_bot import CHAT, OWNER, FakeStore, FakeTelegram, use_clock

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

    def test_transcription_is_ignored(self):
        for answer in ("towel", "Towel!", "towel [ˈtaʊəl]"):
            self.assertEqual(pr.grade(answer, "towel [ˈtaʊəl]", True), pr.OK, answer)
        self.assertEqual(pr.grade("towl", "towel [ˈtaʊəl]", True), pr.NEAR)
        self.assertEqual(pr.grade("mirror", "towel [ˈtaʊəl]", True), pr.WRONG)
        # транскрипция не мешает и в обратную сторону, и в разборе ответа целой строкой
        self.assertEqual(pr.split_answers("towel, mirror", ["towel [ˈtaʊəl]", "mirror [ˈmɪrər]"], True),
                         ["towel", "mirror"])
        self.assertEqual(pr.grade("утюг", "Утюг [ˈaɪərn]", False), pr.OK)

    def test_any_of_several_translations_counts(self):
        expected = "couch / sofa [kaʊtʃ / ˈsoʊfə]"
        for answer in ("sofa", "couch", "couch sofa", "couch / sofa"):
            self.assertEqual(pr.grade(answer, expected, True), pr.OK, answer)
        self.assertEqual(pr.grade("chair", expected, True), pr.WRONG)
        self.assertEqual(pr.grade("догнать", "догнать, наверстать", False), pr.OK)
        self.assertEqual(pr.grade("замужем", "женат или замужем", False), pr.OK)

    def test_other_variants_are_suggested_but_not_required(self):
        expected = "couch / sofa [kaʊtʃ / ˈsoʊfə]"
        self.assertEqual(pr.other_variants(expected, "sofa", True), ["couch"])
        self.assertEqual(pr.other_variants(expected, "couch", True), ["sofa"])
        self.assertEqual(pr.other_variants(expected, "couch sofa", True), [])  # написаны оба
        self.assertEqual(pr.other_variants("towel [ˈtaʊəl]", "towel", True), [])  # вариант один
        graded = pr.grade_round([{"en": expected, "ru": "диван"}], pr.RU_EN, "sofa")
        self.assertEqual(graded[0][2:], (pr.OK, ["couch"]))

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
        now = datetime(2026, 9, 26, 22, 30, tzinfo=MSK)
        outcome = pr.apply_results(queue, known, practice, results, Schedule(), now)
        self.assertEqual([x["en"] for x in known], ["a"])
        self.assertEqual(a["archived_at"], now.isoformat())
        self.assertEqual([x["en"] for x in queue], ["q1", "q2", "q3", "b", "c"])
        self.assertEqual((b["stage"], c["stage"]), (1, 0))
        # опечатка и ошибка возвращаются в обучение завтра утром, а не в тот же вечер
        for word in (b, c):
            self.assertEqual((word["box"], word["due_at"]), (0, "2026-09-27T10:00:00+03:00"))
        self.assertEqual(practice, [])
        self.assertEqual({k: [x["en"] for x in v] for k, v in outcome.items()}, {"ok": ["a"], "near": ["b"], "wrong": ["c"]})


class PracticeFlowTest(unittest.TestCase):
    def setUp(self):
        self.store, self.tg = FakeStore(), FakeTelegram()
        self.bot = Bot(self.store, self.tg, owner=OWNER, schedule=Schedule())
        self.clock = use_clock(self, self.bot, at(8, 0))
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
        self.clock.set(when)
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

    def test_evening_practice_full_flow(self):
        self.seed_practice([
            ("loud", "звонкий", ("The bell is loud.", "Звонок громкий.")),
            ("silence", "тишина", None),
            ("hit it off", "сразу поладить", None),
            ("sound", "звук", None),
        ])
        self.put("queue", [w.new_word(n, n + "_ru") for n in ("q1", "q2", "q3", "q4", "q5")])

        self.assertEqual(self.tick(at(22, 29)), [])
        self.assertEqual(self.tick(at(22, 30)), [OWNER])  # практика каждый день в конце дня
        round1 = self.tg.last_text()
        self.assertIn("1/3.</b> Напиши по-английски", round1)
        self.assertIn("1) Звонкий\n2) Тишина\n3) Сразу поладить\n4) Звук", round1)

        # слот, попавший на практику, уходит в долг
        self.tick(at(22, 4))
        self.assertEqual(self.cards_sent(), [])
        self.assertEqual(self.get("sched")["missed"], 1)

        self.msg("loud silense hit it off sound")  # опечатка в "silence"
        self.assertIn("2) 🟡 silense → <b>silence</b>", self.tg.sent()[-2]["text"])
        self.assertIn("1) ✅ loud", self.tg.sent()[-2]["text"])
        self.assertIn("2/3.</b> Теперь по-русски", self.tg.last_text())

        self.msg("звонкий, тишина, поладить, тихий")  # 3 почти, 4 неверно
        self.assertIn("4) ❌ тихий → <b>звук</b>", self.tg.sent()[-2]["text"])
        sentences = self.tg.last_text()
        self.assertIn("3/3.</b> Предложения", sentences)
        self.assertIn("1) Звонок громкий.\n<tg-spoiler>The bell is loud.</tg-spoiler>", sentences)

        self.msg("The bell is loud")  # предложения по желанию
        texts = [p["text"] for p in self.tg.sent()[-5:]]
        self.assertTrue(any("Сверь с эталоном:\n1) The bell is loud." in t for t in texts))
        self.assertTrue(any("В архив: loud" in t for t in texts))

        self.assertEqual([x["en"] for x in self.get("known")], ["loud"])
        self.assertEqual(self.get("practice"), [])
        self.assertEqual(self.get("session"), {})
        # опечатка -> завтра EN→RU, ошибки -> завтра с самого начала
        back = {x["en"]: x for x in self.get("queue") if x["en"] in ("silence", "sound", "hit it off")}
        self.assertEqual(back["silence"]["stage"], 1)
        self.assertEqual((back["sound"]["stage"], back["hit it off"]["stage"]), (0, 0))
        for word in back.values():
            self.assertEqual(word["due_at"], "2026-09-27T10:00:00+03:00")

        # пропущенный слот доезжает сразу после практики
        self.assertEqual(len(self.cards_sent()), 1)
        self.assertEqual(self.get("sched")["missed"], 0)

    def test_word_with_transcription_and_variants_is_archived(self):
        self.seed_practice([("towel [ˈtaʊəl]", "полотенце", None), ("couch / sofa [kaʊtʃ / ˈsoʊfə]", "диван", None)])
        self.msg("/practice")
        self.msg("towel, sofa")
        feedback = self.tg.sent()[-2]["text"]
        self.assertIn("1) ✅ towel [ˈtaʊəl]", feedback)
        self.assertIn("можно и так: couch", feedback)
        self.msg("полотенце, диван")
        self.assertEqual([x["en"] for x in self.get("known")], ["towel [ˈtaʊəl]", "couch / sofa [kaʊtʃ / ˈsoʊfə]"])
        self.assertEqual(self.get("queue"), [])  # обе карточки ушли в архив

    def test_weekly_stats_show_practice_mistakes(self):
        self.seed_practice([("towel [ˈtaʊəl]", "полотенце", None), ("mirror", "зеркало", None), ("sofa", "диван", None)])
        self.msg("/practice")
        self.msg("towel, miror, chair")      # верно, опечатка, ошибка
        self.msg("полотенце, зеркало, стул")  # верно, верно, ошибка
        log = self.get("plog")
        self.assertEqual([(x["en"], x["ru_en"], x["en_ru"]) for x in log],
                         [("towel [ˈtaʊəl]", "ok", "ok"), ("mirror", "near", "ok"), ("sofa", "wrong", "wrong")])
        self.msg(cards.STATS_BUTTON)
        text = self.tg.last_text()
        self.assertIn("🧠 <b>Практика за неделю</b> — слов: 3", text)
        self.assertIn("RU→EN (писал по-английски): ✅ 1 · 🟡 1 · ❌ 1", text)
        self.assertIn("EN→RU (писал по-русски): ✅ 2 · 🟡 0 · ❌ 1", text)
        self.assertIn("Чаще всего ошибки: sofa (2), mirror (1)", text)

    def test_old_practice_is_not_in_the_weekly_stats(self):
        self.put("plog", [{"at": "2026-09-01T10:00:00+00:00", "en": "old", "ru_en": "wrong", "en_ru": "wrong"}])
        self.msg(cards.STATS_BUTTON)
        self.assertIn("Практика за неделю</b>: пока не было", self.tg.last_text())

    def test_practice_takes_words_in_batches(self):
        self.bot.schedule = Schedule(practice_batch=2)
        self.seed_practice([(f"w{i}", f"с{i}", None) for i in range(5)])
        self.tick(at(22, 30))
        self.assertEqual(len(self.get("session")["ids"]), 2)
        self.assertIn("🧠 <b>Практика</b> — 2 сл.", self.tg.last_text())
        self.msg("w0 w1")
        self.msg("с0 с1")
        self.assertEqual(self.get("session"), {})
        self.assertEqual(len(self.get("known")), 2)
        self.assertEqual(len(self.get("practice")), 3)  # остальные ждут следующего вечера

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
        self.assertEqual(self.tick(at(22, 30)), [])

    def test_unfinished_practice_is_reminded_next_evening(self):
        self.seed_practice([("silence", "тишина", None)])
        self.tick(at(22, 30))
        self.tick(at(22, 30, day=(2026, 9, 27)))
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
        self.clock = use_clock(self, self.bot, datetime(2026, 9, 21, 8, 0, tzinfo=MSK))
        asyncio.run(self.bot.handle_update({"message": {"message_id": 1, "from": {"id": CHAT, "username": OWNER},
                                                        "chat": {"id": CHAT, "type": "private"}, "text": "/start"}}))
        asyncio.run(self.bot.repo.put(f"queue:{KEY}", [w.new_word(f"w{i}", f"с{i}") for i in range(20)]))

    SLOTS = [(10, 0), (10, 55), (11, 51), (12, 47), (13, 42), (14, 38), (15, 34),
             (16, 30), (17, 25), (18, 21), (19, 17), (20, 12), (21, 8), (22, 4)]

    def tick(self, h, m, day=(2026, 9, 21)):
        when = datetime(*day, h, m, tzinfo=MSK)
        self.clock.set(when)
        asyncio.run(self.bot.on_cron(when))

    def cards(self):
        return [p for p in self.tg.sent() if "reply_markup" in p and p["reply_markup"].get("inline_keyboard", [[{}]])[0][0].get("callback_data", "").startswith("k:")]

    def answer(self, card, action="n"):
        data = card["reply_markup"]["inline_keyboard"][0][0]["callback_data"].replace("k:", f"{action}:", 1)
        asyncio.run(self.bot.handle_update({"callback_query": {"id": "q", "from": {"id": CHAT, "username": OWNER},
                                                               "message": {"message_id": 3, "chat": {"id": CHAT, "type": "private"}}, "data": data}}))

    def test_whole_day_unanswered_sends_all_missed_at_once(self):
        for h, m in self.SLOTS:
            self.tick(h, m)
        self.assertEqual(len(self.cards()), 1)  # только карточка 10:00, остальные слоты пропущены
        self.assertEqual(self.store.json(f"sched:{KEY}")["missed"], 13)
        self.answer(self.cards()[0], "n")
        batch = self.cards()[1:]
        # долг закрывается пачкой, но дневная квота новых слов (6) не превышается
        self.assertEqual(len(batch), 5)
        self.assertEqual(len({c["text"] for c in batch}), 5)
        self.assertEqual(self.store.json(f"sched:{KEY}")["new"], 6)
        # пачка ждёт ответов: следующий слот снова уходит в долг
        self.tick(10, 0, day=(2026, 9, 22))
        self.assertEqual(len(self.cards()), 6)

    def test_missed_count_is_capped_at_cards_per_day(self):
        self.tick(10, 0)
        for day in (21, 22, 23):
            for h, m in self.SLOTS[1:]:
                self.tick(h, m, day=(2026, 9, day))
        self.assertEqual(self.store.json(f"sched:{KEY}")["missed"], 14)
        self.answer(self.cards()[0], "n")
        self.assertEqual(len(self.cards()), 7)  # 1 + 5 новых до квоты + отложенное первое слово

    def test_batch_answered_one_by_one_then_nothing_extra(self):
        self.tick(10, 0)
        self.tick(10, 55)
        self.tick(11, 51)
        self.answer(self.cards()[0], "n")
        self.assertEqual(len(self.cards()), 3)
        for card in self.cards()[1:]:
            self.answer(card, "n")
        self.assertEqual(len(self.cards()), 3)  # долгов не осталось


if __name__ == "__main__":
    unittest.main()
