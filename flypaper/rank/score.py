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

import math
import time
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import numpy as np

from flypaper.brain.bloom import FlyBloomFilter
from flypaper.brain.flyhash import (
    FlyHash,
    connectome_projection,
    degree_sampled_projection,
    random_matched_projection,
    random_projection,
)
from flypaper.encode import CHANNEL_SET_VERSION, Encoder
from flypaper.ingest.ffuf import FfufResult

__all__ = [
    "DEFAULTS",
    "PARTITIONS",
    "PartitionedRanker",
    "Ranker",
    "Scored",
    "partition_key",
    "rank",
]

#: The measured circuit projects from a fixed number of glomeruli, so a channel set that is
#: not exactly that wide cannot be fed to it at all. Said once, here, because the error is
#: otherwise a bare integer mismatch with no route to the fix.
GLOMERULAR_HINT = "Use channel_set='v4-glomerular', which is sized to the circuit."

#: Defaults, each traceable to a paper or a measurement. See bench/thresholds.yaml.
DEFAULTS = {
    "n_kc": 2045,  # measured: Kenyon cells, MaleCNS right hemisphere
    "fan_in": 6,  # published: Dasgupta, Stevens & Navlakha 2017
    "sparsity": 0.05,  # published: ~95% of the tag is zero
    "learning_rate": 0.4,
}


def _host_of(result: FfufResult) -> str:
    if result.host:
        return result.host
    try:
        return urlsplit(result.url).netloc
    except ValueError:
        return ""


def _dir_of(result: FfufResult) -> str:
    """Host plus the base the scan was fuzzing, with the fuzzed word removed.

    ffuf's `-recursion` walks into every directory it finds, and a directory usually has its
    own idea of what "not found" looks like - a different error template, a different
    framework, sometimes a different server. Treating a recursive scan as one population
    blurs all of them together. This is the partition feroxbuster gets from detecting
    wildcards per directory; here it falls out of keeping one filter per key.

    The word is stripped rather than the path simply being split on its last slash, because
    **wordlists contain slashes**. `admin/backup.php` against `/cd/basic/` would otherwise
    be filed under `/cd/basic/admin/` - a partition of one, which then scores 1.000 because
    it has never seen anything else. Measured, on a real scan, before this was fixed.
    """
    url = result.url
    word = result.word
    if word and url.endswith(word):
        base = url[: -len(word)]
    else:
        try:
            path = urlsplit(url).path or "/"
        except ValueError:
            return _host_of(result) + "/"
        base = path.rsplit("/", 1)[0] + "/"
    try:
        parts = urlsplit(base)
        path = parts.path or "/"
    except ValueError:
        return _host_of(result) + "/"
    if not path.endswith("/"):
        path = path.rsplit("/", 1)[0] + "/"
    return f"{_host_of(result)}{path}"


#: Size buckets for `shape`, one per power of two. Coarse on purpose: the point is to put a
#: response beside the population it resembles, not to give it a bucket of its own. A finer
#: split would hand every unusual response a private partition, where it is novel by
#: definition and the ranking means nothing.
def _shape_of(result: FfufResult) -> str:
    """Host, status, and the size decade - one baseline per kind of page a host serves.

    `host` and `dir` split a stream by *where* a response came from. This splits it by
    *what it looks like*, which is the thing that actually breaks a single baseline: a host
    serving seven different response populations has one standard deviation spanning all
    seven, so a page 25% off its own population's size sits well under one sigma of the
    whole and reads as unremarkable. Measured on `bench-sprawl`, that is the difference
    between the hardest labelled hit landing past rank 700 and landing in the top ten.

    Thin partitions are the hazard, and `PartitionedRanker` handles them by falling back
    rather than by suppressing: a response alone in its shape is scored against the host.
    """
    size = result.length if result.length > 0 else 0
    decade = int(math.log2(size)) if size > 0 else -1
    return f"{_host_of(result)}|{result.status}|{decade}"


#: How to split a stream into populations that each deserve their own baseline.
PARTITIONS: dict[str, Callable[[FfufResult], str]] = {
    "none": lambda result: "",
    "host": _host_of,
    "dir": _dir_of,
    "shape": _shape_of,
}

#: Where a partition falls back to when it has not seen enough to have an opinion. `shape`
#: is fine-grained enough to produce partitions of one, and a partition of one calls its
#: single member maximally novel - which is how `--per dir` once scored 1.000 on partitions
#: of one and meant nothing by it.
FALLBACK: dict[str, str] = {"shape": "host"}


def partition_key(result: FfufResult, how: str) -> str:
    try:
        return PARTITIONS[how](result)
    except KeyError:
        raise ValueError(
            f"unknown partition {how!r}; known: {', '.join(sorted(PARTITIONS))}"
        ) from None


@dataclass(frozen=True, slots=True)
class Scored:
    """One result with its novelty score and the provenance to interpret it."""

    result: FfufResult
    novelty: float
    position: int
    channel_set: str
    projection: str
    partition: str = ""
    #: Whether this result's partition had seen enough to have an opinion. A host or
    #: directory seen a handful of times has no baseline, so everything in it looks novel;
    #: unsettled results are scored but not surfaced.
    settled: bool = True

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
            "partition": self.partition,
            "settled": self.settled,
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
    #: How "time" is counted for temporal decay. "records" is right within one scan, where
    #: elapsed time is how much else has gone past. "wallclock" is right across scans, and
    #: is required for a persisted baseline: a baseline saved six months ago should be six
    #: months stale, not 1,998 records stale. Mixing the two corrupts a baseline silently,
    #: because the stored last-seen stamps would be on the wrong scale, so the store
    #: records which was used and refuses a mismatch.
    time_base: str = "records"

    encoder: Encoder = field(init=False)
    flyhash: FlyHash = field(init=False)
    filter: FlyBloomFilter = field(init=False)
    n_scored: int = field(init=False, default=0)

    def __post_init__(self) -> None:
        if self.time_base not in ("records", "wallclock"):
            raise ValueError(f"unknown time_base {self.time_base!r}")
        self.encoder = Encoder(self.channel_set)
        rng = np.random.default_rng(self.seed)

        if self.projection in ("connectome", "random-matched"):
            from flypaper.brain.extract import Circuit

            if not self.circuit_path:
                raise ValueError(
                    f"projection={self.projection!r} needs circuit_path, produced by "
                    "flypaper.brain.extract.extract(...).save(...)"
                )
            circuit = Circuit.load(self.circuit_path)
            if circuit.n_channels != len(self.encoder):
                raise ValueError(
                    f"channel set {self.channel_set} has {len(self.encoder)} channels but the "
                    f"circuit has {circuit.n_channels} receptor channels; they must match. "
                    f"{GLOMERULAR_HINT}"
                )
            matrix = (
                connectome_projection(circuit.pn_to_kc, binary=True)
                if self.projection == "connectome"
                # The degree-preserving null: same number of claws per Kenyon cell as the
                # measured wiring, rewired at random. A difference against plain `random` is
                # the degree distribution; a difference against this one is the wiring.
                else random_matched_projection(circuit.pn_to_kc, rng=rng)
            )
            self.n_kc = circuit.n_kc
        elif self.projection == "random":
            matrix = random_projection(len(self.encoder), self.n_kc, self.fan_in, rng=rng)
        elif self.projection == "degree-sampled":
            matrix = degree_sampled_projection(len(self.encoder), self.n_kc, rng=rng)
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

    def _when(self) -> float | None:
        """The timestamp to score at. `None` lets the filter tick its own record counter."""
        return time.time() if self.time_base == "wallclock" else None

    def score(self, result: FfufResult) -> Scored:
        """Score one result, then learn from it. In that order."""
        vector = self.encoder.encode(result)
        tag = self.flyhash.tag_valued(vector[None, :])
        novelty = float(self.filter.observe(tag, when=self._when())[0])
        scored = Scored(
            result=result,
            novelty=novelty,
            position=self.n_scored,
            channel_set=self.channel_set,
            projection=self.projection,
        )
        self.n_scored += 1
        return scored

    def score_only(self, result: FfufResult) -> Scored:
        """Score against the baseline as it stands: no learning, no leave-one-out.

        Distinct from `score_against_baseline`, and the difference matters. That one
        discounts the record's own contribution, which is right when ranking a completed run
        where every record is *in* the baseline. When comparing a new scan against a stored
        one the record is **not** in it, and discounting a contribution it never made
        inflates its novelty - a page seen once last week scored 0.64 instead of 0.0, so
        every real page on the target reported as new every week.

        **The encoder still learns here, and that is a known gap rather than a decision.**
        `score_against_baseline` freezes its encoder because pass one built the statistics
        it needs. This cannot: `Store` persists the filter's weights and not the encoder's
        running statistics, so a restored baseline arrives with an encoder that has seen
        nothing, and freezing it would leave every channel answering "no opinion" forever.
        Learning from the new stream rebuilds usable statistics, at the cost that this
        week's tags are computed against this week's statistics while the weights were
        learned against last week's. Fixing it properly means persisting the encoder
        context, which is a store schema change.
        """
        vector = self.encoder.encode(result)
        tag = self.flyhash.tag_valued(vector[None, :])
        scored = Scored(
            result=result,
            novelty=float(self.filter.score(tag, when=self._when())[0]),
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
            self.filter.observe(self.flyhash.tag_valued(vector[None, :]), when=self._when())

    def score_against_baseline(self, result: FfufResult) -> Scored:
        """Score without learning, discounting this record's own contribution.

        Leave-one-out: the record was written into the baseline in pass one, so scoring it
        against that baseline would score it partly against itself. `score_excluding` divides
        its own depression back out, which keeps offline scores on the same scale as live
        ones.

        The encoder does not learn here either. Pass one built the statistics; pass two
        reads them. Letting pass two keep updating them made a record's score depend on how
        many other records had been scored before it, so the same record against the same
        baseline could come out differently depending on the order of the second pass.
        """
        vector = self.encoder.encode(result, observe=False)
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


@dataclass
class PartitionedRanker:
    """One baseline per host, or per directory, learned as the stream arrives.

    This is the thing ffuf cannot do and says so. From its own issue tracker, on scanning
    several targets at once: *"would be impossible to put correct flag for each host"* -
    because `-fs` and `-ac` derive one filter and apply it to everything. Point a scan at
    fifty hosts and you get one baseline for fifty different ideas of "not found"; point it
    at one host with `-recursion` and you get one baseline for every directory's error page.

    A Bloom filter is 2,045 floats. Keeping one per host costs 16 kB each, so the answer is
    simply to keep one per host - and per directory, if the scan recursed. The projection is
    shared across partitions, so a score means the same thing in all of them and they can be
    compared; only the learned baseline differs, which is the part that should.

    Partitions with barely any traffic are the failure case: a host seen five times has no
    baseline, and everything in it would look novel. `min_observations` holds those back
    rather than flooding the operator with the first few responses from every host.
    """

    how: str = "host"
    min_observations: int = 25
    #: Set automatically from `FALLBACK` for partitions fine enough to need one. `None`
    #: keeps the original behaviour exactly: one baseline per partition, no fallback.
    fallback_how: str | None = None
    channel_set: str = CHANNEL_SET_VERSION
    projection: str = "random"
    n_kc: int = DEFAULTS["n_kc"]
    fan_in: int = DEFAULTS["fan_in"]
    sparsity: float = DEFAULTS["sparsity"]
    learning_rate: float = DEFAULTS["learning_rate"]
    decay_halflife: float | None = None
    seed: int = 0
    time_base: str = "records"

    flyhash: FlyHash = field(init=False)
    encoders: dict[str, Encoder] = field(init=False, default_factory=dict)
    filters: dict[str, FlyBloomFilter] = field(init=False, default_factory=dict)
    counts: dict[str, int] = field(init=False, default_factory=dict)
    n_scored: int = field(init=False, default=0)

    def __post_init__(self) -> None:
        if self.how not in PARTITIONS:
            raise ValueError(
                f"unknown partition {self.how!r}; known: {', '.join(sorted(PARTITIONS))}"
            )
        if self.fallback_how is None:
            self.fallback_how = FALLBACK.get(self.how)
        if self.fallback_how == self.how:
            self.fallback_how = None
        if self.projection not in ("random", "degree-sampled"):
            raise ValueError(
                f"partitioned ranking shares one projection across every partition, so it "
                f"cannot use {self.projection!r}, which is built from a loaded circuit; use "
                f"Ranker for that path. 'random' and 'degree-sampled' are supported."
            )
        probe = Encoder(self.channel_set)
        rng = np.random.default_rng(self.seed)
        matrix = (
            degree_sampled_projection(len(probe), self.n_kc, rng=rng)
            if self.projection == "degree-sampled"
            else random_projection(len(probe), self.n_kc, self.fan_in, rng=rng)
        )
        self.flyhash = FlyHash(matrix, sparsity=self.sparsity)

    @property
    def partitions(self) -> int:
        return len(self.filters)

    def _for(self, key: str) -> tuple[Encoder, FlyBloomFilter]:
        if key not in self.filters:
            self.encoders[key] = Encoder(self.channel_set)
            self.filters[key] = FlyBloomFilter(
                self.flyhash.n_kc,
                learning_rate=self.learning_rate,
                decay_halflife=self.decay_halflife,
            )
            self.counts[key] = 0
        return self.encoders[key], self.filters[key]

    def _when(self) -> float | None:
        return time.time() if self.time_base == "wallclock" else None

    def score(self, result: FfufResult) -> Scored:
        """Score against the finest partition that has seen enough, and learn into all of them.

        With a coarse partition this is exactly what it was before. With `shape`, which is
        fine enough to produce partitions of one, the fallback is what stops a response
        alone in its bucket from being called maximally novel because nothing has ever
        depressed its cells. Both baselines always learn, so a partition that is thin now
        becomes authoritative the moment it has enough members.
        """
        key = partition_key(result, self.how)
        encoder, filt = self._for(key)
        vector = encoder.encode(result)
        tag = self.flyhash.tag_valued(vector[None, :])

        # Learn into the fine partition regardless of which one is scoring.
        fine_novelty = float(filt.observe(tag, when=self._when())[0])
        self.counts[key] += 1
        settled = self.counts[key] >= self.min_observations

        novelty, scoring_key = fine_novelty, key
        if self.fallback_how:
            coarse_key = partition_key(result, self.fallback_how)
            coarse_encoder, coarse_filt = self._for(coarse_key)
            # The coarse baseline has to see every response or it is not a host baseline,
            # and it must see it through its own encoder - see `observe_only`.
            coarse_tag = self.flyhash.tag_valued(coarse_encoder.encode(result)[None, :])
            coarse_novelty = float(coarse_filt.observe(coarse_tag, when=self._when())[0])
            self.counts[coarse_key] += 1
            if not settled:
                novelty, scoring_key = coarse_novelty, coarse_key

        scored = Scored(
            result=result,
            novelty=novelty,
            position=self.n_scored,
            channel_set=self.channel_set,
            projection=self.projection,
            partition=scoring_key,
            # A response scored against the fallback is as settled as that fallback is.
            settled=settled or self.counts.get(scoring_key, 0) >= self.min_observations,
        )
        self.n_scored += 1
        return scored

    def settled(self, key: str) -> bool:
        """Whether a partition has seen enough to have an opinion worth showing."""
        return self.counts.get(key, 0) >= self.min_observations

    def stream(self, results: Iterable[FfufResult]) -> Iterator[Scored]:
        for result in results:
            yield self.score(result)

    def observe_only(self, results: Iterable[FfufResult]) -> None:
        for result in results:
            key = partition_key(result, self.how)
            encoder, filt = self._for(key)
            tag = self.flyhash.tag_valued(encoder.encode(result)[None, :])
            filt.observe(tag, when=self._when())
            self.counts[key] += 1
            if self.fallback_how:
                # The fallback must learn from the same pass, or the second pass would score
                # thin partitions against a baseline that had seen nothing.
                #
                # Its tag is recomputed from its *own* encoder. Observing the fine
                # partition's tag into it would teach the host baseline tags derived from
                # partition-local statistics - and a response alone in its partition has an
                # encoder that has seen one record and answers "no opinion" on every
                # channel, so the host would learn a tag that means nothing and then score
                # real responses against it.
                coarse_key = partition_key(result, self.fallback_how)
                coarse_encoder, coarse_filt = self._for(coarse_key)
                coarse_tag = self.flyhash.tag_valued(coarse_encoder.encode(result)[None, :])
                coarse_filt.observe(coarse_tag, when=self._when())
                self.counts[coarse_key] += 1

    def score_against_baseline(self, result: FfufResult) -> Scored:
        key = self._scoring_key(result)
        encoder, filt = self._for(key)
        tag = self.flyhash.tag_valued(encoder.encode(result, observe=False)[None, :])
        scored = Scored(
            result=result,
            novelty=float(filt.score_excluding(tag)[0]),
            position=self.n_scored,
            channel_set=self.channel_set,
            projection=self.projection,
            partition=key,
            settled=self.counts.get(key, 0) >= self.min_observations,
        )
        self.n_scored += 1
        return scored

    def _scoring_key(self, result: FfufResult) -> str:
        """The finest partition that has seen enough to have an opinion.

        Without this, a `shape` partition holding one response scores that response against
        a baseline consisting of itself, which - leave-one-out - means nothing has depressed
        its cells and it is maximally novel by construction. `--per dir` shipped that bug
        once already, scoring 1.000 on partitions of one.
        """
        key = partition_key(result, self.how)
        if self.fallback_how and self.counts.get(key, 0) < self.min_observations:
            return partition_key(result, self.fallback_how)
        return key

    @property
    def saturation(self) -> float:
        """Mean saturation across partitions, for the footer."""
        if not self.filters:
            return 0.0
        return sum(f.saturation for f in self.filters.values()) / len(self.filters)

    def score_only(self, result: FfufResult) -> Scored:
        """Score against the stored baseline: no learning, no leave-one-out. See `Ranker`."""
        key = self._scoring_key(result)
        encoder, filt = self._for(key)
        tag = self.flyhash.tag_valued(encoder.encode(result)[None, :])
        scored = Scored(
            result=result,
            novelty=float(filt.score(tag, when=self._when())[0]),
            position=self.n_scored,
            channel_set=self.channel_set,
            projection=self.projection,
            partition=key,
            settled=self.counts.get(key, 0) >= self.min_observations,
        )
        self.n_scored += 1
        return scored

    def summary(self) -> str:
        settled = sum(1 for k in self.filters if self.settled(k))
        return (
            f"{self.partitions} {self.how} partition(s), {settled} with at least "
            f"{self.min_observations} responses"
        )


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

    how = kwargs.pop("partition", "none")
    build = (
        (lambda: PartitionedRanker(how=how, **kwargs))
        if how != "none"
        else (lambda: Ranker(**kwargs))
    )

    if passes == 1:
        ranker = build()
        scored = list(ranker.stream(results))
    else:
        results = list(results)
        ranker = build()
        ranker.observe_only(results)
        # A fresh encoder would re-derive running statistics from scratch; reuse the settled
        # one so the second pass encodes against the whole run, as it scores against it.
        scored = [ranker.score_against_baseline(r) for r in results]

    # Unsettled partitions sort last however novel they look: a partition of three
    # responses has no baseline, and its scores are an artifact of that rather than a
    # statement about the target.
    scored.sort(key=lambda s: (not s.settled, -s.novelty, s.position))
    return scored
