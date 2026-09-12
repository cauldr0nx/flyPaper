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
    from flypaper.rank.health import StreamHealth
    from flypaper.rank.report import Terminal, write_jsonl
    from flypaper.rank.score import Ranker, rank

    if args.file and args.follow:
        # Followed forever, `fly rank --follow` never exits and the operator has no way to
        # know the scan finished. Stopping after a quiet period is the pragmatic answer:
        # ffuf writes continuously while it runs, so silence means it is done.
        source = iter_tail(args.file, stop_after_idle=args.follow_idle)
        args.live = True
    elif args.file:
        source = iter_batch(args.file)
    else:
        source = iter_stdin()
    health = StreamHealth()
    kwargs = {
        "channel_set": args.channel_set,
        "projection": args.projection,
        "circuit_path": args.circuit,
        "decay_halflife": args.decay_halflife,
    }
    if args.per != "none":
        if args.projection != "random":
            raise SystemExit("--per needs the random projection; drop --projection connectome")
        kwargs.pop("circuit_path")
        kwargs.pop("projection")

    if args.baseline:
        return _rank_with_baseline(args, source, kwargs)

    if args.file and not args.live:
        scored = rank(source, passes=2, partition=args.per, **kwargs)
        for item in scored:
            health.observe(item.result, partition=item.partition)
        if args.jsonl:
            write_jsonl(scored[: args.top] if args.top else scored)
            _report_health(health)
            return 0
        term = Terminal(
            threshold=args.threshold,
            percentile=args.percentile,
            partitioned=args.per != "none",
        )
        # A completed file is already sorted, so a review budget is the useful default: a
        # percentile gate can legitimately pass nothing, and "0 of 1592" is a bad answer to
        # "show me this scan". It happens for real with --per, where every partition has to
        # calibrate its own gate and an early hit in a partition arrives before it has.
        budget = args.top if args.top is not None else DEFAULT_TOP
        term.header(args.channel_set, args.projection, budget=budget)
        if budget:
            for item in scored[:budget]:
                term.result(item, force=True)
        else:
            for item in scored:
                term.result(item)
        term.footer(0.0)
        _report_health(health)
        return 0

    # Live: one pass, scoring each result against only what came before it.
    ranker = Ranker(**kwargs)
    term = None
    if not args.jsonl:
        term = Terminal(threshold=args.threshold, percentile=args.percentile, warmup=args.warmup)
        term.header(args.channel_set, args.projection)
    shown_before = 0
    for item in ranker.stream(source):
        if term is not None:
            term.result(item)
            health.observe(
                item.result, surfaced=term.shown > shown_before, partition=item.partition
            )
            shown_before = term.shown
        else:
            print(json.dumps(item.as_dict(), ensure_ascii=False))
            health.observe(item.result, partition=item.partition)
    if term is not None:
        term.footer(getattr(ranker, "saturation", 0.0))
    _report_health(health)
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
    from flypaper.rank.health import StreamHealth
    from flypaper.rank.report import Terminal
    from flypaper.rank.score import PartitionedRanker, Ranker
    from flypaper.store.db import Store

    # Across scans, elapsed time is wall-clock. Within one it is how much else went past.
    partitioned = args.per != "none"
    if partitioned:
        ranker = PartitionedRanker(how=args.per, time_base="wallclock", **kwargs)
    else:
        ranker = Ranker(time_base="wallclock", **kwargs)
    health = StreamHealth()

    with Store(args.db) as store:
        known = args.baseline in store.names()
        if known and partitioned:
            restored = store.restore_all_into(args.baseline, ranker)
            if restored:
                oldest = max(b.age_seconds for b in restored) / 86400.0
                print(
                    f"flypaper: baseline {args.baseline!r} restored - {len(restored)} "
                    f"{args.per} partitions, oldest {oldest:.1f} days.",
                    file=sys.stderr,
                )
        elif known:
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
                threshold=args.threshold,
                percentile=args.percentile,
                warmup=args.warmup,
                partitioned=partitioned,
            )
            term.header(args.channel_set, args.projection)

        shown_before = 0
        for item in ranker.stream(source):
            if term is not None:
                term.result(item)
                health.observe(
                    item.result, surfaced=term.shown > shown_before, partition=item.partition
                )
                shown_before = term.shown
            else:
                print(json.dumps(item.as_dict(), ensure_ascii=False))
                health.observe(item.result, partition=item.partition)
        if term is not None:
            term.footer(ranker.saturation)

        if not args.no_update:
            store.save(args.baseline, ranker)
            where = (
                f"{ranker.partitions} {args.per} partitions"
                if partitioned
                else f"{ranker.filter.n_observed} responses"
            )
            print(
                f"flypaper: baseline {args.baseline!r} updated ({where}, "
                f"{ranker.saturation:.0%} saturated).",
                file=sys.stderr,
            )
    _report_health(health)
    return 0


def cmd_watch(args: argparse.Namespace) -> int:
    """Score a scan against a saved baseline without folding it in: what is new since then.

    This is the workflow the persistence and the decay exist for, and the one `fly rank
    --baseline` does not express. `rank` learns as it goes, so by the time it prints a
    result it has already made it familiar; asked to rank the same target twice it will
    dutifully report that nothing is surprising, which is true and useless.

    `watch` holds the baseline still. Everything is scored against what the target looked
    like last time and nothing is written back unless asked, so the question it answers is
    "what is here now that was not here then" rather than "what stands out today".

    **It reports structurally new, not newly-seen.** A new URL that looks like a page the
    target already served will not be flagged - `/admin-v2` at 7,321 bytes against a
    baseline already holding `/admin` at 6,914 scores 0.296, because structurally it is the
    same kind of page. That is the right behaviour for monitoring at scale, where a new
    marketing page should not wake anyone, and it is the wrong tool if what you want is a
    URL diff. The filter stores no identities, only shapes.
    """
    from flypaper.rank.health import StreamHealth
    from flypaper.rank.report import Terminal, write_jsonl
    from flypaper.rank.score import PartitionedRanker, Ranker
    from flypaper.store.db import Store

    kwargs = {"channel_set": args.channel_set, "decay_halflife": args.decay_halflife}
    partitioned = args.per != "none"
    health = StreamHealth()

    with Store(args.db) as store:
        # Checked before the input is read, so a typo in the name does not first wait for a
        # scan to be parsed.
        if args.baseline not in store.names():
            print(
                f"flypaper: no baseline named {args.baseline!r}. Establish one first:\n"
                f"    fly rank <results> --baseline {args.baseline}"
                + (f" --per {args.per}" if partitioned else ""),
                file=sys.stderr,
            )
            return 1

        if partitioned:
            ranker = PartitionedRanker(how=args.per, time_base="wallclock", **kwargs)
            restored = store.restore_all_into(args.baseline, ranker)
            age = max((b.age_seconds for b in restored), default=0.0) / 86400.0
            print(
                f"flypaper: {args.baseline!r} - {len(restored)} {args.per} partitions, "
                f"oldest {age:.1f} days old.",
                file=sys.stderr,
            )
        else:
            ranker = Ranker(time_base="wallclock", **kwargs)
            baseline = store.restore_into(args.baseline, ranker)
            age = baseline.age_seconds / 86400.0
            print(
                f"flypaper: {args.baseline!r} - {baseline.n_observed} responses, "
                f"{baseline.saturation:.0%} saturated, {age:.1f} days old.",
                file=sys.stderr,
            )

        source = list(iter_batch(args.file) if args.file else iter_stdin())
        # Score against the stored baseline, holding it still. `score_only`, not
        # `score_against_baseline`: these records are not in the baseline, so discounting a
        # contribution they never made would inflate every one of them.
        scored = [ranker.score_only(r) for r in source]
        for item in scored:
            health.observe(item.result, partition=item.partition)
        scored.sort(key=lambda s: (not s.settled, -s.novelty, s.position))

        new = [s for s in scored if s.novelty >= args.threshold]
        if args.jsonl:
            write_jsonl(new[: args.top] if args.top else new)
        else:
            term = Terminal(threshold=args.threshold, partitioned=partitioned)
            term.header(args.channel_set, "random", budget=None)
            for item in new[: args.top] if args.top else new:
                term.result(item, force=True)
            print(
                f"flypaper: {len(new)} of {len(scored)} responses are new against a baseline "
                f"{age:.1f} days old (novelty >= {args.threshold:.2f}).",
                file=sys.stderr,
            )
            if not new:
                print(
                    "flypaper: nothing new. That is the useful answer most weeks - it means "
                    "the target looks like it did last time.",
                    file=sys.stderr,
                )

        if args.update:
            store.save(args.baseline, ranker)
            print(f"flypaper: baseline {args.baseline!r} updated.", file=sys.stderr)
        else:
            print(
                "flypaper: baseline left untouched. Pass --update to fold this scan in once "
                "you have looked at what it found.",
                file=sys.stderr,
            )

    _report_health(health)
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
            parts = store.partitions(name)
            d = store.describe(name, parts[0] if parts else "")
            scope = f"{len(parts)} partitions" if len(parts) > 1 or parts[0] else "unpartitioned"
            print(
                f"{d['name']:24} {d['channel_set']:12} {d['projection']:11} "
                f"{scope:>16}  {d['saturation']:>6.1%} saturated  "
                f"{d['age_seconds'] / 86400:.1f}d old"
            )
    return 0


def cmd_scope(args: argparse.Namespace) -> int:
    """Convert a program's published scope table into a scope file, never widening it."""
    from flypaper.stage2.program_scope import convert_program_scope

    report = convert_program_scope(args.source)
    print(f"flypaper: {report.summary()}", file=sys.stderr)

    if args.explain or not args.out:
        for identifier, asset_type, why in report.skipped:
            print(f"  skipped  {identifier}  [{asset_type or '-'}]  {why}", file=sys.stderr)

    if not report.patterns:
        print(
            "flypaper: nothing eligible and addressable in that table; refusing to write a "
            "scope file that authorises nothing.",
            file=sys.stderr,
        )
        return 1

    if args.out:
        report.write(args.out, source=str(args.source))
        print(f"flypaper: wrote {args.out}", file=sys.stderr)
        print(
            "flypaper: check it against the program's own page before pointing anything at "
            "it. This is a convenience, not an authorisation.",
            file=sys.stderr,
        )
    else:
        for pattern in report.patterns:
            print(pattern)
    return 0


#: How many results `fly rank <file>` shows when not told otherwise. A review budget,
#: chosen to fit on a screen.
DEFAULT_TOP = 25


def _report_health(health) -> None:
    """Say whether the stream could have taught a baseline at all."""
    warnings = health.warnings()
    if not warnings:
        return
    print(f"flypaper: {health.summary()}", file=sys.stderr)
    for warning in warnings:
        print(f"flypaper: warning: {warning}", file=sys.stderr)


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
    ranker.add_argument(
        "--follow",
        action="store_true",
        help=(
            "with a file, follow it as ffuf writes it (`ffuf -of json -o out.json`), rather "
            "than reading it once. Implies --live."
        ),
    )
    ranker.add_argument(
        "--follow-idle",
        type=float,
        default=10.0,
        help="with --follow, stop after this many seconds without a new result",
    )
    ranker.add_argument(
        "--top",
        type=int,
        default=None,
        help=(
            f"how many results to print. Defaults to {DEFAULT_TOP} for a completed file, "
            f"which is already sorted; 0 prints everything above the cutoff. Live input is "
            f"gated by --percentile instead, since there is no 'top' of a stream."
        ),
    )
    ranker.add_argument(
        "--per",
        choices=("none", "host", "dir"),
        default="none",
        help=(
            "keep a separate baseline per host, or per directory. This is the case ffuf "
            "cannot handle - one -fs or -ac filter cannot describe fifty hosts, or every "
            "directory a recursive scan walks into. Costs 16 kB per partition."
        ),
    )
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

    watch = sub.add_parser(
        "watch",
        help="what is new on a target since the last scan",
        description=(
            "Scores a scan against a saved baseline without folding it in, so the question "
            "is 'what is here now that was not here then'. `fly rank --baseline` learns as "
            "it goes and will tell you a second scan of the same target is unsurprising; "
            "this holds the baseline still instead."
        ),
    )
    watch.add_argument("file", nargs="?", help="a results file; omit to read stdin")
    watch.add_argument("--baseline", required=True, help="the saved baseline to compare against")
    watch.add_argument("--db", default=str(default_db()))
    watch.add_argument(
        "--per",
        choices=("none", "host", "dir"),
        default="none",
        help="must match how the baseline was established",
    )
    watch.add_argument(
        "--threshold",
        type=float,
        default=0.5,
        help="how novel against the stored baseline a response must be to count as new",
    )
    watch.add_argument("--decay-halflife", type=float, default=None, help="in seconds")
    watch.add_argument("--channel-set", default=CHANNEL_SET_VERSION)
    watch.add_argument("--top", type=int, default=None)
    watch.add_argument("--jsonl", action="store_true")
    watch.add_argument(
        "--update",
        action="store_true",
        help="fold this scan into the baseline after reporting, so next time compares to now",
    )
    watch.set_defaults(func=cmd_watch)

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

    scope = sub.add_parser(
        "scope",
        help="convert a program scope export into a scope file for `fly taste`",
        description=(
            "Reads a bug bounty program's structured scope export and writes the host "
            "patterns it authorises. Drops assets the program marks ineligible, drops asset "
            "types that are not web hosts, and SKIPS path-scoped assets rather than "
            "widening them to their host. Everything dropped is reported."
        ),
    )
    scope.add_argument("source", help="the program's scope export (CSV)")
    scope.add_argument("--out", help="write a scope file here instead of printing patterns")
    scope.add_argument("--explain", action="store_true", help="list every dropped asset and why")
    scope.set_defaults(func=cmd_scope)

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
