"""Reading fuzzer output. Tolerant by design: schema drift warns, it never crashes."""

from flypaper.ingest.ffuf import FfufResult, ParseStats, iter_ndjson, load_batch, parse_record
from flypaper.ingest.stream import iter_batch, iter_stdin, iter_tail

__all__ = [
    "FfufResult",
    "ParseStats",
    "iter_batch",
    "iter_ndjson",
    "iter_stdin",
    "iter_tail",
    "load_batch",
    "parse_record",
]
