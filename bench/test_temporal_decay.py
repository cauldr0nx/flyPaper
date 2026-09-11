"""M3: the Fly Bloom Filter's time-sensitivity.

A baseline must age on its own, so a target scanned six months ago becomes gradually novel
again. This is the property a plain hash set can never have, and it is half the reason the
Fly Bloom Filter was chosen over one.
"""

import pytest

pytestmark = pytest.mark.skip(reason="M3: the Fly Bloom Filter is not implemented yet")


def test_familiarity_decays_with_time_since_last_encounter():
    raise NotImplementedError
