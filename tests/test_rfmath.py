import cmath
import math
import os
import unittest

from server import rfmath, touchstone

HERE = os.path.dirname(os.path.abspath(__file__))
CORPUS = os.path.join(os.path.dirname(HERE), "fixtures", "corpus")


class ScalarTests(unittest.TestCase):
    def test_db20_known_values(self):
        self.assertAlmostEqual(rfmath.db20(1.0), 0.0)
        self.assertAlmostEqual(rfmath.db20(0.5), -6.020599913, places=6)
        self.assertAlmostEqual(rfmath.db20(2.0), 6.020599913, places=6)
        self.assertAlmostEqual(rfmath.db20(0.1), -20.0, places=9)

    def test_db20_floors_instead_of_returning_negative_infinity(self):
        self.assertEqual(rfmath.db20(0.0), -300.0)
        self.assertTrue(math.isfinite(rfmath.db20(0j)))

    def test_vswr_matches_the_closed_form(self):
        # |gamma| = 1/3 is the textbook 2:1 standing wave ratio.
        self.assertAlmostEqual(rfmath.vswr(complex(1.0 / 3.0, 0)), 2.0, places=9)
        self.assertAlmostEqual(rfmath.vswr(0j), 1.0)
        self.assertEqual(rfmath.vswr(complex(1.0, 0)), float("inf"))

    def test_vswr_ignores_phase(self):
        for angle in [0, 45, 90, 180, 270]:
            value = cmath.rect(0.2, math.radians(angle))
            self.assertAlmostEqual(rfmath.vswr(value), 1.5, places=9)

    def test_return_loss_is_positive_and_larger_is_better(self):
        good = rfmath.return_loss_db(complex(0.01, 0))
        bad = rfmath.return_loss_db(complex(0.5, 0))
        self.assertGreater(good, bad)
        self.assertAlmostEqual(bad, 6.020599913, places=6)
        self.assertEqual(rfmath.return_loss_db(0j), 300.0)

    def test_insertion_loss_and_gain_are_mirror_images(self):
        value = cmath.rect(0.25, 0.7)
        self.assertAlmostEqual(rfmath.insertion_loss_db(value), -rfmath.gain_db(value), places=12)
        self.assertGreater(rfmath.insertion_loss_db(value), 0.0)
        self.assertLess(rfmath.gain_db(value), 0.0)

    def test_mismatch_loss(self):
        self.assertAlmostEqual(rfmath.mismatch_loss_db(0j), 0.0)
        # |gamma| = 0.5 reflects a quarter of the power, so 1.2494 dB is lost.
        self.assertAlmostEqual(rfmath.mismatch_loss_db(complex(0.5, 0)), 1.2493873661, places=8)

    def test_impedance_round_trip(self):
        for z in [complex(50, 0), complex(75, 0), complex(25, 30), complex(120, -60)]:
            gamma = rfmath.gamma_from_impedance(z, 50.0)
            back = rfmath.impedance_from_gamma(gamma, 50.0)
            self.assertAlmostEqual(back.real, z.real, places=9)
            self.assertAlmostEqual(back.imag, z.imag, places=9)

    def test_matched_load_has_zero_reflection(self):
        self.assertAlmostEqual(abs(rfmath.gamma_from_impedance(complex(50, 0), 50.0)), 0.0)

    def test_open_and_short_are_full_reflection(self):
        short = rfmath.gamma_from_impedance(complex(0, 0), 50.0)
        self.assertAlmostEqual(short.real, -1.0, places=9)
        big = rfmath.gamma_from_impedance(complex(1e12, 0), 50.0)
        self.assertAlmostEqual(big.real, 1.0, places=6)


class PhaseTests(unittest.TestCase):
    def test_unwrap_removes_the_jump(self):
        wrapped = [170.0, 175.0, -179.0, -170.0]
        out = rfmath.unwrap_deg(wrapped)
        self.assertAlmostEqual(out[2], 181.0)
        self.assertAlmostEqual(out[3], 190.0)
        for k in range(1, len(out)):
            self.assertLess(abs(out[k] - out[k - 1]), 180.0)

    def test_unwrap_leaves_a_smooth_series_alone(self):
        smooth = [0.0, 10.0, 20.0, 30.0]
        self.assertEqual(rfmath.unwrap_deg(smooth), smooth)

    def test_unwrap_handles_empty_and_single(self):
        self.assertEqual(rfmath.unwrap_deg([]), [])
        self.assertEqual(rfmath.unwrap_deg([5.0]), [5.0])


class GroupDelayTests(unittest.TestCase):
    def test_constant_delay_line_recovers_its_delay(self):
        # A pure delay of tau has phase -2 pi f tau, so group delay must come
        # back as exactly tau at every point including the two ends.
        tau = 2.5e-9
        freqs = [1e9 + 1e7 * k for k in range(40)]
        s21 = [cmath.rect(1.0, -2.0 * math.pi * f * tau) for f in freqs]
        delays = rfmath.group_delay_s(freqs, s21)
        self.assertEqual(len(delays), len(freqs))
        for d in delays:
            self.assertAlmostEqual(d, tau, places=15)

    def test_zero_phase_slope_is_zero_delay(self):
        freqs = [1e9 + 1e7 * k for k in range(10)]
        s21 = [complex(1.0, 0.0) for _ in freqs]
        for d in rfmath.group_delay_s(freqs, s21):
            self.assertAlmostEqual(d, 0.0, places=15)

    def test_too_few_points_returns_empty(self):
        self.assertEqual(rfmath.group_delay_s([1e9], [complex(1, 0)]), [])
        self.assertEqual(rfmath.group_delay_s([], []), [])

    def test_delay_survives_phase_wrapping(self):
        # A long delay wraps the phase many times across the sweep; if unwrapping
        # were skipped the answer would be wrong by a large factor.
        tau = 40e-9
        freqs = [1e9 + 2e6 * k for k in range(60)]
        s21 = [cmath.rect(1.0, -2.0 * math.pi * f * tau) for f in freqs]
        delays = rfmath.group_delay_s(freqs, s21)
        for d in delays:
            self.assertAlmostEqual(d / tau, 1.0, places=9)


class StabilityTests(unittest.TestCase):
    def test_unilateral_device_is_infinitely_stable(self):
        k, delta = rfmath.stability_k(complex(0.3, 0), 0j, complex(4, 0), complex(0.2, 0))
        self.assertEqual(k, float("inf"))
        self.assertAlmostEqual(delta, 0.06, places=9)

    def test_known_stable_device(self):
        s11, s12, s21, s22 = complex(0.3, 0), complex(0.02, 0), complex(4.0, 0), complex(0.2, 0)
        k, delta = rfmath.stability_k(s11, s12, s21, s22)
        # Hand computed: delta = 0.06 - 0.08 = -0.02, denom = 2*0.08 = 0.16,
        # numerator = 1 - 0.09 - 0.04 + 0.0004 = 0.8704, so K = 5.44.
        self.assertAlmostEqual(delta, 0.02, places=9)
        self.assertAlmostEqual(k, 5.44, places=9)
        self.assertGreater(rfmath.mu_stability(s11, s12, s21, s22), 1.0)

    def test_potentially_unstable_device_fails_both_tests(self):
        s11, s12, s21, s22 = complex(0.9, 0), complex(0.4, 0), complex(3.0, 0), complex(0.85, 0)
        k, _ = rfmath.stability_k(s11, s12, s21, s22)
        self.assertLess(k, 1.0)
        self.assertLess(rfmath.mu_stability(s11, s12, s21, s22), 1.0)

    def test_mu_and_k_agree_on_the_verdict(self):
        cases = [
            (complex(0.3, 0), complex(0.02, 0), complex(4.0, 0), complex(0.2, 0)),
            (complex(0.9, 0), complex(0.4, 0), complex(3.0, 0), complex(0.85, 0)),
            (complex(0.5, 0.1), complex(0.05, -0.02), complex(2.5, 0.4), complex(0.45, -0.2)),
            (complex(0.1, 0.05), complex(0.01, 0), complex(6.0, 0), complex(0.12, 0)),
        ]
        for s11, s12, s21, s22 in cases:
            k, delta = rfmath.stability_k(s11, s12, s21, s22)
            mu = rfmath.mu_stability(s11, s12, s21, s22)
            by_k = (k > 1.0 and delta < 1.0)
            by_mu = mu > 1.0
            self.assertEqual(by_k, by_mu, "K/delta and mu disagree for %r" % (s11,))

    def test_max_available_gain_is_none_when_unstable(self):
        self.assertIsNone(rfmath.max_available_gain_db(
            complex(0.9, 0), complex(0.4, 0), complex(3.0, 0), complex(0.85, 0)))

    def test_max_available_gain_when_stable(self):
        mag = rfmath.max_available_gain_db(
            complex(0.3, 0), complex(0.02, 0), complex(4.0, 0), complex(0.2, 0))
        self.assertIsNotNone(mag)
        # MAG can never exceed the raw |S21|/|S12| ratio in dB.
        self.assertLess(mag, 10.0 * math.log10(4.0 / 0.02))


class TraceAndSummaryTests(unittest.TestCase):
    def setUp(self):
        self.net = touchstone.load(os.path.join(CORPUS, "touchstone", "QPF4216_revA.s2p"))
        self.amp = touchstone.load(os.path.join(CORPUS, "touchstone", "QPA9903_revA.s2p"))

    def test_trace_kinds_all_return_matching_lengths(self):
        for kind in ["db", "phase", "vswr", "real", "imag"]:
            freqs, values = rfmath.trace(self.net, 2, 1, kind)
            self.assertEqual(len(freqs), len(values), kind)
            self.assertEqual(len(freqs), len(self.net))

    def test_unknown_trace_kind_raises(self):
        with self.assertRaises(ValueError):
            rfmath.trace(self.net, 2, 1, "smith")

    def test_bandpass_summary_looks_like_a_bandpass(self):
        summary = rfmath.summarize(self.net)
        self.assertEqual(summary["ports"], 2)
        self.assertEqual(summary["points"], len(self.net))
        # The generator centred this part at 2.45 GHz.
        self.assertLess(abs(summary["peak_gain_hz"] - 2.45e9), 5e7)
        # A passive filter cannot have gain.
        self.assertLess(summary["max_gain_db"], 0.5)
        # And it must reject hard somewhere in the sweep.
        self.assertGreater(summary["max_insertion_loss_db"], 30.0)

    def test_amplifier_summary_shows_gain(self):
        summary = rfmath.summarize(self.amp)
        self.assertGreater(summary["max_gain_db"], 15.0)
        self.assertIn("min_mu", summary)
        self.assertIn("unconditionally_stable", summary)

    def test_summary_of_a_one_port_omits_two_port_fields(self):
        term = touchstone.load(os.path.join(CORPUS, "touchstone", "TR50X_revA.s1p"))
        summary = rfmath.summarize(term)
        self.assertEqual(summary["ports"], 1)
        self.assertNotIn("max_gain_db", summary)
        self.assertIn("worst_return_loss_db", summary)
        # A good termination reflects very little.
        self.assertGreater(summary["worst_return_loss_db"], 20.0)

    def test_summary_of_an_empty_network(self):
        empty = touchstone.Network(2, [], [], dict(touchstone.DEFAULT_OPTIONS))
        self.assertEqual(rfmath.summarize(empty), {"points": 0})


if __name__ == "__main__":
    unittest.main()
