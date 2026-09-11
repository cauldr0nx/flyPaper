#!/usr/bin/env python3
"""Generate the deterministic wordlist used by the M1 ingest spike.

There is no SecLists checkout on this machine and the M1 gate needs 10,000 requests, so
the list is generated rather than downloaded: same seed, same 10,000 lines, on any box.

A slice of the list is deliberately hostile to a JSON parser - words containing quotes,
backslashes, control-adjacent punctuation and non-ASCII bytes - because those are what
land in ffuf's `input` field and are the cheapest way to find out whether the record
shape survives escaping.

    python bench/make_wordlist.py --out list.txt --count 10000 --seed 20260911
"""

from __future__ import annotations

import argparse
import random

# Names a real content-discovery list would carry. Kept short; the bulk is generated.
COMMON = """
admin administrator login logout signin signup register dashboard panel cpanel
api api/v1 api/v2 graphql rest soap rpc jsonrpc
backup backups bak old new tmp temp cache logs log
config configuration settings setup install installer
db database sql dump dumps mysql postgres redis
upload uploads files file download downloads media static assets
images img css js scripts styles fonts vendor node_modules
test tests testing dev develop development stage staging prod production
user users account accounts profile profiles member members
.git .git/config .git/HEAD .svn .env .htaccess .htpasswd .DS_Store
robots.txt sitemap.xml crossdomain.xml security.txt humans.txt
wp-admin wp-content wp-includes wp-login.php xmlrpc.php
phpinfo.php info.php server-status server-info
console shell cmd exec eval debug trace status health healthz metrics ping
search query filter sort page index home main default
cgi-bin bin lib src app apps public private internal external
docs doc documentation swagger openapi redoc
mail email webmail smtp imap
portal gateway proxy router
""".split()

EXTENSIONS = ["", "", "", ".php", ".html", ".txt", ".json", ".xml", ".bak", ".old", ".zip", ".log"]

# Words built to be awkward for anything that treats a JSON record as plain text.
AWKWARD = [
    'quote"inside',
    "back\\slash",
    'both"and\\slash',
    "tab\tinside",
    "brace{}",
    "bracket[]",
    "percent%20encoded",
    "plus+sign",
    "amp&ersand",
    "hash#fragment",
    "question?mark",
    "semi;colon",
    "café",
    "ümlaut",
    "日本語",
    "emoji\U0001f41b",
    "null␀byte",
    "dot.dot.dot",
    "-leading-dash",
    "trailing-dash-",
]

ALPHABETS = {
    "hex": "0123456789abcdef",
    "lower": "abcdefghijklmnopqrstuvwxyz",
    "mixed": "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789",
    "punct": "abcdefghijklmnopqrstuvwxyz0123456789-_.",
}


def generate(count: int, seed: int) -> list[str]:
    rng = random.Random(seed)
    words: list[str] = []

    # 1. Real names, plain and with extensions.
    for name in COMMON:
        words.append(name)
        if "." not in name:
            words.append(name + rng.choice(EXTENSIONS))

    # 2. The awkward slice, plain and nested, so escaping is exercised at two depths.
    words.extend(AWKWARD)
    words.extend(f"dir/{w}" for w in AWKWARD)

    # 3. Bulk junk across several character classes and depths, to fill out the count.
    while len(words) < count:
        alphabet = ALPHABETS[rng.choice(list(ALPHABETS))]
        length = rng.randint(3, 18)
        token = "".join(rng.choice(alphabet) for _ in range(length))
        depth = rng.choices([0, 1, 2], weights=[75, 20, 5])[0]
        if depth:
            parts = ["".join(rng.choice(ALPHABETS["lower"]) for _ in range(rng.randint(3, 8)))]
            parts *= depth
            token = "/".join([*parts, token])
        words.append(token + rng.choice(EXTENSIONS))

    # Deduplicate while keeping order, then trim or top up to exactly `count`.
    seen: set[str] = set()
    unique = [w for w in words if not (w in seen or seen.add(w))]
    while len(unique) < count:
        filler = "pad" + "".join(rng.choice(ALPHABETS["hex"]) for _ in range(12))
        if filler not in seen:
            seen.add(filler)
            unique.append(filler)
    return unique[:count]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", required=True)
    ap.add_argument("--count", type=int, default=10000)
    ap.add_argument("--seed", type=int, default=20260911)
    args = ap.parse_args()
    words = generate(args.count, args.seed)
    with open(args.out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(words) + "\n")
    print(f"{len(words)} words -> {args.out} (seed {args.seed})")


if __name__ == "__main__":
    main()
