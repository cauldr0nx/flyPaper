"""M3: connectome-wired FlyHash against the published random-projection baseline.

The scientific spine of the project. Benchmarked on the standard datasets used in the
FlyHash / Fly Bloom Filter literature, with their metrics. The gate is that the connectome
version at minimum matches the random-projection baseline.

If it is worse, that is a real result: it gets reported in
reports/m3-flyhash-benchmark.md and the work stops for review. The tool can still ship on
random projection; what changes is the claim it is allowed to make.
"""

import pytest

pytestmark = pytest.mark.skip(reason="M3: FlyHash is not implemented yet")


def test_connectome_flyhash_matches_random_projection_baseline():
    raise NotImplementedError
