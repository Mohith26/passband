import unittest

from server import ingest, search
from tests import support


class TokenizerTests(unittest.TestCase):
    def test_lowercases_and_splits(self):
        self.assertEqual(search.tokenize("Return Loss"), ["return", "loss"])

    def test_drops_stopwords_and_single_characters(self):
        self.assertEqual(search.tokenize("the a of gain"), ["gain"])

    def test_keeps_part_numbers_intact(self):
        self.assertIn("qpf4216", search.tokenize("See QPF4216 for details"))

    def test_keeps_internal_hyphens_and_dots(self):
        self.assertIn("s-parameter", search.tokenize("the s-parameter file"))
        self.assertIn("utf8mb4", search.tokenize("utf8mb4 collation"))

    def test_trailing_punctuation_is_trimmed(self):
        self.assertEqual(search.tokenize("gain, loss."), ["gain", "loss"])

    def test_term_frequencies_count_repeats(self):
        counts = search.term_frequencies("gain gain loss")
        self.assertEqual(counts["gain"], 2)
        self.assertEqual(counts["loss"], 1)

    def test_part_number_detector(self):
        self.assertTrue(search.looks_like_part_number("qpf4216"))
        self.assertTrue(search.looks_like_part_number("qpa9903"))
        self.assertFalse(search.looks_like_part_number("gain"))
        self.assertFalse(search.looks_like_part_number("50"))


class SearchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.store = support.fresh_store()
        ingest.ingest(cls.store, support.CORPUS)
        cls.searcher = search.Searcher(cls.store)

    def test_empty_query_returns_nothing(self):
        self.assertEqual(self.searcher.search(""), (0, []))
        self.assertEqual(self.searcher.search("the of a"), (0, []))

    def test_nonsense_query_returns_nothing(self):
        total, rows = self.searcher.search("zzzzqqqq")
        self.assertEqual(total, 0)
        self.assertEqual(rows, [])

    def test_part_number_query_ranks_that_part_first(self):
        total, rows = self.searcher.search("QPF4216")
        self.assertGreater(total, 0)
        self.assertEqual(rows[0]["part_number"], "QPF4216")
        # Every document for that part should outrank anything else.
        own = [r for r in rows if r["part_number"] == "QPF4216"]
        others = [r for r in rows if r["part_number"] != "QPF4216"]
        if others:
            self.assertGreater(min(r["score"] for r in own), max(r["score"] for r in others))

    def test_topic_query_finds_the_right_kind(self):
        total, rows = self.searcher.search("bias sequencing choke decouple")
        self.assertGreater(total, 0)
        self.assertEqual(rows[0]["kind"], "application note")

    def test_stability_margin_query_finds_errata(self):
        total, rows = self.searcher.search("stability margin mitigation bias resistor")
        self.assertGreater(total, 0)
        self.assertEqual(rows[0]["kind"], "errata")

    def test_calibration_query_finds_test_reports(self):
        total, rows = self.searcher.search("short open load thru calibration")
        self.assertGreater(total, 0)
        self.assertEqual(rows[0]["kind"], "test report")

    def test_results_carry_a_snippet_containing_a_query_term(self):
        _, rows = self.searcher.search("keepout stackup")
        self.assertTrue(rows)
        joined = rows[0]["snippet"].lower()
        self.assertTrue("keepout" in joined or "stackup" in joined)

    def test_scores_are_descending(self):
        _, rows = self.searcher.search("gain return loss", limit=20)
        scores = [r["score"] for r in rows]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_kind_filter_applies(self):
        _, rows = self.searcher.search("measured", kind="test report", limit=50)
        self.assertTrue(rows)
        self.assertTrue(all(r["kind"] == "test report" for r in rows))

    def test_family_filter_applies(self):
        _, rows = self.searcher.search("device", family="baw filter", limit=50)
        self.assertTrue(all(r["family"] == "baw filter" for r in rows))

    def test_paging_is_stable_and_non_overlapping(self):
        total, page1 = self.searcher.search("the device band", limit=5, offset=0)
        _, page2 = self.searcher.search("the device band", limit=5, offset=5)
        ids = [r["id"] for r in page1] + [r["id"] for r in page2]
        self.assertEqual(len(ids), len(set(ids)))
        _, again = self.searcher.search("the device band", limit=5, offset=0)
        self.assertEqual([r["id"] for r in page1], [r["id"] for r in again])

    def test_total_is_the_full_match_count_not_the_page_size(self):
        total, rows = self.searcher.search("the device band", limit=3)
        self.assertLessEqual(len(rows), 3)
        self.assertGreater(total, len(rows))

    def test_length_normalisation_does_not_let_long_documents_win_on_volume(self):
        # "errata" bodies are the shortest in the corpus. A query aimed squarely
        # at their content should still rank them top even though longer
        # documents contain more words overall.
        _, rows = self.searcher.search("date codes affected production lot")
        self.assertEqual(rows[0]["kind"], "errata")

    def test_ranking_quality_over_a_labelled_query_set(self):
        # Each query has one kind that is unambiguously the right answer.
        labelled = [
            ("recommended land pattern dielectric", "layout guide"),
            ("absolute maximum ratings package paddle", "datasheet"),
            ("vector network analyzer sweep corners", "test report"),
            ("quarter wave choke layout practice", "application note"),
            ("known issue upper band edge series resistor", "errata"),
        ]
        correct = 0
        for query, expected in labelled:
            _, rows = self.searcher.search(query, limit=1)
            if rows and rows[0]["kind"] == expected:
                correct += 1
        self.assertEqual(correct, len(labelled),
                         "precision at 1 was %d/%d" % (correct, len(labelled)))


if __name__ == "__main__":
    unittest.main()
