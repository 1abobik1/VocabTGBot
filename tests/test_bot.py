import asyncio
import json
import unittest
from unittest import mock

from datetime import datetime, timedelta, timezone

from shared import cards
from shared import words as w
from shared.bot import Bot
from shared.schedule import Schedule

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


class FakeClock:
    """Управляемые часы: бот и shared.words берут время отсюда (см. use_clock)."""

    def __init__(self, moment):
        self.moment = moment

    def __call__(self):
        return self.moment

    def set(self, moment):
        self.moment = moment


def use_clock(test, bot, moment):
    """Привязывает бота и генерацию меток времени к управляемым часам."""
    clock = FakeClock(moment)
    bot.clock = clock
    patcher = mock.patch.object(w, "now_iso", lambda: clock().isoformat(timespec="seconds"))
    patcher.start()
    test.addCleanup(patcher.stop)
    return clock


def run(coro):
    return asyncio.run(coro)


class BotTest(unittest.TestCase):
    def setUp(self):
        self.store = FakeStore()
        self.tg = FakeTelegram()
        self.bot = Bot(self.store, self.tg, owner=OWNER, schedule=Schedule())
        self.clock = use_clock(self, self.bot, datetime(2026, 9, 21, 10, 0, tzinfo=timezone.utc))
        self.update_id = 0

    def advance(self, days=0, hours=0):
        self.clock.set(self.clock() + timedelta(days=days, hours=hours))

    def learn_until_practice(self, limit=60):
        """Прогоняет очередь по интервалам, отвечая «Знаю», пока слова не дойдут до практики."""
        for _ in range(limit):
            if not self.queue():
                return
            if any(x.get("sent_at") for x in self.queue()):
                self.press("k:" + self.last_card_word())  # ответ на уже присланную карточку
            elif not run(self.bot.broadcast_cards()):
                self.advance(days=1)
        raise AssertionError("слова так и не дошли до практики")

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

        # карточка по расписанию: первое новое слово, RU→EN
        self.assertEqual(run(self.bot.broadcast_cards()), [OWNER])
        card = self.tg.sent()[-1]
        self.assertTrue(card["text"].startswith("Яблоко - <tg-spoiler>apple</tg-spoiler>"))
        self.assertEqual(card["parse_mode"], "HTML")
        apple_id = self.last_card_word()
        self.assertEqual(self.queue()[0]["shown_count"], 1)

        # «Не знаю» — слово остаётся в очереди, но откладывается на 30 минут
        self.press("n:" + apple_id)
        apple = self.queue()[0]
        self.assertEqual((apple["en"], apple["stage"], apple["box"]), ("apple", 0, 0))
        self.assertEqual(apple["due_at"], (self.clock() + timedelta(minutes=30)).isoformat())
        self.assertIn(("editMessageReplyMarkup", {"chat_id": CHAT, "message_id": 77, "reply_markup": {"inline_keyboard": []}}), self.tg.calls)

        # «Знаю» на новом слове: вторая сторона приходит сразу
        run(self.bot.broadcast_cards())
        cat_id = self.last_card_word()
        self.press("k:" + cat_id)
        self.assertTrue(self.tg.sent()[-1]["text"].startswith("Cat - <tg-spoiler>кот</tg-spoiler>"))
        cat = next(x for x in self.queue() if x["en"] == "cat")
        self.assertEqual((cat["stage"], cat["box"]), (1, 0))

        # старая кнопка RU→EN для cat больше не работает
        self.press("k:" + cat_id)
        answers = [p for m, p in self.tg.calls if m == "answerCallbackQuery"]
        self.assertEqual(answers[-1]["text"], "Эта карточка уже неактуальна")

        # «Знаю» на второй стороне: слово уходит на повтор через сутки
        self.press(f"k:{cat['id']}:1")
        cat = next(x for x in self.queue() if x["en"] == "cat")
        self.assertEqual((cat["box"], cat["stage"]), (1, 0))
        self.assertEqual(cat["due_at"], (self.clock() + timedelta(days=1)).isoformat())
        answers = [p for m, p in self.tg.calls if m == "answerCallbackQuery"]
        self.assertIn("вернусь к слову позже", answers[-1]["text"])

        # ещё два успешных повтора — и слово ждёт практики
        self.advance(days=1)
        self.press(f"k:{cat['id']}:0")
        cat = next(x for x in self.queue() if x["en"] == "cat")
        self.assertEqual(cat["box"], 2)
        self.advance(days=3)
        self.press(f"k:{cat['id']}:1")
        self.assertEqual(self.known(), [])
        practice = self.store.json(f"practice:{OWNER.lower()}")
        self.assertEqual([(x["en"], x["stage"]) for x in practice], [("cat", 2)])
        self.assertNotIn("cat", [x["en"] for x in self.queue()])

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

    def test_archive_button(self):
        self.msg("/start")
        self.msg("apple - яблоко")
        self.msg("cat - кот")
        run(self.bot.broadcast_cards())
        self.press("a:" + self.last_card_word())
        self.assertEqual([w["en"] for w in self.known()], ["apple"])
        self.assertTrue(self.known()[0]["archived_at"])
        self.assertEqual([w["en"] for w in self.queue()], ["cat"])
        self.assertIsNone(self.store.json(f"practice:{OWNER.lower()}"))
        answers = [p for m, p in self.tg.calls if m == "answerCallbackQuery"]
        self.assertEqual(answers[-1]["text"], "📥 Слово сразу в архиве")
        # the archived word shows up in "Повтор архивных слов"
        self.msg(cards.REVIEW_BUTTON)
        self.assertIn("1) apple — яблоко", self.tg.last_text())

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

    def test_next_shows_a_card_even_before_it_is_due(self):
        self.msg("apple - яблоко")
        run(self.bot.broadcast_cards())
        self.press("k:" + self.last_card_word())       # RU→EN
        self.press("k:" + self.last_card_word())       # EN→RU, слово уходит на сутки
        self.assertIn("вернусь к слову позже", [p for m, p in self.tg.calls if m == "answerCallbackQuery"][-1]["text"])

        sent_before = len(self.tg.sent())
        self.msg(cards.NEXT_BUTTON)
        texts = [p["text"] for p in self.tg.sent()[sent_before:]]
        self.assertEqual(len(texts), 1)  # только карточка, без пояснений
        self.assertTrue(texts[0].startswith("Яблоко") or texts[0].startswith("Apple"), texts)
        # срок слова при этом не съезжает
        due = self.queue()[0]["due_at"]
        self.assertEqual(due, (self.clock() + timedelta(days=1)).isoformat())

    def test_review_flow(self):
        for i, word in enumerate(["one - один", "two - два", "three - три"]):
            self.msg(word)
        self.learn_until_practice()
        self.practise_all_correct()
        self.assertEqual(len(self.known()), 3)

        self.msg(cards.REVIEW_BUTTON)
        listing = self.tg.sent()[-1]
        order = [w["en"] for w in sorted(self.known(), key=lambda w: w["archived_at"], reverse=True)]
        self.assertIn(f"1) {order[0]}", listing["text"])
        self.assertIn(cards.ARCHIVE_PATH, listing["text"])
        self.assertEqual(listing["reply_markup"], cards.menu_inline_keyboard())

        self.msg("1 3 9")
        self.assertEqual([w["en"] for w in self.queue()], [order[0], order[2]])
        self.assertTrue(all(w["stage"] == 0 and w["box"] == 0 and w["archived_at"] is None for w in self.queue()))
        self.assertEqual([w["en"] for w in self.known()], [order[1]])
        self.assertIn("Нет таких номеров: 9", self.tg.last_text())

        # numbers outside review mode are treated as a (bad) word
        self.msg("2")
        self.assertIn("⚠️", self.tg.last_text())

    def test_menu_leaves_review_mode(self):
        self.msg("/review")
        self.assertIn("Архив пока пуст", self.tg.last_text())
        self.assertIn(cards.ARCHIVE_PATH, self.tg.last_text())
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
        self.learn_until_practice()
        self.practise_all_correct()
        self.assertEqual(run(self.bot.broadcast_stats()), [OWNER])
        text = self.tg.last_text()
        self.assertIn("Выучено слов: <b>1</b>", text)
        self.assertIn("4.0 дн.", text)  # сутки + три дня интервалов до практики
        self.assertIn("Новых слов сегодня: 0 из 6", text)

    def test_menu_buttons(self):
        self.msg("/start")
        self.msg(cards.HELP_BUTTON)
        help_text = self.tg.last_text()
        for button in (cards.NEXT_BUTTON, cards.REVIEW_BUTTON, cards.GENERATE_BUTTON, cards.PRACTICE_BUTTON, cards.STATS_BUTTON):
            self.assertIn(button, help_text)
        self.assertNotIn("/allow", str(cards.main_keyboard()))
        self.msg(cards.STATS_BUTTON)
        self.assertIn("Статистика за неделю", self.tg.last_text())
        self.msg(cards.PRACTICE_BUTTON)
        self.assertIn("пока нет слов", self.tg.last_text())
        self.msg(cards.REVIEW_BUTTON)
        self.assertIn("Архив пока пуст", self.tg.last_text())
        self.msg(cards.NEXT_BUTTON)
        self.assertIn("Очередь пуста", self.tg.last_text())

    def test_settings_menu(self):
        self.msg("/start")
        main = [b["text"] for row in cards.main_keyboard()["keyboard"] for b in row]
        self.assertEqual(main, [cards.NEXT_BUTTON, cards.PRACTICE_BUTTON, cards.GENERATE_BUTTON, cards.SETTINGS_BUTTON])
        self.msg(cards.SETTINGS_BUTTON)
        self.assertEqual(self.tg.sent()[-1]["reply_markup"], cards.settings_keyboard())
        inside = [b["text"] for row in cards.settings_keyboard()["keyboard"] for b in row]
        for button in (cards.LEVEL_BUTTON, cards.STATS_BUTTON, cards.REVIEW_BUTTON, cards.HELP_BUTTON, cards.BACK_BUTTON):
            self.assertIn(button, inside)
        self.msg(cards.BACK_BUTTON)
        self.assertEqual(self.tg.sent()[-1]["reply_markup"], cards.main_keyboard())

    def test_old_keyboard_buttons_still_work(self):
        self.msg("/start")
        self.msg("Повторить слова")
        self.assertIn("Архив пока пуст", self.tg.last_text())
        self.msg("Карточка сейчас")
        self.assertIn("Очередь пуста", self.tg.last_text())

    def test_outdated_keyboard_is_replaced_once(self):
        # a user who got the old keyboard before this version
        run(self.store.put("allowed_users", json.dumps([{"username": OWNER, "role": "owner", "chat_id": CHAT}])))
        self.msg("cat - кот")
        self.assertEqual(self.tg.sent()[-1]["reply_markup"], cards.main_keyboard())
        self.assertEqual(self.store.json(f"settings:{OWNER.lower()}"), {"keyboard": cards.KEYBOARD_VERSION})
        self.msg("dog - собака")
        self.assertNotIn("reply_markup", self.tg.sent()[-1])

    def test_unknown_command(self):
        self.msg("/foo")
        self.assertIn("Неизвестная команда", self.tg.last_text())


if __name__ == "__main__":
    unittest.main()
