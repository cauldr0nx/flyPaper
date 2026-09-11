#!/usr/bin/env python3
"""Fetch the standard datasets the FlyHash / Fly Bloom Filter papers benchmark on.

MNIST and Fashion-MNIST, in the original IDX format, from their canonical mirrors. Every
file is verified against a pinned SHA256 and nothing unverified is left on disk - the same
discipline as `data/fetch.py`, for the same reason.

    python bench/datasets.py --list
    python bench/datasets.py --fetch

Downloads land in `data/benchmarks/`, which is gitignored. Nothing here is committed.

Licensing: MNIST is distributed by Yann LeCun and Corinna Cortes under CC BY-SA 3.0.
Fashion-MNIST is MIT, (c) Zalando SE. Neither is vendored; both are fetched on demand and
recorded in reports/licenses.md.
"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import struct
import sys
from pathlib import Path

import numpy as np
import requests

REPO_ROOT = Path(__file__).resolve().parent.parent
CACHE = REPO_ROOT / "data" / "benchmarks"

MNIST_MIRROR = "https://storage.googleapis.com/cvdf-datasets/mnist"
FASHION_MIRROR = (
    "https://raw.githubusercontent.com/zalandoresearch/fashion-mnist/master/data/fashion"
)

FILES: dict[str, dict] = {
    "mnist-images": {
        "url": f"{MNIST_MIRROR}/train-images-idx3-ubyte.gz",
        "sha256": "440fcabf73cc546fa21475e81ea370265605f56be210a4024d2ca8f203523609",
        "dataset": "mnist",
        "kind": "images",
    },
    "mnist-labels": {
        "url": f"{MNIST_MIRROR}/train-labels-idx1-ubyte.gz",
        "sha256": "3552534a0a558bbed6aed32b30c495cca23d567ec52cac8be1a0730e8010255c",
        "dataset": "mnist",
        "kind": "labels",
    },
    "fashion-images": {
        "url": f"{FASHION_MIRROR}/train-images-idx3-ubyte.gz",
        "sha256": "3aede38d61863908ad78613f6a32ed271626dd12800ba2636569512369268a84",
        "dataset": "fashion-mnist",
        "kind": "images",
    },
    "fashion-labels": {
        "url": f"{FASHION_MIRROR}/train-labels-idx1-ubyte.gz",
        "sha256": "a04f17134ac03560a47e3764e11b92fc97de4d1bfaf8ba1a3aa29af54cc90845",
        "dataset": "fashion-mnist",
        "kind": "labels",
    },
}


class VerificationError(RuntimeError):
    """A file does not match its pinned checksum. Always fatal."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while chunk := fh.read(1 << 22):
            digest.update(chunk)
    return digest.hexdigest()


def fetch_one(name: str) -> Path:
    entry = FILES[name]
    CACHE.mkdir(parents=True, exist_ok=True)
    dest = CACHE / f"{name}.gz"

    if dest.exists():
        actual = _sha256(dest)
        if actual == entry["sha256"]:
            return dest
        raise VerificationError(f"{dest}: sha256 {actual} != pinned {entry['sha256']}")

    part = dest.with_suffix(".gz.part")
    print(f"  fetching {name} ...", file=sys.stderr)
    with requests.get(entry["url"], stream=True, timeout=120) as resp:
        resp.raise_for_status()
        with part.open("wb") as fh:
            for chunk in resp.iter_content(1 << 20):
                fh.write(chunk)
    actual = _sha256(part)
    if entry["sha256"] and actual != entry["sha256"]:
        part.unlink(missing_ok=True)
        raise VerificationError(f"{name}: sha256 {actual} != pinned {entry['sha256']}")
    part.replace(dest)
    return dest


def _read_idx(path: Path) -> np.ndarray:
    with gzip.open(path, "rb") as fh:
        magic, count = struct.unpack(">II", fh.read(8))
        dims = magic & 0xFF
        shape = [count] + [struct.unpack(">I", fh.read(4))[0] for _ in range(dims - 1)]
        return np.frombuffer(fh.read(), dtype=np.uint8).reshape(shape)


def available() -> bool:
    return all((CACHE / f"{name}.gz").exists() for name in FILES)


def load(dataset: str, limit: int | None = None) -> tuple[np.ndarray, np.ndarray]:
    """(X, y) for `mnist` or `fashion-mnist`. X is float32 in [0, 1], flattened."""
    prefix = {"mnist": "mnist", "fashion-mnist": "fashion"}[dataset]
    images = _read_idx(fetch_one(f"{prefix}-images"))
    labels = _read_idx(fetch_one(f"{prefix}-labels"))
    if limit:
        images, labels = images[:limit], labels[:limit]
    X = images.reshape(len(images), -1).astype(np.float32) / 255.0
    return X, labels.astype(np.int64)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--fetch", action="store_true")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    if args.list:
        for name, entry in FILES.items():
            state = "cached" if (CACHE / f"{name}.gz").exists() else "absent"
            print(f"{name:16} {state:7} {entry['url']}")
        return 0

    if args.fetch:
        for name in FILES:
            path = fetch_one(name)
            size_mb = path.stat().st_size / 1e6
            print(f"ok {name:16} {size_mb:6.1f} MB  sha256={_sha256(path)[:16]}...")
        for dataset in ("mnist", "fashion-mnist"):
            X, y = load(dataset, limit=1000)
            print(f"   {dataset}: X{X.shape} y{y.shape} classes={sorted(set(y.tolist()))}")
        return 0

    ap.error("pass --fetch or --list")
    return 2


if __name__ == "__main__":
    sys.exit(main())
