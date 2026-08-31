"""Storage layer over a DB-API 2.0 connection.

Two things this file is careful about.

First, placeholders. MariaDB drivers use %s and sqlite3 uses ?, and a codebase
that writes one of them everywhere quietly welds itself to a single database.
Every query here is written with ? and translated at the boundary, which is
mechanical enough to be safe and keeps the SQL readable.

Second, string formatting. No query is ever built by interpolating a value.
Where a variable number of placeholders is needed the placeholders themselves are
generated but the values still go through the driver. Column and table names that
do vary, such as an ORDER BY, are checked against an allow list first.
"""

import json
import re
import sqlite3

from server import schema

ORDERABLE = {
    "part_number": "p.part_number",
    "title": "d.title",
    "kind": "d.kind",
    "size": "d.byte_size",
    "id": "d.id",
}


class StoreError(RuntimeError):
    pass


def _translate(sql, paramstyle):
    if paramstyle == "qmark":
        return sql
    if paramstyle == "format":
        return sql.replace("?", "%s")
    raise StoreError("unsupported paramstyle %r" % paramstyle)


class Store(object):
    """Thin repository. Owns no connection pooling; the caller decides that."""

    def __init__(self, connection, dialect=schema.SQLITE, paramstyle=None):
        self.conn = connection
        self.dialect = dialect
        if paramstyle is None:
            paramstyle = "qmark" if dialect == schema.SQLITE else "format"
        self.paramstyle = paramstyle

    # ---- plumbing --------------------------------------------------------

    def execute(self, sql, params=()):
        cur = self.conn.cursor()
        cur.execute(_translate(sql, self.paramstyle), tuple(params))
        return cur

    def executemany(self, sql, seq):
        cur = self.conn.cursor()
        cur.executemany(_translate(sql, self.paramstyle), [tuple(p) for p in seq])
        return cur

    def query(self, sql, params=()):
        cur = self.execute(sql, params)
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, row)) for row in cur.fetchall()]

    def one(self, sql, params=()):
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def scalar(self, sql, params=()):
        cur = self.execute(sql, params)
        row = cur.fetchone()
        return None if row is None else row[0]

    def commit(self):
        self.conn.commit()

    def rollback(self):
        self.conn.rollback()

    def create_schema(self):
        for statement in schema.statements(self.dialect):
            try:
                self.execute(statement)
            except Exception as exc:
                # CREATE INDEX has no IF NOT EXISTS on older MariaDB. A duplicate
                # index is fine; anything else is a real problem.
                if "duplicate" in str(exc).lower() or "exists" in str(exc).lower():
                    continue
                raise
        self.commit()

    # ---- writes ----------------------------------------------------------

    def upsert_part(self, part_number, family, description=None):
        existing = self.one("SELECT id FROM parts WHERE part_number = ?", (part_number,))
        if existing:
            self.execute("UPDATE parts SET family = ?, description = ? WHERE id = ?",
                         (family, description, existing["id"]))
            return existing["id"]
        cur = self.execute(
            "INSERT INTO parts (part_number, family, description) VALUES (?, ?, ?)",
            (part_number, family, description))
        return cur.lastrowid

    def upsert_revision(self, part_id, revision, released_on=None):
        existing = self.one("SELECT id FROM revisions WHERE part_id = ? AND revision = ?",
                            (part_id, revision))
        if existing:
            return existing["id"]
        cur = self.execute(
            "INSERT INTO revisions (part_id, revision, released_on) VALUES (?, ?, ?)",
            (part_id, revision, released_on))
        return cur.lastrowid

    def insert_document(self, part_id, revision_id, kind, title, body, path, sha256, byte_size):
        cur = self.execute(
            "INSERT INTO documents (part_id, revision_id, kind, title, body, path, sha256, byte_size)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (part_id, revision_id, kind, title, body, path, sha256, byte_size))
        return cur.lastrowid

    def insert_dataset(self, part_id, revision_id, path, ports, data_format, reference_impedance,
                       f_start_hz, f_stop_hz, points, sha256, summary):
        cur = self.execute(
            "INSERT INTO datasets (part_id, revision_id, path, ports, data_format,"
            " reference_impedance, f_start_hz, f_stop_hz, points, sha256, summary_json)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (part_id, revision_id, path, ports, data_format, reference_impedance,
             f_start_hz, f_stop_hz, points, sha256, json.dumps(summary, sort_keys=True)))
        return cur.lastrowid

    def add_tag(self, document_id, name):
        row = self.one("SELECT id FROM tags WHERE name = ?", (name,))
        if row:
            tag_id = row["id"]
        else:
            tag_id = self.execute("INSERT INTO tags (name) VALUES (?)", (name,)).lastrowid
        try:
            self.execute("INSERT INTO document_tags (document_id, tag_id) VALUES (?, ?)",
                         (document_id, tag_id))
        except Exception:
            pass  # already tagged
        return tag_id

    def index_document(self, document_id, term_frequencies):
        self.execute("DELETE FROM doc_terms WHERE document_id = ?", (document_id,))
        self.executemany(
            "INSERT INTO doc_terms (document_id, term, tf) VALUES (?, ?, ?)",
            [(document_id, term, tf) for term, tf in sorted(term_frequencies.items())])
        total = sum(term_frequencies.values())
        self.execute("DELETE FROM doc_stats WHERE document_id = ?", (document_id,))
        self.execute("INSERT INTO doc_stats (document_id, term_count) VALUES (?, ?)",
                     (document_id, total))

    # ---- reads -----------------------------------------------------------

    def part_by_number(self, part_number):
        return self.one("SELECT * FROM parts WHERE part_number = ?", (part_number,))

    def parts(self):
        return self.query("SELECT * FROM parts ORDER BY part_number")

    def documents_for_part(self, part_id):
        return self.query(
            "SELECT d.*, r.revision FROM documents d"
            " LEFT JOIN revisions r ON r.id = d.revision_id"
            " WHERE d.part_id = ? ORDER BY d.kind, d.title", (part_id,))

    def datasets_for_part(self, part_id):
        rows = self.query(
            "SELECT s.*, r.revision FROM datasets s"
            " LEFT JOIN revisions r ON r.id = s.revision_id"
            " WHERE s.part_id = ? ORDER BY r.revision, s.path", (part_id,))
        for row in rows:
            row["summary"] = json.loads(row.pop("summary_json"))
        return rows

    def dataset(self, dataset_id):
        row = self.one("SELECT * FROM datasets WHERE id = ?", (dataset_id,))
        if row:
            row["summary"] = json.loads(row.pop("summary_json"))
        return row

    def document(self, document_id):
        return self.one(
            "SELECT d.*, p.part_number, r.revision FROM documents d"
            " JOIN parts p ON p.id = d.part_id"
            " LEFT JOIN revisions r ON r.id = d.revision_id"
            " WHERE d.id = ?", (document_id,))

    def tags_for_document(self, document_id):
        rows = self.query(
            "SELECT t.name FROM tags t JOIN document_tags dt ON dt.tag_id = t.id"
            " WHERE dt.document_id = ? ORDER BY t.name", (document_id,))
        return [r["name"] for r in rows]

    def counts(self):
        return {
            "parts": self.scalar("SELECT COUNT(*) FROM parts"),
            "documents": self.scalar("SELECT COUNT(*) FROM documents"),
            "datasets": self.scalar("SELECT COUNT(*) FROM datasets"),
            "terms": self.scalar("SELECT COUNT(*) FROM doc_terms"),
        }

    def facets(self):
        kinds = self.query("SELECT kind, COUNT(*) AS n FROM documents GROUP BY kind ORDER BY kind")
        families = self.query(
            "SELECT p.family AS family, COUNT(*) AS n FROM documents d"
            " JOIN parts p ON p.id = d.part_id GROUP BY p.family ORDER BY p.family")
        return {"kind": kinds, "family": families}

    def browse(self, kind=None, family=None, part_number=None, order="title",
               limit=25, offset=0):
        if order not in ORDERABLE:
            raise StoreError("cannot order by %r" % order)
        where = []
        params = []
        if kind:
            where.append("d.kind = ?")
            params.append(kind)
        if family:
            where.append("p.family = ?")
            params.append(family)
        if part_number:
            where.append("p.part_number = ?")
            params.append(part_number)
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        total = self.scalar(
            "SELECT COUNT(*) FROM documents d JOIN parts p ON p.id = d.part_id" + clause,
            params)
        rows = self.query(
            "SELECT d.id, d.kind, d.title, d.byte_size, p.part_number, p.family"
            " FROM documents d JOIN parts p ON p.id = d.part_id" + clause +
            " ORDER BY " + ORDERABLE[order] + ", d.id LIMIT ? OFFSET ?",
            params + [int(limit), int(offset)])
        return total, rows


def sqlite_store(path=":memory:"):
    conn = sqlite3.connect(path)
    # SQLite ignores foreign keys unless asked, and a test suite that does not
    # enforce them is weaker than the production database it stands in for.
    conn.execute("PRAGMA foreign_keys = ON")
    store = Store(conn, dialect=schema.SQLITE, paramstyle="qmark")
    store.create_schema()
    return store
