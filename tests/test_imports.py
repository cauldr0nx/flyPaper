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


def test_unbuilt_milestones_raise_rather_than_return_garbage():
    """A stub must fail loudly. Silently returning nothing is how bad results get shipped."""
    from flypaper.brain import bloom, extract, flyhash
    from flypaper.rank import report, score
    from flypaper.stage2 import body_features, refetch
    from flypaper.store import db

    for call in (
        extract.extract,
        flyhash.RandomProjectionFlyHash,
        flyhash.ConnectomeFlyHash,
        bloom.FlyBloomFilter,
        score.score,
        report.render,
        refetch.refetch,
        body_features.body_features,
        db.open_db,
    ):
        with pytest.raises(NotImplementedError):
            call()


def test_the_encoder_is_built():
    """M2 is done; these must no longer be stubs."""
    from flypaper.encode import CHANNEL_SETS, channel_names, n_channels

    assert len(CHANNEL_SETS) >= 2
    assert n_channels() > 20
    assert all(isinstance(name, str) for name in channel_names())
