"""Упражнения с пропуском по программе A1–B2 (см. curriculum).

Какие правила дать, решает код (`curriculum.plan_batch`): слабые места, новые правила,
повтор освоенных. Модель только пишет предложения под эти правила. Каждое задание код
проверяет (один пропуск, ответ среди вариантов, подсказка в скобках, русский текст без мусора)
и сам сверяет ответы пользователя.
"""

import random
import re
from datetime import timedelta

from . import ai
from . import curriculum as cur
from . import practice as pr
from . import words as w
from .text import clean, has_cyrillic, has_foreign_script

GAP = "___"
_GAP_RE = re.compile(r"_{2,}")
BATCH = 5
MISTAKE_LOG_LIMIT = 500
DISPUTED = "disputed"  # «🤔 Спорное»: не засчитывается и не попадает в журнал

# Жизненные ситуации для разнообразия, иначе модель пишет одни и те же предложения.
SITUATIONS = [
    "daily routine", "food and cooking", "shopping", "travel and holidays", "work and colleagues",
    "friends and small talk", "feelings and mood", "health", "home and chores", "money",
    "weather", "making plans", "phone calls and messaging", "hobbies", "getting around the city",
    "restaurants and cafes", "relationships", "problems and complaints", "studying", "the internet",
    "films and music", "sport", "sleep", "family", "the office", "a trip abroad",
]

MODES = {
    cur.CHOICE: "the gap takes one word or short phrase; give 3-4 'options' including the answer, "
                "and only the answer may fit the sentence; no hint in brackets",
    cur.FORM: "put the base word(s) in brackets right after the gap, e.g. 'She ___ (live) here since 2020.' "
              "or 'Thanks for your ___ (kind).'; the answer is their correct form",
}


def uses_buttons(item):
    return item["mode"] == cur.CHOICE


def build_request(level, plan, vocab_words=(), mistakes=(), model=ai.DEFAULT_MODEL, rng=random):
    """Запрос к модели: по одному заданию на каждое правило из плана."""
    system = (
        f"You write short English exercises for a native Russian speaker at CEFR level {level}. "
        "Each exercise is one natural, conversational sentence with exactly one gap written as ___. "
        f"Answer modes: 'choice' — {MODES[cur.CHOICE]}; 'form' — {MODES[cur.FORM]}. "
        "The context must make the answer unambiguous. List in 'accepted' every other fully correct answer "
        "(contractions, full forms), or []. Copy each exercise's rule id into 'rule' exactly. "
        "'explanation_ru': one short sentence in natural Russian explaining the rule, no other languages. "
        "'translation_ru': a natural Russian translation of the whole sentence with the gap filled in. "
    )
    lines = []
    for number, rule in enumerate(plan, start=1):
        if rule.id == cur.VOCAB.id:
            lines.append(f"{number}. rule '{rule.id}' ({cur.FORM}): one of the learner's words in the right form — "
                         f"{', '.join(vocab_words)}; put the base word in brackets and in 'word'.")
        else:
            lines.append(f"{number}. rule '{rule.id}' ({rule.mode}): {rule.en}.")
    user = "Write these exercises:\n" + "\n".join(lines)
    user += f"\nSet the sentences in this situation: {rng.choice(SITUATIONS)}."
    if mistakes:
        examples = "; ".join(f"'{m['sentence']}' (answered '{m['given']}', correct '{m['answer']}')" for m in mistakes)
        user += (f"\nThe learner recently got these wrong: {examples}. Test the same rules again in new sentences "
                 "and different constructions (questions, negatives, other contexts), not copies.")
    return ai.request(system, user, ai.ExerciseBatch, 400 + 300 * len(plan), 0.7, model)


def _stem(token):
    return token[: max(3, len(token) - 2)]


def _matches_word(answer, words):
    """Ответ «вставь слово» должен быть формой слова из словаря: cancel -> cancelled,
    look forward to -> looking forward to. Каждое слово фразы узнаётся по началу."""
    given = pr.normalize(answer).split()
    for word in words:
        base = pr.normalize(word).split()
        if base and all(any(g.startswith(_stem(b)) for g in given) for b in base):
            return word
    return None


def validate(raw, requested=None, vocab_words=()):
    """Готовое к показу задание (dict для KV) или None, если модель ошиблась.

    `raw` — ai.GeneratedExercise; `requested` — id правил из плана (None — любое из программы).
    """
    rule = cur.BY_ID.get(cur.rule_id(raw.rule))
    if rule is None or (requested is not None and rule.id not in requested):
        return None
    sentence = _GAP_RE.sub(GAP, clean(raw.sentence))
    answer = clean(raw.answer)
    explanation = clean(raw.explanation_ru)
    if sentence.count(GAP) != 1 or not answer or has_cyrillic(sentence) or has_cyrillic(answer):
        return None
    if not has_cyrillic(explanation) or any(has_foreign_script(x) for x in (sentence, answer, explanation)):
        return None
    translation = clean(raw.translation_ru)
    if not has_cyrillic(translation) or has_foreign_script(translation):
        translation = ""  # перевод необязателен: без него задание всё равно полезно
    item = {
        "rule": rule.id,
        "group": rule.group,
        "mode": rule.mode,
        "sentence": sentence,
        "answer": answer,
        "accepted": [clean(a) for a in raw.accepted if clean(a) and clean(a) != answer],
        "explanation": explanation,
        "translation": translation,
    }
    if rule.mode == cur.CHOICE:
        options = []
        for option in map(clean, raw.options):
            if option and option.lower() not in [o.lower() for o in options]:
                options.append(option)
        if answer.lower() not in [o.lower() for o in options] or not 2 <= len(options) <= 5:
            return None
        item["options"] = options
    elif "(" not in sentence:
        return None  # без подсказки в скобках задание не решить
    if rule.id == cur.VOCAB.id:
        word = _matches_word(answer, vocab_words)
        if word is None:
            return None
        item["word"] = word
    return item


def parse(output, requested=None, vocab_words=(), limit=BATCH):
    batch = ai.parse(ai.ExerciseBatch, output)
    seen, result = set(), []
    for raw in batch.exercises if batch else []:
        item = validate(raw, requested, vocab_words)
        if item is None:
            print(f"exercises: dropped {raw}"[:240])
            continue
        key = w.compact(item["sentence"])
        if key not in seen:
            seen.add(key)
            result.append(item)
        if len(result) >= limit:
            break
    return result


# Старые задания (до программы) в сессии или запасе: {"type": "preposition", "rule": "prep_time", ...}.
_LEGACY_BUTTON_TYPES = {"preposition", "article", "phrasal"}


def normalize(item):
    """Задание в текущем формате; старые из KV дополняются группой и режимом."""
    if "mode" in item:
        return item
    rule = cur.BY_ID.get(cur.rule_id(item.get("rule", "")), cur.VOCAB if item.get("type") == "vocab" else None)
    item = dict(item)
    item["rule"] = rule.id if rule else item.get("rule", "")
    item["group"] = rule.group if rule else "tenses"
    item["mode"] = cur.CHOICE if item.get("type") in _LEGACY_BUTTON_TYPES else cur.FORM
    item.setdefault("translation", "")
    return item


_HINT_AFTER_GAP = re.compile(re.escape(GAP) + r"\s*\([^)]*\)")
# Подсказка, которую модель иногда ставит не сразу после пропуска: "Money can't buy ___. (happy)".
_STRAY_HINT = re.compile(r"\s*\([A-Za-z' /]+\)")


def filled_sentence(item):
    """(до, ответ, после): ответ на месте пропуска, подсказка в скобках убрана — "___ (careful)" -> "carefully"."""
    sentence = item["sentence"]
    match = _HINT_AFTER_GAP.search(sentence)
    start, end = (match.start(), match.end()) if match else (sentence.index(GAP), sentence.index(GAP) + len(GAP))
    before, after = sentence[:start], sentence[end:]
    if item["mode"] == cur.FORM:
        before, after = _STRAY_HINT.sub("", before), _STRAY_HINT.sub("", after)
    return before, item["answer"], after


def grade(item, given):
    """OK / NEAR / WRONG. Кнопки сверяются точно, ввод — с допуском одной опечатки."""
    if uses_buttons(item):
        return pr.OK if given.strip().lower() == item["answer"].lower() else pr.WRONG
    expected = " / ".join([item["answer"]] + item.get("accepted", []))
    verdict = pr.grade(given, expected, english=True)
    if verdict == pr.NEAR:
        # В грамматике опечатка — одна буква в тех же словах. "lived" вместо "has lived" (пропущено слово)
        # и "have lived" вместо "has lived" (согласование) — это ошибки правила, а не опечатки.
        tokens = pr.normalize(given).split()
        same_shape = [o for o in pr.variants(expected, True) if len(o.split()) == len(tokens)]
        if not any(pr.levenshtein(" ".join(tokens), o) <= 1 for o in same_shape):
            return pr.WRONG
    return verdict


def weak_spots(log, now, top=3):
    return cur.weak_rules(log, now, w.parse_iso, top)


def recent_mistakes(log, rules, limit=4):
    wrong, seen = [], set()
    for entry in reversed(log):
        if cur.rule_id(entry["rule"]) in rules and entry["verdict"] != pr.OK and entry["sentence"] not in seen:
            seen.add(entry["sentence"])
            wrong.append(entry)
    return wrong[:limit][::-1]


def weekly_stats(log, now, days=7):
    since = (now - timedelta(days=days)).isoformat()
    recent = [e for e in log if e["at"] >= since]
    counts = {pr.OK: 0, pr.NEAR: 0, pr.WRONG: 0}
    for entry in recent:
        counts[entry["verdict"]] = counts.get(entry["verdict"], 0) + 1
    return {"total": len(recent), "counts": counts}


async def generate(ai_client, level, count, groups, vocab_words=(), log=(), now=None, model=ai.DEFAULT_MODEL,
                   rng=random):
    """Пачка по плану программы. Отбракованные задания запрашиваются ещё раз — по тем же правилам."""
    include_vocab = "vocab" in groups and len(vocab_words) >= 3
    plan = cur.plan_batch(log, now, w.parse_iso, level, groups, count, include_vocab, rng)
    weak_ids = {s["rule"] for s in weak_spots(log, now)}
    mistakes = recent_mistakes(log, weak_ids)
    result, missing = [], list(plan)
    for _ in range(3):
        if not missing:
            break
        output = await ai_client.run(model, build_request(level, missing, vocab_words, mistakes, model, rng))
        seen = {w.compact(x["sentence"]) for x in result}
        for item in parse(output, {r.id for r in missing}, vocab_words, limit=len(missing)):
            rule = cur.BY_ID[item["rule"]]
            # По одному заданию на пункт плана: лишнее на то же правило не берётся.
            if rule in missing and w.compact(item["sentence"]) not in seen:
                missing.remove(rule)
                result.append(item)
    return result[:count]


# ---- недельный разбор -----------------------------------------------------------------------


def build_advice_request(level, weak, practice_worst, exercise_counts, progress_rows, model=ai.DEFAULT_MODEL):
    """ИИ получает только посчитанные кодом цифры и формулирует совет; решения он не принимает."""
    facts = [f"rule '{cur.rule_name(s['rule'])}': {s['wrong']} wrong of {s['count']}" for s in weak]
    if practice_worst:
        facts.append("words most often wrong when typing: " + ", ".join(f"{en} ({n})" for en, n in practice_worst[:5]))
    facts.append(
        f"exercises this week: {exercise_counts[pr.OK]} correct, {exercise_counts[pr.NEAR]} almost, "
        f"{exercise_counts[pr.WRONG]} wrong"
    )
    for row in progress_rows:
        facts.append(f"{row['level']}: {row['confident']} of {row['total']} grammar points mastered")
    system = (
        f"You are a friendly English tutor for a native Russian speaker at CEFR level {level}. "
        "Given the learner's weekly results, write 2-3 short sentences of concrete advice in natural Russian: "
        "name the weakest rule, give one tip or a tiny example how to remember it, and one thing to focus on "
        "next week. English only inside short examples. No greetings, no lists, plain text."
    )
    return {
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": "; ".join(facts)}],
        "max_tokens": 300,
        "temperature": 0.5,
        **ai.model_options(model),
    }


def parse_advice(output):
    text = ai.text_payload(output)
    if not has_cyrillic(text) or has_foreign_script(text):
        return None
    return text[:700]


async def advise(ai_client, level, weak, practice_worst, exercise_counts, progress_rows, model=ai.DEFAULT_MODEL):
    if not weak and not practice_worst:
        return None
    request = build_advice_request(level, weak, practice_worst, exercise_counts, progress_rows, model)
    return parse_advice(await ai_client.run(model, request))
