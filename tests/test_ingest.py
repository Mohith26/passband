import json
import os
import shutil
import tempfile
import unittest

from server import ingest
from tests import support


class IngestTests(unittest.TestCase):
    def setUp(self):
        self.store = support.fresh_store()

    def test_first_run_loads_everything(self):
        stats, errors = ingest.ingest(self.store, support.CORPUS)
        self.assertEqual(errors, [])
        self.assertEqual(stats["parts"], 8)
        self.assertEqual(stats["documents"], 40)
        self.assertEqual(stats["datasets"], 16)
        self.assertEqual(stats["failed"], 0)

    def test_second_run_skips_everything_unchanged(self):
        ingest.ingest(self.store, support.CORPUS)
        stats, _ = ingest.ingest(self.store, support.CORPUS)
        self.assertEqual(stats["documents"], 0, "nothing should be re-inserted")
        self.assertEqual(stats["datasets"], 0)
        self.assertEqual(stats["skipped"], 56)
        counts = self.store.counts()
        self.assertEqual(counts["documents"], 40)
        self.assertEqual(counts["datasets"], 16)

    def test_reingest_is_idempotent_at_the_row_level(self):
        ingest.ingest(self.store, support.CORPUS)
        before = self.store.counts()
        for _ in range(3):
            ingest.ingest(self.store, support.CORPUS)
        self.assertEqual(self.store.counts(), before)


class ChangedCorpusTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.corpus = os.path.join(self.dir, "corpus")
        shutil.copytree(support.CORPUS, self.corpus)
        self.store = support.fresh_store()

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _manifest(self):
        with open(os.path.join(self.corpus, "manifest.json")) as handle:
            return json.load(handle)

    def test_an_edited_document_is_reindexed(self):
        ingest.ingest(self.store, self.corpus)
        entry = self._manifest()["documents"][0]
        path = os.path.join(self.corpus, entry["path"])
        with open(path, "a") as handle:
            handle.write("\n\nAdded paragraph about thermal derating and heatsink area.\n")

        stats, _ = ingest.ingest(self.store, self.corpus)
        self.assertEqual(stats["documents"], 1)
        self.assertEqual(self.store.counts()["documents"], 40, "must replace, not duplicate")

        from server import search
        total, rows = search.Searcher(self.store).search("thermal derating heatsink")
        self.assertGreater(total, 0)

    def test_a_corrupt_measurement_file_is_reported_and_the_rest_survives(self):
        entry = self._manifest()["datasets"][0]
        path = os.path.join(self.corpus, entry["path"])
        with open(path, "w") as handle:
            handle.write("# GHZ S RI\n1 not-a-number 0 0 0 0 0 0 0\n")

        stats, errors = ingest.ingest(self.store, self.corpus)
        self.assertEqual(stats["failed"], 1)
        self.assertEqual(len(errors), 1)
        self.assertIn(entry["path"], errors[0]["path"])
        self.assertEqual(stats["datasets"], 15, "the other fifteen still load")
        self.assertEqual(stats["documents"], 40, "documents are unaffected")

    def test_a_missing_document_is_reported_not_fatal(self):
        entry = self._manifest()["documents"][0]
        os.remove(os.path.join(self.corpus, entry["path"]))
        stats, errors = ingest.ingest(self.store, self.corpus)
        self.assertEqual(stats["failed"], 1)
        self.assertEqual(stats["documents"], 39)

    def test_summary_is_recomputed_not_carried_over(self):
        ingest.ingest(self.store, self.corpus)
        part = self.store.part_by_number("QPA9903")
        before = self.store.datasets_for_part(part["id"])[0]["summary"]["max_gain_db"]

        # Halve every S21 magnitude by rewriting the file through the parser.
        from server import touchstone
        entry = [d for d in self._manifest()["datasets"]
                 if d["part_number"] == "QPA9903" and d["revision"] == "A"][0]
        path = os.path.join(self.corpus, entry["path"])
        net = touchstone.load(path)
        for matrix in net.matrices:
            matrix[1][0] = matrix[1][0] * 0.5
        with open(path, "w") as handle:
            handle.write(touchstone.dumps(net, fmt="MA"))

        ingest.ingest(self.store, self.corpus)
        after = self.store.datasets_for_part(part["id"])[0]["summary"]["max_gain_db"]
        self.assertAlmostEqual(after, before - 6.0206, places=2)


if __name__ == "__main__":
    unittest.main()
