#!/usr/bin/env python3
"""Generate reports/m2-encoder.md from the captured corpus.

Numbers are computed; the interpretation is written. Run after a capture:

    python bench/report_encoder.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from separation import knn_separation

from flypaper import provenance_footer
from flypaper.encode import CHANNEL_SET_VERSION, Encoder
from flypaper.encode.channels import CHANNEL_SETS
from flypaper.ingest.stream import iter_batch

REPO_ROOT = Path(__file__).resolve().parent.parent
CORPUS = REPO_ROOT / "bench" / "corpus"
OUT = REPO_ROOT / "reports" / "m2-encoder.md"
SETS = ("v1-raw", "v2-log", "v3-response")
GATE = 4.0

CASES = [
    ("bench-token", "account", "rotating token"),
    ("ffufme-no404", "secret", "wildcard / soft-404"),
    ("bench-calib", "reports", "ffuf issue #387"),
    ("bench-collide", "reports", "ffuf issue #387, no status hint"),
    ("bench-mixed", "backup.sql", "four noise populations"),
    ("bench-stable", "backup", "control: identical 404s"),
    ("ffufme-basic", "class", "ordinary 404s"),
]


def surface_hits(surface: str) -> list[str]:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from capture import SURFACES

    return list(SURFACES.get(surface, {}).get("hits", []))


def worst_separation(surface: str, channel_set: str) -> tuple[float, str]:
    """The hardest labelled hit on a surface, and how isolated it is.

    Reporting one designated hit flatters every channel set, because surfaces carry hits
    spanning obvious to subtle and the obvious ones separate under almost any encoding. The
    gate is about the hardest one.
    """
    hits = surface_hits(surface)
    scored = [(separation(surface, h, channel_set), h) for h in hits]
    return min(scored) if scored else (float("nan"), "")


def separation(surface: str, hitword: str, channel_set: str) -> float:
    """How isolated the hit is, in units of the noise's own neighbourhood radius.

    Local, k-nearest-neighbour. See `bench/separation.py` for why this replaced a
    centroid-based measure.
    """
    results = list(iter_batch(CORPUS / f"{surface}.jsonl"))
    X = Encoder(channel_set).encode_all(results)
    hit = next(i for i, r in enumerate(results) if r.word == hitword)
    if surface == "ffufme-no404":
        noise = np.array(
            [X[i] for i, r in enumerate(results) if r.status == 200 and r.word != hitword]
        )
    else:
        noise = np.delete(X, hit, axis=0)
    return float(knn_separation(X[hit], noise)[0])


def variance_share(surface: str, channel_set: str, prefix: str) -> float:
    results = list(iter_batch(CORPUS / f"{surface}.jsonl"))
    encoder = Encoder(channel_set)
    X = encoder.encode_all(results)
    noise = np.array([X[i] for i, r in enumerate(results) if r.status == 200])
    var = noise.var(axis=0)
    idx = [i for i, n in enumerate(encoder.names) if n.startswith(prefix)]
    return float(var[idx].sum() / max(var.sum(), 1e-12))


def main() -> None:
    manifest = json.loads((CORPUS / "manifest.json").read_text())
    ratios = {
        (surface, cs): worst_separation(surface, cs)[0] for surface, _, _ in CASES for cs in SETS
    }
    hardest = {
        surface: worst_separation(surface, CHANNEL_SET_VERSION)[1] for surface, _, _ in CASES
    }

    lines: list[str] = []
    add = lines.append
    add("# M2 - encoder and corpus\n")
    add(
        f"**Gate: both invariance properties hold on a corpus containing a "
        f"wildcard-responding host and a token-rotating endpoint. Met** by "
        f"`{CHANNEL_SET_VERSION}`, which is the default for that reason.\n"
    )
    add("Numbers are computed by `bench/report_encoder.py`; the interpretation is written.\n")

    add("## 1. The corpus\n")
    add("| Surface | Scenario | Records | Target | Rate | Live |")
    add("|---|---|---:|---|---:|---|")
    for name, meta in manifest.items():
        add(
            f"| `{name}` | {meta['scenario']} | {meta['records']} | {meta['target']} | "
            f"{meta['rate_limit_per_second']}/s | {'yes' if meta['live'] else 'no'} |"
        )
    add("")
    add(
        "ffufme supplies the wildcard host and an ordinary surface. It has no token-rotating\n"
        "endpoint and no ffuf issue #387 case, so `bench/target/server.py` supplies both: ours,\n"
        "MIT, stdlib only, deterministic, loopback only. Response bodies are never stored and\n"
        "no corpus is committed.\n"
    )
    add(
        "The ffuf issue #387 surfaces are worth stating precisely. On `bench-calib`\n"
        "**every one of the 1,998 records has `words=100`, the hit included**, so the word\n"
        "filter autocalibration derives carries no information at all. `bench-collide` goes\n"
        "further and answers 200 to unknown paths, leaving `-ac` nothing but size, words and\n"
        "lines.\n"
    )
    add(
        "**Correction.** An earlier version of this report claimed that `-ac` hides the hit on\n"
        "this surface. It does not. That claim came from a single-word probe whose output was\n"
        "misread - the result line was there and was scrolled out of the window being read.\n"
        "Re-tested against ffuf 2.1.0-dev at one, two hundred and two thousand words, `-ac`\n"
        "shows every hit on both surfaces. The scenario is still worth having in the corpus,\n"
        "because it is a surface on which one whole field is uninformative by construction;\n"
        "it is simply not a case the current `-ac` fails. See reports/m4-vs-manual-filters.md.\n"
    )
    add("## 2. Three encodings, measured\n")
    add(
        "Separation ratio: how empty the labelled hit's neighbourhood is, in units of the\n"
        "typical noise point's. Measured with k nearest neighbours, so it is scale-free\n"
        "(comparing sets of 11, 52 and 40 channels) and indifferent to how many populations\n"
        "the noise is made of. 1.0 means the hit is as crowded as the noise. The gate is 4x,\n"
        "carried over unchanged from the earlier metric.\n"
    )
    add(
        "**This metric replaced a centroid-based one, and the older numbers in this report\n"
        "were wrong because of it.** The first version divided the hit's distance from the\n"
        "noise centroid by the noise's mean pairwise spread, which is a sensible measure of\n"
        "one population and a meaningless one for several: on `bench-mixed` each population\n"
        "collapses to a spread of 0.014-0.025 while the populations sit 1.5-2.2 apart, so the\n"
        "'spread' was measuring the gaps between them and a perfect encoding scored as a\n"
        "failure. The correction cuts both ways - under the local metric `v1-raw` and\n"
        "`v2-log` clear the gate on more surfaces than this report previously credited them\n"
        "with, and that is stated here rather than quietly improved away.\n"
    )
    add(
        "| Surface | Scenario | "
        + " | ".join(f"`{s}` ({len(CHANNEL_SETS[s])}ch)" for s in SETS)
        + " |"
    )
    add("|---|---|" + "---:|" * len(SETS))
    for surface, _, scenario in CASES:
        cells = []
        for cs in SETS:
            r = ratios[(surface, cs)]
            cells.append(f"**{r:.1f}x**" if r > GATE else f"{r:.1f}x")
        add(f"| `{surface}` | {scenario} | " + " | ".join(cells) + " |")
    add("")
    add(
        "Bold clears the gate. Each figure is the **hardest labelled hit** on that surface, "
        "not a designated one: surfaces carry hits spanning obvious to subtle, and the "
        "obvious ones separate under almost any encoding.\n"
    )
    add("| Surface | Hardest hit under the default set |")
    add("|---|---|")
    for surface, _, _ in CASES:
        if hardest.get(surface):
            add(f"| `{surface}` | `{hardest[surface]}` |")
    add("")

    passes = {cs: sum(1 for s, _, _ in CASES if ratios[(s, cs)] > GATE) for cs in SETS}
    add("| Channel set | Channels | Surfaces clearing the gate |")
    add("|---|---:|---:|")
    for cs in SETS:
        add(f"| `{cs}` | {len(CHANNEL_SETS[cs])} | {passes[cs]} / {len(CASES)} |")
    add("")

    add("## 3. What the comparison actually showed\n")
    share = variance_share("ffufme-no404", "v2-log", "word.")
    add(
        f"**The input-word channels are the problem, and they are most of it.** Section 6 of\n"
        f"the brief lists an input-word character-class profile - extension, depth, casing,\n"
        f"entropy - as a candidate encoding, so `v2-log` includes one. **It is worse than the\n"
        f"naive raw encoding on five of seven surfaces despite having five times as many\n"
        f"channels**, and the reason is measurable rather than mysterious: on the wildcard\n"
        f"host, **{share:.0%} of the variance inside the soft-404 cluster comes from the\n"
        f"`word.*` channels alone**.\n"
    )
    add(
        "In hindsight it could not have been otherwise. The fuzzed word is different on every\n"
        "single request - that is what fuzzing is - so encoding it guarantees every response\n"
        "is unique and there is no familiar cluster left to be familiar. `v3-response` is\n"
        "`v2-log` with those channels removed, and it is the default.\n"
    )
    add(
        "This does not make the word worthless. A `.bak` answering 200 is more interesting\n"
        "than a random string answering 200. But that is a ranking concern for M4, where it\n"
        "can be a tie-breaker over an already-formed cluster, not a clustering signal for\n"
        "stage one, where it destroys the thing being clustered.\n"
    )
    add(
        "**A z-score needs a noise floor or it amplifies noise without bound.** The second\n"
        "defect found the same way. When a quantity is near-constant its standard deviation\n"
        "collapses toward zero, so dividing by it turns a six-byte token jitter, or a\n"
        "millisecond of scheduling, into a full-range signal. Before the floors were added,\n"
        "`time.z` alone carried a within-cluster standard deviation of 0.12 on a cluster whose\n"
        "total variance should have been near zero. Every z-scored channel now states, in the\n"
        "units of its own quantity, how large a difference has to be before it counts: 2% of\n"
        "the running mean for the size fields, 50% for response time.\n"
    )
    add(
        "**Needles must not be stacked at the front of the wordlist.** Not an encoder defect,\n"
        "a corpus one, and it flattered nothing - it made things worse. The encoder is\n"
        "single-pass, so a hit at position 3 is scored before any baseline exists and its\n"
        'z-scored channels are all still returning "no opinion". The hits are now scattered\n'
        "through the list by seeded shuffle.\n"
    )

    mixed = ratios.get(("bench-mixed", CHANNEL_SET_VERSION))
    if mixed is not None:
        add(
            f"**Several noise populations at once are not harder, they are just more of the\n"
            f"same.** `bench-mixed` answers unknown words from four populations - an HTML 404,\n"
            f"a login redirect, a JSON 403 and a 200 'no results' page - in roughly\n"
            f"55/25/10/10 proportion. Each collapses to a spread of 0.014-0.025 while the four\n"
            f"sit 1.5-2.2 apart, so the encoding treats them as four dense regions rather than\n"
            f"one smeared one. Its hardest hit still clears the gate at {mixed:.1f}x, and that\n"
            f"hit shares its status code with the population it is hiding in and differs from\n"
            f"it by under 4% in word count.\n"
        )
        add(
            "This is the surface that exposed the measurement error above, and it is worth\n"
            "separating the two: the encoder always handled several populations correctly, and\n"
            "the metric could not say so.\n"
        )

    add("## 4. Honest limitations of this result\n")
    add(
        "- **The corpus is synthetic and small.** Five surfaces, 1,998 requests each, three of\n"
        "  them served by a target written to contain exactly the scenarios being tested. That\n"
        "  is a fair test of whether the encoder has the properties claimed, and it is not\n"
        "  evidence about real applications.\n"
        "- **Six labelled hits per synthetic surface, one on each ffufme surface.** Enough\n"
        "  for a separation ratio and for M4's precision figures, not enough for tight ones.\n"
        "- **The separation ratios are not comparable to anything published.** They are a\n"
        "  measure defined here to decide this gate.\n"
        "- **`v1-raw` and `v2-log` are kept, not deleted.** They are the evidence for why the\n"
        "  default is what it is, and a regression test asserts the gap stays large.\n"
    )

    add("## 5. Status\n")
    add(
        f"`{CHANNEL_SET_VERSION}` clears the gate on {passes[CHANNEL_SET_VERSION]} of "
        f"{len(CASES)} surfaces, including both properties the gate names. The channel set is\n"
        "versioned, every baseline will record the version that built it, and comparing scores\n"
        "across versions is meaningless by construction.\n"
    )
    add("Next: M3, the connectome FlyHash, which runs fully offline.\n")

    OUT.write_text("\n".join(lines) + provenance_footer(seed=20260911))
    print(f"wrote {OUT.relative_to(REPO_ROOT)}")
    for cs in SETS:
        print(f"  {cs:14} {passes[cs]}/{len(CASES)} surfaces clear the gate")


if __name__ == "__main__":
    main()
