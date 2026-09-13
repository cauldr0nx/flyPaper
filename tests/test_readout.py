"""The Bloom filter's resting weights, which the published model assumes are all equal.

`w_rest` was a scalar for the life of this project, which encodes the assumption that every
Kenyon cell reaches the novelty readout equally. The measured circuit says 345 of 2,045 do.
These cover the arithmetic that makes a per-cell weight mean the same thing as the scalar it
replaces, so that `reports/readout.md` is comparing filters rather than scales.
"""

from __future__ import annotations

import numpy as np
import pytest

from flypaper.brain.bloom import FlyBloomFilter


def test_a_scalar_and_a_flat_array_are_the_same_filter():
    """The refactor's contract: making w_rest a vector must not move the published model."""
    tag = np.zeros(64)
    tag[[1, 5, 9, 20]] = 1.0

    scalar = FlyBloomFilter(64, w_rest=1.0)
    array = FlyBloomFilter(64, w_rest=np.ones(64))
    for _ in range(5):
        assert scalar.observe(tag[None, :])[0] == pytest.approx(array.observe(tag[None, :])[0])
    assert scalar.saturation == pytest.approx(array.saturation)


def test_an_unread_cell_contributes_nothing_to_novelty():
    """A weight of zero is what "does not synapse onto the readout" has to mean."""
    w = np.zeros(32)
    w[:8] = 1.0
    bloom = FlyBloomFilter(32, w_rest=w)

    read = np.zeros(32)
    read[[0, 1, 2]] = 1.0
    unread = np.zeros(32)
    unread[[20, 21, 22]] = 1.0

    # A tag that lands entirely outside the readout drives nothing, so it cannot be novel.
    assert bloom.score(unread[None, :])[0] == pytest.approx(0.0)
    assert bloom.score(read[None, :])[0] == pytest.approx(1.0)


def test_novelty_still_starts_at_one_when_the_weights_are_uneven():
    """Uneven resting weights must not make an unfamiliar input look partly familiar.

    The ceiling is the drive an unfamiliar tag of this shape would produce *through these
    weights*, so it is a dot product rather than a count times a constant. Getting that
    wrong would make every score on a weighted readout look lower than it is.
    """
    rng = np.random.default_rng(0)
    w = rng.uniform(0.2, 3.0, size=48)
    bloom = FlyBloomFilter(48, w_rest=w)
    tag = np.zeros(48)
    tag[[3, 11, 19, 27]] = 1.0
    assert bloom.score(tag[None, :])[0] == pytest.approx(1.0)


def test_a_wrongly_sized_readout_is_refused():
    with pytest.raises(ValueError, match="weights for"):
        FlyBloomFilter(32, w_rest=np.ones(31))


@pytest.mark.data
def test_the_measured_readout_reads_a_minority_of_the_layer():
    """The fact the whole report rests on, asserted against the circuit rather than quoted."""
    from flypaper import REPO_ROOT
    from flypaper.brain.extract import Circuit, readout_weights

    path = REPO_ROOT / "data" / "derived" / "olfactory-circuit-R"
    if not path.with_suffix(".json").exists():
        pytest.skip("no extracted circuit")
    circuit = Circuit.load(path)

    weighted = readout_weights(circuit, mode="weighted")
    masked = readout_weights(circuit, mode="mask")
    uniform = readout_weights(circuit, mode="uniform")

    assert (uniform > 0).all(), "the published model reads every cell"
    assert 0 < (masked > 0).sum() < 0.5 * len(masked), "the measured one reads a minority"
    assert np.array_equal(masked > 0, weighted > 0), "same cells, different strengths"
    # Mean-normalised over the connected cells, so the three are on one scale and a novelty
    # score means the same thing under each.
    for w in (weighted, masked, uniform):
        assert w[w > 0].mean() == pytest.approx(1.0)
