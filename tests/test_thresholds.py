"""Every threshold cites a paper or a measurement, and matches the code.

The brief asks for this file specifically: `thresholds.yaml` where "each threshold cites a
paper or a measurement". A documentation file that drifts from the code it documents is
worse than no file, so the values are checked against the code that uses them.
"""

from __future__ import annotations

import yaml

from flypaper import REPO_ROOT

THRESHOLDS = REPO_ROOT / "bench" / "thresholds.yaml"
VALID_SOURCES = {"paper", "measurement", "domain"}


def load() -> dict:
    return yaml.safe_load(THRESHOLDS.read_text(encoding="utf-8"))


def entries():
    for group, items in load().items():
        for name, entry in items.items():
            yield f"{group}.{name}", entry


def test_the_file_is_populated():
    """It shipped empty at M0 and stayed that way for several milestones."""
    assert sum(1 for _ in entries()) >= 12


def test_every_threshold_names_a_source_and_a_reason():
    for key, entry in entries():
        assert entry.get("source") in VALID_SOURCES, f"{key}: source={entry.get('source')!r}"
        assert entry.get("rationale", "").strip(), f"{key} has no rationale"
        assert entry.get("value") is not None, f"{key} has no value"


def test_paper_thresholds_cite_a_paper():
    for key, entry in entries():
        if entry["source"] == "paper":
            assert "cite" in entry, f"{key} claims a paper and names none"
            assert any(y in entry["cite"] for y in ("2017", "2018", "2024", "2026")), entry["cite"]


def test_measured_thresholds_cite_where_they_were_measured():
    for key, entry in entries():
        if entry["source"] == "measurement":
            cite = entry.get("cite", "")
            assert cite.startswith(("reports/", "bench/")), f"{key}: {cite!r}"
            # A citation may point at a section: "reports/x.md section 2".
            path = cite.split()[0]
            assert (REPO_ROOT / path).exists(), f"{key} cites {path}, which does not exist"


# --- and the numbers match the code ---------------------------------------------------------


def test_published_constants_match_the_defaults():
    from flypaper.rank.score import DEFAULTS

    published = load()["published"]
    assert DEFAULTS["sparsity"] == published["tag_sparsity"]["value"]
    assert DEFAULTS["fan_in"] == published["pn_to_kc_fan_in"]["value"]
    assert DEFAULTS["learning_rate"] == load()["ranking"]["learning_rate"]["value"]


def test_safety_ceilings_match_the_code():
    import sys
    from pathlib import Path

    sys.path.insert(0, str(REPO_ROOT / "bench"))
    import live_probe

    from flypaper.stage2.refetch import MAX_RATE

    safety = load()["safety"]
    assert MAX_RATE == safety["stage_two_max_rate"]["value"]
    assert live_probe.MAX_RATE == safety["live_capture_rate"]["value"]
    assert live_probe.MAX_REQUESTS == safety["live_capture_requests"]["value"]
    assert live_probe.REFUSAL_JUMP == safety["block_refusal_jump"]["value"]
    assert Path(live_probe.__file__).exists()


def test_gate_and_display_constants_match_the_code():
    import sys

    sys.path.insert(0, str(REPO_ROOT / "bench"))
    import separation

    gate = load()["gate"]
    assert separation.DEFAULT_K == gate["knn_k"]["value"]

    from flypaper.rank.report import RollingPercentile

    assert RollingPercentile().percentile == load()["ranking"]["display_percentile"]["value"]


def test_the_measured_circuit_numbers_are_recorded_as_measured_not_assumed():
    """The published figure and what MaleCNS actually says are both written down."""
    published = load()["published"]
    for key in ("orn_channels", "kenyon_cells", "pn_to_kc_fan_in"):
        assert "measured" in published[key], f"{key} records no measured value"
        assert published[key]["measured"] != published[key]["value"], (
            f"{key}: measured and published are identical, which would be a suspicious "
            f"coincidence rather than a result"
        )
