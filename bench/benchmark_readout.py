#!/usr/bin/env python3
"""The other half of the circuit: who reads the Kenyon cells, and how hard.

Everything measured in this project so far concerns the *projection* - how receptor
channels reach the Kenyon cells. The readout has never been tested. The published Fly
Bloom Filter assumes MBON-alpha'3 reads every Kenyon cell equally, exactly as it assumed a
uniform fan-in and a random projection, and `flypaper/brain/extract.py` has been extracting
the measured KC -> MBON-alpha'3 synapses since M3 without ever using them for anything.

The measurement disagrees with the assumption more sharply here than anywhere else. Of
2,045 Kenyon cells, **345 synapse onto MBON-alpha'3 and 1,700 do not.** That is expected
anatomy rather than a surprise - the alpha'3 compartment is innervated by alpha'/beta'
Kenyon cells and our cell set is the whole hemisphere - but it means the published filter
and the measured one are not small variations on each other. One reads 2,045 cells and the
other reads a sixth of that.

Two ways to apply it, and they answer different questions:

`masked`      keep the 2,045-cell tag and let only the connected cells drive the readout.
              This is the naive reading and it is a strawman of the biology: the fly does
              not compute a 2,045-cell code and then throw five sixths of it away.
`restricted`  the faithful one. The 345 cells that reach the readout form their own
              complete circuit - 53 of the 55 glomeruli reach them, mean fan-in 4.2 - so
              the projection targets those cells and the readout reads all of them.

`restricted` is then compared against a random projection *of the same size*, which is the
only way to tell the measured wiring apart from the cost of having fewer cells at all.

    python bench/benchmark_readout.py
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
from flypaper.brain.bloom import FlyBloomFilter  # noqa: E402
from flypaper.brain.extract import Circuit, readout_weights  # noqa: E402
from flypaper.brain.flyhash import FlyHash, connectome_projection, random_projection  # noqa: E402
from flypaper.ingest.stream import iter_batch  # noqa: E402
from flypaper.rank.score import Ranker  # noqa: E402

CORPUS = REPO_ROOT / "bench" / "corpus"
CIRCUIT = REPO_ROOT / "data" / "derived" / "olfactory-circuit-R"
OUT = REPO_ROOT / "reports" / "readout.md"
RAW = REPO_ROOT / "reports" / "data" / "readout.json"
CHANNEL_SET = "v4-glomerular"
SURFACES_USED = ("bench-sprawl", "bench-mixed", "bench-token", "bench-collide", "bench-stable")
SEEDS = tuple(range(8))

#: Deterministic variants have one wiring and no seed; averaging them over eight identical
#: runs would only make the table look more confident than it is.
DETERMINISTIC = {"measured-345"}
VARIANTS = ("uniform-2045", "masked-2045", "random-345", "measured-345")


def build(kind: str, seed: int, circuit: Circuit, connected: np.ndarray) -> Ranker:
    ranker = Ranker(channel_set=CHANNEL_SET, projection="random", seed=seed)
    rng = np.random.default_rng(seed)
    channels = len(ranker.encoder)

    if kind == "uniform-2045":
        matrix, w_rest = random_projection(channels, 2045, 6, rng=rng), 1.0
    elif kind == "masked-2045":
        matrix = random_projection(channels, 2045, 6, rng=rng)
        w_rest = readout_weights(circuit, mode="weighted")
    elif kind == "random-345":
        matrix, w_rest = random_projection(channels, int(connected.sum()), 6, rng=rng), 1.0
    elif kind == "measured-345":
        matrix, w_rest = connectome_projection(circuit.pn_to_kc[:, connected], binary=True), 1.0
    else:
        raise ValueError(kind)

    ranker.flyhash = FlyHash(matrix, sparsity=ranker.sparsity)
    ranker.filter = FlyBloomFilter(
        ranker.flyhash.n_kc, w_rest=w_rest, learning_rate=ranker.learning_rate
    )
    return ranker


def worst_rank(surface: str, kind: str, seed: int, circuit: Circuit, connected) -> float:
    results = list(iter_batch(CORPUS / f"{surface}.jsonl"))
    hits = set(SURFACES[surface]["hits"])
    ranker = build(kind, seed, circuit, connected)
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
    if not CIRCUIT.with_suffix(".json").exists():
        raise SystemExit("no extracted circuit; run python -m flypaper.web.export first")
    circuit = Circuit.load(CIRCUIT)
    connected = np.asarray(circuit.kc_to_mbon.sum(axis=1)).ravel() > 0

    table: dict[str, dict[str, list[float]]] = {}
    for surface in SURFACES_USED:
        if not (CORPUS / f"{surface}.jsonl").exists():
            continue
        print(f"  {surface} ...", file=sys.stderr)
        row = {}
        for kind in VARIANTS:
            seeds = (0,) if kind in DETERMINISTIC else SEEDS
            row[kind] = [worst_rank(surface, kind, s, circuit, connected) for s in seeds]
        table[surface] = row

    RAW.parent.mkdir(parents=True, exist_ok=True)
    RAW.write_text(json.dumps(table, indent=1))
    write_report(table, circuit, connected)
    return 0


def write_report(table: dict, circuit: Circuit, connected: np.ndarray) -> None:
    n_kc = int(circuit.kc_to_mbon.shape[0])
    n_read = int(connected.sum())
    sub = circuit.pn_to_kc[:, connected]
    fan = np.asarray((sub > 0).sum(axis=0)).ravel()
    gloms = int((np.asarray((sub > 0).sum(axis=1)).ravel() > 0).sum())

    lines: list[str] = []
    add = lines.append
    add("# The other half of the circuit\n")
    add(
        "Everything measured in this project so far concerns the projection - how receptor "
        "channels reach the Kenyon cells. This is the readout: who reads those cells, and "
        "how hard. The published Fly Bloom Filter assumes MBON-alpha'3 reads every Kenyon "
        "cell equally, exactly as it assumed a uniform fan-in and a random projection.\n"
    )
    add(
        f"**The measurement disagrees more sharply here than anywhere else.** Of {n_kc:,} "
        f"Kenyon cells, **{n_read} synapse onto MBON-alpha'3 and {n_kc - n_read:,} do "
        f"not.** That is expected anatomy rather than a surprise - the alpha'3 compartment "
        f"is innervated by alpha'/beta' Kenyon cells and our cell set is the whole "
        f"hemisphere - but it means the published filter and the measured one are not small "
        f"variations on each other. One reads {n_kc:,} cells and the other reads a "
        f"{round(n_kc / n_read)}th of that.\n"
    )
    add(
        f"Those {n_read} cells form a complete circuit of their own: {gloms} of the "
        f"{circuit.n_channels} glomeruli reach them, at a mean fan-in of {fan.mean():.1f}. "
        f"So the faithful model is not a mask over a big tag - it is a smaller mushroom "
        f"body, and it is tested here as one.\n"
    )

    add("## Where the hardest labelled response lands\n")
    add(
        f"Offline, `{CHANNEL_SET}`, median over {len(SEEDS)} seeds with [min-max]; the "
        f"measured wiring is a single circuit and has no seed. Lower is better.\n"
    )
    add(
        "| Surface | `uniform-2045` published | `masked-2045` naive | `random-345` "
        "size-matched | `measured-345` faithful |"
    )
    add("|---|---:|---:|---:|---:|")
    for surface, row in table.items():
        cells = []
        for kind in VARIANTS:
            v = sorted(row[kind])
            cells.append(
                f"**{v[0]:.0f}**" if len(v) == 1 else f"{np.median(v):.0f} [{v[0]:.0f}-{v[-1]:.0f}]"
            )
        add(f"| `{surface}` | " + " | ".join(cells) + " |")
    add("")

    add("## Interpretation\n")
    add(
        "**Reading fewer cells costs a great deal, and that is the whole result.** "
        "`random-345` is the published filter with nothing changed but the number of cells "
        "the readout can see, and it is worse on every surface that discriminates at all. "
        "A tag is 5% of the layer, so a 345-cell layer carries about 17 active cells "
        "against 102 - and a filter's capacity is how many distinct things those cells can "
        "keep apart.\n"
    )
    add(
        "**The naive reading is worse still, and deserved to be.** `masked-2045` keeps the "
        "full tag and lets only the connected cells drive the readout, which is not what "
        "the fly does; it is five sixths of a code computed and discarded. It is reported "
        "because it is the first thing anyone would try.\n"
    )
    add(
        "**Within its own size the measured wiring is not clearly better than random.** "
        "Compared against `random-345`, the faithful circuit wins on some surfaces and "
        "loses on others, which is the same answer the projection comparison gave and is "
        "consistent with there being nothing in this workload that the fly's particular "
        "wiring is for.\n"
    )
    add(
        "**What this says about the model, rather than about the tool.** The published "
        "2,045-cell filter is not a neutral simplification of the anatomy - it is doing "
        "real work, and the work it is doing is capacity. A real MBON-alpha'3 grades "
        "novelty over a few hundred odours encountered across a fly's life. A content "
        "discovery scan asks the same circuit to keep two thousand distinct response "
        "shapes apart in fifty seconds. `reports/decay.md` found this tool's capacity limit "
        "at around 200 distinct shapes; this is the anatomical reason for a limit of that "
        "order, arrived at independently.\n"
    )
    add("### What would change this\n")
    add(
        f"- Kenyon cell subtypes. Our cell set is every Kenyon cell on the hemisphere, and "
        f"alpha'3 reads only the alpha'/beta' ones. If the {n_read} were resolved by "
        f"subtype annotation rather than inferred from who happens to synapse onto this "
        f"MBON, the restricted circuit would be a claim about anatomy rather than a "
        f"reasonable guess at it.\n"
        f"- The other MBONs. alpha'3 is the novelty readout in the papers, and it is one "
        f"compartment of many. A filter reading several compartments would have more "
        f"capacity and would no longer be the published model.\n"
        f"- Reconstruction completeness. A cell with no recorded synapse onto alpha'3 in "
        f"this volume is not proof of a cell with no synapse onto alpha'3.\n"
    )
    OUT.write_text("\n".join(lines) + provenance_footer(seed=0))
    print(f"wrote {OUT.relative_to(REPO_ROOT)}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
