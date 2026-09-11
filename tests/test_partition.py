"""One baseline per host, or per directory.

This is the thing ffuf documents that it cannot do. From its own issue tracker, on scanning
several targets at once: *"would be impossible to put correct flag for each host"* - because
`-fs` and `-ac` derive one filter and apply it to everything.
"""

from __future__ import annotations

import pytest

from flypaper.ingest.ffuf import FfufResult
from flypaper.rank.report import PartitionedGate
from flypaper.rank.score import PartitionedRanker, partition_key, rank


def response(host: str, word: str, length: int, *, status: int = 404, base: str = "/"):
    return FfufResult(
        inputs={"FUZZ": word},
        url=f"https://{host}{base}{word}",
        status=status,
        length=length,
        words=max(length // 10, 1),
        lines=max(length // 60, 1),
        content_type="text/html",
        host=host,
        duration_ms=1.0,
    )


# --- what a partition key is -------------------------------------------------------------------


def test_host_partitions_by_host():
    assert partition_key(response("a.example.com", "x", 10), "host") == "a.example.com"


def test_none_partitions_into_one():
    assert partition_key(response("a.example.com", "x", 10), "none") == ""


def test_dir_partitions_by_the_scan_base():
    r = response("h", "admin", 10, base="/cd/basic/")
    assert partition_key(r, "dir") == "h/cd/basic/"


def test_dir_ignores_slashes_inside_the_fuzzed_word():
    """A real bug, found by running it.

    Wordlists contain slashes. Splitting the URL on its last slash filed
    `cd/basic/admin/backup.php` under its own directory - a partition of one, which then
    scored 1.000 because it had never seen anything else. The word is stripped instead.
    """
    r = response("h", "cjcrema/VmAS.php", 10, base="/cd/basic/")
    assert partition_key(r, "dir") == "h/cd/basic/"


def test_unknown_partition_is_refused():
    with pytest.raises(ValueError, match="unknown partition"):
        partition_key(response("h", "x", 10), "vibes")
    with pytest.raises(ValueError, match="unknown partition"):
        PartitionedRanker(how="vibes")


# --- the thing it is for -------------------------------------------------------------------------


def two_hosts(n: int = 300):
    """Two hosts whose idea of "not found" is nothing like the other's, interleaved.

    Each host's real page is shaped exactly like the *other* host's noise - same status,
    same size - so size is the only thing that separates it, and a filter that has seen
    both hosts has been taught that both sizes are ordinary. Giving the hits a distinct
    status code would make the test pass for the wrong reason.
    """
    out = []
    for i in range(n):
        out.append(response("small.example.com", f"w{i}", 120 + (i % 3)))
        out.append(response("big.example.com", f"w{i}", 48_000 + (i % 3)))
    out.insert(200, response("small.example.com", "treasure", 48_000))
    out.insert(400, response("big.example.com", "treasure", 120))
    return out


def test_one_baseline_cannot_describe_two_hosts():
    """The failure the partitioning exists to fix.

    Each host's hit is shaped like the *other* host's noise, so a filter that has seen both
    has been taught that both shapes are ordinary.
    """
    shared = rank(two_hosts(), passes=2, partition="none")
    split = rank(two_hosts(), passes=2, partition="host")

    def rank_of(scored, host):
        return next(
            i
            for i, s in enumerate(scored, 1)
            if s.result.word == "treasure" and s.result.host == host
        )

    for host in ("small.example.com", "big.example.com"):
        assert rank_of(split, host) < rank_of(shared, host), host


def test_each_partition_gets_its_own_filter():
    ranker = PartitionedRanker(how="host", min_observations=1)
    for r in two_hosts(60):
        ranker.score(r)
    assert ranker.partitions == 2
    assert set(ranker.filters) == {"small.example.com", "big.example.com"}
    # Separate filters, separately saturated.
    assert all(f.n_observed > 0 for f in ranker.filters.values())


def test_the_projection_is_shared_so_scores_are_comparable():
    """Each partition learns its own baseline; none of them learns its own hash."""
    ranker = PartitionedRanker(how="host", min_observations=1)
    for r in two_hosts(40):
        ranker.score(r)
    assert ranker.partitions == 2
    # One FlyHash for the whole ranker, not one per partition.
    assert not hasattr(ranker, "flyhashes")
    assert ranker.flyhash is not None


# --- partitions with no baseline yet --------------------------------------------------------------


def test_a_thin_partition_is_scored_but_not_surfaced():
    """A host seen three times has no baseline, so everything in it looks novel.

    Found by running `--per dir` on a real scan: partitions of one response each all scored
    1.000 and filled the top of the ranking.
    """
    ranker = PartitionedRanker(how="host", min_observations=25)
    stream = [response("busy.example.com", f"w{i}", 120) for i in range(60)]
    stream += [response("thin.example.com", f"t{i}", 90_000) for i in range(3)]

    scored = [ranker.score(r) for r in stream]
    thin = [s for s in scored if s.result.host == "thin.example.com"]
    busy = [s for s in scored if s.result.host == "busy.example.com"]

    assert all(not s.settled for s in thin)
    assert any(s.settled for s in busy)
    assert max(s.novelty for s in thin) > 0.5, "thin partitions do look novel - that is why"


def test_unsettled_results_sort_below_settled_ones():
    stream = [response("busy.example.com", f"w{i}", 120) for i in range(60)]
    stream += [response("thin.example.com", f"t{i}", 90_000) for i in range(3)]
    scored = rank(stream, passes=1, partition="host", min_observations=25)
    settled_flags = [s.settled for s in scored]
    assert settled_flags[0] is True
    assert settled_flags[-1] is False


# --- the display gate -----------------------------------------------------------------------------


def test_the_gate_is_per_partition():
    """One gate across a multi-host scan is decided by the noisiest host, and the quiet
    ones then never surface anything."""
    gate = PartitionedGate(99.0, minimum=50)
    for _ in range(60):
        gate.observe("noisy", 0.9)
        gate.observe("quiet", 0.0)
    assert len(gate) == 2
    assert gate.passes("quiet", 0.4)
    assert not gate.passes("noisy", 0.4)


def test_partition_travels_into_the_output():
    scored = rank(two_hosts(60), passes=2, partition="host")
    assert scored[0].as_dict()["partition"]
    assert "settled" in scored[0].as_dict()


def test_connectome_projection_is_refused_when_partitioning():
    """Partitions share one projection; the connectome path builds its own."""
    with pytest.raises(ValueError, match="random projection"):
        PartitionedRanker(how="host", projection="connectome")


# --- partitioned baselines persist, one row per partition ---------------------------------------


def test_partitions_persist_and_restore_separately(tmp_path):
    """The two headline features have to compose: a weekly sweep across fifty hosts keeps
    fifty baselines that each age on their own."""
    import numpy as np

    from flypaper.store.db import Store

    ranker = PartitionedRanker(how="host", time_base="wallclock", min_observations=1)
    for r in two_hosts(200):
        ranker.score(r)

    db = tmp_path / "b.sqlite3"
    with Store(db) as store:
        store.save("sweep", ranker)
        assert sorted(store.partitions("sweep")) == ["big.example.com", "small.example.com"]

        restored = PartitionedRanker(how="host", time_base="wallclock", min_observations=1)
        got = store.restore_all_into("sweep", restored)

    assert len(got) == 2
    assert restored.partitions == 2
    for key, filt in ranker.filters.items():
        assert np.allclose(filt.weights, restored.filters[key].weights)


def test_a_restored_partition_makes_that_host_familiar(tmp_path):
    from flypaper.store.db import Store

    db = tmp_path / "b.sqlite3"
    first = PartitionedRanker(how="host", time_base="wallclock", min_observations=1)
    for r in two_hosts(200):
        first.score(r)
    with Store(db) as store:
        store.save("sweep", first)
        second = PartitionedRanker(how="host", time_base="wallclock", min_observations=1)
        store.restore_all_into("sweep", second)
        fresh = PartitionedRanker(how="host", time_base="wallclock", min_observations=1)

    probe = response("small.example.com", "w7", 121)
    assert second.score(probe).novelty < fresh.score(probe).novelty


def test_a_mismatched_partitioned_baseline_is_refused(tmp_path):
    from flypaper.store.db import BaselineMismatch, Store

    db = tmp_path / "b.sqlite3"
    ranker = PartitionedRanker(how="host", time_base="wallclock", min_observations=1, seed=0)
    for r in two_hosts(60):
        ranker.score(r)
    with Store(db) as store:
        store.save("sweep", ranker)
        wrong = PartitionedRanker(how="host", time_base="wallclock", min_observations=1, seed=7)
        with pytest.raises(BaselineMismatch, match="seed"):
            store.restore_all_into("sweep", wrong)
