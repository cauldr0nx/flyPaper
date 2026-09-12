"""Monitoring: what is new on a target since the last scan.

`fly rank --baseline` learns as it goes, so by the time it prints a result it has already
made it familiar; asked to rank the same target twice it reports that nothing is
surprising, which is true and useless. `watch` holds the baseline still.
"""

from __future__ import annotations

import pytest

from flypaper.ingest.ffuf import FfufResult
from flypaper.rank.score import Ranker
from flypaper.store.db import Store


def response(word: str, length: int, *, status: int = 404) -> FfufResult:
    return FfufResult(
        inputs={"FUZZ": word},
        url=f"http://t/{word}",
        status=status,
        length=length,
        words=max(length // 11, 1),
        lines=max(length // 70, 1),
        content_type="text/html",
        host="t",
        duration_ms=1.0,
    )


def last_week(n: int = 600) -> list[FfufResult]:
    """A target: a wall of 404s and a couple of real pages."""
    out = [response(f"w{i}", 900 + (i % 4)) for i in range(n)]
    out.append(response("admin", 6914, status=200))
    out.append(response("backup", 3905, status=200))
    return out


def established(tmp_path, name="acme"):
    ranker = Ranker(time_base="wallclock")
    for r in last_week():
        ranker.score(r)
    db = tmp_path / "b.sqlite3"
    with Store(db) as store:
        store.save(name, ranker)
    return db


def scores_against(db, stream, name="acme") -> dict[str, float]:
    ranker = Ranker(time_base="wallclock")
    with Store(db) as store:
        store.restore_into(name, ranker)
    return {r.word: ranker.score_only(r).novelty for r in stream}


def test_an_unchanged_target_has_nothing_new(tmp_path):
    """The answer most weeks, and it has to be quiet."""
    db = established(tmp_path)
    scores = scores_against(db, last_week())
    assert max(scores.values()) < 0.5


def test_a_structurally_new_page_is_flagged(tmp_path):
    db = established(tmp_path)
    stream = [*last_week(), response(".git/config", 412, status=200)]
    assert scores_against(db, stream)[".git/config"] > 0.5


def test_a_new_url_that_looks_like_an_old_page_is_not_flagged(tmp_path):
    """By design, and the distinction matters.

    `/admin-v2` at 7,321 bytes against a baseline already holding `/admin` at 6,914 is
    structurally the same kind of page. A URL diff would flag it; this does not, which is
    right for monitoring at scale and wrong if a URL diff is what you wanted. The filter
    stores shapes, not identities.
    """
    db = established(tmp_path)
    stream = [*last_week(), response("admin-v2", 7321, status=200)]
    assert scores_against(db, stream)["admin-v2"] < 0.5


def test_watching_does_not_disturb_the_baseline(tmp_path):
    """The bug that makes `rank --baseline` unsuitable: it learns what it reports."""
    db = established(tmp_path)
    new = [*last_week(), response(".git/config", 412, status=200)]

    first = scores_against(db, new)[".git/config"]
    second = scores_against(db, new)[".git/config"]
    assert first == pytest.approx(second), "scoring must not teach the stored baseline"


def test_rank_by_contrast_does_learn_what_it_reports(tmp_path):
    """Why `watch` exists at all, asserted rather than asserted about."""
    db = established(tmp_path)
    ranker = Ranker(time_base="wallclock")
    with Store(db) as store:
        store.restore_into("acme", ranker)
    probe = response(".git/config", 412, status=200)
    first = ranker.score(probe).novelty
    second = ranker.score(probe).novelty
    assert second < first


def test_a_missing_baseline_says_how_to_make_one(tmp_path):
    from flypaper.cli import main

    db = tmp_path / "empty.sqlite3"
    code = main(["watch", str(tmp_path / "nothing.jsonl"), "--baseline", "nope", "--db", str(db)])
    assert code == 1
