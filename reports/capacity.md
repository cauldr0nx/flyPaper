# Is 2,045 Kenyon cells the right number?

It is the fly's number, measured from MaleCNS, and this tool inherited it without ever asking. Three separate measurements now say capacity is what binds: partitioning is the largest win in the project and a partition is capacity bought by splitting the problem; the measured MBON-alpha'3 reads 345 cells instead of 2,045 and is much worse; and the filter stops working somewhere near 200 distinct response shapes whatever the decay setting.

If capacity binds, the number of cells is the most direct lever there is - and it is free for us in a way it is not for a fly. A Bloom filter is one float per cell: 2,045 cells is 16 kB and 16,384 is 128 kB.

Swept unpartitioned, so this and partitioning are not confounded, over 16 seeds. A tag stays a fixed 5% of the layer, so a bigger layer means more active cells too, as in the published model.

## Where the hardest labelled response lands

| Surface | 512 | 1,024 | 2,045 | 4,096 | 8,192 | 16,384 |
|---|---|---|---|---|---|---|
| `bench-sprawl` | 904 | 1036 | 209 · | 258 | **66** | 118 |
| `bench-mixed` | 198 | 52 | 18 · | 29 | **6** | 6 |
| `bench-token` | 8 | 8 | 8 · | **8** | 8 | 8 |
| `bench-collide` | tied | tied | tied | tied | tied | tied |
| `bench-stable` | tied | tied | tied | tied | tied | tied |

Median of the worst labelled response's rank; lower is better. **Bold** is the best size for that surface, `·` marks the fly's 2,045. A surface where every size ties has no headroom and measures nothing.

## Is bigger actually better

Mann-Whitney U, one-sided, each size against the fly's 2,045. Uncorrected across 5 comparisons per surface, which matters for reading a single cell and not for reading the shape of a column.

| Surface | 512 | 1,024 | 4,096 | 8,192 | 16,384 |
|---|---|---|---|---|---|
| `bench-sprawl` | 0.727 — | 0.791 — | 0.346 — | 0.043 better | 0.110 — |
| `bench-mixed` | 0.842 — | 0.538 — | 0.409 — | 0.035 better | 0.003 better |
| `bench-token` | 0.848 — | 0.412 — | 0.178 — | 0.080 — | 0.007 better |
| `bench-collide` | tied | tied | tied | tied | tied |
| `bench-stable` | tied | tied | tied | tied | tied |

## Does it trend, or is one cell lucky

The table above is 5 comparisons per surface and none of them is corrected, so no single cell in it carries much. The claim worth testing is the shape of the column: does the worst response's rank fall as the layer grows? Spearman over every seed at every size, so it asks about the trend rather than about a pair.

| Surface | rho | p | n |
|---|---:|---:|---:|
| `bench-sprawl` | -0.415 | 0.00003 | 96 |
| `bench-mixed` | -0.405 | 0.00004 | 96 |
| `bench-token` | -0.319 | 0.00153 | 96 |
| `bench-collide` | tied | — | — |
| `bench-stable` | tied | — | — |
| **pooled** | **-0.396** | **2.83e-12** | 288 |

Pooled after centring each surface on its own mean, so the correlation is about size and not about one surface being harder than another.

## Interpretation

**Bigger is better, monotonically, across every surface with headroom.** Pooled over the three that discriminate, the median worst-response rank falls 264 → 50 at the fly's number → 8 at 16,384 cells. Each surface trends the same way on its own, and they were built independently of one another.

**So 2,045 is not the right number for this job.** It is the fly's number, and this tool inherited it because the circuit it models has that many Kenyon cells. That is a fact about a fly, not about content discovery. A fly grades novelty over the odours of a life; a scan asks the same filter to keep two thousand response shapes apart in under a minute, and gives it 16 kB of state to do it with.

**It is cheap, once the filter stops doing needless work.** A tag is 5% non-zero, and the filter used to sum over the whole layer - 95% of that arithmetic on zeros. Above about 8,000 cells numpy also leaves its fast path for small operands, so the dense version cost twelve times as much at 16,384 cells as at 8,192, for twice the size. Summing over the active cells instead removes both: a large layer is now roughly linear in cost and 16,384 cells runs at over 1,700 responses a second, far above any rate a polite scan uses.

**This is the same result as every other one here, in a different costume.** Partitioning wins because a partition is capacity. The measured MBON-alpha'3 loses because 345 cells is less capacity. Temporal decay stops helping at a few hundred distinct shapes because that is the capacity. The one lever that was never pulled was the number of cells itself, and it was set to a fly's.

**And it is something the connectome cannot do.** `--projection connectome` is pinned to 2,045 cells by construction: the wiring is a measurement, and a measurement of a fly has as many Kenyon cells as the fly had. The random projection can be any width. That is a practical advantage of the published model over the measured one which has nothing to do with accuracy, and it is worth more here than anything the wiring was ever asked for.

### What would change this

- A real corpus. Three synthetic surfaces of 1,998 responses each agree, and they are still three surfaces built by the same hand.
- The interaction with partitioning. Both buy capacity, and they are measured separately here on purpose; whether they add or overlap is not known.
- The top of the curve. The trend has not turned by 16,384 cells, so the useful size may be larger still - or may be bounded by something this corpus is too small to show.

---

*Generated by flypaper 0.1.0 at commit `6177528c831c52868570e4e66e60d6b12e57ffdc` **(dirty working tree - not reproducible)** | seed `0` on 2026-09-13T07:20:57+00:00.*
