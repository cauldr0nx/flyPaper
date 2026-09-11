#!/usr/bin/env python3
"""Record a real flypaper run for the dashboard to replay.

The dashboard does not simulate anything and does not invent activity. It replays a run
that actually happened: for each response, which Kenyon cells the tag selected, what the
Bloom filter scored it, and what the terminal would have printed.

That means the synapses lighting up on screen are the synapses that were read, in the order
they were read, at their measured coordinates.

    python -m flypaper.web.replay --corpus bench/corpus/bench-token.jsonl --out …

Live capture is the same code path: `flypaper.web.server` calls `record()` directly.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from flypaper import REPO_ROOT, provenance
from flypaper.ingest.ffuf import FfufResult
from flypaper.ingest.stream import iter_batch
from flypaper.rank.report import RollingPercentile, band
from flypaper.rank.score import Ranker

STATIC = REPO_ROOT / "flypaper" / "web" / "static"
MAX_FRAMES = 1400


def _log_line(index: int, result: FfufResult, novelty: float, shown: bool) -> str:
    """What the terminal would have printed for this response."""
    _, label = band(novelty)
    mark = ">" if shown else " "
    return (
        f"{mark} {index:5d}  {novelty:5.3f} {label:10} "
        f"[{result.status} {result.length}b {result.words}w {result.lines}l] {result.word}"
    )


def record(
    results: list[FfufResult],
    *,
    hits: set[str] | None = None,
    channel_set: str | None = None,
    percentile: float = 99.5,
    warmup: int = 50,
    max_frames: int = MAX_FRAMES,
    label: str = "",
) -> dict:
    """Score a run and capture every frame the dashboard needs.

    Single pass, live semantics: each response is scored against only what came before it,
    which is what a running scan does and what makes the replay honest.
    """
    hits = hits or set()
    kwargs = {"channel_set": channel_set} if channel_set else {}
    ranker = Ranker(**kwargs)
    gate = RollingPercentile(percentile)

    frames: list[dict] = []
    saturation: list[float] = []
    stride = max(1, len(results) // max_frames)

    for index, result in enumerate(results):
        scored = ranker.score(result)
        novelty = scored.novelty

        shown = False
        if index >= warmup:
            gate.observe(novelty)
            shown = gate.passes(novelty)

        if index % stride and not shown:
            continue

        vector = ranker.encoder.encode(result)
        tag = ranker.flyhash.tag(vector[None, :])[0]
        active = np.flatnonzero(tag).astype(int).tolist()

        frames.append(
            {
                "i": index,
                "n": round(float(novelty), 5),
                "kc": active,
                "w": result.word,
                "s": result.status,
                "len": result.length,
                "wd": result.words,
                "ln": result.lines,
                "ms": round(result.duration_ms, 2),
                "hit": result.word in hits,
                "shown": bool(shown),
                "log": _log_line(index, result, novelty, shown),
            }
        )
        saturation.append(round(ranker.saturation, 5))

    return {
        "label": label,
        "channel_set": ranker.channel_set,
        "projection": ranker.projection,
        "n_kc": int(ranker.flyhash.n_kc),
        "n_active": int(ranker.flyhash.n_active),
        "sparsity": ranker.sparsity,
        "total_responses": len(results),
        "frames": frames,
        "saturation": saturation,
        "weights": [round(float(w), 4) for w in ranker.filter.weights],
        "hits": sorted(hits),
        "shown_count": sum(1 for f in frames if f["shown"]),
        "provenance": provenance(),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--corpus", default="bench/corpus/bench-token.jsonl")
    ap.add_argument("--surface", default=None, help="name it, to pick up the labelled hits")
    ap.add_argument("--out", default=str(STATIC / "run.json"))
    ap.add_argument("--max-frames", type=int, default=MAX_FRAMES)
    args = ap.parse_args()

    hits: set[str] = set()
    surface = args.surface or Path(args.corpus).stem
    try:
        import sys

        sys.path.insert(0, str(REPO_ROOT / "bench"))
        from capture import SURFACES

        hits = set(SURFACES.get(surface, {}).get("hits", []))
    except Exception:
        pass

    results = list(iter_batch(args.corpus))
    payload = record(results, hits=hits, max_frames=args.max_frames, label=surface)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, separators=(",", ":")))
    print(f"wrote {out} ({out.stat().st_size / 1e6:.1f} MB)")
    print(f"  {payload['total_responses']} responses -> {len(payload['frames'])} frames")
    print(f"  {payload['shown_count']} surfaced, {len(hits)} labelled hits")


if __name__ == "__main__":
    main()
