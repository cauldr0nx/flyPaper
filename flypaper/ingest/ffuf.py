"""Schema-tolerant parser for ffuf result records.

ffuf emits the same logical record in two different shapes, and the difference is not
documented anywhere but the source:

  * `-json` (newline-delimited, stdout) marshals `pkg/ffuf.Result`, whose `Input` field is
    `map[string][]byte`. Go base64-encodes `[]byte`, so the fuzzed word arrives as
    `"FUZZ": "YWRtaW4="`.
  * `-of json` / `-of ejson` (a file) marshals `pkg/output.JsonResult`, whose `Input` is
    `map[string]string`. The same word arrives as `"FUZZ": "admin"`.

Which of the two applies is a property of the source, not of the value, and it is decided
that way here. Sniffing each value instead - "does it decode as base64 and round-trip?" -
is the obvious shortcut and it is wrong: on the M1 capture, 524 of 9,998 real wordlist
entries satisfy that test while being plain text, among them `test`, `soap`, `temp`,
`register`, `settings` and `database`. Sniffing would have silently replaced 5.2% of the
words with binary garbage, which is the exact failure this parser exists to avoid.

`duration` is a Go `time.Duration` in both, which marshals as an integer count of
**nanoseconds** - not milliseconds, and not seconds.

Nothing here filters, thresholds or scores. This stage only normalises, counts what it
could not read, and says so. Unknown fields are kept in `extra` rather than dropped: a
future ffuf release adding a field must surface as a warning, never as silent data loss.

Verified against ffuf v2.1.0-dev; see reports/m1-ingest.md for the capture.
"""

from __future__ import annotations

import base64
import binascii
import json
import sys
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from itertools import chain
from pathlib import Path
from typing import Any

__all__ = [
    "AUTO",
    "BASE64",
    "KNOWN_FIELDS",
    "PLAIN",
    "FfufResult",
    "ParseStats",
    "detect_encoding",
    "iter_ndjson",
    "load_batch",
    "parse_record",
]

# Every field ffuf is known to emit. Anything outside this set is schema drift: it is
# retained in `FfufResult.extra` and warned about once.
KNOWN_FIELDS = frozenset(
    {
        "input",
        "position",
        "status",
        "length",
        "words",
        "lines",
        "content-type",
        "redirectlocation",
        "url",
        "duration",
        "scraper",
        "resultfile",
        "host",
    }
)

NS_PER_MS = 1_000_000.0

# How the `input` map is encoded. A property of the ffuf output mode, not of the value.
BASE64 = "base64"  # `-json` on stdout
PLAIN = "plain"  # `-of json` / `-of ejson` in a file
AUTO = "auto"  # sample the stream and decide; see `detect_encoding`


@dataclass(frozen=True, slots=True)
class FfufResult:
    """One response, normalised. Metadata only - never a body.

    `inputs` maps each fuzz keyword to its decoded word. ffuf injects `FFUFHASH` into
    every record alongside the wordlist keywords, whether or not the URL uses it, which is
    what makes out-of-band callback correlation possible later.
    """

    inputs: dict[str, str]
    url: str
    status: int
    length: int
    words: int
    lines: int
    content_type: str = ""
    redirect_location: str = ""
    host: str = ""
    duration_ms: float = 0.0
    position: int = 0
    scraper: dict[str, list[str]] = field(default_factory=dict)
    result_file: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def word(self) -> str:
        """The fuzzed word, for the common single-keyword case.

        Prefers `FUZZ`, then any non-FFUFHASH keyword, so a `-w list:WORD` run still
        answers. Empty when the run is keyword-less or command-driven.
        """
        if "FUZZ" in self.inputs:
            return self.inputs["FUZZ"]
        for key, value in self.inputs.items():
            if key != "FFUFHASH":
                return value
        return ""

    @property
    def ffufhash(self) -> str:
        """ffuf's per-request hash, the handle for correlating a delayed callback."""
        return self.inputs.get("FFUFHASH", "")


@dataclass
class ParseStats:
    """What the parser read, and what it could not. Never thrown away silently."""

    ok: int = 0
    malformed: int = 0
    empty: int = 0
    unknown_fields: set[str] = field(default_factory=set)
    coercion_failures: dict[str, int] = field(default_factory=dict)

    @property
    def total(self) -> int:
        return self.ok + self.malformed

    def summary(self) -> str:
        parts = [f"{self.ok} records", f"{self.malformed} malformed"]
        if self.empty:
            parts.append(f"{self.empty} blank lines")
        if self.unknown_fields:
            parts.append("unknown fields: " + ", ".join(sorted(self.unknown_fields)))
        if self.coercion_failures:
            bad = ", ".join(f"{k}x{v}" for k, v in sorted(self.coercion_failures.items()))
            parts.append(f"coercion failures: {bad}")
        return " | ".join(parts)


class _Warner:
    """Warns once per distinct message. A 10,000-record stream must not emit 10,000 lines."""

    def __init__(self, stream=None):
        self._seen: set[str] = set()
        self._stream = stream

    def __call__(self, message: str) -> None:
        if message in self._seen:
            return
        self._seen.add(message)
        print(f"flypaper: warning: {message}", file=self._stream or sys.stderr)


def _decode_inputs(raw: dict, encoding: str) -> dict[str, str]:
    """Decode an `input` map under a known encoding. `encoding` is never guessed per value."""
    out: dict[str, str] = {}
    for key, value in raw.items():
        name = _as_str(key)
        if not isinstance(value, str):
            out[name] = "" if value is None else str(value)
            continue
        if encoding != BASE64 or not value:
            out[name] = value
            continue
        try:
            decoded = base64.b64decode(value, validate=True)
        except (binascii.Error, ValueError):
            # Declared base64 but is not. Keep the literal rather than lose the word.
            out[name] = value
            continue
        out[name] = decoded.decode("utf-8", errors="replace")
    return out


def _looks_like_base64(value: str) -> bool:
    if not value:
        return False
    try:
        raw = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        return False
    return base64.b64encode(raw).decode("ascii") == value


def detect_encoding(records: Iterable[dict[str, Any]]) -> str:
    """Decide the input encoding for a whole stream from a sample of its records.

    Only for `AUTO`, where the caller genuinely does not know which ffuf mode produced the
    data. The test is one-sided on purpose: every base64 value decodes, so a single value
    that does not decode proves the stream is plain, while no finite sample can prove the
    converse. A stream of entirely-decodable values is therefore *assumed* base64, and a
    caller who knows better should pass `PLAIN` rather than rely on this.
    """
    saw_value = False
    for record in records:
        raw = record.get("input")
        if not isinstance(raw, dict):
            continue
        for value in raw.values():
            if not isinstance(value, str) or not value:
                continue
            saw_value = True
            if not _looks_like_base64(value):
                return PLAIN
    return BASE64 if saw_value else PLAIN


def _as_int(value: Any, default: int, key: str, stats: ParseStats | None) -> int:
    try:
        if isinstance(value, bool):
            raise TypeError("bool is not a count")
        return int(value)
    except (TypeError, ValueError):
        if value is not None and stats is not None:
            stats.coercion_failures[key] = stats.coercion_failures.get(key, 0) + 1
        return default


def _as_str(value: Any) -> str:
    if value is None:
        return ""
    return value if isinstance(value, str) else str(value)


def parse_record(
    record: dict[str, Any],
    *,
    encoding: str = BASE64,
    stats: ParseStats | None = None,
    warn: _Warner | None = None,
) -> FfufResult:
    """Normalise one decoded ffuf record. Never raises on a field it does not recognise.

    `encoding` says how this record's `input` map is encoded - `BASE64` for the `-json`
    stdout shape, `PLAIN` for the `-of json` file shape. It defaults to `BASE64` because
    the streaming path is the one records arrive on one at a time.
    """
    raw_inputs = record.get("input")
    inputs: dict[str, str] = {}
    if isinstance(raw_inputs, dict):
        inputs = _decode_inputs(raw_inputs, encoding)
    elif raw_inputs is not None and warn is not None:
        warn(f"`input` is {type(raw_inputs).__name__}, expected object; treated as empty")

    raw_scraper = record.get("scraper")
    scraper: dict[str, list[str]] = {}
    if isinstance(raw_scraper, dict):
        for key, value in raw_scraper.items():
            if isinstance(value, list):
                scraper[_as_str(key)] = [_as_str(v) for v in value]
            elif value is not None:
                scraper[_as_str(key)] = [_as_str(value)]

    unknown = set(record) - KNOWN_FIELDS
    if unknown:
        if stats is not None:
            stats.unknown_fields |= unknown
        if warn is not None:
            warn(
                "unrecognised ffuf field(s) "
                + ", ".join(sorted(unknown))
                + " - kept in .extra, check for an ffuf schema change"
            )

    return FfufResult(
        inputs=inputs,
        url=_as_str(record.get("url")),
        status=_as_int(record.get("status"), 0, "status", stats),
        length=_as_int(record.get("length"), 0, "length", stats),
        words=_as_int(record.get("words"), 0, "words", stats),
        lines=_as_int(record.get("lines"), 0, "lines", stats),
        content_type=_as_str(record.get("content-type")),
        redirect_location=_as_str(record.get("redirectlocation")),
        host=_as_str(record.get("host")),
        duration_ms=_as_int(record.get("duration"), 0, "duration", stats) / NS_PER_MS,
        position=_as_int(record.get("position"), 0, "position", stats),
        scraper=scraper,
        result_file=_as_str(record.get("resultfile")),
        extra={k: record[k] for k in unknown},
    )


def _decoded_lines(
    lines: Iterable[str],
    stats: ParseStats,
    warn: _Warner,
) -> Iterator[dict[str, Any]]:
    """JSON-decode each line, counting and skipping what will not read."""
    for raw in lines:
        line = raw.strip()
        if not line:
            stats.empty += 1
            continue
        try:
            record = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError):
            stats.malformed += 1
            warn("skipping malformed JSON line(s); see the malformed count in the summary")
            continue
        if not isinstance(record, dict):
            stats.malformed += 1
            warn(f"skipping non-object JSON line(s) of type {type(record).__name__}")
            continue
        yield record


# How many records `AUTO` buffers before committing to an encoding. Enough that a real run
# will contain a word that is not valid base64, small enough to stay streaming.
AUTO_SAMPLE = 64


def iter_ndjson(
    lines: Iterable[str],
    *,
    encoding: str = BASE64,
    stats: ParseStats | None = None,
    warn_stream=None,
) -> Iterator[FfufResult]:
    """Parse newline-delimited ffuf records, skipping and counting what will not read.

    A malformed line is never fatal. A fuzzing run is long and expensive; losing it because
    one line was truncated by a killed pipe would be the wrong trade.

    `encoding` defaults to `BASE64`, the measured shape of `ffuf -json` on stdout. `AUTO`
    buffers the first `AUTO_SAMPLE` records to decide, which costs a bounded amount of
    memory and delays the first result by that many lines.
    """
    if stats is None:
        stats = ParseStats()
    warn = _Warner(warn_stream)
    records = _decoded_lines(lines, stats, warn)

    if encoding == AUTO:
        head: list[dict[str, Any]] = []
        for record in records:
            head.append(record)
            if len(head) >= AUTO_SAMPLE:
                break
        encoding = detect_encoding(head)
        warn(f"input encoding auto-detected as {encoding}")
        records = chain(head, records)

    for record in records:
        stats.ok += 1
        yield parse_record(record, encoding=encoding, stats=stats, warn=warn)


def load_batch(
    path: str | Path,
    *,
    encoding: str | None = None,
    stats: ParseStats | None = None,
    warn_stream=None,
) -> list[FfufResult]:
    """Read a completed ffuf results file.

    Handles `-of json` / `-of ejson` (a `{commandline, time, results, config}` wrapper), a
    bare array of records, and a file that is actually newline-delimited JSON - which is
    what `-json > out.json` produces, and which people do write.

    `encoding` defaults per shape: `PLAIN` for a JSON document, because that is the
    measured shape of `-of json`, and `BASE64` for the NDJSON fallback, because that is the
    measured shape of `-json`. Pass it explicitly to override either.
    """
    if stats is None:
        stats = ParseStats()
    warn = _Warner(warn_stream)
    text = Path(path).read_text(encoding="utf-8", errors="replace")

    try:
        document = json.loads(text)
    except json.JSONDecodeError:
        return list(
            iter_ndjson(
                text.splitlines(),
                encoding=encoding or BASE64,
                stats=stats,
                warn_stream=warn_stream,
            )
        )
    encoding = encoding or PLAIN

    if isinstance(document, dict):
        records = document.get("results")
        if records is None:
            warn("batch file has no `results` key; treating the object as a single record")
            records = [document]
    elif isinstance(document, list):
        records = document
    else:
        warn(f"batch file is a bare {type(document).__name__}; nothing to read")
        return []

    out: list[FfufResult] = []
    for record in records:
        if not isinstance(record, dict):
            stats.malformed += 1
            continue
        stats.ok += 1
        out.append(parse_record(record, encoding=encoding, stats=stats, warn=warn))
    return out
