"""The dashboard's data layer.

The page is only worth having if what it draws is real, so these tests are about
provenance: the replay must be the run that happened, and the anatomy must be measured
coordinates rather than anything invented for the picture.
"""

from __future__ import annotations

import base64
import json

import pytest

from flypaper import REPO_ROOT
from flypaper.ingest.ffuf import FfufResult
from flypaper.web.replay import record

STATIC = REPO_ROOT / "flypaper" / "web" / "static"


def response(word: str, length: int, *, status: int = 200):
    return FfufResult(
        inputs={"FUZZ": word},
        url=f"http://t/{word}",
        status=status,
        length=length,
        words=80,
        lines=12,
        content_type="text/html",
        duration_ms=1.0,
    )


def a_run(n: int = 300, odd_at: int = 200):
    return [
        response("treasure", 90_000) if i == odd_at else response(f"w{i}", 1000 + (i % 5))
        for i in range(n)
    ]


# --- the replay is the run that happened ---------------------------------------------------


def test_replay_records_every_response():
    payload = record(a_run(), hits={"treasure"}, warmup=20)
    assert payload["total_responses"] == 300
    assert len(payload["frames"]) == 300


def test_active_cells_match_the_declared_sparsity():
    """Each frame's Kenyon cells are the tag's winners, not a sample chosen for looks."""
    payload = record(a_run(120), warmup=10)
    for frame in payload["frames"]:
        assert len(frame["kc"]) == payload["n_active"]
        assert len(set(frame["kc"])) == len(frame["kc"])
        assert all(0 <= i < payload["n_kc"] for i in frame["kc"])


def weights_of(frame) -> list[float]:
    """The frame's synaptic weights, as the page decodes them."""
    return [b / 255 for b in base64.b64decode(frame["wq"])]


def test_frames_carry_one_weight_per_active_cell():
    """The page colours a synapse by its cell's weight, so the two must line up."""
    payload = record(a_run(120), warmup=10)
    for frame in payload["frames"]:
        weights = weights_of(frame)
        assert len(weights) == len(frame["kc"])
        assert all(0.0 <= w <= 1.0 for w in weights)


def test_a_cell_only_ever_darkens_within_a_run():
    """Depression is one-way without temporal decay, so a cell the page has dimmed must
    never brighten again. A page that showed one brightening would be showing an artifact of
    the recording rather than the filter."""
    payload = record(a_run(300), warmup=20)
    last: dict[int, float] = {}
    for frame in payload["frames"]:
        for cell, weight in zip(frame["kc"], weights_of(frame), strict=True):
            assert weight <= last.get(cell, 1.0) + 1 / 255
            last[cell] = weight
    assert min(last.values()) < 0.5, "nothing was depressed; the weights are not the filter's"


def test_the_recorded_scores_are_the_live_ones():
    """The replay must be the run that would have happened, to the last digit: the page is
    only worth having if its numbers are the tool's."""
    from flypaper.rank.score import Ranker

    results = a_run(200)
    live = [s.novelty for s in Ranker().stream(results)]
    recorded = record(results, warmup=20)["frames"]
    assert len(recorded) == len(live)
    for frame, novelty in zip(recorded, live, strict=True):
        assert frame["n"] == round(novelty, 5)


def test_the_odd_response_scores_far_above_the_rest():
    payload = record(a_run(), hits={"treasure"}, warmup=20)
    odd = next(f for f in payload["frames"] if f["w"] == "treasure")
    rest = [f["n"] for f in payload["frames"] if f["w"] != "treasure" and f["i"] > 40]
    assert odd["n"] > 4 * max(rest)
    assert odd["hit"] is True


def test_log_lines_carry_the_real_numbers():
    payload = record(a_run(80), warmup=5)
    frame = payload["frames"][-1]
    assert str(frame["s"]) in frame["log"]
    assert str(frame["len"]) in frame["log"]
    assert frame["w"] in frame["log"]
    assert f"{frame['n']:.3f}" in frame["log"]


def test_warmup_frames_are_not_surfaced():
    """The cold start is an artifact; the dashboard must not present it as a result."""
    payload = record(a_run(), warmup=50)
    assert all(not f["shown"] for f in payload["frames"] if f["i"] < 50)


def test_saturation_only_grows_without_decay():
    payload = record(a_run(200), warmup=10)
    sat = payload["saturation"]
    assert sat[0] <= sat[-1]
    assert all(b >= a - 1e-9 for a, b in zip(sat, sat[1:], strict=False))


def test_replay_carries_its_provenance():
    payload = record(a_run(40), warmup=5)
    assert payload["provenance"]["git_commit"]
    assert payload["channel_set"] and payload["projection"]


def test_replay_holds_no_response_bodies():
    """Stage one is metadata only, and so is anything built from it."""
    payload = record(a_run(40), warmup=5)
    blob = json.dumps(payload)
    assert "body" not in payload
    for frame in payload["frames"]:
        assert set(frame) == {
            "i",
            "n",
            "kc",
            "wq",
            "w",
            "s",
            "len",
            "wd",
            "ln",
            "ms",
            "hit",
            "shown",
            "log",
        }
    assert "<html" not in blob


# --- the anatomy is measured ------------------------------------------------------------------

ANATOMY = STATIC / "anatomy.json"
pytestmark_anatomy = pytest.mark.skipif(
    not ANATOMY.exists(), reason="anatomy.json not exported; run python -m flypaper.web.export"
)


@pytestmark_anatomy
def test_anatomy_counts_match_the_measured_circuit():
    a = json.loads(ANATOMY.read_text())
    counts = a["counts"]
    assert counts["kenyon_cells"] == len(a["kenyon"])
    assert counts["alpha3_synapses"] == len(a["alpha3"]) == len(a["alpha3_kc"])
    assert counts["glomeruli"] == len(a["glomeruli"])
    # The published orders of magnitude, restated here so a bad export is loud.
    assert 1500 <= counts["kenyon_cells"] <= 3000
    assert 40 <= counts["glomeruli"] <= 70
    assert counts["mbon_alpha3"] == 2


@pytestmark_anatomy
def test_every_alpha3_synapse_belongs_to_a_real_kenyon_cell():
    """The dashboard lights synapses by their owning cell; a bad index would light a lie."""
    a = json.loads(ANATOMY.read_text())
    n_kc = a["counts"]["kenyon_cells"]
    assert all(0 <= i < n_kc for i in a["alpha3_kc"])


@pytestmark_anatomy
def test_coordinates_are_in_microns_and_plausible():
    """A whole Drosophila brain is a few hundred microns across; anything far outside that
    means the voxel-to-micron conversion has gone wrong."""
    a = json.loads(ANATOMY.read_text())
    for cloud in ("kenyon", "alpha3", "calyx"):
        points = a[cloud]
        assert points
        for axis in range(3):
            values = [p[axis] for p in points]
            assert max(abs(min(values)), abs(max(values))) < 500, cloud


@pytestmark_anatomy
def test_anatomy_states_its_units_and_provenance():
    a = json.loads(ANATOMY.read_text())
    assert "micrometres" in a["units"]
    assert "MaleCNS" in a["units"]
    assert a["provenance"]["git_commit"]


# --- static assets ------------------------------------------------------------------------------


def test_the_page_says_what_is_measured_and_what_is_staging():
    """The honesty labels are part of the deliverable, so they are asserted."""
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert "staging, not simulated" in html
    assert "not spatially registered" in html
    assert "measured location" in html
    assert "ranks, does not detect" in html


def test_vendored_assets_keep_their_notices():
    three = (STATIC / "three.module.js").read_text(encoding="utf-8", errors="replace")[:400]
    assert "SPDX-License-Identifier: MIT" in three
    fly = json.loads((STATIC / "fly.json").read_text())
    assert "Apache-2.0" in fly["source"]
    assert "Lobato-Rios" in fly["citation"]


def test_server_never_targets_anything():
    """The dashboard reads corpora from disk. It must not grow a fetching habit."""
    source = (REPO_ROOT / "flypaper" / "web" / "server.py").read_text()
    assert "requests.get" not in source
    assert "urlopen" not in source


def test_tailscale_helper_never_funnels():
    """`tailscale serve` is tailnet-only; `tailscale funnel` publishes to the internet.

    Checked against the actual command that gets executed rather than against the text of
    the file, which also has to be free to explain why funnel is not used.
    """
    import ast

    source = (REPO_ROOT / "flypaper" / "web" / "server.py").read_text()
    tree = ast.parse(source)
    commands = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for arg in node.args:
            if isinstance(arg, ast.List):
                parts = [e.value for e in arg.elts if isinstance(e, ast.Constant)]
                if parts and parts[0] == "tailscale":
                    commands.append(parts)
    assert commands, "expected at least one tailscale invocation to check"
    for command in commands:
        assert "funnel" not in command, command
        assert command[1] in {"serve", "status"}, command
