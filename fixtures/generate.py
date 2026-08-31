"""Builds the sample corpus: Touchstone files plus the engineering documents that
go with them.

Everything is synthetic and generated from a fixed seed, because the point of the
corpus is to be reproducible and to contain parts whose correct answers I already
know, not to look like real Qorvo data. Physical models are simple textbook ones:
a Butterworth bandpass response, a two pole amplifier rolloff, an ideal coupler.
"""

import cmath
import json
import math
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from server import touchstone

SEED = 20260831


def linspace(a, b, n):
    if n == 1:
        return [a]
    step = (b - a) / (n - 1)
    return [a + step * i for i in range(n)]


def butterworth_bandpass(f, f0, bw, order):
    """Magnitude response of a Butterworth bandpass, plus a phase that behaves
    like a real filter (roughly linear in band, steep at the skirts)."""
    if f <= 0:
        return 0j
    q = f0 / bw
    x = q * (f / f0 - f0 / f)
    mag = 1.0 / math.sqrt(1.0 + x ** (2 * order))
    phase = -order * math.atan(x) - 2.0 * math.pi * f * (order / (2.0 * math.pi * bw))
    return cmath.rect(mag, phase)


def amplifier_gain(f, gain_db_dc, f3db, poles):
    mag = 10.0 ** (gain_db_dc / 20.0) / ((1.0 + (f / f3db) ** 2) ** (poles / 2.0))
    phase = -poles * math.atan(f / f3db)
    return cmath.rect(mag, phase)


def make_filter(name, f0, bw, order, points, rng, ripple_db=0.0):
    freqs = linspace(f0 - 3.0 * bw, f0 + 3.0 * bw, points)
    freqs = [f for f in freqs if f > 0]
    matrices = []
    for f in freqs:
        s21 = butterworth_bandpass(f, f0, bw, order)
        if ripple_db:
            s21 *= 10.0 ** (rng.uniform(-ripple_db, ripple_db) / 20.0)
        # Reflection is whatever is not transmitted, with a small resistive loss
        # so the part is passive but not lossless.
        through = abs(s21) ** 2
        refl_mag = math.sqrt(max(0.0, 1.0 - through) * 0.94)
        s11 = cmath.rect(refl_mag, rng.uniform(-math.pi, math.pi))
        s22 = cmath.rect(refl_mag * 0.97, rng.uniform(-math.pi, math.pi))
        matrices.append([[s11, s21], [s21, s22]])
    options = dict(touchstone.DEFAULT_OPTIONS)
    options["format"] = "DB"
    return touchstone.Network(2, freqs, matrices, options, comments=[name])


def make_amplifier(name, gain_db_dc, f3db, points, rng):
    freqs = linspace(f3db * 0.05, f3db * 2.5, points)
    matrices = []
    for f in freqs:
        s21 = amplifier_gain(f, gain_db_dc, f3db, 2)
        s12 = cmath.rect(10.0 ** (-32.0 / 20.0), rng.uniform(-math.pi, math.pi))
        s11 = cmath.rect(rng.uniform(0.06, 0.22), rng.uniform(-math.pi, math.pi))
        s22 = cmath.rect(rng.uniform(0.08, 0.26), rng.uniform(-math.pi, math.pi))
        matrices.append([[s11, s12], [s21, s22]])
    options = dict(touchstone.DEFAULT_OPTIONS)
    options["format"] = "MA"
    return touchstone.Network(2, freqs, matrices, options, comments=[name])


def make_termination(name, points, rng):
    freqs = linspace(1e8, 6e9, points)
    matrices = []
    for f in freqs:
        mag = 0.01 + 0.02 * (f / 6e9)
        matrices.append([[cmath.rect(mag, rng.uniform(-math.pi, math.pi))]])
    options = dict(touchstone.DEFAULT_OPTIONS)
    options["format"] = "RI"
    return touchstone.Network(1, freqs, matrices, options, comments=[name])


PART_PLAN = [
    ("QPF4216", "front end module", "filter", dict(f0=2.45e9, bw=0.20e9, order=5, ripple_db=0.15)),
    ("QPF4228", "front end module", "filter", dict(f0=5.5e9, bw=0.9e9, order=4, ripple_db=0.2)),
    ("QPA9903", "low noise amplifier", "amp", dict(gain_db_dc=21.5, f3db=6.0e9)),
    ("QPA2933", "power amplifier", "amp", dict(gain_db_dc=31.0, f3db=3.8e9)),
    ("QPL9547", "low noise amplifier", "amp", dict(gain_db_dc=18.0, f3db=4.4e9)),
    ("QPQ1903", "baw filter", "filter", dict(f0=1.9e9, bw=0.06e9, order=7, ripple_db=0.05)),
    ("QPQ1904", "baw filter", "filter", dict(f0=2.6e9, bw=0.08e9, order=7, ripple_db=0.05)),
    ("TR50X", "termination", "term", dict()),
]

DOC_KINDS = ["datasheet", "application note", "test report", "layout guide", "errata"]

BODY_SNIPPETS = {
    "datasheet": (
        "Electrical characteristics measured at 25 C with a 50 ohm reference impedance. "
        "Small signal gain, input and output return loss, and noise figure are specified "
        "over the operating band. Absolute maximum ratings must not be exceeded even "
        "momentarily. Package is a laminate module with a ground paddle that must be "
        "soldered for thermal and RF performance."
    ),
    "application note": (
        "This note covers the recommended matching network, bias sequencing, and layout "
        "practice. Bias the device through a quarter wave line or a wideband choke and "
        "decouple close to the pin. Keep the ground return path short: via stitching "
        "around the ground paddle dominates measured stability at the high end of the band."
    ),
    "test report": (
        "Verification sweep across temperature and supply corners. Each unit was measured "
        "on a calibrated two port vector network analyzer using a short open load thru "
        "calibration. Return loss, insertion loss, gain flatness, and group delay ripple "
        "were recorded and compared against the specification limits."
    ),
    "layout guide": (
        "Recommended land pattern, stackup, and keepout. The transmission line impedance "
        "on the input and output should be held to 50 ohms with a controlled dielectric. "
        "Avoid routing digital control traces underneath the RF path or coupling will show "
        "up as spurious content in the transmitted signal."
    ),
    "errata": (
        "Known issue affecting an early production lot. Under a specific bias and "
        "temperature combination the device can exhibit a stability margin below the "
        "specified limit near the upper band edge. Mitigation is a series resistor on the "
        "bias line. Later date codes are not affected."
    ),
}


def build(out_dir):
    rng = random.Random(SEED)
    os.makedirs(out_dir, exist_ok=True)
    ts_dir = os.path.join(out_dir, "touchstone")
    doc_dir = os.path.join(out_dir, "documents")
    os.makedirs(ts_dir, exist_ok=True)
    os.makedirs(doc_dir, exist_ok=True)

    manifest = {"parts": [], "documents": [], "datasets": []}

    for part_number, family, kind, params in PART_PLAN:
        manifest["parts"].append({
            "part_number": part_number,
            "family": family,
            "description": "%s %s" % (part_number, family),
        })
        for rev_index, revision in enumerate(["A", "B"]):
            if kind == "filter":
                net = make_filter(part_number, params["f0"], params["bw"], params["order"], 201, rng,
                                  params.get("ripple_db", 0.0))
                fmt = "DB"
            elif kind == "amp":
                net = make_amplifier(part_number, params["gain_db_dc"] - rev_index * 0.4,
                                     params["f3db"], 201, rng)
                fmt = "MA"
            else:
                net = make_termination(part_number, 121, rng)
                fmt = "RI"
            ext = ".s%dp" % net.ports
            filename = "%s_rev%s%s" % (part_number, revision, ext)
            with open(os.path.join(ts_dir, filename), "w") as handle:
                handle.write(touchstone.dumps(net, fmt=fmt))
            manifest["datasets"].append({
                "part_number": part_number,
                "revision": revision,
                "path": os.path.join("touchstone", filename),
                "ports": net.ports,
                "format": fmt,
            })

        for doc_index, doc_kind in enumerate(DOC_KINDS):
            title = "%s %s" % (part_number, doc_kind)
            body = "%s\n\n%s\n\nApplies to %s in the %s family. Document revision %s." % (
                title.upper(), BODY_SNIPPETS[doc_kind], part_number, family,
                rng.choice(["1.0", "1.1", "2.0", "2.3"]),
            )
            filename = "%s_%s.txt" % (part_number, doc_kind.replace(" ", "_"))
            with open(os.path.join(doc_dir, filename), "w") as handle:
                handle.write(body)
            manifest["documents"].append({
                "part_number": part_number,
                "title": title,
                "kind": doc_kind,
                "path": os.path.join("documents", filename),
            })

    with open(os.path.join(out_dir, "manifest.json"), "w") as handle:
        json.dump(manifest, handle, indent=2, sort_keys=True)

    return manifest


def main():
    here = os.path.dirname(os.path.abspath(__file__))
    manifest = build(os.path.join(here, "corpus"))
    print("parts     %d" % len(manifest["parts"]))
    print("datasets  %d" % len(manifest["datasets"]))
    print("documents %d" % len(manifest["documents"]))


if __name__ == "__main__":
    main()
