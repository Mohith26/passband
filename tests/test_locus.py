"""The reflection locus has to be physically plausible, not just numerically valid.

A Smith chart is the one view in this tool where a wrong model is obvious at a
glance and invisible to every other test: magnitudes can all be correct while the
phase jumps at random, and every unit test still passes. That is exactly what
happened, and it only showed up when the chart was rendered.

So the property is asserted directly: between adjacent frequency points the
reflection coefficient must move a small distance, because it is a continuous
function of frequency and the sweep is finely spaced.
"""

import cmath
import math
import os
import unittest

from server import touchstone
from tests import support

TOUCHSTONE_DIR = os.path.join(support.CORPUS, "touchstone")

# One quarter of the unit circle between adjacent points would already be a very
# coarse sweep. Random phase averages about 1.3 in this metric, so the threshold
# separates the two cases by a wide margin.
MAX_STEP = 0.25


def locus_steps(values):
    return [abs(values[k + 1] - values[k]) for k in range(len(values) - 1)]


class LocusTests(unittest.TestCase):
    def test_every_reflection_locus_is_continuous(self):
        checked = 0
        for name in sorted(os.listdir(TOUCHSTONE_DIR)):
            net = touchstone.load(os.path.join(TOUCHSTONE_DIR, name))
            for port in range(1, net.ports + 1):
                steps = locus_steps(net.s(port, port))
                checked += 1
                worst = max(steps)
                self.assertLess(
                    worst, MAX_STEP,
                    "%s S%d%d jumps %.3f between adjacent points, which is not a "
                    "continuous locus" % (name, port, port, worst))
        self.assertGreater(checked, 0)

    def test_phase_advances_monotonically_for_a_delay_dominated_port(self):
        # A reflection seen through a length of line rotates clockwise as
        # frequency rises. Unwrapped, that means the phase decreases overall.
        net = touchstone.load(os.path.join(TOUCHSTONE_DIR, "TR50X_revA.s1p"))
        phases = [math.degrees(cmath.phase(v)) for v in net.s(1, 1)]
        from server import rfmath
        unwrapped = rfmath.unwrap_deg(phases)
        self.assertLess(unwrapped[-1], unwrapped[0])

    def test_a_random_phase_locus_would_be_caught(self):
        # Negative control: the detector has to reject the thing it was written
        # for, otherwise the test above proves nothing.
        import random
        rng = random.Random(7)
        fake = [cmath.rect(0.2, rng.uniform(-math.pi, math.pi)) for _ in range(200)]
        self.assertGreater(max(locus_steps(fake)), MAX_STEP)

    def test_locus_stays_inside_the_unit_circle_for_passive_parts(self):
        for name in sorted(os.listdir(TOUCHSTONE_DIR)):
            if not name.startswith(("QPF", "QPQ", "TR")):
                continue
            net = touchstone.load(os.path.join(TOUCHSTONE_DIR, name))
            for port in range(1, net.ports + 1):
                for value in net.s(port, port):
                    self.assertLessEqual(abs(value), 1.0 + 1e-9, name)


if __name__ == "__main__":
    unittest.main()
