"""Is the input something a baseline can be learned from?

The commonest way to misuse flypaper is to feed it a stream that ffuf already filtered.
Every ffuf tutorial teaches `-mc 200,301` or `-fc 404` and that is the right habit for ffuf.
Here it removes the only thing the tool learns from, and it fails quietly: the ranking still
looks fine.
"""

from __future__ import annotations

from flypaper.ingest.ffuf import FfufResult
from flypaper.rank.health import StreamHealth


def response(status: int, length: int = 1000) -> FfufResult:
    return FfufResult(
        inputs={"FUZZ": "x"},
        url="http://h/x",
        status=status,
        length=length,
        words=10,
        lines=3,
        content_type="text/html",
    )


def feed(health: StreamHealth, status: int, n: int, *, surfaced: int = 0, partition: str = ""):
    for i in range(n):
        health.observe(response(status), surfaced=i < surfaced, partition=partition)


def test_a_healthy_stream_says_nothing():
    health = StreamHealth()
    feed(health, 404, 2000, surfaced=8)
    assert health.warnings() == []


def test_a_prefiltered_stream_is_called_out_in_the_terms_of_the_mistake():
    """`-mc 200,301` is the habit; the warning has to name it."""
    health = StreamHealth()
    feed(health, 200, 400)
    warnings = " ".join(health.warnings())
    assert "-mc all" in warnings
    assert "-mc 200,301" in warnings or "-fc 404" in warnings


def test_an_empty_stream_suggests_the_likely_cause():
    assert "-json" in " ".join(StreamHealth().warnings())


def test_a_short_stream_is_flagged_as_mostly_cold_start():
    health = StreamHealth()
    feed(health, 404, 40)
    assert any("cold start" in w for w in health.warnings())


def test_surfacing_most_of_the_stream_is_implausible():
    health = StreamHealth()
    feed(health, 404, 1000, surfaced=500)
    assert any("too many to be outliers" in w for w in health.warnings())


def test_a_normal_surfacing_rate_is_not_flagged():
    health = StreamHealth()
    feed(health, 404, 2000, surfaced=10)
    assert not any("outliers" in w for w in health.warnings())


def test_mostly_thin_partitions_are_flagged():
    health = StreamHealth()
    for i in range(20):
        feed(health, 404, 3, partition=f"host{i}")
    feed(health, 404, 500, partition="busy")
    assert any("cannot have a baseline" in w for w in health.warnings())


def test_a_soft_404_host_is_hedged_not_accused():
    """A host that genuinely answers 200 to everything looks identical to a filtered stream
    from here, so the warning has to be conditional rather than an accusation."""
    health = StreamHealth()
    feed(health, 200, 2000)
    filtered = next(w for w in health.warnings() if "misses" in w)
    assert filtered.lstrip().startswith("only") and "If you filtered" in filtered


def test_health_never_changes_a_result():
    """It reports. It does not drop, reorder or rescore anything."""
    import inspect

    from flypaper.rank import health as module

    source = inspect.getsource(module)
    for forbidden in ("novelty =", "def score", "sort(", "del "):
        assert forbidden not in source, f"health checks must not {forbidden!r}"
