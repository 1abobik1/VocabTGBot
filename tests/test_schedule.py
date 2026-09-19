import asyncio
import unittest
from datetime import datetime, timedelta, timezone

from shared.bot import Bot
from shared.schedule import Schedule
from tests.test_bot import CHAT, OWNER, FakeStore, FakeTelegram

MSK = timezone(timedelta(hours=3))


def msk(hour, minute):
    return datetime(2026, 9, 21, hour, minute, tzinfo=MSK)


class ScheduleTest(unittest.TestCase):
    def test_default_slots(self):
        schedule = Schedule()
        self.assertEqual(
            schedule.describe(), "10:00, 11:18, 12:36, 13:54, 15:12, 16:30, 17:48, 19:06, 20:24, 21:42"
        )

    def test_is_slot_uses_moscow_time(self):
        schedule = Schedule()
        self.assertTrue(schedule.is_slot(msk(10, 0)))
        self.assertTrue(schedule.is_slot(msk(21, 42)))
        self.assertTrue(schedule.is_slot(datetime(2026, 9, 21, 8, 18, tzinfo=timezone.utc)))  # 11:18 MSK
        for hour, minute in [(9, 59), (10, 1), (11, 17), (22, 0), (23, 0), (3, 0)]:
            self.assertFalse(schedule.is_slot(msk(hour, minute)), (hour, minute))

    def test_answer_buffer(self):
        # 20 cards in 10:00-14:00 = every 12 minutes, the last at 13:48: less than 30 minutes left
        with self.assertRaises(ValueError):
            Schedule(start="10:00", end="14:00", cards_per_day=20)

    def test_from_env(self):
        env = {"CARD_START": "09:00", "CARD_END": "23:00", "CARDS_PER_DAY": "7", "UTC_OFFSET_HOURS": "3"}
        schedule = Schedule.from_env(env.get)
        self.assertEqual(schedule.describe(), "09:00, 11:00, 13:00, 15:00, 17:00, 19:00, 21:00")
        self.assertEqual(Schedule.from_env({}.get).describe(), Schedule().describe())


class CronTest(unittest.TestCase):
    def setUp(self):
        self.store, self.tg = FakeStore(), FakeTelegram()
        self.bot = Bot(self.store, self.tg, owner=OWNER, schedule=Schedule())
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
        return asyncio.run(self.bot.on_cron(when))

    def test_cron_sends_only_on_slots_and_waits_for_answers(self):
        self.msg("/start")
        for word in ["apple - яблоко", "cat - кот", "dog - собака"]:
            self.msg(word)
        self.assertEqual(self.tick(msk(9, 0)), [])
        self.assertEqual(self.tick(msk(10, 1)), [])
        self.assertEqual(self.tick(msk(10, 0)), [OWNER])
        self.assertEqual(len(self.cards_sent()), 1)
        # unanswered: the 11:18 slot is skipped
        self.assertEqual(self.tick(msk(11, 18)), [])
        self.assertEqual(len(self.cards_sent()), 1)

    def test_answer_after_missed_slot_sends_next_card_right_away(self):
        self.msg("/start")
        for word in ["apple - яблоко", "cat - кот"]:
            self.msg(word)
        self.tick(msk(10, 0))
        card = self.cards_sent()[-1]["reply_markup"]["inline_keyboard"][0][1]["callback_data"]
        self.tick(msk(11, 18))  # skipped: apple is unanswered
        self.assertEqual(len(self.cards_sent()), 1)
        self.press(card)
        self.assertEqual(len(self.cards_sent()), 2)
        self.assertTrue(self.cards_sent()[-1]["text"].startswith("Кот"))

    def test_answer_within_slot_does_not_send_extra_card(self):
        self.msg("/start")
        for word in ["apple - яблоко", "cat - кот"]:
            self.msg(word)
        self.tick(msk(10, 0))
        card = self.cards_sent()[-1]["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
        self.press(card)  # answered before the 11:18 slot: nothing was missed
        self.assertEqual(len(self.cards_sent()), 1)


if __name__ == "__main__":
    unittest.main()
