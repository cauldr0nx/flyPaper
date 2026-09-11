"""Licensing audit, enforced by the test suite.

The README license matrix must contain no unresolved entry. This test parses it and fails
the build if one appears, so the audit is a check rather than a claim.
"""

from __future__ import annotations

import re

import pytest

from flypaper import REPO_ROOT

README = REPO_ROOT / "README.md"
UNRESOLVED = re.compile(r"\b(unknown|tbd|to be determined|\?\?\?|verify me)\b", re.IGNORECASE)


def matrix_rows() -> list[list[str]]:
    text = README.read_text(encoding="utf-8")
    start = text.index("<!-- LICENSE-MATRIX-START -->")
    end = text.index("<!-- LICENSE-MATRIX-END -->")
    rows = []
    for line in text[start:end].splitlines():
        line = line.strip()
        if not line.startswith("|") or re.fullmatch(r"\|[\s|:-]+\|", line):
            continue
        rows.append([c.strip() for c in line.strip("|").split("|")])
    return rows


def test_matrix_is_present_and_populated():
    rows = matrix_rows()
    assert len(rows) >= 20, f"license matrix looks truncated: {len(rows)} rows"


def test_no_unresolved_license_entries():
    for cells in matrix_rows():
        for cell in cells:
            assert not UNRESOLVED.search(cell), f"unresolved license entry: {cells}"


def test_no_empty_license_column():
    """Every component row names a license."""
    for cells in matrix_rows():
        if cells[0] in {"Component", "Package"}:
            continue
        license_cell = cells[2] if len(cells) == 4 else cells[1]
        assert license_cell, f"row has no license: {cells}"


def test_unlicensed_upstreams_state_their_handling():
    """A repository with no license is a real constraint, not a blank to be glossed over."""
    for repo in ("ffufme", "ffufPostprocessing", "ffufw", "FFUF-Workflow-Tool"):
        row = next(c for c in matrix_rows() if repo in c[0])
        assert "No license file published" in row[2], row
        assert "never vendored" in row[3] or "No code reuse" in row[3], row


def test_gpl_sources_are_marked_excluded():
    """GPL upstreams must never be vendored into an MIT repository."""
    row = next(c for c in matrix_rows() if "eonsystemspbc/fly-brain" in c[0])
    assert "GPL" in row[2], row
    assert "Excluded" in row[3], row


@pytest.mark.parametrize(
    "phrase",
    [
        "The Male CNS dataset is licensed under CC-BY.",
        "creativecommons.org/licenses/by/4.0/",
        "10.1016/j.cell.2026.08.015",
        "Dasgupta, Stevens & Navlakha",
        "Dasgupta, Sheehan, Stevens & Navlakha",
    ],
)
def test_data_license_and_citations_are_recorded(phrase):
    """CC-BY applies to the data regardless of our MIT code license."""
    assert phrase in README.read_text(encoding="utf-8")


def test_readme_states_the_known_limitations():
    text = README.read_text(encoding="utf-8")
    for required in ("Baseline poisoning", "Encoder sensitivity", "Ranking, not detection"):
        assert required in text, f"README no longer states: {required!r}"


def test_manifest_carries_the_data_terms_verbatim():
    import json

    manifest = json.loads((REPO_ROOT / "data" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["license"] == "CC-BY 4.0"
    assert manifest["license_text_verbatim"] == "The Male CNS dataset is licensed under CC-BY."
    assert "Janelia" in manifest["attribution"]
    assert "10.1016/j.cell.2026.08.015" in manifest["citation"]
    for entry in manifest["files"]:
        assert entry["sha256"], f"{entry['name']} has no pinned SHA256"
