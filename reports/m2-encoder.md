# M2 - encoder and corpus

**Gate: both invariance properties hold on a corpus containing a wildcard-responding host and a token-rotating endpoint. Met** by `v3-response`, which is the default for that reason.

Numbers are computed by `bench/report_encoder.py`; the interpretation is written.

## 1. The corpus

| Surface | Scenario | Records | Target | Rate | Live |
|---|---|---:|---|---:|---|
| `ffufme-no404` | wildcard / soft-404 host: every unknown path answers 200 | 1998 | ffufme container (local) | 400/s | no |
| `ffufme-basic` | ordinary surface: real 404s, one real hit | 1998 | ffufme container (local) | 400/s | no |
| `bench-token` | token rotation: identical page, variable-length CSRF token, defeating -fs | 1998 | bench/target/server.py (ours) | 400/s | no |
| `bench-calib` | ffuf issue #387: the hit's word count collides with the autocalibrated filter | 1998 | bench/target/server.py (ours) | 400/s | no |
| `bench-stable` | control: byte-identical 404s, nothing jitters | 1998 | bench/target/server.py (ours) | 400/s | no |

ffufme supplies the wildcard host and an ordinary surface. It has no token-rotating
endpoint and no ffuf issue #387 case, so `bench/target/server.py` supplies both: ours,
MIT, stdlib only, deterministic, loopback only. Response bodies are never stored and
no corpus is committed.

The ffuf issue #387 surface is worth stating precisely: it is stronger than the
tracker's report. **Every one of the 1,998 records has `words=100`, the hit included.**
The word filter `-ac` derives is not merely unlucky, it carries no information at all.
Confirmed against real ffuf: without `-ac` the hit shows as
`[Status: 200, Size: 1503, Words: 100, Lines: 40]`; with `-ac` it is hidden.

## 2. Three encodings, measured

Separation ratio: how far the labelled hit sits from the noise cluster's centroid,
in units of that cluster's own mean pairwise spread. Scale-free, so it compares
across sets of different dimensionality. The gate is 4x.

| Surface | Scenario | `v1-raw` (11ch) | `v2-log` (52ch) | `v3-response` (39ch) |
|---|---|---:|---:|---:|
| `bench-token` | rotating token | 1.9x | 1.0x | **33.5x** |
| `ffufme-no404` | wildcard / soft-404 | **5.3x** | 1.1x | **40.0x** |
| `bench-calib` | ffuf issue #387 | 3.1x | 1.8x | **142.9x** |
| `bench-stable` | control: identical 404s | 3.0x | 1.9x | **172.6x** |
| `ffufme-basic` | ordinary 404s | **4.1x** | 1.8x | **11.9x** |

Bold clears the gate.

| Channel set | Channels | Surfaces clearing the gate |
|---|---:|---:|
| `v1-raw` | 11 | 2 / 5 |
| `v2-log` | 52 | 0 / 5 |
| `v3-response` | 39 | 5 / 5 |

## 3. What the comparison actually showed

**The input-word channels are the problem, and they are most of it.** Section 6 of
the brief lists an input-word character-class profile - extension, depth, casing,
entropy - as a candidate encoding, so `v2-log` includes one. It is the worst of the
three on every surface, and the reason is measurable rather than mysterious: on the
wildcard host, **99% of the variance inside the soft-404 cluster comes from
the `word.*` channels alone**.

In hindsight it could not have been otherwise. The fuzzed word is different on every
single request - that is what fuzzing is - so encoding it guarantees every response
is unique and there is no familiar cluster left to be familiar. `v3-response` is
`v2-log` with those channels removed, and it is the default.

This does not make the word worthless. A `.bak` answering 200 is more interesting
than a random string answering 200. But that is a ranking concern for M4, where it
can be a tie-breaker over an already-formed cluster, not a clustering signal for
stage one, where it destroys the thing being clustered.

**A z-score needs a noise floor or it amplifies noise without bound.** The second
defect found the same way. When a quantity is near-constant its standard deviation
collapses toward zero, so dividing by it turns a six-byte token jitter, or a
millisecond of scheduling, into a full-range signal. Before the floors were added,
`time.z` alone carried a within-cluster standard deviation of 0.12 on a cluster whose
total variance should have been near zero. Every z-scored channel now states, in the
units of its own quantity, how large a difference has to be before it counts: 2% of
the running mean for the size fields, 50% for response time.

**Needles must not be stacked at the front of the wordlist.** Not an encoder defect,
a corpus one, and it flattered nothing - it made things worse. The encoder is
single-pass, so a hit at position 3 is scored before any baseline exists and its
z-scored channels are all still returning "no opinion". The hits are now scattered
through the list by seeded shuffle.

## 4. Honest limitations of this result

- **The corpus is synthetic and small.** Five surfaces, 1,998 requests each, three of
  them served by a target written to contain exactly the scenarios being tested. That
  is a fair test of whether the encoder has the properties claimed, and it is not
  evidence about real applications.
- **One labelled hit per surface.** Enough for a separation ratio, not enough for a
  precision figure. Ranking metrics arrive at M4 and need more labels than this.
- **The separation ratios are not comparable to anything published.** They are a
  measure defined here to decide this gate.
- **`v1-raw` and `v2-log` are kept, not deleted.** They are the evidence for why the
  default is what it is, and a regression test asserts the gap stays large.

## 5. Status

`v3-response` clears the gate on 5 of 5 surfaces, including both properties the gate names. The channel set is
versioned, every baseline will record the version that built it, and comparing scores
across versions is meaningless by construction.

Next: M3, the connectome FlyHash, which runs fully offline.

---

*Generated by flypaper 0.1.0 at commit `74e4bf4919fdc21e0c0ae541f1df9fa0f7c2631a` **(dirty working tree - not reproducible)** | seed `20260911` on 2026-09-11T18:49:16+00:00.*
