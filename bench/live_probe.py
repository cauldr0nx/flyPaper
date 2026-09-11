#!/usr/bin/env python3
"""Run flypaper against a real, in-scope target - carefully.

This is the harness for testing the ranker on something that is not synthetic. It exists
because the corpus in `bench/corpus/` is synthetic and small, and every report in
`reports/` says so.

It is built around one lesson already paid for. A capture of an authorized host at a polite
10 requests/second still tripped that host's edge protection, because **volume is a separate
axis from rate** and nothing was watching the responses to notice the target had started
refusing. So:

  * A scope file is mandatory, and the host must match it. Generate one with `fly scope`
    from the program's own published scope rather than typing it.
  * Rate and total requests are both capped, in the code, below anything a flag can raise.
  * **The scan aborts the moment the target's answers change character.** A wall of 403s or
    429s that was not there at the start means the target is refusing, and continuing is
    both rude and pointless. This is the habituation inversion from section 5 of the brief:
    a WAF that starts blocking mid-scan is a change in the baseline, and the right response
    is to stop and say so.

    python bench/live_probe.py --host www.example.com --scope scope.txt --dry-run
    python bench/live_probe.py --host www.example.com --scope scope.txt

Nothing is committed: captures land in `bench/corpus/live/`, which is gitignored.
"""

from __future__ import annotations

import argparse
import base64
import collections
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from flypaper.ingest.ffuf import parse_record  # noqa: E402
from flypaper.rank.report import RollingPercentile  # noqa: E402
from flypaper.rank.score import Ranker  # noqa: E402
from flypaper.stage2.scope import Scope  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = REPO_ROOT / "bench" / "corpus" / "live"

#: Ceilings. Both are in the code because a flag is too easy to get wrong, and because the
#: last time only one of them existed it was not enough.
MAX_RATE = 10
MAX_REQUESTS = 250
MAX_THREADS = 4

#: Statuses that mean "go away" rather than "here is a page".
REFUSAL = frozenset({401, 403, 405, 406, 429, 503})

#: Block detection. Compare the most recent `WINDOW` responses against the first `BASELINE`.
BASELINE = 40
WINDOW = 25
REFUSAL_JUMP = 0.5  # share of the window that must newly be refusals to call it a block


class Blocked(RuntimeError):
    """The target started refusing. Always stops the scan."""


class BlockWatch:
    """Watch for the target's answers changing character mid-scan.

    Deliberately one-sided. It is looking for the baseline *becoming* refusals, not for
    refusals as such: a target that answers 403 to everything from the first request is
    simply a target that answers 403, and that is a finding for the ranker, not a reason to
    abort.
    """

    def __init__(self) -> None:
        self.statuses: list[int] = []
        self.baseline_refusal: float | None = None

    def observe(self, status: int) -> None:
        self.statuses.append(status)
        n = len(self.statuses)

        if n == BASELINE:
            head = self.statuses[:BASELINE]
            self.baseline_refusal = sum(s in REFUSAL for s in head) / BASELINE
            return
        if self.baseline_refusal is None or n < BASELINE + WINDOW:
            return

        recent = self.statuses[-WINDOW:]
        share = sum(s in REFUSAL for s in recent) / WINDOW
        if share - self.baseline_refusal >= REFUSAL_JUMP:
            counts = collections.Counter(recent).most_common(3)
            raise Blocked(
                f"the target started refusing after {n} requests: "
                f"{share:.0%} of the last {WINDOW} responses are refusals against "
                f"{self.baseline_refusal:.0%} at the start ({counts}). Stopping."
            )


def load_wordlist(path: Path | None, limit: int) -> list[str]:
    """Meaningful paths only. Random junk is wasted requests against someone's production."""
    if path:
        words = [w.strip() for w in path.read_text().splitlines() if w.strip()]
    else:
        sys.path.insert(0, str(REPO_ROOT / "bench"))
        from make_wordlist import COMMON

        words = list(dict.fromkeys(COMMON))
    return words[:limit]


def word_of(record: dict) -> str:
    raw = record.get("input", {}).get("FUZZ", "")
    try:
        return base64.b64decode(raw, validate=True).decode("utf-8", "replace")
    except Exception:  # noqa: BLE001
        return raw


def probe(host: str, scope: Scope, words: list[str], rate: int, scheme: str) -> dict:
    url = f"{scheme}://{host}/FUZZ"
    decision = scope.decide(f"{scheme}://{host}/")
    if not decision.allowed:
        raise SystemExit(f"refusing: {host} is not in scope ({decision.reason})")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    wordlist = OUT_DIR / f".words-{host}.txt"
    wordlist.write_text("\n".join(words) + "\n")
    capture = OUT_DIR / f"{host}.jsonl"

    command = [
        "ffuf",
        "-mc",
        "all",
        "-json",
        "-u",
        url,
        "-w",
        str(wordlist),
        "-noninteractive",
        "-t",
        str(MAX_THREADS),
        "-rate",
        str(rate),
        "-timeout",
        "8",
        "-H",
        "User-Agent: flypaper/0.1 (bug bounty research; contact via program)",
    ]
    print(f"  {url}  {len(words)} words at {rate}/s", file=sys.stderr)

    ranker = Ranker()
    gate = RollingPercentile(99.0, minimum=60)
    watch = BlockWatch()
    scored: list[tuple[float, dict]] = []
    statuses: collections.Counter = collections.Counter()
    blocked: str | None = None
    started = time.monotonic()

    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    try:
        with capture.open("w", encoding="utf-8") as sink:
            for raw in process.stdout:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("{"):
                    continue
                sink.write(line + "\n")
                record = json.loads(line)
                result = parse_record(record)
                statuses[result.status] += 1

                item = ranker.score(result)
                gate.observe(item.novelty)
                scored.append(
                    (
                        item.novelty,
                        {
                            "word": result.word,
                            "status": result.status,
                            "length": result.length,
                            "words": result.words,
                            "lines": result.lines,
                            "type": result.content_type,
                        },
                    )
                )
                try:
                    watch.observe(result.status)
                except Blocked as stop:
                    blocked = str(stop)
                    break
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
        wordlist.unlink(missing_ok=True)

    scored.sort(key=lambda pair: -pair[0])

    # The live order is what a running scan can produce, and on a short run it is mostly
    # cold start: the first responses score high because nothing is familiar yet. Re-rank
    # the capture in two passes now the run is over, which is what `fly rank` does with a
    # completed file and what the operator should actually read.
    from flypaper.ingest.stream import iter_batch
    from flypaper.rank.score import rank as rank_offline

    final = [
        (
            round(item.novelty, 4),
            {
                "word": item.result.word,
                "status": item.result.status,
                "length": item.result.length,
                "words": item.result.words,
                "lines": item.result.lines,
                "type": item.result.content_type,
            },
        )
        for item in rank_offline(iter_batch(capture), passes=2)[:12]
    ]

    return {
        "host": host,
        "requests": sum(statuses.values()),
        "elapsed_s": round(time.monotonic() - started, 1),
        "statuses": dict(statuses.most_common()),
        "blocked": blocked,
        "capture": str(capture),
        "top_live": [(round(n, 4), d) for n, d in scored[:12]],
        "top_offline": final,
        "saturation": round(ranker.saturation, 4),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--host", required=True, action="append", help="an in-scope host; repeatable")
    ap.add_argument("--scope", required=True, help="a scope file; see `fly scope`")
    ap.add_argument("--scheme", default="https", choices=("https", "http"))
    ap.add_argument("--rate", type=int, default=MAX_RATE)
    ap.add_argument("--requests", type=int, default=MAX_REQUESTS)
    ap.add_argument("--wordlist", type=Path, default=None)
    ap.add_argument("--dry-run", action="store_true", help="check scope and stop")
    args = ap.parse_args()

    scope = Scope.from_file(args.scope)
    rate = min(max(args.rate, 1), MAX_RATE)
    words = load_wordlist(args.wordlist, min(args.requests, MAX_REQUESTS))

    print(
        f"flypaper live probe: {len(args.host)} host(s), {len(words)} words, {rate}/s, "
        f"abort on a {REFUSAL_JUMP:.0%} jump in refusals",
        file=sys.stderr,
    )
    if rate != args.rate or len(words) != args.requests:
        print(f"  capped: rate {rate}/s, {len(words)} requests", file=sys.stderr)

    for host in args.host:
        decision = scope.decide(f"{args.scheme}://{host}/")
        mark = "in scope" if decision.allowed else f"OUT OF SCOPE - {decision.reason}"
        print(f"  {host:44} {mark}", file=sys.stderr)
    if args.dry_run:
        return 0

    results = []
    for host in args.host:
        try:
            results.append(probe(host, scope, words, rate, args.scheme))
        except SystemExit as exc:
            print(f"  {exc}", file=sys.stderr)
    print(json.dumps(results, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
