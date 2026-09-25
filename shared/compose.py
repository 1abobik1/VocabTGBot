"""Свои предложения со словом с карточки — вместо «Знаю»: текстом или голосовым.

ИИ разбирает каждое предложение (верно / слово верно, но есть ошибки / слово употреблено неверно)
и предлагает, как сказать естественнее. Засчитано ли слово, решает код: слово правильно употреблено
хотя бы в MIN_GOOD предложениях — как «Знаю», иначе — как «Не знаю».
"""

import base64
import re

from . import ai
from . import practice as pr
from .text import clean, has_cyrillic, has_foreign_script, strip_extras

MIN_SENTENCES = 2
MAX_SENTENCES = 4
MIN_GOOD = 2
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
    if len(sentences) < MIN_SENTENCES:
        return f"Нужно хотя бы {MIN_SENTENCES} предложения со словом — пришли их одним сообщением."
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
        "the main mistake, addressing the learner as «ты»; empty if there is none. 'tip_ru': one short tip in Russian on how else the word is "
        "used, with a short English example."
    )
    if spoken:
        system += (" The sentences were transcribed from the learner's speech: ignore punctuation, capital letters "
                   "and obvious transcription slips.")
    user = "\n".join(f"{n}. {s}" for n, s in enumerate(sentences, start=1))
    return ai.request(system, user, ai.CompositionReview, 250 + 180 * len(sentences), 0.3, model)


def parse(output, sentences):
    """[{'sentence', 'verdict', 'corrected', 'comment'}] по порядку предложений и совет; None — ответ негоден."""
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
    tip = clean(review.tip_ru)
    if not has_cyrillic(tip) or has_foreign_script(tip):
        tip = ""
    return items, tip


def changed(item):
    """Исправление отличается не только пунктуацией и регистром (у голосовых их расставляет Whisper)."""
    return pr.normalize(item["corrected"]) != pr.normalize(item["sentence"])


def passed(items):
    return sum(1 for x in items if x["verdict"] in (CORRECT, GRAMMAR)) >= MIN_GOOD


async def review(ai_client, word, sentences, level, spoken=False, model=ai.DEFAULT_MODEL):
    """Разбор с одной повторной попыткой, если модель ответила не по форме; None — не вышло."""
    for _ in range(2):
        result = parse(await ai_client.run(model, build_request(word, sentences, level, spoken, model)), sentences)
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
