"""The Fly Bloom Filter.

Dasgupta, Sheehan, Stevens & Navlakha, *PNAS* 115(51):13093, 2018. The Kenyon cell ->
MBON-alpha'3 synapses act as a Bloom filter over everything the fly has encountered. Each
Kenyon cell holds one synaptic weight onto the readout neuron; an odour that fires a cell
**depresses** that cell's weight, so a familiar odour drives the readout weakly and a novel
one drives it strongly. Novelty is the readout's response.

Two properties distinguish it from a conventional Bloom filter, and both are the reason it
was chosen here over a hash set:

**It grades by similarity.** The tag is a locality-sensitive hash, so an input near a
familiar one shares most of its active cells and inherits most of their depression. A
response whose only difference from the baseline is a rotating CSRF token lands on nearly
the same cells and stays quiet - without anyone having written a rule about tokens.

**It grades by time.** Depressed weights recover toward their resting value, so a baseline
ages on its own. A target scanned six months ago becomes gradually novel again. A hash set
can never do this; it only knows *seen* and *not seen*.

Both are single-pass and unsupervised: no labels, no training corpus, no second look.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

__all__ = ["FlyBloomFilter"]


@dataclass
class FlyBloomFilter:
    """Synaptic weights from Kenyon cells onto the novelty readout.

    Weights start at `w_rest` and are depressed toward zero by activity. Novelty is the
    weighted sum over the active cells, normalised so that a wholly unfamiliar input scores
    near 1 and a wholly familiar one near 0.

    `decay_halflife` is in the same units as the timestamps passed to `observe`/`score`. It
    is the time for a fully depressed weight to recover half way back to rest. `None`
    disables temporal decay, which reproduces the distance-sensitive-only filter from the
    paper.
    """

    n_kc: int
    w_rest: float = 1.0
    #: How much a single encounter depresses an active synapse, as a fraction of its
    #: current weight. The paper's depression is proportional to activation; this is the
    #: same rule with the activation folded into `observe`.
    learning_rate: float = 0.4
    decay_halflife: float | None = None
    eps: float = 1e-9

    weights: np.ndarray = field(init=False)
    #: When each synapse was last depressed, for the temporal term.
    last_seen: np.ndarray = field(init=False)
    n_observed: int = field(init=False, default=0)
    _clock: float = field(init=False, default=0.0)

    def __post_init__(self) -> None:
        self.weights = np.full(self.n_kc, float(self.w_rest), dtype=np.float64)
        self.last_seen = np.zeros(self.n_kc, dtype=np.float64)

    # --- time ---------------------------------------------------------------------------

    def _now(self, when: float | None) -> float:
        if when is None:
            self._clock += 1.0
            return self._clock
        self._clock = max(self._clock, float(when))
        return float(when)

    def _decayed(self, when: float) -> np.ndarray:
        """Weights recovered toward rest by however long it has been since each was touched.

        Recovery is exponential toward `w_rest`, which is what "time since last encounter"
        means mechanically: a synapse that was depressed long ago has mostly forgotten.
        """
        if self.decay_halflife is None:
            return self.weights
        elapsed = np.maximum(when - self.last_seen, 0.0)
        recovered = 1.0 - np.exp2(-elapsed / self.decay_halflife)
        return self.weights + (self.w_rest - self.weights) * recovered

    # --- the filter ---------------------------------------------------------------------

    def score(self, tag: np.ndarray, *, when: float | None = None) -> np.ndarray:
        """Novelty in [0, 1] for each row of `tag`. Does not learn; `observe` does that.

        `tag` may be binary or carry the winners' activation values; values are used as
        weights on the readout, which is what the biology does.
        """
        tag = np.atleast_2d(np.asarray(tag, dtype=np.float64))
        weights = self._decayed(when if when is not None else self._clock)
        driven = tag @ weights
        # Normalise by what a completely unfamiliar tag of the same shape would drive, so
        # the score does not depend on how many cells happen to be active.
        ceiling = tag.sum(axis=1) * self.w_rest
        return np.clip(driven / np.maximum(ceiling, self.eps), 0.0, 1.0)

    def score_excluding(self, tag: np.ndarray, *, when: float | None = None) -> np.ndarray:
        """Novelty for a tag that is already written into the filter, as if it were not.

        Leave-one-out, and exact rather than approximate: `_depress` multiplies each active
        weight by `1 - learning_rate * strength`, and multiplication commutes, so dividing
        that same factor back out recovers the weight the cell would have had if this one
        encounter had never happened - whatever order the encounters came in.

        This is what makes offline scores comparable to live ones. Without it, scoring a
        completed run in two passes compresses every score, because each record has been
        depressed once by itself before being scored.

        Exact only while `decay_halflife` is None. With decay the recovery between then and
        now is not inverted, so the result is an approximation and is documented as one.
        """
        tag = np.atleast_2d(np.asarray(tag, dtype=np.float64))
        now = when if when is not None else self._clock
        weights = self._decayed(now)
        out = np.empty(len(tag), dtype=np.float64)
        for i, row in enumerate(tag):
            active = row > 0
            restored = weights
            if active.any():
                strength = row[active]
                strength = strength / max(strength.max(), self.eps)
                factor = 1.0 - self.learning_rate * strength
                restored = weights.copy()
                restored[active] /= np.maximum(factor, self.eps)
                restored = np.minimum(restored, self.w_rest)
            driven = float(row @ restored)
            ceiling = float(row.sum()) * self.w_rest
            out[i] = min(max(driven / max(ceiling, self.eps), 0.0), 1.0)
        return out

    def observe(self, tag: np.ndarray, *, when: float | None = None) -> np.ndarray:
        """Score, then learn. One pass, in that order - a record is never scored against
        knowledge of itself."""
        tag = np.atleast_2d(np.asarray(tag, dtype=np.float64))
        out = np.empty(len(tag), dtype=np.float64)
        for i, row in enumerate(tag):
            now = self._now(when)
            out[i] = self.score(row[None, :], when=now)[0]
            self._depress(row, now)
            self.n_observed += 1
        return out

    def _depress(self, row: np.ndarray, now: float) -> None:
        active = row > 0
        if not active.any():
            return
        # Recover first, so a long gap is reflected before this encounter is written in.
        self.weights = self._decayed(now)
        strength = row[active]
        strength = strength / max(strength.max(), self.eps)
        self.weights[active] *= 1.0 - self.learning_rate * strength
        self.last_seen[active] = now

    # --- inspection ----------------------------------------------------------------------

    @property
    def saturation(self) -> float:
        """How much of the filter has been written into. A filter near 1 has forgotten how
        to be surprised, which is the classic Bloom filter failure and worth watching."""
        return float(1.0 - self.weights.mean() / max(self.w_rest, self.eps))
