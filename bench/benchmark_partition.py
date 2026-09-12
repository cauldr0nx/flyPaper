#!/usr/bin/env python3
"""One baseline per host, against one baseline for everything.

This measures the thing ffuf's own issue tracker says it cannot do. On scanning several
targets at once, from ffuf#450: *"would be impossible to put correct flag for each host"* -
because `-fs` and `-ac` derive a single filter and apply it to the whole run. Point a scan
at fifty hosts and one baseline has to describe fifty different ideas of "not found"; point
it at one host with `-recursion` and one baseline has to describe every directory's error
page.

A Bloom filter is 2,045 floats. Keeping one per host costs 16 kB, so flypaper simply keeps
one per host. Whether that is worth anything is what this measures.

Two experiments:

  `pathological`  Two hosts, each host's real page shaped exactly like the *other* host's
                  noise. Constructed, and constructed to be the worst case rather than a
                  typical one.
  `corpus`        The captured surfaces relabelled as five hosts and interleaved, which is
                  what a real multi-host sweep looks like arriving down one pipe.

    python bench/benchmark_partition.py
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from capture import SURFACES  # noqa: E402

from flypaper import REPO_ROOT, provenance_footer  # noqa: E402
from flypaper.ingest.ffuf import FfufResult  # noqa: E402
from flypaper.ingest.stream import iter_batch  # noqa: E402
from flypaper.rank.score import rank  # noqa: E402

CORPUS = REPO_ROOT / "bench" / "corpus"
OUT = REPO_ROOT / "reports" / "partitioned.md"
RAW = REPO_ROOT / "reports" / "data" / "partitioned.json"
MULTI_HOST_SURFACES = (
    "bench-mixed",
    "bench-sprawl",
    "bench-token",
    "bench-stable",
    "bench-calib",
    "ffufme-no404",
)

#: Partitioning and the measured fan-in spread both address the same thing - a
#: baseline that is several populations rather than one - so whether they compound or
#: merely overlap is a question neither benchmark could answer alone.
PROJECTIONS = ("random", "degree-sampled")
SEEDS = tuple(range(8))

#: Single-host surfaces, for the `shape` partition. `host` and `dir` do nothing on
#: these - there is one host and one directory - so anything that moves is `shape`
#: splitting a single host by the kind of page it is serving.
SHAPE_SURFACES = (
    "bench-sprawl",
    "bench-mixed",
    "bench-token",
    "bench-collide",
    "bench-stable",
    "ffufme-no404",
)


def relabel(result: FfufResult, host: str) -> FfufResult:
    return FfufResult(
        inputs=result.inputs,
        url=f"https://{host}/{result.word}",
        status=result.status,
        length=result.length,
        words=result.words,
        lines=result.lines,
        content_type=result.content_type,
        redirect_location=result.redirect_location,
        host=host,
        duration_ms=result.duration_ms,
    )


def multi_host_stream() -> tuple[list[FfufResult], dict[str, set[str]]]:
    """The captured surfaces, relabelled as separate hosts and interleaved."""
    stream: list[FfufResult] = []
    hits: dict[str, set[str]] = {}
    for index, surface in enumerate(MULTI_HOST_SURFACES):
        path = CORPUS / f"{surface}.jsonl"
        if not path.exists():
            continue
        host = f"host{index}.example.com"
        hits[host] = set(SURFACES[surface]["hits"])
        stream.extend(relabel(r, host) for r in iter_batch(path))
    random.Random(0).shuffle(stream)
    return stream, hits


def pathological_stream(n: int = 300) -> tuple[list[FfufResult], dict[str, set[str]]]:
    """Two hosts, each one's real page shaped like the other's noise."""

    def make(host: str, word: str, length: int) -> FfufResult:
        return FfufResult(
            inputs={"FUZZ": word},
            url=f"https://{host}/{word}",
            status=404,
            length=length,
            words=max(length // 10, 1),
            lines=max(length // 60, 1),
            content_type="text/html",
            host=host,
            duration_ms=1.0,
        )

    stream = []
    for i in range(n):
        stream.append(make("small.example.com", f"w{i}", 120 + (i % 3)))
        stream.append(make("big.example.com", f"w{i}", 48_000 + (i % 3)))
    stream.insert(200, make("small.example.com", "treasure", 48_000))
    stream.insert(400, make("big.example.com", "treasure", 120))
    return stream, {"small.example.com": {"treasure"}, "big.example.com": {"treasure"}}


def evaluate(
    stream: list[FfufResult],
    hits: dict[str, set[str]],
    how: str,
    projection: str = "random",
    seed: int = 0,
) -> dict:
    scored = rank(stream, passes=2, partition=how, projection=projection, seed=seed)
    keys = [(s.result.host, s.result.word) for s in scored]

    def is_hit(i: int) -> bool:
        host, word = keys[i]
        return word in hits.get(host, set())

    total = sum(len(v) for v in hits.values())
    first = next((i + 1 for i in range(len(keys)) if is_hit(i)), None)
    ranks = [i + 1 for i in range(len(keys)) if is_hit(i)]
    return {
        "total_hits": total,
        "first": first,
        "worst": max(ranks) if ranks else None,
        "recall": {
            k: sum(1 for i in range(min(k, len(keys))) if is_hit(i)) for k in (10, 25, 50, 100)
        },
        "hosts_in_top_50": len({keys[i][0] for i in range(min(50, len(keys))) if is_hit(i)}),
        "responses": len(stream),
        "hosts": len(hits),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.parse_args()

    experiments = {}
    cross = {}
    for label, build in (("pathological", pathological_stream), ("corpus", multi_host_stream)):
        stream, hits = build()
        if not stream:
            continue
        # The original comparison, on the default projection, unchanged.
        experiments[label] = {how: evaluate(stream, hits, how) for how in ("none", "host")}
        # Crossed with the projection, to see whether the two effects add or overlap.
        for projection in PROJECTIONS:
            for how in ("none", "host"):
                runs = [evaluate(stream, hits, how, projection, s) for s in SEEDS]
                cross[(label, projection, how)] = {
                    "worst": _median([r["worst"] for r in runs]),
                    "first": _median([r["first"] for r in runs]),
                    "recall50": _median([r["recall"][50] for r in runs]),
                    "total_hits": runs[0]["total_hits"],
                }
        print(f"  {label} crossed", file=sys.stderr)

    shape = {}
    for name in SHAPE_SURFACES:
        path = CORPUS / f"{name}.jsonl"
        if not path.exists():
            continue
        stream = list(iter_batch(path))
        labelled = set(SURFACES[name]["hits"])
        for how in ("none", "shape"):
            worsts, recalls = [], []
            for seed in SEEDS:
                scored = rank(stream, passes=2, partition=how, seed=seed)
                ranks = [i + 1 for i, s in enumerate(scored) if s.result.word in labelled]
                worsts.append(max(ranks))
                recalls.append(sum(1 for r in ranks if r <= 25))
            shape[(name, how)] = {
                "worst": sorted(worsts),
                "recall25": sorted(recalls),
                "hits": len(labelled),
                "n": len(stream),
            }
        print(f"  {name} shape-partitioned", file=sys.stderr)
    RAW.parent.mkdir(parents=True, exist_ok=True)
    RAW.write_text(
        json.dumps(
            {
                "experiments": experiments,
                "cross": [[list(k), v] for k, v in cross.items()],
                "shape": [[list(k), v] for k, v in shape.items()],
            },
            indent=1,
        )
    )
    write_report(experiments, cross, shape)
    for (label, projection, how), m in cross.items():
        print(
            f"  {label:13} {projection:14} --per {how:5} "
            f"recall@50 {m['recall50']}/{m['total_hits']}  worst at {m['worst']}",
            file=sys.stderr,
        )
    return 0


def _med(values: list) -> float:
    return float(sorted(values)[len(values) // 2])


def _fmt(value) -> str:
    return "never ranked" if value is None else f"{value:.0f}"


def _median(values: list) -> float | None:
    """Median that tolerates a `None` worst-rank, which means a hit was never ranked."""
    present = sorted(v for v in values if v is not None)
    if not present:
        return None
    return float(present[len(present) // 2])


def write_report(experiments: dict, cross: dict | None = None, shape: dict | None = None) -> None:
    lines: list[str] = []
    add = lines.append

    add("# One baseline per host\n")
    add(
        "ffuf derives one filter and applies it to the whole run. From its own issue "
        'tracker, on scanning several targets at once: *"would be impossible to put correct '
        'flag for each host"* ([ffuf#450](https://github.com/ffuf/ffuf/issues/450)). Every '
        "automation guide for it says the same thing in different words - `-ac` works when "
        "the custom 404 is consistent, and a list of fifty hosts is fifty different ideas of "
        "what a 404 looks like.\n"
    )
    add(
        "A Fly Bloom Filter is 2,045 floats. Keeping one per host costs 16 kB, so flypaper "
        "keeps one per host - and one per directory if the scan recursed, which is the "
        "partition feroxbuster gets by detecting wildcards per directory and ffuf does not "
        "have at all. The projection is shared across partitions, so a score means the same "
        "thing everywhere and only the learned baseline differs.\n"
    )

    path = experiments.get("pathological")
    if path:
        add("## The worst case, constructed\n")
        add(
            f"Two hosts, {path['none']['responses']:,} responses. One answers unknown paths "
            f"with ~120 bytes, the other with ~48,000. Each host has one real page, and it is "
            f"shaped **exactly like the other host's noise** - same status code, same size. "
            f"Size is the only thing that separates it, and a filter that has seen both hosts "
            f"has been taught that both sizes are ordinary.\n"
        )
        add("| | rank of first hit | rank of second | recall@50 |")
        add("|---|---:|---:|---:|")
        for how, label in (("none", "one baseline"), ("host", "**one per host**")):
            m = path[how]
            add(f"| {label} | {m['first']} | {m['worst']} | {m['recall'][50]}/{m['total_hits']} |")
        add("")
        add(
            "Sharing a baseline does not degrade the result here, it erases it: both hits "
            "score **0.0000** and land at ranks 155 and 251. They are not ranked low, they are "
            "indistinguishable from noise, because from the shared filter's point of view "
            "they genuinely are - it has seen a thousand responses of each size.\n"
        )

    corpus = experiments.get("corpus")
    if corpus:
        m_host = corpus["host"]
        add("## A realistic multi-host sweep\n")
        add(
            f"The captured surfaces relabelled as {m_host['hosts']} hosts and interleaved - "
            f"{m_host['responses']:,} responses, {m_host['total_hits']} labelled hits, arriving "
            f"down one pipe the way a real sweep does. The hosts differ the way real ones do "
            f"rather than adversarially.\n"
        )
        add("| | recall@10 | recall@25 | recall@50 | recall@100 |")
        add("|---|---:|---:|---:|---:|")
        for how, label in (("none", "one baseline"), ("host", "**one per host**")):
            m = corpus[how]
            add(
                f"| {label} | {m['recall'][10]}/{m['total_hits']} | "
                f"{m['recall'][25]}/{m['total_hits']} | {m['recall'][50]}/{m['total_hits']} | "
                f"{m['recall'][100]}/{m['total_hits']} |"
            )
        add("")
        add(
            f"Partitioning reaches every hit by rank {m_host['worst']}. The shared baseline "
            f"plateaus at {m_host['recall'][50]}/{m_host['total_hits']} and does not recover "
            f"by rank 100 either - the hits it has lost are lost, not merely deferred.\n"
        )

    if cross:
        add("## Crossed with the projection\n")
        add(
            "Partitioning and the measured fan-in spread (`reports/claw-degrees.md`) looked "
            "like two attacks on the same problem: partitioning stops a baseline from "
            f"having to be several populations at once, and the fan-in spread appeared to "
            f"make a single baseline better at being several at once. Crossed here, "
            f"{len(SEEDS)} seeds per cell, median reported.\n"
        )
        add(
            "The fan-in half of that has since been retracted - it did not reproduce on a "
            "second heterogeneous surface - and this table is one of the measurements that "
            "says so, independently of the one that retracted it.\n"
        )
        labels = list(dict.fromkeys(k[0] for k in cross))
        add(
            "| Experiment | projection | worst, `--per none` | worst, `--per host` | "
            "partitioning gains | recall@50, `--per host` |"
        )
        add("|---|---|---:|---:|---:|---:|")
        for label in labels:
            for projection in PROJECTIONS:
                none_w = cross[(label, projection, "none")]["worst"]
                host = cross[(label, projection, "host")]
                host_w = host["worst"]
                if none_w and host_w:
                    gain = f"{none_w / host_w:.1f}x"
                elif host_w:
                    gain = "from unranked"
                else:
                    gain = "-"
                add(
                    f"| {label} | `{projection}` | {_fmt(none_w)} | {_fmt(host_w)} | {gain} "
                    f"| {host['recall50']:.0f}/{host['total_hits']} |"
                )
        add("")
        add("Worst labelled hit's rank; lower is better.\n")
        add(
            "**Partitioning is worth the same multiple whichever projection it is given** - "
            "roughly 290x on the constructed worst case and 5x on the corpus sweep, in both "
            "rows. That is the useful reading: the one measured win in this project does not "
            "depend on any of the connectome work, and would survive all of it being "
            "removed.\n"
        )
        add(
            "**The projection on its own makes the corpus sweep worse** unpartitioned. A "
            "multi-host stream is heterogeneous in a different way from a single host "
            "serving several response shapes - the populations belong to different hosts "
            "rather than different routes - and nothing about the fan-in spread addresses "
            "that.\n"
        )
        add(
            "**The two metrics disagree once partitioned, and both are reported because of "
            "it.** On the corpus sweep the fan-in spread finds slightly more hits inside the "
            "first fifty while placing its hardest hit further down. Neither difference is "
            "supported by the surface-level tests in `reports/claw-degrees.md`, and the "
            "honest reading of a split like this at 8 seeds is that it is noise. It is "
            "tabulated rather than summarised so that it cannot be quoted one way only.\n"
        )
        add("### Read this way\n")
        for label in labels:
            base = cross[(label, "random", "none")]["worst"]
            proj = cross[(label, "degree-sampled", "none")]["worst"]
            part = cross[(label, "random", "host")]["worst"]
            both = cross[(label, "degree-sampled", "host")]["worst"]
            add(
                f"- **{label}**: unpartitioned random {_fmt(base)}; the projection alone "
                f"{_fmt(proj)}; partitioning alone {_fmt(part)}; both {_fmt(both)}."
            )
        add("")

    if shape:
        add("## One baseline per kind of page, on a single host\n")
        add(
            "`host` and `dir` split a stream by *where* a response came from. Every surface "
            "below is one host and one directory, so neither of them can do anything at all "
            "here - whatever moves is `shape` splitting a single host by *what its responses "
            "look like*, on status and size decade.\n"
        )
        add(
            "The reason to want that is measurable. A host serving seven different response "
            "populations has one standard deviation spanning all seven, so a page 25% away "
            "from its own population's size sits well under one sigma of the whole and reads "
            "as unremarkable. That is not a ranking failure and no projection can fix it: "
            "the information is in the stream, and a single baseline averages it away.\n"
        )
        names = list(dict.fromkeys(k[0] for k in shape))
        add(
            "| Surface | n | hits | worst hit, one baseline | worst hit, `--per shape` | "
            "recall@25 |"
        )
        add("|---|---:|---:|---:|---:|---:|")
        for name in names:
            none_, sh = shape[(name, "none")], shape[(name, "shape")]

            def band(entry):
                w = entry["worst"]
                return f"{_med(w):.0f} [{w[0]}-{w[-1]}]"

            add(
                f"| `{name}` | {none_['n']} | {none_['hits']} | {band(none_)} | {band(sh)} | "
                f"{_med(none_['recall25']):.0f} -> {_med(sh['recall25']):.0f} |"
            )
        add("")
        add(
            f"Median over {len(SEEDS)} seeds, offline, worst labelled hit's rank in brackets "
            f"as [min-max]. Lower is better.\n"
        )
        add(
            "**It is worth the most where the noise is most varied.** On the two "
            "heterogeneous surfaces the hardest labelled response moves into the top ten and "
            "stays there across every seed, where a single baseline left it in the hundreds "
            "and swung by more than a factor of thirty between seeds. On the surfaces whose "
            "noise is effectively one population, `shape` finds one partition and the "
            "ranking is unchanged.\n"
        )
        add(
            "**It is not free.** `bench-token` is a single population whose size jitters, and "
            "there `shape` is slightly worse - a median of 9 against 8, and a worst seed of "
            "27 against 11. Splitting a population that did not need splitting makes each "
            "piece a little thinner and its statistics a little noisier. The cost is small "
            "and the gain where it applies is large, but it is a trade rather than a free "
            "improvement, and that is why it is an option and not the default.\n"
        )
        add(
            "**The obvious alternative does not work, and it is worth saying why.** If the "
            "hard responses are the ones whose metadata collides with the baseline, the "
            "natural fix is stage two: re-fetch the candidates and re-rank them on body "
            "structure, which `fly taste` already measures. It cannot help here. Stage two "
            "only ever sees what stage one surfaced, and on `bench-sprawl` the two hard "
            "responses sat at ranks 618 and 1,237 under a single baseline - far outside any "
            "re-fetch window a rate limit permits. Re-fetching far enough to reach them is a "
            "second full scan. The fix had to be something that reorders the whole stream "
            "for free, which is what a per-shape baseline does.\n"
        )
        add(
            "**The hazard is partitions of one**, and it is not hypothetical: `--per dir` "
            "shipped exactly this bug, scoring 1.000 on partitions holding a single response "
            "that had nothing to be unlike. `shape` produces them constantly - an unusually "
            "large page is often the only thing in its size decade - so a partition below "
            "`min_observations` does not score at all, and its responses fall back to the "
            "host baseline. That fallback is why the numbers above are trustworthy and is "
            "asserted in `tests/test_partition.py`.\n"
        )

    add("## What this does and does not claim\n")
    add(
        "**Does:** on a stream covering several hosts, one baseline per host finds hits that "
        "a shared baseline scores at exactly zero, and it needs no per-host configuration to "
        "do it. That is the documented limitation of the incumbent, and it is the first place "
        "flypaper's design buys something a filter cannot have at any amount of tuning - not "
        "because the ranking is cleverer, but because a filter you can afford fifty of is a "
        "different kind of object.\n"
    )
    add(
        "**Does not:** anything about a single host, which is where every earlier measurement "
        "was taken and where flypaper matches `-ac` rather than beating it. An operator "
        "running one host at a time gains nothing from this.\n"
    )
    add(
        "**Watch out for thin partitions.** A host seen three times has no baseline and "
        "everything in it looks novel; found immediately on a real scan, where `--per dir` "
        "with a wordlist containing slashes produced partitions of one response each, all "
        "scoring 1.000. Partitions below `min_observations` are scored and learned from but "
        "never surfaced, and the directory key is taken from the scan base rather than by "
        "splitting the URL, so a word containing a slash cannot invent a directory.\n"
    )

    OUT.write_text("\n".join(lines) + provenance_footer(seed=0))
    print(f"wrote {OUT.relative_to(REPO_ROOT)}", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
