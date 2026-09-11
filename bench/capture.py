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

FFUFME = "http://127.0.0.1:8099"
BENCH = "http://127.0.0.1:8110"

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
        "hits": ["account"],
        "scenario": "token rotation: identical page, variable-length CSRF token, defeating -fs",
        "target": "bench/target/server.py (ours)",
        "authorization": "our own code, loopback only",
    },
    "bench-calib": {
        "url": f"{BENCH}/calib/FUZZ",
        "hits": ["reports"],
        "scenario": "ffuf issue #387: the hit's word count collides with the autocalibrated filter",
        "target": "bench/target/server.py (ours)",
        "authorization": "our own code, loopback only",
    },
    "bench-stable": {
        "url": f"{BENCH}/stable/FUZZ",
        "hits": ["backup"],
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
    if surface.get("live"):
        rate = min(rate, LIVE_RATE_CEILING)
        threads = min(threads, 4)

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
        "wordlist": WORDLIST.name,
        "records": records,
        "hits": surface["hits"],
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
