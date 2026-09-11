"""Extract the olfactory circuit from MaleCNS: PN -> KC -> MBON-alpha'3.

Every population is resolved by annotation on every run. No body ID is hardcoded, and the
near-miss names a prefix match would have swallowed are recorded so that a silent
mis-identification is impossible rather than merely unlikely.

Three things are worth stating before the numbers.

**The mushroom body is per-hemisphere.** Kenyon cells receive from projection neurons on
their own side, so the circuit is extracted for one hemisphere and the other is reported
beside it. Pooling them would inflate the Kenyon cell count and halve the apparent fan-in.

**MaleCNS names MBONs numerically.** `type` gives `MBON16`, not `MBON-alpha'3ap`. The
compartment is in `instance` - `MBON16(a'3ap)` - so alpha'3 is matched there. Two further
cells touch alpha'3 partially, `MBON28(a'3a)` and `MBON17-like(a'2a'3)`; they are reported
as adjacent and are not folded into the readout.

**Differences from the published figures are results, not bugs.** The FlyHash papers assume
~50 receptor channels, ~2,000 Kenyon cells and ~6 projection neurons converging on each.
What MaleCNS measures is written down as measured.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
import scipy.sparse as sp

from flypaper.brain import load
from flypaper.brain import schema as S

__all__ = ["Circuit", "extract", "resolve_populations"]


@dataclass
class Populations:
    """Who is who, resolved by annotation."""

    side: str
    pn: pd.DataFrame
    kc: pd.DataFrame
    mbon_alpha3: pd.DataFrame
    mbon_alpha3_adjacent: pd.DataFrame
    glomeruli: list[str]
    near_misses: dict[str, list[str]] = field(default_factory=dict)


@dataclass
class Circuit:
    """The extracted circuit, plus everything needed to judge it."""

    side: str
    glomeruli: list[str]
    pn_bodies: np.ndarray
    kc_bodies: np.ndarray
    mbon_bodies: np.ndarray
    #: glomerulus x Kenyon cell synapse counts, the measured projection
    pn_to_kc: sp.csr_matrix
    #: Kenyon cell x MBON-alpha'3 synapse counts, the measured readout
    kc_to_mbon: sp.csr_matrix
    stats: dict = field(default_factory=dict)

    @property
    def n_channels(self) -> int:
        return len(self.glomeruli)

    @property
    def n_kc(self) -> int:
        return len(self.kc_bodies)

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        sp.save_npz(path.with_suffix(".pn_to_kc.npz"), self.pn_to_kc)
        sp.save_npz(path.with_suffix(".kc_to_mbon.npz"), self.kc_to_mbon)
        path.with_suffix(".json").write_text(
            json.dumps(
                {
                    "side": self.side,
                    "glomeruli": self.glomeruli,
                    "pn_bodies": self.pn_bodies.tolist(),
                    "kc_bodies": self.kc_bodies.tolist(),
                    "mbon_bodies": self.mbon_bodies.tolist(),
                    "stats": self.stats,
                },
                indent=2,
            )
        )

    @classmethod
    def load(cls, path: str | Path) -> Circuit:
        path = Path(path)
        meta = json.loads(path.with_suffix(".json").read_text())
        return cls(
            side=meta["side"],
            glomeruli=meta["glomeruli"],
            pn_bodies=np.array(meta["pn_bodies"], dtype=np.int64),
            kc_bodies=np.array(meta["kc_bodies"], dtype=np.int64),
            mbon_bodies=np.array(meta["mbon_bodies"], dtype=np.int64),
            pn_to_kc=sp.load_npz(path.with_suffix(".pn_to_kc.npz")),
            kc_to_mbon=sp.load_npz(path.with_suffix(".kc_to_mbon.npz")),
            stats=meta["stats"],
        )


def _glomerulus_of(type_name: str) -> str | None:
    """The glomerulus a uniglomerular PN reports from, or None if it is not one.

    A uniglomerular antennal-lobe PN's type begins with its glomerulus and a tract suffix -
    `DA1_lPN`, `DM1_adPN`. Multiglomerular and central-brain types do not name one and must
    not be forced into a channel.
    """
    if not isinstance(type_name, str) or not type_name:
        return None
    head = type_name.split("_")[0]
    if head.startswith(S.NON_GLOMERULAR_PREFIXES):
        return None
    if any(marker in head for marker in S.MULTIGLOMERULAR_MARKERS):
        return None
    if head in ("M", "MZ", "V") and type_name.startswith(("M_", "MZ_")):
        return None
    return head or None


def resolve_populations(side: str = "R", ann: pd.DataFrame | None = None) -> Populations:
    """Resolve PN, KC and MBON-alpha'3 for one hemisphere, by annotation."""
    if ann is None:
        ann = load.load_annotations(
            [S.A_BODY, S.A_TYPE, S.A_INSTANCE, S.A_CLASS, S.A_SOMA_SIDE, S.A_STATUS]
        )
    cls = ann[S.A_CLASS].astype("string")
    this_side = ann[S.A_SOMA_SIDE].astype("string") == side

    kc = ann[(cls == S.CLASS_KENYON_CELL) & this_side].copy()

    alpn = ann[(cls == S.CLASS_ALPN) & this_side].copy()
    alpn["glomerulus"] = alpn[S.A_TYPE].map(_glomerulus_of)
    pn = alpn[alpn["glomerulus"].notna()].copy()

    mbon = ann[(cls == S.CLASS_MBON) & this_side].copy()
    instance = mbon[S.A_INSTANCE].astype("string")
    alpha3 = mbon[instance.str.contains(S.MBON_ALPHA3_INSTANCE, regex=True, na=False)]
    adjacent = mbon[instance.str.contains(S.MBON_ALPHA3_ADJACENT_INSTANCE, regex=True, na=False)]

    near_misses = {
        # Types excluded from the receptor channels, so a later prefix match cannot quietly
        # swallow them.
        "alpn_not_uniglomerular": sorted(
            set(alpn.loc[alpn["glomerulus"].isna(), S.A_TYPE].dropna())
        ),
        "mbon_adjacent_to_alpha3": sorted(set(adjacent[S.A_INSTANCE].dropna())),
        "mbon_other": sorted(set(mbon[S.A_TYPE].dropna()) - set(alpha3[S.A_TYPE].dropna())),
    }

    return Populations(
        side=side,
        pn=pn,
        kc=kc,
        mbon_alpha3=alpha3,
        mbon_alpha3_adjacent=adjacent,
        glomeruli=sorted(set(pn["glomerulus"].dropna())),
        near_misses=near_misses,
    )


def _edges_between(weights: pd.DataFrame, pre: np.ndarray, post: np.ndarray) -> pd.DataFrame:
    pre_sorted, post_sorted = np.sort(pre), np.sort(post)
    a = weights[S.W_PRE].to_numpy(dtype=np.int64)
    b = weights[S.W_POST].to_numpy(dtype=np.int64)
    ia = np.searchsorted(pre_sorted, a).clip(max=len(pre_sorted) - 1)
    ib = np.searchsorted(post_sorted, b).clip(max=len(post_sorted) - 1)
    mask = (pre_sorted[ia] == a) & (post_sorted[ib] == b)
    return weights[mask]


def extract(side: str = "R", *, weights: pd.DataFrame | None = None) -> Circuit:
    """Build the measured circuit for one hemisphere.

    Channels are glomeruli, not individual projection neurons: several PNs report from the
    same glomerulus, and it is the glomerulus that corresponds to a receptor type in the
    FlyHash model's ~50 input channels. PN synapse counts are summed within a glomerulus.
    """
    pops = resolve_populations(side)
    if weights is None:
        weights = load.load_weights([S.W_PRE, S.W_POST, S.W_WEIGHT], traced_only=True)

    pn_bodies = pops.pn[S.A_BODY].to_numpy(dtype=np.int64)
    kc_bodies = np.sort(pops.kc[S.A_BODY].to_numpy(dtype=np.int64))
    mbon_bodies = np.sort(pops.mbon_alpha3[S.A_BODY].to_numpy(dtype=np.int64))

    body_to_glom = dict(zip(pn_bodies, pops.pn["glomerulus"], strict=True))
    glom_index = {g: i for i, g in enumerate(pops.glomeruli)}
    kc_index = {int(b): i for i, b in enumerate(kc_bodies)}
    mbon_index = {int(b): i for i, b in enumerate(mbon_bodies)}

    pn_kc_edges = _edges_between(weights, pn_bodies, kc_bodies)
    rows = np.array([glom_index[body_to_glom[p]] for p in pn_kc_edges[S.W_PRE]], dtype=np.int64)
    cols = np.array([kc_index[k] for k in pn_kc_edges[S.W_POST]], dtype=np.int64)
    data = pn_kc_edges[S.W_WEIGHT].to_numpy(dtype=np.float64)
    pn_to_kc = sp.csr_matrix((data, (rows, cols)), shape=(len(pops.glomeruli), len(kc_bodies)))

    kc_mbon_edges = _edges_between(weights, kc_bodies, mbon_bodies)
    kc_to_mbon = sp.csr_matrix(
        (
            kc_mbon_edges[S.W_WEIGHT].to_numpy(dtype=np.float64),
            (
                [kc_index[k] for k in kc_mbon_edges[S.W_PRE]],
                [mbon_index[m] for m in kc_mbon_edges[S.W_POST]],
            ),
        ),
        shape=(len(kc_bodies), max(len(mbon_bodies), 1)),
    )

    # Fan-in: how many distinct glomeruli reach each Kenyon cell. The published model says
    # about six, drawn at random.
    binary = (pn_to_kc > 0).astype(np.int8)
    fan_in = np.asarray(binary.sum(axis=0)).ravel()
    connected = fan_in > 0

    stats = {
        "side": side,
        "glomeruli": len(pops.glomeruli),
        "pn_bodies": int(len(pn_bodies)),
        "kc_bodies": int(len(kc_bodies)),
        "kc_receiving_pn_input": int(connected.sum()),
        "mbon_alpha3_bodies": int(len(mbon_bodies)),
        "mbon_alpha3_instances": sorted(pops.mbon_alpha3[S.A_INSTANCE].dropna()),
        "pn_to_kc_connections": int(binary.nnz),
        "pn_to_kc_synapses": int(pn_to_kc.sum()),
        "kc_to_mbon_connections": int((kc_to_mbon > 0).nnz),
        "kc_to_mbon_synapses": int(kc_to_mbon.sum()),
        "fan_in_mean": float(fan_in[connected].mean()) if connected.any() else 0.0,
        "fan_in_median": float(np.median(fan_in[connected])) if connected.any() else 0.0,
        "fan_in_std": float(fan_in[connected].std()) if connected.any() else 0.0,
        "fan_in_min": int(fan_in[connected].min()) if connected.any() else 0,
        "fan_in_max": int(fan_in[connected].max()) if connected.any() else 0,
        "near_misses": pops.near_misses,
    }

    return Circuit(
        side=side,
        glomeruli=pops.glomeruli,
        pn_bodies=pn_bodies,
        kc_bodies=kc_bodies,
        mbon_bodies=mbon_bodies,
        pn_to_kc=pn_to_kc,
        kc_to_mbon=kc_to_mbon,
        stats=stats,
    )
