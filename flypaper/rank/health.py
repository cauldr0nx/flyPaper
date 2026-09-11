"""Is this input stream something a baseline can be learned from?

The single most likely way to misuse flypaper is to feed it a stream that has already been
filtered. Every ffuf tutorial teaches `-mc 200,301,302` or `-fc 404`, and that is the right
habit for ffuf: show me the interesting ones. It is exactly wrong here. **The noise is the
baseline.** Filter the 404s upstream and there is nothing left to learn what ordinary looks
like, so everything that remains looks ordinary - and flypaper will rank it confidently and
uselessly.

It fails quietly, which is the problem. A pre-filtered stream produces a ranking that looks
fine. So the stream is checked as it goes and the operator is told, in the terms of the
mistake they actually made.

Nothing here changes a score or drops a result. It only reports.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from flypaper.ingest.ffuf import FfufResult

__all__ = ["StreamHealth"]

#: Below this, no baseline worth the name has formed.
THIN_STREAM = 200

#: A scan of a real target is mostly misses. If almost nothing is, the misses were removed.
SUSPICIOUS_MISS_SHARE = 0.10

#: If this much of a stream is novel enough to surface, the baseline is not describing it.
IMPLAUSIBLE_SURFACED_SHARE = 0.20

MISS_STATUSES = frozenset({400, 401, 403, 404, 405, 406, 410, 429, 500, 502, 503})


@dataclass
class StreamHealth:
    """Watches a stream and says whether it could have taught a baseline."""

    total: int = 0
    surfaced: int = 0
    statuses: Counter = field(default_factory=Counter)
    partitions: Counter = field(default_factory=Counter)

    def observe(self, result: FfufResult, *, surfaced: bool = False, partition: str = "") -> None:
        self.total += 1
        self.statuses[result.status] += 1
        if surfaced:
            self.surfaced += 1
        if partition:
            self.partitions[partition] += 1

    @property
    def miss_share(self) -> float:
        if not self.total:
            return 0.0
        return sum(n for s, n in self.statuses.items() if s in MISS_STATUSES) / self.total

    @property
    def surfaced_share(self) -> float:
        return self.surfaced / self.total if self.total else 0.0

    def warnings(self) -> list[str]:
        """What is wrong with this stream, in the terms of the mistake that caused it."""
        out: list[str] = []
        if not self.total:
            return ["no results were read at all - is ffuf writing JSON to stdout? (-json)"]

        if self.total < THIN_STREAM:
            out.append(
                f"only {self.total} responses. A baseline needs a few hundred before it "
                f"means anything; below that the ranking is mostly cold start."
            )

        if self.miss_share < SUSPICIOUS_MISS_SHARE:
            shown = ", ".join(f"{s}x{n}" for s, n in self.statuses.most_common(4))
            out.append(
                f"only {self.miss_share:.0%} of responses look like misses ({shown}). "
                f"If you filtered them upstream - `-mc 200,301` or `-fc 404` - flypaper has "
                f"nothing to learn 'ordinary' from, and will rank confidently and uselessly. "
                f"Run ffuf with `-mc all` and let flypaper do the filtering."
            )

        if self.surfaced and self.surfaced_share > IMPLAUSIBLE_SURFACED_SHARE:
            out.append(
                f"{self.surfaced_share:.0%} of responses surfaced. That is far too many to "
                f"be outliers; either the input was already filtered, or the target answers "
                f"differently to every request and this is the wrong instrument for it."
            )

        thin = [p for p, n in self.partitions.items() if n < 25]
        if thin and len(thin) > len(self.partitions) / 2:
            out.append(
                f"{len(thin)} of {len(self.partitions)} partitions have under 25 responses "
                f"and cannot have a baseline. They are scored but never surfaced."
            )
        return out

    def summary(self) -> str:
        return f"{self.total} responses, {self.miss_share:.0%} misses" + (
            f", {len(self.partitions)} partitions" if self.partitions else ""
        )
