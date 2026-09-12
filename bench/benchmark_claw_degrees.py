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
from scipy.stats import mannwhitneyu, wilcoxon

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
SURFACES_USED = ("bench-mixed", "bench-sprawl", "bench-token", "bench-collide", "bench-stable")
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


def _saturated(a, b) -> bool:
    """Both variants gave the identical answer on every seed: the surface measures nothing."""
    return len(set(a)) == 1 and len(set(b)) == 1 and a[0] == b[0]


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
    add(
        "| Surface | `uniform-5` | `degree-sampled` | `degree-sampled` paired | "
        "seeds better / worse |"
    )
    add("|---|---|---|---|---|")
    for surface, row in table.items():
        base = row["uniform-6"]
        cells = []
        for kind in ("uniform-5", "degree-sampled"):
            if _saturated(base, row[kind]):
                cells.append("tied - saturated")
                continue
            pv = mannwhitneyu(row[kind], base, alternative="less").pvalue
            verdict = "better" if pv < 0.05 else ("worse" if pv > 0.95 else "no difference")
            cells.append(f"p={pv:.4f} ({verdict})")
        sampled = row["degree-sampled"]
        if _saturated(base, sampled):
            cells += ["tied - saturated", "-"]
        else:
            paired = wilcoxon(sampled, base, alternative="less").pvalue
            diff = sampled - base
            cells.append(f"p={paired:.4f}")
            cells.append(f"{int((diff < 0).sum())} / {int((diff > 0).sum())} of {len(base)}")
        add(f"| `{surface}` | " + " | ".join(cells) + " |")
    add("")
    add(
        "Both tests are reported because they disagree. Mann-Whitney treats the two sets of "
        "seeds as independent samples; Wilcoxon pairs them by seed, which is the more "
        "conservative reading and the defensible one here, since both variants are built "
        "from the same RNG stream. Where a result survives only the unpaired test, it is "
        "not a result.\n"
    )

    add("## Interpretation\n")
    mixed, sprawl = table.get("bench-mixed"), table.get("bench-sprawl")
    if mixed is not None and sprawl is not None:
        m_un = mannwhitneyu(mixed["degree-sampled"], mixed["uniform-6"], alternative="less")
        m_pa = wilcoxon(mixed["degree-sampled"], mixed["uniform-6"], alternative="less")
        s_un = mannwhitneyu(sprawl["degree-sampled"], sprawl["uniform-6"], alternative="less")
        add(
            f"**It did not replicate.** The first version of this measured `bench-mixed` "
            f"alone, found the measured fan-in spread cut the median worst-hit rank from "
            f"{np.median(mixed['uniform-6']):.0f} to {np.median(mixed['degree-sampled']):.0f} "
            f"at p={m_un.pvalue:.4f}, and said in as many words that a second heterogeneous "
            f"surface reproducing it would be worth more than any further analysis of the "
            f"first. `bench-sprawl` is that surface - seven populations against four, each "
            f"jittering internally, built and captured before the projection was run against "
            f"it. On it the median goes the *wrong* way, "
            f"{np.median(sprawl['uniform-6']):.0f} to "
            f"{np.median(sprawl['degree-sampled']):.0f}, p={s_un.pvalue:.4f}.\n"
        )
        add(
            f"**And the surviving result does not survive pairing.** On `bench-mixed` "
            f"itself, pairing the seeds instead of treating them as independent samples "
            f"takes p={m_un.pvalue:.4f} to p={m_pa.pvalue:.4f}. One nominally significant "
            f"result, on one surface, under one of two reasonable tests, with no correction "
            f"for the several variants and surfaces tried, is what noise looks like.\n"
        )
    add(
        "**So the honest verdict is that the measured fan-in distribution does not help.** "
        "That is a real answer to the question `reports/connectome-on-workload.md` raised, "
        "and it closes the last route by which the connectome was contributing anything to "
        "this tool's ranking. `--projection degree-sampled` stays in the code because it is "
        "the control that makes the connectome comparison interpretable, and because "
        "removing a variant because its result was negative is how a benchmark suite starts "
        "lying. It is not recommended and it is not the default.\n"
    )
    token = table.get("bench-token")
    if token is not None:
        add(
            f"**One thing is left, and it is small.** `bench-token` has a rare catastrophic "
            f"seed under `uniform-6` - the worst of 64 puts the hardest hit at rank "
            f"{token['uniform-6'].max():.0f}, and its p99 is "
            f"{np.percentile(token['uniform-6'], 99):.0f}. Under `degree-sampled` the worst "
            f"of 64 is {token['degree-sampled'].max():.0f}. That is one surface and a "
            f"handful of seeds, nowhere near enough to act on, and it is recorded here only "
            f"so that it is not rediscovered later and mistaken for a new result.\n"
        )
    if mixed is not None:
        five = mannwhitneyu(mixed["uniform-5"], mixed["uniform-6"], alternative="less").pvalue
        add(
            f"**`uniform-5` remains a clean negative.** Matching the measured *mean* fan-in "
            f"is not merely insufficient, it is harmful: no different on `bench-mixed` "
            f"(p={five:.4f}) and much worse on `bench-token` and `bench-collide`, where it "
            f"turns a surface every other variant saturates into an unreliable one. "
            f"Whatever the published fan-in of 6 is doing, moving it is not free.\n"
        )
    add(
        "**What this cost and what it bought.** A result was published on one surface and "
        "retracted on two. The retraction is the point: the falsification criterion was "
        "written into the report before the surface existed, which is the only reason it "
        "could fire. `bench-sprawl` stays in the corpus - it is the hardest surface here by "
        "a wide margin, every variant leaves the worst hit past rank 250, and that makes it "
        "the most useful thing to build against next.\n"
    )

    OUT.write_text("\n".join(lines) + provenance_footer(seed=0))
    print(f"wrote {OUT.relative_to(REPO_ROOT)}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
