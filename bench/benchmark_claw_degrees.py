#!/usr/bin/env python3
"""The one thing the connectome contributed that a random projection would not suggest.

`reports/connectome-on-workload.md` found that the measured wiring beats a plain random
projection and does *not* beat its own degree-preserving null. So whatever advantage exists
is not in which glomerulus reaches which Kenyon cell - it is in how unevenly the claws are
spread. That is a histogram of 13 numbers, which means it can be shipped without the 508 MB
connectome and, unlike the wiring, applies at any channel count.

This tests it on the encoder the tool actually uses, decomposing the three things that
differ from the published uniform fan-in so the effect can be attributed:

  A  uniform 6        the published baseline, and today's default
  B  uniform 5        mean-matched - the measured mean is 5.4, not 6
  C  degree-sampled   the measured histogram

If B explains it, the result is "use fewer claws" and has nothing to do with the fly. If C
beats both, it is the spread.

    python bench/benchmark_claw_degrees.py
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from scipy.stats import mannwhitneyu

sys.path.insert(0, str(Path(__file__).resolve().parent))

from capture import SURFACES  # noqa: E402

from flypaper import REPO_ROOT, provenance_footer  # noqa: E402
from flypaper.brain.flyhash import (  # noqa: E402
    FlyHash,
    claw_degrees,
    degree_sampled_projection,
    random_projection,
)
from flypaper.ingest.stream import iter_batch  # noqa: E402
from flypaper.rank.score import Ranker  # noqa: E402

CORPUS = REPO_ROOT / "bench" / "corpus"
OUT = REPO_ROOT / "reports" / "claw-degrees.md"
#: Every rank, kept, so the written interpretation can be corrected against the numbers
#: without paying for the whole sweep again.
RAW = REPO_ROOT / "reports" / "data" / "claw-degrees.json"
CHANNEL_SET = "v3-response"
#: Measured, not asserted: the file grew when attribution was added to it and three
#: reports went stale claiming the old number.
_CLAW_FILE_BYTES = (REPO_ROOT / "flypaper" / "brain" / "claw-degrees.json").stat().st_size
SURFACES_USED = ("bench-mixed", "bench-token", "bench-collide", "bench-stable")
SEEDS = tuple(range(64))
VARIANTS = ("uniform-6", "uniform-5", "degree-sampled")


def projection_for(kind: str, n_channels: int, n_kc: int, rng: np.random.Generator):
    if kind == "uniform-6":
        return random_projection(n_channels, n_kc, 6, rng=rng)
    if kind == "uniform-5":
        return random_projection(n_channels, n_kc, 5, rng=rng)
    return degree_sampled_projection(n_channels, n_kc, rng=rng)


def worst_rank(surface: str, kind: str, seed: int) -> float:
    """Where the hardest labelled hit lands, offline, out of the whole surface."""
    results = list(iter_batch(CORPUS / f"{surface}.jsonl"))
    hits = set(SURFACES[surface]["hits"])
    ranker = Ranker(channel_set=CHANNEL_SET, projection="random", seed=seed)
    rng = np.random.default_rng(seed)
    ranker.flyhash = FlyHash(
        projection_for(kind, len(ranker.encoder), ranker.n_kc, rng), sparsity=ranker.sparsity
    )
    ranker.observe_only(results)
    scored = sorted(
        (ranker.score_against_baseline(r) for r in results),
        key=lambda s: s.novelty,
        reverse=True,
    )
    ranks = [i + 1 for i, s in enumerate(scored) if s.result.word in hits]
    return float(max(ranks)) if ranks else float("nan")


def main() -> int:
    argparse.ArgumentParser(description=__doc__).parse_args()
    table: dict[str, dict[str, np.ndarray]] = {}
    for surface in SURFACES_USED:
        if not (CORPUS / f"{surface}.jsonl").exists():
            continue
        print(f"  {surface} ...", file=sys.stderr)
        table[surface] = {
            kind: np.array([worst_rank(surface, kind, s) for s in SEEDS]) for kind in VARIANTS
        }
    RAW.parent.mkdir(parents=True, exist_ok=True)
    RAW.write_text(json.dumps({k: {j: v.tolist() for j, v in r.items()} for k, r in table.items()}))
    write_report(table)
    return 0


def write_report(table: dict) -> None:
    degrees = claw_degrees()
    lines: list[str] = []
    add = lines.append

    add("# The fly's fan-in spread, without the fly\n")
    add(
        "[connectome-on-workload.md](connectome-on-workload.md) found that the measured "
        "wiring beats a plain random projection and does **not** beat its own "
        "degree-preserving null. Whatever advantage exists is therefore not in which "
        "glomerulus reaches which Kenyon cell. It is in how unevenly the claws are spread - "
        "and that is a histogram of 13 numbers.\n"
    )
    add(
        f"The published FlyHash gives every Kenyon cell the same fan-in, usually 6. The "
        f"measured circuit gives them {degrees.min()} to {degrees.max()}, mean "
        f"{degrees.mean():.2f}, s.d. {degrees.std():.2f}. Shipped as "
        f"`flypaper/brain/claw-degrees.json` ({_CLAW_FILE_BYTES} bytes, most of it the CC BY "
        f"attribution), it needs no download and applies at "
        f"any channel count - including the {CHANNEL_SET} encoder the tool actually uses, "
        f"which the connectome itself cannot be fed at all.\n"
    )
    add(
        f"Everything below is on `{CHANNEL_SET}`, offline mode, {len(SEEDS)} seeds per cell. "
        f"`uniform-5` is the control that matters as much as the connectome's own: if "
        f"matching the measured *mean* explains the effect, the result is 'use fewer claws' "
        f"and has nothing to do with the fly.\n"
    )

    add("## Where the hardest labelled hit lands\n")
    add("| Surface | variant | median | mean | p90 | worst |")
    add("|---|---|---:|---:|---:|---:|")
    for surface, row in table.items():
        for kind in VARIANTS:
            v = row[kind]
            add(
                f"| `{surface}` | {kind} | {np.median(v):.0f} | {v.mean():.1f} | "
                f"{np.percentile(v, 90):.0f} | {v.max():.0f} |"
            )
    add("")

    add("## Is the difference real\n")
    add(
        "Mann-Whitney U, one-sided, against `uniform-6`, on the rank itself. A surface where "
        "every variant ties is saturated and measures nothing. The worst case is reported "
        "beside it because the two do not always move together, and for this tool they are "
        "not equally important: a median of 8 against 9 is invisible to an operator, and a "
        "worst case of 482 is a hit nobody ever scrolls to.\n"
    )
    add("| Surface | `uniform-5` | `degree-sampled` | worst case: 6 / 5 / sampled |")
    add("|---|---|---|---|")
    for surface, row in table.items():
        base = row["uniform-6"]
        cells = []
        for kind in ("uniform-5", "degree-sampled"):
            if len(set(base)) == 1 and len(set(row[kind])) == 1 and base[0] == row[kind][0]:
                cells.append("tied - saturated")
                continue
            pv = mannwhitneyu(row[kind], base, alternative="less").pvalue
            verdict = "better" if pv < 0.05 else ("worse" if pv > 0.95 else "no difference")
            cells.append(f"p={pv:.4f} ({verdict})")
        worst = " / ".join(f"{row[k].max():.0f}" for k in VARIANTS)
        add(f"| `{surface}` | " + " | ".join(cells) + f" | {worst} |")
    add("")

    add("## Interpretation\n")
    mixed, token = table.get("bench-mixed"), table.get("bench-token")
    if mixed is not None:
        pv = mannwhitneyu(mixed["degree-sampled"], mixed["uniform-6"], alternative="less").pvalue
        add(
            f"**On a surface whose noise is several populations, it works.** `bench-mixed` "
            f"median worst-hit rank falls from {np.median(mixed['uniform-6']):.0f} to "
            f"{np.median(mixed['degree-sampled']):.0f}, p={pv:.4f}. That is the whole "
            f"positive result, and it rests on one surface.\n"
        )
    if token is not None:
        add(
            f"**On `bench-token` it trades a median it cannot spend for a tail that "
            f"matters.** The median goes the wrong way, "
            f"{np.median(token['uniform-6']):.0f} to "
            f"{np.median(token['degree-sampled']):.0f}, which is what the rank test sees and "
            f"why it reports a loss. But the mean falls from {token['uniform-6'].mean():.1f} "
            f"to {token['degree-sampled'].mean():.1f}, the p90 from "
            f"{np.percentile(token['uniform-6'], 90):.0f} to "
            f"{np.percentile(token['degree-sampled'], 90):.0f}, and the worst seed from "
            f"{token['uniform-6'].max():.0f} to {token['degree-sampled'].max():.0f}. One "
            f"position of median is invisible; a hit at rank {token['uniform-6'].max():.0f} "
            f"is one nobody finds. Read as a rank test this is a loss, and read as an "
            f"operator it is the better projection.\n"
        )
    if mixed is not None:
        five = mannwhitneyu(mixed["uniform-5"], mixed["uniform-6"], alternative="less").pvalue
        add(
            f"**It is the spread, not the mean.** The obvious deflationary explanation is "
            f"that the measured mean is 5.4 and the baseline uses 6, so the claim reduces to "
            f"'fewer claws'. `uniform-5` tests that directly and fails it: no different on "
            f"`bench-mixed` (p={five:.4f}), and much worse on `bench-token` and "
            f"`bench-collide`, where it turns a saturated surface back into an unreliable "
            f"one. Matching the mean is not merely insufficient, it is harmful. Only drawing "
            f"the fan-in from the measured *distribution* helps.\n"
        )
    add(
        "**Why it might work.** A uniform fan-in gives every Kenyon cell the same receptive "
        "field size, so the whole layer generalises at one scale. A spread of fan-ins gives "
        "some cells narrow fields and some wide, so a baseline made of several different "
        "response populations can be absorbed at several scales at once. That is a "
        "hypothesis consistent with the measurement and with where the effect appears; it "
        "is not established by it.\n"
    )
    add(
        "**What would change this.** One surface carries the positive result. The p-values "
        "are uncorrected across the variants and surfaces tried, and several were tried. "
        "The hits are planted rather than real. A second heterogeneous surface that "
        "reproduced it would be worth more than any further analysis of this one.\n"
    )

    OUT.write_text("\n".join(lines) + provenance_footer(seed=0))
    print(f"wrote {OUT.relative_to(REPO_ROOT)}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
