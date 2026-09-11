"""M2 gate: rotating-token invariance and soft-404 collapse.

These are not smoke tests. They are the claim the tool rests on, measured on a corpus that
contains a wildcard-responding host and a token-rotating endpoint, and they are what
decides whether the encoder is worth building a neural code on top of.

The corpus is captured locally and is not committed; these tests skip when it is absent.

    python bench/target/server.py --port 8110 &
    docker run -d --name ffufme -p 127.0.0.1:8099:80 ffufme:8814611
    python bench/make_corpus_words.py --out bench/corpus/corpus-words.txt
    python bench/capture.py --all-local
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

from flypaper.encode import CHANNEL_SET_VERSION, Encoder
from flypaper.ingest.stream import iter_batch

CORPUS = Path(__file__).resolve().parent / "corpus"
SETS = ("v1-raw", "v2-log", "v3-response")

# The gate is asserted on the default set. The others are measured and reported, which
# is the point of building more than one: two of the three do not clear it.
GATE_RATIO = 4.0

pytestmark = pytest.mark.skipif(
    not (CORPUS / "manifest.json").exists(),
    reason="replay corpus not captured; see this module's docstring",
)


def load(surface: str):
    path = CORPUS / f"{surface}.jsonl"
    if not path.exists():
        pytest.skip(f"{surface} not captured")
    return list(iter_batch(path))


def encode(surface: str, channel_set: str):
    results = load(surface)
    return results, Encoder(channel_set).encode_all(results)


def pairwise_spread(X: np.ndarray, sample: int = 400, seed: int = 0) -> float:
    """Mean pairwise Euclidean distance over a random sample. The cluster's diameter."""
    rng = np.random.default_rng(seed)
    if len(X) > sample:
        X = X[rng.choice(len(X), sample, replace=False)]
    diff = X[:, None, :] - X[None, :, :]
    d = np.sqrt((diff**2).sum(-1))
    iu = np.triu_indices(len(X), k=1)
    return float(d[iu].mean())


def distance_to_centroid(X: np.ndarray, point: np.ndarray) -> float:
    return float(np.linalg.norm(point - X.mean(axis=0)))


def separation(surface: str, hitword: str, channel_set: str) -> tuple[float, float, float]:
    """(ratio, hit distance, cluster spread) for one surface under one channel set.

    The ratio is how far the labelled hit sits from the noise cluster's centroid, measured
    in units of the cluster's own diameter. It is scale-free, so it compares across channel
    sets of different dimensionality - which matters here, since the three sets have 11, 52
    and 40 channels.
    """
    results = load(surface)
    X = Encoder(channel_set).encode_all(results)
    hit = next(i for i, r in enumerate(results) if r.word == hitword)
    if surface == "ffufme-no404":
        # The soft-404 population is the 200s; this surface also returns some real 404s.
        noise = np.array(
            [X[i] for i, r in enumerate(results) if r.status == 200 and r.word != hitword]
        )
    else:
        noise = np.delete(X, hit, axis=0)
    spread = pairwise_spread(noise)
    distance = distance_to_centroid(noise, X[hit])
    return distance / max(spread, 1e-9), distance, spread


# --- the gate ---------------------------------------------------------------------------------


def test_rotating_token_responses_land_in_the_same_neighbourhood():
    """Every /token/ miss is the same page with a fresh variable-length CSRF token, so
    Content-Length jitters and `-fs` is useless. The encoder must not be fooled by it."""
    ratio, distance, spread = separation("bench-token", "account", CHANNEL_SET_VERSION)
    assert ratio > GATE_RATIO, f"spread={spread:.4f}, hit at {distance:.4f} ({ratio:.1f}x)"


def test_wildcard_host_collapses_to_one_dense_cluster():
    """ffufme's /cd/no404/ answers 200 to everything. A thousand-plus identical soft-404s
    must be one familiar cluster, not a thousand novel items."""
    results = load("ffufme-no404")
    soft404 = [r for r in results if r.status == 200 and r.word != "secret"]
    assert len(soft404) > 1000, f"expected a large soft-404 population, got {len(soft404)}"

    ratio, distance, spread = separation("ffufme-no404", "secret", CHANNEL_SET_VERSION)
    assert ratio > GATE_RATIO, f"spread={spread:.4f}, hit at {distance:.4f} ({ratio:.1f}x)"


def test_issue387_hit_is_separable_although_its_word_count_is_not():
    """Every record on this surface has words=100, the hit included - so the word filter
    `-ac` derives cannot distinguish it. Joint similarity across all fields still must."""
    results = load("bench-calib")
    assert len({r.words for r in results}) == 1, "the collision is not actually total"
    ratio, _, _ = separation("bench-calib", "reports", CHANNEL_SET_VERSION)
    assert ratio > GATE_RATIO


def test_length_jitter_is_really_in_the_corpus():
    """If it were not, the rotating-token test would be passing for the wrong reason."""
    lengths = {r.length for r in load("bench-token") if r.word != "account"}
    assert len(lengths) > 1, "the corpus does not actually contain length jitter"


# --- the measured reason the default set is the default -------------------------------------------


def test_input_word_channels_destroy_clustering():
    """The finding M2 exists to produce, pinned so it cannot silently reverse.

    `v2-log` carries the input-word character profile the brief lists as a candidate
    encoding. The word is different on every request by construction, so those channels
    make every response unique and the familiar cluster stops being a cluster. Removing them
    is the whole difference between `v2-log` and `v3-response`.
    """
    for surface, hitword in (("bench-token", "account"), ("ffufme-no404", "secret")):
        with_word, _, _ = separation(surface, hitword, "v2-log")
        without_word, _, _ = separation(surface, hitword, "v3-response")
        assert with_word < GATE_RATIO < without_word, (
            f"{surface}: v2-log {with_word:.1f}x, v3-response {without_word:.1f}x"
        )
        assert without_word > 5 * with_word, f"{surface}: expected a large gap"


def test_word_channels_are_the_bulk_of_within_cluster_variance():
    """Quantifies the above: ~98% of the variance inside a wildcard cluster is the word."""
    results = load("ffufme-no404")
    encoder = Encoder("v2-log")
    X = encoder.encode_all(results)
    noise = np.array(
        [X[i] for i, r in enumerate(results) if r.status == 200 and r.word != "secret"]
    )
    variance = noise.var(axis=0)
    word = [i for i, n in enumerate(encoder.names) if n.startswith("word.")]
    share = variance[word].sum() / max(variance.sum(), 1e-12)
    assert share > 0.9, f"input-word channels account for only {share:.1%} of the variance"


def test_channel_sets_are_versioned_and_distinct():
    from flypaper.encode.channels import CHANNEL_SETS

    assert len(CHANNEL_SETS) >= 2
    seen = {name: cs.names for name, cs in CHANNEL_SETS.items()}
    assert len(set(map(tuple, seen.values()))) == len(seen), "two channel sets are identical"
    for name, cs in CHANNEL_SETS.items():
        assert cs.version == name


# --- the corpus is what it claims to be -------------------------------------------------


def test_corpus_manifest_records_provenance_for_every_surface():
    manifest = json.loads((CORPUS / "manifest.json").read_text())
    assert manifest
    for name, meta in manifest.items():
        for key in ("target", "authorization", "captured_utc", "rate_limit_per_second"):
            assert meta.get(key), f"{name} has no {key}"
        assert meta["bodies_retained"] is False, f"{name} claims to retain bodies"
        if meta["live"]:
            assert meta["rate_limit_per_second"] <= 10, f"{name} captured above the live ceiling"


def test_no_response_bodies_anywhere_in_the_corpus():
    """Stage one is metadata only. A body in the corpus would be a design breach."""
    for path in CORPUS.glob("*.jsonl"):
        with path.open(encoding="utf-8") as fh:
            for _, line in zip(range(50), fh, strict=False):
                assert set(json.loads(line)) <= {
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
                }, f"unexpected field in {path.name}"
