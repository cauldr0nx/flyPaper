# The fly's fan-in spread, without the fly

[connectome-on-workload.md](connectome-on-workload.md) found that the measured wiring beats a plain random projection and does **not** beat its own degree-preserving null. Whatever advantage exists is therefore not in which glomerulus reaches which Kenyon cell. It is in how unevenly the claws are spread - and that is a histogram of 13 numbers.

The published FlyHash gives every Kenyon cell the same fan-in, usually 6. The measured circuit gives them 1 to 29, mean 5.38, s.d. 1.98. Shipped as `flypaper/brain/claw-degrees.json` (1355 bytes, most of it the CC BY attribution), it needs no download and applies at any channel count - including the v3-response encoder the tool actually uses, which the connectome itself cannot be fed at all.

Everything below is on `v3-response`, offline mode, 64 seeds per cell. `uniform-5` is the control that matters as much as the connectome's own: if matching the measured *mean* explains the effect, the result is 'use fewer claws' and has nothing to do with the fly.

## Where the hardest labelled hit lands

| Surface | variant | median | mean | p90 | worst |
|---|---|---:|---:|---:|---:|
| `bench-mixed` | uniform-6 | 29 | 159.8 | 537 | 986 |
| `bench-mixed` | uniform-5 | 26 | 177.9 | 430 | 1262 |
| `bench-mixed` | degree-sampled | 9 | 128.8 | 429 | 858 |
| `bench-sprawl` | uniform-6 | 267 | 497.7 | 1233 | 1940 |
| `bench-sprawl` | uniform-5 | 174 | 534.4 | 1573 | 1998 |
| `bench-sprawl` | degree-sampled | 358 | 479.7 | 1168 | 1852 |
| `bench-token` | uniform-6 | 8 | 19.4 | 12 | 482 |
| `bench-token` | uniform-5 | 96 | 301.1 | 794 | 1770 |
| `bench-token` | degree-sampled | 9 | 9.0 | 10 | 18 |
| `bench-collide` | uniform-6 | 6 | 6.0 | 6 | 6 |
| `bench-collide` | uniform-5 | 6 | 28.9 | 6 | 760 |
| `bench-collide` | degree-sampled | 6 | 6.0 | 6 | 6 |
| `bench-stable` | uniform-6 | 6 | 6.0 | 6 | 6 |
| `bench-stable` | uniform-5 | 6 | 6.0 | 6 | 6 |
| `bench-stable` | degree-sampled | 6 | 6.0 | 6 | 6 |

## Is the difference real

Mann-Whitney U, one-sided, against `uniform-6`, on the rank itself. A surface where every variant ties is saturated and measures nothing. The worst case is reported beside it because the two do not always move together, and for this tool they are not equally important: a median of 8 against 9 is invisible to an operator, and a worst case of 482 is a hit nobody ever scrolls to.

| Surface | `uniform-5` | `degree-sampled` | `degree-sampled` paired | seeds better / worse |
|---|---|---|---|---|
| `bench-mixed` | p=0.4657 (no difference) | p=0.0298 (better) | p=0.1674 | 36 / 24 of 64 |
| `bench-sprawl` | p=0.2886 (no difference) | p=0.5238 (no difference) | p=0.3478 | 33 / 31 of 64 |
| `bench-token` | p=1.0000 (worse) | p=0.9889 (worse) | p=0.7616 | 13 / 27 of 64 |
| `bench-collide` | p=0.9604 (worse) | tied - saturated | tied - saturated | - |
| `bench-stable` | tied - saturated | tied - saturated | tied - saturated | - |

Both tests are reported because they disagree. Mann-Whitney treats the two sets of seeds as independent samples; Wilcoxon pairs them by seed, which is the more conservative reading and the defensible one here, since both variants are built from the same RNG stream. Where a result survives only the unpaired test, it is not a result.

## Interpretation

**It did not replicate.** The first version of this measured `bench-mixed` alone, found the measured fan-in spread cut the median worst-hit rank from 29 to 9 at p=0.0298, and said in as many words that a second heterogeneous surface reproducing it would be worth more than any further analysis of the first. `bench-sprawl` is that surface - seven populations against four, each jittering internally, built and captured before the projection was run against it. On it the median goes the *wrong* way, 267 to 358, p=0.5238.

**And the surviving result does not survive pairing.** On `bench-mixed` itself, pairing the seeds instead of treating them as independent samples takes p=0.0298 to p=0.1674. One nominally significant result, on one surface, under one of two reasonable tests, with no correction for the several variants and surfaces tried, is what noise looks like.

**So the honest verdict is that the measured fan-in distribution does not help.** That is a real answer to the question `reports/connectome-on-workload.md` raised, and it closes the last route by which the connectome was contributing anything to this tool's ranking. `--projection degree-sampled` stays in the code because it is the control that makes the connectome comparison interpretable, and because removing a variant because its result was negative is how a benchmark suite starts lying. It is not recommended and it is not the default.

**One thing is left, and it is small.** `bench-token` has a rare catastrophic seed under `uniform-6` - the worst of 64 puts the hardest hit at rank 482, and its p99 is 239. Under `degree-sampled` the worst of 64 is 18. That is one surface and a handful of seeds, nowhere near enough to act on, and it is recorded here only so that it is not rediscovered later and mistaken for a new result.

**`uniform-5` remains a clean negative.** Matching the measured *mean* fan-in is not merely insufficient, it is harmful: no different on `bench-mixed` (p=0.4657) and much worse on `bench-token` and `bench-collide`, where it turns a surface every other variant saturates into an unreliable one. Whatever the published fan-in of 6 is doing, moving it is not free.

**What this cost and what it bought.** A result was published on one surface and retracted on two. The retraction is the point: the falsification criterion was written into the report before the surface existed, which is the only reason it could fire. `bench-sprawl` stays in the corpus - it is the hardest surface here by a wide margin, every variant leaves the worst hit past rank 250, and that makes it the most useful thing to build against next.

---

*Generated by flypaper 0.1.0 at commit `760a5021913bf164bb6242e07a259a7404d7c4a8` **(dirty working tree - not reproducible)** | seed `0` on 2026-09-12T02:34:30+00:00.*
