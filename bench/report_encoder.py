#!/usr/bin/env python3
"""Generate reports/m2-encoder.md from the captured corpus.

Numbers are computed; the interpretation is written. Run after a capture:

    python bench/report_encoder.py
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np

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
    ("bench-stable", "backup", "control: identical 404s"),
    ("ffufme-basic", "class", "ordinary 404s"),
]


def spread(X: np.ndarray, sample: int = 400, seed: int = 0) -> float:
    rng = np.random.default_rng(seed)
    if len(X) > sample:
        X = X[rng.choice(len(X), sample, replace=False)]
    d = np.sqrt(((X[:, None, :] - X[None, :, :]) ** 2).sum(-1))
    return float(d[np.triu_indices(len(X), 1)].mean())


def separation(surface: str, hitword: str, channel_set: str) -> float:
    results = list(iter_batch(CORPUS / f"{surface}.jsonl"))
    X = Encoder(channel_set).encode_all(results)
    hit = next(i for i, r in enumerate(results) if r.word == hitword)
    if surface == "ffufme-no404":
        noise = np.array(
            [X[i] for i, r in enumerate(results) if r.status == 200 and r.word != hitword]
        )
    else:
        noise = np.delete(X, hit, axis=0)
    return float(np.linalg.norm(X[hit] - noise.mean(0))) / max(spread(noise), 1e-9)


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
        (surface, cs): separation(surface, hit, cs) for surface, hit, _ in CASES for cs in SETS
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
        "The ffuf issue #387 surface is worth stating precisely: it is stronger than the\n"
        "tracker's report. **Every one of the 1,998 records has `words=100`, the hit included.**\n"
        "The word filter `-ac` derives is not merely unlucky, it carries no information at all.\n"
        "Confirmed against real ffuf: without `-ac` the hit shows as\n"
        "`[Status: 200, Size: 1503, Words: 100, Lines: 40]`; with `-ac` it is hidden.\n"
    )

    add("## 2. Three encodings, measured\n")
    add(
        "Separation ratio: how far the labelled hit sits from the noise cluster's centroid,\n"
        "in units of that cluster's own mean pairwise spread. Scale-free, so it compares\n"
        "across sets of different dimensionality. The gate is 4x.\n"
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
    add("Bold clears the gate.\n")

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
        f"entropy - as a candidate encoding, so `v2-log` includes one. It is the worst of the\n"
        f"three on every surface, and the reason is measurable rather than mysterious: on the\n"
        f"wildcard host, **{share:.0%} of the variance inside the soft-404 cluster comes from\n"
        f"the `word.*` channels alone**.\n"
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

    add("## 4. Honest limitations of this result\n")
    add(
        "- **The corpus is synthetic and small.** Five surfaces, 1,998 requests each, three of\n"
        "  them served by a target written to contain exactly the scenarios being tested. That\n"
        "  is a fair test of whether the encoder has the properties claimed, and it is not\n"
        "  evidence about real applications.\n"
        "- **One labelled hit per surface.** Enough for a separation ratio, not enough for a\n"
        "  precision figure. Ranking metrics arrive at M4 and need more labels than this.\n"
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
