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

# --- the olfactory circuit -----------------------------------------------------------------

# MaleCNS annotates these populations directly in the `class` column, so nothing here is a
# guess at which cells are which.
CLASS_KENYON_CELL = "Kenyon_Cell"
CLASS_ALPN = "ALPN"  # antennal lobe projection neurons
CLASS_MBON = "MBON"
CLASS_OLFACTORY = "olfactory"  # olfactory receptor neurons
CLASS_DAN = "DAN"  # dopaminergic neurons - the reinforcement channel, M7

# MaleCNS names MBONs numerically (MBON01..MBON35), not by compartment, so the alpha'3
# identity is not readable from `type`. It *is* readable from `instance`, which carries the
# compartment in parentheses - `MBON16(a'3ap)`, `MBON17(a'3m)`. The Fly Bloom Filter paper
# reads novelty off MBON-alpha'3, which in Aso et al. 2014 nomenclature is MBON-a'3ap and
# MBON-a'3m; those are exactly these two types.
#
# The compartment is matched on `instance` rather than assumed from a remembered type
# number, and the adjacent cells that only partly innervate alpha'3 are listed separately
# rather than folded in. See reports/m3-flyhash-benchmark.md.
MBON_ALPHA3_INSTANCE = r"\(a'3(?:ap|m)\)"
MBON_ALPHA3_ADJACENT_INSTANCE = r"\(a'3a\)|\(a'2a'3\)"

# A uniglomerular antennal-lobe PN's type begins with its glomerulus. Multiglomerular PNs
# and central-brain types do not name one, and are excluded from the receptor channels.
NON_GLOMERULAR_PREFIXES = ("CB", "MZ")
MULTIGLOMERULAR_MARKERS = ("+",)


def raw_path(filename: str) -> Path:
    path = RAW_DIR / filename
    if not path.exists():
        raise FileNotFoundError(
            f"{path} is missing. Fetch the data first:\n"
            f"    python data/fetch.py --tier core\n"
            f"or point FLYPAPER_RAW_DIR at an existing copy of the tables."
        )
    return path
