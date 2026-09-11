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
from flypaper.store.db import default_path as default_db

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

    if args.baseline:
        return _rank_with_baseline(args, source, kwargs)

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


def cmd_taste(args: argparse.Namespace) -> int:
    """Stage two: re-fetch the top-N most novel candidates. Scope-gated, rate-limited."""
    from flypaper.rank.score import rank
    from flypaper.stage2.body_features import body_features, shared_boilerplate
    from flypaper.stage2.refetch import MAX_RATE, RateLimiter, refetch
    from flypaper.stage2.scope import Scope

    scope = Scope.from_file(args.scope)
    source = iter_batch(args.file) if args.file else iter_stdin()
    ranked = rank(source, passes=2, channel_set=args.channel_set)

    limiter = RateLimiter(args.rate)
    if limiter.capped:
        print(
            f"flypaper: requested rate {args.rate}/s exceeds the stage-two ceiling; "
            f"using {MAX_RATE}/s.",
            file=sys.stderr,
        )
    print(
        f"flypaper: stage two on the top {args.top} of {len(ranked)} by novelty, "
        f"{limiter.rate}/s, scope {args.scope}. Redirects are recorded, never followed.",
        file=sys.stderr,
    )

    headers = {}
    for item in args.header or []:
        name, _, value = item.partition(":")
        if not value:
            raise SystemExit(f"--header must be 'Name: value', got {item!r}")
        headers[name.strip()] = value.strip()

    by_url = {s.url: s for s in ranked}
    fetched = list(
        refetch(
            (s.result for s in ranked),
            scope,
            top=args.top,
            rate=args.rate,
            headers=headers or None,
            retain_bodies=True,
            limiter=limiter,
        )
    )
    features = {
        f.url: body_features(f.body, scraper=by_url[f.url].result.scraper)
        for f in fetched
        if f.url in by_url
    }
    boilerplate = shared_boilerplate(features.values())

    for f in fetched:
        scored = by_url.get(f.url)
        feat = features.get(f.url)
        record = {
            "url": f.url,
            "stage1_novelty": round(scored.novelty, 6) if scored else None,
            "status": f.status,
            "length": f.length,
            "content_type": f.content_type,
            "redirect_location": f.location,
            "elapsed_ms": round(f.elapsed_ms, 2),
            "error": f.error or None,
            "body": feat.as_dict() if feat else None,
            "shared_title": bool(feat and feat.title in boilerplate),
        }
        print(json.dumps(record, ensure_ascii=False))

    for decision in scope.skipped:
        print(f"flypaper: skipped {decision.url} - {decision.reason}", file=sys.stderr)
    print(
        f"flypaper: requested {len(fetched)}, skipped {len(scope.skipped)} out of scope.",
        file=sys.stderr,
    )
    return 0


def _rank_with_baseline(args, source, kwargs) -> int:
    """Rank against a saved baseline, then optionally fold this run into it.

    This is the mode that makes temporal decay mean anything. Within a single scan, "time"
    is how many responses have gone past; across scans it is wall-clock, so a baseline built
    six months ago is six months stale rather than 1,998 records stale.
    """
    from flypaper.rank.report import Terminal
    from flypaper.rank.score import Ranker
    from flypaper.store.db import Store

    # Across scans, elapsed time is wall-clock. Within one it is how much else went past.
    ranker = Ranker(time_base="wallclock", **kwargs)
    with Store(args.db) as store:
        existing = None
        if args.baseline in store.names():
            existing = store.restore_into(args.baseline, ranker)
            age_days = existing.age_seconds / 86400.0
            print(
                f"flypaper: baseline {args.baseline!r} restored - {existing.n_observed} "
                f"responses, {existing.saturation:.0%} saturated, {age_days:.1f} days old.",
                file=sys.stderr,
            )
        else:
            print(
                f"flypaper: baseline {args.baseline!r} is new; everything will look novel "
                f"on this first run.",
                file=sys.stderr,
            )

        term = None
        if not args.jsonl:
            term = Terminal(
                threshold=args.threshold, percentile=args.percentile, warmup=args.warmup
            )
            term.header(args.channel_set, args.projection)

        for item in ranker.stream(source):
            if term is not None:
                term.result(item)
            else:
                print(json.dumps(item.as_dict(), ensure_ascii=False))
        if term is not None:
            term.footer(ranker.saturation)

        if not args.no_update:
            store.save(args.baseline, ranker)
            print(
                f"flypaper: baseline {args.baseline!r} updated "
                f"({ranker.filter.n_observed} responses, {ranker.saturation:.0%} saturated).",
                file=sys.stderr,
            )
    return 0


def cmd_baselines(args: argparse.Namespace) -> int:
    """List or inspect saved baselines."""
    from flypaper.store.db import Store

    with Store(args.db) as store:
        names = store.names()
        if not names:
            print(f"no baselines in {args.db}", file=sys.stderr)
            return 0
        if args.name:
            print(json.dumps(store.describe(args.name), indent=2))
            return 0
        for name in names:
            d = store.describe(name)
            print(
                f"{d['name']:24} {d['channel_set']:12} {d['projection']:11} "
                f"{d['n_observed']:>8} responses  {d['saturation']:>6.1%} saturated  "
                f"{d['age_seconds'] / 86400:.1f}d old"
            )
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
        help=(
            "how long until a depressed baseline has recovered half way to novel again. "
            "In responses for a single run; in SECONDS when --baseline is used, because "
            "across scans elapsed time is wall-clock."
        ),
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
    ranker.add_argument(
        "--baseline",
        help=(
            "score against a named saved baseline and fold this run into it. This is what "
            "makes temporal decay meaningful: across scans, time is wall-clock."
        ),
    )
    ranker.add_argument(
        "--db",
        default=str(default_db()),
        help="where baselines live. Feature vectors and hashes only; never response bodies.",
    )
    ranker.add_argument(
        "--no-update",
        action="store_true",
        help="score against the baseline without writing this run into it",
    )
    ranker.set_defaults(func=cmd_rank)

    baselines = sub.add_parser("baselines", help="list or inspect saved baselines")
    baselines.add_argument("name", nargs="?", help="describe just this one")
    baselines.add_argument("--db", default=str(default_db()))
    baselines.set_defaults(func=cmd_baselines)

    taste = sub.add_parser(
        "taste",
        help="stage two: re-fetch the top-N novel candidates, scope-gated and rate-limited",
        description=(
            "Re-requests only URLs already present in the input stream, only those matching "
            "an explicit --scope file, slowly, following no redirects. Never crawls, never "
            "guesses, never expands scope."
        ),
    )
    taste.add_argument("file", nargs="?", help="a completed ffuf results file; omit for stdin")
    taste.add_argument(
        "--scope",
        required=True,
        help=(
            "file of host patterns, one per line: an exact host, or '*.example.com' for "
            "subdomains. Mandatory - there is no default and no same-domain inference."
        ),
    )
    taste.add_argument("--top", type=int, default=10, help="how many candidates to re-fetch")
    taste.add_argument(
        "--rate",
        type=float,
        default=1.0,
        help="requests per second. Capped at a hard ceiling in the code, not by this flag.",
    )
    taste.add_argument(
        "--header",
        action="append",
        help="'Name: value', passed through verbatim. Repeatable.",
    )
    taste.add_argument("--channel-set", default=CHANNEL_SET_VERSION)
    taste.set_defaults(func=cmd_taste)

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
