"""The live probe's guard rails.

The probe is the only thing in this repository that sends traffic to someone else's
machine, so the tests are about what stops it rather than what it does.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bench"))

from live_probe import (  # noqa: E402
    BASELINE,
    MAX_RATE,
    MAX_REQUESTS,
    MAX_THREADS,
    WINDOW,
    Blocked,
    BlockWatch,
)


def feed(watch: BlockWatch, statuses: list[int]) -> None:
    for status in statuses:
        watch.observe(status)


def test_ceilings_stay_conservative():
    """Both axes, because the last time only rate was capped it was not enough."""
    assert MAX_RATE <= 10
    assert MAX_REQUESTS <= 250
    assert MAX_THREADS <= 4


def test_a_normal_scan_does_not_trip_the_guard():
    watch = BlockWatch()
    feed(watch, [404] * (BASELINE + WINDOW * 4))
    feed(watch, [200, 404, 301, 404, 200])


def test_a_target_that_starts_refusing_aborts_the_scan():
    watch = BlockWatch()
    feed(watch, [404] * BASELINE)
    with pytest.raises(Blocked, match="started refusing"):
        feed(watch, [403] * WINDOW)


def test_rate_limiting_mid_scan_aborts_too():
    watch = BlockWatch()
    feed(watch, [200] * BASELINE)
    with pytest.raises(Blocked):
        feed(watch, [429] * WINDOW)


def test_a_target_that_refused_from_the_start_is_not_a_block():
    """A host that answers 403 to everything is a host that answers 403. That is a result
    for the ranker, not a reason to abort - and aborting would hide it."""
    watch = BlockWatch()
    feed(watch, [403] * (BASELINE + WINDOW * 3))


def test_a_few_scattered_refusals_are_not_a_block():
    watch = BlockWatch()
    feed(watch, [404] * BASELINE)
    feed(watch, ([404] * 8 + [403]) * 6)


def test_the_guard_says_nothing_before_it_has_a_baseline():
    """No opinion until it has seen enough to have one."""
    watch = BlockWatch()
    feed(watch, [403] * (BASELINE - 1))
    assert watch.baseline_refusal is None


def test_the_guard_reports_what_it_saw():
    watch = BlockWatch()
    feed(watch, [404] * BASELINE)
    with pytest.raises(Blocked) as exc:
        feed(watch, [429] * WINDOW)
    message = str(exc.value)
    assert "429" in message
    assert "%" in message


# --- being refused for the whole scan ----------------------------------------------------------


def test_a_scan_answered_entirely_by_a_refusal_is_reported():
    """Measured for real: 144 of 146 responses were a 1-byte 403 that curl never saw."""
    watch = BlockWatch()
    feed(watch, [403] * 140 + [200, 301])
    wall = watch.wall()
    assert wall is not None
    assert "403" in wall and "WAF" in wall


def test_an_ordinary_scan_reports_no_wall():
    watch = BlockWatch()
    feed(watch, [404] * 140 + [200] * 6)
    assert watch.wall() is None


def test_a_wall_is_reported_but_does_not_abort():
    """Aborting would throw away a scan that is still rankable - and the two responses that
    escaped the refusal are exactly what the operator wants to see."""
    watch = BlockWatch()
    feed(watch, [403] * (BASELINE + WINDOW * 3))  # no Blocked raised
    assert watch.wall() is not None


def test_a_wall_needs_to_be_nearly_total():
    watch = BlockWatch()
    feed(watch, ([404] * 4 + [403]) * 30)
    assert watch.wall() is None
