"""Ranking mechanics.

The benchmark against ffuf's filters lives in `bench/benchmark_ranking.py`. These tests
assert the properties the benchmark depends on, so a change that breaks the ranker cannot
present itself as a change in the result.
"""

from __future__ import annotations

import io

import pytest

from flypaper.ingest.ffuf import FfufResult
from flypaper.rank.report import RollingPercentile, Terminal, band, write_jsonl
from flypaper.rank.score import Ranker, Scored, rank


def response(word: str, length: int, *, status: int = 200, words: int = 10, lines: int = 3):
    return FfufResult(
        inputs={"FUZZ": word, "FFUFHASH": "abc123"},
        url=f"http://t/{word}",
        status=status,
        length=length,
        words=words,
        lines=lines,
        content_type="text/html",
        host="t",
        duration_ms=1.0,
    )


def a_stream(n: int = 400, odd_at: int = 300):
    """A monotonous stream with exactly one structurally different response in it."""
    out = []
    for i in range(n):
        if i == odd_at:
            out.append(response("treasure", 90_000, words=9000, lines=900))
        else:
            out.append(response(f"w{i}", 1000 + (i % 5), words=100, lines=20))
    return out


# --- scoring -----------------------------------------------------------------------------------


def test_the_one_odd_response_ranks_first():
    ranked = rank(a_stream(), passes=2)
    assert ranked[0].result.word == "treasure"


def test_small_jitter_does_not_make_a_response_novel():
    """Lengths vary by a few bytes, as a rotating token makes them. That must not count."""
    ranked = rank(a_stream(), passes=2)
    noise = [s.novelty for s in ranked if s.result.word != "treasure"]
    assert max(noise) < ranked[0].novelty / 2


def test_live_mode_scores_against_the_past_only():
    """Each result is judged against what preceded it, which is all a running scan has."""
    stream = a_stream()
    scored = list(Ranker().stream(stream))
    assert len(scored) == len(stream)
    assert scored[0].novelty > 0.9, "the first response has nothing to be familiar against"
    assert scored[-1].novelty < 0.2, "by the end the monotony should be familiar"


def test_offline_mode_removes_the_cold_start():
    """Two passes: the ranking must not be dominated by whatever arrived first."""
    live = rank(a_stream(), passes=1)
    offline = rank(a_stream(), passes=2)
    assert offline[0].result.word == "treasure"
    # In live mode the cold start puts early arrivals on top instead.
    assert live[0].result.word != "treasure"


def test_leave_one_out_undoes_exactly_one_self_observation():
    """Scoring a record against a baseline it is part of scores it partly against itself.

    The correction is exact rather than approximate: depression multiplies each active
    weight by `1 - learning_rate * strength`, and multiplication commutes, so dividing that
    factor back out recovers the weight the cell would have had. The test asserts the
    mechanism - that discounting raises the score by the right amount - and not some
    absolute figure. A response can share most of its tag with the baseline for honest
    reasons, and then a low score is the correct answer.
    """
    import numpy as np

    from flypaper.brain.bloom import FlyBloomFilter
    from flypaper.brain.flyhash import FlyHash, random_projection

    fh = FlyHash(random_projection(8, 500, 3, rng=np.random.default_rng(0)))
    filt = FlyBloomFilter(fh.n_kc, learning_rate=0.4)
    tag = fh.tag_valued(np.random.default_rng(1).random((1, 8)).astype(np.float32))

    before = float(filt.score(tag)[0])
    filt.observe(tag)
    after = float(filt.score(tag)[0])
    restored = float(filt.score_excluding(tag)[0])

    assert after < before, "observing must depress"
    assert restored == pytest.approx(before, abs=1e-9), "discounting must restore exactly"


def test_offline_scores_sit_above_their_self_observed_values():
    """The practical consequence: offline ranking is not systematically compressed."""
    offline = rank(a_stream(), passes=2)
    treasure = next(s for s in offline if s.result.word == "treasure")
    noise = max(s.novelty for s in offline if s.result.word != "treasure")
    assert treasure.novelty > 4 * noise


def test_every_scored_record_names_its_channel_set_and_projection():
    """A score without its channel set is not interpretable; sets are not comparable."""
    for scored in rank(a_stream()[:100]):
        assert scored.channel_set
        assert scored.projection
        assert scored.as_dict()["channel_set"] == scored.channel_set


def test_unknown_projection_is_rejected():
    with pytest.raises(ValueError, match="unknown projection"):
        Ranker(projection="wishful")


def test_connectome_projection_requires_a_circuit():
    with pytest.raises(ValueError, match="circuit_path"):
        Ranker(projection="connectome")


def test_decay_makes_an_aged_baseline_novel_again():
    ranker = Ranker(decay_halflife=20.0)
    stream = [response(f"w{i}", 1000) for i in range(300)]
    scores = [ranker.score(r).novelty for r in stream]
    assert scores[-1] < 0.2
    # Nothing happens for a long while, then the same page comes back.
    later = ranker.filter.score(
        ranker.flyhash.tag_valued(ranker.encoder.encode(stream[0])[None, :]),
        when=ranker.filter._clock + 2000,
    )[0]
    assert later > 0.8


# --- the display gate ---------------------------------------------------------------------------


def test_rolling_percentile_says_nothing_until_it_has_a_distribution():
    gate = RollingPercentile(99.0, minimum=50)
    for _ in range(49):
        gate.observe(0.0)
    assert not gate.passes(1.0)
    gate.observe(0.0)
    assert gate.passes(1.0)


def test_rolling_percentile_selects_the_tail():
    gate = RollingPercentile(99.0, minimum=10)
    for i in range(1000):
        gate.observe(0.001 * (i % 10))
    assert gate.passes(0.5)
    assert not gate.passes(0.0)


def test_rolling_percentile_forgets_old_scores():
    """A target that changes behaviour mid-scan must recalibrate, not go quiet."""
    gate = RollingPercentile(99.0, window=200, minimum=10)
    for _ in range(200):
        gate.observe(0.9)
    assert not gate.passes(0.5)
    for _ in range(200):
        gate.observe(0.0)
    assert gate.passes(0.5)


def test_warmup_results_neither_print_nor_set_the_cutoff():
    """The cold start is a known artifact; letting it calibrate the gate silences the run."""
    out = io.StringIO()
    term = Terminal(stream=out, percentile=99.0, warmup=5, colour=False)
    for _ in range(5):
        term.result(Scored(response("x", 10), 1.0, 0, "v", "random"))
    assert term.gate._scores == []
    assert term.suppressed == 5
    assert out.getvalue() == ""


def test_top_is_a_budget_not_a_second_filter():
    out = io.StringIO()
    term = Terminal(stream=out, threshold=0.99, colour=False)
    term.result(Scored(response("x", 10), 0.01, 0, "v", "random"), force=True)
    assert "x" in out.getvalue()


def test_band_labels_describe_the_number_not_a_verdict():
    for novelty in (0.0, 0.3, 0.6, 0.95):
        _, label = band(novelty)
        assert label not in {"vulnerable", "finding", "issue", "critical"}


def test_jsonl_round_trips():
    import json

    out = io.StringIO()
    n = write_jsonl(rank(a_stream()[:20]), stream=out)
    lines = [json.loads(x) for x in out.getvalue().splitlines()]
    assert n == len(lines) == 20
    assert {"novelty", "url", "word", "channel_set", "projection"} <= set(lines[0])
