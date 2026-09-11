"""The `fly` command.

`fly` never initiates a request. It reads results an operator has already generated.
Stage-two re-fetch (M5) will re-request only URLs already present in the input stream, and
only under an explicit `--scope` file.
"""

from __future__ import annotations

import argparse
import json
import sys

from flypaper import __version__
from flypaper.encode.channels import CHANNEL_SET_VERSION
from flypaper.ingest.ffuf import AUTO, BASE64, PLAIN, ParseStats
from flypaper.ingest.stream import iter_batch, iter_stdin, iter_tail

__all__ = ["main"]


def _emit(result, out) -> None:
    print(
        json.dumps(
            {
                "url": result.url,
                "word": result.word,
                "ffufhash": result.ffufhash,
                "status": result.status,
                "length": result.length,
                "words": result.words,
                "lines": result.lines,
                "content_type": result.content_type,
                "redirect_location": result.redirect_location,
                "host": result.host,
                "duration_ms": round(result.duration_ms, 3),
            },
            ensure_ascii=False,
        ),
        file=out,
    )


def cmd_ingest(args: argparse.Namespace) -> int:
    """Parse fuzzer output and normalise it. No scoring, no filtering, no ranking."""
    stats = ParseStats()
    encoding = args.input_encoding
    if args.file:
        if args.follow:
            source = iter_tail(args.file, encoding=encoding or BASE64, stats=stats)
        else:
            source = iter_batch(args.file, encoding=encoding, stats=stats)
    else:
        source = iter_stdin(encoding=encoding or BASE64, stats=stats)

    for result in source:
        if not args.quiet:
            _emit(result, sys.stdout)

    print(f"flypaper: {stats.summary()}", file=sys.stderr)
    return 0


def cmd_rank(args: argparse.Namespace) -> int:
    print(
        "fly rank is not implemented yet.\n"
        "  Ranking arrives at M4, after the encoder (M2) and the connectome FlyHash "
        "benchmark (M3) have passed their gates.\n"
        "  `fly ingest` parses and normalises fuzzer output today.",
        file=sys.stderr,
    )
    return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fly",
        description=(
            "Rank web fuzzing output by structural novelty. Ranks, does not detect: "
            "a novel response is a statistical outlier, not a vulnerability."
        ),
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"flypaper {__version__} (channel set {CHANNEL_SET_VERSION})",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    ingest = sub.add_parser("ingest", help="parse ffuf output and print normalised records")
    ingest.add_argument("--file", help="read a results file instead of stdin")
    ingest.add_argument(
        "--follow",
        action="store_true",
        help="with --file, follow a file still being written instead of reading it once",
    )
    ingest.add_argument(
        "--input-encoding",
        choices=[BASE64, PLAIN, AUTO],
        default=None,
        help=(
            "how the ffuf `input` map is encoded. Default: base64 for a stream "
            "(`ffuf -json`), plain for a results file (`ffuf -of json`). Override only if "
            "the producer is not ffuf."
        ),
    )
    ingest.add_argument("-q", "--quiet", action="store_true", help="print only the summary")
    ingest.set_defaults(func=cmd_ingest)

    rank = sub.add_parser("rank", help="rank results by novelty (M4)")
    rank.add_argument("file", nargs="?", help="a completed ffuf results file")
    rank.set_defaults(func=cmd_rank)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except BrokenPipeError:
        # `fly ingest | head` is a normal way to use this.
        return 0
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
