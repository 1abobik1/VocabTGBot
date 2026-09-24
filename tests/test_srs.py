import unittest
from datetime import datetime, timedelta, timezone

from shared import srs
from shared import words as w
from shared.schedule import Schedule

MSK = timezone(timedelta(hours=3))
NOW = datetime(2026, 9, 24, 12, 0, tzinfo=MSK)
TODAY = "2026-09-24"


def word(en, box=0, shown=0, due=NOW, shows_today=0):
    x = w.new_word(en, en + "_ru")
    x.update(box=box, shown_count=shown, due_at=due.isoformat())
    if shows_today:
        x["shows"] = {TODAY: shows_today}
    return x


class SelectionTest(unittest.TestCase):
    def test_new_words_come_first_until_the_quota_is_spent(self):
        queue = [word("new1"), word("new2"), word("due", box=1, shown=2, due=NOW - timedelta(hours=1))]
        self.assertEqual(srs.pick_next(queue, NOW, TODAY, 0, 2)["en"], "new1")
        self.assertEqual(srs.pick_next(queue, NOW, TODAY, 2, 2)["en"], "due")  # квота выбрана
        self.assertEqual(srs.pick_next(queue, NOW, TODAY, 0, 2, exclude=[queue[0]])["en"], "new2")

    def test_most_overdue_review_goes_first(self):
        old = word("old", box=1, shown=3, due=NOW - timedelta(days=2))
        fresh = word("fresh", box=1, shown=3, due=NOW - timedelta(minutes=5))
        later = word("later", box=1, shown=3, due=NOW + timedelta(hours=1))
        queue = [fresh, later, old]
        self.assertEqual([x["en"] for x in srs.due_words(queue, NOW, TODAY)], ["old", "fresh"])
        self.assertEqual(srs.pick_next(queue, NOW, TODAY, 5, 5)["en"], "old")

    def test_a_word_is_shown_at_most_three_times_a_day(self):
        hard = word("hard", box=1, shown=5, due=NOW - timedelta(minutes=1), shows_today=srs.MAX_SHOWS_PER_DAY)
        queue = [hard]
        self.assertEqual(srs.due_words(queue, NOW, TODAY), [])
        self.assertIsNone(srs.pick_next(queue, NOW, TODAY, 5, 5))
        hard["shows"] = {TODAY: srs.MAX_SHOWS_PER_DAY - 1}
        self.assertEqual(srs.pick_next(queue, NOW, TODAY, 5, 5)["en"], "hard")

    def test_nothing_to_send_when_everything_is_scheduled_for_later(self):
        queue = [word("x", box=1, shown=2, due=NOW + timedelta(days=1))]
        self.assertIsNone(srs.pick_next(queue, NOW, TODAY, 0, 6))

    def test_note_shown_keeps_only_today(self):
        x = word("x")
        x["shows"] = {"2026-09-01": 3}
        srs.note_shown(x, TODAY)
        self.assertEqual(x["shows"], {TODAY: 1})
        self.assertEqual(x["shown_count"], 1)

    def test_words_added_before_intervals_are_due_now(self):
        legacy = {"id": "1", "en": "a", "ru": "б", "stage": 0, "shown_count": 0}
        srs.prepare(legacy, NOW)
        self.assertEqual((legacy["box"], legacy["due_at"]), (0, NOW.isoformat()))
        self.assertTrue(srs.is_due(legacy, NOW, TODAY))

    def test_stats(self):
        queue = [word("new"), word("due", box=1, shown=2, due=NOW - timedelta(hours=1)),
                 word("later", box=2, shown=4, due=NOW + timedelta(days=2))]
        self.assertEqual(srs.stats(queue, NOW, TODAY), {"new": 1, "due": 1, "later": 1})


class IntervalTest(unittest.TestCase):
    def setUp(self):
        self.schedule = Schedule()

    def test_success_chain(self):
        x = word("x")
        self.assertEqual(srs.on_success(x, self.schedule, NOW, TODAY), "flip")
        self.assertEqual((x["stage"], x["box"], x["due_at"]), (1, 0, NOW.isoformat()))
        self.assertEqual(srs.on_success(x, self.schedule, NOW, TODAY), "review")
        self.assertEqual((x["box"], x["due_at"]), (1, (NOW + timedelta(days=1)).isoformat()))
        self.assertEqual(srs.on_success(x, self.schedule, NOW, TODAY), "review")
        self.assertEqual((x["box"], x["due_at"]), (2, (NOW + timedelta(days=3)).isoformat()))
        self.assertEqual(srs.on_success(x, self.schedule, NOW, TODAY), "practice")

    def test_lapse_resets_the_box_and_steps_through_the_day(self):
        x = word("x", box=2, shown=4)
        srs.on_lapse(x, self.schedule, NOW, TODAY)
        self.assertEqual((x["box"], x["due_at"]), (0, (NOW + timedelta(minutes=30)).isoformat()))
        srs.on_lapse(x, self.schedule, NOW, TODAY)
        self.assertEqual(x["due_at"], (NOW + timedelta(hours=2)).isoformat())
        srs.on_lapse(x, self.schedule, NOW, TODAY)
        self.assertEqual(x["due_at"], "2026-09-25T10:00:00+03:00")  # завтра, к началу окна

    def test_success_clears_the_lapse_streak(self):
        x = word("x", box=1, shown=3)
        srs.on_lapse(x, self.schedule, NOW, TODAY)
        srs.on_success(x, self.schedule, NOW, TODAY)
        self.assertNotIn("lapses_in_a_row", x)
        srs.on_lapse(x, self.schedule, NOW, TODAY)
        self.assertEqual(x["due_at"], (NOW + timedelta(minutes=30)).isoformat())

    def test_reset_from_archive(self):
        x = word("x", box=2, shown=9)
        x["archived_at"] = "2026-09-01T00:00:00+00:00"
        srs.reset(x, self.schedule, NOW)
        self.assertEqual((x["box"], x["stage"], x["archived_at"], x["due_at"]), (0, 0, None, NOW.isoformat()))


if __name__ == "__main__":
    unittest.main()
