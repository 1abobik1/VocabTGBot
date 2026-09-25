"""Карточки от ИИ: новые слова по уровню и теме, примеры и синонимы к словам, добавленным вручную.

`ai_client` умеет `async run(model, input) -> dict` (см. shared.ai). Примеры предложений строятся
на уровне пользователя и, где это естественно, на грамматике, которую он сейчас подтягивает.
"""

import random
import re

from . import ai
from . import words as w
from .curriculum import (  # noqa: F401 — уровни импортируют отсюда
    DEFAULT_LEVEL,
    LEVELS,
    normalize_level,
)
from .text import CYRILLIC, clean, has_cyrillic, has_foreign_script

DEFAULT_MODEL = ai.DEFAULT_MODEL
model_options = ai.model_options
MAX_CARDS = 10
# Ограничивает промпт: в списке «уже есть» только самые свежие слова.
AVOID_LIMIT = 150
# Запросов на одну генерацию: первый плюс добор отбракованных карточек.
MAX_ATTEMPTS = 3

# Когда тема не задана — случайная, иначе модель из раза в раз предлагает одни и те же слова.
RANDOM_TOPICS = [
    "daily routine", "food and cooking", "shopping", "travel and holidays", "work and colleagues",
    "friends and small talk", "feelings and mood", "health and the body", "home and chores",
    "money", "weather", "plans and making arrangements", "phone calls and messaging", "free time and hobbies",
    "transport and getting around", "restaurants and cafes", "relationships", "complaints and problems",
    "opinions and agreeing/disagreeing", "studying and learning", "the internet and social media",
    "movies, music and TV", "sports and exercise", "sleep and rest", "city life",
]

LEVEL_GUIDE = (
    "Level guide: A1 very basic everyday words (family, food, time); A2 simple everyday words and set phrases; "
    "B1 common phrasal verbs and everyday expressions; B2 less obvious collocations, phrasal verbs and "
    "natural expressions (e.g. 'take for granted', 'on the fence')."
)

EXAMPLES_RULES = (
    "exactly 2 short example sentences that people really say in conversation and that contain the word, "
    "each with a Russian translation of the meaning (not word for word), and English synonyms with Russian "
    "translations. Synonyms must be real synonyms: same meaning and part of speech, able to replace the word "
    "in the example sentence. Give 1-2 of them; if there is no good synonym, give an empty list. "
    "Russian text must be grammatical, sound natural to a native speaker and contain no English words."
)


def _grammar_hint(level, grammar):
    """Примеры на уровне пользователя и на грамматике, которую он сейчас подтягивает."""
    hint = f" Write the example sentences at CEFR level {level}."
    if grammar:
        hint += (" Where it is natural, build them with grammar the learner is practising: "
                 + "; ".join(grammar) + ".")
    return hint


def build_input(level, count, topic=None, avoid=(), grammar=(), rng=random, model=DEFAULT_MODEL):
    topic = (topic or "").strip() or rng.choice(RANDOM_TOPICS)
    avoid = [a for a in avoid if a][-AVOID_LIMIT:]
    system = (
        "You create English vocabulary flashcards for a native Russian speaker. "
        f"Pick words or short phrases of CEFR level {level} that are frequent in everyday spoken English: "
        f"conversational, not bookish or formal. {LEVEL_GUIDE} "
        f"The words must not be easier than {level}: skip words a lower-level learner already knows. "
        f"Topic: {topic}. "
        "For each card give: the English word or phrase (no leading 'to' for verbs), a short natural Russian "
        f"translation with the same part of speech, {EXAMPLES_RULES}"
        f"{_grammar_hint(level, grammar)} "
        f"Every card must be a different word. Do not use any of these words: {', '.join(avoid) or 'none'}. "
    )
    return ai.request(system, f"Generate {count} cards.", ai.CardBatch, 250 + 200 * count, 0.8, model)


def build_enrich_input(en, ru, level=None, grammar=(), model=DEFAULT_MODEL):
    system = (
        "You help a native Russian speaker learn English. For the given English word or phrase and its "
        f"Russian translation write {EXAMPLES_RULES}"
        f"{_grammar_hint(level, grammar) if level else ''} "
    )
    return ai.request(system, f"Word: {en}\nRussian translation: {ru}", ai.Enrichment, 500, 0.7, model)


# ---- проверка ответа модели ---------------------------------------------------------------

# Английский в русском тексте ("бывший boyfriend", "crashedнуло"). Короткие сокращения ("IT") можно,
# и имена/термины, которые есть в английском тексте ("Python", "API"), тоже.
_LATIN_WORD = re.compile("[A-Za-z]{3,}")
_LATIN_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9+#.]*")


def _has_english_leak(ru, en):
    en_tokens = set(_LATIN_TOKEN.findall(en))
    for match in _LATIN_TOKEN.finditer(ru):
        token = match.group()
        glued = (match.start() > 0 and CYRILLIC.match(ru[match.start() - 1])) or (
            match.end() < len(ru) and CYRILLIC.match(ru[match.end()])
        )
        if glued:
            return True  # "crashedнуло"
        if _LATIN_WORD.search(token) and not (token[0].isupper() and token in en_tokens):
            return True
    return False


def _clean_pair(pair, main=False):
    """Годная пара {"en", "ru"} или None. `main` — само слово карточки: латиница в переводе запрещена."""
    en, ru = clean(pair.en), clean(pair.ru)
    if not en or not ru or has_cyrillic(en) or not has_cyrillic(ru):
        return None
    if _LATIN_WORD.search(ru) if main else _has_english_leak(ru, en):
        return None
    if has_foreign_script(en) or has_foreign_script(ru):
        return None
    return {"en": en, "ru": ru}


def clean_pairs(pairs, limit=2):
    return [p for p in map(_clean_pair, pairs) if p][:limit]


def parse_cards(output, existing=(), limit=MAX_CARDS):
    """Проверенные карточки из ответа модели; бракованные и уже известные слова отбрасываются."""
    batch = ai.parse(ai.CardBatch, output)
    seen = {w.compact(x) for x in existing}
    cards = []
    for raw in batch.cards if batch else []:
        pair = _clean_pair(raw, main=True)
        if pair is None:
            print(f"generator: dropped malformed card {raw}"[:240])
            continue
        en = re.sub(r"^to\s+", "", pair["en"], flags=re.IGNORECASE)
        if w.compact(en) in seen:
            print(f"generator: dropped duplicate {en!r}")
            continue
        examples = clean_pairs(raw.examples)
        if not examples:
            print(f"generator: dropped card without valid examples {raw}"[:240])
            continue  # карточка без примера не стоит того, чтобы её учить
        seen.add(w.compact(en))
        cards.append(w.new_word(en, pair["ru"], examples, clean_pairs(raw.synonyms)))
        if len(cards) >= limit:
            break
    return cards


def parse_enrichment(output):
    """(examples, synonyms) из ответа модели; негодные пары отбрасываются."""
    enrichment = ai.parse(ai.Enrichment, output)
    if enrichment is None:
        return [], []
    return clean_pairs(enrichment.examples), clean_pairs(enrichment.synonyms)


async def generate(ai_client, level, count, topic=None, existing=(), model=DEFAULT_MODEL, grammar=()):
    """`count` карточек; отбракованные запрашиваются ещё раз, до MAX_ATTEMPTS запросов."""
    count = max(1, min(int(count), MAX_CARDS))
    cards = []
    for _ in range(MAX_ATTEMPTS):
        missing = count - len(cards)
        if missing <= 0:
            break
        known = list(existing) + [c["en"] for c in cards]
        output = await ai_client.run(model, build_input(level, missing, topic, known, grammar, model=model))
        cards += parse_cards(output, known, limit=missing)
    return cards


async def enrich(ai_client, en, ru, model=DEFAULT_MODEL, level=None, grammar=()):
    """Примеры и синонимы к слову, добавленному вручную. Пустые списки, если ничего годного."""
    for _ in range(2):
        examples, synonyms = parse_enrichment(await ai_client.run(model, build_enrich_input(en, ru, level, grammar, model)))
        if examples:
            return examples, synonyms
    return [], []
