"""Point the Telegram bot at the deployed Worker.

    TELEGRAM_BOT_TOKEN=... TELEGRAM_WEBHOOK_SECRET=... python scripts/set_webhook.py https://vocab-bot.<you>.workers.dev
"""

import asyncio
import sys

from clients import Telegram, env


async def main(url):
    tg = Telegram(env("TELEGRAM_BOT_TOKEN"))
    result = await tg.call(
        "setWebhook",
        {
            "url": url,
            "secret_token": env("TELEGRAM_WEBHOOK_SECRET"),
            "allowed_updates": ["message", "callback_query"],
            "drop_pending_updates": True,
        },
    )
    print(result)
    await tg.call(
        "setMyCommands",
        {
            "commands": [
                {"command": "next", "description": "Карточка прямо сейчас"},
                {"command": "review", "description": "Повторить выученные слова"},
                {"command": "gen", "description": "Сгенерировать карточки ИИ: /gen 5 B2 тема"},
                {"command": "practice", "description": "Практика: написать выученные слова"},
                {"command": "ex", "description": "Упражнения по грамматике A1–B2"},
                {"command": "stats", "description": "Статистика за неделю"},
                {"command": "help", "description": "Как добавлять слова"},
            ]
        },
    )


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    asyncio.run(main(sys.argv[1]))
