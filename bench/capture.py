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
import os
import subprocess
import sys
import urllib.request
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

#: The operator's own authorised live host, if they have one. Never a default: see the
#: `operator-live` surface below.
LIVE_TARGET = os.environ.get("FLYPAPER_LIVE_TARGET", "").rstrip("/")

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
    "bench-sprawl": {
        "url": f"{BENCH}/sprawl/FUZZ",
        "hits": _bench_hits("/sprawl/"),
        "subtle_hits": _bench_subtle("/sprawl/"),
        "scenario": (
            "seven noise populations across four content types and six status codes, each "
            "jittering internally so nothing repeats byte-for-byte - a second, "
            "differently-built heterogeneous surface, captured to test whether the "
            "measured fan-in distribution reproduces off bench-mixed"
        ),
        "target": "bench/target/server.py (ours)",
        "authorization": "our own code, loopback only",
        "wordlist": "sprawl-words.txt",
    },
    "bench-stable": {
        "url": f"{BENCH}/stable/FUZZ",
        "hits": _bench_hits("/stable/"),
        "subtle_hits": _bench_subtle("/stable/"),
        "scenario": "control: byte-identical 404s, nothing jitters",
        "target": "bench/target/server.py (ours)",
        "authorization": "our own code, loopback only",
    },
    # The one live surface, and it has no default target on purpose. A public repository
    # must not ship a hardcoded third-party hostname that anyone cloning it would then
    # scan by running `--all-local`'s neighbour command. Set FLYPAPER_LIVE_TARGET to a host
    # you are authorised against; without it this surface is skipped.
    "operator-live": {
        "url": f"{LIVE_TARGET}/FUZZ" if LIVE_TARGET else "",
        "hits": [],
        "scenario": "live surface: a real host, captured under the live rate and volume caps",
        "target": LIVE_TARGET or "(unset: export FLYPAPER_LIVE_TARGET)",
        "authorization": "operator asserts authorisation by setting FLYPAPER_LIVE_TARGET",
        "live": True,
    },
}


def ffuf_available() -> bool:
    try:
        subprocess.run(["ffuf", "-V"], capture_output=True, timeout=10, check=True)
    except (subprocess.SubprocessError, OSError):
        return False
    return True


def preflight(name: str) -> None:
    """Refuse to capture against a target that does not know this surface.

    Our own bench target serves its ground truth at `/labels.json`, so we can ask it what
    it thinks it is serving rather than assume. Without this, a stale process already
    holding the port answers every request with its generic fallback, ffuf records 1,998
    perfectly well-formed responses, and the corpus looks fine: same record count, same
    schema, one noise population instead of seven. That happened. Nothing downstream can
    detect it, because "every response identical" is a legitimate thing for a surface to
    be - `bench-stable` is exactly that on purpose.

    Only applies to our own target. ffufme and live hosts publish no such manifest, and
    guessing one from response shape would be the same assumption this exists to remove.
    """
    surface = SURFACES[name]
    if not surface["url"].startswith(BENCH):
        return
    try:
        with urllib.request.urlopen(f"{BENCH}/labels.json", timeout=5) as response:
            served = json.loads(response.read())
    except (OSError, ValueError) as exc:
        raise SystemExit(
            f"[{name}] cannot read {BENCH}/labels.json: {exc}\n"
            f"Start it with: python bench/target/server.py --port {BENCH.rsplit(':', 1)[1]}"
        ) from exc

    prefix = "/" + surface["url"].removeprefix(BENCH + "/").split("/", 1)[0] + "/"
    known = {path.rsplit("/", 1)[-1] for path in served if path.startswith(prefix)}
    missing = sorted(set(surface["hits"]) - known)
    if missing:
        raise SystemExit(
            f"[{name}] the server on {BENCH} does not serve {prefix} as this surface "
            f"defines it - it has no labels for {missing}.\n"
            f"An older process is almost certainly still holding the port. Find it with "
            f"`ss -lptn 'sport = :{BENCH.rsplit(':', 1)[1]}'` and restart the target."
        )


def capture(name: str, rate: int, threads: int) -> dict:
    preflight(name)
    surface = SURFACES[name]
    if not surface["url"]:
        raise SystemExit(
            f"[{name}] has no target. This surface is deliberately unset: point it at a host "
            f"you are authorised against with\n"
            f"    export FLYPAPER_LIVE_TARGET=https://host.you.own\n"
            f"It is captured at {LIVE_RATE_CEILING} requests/second and at most "
            f"{LIVE_MAX_REQUESTS} requests, both fixed in code rather than by flag."
        )
    # A surface may bring its own haystack; bench-sprawl does, so that adding it could not
    # rewrite the list every already-published number was measured in.
    wordlist = CORPUS / surface["wordlist"] if surface.get("wordlist") else WORDLIST
    if surface.get("live"):
        rate = min(rate, LIVE_RATE_CEILING)
        threads = min(threads, 4)
        words = wordlist.read_text(encoding="utf-8").split("\n")
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
        # `wordlist`, not `WORDLIST`. These were different names for most of this file's
        # life and the command used the wrong one, so the live-surface truncation below was
        # computed, written to disk, named in the provenance record - and never passed to
        # ffuf. A live capture sent the full list every time. That safeguard exists because
        # a 2,000-word run at a polite 10/s tripped a real target's edge protection, which
        # is exactly what it was supposed to prevent, and the provenance said it had.
        str(wordlist),
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
    for chosen_name in names:
        own = SURFACES[chosen_name].get("wordlist")
        if own and not (CORPUS / own).exists():
            raise SystemExit(
                f"{CORPUS / own} is missing. Run: python bench/make_corpus_words.py "
                f"--surface {chosen_name} --out bench/corpus/{own}"
            )

    manifest_path = CORPUS / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    for name in dict.fromkeys(names):
        manifest[name] = capture(name, args.rate, args.threads)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"manifest -> {manifest_path.relative_to(REPO_ROOT)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
