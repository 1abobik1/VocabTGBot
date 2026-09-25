import asyncio
import json
import random
import re
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from shared import ai, cards
from shared import curriculum as cur
from shared import exercises as ex
from shared import practice as pr
from shared import words as w
from shared.bot import Bot
from shared.schedule import Schedule
from tests.test_bot import CHAT, OWNER, FakeStore, FakeTelegram, use_clock
from tests.test_generator import FakeAI

MSK = timezone(timedelta(hours=3))
NOW = datetime(2026, 9, 24, 20, 0, tzinfo=MSK)
KEY = OWNER.lower()


def item(sentence, answer, rule, options=None, accepted=None, word=None, explanation="Объяснение правила."):
    raw = {"sentence": sentence, "answer": answer, "rule": rule, "explanation_ru": explanation,
           "accepted": accepted or [], "options": options or []}
    if word:
        raw["word"] = word
    return raw


def exercise(raw):
    return ai.from_json(ai.GeneratedExercise, raw)


PREP = item("We met ___ Monday.", "on", "a1_prepositions_time", options=["in", "on", "at", "by"])
TENSE = item("She ___ (live) here since 2020.", "has lived", "a2_present_perfect", accepted=["'s lived"])
VOCAB = item("The concert was ___ (cancel) because of the rain.", "cancelled", "vocab", word="cancel")
ARTICLE = item("It was ___ amazing day.", "an", "a2_articles", options=["a", "an", "the", "-"])
FORM = item("This bag is ___ (heavy) than mine.", "heavier", "a1_comparatives")


def batch(*items):
    return {"response": {"exercises": list(items)}}


def entry(rule, verdict, days_ago=1):
    return {"at": (NOW - timedelta(days=days_ago)).isoformat(), "rule": rule, "verdict": verdict,
            "sentence": "", "answer": "", "given": ""}


class ValidationTest(unittest.TestCase):
    def test_good_exercises_pass(self):
        parsed = ex.parse(batch(PREP, TENSE, VOCAB, ARTICLE, FORM), vocab_words=["cancel"], limit=10)
        self.assertEqual([x["rule"] for x in parsed],
                         ["a1_prepositions_time", "a2_present_perfect", "vocab", "a2_articles", "a1_comparatives"])
        self.assertEqual([x["mode"] for x in parsed], ["choice", "form", "form", "choice", "form"])
        self.assertEqual(parsed[0]["group"], "prepositions")
        self.assertEqual(parsed[0]["options"], ["in", "on", "at", "by"])
        self.assertEqual(parsed[2]["word"], "cancel")
        self.assertEqual(parsed[1]["accepted"], ["'s lived"])

    def test_broken_exercises_are_dropped(self):
        broken = [
            item("___ you ___ (finish) it?", "Have finished", "a2_present_perfect"),            # два пропуска
            item("They ___ their homework.", "had finished", "b1_past_perfect"),              # нет глагола в скобках
            item("Good ___ math.", "at", "a1_prepositions_common", options=["in", "on"]),     # ответа нет в вариантах
            item("I ___ it.", "love", "vocab", word="love"),                                     # слова нет в словаре
            item("She ___ (go) home.", "went", "a1_past_simple", explanation="Past Simple 到现在."),  # иероглифы
            item("She ___ (go) home.", "went", "a1_past_simple", explanation="Past simple is used."),  # не по-русски
            item("I ___ (see) it.", "saw", "made_up_rule"),                                      # правила нет в программе
            "not an exercise",
        ]
        self.assertEqual(ex.parse(batch(*broken), vocab_words=["cancel"], limit=10), [])

    def test_only_requested_rules_are_kept(self):
        parsed = ex.parse(batch(PREP, TENSE), requested={"a2_present_perfect"}, limit=5)
        self.assertEqual([x["rule"] for x in parsed], ["a2_present_perfect"])

    def test_bare_list_and_code_fences_are_accepted(self):
        text = "```json\n" + json.dumps([PREP, TENSE]) + "\n```"
        for output in ({"choices": [{"message": {"content": text}}]}, {"response": [PREP, TENSE]}):
            self.assertEqual([x["rule"] for x in ex.parse(output, limit=5)], ["a1_prepositions_time", "a2_present_perfect"])

    def test_legacy_rule_ids_are_mapped(self):
        parsed = ex.parse(batch(dict(PREP, rule="prep_time")), limit=5)
        self.assertEqual(parsed[0]["rule"], "a1_prepositions_time")

    def test_legacy_items_from_kv_are_normalized(self):
        old = {"type": "preposition", "sentence": "We met ___ Monday.", "answer": "on", "rule": "prep_time",
               "options": ["in", "on"], "explanation": "Дни недели — on.", "accepted": []}
        item_ = ex.normalize(old)
        self.assertEqual((item_["rule"], item_["group"], item_["mode"]), ("a1_prepositions_time", "prepositions", "choice"))
        self.assertTrue(ex.uses_buttons(item_))
        self.assertEqual(ex.normalize(dict(old, type="vocab", rule="vocab"))["mode"], "form")

    def test_vocab_answer_is_a_form_of_a_dictionary_word(self):
        words = ["cancel", "look forward to", "reliable"]
        for answer, word in (("cancelled", "cancel"), ("looking forward to", "look forward to"),
                             ("reliability", "reliable"), ("our trip", None), ("looking at", None)):
            raw = item("I am ___ (x) it.", answer, "vocab")
            parsed = ex.validate(exercise(raw), vocab_words=words)
            self.assertEqual(parsed and parsed["word"], word, answer)

    def test_long_gaps_are_normalized_and_duplicates_removed(self):
        raw = item("We met ______ Monday.", "on", "a1_prepositions_time", options=["on", "in"])
        parsed = ex.parse(batch(raw, raw), limit=5)
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["sentence"], "We met ___ Monday.")


class GradingTest(unittest.TestCase):
    def test_buttons_are_exact_and_typing_allows_typos(self):
        prep, tense = ex.parse(batch(PREP, TENSE), limit=5)
        self.assertEqual(ex.grade(prep, "on"), pr.OK)
        self.assertEqual(ex.grade(prep, "in"), pr.WRONG)
        self.assertEqual(ex.grade(tense, "has lived"), pr.OK)
        self.assertEqual(ex.grade(tense, "'s lived"), pr.OK)
        self.assertEqual(ex.grade(tense, "has lvied"), pr.NEAR)
        self.assertEqual(ex.grade(tense, "lived"), pr.WRONG)        # пропущен has — это не опечатка
        self.assertEqual(ex.grade(tense, "have lived"), pr.WRONG)   # согласование — тоже ошибка правила
        self.assertEqual(ex.grade(tense, "has lievd"), pr.NEAR)     # переставлены две буквы

    def test_feedback_shows_the_filled_sentence_and_translation(self):
        form = ex.validate(exercise(item("She drives very ___ (careful) when it rains.", "carefully", "b1_adverbs",
                                         explanation="Для описания действия нужно наречие.")))
        form["translation"] = "Она водит очень осторожно, когда идёт дождь."
        text = cards.render_exercise_feedback(form, "carefully", pr.OK)
        self.assertIn("She drives very <b>carefully</b> when it rains.\nОна водит очень осторожно", text)
        self.assertNotIn("(careful)", text)
        article = ex.validate(exercise(item("Can you see ___ owl?", "an", "a2_articles", options=["a", "an", "the", "-"])))
        self.assertIn("Can you see <b>an</b> owl?", cards.render_exercise_feedback(article, "an", pr.OK))
        stray = ex.validate(exercise(item("Money can't buy ___. (happy)", "happiness", "b1_adverbs")))
        self.assertIn("Money can't buy <b>happiness</b>.", cards.render_exercise_feedback(stray, "happiness", pr.OK))

    def test_translation_is_optional_and_validated(self):
        raw = dict(PREP, translation_ru="Мы встретились в понедельник.")
        self.assertEqual(ex.validate(exercise(raw))["translation"], "Мы встретились в понедельник.")
        self.assertEqual(ex.validate(exercise(dict(PREP, translation_ru="We met on Monday.")))["translation"], "")
        self.assertEqual(ex.validate(exercise(PREP))["translation"], "")

    def test_weak_spots(self):
        log = [entry("a1_prepositions_time", "wrong"), entry("prep_time", "wrong"), entry("a1_prepositions_time", "ok"),
               entry("a2_present_perfect", "wrong"), entry("a2_present_perfect", "ok"), entry("a2_present_perfect", "ok"),
               entry("a2_articles", "ok"), entry("a2_articles", "ok"),
               entry("a1_past_simple", "wrong"),                                  # одна попытка — рано судить
               entry("b1_conditional_2", "wrong", 60), entry("b1_conditional_2", "wrong", 60)]  # старое
        weak = ex.weak_spots(log, NOW)
        self.assertEqual([s["rule"] for s in weak], ["a1_prepositions_time"])     # старый id засчитан туда же
        self.assertEqual((weak[0]["wrong"], weak[0]["count"]), (2, 3))

    def test_request_lists_one_line_per_planned_rule(self):
        plan = [cur.BY_ID["a1_prepositions_time"], cur.BY_ID["a2_present_perfect"], cur.VOCAB]
        prompt = ex.build_request("B1", plan, ["cancel"],
                                  [{"sentence": "We met ___ Monday.", "given": "in", "answer": "on"}])
        user = prompt["messages"][1]["content"]
        self.assertIn("1. rule 'a1_prepositions_time' (choice): prepositions of time in/on/at.", user)
        self.assertIn("2. rule 'a2_present_perfect' (form)", user)
        self.assertIn("3. rule 'vocab' (form): one of the learner's words in the right form — cancel", user)
        self.assertIn("answered 'in', correct 'on'", user)
        self.assertIn("CEFR level B1", prompt["messages"][0]["content"])
        self.assertNotIn("response_format", prompt)                            # см. shared/ai.py
        self.assertIn('Answer with JSON only, in this shape: {"exercises":[{"rule":"","sentence":"","options":[""],'
                      '"answer":""', prompt["messages"][0]["content"])

    def test_generate_tops_up_dropped_rules(self):
        plan = [cur.BY_ID["a1_prepositions_time"], cur.BY_ID["a2_present_perfect"]]
        fake = FakeAI(batch(PREP, dict(TENSE, sentence="broken")), batch(TENSE))
        with mock.patch.object(cur, "plan_batch", return_value=plan):
            result = asyncio.run(ex.generate(fake, "B1", 2, list(cur.GROUPS), now=NOW))
        self.assertEqual([x["rule"] for x in result], ["a1_prepositions_time", "a2_present_perfect"])
        retry = fake.calls[1][1]["messages"][1]["content"]
        self.assertIn("a2_present_perfect", retry)
        self.assertNotIn("a1_prepositions_time", retry)                         # добор только по отбракованным


    def test_one_exercise_per_planned_rule(self):
        plan = [cur.BY_ID["a1_prepositions_time"], cur.VOCAB]
        second_vocab = dict(VOCAB, sentence="They ___ (cancel) the trip.", answer="cancelled")
        fake = FakeAI(batch(VOCAB, second_vocab), batch(PREP))
        with mock.patch.object(cur, "plan_batch", return_value=plan):
            result = asyncio.run(ex.generate(fake, "B1", 2, list(cur.GROUPS), ["cancel"], now=NOW))
        self.assertEqual(sorted(x["rule"] for x in result), ["a1_prepositions_time", "vocab"])


class CurriculumTest(unittest.TestCase):
    def test_program_levels(self):
        self.assertEqual(cur.LEVELS, ("A1", "A2", "B1", "B2"))
        self.assertTrue(all(r.level in cur.LEVELS and r.group in cur.GROUPS for r in cur.RULES))
        self.assertEqual(len({r.id for r in cur.RULES}), len(cur.RULES))
        self.assertTrue(all(v in cur.BY_ID for v in cur.LEGACY_RULES.values()))
        self.assertIsNone(cur.normalize_level("c1"))
        self.assertEqual(cur.normalize_level(" b2 "), "B2")
        self.assertEqual(cur.clamp_level("C1"), "B2")
        self.assertEqual(cur.clamp_level(None), cur.DEFAULT_LEVEL)

    def test_enabled_groups(self):
        self.assertEqual(cur.enabled_groups({}), list(cur.GROUPS))
        self.assertEqual(cur.enabled_groups({"ex_types": ["tense", "tenses"]}), ["tenses"])  # старое имя темы
        self.assertNotIn("vocab", cur.enabled_groups({}, has_vocab=False))
        self.assertEqual(cur.enabled_groups({"ex_types": ["vocab"]}, has_vocab=False), ["tenses"])

    def test_status_needs_attempts_and_accuracy(self):
        self.assertEqual(cur.status([]), cur.NOT_STARTED)
        self.assertEqual(cur.status(["ok"] * 3), cur.LEARNING)                 # мало попыток
        self.assertEqual(cur.status(["wrong"] + ["ok"] * 4), cur.CONFIDENT)    # 4 из последних 5
        self.assertEqual(cur.status(["ok"] * 5 + ["wrong", "wrong"]), cur.LEARNING)

    def test_progress_and_working_level(self):
        a1 = [r.id for r in cur.RULES if r.level == "A1"]
        log = [entry(rule, "ok") for rule in a1 for _ in range(4)]
        rows = cur.progress(log, "B1")
        self.assertEqual([r["level"] for r in rows], ["A1", "A2", "B1"])
        self.assertEqual((rows[0]["confident"], rows[0]["total"]), (len(a1), len(a1)))
        self.assertEqual(rows[1]["confident"], 0)
        self.assertEqual(cur.working_level(log, "B1"), "B1")                  # пробелов нет
        gap = log + [entry("a2_articles", "wrong"), entry("a2_articles", "ok")]
        self.assertEqual(cur.working_level(gap, "B1"), "A2")                  # ошибки на A2 — работаем там
        self.assertEqual(cur.working_level([], "B1"), "B1")                   # непроверенное — не пробел
        self.assertEqual(cur.working_level(log, "A1"), "A1")
        self.assertEqual(cur.scope_levels(log, "A1"), ["A1", "A2"])            # всё освоено — заглядываем выше

    def test_plan_mixes_weak_new_and_review(self):
        weak = [entry("a2_articles", "wrong")] * 3
        confident = [entry("a1_to_be", "ok")] * 5
        plan = cur.plan_batch(confident + weak, NOW, w.parse_iso, "B1", list(cur.GROUPS), 5, rng=random.Random(1))
        ids = [r.id for r in plan]
        self.assertEqual(len(ids), 5)
        self.assertIn("a2_articles", ids)                                    # слабое
        self.assertIn("a1_to_be", ids)                                       # повтор освоенного
        self.assertTrue(all(cur.LEVELS.index(r.level) <= cur.LEVELS.index("B1") for r in plan))
        self.assertGreaterEqual(sum(r.level == "B1" for r in plan), 2)      # новые — в основном своего уровня

    def test_plan_respects_groups_and_vocab(self):
        plan = cur.plan_batch([], NOW, w.parse_iso, "B2", ["modals", "vocab"], 5, include_vocab=True,
                              rng=random.Random(2))
        self.assertEqual(plan[-1], cur.VOCAB)
        self.assertTrue(all(r.group == "modals" for r in plan[:-1]))
        self.assertEqual(len(plan), 5)


class AIContractTest(unittest.TestCase):
    def test_example_comes_from_dataclasses(self):
        self.assertEqual(json.loads(ai.example_json(ai.Enrichment)),
                         {"examples": [{"en": "", "ru": ""}], "synonyms": [{"en": "", "ru": ""}]})
        self.assertEqual(list(ai.example(ai.GeneratedExercise))[:4], ["rule", "sentence", "options", "answer"])
        self.assertIsNone(ai.from_json(ai.GeneratedExercise, {"rule": "x", "sentence": "y", "answer": "z"}))
        exercise_ = ai.from_json(ai.GeneratedExercise, {"rule": "x", "sentence": "y", "answer": "z", "explanation_ru": "п"})
        self.assertEqual((exercise_.options, exercise_.word), ([], ""))

    def test_lenient_parsing(self):
        parsed = ai.parse(ai.Enrichment, {"response": 'Sure! {"examples": [{"en": "Hi.", "ru": "Привет."}, '
                                                      '{"en": 5}], "synonyms": null} Bye'})
        self.assertEqual(parsed.examples, [ai.Pair("Hi.", "Привет.")])        # битый пример отброшен
        self.assertEqual(parsed.synonyms, [])
        self.assertIsNone(ai.parse(ai.Enrichment, {"response": "no json"}))
        self.assertIsNone(ai.from_json(ai.Pair, {"en": "x"}))                 # нет обязательного поля
        self.assertEqual(ai.from_json(ai.Pair, {"en": 1, "ru": "один"}), ai.Pair("1", "один"))


class ExerciseFlowTest(unittest.TestCase):
    def setUp(self):
        self.store, self.tg, self.ai = FakeStore(), FakeTelegram(), FakeAI()
        self.bot = Bot(self.store, self.tg, owner=OWNER, schedule=Schedule(), ai=self.ai)
        self.clock = use_clock(self, self.bot, NOW)
        # План правил выбирает код; здесь он берётся из следующего заготовленного ответа ИИ,
        # чтобы тесты сценариев не зависели от случайного плана. Сам план проверяет CurriculumTest.
        self.planner = mock.patch.object(cur, "plan_batch", side_effect=self.plan_from_answer)
        self.planner.start()
        self.addCleanup(mock.patch.stopall)
        self.n = 0
        self.msg("/start")

    real_plan = staticmethod(cur.plan_batch)

    def plan_from_answer(self, log, now, parse_time, level, groups, count, include_vocab=False, rng=random):
        answer = self.ai.responses[0] if self.ai.responses else {}
        items = (answer.get("response") or {}).get("exercises") if isinstance(answer, dict) else None
        if not isinstance(items, list):
            return self.real_plan(log, now, parse_time, level, groups, count, include_vocab, rng)
        return [cur.BY_ID[cur.rule_id(x["rule"])] for x in items][:count]

    def msg(self, text, user=OWNER, chat=CHAT):
        self.n += 1
        asyncio.run(self.bot.handle_update({"message": {"message_id": self.n, "from": {"id": chat, "username": user},
                                                        "chat": {"id": chat, "type": "private"}, "text": text}}))

    def press(self, data, user=OWNER, chat=CHAT):
        self.n += 1
        asyncio.run(self.bot.handle_update({"callback_query": {
            "id": f"q{self.n}", "from": {"id": chat, "username": user},
            "message": {"message_id": 40, "chat": {"id": chat, "type": "private"}}, "data": data}}))

    def get(self, name, user=KEY):
        return self.store.json(f"{name}:{user}")

    def last_keyboard(self):
        return self.tg.sent()[-1].get("reply_markup", {}).get("inline_keyboard", [])

    def test_full_session(self):
        self.ai.responses.append(batch(PREP, TENSE, ARTICLE, FORM, dict(PREP, sentence="See you ___ Friday.")))
        self.msg(cards.EXERCISE_BUTTON)
        self.assertIn("✍️ <b>Упражнение 1/5</b> · предлоги времени in / on / at", self.tg.last_text())
        self.assertIn("We met ___ Monday.", self.tg.last_text())
        options = self.last_keyboard()[0]
        self.assertEqual([b["text"] for b in options], ["in", "on", "at", "by"])

        self.press(options[1]["callback_data"])                        # on — верно
        self.assertIn("✅ Верно: <b>on</b>", self.tg.sent()[-2]["text"])
        self.assertIn("Упражнение 2/5", self.tg.last_text())
        self.msg("has livd")                                            # опечатка
        self.assertIn("🟡 Почти", self.tg.sent()[-2]["text"])
        self.press(self.last_keyboard()[0][0]["callback_data"])        # a вместо an — ошибка
        self.assertIn("❌ Правильно: <b>an</b> (у тебя: a)", self.tg.sent()[-2]["text"])
        self.press("xd:3")                                              # спорное
        self.assertIn("Убрал это упражнение", self.tg.sent()[-2]["text"])
        self.press("xn:4")                                              # не знаю
        summary = self.tg.last_text()
        self.assertIn("✅ 1 из 4", summary)
        self.assertEqual(self.get("exsession"), {})
        log = self.get("mlog")
        self.assertEqual([e["verdict"] for e in log], ["ok", "near", "wrong", "wrong"])  # спорное не в журнале

    def test_stale_and_foreign_buttons_are_ignored(self):
        self.ai.responses.append(batch(PREP, TENSE))
        self.msg(cards.EXERCISE_BUTTON)
        self.press("xo:3:1")
        self.assertIn("уже неактуально", self.tg.last_text())
        self.assertEqual(self.get("exsession")["i"], 0)

    def test_typed_answer_does_not_become_a_word(self):
        self.ai.responses.append(batch(TENSE, PREP))
        self.msg(cards.EXERCISE_BUTTON)
        self.msg("has lived")
        self.assertIsNone(self.get("queue"))
        self.assertIn("✅ Верно", self.tg.sent()[-2]["text"])
        # на задании с кнопками текст — это снова добавление слова
        self.msg("cat - кот\nThe cat sleeps. - Кот спит.")
        self.assertEqual([x["en"] for x in self.get("queue")], ["cat"])

    def test_exercises_at_20_and_unfinished_ones_wait(self):
        self.ai.responses.append(batch(PREP, TENSE, ARTICLE))
        asyncio.run(self.bot.on_cron(NOW))
        self.assertIn("Упражнение 1/3", self.tg.last_text())
        calls = len(self.ai.calls)
        # на следующий день в 20:00 новая пачка не создаётся — присылается та же
        tomorrow = NOW + timedelta(days=1)
        self.clock.set(tomorrow)
        asyncio.run(self.bot.on_cron(tomorrow))
        self.assertIn("ещё ждут", self.tg.sent()[-2]["text"])
        self.assertIn("Упражнение 1/3", self.tg.last_text())
        self.assertEqual(len(self.ai.calls), calls)

    def test_exercises_do_not_block_cards(self):
        self.msg("cat - кот\nThe cat sleeps. - Кот спит.")
        self.ai.responses.append(batch(PREP, TENSE))
        self.msg(cards.EXERCISE_BUTTON)
        slot = datetime(2026, 9, 24, 21, 8, tzinfo=MSK)
        self.clock.set(slot)
        self.assertEqual(asyncio.run(self.bot.on_cron(slot)), [OWNER])
        self.assertTrue(self.tg.last_text().startswith("Кот"))

    def test_vocab_mistake_rolls_the_word_back(self):
        word = w.new_word("cancel", "отменить", [{"en": "Cancel it.", "ru": "Отмени."}])
        word.update(box=2, shown_count=4)
        asyncio.run(self.bot.repo.put(f"queue:{KEY}", [word, w.new_word("towel", "полотенце"), w.new_word("iron", "утюг")]))
        self.ai.responses.append(batch(VOCAB))
        self.msg(cards.EXERCISE_BUTTON)
        self.assertIn("cancel", self.ai.calls[-1][1]["messages"][1]["content"])  # слова из словаря в промпте
        self.press("xn:0")
        cancel = next(x for x in self.get("queue") if x["en"] == "cancel")
        self.assertEqual(cancel["box"], 1)
        self.assertEqual(cancel["due_at"], "2026-09-25T10:00:00+03:00")

    def test_weak_spots_drive_the_next_batch(self):
        log = [{"at": NOW.isoformat(), "rule": "prep_time", "verdict": "wrong", "sentence": "We met ___ Monday.",
                "answer": "on", "given": "in"}] * 3
        asyncio.run(self.bot.repo.put(f"mlog:{KEY}", log))
        self.planner.stop()                                        # настоящий план: слабое правило в нём
        self.ai.responses.append(batch(PREP))
        self.msg(cards.EXERCISE_BUTTON)
        user_prompt = self.ai.calls[0][1]["messages"][1]["content"]
        self.assertIn("rule 'a1_prepositions_time' (choice)", user_prompt)
        self.assertEqual(user_prompt.count("answered 'in', correct 'on'"), 1)  # одинаковые ошибки — один раз

    def test_topics_menu(self):
        self.msg(cards.TOPICS_BUTTON)
        self.assertIn("все темы программы по твоему уровню", self.tg.last_text())
        self.press("xt:vocab")                       # выключить одну тему
        chosen = self.get("settings")["ex_types"]
        self.assertNotIn("vocab", chosen)
        self.assertEqual(len(chosen), len(cur.GROUPS) - 1)
        self.press("xt:auto")
        self.assertEqual(self.get("settings")["ex_types"], [])
        self.bot.clock.set(NOW)
        asyncio.run(self.bot.repo.put(f"settings:{KEY}", {"ex_types": ["tense"]}))   # старое имя темы
        self.planner.stop()
        before = len(self.ai.calls)
        self.msg(cards.EXERCISE_BUTTON)
        prompt = self.ai.calls[before][1]["messages"][1]["content"]
        rules = re.findall(r"rule '(\w+)'", prompt)
        self.assertEqual(len(rules), ex.BATCH)
        self.assertTrue(all(cur.BY_ID[r].group == "tenses" for r in rules), rules)

    def test_weekly_report_has_exercise_stats_and_ai_advice(self):
        log = [{"at": NOW.isoformat(), "rule": "prep_time", "verdict": v, "sentence": "x ___", "answer": "on",
                "given": "in"} for v in ("wrong", "wrong", "ok")]
        asyncio.run(self.bot.repo.put(f"mlog:{KEY}", log))
        self.ai.responses.append({"response": "Чаще всего ошибаешься в предлогах времени: on — с днями недели."})
        sunday = datetime(2026, 9, 27, 21, 0, tzinfo=MSK)
        self.clock.set(sunday)
        asyncio.run(self.bot.on_cron(sunday))
        report = self.tg.last_text()
        self.assertIn("✍️ <b>Упражнения за неделю</b> — 3: ✅ 1 · 🟡 0 · ❌ 2", report)
        self.assertIn("🔎 Слабые места: предлоги времени in / on / at (2 из 3)", report)
        self.assertIn("💡 Чаще всего ошибаешься в предлогах времени", report)
        self.assertIn("rule 'предлоги времени in / on / at': 2 wrong of 3", self.ai.calls[-1][1]["messages"][1]["content"])
        self.assertIn("A1 ░░░░░░░░░░ 0% — сейчас изучаешь", report)          # ошибки на A1 — пробел там
        self.assertIn("Дальше на A1: предлоги времени in / on / at", report)
        self.assertIn("A1: 0 of 22 grammar points mastered", self.ai.calls[-1][1]["messages"][1]["content"])
        # кнопка статистики показывает последний разбор без нового запроса к ИИ
        calls = len(self.ai.calls)
        self.msg(cards.STATS_BUTTON)
        self.assertIn("💡 Чаще всего", self.tg.last_text())
        self.assertEqual(len(self.ai.calls), calls)

    def test_pool_is_refilled_after_a_batch_and_served_instantly(self):
        self.ai.responses.append(batch(PREP))                      # первая пачка — генерация на месте
        self.msg(cards.EXERCISE_BUTTON)
        self.assertIn("⏳ Готовлю", self.tg.sent()[-2]["text"])
        self.ai.responses.append(batch(TENSE, ARTICLE))            # фоновая: следующая пачка
        self.press("xo:0:0")                                       # in — ошибка
        # после итога следующая пачка уже лежит в запасе и учитывает только что сделанную ошибку
        pool = self.get("expool")
        self.assertEqual([x["rule"] for x in pool["items"]], ["a2_present_perfect", "a2_articles"])
        self.assertEqual(pool["level"], "B1")
        calls = len(self.ai.calls)
        self.msg(cards.EXERCISE_BUTTON)
        self.assertEqual(len(self.ai.calls), calls)                # из запаса — без ожидания ИИ
        self.assertNotIn("Готовлю", self.tg.sent()[-2]["text"])
        self.assertIn("Упражнение 1/2", self.tg.last_text())
        self.assertEqual(self.get("expool"), {})

    def test_refill_prompt_uses_fresh_mistakes(self):
        self.ai.responses.append(batch(PREP, dict(PREP, sentence="See you ___ Friday.")))
        self.msg(cards.EXERCISE_BUTTON)
        self.press("xo:0:0")
        self.press("xo:1:0")                                       # две ошибки в предлогах времени
        refill_prompt = self.ai.calls[-1][1]["messages"][1]["content"]   # запаса нет в ответах — план настоящий
        self.assertIn("rule 'a1_prepositions_time' (choice)", refill_prompt)
        self.assertIn("answered 'in', correct 'on'", refill_prompt)

    def test_background_work_goes_through_defer(self):
        deferred = []
        self.bot.defer = deferred.append
        self.ai.responses.append(batch(PREP))
        self.msg(cards.EXERCISE_BUTTON)
        calls = len(self.ai.calls)
        self.press("xo:0:1")
        self.assertIn("🏁", self.tg.last_text())                  # итог пришёл, не дожидаясь генерации
        self.assertEqual(len(self.ai.calls), calls)
        self.assertEqual(len(deferred), 1)
        self.ai.responses.append(batch(TENSE))
        asyncio.run(deferred[0])
        self.assertEqual([x["rule"] for x in self.get("expool")["items"]], ["a2_present_perfect"])

    def test_level_or_topic_change_replaces_the_pool(self):
        asyncio.run(self.bot.repo.put(f"expool:{KEY}", {"items": ex.parse(batch(PREP), limit=5), "level": "B1",
                                                        "groups": list(cur.GROUPS)}))
        self.ai.responses.append(batch(TENSE))
        self.press("glv:B2")
        pool = self.get("expool")
        self.assertEqual((pool["level"], [x["rule"] for x in pool["items"]]), ("B2", ["a2_present_perfect"]))
        self.assertIn("CEFR level B2", self.ai.calls[-1][1]["messages"][0]["content"])

    def test_stale_pool_is_not_served(self):
        asyncio.run(self.bot.repo.put(f"expool:{KEY}", {"items": ex.parse(batch(PREP), limit=5), "level": "A1",
                                                        "groups": list(cur.GROUPS)}))
        self.ai.responses.append(batch(TENSE))
        self.msg(cards.EXERCISE_BUTTON)
        self.assertIn("Готовлю", self.tg.sent()[-2]["text"])
        self.assertIn("(live)", self.tg.last_text())

    def test_cron_fills_an_empty_pool_before_20(self):
        self.ai.responses.append(batch(PREP, TENSE))
        before = NOW - timedelta(minutes=20)
        self.clock.set(before)
        asyncio.run(self.bot.on_cron(before))
        self.assertEqual(len(self.get("expool")["items"]), 2)
        calls = len(self.ai.calls)
        self.clock.set(NOW)
        asyncio.run(self.bot.on_cron(NOW))                        # в 20:00 — сразу из запаса
        self.assertEqual(len(self.ai.calls), calls)
        self.assertIn("Упражнение 1/2", self.tg.last_text())

    def test_users_have_separate_contexts(self):
        self.msg("/allow @Friend")
        self.msg("/start", user="friend", chat=2002)
        self.msg("cat - кот\nThe cat sleeps. - Кот спит.")                         # владелец
        self.msg("dog - собака\nThe dog barks. - Пёс лает.", user="friend", chat=2002)
        self.press("glv:B2", user="friend", chat=2002)
        self.ai.responses.append(batch(PREP, TENSE))
        self.msg(cards.EXERCISE_BUTTON, user="friend", chat=2002)

        self.assertEqual([x["en"] for x in self.get("queue")], ["cat"])
        self.assertEqual([x["en"] for x in self.get("queue", "friend")], ["dog"])
        self.assertIsNone((self.get("settings") or {}).get("level"))
        self.assertEqual(self.get("settings", "friend")["level"], "B2")
        self.assertIn("CEFR level B2", self.ai.calls[-1][1]["messages"][0]["content"])
        self.assertIsNone(self.get("exsession"))
        self.assertTrue(self.get("exsession", "friend")["items"])
        self.assertEqual(self.tg.sent()[-1]["chat_id"], 2002)
        # ответ владельца не попадает в упражнения друга
        self.msg("on")
        self.assertEqual(self.get("exsession", "friend")["i"], 0)
        self.assertEqual(self.get("seen"), ["cat"])
        self.assertEqual(self.get("seen", "friend"), ["dog"])


if __name__ == "__main__":
    unittest.main()
