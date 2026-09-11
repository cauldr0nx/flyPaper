#!/usr/bin/env python3
"""A deliberately-shaped local target for the two scenarios ffufme does not cover.

ffufme supplies the wildcard / soft-404 host (`/cd/no404/*` answers 200 to everything, with
a real hit at `/secret`) and a multi-wordlist surface. It does not supply a token-rotating
endpoint, and it does not supply the ffuf issue #387 case. Both are required by section 8
of the brief, so they are built here rather than left as a half-met gate.

Ours, MIT, stdlib only, and deterministic: every response is a pure function of the request
path and a fixed seed, so a capture replays identically and a test can assert against exact
numbers. It serves no real content and models no real application.

    python bench/target/server.py --port 8110

Surfaces
--------

`/token/<word>`   Every unknown word returns the same page carrying a fresh CSRF token of
                  *variable* length, so Content-Length jitters by a few bytes and `-fs`
                  cannot filter it. Word count and line count stay fixed. The hit is
                  `/token/account`, a genuinely different page.

`/calib/<word>`   The ffuf issue #387 case. Unknown words return a 404 page of a fixed
                  size, word count and line count; `-ac` derives filters from exactly that.
                  The hit, `/calib/reports`, is a real 200 page whose **word count
                  coincides with the 404 page's** while its size and line count do not - so
                  autocalibration's OR logic hides it on the word filter alone.

`/stable/<word>`  A control: unknown words return a byte-identical 404. Nothing jitters.
                  The hit is `/stable/backup`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import unquote, urlparse

SEED = "flypaper-m2-20260911"

# The ffuf issue #387 coincidence, stated as numbers rather than left to chance.
CALIB_WORDS = 100  # both the 404 page and the hit have exactly this many words
CALIB_404_LINES = 20
CALIB_HIT_LINES = 40

# Ground truth. Everything else on these surfaces is noise by construction.
HITS = {
    "/token/account": "token-rotating surface: the one genuinely different page",
    "/calib/reports": "issue #387: word count collides with the autocalibrated filter",
    "/stable/backup": "stable surface: the one genuinely different page",
}


def _digest(path: str) -> int:
    return int(hashlib.sha256((SEED + path).encode()).hexdigest()[:8], 16)


def _token(path: str) -> str:
    """A CSRF-token lookalike whose length varies with the path: 20-28 characters.

    Fixed-length tokens would not be a challenge - Content-Length would not move. Real
    session and CSRF tokens that break `-fs` are the variable-length ones.
    """
    raw = hashlib.sha256((SEED + "token" + path).encode()).hexdigest()
    return raw[: 20 + _digest(path) % 9]


def _page(title: str, words: int, lines: int, filler: str, token: str = "") -> bytes:
    """A page that ffuf will count as exactly `words` words and `lines` lines.

    ffuf does not split on whitespace. `pkg/runner/simple.go` counts
    `bytes.Count(body, " ") + 1` words and `bytes.Count(body, "\n") + 1` lines, so a
    newline is not a word separator and the counts are exactly controllable. That is what
    makes the issue #387 collision an asserted number rather than a hope.

    `token` is inserted without adding a space or a newline, so it moves Content-Length
    and nothing else - which is precisely how a real CSRF token defeats `-fs`.
    """
    if lines < 3:
        raise ValueError("need at least 3 lines for the html wrapper")
    prefix = f"<html><title>{title}</title>"
    suffix = "</html>"
    assert " " not in prefix and " " not in suffix, "wrapper must contribute no spaces"

    # prefix + "\n" + content + "\n" + suffix contributes 2 newlines and 0 spaces.
    content_lines = (lines - 1) - 2 + 1
    content_spaces = words - 1

    per_line, remainder = divmod(content_spaces + content_lines, content_lines)
    rows = []
    for index in range(content_lines):
        count = per_line + (1 if index < remainder else 0)
        rows.append(" ".join(f"{filler}{index}x{n}" for n in range(count)))
    if token:
        rows[0] = f"<input,name=csrf_token,value={token}>{rows[0]}"
    body = prefix + "\n" + "\n".join(rows) + "\n" + suffix
    return body.encode()


def counts(body: bytes) -> tuple[int, int]:
    """(words, lines) exactly as ffuf counts them. Used by the tests and `--describe`."""
    return body.count(b" ") + 1, body.count(b"\n") + 1


def build_response(path: str) -> tuple[int, bytes, str]:
    """(status, body, content_type) for a path. Pure; no state, no clock, no randomness."""
    if path == "/token/account":
        return 200, _page("account", 240, 46, "account"), "text/html"
    if path == "/calib/reports":
        # Same word count as the /calib 404 page; different size and line count.
        return 200, _page("reports", CALIB_WORDS, CALIB_HIT_LINES, "report"), "text/html"
    if path == "/stable/backup":
        return 200, _page("backup", 310, 52, "backup"), "text/html"

    if path.startswith("/token/"):
        # Identical page, fresh token. Only Content-Length moves.
        return 200, _page("NotFound", 80, 12, "notice", token=_token(path)), "text/html"

    if path.startswith("/calib/"):
        return 404, _page("NotFound", CALIB_WORDS, CALIB_404_LINES, "missing"), "text/html"

    if path.startswith("/stable/"):
        return 404, _page("NotFound", 60, 10, "gone"), "text/html"

    if path in ("/", "/index.html"):
        return 200, _page("flypaper-bench-target", 40, 8, "index"), "text/html"

    if path == "/labels.json":
        return 200, json.dumps(HITS, indent=2).encode(), "application/json"

    return 404, _page("NotFound", 30, 6, "nothing"), "text/html"


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "flypaper-bench/1.0"

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's interface
        path = unquote(urlparse(self.path).path)
        status, body, content_type = build_response(path)
        self.send_response(status)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_HEAD = do_GET

    def log_message(self, *args) -> None:
        """Silent. A 10,000-request capture does not need 10,000 lines of access log."""


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=8110)
    ap.add_argument("--host", default="127.0.0.1", help="loopback only by default")
    ap.add_argument("--describe", action="store_true", help="print the shaped numbers and exit")
    args = ap.parse_args()

    if args.describe:
        for path in (
            "/calib/zzq7x9",
            "/calib/reports",
            "/token/zzq7x9",
            "/token/aaaa",
            "/token/account",
            "/stable/zzq7x9",
            "/stable/backup",
        ):
            status, body, _ = build_response(path)
            words, lines = counts(body)
            print(f"{path:24} status={status} size={len(body):5} words={words:4} lines={lines:3}")
        return

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"flypaper bench target on http://{args.host}:{args.port} (loopback only)")
    server.serve_forever()


if __name__ == "__main__":
    main()
