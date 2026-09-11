#!/usr/bin/env python3
"""Capture the replay corpus.

Live targets cannot be evaluated against directly - non-deterministic, slow, and ethically
constrained - so every surface is captured once and replayed forever.

Each surface records its provenance: source, date, authorization basis, and the rate it was
captured at. A surface marked `live: true` is hard-capped at `LIVE_RATE_CEILING` requests
per second and cannot be raised from the command line; the cap is in the code because a
flag is too easy to get wrong.

    python bench/capture.py --list
    python bench/capture.py --surface bench-calib
    python bench/capture.py --all-local

Bodies are never stored. The capture holds ffuf's response metadata and nothing else.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CORPUS = REPO_ROOT / "bench" / "corpus"
WORDLIST = CORPUS / "corpus-words.txt"

# A live third-party surface is never captured faster than this, whatever the flags say.
LIVE_RATE_CEILING = 10

# ...nor for more requests than this. A rate ceiling alone is not enough, and this repository
# learned that the expensive way: a capture invoked with the full 2,000-word corpus list ran
# at a polite 10/s for 200 seconds and still tripped the target's edge protection, which then
# returned 403 to everything including its own front page. Volume is a separate axis from
# rate and needs its own ceiling.
LIVE_MAX_REQUESTS = 250

FFUFME = "http://127.0.0.1:8099"
BENCH = "http://127.0.0.1:8110"

# Labels come from the target that serves them, so the corpus and the ground truth cannot
# drift apart.
sys.path.insert(0, str(Path(__file__).resolve().parent / "target"))
from server import HITS as BENCH_HITS  # noqa: E402


def _bench_hits(prefix: str) -> list[str]:
    return sorted(p.rsplit("/", 1)[-1] for p in BENCH_HITS if p.startswith(prefix))


def _bench_subtle(prefix: str) -> list[str]:
    return sorted(
        p.rsplit("/", 1)[-1]
        for p, kind in BENCH_HITS.items()
        if p.startswith(prefix) and kind == "subtle"
    )


SURFACES: dict[str, dict] = {
    "ffufme-no404": {
        "url": f"{FFUFME}/cd/no404/FUZZ",
        "hits": ["secret"],
        "scenario": "wildcard / soft-404 host: every unknown path answers 200",
        "target": "ffufme container (local)",
        "authorization": "built from public source and run by the operator on loopback",
    },
    "ffufme-basic": {
        "url": f"{FFUFME}/cd/basic/FUZZ",
        "hits": ["class"],
        "scenario": "ordinary surface: real 404s, one real hit",
        "target": "ffufme container (local)",
        "authorization": "built from public source and run by the operator on loopback",
    },
    "bench-token": {
        "url": f"{BENCH}/token/FUZZ",
        "hits": _bench_hits("/token/"),
        "subtle_hits": _bench_subtle("/token/"),
        "scenario": "token rotation: identical page, variable-length CSRF token, defeating -fs",
        "target": "bench/target/server.py (ours)",
        "authorization": "our own code, loopback only",
    },
    "bench-calib": {
        "url": f"{BENCH}/calib/FUZZ",
        "hits": _bench_hits("/calib/"),
        "subtle_hits": _bench_subtle("/calib/"),
        "scenario": "ffuf issue #387: the hit's word count collides with the autocalibrated filter",
        "target": "bench/target/server.py (ours)",
        "authorization": "our own code, loopback only",
    },
    "bench-collide": {
        "url": f"{BENCH}/collide/FUZZ",
        "hits": _bench_hits("/collide/"),
        "subtle_hits": _bench_subtle("/collide/"),
        "scenario": (
            "ffuf issue #387, faithfully: noise answers 200 and every response shares the "
            "hits' word count, so autocalibration has only size/words/lines to work with"
        ),
        "target": "bench/target/server.py (ours)",
        "authorization": "our own code, loopback only",
    },
    "bench-mixed": {
        "url": f"{BENCH}/mixed/FUZZ",
        "hits": _bench_hits("/mixed/"),
        "subtle_hits": _bench_subtle("/mixed/"),
        "scenario": (
            "four noise populations at once - an HTML 404, a login redirect, a JSON 403 "
            "and a 200 'no results' page - with hits hiding inside them"
        ),
        "target": "bench/target/server.py (ours)",
        "authorization": "our own code, loopback only",
    },
    "bench-stable": {
        "url": f"{BENCH}/stable/FUZZ",
        "hits": _bench_hits("/stable/"),
        "subtle_hits": _bench_subtle("/stable/"),
        "scenario": "control: byte-identical 404s, nothing jitters",
        "target": "bench/target/server.py (ours)",
        "authorization": "our own code, loopback only",
    },
    "mcware-live": {
        "url": "https://www.mcware.org/FUZZ",
        "hits": [],
        "scenario": "live surface: constant-size rendered 404, Vercel edge",
        "target": "www.mcware.org",
        "authorization": "operator authorized this target on 2026-09-11",
        "live": True,
    },
}


def ffuf_available() -> bool:
    try:
        subprocess.run(["ffuf", "-V"], capture_output=True, timeout=10, check=True)
    except (subprocess.SubprocessError, OSError):
        return False
    return True


def capture(name: str, rate: int, threads: int) -> dict:
    surface = SURFACES[name]
    wordlist = WORDLIST
    if surface.get("live"):
        rate = min(rate, LIVE_RATE_CEILING)
        threads = min(threads, 4)
        words = WORDLIST.read_text(encoding="utf-8").split("\n")
        if len([w for w in words if w]) > LIVE_MAX_REQUESTS:
            wordlist = CORPUS / f".live-{LIVE_MAX_REQUESTS}.txt"
            kept = [w for w in words if w][:LIVE_MAX_REQUESTS]
            wordlist.write_text("\n".join(kept) + "\n")
            print(
                f"[{name}] live surface: wordlist truncated to {LIVE_MAX_REQUESTS} words",
                file=sys.stderr,
            )

    out = CORPUS / f"{name}.jsonl"
    CORPUS.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffuf",
        "-mc",
        "all",
        "-json",
        "-u",
        surface["url"],
        "-w",
        str(WORDLIST),
        "-noninteractive",
        "-t",
        str(threads),
        "-rate",
        str(rate),
    ]
    started = datetime.now(UTC)
    print(f"[{name}] {surface['url']}  rate={rate}/s threads={threads}", file=sys.stderr)
    with out.open("wb") as handle:
        proc = subprocess.run(cmd, stdout=handle, stderr=subprocess.PIPE)
    if proc.returncode != 0:
        raise SystemExit(f"[{name}] ffuf exited {proc.returncode}:\n{proc.stderr.decode()[-800:]}")

    records = sum(1 for _ in out.open("rb"))
    print(f"[{name}] {records} records -> {out.relative_to(REPO_ROOT)}", file=sys.stderr)
    return {
        "surface": name,
        "url": surface["url"],
        "scenario": surface["scenario"],
        "target": surface["target"],
        "authorization": surface["authorization"],
        "live": bool(surface.get("live")),
        "captured_utc": started.isoformat(timespec="seconds"),
        "rate_limit_per_second": rate,
        "threads": threads,
        "wordlist": wordlist.name,
        "max_requests": LIVE_MAX_REQUESTS if surface.get("live") else None,
        "records": records,
        "hits": surface["hits"],
        "subtle_hits": surface.get("subtle_hits", []),
        "file": out.name,
        "bodies_retained": False,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--surface", action="append", help="capture one named surface")
    ap.add_argument("--all-local", action="store_true", help="capture every non-live surface")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--rate", type=int, default=500, help="requests/second (live is capped)")
    ap.add_argument("--threads", type=int, default=20)
    args = ap.parse_args()

    if args.list:
        for name, s in SURFACES.items():
            flag = " [LIVE, capped]" if s.get("live") else ""
            print(f"{name:18}{flag}\n    {s['url']}\n    {s['scenario']}")
        return 0

    names = list(args.surface or [])
    if args.all_local:
        names += [n for n, s in SURFACES.items() if not s.get("live")]
    if not names:
        ap.error("nothing to do: pass --surface, --all-local or --list")
    unknown = [n for n in names if n not in SURFACES]
    if unknown:
        ap.error(f"unknown surface(s): {', '.join(unknown)}")
    if not ffuf_available():
        raise SystemExit("ffuf is not on PATH")
    if not WORDLIST.exists():
        raise SystemExit(f"{WORDLIST} is missing. Run: python bench/make_corpus_words.py")

    manifest_path = CORPUS / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    for name in dict.fromkeys(names):
        manifest[name] = capture(name, args.rate, args.threads)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"manifest -> {manifest_path.relative_to(REPO_ROOT)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
