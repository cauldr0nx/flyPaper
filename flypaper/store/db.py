"""SQLite store for baselines and hashes. M6.

Feature vectors and hashes only. Response bodies are not stored by default, and
`--retain-bodies` will never write to the default database path.

A baseline records the channel-set version that built it and refuses to load under a
different one, rather than silently producing garbage.
"""

from __future__ import annotations

__all__ = ["open_db"]


def open_db(*args, **kwargs):
    raise NotImplementedError("M6: the store is not implemented yet.")
