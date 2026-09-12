# Is the connectome better at *this* job?

M3 measured the connectome-wired FlyHash on MNIST and Fashion-MNIST. That is the right comparison for the literature - those are the datasets the FlyHash papers used - and it is not this workload. It found no durable difference on novelty detection and a clear loss on retrieval.

Those datasets look nothing like fuzzing traffic. MNIST is ten balanced classes of images reduced by PCA to 55 arbitrary components. A content-discovery scan is a handful of response populations, wildly unbalanced, repeated thousands of times, and the only question is *is this one of the familiar shapes*.

**Until now the comparison could not be made at all.** The measured circuit projects from 55 glomeruli and the default `v3-response` encoder produces 39 channels, so the constructor refused the pair. Every measurement of the connectome in this repository was taken on PCA-reduced image data rather than on the workload the tool exists for. The `v4-glomerular` channel set - v3's response-only features widened to exactly 55 - is what makes this run possible.

What is measured is the shipped ranker, not a geometry. An earlier version of this benchmark measured k-NN separation in tag space and returned ratios around 1e12, because the noise collapses to *identical* tags and a neighbourhood of width zero divides badly. The collapse is measured directly below instead.

## Where the hardest labelled hit lands

Rank of the worst labelled hit, offline mode, out of the full surface - the number that decides whether an operator finds it. Lower is better. Each random variant is 32 independent seeds shown as median [min-max]; `connectome` is a single fixed wiring and has no seed.

| Surface | n | shipped today | `random` | `random-matched` | `connectome` | `connectome` shuffled |
|---|---:|---:|---:|---:|---:|---:|
| `bench-collide` | 1998 | 6 [6-6] | 6 [6-415] | 6 [6-6] | **6** | 6 [6-6] |
| `bench-mixed` | 1998 | 28 [6-986] | 215 [6-1188] | 24 [6-831] | **6** | 14 [6-830] |
| `bench-stable` | 1998 | 6 [6-6] | 6 [6-6] | 6 [6-6] | **6** | 6 [6-6] |
| `bench-token` | 1998 | 8 [8-96] | 40 [8-1375] | 9 [8-332] | **9** | 9 [8-1998] |
| `ffufme-no404` | 1998 | 1 [1-1] | 1 [1-1] | 1 [1-1] | **1** | 1 [1-1] |

### Placed inside the null distribution

The means above cannot carry a claim - the spreads exceed them. So the connectome's single result is placed in each null by counting: how many of the 32 random draws did at least as well. That is an exact one-sided p-value, `(k+1)/(n+1)`, and assumes nothing about the shape of the distribution.

| Surface | connectome | vs `random` | vs `random-matched` | vs shuffled wiring |
|---|---:|---:|---:|---:|
| `bench-collide` | 6 | 29/32, p=0.909 | 32/32, p=1.000 | 32/32, p=1.000 |
| `bench-mixed` | 6 | 4/32, p=0.152 | 9/32, p=0.303 | 10/32, p=0.333 |
| `bench-stable` | 6 | 32/32, p=1.000 | 32/32, p=1.000 | 32/32, p=1.000 |
| `bench-token` | 9 | 14/32, p=0.455 | 25/32, p=0.788 | 28/32, p=0.879 |
| `ffufme-no404` | 1 | 32/32, p=1.000 | 32/32, p=1.000 | 32/32, p=1.000 |

A surface where every draw ties the connectome is a surface with no headroom - the hits are already at the top for everyone, and it discriminates nothing.

### The same projection across every surface at once

The per-surface tests above each compare one number against one null, and none of them reaches significance. They are also not the question an operator faces: you choose a projection once, before seeing any of these surfaces, and you cannot pick the lucky seed afterwards. So the seeds are held together - a draw counts only if it matches the connectome on *every* discriminating surface at once.

Discriminating surfaces (those where the seed changes the answer): `bench-collide`, `bench-mixed`, `bench-token`.

| Null | draws matching the connectome on all of them | p |
|---|---:|---:|
| `random on v3-response` | 3/32 | 0.121 |
| `random` | 1/32 | 0.061 |
| `random-matched` | 8/32 | 0.273 |
| `connectome-permuted` | 9/32 | 0.303 |

## Hits inside the first 25

| Surface | hits | `random` | `random-matched` | `connectome` | shuffled |
|---|---:|---:|---:|---:|---:|
| `bench-collide` | 6 | 6.00 | 6.00 | 6.00 | 6.00 |
| `bench-mixed` | 6 | 5.00 | 5.50 | 6.00 | 6.00 |
| `bench-stable` | 6 | 6.00 | 6.00 | 6.00 | 6.00 |
| `bench-token` | 8 | 7.50 | 8.00 | 8.00 | 8.00 |
| `ffufme-no404` | 1 | 1.00 | 1.00 | 1.00 | 1.00 |

## How hard the familiar collapses

Distinct tags produced by the non-hit responses. Fewer means the projection is treating more near-identical responses as the same thing, which is generalisation rather than discrimination - and generalisation is what a baseline needs when the same page comes back with a different nonce each time.

| Surface | non-hits | `random` | `random-matched` | `connectome` |
|---|---:|---:|---:|---:|
| `bench-collide` | 1992 | 15 | 3 | 65 |
| `bench-mixed` | 1992 | 253 | 22 | 15 |
| `bench-stable` | 1992 | 64 | 4 | 5 |
| `bench-token` | 1990 | 22 | 4 | 75 |
| `ffufme-no404` | 1997 | 88 | 10 | 10 |

## Interpretation

**The connectome beats a plain random projection, and does not beat its own degree-preserving null.** Held across every discriminating surface at once, 1 of 32 uniform random draws matches it (p=0.061); 8 of 32 draws that keep the measured *fan-in* while randomising the *targets* match it (p=0.273), as do 9 of 32 that keep the wiring and shuffle which response feature feeds which glomerulus (p=0.303). Two independent controls agree: nothing detectable here comes from which glomerulus reaches which Kenyon cell. What is left is how unevenly the claws are spread.

**The channel-shuffle control also tells us the encoder-to-glomerulus assignment is arbitrary, which is what we should have expected.** Our channels are response features, not odours. There is no reason status-code-403 belongs in the glomerulus it landed in, and the measurement says it does not matter that it did.

**`v4-glomerular` is a worse encoder than `v3-response`, and that flatters the connectome in the table above.** The four v4 columns are a fair comparison of projections against each other, because they share an encoder. They are not a claim about improving the tool. Measured separately under the random projection, widening v3 to 55 channels *hurt*: median worst rank on `bench-mixed` went from 28 to 215, and v4 won on 0 of 32 seeds on two surfaces. So much of the connectome's apparent gain is recovering ground the wider encoder gave away. Against the tool as it actually ships - the `shipped today` column - the connectome's joint advantage is 3 of 32 draws, p=0.121. Not significant.

**The mechanism is collapse, and it is only part of the story.** Pooled within surfaces across every projection and seed, the number of distinct tags the noise produces predicts where the hardest hit lands: Spearman rho=+0.450, p=7e-16, n=291. Fewer distinct noise tags means the familiar responses depress the same cells together, which leaves the filter's remaining capacity to make a hit stand out. But it explains about a fifth of the variance and no more: `random-matched` collapses the noise harder than the connectome on four of the five surfaces and still loses on `bench-mixed`.

**Where any of this helps is narrow and specific.** Three of five surfaces are saturated - every projection puts every hit at the top, and they measure nothing. The gap opens on `bench-mixed`, whose noise is several different response populations rather than one. That is the same axis on which multi-baseline partitioning was the one decisive win in this project, and it is worth taking seriously as the thing this circuit is actually for: not sharper discrimination, but not being confused by a baseline that is several things at once.

**What shipped.** Not the connectome - it needs a 508 MB download, a 55-channel encoder that is worse on its own, and it does not beat a random projection with its fan-in distribution. The fan-in distribution did ship, as `--projection degree-sampled`: a 13-number histogram, about a kilobyte, no download, usable at any channel width including the one the tool already uses. On `bench-mixed` it cuts the median worst-hit rank from 29 to 9; on `bench-token` it gives up one position of median and removes the tail, worst seed 482 to 18. It is not a general improvement and is not the default. See [claw-degrees.md](claw-degrees.md).

### What would change this

- A corpus with more surfaces whose noise is genuinely heterogeneous. Three of five here are saturated, so most of the table is measuring nothing, and the one surface that discriminates is carrying the whole conclusion.
- Labelled hits that are not synthetic. These are planted, and planted hits may be easier to separate than real ones in ways that favour whichever projection collapses hardest.
- An honest accounting of multiple comparisons. Several variants were tried against several surfaces; no p-value here is corrected, and the one nominally significant result elsewhere (p=0.02) should be read with that in mind.
- A reason to believe the encoder-to-glomerulus assignment *could* matter. If the channels were ever made to correspond to something odour-like, the shuffle control would stop being a formality.

---

*Generated by flypaper 0.1.0 at commit `ff47af04ffa291af1617b6ced0dd28398277ec88` | seed `0` on 2026-09-12T02:02:19+00:00.*
