"""Extracting PN, KC and MBON-alpha'3 from MaleCNS. M3.

Reuses `flypaper.brain.load` for the tables and the global adjacency. The gate requires
that if the MaleCNS annotation does not cleanly identify alpha'3, that is said out loud
and the substitute documented - never a quiet pick of something adjacent.

Measured statistics (PN->KC fan-in per KC, KC count, resulting sparsity) are recorded
against the published values (~50 ORN types, ~2,000 KCs, ~95% sparsity). Differences are
results, not bugs.
"""

from __future__ import annotations

__all__ = ["extract"]


def extract():
    raise NotImplementedError("M3: circuit extraction is not implemented yet.")
