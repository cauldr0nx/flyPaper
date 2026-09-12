"""The projections themselves, independent of any ranking built on them."""

import numpy as np

from flypaper.brain.flyhash import (
    claw_degrees,
    degree_sampled_projection,
    random_projection,
)


def test_claw_degrees_match_the_shipped_histogram():
    """The histogram is measured data reduced to 13 numbers; it must not drift silently."""
    degrees = claw_degrees()
    assert len(degrees) == 1891, "one entry per Kenyon cell that has any claws"
    assert degrees.min() == 1 and degrees.max() == 29
    assert 5.3 < degrees.mean() < 5.5, "measured mean claw count"


def test_degree_sampled_projection_spreads_fan_in():
    """The point of it is the spread: a uniform projection has none, this one must."""
    rng = np.random.default_rng(0)
    sampled = degree_sampled_projection(39, 2045, rng=rng)
    uniform = random_projection(39, 2045, 6, rng=np.random.default_rng(0))
    spread = np.asarray((sampled > 0).sum(axis=0)).ravel()
    flat = np.asarray((uniform > 0).sum(axis=0)).ravel()
    assert flat.std() == 0.0, "the published baseline gives every cell the same fan-in"
    assert spread.std() > 1.5, "the measured one does not"
    assert spread.min() >= 1, "a cell listening to nothing can never fire"


def test_degree_sampled_projection_cannot_exceed_the_channels_it_has():
    """The histogram runs to 29 claws; a narrow encoder has fewer channels than that."""
    matrix = degree_sampled_projection(12, 500, rng=np.random.default_rng(3))
    assert np.asarray((matrix > 0).sum(axis=0)).ravel().max() <= 12
