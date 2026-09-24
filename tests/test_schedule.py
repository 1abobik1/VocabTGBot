import asyncio
import unittest
from datetime import datetime, timedelta, timezone

from shared.bot import Bot
from shared.schedule import Schedule
from tests.test_bot import CHAT, OWNER, FakeStore, FakeTelegram, use_clock

MSK = timezone(timedelta(hours=3))


def msk(hour, minute):
    return datetime(2026, 9, 21, hour, minute, tzinfo=MSK)


class ScheduleTest(unittest.TestCase):
    def test_default_slots(self):
        schedule = Schedule()
        self.assertEqual(len(schedule.slots), 14)
        self.assertTrue(schedule.describe().startswith("10:00, 10:55, 11:51"))
        self.assertTrue(schedule.describe().endswith("22:04"))

    def test_is_slot_uses_moscow_time(self):
        schedule = Schedule()
        self.assertTrue(schedule.is_slot(msk(10, 0)))
        self.assertTrue(schedule.is_slot(msk(22, 4)))
        self.assertTrue(schedule.is_slot(datetime(2026, 9, 21, 7, 55, tzinfo=timezone.utc)))  # 10:55 MSK
        for hour, minute in [(9, 59), (10, 1), (11, 17), (22, 30), (23, 0), (3, 0)]:
            self.assertFalse(schedule.is_slot(msk(hour, minute)), (hour, minute))

    def test_practice_runs_every_day_by_default(self):
        schedule = Schedule()
        for day in (21, 22, 26, 27):  # понедельник, вторник, суббота, воскресенье
            self.assertTrue(schedule.is_practice_time(datetime(2026, 9, day, 22, 30, tzinfo=MSK)), day)
        self.assertFalse(schedule.is_practice_time(msk(22, 29)))
        weekly = Schedule(practice_day="sat")
        self.assertTrue(weekly.is_practice_time(datetime(2026, 9, 26, 22, 30, tzinfo=MSK)))
        self.assertFalse(weekly.is_practice_time(datetime(2026, 9, 27, 22, 30, tzinfo=MSK)))

    def test_answer_buffer(self):
        # 20 cards in 10:00-14:00 = every 12 minutes, the last at 13:48: less than 30 minutes left
        with self.assertRaises(ValueError):
            Schedule(start="10:00", end="14:00", cards_per_day=20)

    def test_from_env(self):
        env = {
            "CARD_START": "09:00", "CARD_END": "23:00", "CARDS_PER_DAY": "7", "UTC_OFFSET_HOURS": "3",
            "NEW_WORDS_PER_DAY": "4", "PRACTICE_BATCH": "5", "PRACTICE_TIME": "21:00",
        }
        schedule = Schedule.from_env(env.get)
        self.assertEqual(schedule.describe(), "09:00, 11:00, 13:00, 15:00, 17:00, 19:00, 21:00")
        self.assertEqual((schedule.new_words_per_day, schedule.practice_batch), (4, 5))
        self.assertTrue(schedule.is_practice_time(msk(21, 0)))
        self.assertEqual(Schedule.from_env({}.get).describe(), Schedule().describe())


class CronTest(unittest.TestCase):
    def setUp(self):
        self.store, self.tg = FakeStore(), FakeTelegram()
        self.bot = Bot(self.store, self.tg, owner=OWNER, schedule=Schedule())
        self.clock = use_clock(self, self.bot, msk(9, 0))
        self.n = 0

    def msg(self, text):
        self.n += 1
        asyncio.run(self.bot.handle_update({"message": {
            "message_id": self.n, "from": {"id": CHAT, "username": OWNER},
            "chat": {"id": CHAT, "type": "private"}, "text": text}}))

    def press(self, data):
        asyncio.run(self.bot.handle_update({"callback_query": {
            "id": "q", "from": {"id": CHAT, "username": OWNER},
            "message": {"message_id": 1, "chat": {"id": CHAT, "type": "private"}}, "data": data}}))

    def cards_sent(self):
        return [p for p in self.tg.sent() if "inline_keyboard" in p.get("reply_markup", {})]

    def tick(self, when):
        self.clock.set(when)
        return asyncio.run(self.bot.on_cron(when))

    def unknown_button(self, card):
        return card["reply_markup"]["inline_keyboard"][0][1]["callback_data"]

    def known_button(self, card):
        return card["reply_markup"]["inline_keyboard"][0][0]["callback_data"]

    def test_cron_sends_only_on_slots_and_waits_for_answers(self):
        self.msg("/start")
        for word in ["apple - яблоко", "cat - кот", "dog - собака"]:
            self.msg(word)
        self.assertEqual(self.tick(msk(9, 0)), [])
        self.assertEqual(self.tick(msk(10, 1)), [])
        self.assertEqual(self.tick(msk(10, 0)), [OWNER])
        self.assertEqual(len(self.cards_sent()), 1)
        # нет ответа — слот 10:55 пропускается
        self.assertEqual(self.tick(msk(10, 55)), [])
        self.assertEqual(len(self.cards_sent()), 1)

    def test_new_word_shows_both_sides_in_one_slot(self):
        self.msg("/start")
        self.msg("apple - яблоко")
        self.msg("cat - кот")
        self.tick(msk(10, 0))
        self.assertTrue(self.cards_sent()[-1]["text"].startswith("Яблоко"))
        self.press(self.known_button(self.cards_sent()[-1]))
        # вторая сторона того же слова приходит сразу, слот не тратится второй раз
        self.assertEqual(len(self.cards_sent()), 2)
        self.assertTrue(self.cards_sent()[-1]["text"].startswith("Apple"))
        self.press(self.known_button(self.cards_sent()[-1]))
        self.assertEqual(len(self.cards_sent()), 2)  # следующее слово — только в свой слот
        queue = self.store.json(f"queue:{OWNER.lower()}")
        self.assertEqual([(x["en"], x["box"]) for x in queue], [("apple", 1), ("cat", 0)])

    def test_hard_word_does_not_eat_the_day(self):
        self.msg("/start")
        for word in ["apple - яблоко", "cat - кот", "dog - собака"]:
            self.msg(word)
        slots = [(10, 0), (10, 55), (11, 51), (12, 47)]
        seen = []
        for hour, minute in slots:
            self.tick(msk(hour, minute))
            card = self.cards_sent()[-1]
            seen.append(card["text"].split("\n")[0])
            self.press(self.unknown_button(card))  # каждый раз "Не знаю"
        # первое слово откладывается на 30 минут, поэтому следующие слоты получают другие слова
        self.assertEqual(len(set(seen)), 3)
        self.assertEqual(seen[:3], ["Яблоко - <tg-spoiler>apple</tg-spoiler>",
                                    "Кот - <tg-spoiler>cat</tg-spoiler>",
                                    "Собака - <tg-spoiler>dog</tg-spoiler>"])

    def test_new_word_quota_leaves_slots_for_reviews(self):
        self.msg("/start")
        self.bot.schedule = Schedule(new_words_per_day=2)
        for word in ["one - один", "two - два", "three - три", "four - четыре"]:
            self.msg(word)
        slots = [(10, 0), (10, 55), (11, 51)]
        for hour, minute in slots:
            self.tick(msk(hour, minute))
            self.press(self.unknown_button(self.cards_sent()[-1]))
        # квота — два новых слова; третий слот ушёл на повтор первого, а не на третье слово
        shown = {c["text"].split(" - ")[0] for c in self.cards_sent()}
        self.assertEqual(shown, {"Один", "Два"})
        self.assertEqual(self.store.json(f"sched:{OWNER.lower()}")["new"], 2)

    def test_answer_after_missed_slot_sends_next_card_right_away(self):
        self.msg("/start")
        for word in ["apple - яблоко", "cat - кот"]:
            self.msg(word)
        self.tick(msk(10, 0))
        card = self.unknown_button(self.cards_sent()[-1])
        self.tick(msk(10, 55))  # пропущен: на apple нет ответа
        self.assertEqual(len(self.cards_sent()), 1)
        self.press(card)
        self.assertEqual(len(self.cards_sent()), 2)
        self.assertTrue(self.cards_sent()[-1]["text"].startswith("Кот"))

    def test_answer_within_slot_does_not_send_extra_card(self):
        self.msg("/start")
        for word in ["apple - яблоко", "cat - кот"]:
            self.msg(word)
        self.tick(msk(10, 0))
        card = self.unknown_button(self.cards_sent()[-1])
        self.press(card)  # ответ до следующего слота: долга нет
        self.assertEqual(len(self.cards_sent()), 1)


if __name__ == "__main__":
    unittest.main()
