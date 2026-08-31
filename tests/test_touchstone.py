import cmath
import math
import os
import unittest

from server import touchstone
from server.touchstone import TouchstoneError

HERE = os.path.dirname(os.path.abspath(__file__))
CORPUS = os.path.join(os.path.dirname(HERE), "fixtures", "corpus")


class OptionLineTests(unittest.TestCase):
    def test_defaults_when_option_line_is_absent(self):
        net = touchstone.parse("1.0 0.5 0.0\n", ports=1)
        self.assertEqual(net.options["frequency_unit"], "GHZ")
        self.assertEqual(net.options["format"], "MA")
        self.assertEqual(net.options["parameter"], "S")
        self.assertEqual(net.options["resistance"], 50.0)

    def test_partial_option_line_keeps_other_defaults(self):
        net = touchstone.parse("# MHZ\n1000 0.5 0.0\n", ports=1)
        self.assertEqual(net.options["frequency_unit"], "MHZ")
        self.assertEqual(net.options["format"], "MA")
        self.assertEqual(net.frequencies, [1e9])

    def test_option_line_is_case_insensitive_and_order_free(self):
        a = touchstone.parse("# ri s mhz r 75\n1000 0.5 0.0\n", ports=1)
        b = touchstone.parse("# R 75 MHZ S RI\n1000 0.5 0.0\n", ports=1)
        self.assertEqual(a.options, b.options)
        self.assertEqual(a.options["resistance"], 75.0)

    def test_reference_impedance_is_read(self):
        net = touchstone.parse("# GHZ S MA R 75\n1 1 0\n", ports=1)
        self.assertEqual(net.reference_impedance, 75.0)

    def test_unknown_option_is_rejected(self):
        with self.assertRaises(TouchstoneError):
            touchstone.parse("# GHZ S XY\n1 1 0\n", ports=1)

    def test_r_without_a_value_is_rejected(self):
        with self.assertRaises(TouchstoneError):
            touchstone.parse("# GHZ S MA R\n1 1 0\n", ports=1)

    def test_two_option_lines_are_rejected(self):
        with self.assertRaises(TouchstoneError):
            touchstone.parse("# GHZ S MA\n# MHZ S RI\n1 1 0\n", ports=1)

    def test_frequency_units_all_scale(self):
        for unit, expected in [("HZ", 1.0), ("KHZ", 1e3), ("MHZ", 1e6), ("GHZ", 1e9)]:
            net = touchstone.parse("# %s S MA\n1 1 0\n" % unit, ports=1)
            self.assertEqual(net.frequencies[0], expected, unit)


class FormatTests(unittest.TestCase):
    def test_real_imaginary(self):
        net = touchstone.parse("# GHZ S RI\n1 0.3 -0.4\n", ports=1)
        self.assertAlmostEqual(net.s(1, 1)[0].real, 0.3)
        self.assertAlmostEqual(net.s(1, 1)[0].imag, -0.4)

    def test_magnitude_angle(self):
        net = touchstone.parse("# GHZ S MA\n1 0.5 90\n", ports=1)
        value = net.s(1, 1)[0]
        self.assertAlmostEqual(abs(value), 0.5)
        self.assertAlmostEqual(math.degrees(cmath.phase(value)), 90.0)

    def test_db_angle(self):
        net = touchstone.parse("# GHZ S DB\n1 -6.020599913 45\n", ports=1)
        value = net.s(1, 1)[0]
        self.assertAlmostEqual(abs(value), 0.5, places=6)
        self.assertAlmostEqual(math.degrees(cmath.phase(value)), 45.0, places=6)

    def test_db_zero_is_unity(self):
        net = touchstone.parse("# GHZ S DB\n1 0 0\n", ports=1)
        self.assertAlmostEqual(abs(net.s(1, 1)[0]), 1.0)


class PortOrderingTests(unittest.TestCase):
    def test_two_port_row_order_is_s11_s21_s12_s22(self):
        # The four values are deliberately distinct so a transposition shows up.
        text = "# GHZ S RI\n1 0.1 0 0.2 0 0.3 0 0.4 0\n"
        net = touchstone.parse(text, ports=2)
        self.assertAlmostEqual(net.s(1, 1)[0].real, 0.1)
        self.assertAlmostEqual(net.s(2, 1)[0].real, 0.2, msg="S21 is the second pair")
        self.assertAlmostEqual(net.s(1, 2)[0].real, 0.3, msg="S12 is the third pair")
        self.assertAlmostEqual(net.s(2, 2)[0].real, 0.4)

    def test_three_port_uses_plain_row_major_order(self):
        values = " ".join("%g 0" % (v / 10.0) for v in range(1, 10))
        net = touchstone.parse("# GHZ S RI\n1 " + values + "\n", ports=3)
        self.assertAlmostEqual(net.s(1, 2)[0].real, 0.2)
        self.assertAlmostEqual(net.s(2, 1)[0].real, 0.4)

    def test_port_count_inferred_from_extension(self):
        net = touchstone.parse("# GHZ S RI\n1 0.1 0 0.2 0 0.3 0 0.4 0\n", filename="thing.s2p")
        self.assertEqual(net.ports, 2)

    def test_port_count_inferred_from_row_width(self):
        self.assertEqual(touchstone.parse("# GHZ S RI\n1 0.1 0\n").ports, 1)
        self.assertEqual(touchstone.parse("# GHZ S RI\n1 .1 0 .2 0 .3 0 .4 0\n").ports, 2)

    def test_out_of_range_port_index_raises(self):
        net = touchstone.parse("# GHZ S RI\n1 0.1 0\n", ports=1)
        with self.assertRaises(IndexError):
            net.s(2, 1)


class LayoutTests(unittest.TestCase):
    def test_data_may_be_folded_across_lines(self):
        one_line = touchstone.parse("# GHZ S RI\n1 .1 0 .2 0 .3 0 .4 0\n", ports=2)
        folded = touchstone.parse("# GHZ S RI\n1 .1 0 .2 0\n.3 0 .4 0\n", ports=2)
        self.assertEqual(one_line.frequencies, folded.frequencies)
        self.assertEqual(one_line.matrices, folded.matrices)

    def test_comments_are_collected_and_ignored(self):
        text = "! header note\n# GHZ S RI\n1 0.1 0 ! trailing note\n"
        net = touchstone.parse(text, ports=1)
        self.assertEqual(len(net.frequencies), 1)
        self.assertIn("header note", net.comments)
        self.assertIn("trailing note", net.comments)

    def test_blank_lines_are_ignored(self):
        net = touchstone.parse("# GHZ S RI\n\n\n1 0.1 0\n\n2 0.2 0\n", ports=1)
        self.assertEqual(len(net), 2)

    def test_truncated_point_is_rejected(self):
        with self.assertRaises(TouchstoneError) as ctx:
            touchstone.parse("# GHZ S RI\n1 .1 0 .2 0 .3 0\n", ports=2)
        self.assertIn("whole number", str(ctx.exception))

    def test_non_numeric_data_is_rejected_with_a_line_number(self):
        with self.assertRaises(TouchstoneError) as ctx:
            touchstone.parse("# GHZ S RI\n1 0.1 0\n2 oops 0\n", ports=1)
        self.assertIn("line 3", str(ctx.exception))

    def test_non_monotonic_frequency_is_rejected(self):
        with self.assertRaises(TouchstoneError) as ctx:
            touchstone.parse("# GHZ S RI\n2 0.1 0\n1 0.1 0\n", ports=1)
        self.assertIn("increase", str(ctx.exception))

    def test_empty_file_is_rejected(self):
        with self.assertRaises(TouchstoneError):
            touchstone.parse("! only a comment\n# GHZ S RI\n", ports=1)

    def test_version_two_keywords_are_rejected_rather_than_misread(self):
        with self.assertRaises(TouchstoneError) as ctx:
            touchstone.parse("[Version] 2.0\n", ports=1)
        self.assertIn("2.0", str(ctx.exception))


class RoundTripTests(unittest.TestCase):
    def _sample(self):
        return touchstone.parse(
            "# GHZ S RI\n"
            "1 0.11 0.02 0.71 -0.31 0.09 0.04 0.21 -0.13\n"
            "2 0.13 0.05 0.65 -0.44 0.08 0.05 0.24 -0.15\n",
            ports=2,
        )

    def test_round_trip_through_every_format(self):
        original = self._sample()
        for fmt in ["RI", "MA", "DB"]:
            text = touchstone.dumps(original, fmt=fmt)
            again = touchstone.parse(text, ports=2)
            self.assertEqual(len(again), len(original), fmt)
            for k in range(len(original)):
                self.assertAlmostEqual(again.frequencies[k], original.frequencies[k], places=6)
                for i in range(1, 3):
                    for j in range(1, 3):
                        a = original.s(i, j)[k]
                        b = again.s(i, j)[k]
                        self.assertAlmostEqual(a.real, b.real, places=6, msg="%s S%d%d" % (fmt, i, j))
                        self.assertAlmostEqual(a.imag, b.imag, places=6, msg="%s S%d%d" % (fmt, i, j))

    def test_round_trip_through_every_frequency_unit(self):
        original = self._sample()
        for unit in ["HZ", "KHZ", "MHZ", "GHZ"]:
            again = touchstone.parse(touchstone.dumps(original, frequency_unit=unit), ports=2)
            for k in range(len(original)):
                self.assertAlmostEqual(again.frequencies[k] / original.frequencies[k], 1.0, places=9)


class CorpusTests(unittest.TestCase):
    def test_every_generated_file_parses(self):
        ts_dir = os.path.join(CORPUS, "touchstone")
        names = sorted(os.listdir(ts_dir))
        self.assertGreater(len(names), 0, "corpus was not generated")
        for name in names:
            net = touchstone.load(os.path.join(ts_dir, name))
            self.assertGreater(len(net), 0, name)
            self.assertEqual(net.ports, int(name.rsplit(".s", 1)[1][0]), name)
            self.assertEqual(sorted(net.frequencies), net.frequencies, name)

    def test_nearest_point_lookup(self):
        net = touchstone.load(os.path.join(CORPUS, "touchstone", "QPF4216_revA.s2p"))
        freq, _ = net.at(2.45e9)
        self.assertLess(abs(freq - 2.45e9), 1e7)


if __name__ == "__main__":
    unittest.main()
