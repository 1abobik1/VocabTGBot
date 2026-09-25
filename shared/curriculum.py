"""Программа грамматики A1–B2 и оценка того, что уже освоено.

Правила взяты из British Council – EAQUALS Core Inventory for General English, 2nd ed., 2015
(раздел Grammar по уровням): https://www.teachingenglish.org.uk/sites/teacheng/files/pub-british-council-eaquals-core-inventoryv2.pdf
Пункт, который повторяется на нескольких уровнях, стоит на первом из них. Пункты, которые
не проверить упражнением с пропуском (порядок слов в наречных оборотах, «ответы в прошедшем»
и т. п.), в программу не включены, чтобы не искажать процент освоения. C1 не входит.

Что изучать дальше, решает код по этой программе и журналу ответов, а не модель:
модель только пишет предложения под заданные правила.
"""

import random
from dataclasses import dataclass
from datetime import timedelta

LEVELS = ("A1", "A2", "B1", "B2")
DEFAULT_LEVEL = "B1"

CHOICE = "choice"  # ответ кнопкой из 3–4 вариантов
FORM = "form"  # ввести нужную форму слова из скобок

# Группы тем: их можно включать и выключать в «Темах упражнений».
GROUPS = {
    "tenses": "Времена глаголов",
    "modals": "Модальные глаголы",
    "conditionals": "Условные предложения и wish",
    "passive_reported": "Пассив и косвенная речь",
    "prepositions": "Предлоги",
    "articles_quantity": "Артикли и количество",
    "adjectives_adverbs": "Прилагательные и наречия",
    "verb_patterns": "Герундий, инфинитив, фразовые глаголы",
    "structure": "Вопросы, местоимения, связки",
    "vocab": "Слова из твоего словаря",
}


@dataclass(frozen=True)
class Rule:
    id: str
    level: str
    group: str
    mode: str
    name: str  # по-русски, для статистики
    en: str  # для модели: что именно проверять


RULES = [
    # ---- A1 ------------------------------------------------------------------------
    Rule("a1_to_be", "A1", "tenses", CHOICE, "to be (am / is / are)", "to be in the present: am/is/are, incl. questions and negatives"),
    Rule("a1_present_simple", "A1", "tenses", FORM, "Present Simple", "present simple for habits and facts, incl. he/she -s and do/does"),
    Rule("a1_present_continuous", "A1", "tenses", FORM, "Present Continuous", "present continuous for actions happening now"),
    Rule("a1_was_were", "A1", "tenses", CHOICE, "was / were", "past simple of to be: was/were"),
    Rule("a1_past_simple", "A1", "tenses", FORM, "Past Simple", "past simple of regular and common irregular verbs"),
    Rule("a1_going_to", "A1", "tenses", FORM, "be going to", "be going to for plans"),
    Rule("a1_imperatives", "A1", "verb_patterns", FORM, "повелительное наклонение", "imperatives, positive and negative (Don't ...)"),
    Rule("a1_like_ing", "A1", "verb_patterns", FORM, "like / love / hate + -ing", "like/love/hate + -ing"),
    Rule("a1_would_like", "A1", "verb_patterns", CHOICE, "I'd like", "I'd like / would you like for polite requests and offers"),
    Rule("a1_can_could", "A1", "modals", CHOICE, "can / can't / could / couldn't", "can/can't/could/couldn't for ability and permission"),
    Rule("a1_prepositions_time", "A1", "prepositions", CHOICE, "предлоги времени in / on / at", "prepositions of time in/on/at"),
    Rule("a1_prepositions_place", "A1", "prepositions", CHOICE, "предлоги места", "prepositions of place (in, on, under, next to, behind...)"),
    Rule("a1_prepositions_common", "A1", "prepositions", CHOICE, "частые предлоги", "common prepositions (with, for, from, about...)"),
    Rule("a1_how_much_many", "A1", "articles_quantity", CHOICE, "how much / how many", "how much/how many with countable and uncountable nouns"),
    Rule("a1_demonstratives", "A1", "adjectives_adverbs", CHOICE, "this / that / these / those", "demonstratives this/that/these/those"),
    Rule("a1_frequency_adverbs", "A1", "adjectives_adverbs", CHOICE, "наречия частоты", "adverbs of frequency (always, usually, sometimes, never)"),
    Rule("a1_comparatives", "A1", "adjectives_adverbs", FORM, "сравнительная и превосходная степень", "comparatives and superlatives of adjectives"),
    Rule("a1_possessive_adjectives", "A1", "structure", CHOICE, "my / your / his / her…", "possessive adjectives (my, your, his, her, its, our, their)"),
    Rule("a1_possessive_s", "A1", "structure", FORM, "притяжательный 's", "possessive 's and s'"),
    Rule("a1_pronouns", "A1", "structure", CHOICE, "личные местоимения", "subject and object pronouns (I/me, he/him, they/them)"),
    Rule("a1_questions", "A1", "structure", CHOICE, "вопросы с do / does / is", "questions with do/does/is/are and question words"),
    Rule("a1_there_is_are", "A1", "structure", CHOICE, "there is / there are", "there is / there are"),
    # ---- A2 ------------------------------------------------------------------------
    Rule("a2_past_continuous", "A2", "tenses", FORM, "Past Continuous", "past continuous for an action in progress in the past"),
    Rule("a2_present_perfect", "A2", "tenses", FORM, "Present Perfect", "present perfect for experience and recent results (ever, never, just, already, yet)"),
    Rule("a2_will_going_to", "A2", "tenses", FORM, "will и going to", "will for decisions and promises vs going to for plans"),
    Rule("a2_present_continuous_future", "A2", "tenses", FORM, "Present Continuous для будущего", "present continuous for fixed future arrangements"),
    Rule("a2_wh_questions_past", "A2", "structure", CHOICE, "вопросы в прошедшем", "wh-questions in the past with did"),
    Rule("a2_have_to", "A2", "modals", CHOICE, "have to", "have to / don't have to for obligation"),
    Rule("a2_should", "A2", "modals", CHOICE, "should", "should / shouldn't for advice"),
    Rule("a2_conditional_0_1", "A2", "conditionals", FORM, "условные 0 и 1 типа", "zero and first conditional"),
    Rule("a2_prepositions_movement", "A2", "prepositions", CHOICE, "предлоги движения", "prepositions of movement (into, out of, across, through, towards)"),
    Rule("a2_articles", "A2", "articles_quantity", CHOICE, "артикли a / an / the / —", "articles a/an/the/zero article with countable and uncountable nouns"),
    Rule("a2_much_many", "A2", "articles_quantity", CHOICE, "much / many / a lot of / some / any", "much/many/a lot of/some/any with countable and uncountable nouns"),
    Rule("a2_gerunds", "A2", "verb_patterns", FORM, "герундий", "gerunds after verbs and prepositions (enjoy doing, good at doing)"),
    Rule("a2_verb_ing_infinitive", "A2", "verb_patterns", FORM, "-ing или инфинитив после глагола", "verb + -ing or to-infinitive (want to, would like to, enjoy -ing)"),
    Rule("a2_phrasal_verbs", "A2", "verb_patterns", CHOICE, "частые фразовые глаголы", "common phrasal verbs: choose the particle (get up, turn off, look for)"),
    # ---- B1 ------------------------------------------------------------------------
    Rule("b1_present_perfect_vs_past", "B1", "tenses", FORM, "Present Perfect или Past Simple", "present perfect vs past simple"),
    Rule("b1_present_perfect_continuous", "B1", "tenses", FORM, "Present Perfect Continuous", "present perfect continuous for duration up to now"),
    Rule("b1_past_perfect", "B1", "tenses", FORM, "Past Perfect", "past perfect for an earlier past action"),
    Rule("b1_future_continuous", "B1", "tenses", FORM, "Future Continuous", "future continuous for an action in progress at a future time"),
    Rule("b1_will_going_to_prediction", "B1", "tenses", FORM, "will / going to для прогнозов", "will and going to for predictions (with and without evidence)"),
    Rule("b1_must_have_to", "B1", "modals", CHOICE, "must / have to", "must vs have to, mustn't vs don't have to"),
    Rule("b1_modals_possibility", "B1", "modals", CHOICE, "might / may / will probably", "might, may, will probably for possibility"),
    Rule("b1_modals_deduction", "B1", "modals", CHOICE, "must / can't — вывод", "must/can't for deduction in the present"),
    Rule("b1_modals_past", "B1", "modals", FORM, "should have / might have", "should have / might have / could have + past participle"),
    Rule("b1_conditional_2", "B1", "conditionals", FORM, "условные 2 типа", "second conditional for unreal present or future"),
    Rule("b1_conditional_3", "B1", "conditionals", FORM, "условные 3 типа", "third conditional for unreal past"),
    Rule("b1_passive_simple", "B1", "passive_reported", FORM, "пассив в простых временах", "simple passive in present and past simple"),
    Rule("b1_reported_speech", "B1", "passive_reported", FORM, "косвенная речь", "reported speech with backshift of tenses"),
    Rule("b1_question_tags", "B1", "structure", CHOICE, "разделительные вопросы", "question tags (isn't it? didn't you?)"),
    Rule("b1_linkers", "B1", "structure", CHOICE, "связки причины, следствия, контраста", "linking words for cause, result and contrast (because, so, but, although)"),
    Rule("b1_too_enough", "B1", "adjectives_adverbs", CHOICE, "too / enough", "too and enough with adjectives and nouns"),
    Rule("b1_adverbs", "B1", "adjectives_adverbs", FORM, "наречия от прилагательных", "adverbs of manner formed from adjectives (careful -> carefully)"),
    Rule("b1_phrasal_verbs", "B1", "verb_patterns", CHOICE, "фразовые глаголы", "a wider range of phrasal verbs: choose the particle"),
    # ---- B2 ------------------------------------------------------------------------
    Rule("b2_future_perfect", "B2", "tenses", FORM, "Future Perfect", "future perfect for an action finished before a future time"),
    Rule("b2_future_perfect_continuous", "B2", "tenses", FORM, "Future Perfect Continuous", "future perfect continuous for duration up to a future time"),
    Rule("b2_past_perfect_continuous", "B2", "tenses", FORM, "Past Perfect Continuous", "past perfect continuous for duration up to a past moment"),
    Rule("b2_narrative_tenses", "B2", "tenses", FORM, "времена в рассказе о прошлом", "narrative tenses: past simple, past continuous and past perfect together"),
    Rule("b2_would_habits", "B2", "tenses", FORM, "would / used to для привычек в прошлом", "would and used to for past habits"),
    Rule("b2_modals_speculation", "B2", "modals", CHOICE, "модальные глаголы предположения", "modals of deduction and speculation (might, could, must, can't)"),
    Rule("b2_modals_past_deduction", "B2", "modals", FORM, "can't have / needn't have", "can't have / must have / needn't have + past participle"),
    Rule("b2_mixed_conditionals", "B2", "conditionals", FORM, "смешанные условные", "mixed conditionals (past condition, present result and vice versa)"),
    Rule("b2_wish", "B2", "conditionals", FORM, "wish / if only", "wish and if only for regrets and present wishes"),
    Rule("b2_passive_all", "B2", "passive_reported", FORM, "пассив во всех временах", "passive in all tenses incl. continuous, perfect and with modals"),
    Rule("b2_relative_clauses", "B2", "structure", CHOICE, "who / which / that / whose / where", "relative pronouns in defining and non-defining relative clauses"),
    Rule("b2_linkers_contrast", "B2", "structure", CHOICE, "although / despite / in spite of", "contrast linkers: although, despite, in spite of, however"),
    Rule("b2_adjectives_ed_ing", "B2", "adjectives_adverbs", FORM, "прилагательные на -ed и -ing", "-ed vs -ing adjectives (bored/boring)"),
]
BY_ID = {rule.id: rule for rule in RULES}

# «Вставь слово из словаря» — не пункт программы, а отдельный вид задания.
VOCAB = Rule("vocab", "", "vocab", FORM, "слова из словаря", "one of the learner's own words in the right form")
BY_ID[VOCAB.id] = VOCAB

# Правила из журнала до появления программы -> новые.
LEGACY_RULES = {
    "prep_time": "a1_prepositions_time", "prep_place": "a1_prepositions_place",
    "prep_movement": "a2_prepositions_movement", "prep_dependent": "a1_prepositions_common",
    "present_simple": "a1_present_simple", "present_continuous": "a1_present_continuous",
    "past_simple": "a1_past_simple", "past_continuous": "a2_past_continuous",
    "present_perfect": "a2_present_perfect", "present_perfect_continuous": "b1_present_perfect_continuous",
    "past_perfect": "b1_past_perfect", "future_will": "a2_will_going_to", "going_to": "a1_going_to",
    "future_perfect": "b2_future_perfect", "conditional_1": "a2_conditional_0_1",
    "conditional_2": "b1_conditional_2", "conditional_3": "b1_conditional_3", "passive": "b1_passive_simple",
    "article_a_an": "a2_articles", "article_the": "a2_articles", "article_zero": "a2_articles",
    "noun_form": "b1_adverbs", "adjective_form": "a1_comparatives", "adverb_form": "b1_adverbs",
    "verb_form": "a2_verb_ing_infinitive", "phrasal_particle": "a2_phrasal_verbs",
}
# Старые «типы упражнений» в настройках тем -> группы.
LEGACY_GROUPS = {
    "preposition": "prepositions", "tense": "tenses", "article": "articles_quantity",
    "word_form": "adjectives_adverbs", "phrasal": "verb_patterns",
}

# ---- уровни ------------------------------------------------------------------------------


def normalize_level(value):
    """Уровень из ввода пользователя; None, если такого нет (C1 и выше больше не поддерживаются)."""
    value = (value or "").strip().upper()
    return value if value in LEVELS else None


def clamp_level(value):
    """Сохранённый уровень: C1/C2 из старых настроек превращается в B2."""
    value = (value or "").strip().upper()
    if value in LEVELS:
        return value
    return "B2" if value in ("C1", "C2") else DEFAULT_LEVEL


def levels_up_to(level):
    return LEVELS[: LEVELS.index(clamp_level(level)) + 1]


def rule_id(value):
    return LEGACY_RULES.get(value, value)


def rule_name(value):
    rule = BY_ID.get(rule_id(value))
    return rule.name if rule else value


def enabled_groups(settings, has_vocab=True):
    """Темы из настроек; пусто — все. Без слов в словаре «вставь слово» не предлагается."""
    chosen = [LEGACY_GROUPS.get(g, g) for g in settings.get("ex_types") or []]
    chosen = [g for g in dict.fromkeys(chosen) if g in GROUPS]
    groups = chosen or list(GROUPS)
    return [g for g in groups if has_vocab or g != "vocab"] or ["tenses"]


# ---- освоение -----------------------------------------------------------------------------

NOT_STARTED = "not_started"
LEARNING = "learning"
CONFIDENT = "confident"

# «Уверенно»: не меньше CONFIDENT_ATTEMPTS попыток и доля верных среди последних RECENT — от CONFIDENT_SHARE.
CONFIDENT_ATTEMPTS = 4
RECENT = 5
CONFIDENT_SHARE = 0.8


def history(log):
    """{id правила: [вердикты по порядку]} из журнала упражнений."""
    result = {}
    for entry in log:
        result.setdefault(rule_id(entry["rule"]), []).append(entry["verdict"])
    return result


def status(verdicts):
    if not verdicts:
        return NOT_STARTED
    recent = verdicts[-RECENT:]
    share = sum(1 for v in recent if v == "ok") / len(recent)
    if len(verdicts) >= CONFIDENT_ATTEMPTS and share >= CONFIDENT_SHARE:
        return CONFIDENT
    return LEARNING


def statuses(log):
    past = history(log)
    return {rule.id: status(past.get(rule.id, [])) for rule in RULES}


def progress(log, up_to="B2"):
    """По каждому уровню: сколько правил освоено уверенно и что осталось."""
    by_rule = statuses(log)
    report = []
    for level in levels_up_to(up_to):
        rules = [r for r in RULES if r.level == level]
        confident = [r for r in rules if by_rule[r.id] == CONFIDENT]
        report.append({
            "level": level,
            "confident": len(confident),
            "total": len(rules),
            "learning": [r for r in rules if by_rule[r.id] == LEARNING],
            "not_started": [r for r in rules if by_rule[r.id] == NOT_STARTED],
        })
    return report


def gaps(log):
    """Правила с пробелом: ещё не освоены, и среди последних попыток есть ошибки."""
    return {
        rule: verdicts for rule, verdicts in history(log).items()
        if rule in BY_ID and status(verdicts) == LEARNING and any(v != "ok" for v in verdicts[-RECENT:])
    }


def working_level(log, chosen):
    """Первый уровень (не выше выбранного), где есть пробелы; нет пробелов — выбранный.
    Непроверенные правила пробелом не считаются: их проверят упражнения."""
    found = gaps(log)
    for level in levels_up_to(chosen):
        if any(BY_ID[rule].level == level for rule in found):
            return level
    return clamp_level(chosen)


def weak_rules(log, now, parse_time, top=3):
    """Правила, где часто ошибаешься: от двух попыток за 30 дней и от трети неверных;
    свежие ошибки весят больше (вес падает вдвое за неделю)."""
    since = now - timedelta(days=30)
    stats = {}
    for entry in log:
        at = parse_time(entry["at"])
        if at < since:
            continue
        weight = 0.5 ** ((now - at).days / 7)
        s = stats.setdefault(rule_id(entry["rule"]), {"errors": 0.0, "total": 0.0, "wrong": 0, "count": 0})
        s["total"] += weight
        s["count"] += 1
        if entry["verdict"] != "ok":
            s["errors"] += weight
            s["wrong"] += 1
    weak = [(r, s) for r, s in stats.items() if s["count"] >= 2 and s["errors"] / s["total"] >= 0.34]
    weak.sort(key=lambda item: (-item[1]["errors"], -item[1]["errors"] / item[1]["total"]))
    return [{"rule": r, "wrong": s["wrong"], "count": s["count"]} for r, s in weak[:top]]


def scope_levels(log, level):
    """Уровни, из которых брать правила: до выбранного включительно, а если всё до него освоено —
    ещё и следующий (но не выше B2)."""
    levels = list(levels_up_to(level))
    mastered = all(row["confident"] >= CONFIDENT_SHARE * row["total"] for row in progress(log, level))
    if mastered and levels[-1] != LEVELS[-1]:
        levels.append(LEVELS[len(levels)])
    return levels


def _round_robin(rules):
    """По одному правилу из каждой группы по кругу, порядок программы внутри группы сохраняется."""
    by_group = {}
    for rule in rules:
        by_group.setdefault(rule.group, []).append(rule)
    ordered = []
    while any(by_group.values()):
        for group in list(by_group):
            if by_group[group]:
                ordered.append(by_group[group].pop(0))
    return ordered


def _new_rules_order(rules, level):
    """Новые правила: в основном своего уровня (по порядку программы), каждое третье — проверка
    нижних уровней, чтобы найти пробелы, не заставляя проходить A1 целиком перед B1."""
    main = _round_robin([r for r in rules if r.level == level])
    lower = _round_robin([r for r in rules if LEVELS.index(r.level) < LEVELS.index(level)])
    higher = [r for r in rules if LEVELS.index(r.level) > LEVELS.index(level)]
    ordered = []
    while main or lower:
        ordered += main[:2]
        main = main[2:]
        ordered += lower[:1]
        lower = lower[1:]
    return ordered + higher


def plan_batch(log, now, parse_time, level, groups, count, include_vocab=False, rng=random):
    """Какие правила дать в пачке: ~50% слабые, ~30% новые по порядку программы, ~20% повтор освоенных."""
    by_rule = statuses(log)
    last_seen = {rule_id(entry["rule"]): i for i, entry in enumerate(log)}
    levels = scope_levels(log, level)
    scope = [r for r in RULES if r.level in levels and r.group in groups]
    if not scope and not include_vocab:
        scope = [r for r in RULES if r.level in levels]

    weak = [BY_ID[w["rule"]] for w in weak_rules(log, now, parse_time) if w["rule"] in BY_ID and BY_ID[w["rule"]] in scope]
    learning = [r for r in scope if by_rule[r.id] == LEARNING and r not in weak]
    new = _new_rules_order([r for r in scope if by_rule[r.id] == NOT_STARTED], clamp_level(level))
    review = sorted((r for r in scope if by_rule[r.id] == CONFIDENT), key=lambda r: last_seen.get(r.id, -1))

    slots = count - (1 if include_vocab else 0)
    plan = []

    def take(pool, n):
        for rule in pool:
            if len(plan) >= slots or n <= 0:
                return
            if rule not in plan:
                plan.append(rule)
                n -= 1

    take(weak, round(slots * 0.5))
    take(new, max(1, round(slots * 0.3)))
    take(review, max(1, round(slots * 0.2)) if review else 0)
    for pool in (learning, weak, new, review):  # добор, если чего-то не хватило
        take(pool, slots)
    while scope and len(plan) < slots:  # тем мало — то же правило в другом предложении
        plan.append(rng.choice(scope))
    rng.shuffle(plan)
    if include_vocab:
        plan.append(VOCAB)
    return plan
