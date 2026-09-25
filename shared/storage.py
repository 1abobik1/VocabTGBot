"""Доступ к KV: JSON поверх строкового хранилища и список допущенных пользователей."""

import json

from .keys import ALLOWED_USERS_KEY


def normalize_username(name):
    return (name or "").strip().lstrip("@").lower()


def find_user(users, username):
    key = normalize_username(username)
    return next((user for user in users if normalize_username(user["username"]) == key), None)


class Repo:
    """`store` умеет `async get(key) -> str | None` и `async put(key, value: str)`."""

    def __init__(self, store):
        self.store = store

    async def get(self, key, default):
        raw = await self.store.get(key)
        return json.loads(raw) if raw else default

    async def put(self, key, value):
        await self.store.put(key, json.dumps(value, ensure_ascii=False))

    async def allowed_users(self, owner):
        users = await self.get(ALLOWED_USERS_KEY, None)
        if users is None:
            if not owner:
                return []  # владелец не настроен — никого не пускаем
            users = [{"username": owner, "role": "owner"}]
            await self.put(ALLOWED_USERS_KEY, users)
        return users
