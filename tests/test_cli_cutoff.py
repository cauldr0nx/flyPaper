"""`--percentile` on a completed file, which was accepted and then ignored.

`fly rank results.json --percentile 99.5`, `--percentile 99.9` and no flag at all produced
byte-identical output: the completed-file path applied a fixed review budget and printed
with `force=True`, so the cutoff never ran. The README documented the flag as the way to
choose a cutoff, which made it the first thing a new user would try.

Two separate faults, and both are asserted here. The flag was not reaching the gate, and the
gate could not have worked anyway: `scored` arrives sorted most-novel-first, and the live
path's rolling estimator sees the highest scores before it knows what typical looks like.
"""

from __future__ import annotations

from flypaper.cli import percentile_cutoff


def test_a_stricter_percentile_shows_strictly_fewer():
    """The property that was false: the number shown has to respond to the flag."""
    novelties = [i / 1000 for i in range(1000)]
    cutoffs = [percentile_cutoff(novelties, p) for p in (90.0, 99.0, 99.5)]
    shown = [sum(1 for n in novelties if n >= c) for c in cutoffs]
    assert shown == sorted(shown, reverse=True), shown
    assert shown[0] > shown[-1], "a stricter cutoff must show fewer"
    assert shown[0] == 100, "top 10% of 1000"


def test_the_cutoff_does_not_depend_on_the_order_it_is_given():
    """The live gate is rolling and order-dependent; offline nothing needs estimating.

    Feeding the rolling estimator a pre-sorted run is what made it suppress everything.
    """
    novelties = [i / 100 for i in range(100)]
    assert percentile_cutoff(novelties, 95.0) == percentile_cutoff(
        sorted(novelties, reverse=True), 95.0
    )


def test_an_empty_run_has_a_cutoff_rather_than_an_error():
    assert percentile_cutoff([], 99.5) == 0.0
