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


SYNONYMS_PREFIX = "Синонимы:"
_SYNONYMS_LINE = re.compile(r"^(синонимы|синоним|synonyms|syn)\s*:\s*", re.IGNORECASE)


def _parse_synonyms(text):
    """"fairly - довольно; rather" -> [{"en": "fairly", "ru": "довольно"}, {"en": "rather", "ru": ""}]."""
    synonyms = []
    for item in text.split(";"):
        item = item.strip()
        if not item:
            continue
        pair = _split_pair(item)
        synonyms.append({"en": pair[0], "ru": pair[1]} if pair else {"en": item, "ru": ""})
    return synonyms


def parse_card_text(text):
    """Parse a card: "en - ru", optional example lines and an optional "Синонимы: a - б; c - д" line.

    Language order in each line is free. Returns (en, ru, examples, synonyms).
    Raises ParseError with a human-readable message.
    """
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    if not lines:
        raise ParseError("Пустое сообщение.")
    pair = _split_pair(lines[0])
    if pair is None:
        raise ParseError(f"Первая строка должна быть в формате «слово{SEPARATOR}перевод» (тире можно и длинное «—»).")
    examples, synonyms = [], []
    for number, line in enumerate(lines[1:], start=2):
        prefix = _SYNONYMS_LINE.match(line)
        if prefix:
            synonyms += _parse_synonyms(line[prefix.end() :])
            continue
        example = _split_pair(line)
        if example is None:
            raise ParseError(f"Строка {number}: нет разделителя «{SEPARATOR}» между примером и переводом.")
        examples.append({"en": example[0], "ru": example[1]})
    return pair[0], pair[1], examples, synonyms


def parse_add_message(text):
    """Like parse_card_text but without synonyms: returns (en, ru, examples)."""
    en, ru, examples, _ = parse_card_text(text)
    return en, ru, examples


def card_to_text(word):
    """Inverse of parse_card_text: the text a user can copy, fix and send back."""
    lines = [f"{word['en']}{SEPARATOR}{word['ru']}"]
    lines += [f"{e['en']}{SEPARATOR}{e['ru']}" for e in word.get("examples") or []]
    synonyms = word.get("synonyms") or []
    if synonyms:
        items = [f"{s['en']}{SEPARATOR}{s['ru']}" if s.get("ru") else s["en"] for s in synonyms]
        lines.append(f"{SYNONYMS_PREFIX} " + "; ".join(items))
    return "\n".join(lines)


def new_word(en, ru, examples=None, synonyms=None):
    return {
        "id": str(uuid.uuid4()),
        "en": en,
        "ru": ru,
        "examples": list(examples or []),
        "synonyms": list(synonyms or []),
        "shown_count": 0,
        "stage": 0,
        "added_at": now_iso(),
        "archived_at": None,
    }


def compact(text):
    """Drop all whitespace and ignore case: "Look  up" == "lookup"."""
    return "".join(text.split()).casefold()


def find_duplicate(en, ru, queue, known):
    """Return an existing word with the same pair, compared without whitespace."""
    key = (compact(en), compact(ru))
    for word in list(queue) + list(known):
        if (compact(word["en"]), compact(word["ru"])) == key:
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
TO_PRACTICE = "practice" # stage 1 -> waits for the Saturday practice (stage 2)
ARCHIVED = "archived"    # "В архив": straight to known, skipping the remaining steps
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


PRACTICE_STAGE = 2


def answer_known(queue, practice, word_id, now=None, stage=None):
    """Apply "Знаю". Mutates queue/practice in place and returns (result, word).

    Stage 0 -> stage 1 at the end of the queue; stage 1 -> the practice pool, where the
    word waits for the typed Saturday practice before it can be archived.
    """
    word = _take(queue, word_id, stage)
    if word is None:
        return NOT_FOUND, None
    if word.get("stage", 0) == 0:
        word["stage"] = 1
        queue.append(word)
        return FLIPPED, word
    word["stage"] = PRACTICE_STAGE
    word["practice_since"] = now or now_iso()
    practice.append(word)
    return TO_PRACTICE, word


def answer_archive(queue, known, word_id, now=None, stage=None):
    """Apply "В архив": the word skips the remaining steps and the practice."""
    word = _take(queue, word_id, stage)
    if word is None:
        return NOT_FOUND, None
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
