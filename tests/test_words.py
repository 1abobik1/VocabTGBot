import unittest
from datetime import datetime, timedelta, timezone

from shared import cards, srs
from shared import words as w
from shared.schedule import Schedule


def make(en, stage=0, box=0, **extra):
    word = w.new_word(en, en + "_ru")
    word["stage"], word["box"] = stage, box
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

    def test_notes_in_brackets_do_not_decide_the_language(self):
        en, ru, _ = w.parse_add_message("Плита - stove (амер.) / cooker (брит.) [stoʊv / ˈkʊkər]")
        self.assertEqual(en, "stove (амер.) / cooker (брит.) [stoʊv / ˈkʊkər]")
        self.assertEqual(ru, "Плита")
        en, ru, _ = w.parse_add_message("towel [ˈtaʊəl] - полотенце")
        self.assertEqual((en, ru), ("towel [ˈtaʊəl]", "полотенце"))

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

    def test_transcription_does_not_hide_duplicates(self):
        existing = w.new_word("towel [ˈtaʊəl]", "полотенце")
        self.assertIs(w.find_duplicate("towel", "полотенце", [existing], []), existing)
        self.assertEqual(w.compact("couch / sofa [kaʊtʃ / ˈsoʊfə]"), w.compact("couch/sofa"))

    def test_numbers(self):
        self.assertEqual(w.parse_numbers("2 5 7"), [2, 5, 7])
        self.assertEqual(w.parse_numbers("2, 5"), [2, 5])
        self.assertIsNone(w.parse_numbers("2 five"))
        self.assertIsNone(w.parse_numbers(""))


class TransitionsTest(unittest.TestCase):
    """Переходы слова: интервалы вместо позиции в очереди (см. shared/srs.py)."""

    def setUp(self):
        self.schedule = Schedule()
        self.now = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)
        self.today = "2026-09-24"

    def known(self, queue, practice, word, stage=None):
        return w.answer_known(queue, practice, word["id"], self.schedule, self.now, self.today, stage=stage)

    def unknown(self, queue, word, stage=None):
        return w.answer_unknown(queue, word["id"], self.schedule, self.now, self.today, stage=stage)

    def test_new_word_flips_to_the_second_side_immediately(self):
        a, b = make("a"), make("b")
        queue, practice = [a, b], []
        result, word = self.known(queue, practice, a)
        self.assertEqual(result, w.FLIPPED)
        self.assertEqual([x["en"] for x in queue], ["a", "b"])  # порядок больше ничего не решает
        self.assertEqual((word["stage"], word["box"]), (1, 0))
        self.assertEqual(word["due_at"], self.now.isoformat())  # вторая сторона сразу
        self.assertEqual(practice, [])

    def test_successful_reviews_grow_the_interval_then_go_to_practice(self):
        a = make("a", stage=1)
        queue, practice = [a], []
        result, word = self.known(queue, practice, a, stage=1)
        self.assertEqual(result, w.REVIEWED)
        self.assertEqual((word["box"], word["stage"]), (1, 0))
        self.assertEqual(word["due_at"], (self.now + timedelta(days=1)).isoformat())

        result, word = self.known(queue, practice, a, stage=0)
        self.assertEqual((result, word["box"]), (w.REVIEWED, 2))
        self.assertEqual(word["due_at"], (self.now + timedelta(days=3)).isoformat())

        result, word = self.known(queue, practice, a, stage=1)
        self.assertEqual(result, w.TO_PRACTICE)
        self.assertEqual(queue, [])
        self.assertEqual(practice, [word])
        self.assertEqual((word["stage"], word["practice_since"]), (2, self.now.isoformat()))

    def test_unknown_postpones_by_steps_and_keeps_the_direction(self):
        for stage in (0, 1):
            a = make("a", stage=stage, box=2)
            queue = [a]
            result, word = self.unknown(queue, a, stage=stage)
            self.assertEqual(result, w.REQUEUED)
            self.assertEqual([x["en"] for x in queue], ["a"])
            self.assertEqual((word["stage"], word["box"]), (stage, 0))
            self.assertEqual(word["due_at"], (self.now + timedelta(minutes=30)).isoformat())

            self.unknown(queue, a, stage=stage)
            self.assertEqual(word["due_at"], (self.now + timedelta(hours=2)).isoformat())

            self.unknown(queue, a, stage=stage)  # третий срыв подряд — завтра утром
            self.assertEqual(word["due_at"], "2026-09-25T10:00:00+03:00")

    def test_a_word_shown_three_times_today_waits_for_tomorrow(self):
        a = make("a")
        a["shows"] = {self.today: 3}
        queue = [a]
        _, word = self.unknown(queue, a)
        self.assertEqual(word["due_at"], "2026-09-25T10:00:00+03:00")
        self.assertFalse(srs.is_due(word, self.now, self.today))

    def test_archive_button_skips_the_remaining_steps(self):
        a, b = make("a"), make("b")
        queue, known = [a, b], []
        result, word = w.answer_archive(queue, known, a["id"], now="2026-09-20T10:00:00+00:00", stage=0)
        self.assertEqual(result, w.ARCHIVED)
        self.assertEqual([x["en"] for x in queue], ["b"])
        self.assertEqual(known, [word])
        self.assertEqual(word["archived_at"], "2026-09-20T10:00:00+00:00")
        # устаревшая кнопка (слово с тех пор развернулось) ничего не делает
        self.assertEqual(w.answer_archive(queue, known, b["id"], stage=1), (w.NOT_FOUND, None))
        self.assertEqual(len(known), 1)

    def test_stale_card(self):
        self.assertEqual(self.known([], [], {"id": "nope"}), (w.NOT_FOUND, None))
        self.assertEqual(self.unknown([], {"id": "nope"}), (w.NOT_FOUND, None))

    def test_restore_from_known(self):
        a = make("a", stage=1, archived_at="2026-09-01T00:00:00+00:00")
        b = make("b", stage=1, archived_at="2026-09-02T00:00:00+00:00")
        queue, known = [make("q")], [a, b]
        restored = w.restore_from_known(queue, known, [b["id"], "missing"], Schedule(), datetime(2026, 9, 24, tzinfo=timezone.utc))
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
        self.assertEqual([b["text"] for b in buttons], ["Знаю", "Не знаю", "📥 В архив"])
        self.assertEqual(buttons[2]["callback_data"], f"a:{self.word['id']}:0")
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
