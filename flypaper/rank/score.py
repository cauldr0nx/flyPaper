"""Novelty -> ranking. M4.

Measured against ffuf's own `-ac` autocalibration and a hand-tuned `-fs`/`-fc`/`-fw`
config on the same corpus with the same labels. Losing is an acceptable outcome and gets
reported plainly; tuning thresholds against the eval set would invalidate the eval set.
"""

from __future__ import annotations

__all__ = ["score"]


def score(*args, **kwargs):
    raise NotImplementedError("M4: ranking is not implemented yet.")
