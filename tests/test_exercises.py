import asyncio
import json
import unittest
from datetime import datetime, timedelta, timezone

from shared import cards
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


def item(kind, sentence, answer, rule, options=None, accepted=None, word=None, explanation="Объяснение правила."):
    raw = {"type": kind, "sentence": sentence, "answer": answer, "rule": rule, "explanation_ru": explanation,
           "accepted": accepted or [], "options": options or []}
    if word:
        raw["word"] = word
    return raw


PREP = item("preposition", "We met ___ Monday.", "on", "prep_time", options=["in", "on", "at", "by"])
TENSE = item("tense", "She ___ (live) here since 2020.", "has lived", "present_perfect", accepted=["'s lived"])
VOCAB = item("vocab", "The concert was ___ because of the rain.", "cancelled", "vocab", word="cancel")
ARTICLE = item("article", "It was ___ amazing day.", "an", "article_a_an", options=["a", "an", "the", "-"])
FORM = item("word_form", "Thanks for your ___ (kind).", "kindness", "noun_form")


def batch(*items):
    return {"response": {"exercises": list(items)}}


class ValidationTest(unittest.TestCase):
    def test_good_exercises_pass(self):
        parsed = ex.parse(batch(PREP, TENSE, VOCAB, ARTICLE, FORM), vocab_words=["cancel"], limit=10)
        self.assertEqual([x["type"] for x in parsed], ["preposition", "tense", "vocab", "article", "word_form"])
        self.assertEqual(parsed[0]["options"], ["in", "on", "at", "by"])
        self.assertEqual(parsed[2]["word"], "cancel")
        self.assertEqual(parsed[1]["accepted"], ["'s lived"])

    def test_broken_exercises_are_dropped(self):
        broken = [
            item("tense", "___ you ___ (finish) it?", "Have finished", "present_perfect"),       # два пропуска
            item("tense", "They ___ their homework.", "had finished", "past_perfect"),         # нет глагола в скобках
            item("preposition", "Good ___ math.", "at", "prep_dependent", options=["in", "on"]),  # ответа нет в вариантах
            item("vocab", "I ___ it.", "love", "vocab", word="love"),                            # слова нет в словаре
            item("tense", "She ___ (go) home.", "went", "past_simple", explanation="Past Simple 到现在."),  # иероглифы
            item("tense", "She ___ (go) home.", "went", "past_simple", explanation="Past simple is used."),  # не по-русски
            item("unknown", "x ___", "y", "z"),
            "not an exercise",
        ]
        self.assertEqual(ex.parse(batch(*broken), vocab_words=["cancel"], limit=10), [])

    def test_unknown_rule_falls_back_to_the_type(self):
        parsed = ex.parse(batch(item("tense", "I ___ (see) it.", "saw", "made_up_rule")), limit=5)
        self.assertEqual(parsed[0]["rule"], "present_simple")

    def test_long_gaps_are_normalized_and_duplicates_removed(self):
        raw = item("preposition", "We met ______ Monday.", "on", "prep_time", options=["on", "in"])
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

    def test_weak_spots(self):
        def entry(rule, verdict, days_ago=1):
            return {"at": (NOW - timedelta(days=days_ago)).isoformat(), "rule": rule, "verdict": verdict,
                    "sentence": "", "answer": "", "given": ""}
        log = [entry("prep_time", "wrong"), entry("prep_time", "wrong"), entry("prep_time", "ok"),
               entry("present_perfect", "wrong"), entry("present_perfect", "ok"), entry("present_perfect", "ok"),
               entry("article_a_an", "ok"), entry("article_a_an", "ok"),
               entry("past_simple", "wrong"),                          # одна попытка — рано судить
               entry("conditional_2", "wrong", 60), entry("conditional_2", "wrong", 60)]  # старое
        weak = ex.weak_spots(log, NOW)
        self.assertEqual([s["rule"] for s in weak], ["prep_time"])
        self.assertEqual((weak[0]["wrong"], weak[0]["count"]), (2, 3))

    def test_focus_goes_into_the_prompt(self):
        prompt = ex.build_input("B1", {"preposition": 2, "tense": 3}, ["cancel"], ["prep_time"],
                                [{"sentence": "We met ___ Monday.", "given": "in", "answer": "on"}])
        user = prompt["messages"][1]["content"]
        self.assertIn("Make 2 preposition, 3 tense.", user)
        self.assertIn("prep_time", user)
        self.assertIn("answered 'in', correct 'on'", user)
        self.assertIn("CEFR level B1", prompt["messages"][0]["content"])

    def test_enabled_types(self):
        self.assertEqual(ex.enabled_types({}), list(ex.TYPES))
        self.assertEqual(ex.enabled_types({"ex_types": ["tense"]}), ["tense"])
        self.assertNotIn("vocab", ex.enabled_types({}, has_vocab=False))


class ExerciseFlowTest(unittest.TestCase):
    def setUp(self):
        self.store, self.tg, self.ai = FakeStore(), FakeTelegram(), FakeAI()
        self.bot = Bot(self.store, self.tg, owner=OWNER, schedule=Schedule(), ai=self.ai)
        self.clock = use_clock(self, self.bot, NOW)
        self.n = 0
        self.msg("/start")

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
        self.assertIn("✍️ <b>Упражнение 1/5</b> · Предлоги", self.tg.last_text())
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
        self.assertIn("cancel", self.ai.calls[-1][1]["messages"][0]["content"])  # слова из словаря в промпте
        self.press("xn:0")
        cancel = next(x for x in self.get("queue") if x["en"] == "cancel")
        self.assertEqual(cancel["box"], 1)
        self.assertEqual(cancel["due_at"], "2026-09-25T10:00:00+03:00")

    def test_weak_spots_drive_the_next_batch(self):
        log = [{"at": NOW.isoformat(), "rule": "prep_time", "verdict": "wrong", "sentence": "We met ___ Monday.",
                "answer": "on", "given": "in"}] * 3
        asyncio.run(self.bot.repo.put(f"mlog:{KEY}", log))
        self.ai.responses.append(batch(PREP))
        self.msg(cards.EXERCISE_BUTTON)
        user_prompt = self.ai.calls[-1][1]["messages"][1]["content"]
        self.assertIn("often gets these rules wrong: prep_time", user_prompt)
        self.assertIn("answered 'in', correct 'on'", user_prompt)

    def test_topics_menu(self):
        self.msg(cards.TOPICS_BUTTON)
        self.assertIn("ИИ подбирает по уровню", self.tg.last_text())
        self.press("xt:vocab")                       # выключить одну тему
        chosen = self.get("settings")["ex_types"]
        self.assertNotIn("vocab", chosen)
        self.assertEqual(len(chosen), len(ex.TYPES) - 1)
        self.press("xt:auto")
        self.assertEqual(self.get("settings")["ex_types"], [])
        self.bot.clock.set(NOW)
        asyncio.run(self.bot.repo.put(f"settings:{KEY}", {"ex_types": ["tense"]}))
        self.ai.responses.append(batch(TENSE))
        before = len(self.ai.calls)
        self.msg(cards.EXERCISE_BUTTON)
        self.assertIn(f"Make {ex.BATCH} tense.", self.ai.calls[before][1]["messages"][1]["content"])

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
        self.assertIn("🔎 Слабые места: предлоги времени (in/on/at) (2 из 3)", report)
        self.assertIn("💡 Чаще всего ошибаешься в предлогах времени", report)
        self.assertIn("rule 'предлоги времени (in/on/at)': 2 wrong of 3", self.ai.calls[-1][1]["messages"][1]["content"])
        # кнопка статистики показывает последний разбор без нового запроса к ИИ
        calls = len(self.ai.calls)
        self.msg(cards.STATS_BUTTON)
        self.assertIn("💡 Чаще всего", self.tg.last_text())
        self.assertEqual(len(self.ai.calls), calls)

    def test_users_have_separate_contexts(self):
        self.msg("/allow @Friend")
        self.msg("/start", user="friend", chat=2002)
        self.msg("cat - кот\nThe cat sleeps. - Кот спит.")                         # владелец
        self.msg("dog - собака\nThe dog barks. - Пёс лает.", user="friend", chat=2002)
        self.press("glv:C1", user="friend", chat=2002)
        self.ai.responses.append(batch(PREP, TENSE))
        self.msg(cards.EXERCISE_BUTTON, user="friend", chat=2002)

        self.assertEqual([x["en"] for x in self.get("queue")], ["cat"])
        self.assertEqual([x["en"] for x in self.get("queue", "friend")], ["dog"])
        self.assertIsNone((self.get("settings") or {}).get("level"))
        self.assertEqual(self.get("settings", "friend")["level"], "C1")
        self.assertIn("CEFR level C1", self.ai.calls[-1][1]["messages"][0]["content"])
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
