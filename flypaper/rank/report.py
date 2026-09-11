"""Terminal output and JSONL, for live use and for piping onward.

Output says **novel**. Never "vulnerable", never "finding", never "issue" - a test enforces
that, because the difference between "this response is unlike the others" and "this response
is a problem" is the whole honesty of the tool.

Every line carries the channel-set version that produced it. Scores from different channel
sets are not comparable, so a score without its version is not interpretable.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Iterable

import numpy as np

from flypaper.rank.score import Scored

__all__ = ["PartitionedGate", "Terminal", "write_jsonl"]

# Colour only when stdout is a terminal; a pipe gets clean text.
_RESET = "\033[0m"

# The score is the fraction of this response's tag the filter has not already depressed, so
# the labels describe that fraction rather than pronouncing a verdict on the response.
_BANDS = (
    (0.80, "\033[1;31m", "unseen"),
    (0.50, "\033[0;33m", "mostly-new"),
    (0.20, "\033[0;36m", "part-new"),
    (0.00, "\033[0;90m", "trace"),
)


def band(novelty: float) -> tuple[str, str]:
    for threshold, colour, label in _BANDS:
        if novelty >= threshold:
            return colour, label
    return _BANDS[-1][1], _BANDS[-1][2]


class RollingPercentile:
    """A self-calibrating cutoff: show a result only if it is unusual *for this target*.

    A fixed novelty threshold cannot work across targets, and the corpus says so plainly:
    baseline noise sits at 0.000 on every surface, but the weakest genuine hit ranges from
    0.001 on one surface to 0.318 on another. Any constant that surfaces the first drowns
    the operator on the second, and any constant that suits the second hides the first.

    So the operator specifies a review budget - "show me the most novel half-percent" - and
    the cutoff is whatever that means here. The window is rolling, so a target whose
    behaviour changes mid-scan recalibrates rather than going quiet.
    """

    def __init__(self, percentile: float = 99.5, window: int = 1000, minimum: int = 200):
        self.percentile = percentile
        self.window = window
        self.minimum = minimum
        self._scores: list[float] = []

    def observe(self, novelty: float) -> None:
        """Add a score to the distribution the cutoff is drawn from.

        Warm-up scores must not be passed in. They are a known artifact of an empty filter,
        and letting them set the cutoff puts it near 1.0 for the rest of the run - which is
        how this gate first came to pass nothing at all.
        """
        self._scores.append(novelty)
        if len(self._scores) > self.window:
            del self._scores[: len(self._scores) - self.window]

    def passes(self, novelty: float) -> bool:
        """True when this score is in the top (100 - percentile)% of what we have seen.

        Before `minimum` scores have arrived there is no distribution to speak of, so
        nothing passes; that also disposes of the cold start, where the filter is empty and
        everything looks novel.
        """
        if len(self._scores) < self.minimum:
            return False
        cutoff = float(np.percentile(self._scores, self.percentile))
        return novelty >= cutoff and novelty > 0.0


class PartitionedGate:
    """One self-calibrating cutoff per partition.

    A single gate across a multi-host scan is decided by whichever host is noisiest, and
    the quiet hosts then never surface anything. Each host gets its own cutoff for the same
    reason each gets its own baseline: "unusual" is a statement about a target, not about a
    scan.
    """

    def __init__(self, percentile: float = 99.5, window: int = 1000, minimum: int = 200):
        self.percentile = percentile
        self.window = window
        self.minimum = minimum
        self._gates: dict[str, RollingPercentile] = {}

    def _gate(self, key: str) -> RollingPercentile:
        if key not in self._gates:
            self._gates[key] = RollingPercentile(self.percentile, self.window, self.minimum)
        return self._gates[key]

    def observe(self, key: str, novelty: float) -> None:
        self._gate(key).observe(novelty)

    def passes(self, key: str, novelty: float) -> bool:
        return self._gate(key).passes(novelty)

    def __len__(self) -> int:
        return len(self._gates)


class Terminal:
    """Live output, readable while a scan runs.

    Only results at or above `threshold` are printed. That is a display choice, not a
    filter: everything still goes into the baseline, and `--jsonl` still carries everything.
    Suppressing the familiar from the screen is the entire point of the tool, but suppressing
    it from the model would break it.

    `warmup` suppresses the first N results. In live mode the filter starts empty, so the
    first responses score high because nothing is familiar yet rather than because they are
    unusual - a cold start, not a result. They are still learned from; they are just not
    worth an operator's attention.
    """

    def __init__(
        self,
        stream=None,
        threshold: float | None = None,
        percentile: float = 99.5,
        colour: bool | None = None,
        warmup: int = 0,
        partitioned: bool = False,
    ):
        self.stream = stream or sys.stdout
        self.threshold = threshold
        self.partitioned = partitioned
        if threshold is not None:
            self.gate = None
        elif partitioned:
            self.gate = PartitionedGate(percentile)
        else:
            self.gate = RollingPercentile(percentile)
        self.percentile = percentile
        self.colour = self.stream.isatty() if colour is None else colour
        self.warmup = warmup
        self.shown = 0
        self.total = 0
        self.suppressed = 0

    def _paint(self, text: str, colour: str) -> str:
        return f"{colour}{text}{_RESET}" if self.colour else text

    def header(self, channel_set: str, projection: str, budget: int | None = None) -> None:
        if budget:
            cut = f"top {budget} by novelty"
        elif self.threshold is not None:
            cut = f"novelty >= {self.threshold:.2f}"
        else:
            cut = f"top {100 - self.percentile:g}% most novel for this target"
        print(
            f"flypaper: ranking by novelty  [channels {channel_set}, projection "
            f"{projection}, showing {cut}]\n"
            f"          novel means structurally unusual. It is not a vulnerability.",
            file=self.stream,
            flush=True,
        )

    def result(self, scored: Scored, *, force: bool = False) -> None:
        self.total += 1
        if not force:
            if self.total <= self.warmup:
                # Scored and learned from, but neither shown nor allowed to set the cutoff.
                self.suppressed += 1
                return
        if not force and not scored.settled:
            # Scored and learned from, but its partition has no baseline yet.
            self.suppressed += 1
            return
        if self.gate is not None:
            if self.partitioned:
                self.gate.observe(scored.partition, scored.novelty)
            else:
                self.gate.observe(scored.novelty)
        if not force:
            if self.threshold is not None:
                if scored.novelty < self.threshold:
                    return
            elif self.partitioned:
                if not self.gate.passes(scored.partition, scored.novelty):
                    return
            elif not self.gate.passes(scored.novelty):
                return
        self.shown += 1
        colour, label = band(scored.novelty)
        r = scored.result
        where = f"{scored.partition}  " if scored.partition else ""
        print(
            f"{self._paint(f'{scored.novelty:5.3f}', colour)} {label:8} "
            f"[Status: {r.status}, Size: {r.length}, Words: {r.words}, Lines: {r.lines}, "
            f"Duration: {r.duration_ms:.0f}ms] {where}{r.word}",
            file=self.stream,
            flush=True,
        )

    def footer(self, saturation: float) -> None:
        warm = f", {self.suppressed} suppressed during warm-up" if self.suppressed else ""
        print(
            f"flypaper: showed {self.shown} of {self.total} responses{warm}; "
            f"baseline {saturation:.0%} saturated.",
            file=self.stream,
            flush=True,
        )


def write_jsonl(scored: Iterable[Scored], stream=None) -> int:
    """One JSON object per line, for piping onward. Ordering is the caller's."""
    stream = stream or sys.stdout
    count = 0
    for item in scored:
        print(json.dumps(item.as_dict(), ensure_ascii=False), file=stream)
        count += 1
    return count
