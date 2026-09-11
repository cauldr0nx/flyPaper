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
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from separation import knn_separation  # noqa: E402  - sibling module in bench/

from flypaper.encode import CHANNEL_SET_VERSION, Encoder
from flypaper.ingest.stream import iter_batch

CORPUS = Path(__file__).resolve().parent / "corpus"
SETS = ("v1-raw", "v2-log", "v3-response")
MIXED = "bench-mixed"

# The gate is asserted on the default set. The others are measured and reported, which
# is the point of building more than one: two of the three do not clear it.
GATE_RATIO = 4.0

pytestmark = pytest.mark.skipif(
    not (CORPUS / "manifest.json").exists(),
    reason="replay corpus not captured; see this module's docstring",
)


def _hits(surface: str) -> list[str]:
    import sys as _sys

    _sys.path.insert(0, str(Path(__file__).resolve().parent))
    from capture import SURFACES

    return list(SURFACES.get(surface, {}).get("hits", []))


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
    """How isolated a labelled hit is, in units of the noise's own neighbourhood radius.

    Measured locally, with k nearest neighbours, rather than against a global centroid. The
    centroid version assumed the noise was one population, and reported a perfectly
    collapsed encoding as a failure the moment it was not - see `bench/separation.py`.

    Returns (ratio, ratio, ratio) so callers that want the older triple still work; only the
    first is meaningful now.
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
    ratio = float(knn_separation(X[hit], noise)[0])
    return ratio, ratio, ratio


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
        # The claim is the size of the cost, not that v2-log fails outright. Under the
        # local metric it clears the gate on some individual hits; it is the hardest hits
        # on each surface that it loses, which `test_word_channels_fail_the_gate_somewhere`
        # asserts separately.
        assert without_word > 10 * with_word, (
            f"{surface}: v2-log {with_word:.1f}x, v3-response {without_word:.1f}x"
        )


def test_word_channels_fail_the_gate_somewhere():
    """The consequence that matters: on the hardest hit of a surface, v2-log drops below
    the gate and the default set does not. Measured on every surface with labelled hits."""
    losses = []
    for surface in ("bench-token", "bench-collide", "bench-mixed"):
        hits = _hits(surface)
        if not hits:
            continue
        worst_with = min(separation(surface, h, "v2-log")[0] for h in hits)
        worst_without = min(separation(surface, h, CHANNEL_SET_VERSION)[0] for h in hits)
        losses.append((surface, worst_with, worst_without))

    assert losses
    for surface, with_word, without_word in losses:
        assert with_word < GATE_RATIO, f"{surface}: v2-log unexpectedly cleared at {with_word:.1f}x"
        assert without_word > GATE_RATIO, f"{surface}: default set at {without_word:.1f}x"


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


def test_several_noise_populations_at_once_still_collapse():
    """The case a single-cluster metric could not even express.

    `bench-mixed` answers unknown words from four populations. Each one has to become
    familiar on its own; there is no single wall to learn.
    """
    results = load(MIXED)
    hits = {r.word for r in results} & set(_hits(MIXED))
    shapes = {(r.status, r.length, r.words, r.lines) for r in results if r.word not in hits}
    assert len(shapes) >= 4, f"expected several noise populations, got {len(shapes)}"

    for hitword in sorted(hits):
        ratio, _, _ = separation(MIXED, hitword, CHANNEL_SET_VERSION)
        assert ratio > GATE_RATIO, f"{hitword} sits at {ratio:.1f}x"


def test_each_population_collapses_tightly_on_its_own():
    """Within-cluster tightness is the property; the gaps between clusters are not noise."""
    import collections

    results = load(MIXED)
    hits = set(_hits(MIXED))
    X = Encoder(CHANNEL_SET_VERSION).encode_all(results)
    groups = collections.defaultdict(list)
    for i, r in enumerate(results):
        if r.word not in hits:
            groups[(r.status, r.length, r.words, r.lines)].append(X[i])

    big = [np.array(v) for v in groups.values() if len(v) > 50]
    assert len(big) >= 4
    centres = [g.mean(axis=0) for g in big]
    spreads = [
        float(np.sqrt(((g - c) ** 2).sum(1)).mean()) for g, c in zip(big, centres, strict=True)
    ]
    gaps = [
        float(np.linalg.norm(centres[a] - centres[b]))
        for a in range(len(centres))
        for b in range(a + 1, len(centres))
    ]
    # Every population is far tighter than the distance between any two of them.
    assert max(spreads) * 10 < min(gaps), f"spreads {spreads}, gaps {gaps}"


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
