"""Full text search over the document corpus, scored with BM25.

The index lives in the database rather than in memory, so search survives a
restart and there is exactly one copy of the truth. That costs a join per query
and buys not having to rebuild anything on boot.

BM25 rather than plain TF-IDF because document lengths here vary by a factor of
five or so between a one paragraph errata and a full application note, and BM25's
length normalisation is what stops the short ones from dominating every result.

Part numbers get special handling. A query like "QPF4216" has to match exactly and
rank above prose that merely mentions the family, so an exact part number match
adds a fixed boost on top of the text score.
"""

import math
import re

K1 = 1.5   # term frequency saturation
B = 0.75   # length normalisation strength
PART_BOOST = 12.0
TITLE_BOOST = 2.5

TOKEN = re.compile(r"[a-z0-9]+(?:[a-z0-9._-]*[a-z0-9])?")

# Words that appear in nearly every document in an RF corpus carry no signal and
# make the index bigger for nothing.
STOPWORDS = frozenset("""
a an the and or but if then than that this these those of in on at to for from by
with without into over under is are was were be been being it its as not no such
""".split())

PART_NUMBER = re.compile(r"^[a-z]{2,4}\d{3,5}[a-z]?$")


def tokenize(text):
    """Lowercase, split, drop stopwords and single characters.

    Hyphens and dots are kept inside a token so that part numbers and things like
    "s-parameter" or "utf8mb4" survive, but a trailing separator is trimmed.
    """
    out = []
    for match in TOKEN.finditer(text.lower()):
        token = match.group(0)
        if len(token) < 2:
            continue
        if token in STOPWORDS:
            continue
        out.append(token)
    return out


def term_frequencies(text):
    counts = {}
    for token in tokenize(text):
        counts[token] = counts.get(token, 0) + 1
    return counts


def looks_like_part_number(token):
    return bool(PART_NUMBER.match(token))


class Searcher(object):
    def __init__(self, store):
        self.store = store

    def _corpus_stats(self):
        total = self.store.scalar("SELECT COUNT(*) FROM doc_stats") or 0
        avg = self.store.scalar("SELECT AVG(term_count) FROM doc_stats") or 0.0
        return total, float(avg)

    def search(self, query, kind=None, family=None, limit=20, offset=0):
        terms = tokenize(query)
        if not terms:
            return 0, []

        total_docs, avg_len = self._corpus_stats()
        if total_docs == 0:
            return 0, []

        placeholders = ",".join("?" for _ in terms)
        rows = self.store.query(
            "SELECT dt.document_id AS doc, dt.term AS term, dt.tf AS tf, ds.term_count AS len"
            " FROM doc_terms dt JOIN doc_stats ds ON ds.document_id = dt.document_id"
            " WHERE dt.term IN (" + placeholders + ")", terms)

        df = {}
        for row in rows:
            df.setdefault(row["term"], set()).add(row["doc"])

        scores = {}
        for row in rows:
            n = len(df[row["term"]])
            # The +0.5 smoothing keeps the idf of a term present in every
            # document at a small positive number instead of a negative one.
            idf = math.log(1.0 + (total_docs - n + 0.5) / (n + 0.5))
            tf = float(row["tf"])
            length = float(row["len"]) or 1.0
            denom = tf + K1 * (1.0 - B + B * length / (avg_len or 1.0))
            scores[row["doc"]] = scores.get(row["doc"], 0.0) + idf * (tf * (K1 + 1.0)) / denom

        if not scores:
            return 0, []

        doc_ids = list(scores.keys())
        meta = self._metadata(doc_ids)

        part_terms = [t for t in terms if looks_like_part_number(t)]
        for doc_id, info in meta.items():
            if part_terms and info["part_number"].lower() in part_terms:
                scores[doc_id] += PART_BOOST
            title_tokens = set(tokenize(info["title"]))
            overlap = len([t for t in terms if t in title_tokens])
            if overlap:
                scores[doc_id] += TITLE_BOOST * overlap / float(len(terms))

        results = []
        for doc_id, score in scores.items():
            info = meta.get(doc_id)
            if info is None:
                continue
            if kind and info["kind"] != kind:
                continue
            if family and info["family"] != family:
                continue
            results.append({
                "id": doc_id,
                "score": round(score, 6),
                "title": info["title"],
                "kind": info["kind"],
                "part_number": info["part_number"],
                "family": info["family"],
                "snippet": self._snippet(info["body"], terms),
            })

        # Ties broken by id so that paging is stable across identical scores.
        results.sort(key=lambda r: (-r["score"], r["id"]))
        return len(results), results[offset:offset + limit]

    def _metadata(self, doc_ids):
        out = {}
        chunk = 400  # stay well under any driver's parameter limit
        for start in range(0, len(doc_ids), chunk):
            batch = doc_ids[start:start + chunk]
            placeholders = ",".join("?" for _ in batch)
            rows = self.store.query(
                "SELECT d.id, d.title, d.kind, d.body, p.part_number, p.family"
                " FROM documents d JOIN parts p ON p.id = d.part_id"
                " WHERE d.id IN (" + placeholders + ")", batch)
            for row in rows:
                out[row["id"]] = row
        return out

    def _snippet(self, body, terms, width=180):
        lowered = body.lower()
        best = -1
        for term in terms:
            idx = lowered.find(term)
            if idx >= 0 and (best < 0 or idx < best):
                best = idx
        if best < 0:
            return body[:width].strip()
        start = max(0, best - width // 3)
        end = min(len(body), start + width)
        prefix = "" if start == 0 else "..."
        suffix = "" if end >= len(body) else "..."
        return prefix + body[start:end].strip().replace("\n", " ") + suffix
