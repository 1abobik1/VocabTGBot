"""Telegram message texts (HTML parse_mode) and keyboards."""

import random
from html import escape as _html_escape

from .words import current_example

KNOWN_BUTTON = "Знаю"
UNKNOWN_BUTTON = "Не знаю"
ARCHIVE_BUTTON = "📥 В архив"
NEXT_BUTTON = "🃏 Карточка сейчас"
REVIEW_BUTTON = "🔁 Повтор архивных слов"
GENERATE_BUTTON = "🤖 AI-Генерация"
PRACTICE_BUTTON = "🧠 Практика"
STATS_BUTTON = "📊 Статистика"
HELP_BUTTON = "❓ Помощь"
SETTINGS_BUTTON = "⚙️ Настройки"
LEVEL_BUTTON = "🎚 Уровень"
BACK_BUTTON = "⬅️ Назад"
MENU_BUTTON = "Меню"
CANCEL_BUTTON = "Отмена"
# Texts of older keyboards still on users' screens until the new one arrives.
LEGACY_BUTTONS = {"Карточка сейчас": NEXT_BUTTON, "Повторить слова": REVIEW_BUTTON, "🤖 Сгенерировать": GENERATE_BUTTON}
# Bump when main_keyboard() changes: every user gets the new keyboard with their next reply.
KEYBOARD_VERSION = 3

ARCHIVE_PATH = (
    "Как слово попадает в архив:\n"
    "1. «Знаю» на обеих сторонах новой карточки (RU→EN, сразу за ней EN→RU)\n"
    "2. «Знаю» на повторе через сутки и ещё раз на повторе через три дня\n"
    f"3. Верно написать его на практике (каждый вечер в 22:30 или кнопка «{PRACTICE_BUTTON}»)\n"
    f"Либо сразу — кнопкой «{ARCHIVE_BUTTON}» под карточкой, если слово уже знаешь."
)

HELP_TEXT = (
    "<b>Как добавить слово</b> — пришли одним сообщением:\n"
    "<code>apple - яблоко\n"
    "I ate an apple. - Я съел яблоко.\n"
    "Синонимы: fruit - фрукт</code>\n"
    "Первая строка — слово и перевод, дальше по желанию примеры и синонимы. Разделитель « - » или « — » "
    "с пробелами, порядок языков любой.\n"
    "Если примеров и синонимов нет, их придумает ИИ и покажет карточку на проверку: "
    "«В очередь», «Исправить», «Без примеров» или «Удалить».\n\n"
    "<b>Кнопки</b>\n"
    f"{NEXT_BUTTON} — следующая карточка из очереди прямо сейчас, не дожидаясь расписания. "
    "Если на прошлую карточку ещё нет ответа, пришлёт её повторно.\n\n"
    f"{REVIEW_BUTTON} — список выученных слов, новые сверху. Пришли номера забытых "
    "(например <code>2 5 7</code>) — они вернутся в очередь и пройдут весь путь заново.\n\n"
    f"{GENERATE_BUTTON} — ИИ подберёт новые карточки: выбери количество, уровень по умолчанию общий. "
    "С темой — командой <code>/gen 5 путешествия</code>. Карточки приходят по одной на проверку: "
    "«В очередь», «Исправить» или «Удалить». Если очередь пуста, ИИ сам предложит слово "
    "за 20 минут до карточки по расписанию.\n\n"
    f"{PRACTICE_BUTTON} — написать слова, прошедшие интервальные повторы: сначала по-английски, "
    "потом по-русски, потом по желанию предложения. Верно — в архив, опечатка — завтра ещё одна "
    "карточка, ошибка — слово учится заново. Сама запускается каждый вечер в 22:30, по 7 слов.\n\n"
    f"<b>{SETTINGS_BUTTON}</b> — редкие действия:\n"
    f"{LEVEL_BUTTON} — общий уровень A1–C1: по нему ИИ подбирает слова, примеры и упражнения.\n"
    f"{STATS_BUTTON} — сколько слов выучено за неделю, что в очереди и где ошибки на практике. "
    "Приходит сама по воскресеньям.\n"
    f"{REVIEW_BUTTON} и {HELP_BUTTON} — тоже здесь. {BACK_BUTTON} — в главное меню.\n\n"
    "<b>Расписание</b>: 14 слотов в день с 10:00 до 23:00, из них 6 — под новые слова. "
    "«Не знаю» не ставит слово следующим: оно вернётся через 30 минут, потом через 2 часа, потом завтра, "
    "и показывается не больше 3 раз в день. «Знаю» отправляет слово на повтор через сутки, "
    "затем через три дня, а потом на практику. Пока на карточку или практику нет ответа, новые "
    "не приходят, а после ответа приходят все пропущенные.\n\n"
    + ARCHIVE_PATH
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
    header = "🆕 Примеры от ИИ" if word.get("source") == "manual" else "🆕 Новая карточка"
    header += f" · {level}" if level else ""
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
    rows = [
        [
            {"text": "✅ В очередь", "callback_data": f"ia:{word_id}"},
            {"text": "✏️ Исправить", "callback_data": f"ie:{word_id}"},
            {"text": "🗑 Удалить", "callback_data": f"ix:{word_id}"},
        ]
    ]
    if word.get("examples") or word.get("synonyms"):
        rows.append([{"text": "🚫 Без примеров", "callback_data": f"ib:{word_id}"}])
    return {"inline_keyboard": rows}


LEVELS = ("A1", "A2", "B1", "B2", "C1")


def _level_row(current, prefix):
    return [{"text": f"• {lv} •" if lv == current else lv, "callback_data": f"{prefix}:{lv}"} for lv in LEVELS]


def render_generate_menu(level, own=False):
    """`own` — у генерации свой уровень, отличный от общего."""
    where = "свой для генерации" if own else f"как общий, сменить общий: {LEVEL_BUTTON}"
    return (
        f"{GENERATE_BUTTON}\n\n"
        f"Уровень: <b>{level}</b> ({where}).\n"
        "Сколько карточек сгенерировать?\n\n"
        "С темой своими словами: <code>/gen 5 путешествия</code> или <code>/gen 3 C1 работа в офисе</code>"
    )


def generate_keyboard(level, own=False):
    rows = [
        [{"text": f"{n} шт.", "callback_data": f"gen:{n}"} for n in (1, 3, 5, 10)],
        _level_row(level, "lv"),
    ]
    if own:
        rows.append([{"text": "↩︎ Как общий уровень", "callback_data": "lv:auto"}])
    return {"inline_keyboard": rows}


def render_level_menu(level):
    return (
        f"{LEVEL_BUTTON}\n\n"
        f"Общий уровень английского: <b>{level}</b>.\n"
        "По нему ИИ подбирает новые слова, примеры к словам, которые ты добавляешь сам, "
        f"и упражнения. В «{GENERATE_BUTTON}» можно выбрать другой уровень только для генерации."
    )


def level_keyboard(level):
    return {"inline_keyboard": [_level_row(level, "glv")]}


def cancel_keyboard():
    return {"inline_keyboard": [[{"text": CANCEL_BUTTON, "callback_data": "cancel"}]]}


def card_keyboard(word):
    # The stage in callback_data makes a button from an older copy of the card stale
    # once the word has flipped direction.
    stage = word.get("stage", 0)
    return {
        "inline_keyboard": [
            [
                {"text": KNOWN_BUTTON, "callback_data": f"k:{word['id']}:{stage}"},
                {"text": UNKNOWN_BUTTON, "callback_data": f"n:{word['id']}:{stage}"},
                {"text": ARCHIVE_BUTTON, "callback_data": f"a:{word['id']}:{stage}"},
            ]
        ]
    }


def main_keyboard():
    """Главное меню — только то, чем пользуешься каждый день; редкое спрятано в «Настройки»."""
    return {
        "keyboard": [
            [{"text": NEXT_BUTTON}, {"text": PRACTICE_BUTTON}],
            [{"text": GENERATE_BUTTON}, {"text": SETTINGS_BUTTON}],
        ],
        "resize_keyboard": True,
        "is_persistent": True,
    }


def settings_keyboard():
    return {
        "keyboard": [
            [{"text": LEVEL_BUTTON}, {"text": STATS_BUTTON}],
            [{"text": REVIEW_BUTTON}, {"text": HELP_BUTTON}],
            [{"text": BACK_BUTTON}],
        ],
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


def render_stats(stats, queue_stats, practice_size=0, new_today=0, new_quota=0):
    avg = "—" if stats["avg_days"] is None else f"{stats['avg_days']:.1f} дн."
    total = queue_stats["new"] + queue_stats["due"] + queue_stats["later"]
    return (
        "📊 <b>Статистика за неделю</b>\n"
        f"Выучено слов: <b>{stats['archived']}</b>\n"
        f"Средний срок от добавления до архива: <b>{avg}</b>\n\n"
        f"Новых слов сегодня: {new_today} из {new_quota}\n"
        f"В очереди: {total} — готовы сейчас {queue_stats['new'] + queue_stats['due']}, "
        f"ждут своего дня {queue_stats['later']}\n"
        f"Ждут практики: {practice_size}\n"
        f"Всего в архиве: {stats['total_known']}"
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
    """graded: [(слово, ответ, вердикт, ожидаемое, другие подходящие варианты)]"""
    lines = []
    for i, (_, answer, verdict, expected, extras) in enumerate(graded, start=1):
        icon = _VERDICT_ICONS[verdict]
        if verdict == "ok":
            line = f"{i}) {icon} {escape(expected)}"
            if extras:
                line += f"\n    можно и так: {escape(', '.join(extras))}"
            lines.append(line)
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


def render_practice_stats(stats, top=5):
    """Блок недельной статистики: где ошибаешься на практике."""
    if not stats["sessions_words"]:
        return "\n\n🧠 <b>Практика за неделю</b>: пока не было."
    names = {"ru_en": "RU→EN (писал по-английски)", "en_ru": "EN→RU (писал по-русски)"}
    lines = ["", "", f"🧠 <b>Практика за неделю</b> — слов: {stats['sessions_words']}"]
    for direction, label in names.items():
        c = stats["counts"][direction]
        lines.append(f"{label}: ✅ {c['ok']} · 🟡 {c['near']} · ❌ {c['wrong']}")
    if stats["worst"]:
        items = ", ".join(f"{escape(en)} ({n})" for en, n in stats["worst"][:top])
        lines.append(f"Чаще всего ошибки: {items}")
    return "\n".join(lines)
