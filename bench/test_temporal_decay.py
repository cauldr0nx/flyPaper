"""M3: the Fly Bloom Filter's time-sensitivity, benchmarked rather than asserted.

`bench/benchmark_decay.py` measures whether decay is worth having; these tests pin the
properties that benchmark relies on, so a change that breaks decay cannot pass as a change
in the result.

The mechanism tests live in `bench/test_flyhash_vs_random.py`. What is here is the thing
the benchmark actually claims: a filter that never forgets loses sensitivity as it fills,
and one that forgets keeps it.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from flypaper.brain.bloom import FlyBloomFilter  # noqa: E402
from flypaper.brain.flyhash import FlyHash, random_projection  # noqa: E402


def a_hash(seed: int = 0, n_in: int = 40, n_kc: int = 2045) -> FlyHash:
    return FlyHash(random_projection(n_in, n_kc, 6, rng=np.random.default_rng(seed)))


def stream(fh: FlyHash, filt: FlyBloomFilter, n: int, seed: int = 1, cycle: int = 40) -> None:
    """Feed repetitive traffic: `cycle` distinct responses, over and over."""
    rng = np.random.default_rng(seed)
    population = rng.random((cycle, fh.n_channels)).astype(np.float32)
    for i in range(n):
        filt.observe(fh.tag_valued(population[i % cycle][None, :]))


def novel_probe(fh: FlyHash, filt: FlyBloomFilter, seed: int = 99) -> float:
    rng = np.random.default_rng(seed)
    x = rng.random(fh.n_channels).astype(np.float32) * 8.0
    return float(filt.score(fh.tag_valued(x[None, :]))[0])


def test_a_filter_that_never_forgets_loses_sensitivity_as_it_fills():
    """The classic Bloom filter failure, which is what decay exists to prevent."""
    fh = a_hash()
    filt = FlyBloomFilter(fh.n_kc, decay_halflife=None)
    fresh = novel_probe(fh, filt)
    stream(fh, filt, 6000)
    worn = novel_probe(fh, filt)
    assert fresh > 0.95
    assert worn < fresh * 0.9, f"expected sensitivity to fall: {fresh:.3f} -> {worn:.3f}"


def test_decay_keeps_more_of_that_sensitivity():
    fh = a_hash()
    without = FlyBloomFilter(fh.n_kc, decay_halflife=None)
    with_decay = FlyBloomFilter(fh.n_kc, decay_halflife=1000.0)
    stream(fh, without, 6000)
    stream(fh, with_decay, 6000)
    assert novel_probe(fh, with_decay) > novel_probe(fh, without)


def margin_after(n_types: int, halflife: float | None, steps: int = 6000, seed: int = 0):
    """Novel minus familiar, after streaming `n_types` responses round-robin."""
    fh = a_hash(seed)
    rng = np.random.default_rng(seed + 1)
    population = rng.random((n_types, fh.n_channels)).astype(np.float32)
    filt = FlyBloomFilter(fh.n_kc, decay_halflife=halflife)
    for i in range(steps):
        filt.observe(fh.tag_valued(population[i % n_types][None, :]))
    familiar = float(filt.score(fh.tag_valued(population[0][None, :]))[0])
    return novel_probe(fh, filt, seed=99) - familiar


def test_too_short_a_halflife_forgets_the_baseline_and_the_margin_collapses():
    """The check that stops "shorter is always better", and it fires.

    A filter that forgets everything scores everything as novel, which looks like
    sensitivity until you also measure what it says about a response it has been reading all
    along. Recover faster than the stream repeats itself and the margin collapses - at 40
    response types and a half-life of 500 it goes negative, meaning the filter calls the
    familiar response *more* novel than the unseen one.
    """
    assert margin_after(40, 500.0) < margin_after(40, None)


def test_the_useful_halflife_depends_on_how_often_the_stream_repeats():
    """The relationship reports/decay.md measures: the optimum moves with diversity.

    With a handful of response types nothing is at risk of being forgotten and decay costs a
    little for nothing. With more of them the gaps between visits grow and decay starts
    paying. There is no half-life that is right for both.
    """
    few = {hl: margin_after(4, hl) for hl in (None, 1000.0)}
    many = {hl: margin_after(200, hl) for hl in (None, 1000.0)}

    assert few[None] >= few[1000.0], "with 4 types, decay should not help"
    assert many[1000.0] > many[None], "with 200 types, decay should help"


def test_the_filter_has_a_capacity_and_runs_out():
    """Measured, not assumed: 2,045 cells firing 102 at a time cannot hold 1,000 patterns.

    This does not bite on the traffic flypaper is for - the premise is that fuzzing produces
    very few distinct response shapes - but it is a real bound and nothing had measured it.
    """
    roomy = margin_after(4, None)
    crowded = margin_after(1000, None)
    assert roomy > 0.5
    assert crowded < 0.05, f"expected saturation at 1,000 types, got margin {crowded:.3f}"


def test_saturation_reports_the_filling_up():
    fh = a_hash()
    filt = FlyBloomFilter(fh.n_kc, decay_halflife=None)
    assert filt.saturation == pytest.approx(0.0)
    stream(fh, filt, 4000)
    assert filt.saturation > 0.05


def test_a_shorter_halflife_forgets_faster():
    fh = a_hash()
    scores = {}
    for halflife in (500.0, 4000.0):
        filt = FlyBloomFilter(fh.n_kc, decay_halflife=halflife)
        stream(fh, filt, 3000)
        tag = fh.tag_valued(np.random.default_rng(1).random((1, fh.n_channels)).astype(np.float32))
        scores[halflife] = float(filt.score(tag, when=filt._clock + 2000)[0])
    assert scores[500.0] > scores[4000.0]
