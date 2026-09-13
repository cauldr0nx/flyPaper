"""The controls on the dashboard's scan endpoint.

This is the one place flypaper initiates a request against a target, so the question these
answer is not "does it work" but "what can the browser make it do". Each test names a
control and the thing that control exists to prevent.

The scope file and the wordlist directory are chosen on the server's command line before a
browser is ever involved; nothing reachable from the page can widen either.
"""

from __future__ import annotations

import pytest

from flypaper.stage2.scope import Scope
from flypaper.web.scan import MAX_RATE, ScanManager, ScanRefused


@pytest.fixture
def manager(tmp_path):
    (tmp_path / "small.txt").write_text("admin\nbackup\n")
    (tmp_path / "secrets").mkdir()
    return ScanManager(Scope(patterns=("example.com", "*.test.internal")), tmp_path)


def test_a_target_outside_the_scope_file_is_refused(manager):
    """The control: no same-domain inference, no wildcard default, no widening from the page."""
    with pytest.raises(ScanRefused, match="not in the scope file"):
        manager._resolve("https://evil.example/FUZZ", "small.txt", 10)


def test_an_exact_host_and_an_explicit_wildcard_are_both_honoured(manager):
    assert manager._resolve("https://example.com/FUZZ", "small.txt", 10)
    assert manager._resolve("https://api.test.internal/FUZZ", "small.txt", 10)


def test_a_target_without_the_placeholder_is_refused(manager):
    """Without FUZZ, ffuf would request one URL repeatedly for the length of the wordlist."""
    with pytest.raises(ScanRefused, match="must contain FUZZ"):
        manager._resolve("https://example.com/admin", "small.txt", 10)


@pytest.mark.parametrize("scheme", ["file", "gopher", "ftp"])
def test_only_http_and_https_are_accepted(manager, scheme):
    with pytest.raises(ScanRefused, match="unsupported scheme"):
        manager._resolve(f"{scheme}://example.com/FUZZ", "small.txt", 10)


@pytest.mark.parametrize(
    "wordlist", ["../../../etc/shadow", "/etc/shadow", "secrets/../../etc/passwd", ""]
)
def test_the_wordlist_cannot_escape_its_directory(manager, wordlist):
    """The control: an arbitrary path would make ffuf request each of its lines.

    That is a file disclosure with extra steps - the dashboard would then render the lines
    of /etc/shadow as the words it had fuzzed with.
    """
    with pytest.raises(ScanRefused):
        manager._resolve("https://example.com/FUZZ", wordlist, 10)


def test_a_directory_inside_the_allowed_directory_is_not_a_wordlist(manager):
    with pytest.raises(ScanRefused):
        manager._resolve("https://example.com/FUZZ", "secrets", 10)


def test_the_rate_ceiling_is_in_the_code_not_the_request(manager):
    """The browser may ask for less and cannot ask for more."""
    _, _, rate = manager._resolve("https://example.com/FUZZ", "small.txt", 10_000)
    assert rate == MAX_RATE
    _, _, slow = manager._resolve("https://example.com/FUZZ", "small.txt", 3)
    assert slow == 3


def test_scanning_is_refused_outright_without_a_scope_file(tmp_path):
    """No scope file means the dashboard can rank and replay, and cannot scan."""
    (tmp_path / "w.txt").write_text("a\n")
    ok, why = ScanManager(None, tmp_path).ready()
    assert not ok
    assert "no scope file" in why


def test_scanning_is_refused_outright_without_a_wordlist_directory():
    ok, why = ScanManager(Scope(patterns=("example.com",)), None).ready()
    assert not ok
    assert "no wordlists" in why


def test_the_command_is_an_argv_never_a_shell_string(manager, monkeypatch):
    """A target containing a semicolon has to stay a target.

    Asserted on the argv rather than on the absence of a side effect, because a test that
    checks `/tmp/pwned` does not exist passes for many reasons, only one of which is that
    the code is correct.
    """
    seen = {}

    class FakeProc:
        stdout = iter(())
        stderr = None

        def poll(self):
            return 0

        def wait(self, timeout=None):
            return 0

        def terminate(self):
            pass

    def fake_popen(argv, **kwargs):
        seen["argv"] = argv
        seen["shell"] = kwargs.get("shell", False)
        return FakeProc()

    monkeypatch.setattr("flypaper.web.scan.subprocess.Popen", fake_popen)
    manager.start("https://example.com/FUZZ; touch /tmp/pwned", "small.txt", 5)
    manager._thread.join(timeout=5)

    assert isinstance(seen["argv"], list), "must be an argv list"
    assert seen["shell"] is False, "never a shell"
    assert "https://example.com/FUZZ; touch /tmp/pwned" in seen["argv"], (
        "the whole target is one argv element, so the shell never sees the semicolon"
    )
    assert "-mc" in seen["argv"] and seen["argv"][seen["argv"].index("-mc") + 1] == "all", (
        "the noise is the baseline; the scan must not pre-filter it away"
    )


def test_only_one_scan_runs_at_a_time(manager, monkeypatch):
    monkeypatch.setattr("flypaper.web.scan.subprocess.Popen", lambda *a, **k: _Hanging())
    manager.start("https://example.com/FUZZ", "small.txt", 5)
    with pytest.raises(ScanRefused, match="already running"):
        manager.start("https://example.com/FUZZ", "small.txt", 5)
    manager.stop()


class _Hanging:
    """A subprocess that never produces output, so the scan stays 'running'."""

    stdout = iter(())
    stderr = None

    def poll(self):
        return None

    def wait(self, timeout=None):
        return 0

    def terminate(self):
        pass

    def kill(self):
        pass


class _Headers(dict):
    """http.server's header object, as much of it as `_authorised` touches."""

    def get(self, key, default=None):
        return super().get(key, default)


class _Req:
    def __init__(self, headers):
        self.headers = _Headers(headers)


def test_a_post_without_the_token_is_rejected(monkeypatch):
    """The control that matters: a page on another origin cannot read this page's HTML.

    Without it, any site the operator visits while the dashboard listens on loopback could
    POST to it and start a scan. The browser would send that request quite happily.
    """
    from flypaper.web import server

    monkeypatch.setattr(server, "TOKEN", "the-real-token")
    assert not server._authorised(_Req({}))
    assert not server._authorised(_Req({"X-Flypaper-Token": "guessed"}))
    assert server._authorised(_Req({"X-Flypaper-Token": "the-real-token"}))


def test_a_cross_origin_post_is_rejected_even_with_the_token(monkeypatch):
    """Belt and braces for the same threat. Browsers attach Origin to cross-site POSTs."""
    from flypaper.web import server

    monkeypatch.setattr(server, "TOKEN", "t")
    same = {"X-Flypaper-Token": "t", "Origin": "http://127.0.0.1:8770", "Host": "127.0.0.1:8770"}
    cross = {"X-Flypaper-Token": "t", "Origin": "https://evil.example", "Host": "127.0.0.1:8770"}
    assert server._authorised(_Req(same))
    assert not server._authorised(_Req(cross))


def test_no_token_means_no_post_at_all(monkeypatch):
    """A server that somehow started without a token must refuse, not wave everything through."""
    from flypaper.web import server

    monkeypatch.setattr(server, "TOKEN", "")
    assert not server._authorised(_Req({"X-Flypaper-Token": ""}))


def test_a_live_frame_is_shaped_exactly_like_a_replayed_one(manager, monkeypatch):
    """The page has one renderer, so a scan must produce what a replay produces.

    Both paths drive the same `Recorder` on the server, and this is what stops that from
    quietly ceasing to be true: if a live frame ever grew or lost a key, the dashboard would
    render a scan differently from a replay and nothing else would notice.
    """
    import base64
    import json as _json

    from flypaper.ingest.ffuf import FfufResult
    from flypaper.web.replay import record

    def ffuf_line(word, status, length):
        return (
            _json.dumps(
                {
                    "input": {"FUZZ": base64.b64encode(word.encode()).decode()},
                    "position": 1,
                    "status": status,
                    "length": length,
                    "words": max(length // 11, 1),
                    "lines": max(length // 70, 1),
                    "content-type": "text/html",
                    "url": f"https://example.com/{word}",
                    "host": "example.com",
                    "duration": 1_000_000,
                }
            ).encode()
            + b"\n"
        )

    lines = [ffuf_line(f"w{i}", 404, 900 + (i % 3)) for i in range(80)]

    class FakeProc:
        def __init__(self):
            self.stdout = iter(lines)
            self.stderr = None

        def poll(self):
            return 0

        def wait(self, timeout=None):
            return 0

        def terminate(self):
            pass

    # The replay is built first, and the patch undone after the scan, because
    # `flypaper.web.scan.subprocess` *is* the stdlib module: patching Popen on it patches it
    # for everything, and `provenance()` shells out to git through `subprocess.run`.
    replayed = record(
        [
            FfufResult(
                inputs={"FUZZ": "w0"},
                url="https://example.com/w0",
                status=404,
                length=900,
                words=81,
                lines=12,
                content_type="text/html",
                host="example.com",
                duration_ms=1.0,
            )
        ]
    )["frames"][0]

    monkeypatch.setattr("flypaper.web.scan.subprocess.Popen", lambda *a, **k: FakeProc())
    manager.start("https://example.com/FUZZ", "small.txt", 5)
    manager._thread.join(timeout=10)
    monkeypatch.undo()

    assert manager.state.requests == 80, manager.state.error
    live = manager.recorder.frames[0]

    assert set(live) == set(replayed), "a live frame and a replayed frame must carry the same keys"
