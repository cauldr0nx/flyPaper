#!/usr/bin/env python3
"""Download and verify the MaleCNS flat-connectome tables.

Bulk data is never committed. This script fetches it from the public Google Cloud Storage
bucket over plain HTTPS (no `gsutil`, no credentials) and verifies every byte against the
checksums pinned in `manifest.json`. It hard-fails on any mismatch and never leaves an
unverified file in place.

`--verify-only` is also how a symlinked `data/raw` is checked: pointing at another
checkout's copy of the tables is fine, silently using different bytes is not.

  python data/fetch.py --tier core        # annotations + traced connectome (~522 MB)
  python data/fetch.py --tier full        # every flat-connectome table (~31.3 GB)
  python data/fetch.py --verify-only      # re-hash what is on disk, exit non-zero on drift
  python data/fetch.py --file body-annotations-male-cns-v1.0-minconf-0.5.feather

The MaleCNS dataset is licensed CC-BY 4.0 and is NOT covered by this repository's MIT
license. See `manifest.json` for the verbatim terms and the required attribution.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import requests

DATA_DIR = Path(__file__).resolve().parent
MANIFEST_PATH = DATA_DIR / "manifest.json"
# `FLYPAPER_RAW_DIR` points at an existing copy of the tables - another checkout, another
# disk - instead of forcing a second 31 GB download. `--verify-only` against that copy is
# what turns "probably the same files" into "the same bytes".
RAW_DIR = Path(os.environ.get("FLYPAPER_RAW_DIR") or DATA_DIR / "raw")
LOG_PATH = DATA_DIR.parent / "reports" / "fetch-log.txt"

# Refuse to start a download that would leave the filesystem with less headroom than this.
MIN_FREE_BYTES_AFTER = 10 * 1000**3
CHUNK = 8 * 1024 * 1024


class VerificationError(RuntimeError):
    """A file on disk does not match its pinned checksum. Always fatal."""


def load_manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text())


def save_manifest(manifest: dict) -> None:
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2) + "\n")


def select(manifest: dict, tier: str, only: list[str] | None) -> list[dict]:
    files = manifest["files"]
    if only:
        names = set(only)
        chosen = [f for f in files if f["name"] in names]
        missing = names - {f["name"] for f in chosen}
        if missing:
            raise SystemExit(f"not in manifest: {', '.join(sorted(missing))}")
        return chosen
    if tier == "core":
        return [f for f in files if f["tier"] == "core"]
    return files


def human(n: float) -> str:
    return f"{n / 1e9:.2f} GB" if n >= 1e9 else f"{n / 1e6:.1f} MB"


def hash_file(path: Path) -> tuple[hashlib._Hash, hashlib._Hash, int]:
    """Hash `path` from the start, returning (md5, sha256, bytes_read)."""
    md5, sha = hashlib.md5(), hashlib.sha256()
    read = 0
    with path.open("rb") as fh:
        while chunk := fh.read(CHUNK):
            md5.update(chunk)
            sha.update(chunk)
            read += len(chunk)
    return md5, sha, read


def check_disk(entries: list[dict]) -> None:
    """Refuse to fill the disk. The full tier is 31 GB; headroom matters."""
    needed = 0
    for entry in entries:
        dest = RAW_DIR / entry["name"]
        if dest.exists() and dest.stat().st_size == entry["size"]:
            continue
        part = RAW_DIR / (entry["name"] + ".part")
        have = part.stat().st_size if part.exists() else 0
        needed += max(0, entry["size"] - have)
    free = shutil.disk_usage(RAW_DIR if RAW_DIR.exists() else DATA_DIR).free
    print(f"need {human(needed)}, free {human(free)}")
    if free - needed < MIN_FREE_BYTES_AFTER:
        raise SystemExit(
            f"refusing to start: downloading {human(needed)} would leave "
            f"{human(free - needed)} free, below the {human(MIN_FREE_BYTES_AFTER)} floor.\n"
            f"Free some space, or use --tier core ({human(needed)} -> smaller)."
        )


def verify_entry(entry: dict, dest: Path, *, strict: bool) -> dict[str, str]:
    """Hash a completed file and compare against every pinned checksum.

    Raises VerificationError on any mismatch. Returns the computed digests.
    """
    size = dest.stat().st_size
    if size != entry["size"]:
        raise VerificationError(f"{entry['name']}: size {size} != manifest {entry['size']}")
    md5, sha, _ = hash_file(dest)
    digests = {"md5": md5.hexdigest(), "sha256": sha.hexdigest()}

    if entry.get("gcs_md5") and digests["md5"] != entry["gcs_md5"]:
        raise VerificationError(
            f"{entry['name']}: MD5 {digests['md5']} != upstream {entry['gcs_md5']}"
        )
    pinned = entry.get("sha256")
    if pinned and digests["sha256"] != pinned:
        raise VerificationError(f"{entry['name']}: SHA256 {digests['sha256']} != pinned {pinned}")
    if not pinned and strict:
        raise VerificationError(
            f"{entry['name']}: no SHA256 pinned in the manifest. Run a download first so "
            f"it is recorded, then commit the manifest."
        )
    return digests


def download(entry: dict, dest: Path) -> None:
    """Stream the object to `dest.part`, resuming if a partial file exists."""
    part = dest.with_suffix(dest.suffix + ".part")
    md5, sha = hashlib.md5(), hashlib.sha256()
    have = 0

    if part.exists():
        have = part.stat().st_size
        if have > entry["size"]:
            print(f"  partial file is larger than expected; restarting {entry['name']}")
            part.unlink()
            have = 0
        elif have:
            print(f"  resuming at {human(have)}")
            md5, sha, have = hash_file(part)

    headers = {"Range": f"bytes={have}-"} if have else {}
    started, last = time.monotonic(), time.monotonic()
    with requests.get(entry["url"], headers=headers, stream=True, timeout=120) as resp:
        if have and resp.status_code != 206:
            # Server ignored the range request; start over rather than concatenate garbage.
            resp.close()
            part.unlink(missing_ok=True)
            return download(entry, dest)
        resp.raise_for_status()
        with part.open("ab" if have else "wb") as fh:
            for chunk in resp.iter_content(CHUNK):
                fh.write(chunk)
                md5.update(chunk)
                sha.update(chunk)
                have += len(chunk)
                now = time.monotonic()
                if now - last > 5:
                    rate = have / max(now - started, 1e-9) / 1e6
                    pct = 100 * have / entry["size"]
                    print(f"  {pct:5.1f}%  {human(have)}  {rate:.0f} MB/s", flush=True)
                    last = now

    if have != entry["size"]:
        part.unlink(missing_ok=True)
        raise VerificationError(f"{entry['name']}: received {have} bytes, expected {entry['size']}")
    if entry.get("gcs_md5") and md5.hexdigest() != entry["gcs_md5"]:
        part.unlink(missing_ok=True)
        raise VerificationError(
            f"{entry['name']}: MD5 {md5.hexdigest()} != upstream {entry['gcs_md5']}. "
            f"Partial file deleted."
        )
    pinned = entry.get("sha256")
    if pinned and sha.hexdigest() != pinned:
        part.unlink(missing_ok=True)
        raise VerificationError(
            f"{entry['name']}: SHA256 {sha.hexdigest()} != pinned {pinned}. Partial file deleted."
        )
    # Only now is it safe to put the file in place.
    part.replace(dest)
    entry["_computed"] = {"md5": md5.hexdigest(), "sha256": sha.hexdigest()}


def log(line: str) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a") as fh:
        fh.write(line + "\n")


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--tier", choices=["core", "full"], default="core")
    ap.add_argument("--file", action="append", dest="files", help="fetch one named file")
    ap.add_argument("--verify-only", action="store_true", help="re-hash on-disk files, no download")
    ap.add_argument(
        "--strict",
        action="store_true",
        help="with --verify-only, also fail when a file has no pinned SHA256",
    )
    args = ap.parse_args()

    manifest = load_manifest()
    entries = select(manifest, args.tier, args.files)
    RAW_DIR.mkdir(parents=True, exist_ok=True)

    print(f"MaleCNS {manifest['dataset']} - {manifest['license']}")
    print(f"  {manifest['license_text_verbatim']}")
    print(f"  Attribution: {manifest['attribution']}")
    print(f"  Cite: {manifest['citation']}\n")

    if args.verify_only:
        failures, checked = [], 0
        for entry in entries:
            dest = RAW_DIR / entry["name"]
            if not dest.exists():
                print(f"MISSING  {entry['name']}")
                failures.append(entry["name"])
                continue
            try:
                digests = verify_entry(entry, dest, strict=args.strict)
            except VerificationError as exc:
                print(f"FAIL     {exc}")
                failures.append(entry["name"])
                continue
            checked += 1
            print(f"ok       {entry['name']}  sha256={digests['sha256'][:16]}...")
        print(f"\n{checked} verified, {len(failures)} failed")
        return 1 if failures else 0

    check_disk(entries)
    pinned_changed = False
    for i, entry in enumerate(entries, 1):
        dest = RAW_DIR / entry["name"]
        print(f"[{i}/{len(entries)}] {entry['name']} ({human(entry['size'])})")
        if dest.exists() and dest.stat().st_size == entry["size"]:
            try:
                digests = verify_entry(entry, dest, strict=False)
            except VerificationError as exc:
                raise SystemExit(f"FATAL: {exc}") from exc
            print("  already present and verified")
        else:
            try:
                download(entry, dest)
            except VerificationError as exc:
                raise SystemExit(f"FATAL: {exc}") from exc
            digests = entry.pop("_computed")
            print(f"  done, sha256={digests['sha256']}")

        if entry.get("sha256") is None:
            entry["sha256"] = digests["sha256"]
            pinned_changed = True
            print("  SHA256 pinned into manifest.json (commit it)")
        log(
            f"{datetime.now(UTC).isoformat(timespec='seconds')}\t{entry['name']}\t"
            f"{entry['size']}\tmd5={digests['md5']}\tsha256={digests['sha256']}\t{entry['url']}"
        )

    if pinned_changed:
        save_manifest(manifest)
        print("\nmanifest.json updated with newly pinned SHA256 digests - commit it.")
    print("all files verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
