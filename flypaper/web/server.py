#!/usr/bin/env python3
"""The dashboard server.

    python -m flypaper.web.server                  # loopback only
    python -m flypaper.web.server --tailscale      # also share it on the tailnet

Standard library only, no web framework, and the page loads a locally vendored three.js, so
the dashboard works with no internet connection. It serves static files and one endpoint
that re-records a run on demand.

It also starts scans, which is the one place in flypaper that initiates a request against
a target. Everything deciding *what may be scanned* is fixed before the browser is involved
- see `flypaper/web/scan.py` - and the state-changing endpoints are guarded two ways:

  a per-process token, printed at startup and embedded in the page, required on every POST.
  A page on another origin cannot read it, so it cannot forge the request.

  an Origin check, because a browser attaches Origin to cross-site POSTs. Together these
  stop a random site the operator happens to visit from driving a scanner listening on
  their loopback interface.

`--tailscale` binds to all interfaces and runs `tailscale serve` so the page is reachable
from the operator's other devices over the tailnet, on HTTPS, without exposing it to the
internet. It is *not* `tailscale funnel`: no public access, deliberately.
"""

from __future__ import annotations

import argparse
import gzip
import json
import secrets
import subprocess
import sys
import threading
import webbrowser
from functools import lru_cache
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from flypaper import REPO_ROOT
from flypaper.web.scan import MAX_RATE, ScanManager, ScanRefused

STATIC = Path(__file__).resolve().parent / "static"
CORPUS = REPO_ROOT / "bench" / "corpus"
DEFAULT_PORT = 8770

#: Compressible types. run.json is 1.3 MB of digits and gzips to a fraction of that, which
#: matters on a phone over a tailnet relay.
GZIP_TYPES = (".json", ".js", ".css", ".html")


def available_runs() -> list[str]:
    if not CORPUS.exists():
        return []
    return sorted(p.stem for p in CORPUS.glob("*.jsonl"))


@lru_cache(maxsize=8)
def record_run(surface: str) -> str:
    """Score a corpus surface and return the dashboard payload."""
    from flypaper.ingest.stream import iter_batch
    from flypaper.web.replay import record

    path = CORPUS / f"{surface}.jsonl"
    if not path.exists():
        raise FileNotFoundError(surface)

    hits: set[str] = set()
    try:
        sys.path.insert(0, str(REPO_ROOT / "bench"))
        from capture import SURFACES

        hits = set(SURFACES.get(surface, {}).get("hits", []))
    except Exception:  # noqa: BLE001 - the corpus is usable without its labels
        pass

    payload = record(list(iter_batch(path)), hits=hits, label=surface)
    return json.dumps(payload, separators=(",", ":"))


#: Set by `main`. The handler is constructed per request by http.server, so the scan
#: manager and the token have to live beside the class rather than on it.
SCANS: ScanManager | None = None
TOKEN = ""


def _authorised(handler) -> bool:
    """A POST must carry this process's token and must not come from another origin.

    The token is the control that matters: a cross-origin page cannot read the dashboard's
    HTML, so it cannot learn the token, so it cannot forge a scan. The Origin check is
    belt and braces for the same threat - a site the operator visits while the dashboard
    is listening on loopback.
    """
    if not TOKEN or handler.headers.get("X-Flypaper-Token") != TOKEN:
        return False
    origin = handler.headers.get("Origin")
    if origin:
        host = handler.headers.get("Host", "")
        if urlparse(origin).netloc != host:
            return False
    return True


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(STATIC), **kwargs)

    def handle_one_request(self) -> None:
        # A phone that locks its screen mid-download of the 2 MB mesh drops the connection,
        # and the default handler prints a traceback for it. That is normal client
        # behaviour, not a fault worth a stack trace.
        try:
            super().handle_one_request()
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True

    def end_headers(self) -> None:
        # A development viewer served from disk; a cached build is never what the reader
        # wants, and a stale app.js looks exactly like a broken dashboard.
        self.send_header("Cache-Control", "no-store, must-revalidate")
        super().end_headers()

    def log_message(self, fmt, *args):
        if "/api/" in (self.path or ""):
            super().log_message(fmt, *args)

    def _send(self, body: bytes, content_type: str) -> None:
        accepts_gzip = "gzip" in self.headers.get("Accept-Encoding", "")
        if accepts_gzip and len(body) > 4096:
            body = gzip.compress(body, 6)
            encoding = "gzip"
        else:
            encoding = None
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if encoding:
            self.send_header("Content-Encoding", encoding)
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path not in ("/api/scan/start", "/api/scan/stop"):
            self.send_error(404)
            return
        if SCANS is None:
            self.send_error(503, "scanning is not configured")
            return
        if not _authorised(self):
            # Deliberately terse: the page knows what went wrong, and an attacker probing
            # this endpoint learns nothing from the wording.
            self.send_error(403, "missing or invalid token")
            return
        try:
            length = min(int(self.headers.get("Content-Length") or 0), 64_000)
            body = json.loads(self.rfile.read(length) or b"{}") if length else {}
        except ValueError:
            self.send_error(400, "expected a JSON body")
            return

        if parsed.path == "/api/scan/stop":
            SCANS.stop()
            self._send(json.dumps(SCANS.status(0)).encode(), "application/json")
            return

        try:
            SCANS.start(
                str(body.get("target", "")),
                str(body.get("wordlist", "")),
                int(body.get("rate", 20) or 20),
            )
        except ScanRefused as exc:
            self._send(json.dumps({"refused": str(exc)}).encode(), "application/json")
            return
        except (TypeError, ValueError) as exc:
            self._send(json.dumps({"refused": f"bad request: {exc}"}).encode(), "application/json")
            return
        self._send(json.dumps(SCANS.status(0)).encode(), "application/json")

    def do_GET(self) -> None:
        parsed = urlparse(self.path)

        if parsed.path == "/api/runs":
            self._send(json.dumps({"runs": available_runs()}).encode(), "application/json")
            return

        if parsed.path == "/api/scan/ready":
            ready, why = SCANS.ready() if SCANS else (False, "scanning is not configured")
            self._send(
                json.dumps(
                    {
                        "ready": ready,
                        "why": why,
                        "hosts": SCANS.scope_hosts() if SCANS else [],
                        "wordlists": SCANS.wordlists() if SCANS else [],
                        "max_rate": MAX_RATE,
                    }
                ).encode(),
                "application/json",
            )
            return

        if parsed.path == "/api/scan/status":
            if not SCANS:
                self.send_error(503, "scanning is not configured")
                return
            since = parse_qs(parsed.query).get("since", ["0"])[0]
            try:
                offset = max(0, int(since))
            except ValueError:
                offset = 0
            self._send(json.dumps(SCANS.status(offset)).encode(), "application/json")
            return

        if parsed.path == "/api/run":
            surface = parse_qs(parsed.query).get("surface", [""])[0]
            try:
                body = record_run(surface).encode()
            except FileNotFoundError:
                self.send_error(404, f"no corpus named {surface!r}")
                return
            self._send(body, "application/json")
            return

        # Static, with gzip for the big payloads.
        name = parsed.path.lstrip("/") or "index.html"
        target = (STATIC / name).resolve()
        if not str(target).startswith(str(STATIC.resolve())) or not target.is_file():
            super().do_GET()
            return
        if target.suffix in GZIP_TYPES:
            types = {
                ".json": "application/json",
                ".js": "text/javascript",
                ".css": "text/css",
                ".html": "text/html; charset=utf-8",
            }
            body = target.read_bytes()
            if target.name == "index.html":
                # The page needs this process's token to POST. Injected rather than stored
                # in a file, so it is per-process and never on disk, and delivered inside
                # the HTML, which the same-origin policy stops another site from reading.
                body = body.replace(
                    b"</head>",
                    f'<script>window.FLYPAPER_TOKEN="{TOKEN}";</script></head>'.encode(),
                    1,
                )
            self._send(body, types[target.suffix])
            return
        super().do_GET()


def tailnet_identity() -> tuple[str | None, str | None]:
    """(MagicDNS name, tailnet IP) for this machine, or (None, None) off the tailnet."""
    try:
        status = subprocess.run(
            ["tailscale", "status", "--json"], capture_output=True, timeout=15, check=True
        )
        self_node = json.loads(status.stdout)["Self"]
        name = self_node.get("DNSName", "").rstrip(".") or None
        addresses = self_node.get("TailscaleIPs") or []
        ipv4 = next((a for a in addresses if ":" not in a), None)
        return name, ipv4
    except (subprocess.SubprocessError, OSError, KeyError, ValueError):
        return None, None


def start_tailscale_serve(port: int, https_port: int | None = None) -> tuple[str | None, str]:
    """Put the dashboard behind the tailnet's own HTTPS, on a name with no port number.

    Returns (url, note). This is `tailscale serve`, which is tailnet-only; it is never
    `tailscale funnel`, which would publish the page to the internet.

    Binding to the tailnet interface already makes the dashboard reachable from the
    operator's other devices, so a failure here is a missing convenience rather than a
    missing feature, and it says so instead of looking broken.
    """
    command = ["tailscale", "serve", "--bg"]
    if https_port:
        command.append(f"--https={https_port}")
    command.append(f"http://127.0.0.1:{port}")

    try:
        subprocess.run(command, check=True, capture_output=True, timeout=30)
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or b"").decode(errors="replace").strip()
        if "denied" in detail.lower():
            note = (
                "tailscale serve needs root or operator rights. The dashboard is still "
                "reachable on the tailnet at the address below; for HTTPS without a port "
                "number, run once:\n"
                "    sudo tailscale set --operator=$USER"
            )
        else:
            note = f"tailscale serve declined: {detail.splitlines()[0] if detail else exc}"
        return None, note
    except (subprocess.SubprocessError, OSError) as exc:
        return None, f"tailscale serve unavailable: {exc}"

    name, _ = tailnet_identity()
    suffix = f":{https_port}" if https_port else ""
    return (f"https://{name}{suffix}/" if name else None), "tailnet only, not published"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument(
        "--tailscale",
        action="store_true",
        help="also share on the tailnet over HTTPS (tailnet only, never funnel)",
    )
    ap.add_argument(
        "--https-port",
        type=int,
        default=None,
        help=(
            "with --tailscale, put the HTTPS proxy on this port instead of 443. Useful when "
            "something else already serves the tailnet root."
        ),
    )
    ap.add_argument("--open", action="store_true", help="open a browser on this machine")
    ap.add_argument(
        "--scope",
        default=None,
        help=(
            "a scope file of explicit host patterns. Without it the dashboard can replay "
            "and rank but cannot start a scan: there is deliberately no default scope and "
            "no same-domain inference. See `fly scope`."
        ),
    )
    ap.add_argument(
        "--wordlist-dir",
        default=None,
        help=(
            "a directory of wordlists the page may choose from, by basename. Without it "
            "the dashboard cannot start a scan. The page can never name a path: an "
            "arbitrary path would make ffuf request each of its lines and render them."
        ),
    )
    args = ap.parse_args()

    global SCANS, TOKEN
    scope = None
    if args.scope:
        from flypaper.stage2.scope import Scope

        scope = Scope.from_file(args.scope)
    SCANS = ScanManager(scope, Path(args.wordlist_dir) if args.wordlist_dir else None)
    TOKEN = secrets.token_urlsafe(32)

    runs = available_runs()
    if not (STATIC / "run.json").exists():
        # run.json is derived from a corpus and is not committed, so a fresh checkout has
        # none. Record one rather than showing an empty page.
        if not runs:
            print(
                "No recorded run and no corpus to record one from. Capture a corpus first:\n"
                "    python bench/capture.py --all-local",
                file=sys.stderr,
            )
            return 2
        surface = "bench-token" if "bench-token" in runs else runs[0]
        print(f"no recorded run; recording {surface} ...", file=sys.stderr)
        (STATIC / "run.json").write_text(record_run(surface))

    host = "0.0.0.0" if args.tailscale else "127.0.0.1"  # noqa: S104 - tailnet, gated
    server = ThreadingHTTPServer((host, args.port), Handler)
    server.daemon_threads = True

    print(f"flypaper dashboard on http://127.0.0.1:{args.port}")
    if args.tailscale:
        name, ipv4 = tailnet_identity()
        for address in (name, ipv4):
            if address:
                print(f"  tailnet: http://{address}:{args.port}/")
        url, note = start_tailscale_serve(args.port, args.https_port)
        if url:
            print(f"  tailnet HTTPS: {url}")
        for line in note.splitlines():
            print(f"  {line}")
        print("  tailnet only - never published to the internet.")
    print(f"  corpora: {', '.join(runs) or '(none captured)'}")

    if args.open:
        threading.Timer(0.6, lambda: webbrowser.open(f"http://127.0.0.1:{args.port}")).start()

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
