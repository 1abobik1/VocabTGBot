"""Работа с Workers AI: контракты ответов модели и их разбор.

Форма ответа описана один раз — dataclass'ом. Из него выводятся образец JSON для промпта
и разбор ответа обратно в объект. Лишние поля модели игнорируются, пропущенные получают значения
по умолчанию, элементы списков неверного вида отбрасываются — дальше их проверяют модули фич.

`response_format: json_schema` не используется: Workers AI выдаёт поля в алфавитном порядке, и
модель пишет ответ раньше предложения — на пробе упражнений это дало 16 годных из 20 и три неверных
ответа против 20 из 20 без схемы. Порядок полей в образце — порядок, в котором модели удобно думать.
"""

import json
import typing
from dataclasses import MISSING, dataclass, field, fields, is_dataclass

# Выбрана сравнением бесплатных моделей Workers AI (сентябрь 2026): лучшие переводы и
# упражнения, ~3 нейрона на карточку. DeepSeek V4, Kimi K2.6, GLM 5.3 — только на платном плане.
DEFAULT_MODEL = "@cf/google/gemma-4-26b-a4b-it"


def model_options(model):
    """Параметры под конкретную модель. Gemma 4 по умолчанию долго «думает» (~100 с и в 8 раз
    больше токенов); без этого она отвечает за секунды и не хуже по качеству."""
    if "gemma-4" in (model or ""):
        return {"chat_template_kwargs": {"enable_thinking": False}}
    return {}


# ---- контракты ответов модели ------------------------------------------------------


@dataclass
class Pair:
    en: str
    ru: str


@dataclass
class GeneratedCard:
    en: str
    ru: str
    examples: list[Pair]
    synonyms: list[Pair]


@dataclass
class CardBatch:
    cards: list[GeneratedCard]


@dataclass
class Enrichment:
    examples: list[Pair]
    synonyms: list[Pair]


@dataclass(kw_only=True)
class GeneratedExercise:
    # Сначала правило и предложение, потом варианты и ответ: так модель реже ошибается в ответе.
    rule: str
    sentence: str
    options: list[str] = field(default_factory=list)
    answer: str
    accepted: list[str] = field(default_factory=list)
    word: str = ""
    explanation_ru: str
    translation_ru: str = ""


@dataclass
class ExerciseBatch:
    exercises: list[GeneratedExercise]


@dataclass(kw_only=True)
class SentenceReview:
    # Сначала вердикт и исправление, потом объяснение: объяснение опирается на исправление.
    sentence: str
    verdict: str  # correct | grammar | wrong (см. shared.compose)
    corrected: str = ""
    comment_ru: str = ""


@dataclass
class CompositionReview:
    sentences: list[SentenceReview]
    # Частая фраза со словом, чтобы запомнить, — только в первом разборе.
    example_en: str = ""
    example_ru: str = ""


# ---- образец ответа из dataclass ---------------------------------------------------


def _field_type(cls, name):
    return typing.get_type_hints(cls)[name]


def _type_example(tp):
    if tp is str:
        return ""
    if is_dataclass(tp):
        return example(tp)
    return [_type_example(typing.get_args(tp)[0])]


def example(cls):
    """Образец ответа для текста промпта: {"cards":[{"en":"","ru":"",...}]}."""
    return {f.name: _type_example(_field_type(cls, f.name)) for f in fields(cls)}


def example_json(cls):
    return json.dumps(example(cls), ensure_ascii=False, separators=(",", ":"))


# ---- разбор ответа ---------------------------------------------------------------------


def _parse_json_text(text):
    """JSON из ответа модели: с обёрткой ```json или без, объектом или списком."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0]
    try:
        return json.loads(text)
    except ValueError:
        pass
    # Лишний текст вокруг: берём самый внешний объект или список.
    for opening, closing in (("{", "}"), ("[", "]")):
        start, end = text.find(opening), text.rfind(closing)
        if 0 <= start < end:
            try:
                return json.loads(text[start : end + 1])
            except ValueError:
                continue
    return None


def raw_payload(output):
    """Содержимое ответа Workers AI: уже разобранный JSON, строка или chat-completion."""
    data = output.get("response") if isinstance(output, dict) else output
    if data is None and isinstance(output, dict) and output.get("choices"):
        data = output["choices"][0]["message"].get("content")
    if isinstance(data, str):
        data = _parse_json_text(data)
    return data


def text_payload(output):
    """Ответ модели как обычный текст (для свободных ответов, например недельного разбора)."""
    data = output.get("response") if isinstance(output, dict) else output
    if data is None and isinstance(output, dict) and output.get("choices"):
        data = output["choices"][0]["message"].get("content")
    return " ".join(str(data or "").split())


def _coerce(tp, value):
    """Значение из JSON в тип поля; None — если не подходит."""
    if tp is str:
        return value if isinstance(value, str) else (str(value) if isinstance(value, (int, float)) else None)
    if is_dataclass(tp):
        return from_json(tp, value)
    if typing.get_origin(tp) is list:
        if not isinstance(value, list):
            return None
        item_type = typing.get_args(tp)[0]
        return [x for x in (_coerce(item_type, v) for v in value) if x is not None]
    return None


def from_json(cls, data):
    """dict из JSON -> объект `cls`. None, если нет обязательного поля или оно не того вида."""
    if not isinstance(data, dict):
        return None
    values = {}
    for f in fields(cls):
        tp = _field_type(cls, f.name)
        if f.name not in data or data[f.name] is None:
            if f.default is MISSING and f.default_factory is MISSING:
                # Обязательный список, которого нет, — пустой; обязательная строка — ошибка.
                if typing.get_origin(tp) is list:
                    values[f.name] = []
                    continue
                return None
            continue
        value = _coerce(tp, data[f.name])
        if value is None:
            if f.default is MISSING and f.default_factory is MISSING:
                return None
            continue
        values[f.name] = value
    return cls(**values)


def parse(cls, output):
    """Ответ модели -> объект `cls`. Голый список (модель забыла обёртку) кладётся в
    единственное списочное поле: [...] -> CardBatch(cards=[...])."""
    data = raw_payload(output)
    if isinstance(data, list):
        list_fields = [f.name for f in fields(cls) if typing.get_origin(_field_type(cls, f.name)) is list]
        if len(list_fields) == 1:
            data = {list_fields[0]: data}
    return from_json(cls, data) if isinstance(data, dict) else None


def request(system, user, contract, max_tokens, temperature, model):
    """Входные данные для ai.run(): сообщения с образцом ответа и параметры модели."""
    system = system.rstrip() + f" Answer with JSON only, in this shape: {example_json(contract)}"
    return {
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "max_tokens": max_tokens,
        "temperature": temperature,
        **model_options(model),
    }
