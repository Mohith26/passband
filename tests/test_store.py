import sqlite3
import unittest

from server import schema, store as store_mod
from tests import support


class SchemaTests(unittest.TestCase):
    def test_both_dialects_render_every_table(self):
        for dialect in schema.DIALECTS:
            ddl = schema.ddl(dialect)
            for table in schema.TABLE_ORDER:
                self.assertIn("CREATE TABLE IF NOT EXISTS %s" % table, ddl, dialect)

    def test_mariadb_gets_innodb_and_utf8mb4(self):
        ddl = schema.ddl(schema.MARIADB)
        self.assertIn("ENGINE=InnoDB", ddl)
        self.assertIn("utf8mb4", ddl)
        self.assertNotIn("AUTOINCREMENT", ddl, "that is the sqlite spelling")

    def test_sqlite_gets_no_engine_clause(self):
        ddl = schema.ddl(schema.SQLITE)
        self.assertNotIn("ENGINE=", ddl)
        self.assertIn("INTEGER PRIMARY KEY AUTOINCREMENT", ddl)

    def test_varchar_lengths_only_appear_in_mariadb(self):
        self.assertIn("VARCHAR(64)", schema.ddl(schema.MARIADB))
        self.assertNotIn("VARCHAR(", schema.ddl(schema.SQLITE))

    def test_foreign_keys_declared_in_both_dialects(self):
        for dialect in schema.DIALECTS:
            self.assertIn("FOREIGN KEY (part_id) REFERENCES parts(id)", schema.ddl(dialect))

    def test_unknown_dialect_is_rejected(self):
        with self.assertRaises(ValueError):
            schema.ddl("postgres")

    def test_mariadb_ddl_actually_parses_as_sql(self):
        # A cheap structural check: balanced parentheses and a terminator on
        # every statement. It will not catch a MariaDB-specific type error, and
        # the README says so, but it does catch a broken render.
        for statement in schema.statements(schema.MARIADB):
            self.assertTrue(statement.rstrip().endswith(";"), statement[:40])
            self.assertEqual(statement.count("("), statement.count(")"), statement[:40])


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.store = support.fresh_store()

    def test_schema_is_idempotent(self):
        self.store.create_schema()
        self.store.create_schema()
        self.assertEqual(self.store.counts()["parts"], 0)

    def test_upsert_part_is_stable(self):
        first = self.store.upsert_part("QPF4216", "front end module", "one")
        second = self.store.upsert_part("QPF4216", "front end module", "two")
        self.assertEqual(first, second)
        self.assertEqual(self.store.counts()["parts"], 1)
        self.assertEqual(self.store.part_by_number("QPF4216")["description"], "two")

    def test_upsert_revision_is_stable(self):
        part = self.store.upsert_part("QPF4216", "fem")
        a = self.store.upsert_revision(part, "A")
        b = self.store.upsert_revision(part, "A")
        self.assertEqual(a, b)

    def test_duplicate_path_is_rejected(self):
        part = self.store.upsert_part("X", "y")
        self.store.insert_document(part, None, "datasheet", "t", "b", "p/1.txt", "d" * 64, 1)
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.insert_document(part, None, "datasheet", "t", "b", "p/1.txt", "d" * 64, 1)

    def test_foreign_keys_are_enforced(self):
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.insert_document(9999, None, "datasheet", "t", "b", "p/2.txt", "d" * 64, 1)

    def test_cascade_delete_removes_children(self):
        part = self.store.upsert_part("X", "y")
        doc = self.store.insert_document(part, None, "k", "t", "b", "p/3.txt", "d" * 64, 1)
        self.store.index_document(doc, {"alpha": 2})
        self.store.execute("DELETE FROM parts WHERE id = ?", (part,))
        self.assertEqual(self.store.counts()["documents"], 0)
        self.assertEqual(self.store.counts()["terms"], 0)

    def test_rollback_undoes_writes(self):
        self.store.upsert_part("KEEP", "y")
        self.store.commit()
        self.store.upsert_part("DROP", "y")
        self.store.rollback()
        self.assertIsNotNone(self.store.part_by_number("KEEP"))
        self.assertIsNone(self.store.part_by_number("DROP"))

    def test_tags_are_deduplicated(self):
        part = self.store.upsert_part("X", "y")
        doc = self.store.insert_document(part, None, "k", "t", "b", "p/4.txt", "d" * 64, 1)
        self.store.add_tag(doc, "errata")
        self.store.add_tag(doc, "errata")
        self.assertEqual(self.store.tags_for_document(doc), ["errata"])

    def test_index_document_replaces_rather_than_appends(self):
        part = self.store.upsert_part("X", "y")
        doc = self.store.insert_document(part, None, "k", "t", "b", "p/5.txt", "d" * 64, 1)
        self.store.index_document(doc, {"alpha": 1, "beta": 2})
        self.store.index_document(doc, {"gamma": 3})
        terms = self.store.query("SELECT term FROM doc_terms WHERE document_id = ?", (doc,))
        self.assertEqual([t["term"] for t in terms], ["gamma"])
        self.assertEqual(self.store.scalar(
            "SELECT term_count FROM doc_stats WHERE document_id = ?", (doc,)), 3)

    def test_browse_rejects_an_unknown_sort_column(self):
        with self.assertRaises(store_mod.StoreError):
            self.store.browse(order="body; DROP TABLE parts")

    def test_placeholder_translation(self):
        sql = "SELECT * FROM parts WHERE part_number = ? AND family = ?"
        self.assertEqual(store_mod._translate(sql, "qmark"), sql)
        self.assertEqual(store_mod._translate(sql, "format").count("%s"), 2)
        with self.assertRaises(store_mod.StoreError):
            store_mod._translate(sql, "named")


class BrowseTests(unittest.TestCase):
    def setUp(self):
        self.store = support.fresh_store()
        from server import ingest
        ingest.ingest(self.store, support.CORPUS)

    def test_browse_pages_without_overlap(self):
        total, first = self.store.browse(limit=10, offset=0)
        _, second = self.store.browse(limit=10, offset=10)
        self.assertEqual(total, 40)
        ids = [r["id"] for r in first] + [r["id"] for r in second]
        self.assertEqual(len(ids), len(set(ids)))

    def test_browse_filters_compose(self):
        total_all, _ = self.store.browse()
        total_kind, rows = self.store.browse(kind="errata", limit=100)
        self.assertLess(total_kind, total_all)
        self.assertTrue(all(r["kind"] == "errata" for r in rows))
        total_both, rows2 = self.store.browse(kind="errata", family="baw filter", limit=100)
        self.assertLessEqual(total_both, total_kind)
        self.assertTrue(all(r["family"] == "baw filter" for r in rows2))

    def test_browse_offset_past_the_end_is_empty_not_an_error(self):
        total, rows = self.store.browse(limit=10, offset=10000)
        self.assertEqual(total, 40)
        self.assertEqual(rows, [])

    def test_facets_cover_every_document(self):
        facets = self.store.facets()
        self.assertEqual(sum(f["n"] for f in facets["kind"]), 40)
        self.assertEqual(sum(f["n"] for f in facets["family"]), 40)


if __name__ == "__main__":
    unittest.main()
