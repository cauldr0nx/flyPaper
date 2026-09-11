"""FlyHash: the sparse locality-sensitive tag. M3.

Dasgupta, Stevens & Navlakha, Science 2017: ~50 receptor types project onto ~2,000 Kenyon
cells, and a winner-take-all leaves roughly 5% of them active. The published model draws
the projection at random because that is what the biology looked like statistically at the
time. MaleCNS gives the measured wiring instead.

Both are implemented here and benchmarked against each other. If the connectome version
loses, that is a real result: it gets reported, and the tool ships on random projection
with a correspondingly narrower claim.
"""

from __future__ import annotations

__all__ = ["ConnectomeFlyHash", "RandomProjectionFlyHash"]


class RandomProjectionFlyHash:
    """The published baseline: a random sparse binary projection."""

    def __init__(self, *args, **kwargs):
        raise NotImplementedError("M3: FlyHash is not implemented yet.")


class ConnectomeFlyHash:
    """The measured MaleCNS PN->KC projection."""

    def __init__(self, *args, **kwargs):
        raise NotImplementedError("M3: FlyHash is not implemented yet.")
