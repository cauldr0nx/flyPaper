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
| `bench-collide` | ffuf issue #387, faithfully: noise answers 200 and every response shares the hits' word count, so autocalibration has only size/words/lines to work with | 1998 | bench/target/server.py (ours) | 400/s | no |
| `mcware-live` | live surface: constant-size rendered 404, Vercel edge | 1998 | www.mcware.org | 10/s | yes |
| `bench-mixed` | four noise populations at once - an HTML 404, a login redirect, a JSON 403 and a 200 'no results' page - with hits hiding inside them | 1998 | bench/target/server.py (ours) | 400/s | no |

ffufme supplies the wildcard host and an ordinary surface. It has no token-rotating
endpoint and no ffuf issue #387 case, so `bench/target/server.py` supplies both: ours,
MIT, stdlib only, deterministic, loopback only. Response bodies are never stored and
no corpus is committed.

The ffuf issue #387 surfaces are worth stating precisely. On `bench-calib`
**every one of the 1,998 records has `words=100`, the hit included**, so the word
filter autocalibration derives carries no information at all. `bench-collide` goes
further and answers 200 to unknown paths, leaving `-ac` nothing but size, words and
lines.

**Correction.** An earlier version of this report claimed that `-ac` hides the hit on
this surface. It does not. That claim came from a single-word probe whose output was
misread - the result line was there and was scrolled out of the window being read.
Re-tested against ffuf 2.1.0-dev at one, two hundred and two thousand words, `-ac`
shows every hit on both surfaces. The scenario is still worth having in the corpus,
because it is a surface on which one whole field is uninformative by construction;
it is simply not a case the current `-ac` fails. See reports/m4-vs-manual-filters.md.

## 2. Three encodings, measured

Separation ratio: how empty the labelled hit's neighbourhood is, in units of the
typical noise point's. Measured with k nearest neighbours, so it is scale-free
(comparing sets of 11, 52 and 40 channels) and indifferent to how many populations
the noise is made of. 1.0 means the hit is as crowded as the noise. The gate is 4x,
carried over unchanged from the earlier metric.

**This metric replaced a centroid-based one, and the older numbers in this report
were wrong because of it.** The first version divided the hit's distance from the
noise centroid by the noise's mean pairwise spread, which is a sensible measure of
one population and a meaningless one for several: on `bench-mixed` each population
collapses to a spread of 0.014-0.025 while the populations sit 1.5-2.2 apart, so the
'spread' was measuring the gaps between them and a perfect encoding scored as a
failure. The correction cuts both ways - under the local metric `v1-raw` and
`v2-log` clear the gate on more surfaces than this report previously credited them
with, and that is stated here rather than quietly improved away.

| Surface | Scenario | `v1-raw` (11ch) | `v2-log` (52ch) | `v3-response` (39ch) |
|---|---|---:|---:|---:|
| `bench-token` | rotating token | 0.9x | 1.1x | **62.2x** |
| `ffufme-no404` | wildcard / soft-404 | **40.9x** | **8.9x** | **761.1x** |
| `bench-calib` | ffuf issue #387 | **24.4x** | **15.0x** | **2149.4x** |
| `bench-collide` | ffuf issue #387, no status hint | 1.4x | 2.1x | **317.5x** |
| `bench-mixed` | four noise populations | 1.9x | 1.0x | **19.8x** |
| `bench-stable` | control: identical 404s | **31.2x** | **16.5x** | **3181.6x** |
| `ffufme-basic` | ordinary 404s | **51.0x** | **19.5x** | **1523.3x** |

Bold clears the gate. Each figure is the **hardest labelled hit** on that surface, not a designated one: surfaces carry hits spanning obvious to subtle, and the obvious ones separate under almost any encoding.

| Surface | Hardest hit under the default set |
|---|---|
| `bench-token` | `internal` |
| `ffufme-no404` | `secret` |
| `bench-calib` | `staging` |
| `bench-collide` | `staging` |
| `bench-mixed` | `internal` |
| `bench-stable` | `old` |
| `ffufme-basic` | `class` |

| Channel set | Channels | Surfaces clearing the gate |
|---|---:|---:|
| `v1-raw` | 11 | 4 / 7 |
| `v2-log` | 52 | 4 / 7 |
| `v3-response` | 39 | 7 / 7 |

## 3. What the comparison actually showed

**The input-word channels are the problem, and they are most of it.** Section 6 of
the brief lists an input-word character-class profile - extension, depth, casing,
entropy - as a candidate encoding, so `v2-log` includes one. **It is worse than the
naive raw encoding on five of seven surfaces despite having five times as many
channels**, and the reason is measurable rather than mysterious: on the wildcard
host, **100% of the variance inside the soft-404 cluster comes from the
`word.*` channels alone**.

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

**Several noise populations at once are not harder, they are just more of the
same.** `bench-mixed` answers unknown words from four populations - an HTML 404,
a login redirect, a JSON 403 and a 200 'no results' page - in roughly
55/25/10/10 proportion. Each collapses to a spread of 0.014-0.025 while the four
sit 1.5-2.2 apart, so the encoding treats them as four dense regions rather than
one smeared one. Its hardest hit still clears the gate at 19.8x, and that
hit shares its status code with the population it is hiding in and differs from
it by under 4% in word count.

This is the surface that exposed the measurement error above, and it is worth
separating the two: the encoder always handled several populations correctly, and
the metric could not say so.

## 4. Honest limitations of this result

- **The corpus is synthetic and small.** Five surfaces, 1,998 requests each, three of
  them served by a target written to contain exactly the scenarios being tested. That
  is a fair test of whether the encoder has the properties claimed, and it is not
  evidence about real applications.
- **Six labelled hits per synthetic surface, one on each ffufme surface.** Enough
  for a separation ratio and for M4's precision figures, not enough for tight ones.
- **The separation ratios are not comparable to anything published.** They are a
  measure defined here to decide this gate.
- **`v1-raw` and `v2-log` are kept, not deleted.** They are the evidence for why the
  default is what it is, and a regression test asserts the gap stays large.

## 5. Status

`v3-response` clears the gate on 7 of 7 surfaces, including both properties the gate names. The channel set is
versioned, every baseline will record the version that built it, and comparing scores
across versions is meaningless by construction.

Next: M3, the connectome FlyHash, which runs fully offline.

---

*Generated by flypaper 0.1.0 at commit `3ce993af9626529bb7e5148e48a17949cf1b77e2` **(dirty working tree - not reproducible)** | seed `20260911` on 2026-09-11T21:46:04+00:00.*
