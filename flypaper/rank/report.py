"""Terminal output and JSONL out. M4/M6.

Output says "novel". Never "vulnerable", never "finding", never "issue". The tool ranks;
it does not detect. Every ranked record carries the channel-set version that produced it.
"""

from __future__ import annotations

__all__ = ["render"]


def render(*args, **kwargs):
    raise NotImplementedError("M4: reporting is not implemented yet.")
