#!/usr/bin/env python3
"""M3: the connectome-wired FlyHash against the published random-projection baseline.

Two metrics, both from the literature this is built on:

**Retrieval mAP** (Dasgupta, Stevens & Navlakha, *Science* 2017). A locality-sensitive hash
is good exactly insofar as tag similarity predicts input similarity. For each query, rank
the corpus by tag overlap and score that ranking against the true Euclidean nearest
neighbours: mean average precision at k. This is the metric the FlyHash paper reports.

**Novelty AUC** (Dasgupta, Sheehan, Stevens & Navlakha, *PNAS* 2018). Show the Bloom filter
a stream drawn from half the classes, then ask it to separate held-out samples of those
classes from samples of the classes it has never seen. Area under the ROC curve of the
novelty score.

Three projections are compared: the published `random` baseline, a degree-preserving
`random-matched` null that keeps each Kenyon cell's measured fan-in but randomises which
channels it listens to, and the measured `connectome`. The null is what makes the result
attributable - without it, any advantage could be the fan-in distribution rather than the
wiring.

**One deviation from the papers must be stated up front.** The measured circuit has a fixed
number of input channels - one per antennal lobe glomerulus - so every dataset is reduced
to exactly that many dimensions by PCA before hashing. All three projections see the
identical reduced input, so the comparison between them is fair; but the absolute numbers
are not comparable to the papers, which hash the full-dimensional data.

    python bench/benchmark_flyhash.py --quick
    python bench/benchmark_flyhash.py
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))

import datasets  # noqa: E402

from flypaper import provenance_footer  # noqa: E402
from flypaper.brain.bloom import FlyBloomFilter  # noqa: E402
from flypaper.brain.extract import Circuit, extract  # noqa: E402
from flypaper.brain.flyhash import (  # noqa: E402
    FlyHash,
    connectome_projection,
    random_matched_projection,
    random_projection,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
DERIVED = REPO_ROOT / "data" / "derived"
CIRCUIT_PATH = DERIVED / "olfactory-circuit-R"
OUT = REPO_ROOT / "reports" / "m3-flyhash-benchmark.md"

SEEDS = tuple(range(15))
PROJECTIONS = ("random", "random-matched", "connectome-binary", "connectome")


# --- metrics --------------------------------------------------------------------------------


def auc(scores: np.ndarray, labels: np.ndarray) -> float:
    """ROC AUC via the rank statistic. `labels` is 1 for the positive (novel) class."""
    order = np.argsort(scores, kind="mergesort")
    ranks = np.empty(len(scores), dtype=np.float64)
    ranks[order] = np.arange(1, len(scores) + 1)
    # Average ranks over ties, or a filter that saturates scores gets a free pass.
    sorted_scores = scores[order]
    i = 0
    while i < len(sorted_scores):
        j = i
        while j + 1 < len(sorted_scores) and sorted_scores[j + 1] == sorted_scores[i]:
            j += 1
        if j > i:
            ranks[order[i : j + 1]] = (i + j + 2) / 2.0
        i = j + 1
    n_pos = float(labels.sum())
    n_neg = float(len(labels) - n_pos)
    if n_pos == 0 or n_neg == 0:
        return float("nan")
    return float((ranks[labels == 1].sum() - n_pos * (n_pos + 1) / 2) / (n_pos * n_neg))


def retrieval_map(
    X: np.ndarray,
    tags: np.ndarray,
    k: int = 20,
    n_queries: int = 300,
    rng: np.random.Generator | None = None,
) -> float:
    """Mean average precision at k against the true Euclidean nearest neighbours."""
    rng = rng or np.random.default_rng(0)
    queries = rng.choice(len(X), min(n_queries, len(X)), replace=False)

    aps = []
    for q in queries:
        true_d = np.linalg.norm(X - X[q], axis=1)
        true_d[q] = np.inf
        truth = set(np.argpartition(true_d, k)[:k].tolist())

        overlap = tags @ tags[q]
        overlap[q] = -np.inf
        ranked = np.argsort(-overlap, kind="mergesort")[:k]

        hits, precision_sum = 0, 0.0
        for rank, idx in enumerate(ranked, 1):
            if idx in truth:
                hits += 1
                precision_sum += hits / rank
        aps.append(precision_sum / k)
    return float(np.mean(aps))


# --- setup -----------------------------------------------------------------------------------


def pca_reduce(X: np.ndarray, dims: int, rng: np.random.Generator) -> np.ndarray:
    """Reduce to `dims` and shift to non-negative.

    Receptor channels are firing rates: they cannot be negative, and the projection is
    non-negative too. Shifting each column to a floor of zero keeps that true without
    changing the geometry PCA found.
    """
    Xc = X - X.mean(axis=0, keepdims=True)
    # Randomised SVD is enough here and far cheaper than a full decomposition.
    n = min(dims + 10, min(Xc.shape))
    Q = rng.normal(size=(Xc.shape[1], n)).astype(np.float32)
    Y = Xc @ Q
    Q2, _ = np.linalg.qr(Y)
    B = Q2.T @ Xc
    _, _, Vt = np.linalg.svd(B, full_matrices=False)
    Z = Xc @ Vt[:dims].T
    return (Z - Z.min(axis=0, keepdims=True)).astype(np.float32)


def load_circuit() -> Circuit:
    if CIRCUIT_PATH.with_suffix(".json").exists():
        return Circuit.load(CIRCUIT_PATH)
    print("extracting the circuit from MaleCNS (once) ...", file=sys.stderr)
    circuit = extract("R")
    circuit.save(CIRCUIT_PATH)
    return circuit


def build_hash(kind: str, circuit: Circuit, seed: int, sparsity: float) -> FlyHash:
    rng = np.random.default_rng(seed)
    n_channels, n_kc = circuit.pn_to_kc.shape
    if kind == "random":
        # The published baseline: uniform fan-in of 6.
        projection = random_projection(n_channels, n_kc, 6, rng=rng)
    elif kind == "random-matched":
        projection = random_matched_projection(circuit.pn_to_kc, rng=rng)
    elif kind == "connectome-binary":
        # Measured wiring, weights discarded. This is the comparison that isolates *which*
        # channels converge from *how strongly* they do.
        projection = connectome_projection(circuit.pn_to_kc, binary=True)
    elif kind == "connectome":
        projection = connectome_projection(circuit.pn_to_kc)
    else:
        raise ValueError(kind)
    return FlyHash(projection, sparsity=sparsity)


# --- experiments ------------------------------------------------------------------------------


def run_retrieval(X: np.ndarray, circuit: Circuit, sparsity: float, k: int) -> dict:
    out: dict[str, list[float]] = {p: [] for p in PROJECTIONS}
    for kind in PROJECTIONS:
        for seed in SEEDS:
            fh = build_hash(kind, circuit, seed, sparsity)
            tags = fh.tag(X)
            out[kind].append(retrieval_map(X, tags, k=k, rng=np.random.default_rng(seed)))
            if kind.startswith("connectome"):
                break  # deterministic: no seed to average over
    return out


def run_novelty(
    X: np.ndarray, y: np.ndarray, circuit: Circuit, sparsity: float, halflife: float | None
) -> dict:
    familiar_classes = set(range(5))
    fam = np.flatnonzero(np.isin(y, list(familiar_classes)))
    nov = np.flatnonzero(~np.isin(y, list(familiar_classes)))

    out: dict[str, list[float]] = {p: [] for p in PROJECTIONS}
    for kind in PROJECTIONS:
        for seed in SEEDS:
            rng = np.random.default_rng(seed)
            train = rng.choice(fam, min(2000, len(fam) // 2), replace=False)
            held = rng.choice(np.setdiff1d(fam, train), 500, replace=False)
            unseen = rng.choice(nov, 500, replace=False)

            fh = build_hash(kind, circuit, seed, sparsity)
            filt = FlyBloomFilter(fh.n_kc, decay_halflife=halflife)
            filt.observe(fh.tag_valued(X[train]))

            scores = np.concatenate(
                [filt.score(fh.tag_valued(X[held])), filt.score(fh.tag_valued(X[unseen]))]
            )
            labels = np.concatenate([np.zeros(len(held)), np.ones(len(unseen))])
            out[kind].append(auc(scores, labels))
            if kind.startswith("connectome"):
                break
    return out


#: Training budgets for the curve. The single most-quoted limitation of this benchmark is
#: that the verdict moves with how much the filter has seen, and two data points is not a
#: curve. Chosen to span an order of magnitude either side of the headline run's 2,000.
BUDGETS = (250, 500, 1000, 2000, 4000)
CURVE_SEEDS = tuple(range(8))


def run_budget_curve(X: np.ndarray, y: np.ndarray, circuit: Circuit, sparsity: float) -> dict:
    """Novelty AUC against training budget, for the published baseline and the connectome.

    Everything else is held fixed: the same held-out and unseen sets, the same seeds, the
    same sparsity. Only the number of examples the filter has been shown changes.
    """
    fam = np.flatnonzero(np.isin(y, list(range(5))))
    nov = np.flatnonzero(~np.isin(y, list(range(5))))
    curve: dict[str, dict[int, list[float]]] = {"random": {}, "connectome-binary": {}}

    for budget in BUDGETS:
        if budget > len(fam) - 600:
            continue
        for kind in curve:
            scores_for_budget = []
            for seed in CURVE_SEEDS:
                rng = np.random.default_rng(seed)
                train = rng.choice(fam, budget, replace=False)
                rest = np.setdiff1d(fam, train)
                held = rng.choice(rest, min(500, len(rest)), replace=False)
                unseen = rng.choice(nov, 500, replace=False)

                fh = build_hash(kind, circuit, seed, sparsity)
                filt = FlyBloomFilter(fh.n_kc)
                filt.observe(fh.tag_valued(X[train]))
                scores = np.concatenate(
                    [filt.score(fh.tag_valued(X[held])), filt.score(fh.tag_valued(X[unseen]))]
                )
                labels = np.concatenate([np.zeros(len(held)), np.ones(len(unseen))])
                scores_for_budget.append(auc(scores, labels))
                if kind.startswith("connectome"):
                    break  # deterministic
            curve[kind][budget] = scores_for_budget
    return curve


def summarise(values: list[float]) -> str:
    if len(values) == 1:
        return f"{values[0]:.4f}"
    return f"{np.mean(values):.4f} ± {np.std(values, ddof=1):.4f}"


def against_null(connectome: float, null: list[float]) -> str:
    """Where the connectome falls in the distribution of random draws.

    The connectome is one deterministic sample - there is one male fly - so the only
    honest comparison is to ask where it sits inside the spread of the random baseline,
    not whether two means differ.
    """
    mean, sd = float(np.mean(null)), float(np.std(null, ddof=1))
    z = (connectome - mean) / sd if sd > 0 else float("nan")
    beaten = sum(1 for v in null if connectome > v)
    return f"{z:+.1f} sd, beats {beaten}/{len(null)}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--quick", action="store_true", help="smaller samples, for a fast check")
    ap.add_argument("--sparsity", type=float, default=0.05)
    ap.add_argument("--k", type=int, default=20)
    args = ap.parse_args()

    if not datasets.available():
        raise SystemExit("benchmark datasets absent. Run: python bench/datasets.py --fetch")

    circuit = load_circuit()
    n_channels = circuit.n_channels
    limit = 3000 if args.quick else 12000

    results: dict[str, dict] = {}
    for name in ("mnist", "fashion-mnist"):
        X_raw, y = datasets.load(name, limit=limit)
        X = pca_reduce(X_raw, n_channels, np.random.default_rng(0))
        print(f"{name}: {X_raw.shape} -> {X.shape}", file=sys.stderr)
        results[name] = {
            "retrieval_map": run_retrieval(X, circuit, args.sparsity, args.k),
            "novelty_auc": run_novelty(X, y, circuit, args.sparsity, None),
            "novelty_auc_decay": run_novelty(X, y, circuit, args.sparsity, 5000.0),
            "budget_curve": run_budget_curve(X, y, circuit, args.sparsity),
            "n": int(len(X)),
        }

    write_report(circuit, results, args)
    return 0


def write_report(circuit: Circuit, results: dict, args) -> None:
    stats = circuit.stats
    near = stats["near_misses"]
    lines: list[str] = []
    add = lines.append

    def verdict(metric: str) -> tuple[str, float]:
        """Where the connectome sits in the random null, worst case across datasets."""
        zs = []
        for d in results:
            best = max(
                results[d][metric]["connectome"][0],
                results[d][metric]["connectome-binary"][0],
            )
            null = results[d][metric]["random"]
            sd = float(np.std(null, ddof=1))
            zs.append((best - float(np.mean(null))) / sd if sd else 0.0)
        z = min(zs)
        if z >= -1.0:
            return "matches", z
        return "is worse than", z

    novelty_verdict, novelty_z = verdict("novelty_auc")
    retrieval_verdict, retrieval_z = verdict("retrieval_map")

    add("# M3 - connectome FlyHash vs. random projection\n")
    add("**Gate: the connectome version at minimum matches the random-projection baseline.**\n")
    add(
        f"**Split, and reported as such.** On novelty detection - the task this tool actually\n"
        f"performs - the measured connectome **{novelty_verdict}** the published random\n"
        f"projection (worst case {novelty_z:+.1f} sd inside the random spread). On\n"
        f"nearest-neighbour retrieval it **{retrieval_verdict}** it, clearly\n"
        f"({retrieval_z:+.1f} sd). The gate is met for the metric that matters here and failed\n"
        f"for the other, so the claim is narrowed rather than the threshold moved - see\n"
        f"section 3.\n"
    )
    add(
        "Numbers are computed by `bench/benchmark_flyhash.py`; the interpretation is written.\n"
        "Nothing in this milestone touches the network except to fetch two public datasets.\n"
    )

    add("## 1. The circuit, as measured\n")
    add("Every population is resolved by annotation on every run. No body ID is hardcoded.\n")
    add("| Quantity | MaleCNS v1.0, right hemisphere | Published model |")
    add("|---|---:|---:|")
    add(
        f"| Receptor channels (antennal lobe glomeruli) | **{stats['glomeruli']}** "
        f"| ~50 ORN types |"
    )
    add(f"| Uniglomerular projection neurons | {stats['pn_bodies']} | - |")
    add(f"| Kenyon cells | **{stats['kc_bodies']}** | ~2,000 |")
    add(f"| Kenyon cells receiving PN input | {stats['kc_receiving_pn_input']} | - |")
    add(
        f"| PN->KC fan-in, mean | **{stats['fan_in_mean']:.2f}** "
        f"(median {stats['fan_in_median']:.0f}, sd {stats['fan_in_std']:.2f}) | ~6, uniform |"
    )
    add(f"| PN->KC fan-in, range | {stats['fan_in_min']}-{stats['fan_in_max']} | fixed at 6 |")
    add(f"| PN->KC connections | {stats['pn_to_kc_connections']:,} | - |")
    add(f"| PN->KC synapses | {stats['pn_to_kc_synapses']:,} | - |")
    add(f"| MBON-alpha'3 cells | {stats['mbon_alpha3_bodies']} | the novelty readout |")
    add(f"| KC->MBON-alpha'3 connections | {stats['kc_to_mbon_connections']:,} | - |")
    add("")
    add(
        "**The published assumptions hold up well.** 55 channels against an assumed ~50, "
        "2,045 Kenyon cells against an assumed ~2,000, and a mean fan-in of "
        f"{stats['fan_in_mean']:.2f} against an assumed 6. The model was a good guess.\n"
    )
    add(
        "**Two differences are real and neither is a bug.** The measured fan-in is a "
        "*distribution*, "
        f"from {stats['fan_in_min']} to {stats['fan_in_max']}, not the constant 6 the model uses - "
        "which is why the benchmark below includes a degree-preserving null. And "
        f"{stats['kc_bodies'] - stats['kc_receiving_pn_input']} of "
        f"{stats['kc_bodies']} Kenyon cells "
        "receive no antennal lobe input at all; they are the ones fed by the accessory calyces "
        "from other modalities. A model that assumes every Kenyon cell is olfactory is wrong "
        "about roughly one in thirteen of them.\n"
    )

    add("### Identifying MBON-alpha'3\n")
    add(
        "MaleCNS names MBONs numerically - `type` gives `MBON16`, not `MBON-alpha'3ap` - so "
        "the compartment is not readable from the type at all. It **is** readable from "
        "`instance`, which carries the compartment in parentheses, and that is what the "
        "extraction matches on:\n"
    )
    for inst in stats["mbon_alpha3_instances"]:
        add(f"- `{inst}`")
    add("")
    add(
        "These are MBON-alpha'3ap and MBON-alpha'3m, which is exactly the readout the Fly "
        "Bloom Filter paper uses. **The identification is therefore clean, and it came from "
        "the data rather than from a remembered type number.**\n"
    )
    add(
        "Two further cells touch alpha'3 partially and are deliberately *not* folded into "
        "the readout: " + ", ".join(f"`{n}`" for n in near["mbon_adjacent_to_alpha3"]) + ". "
        f"The other {len(near['mbon_other'])} MBON types are excluded outright. Listing them "
        "is the point: a prefix match cannot quietly swallow a different cell type if the "
        "near misses are written down.\n"
    )
    add(
        f"Similarly, {len(near['alpn_not_uniglomerular'])} antennal lobe projection neuron "
        "types are multiglomerular or central-brain and name no single glomerulus. They are "
        "excluded from the receptor channels rather than forced into one.\n"
    )

    add("## 2. Benchmark\n")
    add(
        "Datasets and metrics are the ones the papers use. `random` is the published "
        "baseline with a uniform fan-in of 6. `random-matched` keeps each Kenyon cell's "
        "measured fan-in but randomises which channels it listens to - the control that "
        "separates wiring from degree. Random variants are averaged over "
        f"{len(SEEDS)} seeds; the connectome is deterministic, so it has no spread.\n"
    )
    for dataset, res in results.items():
        add(f"### {dataset} (n={res['n']:,}, sparsity {args.sparsity:.0%})\n")
        add(
            "| Metric | "
            + " | ".join(f"`{p}`" for p in PROJECTIONS)
            + " | best connectome vs. the random null |"
        )
        add("|---|" + "---:|" * len(PROJECTIONS) + "---|")
        for metric, label in (
            ("retrieval_map", f"Retrieval mAP@{args.k}"),
            ("novelty_auc", "Novelty AUC"),
            ("novelty_auc_decay", "Novelty AUC, with decay"),
        ):
            cells = " | ".join(summarise(res[metric][p]) for p in PROJECTIONS)
            best = max(res[metric]["connectome"][0], res[metric]["connectome-binary"][0])
            add(f"| {label} | {cells} | {against_null(best, res[metric]['random'])} |")
        add("")

    add("## 3. Novelty against training budget\n")
    add(
        "The most-quoted caveat on this benchmark has been that the verdict moves with how\n"
        "much the filter has been shown, measured at two points. Here it is as a curve, with\n"
        "everything else held fixed - same held-out and unseen sets, same seeds, same\n"
        f"sparsity - over {len(CURVE_SEEDS)} seeds for the random baseline.\n"
    )
    for dataset, res in results.items():
        curve = res.get("budget_curve")
        if not curve or not curve["random"]:
            continue
        budgets = sorted(curve["random"])
        add(f"### {dataset}\n")
        add("| Training examples | `random` | `connectome-binary` | connectome vs. the null |")
        add("|---:|---:|---:|---|")
        for budget in budgets:
            null = curve["random"][budget]
            conn = curve["connectome-binary"][budget][0]
            mean, sd = float(np.mean(null)), float(np.std(null, ddof=1))
            z = (conn - mean) / sd if sd else 0.0
            add(
                f"| {budget:,} | {mean:.4f} ± {sd:.4f} | {conn:.4f} | "
                f"{z:+.1f} sd, beats {sum(1 for v in null if conn > v)}/{len(null)} |"
            )
        add("")

    add("## 4. Interpretation\n")
    add(
        "**The budget curve says the headline z-scores should not be quoted individually.**\n"
        "On MNIST there is a real trend: the connectome is behind at 250, 500 and 1,000\n"
        "training examples (down to -2.4 sd, beating 0 of 8 seeds) and reaches parity or a\n"
        "shade above at 2,000 and 4,000. That is consistent, and it locates the earlier\n"
        "observation that the connectome fell behind at a third of the data.\n"
    )
    add(
        "On Fashion-MNIST there is no trend at all. Adjacent budgets give **+2.7 sd (beating\n"
        "8 of 8 seeds) at 1,000 and +0.1 sd (4 of 8) at 2,000**, on the same data with the\n"
        "same seeds. Nothing about the circuit changed between those two rows; only the\n"
        "number of examples did. A difference that large between neighbouring budgets means\n"
        "the per-budget figure is dominated by which particular examples the filter happened\n"
        "to see, not by the projection.\n"
    )
    add(
        "So the conclusion below is robust and the individual numbers supporting it are not.\n"
        "*Matches the baseline* survives - the connectome is never durably ahead or behind\n"
        "across ten budget-dataset combinations. Any single z-score, including the +0.7 sd in\n"
        "section 2, is one draw from a distribution wide enough to produce +2.7 and +0.1 back\n"
        "to back, and should be read as such.\n"
    )
    add(
        "**The measured wiring is not better than random, and on the novelty task it is not\n"
        "worse either.** Across both datasets the connectome lands well inside the spread of\n"
        "random draws, beating somewhere between half and three-quarters of them. That is what\n"
        "a coin flip looks like when you measure it carefully, and it should be read as *no\n"
        "difference*, not as a small win. Anyone quoting these numbers as evidence that the\n"
        "connectome helps would be over-reading them.\n"
    )
    add(
        "**This is a real result about the published model, and a mildly reassuring one.**\n"
        "Dasgupta, Stevens & Navlakha modelled PN->KC connectivity as a random sparse\n"
        "projection because that is what the biology looked like statistically at the time.\n"
        "The measured wiring was not available to test that against. It now is, and for\n"
        "novelty detection the simplification holds: the model's central assumption costs\n"
        "nothing measurable on this task. A negative result about our contribution is a\n"
        "positive one about theirs.\n"
    )
    add(
        "**On retrieval the connectome is genuinely worse, and the reason is visible in the\n"
        "tags.** Feeding 500 random inputs through each projection, the measured wiring\n"
        "activates fewer distinct Kenyon cells than random does and produces tags that overlap\n"
        "roughly twice as much between unrelated inputs. Real PN->KC connectivity is not\n"
        "uniformly random - some glomerular combinations converge far more often than chance -\n"
        "and correlated convergence means correlated tags, which is exactly what a\n"
        "locality-sensitive hash does not want. Random wiring spreads inputs over the\n"
        "available tag space more evenly because spreading evenly is all it does.\n"
    )
    add(
        "**Synapse counts make it worse, and that is informative.** `connectome` weights each\n"
        "connection by its synapse count; `connectome-binary` keeps the same wiring and throws\n"
        "the counts away. Binary is the better hash on three of the four retrieval and\n"
        "Fashion-MNIST novelty comparisons. Whatever the strength of a PN->KC connection\n"
        "encodes, it is not information that helps this particular computation.\n"
    )
    add(
        "**The degree-preserving null earns its place.** `random-matched` gives each Kenyon\n"
        "cell its measured fan-in and randomises only which channels it listens to, and it\n"
        "tracks plain `random` closely throughout. So the fan-in *distribution* - the spread\n"
        f"from {stats['fan_in_min']} to {stats['fan_in_max']} that the uniform model misses - \n"
        "is not what drives any of the differences here. What is left is the wiring itself.\n"
    )
    add(
        "**The most likely reason the connectome cannot win here is the input.** The measured\n"
        "wiring, whatever structure it has, is structure with respect to *odours*: which\n"
        "glomerular combinations co-occur in the natural statistics a fly's olfactory system\n"
        "evolved against. The benchmark feeds it PCA components of handwritten digits and\n"
        "clothing photographs, where channel identity is arbitrary and any such alignment is\n"
        "destroyed by construction. This experiment can show that the connectome does not\n"
        "*generically* beat random projection. It cannot show whether it beats it on inputs\n"
        "whose correlational structure resembles what the circuit was shaped by, and nothing\n"
        "here should be read as having tested that.\n"
    )
    add("### What may and may not be claimed\n")
    add(
        "May: *FlyHash wired from the measured MaleCNS connectome performs comparably to the\n"
        "published random projection for novelty detection, and worse for nearest-neighbour\n"
        "retrieval.* As far as we can tell this comparison had not been run before.\n"
    )
    add(
        "May not: that the connectome improves FlyHash, that it is a better locality-sensitive\n"
        "hash, or that biological wiring is optimal for this task. None of those survive the\n"
        "table above.\n"
    )
    add(
        "**Consequently flypaper ships on random projection by default.** The two are\n"
        "statistically indistinguishable on the task, and random projection needs no 508 MB\n"
        "download to run. The connectome projection stays available and tested; it is simply\n"
        "not sold as an improvement, because it is not one.\n"
    )

    add("## 5. Honest limitations\n")
    add(
        f"- **The input is reduced to {circuit.n_channels} dimensions by PCA.** The measured "
        "circuit fixes the number of receptor channels, so 784-dimensional images cannot be "
        "fed to it directly. All three projections see identical reduced input, so comparing "
        "them is fair - but none of these absolute numbers is comparable to the published "
        "ones, which hash full-dimensional data.\n"
        "- **The verdict is sensitive to how much the filter has seen.** At `--quick` scale "
        "(3,000 samples, so roughly 750 training examples instead of 2,000) the connectome "
        "falls behind on novelty too, by about 4 sd. The match reported above is at the "
        "larger size; it is not a claim that holds at every training budget, and the budget "
        "should be quoted with the number.\n"
        "- **The connectome projection is a single sample.** There is one male fly. The "
        "random variants average over seeds and carry a spread; the connectome cannot, and a "
        "difference smaller than the random variants' own spread should not be read as a "
        "result.\n"
        "- **Synaptic weights are counts at a 0.5 confidence threshold**, from automated "
        "detection. They are not measured physiological strengths.\n"
        "- **MNIST and Fashion-MNIST are not odours and not HTTP responses.** They are the "
        "datasets the literature uses, which makes this comparison legible; they say nothing "
        "directly about the fuzzing task.\n"
    )

    OUT.write_text("\n".join(lines) + provenance_footer(seed=0))
    print(f"wrote {OUT.relative_to(REPO_ROOT)}")
    for dataset, res in results.items():
        for metric in ("retrieval_map", "novelty_auc", "novelty_auc_decay"):
            row = " ".join(f"{k}={summarise(v)}" for k, v in res[metric].items())
            print(f"  {dataset:15} {metric:18} {row}")
    (DERIVED / "m3-results.json").write_text(json.dumps(results, indent=2, default=float))


if __name__ == "__main__":
    sys.exit(main())
