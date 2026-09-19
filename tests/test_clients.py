import asyncio
import json
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import clients  # noqa: E402


class FakeHTTP:
    """Stands in for clients._request: an in-memory KV REST API plus a Telegram API."""

    def __init__(self):
        self.kv = {}
        self.telegram = []

    def __call__(self, url, method="GET", data=None, headers=None):
        if url.startswith("https://api.cloudflare.com/"):
            assert headers["Authorization"] == "Bearer cf-token"
            assert "/accounts/acc/storage/kv/namespaces/ns/values/" in url
            key = url.rsplit("/", 1)[1]
            if method == "PUT":
                self.kv[key] = data.decode()
                return 200, b'{"success":true}'
            return (200, self.kv[key].encode()) if key in self.kv else (404, b"")
        method_name = url.rsplit("/", 1)[1]
        self.telegram.append((method_name, json.loads(data)))
        return 200, b'{"ok":true,"result":{}}'


ENV = {
    "TELEGRAM_BOT_TOKEN": "tg",
    "CLOUDFLARE_API_TOKEN": "cf-token",
    "CLOUDFLARE_ACCOUNT_ID": "acc",
    "CF_KV_NAMESPACE_ID": "ns",
}


class ClientsTest(unittest.TestCase):
    def test_scheduled_card_over_rest(self):
        http = FakeHTTP()
        with mock.patch.object(clients, "_request", http), mock.patch.dict(os.environ, ENV):
            bot = clients.bot_from_env()
            # nobody has chatted with the bot yet -> nothing is sent and nothing is seeded
            self.assertEqual(asyncio.run(bot.broadcast_cards()), [])
            self.assertEqual(http.kv, {})

            http.kv["allowed_users"] = json.dumps([{"username": "OwnerUser", "role": "owner", "chat_id": 5}])
            http.kv["queue%3Aowneruser"] = json.dumps([{"id": "x", "en": "cat", "ru": "кот", "examples": [], "shown_count": 0, "stage": 0}])
            self.assertEqual(asyncio.run(bot.broadcast_cards()), ["OwnerUser"])
            method, payload = http.telegram[-1]
            self.assertEqual((method, payload["chat_id"]), ("sendMessage", 5))
            self.assertTrue(payload["text"].startswith("Кот - <tg-spoiler>cat</tg-spoiler>"))
            self.assertEqual(json.loads(http.kv["queue%3Aowneruser"])[0]["shown_count"], 1)

            self.assertEqual(asyncio.run(bot.broadcast_stats()), ["OwnerUser"])
            self.assertIn("Статистика", http.telegram[-1][1]["text"])

    def test_missing_env_fails_loudly(self):
        with mock.patch.dict(os.environ, {}, clear=True), self.assertRaises(SystemExit):
            clients.bot_from_env()


if __name__ == "__main__":
    unittest.main()
