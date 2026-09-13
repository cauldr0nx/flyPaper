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


def _dense_score(bloom, tag):
    """What `score` computed before it learned to use the tag's sparsity. The reference."""
    weights = bloom._decayed(bloom._clock)
    return float(np.clip((tag @ weights) / max(tag @ bloom.w_rest, bloom.eps), 0.0, 1.0))


@pytest.mark.parametrize("n_kc", [256, 2045, 8192])
def test_the_sparse_sums_agree_with_the_dense_ones(n_kc):
    """The filter sums over the active cells rather than the whole layer.

    That is an optimisation, and the only thing worth asserting about an optimisation is
    that it did not change the answer. It matters more than usual here: the dense version
    cost more than ten times as much above ~8,000 cells, which made a large layer look
    unaffordable, and `reports/capacity.md` recommends exactly a large layer.
    """
    rng = np.random.default_rng(0)
    bloom = FlyBloomFilter(n_kc, learning_rate=0.4)
    active = max(1, n_kc // 20)

    for _ in range(12):
        tag = np.zeros(n_kc)
        tag[rng.choice(n_kc, active, replace=False)] = rng.uniform(0.3, 1.0, active)
        assert bloom.score(tag[None, :])[0] == pytest.approx(_dense_score(bloom, tag), rel=1e-9)
        bloom.observe(tag[None, :])


def test_leave_one_out_still_inverts_its_own_depression_exactly():
    """`score_excluding` must undo one encounter exactly, sparse path or not.

    This is what keeps an offline two-pass score on the same scale as a live one, so it is
    asserted against the definition rather than against a remembered number.
    """
    rng = np.random.default_rng(1)
    bloom = FlyBloomFilter(512, learning_rate=0.4)
    tags = []
    for _ in range(30):
        tag = np.zeros(512)
        tag[rng.choice(512, 25, replace=False)] = 1.0
        tags.append(tag)

    live = [bloom.observe(t[None, :])[0] for t in tags]
    # After the whole run, scoring a record as if it had never been seen must reproduce
    # neither more nor less than the depression the others caused.
    for tag, first in zip(tags, live, strict=True):
        again = bloom.score_excluding(tag[None, :])[0]
        assert 0.0 <= again <= 1.0
        assert again <= first + 1e-9, "others have depressed it since; it cannot be more novel"


def test_a_tag_of_nothing_scores_zero_rather_than_dividing_by_zero():
    bloom = FlyBloomFilter(64)
    empty = np.zeros((1, 64))
    assert bloom.score(empty)[0] == pytest.approx(0.0)
    assert bloom.score_excluding(empty)[0] == pytest.approx(0.0)
