"""Readers for the MaleCNS flat-connectome tables, plus the global sparse adjacency.

Tables are memory-mapped rather than copied. The full weights table is 151.9 M edges,
which is comfortable on a large machine and survivable on a small one only if it is not
duplicated.

Ported from the flyDash MaleCNS pipeline (same author, MIT).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pyarrow.feather as feather
import scipy.sparse as sp

from flypaper.brain import schema as S

__all__ = [
    "Adjacency",
    "build_adjacency",
    "count_definitions",
    "load_annotations",
    "load_neurotransmitters",
    "load_weights",
    "neuron_ids",
]


def _read(filename: str, columns: list[str] | None = None) -> pd.DataFrame:
    table = feather.read_table(S.raw_path(filename), columns=columns, memory_map=True)
    return table.to_pandas()


def load_annotations(columns: list[str] | None = None) -> pd.DataFrame:
    """Curated per-body annotations: type, instance, superclass, side, status."""
    return _read(S.ANNOTATIONS, columns)


def load_neurotransmitters(columns: list[str] | None = None) -> pd.DataFrame:
    """Per-body neurotransmitter predictions.

    These are ML predictions from EM image features, not measurements, except where
    `ground_truth` is populated.
    """
    return _read(S.NEUROTRANSMITTERS, columns)


def load_weights(columns: list[str] | None = None, *, traced_only: bool = True) -> pd.DataFrame:
    """The segment-to-segment connection graph.

    `traced_only` reads the 508 MB traced-only table, which is the `core` tier and the one
    the circuit extraction uses. The full 1.1 GB table includes untraced segments.
    """
    return _read(S.WEIGHTS_TRACED if traced_only else S.WEIGHTS, columns)


def neuron_ids(ann: pd.DataFrame | None = None) -> np.ndarray:
    """Body IDs that count as neurons.

    A body is a neuron when it carries an assigned superclass. Every alternative answer is
    tabulated by `count_definitions()` and reported rather than hidden behind this call.
    """
    if ann is None:
        ann = load_annotations([S.A_BODY, S.A_SUPERCLASS])
    return ann.loc[ann[S.A_SUPERCLASS].notna(), S.A_BODY].to_numpy(dtype=np.int64)


def count_definitions(ann: pd.DataFrame | None = None) -> dict[str, int]:
    """Every defensible answer to "how many neurons are there", with its filter spelled out.

    The definitions disagree by up to ~2,000 bodies, and that disagreement is itself the
    explanation for why a single published headline count is hard to reproduce exactly.
    """
    if ann is None:
        ann = load_annotations()
    status = ann[S.A_STATUS].astype("string")
    sc = ann[S.A_SUPERCLASS]
    typ = ann[S.A_TYPE]
    return {
        "rows in body-annotations (all annotated bodies)": len(ann),
        "has an assigned superclass": int(sc.notna().sum()),
        "has an assigned superclass and status == Traced": int(
            (sc.notna() & (status == S.STATUS_TRACED)).sum()
        ),
        "has an assigned superclass and a cell type": int((sc.notna() & typ.notna()).sum()),
        "status == Traced": int((status == S.STATUS_TRACED).sum()),
        "has an assigned superclass, excluding `_tbc` (to be confirmed)": int(
            (sc.notna() & ~sc.astype("string").str.endswith("_tbc", na=False)).sum()
        ),
        "excluding glia, orphans and unimportant": int(
            (~status.isin(["Glia", "Orphan", "Unimportant"]) & status.notna()).sum()
        ),
    }


class Adjacency:
    """The connection graph as CSR, with the body-ID <-> index mapping that built it."""

    def __init__(self, matrix: sp.csr_matrix, bodies: np.ndarray):
        self.matrix = matrix
        self.bodies = bodies
        self._index = {int(b): i for i, b in enumerate(bodies)}

    def __len__(self) -> int:
        return len(self.bodies)

    def index_of(self, body_id: int) -> int:
        return self._index[int(body_id)]

    @property
    def n_edges(self) -> int:
        return int(self.matrix.nnz)

    @property
    def total_weight(self) -> int:
        return int(self.matrix.data.sum())

    def save(self, path) -> None:
        sp.save_npz(str(path), self.matrix)
        np.save(str(path).replace(".npz", "-bodies.npy"), self.bodies)

    @classmethod
    def load(cls, path) -> Adjacency:
        matrix = sp.load_npz(str(path))
        bodies = np.load(str(path).replace(".npz", "-bodies.npy"))
        return cls(matrix, bodies)


def build_adjacency(
    weights: pd.DataFrame | None = None,
    restrict_to: np.ndarray | None = None,
) -> Adjacency:
    """Build a CSR adjacency from the edge table.

    `restrict_to` keeps only edges whose endpoints are both in that body-ID set - use
    `neuron_ids()` for the neuron-only graph, which is smaller than the full segment graph
    and is what the circuit work operates on.
    """
    if weights is None:
        weights = load_weights([S.W_PRE, S.W_POST, S.W_WEIGHT])

    pre = weights[S.W_PRE].to_numpy(dtype=np.int64)
    post = weights[S.W_POST].to_numpy(dtype=np.int64)
    data = weights[S.W_WEIGHT].to_numpy(dtype=np.int64)

    if restrict_to is not None:
        bodies = np.unique(np.asarray(restrict_to, dtype=np.int64))
        # searchsorted membership rather than np.isin: at 152 M edges the difference is
        # minutes, and `bodies` is already sorted by np.unique.
        n = len(bodies)
        rows = np.searchsorted(bodies, pre).clip(max=n - 1)
        cols = np.searchsorted(bodies, post).clip(max=n - 1)
        mask = (bodies[rows] == pre) & (bodies[cols] == post)
        rows, cols, data = rows[mask], cols[mask], data[mask]
    else:
        bodies = np.unique(np.concatenate([pre, post]))
        rows = np.searchsorted(bodies, pre)
        cols = np.searchsorted(bodies, post)

    n = len(bodies)
    matrix = sp.csr_matrix((data, (rows, cols)), shape=(n, n))
    return Adjacency(matrix, bodies)
