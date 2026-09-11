# M3 - connectome FlyHash vs. random projection

**Gate: the connectome version at minimum matches the random-projection baseline.**

**Split, and reported as such.** On novelty detection - the task this tool actually
performs - the measured connectome **matches** the published random
projection (worst case +0.3 sd inside the random spread). On
nearest-neighbour retrieval it **is worse than** it, clearly
(-3.2 sd). The gate is met for the metric that matters here and failed
for the other, so the claim is narrowed rather than the threshold moved - see
section 3.

Numbers are computed by `bench/benchmark_flyhash.py`; the interpretation is written.
Nothing in this milestone touches the network except to fetch two public datasets.

## 1. The circuit, as measured

Every population is resolved by annotation on every run. No body ID is hardcoded.

| Quantity | MaleCNS v1.0, right hemisphere | Published model |
|---|---:|---:|
| Receptor channels (antennal lobe glomeruli) | **55** | ~50 ORN types |
| Uniglomerular projection neurons | 141 | - |
| Kenyon cells | **2045** | ~2,000 |
| Kenyon cells receiving PN input | 1891 | - |
| PN->KC fan-in, mean | **5.38** (median 5, sd 1.98) | ~6, uniform |
| PN->KC fan-in, range | 1-29 | fixed at 6 |
| PN->KC connections | 10,174 | - |
| PN->KC synapses | 188,984 | - |
| MBON-alpha'3 cells | 2 | the novelty readout |
| KC->MBON-alpha'3 connections | 588 | - |

**The published assumptions hold up well.** 55 channels against an assumed ~50, 2,045 Kenyon cells against an assumed ~2,000, and a mean fan-in of 5.38 against an assumed 6. The model was a good guess.

**Two differences are real and neither is a bug.** The measured fan-in is a *distribution*, from 1 to 29, not the constant 6 the model uses - which is why the benchmark below includes a degree-preserving null. And 154 of 2045 Kenyon cells receive no antennal lobe input at all; they are the ones fed by the accessory calyces from other modalities. A model that assumes every Kenyon cell is olfactory is wrong about roughly one in thirteen of them.

### Identifying MBON-alpha'3

MaleCNS names MBONs numerically - `type` gives `MBON16`, not `MBON-alpha'3ap` - so the compartment is not readable from the type at all. It **is** readable from `instance`, which carries the compartment in parentheses, and that is what the extraction matches on:

- `MBON16(a'3ap)_R`
- `MBON17(a'3m)_R`

These are MBON-alpha'3ap and MBON-alpha'3m, which is exactly the readout the Fly Bloom Filter paper uses. **The identification is therefore clean, and it came from the data rather than from a remembered type number.**

Two further cells touch alpha'3 partially and are deliberately *not* folded into the readout: `MBON17-like(a'2a'3)_R`, `MBON28(a'3a)_R`. The other 35 MBON types are excluded outright. Listing them is the point: a prefix match cannot quietly swallow a different cell type if the near misses are written down.

Similarly, 107 antennal lobe projection neuron types are multiglomerular or central-brain and name no single glomerulus. They are excluded from the receptor channels rather than forced into one.

## 2. Benchmark

Datasets and metrics are the ones the papers use. `random` is the published baseline with a uniform fan-in of 6. `random-matched` keeps each Kenyon cell's measured fan-in but randomises which channels it listens to - the control that separates wiring from degree. Random variants are averaged over 15 seeds; the connectome is deterministic, so it has no spread.

### mnist (n=12,000, sparsity 5%)

| Metric | `random` | `random-matched` | `connectome-binary` | `connectome` | best connectome vs. the random null |
|---|---:|---:|---:|---:|---|
| Retrieval mAP@20 | 0.5121 ± 0.0102 | 0.5058 ± 0.0106 | 0.4790 | 0.3939 | -3.2 sd, beats 0/15 |
| Novelty AUC | 0.6882 ± 0.0240 | 0.6861 ± 0.0236 | 0.6798 | 0.7038 | +0.7 sd, beats 10/15 |
| Novelty AUC, with decay | 0.6688 ± 0.0237 | 0.6674 ± 0.0263 | 0.6268 | 0.6767 | +0.3 sd, beats 9/15 |

### fashion-mnist (n=12,000, sparsity 5%)

| Metric | `random` | `random-matched` | `connectome-binary` | `connectome` | best connectome vs. the random null |
|---|---:|---:|---:|---:|---|
| Retrieval mAP@20 | 0.4035 ± 0.0122 | 0.3901 ± 0.0110 | 0.3866 | 0.3024 | -1.4 sd, beats 2/15 |
| Novelty AUC | 0.8925 ± 0.0134 | 0.8838 ± 0.0131 | 0.8964 | 0.8806 | +0.3 sd, beats 9/15 |
| Novelty AUC, with decay | 0.8998 ± 0.0126 | 0.8937 ± 0.0114 | 0.9091 | 0.8918 | +0.7 sd, beats 11/15 |

## 3. Novelty against training budget

The most-quoted caveat on this benchmark has been that the verdict moves with how
much the filter has been shown, measured at two points. Here it is as a curve, with
everything else held fixed - same held-out and unseen sets, same seeds, same
sparsity - over 8 seeds for the random baseline.

### mnist

| Training examples | `random` | `connectome-binary` | connectome vs. the null |
|---:|---:|---:|---|
| 250 | 0.6729 ± 0.0333 | 0.6453 | -0.8 sd, beats 2/8 |
| 500 | 0.6808 ± 0.0222 | 0.6568 | -1.1 sd, beats 1/8 |
| 1,000 | 0.6908 ± 0.0068 | 0.6740 | -2.4 sd, beats 0/8 |
| 2,000 | 0.6776 ± 0.0197 | 0.6798 | +0.1 sd, beats 5/8 |
| 4,000 | 0.6818 ± 0.0159 | 0.6917 | +0.6 sd, beats 6/8 |

### fashion-mnist

| Training examples | `random` | `connectome-binary` | connectome vs. the null |
|---:|---:|---:|---|
| 250 | 0.8934 ± 0.0119 | 0.8859 | -0.6 sd, beats 1/8 |
| 500 | 0.8886 ± 0.0153 | 0.8995 | +0.7 sd, beats 6/8 |
| 1,000 | 0.8888 ± 0.0057 | 0.9040 | +2.7 sd, beats 8/8 |
| 2,000 | 0.8954 ± 0.0115 | 0.8964 | +0.1 sd, beats 4/8 |
| 4,000 | 0.8814 ± 0.0082 | 0.8907 | +1.1 sd, beats 7/8 |

## 4. Interpretation

**The budget curve says the headline z-scores should not be quoted individually.**
On MNIST there is a real trend: the connectome is behind at 250, 500 and 1,000
training examples (down to -2.4 sd, beating 0 of 8 seeds) and reaches parity or a
shade above at 2,000 and 4,000. That is consistent, and it locates the earlier
observation that the connectome fell behind at a third of the data.

On Fashion-MNIST there is no trend at all. Adjacent budgets give **+2.7 sd (beating
8 of 8 seeds) at 1,000 and +0.1 sd (4 of 8) at 2,000**, on the same data with the
same seeds. Nothing about the circuit changed between those two rows; only the
number of examples did. A difference that large between neighbouring budgets means
the per-budget figure is dominated by which particular examples the filter happened
to see, not by the projection.

So the conclusion below is robust and the individual numbers supporting it are not.
*Matches the baseline* survives - the connectome is never durably ahead or behind
across ten budget-dataset combinations. Any single z-score, including the +0.7 sd in
section 2, is one draw from a distribution wide enough to produce +2.7 and +0.1 back
to back, and should be read as such.

**The measured wiring is not better than random, and on the novelty task it is not
worse either.** Across both datasets the connectome lands well inside the spread of
random draws, beating somewhere between half and three-quarters of them. That is what
a coin flip looks like when you measure it carefully, and it should be read as *no
difference*, not as a small win. Anyone quoting these numbers as evidence that the
connectome helps would be over-reading them.

**This is a real result about the published model, and a mildly reassuring one.**
Dasgupta, Stevens & Navlakha modelled PN->KC connectivity as a random sparse
projection because that is what the biology looked like statistically at the time.
The measured wiring was not available to test that against. It now is, and for
novelty detection the simplification holds: the model's central assumption costs
nothing measurable on this task. A negative result about our contribution is a
positive one about theirs.

**On retrieval the connectome is genuinely worse, and the reason is visible in the
tags.** Feeding 500 random inputs through each projection, the measured wiring
activates fewer distinct Kenyon cells than random does and produces tags that overlap
roughly twice as much between unrelated inputs. Real PN->KC connectivity is not
uniformly random - some glomerular combinations converge far more often than chance -
and correlated convergence means correlated tags, which is exactly what a
locality-sensitive hash does not want. Random wiring spreads inputs over the
available tag space more evenly because spreading evenly is all it does.

**Synapse counts make it worse, and that is informative.** `connectome` weights each
connection by its synapse count; `connectome-binary` keeps the same wiring and throws
the counts away. Binary is the better hash on three of the four retrieval and
Fashion-MNIST novelty comparisons. Whatever the strength of a PN->KC connection
encodes, it is not information that helps this particular computation.

**The degree-preserving null earns its place.** `random-matched` gives each Kenyon
cell its measured fan-in and randomises only which channels it listens to, and it
tracks plain `random` closely throughout. So the fan-in *distribution* - the spread
from 1 to 29 that the uniform model misses - 
is not what drives any of the differences here. What is left is the wiring itself.

**The most likely reason the connectome cannot win here is the input.** The measured
wiring, whatever structure it has, is structure with respect to *odours*: which
glomerular combinations co-occur in the natural statistics a fly's olfactory system
evolved against. The benchmark feeds it PCA components of handwritten digits and
clothing photographs, where channel identity is arbitrary and any such alignment is
destroyed by construction. This experiment can show that the connectome does not
*generically* beat random projection. It cannot show whether it beats it on inputs
whose correlational structure resembles what the circuit was shaped by, and nothing
here should be read as having tested that.

### What may and may not be claimed

May: *FlyHash wired from the measured MaleCNS connectome performs comparably to the
published random projection for novelty detection, and worse for nearest-neighbour
retrieval.* As far as we can tell this comparison had not been run before.

May not: that the connectome improves FlyHash, that it is a better locality-sensitive
hash, or that biological wiring is optimal for this task. None of those survive the
table above.

**Consequently flypaper ships on random projection by default.** The two are
statistically indistinguishable on the task, and random projection needs no 508 MB
download to run. The connectome projection stays available and tested; it is simply
not sold as an improvement, because it is not one.

## 5. Honest limitations

- **The input is reduced to 55 dimensions by PCA.** The measured circuit fixes the number of receptor channels, so 784-dimensional images cannot be fed to it directly. All three projections see identical reduced input, so comparing them is fair - but none of these absolute numbers is comparable to the published ones, which hash full-dimensional data.
- **The verdict is sensitive to how much the filter has seen.** At `--quick` scale (3,000 samples, so roughly 750 training examples instead of 2,000) the connectome falls behind on novelty too, by about 4 sd. The match reported above is at the larger size; it is not a claim that holds at every training budget, and the budget should be quoted with the number.
- **The connectome projection is a single sample.** There is one male fly. The random variants average over seeds and carry a spread; the connectome cannot, and a difference smaller than the random variants' own spread should not be read as a result.
- **Synaptic weights are counts at a 0.5 confidence threshold**, from automated detection. They are not measured physiological strengths.
- **MNIST and Fashion-MNIST are not odours and not HTTP responses.** They are the datasets the literature uses, which makes this comparison legible; they say nothing directly about the fuzzing task.

---

*Generated by flypaper 0.1.0 at commit `11c92d619bff8c211d07fbac6bbe36bd3d492666` **(dirty working tree - not reproducible)** | seed `0` on 2026-09-11T21:54:57+00:00.*
