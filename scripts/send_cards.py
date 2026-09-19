"""Manually send the next flashcard to every user whose previous card is answered.

Scheduled cards come from the Worker cron; this is run by .github/workflows/send-card.yml.
"""

import asyncio

from clients import bot_from_env


async def main():
    sent = await bot_from_env().broadcast_cards()
    print(f"cards sent to: {sent or 'nobody (empty queues or no chat yet)'}")


if __name__ == "__main__":
    asyncio.run(main())
