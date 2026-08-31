"""Benchmarks. Every number in the README comes from here.

Two rules I held to. Timings are medians and percentiles over many repeats, never
a single run, because a single run of anything this fast is mostly noise. And
every measurement says what it measured on, since the interpreter and machine
matter more than the code for numbers at this scale.
"""

import json
import os
import platform
import statistics
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from server import api, ingest, rfmath, search, store as store_mod, touchstone

CORPUS = os.path.join(ROOT, "fixtures", "corpus")
RESULTS = os.path.join(ROOT, "results")


def clock_resolution():
    """Smallest non zero gap perf_counter will report.

    This matters more than usual here. Browser hosted runtimes clamp their timers
    as a side channel defence, so a benchmark can quietly report a floor value
    instead of a measurement. Measuring the floor means the write up can say
    which numbers are real and which are just the clock.
    """
    gaps = []
    for _ in range(2000):
        a = time.perf_counter()
        b = time.perf_counter()
        while b == a:
            b = time.perf_counter()
        gaps.append(b - a)
    return {
        "min_gap_ms": round(min(gaps) * 1000.0, 6),
        "median_gap_ms": round(statistics.median(gaps) * 1000.0, 6),
    }


def percentiles(samples):
    ordered = sorted(samples)
    def at(p):
        if not ordered:
            return None
        idx = min(len(ordered) - 1, int(p / 100.0 * len(ordered)))
        return round(ordered[idx] * 1000.0, 4)
    return {
        "n": len(ordered),
        "p50_ms": at(50), "p90_ms": at(90), "p99_ms": at(99),
        "min_ms": round(ordered[0] * 1000.0, 4) if ordered else None,
        "max_ms": round(ordered[-1] * 1000.0, 4) if ordered else None,
        "mean_ms": round(statistics.fmean(ordered) * 1000.0, 4) if ordered else None,
    }


def timed(fn, repeats):
    samples = []
    for _ in range(repeats):
        start = time.perf_counter()
        fn()
        samples.append(time.perf_counter() - start)
    return samples


def bench_parser():
    ts_dir = os.path.join(CORPUS, "touchstone")
    names = sorted(os.listdir(ts_dir))
    blobs = []
    total_points = 0
    for name in names:
        with open(os.path.join(ts_dir, name)) as handle:
            text = handle.read()
        blobs.append((name, text))
        total_points += len(touchstone.parse(text, filename=name))

    def parse_all():
        for name, text in blobs:
            touchstone.parse(text, filename=name)

    samples = timed(parse_all, 20)
    median = statistics.median(samples)
    return {
        "files": len(blobs),
        "frequency_points": total_points,
        "per_sweep_ms": percentiles([s / len(blobs) for s in samples]),
        "points_per_second": int(total_points / median),
        "sweeps_per_second": round(len(blobs) / median, 1),
    }


def bench_rfmath():
    net = touchstone.load(os.path.join(CORPUS, "touchstone", "QPA9903_revA.s2p"))
    summary_samples = timed(lambda: rfmath.summarize(net), 200)
    delay_samples = timed(lambda: rfmath.group_delay_s(net.frequencies, net.s(2, 1)), 500)
    return {
        "points": len(net),
        "summarize_ms": percentiles(summary_samples),
        "group_delay_ms": percentiles(delay_samples),
    }


def bench_ingest():
    samples = []
    stats = None
    for _ in range(10):
        st = store_mod.sqlite_store(":memory:")
        start = time.perf_counter()
        stats, errors = ingest.ingest(st, CORPUS)
        samples.append(time.perf_counter() - start)
        assert not errors, errors
    median = statistics.median(samples)
    documents = stats["documents"]
    datasets = stats["datasets"]

    st = store_mod.sqlite_store(":memory:")
    ingest.ingest(st, CORPUS)
    reingest = timed(lambda: ingest.ingest(st, CORPUS), 10)

    return {
        "documents": documents,
        "datasets": datasets,
        "terms_indexed": stats["terms_indexed"],
        "full_ingest_ms": percentiles(samples),
        "documents_per_second": int(documents / median),
        "files_per_second": int((documents + datasets) / median),
        "unchanged_reingest_ms": percentiles(reingest),
        "reingest_speedup": round(statistics.median(samples) / statistics.median(reingest), 1),
    }


QUERIES = [
    "QPF4216", "return loss", "bias sequencing choke", "stability margin",
    "calibration sweep corners", "land pattern dielectric", "gain flatness",
    "thermal paddle solder", "band edge mitigation", "network analyzer",
]


def bench_search():
    st = store_mod.sqlite_store(":memory:")
    ingest.ingest(st, CORPUS)
    searcher = search.Searcher(st)
    samples = []
    hits = 0
    for query in QUERIES:
        for _ in range(30):
            start = time.perf_counter()
            total, rows = searcher.search(query, limit=20)
            samples.append(time.perf_counter() - start)
        hits += total
    tokenize_samples = timed(lambda: [search.tokenize(q) for q in QUERIES], 500)
    return {
        "queries": len(QUERIES),
        "total_hits": hits,
        "query_ms": percentiles(samples),
        "tokenize_batch_ms": percentiles(tokenize_samples),
        "index_terms": st.counts()["terms"],
    }


def bench_api():
    from tests import support
    app = api.build(db_path=":memory:", corpus_dir=CORPUS)
    client = support.Client(app)
    part = client.get("/api/parts/QPA9903").json()
    ds_id = part["datasets"][0]["id"]

    endpoints = {
        "health": "/api/health",
        "facets": "/api/facets",
        "documents_page": "/api/documents?limit=25",
        "search": "/api/search?q=stability+margin",
        "part_detail": "/api/parts/QPA9903",
        "dataset_detail": "/api/datasets/%d" % ds_id,
        "trace_db": "/api/datasets/%d/trace?i=2&j=1&kind=db" % ds_id,
        "trace_group_delay": "/api/datasets/%d/trace?i=2&j=1&kind=group_delay" % ds_id,
    }

    out = {}
    for name, path in endpoints.items():
        res = client.get(path)
        assert res.status_code == 200, (path, res.status_code)
        out[name] = percentiles(timed(lambda p=path: client.get(p), 60))
        out[name]["bytes"] = len(res.body)

    # What the ETag actually saves on the endpoint the front end hits hardest.
    # Answer: bytes, and nothing else. The tag is a hash of the body, so the
    # server has already parsed the file and built the payload by the time it can
    # decide to send a 304. Server side this is a wash; it is a transfer
    # optimisation, not a compute one, and pretending otherwise would be the
    # easiest number in this file to quote misleadingly.
    trace_path = endpoints["trace_db"]
    fresh = client.get(trace_path)
    etag = fresh.etag
    revalidate = timed(lambda: client.get(trace_path, headers={"If-None-Match": etag}), 60)
    out["trace_db_revalidated"] = percentiles(revalidate)
    out["trace_db_revalidated"]["bytes"] = 0
    out["etag_bytes_saved_per_hit"] = len(fresh.body)
    out["etag_saves_server_work"] = False
    return out


def main():
    os.makedirs(RESULTS, exist_ok=True)
    started = time.time()
    payload = {
        "environment": {
            "python": sys.version.split()[0],
            "implementation": platform.python_implementation(),
            "platform": platform.platform(),
            "note": "single process, in memory SQLite, WSGI application called directly",
            "clock": clock_resolution(),
        },
        "parser": bench_parser(),
        "rfmath": bench_rfmath(),
        "ingest": bench_ingest(),
        "search": bench_search(),
        "api": bench_api(),
    }
    payload["wall_seconds"] = round(time.time() - started, 2)

    path = os.path.join(RESULTS, "benchmarks.json")
    with open(path, "w") as handle:
        json.dump(payload, handle, indent=2, sort_keys=True)

    print("parser   %s sweeps/s, %s points/s"
          % (payload["parser"]["sweeps_per_second"], payload["parser"]["points_per_second"]))
    print("ingest   %s docs/s, full %.1f ms, unchanged re-ingest %.1f ms (%sx faster)"
          % (payload["ingest"]["documents_per_second"],
             payload["ingest"]["full_ingest_ms"]["p50_ms"],
             payload["ingest"]["unchanged_reingest_ms"]["p50_ms"],
             payload["ingest"]["reingest_speedup"]))
    print("search   p50 %.3f ms, p99 %.3f ms over %d queries"
          % (payload["search"]["query_ms"]["p50_ms"], payload["search"]["query_ms"]["p99_ms"],
             payload["search"]["queries"]))
    for name in sorted(payload["api"]):
        entry = payload["api"][name]
        if isinstance(entry, dict):
            print("api      %-22s p50 %.3f ms  p99 %.3f ms  %6d bytes"
                  % (name, entry["p50_ms"], entry["p99_ms"], entry.get("bytes", 0)))
    clock = payload["environment"]["clock"]
    print("clock    resolution floor %.4f ms; anything at or below that is the "
          "timer, not the code" % clock["median_gap_ms"])
    print("wrote %s in %.1fs" % (path, payload["wall_seconds"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
