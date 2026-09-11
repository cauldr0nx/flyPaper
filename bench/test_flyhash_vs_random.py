"""M3: FlyHash and the Fly Bloom Filter, and the connectome comparison.

The benchmark itself lives in `bench/benchmark_flyhash.py` and writes
`reports/m3-flyhash-benchmark.md`. These tests assert the mechanics the benchmark rests on,
so a change that quietly breaks the hash cannot pass as a change in the result.

Tests needing the MaleCNS tables are marked `data` and deselected by default.
"""

from __future__ import annotations

import numpy as np
import pytest

from flypaper.brain.bloom import FlyBloomFilter
from flypaper.brain.flyhash import (
    FlyHash,
    connectome_projection,
    random_matched_projection,
    random_projection,
)


def a_hash(seed: int = 0, n_channels: int = 55, n_kc: int = 2045) -> FlyHash:
    return FlyHash(random_projection(n_channels, n_kc, 6, rng=np.random.default_rng(seed)))


# --- FlyHash ---------------------------------------------------------------------------------


def test_tag_is_sparse_at_the_published_level():
    """~95% zeros: the sparsity the Science 2017 paper reports."""
    fh = a_hash()
    X = np.random.default_rng(1).random((50, 55)).astype(np.float32)
    assert fh.measured_sparsity(X) == pytest.approx(0.95, abs=0.01)
    assert (fh.tag(X).sum(axis=1) == fh.n_active).all()


def test_tag_expands_dimensionality():
    """FlyHash is expansive - unlike a classical LSH, which reduces."""
    fh = a_hash()
    assert fh.n_kc > fh.n_channels * 10


def test_similar_inputs_get_similar_tags():
    """The locality-sensitive property. Without it nothing downstream works."""
    rng = np.random.default_rng(2)
    fh = a_hash()
    base = rng.random(55).astype(np.float32)
    near = base + rng.normal(0, 0.01, (30, 55)).astype(np.float32)
    far = rng.random((30, 55)).astype(np.float32)

    base_tag = fh.tag(base)[0]
    near_overlap = (fh.tag(near) @ base_tag).mean()
    far_overlap = (fh.tag(far) @ base_tag).mean()
    assert near_overlap > 5 * far_overlap, f"near={near_overlap:.1f} far={far_overlap:.1f}"


def test_tag_is_invariant_to_input_gain():
    """Divisive normalisation: a louder version of the same odour is the same odour."""
    rng = np.random.default_rng(3)
    fh = a_hash()
    x = rng.random((5, 55)).astype(np.float32)
    assert (fh.tag(x) == fh.tag(x * 3.0 + 0.0)).all()


def test_random_matched_preserves_the_measured_fan_in():
    """The degree-preserving null must actually preserve degree, or it controls nothing."""
    rng = np.random.default_rng(4)
    measured = random_projection(55, 300, 6, rng=rng)
    measured = measured.multiply(rng.random(measured.shape) * 10).tocsr()
    matched = random_matched_projection(measured, rng=rng)
    got = np.asarray((matched > 0).sum(axis=0)).ravel()
    want = np.asarray((measured > 0).sum(axis=0)).ravel()
    assert (got == want).all()
    assert set(matched.data) == {1.0}, "the null must not inherit weights, only degree"


def test_connectome_projection_binary_drops_weights_but_not_wiring():
    rng = np.random.default_rng(5)
    measured = random_projection(55, 100, 6, rng=rng).multiply(7.0).tocsr()
    binary = connectome_projection(measured, binary=True)
    assert set(binary.data) == {1.0}
    assert (binary > 0).nnz == (measured > 0).nnz


# --- the Fly Bloom Filter ----------------------------------------------------------------------


def test_familiarity_accumulates_and_novelty_survives_it():
    """Repetition must make a thing familiar without making everything familiar."""
    rng = np.random.default_rng(6)
    fh = a_hash()
    base = rng.random(55).astype(np.float32)
    familiar = base + rng.normal(0, 0.01, (200, 55)).astype(np.float32)

    filt = FlyBloomFilter(fh.n_kc)
    scores = filt.observe(fh.tag_valued(familiar))
    assert scores[0] > 0.9, "the first sighting of anything must be novel"
    assert scores[-1] < 0.2, "the two-hundredth must not be"
    assert filt.score(fh.tag_valued(rng.random((1, 55)).astype(np.float32)))[0] > 0.8


def test_novelty_grades_with_similarity_not_just_identity():
    """The property a hash set cannot have: near misses score between seen and unseen."""
    rng = np.random.default_rng(7)
    fh = a_hash()
    base = rng.random(55).astype(np.float32)

    filt = FlyBloomFilter(fh.n_kc)
    filt.observe(fh.tag_valued(base + rng.normal(0, 0.005, (150, 55)).astype(np.float32)))

    def score_at(noise: float) -> float:
        probe = base + rng.normal(0, noise, (40, 55)).astype(np.float32)
        return float(filt.score(fh.tag_valued(probe)).mean())

    near, middle, far = score_at(0.005), score_at(0.15), score_at(2.0)
    assert near < middle < far, f"{near:.3f} {middle:.3f} {far:.3f}"


def test_temporal_decay_makes_an_old_baseline_novel_again():
    """A target scanned six months ago should become novel again on its own."""
    rng = np.random.default_rng(8)
    fh = a_hash()
    base = rng.random(55).astype(np.float32)

    filt = FlyBloomFilter(fh.n_kc, decay_halflife=50.0)
    filt.observe(fh.tag_valued(base + rng.normal(0, 0.005, (150, 55)).astype(np.float32)))
    tag = fh.tag_valued(base[None, :])

    fresh = filt.score(tag, when=filt._clock)[0]
    later = filt.score(tag, when=filt._clock + 50)[0]
    much_later = filt.score(tag, when=filt._clock + 500)[0]
    assert fresh < 0.2
    assert fresh < later < much_later
    assert much_later > 0.9


def test_without_decay_a_baseline_does_not_age():
    rng = np.random.default_rng(9)
    fh = a_hash()
    filt = FlyBloomFilter(fh.n_kc, decay_halflife=None)
    base = rng.random(55).astype(np.float32)
    filt.observe(fh.tag_valued(base + rng.normal(0, 0.005, (150, 55)).astype(np.float32)))
    tag = fh.tag_valued(base[None, :])
    assert filt.score(tag, when=filt._clock)[0] == pytest.approx(
        filt.score(tag, when=filt._clock + 10_000)[0]
    )


def test_scoring_happens_before_learning():
    """A record must never be scored against knowledge of itself."""
    rng = np.random.default_rng(10)
    fh = a_hash()
    x = fh.tag_valued(rng.random((1, 55)).astype(np.float32))
    filt = FlyBloomFilter(fh.n_kc)
    assert filt.observe(x)[0] > 0.99
    assert filt.observe(x)[0] < 0.99


def test_saturation_is_visible():
    """A filter that has forgotten how to be surprised should say so."""
    rng = np.random.default_rng(11)
    fh = a_hash()
    filt = FlyBloomFilter(fh.n_kc)
    assert filt.saturation == pytest.approx(0.0)
    filt.observe(fh.tag_valued(rng.random((500, 55)).astype(np.float32)))
    assert filt.saturation > 0.1


# --- the measured circuit ------------------------------------------------------------------------


@pytest.mark.data
def test_measured_circuit_matches_the_published_orders_of_magnitude():
    """Differences from the published figures are results; wild differences are bugs."""
    from flypaper.brain.extract import extract

    circuit = extract("R")
    stats = circuit.stats
    assert 40 <= stats["glomeruli"] <= 70, stats["glomeruli"]
    assert 1500 <= stats["kc_bodies"] <= 3000, stats["kc_bodies"]
    assert 3 <= stats["fan_in_mean"] <= 10, stats["fan_in_mean"]
    assert stats["mbon_alpha3_bodies"] == 2, stats["mbon_alpha3_instances"]


@pytest.mark.data
def test_alpha3_is_identified_from_the_instance_not_a_remembered_type_number():
    from flypaper.brain.extract import resolve_populations

    pops = resolve_populations("R")
    instances = sorted(pops.mbon_alpha3["instance"].dropna())
    assert len(instances) == 2
    assert all("a'3" in i for i in instances)
    # The partially-overlapping cells must be reported, not folded in.
    assert len(pops.mbon_alpha3_adjacent) >= 1
    assert set(instances).isdisjoint(set(pops.mbon_alpha3_adjacent["instance"].dropna()))
