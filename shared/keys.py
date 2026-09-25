"""Ключи Cloudflare KV. Всё, кроме списка пользователей, хранится отдельно для каждого пользователя."""

ALLOWED_USERS_KEY = "allowed_users"


def _per_user(prefix, doc):
    def key(username):
        return f"{prefix}:{username}"

    key.__doc__ = doc
    return key


queue_key = _per_user("queue", "Слова в обучении: [слово], срок показа у каждого свой (см. srs).")
known_key = _per_user("known", "Архив выученных слов.")
practice_key = _per_user("practice", "Слова, прошедшие интервалы и ждущие письменной практики.")
inbox_key = _per_user("inbox", "Карточки от ИИ на проверке, до очереди.")
state_key = _per_user("state", "Режим диалога: {'mode': 'review' | 'edit', ...}.")
settings_key = _per_user("settings", "Уровень, уровень генерации, темы упражнений, версия клавиатуры.")
sched_key = _per_user("sched", "{'date', 'new', 'missed'}: новые слова за день и пропущенные слоты.")
session_key = _per_user("session", "Текущая практика: {'ids', 'round', 'results'}.")
practice_log_key = _per_user("plog", "Итоги практики: [{'at', 'en', 'ru_en', 'en_ru'}].")
exercise_session_key = _per_user("exsession", "Текущие упражнения: {'items', 'i', 'results'}.")
exercise_pool_key = _per_user("expool", "Готовая пачка упражнений: {'items', 'level', 'types', 'at'}.")
mistake_log_key = _per_user("mlog", "Журнал упражнений: [{'at', 'rule', 'verdict', 'sentence', 'answer', 'given'}].")
advice_key = _per_user("advice", "Последний недельный разбор ИИ: {'at', 'text'}.")
seen_key = _per_user("seen", "Все английские слова, что когда-либо были у пользователя, — ИИ их не повторяет.")
compose_key = _per_user("compose", "Предложения со словом с карточки: {'id', 'stage', 'card_message_id', 'prompt_message_id'}.")
