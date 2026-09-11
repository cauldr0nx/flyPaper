"""Column and value names of the MaleCNS flat-connectome tables, in one place.

The download page documents the files but not their schemas, so every column name below
was read off the Arrow schema of the actual v1.0 tables. Keeping them here means a naming
change in a future release is a one-file fix rather than a grep across the package.

Ported from the flyDash MaleCNS pipeline (same author, MIT).
"""

from __future__ import annotations

import os
from pathlib import Path

from flypaper import REPO_ROOT

# The tables are 31 GB at the full tier and are commonly already on disk from another
# checkout. `FLYPAPER_RAW_DIR` points at that copy instead of forcing a second download;
# `python data/fetch.py --verify-only` then proves the bytes match the pinned digests.
RAW_DIR = Path(os.environ.get("FLYPAPER_RAW_DIR") or REPO_ROOT / "data" / "raw")
DERIVED_DIR = Path(os.environ.get("FLYPAPER_DERIVED_DIR") or REPO_ROOT / "data" / "derived")

# --- file names ------------------------------------------------------------------------

ANNOTATIONS = "body-annotations-male-cns-v1.0-minconf-0.5.feather"
NEUROTRANSMITTERS = "body-neurotransmitters-male-cns-v1.0.feather"
WEIGHTS = "connectome-weights-male-cns-v1.0-minconf-0.5.feather"
WEIGHTS_TRACED = "connectome-weights-male-cns-v1.0-minconf-0.5-traced-only.feather"
SYN_PARTNERS = "syn-partners-male-cns-v1.0-minconf-0.5.feather"
BODY_STATS = "body-stats-male-cns-v1.0-minconf-0.5.feather"

# --- body-annotations ------------------------------------------------------------------

A_BODY = "bodyId"
A_TYPE = "type"
A_INSTANCE = "instance"
A_SUPERCLASS = "superclass"
A_CLASS = "class"
A_STATUS = "status"
A_STATUS_LABEL = "statusLabel"
A_SOMA_SIDE = "somaSide"
A_ROOT_SIDE = "rootSide"

# A body counts as a neuron when it has been assigned a superclass. This is the definition
# that reproduces the published headline count; the alternatives are tabulated by
# `load.count_definitions()` rather than hidden behind this one choice.
NEURON_PREDICATE = A_SUPERCLASS
STATUS_TRACED = "Traced"

# --- body-neurotransmitters ------------------------------------------------------------

NT_BODY = "body"
NT_PREDICTED = "predicted_nt"
NT_CONFIDENCE = "predicted_nt_confidence"
NT_GROUND_TRUTH = "ground_truth"
NT_CONSENSUS = "consensus_nt"

# --- connectome-weights ----------------------------------------------------------------

W_PRE = "body_pre"
W_POST = "body_post"
W_WEIGHT = "weight"

# --- the olfactory circuit ---------------------------------------------------------------

# NOT RESOLVED YET. M3 resolves the projection neuron, Kenyon cell and MBON-alpha'3
# populations by annotation, records the near-miss type names a prefix match would have
# swallowed, and writes the measured statistics against the published ~50 / ~2,000 / ~95%.
# If MaleCNS annotation does not cleanly identify alpha'3, the substitute is documented in
# reports/m3-flyhash-benchmark.md rather than quietly chosen here.
CIRCUIT_TYPES: tuple[str, ...] = ()


def raw_path(filename: str) -> Path:
    path = RAW_DIR / filename
    if not path.exists():
        raise FileNotFoundError(
            f"{path} is missing. Fetch the data first:\n"
            f"    python data/fetch.py --tier core\n"
            f"or point FLYPAPER_RAW_DIR at an existing copy of the tables."
        )
    return path
