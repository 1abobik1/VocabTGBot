"""Stdlib-only clients used by the GitHub Actions scripts."""

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from shared.bot import Bot  # noqa: E402


def env(name, default=None):
    value = os.environ.get(name, default)
    if value is None or value == "":
        raise SystemExit(f"Environment variable {name} is not set")
    return value


def _request(url, method="GET", data=None, headers=None):
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


class CloudflareKV:
    """Cloudflare KV over the REST API (token needs "Workers KV Storage: Edit")."""

    def __init__(self, account_id, namespace_id, api_token):
        self.base = (
            f"https://api.cloudflare.com/client/v4/accounts/{account_id}"
            f"/storage/kv/namespaces/{namespace_id}/values/"
        )
        self.headers = {"Authorization": f"Bearer {api_token}"}

    async def get(self, key):
        status, body = _request(self.base + urllib.parse.quote(key, safe=""), headers=self.headers)
        if status == 404:
            return None
        if status != 200:
            raise RuntimeError(f"KV get {key}: HTTP {status} {body[:200]!r}")
        return body.decode("utf-8")

    async def put(self, key, value):
        headers = dict(self.headers, **{"Content-Type": "text/plain; charset=utf-8"})
        status, body = _request(
            self.base + urllib.parse.quote(key, safe=""), "PUT", value.encode("utf-8"), headers
        )
        if status != 200:
            raise RuntimeError(f"KV put {key}: HTTP {status} {body[:200]!r}")


class Telegram:
    def __init__(self, token, api_base="https://api.telegram.org"):
        self.base = f"{api_base}/bot{token}/"

    async def call(self, method, payload):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        status, body = _request(self.base + method, "POST", data, {"Content-Type": "application/json"})
        result = json.loads(body or b"{}")
        if not result.get("ok"):
            print(f"telegram {method} failed: HTTP {status} {result}")
        return result


def bot_from_env():
    store = CloudflareKV(env("CLOUDFLARE_ACCOUNT_ID"), env("CF_KV_NAMESPACE_ID"), env("CLOUDFLARE_API_TOKEN"))
    tg = Telegram(env("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_API_BASE") or "https://api.telegram.org")
    return Bot(store, tg)
