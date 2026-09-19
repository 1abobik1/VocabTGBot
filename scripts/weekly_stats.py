"""Send the weekly statistics to every allowed user (run by .github/workflows/weekly-stats.yml)."""

import asyncio

from clients import bot_from_env


async def main():
    sent = await bot_from_env().broadcast_stats()
    print(f"stats sent to: {sent or 'nobody'}")


if __name__ == "__main__":
    asyncio.run(main())
