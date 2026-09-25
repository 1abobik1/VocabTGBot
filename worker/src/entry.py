"""Telegram webhook handler running as a Cloudflare Python Worker.

All business logic lives in `shared/` (copied into src/ by the [build] step in wrangler.toml).
"""

import asyncio
import json
import traceback
from datetime import datetime, timezone

from js import Object, Promise
from js import fetch as js_fetch
from pyodide.ffi import to_js
from workers import Response, WorkerEntrypoint, fetch

from shared.bot import Bot
from shared.schedule import Schedule


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
        self.file_base = f"{api_base or 'https://api.telegram.org'}/file/bot{token}/"

    async def download(self, file_path):
        """Файл из Telegram (например, голосовое) как bytes."""
        response = await js_fetch(self.file_base + file_path)
        if not response.ok:
            raise RuntimeError(f"telegram file download failed: {response.status}")
        return bytes((await response.arrayBuffer()).to_py())

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


class WorkersAI:
    """Cloudflare Workers AI through the `AI` binding (no API key needed)."""

    def __init__(self, binding):
        self.binding = binding

    async def run(self, model, payload):
        result = await self.binding.run(model, to_js(payload, dict_converter=Object.fromEntries))
        return result.to_py() if hasattr(result, "to_py") else result


class HttpAI:
    """Local development only: POST {"model", "input"} to AI_HTTP_URL (e.g. a mock server)."""

    def __init__(self, url):
        self.url = url

    async def run(self, model, payload):
        response = await fetch(
            self.url,
            method="POST",
            headers={"content-type": "application/json"},
            body=json.dumps({"model": model, "input": payload}),
        )
        return await response.json()


class Default(WorkerEntrypoint):
    def _ai(self):
        binding = getattr(self.env, "AI", None)
        if binding is not None:
            return WorkersAI(binding)
        url = getattr(self.env, "AI_HTTP_URL", None)
        return HttpAI(url) if url else None

    def _bot(self):
        env_get = lambda name: getattr(self.env, name, None)  # noqa: E731
        return Bot(
            KVStore(self.env.VOCAB_KV),
            Telegram(self.env.TELEGRAM_BOT_TOKEN, env_get("TELEGRAM_API_BASE")),
            owner=env_get("OWNER_USERNAME"),
            schedule=Schedule.from_env(env_get),
            ai=self._ai(),
            ai_model=env_get("AI_MODEL"),
            defer=self._defer,
        )

    def _defer(self, coro):
        """Фоновая работа после ответа Telegram (например, следующая пачка упражнений)."""
        async def guarded():
            try:
                await coro
            except Exception:
                traceback.print_exc()

        self.ctx.waitUntil(Promise.resolve(asyncio.ensure_future(guarded())))

    async def scheduled(self, controller, *args):
        """Cron trigger (every minute): sends a card when the minute is a schedule slot."""
        when = datetime.fromtimestamp(controller.scheduledTime / 1000, tz=timezone.utc)
        sent = await self._bot().on_cron(when)
        if sent:
            print(f"cards sent at {when.isoformat()} to {sent}")

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
            await self._bot().handle_update(update)
        except Exception:
            # Always answer 200, otherwise Telegram keeps redelivering the same update.
            traceback.print_exc()
        return Response("ok")
