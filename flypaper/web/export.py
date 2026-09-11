#!/usr/bin/env python3
"""Export the olfactory circuit's real geometry for the dashboard.

Every point the dashboard renders is a measured location in MaleCNS EM space, in 8 nm voxel
units converted to microns and centred on the mushroom body. Nothing here is decorative
geometry, and nothing is placed for looks.

Three clouds come out of this, and they are different things:

`calyx`
    Where projection neurons contact Kenyon cells. This is the input side - the ~50
    receptor channels fanning onto ~2,000 cells, which is the projection FlyHash models.

`alpha3`
    Where Kenyon cells contact MBON-alpha'3. **These synapses are the Bloom filter.** Each
    one is a weight that a familiar odour depresses, and the MBON's response to what is
    left is the novelty score. When the dashboard lights a synapse it is lighting the
    actual thing being read.

`kenyon`
    One point per Kenyon cell, at the centroid of its calyx input sites. These are the
    2,045 cells a tag selects 5% of.

    python -m flypaper.web.export

Needs the MaleCNS tables; see data/fetch.py, or point FLYPAPER_RAW_DIR at a copy.
"""

from __future__ import annotations

import json

import numpy as np
import pyarrow.compute as pc
import pyarrow.dataset as ds

from flypaper import REPO_ROOT, provenance
from flypaper.brain import schema as S
from flypaper.brain.extract import Circuit, extract

STATIC = REPO_ROOT / "flypaper" / "web" / "static"
DERIVED = REPO_ROOT / "data" / "derived"
CIRCUIT_PATH = DERIVED / "olfactory-circuit-R"
ANATOMY = STATIC / "anatomy.json"

VOXEL_NM = 8.0
SCALE = VOXEL_NM / 1000.0  # voxel units -> microns
MAX_CALYX_POINTS = 12000  # enough to read the shape; the full set is ~189k


def load_circuit() -> Circuit:
    if CIRCUIT_PATH.with_suffix(".json").exists():
        return Circuit.load(CIRCUIT_PATH)
    circuit = extract("R")
    circuit.save(CIRCUIT_PATH)
    return circuit


def _scan(pre_ids: np.ndarray, post_ids: np.ndarray) -> dict[str, np.ndarray]:
    """Pull the synapses between two body sets out of the 124 M-row partner table.

    Streamed in batches with a pushed-down filter rather than loaded whole: the traced-only
    table is 2.9 GB on disk and materialising it as pandas is not survivable on a small
    machine.
    """
    dataset = ds.dataset(S.raw_path(S.SYN_PARTNERS_TRACED), format="feather")
    condition = pc.field("body_pre").isin(pre_ids) & pc.field("body_post").isin(post_ids)
    table = dataset.to_table(
        columns=["body_pre", "body_post", "x_post", "y_post", "z_post"],
        filter=condition,
    )
    return {name: table[name].to_numpy(zero_copy_only=False) for name in table.column_names}


def export_anatomy() -> dict:
    circuit = load_circuit()
    kc = circuit.kc_bodies
    pn = circuit.pn_bodies
    mbon = circuit.mbon_bodies

    print(f"scanning PN->KC synapses ({len(pn)} PNs, {len(kc)} KCs)...", flush=True)
    calyx = _scan(pn, kc)
    print(f"  {len(calyx['body_post']):,} sites", flush=True)

    print(f"scanning KC->MBON-alpha'3 synapses ({len(mbon)} MBONs)...", flush=True)
    alpha3 = _scan(kc, mbon)
    print(f"  {len(alpha3['body_post']):,} sites", flush=True)

    calyx_xyz = np.stack([calyx["x_post"], calyx["y_post"], calyx["z_post"]], axis=1).astype(
        np.float64
    )
    alpha3_xyz = np.stack([alpha3["x_post"], alpha3["y_post"], alpha3["z_post"]], axis=1).astype(
        np.float64
    )

    # Centre on the whole mushroom body so the calyx and the lobe keep their real offset.
    centre = np.concatenate([calyx_xyz, alpha3_xyz]).mean(axis=0)

    # One point per Kenyon cell, at the centroid of its calyx input.
    index_of = {int(b): i for i, b in enumerate(kc)}
    sums = np.zeros((len(kc), 3))
    counts = np.zeros(len(kc))
    for body, point in zip(calyx["body_post"], calyx_xyz, strict=True):
        i = index_of[int(body)]
        sums[i] += point
        counts[i] += 1
    have = counts > 0
    kc_points = np.zeros((len(kc), 3))
    kc_points[have] = (sums[have] / counts[have, None] - centre) * SCALE

    rng = np.random.default_rng(0)
    pick = (
        rng.choice(len(calyx_xyz), MAX_CALYX_POINTS, replace=False)
        if len(calyx_xyz) > MAX_CALYX_POINTS
        else np.arange(len(calyx_xyz))
    )

    # Each alpha'3 synapse is tied to the Kenyon cell that makes it, so the dashboard can
    # light exactly the synapses a tag's active cells own.
    alpha3_kc = np.array([index_of[int(b)] for b in alpha3["body_pre"]], dtype=np.int32)

    payload = {
        "units": "micrometres, MaleCNS v1.0 EM space, centred on the mushroom body",
        # This file is a derivative of the MaleCNS dataset and is committed, unlike the
        # bulk tables. CC-BY permits that with attribution, so the attribution travels
        # inside the file rather than only in a document beside it.
        "source": {
            "dataset": "MaleCNS v1.0",
            "license": "CC BY 4.0",
            "license_text_verbatim": "The Male CNS dataset is licensed under CC-BY.",
            "license_url": "https://creativecommons.org/licenses/by/4.0/",
            "attribution": (
                "FlyEM Project Team (HHMI Janelia); University of Cambridge, Dept. of "
                "Zoology; MRC Laboratory of Molecular Biology; Google Research."
            ),
            "citation": "Berg et al., Cell, 2026. doi:10.1016/j.cell.2026.08.015",
        },
        "counts": {
            "kenyon_cells": int(len(kc)),
            "kenyon_cells_with_pn_input": int(have.sum()),
            "projection_neurons": int(len(pn)),
            "glomeruli": int(circuit.n_channels),
            "mbon_alpha3": int(len(mbon)),
            "calyx_synapses": int(len(calyx_xyz)),
            "alpha3_synapses": int(len(alpha3_xyz)),
        },
        "kenyon": [[round(float(v), 2) for v in p] for p in kc_points],
        "kenyon_has_input": have.astype(int).tolist(),
        "calyx": [[round(float(v), 2) for v in p] for p in (calyx_xyz[pick] - centre) * SCALE],
        "alpha3": [[round(float(v), 2) for v in p] for p in (alpha3_xyz - centre) * SCALE],
        "alpha3_kc": alpha3_kc.tolist(),
        "glomeruli": circuit.glomeruli,
        "stats": {k: v for k, v in circuit.stats.items() if k != "near_misses"},
        "provenance": provenance(),
    }
    return payload


def main() -> None:
    STATIC.mkdir(parents=True, exist_ok=True)
    payload = export_anatomy()
    ANATOMY.write_text(json.dumps(payload, separators=(",", ":")))
    print(f"wrote {ANATOMY.relative_to(REPO_ROOT)} ({ANATOMY.stat().st_size / 1e6:.1f} MB)")
    for key, value in payload["counts"].items():
        print(f"  {key:28} {value:,}")


if __name__ == "__main__":
    main()
