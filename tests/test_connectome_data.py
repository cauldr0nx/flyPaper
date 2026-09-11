"""Loaders against the real MaleCNS tables.

Marked `data` and deselected by default, so the everyday suite stays offline and
dataset-free. Run with the tables present:

    FLYPAPER_RAW_DIR=/path/to/raw pytest -m data
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.data


def test_annotations_load_and_carry_the_expected_columns():
    from flypaper.brain import load
    from flypaper.brain import schema as S

    ann = load.load_annotations([S.A_BODY, S.A_TYPE, S.A_SUPERCLASS, S.A_STATUS])
    assert len(ann) > 100_000
    assert set(ann.columns) == {S.A_BODY, S.A_TYPE, S.A_SUPERCLASS, S.A_STATUS}


def test_neuron_count_is_reported_not_asserted():
    """The published headline is ~166,691 neurons. The definitions disagree by ~2,000
    bodies, and that disagreement is a result to report, not a bug to paper over."""
    from flypaper.brain import load

    counts = load.count_definitions()
    assert counts["has an assigned superclass"] > 100_000
    # Every definition is a defensible answer; none is silently privileged.
    assert len(counts) >= 6


def test_traced_weights_table_loads():
    from flypaper.brain import load
    from flypaper.brain import schema as S

    weights = load.load_weights([S.W_PRE, S.W_POST, S.W_WEIGHT], traced_only=True)
    assert len(weights) > 1_000_000
    assert (weights[S.W_WEIGHT] > 0).all()


def test_missing_table_names_the_fix():
    """A missing table must say how to get it, not raise a bare path error."""
    from flypaper.brain import schema as S

    with pytest.raises(FileNotFoundError, match="data/fetch.py"):
        S.raw_path("no-such-table.feather")
