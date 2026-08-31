"""Captures a set of real API responses to a JSON file.

The browser check for the front end replays these instead of talking to a live
server, so what gets rendered is genuinely what the Python backend produced. If
the backend changes shape, the snapshot changes and the front end check sees it.
"""

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tests import support

PATHS = [
    "/api/health",
    "/api/facets",
    "/api/parts",
    "/api/documents?limit=12&offset=0",
    "/api/search?q=stability+margin+bias+resistor&limit=12&offset=0",
    "/api/parts/QPA9903",
    "/api/parts/QPF4216",
]


def main():
    app = support.build_app()
    client = support.Client(app)

    snapshot = {}
    for path in PATHS:
        res = client.get(path)
        if res.status_code != 200:
            raise SystemExit("%s returned %d" % (path, res.status_code))
        snapshot[path] = res.json()

    # Traces for whichever datasets the front end will ask about first.
    for part_number in ["QPA9903", "QPF4216"]:
        part = snapshot["/api/parts/" + part_number]
        dataset = part["datasets"][0]
        for i, j, kind in [(2, 1, "db"), (1, 1, "db"), (2, 1, "phase"), (2, 1, "vswr"),
                           (2, 1, "group_delay"), (1, 1, "vswr"),
                           (1, 1, "real"), (1, 1, "imag")]:
            path = "/api/datasets/%d/trace?i=%d&j=%d&kind=%s" % (dataset["id"], i, j, kind)
            res = client.get(path)
            if res.status_code != 200:
                raise SystemExit("%s returned %d" % (path, res.status_code))
            snapshot[path] = res.json()

    out = os.path.join(ROOT, "fixtures", "api_snapshot.json")
    with open(out, "w") as handle:
        json.dump(snapshot, handle, sort_keys=True)

    total_points = sum(len(v.get("values", [])) for v in snapshot.values() if isinstance(v, dict))
    print("captured %d responses, %d plotted points" % (len(snapshot), total_points))
    print("wrote %s" % out)


if __name__ == "__main__":
    main()
