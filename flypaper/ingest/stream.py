"""Where the records come from: a live pipe, a file being written, or a finished file.

Measured on ffuf v2.1.0-dev: `-json` writes one record per line to stdout as each response
completes, and streams through a pipe unbuffered - 200 records/second out of a `-rate 200`
run, sampled every 5 seconds (reports/m1-ingest.md). So strategy 1 is the live path and
`iter_tail` exists as the documented fallback for a tool that does not stream.

The banner and the progress counter go to stderr, so stdout is pure NDJSON and needs no
pre-filtering. Nothing here initiates a request.
"""

from __future__ import annotations

import sys
import time
from collections.abc import Iterator
from pathlib import Path

from flypaper.ingest.ffuf import BASE64, FfufResult, ParseStats, iter_ndjson, load_batch

__all__ = ["iter_batch", "iter_stdin", "iter_tail"]


def iter_stdin(*, encoding: str = BASE64, stats: ParseStats | None = None) -> Iterator[FfufResult]:
    """Strategy 1: read NDJSON from stdin as the fuzzer produces it.

    ffuf -mc all -json -u http://host/FUZZ -w list.txt | fly ingest
    """
    yield from iter_ndjson(sys.stdin, encoding=encoding, stats=stats)


def iter_tail(
    path: str | Path,
    *,
    encoding: str = BASE64,
    stats: ParseStats | None = None,
    poll: float = 0.25,
    stop_after_idle: float | None = None,
) -> Iterator[FfufResult]:
    """Strategy 2: follow a file that is still being written.

    The fallback for a producer that buffers its stdout. Starts at the beginning, so a
    file that already has content is read in full before the follow begins. `poll` is the
    sleep between reads; `stop_after_idle` ends the iteration after that many seconds
    without a new line, and `None` follows until interrupted.
    """
    path = Path(path)
    if stats is None:
        stats = ParseStats()
    pending = ""
    last_data = time.monotonic()

    with path.open("r", encoding="utf-8", errors="replace") as handle:
        while True:
            chunk = handle.read()
            if chunk:
                last_data = time.monotonic()
                pending += chunk
                # Hold back a trailing partial line: the writer may be mid-record.
                *complete, pending = pending.split("\n")
                yield from iter_ndjson(complete, encoding=encoding, stats=stats)
                continue
            if stop_after_idle is not None and time.monotonic() - last_data >= stop_after_idle:
                break
            time.sleep(poll)

    if pending.strip():
        yield from iter_ndjson([pending], encoding=encoding, stats=stats)


def iter_batch(
    path: str | Path, *, encoding: str | None = None, stats: ParseStats | None = None
) -> Iterator[FfufResult]:
    """Strategy 3: read a finished results file.

    Always supported regardless of which live path works, because it is what makes offline
    replay benchmarking possible at all.
    """
    yield from load_batch(path, encoding=encoding, stats=stats)
