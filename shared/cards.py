"""Telegram message texts (HTML parse_mode) and keyboards."""

import random
from html import escape

from .words import current_example

KNOWN_BUTTON = "Знаю"
UNKNOWN_BUTTON = "Не знаю"
REVIEW_BUTTON = "Повторить слова"
NEXT_BUTTON = "Карточка сейчас"
MENU_BUTTON = "Меню"

NEW_WORD_CAPTION = "I learned a new word! / Я выучил новое слово!"

ARCHIVE_CHEERS = [
    "🎉 «{en}» — в архиве! Так держать!",
    "🏆 Ещё одно слово покорено: «{en}»",
    "🚀 «{en}» выучено в обе стороны. Отправляю в архив!",
    "🧠 +1 в копилку: «{en}» теперь твоё",
    "🌟 Бум! «{en}» больше не секрет",
]

HELP_TEXT = (
    "Пришли слово одним сообщением:\n"
    "<code>apple - яблоко\n"
    "I ate an apple. - Я съел яблоко.</code>\n\n"
    "Первая строка — слово и перевод, остальные (необязательно) — примеры. "
    "Разделитель — « - ».\n\n"
    "Карточки приходят по расписанию. Кнопки:\n"
    f"• «{REVIEW_BUTTON}» — список выученных слов, можно вернуть забытые в очередь\n"
    f"• «{NEXT_BUTTON}» — получить карточку прямо сейчас\n\n"
    "Команды: /next, /review, /stats, /allow @username (только владелец)"
)


def _capitalize(text):
    return text[:1].upper() + text[1:]


def _spoiler(text):
    return f"<tg-spoiler>{escape(text)}</tg-spoiler>"


def render_card(word):
    """Stage 0: Russian visible, English hidden. Stage 1: the other way round."""
    example = current_example(word)
    if word.get("stage", 0) == 0:
        lines = [f"{escape(_capitalize(word['ru']))} - {_spoiler(word['en'])}"]
        if example:
            lines += ["", _spoiler(example["en"]), escape(example["ru"])]
    else:
        lines = [f"{escape(_capitalize(word['en']))} - {_spoiler(word['ru'])}"]
        if example:
            lines += ["", escape(example["en"]), _spoiler(example["ru"])]
    if word.get("shown_count", 0) == 0:
        lines += ["", f"<i>{escape(NEW_WORD_CAPTION)}</i>"]
    return "\n".join(lines)


def card_keyboard(word):
    return {
        "inline_keyboard": [
            [
                {"text": KNOWN_BUTTON, "callback_data": f"k:{word['id']}"},
                {"text": UNKNOWN_BUTTON, "callback_data": f"n:{word['id']}"},
            ]
        ]
    }


def main_keyboard():
    return {
        "keyboard": [[{"text": REVIEW_BUTTON}, {"text": NEXT_BUTTON}]],
        "resize_keyboard": True,
        "is_persistent": True,
    }


def menu_inline_keyboard():
    return {"inline_keyboard": [[{"text": MENU_BUTTON, "callback_data": "menu"}]]}


def archive_cheer(word, rng=random):
    return rng.choice(ARCHIVE_CHEERS).format(en=escape(word["en"]))


def render_review_lines(words):
    return [f"{i}) {escape(w['en'])} — {escape(w['ru'])}" for i, w in enumerate(words, start=1)]


def chunk_lines(lines, header="", limit=4000):
    """Split lines into messages below Telegram's 4096-char limit."""
    chunks, current = [], header
    for line in lines:
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit and current:
            chunks.append(current)
            current = line
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def render_stats(stats, queue_size):
    avg = "—" if stats["avg_days"] is None else f"{stats['avg_days']:.1f} дн."
    return (
        "📊 <b>Статистика за неделю</b>\n"
        f"Выучено слов: <b>{stats['archived']}</b>\n"
        f"Средний срок от добавления до архива: <b>{avg}</b>\n\n"
        f"Всего в архиве: {stats['total_known']}\n"
        f"В очереди на изучение: {queue_size}"
    )
