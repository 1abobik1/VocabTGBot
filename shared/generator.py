"""AI-generated flashcards (Cloudflare Workers AI).

`ai` must provide `async run(model, input: dict) -> dict` returning the model output
(for chat models: {"response": str | dict, ...}).
"""

import json
import random
import re

from . import words as w

DEFAULT_MODEL = "@cf/meta/llama-4-scout-17b-16e-instruct"
LEVELS = ("A1", "A2", "B1", "B2", "C1")
DEFAULT_LEVEL = "B1"
MAX_CARDS = 10
# Keeps the prompt small: only the most recent words are listed as "already known".
AVOID_LIMIT = 150

# Used when no topic is given, so batches don't keep returning the same phrasal verbs.
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
    "expressions; C1 idiomatic, nuanced expressions natives use in conversation (e.g. 'take for granted', "
    "'on the fence')."
)

SYSTEM_PROMPT = (
    "You create English vocabulary flashcards for a native Russian speaker. "
    "Pick words or short phrases of CEFR level {level} that are frequent in everyday spoken English: "
    "conversational, not bookish or formal. " + LEVEL_GUIDE + " "
    "The words must not be easier than {level}: skip words a lower-level learner already knows. "
    "Topic: {topic}. "
    "For each card give: the English word or phrase (no leading 'to' for verbs), a short natural Russian "
    "translation with the same part of speech, exactly 2 short example sentences that people really say in "
    "conversation and that contain the word, each with a Russian translation of the meaning (not word for "
    "word), and English synonyms with Russian translations. "
    "Synonyms must be real synonyms: same meaning and part of speech, able to replace the word in the example "
    "sentence. Give 1-2 of them; if there is no good synonym, give an empty list. "
    "Russian text must be grammatical, sound natural to a native speaker and contain no English words. "
    "Every card must be a different word. Do not use any of these words: {avoid}. "
    'Answer with JSON only: {{"cards":[{{"en":"","ru":"","examples":[{{"en":"","ru":""}}],'
    '"synonyms":[{{"en":"","ru":""}}]}}]}}'
)

ENRICH_PROMPT = (
    "You help a native Russian speaker learn English. For the given English word or phrase and its "
    "Russian translation write exactly 2 short example sentences that people really say in conversation "
    "and that contain the word, each with a Russian translation of the meaning (not word for word), "
    "and English synonyms with Russian translations. "
    "Synonyms must be real synonyms: same meaning and part of speech, able to replace the word in the "
    "example sentence. Give 1-2 of them; if there is no good synonym, give an empty list. "
    "Russian text must be grammatical, sound natural to a native speaker and contain no English words. "
    'Answer with JSON only: {{"examples":[{{"en":"","ru":""}}],"synonyms":[{{"en":"","ru":""}}]}}'
)

_PAIR_SCHEMA = {
    "type": "object",
    "properties": {"en": {"type": "string"}, "ru": {"type": "string"}},
    "required": ["en", "ru"],
}
RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "cards": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "en": {"type": "string"},
                    "ru": {"type": "string"},
                    "examples": {"type": "array", "items": _PAIR_SCHEMA},
                    "synonyms": {"type": "array", "items": _PAIR_SCHEMA},
                },
                "required": ["en", "ru", "examples", "synonyms"],
            },
        }
    },
    "required": ["cards"],
}

ENRICH_SCHEMA = {
    "type": "object",
    "properties": {
        "examples": {"type": "array", "items": _PAIR_SCHEMA},
        "synonyms": {"type": "array", "items": _PAIR_SCHEMA},
    },
    "required": ["examples", "synonyms"],
}

_CYRILLIC = re.compile("[а-яё]", re.IGNORECASE)
# English leaking into the Russian side ("бывший boyfriend", "crashedнуло"). Short abbreviations
# ("IT") are fine, and so are names and terms copied from the English side ("Python", "API").
_LATIN_WORD = re.compile("[A-Za-z]{3,}")
_LATIN_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9+#.]*")
# Anything outside Latin, Cyrillic, digits, whitespace and common punctuation (e.g. a stray "细节").
_FOREIGN = re.compile(r"[^\sA-Za-zА-Яа-яЁё0-9.,!?;:'\"()\-–—’‘“”«»…/&%$€£+]")


def normalize_level(value):
    value = (value or "").strip().upper()
    return value if value in LEVELS else None


def build_input(level, count, topic=None, avoid=(), rng=random):
    topic = (topic or "").strip() or rng.choice(RANDOM_TOPICS)
    avoid = [a for a in avoid if a][-AVOID_LIMIT:]
    system = SYSTEM_PROMPT.format(level=level, topic=topic, avoid=", ".join(avoid) or "none")
    return {
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": f"Generate {count} cards."},
        ],
        "max_tokens": 250 + 200 * count,
        "temperature": 0.8,
        "response_format": {"type": "json_schema", "json_schema": RESPONSE_SCHEMA},
    }


def _response_payload(output):
    """Extract the JSON object from a Workers AI response (dict, JSON string or chat-completion)."""
    data = output.get("response") if isinstance(output, dict) else output
    if data is None and isinstance(output, dict) and output.get("choices"):
        data = output["choices"][0]["message"]["content"]
    if isinstance(data, str):
        start, end = data.find("{"), data.rfind("}")
        if start < 0 or end < start:
            return {}
        try:
            data = json.loads(data[start : end + 1])
        except ValueError:
            return {}
    return data if isinstance(data, dict) else {}


def _clean(text):
    return " ".join(str(text or "").split())


def _has_english_leak(ru, en):
    """Latin words in Russian text, except capitalised names/terms that also appear in the English text."""
    en_tokens = set(_LATIN_TOKEN.findall(en))
    for match in _LATIN_TOKEN.finditer(ru):
        token = match.group()
        glued = match.start() > 0 and _CYRILLIC.match(ru[match.start() - 1]) or (
            match.end() < len(ru) and _CYRILLIC.match(ru[match.end()])
        )
        if glued:
            return True  # "crashedнуло"
        if _LATIN_WORD.search(token) and not (token[0].isupper() and token in en_tokens):
            return True
    return False


def _clean_pair(item, main=False):
    """A valid {"en", "ru"} pair or None. `main` (the card's word itself) allows no Latin in Russian."""
    if not isinstance(item, dict):
        return None
    en, ru = _clean(item.get("en")), _clean(item.get("ru"))
    if not en or not ru or _CYRILLIC.search(en) or not _CYRILLIC.search(ru):
        return None
    if (_LATIN_WORD.search(ru) if main else _has_english_leak(ru, en)):
        return None
    if _FOREIGN.search(en) or _FOREIGN.search(ru):
        return None
    return {"en": en, "ru": ru}


def parse_cards(output, existing=(), limit=MAX_CARDS):
    """Validated cards from a model response; drops malformed ones and duplicates of `existing` words."""
    seen = {w.compact(x) for x in existing}
    cards = []
    for raw in _response_payload(output).get("cards") or []:
        pair = _clean_pair(raw, main=True)
        if pair is None:
            print(f"generator: dropped malformed card {raw!r}")
            continue
        en = re.sub(r"^to\s+", "", pair["en"], flags=re.IGNORECASE)
        if w.compact(en) in seen:
            print(f"generator: dropped duplicate {en!r}")
            continue
        examples = [p for p in map(_clean_pair, raw.get("examples") or []) if p][:2]
        if not examples:
            print(f"generator: dropped card without valid examples {raw!r}")
            continue  # a card without an example is not worth keeping
        synonyms = [p for p in map(_clean_pair, raw.get("synonyms") or []) if p][:2]
        seen.add(w.compact(en))
        cards.append(w.new_word(en, pair["ru"], examples, synonyms))
        if len(cards) >= limit:
            break
    return cards


# Requests per generation: the first one plus top-ups for cards the validation dropped.
MAX_ATTEMPTS = 3


async def generate(ai, level, count, topic=None, existing=(), model=DEFAULT_MODEL):
    """Ask the model for `count` cards; if some are dropped, ask again for the missing ones."""
    count = max(1, min(int(count), MAX_CARDS))
    cards = []
    for _ in range(MAX_ATTEMPTS):
        missing = count - len(cards)
        if missing <= 0:
            break
        known = list(existing) + [c["en"] for c in cards]
        output = await ai.run(model, build_input(level, missing, topic, known))
        cards += parse_cards(output, known, limit=missing)
    return cards


def build_enrich_input(en, ru):
    return {
        "messages": [
            {"role": "system", "content": ENRICH_PROMPT},
            {"role": "user", "content": f"Word: {en}\nRussian translation: {ru}"},
        ],
        "max_tokens": 500,
        "temperature": 0.7,
        "response_format": {"type": "json_schema", "json_schema": ENRICH_SCHEMA},
    }


def parse_enrichment(output):
    """(examples, synonyms) from a model response; invalid pairs are dropped."""
    payload = _response_payload(output)
    examples = [p for p in map(_clean_pair, payload.get("examples") or []) if p][:2]
    synonyms = [p for p in map(_clean_pair, payload.get("synonyms") or []) if p][:2]
    return examples, synonyms


async def enrich(ai, en, ru, model=DEFAULT_MODEL):
    """Examples and synonyms for a word the user added by hand. Empty lists if nothing usable."""
    for _ in range(2):
        examples, synonyms = parse_enrichment(await ai.run(model, build_enrich_input(en, ru)))
        if examples:
            return examples, synonyms
    return [], []
