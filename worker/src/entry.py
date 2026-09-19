"""Telegram webhook handler running as a Cloudflare Python Worker.

All business logic lives in `shared/` (copied into src/ by the [build] step in wrangler.toml).
"""

import json
import traceback

from workers import Response, WorkerEntrypoint, fetch

from shared.bot import Bot


class KVStore:
    def __init__(self, namespace):
        self.namespace = namespace

    async def get(self, key):
        value = await self.namespace.get(key)
        # Missing keys come back as JS null/undefined; only strings are real values.
        return value if isinstance(value, str) else None

    async def put(self, key, value):
        await self.namespace.put(key, value)


class Telegram:
    def __init__(self, token, api_base=None):
        self.base = f"{api_base or 'https://api.telegram.org'}/bot{token}/"

    async def call(self, method, payload):
        response = await fetch(
            self.base + method,
            method="POST",
            headers={"content-type": "application/json"},
            body=json.dumps(payload, ensure_ascii=False),
        )
        data = await response.json()
        if not data.get("ok"):
            print(f"telegram {method} failed: {data}")
        return data


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        if request.method != "POST":
            return Response("ok")
        secret = getattr(self.env, "TELEGRAM_WEBHOOK_SECRET", None)
        # Read the body before any early return: an unconsumed body breaks the next request in `wrangler dev`.
        body = await request.text()
        if not secret or request.headers.get("x-telegram-bot-api-secret-token") != secret:
            return Response("forbidden", status=403)
        try:
            update = json.loads(body)
            bot = Bot(
                KVStore(self.env.VOCAB_KV),
                Telegram(self.env.TELEGRAM_BOT_TOKEN, getattr(self.env, "TELEGRAM_API_BASE", None)),
                owner=getattr(self.env, "OWNER_USERNAME", None),
            )
            await bot.handle_update(update)
        except Exception:
            # Always answer 200, otherwise Telegram keeps redelivering the same update.
            traceback.print_exc()
        return Response("ok")
