#!/usr/bin/env python3
"""Regenerate `data/manifest.json` from the MaleCNS Google Cloud Storage bucket.

The bucket is anonymously readable over plain HTTPS, and the GCS JSON API reports each
object's authoritative size and MD5 *before* any download. That gives `fetch.py` a real
upstream integrity anchor rather than a checksum we computed ourselves from bytes we
already trusted. `gsutil` is not required.

SHA256 cannot be known until a file has been fetched, so it starts as null and is pinned
by `fetch.py` on the first verified download. Run this script only to add files or to pick
up a new dataset release; the generated manifest is committed.
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from pathlib import Path

import requests

BUCKET = "flyem-male-cns"
PREFIX = "v1.0/connectome-data/flat-connectome/"
LIST_URL = f"https://storage.googleapis.com/storage/v1/b/{BUCKET}/o"
MANIFEST = Path(__file__).resolve().parent / "manifest.json"

# Tier assignment. `core` is everything the connectivity analysis strictly needs; `full`
# adds the per-synapse tables. The split is kept so the pipeline stays reproducible on a
# machine that cannot hold 31 GB, even though this project downloads `full`.
CORE = {
    "body-annotations-male-cns-v1.0-minconf-0.5.feather",
    "body-neurotransmitters-male-cns-v1.0.feather",
    "connectome-weights-male-cns-v1.0-minconf-0.5.feather",
}

DESCRIPTIONS = {
    "body-annotations-male-cns-v1.0-minconf-0.5.feather": (
        "Curated neuron annotations (classes, types, sides), excluding neurotransmitters. "
        "Source of the LC4 / LPLC2 / DNp01 cell-type identities."
    ),
    "body-neurotransmitters-male-cns-v1.0.feather": (
        "Aggregate neurotransmitter predictions per neuron. ML predictions from EM image "
        "features, NOT measurements - see HONESTY.md section 4."
    ),
    "body-stats-male-cns-v1.0-minconf-0.5.feather": (
        "Summary synapse-count statistics for all segments with at least one synapse."
    ),
    "connectome-weights-male-cns-v1.0-minconf-0.5.feather": (
        "Segment-to-segment connection strengths for all segments. The full connection graph."
    ),
    "connectome-weights-male-cns-v1.0-minconf-0.5-significant-only.feather": (
        "As above, restricted to statistically significant connections."
    ),
    "connectome-weights-male-cns-v1.0-minconf-0.5-traced-only.feather": (
        "As above, restricted to traced neurons."
    ),
    "syn-partners-male-cns-v1.0-minconf-0.5.feather": (
        "Synaptic partner pairs with body IDs and primary neuropil ('primary_post'). "
        "Source of the GF dendritic-compartment assignment."
    ),
    "syn-partners-male-cns-v1.0-minconf-0.5-significant-only.feather": (
        "As above, significant connections only."
    ),
    "syn-partners-male-cns-v1.0-minconf-0.5-traced-only.feather": (
        "As above, traced neurons only."
    ),
    "syn-points-male-cns-v1.0-minconf-0.5.feather": (
        "Pre- and post-synapse locations, body ID and encompassing ROIs. 8 nm voxel units."
    ),
    "tbar-neurotransmitters-male-cns-v1.0.feather": (
        "Neurotransmitter prediction probabilities per pre-synapse."
    ),
}


def list_objects() -> list[dict]:
    """List the flat-connectome prefix, following pagination."""
    items, token = [], None
    while True:
        params = {"prefix": PREFIX}
        if token:
            params["pageToken"] = token
        resp = requests.get(LIST_URL, params=params, timeout=60)
        resp.raise_for_status()
        payload = resp.json()
        items.extend(payload.get("items", []))
        token = payload.get("nextPageToken")
        if not token:
            return items


def main() -> None:
    existing = {}
    if MANIFEST.exists():
        existing = {f["name"]: f for f in json.loads(MANIFEST.read_text())["files"]}

    files = []
    for obj in sorted(list_objects(), key=lambda o: o["name"]):
        name = obj["name"].rsplit("/", 1)[-1]
        if not name:
            continue
        md5 = obj.get("md5Hash")
        files.append(
            {
                "name": name,
                "url": f"https://storage.googleapis.com/{BUCKET}/{obj['name']}",
                "gs_uri": f"gs://{BUCKET}/{obj['name']}",
                "size": int(obj["size"]),
                # Hex-encoded from the base64 the GCS API returns. None for objects
                # uploaded as composites, where GCS reports no whole-object MD5.
                "gcs_md5": base64.b64decode(md5).hex() if md5 else None,
                "gcs_crc32c": obj.get("crc32c"),
                "gcs_generation": obj.get("generation"),
                # Pinned by fetch.py on first verified download, then committed.
                "sha256": existing.get(name, {}).get("sha256"),
                "tier": "core" if name in CORE else "full",
                "description": DESCRIPTIONS.get(name, ""),
            }
        )

    manifest = {
        "dataset": "male-cns:v1.0",
        "released": "2026-06-08",
        "license": "CC-BY 4.0",
        "license_text_verbatim": "The Male CNS dataset is licensed under CC-BY.",
        "license_url": "https://creativecommons.org/licenses/by/4.0/",
        "source_page": "https://male-cns.janelia.org/download/",
        "attribution": (
            "FlyEM Project Team (HHMI Janelia); University of Cambridge, Dept. of Zoology; "
            "MRC Laboratory of Molecular Biology; Google Research."
        ),
        "citation": "Berg et al., Cell, 2026. doi:10.1016/j.cell.2026.08.015",
        "preprint": "doi:10.1101/2025.10.09.680999",
        "note": (
            "The CC-BY terms above govern this data regardless of the MIT license on this "
            "repository's code. Bulk data is never committed; this manifest and fetch.py are."
        ),
        "retrieved_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "bucket_prefix": f"gs://{BUCKET}/{PREFIX}",
        "excluded_tiers": {
            "em_volumes": (
                "gs://flyem_cns_z0720_07m_dvidcoords_n5 and gs://flyem-male-cns/em/* - "
                "TB-scale EM imagery, not needed for connectivity analysis."
            ),
            "segmentation": "gs://flyem-male-cns/v1.0/segmentation - voxel segmentation.",
            "skeletons": (
                "gs://flyem-male-cns/v1.0/segmentation/skeletons-malecns/* - neuron "
                "centerline skeletons. Useful for morphology; not needed for connectivity analysis."
            ),
            "neo4j": (
                "gs://flyem-male-cns/v1.0/database/neo4j - the neuprint database. Use the "
                "hosted neuprint instance instead."
            ),
        },
        "files": files,
    }
    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n")
    total = sum(f["size"] for f in files)
    print(f"wrote {MANIFEST} : {len(files)} files, {total / 1e9:.2f} GB")
    for f in files:
        print(f"  [{f['tier']:>4}] {f['size'] / 1e9:8.3f} GB  {f['name']}")


if __name__ == "__main__":
    main()
