"""Baselines on disk.

The point of this module is refusal. A baseline restored under settings that make it
meaningless would produce numbers that look fine and mean nothing, which is worse than a
crash - so every mismatch is an error and none is a warning.
"""

from __future__ import annotations

import time

import pytest

from flypaper.ingest.ffuf import FfufResult
from flypaper.rank.score import Ranker
from flypaper.store.db import Baseline, BaselineMismatch, Store, open_db


def response(word: str, length: int = 1000):
    return FfufResult(
        inputs={"FUZZ": word},
        url=f"http://t/{word}",
        status=200,
        length=length,
        words=100,
        lines=20,
        content_type="text/html",
    )


def trained(n: int = 300, **kwargs) -> Ranker:
    ranker = Ranker(**kwargs)
    for i in range(n):
        ranker.score(response(f"w{i}", 1000 + (i % 4)))
    return ranker


@pytest.fixture
def db(tmp_path):
    return tmp_path / "baselines.sqlite3"


# --- round trip -------------------------------------------------------------------------------


def test_save_and_restore_round_trips(db):
    ranker = trained()
    with Store(db) as store:
        store.save("t", ranker)
        restored = Ranker()
        baseline = store.restore_into("t", restored)

    assert baseline.n_observed == ranker.filter.n_observed
    assert (restored.filter.weights == ranker.filter.weights).all()
    assert (restored.filter.last_seen == ranker.filter.last_seen).all()


def test_a_restored_baseline_makes_the_same_traffic_familiar(db):
    """The point of persistence: a second scan of the same target is not novel again."""
    first = trained()
    with Store(db) as store:
        store.save("t", first)
        second = Ranker()
        store.restore_into("t", second)
        fresh = Ranker()

        probe = response("w7", 1001)
        assert second.score(probe).novelty < fresh.score(probe).novelty


def test_saving_twice_keeps_the_original_creation_time(db):
    with Store(db) as store:
        store.save("t", trained(50))
        created = store.load("t").created_utc
        time.sleep(0.01)
        store.save("t", trained(60))
        assert store.load("t").created_utc == created
        assert store.load("t").updated_utc > created


def test_names_and_describe(db):
    with Store(db) as store:
        store.save("alpha", trained(20))
        store.save("beta", trained(20))
        assert store.names() == ["alpha", "beta"]
        described = store.describe("alpha")
        assert described["channel_set"] and described["projection"]
        assert 0.0 <= described["saturation"] <= 1.0


def test_missing_baseline_names_what_is_there(db):
    with Store(db) as store:
        store.save("alpha", trained(20))
        with pytest.raises(KeyError, match="alpha"):
            store.load("nope")


def test_open_db_closes(db):
    with open_db(db) as store:
        store.save("t", trained(20))
    with open_db(db) as store:
        assert store.names() == ["t"]


# --- refusals ---------------------------------------------------------------------------------


def test_a_different_channel_set_is_refused(db):
    """Vectors from different channel sets mean different things; scores are incomparable."""
    with Store(db) as store:
        store.save("t", trained(50, channel_set="v3-response"))
        with pytest.raises(BaselineMismatch, match="channel set"):
            store.restore_into("t", Ranker(channel_set="v1-raw"))


def test_a_different_random_seed_is_refused(db):
    """A different seed is a different hash: the stored weights address other cells."""
    with Store(db) as store:
        store.save("t", trained(50, seed=0))
        with pytest.raises(BaselineMismatch, match="seed"):
            store.restore_into("t", Ranker(seed=1))


def test_a_different_time_base_is_refused(db):
    """Record counts and wall-clock stamps are not on the same scale."""
    with Store(db) as store:
        store.save("t", trained(50, time_base="wallclock"))
        with pytest.raises(BaselineMismatch, match="time base"):
            store.restore_into("t", Ranker(time_base="records"))


def test_a_future_schema_version_is_refused(db):
    import sqlite3

    with Store(db) as store:
        store.save("t", trained(20))
    conn = sqlite3.connect(str(db))
    conn.execute("UPDATE meta SET value='999' WHERE key='schema_version'")
    conn.commit()
    conn.close()
    with pytest.raises(BaselineMismatch, match="schema"):
        Store(db)


# --- what must never be stored ------------------------------------------------------------------


def test_the_schema_has_nowhere_to_put_a_response_body(db):
    """Not a policy, a fact about the table: there is no column for it."""
    import sqlite3

    with Store(db) as store:
        store.save("t", trained(20))
    conn = sqlite3.connect(str(db))
    columns = {row[1] for row in conn.execute("PRAGMA table_info(baselines)")}
    conn.close()
    assert not ({"body", "content", "response", "html", "bytes"} & columns)
    assert "weights" in columns


def test_default_path_is_not_a_place_bodies_get_written():
    from flypaper.store.db import default_path

    assert default_path().suffix == ".sqlite3"
    assert "flypaper" in str(default_path())


# --- temporal decay across scans ---------------------------------------------------


def test_wallclock_decay_makes_an_old_baseline_novel_again(db):
    """The property a hash set cannot have, across sessions rather than within one run."""
    halflife = 86400.0
    ranker = trained(300, time_base="wallclock", decay_halflife=halflife)
    with Store(db) as store:
        store.save("t", ranker)
        restored = Ranker(time_base="wallclock", decay_halflife=halflife)
        store.restore_into("t", restored)

    vector = restored.encoder.encode(response("w7", 1001))
    tag = restored.flyhash.tag_valued(vector[None, :])
    now = time.time()
    fresh = restored.filter.score(tag, when=now)[0]
    one_halflife = restored.filter.score(tag, when=now + halflife)[0]
    much_later = restored.filter.score(tag, when=now + 30 * halflife)[0]

    assert fresh < 0.2
    assert one_halflife == pytest.approx(0.5, abs=0.1)
    assert much_later > 0.95


def test_without_a_halflife_a_baseline_never_ages(db):
    ranker = trained(300, time_base="wallclock", decay_halflife=None)
    with Store(db) as store:
        store.save("t", ranker)
        restored = Ranker(time_base="wallclock")
        store.restore_into("t", restored)
    vector = restored.encoder.encode(response("w7", 1001))
    tag = restored.flyhash.tag_valued(vector[None, :])
    now = time.time()
    assert restored.filter.score(tag, when=now)[0] == pytest.approx(
        restored.filter.score(tag, when=now + 10**7)[0]
    )


def test_baseline_reports_its_own_age_and_saturation(db):
    with Store(db) as store:
        store.save("t", trained(300))
        baseline: Baseline = store.load("t")
    assert baseline.age_seconds >= 0.0
    assert 0.0 < baseline.saturation < 1.0


def test_unknown_time_base_is_rejected():
    with pytest.raises(ValueError, match="time_base"):
        Ranker(time_base="vibes")
