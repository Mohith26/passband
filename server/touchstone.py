"""Reader for Touchstone files, the format every RF measurement tool spits out.

The spec I worked from is the Touchstone 1.1 file format. The parts that bite you
are all in the option line and the row folding rules, so those get most of the
attention here:

  * the option line starts with '#' and any of its four fields may be omitted,
    in which case the default applies (GHz, S, MA, R 50)
  * data pairs are ordered (real, imag), (mag, angle) or (dB, angle) depending on
    the format flag, and the angle is always in degrees
  * a two port file lists parameters in the order S11 S21 S12 S22, which is
    transposed relative to every other port count, and that ordering trap is the
    single most common way to read one of these files wrong
  * files with more than two ports fold each frequency point across several
    physical lines, and the folding is by row, not by a fixed pair count
  * '!' starts a comment anywhere on a line, including after data

Everything is stored internally in one canonical form: frequency in Hz and
parameters as complex numbers, so nothing downstream has to care what the file
said.
"""

import cmath
import math
import os

FREQ_MULTIPLIER = {"HZ": 1.0, "KHZ": 1e3, "MHZ": 1e6, "GHZ": 1e9}
VALID_PARAMS = {"S", "Y", "Z", "H", "G"}
VALID_FORMATS = {"MA", "DB", "RI"}

DEFAULT_OPTIONS = {
    "frequency_unit": "GHZ",
    "parameter": "S",
    "format": "MA",
    "resistance": 50.0,
}


class TouchstoneError(ValueError):
    """Raised for anything the file gets wrong. Always names the line number."""

    def __init__(self, message, line_number=None):
        self.line_number = line_number
        if line_number is not None:
            message = "line %d: %s" % (line_number, message)
        super(TouchstoneError, self).__init__(message)


class Network(object):
    """A parsed n-port network.

    frequencies is a list of floats in Hz. matrices is a list of n x n lists of
    complex numbers, one per frequency point, indexed [row][col] so that
    matrices[k][i][j] is S(i+1)(j+1) at frequencies[k].
    """

    def __init__(self, ports, frequencies, matrices, options, comments=None, noise=None):
        self.ports = ports
        self.frequencies = frequencies
        self.matrices = matrices
        self.options = options
        self.comments = comments or []
        self.noise = noise or []

    def __len__(self):
        return len(self.frequencies)

    @property
    def reference_impedance(self):
        return self.options["resistance"]

    def s(self, i, j):
        """All points of one parameter, one indexed like the file names them."""
        if not (1 <= i <= self.ports and 1 <= j <= self.ports):
            raise IndexError("port index out of range for a %d port network" % self.ports)
        return [m[i - 1][j - 1] for m in self.matrices]

    def at(self, frequency_hz):
        """Nearest measured point. No interpolation, on purpose: inventing a
        measurement that was never taken is not something a documentation system
        should quietly do."""
        if not self.frequencies:
            raise ValueError("empty network")
        best = min(range(len(self.frequencies)),
                   key=lambda k: abs(self.frequencies[k] - frequency_hz))
        return self.frequencies[best], self.matrices[best]

    def span(self):
        return (self.frequencies[0], self.frequencies[-1]) if self.frequencies else (None, None)


def _strip_comment(line):
    idx = line.find("!")
    if idx < 0:
        return line, None
    return line[:idx], line[idx + 1:].strip()


def _parse_option_line(line, line_number):
    options = dict(DEFAULT_OPTIONS)
    tokens = line[1:].split()
    i = 0
    while i < len(tokens):
        token = tokens[i].upper()
        if token in FREQ_MULTIPLIER:
            options["frequency_unit"] = token
        elif token in VALID_PARAMS:
            options["parameter"] = token
        elif token in VALID_FORMATS:
            options["format"] = token
        elif token == "R":
            if i + 1 >= len(tokens):
                raise TouchstoneError("option R is missing its value", line_number)
            try:
                options["resistance"] = float(tokens[i + 1])
            except ValueError:
                raise TouchstoneError("option R value %r is not a number" % tokens[i + 1], line_number)
            i += 1
        else:
            raise TouchstoneError("unrecognised option %r" % tokens[i], line_number)
        i += 1
    return options


def _to_complex(a, b, fmt):
    if fmt == "RI":
        return complex(a, b)
    if fmt == "MA":
        return cmath.rect(a, math.radians(b))
    if fmt == "DB":
        return cmath.rect(10.0 ** (a / 20.0), math.radians(b))
    raise TouchstoneError("unsupported format %r" % fmt)


def _ports_from_name(path):
    root, ext = os.path.splitext(path)
    ext = ext.lower()
    if len(ext) >= 4 and ext.startswith(".s") and ext.endswith("p"):
        digits = ext[2:-1]
        if digits.isdigit():
            return int(digits)
    return None


def parse(text, ports=None, filename=None):
    """Parse Touchstone text. Port count comes from the extension when a
    filename is given, otherwise it is inferred from the row width."""
    if ports is None and filename is not None:
        ports = _ports_from_name(filename)

    options = None
    comments = []
    numbers = []
    first_row_width = None

    for line_number, raw in enumerate(text.splitlines(), start=1):
        body, comment = _strip_comment(raw)
        if comment:
            comments.append(comment)
        body = body.strip()
        if not body:
            continue
        if body.startswith("["):
            # Touchstone 2.0 keywords. Rejected rather than half supported.
            raise TouchstoneError("version 2.0 keyword %r is not supported" % body, line_number)
        if body.startswith("#"):
            if options is not None:
                raise TouchstoneError("a second option line is not allowed", line_number)
            options = _parse_option_line(body, line_number)
            continue
        parts = body.split()
        try:
            values = [float(p) for p in parts]
        except ValueError:
            raise TouchstoneError("non numeric data %r" % body, line_number)
        if first_row_width is None:
            first_row_width = len(values)
        numbers.append((line_number, values))

    if options is None:
        options = dict(DEFAULT_OPTIONS)
    if not numbers:
        raise TouchstoneError("file contains no data points")

    if ports is None:
        ports = _infer_ports(first_row_width)

    return _assemble(numbers, ports, options, comments)


def _infer_ports(width):
    # A one port row is f plus one pair. A two port row is f plus four pairs.
    if width == 3:
        return 1
    if width == 9:
        return 2
    # For n > 2 the first line of a point holds f plus n pairs.
    if width >= 5 and (width - 1) % 2 == 0:
        return (width - 1) // 2
    raise TouchstoneError("cannot infer port count from a row of %d values" % width)


def _assemble(numbers, ports, options, comments):
    fmt = options["format"]
    scale = FREQ_MULTIPLIER[options["frequency_unit"]]
    per_point = 1 + 2 * ports * ports

    flat = []
    line_of = []
    for line_number, values in numbers:
        for v in values:
            flat.append(v)
            line_of.append(line_number)

    if len(flat) % per_point != 0:
        raise TouchstoneError(
            "data length %d is not a whole number of %d port points (expected a multiple of %d)"
            % (len(flat), ports, per_point),
            line_of[-1] if line_of else None,
        )

    frequencies = []
    matrices = []
    for start in range(0, len(flat), per_point):
        chunk = flat[start:start + per_point]
        freq = chunk[0] * scale
        if frequencies and freq <= frequencies[-1]:
            raise TouchstoneError(
                "frequencies must increase: %g follows %g" % (freq, frequencies[-1]),
                line_of[start],
            )
        pairs = [(chunk[1 + 2 * k], chunk[2 + 2 * k]) for k in range(ports * ports)]
        values = [_to_complex(a, b, fmt) for a, b in pairs]

        matrix = [[0j] * ports for _ in range(ports)]
        if ports == 2:
            # S11 S21 S12 S22, the one ordering that differs from every other
            # port count.
            matrix[0][0], matrix[1][0], matrix[0][1], matrix[1][1] = values
        else:
            idx = 0
            for row in range(ports):
                for col in range(ports):
                    matrix[row][col] = values[idx]
                    idx += 1
        frequencies.append(freq)
        matrices.append(matrix)

    return Network(ports, frequencies, matrices, options, comments)


def load(path):
    with open(path, "r") as handle:
        return parse(handle.read(), filename=path)


def dumps(network, fmt="MA", frequency_unit="GHZ"):
    """Write a network back out. Round tripping through this is how the parser
    tests check themselves against every format."""
    fmt = fmt.upper()
    frequency_unit = frequency_unit.upper()
    scale = FREQ_MULTIPLIER[frequency_unit]
    lines = ["! written by passband"]
    lines.append("# %s %s %s R %g" % (frequency_unit, network.options["parameter"], fmt,
                                      network.options["resistance"]))
    for k, freq in enumerate(network.frequencies):
        matrix = network.matrices[k]
        if network.ports == 2:
            ordered = [matrix[0][0], matrix[1][0], matrix[0][1], matrix[1][1]]
        else:
            ordered = [matrix[r][c] for r in range(network.ports) for c in range(network.ports)]
        cells = []
        for value in ordered:
            if fmt == "RI":
                cells.append("%.10g %.10g" % (value.real, value.imag))
            elif fmt == "MA":
                cells.append("%.10g %.10g" % (abs(value), math.degrees(cmath.phase(value))))
            elif fmt == "DB":
                mag = abs(value)
                db = -300.0 if mag == 0 else 20.0 * math.log10(mag)
                cells.append("%.10g %.10g" % (db, math.degrees(cmath.phase(value))))
            else:
                raise ValueError("unsupported format " + fmt)
        lines.append("%.10g %s" % (freq / scale, " ".join(cells)))
    return "\n".join(lines) + "\n"
