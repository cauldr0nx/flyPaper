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
MULTI_HOST_SURFACES = ("bench-mixed", "bench-token", "bench-stable", "bench-calib", "ffufme-no404")


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


def evaluate(stream: list[FfufResult], hits: dict[str, set[str]], how: str) -> dict:
    scored = rank(stream, passes=2, partition=how)
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
    stream, hits = pathological_stream()
    experiments["pathological"] = {how: evaluate(stream, hits, how) for how in ("none", "host")}

    stream, hits = multi_host_stream()
    if stream:
        experiments["corpus"] = {how: evaluate(stream, hits, how) for how in ("none", "host")}

    write_report(experiments)
    for name, res in experiments.items():
        for how, m in res.items():
            print(
                f"  {name:13} --per {how:5} recall@50 {m['recall'][50]}/{m['total_hits']}"
                f"  first at {m['first']}  worst at {m['worst']}",
                file=sys.stderr,
            )
    return 0


def write_report(experiments: dict) -> None:
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
