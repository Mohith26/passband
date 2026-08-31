"""WSGI application. No framework, because the whole thing is a dozen routes and
a dependency would be more code than this file.

Design notes worth stating:

  * every JSON response carries an ETag derived from the body, and a matching
    If-None-Match gets a 304 with no body. The trace endpoints return a few
    hundred points each and the front end re-requests them every time a user
    toggles a curve, so this is the difference between a responsive chart and a
    laggy one.
  * measurement files are parsed on demand rather than stored as blobs. The
    parse is cheap and it means the numbers on screen always come from the file
    on disk, so a corrected file shows corrected numbers with no reindex.
  * every handler validates its query string and returns a 400 that names the
    offending parameter. A chart that silently renders nothing because of a typo
    in a URL is the worst possible failure mode for a documentation tool.
"""

import hashlib
import json
import math
import mimetypes
import os
import re

from server import rfmath, search, touchstone

STATIC_ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "web")

MAX_LIMIT = 200


class HttpError(Exception):
    def __init__(self, status, message, detail=None):
        super(HttpError, self).__init__(message)
        self.status = status
        self.message = message
        self.detail = detail


def _int_param(params, name, default, low, high):
    raw = params.get(name)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        raise HttpError(400, "parameter %s must be an integer" % name, {"got": raw})
    if value < low or value > high:
        raise HttpError(400, "parameter %s must be between %d and %d" % (name, low, high),
                        {"got": value})
    return value


def parse_query(query_string):
    """Small query string parser. Later duplicates win, values are unquoted."""
    out = {}
    if not query_string:
        return out
    for chunk in query_string.split("&"):
        if not chunk:
            continue
        if "=" in chunk:
            key, _, value = chunk.partition("=")
        else:
            key, value = chunk, ""
        out[_unquote(key)] = _unquote(value)
    return out


_PCT = re.compile(r"%([0-9a-fA-F]{2})")


def _unquote(text):
    text = text.replace("+", " ")
    return _PCT.sub(lambda m: chr(int(m.group(1), 16)), text)


class Application(object):
    def __init__(self, store, corpus_dir, static_root=STATIC_ROOT):
        self.store = store
        self.corpus_dir = corpus_dir
        self.static_root = static_root
        self.searcher = search.Searcher(store)
        self.routes = [
            (re.compile(r"^/api/health$"), self.health),
            (re.compile(r"^/api/facets$"), self.facets),
            (re.compile(r"^/api/parts$"), self.parts),
            (re.compile(r"^/api/parts/([A-Za-z0-9_-]+)$"), self.part_detail),
            (re.compile(r"^/api/documents$"), self.documents),
            (re.compile(r"^/api/documents/(\d+)$"), self.document_detail),
            (re.compile(r"^/api/search$"), self.search),
            (re.compile(r"^/api/datasets/(\d+)$"), self.dataset_detail),
            (re.compile(r"^/api/datasets/(\d+)/trace$"), self.dataset_trace),
        ]

    # ---- routes ----------------------------------------------------------

    def health(self, params):
        return {"status": "ok", "counts": self.store.counts()}

    def facets(self, params):
        return self.store.facets()

    def parts(self, params):
        return {"parts": self.store.parts()}

    def part_detail(self, params, part_number):
        part = self.store.part_by_number(part_number)
        if not part:
            raise HttpError(404, "no part named %s" % part_number)
        return {
            "part": part,
            "documents": self.store.documents_for_part(part["id"]),
            "datasets": self.store.datasets_for_part(part["id"]),
        }

    def documents(self, params):
        limit = _int_param(params, "limit", 25, 1, MAX_LIMIT)
        offset = _int_param(params, "offset", 0, 0, 10 ** 6)
        order = params.get("order", "title")
        try:
            total, rows = self.store.browse(
                kind=params.get("kind") or None,
                family=params.get("family") or None,
                part_number=params.get("part") or None,
                order=order, limit=limit, offset=offset)
        except Exception as exc:
            raise HttpError(400, str(exc))
        return {"total": total, "limit": limit, "offset": offset, "documents": rows}

    def document_detail(self, params, doc_id):
        doc = self.store.document(int(doc_id))
        if not doc:
            raise HttpError(404, "no document %s" % doc_id)
        doc["tags"] = self.store.tags_for_document(doc["id"])
        return doc

    def search(self, params):
        query = params.get("q", "").strip()
        if not query:
            raise HttpError(400, "parameter q is required")
        limit = _int_param(params, "limit", 20, 1, MAX_LIMIT)
        offset = _int_param(params, "offset", 0, 0, 10 ** 6)
        total, rows = self.searcher.search(
            query, kind=params.get("kind") or None, family=params.get("family") or None,
            limit=limit, offset=offset)
        return {"query": query, "total": total, "limit": limit, "offset": offset, "results": rows}

    def dataset_detail(self, params, dataset_id):
        row = self.store.dataset(int(dataset_id))
        if not row:
            raise HttpError(404, "no dataset %s" % dataset_id)
        return row

    def dataset_trace(self, params, dataset_id):
        row = self.store.dataset(int(dataset_id))
        if not row:
            raise HttpError(404, "no dataset %s" % dataset_id)
        network = self._network(row)
        i = _int_param(params, "i", 2 if network.ports >= 2 else 1, 1, network.ports)
        j = _int_param(params, "j", 1, 1, network.ports)
        kind = params.get("kind", "db")
        try:
            freqs, values = rfmath.trace(network, i, j, kind)
        except ValueError as exc:
            raise HttpError(400, str(exc))
        return {
            "dataset_id": row["id"],
            "parameter": "S%d%d" % (i, j),
            "kind": kind,
            "reference_impedance": row["reference_impedance"],
            "frequencies_hz": freqs,
            "values": values,
        }

    def _network(self, row):
        path = os.path.join(self.corpus_dir, row["path"])
        try:
            return touchstone.load(path)
        except OSError:
            raise HttpError(410, "measurement file for dataset %s is missing" % row["id"])
        except touchstone.TouchstoneError as exc:
            raise HttpError(500, "measurement file is unreadable", {"reason": str(exc)})

    # ---- WSGI ------------------------------------------------------------

    def __call__(self, environ, start_response):
        method = environ.get("REQUEST_METHOD", "GET").upper()
        path = environ.get("PATH_INFO", "/") or "/"
        params = parse_query(environ.get("QUERY_STRING", ""))

        if method not in ("GET", "HEAD"):
            return self._json(start_response, 405, {"error": "method not allowed"},
                              extra=[("Allow", "GET, HEAD")], method=method)

        if not path.startswith("/api/"):
            return self._static(start_response, path, method)

        for pattern, handler in self.routes:
            match = pattern.match(path)
            if match:
                try:
                    payload = handler(params, *match.groups())
                except HttpError as exc:
                    body = {"error": exc.message}
                    if exc.detail:
                        body["detail"] = exc.detail
                    return self._json(start_response, exc.status, body, method=method)
                return self._json(start_response, 200, payload,
                                  if_none_match=environ.get("HTTP_IF_NONE_MATCH"), method=method)

        return self._json(start_response, 404, {"error": "no route for " + path}, method=method)

    def _json(self, start_response, status, payload, if_none_match=None, extra=None, method="GET"):
        # A physically real infinity does exist here: a perfect open circuit has
        # an infinite standing wave ratio. It becomes JSON null so the chart
        # leaves a gap, rather than being clamped to a large number that would
        # read like a measurement. allow_nan stays False so anything this pass
        # misses still fails loudly instead of emitting invalid JSON.
        body = json.dumps(_finite(payload), sort_keys=True, allow_nan=False, default=_fallback)
        raw = body.encode("utf-8")
        etag = '"%s"' % hashlib.sha256(raw).hexdigest()[:32]
        if if_none_match and if_none_match == etag:
            start_response("304 Not Modified", [("ETag", etag)])
            return [b""]
        headers = [
            ("Content-Type", "application/json; charset=utf-8"),
            ("Content-Length", str(len(raw))),
            ("ETag", etag),
            ("Cache-Control", "no-cache"),
            ("X-Content-Type-Options", "nosniff"),
        ]
        if extra:
            headers.extend(extra)
        start_response("%d %s" % (status, STATUS_TEXT.get(status, "Unknown")), headers)
        return [b""] if method == "HEAD" else [raw]

    def _static(self, start_response, path, method):
        if path == "/":
            path = "/index.html"
        # Reject anything that tries to climb out of the web directory.
        clean = os.path.normpath(path).lstrip("/")
        full = os.path.normpath(os.path.join(self.static_root, clean))
        if not full.startswith(os.path.normpath(self.static_root) + os.sep):
            return self._json(start_response, 403, {"error": "forbidden"}, method=method)
        if not os.path.isfile(full):
            return self._json(start_response, 404, {"error": "not found"}, method=method)
        with open(full, "rb") as handle:
            raw = handle.read()
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript",):
            ctype += "; charset=utf-8"
        start_response("200 OK", [
            ("Content-Type", ctype),
            ("Content-Length", str(len(raw))),
            ("X-Content-Type-Options", "nosniff"),
        ])
        return [b""] if method == "HEAD" else [raw]


def _finite(value):
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: _finite(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_finite(v) for v in value]
    return value


def _fallback(value):
    if isinstance(value, complex):
        return {"re": value.real, "im": value.imag}
    raise TypeError("cannot serialize %r" % type(value))


STATUS_TEXT = {
    200: "OK", 304: "Not Modified", 400: "Bad Request", 403: "Forbidden",
    404: "Not Found", 405: "Method Not Allowed", 410: "Gone",
    500: "Internal Server Error",
}


def build(db_path=":memory:", corpus_dir=None):
    """Wire a ready to serve application. Used by both the test suite and the
    development server."""
    from server import ingest, store as store_mod

    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    corpus_dir = corpus_dir or os.path.join(here, "fixtures", "corpus")
    st = store_mod.sqlite_store(db_path)
    if (st.counts()["documents"] or 0) == 0:
        ingest.ingest(st, corpus_dir)
    return Application(st, corpus_dir)


def main():
    from wsgiref.simple_server import make_server
    port = int(os.environ.get("PORT", "8080"))
    app = build()
    print("passband listening on http://127.0.0.1:%d" % port)
    make_server("127.0.0.1", port, app).serve_forever()


if __name__ == "__main__":
    main()
