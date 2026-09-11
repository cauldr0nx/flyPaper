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

`/collide/<word>` The ffuf issue #387 case with the status code taken away. Same word-count
                  collision as `/calib/`, but the noise answers **200** rather than 404, so
                  autocalibration has nothing but size, words and lines to work with. This
                  is the faithful reproduction; `/calib/` turns out not to be, because a
                  distinct 404 hands `-ac` a clean discriminator.
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
#
# Several hits per surface, deliberately spanning obvious to subtle, because a ranking
# metric computed against a single needle says almost nothing: precision@10 with one
# labelled item cannot exceed 0.1 whatever the ranker does.
#
# `(words, lines, filler)` shapes each page. On /calib/ every page - hits included - is
# pinned to CALIB_WORDS so the word filter `-ac` derives carries no information at all.
HIT_SHAPES: dict[str, tuple[int, int, str]] = {
    # Token surface. Baseline is (80, 12); these range from far away to very close.
    "/token/account": (240, 46, "account"),
    "/token/admin": (700, 90, "adminpanel"),
    "/token/backup.sql": (1500, 160, "dump"),
    "/token/.git": (95, 14, "gitcfg"),  # subtle: near the baseline shape
    "/token/config.json": (60, 9, "cfg"),  # subtle: smaller than baseline
    "/token/debug": (300, 30, "debug"),
    "/token/internal": (88, 13, "internal"),  # very subtle
    "/token/metrics": (450, 61, "metric"),
    # Issue #387 surface. Every hit keeps the 404 page's word count.
    "/calib/reports": (CALIB_WORDS, 40, "report"),
    "/calib/exports": (CALIB_WORDS, 60, "export"),
    "/calib/audit": (CALIB_WORDS, 8, "audit"),
    "/calib/console": (CALIB_WORDS, 31, "console"),
    "/calib/staging": (CALIB_WORDS, 22, "staging"),  # subtle: 2 lines off baseline
    "/calib/legacy": (CALIB_WORDS, 19, "legacy"),  # subtle: 1 line off baseline
    # Issue #387 with no status discriminator: noise answers 200, so only size, words and
    # lines are available to a filter, and the word count is pinned across all of them.
    "/collide/reports": (CALIB_WORDS, 40, "report"),
    "/collide/exports": (CALIB_WORDS, 60, "export"),
    "/collide/audit": (CALIB_WORDS, 8, "audit"),
    "/collide/console": (CALIB_WORDS, 31, "console"),
    "/collide/staging": (CALIB_WORDS, 22, "staging"),
    "/collide/legacy": (CALIB_WORDS, 19, "legacy"),
    # Stable surface. Baseline is (60, 10).
    "/stable/backup": (310, 52, "backup"),
    "/stable/admin": (620, 80, "admin"),
    "/stable/.env": (64, 11, "env"),  # subtle
    "/stable/private": (180, 26, "private"),
    "/stable/trace": (900, 110, "trace"),
    "/stable/old": (58, 9, "old"),  # subtle: smaller than baseline
}

#: Hits whose shape sits within a few percent of their surface's noise baseline. Reported
#: separately at M4: a ranker that only finds the obvious ones is not doing much.
SUBTLE = frozenset(
    {
        "/token/.git",
        "/token/config.json",
        "/token/internal",
        "/calib/staging",
        "/calib/legacy",
        "/collide/staging",
        "/collide/legacy",
        "/stable/.env",
        "/stable/old",
    }
)

HITS = {path: ("subtle" if path in SUBTLE else "obvious") for path in HIT_SHAPES}


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
    if path in HIT_SHAPES:
        words, lines, filler = HIT_SHAPES[path]
        title = path.rsplit("/", 1)[-1].replace(".", "") or "page"
        return 200, _page(title, words, lines, filler), "text/html"

    if path.startswith("/token/"):
        # Identical page, fresh token. Only Content-Length moves.
        return 200, _page("NotFound", 80, 12, "notice", token=_token(path)), "text/html"

    if path.startswith("/calib/"):
        return 404, _page("NotFound", CALIB_WORDS, CALIB_404_LINES, "missing"), "text/html"

    if path.startswith("/collide/"):
        # 200, not 404: the soft-404 shape, so status separates nothing.
        return 200, _page("NotFound", CALIB_WORDS, CALIB_404_LINES, "missing"), "text/html"

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
        probes = ["/calib/zzq7x9", "/token/zzq7x9", "/token/aaaa", "/stable/zzq7x9"]
        for path in probes + sorted(HIT_SHAPES):
            status, body, _ = build_response(path)
            words, lines = counts(body)
            print(f"{path:24} status={status} size={len(body):5} words={words:4} lines={lines:3}")
        return

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"flypaper bench target on http://{args.host}:{args.port} (loopback only)")
    server.serve_forever()


if __name__ == "__main__":
    main()
