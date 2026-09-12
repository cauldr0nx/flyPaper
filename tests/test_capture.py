"""The capture harness, and specifically the caps that exist for other people's servers.

`bench/capture.py` builds an ffuf command line. Two of its inputs are safety limits rather
than parameters: a live third-party surface is capped at `LIVE_RATE_CEILING` requests per
second and `LIVE_MAX_REQUESTS` requests total. The second cap was added after a capture ran
2,000 words at a polite 10/s and tripped a real target's edge protection.

It did not work. The truncated wordlist was built, written to disk and named in the
provenance record, and then the command was assembled with the *untruncated* path, so every
live capture sent the full list and the manifest said otherwise. Nothing caught it, because
nothing tested the command line itself. These do.
"""

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "bench"))

import capture as cap  # noqa: E402


def _command(monkeypatch, name, rate=50, threads=20):
    """Run `capture()` far enough to see the ffuf argv, without running ffuf."""
    seen = {}

    class Done:
        returncode = 0
        stderr = b""

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        return Done()

    monkeypatch.setattr(cap.subprocess, "run", fake_run)
    cap.capture(name, rate=rate, threads=threads)
    return seen["cmd"]


def _arg(cmd, flag):
    return cmd[cmd.index(flag) + 1]


def test_live_capture_uses_the_truncated_wordlist_it_wrote(monkeypatch, tmp_path):
    """The regression: the cap was computed and then not passed to ffuf."""
    monkeypatch.setattr(cap, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(cap, "CORPUS", tmp_path)
    monkeypatch.setattr(cap, "WORDLIST", tmp_path / "words.txt")
    (tmp_path / "words.txt").write_text("\n".join(f"w{i}" for i in range(2000)) + "\n")
    monkeypatch.setitem(
        cap.SURFACES,
        "fake-live",
        {
            "url": "http://example.invalid/FUZZ",
            "hits": [],
            "scenario": "test",
            "target": "test",
            "authorization": "test",
            "live": True,
        },
    )
    cmd = _command(monkeypatch, "fake-live", rate=500, threads=40)

    used = Path(_arg(cmd, "-w"))
    assert used.name == f".live-{cap.LIVE_MAX_REQUESTS}.txt", "must use the truncated list"
    assert len([w for w in used.read_text().split("\n") if w]) == cap.LIVE_MAX_REQUESTS
    assert int(_arg(cmd, "-rate")) == cap.LIVE_RATE_CEILING, "rate is capped in code"
    assert int(_arg(cmd, "-t")) <= 4, "threads are capped for live surfaces"


def test_local_capture_is_not_capped(monkeypatch, tmp_path):
    """The caps are about other people's servers; our own loopback target is not one."""
    monkeypatch.setattr(cap, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(cap, "CORPUS", tmp_path)
    monkeypatch.setattr(cap, "WORDLIST", tmp_path / "words.txt")
    (tmp_path / "words.txt").write_text("\n".join(f"w{i}" for i in range(2000)) + "\n")
    cmd = _command(monkeypatch, "bench-stable", rate=500, threads=40)
    assert Path(_arg(cmd, "-w")).name == "words.txt"
    assert int(_arg(cmd, "-rate")) == 500


def test_a_surface_may_bring_its_own_wordlist(monkeypatch, tmp_path):
    """bench-sprawl does, so that adding it could not rewrite the shared haystack."""
    monkeypatch.setattr(cap, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(cap, "CORPUS", tmp_path)
    monkeypatch.setattr(cap, "WORDLIST", tmp_path / "words.txt")
    (tmp_path / "words.txt").write_text("shared\n")
    (tmp_path / "sprawl-words.txt").write_text("own\n")
    cmd = _command(monkeypatch, "bench-sprawl")
    assert Path(_arg(cmd, "-w")).name == "sprawl-words.txt"


def test_every_labelled_hit_is_in_the_wordlist_that_will_be_used():
    """A hit the capture never requests is a hit no benchmark can ever find."""
    for name, surface in cap.SURFACES.items():
        if surface.get("live"):
            continue  # truncated on purpose; covered above
        path = cap.CORPUS / surface.get("wordlist", cap.WORDLIST.name)
        if not path.exists():
            pytest.skip(f"{path.name} not generated")
        words = set(path.read_text(encoding="utf-8").split("\n"))
        missing = [h for h in surface["hits"] if h not in words]
        assert not missing, f"{name}: {missing} would never be requested"


def test_preflight_refuses_a_target_that_does_not_know_the_surface(monkeypatch):
    """The stale-process case: something answers on the port, but it is the old code.

    This produced a complete, well-formed, entirely wrong 1,998-record corpus - one noise
    population where the surface defines seven - and nothing downstream could have noticed,
    because a single-population surface is a legitimate thing to capture.
    """
    import io

    stale = json.dumps({"/mixed/admin": "obvious"}).encode()  # no /sprawl/ labels at all
    monkeypatch.setattr(cap.urllib.request, "urlopen", lambda *a, **k: _ctx(io.BytesIO(stale)))
    with pytest.raises(SystemExit, match="does not serve /sprawl/"):
        cap.preflight("bench-sprawl")


def test_preflight_passes_when_the_target_serves_this_surface(monkeypatch):
    import io

    served = json.dumps({f"/sprawl/{h}": "obvious" for h in cap.SURFACES["bench-sprawl"]["hits"]})
    monkeypatch.setattr(
        cap.urllib.request, "urlopen", lambda *a, **k: _ctx(io.BytesIO(served.encode()))
    )
    cap.preflight("bench-sprawl")


def test_preflight_ignores_targets_that_publish_no_manifest(monkeypatch):
    """ffufme and live hosts have no ground truth to ask for; guessing one is not better."""

    def explode(*a, **k):
        raise AssertionError("must not probe a third-party target")

    monkeypatch.setattr(cap.urllib.request, "urlopen", explode)
    cap.preflight("ffufme-no404")


class _ctx:
    """Minimal stand-in for urlopen's context manager."""

    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self.body

    def __exit__(self, *exc):
        return False
