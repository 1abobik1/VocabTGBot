"""Telegram message texts (HTML parse_mode) and keyboards."""

import random
from html import escape as _html_escape

from .words import current_example

KNOWN_BUTTON = "Знаю"
UNKNOWN_BUTTON = "Не знаю"
REVIEW_BUTTON = "Повторить слова"
NEXT_BUTTON = "Карточка сейчас"
GENERATE_BUTTON = "🤖 Сгенерировать"
MENU_BUTTON = "Меню"

HELP_TEXT = (
    "Пришли слово одним сообщением:\n"
    "<code>apple - яблоко\n"
    "I ate an apple. - Я съел яблоко.</code>\n\n"
    "Первая строка — слово и перевод, остальные (необязательно) — примеры. "
    "Синонимы — строкой <code>Синонимы: fairly - довольно; rather - скорее</code>. "
    "Разделитель — « - » или « — » с пробелами (дефис внутри слова, как в «куда-то», можно). "
    "Порядок языков любой: «куда-то - somewhere» тоже сработает.\n\n"
    "Карточки приходят по расписанию. Кнопки:\n"
    f"• «{REVIEW_BUTTON}» — список выученных слов, можно вернуть забытые в очередь\n"
    f"• «{NEXT_BUTTON}» — получить карточку прямо сейчас\n"
    f"• «{GENERATE_BUTTON}» — новые карточки от ИИ\n\n"
    "<b>Генерация:</b> <code>/gen 5</code> — 5 карточек твоего уровня, "
    "<code>/gen 3 B2 путешествия</code> — уровень и тема своими словами. "
    "Уровень по умолчанию — /level. Карточки приходят по одной на проверку: "
    "«В очередь», «Исправить» или «Удалить». Если очередь пуста, за 20 минут до слота "
    "ИИ сам предложит новую карточку.\n\n"
    "<b>Практика:</b> слово, на которое ответили «Знаю» в обе стороны, ждёт субботней практики "
    "(9:00): его надо написать RU→EN и EN→RU. Верно — в архив, опечатка — повтор, ошибка — "
    "учим заново. Пока практика не пройдена, новые карточки не приходят. /practice — начать сейчас.\n\n"
    "Команды: /next, /review, /stats, /gen, /level, /practice, /cancel, /allow @username (только владелец)"
)


def escape(text):
    # Quotes need no escaping outside attributes; keeps "He's" readable in the payload.
    return _html_escape(text, quote=False)


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
    synonyms = render_synonyms(word)
    if synonyms:
        lines += ["", synonyms]
    return "\n".join(lines)


def render_synonyms(word):
    """Synonyms go under the card, not hidden."""
    items = [
        f"{escape(s['en'])} — {escape(s['ru'])}" if s.get("ru") else escape(s["en"])
        for s in word.get("synonyms") or []
    ]
    return f"<i>Синонимы:</i> {'; '.join(items)}" if items else ""


def render_inbox_card(word, remaining, level=None):
    """A generated card shown fully open, to memorise it before it goes to the queue."""
    header = "🆕 Новая карточка" + (f" · {level}" if level else "")
    if remaining > 1:
        header += f" · на проверке ещё {remaining - 1}"
    lines = [header, "", f"<b>{escape(_capitalize(word['en']))}</b> — {escape(word['ru'])}"]
    for example in word.get("examples") or []:
        lines += ["", escape(example["en"]), f"<i>{escape(example['ru'])}</i>"]
    synonyms = render_synonyms(word)
    if synonyms:
        lines += ["", synonyms]
    return "\n".join(lines)


def inbox_keyboard(word):
    word_id = word["id"]
    return {
        "inline_keyboard": [
            [
                {"text": "✅ В очередь", "callback_data": f"ia:{word_id}"},
                {"text": "✏️ Исправить", "callback_data": f"ie:{word_id}"},
                {"text": "🗑 Удалить", "callback_data": f"ix:{word_id}"},
            ]
        ]
    }


def level_keyboard(current):
    return {
        "inline_keyboard": [
            [
                {"text": f"• {level} •" if level == current else level, "callback_data": f"lv:{level}"}
                for level in ("A1", "A2", "B1", "B2", "C1")
            ]
        ]
    }


def generate_keyboard():
    return {
        "inline_keyboard": [
            [{"text": f"{n} шт.", "callback_data": f"gen:{n}"} for n in (1, 3, 5, 10)],
        ]
    }


def card_keyboard(word):
    # The stage in callback_data makes a button from an older copy of the card stale
    # once the word has flipped direction.
    stage = word.get("stage", 0)
    return {
        "inline_keyboard": [
            [
                {"text": KNOWN_BUTTON, "callback_data": f"k:{word['id']}:{stage}"},
                {"text": UNKNOWN_BUTTON, "callback_data": f"n:{word['id']}:{stage}"},
            ]
        ]
    }


def main_keyboard():
    return {
        "keyboard": [[{"text": REVIEW_BUTTON}, {"text": NEXT_BUTTON}], [{"text": GENERATE_BUTTON}]],
        "resize_keyboard": True,
        "is_persistent": True,
    }


def menu_inline_keyboard():
    return {"inline_keyboard": [[{"text": MENU_BUTTON, "callback_data": "menu"}]]}



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


def render_stats(stats, queue_size, practice_size=0):
    avg = "—" if stats["avg_days"] is None else f"{stats['avg_days']:.1f} дн."
    return (
        "📊 <b>Статистика за неделю</b>\n"
        f"Выучено слов: <b>{stats['archived']}</b>\n"
        f"Средний срок от добавления до архива: <b>{avg}</b>\n\n"
        f"Всего в архиве: {stats['total_known']}\n"
        f"В очереди на изучение: {queue_size}\n"
        f"Ждут практики: {practice_size}"
    )


# ---- Saturday practice -------------------------------------------------------

PRACTICE_SKIP_BUTTON = "Пропустить"
_VERDICT_ICONS = {"ok": "✅", "near": "🟡", "wrong": "❌"}


def render_practice_round(words, direction):
    """direction "ru_en": Russian shown, English typed; "en_ru": the other way round."""
    if direction == "ru_en":
        head = f"🧠 <b>Практика</b> — {len(words)} сл.\n\n<b>1/3.</b> Напиши по-английски:"
        items = [_capitalize(w["ru"]) for w in words]
    else:
        head = "<b>2/3.</b> Теперь по-русски:"
        items = [_capitalize(w["en"]) for w in words]
    lines = [head] + [f"{i}) {escape(item)}" for i, item in enumerate(items, start=1)]
    lines += ["", "<i>Ответ одним сообщением: через запятую, пробел, таб или с новой строки.</i>"]
    return "\n".join(lines)


def render_practice_sentences(words):
    """Russian visible, English hidden; typing the translation is optional."""
    lines = ["<b>3/3.</b> Предложения (необязательно): переведи на английский или нажми «Пропустить»."]
    number = 0
    for word in words:
        example = current_example(word)
        if not example:
            continue
        number += 1
        lines += ["", f"{number}) {escape(example['ru'])}", _spoiler(example["en"])]
    return "\n".join(lines)


def practice_skip_keyboard():
    return {"inline_keyboard": [[{"text": PRACTICE_SKIP_BUTTON, "callback_data": "px"}]]}


def render_practice_feedback(graded):
    """graded: [(word, answer, verdict, expected)]"""
    lines = []
    for i, (_, answer, verdict, expected) in enumerate(graded, start=1):
        icon = _VERDICT_ICONS[verdict]
        if verdict == "ok":
            lines.append(f"{i}) {icon} {escape(expected)}")
        else:
            given = escape(answer) if answer else "—"
            lines.append(f"{i}) {icon} {given} → <b>{escape(expected)}</b>")
    return "\n".join(lines)


def render_practice_summary(outcome, cheer):
    lines = ["🏁 <b>Практика завершена</b>"]
    if outcome["ok"]:
        lines += ["", f"{cheer}", "В архив: " + ", ".join(escape(w["en"]) for w in outcome["ok"])]
    if outcome["near"]:
        lines += ["", "🟡 Почти (опечатка) — вторым в очереди: " + ", ".join(escape(w["en"]) for w in outcome["near"])]
    if outcome["wrong"]:
        lines += ["", "🔁 Учим заново с RU→EN — третьим в очереди: " + ", ".join(escape(w["en"]) for w in outcome["wrong"])]
    return "\n".join(lines)


def practice_cheer(count, rng=random):
    return rng.choice(["🎉", "🏆", "🚀", "🌟", "🧠"]) + f" Выучено слов: {count}!"
