"""The Fly Bloom Filter. M3.

Dasgupta, Sheehan, Stevens & Navlakha, PNAS 115(51):13093, 2018. The KC -> MBON-alpha'3
synapses act as a Bloom filter over everything the fly has encountered. Unlike a
conventional Bloom filter it grades novelty by similarity to prior stimuli and by time
since last encounter.

Those two properties are the whole reason this beats a hash set: a response with a
rotating token hashes near the familiar cluster and stays quiet, and a baseline ages on
its own, so a target scanned six months ago becomes gradually novel again.
"""

from __future__ import annotations

__all__ = ["FlyBloomFilter"]


class FlyBloomFilter:
    def __init__(self, *args, **kwargs):
        raise NotImplementedError("M3: the Fly Bloom Filter is not implemented yet.")
