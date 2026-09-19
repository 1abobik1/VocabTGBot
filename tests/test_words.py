import unittest
from datetime import datetime, timedelta, timezone

from shared import cards
from shared import words as w


def make(en, stage=0, **extra):
    word = w.new_word(en, en + "_ru")
    word["stage"] = stage
    word.update(extra)
    return word


class ParseTest(unittest.TestCase):
    def test_word_with_examples(self):
        en, ru, examples = w.parse_add_message(
            "apple - яблоко\nI ate an apple. - Я съел яблоко.\n\nAn apple a day... - Одно яблоко в день...\n"
        )
        self.assertEqual((en, ru), ("apple", "яблоко"))
        self.assertEqual(
            examples,
            [
                {"en": "I ate an apple.", "ru": "Я съел яблоко."},
                {"en": "An apple a day...", "ru": "Одно яблоко в день..."},
            ],
        )

    def test_splits_on_first_separator_only(self):
        en, ru, _ = w.parse_add_message("well-being - благо - получие")
        self.assertEqual((en, ru), ("well-being", "благо - получие"))

    def test_hyphenated_words_and_russian_first(self):
        en, ru, examples = w.parse_add_message(
            "Куда-то - somewhere\nСмотри! Он куда-то бежит. - Look! He's running somewhere"
        )
        self.assertEqual((en, ru), ("somewhere", "Куда-то"))
        self.assertEqual(examples, [{"en": "Look! He's running somewhere", "ru": "Смотри! Он куда-то бежит."}])
        # mixed order between lines is fine too
        _, _, examples = w.parse_add_message("well-being - благополучие\nЭто важно - It matters")
        self.assertEqual(examples, [{"en": "It matters", "ru": "Это важно"}])

    def test_dashes_are_equivalent(self):
        for text in ["apple - яблоко", "apple — яблоко", "apple – яблоко", "яблоко — apple", "apple   —   яблоко"]:
            self.assertEqual(w.parse_add_message(text)[:2], ("apple", "яблоко"), text)
        with self.assertRaises(w.ParseError):
            w.parse_add_message("apple—яблоко")  # needs spaces around the dash

    def test_dash_inside_russian_sentence(self):
        _, _, examples = w.parse_add_message(
            "capital — столица\nМосква — столица России. — Moscow is the capital of Russia."
        )
        self.assertEqual(examples, [{"en": "Moscow is the capital of Russia.", "ru": "Москва — столица России."}])
        _, _, examples = w.parse_add_message("capital - столица\nMoscow is the capital. - Москва — столица.")
        self.assertEqual(examples, [{"en": "Moscow is the capital.", "ru": "Москва — столица."}])

    def test_word_without_examples(self):
        self.assertEqual(w.parse_add_message("cat - кот"), ("cat", "кот", []))

    def test_errors(self):
        for text in ["", "apple", "apple - ", " - яблоко", "apple-яблоко", "cat - кот\nbad example line"]:
            with self.subTest(text=text), self.assertRaises(w.ParseError):
                w.parse_add_message(text)

    def test_duplicates_ignore_whitespace_case_and_order(self):
        existing = w.new_word("look up", "искать")
        queue, known = [existing], []
        for text in ["look up - искать", "lookup - искать", "  Look   Up  -  Искать ", "искать - look up"]:
            en, ru, _ = w.parse_add_message(text)
            self.assertIs(w.find_duplicate(en, ru, queue, known), existing, text)
        # same English word with another meaning is a different card
        self.assertIsNone(w.find_duplicate("look up", "посмотреть наверх", queue, known))
        self.assertIsNone(w.find_duplicate("look out", "искать", queue, known))
        # archived words count too
        self.assertIs(w.find_duplicate("lookup", "искать", [], [existing]), existing)

    def test_numbers(self):
        self.assertEqual(w.parse_numbers("2 5 7"), [2, 5, 7])
        self.assertEqual(w.parse_numbers("2, 5"), [2, 5])
        self.assertIsNone(w.parse_numbers("2 five"))
        self.assertIsNone(w.parse_numbers(""))


class TransitionsTest(unittest.TestCase):
    def test_known_stage0_flips_and_moves_to_end(self):
        a, b, c = make("a"), make("b"), make("c")
        queue, known = [a, b, c], []
        result, word = w.answer_known(queue, known, a["id"])
        self.assertEqual(result, w.FLIPPED)
        self.assertEqual([x["en"] for x in queue], ["b", "c", "a"])
        self.assertEqual(word["stage"], 1)
        self.assertEqual(known, [])

    def test_known_stage1_goes_to_practice(self):
        a, b = make("a", stage=1), make("b")
        queue, practice = [a, b], []
        result, word = w.answer_known(queue, practice, a["id"], now="2026-09-19T10:00:00+00:00")
        self.assertEqual(result, w.TO_PRACTICE)
        self.assertEqual([x["en"] for x in queue], ["b"])
        self.assertEqual(practice, [word])
        self.assertEqual((word["stage"], word["practice_since"], word["archived_at"]), (2, "2026-09-19T10:00:00+00:00", None))

    def test_unknown_goes_to_third_position_and_keeps_stage(self):
        for stage in (0, 1):
            queue = [make("a", stage=stage), make("b"), make("c"), make("d")]
            result, word = w.answer_unknown(queue, queue[0]["id"])
            self.assertEqual(result, w.REQUEUED)
            self.assertEqual([x["en"] for x in queue], ["b", "c", "a", "d"])
            self.assertEqual(word["stage"], stage)

    def test_unknown_in_short_queue(self):
        queue = [make("a"), make("b")]
        w.answer_unknown(queue, queue[0]["id"])
        self.assertEqual([x["en"] for x in queue], ["b", "a"])
        queue = [make("a")]
        w.answer_unknown(queue, queue[0]["id"])
        self.assertEqual([x["en"] for x in queue], ["a"])

    def test_stale_card(self):
        self.assertEqual(w.answer_known([], [], "nope"), (w.NOT_FOUND, None))
        self.assertEqual(w.answer_unknown([], "nope"), (w.NOT_FOUND, None))

    def test_restore_from_known(self):
        a = make("a", stage=1, archived_at="2026-09-01T00:00:00+00:00")
        b = make("b", stage=1, archived_at="2026-09-02T00:00:00+00:00")
        queue, known = [make("q")], [a, b]
        restored = w.restore_from_known(queue, known, [b["id"], "missing"])
        self.assertEqual(restored, [b])
        self.assertEqual([x["en"] for x in queue], ["q", "b"])
        self.assertEqual(known, [a])
        self.assertEqual((b["stage"], b["archived_at"]), (0, None))

    def test_known_newest_first(self):
        a = make("a", archived_at="2026-09-01T00:00:00+00:00")
        b = make("b", archived_at="2026-09-03T00:00:00+00:00")
        c = make("c", archived_at="2026-09-02T00:00:00+00:00")
        self.assertEqual([x["en"] for x in w.known_newest_first([a, b, c])], ["b", "c", "a"])

    def test_weekly_stats(self):
        now = datetime(2026, 9, 20, 18, 0, tzinfo=timezone.utc)
        iso = lambda d: (now - timedelta(days=d)).isoformat()  # noqa: E731
        known = [
            make("a", added_at=iso(5), archived_at=iso(1)),  # 4 days
            make("b", added_at=iso(10), archived_at=iso(2)),  # 8 days
            make("c", added_at=iso(30), archived_at=iso(8)),  # older than a week
        ]
        stats = w.weekly_stats(known, now=now)
        self.assertEqual(stats["archived"], 2)
        self.assertAlmostEqual(stats["avg_days"], 6.0)
        self.assertEqual(stats["total_known"], 3)
        self.assertIsNone(w.weekly_stats([], now=now)["avg_days"])


class CardTest(unittest.TestCase):
    def setUp(self):
        self.word = w.new_word(
            "apple",
            "яблоко",
            [{"en": "I ate an apple.", "ru": "Я съел яблоко."}, {"en": "An apple a day...", "ru": "Одно яблоко в день..."}],
        )

    def test_stage0_first_show(self):
        self.assertEqual(
            cards.render_card(self.word),
            "Яблоко - <tg-spoiler>apple</tg-spoiler>\n\n"
            "<tg-spoiler>I ate an apple.</tg-spoiler>\nЯ съел яблоко.",
        )

    def test_stage1_reversed_with_rotating_example(self):
        self.word.update(stage=1, shown_count=3)
        self.assertEqual(
            cards.render_card(self.word),
            "Apple - <tg-spoiler>яблоко</tg-spoiler>\n\nAn apple a day...\n<tg-spoiler>Одно яблоко в день...</tg-spoiler>",
        )

    def test_no_examples_and_html_escaping(self):
        word = w.new_word("<b>&", "a<i>")
        self.assertEqual(cards.render_card(word), "A&lt;i&gt; - <tg-spoiler>&lt;b&gt;&amp;</tg-spoiler>")

    def test_keyboard(self):
        buttons = cards.card_keyboard(self.word)["inline_keyboard"][0]
        self.assertEqual([b["text"] for b in buttons], ["Знаю", "Не знаю"])
        self.assertEqual(buttons[0]["callback_data"], f"k:{self.word['id']}:0")
        self.assertEqual(buttons[1]["callback_data"], f"n:{self.word['id']}:0")
        self.word["stage"] = 1
        self.assertEqual(cards.card_keyboard(self.word)["inline_keyboard"][0][0]["callback_data"], f"k:{self.word['id']}:1")
        self.assertLessEqual(len(buttons[1]["callback_data"].encode()), 64)

    def test_chunk_lines(self):
        lines = [f"{i}) " + "x" * 100 for i in range(100)]
        chunks = cards.chunk_lines(lines, header="H", limit=1000)
        self.assertTrue(all(len(c) <= 1000 for c in chunks))
        self.assertEqual("\n".join(chunks), "\n".join(["H"] + lines))


if __name__ == "__main__":
    unittest.main()
