"""Свои предложения со словом с карточки — вместо «Знаю»: текстом или голосовыми.

Предложения можно присылать по одному и сколько угодно: каждое сообщение ИИ разбирает сразу (верно /
слово верно, но есть ошибки / слово употреблено неверно) и предлагает, как сказать естественнее.
По «Готово» — итог: совет, как ещё употребляют слово, и частые конструкции с ним. Засчитано ли слово,
решает код: слово правильно употреблено хотя бы в MIN_GOOD предложениях — как «Знаю», иначе — «Не знаю».

Расход (сентябрь 2026): голосовое ~40 нейронов за минуту записи, разбор предложения ~6–8, итог ~10 —
из 10 000 бесплатных в день, поэтому число предложений не ограничено.
"""

import base64
import re

from . import ai, generator
from . import practice as pr
from .text import clean, has_cyrillic, has_foreign_script, strip_extras

# Сколько предложений разбирать из одного сообщения: ограничивает размер запроса, не число попыток.
MAX_PER_MESSAGE = 5
# Хватает одного верного употребления слова — как «Знаю».
MIN_GOOD = 1
MAX_VOICE_SECONDS = 60
# Бесплатная расшифровка речи в Workers AI; голосовые Telegram (OGG/Opus) принимает как есть.
WHISPER_MODEL = "@cf/openai/whisper-large-v3-turbo"

CORRECT = "correct"  # слово употреблено верно, ошибок нет
GRAMMAR = "grammar"  # слово употреблено верно, но есть другие ошибки
WRONG = "wrong"  # слова нет, не то значение или так не говорят
VERDICTS = (CORRECT, GRAMMAR, WRONG)

_NUMBERING = re.compile(r"^\s*(\d+[.)]|[-•*])\s+")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


def split_sentences(text):
    """Предложения из ответа: по строкам и по концам предложений; нумерация и маркеры списка убираются."""
    result = []
    for line in (text or "").splitlines():
        for part in _SENTENCE_END.split(_NUMBERING.sub("", line).strip()):
            part = clean(part)
            if len(part.split()) >= 2:  # одно слово — не предложение
                result.append(part)
    return result


def check_input(sentences):
    """Текст ошибки для пользователя или None, если предложения можно отдавать ИИ."""
    if any(has_cyrillic(s) for s in sentences):
        return "Предложения нужны на английском."
    if not sentences:
        return "Не нашёл предложения — нужно хотя бы пару слов со словом с карточки."
    return None


def build_request(word, sentences, level, spoken=False, model=ai.DEFAULT_MODEL):
    en, ru = strip_extras(word["en"]), strip_extras(word["ru"])
    system = (
        f"You are a kind English teacher for a native Russian speaker at CEFR level {level}. "
        f"The learner practises the word or phrase '{en}' (Russian: '{ru}') by writing their own sentences. "
        "Review every sentence, in the given order. 'sentence': copy it. 'verdict': "
        f"'{CORRECT}' — the target word (in any grammatical form) is used with the right meaning and the sentence "
        f"has no mistakes; '{GRAMMAR}' — the target word is used correctly but there are other mistakes; "
        f"'{WRONG}' — the target word is missing, has a different meaning or is used in a way people do not say. "
        "'corrected': how a native speaker would say it, with minimal changes and the same meaning "
        "(the same sentence if it is already correct). 'comment_ru': one short sentence in natural Russian about "
        "the main mistake, addressing the learner as «ты»; empty if there is none."
    )
    if spoken:
        system += (" The sentences were transcribed from the learner's speech: ignore punctuation, capital letters "
                   "and obvious transcription slips.")
    user = "\n".join(f"{n}. {s}" for n, s in enumerate(sentences, start=1))
    return ai.request(system, user, ai.CompositionReview, 250 + 180 * len(sentences), 0.3, model)


def parse(output, sentences):
    """[{'sentence', 'verdict', 'corrected', 'comment'}] по порядку предложений; None — ответ негоден."""
    review = ai.parse(ai.CompositionReview, output)
    if review is None or len(review.sentences) < len(sentences):
        return None
    items = []
    for sentence, raw in zip(sentences, review.sentences):
        verdict = clean(raw.verdict).lower()
        if verdict not in VERDICTS:
            return None
        corrected = clean(raw.corrected)
        if not corrected or has_cyrillic(corrected) or has_foreign_script(corrected):
            corrected = sentence
        comment = clean(raw.comment_ru)
        if comment and (not has_cyrillic(comment) or has_foreign_script(comment)):
            comment = ""
        items.append({"sentence": sentence, "verdict": verdict, "corrected": corrected, "comment": comment})
    return items


def changed(item):
    """Исправление отличается не только пунктуацией и регистром (у голосовых их расставляет Whisper)."""
    return pr.normalize(item["corrected"]) != pr.normalize(item["sentence"])


def good_count(items):
    return sum(1 for x in items if x["verdict"] in (CORRECT, GRAMMAR))


def passed(items):
    return good_count(items) >= MIN_GOOD


async def review(ai_client, word, sentences, level, spoken=False, model=ai.DEFAULT_MODEL):
    """Разбор с одной повторной попыткой, если модель ответила не по форме; None — не вышло."""
    request = build_request(word, sentences, level, spoken, model)
    for _ in range(2):
        result = parse(await ai_client.run(model, request), sentences)
        if result is not None:
            return result
    return None


# ---- итог после «Готово» -------------------------------------------------------------------


def build_usage_request(word, sentences, level, model=ai.DEFAULT_MODEL):
    en, ru = strip_extras(word["en"]), strip_extras(word["ru"])
    system = (
        f"You are a kind English teacher for a native Russian speaker at CEFR level {level}. The learner has just "
        f"made their own sentences with the word or phrase '{en}' (Russian: '{ru}'). Show what else is worth "
        "knowing about it. 'tip_ru': 1-2 short sentences in natural Russian, addressing the learner as «ты»: other "
        "common meanings or uses of the word and the typical constructions and collocations with it that the "
        "learner did not use (write English words and phrases as they are, without quotes). 'past', 'present', "
        "'future': one short, natural sentence each that people really say with the word — in a past tense, "
        "a present tense and a future form (will, be going to…) respectively, preferably showing those "
        "constructions. Each must sound like something a native speaker would really say: if a form is awkward "
        "with this word, choose a natural context for it rather than forcing it. 'en' is the sentence, "
        "'ru' its natural Russian translation."
    )
    user = "The learner's sentences:\n" + "\n".join(f"- {s}" for s in sentences)
    return ai.request(system, user, ai.WordUsage, 500, 0.6, model)


PAST, PRESENT, FUTURE = "past", "present", "future"
TENSES = (PAST, PRESENT, FUTURE)


def parse_usage(output):
    """(совет, [{'tense', 'en', 'ru'}] в порядке прошлое → настоящее → будущее) или None,
    если ответ не по форме или в нём нет ничего годного."""
    usage = ai.parse(ai.WordUsage, output)
    if usage is None:
        return None
    tip = clean(usage.tip_ru)
    if not has_cyrillic(tip) or has_foreign_script(tip):
        tip = ""
    examples = []
    for tense in TENSES:
        pair = generator.clean_pairs([getattr(usage, tense)], limit=1)
        if pair:
            examples.append({"tense": tense, **pair[0]})
    return (tip, examples) if tip or examples else None


async def usage(ai_client, word, sentences, level, model=ai.DEFAULT_MODEL):
    """Совет с одной повторной попыткой, если модель ответила не по форме; None — не вышло."""
    request = build_usage_request(word, sentences, level, model)
    for _ in range(2):
        result = parse_usage(await ai_client.run(model, request))
        if result is not None:
            return result
    return None


# ---- голосовые ------------------------------------------------------------------------------


def transcribe_request(audio):
    # Без initial_prompt: на пробе подсказка «Sentences with the word …» дала галлюцинацию
    # «Thank you very much.», а подсказка из одного слова обрезала начало записи.
    return {"audio": base64.b64encode(audio).decode("ascii"), "language": "en", "vad_filter": True}


def parse_transcript(output):
    text = output.get("text") if isinstance(output, dict) else None
    return clean(text) if isinstance(text, str) else ""


async def transcribe(ai_client, audio):
    return parse_transcript(await ai_client.run(WHISPER_MODEL, transcribe_request(audio)))
