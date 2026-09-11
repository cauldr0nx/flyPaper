"""FlyHash: the sparse locality-sensitive tag.

Dasgupta, Stevens & Navlakha, *Science* 2017. About 50 receptor channels project onto about
2,000 Kenyon cells through a sparse, expansive, non-negative projection; a winner-take-all
inhibition then leaves only the strongest few percent active. The resulting tag is a
locality-sensitive hash - similar inputs get similar tags - and unlike a classical LSH it
expands rather than reduces dimensionality.

The published model draws that projection **at random**, because that is what the biology
looked like statistically when the paper was written. MaleCNS measures it instead. Three
projections are implemented so the difference can be attributed:

`random`
    The published baseline. Every Kenyon cell samples the same number of channels,
    uniformly at random.

`random-matched`
    A degree-preserving null. Each Kenyon cell keeps **its own measured fan-in** but draws
    its channels uniformly at random. This is the control that matters: if `connectome`
    beats `random` it could just be the fan-in *distribution* doing the work, and this
    separates that from the wiring itself.

`connectome`
    The measured MaleCNS projection, synapse counts and all.

A comparison against only the uniform baseline would not be able to tell structure from
degree, so all three are reported.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import scipy.sparse as sp

__all__ = [
    "FlyHash",
    "connectome_projection",
    "random_matched_projection",
    "random_projection",
]


def _as_matrix(x: np.ndarray) -> np.ndarray:
    x = np.asarray(x, dtype=np.float32)
    return x[None, :] if x.ndim == 1 else x


def random_projection(
    n_channels: int, n_kc: int, fan_in: int = 6, *, rng: np.random.Generator | None = None
) -> sp.csr_matrix:
    """The published baseline: uniform fan-in, targets drawn at random."""
    rng = rng or np.random.default_rng(0)
    fan_in = min(fan_in, n_channels)
    rows = np.concatenate([rng.choice(n_channels, fan_in, replace=False) for _ in range(n_kc)])
    cols = np.repeat(np.arange(n_kc), fan_in)
    return sp.csr_matrix(
        (np.ones(len(rows), dtype=np.float32), (rows, cols)), shape=(n_channels, n_kc)
    )


def random_matched_projection(
    measured: sp.csr_matrix, *, rng: np.random.Generator | None = None
) -> sp.csr_matrix:
    """Degree-preserving null: the measured per-Kenyon-cell fan-in, random targets.

    Weights are not preserved - only the number of channels each cell listens to. What this
    isolates is whether *which* channels converge matters, given that *how many* do is held
    fixed at the measured value.
    """
    rng = rng or np.random.default_rng(0)
    n_channels, n_kc = measured.shape
    degrees = np.asarray((measured > 0).sum(axis=0)).ravel()
    rows, cols = [], []
    for kc, degree in enumerate(degrees):
        if degree == 0:
            continue
        picked = rng.choice(n_channels, min(int(degree), n_channels), replace=False)
        rows.append(picked)
        cols.append(np.full(len(picked), kc))
    if not rows:
        return sp.csr_matrix(measured.shape, dtype=np.float32)
    rows, cols = np.concatenate(rows), np.concatenate(cols)
    return sp.csr_matrix((np.ones(len(rows), dtype=np.float32), (rows, cols)), shape=measured.shape)


def connectome_projection(measured: sp.csr_matrix, *, binary: bool = False) -> sp.csr_matrix:
    """The measured projection. `binary` drops synapse counts and keeps only who connects."""
    matrix = (measured > 0).astype(np.float32) if binary else measured.astype(np.float32)
    return sp.csr_matrix(matrix)


@dataclass
class FlyHash:
    """Sparse expansive projection plus winner-take-all.

    `sparsity` is the fraction of Kenyon cells left active - the papers use 0.05, giving the
    ~95% zeros that make the tag sparse.
    """

    projection: sp.csr_matrix
    sparsity: float = 0.05
    #: Divisive normalisation before projecting, as in the paper: the antennal lobe
    #: subtracts the mean so that tag identity depends on the odour's shape, not its
    #: concentration. Without it a louder version of the same input gets a different tag.
    normalise: bool = True

    @property
    def n_channels(self) -> int:
        return self.projection.shape[0]

    @property
    def n_kc(self) -> int:
        return self.projection.shape[1]

    @property
    def n_active(self) -> int:
        return max(1, int(round(self.sparsity * self.n_kc)))

    def activations(self, x: np.ndarray) -> np.ndarray:
        x = _as_matrix(x)
        if self.normalise:
            x = x - x.mean(axis=1, keepdims=True)
        return np.asarray(x @ self.projection, dtype=np.float32)

    def tag(self, x: np.ndarray) -> np.ndarray:
        """Binary tag: 1 for the top `n_active` Kenyon cells, 0 elsewhere."""
        scores = self.activations(x)
        k = self.n_active
        idx = np.argpartition(-scores, kth=k - 1, axis=1)[:, :k]
        out = np.zeros_like(scores, dtype=np.float32)
        np.put_along_axis(out, idx, 1.0, axis=1)
        return out

    def tag_valued(self, x: np.ndarray) -> np.ndarray:
        """Tag keeping the winners' activation values. What the Bloom filter writes with."""
        scores = self.activations(x)
        k = self.n_active
        idx = np.argpartition(-scores, kth=k - 1, axis=1)[:, :k]
        out = np.zeros_like(scores, dtype=np.float32)
        np.put_along_axis(out, idx, np.take_along_axis(scores, idx, axis=1), axis=1)
        return np.maximum(out, 0.0)

    def measured_sparsity(self, x: np.ndarray) -> float:
        tags = self.tag(x)
        return float(1.0 - tags.mean())
