# Results

Working notes. Every number quoted in the README appears here with the command
that produced it, and anything not in `results/benchmarks.json` does not belong in
either file.

```
python3 fixtures/generate.py    # rebuild the corpus, deterministic from SEED
python3 tests/run.py            # full suite
python3 tests/count_tests.py    # per file test counts
python3 tests/snapshot_api.py   # capture real API responses for the browser check
python3 bench/run.py            # results/benchmarks.json
```

## Environment

```
CPython 3.12.1
Emscripten-3.1.58-wasm32-32bit
perf_counter resolution floor: 0.1 ms (measured, not assumed)
```

This is Python compiled to WebAssembly. There was no native interpreter available
on the machine I built this on, which has two consequences I have to state rather
than bury:

- WebAssembly is meaningfully slower than native CPython. Everything below is a
  floor, not a fair reading of the code.
- The runtime clamps its high resolution timer as a side channel defence. The
  benchmark measures the floor by spinning on `perf_counter` until the value
  changes, and reports it alongside the results. **0.1 ms.** Any figure at or
  below that is the timer.

I would rather publish a slow honest number with its caveat than a fast one I
cannot defend.

## Test suite

```
tests run 165, failures 0, errors 0, skipped 0        about 2.0 s
```

| File | Tests | Covers |
| --- | --- | --- |
| test_api.py | 41 | routing, ETag revalidation, parameter validation, traversal |
| test_rfmath.py | 30 | derived quantities against closed forms and hand computed values |
| test_touchstone.py | 29 | option line, three formats, four units, port ordering, malformed input |
| test_store.py | 22 | dual dialect schema, constraints, cascade, rollback, paging |
| test_search.py | 21 | tokenizer, BM25 ranking, filters, labelled query set |
| test_physics.py | 11 | passivity, trace domain restrictions, JSON sanitisation |
| test_ingest.py | 7 | idempotency, partial failure, recomputation |
| test_locus.py | 4 | reflection continuity, with a negative control |

Tests worth naming individually:

- `test_constant_delay_line_recovers_its_delay` builds a pure delay of 2.5 ns and
  requires group delay back to 15 decimal places at every point, including the two
  one sided ends.
- `test_delay_survives_phase_wrapping` uses a 40 ns delay so the phase wraps many
  times across the sweep. Skip the unwrap and the answer is wrong by a large
  factor, so this is the test that actually pins the unwrapping.
- `test_known_stable_device` uses hand computed values: delta = 0.02 and K = 5.44,
  worked out on paper from the definition rather than recorded from a run.
- `test_mu_and_k_agree_on_the_verdict` cross checks two independent stability
  criteria against each other on four devices, so a sign error in one shows up.
- `test_a_random_phase_locus_would_be_caught` is the negative control for the
  Smith chart continuity check.
- `test_directory_traversal_is_refused` tries three encodings and asserts no
  source code comes back in the body.

## Parser

| | |
| --- | --- |
| Files | 16 |
| Frequency points | 3,056 |
| Sweeps per second | 423 |
| Points per second | 80,846 |
| Per sweep | p50 2.4 ms |

Round trip coverage: every network is written back out in RI, MA and DB, and in
Hz, kHz, MHz and GHz, then reparsed and compared to six decimal places. That is
what gives confidence in the format conversions, since a sign or a degrees to
radians slip survives any single format test.

## Ingest

| | |
| --- | --- |
| Files | 56 (40 documents, 16 measurement sweeps) |
| Full ingest | p50 91.2 ms |
| Documents per second | 441 |
| Terms indexed | 1,713 |
| Unchanged re-ingest | p50 51.5 ms |
| Speedup when nothing changed | 1.8x |

The 1.8x is smaller than it looks like it should be, and the reason is worth
writing down: the sha256 skip avoids the insert and the reindex, but the file
still has to be read and hashed to know it is unchanged. So re-ingest saves the
write path and not the read path. Getting this to a larger number would mean
trusting mtime, and trusting mtime is how you end up serving a stale document
after a file is restored from backup.

## Search

| | |
| --- | --- |
| Queries measured | 10 |
| Per query | p50 0.5 ms, p90 0.8 ms, p99 1.3 ms |
| Index terms | 1,713 |
| Precision at 1 on the labelled set | 5/5 |

The labelled set is five queries, each aimed at content that only one document
kind contains. It is small and the corpus is synthetic, so this is a regression
guard, not a claim about search quality in general. If it ever drops to 4/5 the
ranking changed and I want to know.

## API

Latency per endpoint, 60 repeats each, WSGI application called directly.

| Endpoint | p50 | p99 | Body |
| --- | --- | --- | --- |
| `/api/health` | 0.0 ms | 0.2 ms | 88 B |
| `/api/facets` | 0.1 ms | 0.2 ms | 380 B |
| `/api/datasets/{id}` | 0.1 ms | 0.3 ms | 822 B |
| `/api/part/{n}` | 0.4 ms | 0.6 ms | 5,248 B |
| `/api/documents?limit=25` | 0.5 ms | 0.6 ms | 3,585 B |
| `/api/search?q=...` | 0.8 ms | 1.1 ms | 5,440 B |
| `/api/datasets/{id}/trace` | 3.6 ms | 16.4 ms | 6,932 B |
| same, revalidated with an ETag | 3.2 ms | 13.0 ms | 0 B |

The first three are at or under the 0.1 ms clock floor and are not measurements.

The trace endpoint is the slowest by an order of magnitude because it reparses the
measurement file on every request. That is deliberate: the alternative is caching
parsed sweeps, and then a corrected file keeps showing the old numbers until
something invalidates the cache. For a documentation system that is the wrong
trade, and 3.6 ms is not a problem to solve yet.

**The ETag does not save server time.** 3.2 ms revalidated against 3.6 ms full is
inside the noise, and it should be: the tag is a sha256 of the response body, so
the server parses the file and builds the whole payload before it can decide to
answer 304. What it saves is 6,932 bytes per hit on the endpoint the front end
requests most often as a user toggles curves. It is a transfer optimisation. The
benchmark records `etag_saves_server_work: false` so nobody reads the table the
other way.

## Front end verification

`tests/snapshot_api.py` captures 23 real API responses covering 3,216 plotted
points into `fixtures/api_snapshot.json`. The browser check loads the actual
`web/` files with `fetch` replaying that snapshot, then drives the UI and asserts:

```
chart trace paths        1
chart axis labels        11
smith grid circles       20
smith trace paths        1
summary table rows       13
disabled controls        VSWR, Return loss   (correct for S21)
console errors           0
```

Rendering against captured backend output rather than a hand written mock is the
point: if the API response shape changes, the snapshot changes and the front end
check sees it.

## Things that went wrong, in order

1. **Infinite VSWR crashed the API.** Found by trying to capture the snapshot, not
   by any test. Root cause was a missing domain restriction, not a bad formula.
   Fixed in `rfmath.trace`, defended again in the JSON encoder, and surfaced in
   the UI as disabled buttons.
2. **Passive filters with 1.02 gain.** The generator's ripple could push the
   response above unity. My assertion, `max_gain_db < 0.5`, was too loose to
   notice 0.134 dB. Ripple now only attenuates and passivity is asserted at
   generation time and again in the test suite.
3. **The Smith chart was a scribble.** Random phase per point. Every unit test
   passed because every magnitude was right. Only visible on screen. Now modelled
   as a smooth rotation and pinned by a continuity test with its own negative
   control.
4. **VSWR buttons looked enabled while being disabled.** The `disabled` attribute
   was set and there was no matching style, so the control lied about its state.
   Caught by looking at the screenshot after the render check passed.

Items 3 and 4 both came from looking at the thing rather than from an assertion,
which is the part I would keep if I built this again: render early, and treat the
picture as a test surface.
