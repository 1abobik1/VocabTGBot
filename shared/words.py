"""Word model, message parsing and queue/archive transitions.

Pure functions over plain dicts/lists so the same code runs inside the
Cloudflare Worker (Pyodide) and in GitHub Actions (CPython).
"""

import re
import uuid
from datetime import datetime, timedelta, timezone

from . import srs
from .text import EXTRAS, is_russian

SEPARATOR = " - "
# Hyphen, en dash and em dash are equivalent separators; spaces around them are required.
_SEPARATOR_RE = re.compile(r"\s[-\u2013\u2014]\s")


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def parse_iso(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class ParseError(ValueError):
    pass


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
        # Пометки и транскрипция ("stove (амер.) [stoʊv]") на определение языка не влияют.
        left_ru, right_ru = is_russian(left), is_russian(right)
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


def new_word(en, ru, examples=None, synonyms=None, due_at=None):
    return {
        "box": 0,
        "due_at": due_at or now_iso(),
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
    """Без пробелов, регистра, транскрипции и пояснений: "Look  up" == "lookup",
    "towel [ˈtaʊəl]" == "towel"."""
    return "".join(EXTRAS.sub(" ", text or "").split()).casefold()


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


# Results of answer_known / answer_unknown / answer_archive.
FLIPPED = "flipped"      # новое слово: вторая сторона показывается сразу
REVIEWED = "reviewed"    # успешный повтор: слово назначено на следующий интервал
TO_PRACTICE = "practice" # интервалы пройдены: слово ждёт практики
ARCHIVED = "archived"    # "В архив": сразу в архив, минуя остальные шаги
REQUEUED = "requeued"    # "Не знаю": слово отложено на 30 мин / 2 часа / завтра
NOT_FOUND = "not_found"  # карточка устарела (слово уже архивировано или удалено)


def is_awaiting_answer(queue):
    """True while a sent card has not been answered with "Знаю" / "Не знаю"."""
    return any(word.get("sent_at") for word in queue)


def _find(queue, word_id, stage):
    """Слово для ответа; `stage` из кнопки должен совпасть, иначе карточка устарела."""
    index = find_index(queue, word_id)
    if index < 0 or (stage is not None and queue[index].get("stage", 0) != stage):
        return None
    word = queue[index]
    word.pop("sent_at", None)
    return word


PRACTICE_STAGE = 2


def answer_known(queue, practice, word_id, schedule, now, today, stage=None):
    """«Знаю»: новое слово разворачивается сразу, повтор уходит на следующий интервал."""
    word = _find(queue, word_id, stage)
    if word is None:
        return NOT_FOUND, None
    outcome = srs.on_success(word, schedule, now, today)
    if outcome == "practice":
        queue.remove(word)
        word["stage"] = PRACTICE_STAGE
        word["practice_since"] = now.isoformat()
        word.pop("due_at", None)
        practice.append(word)
        return TO_PRACTICE, word
    return (FLIPPED if outcome == "flip" else REVIEWED), word


def answer_archive(queue, known, word_id, now=None, stage=None):
    """«В архив»: слово минует оставшиеся шаги и практику."""
    word = _find(queue, word_id, stage)
    if word is None:
        return NOT_FOUND, None
    queue.remove(word)
    word["archived_at"] = now or now_iso()
    word.pop("due_at", None)
    known.append(word)
    return ARCHIVED, word


def answer_unknown(queue, word_id, schedule, now, today, stage=None):
    """«Не знаю»: направление то же, слово откладывается по шагам срыва."""
    word = _find(queue, word_id, stage)
    if word is None:
        return NOT_FOUND, None
    srs.on_lapse(word, schedule, now, today)
    return REQUEUED, word


def known_newest_first(known):
    return sorted(known, key=lambda w: w.get("archived_at") or "", reverse=True)


def parse_numbers(text):
    """"2 5 7" -> [2, 5, 7]; returns None if the text is not a list of numbers."""
    tokens = (text or "").replace(",", " ").split()
    if not tokens or not all(token.isdigit() for token in tokens):
        return None
    return [int(token) for token in tokens]


def restore_from_known(queue, known, word_ids, schedule=None, now=None):
    """Забытые слова возвращаются в обучение с самого начала."""
    restored = []
    for word_id in word_ids:
        index = find_index(known, word_id)
        if index < 0:
            continue
        word = known.pop(index)
        word.pop("sent_at", None)
        if schedule is not None and now is not None:
            srs.reset(word, schedule, now)
        else:
            word["stage"], word["archived_at"] = 0, None
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
