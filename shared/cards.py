"""Telegram message texts (HTML parse_mode) and keyboards."""

import random
import re
from html import escape as _html_escape

from . import compose
from . import curriculum as cur
from . import exercises as ex
from .practice import NEAR, OK, WRONG
from .words import current_example

KNOWN_BUTTON = "Знаю"
UNKNOWN_BUTTON = "Не знаю"
ARCHIVE_BUTTON = "📥 В архив"
COMPOSE_BUTTON = "🗣 Составить предложения"
COMPOSE_DONE_BUTTON = "🏁 Готово"
NEXT_BUTTON = "🃏 Карточка сейчас"
REVIEW_BUTTON = "🔁 Повтор архивных слов"
GENERATE_BUTTON = "🤖 AI-Генерация"
PRACTICE_BUTTON = "🧠 Практика"
STATS_BUTTON = "📊 Статистика"
HELP_BUTTON = "❓ Помощь"
SETTINGS_BUTTON = "⚙️ Настройки"
EXERCISE_BUTTON = "✍️ Упражнения"
TOPICS_BUTTON = "🧩 Темы упражнений"
EXERCISE_DONT_KNOW = "🤷 Не знаю"
EXERCISE_DISPUTE = "🤔 Спорное"
LEVEL_BUTTON = "🎚 Уровень"
BACK_BUTTON = "⬅️ Назад"
MENU_BUTTON = "Меню"
CANCEL_BUTTON = "Отмена"
# Texts of older keyboards still on users' screens until the new one arrives.
LEGACY_BUTTONS = {"Карточка сейчас": NEXT_BUTTON, "Повторить слова": REVIEW_BUTTON, "🤖 Сгенерировать": GENERATE_BUTTON}
# Bump when main_keyboard() changes: every user gets the new keyboard with their next reply.
KEYBOARD_VERSION = 4

ARCHIVE_PATH = (
    "Как слово попадает в архив:\n"
    "1. «Знаю» на обеих сторонах новой карточки (RU→EN, сразу за ней EN→RU)\n"
    "2. «Знаю» на повторе через сутки и ещё раз на повторе через три дня\n"
    f"3. Верно написать его на практике (каждое утро в 10:00 или кнопка «{PRACTICE_BUTTON}»)\n"
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
    f"{COMPOSE_BUTTON} под карточкой — вместо «{KNOWN_BUTTON}»: свои предложения со словом, текстом или "
    "голосовыми, по одному и сколько хочешь. ИИ сразу разбирает каждое и показывает, как сказать "
    f"естественнее; по «{COMPOSE_DONE_BUTTON}» — советы и частые конструкции со словом. Слово "
    f"употреблено верно хотя бы раз — засчитается как «{KNOWN_BUTTON}». Такой же совет приходит и после "
    f"обычного «{KNOWN_BUTTON}», когда слово отвечено с обеих сторон.\n\n"
    f"{NEXT_BUTTON} — следующая карточка из очереди прямо сейчас, не дожидаясь расписания. "
    "Если на прошлую карточку ещё нет ответа, пришлёт её повторно.\n\n"
    f"{REVIEW_BUTTON} — список выученных слов, новые сверху. Пришли номера забытых "
    "(например <code>2 5 7</code>) — они вернутся в очередь и пройдут весь путь заново.\n\n"
    f"{GENERATE_BUTTON} — ИИ подберёт новые карточки: выбери количество, уровень по умолчанию общий. "
    "С темой — командой <code>/gen 5 путешествия</code>. Карточки приходят по одной на проверку: "
    "«В очередь», «Исправить» или «Удалить». Если очередь пуста, ИИ сам предложит слово "
    "за 20 минут до карточки по расписанию.\n\n"
    f"{EXERCISE_BUTTON} — 5 заданий с пропуском по грамматике A1–B2 (программа British Council) и на слова "
    "из твоего словаря. Правила подбирает бот: примерно половина — где ты ошибаешься, треть — новые "
    "по программе, остальное — повтор освоенного. Приходят сами в 20:00 и ждут, пока не пройдёшь; "
    f"карточкам не мешают. «{EXERCISE_DISPUTE}» — если задание показалось спорным: оно не засчитается.\n\n"
    f"{PRACTICE_BUTTON} — написать слова, прошедшие интервальные повторы: сначала по-английски, "
    "потом по-русски, потом по желанию предложения. Верно — в архив, опечатка — завтра ещё одна "
    "карточка, ошибка — слово учится заново. Сама запускается каждое утро в 10:00, по 7 слов; карточки подождут и придут после неё.\n\n"
    f"<b>{SETTINGS_BUTTON}</b> — редкие действия:\n"
    f"{LEVEL_BUTTON} — общий уровень A1–B2: по нему подбираются слова, примеры и упражнения. Примеры к новым словам строятся на грамматике, где у тебя пробелы.\n"
    f"{TOPICS_BUTTON} — какие разделы грамматики давать; по умолчанию все.\n"
    f"{STATS_BUTTON} — сколько слов выучено за неделю, что в очереди, ошибки на практике, слабые места "
    "в упражнениях и прогресс по уровням A1–B2. Приходит сама по воскресеньям в 21:00 вместе с разбором от ИИ.\n"
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


def _level_row(current, prefix):
    return [{"text": f"• {lv} •" if lv == current else lv, "callback_data": f"{prefix}:{lv}"} for lv in cur.LEVELS]


def render_generate_menu(level, own=False):
    """`own` — у генерации свой уровень, отличный от общего."""
    where = "свой для генерации" if own else f"как общий, сменить общий: {LEVEL_BUTTON}"
    return (
        f"{GENERATE_BUTTON}\n\n"
        f"Уровень: <b>{level}</b> ({where}).\n"
        "Сколько карточек сгенерировать?\n\n"
        "С темой своими словами: <code>/gen 5 путешествия</code> или <code>/gen 3 B2 работа в офисе</code>"
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
            ],
            [{"text": COMPOSE_BUTTON, "callback_data": f"sx:{word['id']}:{stage}"}],
        ]
    }


# ---- свои предложения со словом ----------------------------------------------------------


def render_compose_prompt(word):
    """Как на карточке: на первой стороне английское слово нужно вспомнить самому."""
    if word.get("stage", 0) == 0:
        target = f"«{escape(_capitalize(word['ru']))}» — {_spoiler(word['en'])}"
    else:
        target = f"<b>{escape(word['en'])}</b> — {_spoiler(word['ru'])}"
    return (
        f"🗣 Составь предложение со словом {target}\n\n"
        "Напиши его или запиши голосовое — можно по одному предложению и сколько хочешь, каждое разберу сразу. "
        f"«{COMPOSE_DONE_BUTTON}» — итог с советами, как ещё употребляют слово. Слово употреблено верно — "
        f"засчитаю как «{KNOWN_BUTTON}», иначе — как «{UNKNOWN_BUTTON}»."
    )


def compose_keyboard(done=False):
    row = [{"text": COMPOSE_DONE_BUTTON, "callback_data": "sd"}] if done else []
    row.append({"text": CANCEL_BUTTON, "callback_data": "sc"})
    return {"inline_keyboard": [row]}


_COMPOSE_MARKS = {compose.CORRECT: "✅", compose.GRAMMAR: "🟡", compose.WRONG: "❌"}


def render_compose_review(items, start=1, transcript=None, skipped=0):
    """Разбор только что присланных предложений; нумерация продолжает прежние."""
    lines = []
    if transcript:
        lines += [f"🎙 Услышал: <i>{escape(transcript)}</i>", ""]
    for number, item in enumerate(items, start=start):
        line = f"{number}. {_COMPOSE_MARKS[item['verdict']]} {escape(item['sentence'])}"
        if compose.changed(item):
            line += f"\n→ <b>{escape(item['corrected'])}</b>"
        if item["comment"]:
            line += f"\n<i>{escape(item['comment'])}</i>"
        lines.append(line)
    if skipped:
        lines += ["", f"<i>Ещё {skipped} не разобрал: в одном сообщении — до {compose.MAX_PER_MESSAGE}. "
                      "Пришли их следующим.</i>"]
    return "\n".join(lines)


def render_compose_progress(items):
    good, total = compose.good_count(items), len(items)
    text = f"Верно: {good} из {total}. "
    if compose.passed(items):
        text += f"Уже засчитывается — «{COMPOSE_DONE_BUTTON}» или ещё предложение."
    else:
        text += "Пока слово не употреблено верно — пришли ещё предложение текстом или голосом."
    return text


# Английская фраза в кавычках внутри русского текста: 'look forward to', «chill out», "reliable".
# Апостроф внутри слова (don't, it's) кавычкой не считается: перед открывающей не должно быть буквы,
# после закрывающей — буквы, а апостроф перед строчной буквой — часть фразы.
_QUOTED_ENGLISH = re.compile(r"(?<![A-Za-z])['‘\"«]([A-Za-z](?:[^'‘’\"«»\n]|['’](?=[a-z]))*?)['’\"»](?![A-Za-z])")


def _highlight_english(text):
    """Русский текст с английскими фразами: кавычки убираются, фразы — жирным."""
    parts, last = [], 0
    for match in _QUOTED_ENGLISH.finditer(text):
        parts += [escape(text[last:match.start()]), f"<b>{escape(match.group(1))}</b>"]
        last = match.end()
    return "".join(parts) + escape(text[last:])


_TENSE_NAMES = {compose.PAST: "прошлое", compose.PRESENT: "настоящее", compose.FUTURE: "будущее"}


def render_compose_result(word, items, usage=None):
    """Итог: засчитано ли слово и, если ИИ ответил, как ещё его употребляют."""
    good, total = compose.good_count(items), len(items)
    lines = [f"🏁 <b>Итог</b> · {escape(word['en'])}", ""]
    if compose.passed(items):
        lines.append(f"Верно {good} из {total} — засчитано как «{KNOWN_BUTTON}».")
    else:
        lines.append(f"Верно {good} из {total} — считаю как «{UNKNOWN_BUTTON}»: слово вернётся позже.")
    if usage:
        lines += _usage_lines(usage)
    return "\n".join(lines)


def _usage_lines(usage):
    tip, examples = usage
    lines = []
    if tip:
        lines += ["", f"💡 {_highlight_english(tip)}"]
    if examples:
        lines += ["", "<b>Ещё так говорят:</b>"]
        lines += [f"• {_TENSE_NAMES[e['tense']]}: {escape(e['en'])}\n  <i>{escape(e['ru'])}</i>" for e in examples]
    return lines


def render_word_advice(word, usage):
    """Совет после «Знаю» на карточке — как после своих предложений."""
    return "\n".join([f"📘 <b>{escape(word['en'])}</b> — {escape(word['ru'])}"] + _usage_lines(usage))


def main_keyboard():
    """Главное меню — только то, чем пользуешься каждый день; редкое спрятано в «Настройки»."""
    return {
        "keyboard": [
            [{"text": NEXT_BUTTON}, {"text": PRACTICE_BUTTON}],
            [{"text": EXERCISE_BUTTON}, {"text": GENERATE_BUTTON}],
            [{"text": SETTINGS_BUTTON}],
        ],
        "resize_keyboard": True,
        "is_persistent": True,
    }


def settings_keyboard():
    return {
        "keyboard": [
            [{"text": LEVEL_BUTTON}, {"text": TOPICS_BUTTON}],
            [{"text": STATS_BUTTON}, {"text": REVIEW_BUTTON}],
            [{"text": HELP_BUTTON}, {"text": BACK_BUTTON}],
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
_VERDICT_ICONS = {OK: "✅", NEAR: "🟡", WRONG: "❌"}


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
        if verdict == OK:
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
    if outcome[OK]:
        lines += ["", f"{cheer}", "В архив: " + ", ".join(escape(w["en"]) for w in outcome[OK])]
    if outcome[NEAR]:
        lines += ["", "🟡 Почти (опечатка) — завтра ещё одна карточка EN→RU: "
                  + ", ".join(escape(w["en"]) for w in outcome[NEAR])]
    if outcome[WRONG]:
        lines += ["", "🔁 Учим заново с RU→EN, начиная с завтра: " + ", ".join(escape(w["en"]) for w in outcome[WRONG])]
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
        lines.append(f"{label}: ✅ {c[OK]} · 🟡 {c[NEAR]} · ❌ {c[WRONG]}")
    if stats["worst"]:
        items = ", ".join(f"{escape(en)} ({n})" for en, n in stats["worst"][:top])
        lines.append(f"Чаще всего ошибки: {items}")
    return "\n".join(lines)


# ---- упражнения ----------------------------------------------------------------

def render_exercise(exercise, index, total):
    hint = "Выбери ответ кнопкой." if ex.uses_buttons(exercise) else "Напиши ответ сообщением."
    topic = cur.rule_name(exercise["rule"])
    return (
        f"✍️ <b>Упражнение {index + 1}/{total}</b> · {escape(topic)}\n\n"
        f"{escape(exercise['sentence'])}\n\n<i>{hint}</i>"
    )


def exercise_keyboard(exercise, index):
    rows = []
    if ex.uses_buttons(exercise):
        rows.append([{"text": o, "callback_data": f"xo:{index}:{k}"} for k, o in enumerate(exercise["options"])])
    rows.append([
        {"text": EXERCISE_DONT_KNOW, "callback_data": f"xn:{index}"},
        {"text": EXERCISE_DISPUTE, "callback_data": f"xd:{index}"},
    ])
    return {"inline_keyboard": rows}


def render_exercise_feedback(exercise, given, verdict):
    answers = " / ".join([exercise["answer"]] + exercise.get("accepted", []))
    if verdict == OK:
        head = f"✅ Верно: <b>{escape(exercise['answer'])}</b>"
    elif verdict == NEAR:
        head = f"🟡 Почти (опечатка): <b>{escape(answers)}</b>"
    elif given:
        head = f"❌ Правильно: <b>{escape(answers)}</b> (у тебя: {escape(given)})"
    else:
        head = f"❌ Правильно: <b>{escape(answers)}</b>"
    before, answer, after = ex.filled_sentence(exercise)
    lines = [head, "", f"{escape(before)}<b>{escape(answer)}</b>{escape(after)}"]
    if exercise.get("translation"):
        lines.append(escape(exercise["translation"]))
    lines += ["", f"<i>{escape(exercise['explanation'])}</i>"]
    return "\n".join(lines)


def render_exercise_summary(results, weak):
    ok = sum(1 for r in results if r["verdict"] == OK)
    counted = [r for r in results if r["verdict"] != ex.DISPUTED]
    lines = [f"🏁 <b>Упражнения на сегодня готовы</b>: ✅ {ok} из {len(counted)}"]
    if weak:
        lines.append("Подтянуть: " + ", ".join(cur.rule_name(s["rule"]) for s in weak))
    return "\n".join(lines)


def render_topics_menu(chosen):
    status = "все темы программы по твоему уровню" if not chosen else "выбраны вручную"
    return (
        f"{TOPICS_BUTTON}\n\nСейчас: <b>{status}</b>.\n"
        "Нажимай на тему, чтобы включить или выключить её. "
        "«Все по уровню» — снова все темы."
    )


def topics_keyboard(chosen):
    rows = []
    for group, name in cur.GROUPS.items():
        mark = "✅" if (not chosen or group in chosen) else "▫️"
        rows.append([{"text": f"{mark} {name}", "callback_data": f"xt:{group}"}])
    rows.append([{"text": "🤖 Все по уровню", "callback_data": "xt:auto"}])
    return {"inline_keyboard": rows}


def render_exercise_stats(stats, weak, advice=None):
    lines = ["", ""]
    if not stats["total"]:
        lines.append("✍️ <b>Упражнения за неделю</b>: пока не было.")
    else:
        c = stats["counts"]
        lines.append(f"✍️ <b>Упражнения за неделю</b> — {stats['total']}: ✅ {c[OK]} · 🟡 {c[NEAR]} · ❌ {c[WRONG]}")
    if weak:
        items = ", ".join(f"{cur.rule_name(s['rule'])} ({s['wrong']} из {s['count']})" for s in weak)
        lines.append(f"🔎 Слабые места: {items}")
    if advice:
        lines.append(f"💡 {escape(advice)}")
    return "\n".join(lines)


def _bar(share, width=10):
    filled = round(share * width)
    return "█" * filled + "░" * (width - filled)


def render_level_progress(rows, working, chosen, top=4):
    """Уровень по упражнениям: доля уверенно освоенных правил программы на каждом уровне."""
    lines = ["", "", "📈 <b>Грамматика по упражнениям</b> (программа British Council, A1–B2)"]
    for row in rows:
        share = row["confident"] / row["total"] if row["total"] else 0
        started = row["total"] - len(row["not_started"])
        if share >= cur.CONFIDENT_SHARE:
            note = "уверенно"
        elif row["level"] == working:
            note = "сейчас изучаешь"
        elif not started:
            note = "ещё не проверялось"
        else:
            note = f"проверено {started} из {row['total']}"
        lines.append(f"{row['level']} {_bar(share)} {round(share * 100)}% — {note}")
    current = next((row for row in rows if row["level"] == working), None)
    if current is not None:
        todo = current["learning"] + current["not_started"]
        if todo:
            names = ", ".join(escape(r.name) for r in todo[:top])
            more = f" и ещё {len(todo) - top}" if len(todo) > top else ""
            lines.append(f"Дальше на {working}: {names}{more}")
    if working != chosen:
        lines.append(f"<i>Выбран уровень {chosen}, но на {working} ещё есть пробелы — упражнения их подтянут.</i>")
    return "\n".join(lines)
