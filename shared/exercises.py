"""Упражнения с пропуском: предлоги, времена, слова из словаря, артикли, формы слова, фразовые глаголы.

ИИ генерирует пачку заданий, код проверяет каждое (один пропуск, ответ среди вариантов, правило
из каталога, русское объяснение без мусора) и сверяет ответы пользователя сам — без ИИ.
Ошибки пишутся в журнал с id правила; по нему `weak_spots` находит слабые места, и следующая
пачка на ~60% состоит из заданий на них, но в других предложениях и конструкциях.
"""

import json
import random
import re
from datetime import timedelta

from . import practice as pr
from . import words as w
from .generator import _response_payload

# Тип -> (название, отвечать кнопками?)
TYPES = {
    "preposition": ("Предлоги", True),
    "tense": ("Времена глаголов", False),
    "vocab": ("Слова из твоего словаря", False),
    "article": ("Артикли", True),
    "word_form": ("Формы слова", False),
    "phrasal": ("Фразовые глаголы", True),
}

# Каталог правил: одинаковые ошибки группируются по id, а не по формулировке модели.
RULES = {
    "preposition": {
        "prep_time": "предлоги времени (in/on/at)",
        "prep_place": "предлоги места",
        "prep_movement": "предлоги движения",
        "prep_dependent": "предлоги после глаголов и прилагательных",
    },
    "tense": {
        "present_simple": "Present Simple",
        "present_continuous": "Present Continuous",
        "past_simple": "Past Simple",
        "past_continuous": "Past Continuous",
        "present_perfect": "Present Perfect",
        "present_perfect_continuous": "Present Perfect Continuous",
        "past_perfect": "Past Perfect",
        "future_will": "Future Simple (will)",
        "going_to": "be going to",
        "future_perfect": "Future Perfect",
        "conditional_1": "условные предложения 1-го типа",
        "conditional_2": "условные предложения 2-го типа",
        "conditional_3": "условные предложения 3-го типа",
        "passive": "пассивный залог",
    },
    "vocab": {"vocab": "слова из словаря"},
    "article": {"article_a_an": "артикль a/an", "article_the": "артикль the", "article_zero": "без артикля"},
    "word_form": {
        "noun_form": "образование существительных",
        "adjective_form": "образование прилагательных",
        "adverb_form": "образование наречий",
        "verb_form": "формы глагола",
    },
    "phrasal": {"phrasal_particle": "частицы фразовых глаголов"},
}

GAP = "___"
_GAP_RE = re.compile(r"_{2,}")
_CYRILLIC = re.compile("[а-яё]", re.IGNORECASE)
_FOREIGN = re.compile(r"[　-鿿가-힯]")  # иероглифы, кана, хангыль

BATCH = 5
# Доля заданий на слабые места, когда они есть.
FOCUS_SHARE = 0.6
WEAK_WINDOW_DAYS = 30
MISTAKE_LOG_LIMIT = 500


def rule_name(rule):
    for rules in RULES.values():
        if rule in rules:
            return rules[rule]
    return rule


def rule_type(rule):
    for kind, rules in RULES.items():
        if rule in rules:
            return kind
    return None


def enabled_types(settings, has_vocab=True):
    """Типы из настроек; пусто — все (ИИ подбирает по уровню). Без слов в словаре vocab нет."""
    chosen = [t for t in settings.get("ex_types") or [] if t in TYPES]
    types = chosen or list(TYPES)
    return [t for t in types if has_vocab or t != "vocab"] or ["preposition", "tense"]


def plan_counts(types, count, rng=random):
    """Сколько заданий какого типа: поровну, остаток — случайным типам."""
    counts = {t: count // len(types) for t in types}
    for t in rng.sample(types, count % len(types)):
        counts[t] += 1
    return {t: n for t, n in counts.items() if n}


def build_input(level, counts, vocab_words=(), focus=(), mistakes=()):
    catalog = "; ".join(f"{kind}: {', '.join(rules)}" for kind, rules in RULES.items() if kind in counts)
    plan = ", ".join(f"{n} {kind}" for kind, n in counts.items())
    system = (
        f"You create short English exercises for a native Russian speaker at CEFR level {level}. "
        "Every exercise is one natural, conversational sentence with exactly one gap written as ___. "
        "Types: 'preposition' — one preposition, give 4 options including the answer; "
        "'tense' — the correct form of the verb given in brackets right after the gap, e.g. 'She ___ (live) here since 2020.'; "
        f"'vocab' — one of the learner's words in the right form: {', '.join(vocab_words) or 'none'}; put the base word in 'word'; "
        "'article' — a, an, the or '-' for no article, give the 4 options a, an, the, -; "
        "'word_form' — the right form of the word given in brackets, e.g. 'Thanks for your ___ (kind).'; "
        "'phrasal' — the missing particle of a phrasal verb, give 4 options. "
        "The context must make the answer unambiguous: no other option may also fit. "
        "List in 'accepted' every other fully correct answer (contractions, full forms), or []. "
        f"'rule' must be one id from this catalog: {catalog}. "
        "'explanation_ru' is one short sentence in natural Russian explaining the rule, no other languages. "
        'Answer with JSON only: {"exercises":[{"type":"","sentence":"","answer":"","accepted":[],"options":[],'
        '"rule":"","word":"","explanation_ru":""}]}'
    )
    user = f"Make {plan}."
    if focus:
        names = ", ".join(focus)
        user += (
            f" The learner often gets these rules wrong: {names}. Make about {int(FOCUS_SHARE * 100)}% of the "
            "exercises on exactly these rules, but in new sentences and different constructions "
            "(questions, negatives, other contexts), not copies of the old ones."
        )
        if mistakes:
            examples = "; ".join(f"'{m['sentence']}' (answered '{m['given']}', correct '{m['answer']}')" for m in mistakes)
            user += f" Recent mistakes: {examples}."
    return {
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "max_tokens": 400 + 250 * sum(counts.values()),
        "temperature": 0.7,
    }


def _clean(text):
    return " ".join(str(text or "").split())


def _matches_word(answer, words):
    """Ответ vocab-задания должен быть формой слова из словаря: cancel -> cancelled."""
    compact = w.compact(answer)
    for word in words:
        base = w.compact(word)
        if base and (base in compact or compact.startswith(base[: max(3, len(base) - 2)])):
            return word
    return None


def validate(raw, vocab_words=()):
    """Готовое к показу упражнение или None, если модель ошиблась в формате."""
    if not isinstance(raw, dict):
        return None
    kind = raw.get("type")
    if kind not in TYPES:
        return None
    sentence = _GAP_RE.sub(GAP, _clean(raw.get("sentence")))
    answer = _clean(raw.get("answer"))
    explanation = _clean(raw.get("explanation_ru"))
    if sentence.count(GAP) != 1 or not answer or _CYRILLIC.search(sentence) or _CYRILLIC.search(answer):
        return None
    if not _CYRILLIC.search(explanation) or any(_FOREIGN.search(x) for x in (sentence, answer, explanation)):
        return None
    if kind in ("tense", "word_form") and "(" not in sentence:
        return None  # без подсказки в скобках задание не решить
    exercise = {
        "type": kind,
        "sentence": sentence,
        "answer": answer,
        "accepted": [_clean(a) for a in raw.get("accepted") or [] if _clean(a) and _clean(a) != answer],
        "rule": raw.get("rule") if raw.get("rule") in RULES[kind] else next(iter(RULES[kind])),
        "explanation": explanation,
    }
    if TYPES[kind][1]:
        options = []
        for option in raw.get("options") or []:
            option = _clean(option)
            if option and option.lower() not in [o.lower() for o in options]:
                options.append(option)
        if answer.lower() not in [o.lower() for o in options] or not 2 <= len(options) <= 5:
            return None
        exercise["options"] = options
    if kind == "vocab":
        word = _matches_word(answer, vocab_words)
        if word is None:
            return None
        exercise["word"] = word
    return exercise


def parse(output, vocab_words=(), limit=BATCH):
    seen, result = set(), []
    for raw in _response_payload(output).get("exercises") or []:
        exercise = validate(raw, vocab_words)
        if exercise is None:
            print(f"exercises: dropped {json.dumps(raw, ensure_ascii=False)[:200]}")
            continue
        key = w.compact(exercise["sentence"])
        if key in seen:
            continue
        seen.add(key)
        result.append(exercise)
        if len(result) >= limit:
            break
    return result


def grade(exercise, given):
    """OK / NEAR / WRONG. Кнопки сверяются точно, ввод — с допуском опечаток, как на практике."""
    if TYPES[exercise["type"]][1]:
        return pr.OK if given.strip().lower() == exercise["answer"].lower() else pr.WRONG
    expected = " / ".join([exercise["answer"]] + exercise.get("accepted", []))
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
    """Правила, в которых чаще ошибаешься: свежие ошибки весят больше (вес падает вдвое за неделю)."""
    since = now - timedelta(days=WEAK_WINDOW_DAYS)
    stats = {}
    for entry in log:
        at = w.parse_iso(entry["at"])
        if at < since:
            continue
        weight = 0.5 ** ((now - at).days / 7)
        s = stats.setdefault(entry["rule"], {"errors": 0.0, "total": 0.0, "wrong": 0, "count": 0})
        s["total"] += weight
        s["count"] += 1
        if entry["verdict"] != pr.OK:
            s["errors"] += weight
            s["wrong"] += 1
    weak = [
        (rule, s) for rule, s in stats.items()
        if s["count"] >= 2 and s["errors"] / s["total"] >= 0.34
    ]
    weak.sort(key=lambda item: (-item[1]["errors"], -item[1]["errors"] / item[1]["total"]))
    return [{"rule": rule, "wrong": s["wrong"], "count": s["count"]} for rule, s in weak[:top]]


def recent_mistakes(log, rules, limit=4):
    wrong = [e for e in log if e["rule"] in rules and e["verdict"] != pr.OK]
    return wrong[-limit:]


def weekly_stats(log, now, days=7):
    since = (now - timedelta(days=days)).isoformat()
    recent = [e for e in log if e["at"] >= since]
    counts = {pr.OK: 0, pr.NEAR: 0, pr.WRONG: 0}
    for entry in recent:
        counts[entry["verdict"]] = counts.get(entry["verdict"], 0) + 1
    return {"total": len(recent), "counts": counts}


async def generate(ai, level, count, types, vocab_words=(), log=(), now=None, model=None):
    """Пачка упражнений с учётом слабых мест. Недобранные после проверки запрашиваются ещё раз."""
    from .generator import DEFAULT_MODEL, model_options

    model = model or DEFAULT_MODEL
    weak = [s["rule"] for s in weak_spots(log, now)] if now is not None else []
    weak = [r for r in weak if rule_type(r) in types]
    mistakes = recent_mistakes(log, set(weak))
    result = []
    for _ in range(3):
        missing = count - len(result)
        if missing <= 0:
            break
        payload = build_input(level, plan_counts(types, missing), vocab_words, weak, mistakes)
        output = await ai.run(model, {**payload, **model_options(model)})
        for exercise in parse(output, vocab_words, limit=missing):
            if w.compact(exercise["sentence"]) not in {w.compact(x["sentence"]) for x in result}:
                result.append(exercise)
    return result[:count]


def build_advice_input(level, weak, practice_worst, exercise_counts):
    """Недельный разбор: ИИ получает только агрегированные цифры, а не весь журнал."""
    facts = []
    for spot in weak:
        facts.append(f"rule '{rule_name(spot['rule'])}': {spot['wrong']} wrong of {spot['count']}")
    if practice_worst:
        facts.append("words most often wrong when typing: " + ", ".join(f"{en} ({n})" for en, n in practice_worst[:5]))
    facts.append(
        f"exercises this week: {exercise_counts[pr.OK]} correct, {exercise_counts[pr.NEAR]} almost, "
        f"{exercise_counts[pr.WRONG]} wrong"
    )
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
    }


def parse_advice(output):
    payload = output.get("response") if isinstance(output, dict) else output
    if payload is None and isinstance(output, dict) and output.get("choices"):
        payload = output["choices"][0]["message"].get("content")
    text = " ".join(str(payload or "").split())
    if not _CYRILLIC.search(text) or _FOREIGN.search(text):
        return None
    return text[:700]


async def advise(ai, level, weak, practice_worst, exercise_counts, model=None):
    from .generator import DEFAULT_MODEL, model_options

    if not weak and not practice_worst:
        return None
    model = model or DEFAULT_MODEL
    output = await ai.run(model, {**build_advice_input(level, weak, practice_worst, exercise_counts), **model_options(model)})
    return parse_advice(output)
