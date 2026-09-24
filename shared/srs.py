"""Spaced repetition: when a word is due, and what an answer does to it.

Instead of a word's position in the queue, every word carries `due_at` and `box`:

    box 0  новое слово или сорвавшееся — показывается сегодня
    box 1  успешный ответ -> через 1 день
    box 2  успешный ответ -> через 3 дня -> дальше практика

"Не знаю" не ставит слово через две карточки, а откладывает его на 30 минут,
потом на 2 часа, потом на завтра: одно трудное слово больше не забирает весь день.
Показов одного слова в день не больше MAX_SHOWS_PER_DAY.
"""

from datetime import timedelta

# Успешные повторы: box 1 -> через сутки, box 2 -> через три дня, дальше практика.
REVIEW_INTERVALS_DAYS = (1, 3)
PRACTICE_AFTER_BOX = len(REVIEW_INTERVALS_DAYS)
# "Не знаю": сегодня ещё пара подходов, потом завтра.
LAPSE_STEPS_MINUTES = (30, 120)
MAX_SHOWS_PER_DAY = 3


def local_date(schedule, now):
    return schedule.local(now).date().isoformat()


def _next_morning(schedule, now):
    """Начало завтрашнего окна карточек."""
    local = schedule.local(now)
    tomorrow = (local + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return tomorrow + timedelta(minutes=schedule.start)


def shows_today(word, today):
    return word.get("shows", {}).get(today, 0)


def note_shown(word, today):
    """Счётчик показов за день; хранится только сегодняшний, чтобы запись не росла."""
    word["shows"] = {today: shows_today(word, today) + 1}
    word["shown_count"] = word.get("shown_count", 0) + 1


def is_new(word):
    return word.get("box", 0) == 0 and not word.get("shown_count")


def due_at(word):
    return word.get("due_at") or word.get("added_at") or ""


def is_due(word, now, today):
    if shows_today(word, today) >= MAX_SHOWS_PER_DAY:
        return False
    return due_at(word) <= now.isoformat()


def due_words(queue, now, today):
    """Созревшие слова, самые просроченные первыми; новые слова сюда не попадают."""
    ready = [x for x in queue if not is_new(x) and is_due(x, now, today)]
    return sorted(ready, key=due_at)


def new_words(queue, now, today):
    return [x for x in queue if is_new(x) and is_due(x, now, today)]


def pick_next(queue, now, today, new_sent_today, new_quota, exclude=()):
    """Что показать в этот слот: новое слово, пока не выбрана дневная квота, иначе повтор.

    `exclude` — слова, уже отобранные в эту же пачку (досылка за пропущенные слоты).
    """
    skip = {x["id"] for x in exclude}
    ready = [x for x in queue if x["id"] not in skip]
    new, due = new_words(ready, now, today), due_words(ready, now, today)
    if new and new_sent_today < new_quota:
        return new[0]
    if due:
        return due[0]
    return None


def on_success(word, schedule, now, today):
    """«Знаю». Возвращает "flip" (сразу вторая сторона), "review" или "practice"."""
    word.pop("lapses_in_a_row", None)
    if word.get("box", 0) == 0 and word.get("stage", 0) == 0:
        # Новое слово: вторая сторона показывается сразу, в этом же слоте.
        word["stage"] = 1
        word["due_at"] = now.isoformat()
        return "flip"
    box = word.get("box", 0) + 1
    word["box"] = box
    word["stage"] = 1 - word.get("stage", 0)
    if box > PRACTICE_AFTER_BOX:
        return "practice"
    days = REVIEW_INTERVALS_DAYS[box - 1]
    word["due_at"] = (now + timedelta(days=days)).isoformat()
    return "review"


def on_lapse(word, schedule, now, today):
    """«Не знаю»: слово сбрасывается в box 0 и откладывается на 30 мин / 2 часа / завтра."""
    word["box"] = 0
    step = word.get("lapses_in_a_row", 0)
    word["lapses_in_a_row"] = step + 1
    if step < len(LAPSE_STEPS_MINUTES) and shows_today(word, today) < MAX_SHOWS_PER_DAY:
        word["due_at"] = (now + timedelta(minutes=LAPSE_STEPS_MINUTES[step])).isoformat()
    else:
        word["due_at"] = _next_morning(schedule, now).isoformat()
    return word["due_at"]


def postpone_to_tomorrow(word, schedule, now):
    word["due_at"] = _next_morning(schedule, now).isoformat()


def reset(word, schedule, now, hard=True):
    """Слово возвращается в обучение: из архива или после ошибки на практике."""
    word["box"] = 0
    word["stage"] = 0 if hard else 1
    word["archived_at"] = None
    word.pop("practice_since", None)
    word.pop("lapses_in_a_row", None)
    word["due_at"] = (now if hard else _next_morning(schedule, now)).isoformat()


def prepare(word, now):
    """Значения по умолчанию для слов, добавленных до перехода на интервалы."""
    word.setdefault("box", 0)
    word.setdefault("due_at", now.isoformat())
    return word


def stats(queue, now, today):
    return {
        "new": len(new_words(queue, now, today)),
        "due": len(due_words(queue, now, today)),
        "later": len([x for x in queue if not is_due(x, now, today) and not is_new(x)]),
    }
