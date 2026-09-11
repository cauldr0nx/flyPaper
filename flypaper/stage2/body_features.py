"""Body-level features from a stage-two re-fetch. M5.

ffuf's own scraper output is consumed where present, because a feature we can read from
the stage-one stream is a request we do not have to send.
"""

from __future__ import annotations

__all__ = ["body_features"]


def body_features(*args, **kwargs):
    raise NotImplementedError("M5: body features are not implemented yet.")
