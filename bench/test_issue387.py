"""M4: the ffuf issue #387 scenario.

`-ac` derives size, word and line filters and applies them with OR logic, so a valid result
matching any one of them is hidden. It is the cleanest demonstration of why independent
per-field thresholds fail, and joint similarity across all fields at once should not hide
the same result.

Reproduced against the corpus, not asserted from the tracker.
"""

import pytest

pytestmark = pytest.mark.skip(reason="M4: ranking is not implemented yet")


def test_joint_similarity_does_not_hide_a_result_autocalibration_hides():
    raise NotImplementedError
