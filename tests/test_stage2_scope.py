"""M5 gate: an out-of-scope URL present in the input stream is never requested.

Not asserted by inspecting the code's intentions. A real HTTP server records every
connection it receives, the input stream is seeded with out-of-scope URLs pointing at it,
and the test fails if it hears anything at all.
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from flypaper.ingest.ffuf import FfufResult
from flypaper.stage2.refetch import MAX_RATE, Fetched, RateLimiter, refetch
from flypaper.stage2.scope import Scope


class Recorder(ThreadingHTTPServer):
    """An HTTP server that writes down every path anybody asks it for."""

    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address):
        super().__init__(address, _Handler)
        self.seen: list[str] = []
        self.lock = threading.Lock()

    def handle_error(self, request, client_address):
        """Tearing the fixture down closes live keep-alive connections, and the default
        handler prints a traceback for each one. That is the fixture ending, not a fault,
        and it should not litter the test output."""


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):  # noqa: N802
        with self.server.lock:  # type: ignore[attr-defined]
            self.server.seen.append(self.path)  # type: ignore[attr-defined]
        body = b"ok"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@pytest.fixture
def server():
    srv = Recorder(("127.0.0.1", 0))
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv
    srv.shutdown()
    srv.server_close()


def result(url: str) -> FfufResult:
    return FfufResult(inputs={"FUZZ": "x"}, url=url, status=200, length=2, words=1, lines=1)


def scope_for(*patterns: str, tmp_path) -> Scope:
    path = tmp_path / "scope.txt"
    path.write_text("\n".join(patterns) + "\n")
    return Scope.from_file(path)


def fast_limiter() -> RateLimiter:
    """Real spacing logic, no real sleeping: the tests assert scope, not wall-clock."""
    return RateLimiter(MAX_RATE, sleep=lambda _: None)


# --- the gate -------------------------------------------------------------------------------


def test_out_of_scope_url_in_the_input_stream_is_never_requested(server, tmp_path):
    """The gate. An in-scope and an out-of-scope URL point at the same recording server;
    only the in-scope one may ever reach it."""
    port = server.server_address[1]
    stream = [
        result(f"http://127.0.0.1:{port}/allowed"),
        result(f"http://localhost:{port}/forbidden"),
        result("https://evil.example.com/forbidden"),
        result(f"http://127.0.0.1.attacker.net:{port}/forbidden"),
    ]
    scope = scope_for(f"127.0.0.1:{port}", tmp_path=tmp_path)

    fetched = list(refetch(stream, scope, top=10, limiter=fast_limiter()))

    assert server.seen == ["/allowed"], f"server heard {server.seen}"
    assert [f.url for f in fetched] == [f"http://127.0.0.1:{port}/allowed"]
    assert len(scope.skipped) == 3
    assert all(not d.allowed and d.reason for d in scope.skipped)


def test_nothing_is_requested_without_a_scope(server, tmp_path):
    port = server.server_address[1]
    with pytest.raises(ValueError, match="explicit scope"):
        list(refetch([result(f"http://127.0.0.1:{port}/x")], Scope(), limiter=fast_limiter()))
    assert server.seen == []


def test_only_urls_from_the_input_stream_are_requested(server, tmp_path):
    """No crawling, no guessing. The candidate list is exactly what was handed in."""
    port = server.server_address[1]
    scope = scope_for(f"127.0.0.1:{port}", tmp_path=tmp_path)
    list(refetch([result(f"http://127.0.0.1:{port}/only-this")], scope, limiter=fast_limiter()))
    assert server.seen == ["/only-this"]


def test_redirects_are_recorded_but_not_followed(server, tmp_path, monkeypatch):
    """A redirect points somewhere that was never scope-checked."""
    port = server.server_address[1]

    class RedirectHandler(_Handler):
        def do_GET(self):  # noqa: N802
            with self.server.lock:
                self.server.seen.append(self.path)
            self.send_response(302)
            self.send_header("Location", "https://evil.example.com/")
            self.send_header("Content-Length", "0")
            self.end_headers()

    server.RequestHandlerClass = RedirectHandler
    scope = scope_for(f"127.0.0.1:{port}", tmp_path=tmp_path)
    out = list(refetch([result(f"http://127.0.0.1:{port}/r")], scope, limiter=fast_limiter()))

    assert out[0].status == 302
    assert out[0].location == "https://evil.example.com/"
    assert server.seen == ["/r"]


def test_top_n_caps_the_number_of_requests(server, tmp_path):
    port = server.server_address[1]
    scope = scope_for(f"127.0.0.1:{port}", tmp_path=tmp_path)
    stream = [result(f"http://127.0.0.1:{port}/{i}") for i in range(20)]
    list(refetch(stream, scope, top=3, limiter=fast_limiter()))
    assert len(server.seen) == 3


def test_duplicate_urls_are_requested_once(server, tmp_path):
    port = server.server_address[1]
    scope = scope_for(f"127.0.0.1:{port}", tmp_path=tmp_path)
    stream = [result(f"http://127.0.0.1:{port}/same")] * 5
    list(refetch(stream, scope, top=10, limiter=fast_limiter()))
    assert server.seen == ["/same"]


def test_bodies_are_not_retained_by_default(server, tmp_path):
    port = server.server_address[1]
    scope = scope_for(f"127.0.0.1:{port}", tmp_path=tmp_path)
    out = list(refetch([result(f"http://127.0.0.1:{port}/x")], scope, limiter=fast_limiter()))
    assert out[0].body is None
    out = list(
        refetch(
            [result(f"http://127.0.0.1:{port}/x")],
            scope,
            retain_bodies=True,
            limiter=fast_limiter(),
        )
    )
    assert out[0].body == b"ok"


def test_one_failing_target_does_not_end_the_pass(tmp_path):
    """A dead host must not cost the operator the rest of their candidates."""
    scope = scope_for("127.0.0.1:9", "127.0.0.1:1", tmp_path=tmp_path)
    out = list(
        refetch(
            [result("http://127.0.0.1:9/dead"), result("http://127.0.0.1:1/alsodead")],
            scope,
            timeout=0.25,
            limiter=fast_limiter(),
        )
    )
    assert len(out) == 2
    assert all(isinstance(f, Fetched) and f.error for f in out)


# --- the rate limiter --------------------------------------------------------------------------


def test_rate_is_capped_and_cannot_be_raised_past_the_ceiling():
    assert RateLimiter(1000.0).rate == MAX_RATE
    assert RateLimiter(1000.0).capped is True
    assert RateLimiter(0.5).rate == 0.5


def test_rate_must_be_positive():
    with pytest.raises(ValueError):
        RateLimiter(0)


def test_limiter_spaces_requests_by_the_full_interval():
    """Verified against a fake clock: no bursts, whatever the arrival pattern."""
    now = [0.0]
    slept: list[float] = []

    def sleep(seconds):
        slept.append(seconds)
        now[0] += seconds

    limiter = RateLimiter(2.0, clock=lambda: now[0], sleep=sleep)
    limiter.wait()  # first is immediate
    limiter.wait()  # must wait the full 0.5s
    assert slept == [pytest.approx(0.5)]

    now[0] += 10.0  # a long natural gap needs no sleep at all
    limiter.wait()
    assert len(slept) == 1


def test_limiter_under_load_never_exceeds_the_rate():
    now = [0.0]

    def sleep(seconds):
        now[0] += seconds

    limiter = RateLimiter(MAX_RATE, clock=lambda: now[0], sleep=sleep)
    for _ in range(100):
        limiter.wait()
    # 100 requests at the ceiling must span at least 99 intervals.
    assert now[0] >= 99 * (1.0 / MAX_RATE) - 1e-9


# --- scope parsing ------------------------------------------------------------------------------


def test_scope_file_must_contain_something():
    import tempfile
    from pathlib import Path

    path = Path(tempfile.mkdtemp()) / "empty.txt"
    path.write_text("# only a comment\n")
    with pytest.raises(ValueError, match="authorises nothing"):
        Scope.from_file(path)


def test_bare_domain_does_not_authorise_subdomains_or_lookalikes(tmp_path):
    scope = scope_for("example.com", tmp_path=tmp_path)
    assert scope.decide("https://example.com/").allowed
    assert not scope.decide("https://sub.example.com/").allowed
    assert not scope.decide("https://notexample.com/").allowed
    assert not scope.decide("https://example.com.evil.net/").allowed


def test_wildcard_matches_subdomains_but_not_the_bare_domain(tmp_path):
    scope = scope_for("*.example.com", tmp_path=tmp_path)
    assert scope.decide("https://a.example.com/").allowed
    assert scope.decide("https://a.b.example.com/").allowed
    assert not scope.decide("https://example.com/").allowed
    assert not scope.decide("https://evilexample.com/").allowed


def test_non_http_schemes_are_refused(tmp_path):
    scope = scope_for("example.com", tmp_path=tmp_path)
    for url in ("file:///etc/passwd", "gopher://example.com/", "ftp://example.com/"):
        assert not scope.decide(url).allowed


def test_paths_in_a_scope_file_are_rejected_loudly(tmp_path):
    path = tmp_path / "bad.txt"
    path.write_text("example.com/only/this/path\n")
    with pytest.raises(ValueError, match="not a host pattern"):
        Scope.from_file(path)
