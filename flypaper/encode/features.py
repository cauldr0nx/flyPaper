"""Encoding one result into the receptor vector. M2.

Two properties must be tested explicitly before this passes its gate, because they are the
value proposition and not a nice-to-have:

  1. Rotating-token invariance - responses identical but for a CSRF token land in the
     same neighbourhood.
  2. Soft-404 collapse - a wildcard-responding host produces one dense familiar cluster,
     not N novel items.
"""

from __future__ import annotations

from flypaper.ingest.ffuf import FfufResult

__all__ = ["encode"]


def encode(result: FfufResult):
    raise NotImplementedError("M2: the encoder is not built yet.")
