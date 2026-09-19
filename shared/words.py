"""Word model, message parsing and queue/archive transitions.

Pure functions over plain dicts/lists so the same code runs inside the
Cloudflare Worker (Pyodide) and in GitHub Actions (CPython).
"""

import re
import uuid
from datetime import datetime, timedelta, timezone

SEPARATOR = " - "
# Hyphen, en dash and em dash are equivalent separators; spaces around them are required.
_SEPARATOR_RE = re.compile(r"\s[-\u2013\u2014]\s")
# "Не знаю" puts the word back at this index: after the current first and second words.
RETRY_POSITION = 2


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_iso(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class ParseError(ValueError):
    pass


_CYRILLIC = re.compile("[а-яё]", re.IGNORECASE)


def _split_pair(line):
    """Split "en - ru" (or "ru - en", with "-", "–" or "—") and return (en, ru).

    Hyphens inside words ("куда-то", "well-being") are kept: the separator needs spaces
    around it. When a line has several dashes ("Москва — столица. - Moscow is the capital"),
    the first one that leaves Russian on one side and non-Russian on the other wins,
    otherwise the first one. Either language order works.
    """
    candidates = []
    for match in _SEPARATOR_RE.finditer(line):
        left, right = line[: match.start()].strip(), line[match.end() :].strip()
        if left and right:
            candidates.append((left, right))
    if not candidates:
        return None
    for left, right in candidates:
        left_ru, right_ru = bool(_CYRILLIC.search(left)), bool(_CYRILLIC.search(right))
        if left_ru != right_ru:
            return (right, left) if left_ru else (left, right)
    return candidates[0]


def parse_add_message(text):
    """Parse "en - ru" on the first line plus optional "example en - example ru" lines (any order).

    Returns (en, ru, examples). Raises ParseError with a human-readable message.
    """
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    if not lines:
        raise ParseError("Пустое сообщение.")
    pair = _split_pair(lines[0])
    if pair is None:
        raise ParseError(f"Первая строка должна быть в формате «слово{SEPARATOR}перевод» (тире можно и длинное «—»).")
    examples = []
    for number, line in enumerate(lines[1:], start=2):
        example = _split_pair(line)
        if example is None:
            raise ParseError(f"Строка {number}: нет разделителя «{SEPARATOR}» между примером и переводом.")
        examples.append({"en": example[0], "ru": example[1]})
    return pair[0], pair[1], examples


def new_word(en, ru, examples=None):
    return {
        "id": str(uuid.uuid4()),
        "en": en,
        "ru": ru,
        "examples": list(examples or []),
        "shown_count": 0,
        "stage": 0,
        "added_at": now_iso(),
        "archived_at": None,
    }


def _compact(text):
    """Drop all whitespace and ignore case: "Look  up" == "lookup"."""
    return "".join(text.split()).casefold()


def find_duplicate(en, ru, queue, known):
    """Return an existing word with the same pair, compared without whitespace."""
    key = (_compact(en), _compact(ru))
    for word in list(queue) + list(known):
        if (_compact(word["en"]), _compact(word["ru"])) == key:
            return word
    return None


def find_index(words, word_id):
    for index, word in enumerate(words):
        if word["id"] == word_id:
            return index
    return -1


def current_example(word):
    examples = word.get("examples") or []
    if not examples:
        return None
    return examples[word.get("shown_count", 0) % len(examples)]


# Results of answer_known / answer_unknown.
FLIPPED = "flipped"      # stage 0 -> 1, moved to the end of the queue
ARCHIVED = "archived"    # stage 1 -> moved to known
REQUEUED = "requeued"    # "Не знаю": moved to RETRY_POSITION
NOT_FOUND = "not_found"  # card is stale (word already archived or deleted)


def is_awaiting_answer(queue):
    """True while a sent card has not been answered with "Знаю" / "Не знаю"."""
    return any(word.get("sent_at") for word in queue)


def _take(queue, word_id, stage):
    """Pop the word for an answer; `stage` (from the button) must match to reject stale cards."""
    index = find_index(queue, word_id)
    if index < 0 or (stage is not None and queue[index].get("stage", 0) != stage):
        return None
    word = queue.pop(index)
    word.pop("sent_at", None)
    return word


def answer_known(queue, known, word_id, now=None, stage=None):
    """Apply "Знаю". Mutates queue/known in place and returns (result, word)."""
    word = _take(queue, word_id, stage)
    if word is None:
        return NOT_FOUND, None
    if word.get("stage", 0) == 0:
        word["stage"] = 1
        queue.append(word)
        return FLIPPED, word
    word["archived_at"] = now or now_iso()
    known.append(word)
    return ARCHIVED, word


def answer_unknown(queue, word_id, stage=None):
    """Apply "Не знаю": stage stays, word goes to index 2 of the queue."""
    word = _take(queue, word_id, stage)
    if word is None:
        return NOT_FOUND, None
    queue.insert(RETRY_POSITION, word)
    return REQUEUED, word


def known_newest_first(known):
    return sorted(known, key=lambda w: w.get("archived_at") or "", reverse=True)


def parse_numbers(text):
    """"2 5 7" -> [2, 5, 7]; returns None if the text is not a list of numbers."""
    tokens = (text or "").replace(",", " ").split()
    if not tokens or not all(token.isdigit() for token in tokens):
        return None
    return [int(token) for token in tokens]


def restore_from_known(queue, known, word_ids):
    """Move forgotten words back to the end of the queue with stage reset to 0."""
    restored = []
    for word_id in word_ids:
        index = find_index(known, word_id)
        if index < 0:
            continue
        word = known.pop(index)
        word["stage"] = 0
        word["archived_at"] = None
        word.pop("sent_at", None)
        queue.append(word)
        restored.append(word)
    return restored


def weekly_stats(known, now=None, days=7):
    """Words archived in the last `days` days and the average days from added_at to archived_at."""
    now = now or datetime.now(timezone.utc)
    since = now - timedelta(days=days)
    recent = [w for w in known if w.get("archived_at") and parse_iso(w["archived_at"]) >= since]
    durations = [
        (parse_iso(w["archived_at"]) - parse_iso(w["added_at"])).total_seconds() / 86400 for w in recent
    ]
    average = sum(durations) / len(durations) if durations else None
    return {"archived": len(recent), "avg_days": average, "total_known": len(known)}
