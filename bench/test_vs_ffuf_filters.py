"""M4: ranking measured against ffuf's own filters.

Against (a) `-ac` autocalibration and (b) a competent operator's hand-tuned `-fs`/`-fc`/
`-fw` config, on the same corpus with the same labels. Metrics: precision@10,
precision@50, recall of labelled-interesting items, and items-reviewed-to-first-hit.

Losing is an acceptable outcome and gets reported plainly. Tuning thresholds until it wins
would invalidate the corpus.
"""

import pytest

pytestmark = pytest.mark.skip(reason="M4: ranking is not implemented yet")


def test_ranking_against_autocalibration():
    raise NotImplementedError


def test_ranking_against_hand_tuned_filters():
    raise NotImplementedError
