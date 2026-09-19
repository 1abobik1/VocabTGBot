import asyncio
import json
import unittest

from shared import cards
from shared.bot import Bot

OWNER = "OwnerUser"
CHAT = 1001


class FakeStore:
    def __init__(self):
        self.data = {}
        self.writes = 0

    async def get(self, key):
        return self.data.get(key)

    async def put(self, key, value):
        assert isinstance(value, str)
        self.writes += 1
        self.data[key] = value

    def json(self, key):
        return json.loads(self.data[key]) if key in self.data else None


class FakeTelegram:
    def __init__(self):
        self.calls = []

    async def call(self, method, payload):
        self.calls.append((method, payload))
        return {"ok": True, "result": {"message_id": len(self.calls)}}

    def sent(self):
        return [p for m, p in self.calls if m == "sendMessage"]

    def last_text(self):
        return self.sent()[-1]["text"]


def run(coro):
    return asyncio.run(coro)


class BotTest(unittest.TestCase):
    def setUp(self):
        self.store = FakeStore()
        self.tg = FakeTelegram()
        self.bot = Bot(self.store, self.tg, owner=OWNER)
        self.update_id = 0

    # helpers
    def msg(self, text, username=OWNER, chat_id=CHAT, chat_type="private"):
        self.update_id += 1
        run(
            self.bot.handle_update(
                {
                    "update_id": self.update_id,
                    "message": {
                        "message_id": self.update_id,
                        "from": {"id": chat_id, "username": username},
                        "chat": {"id": chat_id, "type": chat_type},
                        "text": text,
                    },
                }
            )
        )

    def press(self, data, username=OWNER, chat_id=CHAT, message_id=77):
        self.update_id += 1
        run(
            self.bot.handle_update(
                {
                    "update_id": self.update_id,
                    "callback_query": {
                        "id": f"cb{self.update_id}",
                        "from": {"id": chat_id, "username": username},
                        "message": {"message_id": message_id, "chat": {"id": chat_id, "type": "private"}},
                        "data": data,
                    },
                }
            )
        )

    def queue(self, user=OWNER.lower()):
        return self.store.json(f"queue:{user}") or []

    def known(self, user=OWNER.lower()):
        return self.store.json(f"known:{user}") or []

    def practise_all_correct(self):
        """Run /practice answering both rounds correctly and skipping the sentences."""
        self.msg("/practice")
        session = self.store.json(f"session:{OWNER.lower()}")
        by_id = {x["id"]: x for x in self.store.json(f"practice:{OWNER.lower()}")}
        words = [by_id[i] for i in session["ids"]]
        self.msg(", ".join(x["en"] for x in words))
        self.msg(", ".join(x["ru"] for x in words))
        if (self.store.json(f"session:{OWNER.lower()}") or {}).get("round") == "sentences":
            self.press("px")

    def last_card_word(self):
        """Callback suffix "<id>:<stage>" of the last card sent."""
        card = [p for p in self.tg.sent() if "inline_keyboard" in p.get("reply_markup", {})][-1]
        return card["reply_markup"]["inline_keyboard"][0][0]["callback_data"][2:]

    # tests
    def test_strangers_are_ignored_silently(self):
        self.msg("/start", username="stranger")
        self.msg("cat - кот", username=None)
        self.press("k:whatever", username="stranger")
        self.assertEqual(self.tg.calls, [])
        self.assertIsNone(self.store.json("queue:stranger"))

    def test_nobody_is_allowed_without_configured_owner(self):
        self.bot = Bot(self.store, self.tg, owner=None)
        self.msg("/start")
        self.assertEqual(self.tg.calls, [])
        self.assertIsNone(self.store.json("allowed_users"))

    def test_group_chats_are_ignored(self):
        self.msg("/start", chat_type="group")
        self.assertEqual(self.tg.calls, [])

    def test_owner_is_seeded_and_chat_id_remembered(self):
        self.msg("/start")
        self.assertEqual(self.store.json("allowed_users"), [{"username": OWNER, "role": "owner", "chat_id": CHAT}])
        self.assertIn("apple - яблоко", self.tg.last_text())
        self.assertEqual(self.tg.sent()[-1]["reply_markup"], cards.main_keyboard())

    def test_username_match_is_case_insensitive(self):
        self.msg("/start", username="ownerUSER")
        self.assertEqual(len(self.tg.sent()), 1)

    def test_add_word_and_validation(self):
        self.msg("apple - яблоко\nI ate an apple. - Я съел яблоко.")
        self.assertEqual(len(self.queue()), 1)
        self.assertEqual(self.queue()[0]["examples"], [{"en": "I ate an apple.", "ru": "Я съел яблоко."}])
        self.assertIn("Добавлено", self.tg.last_text())
        self.msg("Apple - яблоко")
        self.assertIn("уже в очереди", self.tg.last_text())
        self.msg("яблоко  -  a p p l e")
        self.assertEqual(self.tg.last_text(), "Не добавил: «apple — яблоко» уже в очереди.")
        self.msg("just text")
        self.assertIn("⚠️", self.tg.last_text())
        self.assertEqual(len(self.queue()), 1)

    def test_full_learning_cycle(self):
        for word in ["apple - яблоко", "cat - кот", "dog - собака"]:
            self.msg(word)

        # scheduled card: queue[0] = apple, stage 0
        self.assertEqual(run(self.bot.broadcast_cards()), [OWNER])
        card = self.tg.sent()[-1]
        self.assertTrue(card["text"].startswith("Яблоко - <tg-spoiler>apple</tg-spoiler>"))
        self.assertNotIn("<i>", card["text"])
        self.assertEqual(card["parse_mode"], "HTML")
        apple_id = self.last_card_word()
        self.assertEqual(self.queue()[0]["shown_count"], 1)

        # "Не знаю" -> index 2, stage stays 0
        self.press("n:" + apple_id)
        self.assertEqual([w["en"] for w in self.queue()], ["cat", "dog", "apple"])
        self.assertEqual(self.queue()[2]["stage"], 0)
        self.assertIn(("editMessageReplyMarkup", {"chat_id": CHAT, "message_id": 77, "reply_markup": {"inline_keyboard": []}}), self.tg.calls)

        # "Знаю" on stage 0 -> stage 1, to the end
        run(self.bot.broadcast_cards())
        cat_id = self.last_card_word()
        self.press("k:" + cat_id)
        self.assertEqual([w["en"] for w in self.queue()], ["dog", "apple", "cat"])
        self.assertEqual(self.queue()[2]["stage"], 1)

        # the old RU->EN card of cat is stale now: it must not archive the word
        self.press("k:" + cat_id)
        answers = [p for m, p in self.tg.calls if m == "answerCallbackQuery"]
        self.assertEqual(answers[-1]["text"], "Эта карточка уже неактуальна")
        self.assertEqual(self.known(), [])

        # "Знаю" on the EN->RU card sends it to the practice pool, not yet to the archive
        cat_word_id = cat_id.split(":")[0]
        self.press(f"k:{cat_word_id}:1")
        self.assertEqual(self.known(), [])
        practice = self.store.json(f"practice:{OWNER.lower()}")
        self.assertEqual([(w["en"], w["stage"]) for w in practice], [("cat", 2)])
        answers = [p for m, p in self.tg.calls if m == "answerCallbackQuery"]
        self.assertIn("субботней практики", answers[-1]["text"])
        self.press("k:" + cat_id)
        answers = [p for m, p in self.tg.calls if m == "answerCallbackQuery"]
        self.assertEqual(answers[-1]["text"], "Эта карточка уже неактуальна")
        self.assertEqual(len(practice), 1)

    def test_no_new_cards_until_answered(self):
        self.msg("/start")
        for word in ["apple - яблоко", "cat - кот"]:
            self.msg(word)
        self.assertEqual(run(self.bot.broadcast_cards()), [OWNER])
        first = self.last_card_word()
        self.assertTrue(self.queue()[0]["sent_at"])
        cards_before = len(self.tg.sent())
        self.assertEqual(run(self.bot.broadcast_cards()), [])  # still waiting for an answer
        self.assertEqual(len(self.tg.sent()), cards_before)

        self.press("n:" + first)
        self.assertFalse(any(w.get("sent_at") for w in self.queue()))
        self.assertEqual(run(self.bot.broadcast_cards()), [OWNER])
        self.assertTrue(self.tg.last_text().startswith("Кот"))

    def test_legacy_buttons_without_stage_still_work(self):
        self.msg("apple - яблоко")
        run(self.bot.broadcast_cards())
        self.press("k:" + self.last_card_word().split(":")[0])
        self.assertEqual(self.queue()[0]["stage"], 1)

    def test_stage1_card_is_reversed(self):
        self.msg("apple - яблоко\nI ate an apple. - Я съел яблоко.")
        run(self.bot.broadcast_cards())
        self.press("k:" + self.last_card_word())
        run(self.bot.broadcast_cards())
        text = self.tg.sent()[-1]["text"]
        self.assertEqual(text, "Apple - <tg-spoiler>яблоко</tg-spoiler>\n\nI ate an apple.\n<tg-spoiler>Я съел яблоко.</tg-spoiler>")

    def test_next_on_empty_queue(self):
        self.msg("/next")
        self.assertIn("Очередь пуста", self.tg.last_text())
        self.msg(cards.NEXT_BUTTON)
        self.assertIn("Очередь пуста", self.tg.last_text())

    def test_review_flow(self):
        for i, word in enumerate(["one - один", "two - два", "three - три"]):
            self.msg(word)
        for _ in range(6):  # each word needs two "Знаю", then the practice
            run(self.bot.broadcast_cards())
            self.press("k:" + self.last_card_word())
        self.assertEqual(self.queue(), [])
        self.practise_all_correct()
        self.assertEqual(len(self.known()), 3)

        self.msg(cards.REVIEW_BUTTON)
        listing = self.tg.sent()[-1]
        order = [w["en"] for w in sorted(self.known(), key=lambda w: w["archived_at"], reverse=True)]
        self.assertIn(f"1) {order[0]}", listing["text"])
        self.assertEqual(listing["reply_markup"], cards.menu_inline_keyboard())

        self.msg("1 3 9")
        self.assertEqual([w["en"] for w in self.queue()], [order[0], order[2]])
        self.assertTrue(all(w["stage"] == 0 and w["archived_at"] is None for w in self.queue()))
        self.assertEqual([w["en"] for w in self.known()], [order[1]])
        self.assertIn("Нет таких номеров: 9", self.tg.last_text())

        # numbers outside review mode are treated as a (bad) word
        self.msg("2")
        self.assertIn("⚠️", self.tg.last_text())

    def test_menu_leaves_review_mode(self):
        self.msg("/review")
        self.assertIn("Архив пока пуст", self.tg.last_text())
        self.press("menu")
        self.assertEqual(self.store.json(f"state:{OWNER.lower()}"), {})
        self.assertIn("Режим добавления слов", self.tg.last_text())

    def test_allow(self):
        self.msg("/start")
        self.msg("/allow @Friend")
        self.assertIn("получил доступ", self.tg.last_text())
        users = self.store.json("allowed_users")
        self.assertEqual(users[1], {"username": "Friend", "role": "full"})

        # friend has an independent queue and cannot manage access
        self.msg("/start", username="friend", chat_id=2002)
        self.msg("cat - кот", username="friend", chat_id=2002)
        self.assertEqual(len(self.queue("friend")), 1)
        self.assertEqual(self.queue(), [])
        self.msg("/allow @other", username="friend", chat_id=2002)
        self.assertIn("только владелец", self.tg.last_text())
        self.assertEqual(len(self.store.json("allowed_users")), 2)

        # scheduled cards go to every user with a non-empty queue
        self.msg("dog - собака")
        self.assertEqual(sorted(run(self.bot.broadcast_cards())), sorted([OWNER, "Friend"]))
        self.assertEqual(sorted(p["chat_id"] for p in self.tg.sent()[-2:]), [CHAT, 2002])

        self.msg("/allow friend")
        self.assertIn("уже есть", self.tg.last_text())
        self.msg("/allow")
        self.assertIn("Формат", self.tg.last_text())

    def test_weekly_stats(self):
        self.msg("/start")
        self.msg("apple - яблоко")
        for _ in range(2):
            run(self.bot.broadcast_cards())
            self.press("k:" + self.last_card_word())
        self.practise_all_correct()
        self.assertEqual(run(self.bot.broadcast_stats()), [OWNER])
        text = self.tg.last_text()
        self.assertIn("Выучено слов: <b>1</b>", text)
        self.assertIn("0.0 дн.", text)

    def test_unknown_command(self):
        self.msg("/foo")
        self.assertIn("Неизвестная команда", self.tg.last_text())


if __name__ == "__main__":
    unittest.main()
