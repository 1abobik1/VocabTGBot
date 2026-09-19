"""When cards are due: CARDS_PER_DAY evenly spaced slots inside a daily window.

With the defaults (10:00-23:00 MSK, 10 cards) the step is 13h / 10 = 78 minutes:
10:00, 11:18, 12:36, 13:54, 15:12, 16:30, 17:48, 19:06, 20:24, 21:42.
The Worker cron fires every minute and sends a card only when the minute is a slot.
"""

from datetime import datetime, timedelta, timezone

# Minimum time left after the last card of the day to answer it before the window closes.
ANSWER_BUFFER_MINUTES = 30


def _parse_hhmm(value):
    hours, minutes = value.strip().split(":")
    return int(hours) * 60 + int(minutes)


class Schedule:
    def __init__(self, start="10:00", end="23:00", cards_per_day=10, utc_offset_hours=3):
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
        }
        return cls(**{k: v for k, v in kwargs.items() if v not in (None, "")})

    def _local(self, now):
        return now.astimezone(self.tz)

    def is_slot(self, now):
        local = self._local(now)
        return local.hour * 60 + local.minute in self.slots

    def last_slot(self, now):
        """Most recent slot at or before `now` today (local time), or None before the first one."""
        local = self._local(now)
        minute = local.hour * 60 + local.minute
        passed = [s for s in self.slots if s <= minute]
        if not passed:
            return None
        midnight = local.replace(hour=0, minute=0, second=0, microsecond=0)
        return midnight + timedelta(minutes=passed[-1])

    def describe(self):
        return ", ".join(f"{s // 60:02d}:{s % 60:02d}" for s in self.slots)
