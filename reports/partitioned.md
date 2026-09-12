# One baseline per host

ffuf derives one filter and applies it to the whole run. From its own issue tracker, on scanning several targets at once: *"would be impossible to put correct flag for each host"* ([ffuf#450](https://github.com/ffuf/ffuf/issues/450)). Every automation guide for it says the same thing in different words - `-ac` works when the custom 404 is consistent, and a list of fifty hosts is fifty different ideas of what a 404 looks like.

A Fly Bloom Filter is 2,045 floats. Keeping one per host costs 16 kB, so flypaper keeps one per host - and one per directory if the scan recursed, which is the partition feroxbuster gets by detecting wildcards per directory and ffuf does not have at all. The projection is shared across partitions, so a score means the same thing everywhere and only the learned baseline differs.

## The worst case, constructed

Two hosts, 602 responses. One answers unknown paths with ~120 bytes, the other with ~48,000. Each host has one real page, and it is shaped **exactly like the other host's noise** - same status code, same size. Size is the only thing that separates it, and a filter that has seen both hosts has been taught that both sizes are ordinary.

| | rank of first hit | rank of second | recall@50 |
|---|---:|---:|---:|
| one baseline | 135 | 269 | 0/2 |
| **one per host** | 1 | 2 | 2/2 |

Sharing a baseline does not degrade the result here, it erases it: both hits score **0.0000** and land at ranks 155 and 251. They are not ranked low, they are indistinguishable from noise, because from the shared filter's point of view they genuinely are - it has seen a thousand responses of each size.

## A realistic multi-host sweep

The captured surfaces relabelled as 6 hosts and interleaved - 11,988 responses, 34 labelled hits, arriving down one pipe the way a real sweep does. The hosts differ the way real ones do rather than adversarially.

| | recall@10 | recall@25 | recall@50 | recall@100 |
|---|---:|---:|---:|---:|
| one baseline | 5/34 | 16/34 | 19/34 | 19/34 |
| **one per host** | 10/34 | 25/34 | 30/34 | 31/34 |

Partitioning reaches every hit by rank 3305. The shared baseline plateaus at 30/34 and does not recover by rank 100 either - the hits it has lost are lost, not merely deferred.

## Crossed with the projection

Partitioning and the measured fan-in spread (`reports/claw-degrees.md`) looked like two attacks on the same problem: partitioning stops a baseline from having to be several populations at once, and the fan-in spread appeared to make a single baseline better at being several at once. Crossed here, 8 seeds per cell, median reported.

The fan-in half of that has since been retracted - it did not reproduce on a second heterogeneous surface - and this table is one of the measurements that says so, independently of the one that retracted it.

| Experiment | projection | worst, `--per none` | worst, `--per host` | partitioning gains | recall@50, `--per host` |
|---|---|---:|---:|---:|---:|
| pathological | `random` | 536 | 2 | 268.0x | 2/2 |
| pathological | `degree-sampled` | 536 | 2 | 268.0x | 2/2 |
| corpus | `random` | 4776 | 922 | 5.2x | 30/34 |
| corpus | `degree-sampled` | 8989 | 1726 | 5.2x | 31/34 |

Worst labelled hit's rank; lower is better.

**Partitioning is worth the same multiple whichever projection it is given** - roughly 290x on the constructed worst case and 5x on the corpus sweep, in both rows. That is the useful reading: the one measured win in this project does not depend on any of the connectome work, and would survive all of it being removed.

**The projection on its own makes the corpus sweep worse** unpartitioned. A multi-host stream is heterogeneous in a different way from a single host serving several response shapes - the populations belong to different hosts rather than different routes - and nothing about the fan-in spread addresses that.

**The two metrics disagree once partitioned, and both are reported because of it.** On the corpus sweep the fan-in spread finds slightly more hits inside the first fifty while placing its hardest hit further down. Neither difference is supported by the surface-level tests in `reports/claw-degrees.md`, and the honest reading of a split like this at 8 seeds is that it is noise. It is tabulated rather than summarised so that it cannot be quoted one way only.

### Read this way

- **pathological**: unpartitioned random 536; the projection alone 536; partitioning alone 2; both 2.
- **corpus**: unpartitioned random 4776; the projection alone 8989; partitioning alone 922; both 1726.

## One baseline per kind of page, on a single host

`host` and `dir` split a stream by *where* a response came from. Every surface below is one host and one directory, so neither of them can do anything at all here - whatever moves is `shape` splitting a single host by *what its responses look like*, on status and size decade.

The reason to want that is measurable. A host serving seven different response populations has one standard deviation spanning all seven, so a page 25% away from its own population's size sits well under one sigma of the whole and reads as unremarkable. That is not a ranking failure and no projection can fix it: the information is in the stream, and a single baseline averages it away.

| Surface | n | hits | worst hit, one baseline | worst hit, `--per shape` | recall@25 |
|---|---:|---:|---:|---:|---:|
| `bench-sprawl` | 1998 | 7 | 823 [46-1590] | 10 [7-32] | 5 -> 7 |
| `bench-mixed` | 1998 | 6 | 23 [6-473] | 6 [6-6] | 6 -> 6 |
| `bench-token` | 1998 | 8 | 8 [8-11] | 9 [8-27] | 8 -> 8 |
| `bench-collide` | 1998 | 6 | 6 [6-6] | 6 [6-6] | 6 -> 6 |
| `bench-stable` | 1998 | 6 | 6 [6-6] | 6 [6-6] | 6 -> 6 |
| `ffufme-no404` | 1998 | 1 | 1 [1-1] | 1 [1-1] | 1 -> 1 |

Median over 8 seeds, offline, worst labelled hit's rank in brackets as [min-max]. Lower is better.

**It is worth the most where the noise is most varied.** On the two heterogeneous surfaces the hardest labelled response moves into the top ten and stays there across every seed, where a single baseline left it in the hundreds and swung by more than a factor of thirty between seeds. On the surfaces whose noise is effectively one population, `shape` finds one partition and the ranking is unchanged.

**It is not free.** `bench-token` is a single population whose size jitters, and there `shape` is slightly worse - a median of 9 against 8, and a worst seed of 27 against 11. Splitting a population that did not need splitting makes each piece a little thinner and its statistics a little noisier. The cost is small and the gain where it applies is large, but it is a trade rather than a free improvement, and that is why it is an option and not the default.

**The obvious alternative does not work, and it is worth saying why.** If the hard responses are the ones whose metadata collides with the baseline, the natural fix is stage two: re-fetch the candidates and re-rank them on body structure, which `fly taste` already measures. It cannot help here. Stage two only ever sees what stage one surfaced, and on `bench-sprawl` the two hard responses sat at ranks 618 and 1,237 under a single baseline - far outside any re-fetch window a rate limit permits. Re-fetching far enough to reach them is a second full scan. The fix had to be something that reorders the whole stream for free, which is what a per-shape baseline does.

**The hazard is partitions of one**, and it is not hypothetical: `--per dir` shipped exactly this bug, scoring 1.000 on partitions holding a single response that had nothing to be unlike. `shape` produces them constantly - an unusually large page is often the only thing in its size decade - so a partition below `min_observations` does not score at all, and its responses fall back to the host baseline. That fallback is why the numbers above are trustworthy and is asserted in `tests/test_partition.py`.

## What this does and does not claim

**Does:** on a stream covering several hosts, one baseline per host finds hits that a shared baseline scores at exactly zero, and it needs no per-host configuration to do it. That is the documented limitation of the incumbent, and it is the first place flypaper's design buys something a filter cannot have at any amount of tuning - not because the ranking is cleverer, but because a filter you can afford fifty of is a different kind of object.

**Does not:** anything about a single host, which is where every earlier measurement was taken and where flypaper matches `-ac` rather than beating it. An operator running one host at a time gains nothing from this.

**Watch out for thin partitions.** A host seen three times has no baseline and everything in it looks novel; found immediately on a real scan, where `--per dir` with a wordlist containing slashes produced partitions of one response each, all scoring 1.000. Partitions below `min_observations` are scored and learned from but never surfaced, and the directory key is taken from the scan base rather than by splitting the URL, so a word containing a slash cannot invent a directory.

---

*Generated by flypaper 0.1.0 at commit `fa16d8dd0009f28320590ec383bbf22ab567cdc3` **(dirty working tree - not reproducible)** | seed `0` on 2026-09-12T03:36:55+00:00.*
