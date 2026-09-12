"""Encoding a result into the receptor vector.

Single pass, unsupervised, no labels and no training corpus. Each record is encoded against
the running statistics of everything before it, then contributes to those statistics - so
the encoder has no way to see the future, which is the same constraint the fly is under and
the same one a live scan is under.

Two properties are tested explicitly in `bench/test_encoder_invariance.py`, because they
are the value proposition rather than a nice-to-have:

  1. Rotating-token invariance - responses identical but for a CSRF token land in the same
     neighbourhood.
  2. Soft-404 collapse - a wildcard-responding host produces one dense familiar cluster,
     not N novel items.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator

import numpy as np

from flypaper.encode.channels import CHANNEL_SET_VERSION, ChannelSet, Context, get_channel_set
from flypaper.ingest.ffuf import FfufResult

__all__ = ["Encoder", "encode", "encode_all"]


class Encoder:
    """Turns results into receptor vectors.

    `observe_first` controls whether a record updates the running statistics before or after
    it is encoded. After is the honest default: a record must not be normalised using
    knowledge of itself.
    """

    def __init__(self, channel_set: str | ChannelSet | None = None, *, observe_first: bool = False):
        self.channel_set = (
            channel_set if isinstance(channel_set, ChannelSet) else get_channel_set(channel_set)
        )
        self.context = Context()
        self.observe_first = observe_first
        self.n_seen = 0

    @property
    def version(self) -> str:
        return self.channel_set.version

    @property
    def names(self) -> tuple[str, ...]:
        return self.channel_set.names

    def __len__(self) -> int:
        return len(self.channel_set)

    def encode(self, result: FfufResult, *, observe: bool = True) -> np.ndarray:
        """Encode one result, optionally without folding it into the running statistics.

        `observe=False` is what "score against a baseline" should always have meant. A
        second pass over a completed run, or a `fly watch` scoring against a stored
        baseline, is supposed to hold the baseline still - but the statistics the channels
        z-score against live in the encoder, and every call was updating them. So the
        answer depended on how many other responses happened to be encoded first, and two
        callers scoring the same response against the same baseline could disagree.

        It is not a small effect where it matters. On `bench-token`, whose subtlest hit sits
        eight words from the baseline, the drift was enough to move that hit between rank 8
        and rank 1998 depending only on which encoder the caller had stepped.
        """
        if observe and self.observe_first:
            self.context.observe(result)
        vector = np.fromiter(
            (channel.fn(result, self.context) for channel in self.channel_set.channels),
            dtype=np.float32,
            count=len(self.channel_set),
        )
        if observe and not self.observe_first:
            self.context.observe(result)
        if observe:
            self.n_seen += 1
        # A channel that returns NaN would poison every downstream distance silently.
        return np.nan_to_num(vector, nan=0.0, posinf=1.0, neginf=0.0)

    def encode_all(self, results: Iterable[FfufResult]) -> np.ndarray:
        rows = [self.encode(r) for r in results]
        if not rows:
            return np.empty((0, len(self.channel_set)), dtype=np.float32)
        return np.vstack(rows)

    def iter_encode(self, results: Iterable[FfufResult]) -> Iterator[tuple[FfufResult, np.ndarray]]:
        """Stream, keeping each result beside its vector. What the ranker will consume."""
        for result in results:
            yield result, self.encode(result)


def encode(result: FfufResult, channel_set: str | None = None) -> np.ndarray:
    """Encode one result in isolation. Convenience for tests; a real run uses `Encoder`,
    which carries the running statistics that several channels depend on."""
    return Encoder(channel_set or CHANNEL_SET_VERSION).encode(result)


def encode_all(results: Iterable[FfufResult], channel_set: str | None = None) -> np.ndarray:
    return Encoder(channel_set or CHANNEL_SET_VERSION).encode_all(results)
