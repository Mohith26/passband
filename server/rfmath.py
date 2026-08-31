"""The derived quantities engineers actually look at, computed from S parameters.

Every function here takes the canonical complex form the parser produces. I kept
the definitions in one place because the sign and reference conventions are the
easiest thing in RF to get quietly wrong, and a documentation system that shows a
positive insertion loss for a lossy part is worse than one that shows nothing.

Conventions used throughout:
  * return loss is reported as a positive number of dB, so a better match is a
    larger return loss
  * insertion loss is reported as a positive number of dB, so a lossier part has
    a larger insertion loss
  * gain is reported signed, so an amplifier is positive and a filter is negative
  * angles are radians internally and degrees only at the edges
"""

import cmath
import math

TINY = 1e-30


def db20(value):
    """Magnitude in dB. Floors at -300 dB so an exact zero does not blow up a plot
    axis with negative infinity."""
    mag = abs(value)
    if mag <= TINY:
        return -300.0
    return 20.0 * math.log10(mag)


def phase_deg(value):
    return math.degrees(cmath.phase(value))


def unwrap_deg(phases):
    """Remove the 360 degree jumps so group delay is differentiable."""
    if not phases:
        return []
    out = [phases[0]]
    offset = 0.0
    for k in range(1, len(phases)):
        delta = phases[k] - phases[k - 1]
        if delta > 180.0:
            offset -= 360.0
        elif delta < -180.0:
            offset += 360.0
        out.append(phases[k] + offset)
    return out


def vswr(reflection):
    """Voltage standing wave ratio from a reflection coefficient."""
    mag = abs(reflection)
    if mag >= 1.0:
        return float("inf")
    return (1.0 + mag) / (1.0 - mag)


def return_loss_db(reflection):
    """Positive dB. A perfect match is infinite, so it is capped at 300."""
    mag = abs(reflection)
    if mag <= TINY:
        return 300.0
    return -20.0 * math.log10(mag)


def insertion_loss_db(transmission):
    """Positive dB for a lossy path."""
    return -db20(transmission)


def gain_db(transmission):
    """Signed dB. This and insertion_loss_db are the same number with opposite
    signs and both exist because engineers ask for them by different names."""
    return db20(transmission)


def impedance_from_gamma(gamma, z0=50.0):
    """Convert a reflection coefficient to an impedance."""
    denom = 1.0 - gamma
    if abs(denom) <= TINY:
        return complex(float("inf"), 0.0)
    return z0 * (1.0 + gamma) / denom


def gamma_from_impedance(z, z0=50.0):
    denom = z + z0
    if abs(denom) <= TINY:
        return complex(1.0, 0.0)
    return (z - z0) / denom


def mismatch_loss_db(reflection):
    """Power lost purely to the mismatch, always positive."""
    mag2 = abs(reflection) ** 2
    if mag2 >= 1.0:
        return 300.0
    return -10.0 * math.log10(1.0 - mag2)


def group_delay_s(frequencies, transmission):
    """Group delay in seconds, from the negative slope of unwrapped phase.

    Uses a centred difference on the interior points and a one sided difference
    at each end, so the output is the same length as the input rather than two
    points shorter. Returns an empty list for fewer than two points because a
    delay is not defined from a single measurement.
    """
    n = len(frequencies)
    if n < 2:
        return []
    phase = unwrap_deg([phase_deg(v) for v in transmission])
    radians = [math.radians(p) for p in phase]
    out = []
    for k in range(n):
        if k == 0:
            dp = radians[1] - radians[0]
            df = frequencies[1] - frequencies[0]
        elif k == n - 1:
            dp = radians[n - 1] - radians[n - 2]
            df = frequencies[n - 1] - frequencies[n - 2]
        else:
            dp = radians[k + 1] - radians[k - 1]
            df = frequencies[k + 1] - frequencies[k - 1]
        out.append(0.0 if df == 0 else -dp / (2.0 * math.pi * df))
    return out


def stability_k(s11, s12, s21, s22):
    """Rollett stability factor K and the determinant magnitude.

    Unconditional stability needs K > 1 and |delta| < 1 together, which is why
    both come back from one call. Returns (K, |delta|).
    """
    delta = s11 * s22 - s12 * s21
    denom = 2.0 * abs(s12 * s21)
    if denom <= TINY:
        return float("inf"), abs(delta)
    k = (1.0 - abs(s11) ** 2 - abs(s22) ** 2 + abs(delta) ** 2) / denom
    return k, abs(delta)


def mu_stability(s11, s12, s21, s22):
    """The mu factor. A single number greater than 1 means unconditionally
    stable, which is easier to display than the two part K and delta test."""
    delta = s11 * s22 - s12 * s21
    denom = abs(s22 - delta * s11.conjugate()) + abs(s12 * s21)
    if denom <= TINY:
        return float("inf")
    return (1.0 - abs(s11) ** 2) / denom


def max_available_gain_db(s11, s12, s21, s22):
    """MAG, defined only where the device is unconditionally stable. Returns None
    elsewhere rather than a misleading number."""
    k, _ = stability_k(s11, s12, s21, s22)
    if k <= 1.0 or abs(s12) <= TINY:
        return None
    ratio = abs(s21) / abs(s12)
    return 10.0 * math.log10(ratio * (k - math.sqrt(k * k - 1.0)))


def summarize(network):
    """One dictionary per network, which is what the API hands the front end.

    Everything in here is computed, never stored, so a re-ingest of the same file
    cannot drift from the numbers shown next to it.
    """
    freqs = network.frequencies
    if not freqs:
        return {"points": 0}

    out = {
        "points": len(freqs),
        "ports": network.ports,
        "f_start_hz": freqs[0],
        "f_stop_hz": freqs[-1],
        "reference_impedance": network.reference_impedance,
    }

    s11 = network.s(1, 1)
    out["worst_return_loss_db"] = min(return_loss_db(v) for v in s11)
    out["worst_vswr"] = max(vswr(v) for v in s11)

    if network.ports >= 2:
        s21 = network.s(2, 1)
        s12 = network.s(1, 2)
        s22 = network.s(2, 2)
        gains = [gain_db(v) for v in s21]
        out["max_gain_db"] = max(gains)
        out["min_gain_db"] = min(gains)
        out["max_insertion_loss_db"] = max(insertion_loss_db(v) for v in s21)
        peak = max(range(len(gains)), key=lambda k: gains[k])
        out["peak_gain_hz"] = freqs[peak]

        delays = group_delay_s(freqs, s21)
        if delays:
            out["max_group_delay_s"] = max(delays)
            out["group_delay_ripple_s"] = max(delays) - min(delays)

        ks = []
        mus = []
        for k in range(len(freqs)):
            kk, _ = stability_k(s11[k], s12[k], s21[k], s22[k])
            ks.append(kk)
            mus.append(mu_stability(s11[k], s12[k], s21[k], s22[k]))
        finite_k = [v for v in ks if v != float("inf")]
        out["min_stability_k"] = min(finite_k) if finite_k else None
        finite_mu = [v for v in mus if v != float("inf")]
        out["min_mu"] = min(finite_mu) if finite_mu else None
        out["unconditionally_stable"] = bool(finite_mu) and min(finite_mu) > 1.0

    return out


def trace(network, i, j, kind):
    """A plot ready series. kind is one of db, phase, vswr, group_delay, real,
    imag. Raises for anything else so a typo in a query string is a 400 and not a
    blank chart."""
    values = network.s(i, j)
    freqs = network.frequencies
    if kind == "db":
        return freqs, [db20(v) for v in values]
    if kind == "phase":
        return freqs, unwrap_deg([phase_deg(v) for v in values])
    if kind == "vswr":
        return freqs, [vswr(v) for v in values]
    if kind == "group_delay":
        return freqs, group_delay_s(freqs, values)
    if kind == "real":
        return freqs, [v.real for v in values]
    if kind == "imag":
        return freqs, [v.imag for v in values]
    raise ValueError("unknown trace kind %r" % kind)
