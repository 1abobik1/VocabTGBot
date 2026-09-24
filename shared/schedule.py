"""When cards are due: CARDS_PER_DAY evenly spaced slots inside a daily window.

With the defaults (10:00-23:00 MSK, 14 cards) the step is 13h / 14 = 55 minutes.
The Worker cron fires every minute and sends a card only when the minute is a slot.
NEW_WORDS_PER_DAY of those slots are reserved for words seen for the first time.
The typed practice runs at PRACTICE_TIME (default 22:00) every day, or on PRACTICE_DAY
only, and takes at most PRACTICE_BATCH words at a time.
"""

from datetime import timedelta, timezone

# Minimum time left after the last card of the day to answer it before the window closes.
ANSWER_BUFFER_MINUTES = 30


def _parse_hhmm(value):
    hours, minutes = value.strip().split(":")
    return int(hours) * 60 + int(minutes)


# "daily" (каждый день) или день недели.
WEEKDAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


class Schedule:
    def __init__(
        self,
        start="10:00",
        end="23:00",
        cards_per_day=14,
        utc_offset_hours=3,
        practice_day="daily",
        practice_time="22:30",
        new_words_per_day=6,
        practice_batch=7,
    ):
        day = str(practice_day).strip().lower()[:3]
        self.practice_day = None if day in ("dai", "eve", "all", "") else WEEKDAYS[day]
        self.practice_time = _parse_hhmm(practice_time)
        self.new_words_per_day = int(new_words_per_day)
        self.practice_batch = int(practice_batch)
        self.start = _parse_hhmm(start)
        self.end = _parse_hhmm(end)
        self.cards_per_day = int(cards_per_day)
        self.tz = timezone(timedelta(hours=float(utc_offset_hours)))
        if not 0 <= self.start < self.end <= 24 * 60 or self.cards_per_day < 1:
            raise ValueError("bad card schedule")
        step = (self.end - self.start) / self.cards_per_day
        # Minutes after local midnight, rounded down to whole minutes (cron has minute precision).
        self.slots = [int(self.start + i * step) for i in range(self.cards_per_day)]
        if self.slots[-1] + ANSWER_BUFFER_MINUTES > self.end:
            raise ValueError("last card leaves less than 30 minutes to answer; widen the window")

    @classmethod
    def from_env(cls, env_get):
        """Build from string settings; `env_get(name)` returns the value or None."""
        kwargs = {
            "start": env_get("CARD_START"),
            "end": env_get("CARD_END"),
            "cards_per_day": env_get("CARDS_PER_DAY"),
            "utc_offset_hours": env_get("UTC_OFFSET_HOURS"),
            "practice_day": env_get("PRACTICE_DAY"),
            "practice_time": env_get("PRACTICE_TIME"),
            "new_words_per_day": env_get("NEW_WORDS_PER_DAY"),
            "practice_batch": env_get("PRACTICE_BATCH"),
        }
        return cls(**{k: v for k, v in kwargs.items() if v not in (None, "")})

    def local(self, now):
        return now.astimezone(self.tz)

    _local = local

    def is_slot(self, now):
        local = self._local(now)
        return local.hour * 60 + local.minute in self.slots

    def is_practice_time(self, now):
        local = self._local(now)
        if self.practice_day is not None and local.weekday() != self.practice_day:
            return False
        return local.hour * 60 + local.minute == self.practice_time

    def describe(self):
        return ", ".join(f"{s // 60:02d}:{s % 60:02d}" for s in self.slots)
