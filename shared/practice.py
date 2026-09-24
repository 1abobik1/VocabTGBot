"""Saturday practice: typed translations of learned words, graded with typo tolerance.

Слово попадает сюда, пройдя интервальные повторы. На практике его нужно написать
RU→EN и EN→RU. Всё верно — в архив. Опечатка (1–2 буквы) — завтра ещё одна карточка
EN→RU. Ошибка — слово учится заново с RU→EN.
"""

import re

from . import srs

OK = "ok"
NEAR = "near"
WRONG = "wrong"

RU_EN = "ru_en"  # Russian shown, English typed
EN_RU = "en_ru"  # English shown, Russian typed
DIRECTIONS = (RU_EN, EN_RU)



_NUMBERING = re.compile(r"^\s*\d+\s*[).:\-–—]?\s+|^\s*\d+\s*[).:]\s*")
_PARENS = re.compile(r"\([^)]*\)")
_NON_WORD = re.compile(r"[^\w\s]", re.UNICODE)
_EN_PREFIX = re.compile(r"^(to|a|an|the)\s+")
_SKIP_ANSWERS = {"", "-", "—", "?", "...", "…"}


def normalize(text):
    text = (text or "").lower().replace("ё", "е").replace("’", "'").replace("'", "")
    text = _NON_WORD.sub(" ", _PARENS.sub(" ", text))
    return " ".join(text.split())


def variants(expected, english):
    """Acceptable answers: the whole translation and each part of "догнать, наверстать" or "женат/замужем"."""
    parts = [expected] + re.split(r"[,;/]| или ", _PARENS.sub(" ", expected))
    result = []
    for part in parts:
        value = normalize(part)
        if english:
            value = _EN_PREFIX.sub("", value)
        if value and value not in result:
            result.append(value)
    return result


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
    """Returns [(word, answer, verdict)] in the order of `words`."""
    english = direction == RU_EN
    expected = expected_answers(words, direction)
    answers = split_answers(text, expected, english)
    return [(word, answer, grade(answer, exp, english)) for word, answer, exp in zip(words, answers, expected)]


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
