"""Novelty scoring: encoder -> FlyHash -> Fly Bloom Filter -> a ranking.

Single pass. Each result is encoded against the statistics of everything before it, hashed
to a sparse tag, scored against the filter, and only then written into it - so nothing is
ever scored using knowledge of itself, and the ranking a live scan produces is the same one
it would produce offline.

The projection defaults to the published random one. M3 measured the connectome-wired
alternative against it and found them statistically indistinguishable on novelty detection
(reports/m3-flyhash-benchmark.md), so the default is the one that does not need a 508 MB
download. Pass `projection="connectome"` to use the measured wiring.

This ranks. It does not detect. A high score means *this response is unlike the others*,
which is not the same as *this response is interesting*.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field

import numpy as np

from flypaper.brain.bloom import FlyBloomFilter
from flypaper.brain.flyhash import (
    FlyHash,
    connectome_projection,
    random_projection,
)
from flypaper.encode import CHANNEL_SET_VERSION, Encoder
from flypaper.ingest.ffuf import FfufResult

__all__ = ["DEFAULTS", "Ranker", "Scored", "rank"]

#: Defaults, each traceable to a paper or a measurement. See bench/thresholds.yaml.
DEFAULTS = {
    "n_kc": 2045,  # measured: Kenyon cells, MaleCNS right hemisphere
    "fan_in": 6,  # published: Dasgupta, Stevens & Navlakha 2017
    "sparsity": 0.05,  # published: ~95% of the tag is zero
    "learning_rate": 0.4,
}


@dataclass(frozen=True, slots=True)
class Scored:
    """One result with its novelty score and the provenance to interpret it."""

    result: FfufResult
    novelty: float
    position: int
    channel_set: str
    projection: str

    @property
    def url(self) -> str:
        return self.result.url

    def as_dict(self) -> dict:
        r = self.result
        return {
            "novelty": round(float(self.novelty), 6),
            "url": r.url,
            "word": r.word,
            "status": r.status,
            "length": r.length,
            "words": r.words,
            "lines": r.lines,
            "content_type": r.content_type,
            "redirect_location": r.redirect_location,
            "duration_ms": round(r.duration_ms, 3),
            "ffufhash": r.ffufhash,
            "position": self.position,
            "channel_set": self.channel_set,
            "projection": self.projection,
        }


@dataclass
class Ranker:
    """Streaming novelty scorer.

    `decay_halflife` is in records, not seconds, when scoring a single run: within one scan
    "time" is how much else has gone past. Persisted baselines (M6) will carry wall-clock
    timestamps instead.
    """

    channel_set: str = CHANNEL_SET_VERSION
    projection: str = "random"
    n_kc: int = DEFAULTS["n_kc"]
    fan_in: int = DEFAULTS["fan_in"]
    sparsity: float = DEFAULTS["sparsity"]
    learning_rate: float = DEFAULTS["learning_rate"]
    decay_halflife: float | None = None
    seed: int = 0
    circuit_path: str | None = None

    encoder: Encoder = field(init=False)
    flyhash: FlyHash = field(init=False)
    filter: FlyBloomFilter = field(init=False)
    n_scored: int = field(init=False, default=0)

    def __post_init__(self) -> None:
        self.encoder = Encoder(self.channel_set)
        rng = np.random.default_rng(self.seed)

        if self.projection == "connectome":
            from flypaper.brain.extract import Circuit

            if not self.circuit_path:
                raise ValueError(
                    "projection='connectome' needs circuit_path, produced by "
                    "flypaper.brain.extract.extract(...).save(...)"
                )
            circuit = Circuit.load(self.circuit_path)
            if circuit.n_channels != len(self.encoder):
                raise ValueError(
                    f"channel set {self.channel_set} has {len(self.encoder)} channels but the "
                    f"circuit has {circuit.n_channels} receptor channels; they must match"
                )
            matrix = connectome_projection(circuit.pn_to_kc, binary=True)
            self.n_kc = circuit.n_kc
        elif self.projection == "random":
            matrix = random_projection(len(self.encoder), self.n_kc, self.fan_in, rng=rng)
        else:
            raise ValueError(f"unknown projection {self.projection!r}")

        self.flyhash = FlyHash(matrix, sparsity=self.sparsity)
        self.filter = FlyBloomFilter(
            self.flyhash.n_kc,
            learning_rate=self.learning_rate,
            decay_halflife=self.decay_halflife,
        )

    @property
    def saturation(self) -> float:
        return self.filter.saturation

    def score(self, result: FfufResult) -> Scored:
        """Score one result, then learn from it. In that order."""
        vector = self.encoder.encode(result)
        tag = self.flyhash.tag_valued(vector[None, :])
        novelty = float(self.filter.observe(tag)[0])
        scored = Scored(
            result=result,
            novelty=novelty,
            position=self.n_scored,
            channel_set=self.channel_set,
            projection=self.projection,
        )
        self.n_scored += 1
        return scored

    def stream(self, results: Iterable[FfufResult]) -> Iterator[Scored]:
        """Live mode: one pass, scoring each result against only what came before it."""
        for result in results:
            yield self.score(result)

    def observe_only(self, results: Iterable[FfufResult]) -> None:
        """Build the baseline without producing scores. The first of two offline passes."""
        for result in results:
            vector = self.encoder.encode(result)
            self.filter.observe(self.flyhash.tag_valued(vector[None, :]))

    def score_against_baseline(self, result: FfufResult) -> Scored:
        """Score without learning, discounting this record's own contribution.

        Leave-one-out: the record was written into the baseline in pass one, so scoring it
        against that baseline would score it partly against itself. `score_excluding` divides
        its own depression back out, which keeps offline scores on the same scale as live
        ones.
        """
        vector = self.encoder.encode(result)
        tag = self.flyhash.tag_valued(vector[None, :])
        scored = Scored(
            result=result,
            novelty=float(self.filter.score_excluding(tag)[0]),
            position=self.n_scored,
            channel_set=self.channel_set,
            projection=self.projection,
        )
        self.n_scored += 1
        return scored


def rank(results: Iterable[FfufResult], *, passes: int = 2, **kwargs) -> list[Scored]:
    """Score everything and return it most-novel first.

    `passes=1` is live mode: each result is scored against only what preceded it. That is
    what a running scan can do, and it has a cold start - the first handful of responses are
    novel because nothing is familiar yet, not because they are unusual.

    `passes=2` is offline mode and the default for a completed run, where withholding
    information you already have buys nothing. The first pass builds the baseline, the
    second scores every record against all of it. A genuine one-off still scores high,
    because one sighting among two thousand depresses its cells far less than two thousand
    sightings of the same page depress theirs.

    Either way the ordering is a ranking, not a verdict.
    """
    if passes not in (1, 2):
        raise ValueError("passes must be 1 (live) or 2 (offline)")

    if passes == 1:
        ranker = Ranker(**kwargs)
        scored = list(ranker.stream(results))
    else:
        results = list(results)
        ranker = Ranker(**kwargs)
        ranker.observe_only(results)
        # A fresh encoder would re-derive running statistics from scratch; reuse the settled
        # one so the second pass encodes against the whole run, as it scores against it.
        scored = [ranker.score_against_baseline(r) for r in results]

    scored.sort(key=lambda s: (-s.novelty, s.position))
    return scored
