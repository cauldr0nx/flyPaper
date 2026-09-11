"""Every module imports. A stub that cannot even be imported is not a placeholder."""

from __future__ import annotations

import importlib
import pkgutil

import pytest

import flypaper

MODULES = sorted(m.name for m in pkgutil.walk_packages(flypaper.__path__, prefix="flypaper."))


@pytest.mark.parametrize("name", MODULES)
def test_module_imports(name):
    importlib.import_module(name)


def test_every_subpackage_is_present():
    """The layout is part of the spec; a missing package should fail loudly."""
    for expected in (
        "flypaper.ingest.ffuf",
        "flypaper.ingest.stream",
        "flypaper.encode.channels",
        "flypaper.encode.features",
        "flypaper.brain.schema",
        "flypaper.brain.load",
        "flypaper.brain.extract",
        "flypaper.brain.flyhash",
        "flypaper.brain.bloom",
        "flypaper.rank.score",
        "flypaper.rank.report",
        "flypaper.stage2.refetch",
        "flypaper.stage2.body_features",
        "flypaper.store.db",
    ):
        assert expected in MODULES, f"missing module: {expected}"


def test_nothing_is_a_stub_any_more():
    """M0-M6 are built. If a stub reappears, it should be a deliberate, visible decision."""
    import inspect

    import flypaper

    for name in MODULES:
        module = importlib.import_module(name)
        source = inspect.getsource(module)
        assert "NotImplementedError" not in source, f"{name} still contains a stub"
    assert flypaper.__version__


def test_the_brain_is_built():
    """M3 is done; the circuit, the hash and the filter are real."""
    import numpy as np

    from flypaper.brain.bloom import FlyBloomFilter
    from flypaper.brain.flyhash import FlyHash, random_projection

    fh = FlyHash(random_projection(55, 2045, 6, rng=np.random.default_rng(0)))
    tag = fh.tag(np.random.default_rng(1).random((2, 55)).astype(np.float32))
    assert tag.shape == (2, 2045)
    assert FlyBloomFilter(2045).observe(tag)[0] > 0.9


def test_the_encoder_is_built():
    """M2 is done; these must no longer be stubs."""
    from flypaper.encode import CHANNEL_SETS, channel_names, n_channels

    assert len(CHANNEL_SETS) >= 2
    assert n_channels() > 20
    assert all(isinstance(name, str) for name in channel_names())


def test_stage_two_is_built_and_refuses_by_default():
    """M5 is done. Its defining property is that it refuses without an explicit scope."""
    import pytest

    from flypaper.stage2.refetch import MAX_RATE, refetch
    from flypaper.stage2.scope import Scope

    assert MAX_RATE <= 10, "the stage-two rate ceiling must stay conservative"
    with pytest.raises(ValueError, match="explicit scope"):
        list(refetch([], Scope()))
