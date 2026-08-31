"""Pulls a directory of engineering files into the database.

The rule that shapes this file: ingestion is idempotent. Running it twice over the
same corpus must leave the database in the same state as running it once, because
in practice you re-run it after fixing one bad file and nobody wants to rebuild
from scratch to do that. Every row is keyed on the file path, and a file whose
sha256 has not changed is skipped rather than rewritten.
"""

import hashlib
import json
import os
import time

from server import rfmath, search, touchstone


def sha256_of(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _split_name(filename):
    """QPF4216_revA.s2p -> (QPF4216, A). Returns (stem, None) if there is no
    revision marker, which is the case for loose documents."""
    stem = os.path.splitext(filename)[0]
    if "_rev" in stem:
        part, revision = stem.split("_rev", 1)
        return part, revision
    return stem, None


class Ingestor(object):
    def __init__(self, store):
        self.store = store
        self.stats = {
            "parts": 0, "revisions": 0, "documents": 0, "datasets": 0,
            "skipped": 0, "failed": 0, "terms_indexed": 0,
        }
        self.errors = []

    def run(self, corpus_dir):
        started = time.time()
        manifest_path = os.path.join(corpus_dir, "manifest.json")
        with open(manifest_path, "r") as handle:
            manifest = json.load(handle)

        part_ids = {}
        for part in manifest["parts"]:
            part_id = self.store.upsert_part(part["part_number"], part["family"],
                                             part.get("description"))
            part_ids[part["part_number"]] = part_id
            self.stats["parts"] += 1

        for entry in manifest["documents"]:
            self._ingest_document(corpus_dir, entry, part_ids)

        for entry in manifest["datasets"]:
            self._ingest_dataset(corpus_dir, entry, part_ids)

        self.store.commit()
        self.stats["seconds"] = round(time.time() - started, 4)
        return self.stats

    def _revision_id(self, part_id, revision):
        if revision is None:
            return None
        rid = self.store.upsert_revision(part_id, revision)
        self.stats["revisions"] += 1
        return rid

    def _ingest_document(self, corpus_dir, entry, part_ids):
        path = entry["path"]
        full = os.path.join(corpus_dir, path)
        try:
            with open(full, "r") as handle:
                body = handle.read()
        except OSError as exc:
            self.stats["failed"] += 1
            self.errors.append({"path": path, "error": str(exc)})
            return

        digest = sha256_of(body)
        existing = self.store.one("SELECT id, sha256 FROM documents WHERE path = ?", (path,))
        if existing and existing["sha256"] == digest:
            self.stats["skipped"] += 1
            return
        if existing:
            self.store.execute("DELETE FROM documents WHERE id = ?", (existing["id"],))

        part_id = part_ids[entry["part_number"]]
        _, revision = _split_name(os.path.basename(path))
        revision_id = self._revision_id(part_id, revision)

        doc_id = self.store.insert_document(
            part_id, revision_id, entry["kind"], entry["title"], body, path,
            digest, len(body.encode("utf-8")))

        # Title text is indexed alongside the body so a title-only hit is
        # findable; the extra weighting happens at query time.
        counts = search.term_frequencies(entry["title"] + "\n" + body)
        self.store.index_document(doc_id, counts)
        self.stats["terms_indexed"] += len(counts)

        self.store.add_tag(doc_id, entry["kind"].replace(" ", "-"))
        self.store.add_tag(doc_id, entry["part_number"].lower())
        self.stats["documents"] += 1

    def _ingest_dataset(self, corpus_dir, entry, part_ids):
        path = entry["path"]
        full = os.path.join(corpus_dir, path)
        try:
            with open(full, "r") as handle:
                text = handle.read()
            network = touchstone.parse(text, filename=full)
        except (OSError, touchstone.TouchstoneError) as exc:
            # A single unreadable measurement file must not abort the run. It is
            # recorded and reported at the end.
            self.stats["failed"] += 1
            self.errors.append({"path": path, "error": str(exc)})
            return

        digest = sha256_of(text)
        existing = self.store.one("SELECT id, sha256 FROM datasets WHERE path = ?", (path,))
        if existing and existing["sha256"] == digest:
            self.stats["skipped"] += 1
            return
        if existing:
            self.store.execute("DELETE FROM datasets WHERE id = ?", (existing["id"],))

        part_id = part_ids[entry["part_number"]]
        revision_id = self._revision_id(part_id, entry.get("revision"))
        summary = rfmath.summarize(network)
        start, stop = network.span()

        self.store.insert_dataset(
            part_id, revision_id, path, network.ports, network.options["format"],
            network.reference_impedance, start, stop, len(network), digest, summary)
        self.stats["datasets"] += 1


def ingest(store, corpus_dir):
    ing = Ingestor(store)
    stats = ing.run(corpus_dir)
    return stats, ing.errors


def main():
    import sys
    from server import store as store_mod

    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    corpus = os.path.join(here, "fixtures", "corpus")
    db_path = sys.argv[1] if len(sys.argv) > 1 else ":memory:"
    st = store_mod.sqlite_store(db_path)
    stats, errors = ingest(st, corpus)
    for key in sorted(stats):
        print("%-14s %s" % (key, stats[key]))
    if errors:
        print("errors:")
        for err in errors:
            print("  %s: %s" % (err["path"], err["error"]))
    print("counts %s" % (st.counts(),))


if __name__ == "__main__":
    main()
