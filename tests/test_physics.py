"""Checks that the numbers the system reports are physically legal.

These exist because two bugs got through the first round of unit tests. Both were
found by trying to serialise a real payload, not by any assertion I had written,
and both are the kind of thing that would have shipped a wrong number to an
engineer rather than crashing loudly.
"""

import json
import math
import os
import unittest

from server import rfmath, touchstone
from tests import support

TOUCHSTONE_DIR = os.path.join(support.CORPUS, "touchstone")
PASSIVE_PREFIXES = ("QPF", "QPQ", "TR")

VALID_KINDS = ["db", "phase", "group_delay", "real", "imag"]


def _passive(name):
    return name.startswith(PASSIVE_PREFIXES)


class PassivityTests(unittest.TestCase):
    def test_no_passive_part_has_gain(self):
        # A filter or a termination cannot put out more power than it takes in,
        # so no scattering parameter may exceed unity. The generator used to
        # apply its ripple in both directions, which pushed the in band |S21| of
        # every bandpass to about 1.02.
        checked = 0
        for name in sorted(os.listdir(TOUCHSTONE_DIR)):
            if not _passive(name):
                continue
            net = touchstone.load(os.path.join(TOUCHSTONE_DIR, name))
            checked += 1
            for k, matrix in enumerate(net.matrices):
                for row in matrix:
                    for value in row:
                        self.assertLessEqual(
                            abs(value), 1.0 + 1e-9,
                            "%s point %d has |S| = %.6f" % (name, k, abs(value)))
        self.assertGreater(checked, 0, "no passive fixtures were found")

    def test_active_parts_are_allowed_gain(self):
        net = touchstone.load(os.path.join(TOUCHSTONE_DIR, "QPA9903_revA.s2p"))
        self.assertGreater(max(abs(v) for v in net.s(2, 1)), 1.0)

    def test_generator_rejects_a_non_passive_network(self):
        from fixtures import generate
        net = touchstone.parse("# GHZ S RI\n1 0 0 1.5 0 0 0 0 0\n", ports=2)
        with self.assertRaises(AssertionError):
            generate.assert_passive(net, "made up")


class TraceDomainTests(unittest.TestCase):
    def setUp(self):
        self.amp = touchstone.load(os.path.join(TOUCHSTONE_DIR, "QPA9903_revA.s2p"))

    def test_vswr_is_refused_for_a_transmission_parameter(self):
        with self.assertRaises(ValueError) as ctx:
            rfmath.trace(self.amp, 2, 1, "vswr")
        self.assertIn("reflection", str(ctx.exception))

    def test_return_loss_is_refused_for_a_transmission_parameter(self):
        with self.assertRaises(ValueError):
            rfmath.trace(self.amp, 1, 2, "return_loss")

    def test_vswr_is_allowed_for_a_reflection_parameter(self):
        _, values = rfmath.trace(self.amp, 1, 1, "vswr")
        self.assertTrue(all(v >= 1.0 for v in values))

    def test_every_valid_trace_on_every_fixture_is_finite(self):
        # The check that would have caught the infinity, had it existed.
        combinations = 0
        for name in sorted(os.listdir(TOUCHSTONE_DIR)):
            net = touchstone.load(os.path.join(TOUCHSTONE_DIR, name))
            for i in range(1, net.ports + 1):
                for j in range(1, net.ports + 1):
                    kinds = list(VALID_KINDS) + (["vswr", "return_loss"] if i == j else [])
                    for kind in kinds:
                        _, values = rfmath.trace(net, i, j, kind)
                        combinations += 1
                        for value in values:
                            self.assertTrue(
                                math.isfinite(value),
                                "%s S%d%d %s produced %r" % (name, i, j, kind, value))
        self.assertGreater(combinations, 100)


class SerialisationTests(unittest.TestCase):
    def test_finite_filter_maps_infinities_to_null(self):
        from server import api
        payload = {"a": float("inf"), "b": [1.0, float("-inf")], "c": {"d": 2.5}}
        cleaned = api._finite(payload)
        self.assertIsNone(cleaned["a"])
        self.assertEqual(cleaned["b"], [1.0, None])
        self.assertEqual(cleaned["c"]["d"], 2.5)
        json.dumps(cleaned, allow_nan=False)

    def test_finite_filter_leaves_ordinary_values_alone(self):
        from server import api
        payload = {"n": 3, "s": "text", "f": 1.5, "b": True, "z": None}
        self.assertEqual(api._finite(payload), payload)

    def test_api_refuses_vswr_on_a_transmission_parameter(self):
        client = support.Client(support.build_app())
        part = client.get("/api/parts/QPA9903").json()
        ds_id = part["datasets"][0]["id"]
        res = client.get("/api/datasets/%d/trace?i=2&j=1&kind=vswr" % ds_id)
        self.assertEqual(res.status_code, 400)
        self.assertIn("reflection", res.json()["error"])

    def test_every_snapshotted_endpoint_serialises(self):
        client = support.Client(support.build_app())
        part = client.get("/api/parts/QPA9903").json()
        ds_id = part["datasets"][0]["id"]
        for path in ["/api/health", "/api/facets", "/api/parts",
                     "/api/documents?limit=5", "/api/search?q=gain",
                     "/api/datasets/%d" % ds_id,
                     "/api/datasets/%d/trace?i=1&j=1&kind=vswr" % ds_id,
                     "/api/datasets/%d/trace?i=2&j=1&kind=group_delay" % ds_id]:
            res = client.get(path)
            self.assertEqual(res.status_code, 200, path)
            json.loads(res.body.decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
