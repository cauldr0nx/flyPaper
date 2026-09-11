"""How far a labelled hit sits from the familiar, measured without assuming one cluster.

The first version of this divided the hit's distance from the noise centroid by the noise's
mean pairwise spread. That is a fine measure when the noise is one population and a
meaningless one when it is several: on a surface with four populations the "spread" is
dominated by the gaps *between* them, so a perfectly collapsed encoding scores badly. On
`bench-mixed` each population has a within-cluster spread of 0.014-0.025 while sitting
1.5-2.2 apart, and the old metric reported that as a failure.

So separation is measured locally instead. For any point, the mean distance to its `k`
nearest noise neighbours describes the neighbourhood it is sitting in. Compare a hit's to
the typical noise point's, and the ratio says how much emptier the hit's neighbourhood is -
which is what the Bloom filter actually responds to, and is indifferent to how many
populations the noise is made of.
"""

from __future__ import annotations

import numpy as np

__all__ = ["knn_separation", "local_scale"]

DEFAULT_K = 10
DEFAULT_SAMPLE = 700


def _knn_mean(distances: np.ndarray, k: int) -> np.ndarray:
    k = min(k, max(distances.shape[1] - 1, 1))
    return np.sort(distances, axis=1)[:, :k].mean(axis=1)


def _pairwise(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    # (a-b)^2 = a^2 - 2ab + b^2, which is far cheaper than broadcasting the difference.
    d2 = (a**2).sum(1)[:, None] - 2 * a @ b.T + (b**2).sum(1)[None, :]
    return np.sqrt(np.maximum(d2, 0.0))


def local_scale(
    noise: np.ndarray, k: int = DEFAULT_K, sample: int = DEFAULT_SAMPLE, seed: int = 0
) -> tuple[float, np.ndarray]:
    """(typical neighbourhood radius of the noise, the sampled noise points).

    The median is used rather than the mean so one sparse outlier in the noise cannot
    inflate the scale everything else is judged against.
    """
    rng = np.random.default_rng(seed)
    if len(noise) > sample:
        noise = noise[rng.choice(len(noise), sample, replace=False)]
    distances = _pairwise(noise, noise)
    np.fill_diagonal(distances, np.inf)
    return float(np.median(_knn_mean(distances, k))), noise


def knn_separation(
    hits: np.ndarray,
    noise: np.ndarray,
    k: int = DEFAULT_K,
    sample: int = DEFAULT_SAMPLE,
    seed: int = 0,
) -> np.ndarray:
    """Each hit's neighbourhood radius, in units of the noise's own.

    1.0 means the hit sits in a neighbourhood as crowded as the noise's - indistinguishable.
    Large means it is alone.
    """
    scale, sampled = local_scale(noise, k, sample, seed)
    hit_knn = _knn_mean(_pairwise(np.atleast_2d(hits), sampled), k)
    return hit_knn / max(scale, 1e-12)
