"""One schema definition, two dialects.

MariaDB is the target. SQLite is what the tests run against, because I wanted the
whole suite to run with nothing installed and no server to start. Keeping two
hand written .sql files in sync is exactly the kind of thing that rots, so the
tables are declared once here as data and each dialect is rendered from that.

Where the dialects genuinely differ the difference is explicit rather than hidden:

  * AUTO_INCREMENT vs INTEGER PRIMARY KEY AUTOINCREMENT
  * VARCHAR length is required on an indexed MariaDB column, SQLite ignores it
  * SQLite does not enforce foreign keys unless you turn them on per connection,
    which store.py does, otherwise the tests would be weaker than production
  * MariaDB gets an explicit InnoDB engine and utf8mb4 collation so that part
    numbers and document text behave the same as they do in the test database
"""

MARIADB = "mariadb"
SQLITE = "sqlite"
DIALECTS = (MARIADB, SQLITE)


def _pk(dialect):
    if dialect == MARIADB:
        return "BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY"
    return "INTEGER PRIMARY KEY AUTOINCREMENT"


def _fk_type(dialect):
    return "BIGINT UNSIGNED" if dialect == MARIADB else "INTEGER"


def _text(dialect):
    return "LONGTEXT" if dialect == MARIADB else "TEXT"


def _varchar(dialect, n):
    return "VARCHAR(%d)" % n if dialect == MARIADB else "TEXT"


def _double(dialect):
    return "DOUBLE" if dialect == MARIADB else "REAL"


def _int(dialect):
    return "INT" if dialect == MARIADB else "INTEGER"


TABLE_ORDER = [
    "parts",
    "revisions",
    "documents",
    "datasets",
    "tags",
    "document_tags",
    "doc_terms",
    "doc_stats",
]


def table_sql(name, dialect):
    pk = _pk(dialect)
    fk = _fk_type(dialect)
    text = _text(dialect)
    dbl = _double(dialect)
    integer = _int(dialect)
    suffix = " ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci" if dialect == MARIADB else ""

    if name == "parts":
        body = """
  id %(pk)s,
  part_number %(vc64)s NOT NULL,
  family %(vc128)s NOT NULL,
  description %(text)s NULL,
  UNIQUE (part_number)
""" % {"pk": pk, "vc64": _varchar(dialect, 64), "vc128": _varchar(dialect, 128), "text": text}

    elif name == "revisions":
        body = """
  id %(pk)s,
  part_id %(fk)s NOT NULL,
  revision %(vc16)s NOT NULL,
  released_on %(vc32)s NULL,
  UNIQUE (part_id, revision),
  FOREIGN KEY (part_id) REFERENCES parts(id) ON DELETE CASCADE
""" % {"pk": pk, "fk": fk, "vc16": _varchar(dialect, 16), "vc32": _varchar(dialect, 32)}

    elif name == "documents":
        body = """
  id %(pk)s,
  part_id %(fk)s NOT NULL,
  revision_id %(fk)s NULL,
  kind %(vc64)s NOT NULL,
  title %(vc255)s NOT NULL,
  body %(text)s NOT NULL,
  path %(vc512)s NOT NULL,
  sha256 %(vc64)s NOT NULL,
  byte_size %(int)s NOT NULL,
  UNIQUE (path),
  FOREIGN KEY (part_id) REFERENCES parts(id) ON DELETE CASCADE,
  FOREIGN KEY (revision_id) REFERENCES revisions(id) ON DELETE SET NULL
""" % {"pk": pk, "fk": fk, "vc64": _varchar(dialect, 64), "vc255": _varchar(dialect, 255),
       "vc512": _varchar(dialect, 512), "text": text, "int": integer}

    elif name == "datasets":
        body = """
  id %(pk)s,
  part_id %(fk)s NOT NULL,
  revision_id %(fk)s NULL,
  path %(vc512)s NOT NULL,
  ports %(int)s NOT NULL,
  data_format %(vc8)s NOT NULL,
  reference_impedance %(dbl)s NOT NULL,
  f_start_hz %(dbl)s NOT NULL,
  f_stop_hz %(dbl)s NOT NULL,
  points %(int)s NOT NULL,
  sha256 %(vc64)s NOT NULL,
  summary_json %(text)s NOT NULL,
  UNIQUE (path),
  FOREIGN KEY (part_id) REFERENCES parts(id) ON DELETE CASCADE,
  FOREIGN KEY (revision_id) REFERENCES revisions(id) ON DELETE SET NULL
""" % {"pk": pk, "fk": fk, "vc512": _varchar(dialect, 512), "vc8": _varchar(dialect, 8),
       "vc64": _varchar(dialect, 64), "dbl": dbl, "int": integer, "text": text}

    elif name == "tags":
        body = """
  id %(pk)s,
  name %(vc64)s NOT NULL,
  UNIQUE (name)
""" % {"pk": pk, "vc64": _varchar(dialect, 64)}

    elif name == "document_tags":
        body = """
  document_id %(fk)s NOT NULL,
  tag_id %(fk)s NOT NULL,
  PRIMARY KEY (document_id, tag_id),
  FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE,
  FOREIGN KEY (tag_id) REFERENCES tags(id) ON DELETE CASCADE
""" % {"fk": fk}

    elif name == "doc_terms":
        body = """
  document_id %(fk)s NOT NULL,
  term %(vc64)s NOT NULL,
  tf %(int)s NOT NULL,
  PRIMARY KEY (document_id, term),
  FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE
""" % {"fk": fk, "vc64": _varchar(dialect, 64), "int": integer}

    elif name == "doc_stats":
        body = """
  document_id %(fk)s NOT NULL,
  term_count %(int)s NOT NULL,
  PRIMARY KEY (document_id),
  FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE
""" % {"fk": fk, "int": integer}

    else:
        raise KeyError(name)

    return "CREATE TABLE IF NOT EXISTS %s (%s)%s;" % (name, body.rstrip() + "\n", suffix)


INDEXES = [
    ("idx_documents_part", "documents", "part_id"),
    ("idx_documents_kind", "documents", "kind"),
    ("idx_datasets_part", "datasets", "part_id"),
    ("idx_revisions_part", "revisions", "part_id"),
    ("idx_doc_terms_term", "doc_terms", "term"),
]


def index_sql(name, table, columns, dialect):
    if dialect == MARIADB:
        # MariaDB has no CREATE INDEX IF NOT EXISTS before 10.5, so the schema
        # applier tolerates the duplicate key error instead.
        return "CREATE INDEX %s ON %s (%s);" % (name, table, columns)
    return "CREATE INDEX IF NOT EXISTS %s ON %s (%s);" % (name, table, columns)


def statements(dialect):
    if dialect not in DIALECTS:
        raise ValueError("unknown dialect %r" % dialect)
    out = [table_sql(name, dialect) for name in TABLE_ORDER]
    out.extend(index_sql(n, t, c, dialect) for n, t, c in INDEXES)
    return out


def ddl(dialect):
    return "\n\n".join(statements(dialect)) + "\n"
