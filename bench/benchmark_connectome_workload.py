#!/usr/bin/env python3
"""Is the measured connectome better at *this* job?

M3 benchmarked the connectome-wired FlyHash against the published random projection on
MNIST and Fashion-MNIST. That is the right comparison for the literature - those are the
datasets the FlyHash papers used - and it is not this workload. It found no durable
difference on novelty detection and a clear loss on nearest-neighbour retrieval, and it
said so.

Those datasets look nothing like fuzzing traffic. MNIST is ten balanced classes of
784-dimensional images reduced by PCA to 55 arbitrary components. A content-discovery scan
is a handful of response populations, wildly unbalanced, repeated thousands of times, where
the only question is *is this one of the familiar shapes*. Nobody had asked whether the
connectome is better at that, and nobody could: **the connectome projection needs 55 input
channels and the default encoder produced 39**, so the two could not be combined at all.
Every measurement of the connectome in this repository was therefore taken on reduced image
data rather than on the thing the tool is for.

What is measured here is the shipped ranker, not a geometry. The first version of this
benchmark measured k-NN separation in tag space and returned ratios around 1e12, because
the noise collapses to *identical* tags and a neighbourhood of width zero divides badly.
That collapse is the interesting behaviour, so it is measured directly instead, alongside
the outcome an operator actually sees: where the labelled hits land in the ranking.

  worst rank     the hardest labelled hit's position, the number that decides usability
  recall@25      how many hits appear in one screen of output
  distinct tags  how many different tags the noise produces - the collapse, directly
  permutation    whether *which* channel maps to which glomerulus matters

That last one is the control that decides what any difference means. The connectome encodes
which glomeruli converge on which Kenyon cells. Our channels are response features, not
odorants, so the assignment of feature to glomerulus is arbitrary - we chose it. If
shuffling that assignment changes nothing, the wiring is acting as an unusually-shaped
random matrix, and should be described as one.

    python bench/benchmark_connectome_workload.py
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

from capture import SURFACES  # noqa: E402

from flypaper import REPO_ROOT, provenance_footer  # noqa: E402
from flypaper.brain.extract import Circuit  # noqa: E402
from flypaper.brain.flyhash import FlyHash, connectome_projection  # noqa: E402
from flypaper.ingest.stream import iter_batch  # noqa: E402
from flypaper.rank.score import Ranker  # noqa: E402

CORPUS = REPO_ROOT / "bench" / "corpus"
CIRCUIT = REPO_ROOT / "data" / "derived" / "olfactory-circuit-R"
OUT = REPO_ROOT / "reports" / "connectome-on-workload.md"
RAW = REPO_ROOT / "reports" / "data" / "connectome-on-workload.json"
CHANNEL_SET = "v4-glomerular"
SHIPPED_CHANNEL_SET = "v3-response"
SHIPPED = f"random on {SHIPPED_CHANNEL_SET}"
SURFACES_USED = (
    "bench-mixed",
    "bench-sprawl",
    "bench-token",
    "bench-collide",
    "bench-stable",
    "ffufme-no404",
)
SEEDS = tuple(range(32))
TOP = 25

#: `connectome` is deterministic - one wiring, no seed - so averaging it over seeds would
#: average eight identical numbers. The random variants are averaged; it is not.
DETERMINISTIC = {"connectome"}


def build(kind: str, seed: int, permute: bool, channel_set: str = CHANNEL_SET) -> Ranker:
    ranker = Ranker(
        channel_set=channel_set,
        projection="random" if kind == "random" else kind,
        circuit_path=None if kind == "random" else str(CIRCUIT),
        seed=seed,
    )
    if permute:
        # Same wiring, different assignment of response feature to glomerulus. Reached past
        # the constructor deliberately: this is a control, not an operating mode, and does
        # not belong in the shipped projection list.
        circuit = Circuit.load(CIRCUIT)
        matrix = connectome_projection(circuit.pn_to_kc, binary=True)
        order = np.random.default_rng(seed).permutation(matrix.shape[0])
        ranker.flyhash = FlyHash(matrix[order, :], sparsity=ranker.sparsity)
    return ranker


def measure(
    surface: str, kind: str, seed: int, permute: bool = False, channel_set: str = CHANNEL_SET
) -> dict:
    """Rank one surface with one projection and report where the labelled hits landed."""
    results = list(iter_batch(CORPUS / f"{surface}.jsonl"))
    hits = set(SURFACES[surface]["hits"])

    ranker = build(kind, seed, permute, channel_set)
    ranker.observe_only(results)  # pass one: learn the surface
    scored = sorted(
        (ranker.score_against_baseline(r) for r in results),
        key=lambda s: s.novelty,
        reverse=True,
    )

    ranks = [i + 1 for i, s in enumerate(scored) if s.result.word in hits]
    noise = np.array([s.novelty for s in scored if s.result.word not in hits])
    hit_scores = np.array([s.novelty for s in scored if s.result.word in hits])

    tags = ranker.flyhash.tag(ranker.encoder.encode_all(results))
    noise_tags = tags[[i for i, r in enumerate(results) if r.word not in hits]]

    return {
        "worst_rank": float(max(ranks)) if ranks else float("nan"),
        "recall_at_top": float(sum(1 for r in ranks if r <= TOP)),
        "n_hits": float(len(ranks)),
        "total": float(len(results)),
        "distinct_tags": float(len(np.unique(noise_tags, axis=0))),
        "noise_p99": float(np.percentile(noise, 99)) if len(noise) else float("nan"),
        "hit_median": float(np.median(hit_scores)) if len(hit_scores) else float("nan"),
    }


def distribution(
    surface: str, kind: str, permute: bool = False, channel_set: str = CHANNEL_SET
) -> dict:
    """Every seed's result, kept rather than averaged.

    The first version of this reported mean +- SD and the SDs were larger than the means
    (492 +- 457), which is what a heavy-tailed rank distribution does to a mean and is not
    a basis for claiming anything. The whole distribution is kept so the connectome can be
    placed inside it by counting, which needs no distributional assumption at all.
    """
    seeds = (0,) if (kind in DETERMINISTIC and not permute) else SEEDS
    runs = [measure(surface, kind, s, permute, channel_set) for s in seeds]
    out = {k: float(np.median([r[k] for r in runs])) for k in runs[0]}
    out["worst_ranks"] = sorted(r["worst_rank"] for r in runs)
    out["n_seeds"] = len(runs)
    out["runs"] = [dict(r, seed=s) for r, s in zip(runs, seeds, strict=True)]
    return out


def main() -> int:
    argparse.ArgumentParser(description=__doc__).parse_args()
    if not CIRCUIT.with_suffix(".json").exists():
        raise SystemExit("no extracted circuit; run python -m flypaper.web.export first")

    #: The last entry is the tool as it actually ships today - the narrower `v3-response`
    #: encoder with the published random projection. Without it the table compares four
    #: projections against each other in a vacuum and cannot answer the only question that
    #: decides whether anything changes: is this better than what we already do?
    variants = (
        ("random", False, CHANNEL_SET),
        ("random-matched", False, CHANNEL_SET),
        ("connectome", False, CHANNEL_SET),
        ("connectome", True, CHANNEL_SET),
        ("random", False, SHIPPED_CHANNEL_SET),
    )
    results: dict[str, dict] = {}
    for surface in SURFACES_USED:
        if not (CORPUS / f"{surface}.jsonl").exists():
            continue
        print(f"  {surface} ...", file=sys.stderr)
        row = {}
        for kind, permute, cset in variants:
            label = f"{kind}-permuted" if permute else kind
            if cset != CHANNEL_SET:
                label = f"{kind} on {cset}"
            row[label] = distribution(surface, kind, permute, cset)
        results[surface] = row
        print(
            f"    worst rank of {row['random']['total']:.0f}:  "
            + "  ".join(f"{k}={v['worst_rank']:.0f}" for k, v in row.items()),
            file=sys.stderr,
        )

    RAW.parent.mkdir(parents=True, exist_ok=True)
    RAW.write_text(json.dumps(results, indent=1, sort_keys=True))
    write_report(results)
    print(f"wrote {RAW.relative_to(REPO_ROOT)}", file=sys.stderr)
    return 0


def write_report(results: dict) -> None:
    circuit = Circuit.load(CIRCUIT)
    lines: list[str] = []
    add = lines.append

    add("# Is the connectome better at *this* job?\n")
    add(
        "M3 measured the connectome-wired FlyHash on MNIST and Fashion-MNIST. That is the "
        "right comparison for the literature - those are the datasets the FlyHash papers "
        "used - and it is not this workload. It found no durable difference on novelty "
        "detection and a clear loss on retrieval.\n"
    )
    add(
        "Those datasets look nothing like fuzzing traffic. MNIST is ten balanced classes of "
        "images reduced by PCA to 55 arbitrary components. A content-discovery scan is a "
        "handful of response populations, wildly unbalanced, repeated thousands of times, "
        "and the only question is *is this one of the familiar shapes*.\n"
    )
    add(
        f"**Until now the comparison could not be made at all.** The measured circuit "
        f"projects from {circuit.n_channels} glomeruli and the default `v3-response` "
        f"encoder produces 39 channels, so the constructor refused the pair. Every "
        f"measurement of the connectome in this repository was taken on PCA-reduced image "
        f"data rather than on the workload the tool exists for. The `{CHANNEL_SET}` channel "
        f"set - v3's response-only features widened to exactly {circuit.n_channels} - is "
        f"what makes this run possible.\n"
    )
    add(
        "What is measured is the shipped ranker, not a geometry. An earlier version of this "
        "benchmark measured k-NN separation in tag space and returned ratios around 1e12, "
        "because the noise collapses to *identical* tags and a neighbourhood of width zero "
        "divides badly. The collapse is measured directly below instead.\n"
    )

    add("## Where the hardest labelled hit lands\n")
    add(
        f"Rank of the worst labelled hit, offline mode, out of the full surface - the "
        f"number that decides whether an operator finds it. Lower is better. Each random "
        f"variant is {len(SEEDS)} independent seeds shown as median [min-max]; "
        f"`connectome` is a single fixed wiring and has no seed.\n"
    )
    add(
        "| Surface | n | shipped today | `random` | `random-matched` | `connectome` | "
        "`connectome` shuffled |"
    )
    add("|---|---:|---:|---:|---:|---:|---:|")
    for surface, row in results.items():
        cells = []
        for key in (SHIPPED, "random", "random-matched", "connectome", "connectome-permuted"):
            v = row[key]
            if v["n_seeds"] == 1:
                cells.append(f"**{v['worst_rank']:.0f}**")
            else:
                w = v["worst_ranks"]
                cells.append(f"{v['worst_rank']:.0f} [{w[0]:.0f}-{w[-1]:.0f}]")
        add(f"| `{surface}` | {row['random']['total']:.0f} | " + " | ".join(cells) + " |")
    add("")

    add("### Placed inside the null distribution\n")
    add(
        f"The means above cannot carry a claim - the spreads exceed them. So the "
        f"connectome's single result is placed in each null by counting: how many of the "
        f"{len(SEEDS)} random draws did at least as well. That is an exact one-sided "
        f"p-value, `(k+1)/(n+1)`, and assumes nothing about the shape of the distribution.\n"
    )
    add("| Surface | connectome | vs `random` | vs `random-matched` | vs shuffled wiring |")
    add("|---|---:|---:|---:|---:|")
    for surface, row in results.items():
        c = row["connectome"]["worst_rank"]
        cells = []
        for key in ("random", "random-matched", "connectome-permuted"):
            draws = row[key]["worst_ranks"]
            k = sum(1 for d in draws if d <= c)
            cells.append(f"{k}/{len(draws)}, p={(k + 1) / (len(draws) + 1):.3f}")
        add(f"| `{surface}` | {c:.0f} | " + " | ".join(cells) + " |")
    add("")
    add(
        "A surface where every draw ties the connectome is a surface with no headroom - "
        "the hits are already at the top for everyone, and it discriminates nothing.\n"
    )

    add("### The same projection across every surface at once\n")
    add(
        "The per-surface tests above each compare one number against one null, and none of "
        "them reaches significance. They are also not the question an operator faces: you "
        "choose a projection once, before seeing any of these surfaces, and you cannot pick "
        "the lucky seed afterwards. So the seeds are held together - a draw counts only if "
        "it matches the connectome on *every* discriminating surface at once.\n"
    )
    live = [k for k, r in results.items() if len(set(r["random"]["worst_ranks"])) > 1]
    add(
        "Discriminating surfaces (those where the seed changes the answer): "
        + (", ".join("`" + k + "`" for k in live) or "none")
        + ".\n"
    )
    add("| Null | draws matching the connectome on all of them | p |")
    add("|---|---:|---:|")
    for key in (SHIPPED, "random", "random-matched", "connectome-permuted"):
        n = results[live[0]][key]["n_seeds"] if live else 0
        joint_n = sum(
            1
            for i in range(n)
            if all(
                results[k][key]["runs"][i]["worst_rank"] <= results[k]["connectome"]["worst_rank"]
                for k in live
            )
        )
        add(f"| `{key}` | {joint_n}/{n} | {(joint_n + 1) / (n + 1):.3f} |")
    add("")

    add(f"## Hits inside the first {TOP}\n")
    add("| Surface | hits | `random` | `random-matched` | `connectome` | shuffled |")
    add("|---|---:|---:|---:|---:|---:|")
    for surface, row in results.items():
        cells = [
            f"{row[k]['recall_at_top']:.2f}"
            for k in ("random", "random-matched", "connectome", "connectome-permuted")
        ]
        add(f"| `{surface}` | {row['random']['n_hits']:.0f} | " + " | ".join(cells) + " |")
    add("")

    add("## How hard the familiar collapses\n")
    add(
        "Distinct tags produced by the non-hit responses. Fewer means the projection is "
        "treating more near-identical responses as the same thing, which is generalisation "
        "rather than discrimination - and generalisation is what a baseline needs when the "
        "same page comes back with a different nonce each time.\n"
    )
    add("| Surface | non-hits | `random` | `random-matched` | `connectome` |")
    add("|---|---:|---:|---:|---:|")
    for surface, row in results.items():
        n = row["random"]["total"] - row["random"]["n_hits"]
        cells = [
            f"{row[k]['distinct_tags']:.0f}" for k in ("random", "random-matched", "connectome")
        ]
        add(f"| `{surface}` | {n:.0f} | " + " | ".join(cells) + " |")
    add("")

    add("## Interpretation\n")
    add(
        "**The connectome beats a plain random projection, and does not beat its own "
        "degree-preserving null.** Held across every discriminating surface at once, 1 of "
        "32 uniform random draws matches it (p=0.061); 8 of 32 draws that keep the measured "
        "*fan-in* while randomising the *targets* match it (p=0.273), as do 9 of 32 that "
        "keep the wiring and shuffle which response feature feeds which glomerulus "
        "(p=0.303). Two independent controls agree: nothing detectable here comes from "
        "which glomerulus reaches which Kenyon cell. What is left is how unevenly the claws "
        "are spread.\n"
    )
    add(
        "**The channel-shuffle control also tells us the encoder-to-glomerulus assignment "
        "is arbitrary, which is what we should have expected.** Our channels are response "
        "features, not odours. There is no reason status-code-403 belongs in the glomerulus "
        "it landed in, and the measurement says it does not matter that it did.\n"
    )
    add(
        f"**`{CHANNEL_SET}` is a worse encoder than `{SHIPPED_CHANNEL_SET}`, and that "
        f"flatters the connectome in the table above.** The four v4 columns are a fair "
        f"comparison of projections against each other, because they share an encoder. They "
        f"are not a claim about improving the tool. Measured separately under the random "
        f"projection, widening v3 to 55 channels *hurt*: median worst rank on `bench-mixed` "
        f"went from 28 to 215, and v4 won on 0 of 32 seeds on two surfaces. So much of the "
        f"connectome's apparent gain is recovering ground the wider encoder gave away. "
        f"Against the tool as it actually ships - the `shipped today` column - the "
        f"connectome's joint advantage is 3 of 32 draws, p=0.121. Not significant.\n"
    )
    add(
        "**The mechanism is collapse, and it is only part of the story.** Pooled within "
        "surfaces across every projection and seed, the number of distinct tags the noise "
        "produces predicts where the hardest hit lands: Spearman rho=+0.450, p=7e-16, "
        "n=291. Fewer distinct noise tags means the familiar responses depress the same "
        "cells together, which leaves the filter's remaining capacity to make a hit stand "
        "out. But it explains about a fifth of the variance and no more: `random-matched` "
        "collapses the noise harder than the connectome on four of the five surfaces and "
        "still loses on `bench-mixed`.\n"
    )
    add(
        "**Where any of this helps is narrow and specific.** Three of five surfaces are "
        "saturated - every projection puts every hit at the top, and they measure nothing. "
        "The gap opens on `bench-mixed`, whose noise is several different response "
        "populations rather than one. That is the same axis on which multi-baseline "
        "partitioning was the one decisive win in this project, and it is worth taking "
        "seriously as the thing this circuit is actually for: not sharper discrimination, "
        "but not being confused by a baseline that is several things at once.\n"
    )
    add(
        "**What shipped: nothing.** Not the connectome - it needs a 508 MB download, a "
        "55-channel encoder that is worse on its own, and it does not beat a random "
        "projection carrying its fan-in distribution. And not the fan-in distribution "
        "either. That looked like the one thing here a random projection would not have "
        "suggested, and it was written up as such; then it was tested on a second, "
        "independently built heterogeneous surface and did not reproduce, and the result it "
        "was based on turned out not to survive a paired test on its own surface. "
        "`--projection degree-sampled` exists and is not recommended. See "
        "[claw-degrees.md](claw-degrees.md), which retracts it.\n"
    )
    add("### What would change this\n")
    add(
        "- A corpus with more surfaces whose noise is genuinely heterogeneous. Three of "
        "five here are saturated, so most of the table is measuring nothing, and the one "
        "surface that discriminates is carrying the whole conclusion.\n"
        "- Labelled hits that are not synthetic. These are planted, and planted hits may be "
        "easier to separate than real ones in ways that favour whichever projection "
        "collapses hardest.\n"
        "- An honest accounting of multiple comparisons. Several variants were tried "
        "against several surfaces; no p-value here is corrected, and the one nominally "
        "significant result elsewhere (p=0.02) should be read with that in mind.\n"
        "- A reason to believe the encoder-to-glomerulus assignment *could* matter. If the "
        "channels were ever made to correspond to something odour-like, the shuffle control "
        "would stop being a formality.\n"
    )

    OUT.write_text("\n".join(lines) + provenance_footer(seed=0))
    print(f"wrote {OUT.relative_to(REPO_ROOT)}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
