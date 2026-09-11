#!/usr/bin/env python3
"""The corpus wordlist: generated noise plus every labelled hit.

Separate from `make_wordlist.py`, which exists to stress the parser. This one exists to
produce a realistic haystack around a known set of needles, so the ranking metrics at M4
have something to be measured against.
"""

from __future__ import annotations

import argparse
import random

from capture import SURFACES  # noqa: E402  - sibling script, run from bench/
from make_wordlist import generate  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="bench/corpus/corpus-words.txt")
    ap.add_argument("--count", type=int, default=2000)
    ap.add_argument("--seed", type=int, default=20260911)
    args = ap.parse_args()

    hits = [h for s in SURFACES.values() for h in s["hits"]]
    words = [w for w in generate(args.count, args.seed) if w not in hits]
    words = words[: args.count - len(hits)]

    # Scatter the needles through the list rather than stacking them at the front. Putting
    # them first looked neutral and is not: the encoder is single-pass, so a hit at
    # position 3 is scored before any baseline exists and its z-scored channels are all
    # still returning "no opinion". Position must not be what makes a hit findable.
    rng = random.Random(args.seed)
    for hit in hits:
        words.insert(rng.randrange(len(words) // 4, len(words)), hit)

    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(words) + "\n")
    print(f"{len(words)} words ({len(hits)} labelled hits) -> {args.out}")


if __name__ == "__main__":
    main()
