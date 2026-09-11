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
    """Rank results by novelty. Ranks; does not detect."""
    from flypaper.rank.report import Terminal, write_jsonl
    from flypaper.rank.score import Ranker, rank

    source = iter_batch(args.file) if args.file else iter_stdin()
    kwargs = {
        "channel_set": args.channel_set,
        "projection": args.projection,
        "circuit_path": args.circuit,
        "decay_halflife": args.decay_halflife,
    }

    if args.file and not args.live:
        scored = rank(source, passes=2, **kwargs)
        if args.jsonl:
            write_jsonl(scored[: args.top] if args.top else scored)
            return 0
        term = Terminal(threshold=args.threshold, percentile=args.percentile)
        term.header(args.channel_set, args.projection)
        if args.top:
            # An explicit budget is a request for that many results, not a second filter.
            for item in scored[: args.top]:
                term.result(item, force=True)
        else:
            for item in scored:
                term.result(item)
        term.footer(0.0)
        return 0

    # Live: one pass, scoring each result against only what came before it.
    ranker = Ranker(**kwargs)
    term = None
    if not args.jsonl:
        term = Terminal(threshold=args.threshold, percentile=args.percentile, warmup=args.warmup)
        term.header(args.channel_set, args.projection)
    for item in ranker.stream(source):
        if term is not None:
            term.result(item)
        else:
            print(json.dumps(item.as_dict(), ensure_ascii=False))
    if term is not None:
        term.footer(ranker.saturation)
    return 0


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

    ranker = sub.add_parser("rank", help="rank results by how structurally novel they are")
    ranker.add_argument("file", nargs="?", help="a completed ffuf results file; omit to read stdin")
    ranker.add_argument(
        "--live",
        action="store_true",
        help=(
            "score each result against only what came before it, as a running scan must. "
            "Implied when reading stdin. A completed file is otherwise scored in two passes, "
            "which avoids the cold start where the first few responses look novel merely "
            "because nothing is familiar yet."
        ),
    )
    ranker.add_argument(
        "--channel-set",
        default=CHANNEL_SET_VERSION,
        help="which encoder channel set to use. Scores from different sets are not comparable.",
    )
    ranker.add_argument(
        "--projection",
        choices=("random", "connectome"),
        default="random",
        help=(
            "random is the published FlyHash baseline and the default; connectome uses the "
            "measured MaleCNS wiring and needs --circuit. M3 found them statistically "
            "indistinguishable for novelty detection."
        ),
    )
    ranker.add_argument(
        "--circuit", help="path to an extracted circuit, for --projection connectome"
    )
    ranker.add_argument(
        "--decay-halflife",
        type=float,
        default=None,
        help="records after which a depressed baseline has recovered half way to novel again",
    )
    ranker.add_argument(
        "--threshold",
        type=float,
        default=None,
        help=(
            "absolute novelty cutoff. Off by default: the novelty scale is target-dependent, "
            "so --percentile is usually what you want."
        ),
    )
    ranker.add_argument(
        "--percentile",
        type=float,
        default=99.5,
        help=(
            "show results in the top (100-P)%% most novel for this target, calibrated from "
            "the run itself. A review budget rather than a novelty standard."
        ),
    )
    ranker.add_argument(
        "--warmup",
        type=int,
        default=50,
        help=(
            "live mode: suppress the first N results from the display. The filter starts "
            "empty, so early responses score high because nothing is familiar yet rather "
            "than because they are unusual. They are still learned from."
        ),
    )
    ranker.add_argument("--top", type=int, default=None, help="print at most this many results")
    ranker.add_argument("--jsonl", action="store_true", help="emit JSONL for piping onward")
    ranker.set_defaults(func=cmd_rank)

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
