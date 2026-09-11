"""SQLite store for baselines. Feature vectors and hashes; never response bodies.

A baseline is a Fly Bloom Filter's synaptic weights plus everything needed to say what they
mean: the channel-set version that encoded them, the projection that hashed them, and the
random seed if the projection was random. Load-time checking is strict on all of it.

**A baseline built under one channel set refuses to load under another.** Scores from
different channel sets are not comparable - the vectors mean different things - so a
mismatch is an error rather than a warning. The same goes for the projection and its seed:
a tag is only meaningful with respect to the projection that produced it, so restoring
weights under a different one would silently score against nonsense.

Timestamps are wall-clock UTC, which is what makes temporal decay meaningful across
sessions: within one scan "time" is how many responses have gone past, but a baseline saved
six months ago should be six months stale, not 1,998 records stale.
"""

from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from flypaper import __version__

__all__ = ["Baseline", "BaselineMismatch", "Store", "open_db"]

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS baselines (
    name           TEXT PRIMARY KEY,
    channel_set    TEXT NOT NULL,
    projection     TEXT NOT NULL,
    seed           INTEGER,
    n_kc           INTEGER NOT NULL,
    sparsity       REAL NOT NULL,
    learning_rate  REAL NOT NULL,
    decay_halflife REAL,
    time_base      TEXT NOT NULL DEFAULT 'records',
    n_observed     INTEGER NOT NULL,
    created_utc    REAL NOT NULL,
    updated_utc    REAL NOT NULL,
    weights        BLOB NOT NULL,
    last_seen      BLOB NOT NULL,
    notes          TEXT NOT NULL DEFAULT ''
);
"""


class BaselineMismatch(RuntimeError):
    """A baseline was built under settings that make it meaningless here. Always fatal."""


@dataclass(frozen=True, slots=True)
class Baseline:
    name: str
    channel_set: str
    projection: str
    seed: int | None
    n_kc: int
    sparsity: float
    learning_rate: float
    decay_halflife: float | None
    time_base: str
    n_observed: int
    created_utc: float
    updated_utc: float
    weights: np.ndarray
    last_seen: np.ndarray
    notes: str = ""

    @property
    def age_seconds(self) -> float:
        return max(time.time() - self.updated_utc, 0.0)

    @property
    def saturation(self) -> float:
        return float(1.0 - self.weights.mean())


class Store:
    """Baselines on disk. Never bodies - there is no column to put one in."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path))
        self._conn.executescript(_SCHEMA)
        self._check_schema_version()

    def _check_schema_version(self) -> None:
        row = self._conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
        if row is None:
            self._conn.execute(
                "INSERT INTO meta(key, value) VALUES ('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
            self._conn.commit()
            return
        found = int(row[0])
        if found != SCHEMA_VERSION:
            raise BaselineMismatch(
                f"{self.path} uses store schema v{found}, this build speaks v{SCHEMA_VERSION}. "
                f"Refusing to read it rather than guess at the difference."
            )

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def names(self) -> list[str]:
        return [r[0] for r in self._conn.execute("SELECT name FROM baselines ORDER BY name")]

    def save(self, name: str, ranker, *, notes: str = "") -> None:
        """Persist a ranker's filter as a named baseline, replacing any previous one."""
        now = time.time()
        created = now
        row = self._conn.execute(
            "SELECT created_utc FROM baselines WHERE name=?", (name,)
        ).fetchone()
        if row:
            created = float(row[0])
        self._conn.execute(
            """INSERT OR REPLACE INTO baselines
               (name, channel_set, projection, seed, n_kc, sparsity, learning_rate,
                decay_halflife, time_base, n_observed, created_utc, updated_utc, weights,
                last_seen, notes)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                name,
                ranker.channel_set,
                ranker.projection,
                ranker.seed,
                int(ranker.flyhash.n_kc),
                float(ranker.sparsity),
                float(ranker.learning_rate),
                ranker.decay_halflife,
                ranker.time_base,
                int(ranker.filter.n_observed),
                created,
                now,
                ranker.filter.weights.astype(np.float64).tobytes(),
                ranker.filter.last_seen.astype(np.float64).tobytes(),
                notes or f"flypaper {__version__}",
            ),
        )
        self._conn.commit()

    def load(self, name: str) -> Baseline:
        row = self._conn.execute(
            """SELECT name, channel_set, projection, seed, n_kc, sparsity, learning_rate,
                      decay_halflife, time_base, n_observed, created_utc, updated_utc,
                      weights, last_seen, notes
               FROM baselines WHERE name=?""",
            (name,),
        ).fetchone()
        if row is None:
            known = ", ".join(self.names()) or "(none)"
            raise KeyError(f"no baseline named {name!r} in {self.path}. Known: {known}")
        return Baseline(
            name=row[0],
            channel_set=row[1],
            projection=row[2],
            seed=row[3],
            n_kc=int(row[4]),
            sparsity=float(row[5]),
            learning_rate=float(row[6]),
            decay_halflife=row[7],
            time_base=row[8],
            n_observed=int(row[9]),
            created_utc=float(row[10]),
            updated_utc=float(row[11]),
            weights=np.frombuffer(row[12], dtype=np.float64).copy(),
            last_seen=np.frombuffer(row[13], dtype=np.float64).copy(),
            notes=row[14],
        )

    def restore_into(self, name: str, ranker) -> Baseline:
        """Load a baseline into a ranker, refusing anything that would make it meaningless.

        Every check here is an error and not a warning. A baseline restored under the wrong
        channel set or the wrong projection would produce numbers that look fine and mean
        nothing, which is worse than a crash.
        """
        baseline = self.load(name)
        problems = []
        if baseline.channel_set != ranker.channel_set:
            problems.append(
                f"channel set {baseline.channel_set!r} != {ranker.channel_set!r} - the "
                f"vectors mean different things, so the scores would not be comparable"
            )
        if baseline.projection != ranker.projection:
            problems.append(f"projection {baseline.projection!r} != {ranker.projection!r}")
        if baseline.projection == "random" and baseline.seed != ranker.seed:
            problems.append(
                f"random projection seed {baseline.seed} != {ranker.seed} - a different "
                f"seed is a different hash, so the stored weights address other cells"
            )
        if baseline.n_kc != ranker.flyhash.n_kc:
            problems.append(f"{baseline.n_kc} Kenyon cells != {ranker.flyhash.n_kc}")
        if baseline.time_base != ranker.time_base:
            problems.append(
                f"time base {baseline.time_base!r} != {ranker.time_base!r} - the stored "
                f"last-seen stamps are on a different scale, so decay would be nonsense"
            )
        if problems:
            raise BaselineMismatch(
                f"baseline {name!r} cannot be used here:\n  - " + "\n  - ".join(problems)
            )

        ranker.filter.weights = baseline.weights.copy()
        ranker.filter.last_seen = baseline.last_seen.copy()
        ranker.filter.n_observed = baseline.n_observed
        return baseline

    def describe(self, name: str) -> dict:
        b = self.load(name)
        return {
            "name": b.name,
            "channel_set": b.channel_set,
            "projection": b.projection,
            "seed": b.seed,
            "n_kc": b.n_kc,
            "time_base": b.time_base,
            "n_observed": b.n_observed,
            "saturation": round(b.saturation, 4),
            "age_seconds": round(b.age_seconds, 1),
            "created_utc": b.created_utc,
            "updated_utc": b.updated_utc,
            "notes": b.notes,
        }


@contextmanager
def open_db(path: str | Path):
    store = Store(path)
    try:
        yield store
    finally:
        store.close()


def default_path() -> Path:
    """Where baselines live unless told otherwise. Never a place bodies are written."""
    return Path.home() / ".local" / "share" / "flypaper" / "baselines.sqlite3"
