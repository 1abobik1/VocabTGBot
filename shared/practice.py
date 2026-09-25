"""Saturday practice: typed translations of learned words, graded with typo tolerance.

Слово попадает сюда, пройдя интервальные повторы. На практике его нужно написать
RU→EN и EN→RU. Всё верно — в архив. Опечатка (1–2 буквы) — завтра ещё одна карточка
EN→RU. Ошибка — слово учится заново с RU→EN.
"""

import re
from datetime import timedelta

from . import srs
from .text import (
    strip_extras,  # noqa: F401 — используется и снаружи как pr.strip_extras
)

OK = "ok"
NEAR = "near"
WRONG = "wrong"

RU_EN = "ru_en"  # Russian shown, English typed
EN_RU = "en_ru"  # English shown, Russian typed
DIRECTIONS = (RU_EN, EN_RU)

_NUMBERING = re.compile(r"^\s*\d+\s*[).:\-–—]?\s+|^\s*\d+\s*[).:]\s*")

# Транскрипция и пометки в скобках на проверку не влияют (см. text.strip_extras).
# Несколько вариантов перевода: "couch / sofa", "догнать, наверстать", "женат или замужем".
_VARIANT_SPLIT = re.compile(r"[,;/]|\bили\b")
_NON_WORD = re.compile(r"[^\w\s]", re.UNICODE)
_EN_PREFIX = re.compile(r"^(to|a|an|the)\s+")
_SKIP_ANSWERS = {"", "-", "—", "?", "...", "…"}


def normalize(text):
    text = strip_extras(text).lower().replace("ё", "е").replace("’", "'").replace("'", "")
    return " ".join(_NON_WORD.sub(" ", text).split())


def readable_variants(expected):
    """Варианты перевода в исходном виде, без транскрипции: "couch / sofa" -> ["couch", "sofa"]."""
    parts = [p.strip() for p in _VARIANT_SPLIT.split(strip_extras(expected))]
    return [p for p in parts if p]


def variants(expected, english):
    """Что засчитывается: вся строка целиком и каждый вариант по отдельности."""
    result = []
    for part in [strip_extras(expected)] + readable_variants(expected):
        value = normalize(part)
        if english:
            value = _EN_PREFIX.sub("", value)
        if value and value not in result:
            result.append(value)
    return result


def other_variants(expected, answer, english):
    """Варианты, которые тоже подошли бы, кроме написанного: ответ "sofa" -> ["couch"]."""
    options = readable_variants(expected)
    if len(options) < 2:
        return []
    given = normalize(answer)
    extra = []
    for option in options:
        value = normalize(option)
        if english:
            value = _EN_PREFIX.sub("", value)
        if value and value not in given:  # вариант, который не написан в ответе
            extra.append(option)
    return extra


def levenshtein(a, b):
    """Edit distance where swapping two neighbouring letters ("teh") counts as one typo."""
    rows = [list(range(len(b) + 1))]
    for i in range(1, len(a) + 1):
        row = [i] + [0] * len(b)
        for j in range(1, len(b) + 1):
            cost = a[i - 1] != b[j - 1]
            row[j] = min(rows[i - 1][j] + 1, row[j - 1] + 1, rows[i - 1][j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                row[j] = min(row[j], rows[i - 2][j - 2] + 1)
        rows.append(row)
    return rows[-1][-1]


def _allowed_typos(length):
    return 1 if length <= 5 else 2


def grade(answer, expected, english):
    """OK, NEAR (a couple of letters off) or WRONG."""
    given = normalize(answer)
    if english:
        given = _EN_PREFIX.sub("", given)
    if not given or given in _SKIP_ANSWERS:
        return WRONG
    options = variants(expected, english)
    if given in options:
        return OK
    if any(levenshtein(given, option) <= _allowed_typos(len(option)) for option in options):
        return NEAR
    return WRONG


def _similarity(answer, expected, english):
    given = normalize(answer)
    options = variants(expected, english) or [""]
    best = min(levenshtein(given, option) / max(len(given), len(option), 1) for option in options)
    return 1 - best


def _strip_numbering(item):
    return _NUMBERING.sub("", item, count=1).strip()


def split_answers(text, expected, english):
    """Split a reply into one answer per expected word.

    Separators: new line, tab, comma or semicolon. Plain spaces work too; then multi-word
    answers ("hit it off") are found by matching the tokens against the expected words.
    """
    count = len(expected)
    items = [_strip_numbering(x) for x in re.split(r"[\n\t,;]+", text or "")]
    items = [x for x in items if x]
    if len(items) == count:
        return items
    tokens = [t for t in re.split(r"[\s,;]+", text or "") if t and not re.fullmatch(r"\d+[).:]?", t)]
    if len(tokens) <= count:
        return tokens + [""] * (count - len(tokens))
    # Best split of the tokens into `count` consecutive groups (dynamic programming).
    m = len(tokens)
    best = [[float("-inf")] * (count + 1) for _ in range(m + 1)]
    back = [[0] * (count + 1) for _ in range(m + 1)]
    best[0][0] = 0.0
    for j in range(1, count + 1):
        for i in range(j, m - (count - j) + 1):
            for k in range(j - 1, i):
                if best[k][j - 1] == float("-inf"):
                    continue
                score = best[k][j - 1] + _similarity(" ".join(tokens[k:i]), expected[j - 1], english)
                if score > best[i][j]:
                    best[i][j], back[i][j] = score, k
    groups, i = [], m
    for j in range(count, 0, -1):
        k = back[i][j]
        groups.append(" ".join(tokens[k:i]))
        i = k
    return groups[::-1]


def expected_answers(words, direction):
    field = "en" if direction == RU_EN else "ru"
    return [word[field] for word in words]


def grade_round(words, direction, text):
    """[(слово, ответ, вердикт, другие подходящие варианты)] в порядке `words`."""
    english = direction == RU_EN
    expected = expected_answers(words, direction)
    answers = split_answers(text, expected, english)
    return [
        (word, answer, grade(answer, exp, english), other_variants(exp, answer, english) if grade(answer, exp, english) == OK else [])
        for word, answer, exp in zip(words, answers, expected)
    ]


def final_verdict(results):
    """Combine both directions: any WRONG -> WRONG, else any NEAR -> NEAR, else OK."""
    verdicts = set(results.values())
    if WRONG in verdicts or not verdicts:
        return WRONG
    return NEAR if NEAR in verdicts else OK


def apply_results(queue, known, practice, results, schedule, now):
    """OK -> архив, NEAR -> завтра ещё одна карточка EN→RU, WRONG -> слово учится заново.

    `results`: id слова -> {направление: вердикт}. Возвращает {вердикт: [слова]}.
    """
    outcome = {OK: [], NEAR: [], WRONG: []}
    for word_id, per_direction in results.items():
        index = next((i for i, x in enumerate(practice) if x["id"] == word_id), -1)
        if index < 0:
            continue
        word = practice.pop(index)
        verdict = final_verdict(per_direction)
        word.pop("practice_since", None)
        if verdict == OK:
            word["archived_at"] = now.isoformat()
            word.pop("due_at", None)
            known.append(word)
        else:
            # Опечатка -> сразу вторая сторона, ошибка -> с начала; в обоих случаях завтра утром.
            srs.reset(word, schedule, now, hard=verdict == WRONG)
            srs.postpone_to_tomorrow(word, schedule, now)
            queue.append(word)
        outcome[verdict].append(word)
    return outcome


def weekly_practice_stats(log, now, days=7):
    """Итоги практики за неделю: счётчики по направлениям и слова с ошибками, самые частые первыми."""
    since = (now - timedelta(days=days)).isoformat()
    recent = [x for x in log if x.get("at", "") >= since]
    counts = {d: {OK: 0, NEAR: 0, WRONG: 0} for d in DIRECTIONS}
    misses = {}
    for entry in recent:
        for direction in DIRECTIONS:
            verdict = entry.get(direction)
            if verdict in counts[direction]:
                counts[direction][verdict] += 1
                if verdict != OK:
                    misses[entry["en"]] = misses.get(entry["en"], 0) + 1
    worst = sorted(misses.items(), key=lambda item: (-item[1], item[0]))
    return {"sessions_words": len(recent), "counts": counts, "worst": worst}
