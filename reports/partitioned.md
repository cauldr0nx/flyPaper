# One baseline per host

ffuf derives one filter and applies it to the whole run. From its own issue tracker, on scanning several targets at once: *"would be impossible to put correct flag for each host"* ([ffuf#450](https://github.com/ffuf/ffuf/issues/450)). Every automation guide for it says the same thing in different words - `-ac` works when the custom 404 is consistent, and a list of fifty hosts is fifty different ideas of what a 404 looks like.

A Fly Bloom Filter is 2,045 floats. Keeping one per host costs 16 kB, so flypaper keeps one per host - and one per directory if the scan recursed, which is the partition feroxbuster gets by detecting wildcards per directory and ffuf does not have at all. The projection is shared across partitions, so a score means the same thing everywhere and only the learned baseline differs.

## The worst case, constructed

Two hosts, 602 responses. One answers unknown paths with ~120 bytes, the other with ~48,000. Each host has one real page, and it is shaped **exactly like the other host's noise** - same status code, same size. Size is the only thing that separates it, and a filter that has seen both hosts has been taught that both sizes are ordinary.

| | rank of first hit | rank of second | recall@50 |
|---|---:|---:|---:|
| one baseline | 155 | 251 | 0/2 |
| **one per host** | 1 | 2 | 2/2 |

Sharing a baseline does not degrade the result here, it erases it: both hits score **0.0000** and land at ranks 155 and 251. They are not ranked low, they are indistinguishable from noise, because from the shared filter's point of view they genuinely are - it has seen a thousand responses of each size.

## A realistic multi-host sweep

The captured surfaces relabelled as 6 hosts and interleaved - 11,988 responses, 34 labelled hits, arriving down one pipe the way a real sweep does. The hosts differ the way real ones do rather than adversarially.

| | recall@10 | recall@25 | recall@50 | recall@100 |
|---|---:|---:|---:|---:|
| one baseline | 5/34 | 16/34 | 19/34 | 19/34 |
| **one per host** | 10/34 | 25/34 | 29/34 | 31/34 |

Partitioning reaches every hit by rank 3123. The shared baseline plateaus at 29/34 and does not recover by rank 100 either - the hits it has lost are lost, not merely deferred.

## Crossed with the projection

Partitioning and the measured fan-in spread (`reports/claw-degrees.md`) looked like two attacks on the same problem: partitioning stops a baseline from having to be several populations at once, and the fan-in spread appeared to make a single baseline better at being several at once. Crossed here, 8 seeds per cell, median reported.

The fan-in half of that has since been retracted - it did not reproduce on a second heterogeneous surface - and this table is one of the measurements that says so, independently of the one that retracted it.

| Experiment | projection | worst, `--per none` | worst, `--per host` | partitioning gains | recall@50, `--per host` |
|---|---|---:|---:|---:|---:|
| pathological | `random` | 577 | 2 | 288.5x | 2/2 |
| pathological | `degree-sampled` | 568 | 2 | 284.0x | 2/2 |
| corpus | `random` | 4831 | 926 | 5.2x | 29/34 |
| corpus | `degree-sampled` | 8985 | 1753 | 5.1x | 31/34 |

Worst labelled hit's rank; lower is better.

**Partitioning is worth the same multiple whichever projection it is given** - roughly 290x on the constructed worst case and 5x on the corpus sweep, in both rows. That is the useful reading: the one measured win in this project does not depend on any of the connectome work, and would survive all of it being removed.

**The projection on its own makes the corpus sweep worse** unpartitioned. A multi-host stream is heterogeneous in a different way from a single host serving several response shapes - the populations belong to different hosts rather than different routes - and nothing about the fan-in spread addresses that.

**The two metrics disagree once partitioned, and both are reported because of it.** On the corpus sweep the fan-in spread finds slightly more hits inside the first fifty while placing its hardest hit further down. Neither difference is supported by the surface-level tests in `reports/claw-degrees.md`, and the honest reading of a split like this at 8 seeds is that it is noise. It is tabulated rather than summarised so that it cannot be quoted one way only.

### Read this way

- **pathological**: unpartitioned random 577; the projection alone 568; partitioning alone 2; both 2.
- **corpus**: unpartitioned random 4831; the projection alone 8985; partitioning alone 926; both 1753.

## What this does and does not claim

**Does:** on a stream covering several hosts, one baseline per host finds hits that a shared baseline scores at exactly zero, and it needs no per-host configuration to do it. That is the documented limitation of the incumbent, and it is the first place flypaper's design buys something a filter cannot have at any amount of tuning - not because the ranking is cleverer, but because a filter you can afford fifty of is a different kind of object.

**Does not:** anything about a single host, which is where every earlier measurement was taken and where flypaper matches `-ac` rather than beating it. An operator running one host at a time gains nothing from this.

**Watch out for thin partitions.** A host seen three times has no baseline and everything in it looks novel; found immediately on a real scan, where `--per dir` with a wordlist containing slashes produced partitions of one response each, all scoring 1.000. Partitions below `min_observations` are scored and learned from but never surfaced, and the directory key is taken from the scan base rather than by splitting the URL, so a word containing a slash cannot invent a directory.

---

*Generated by flypaper 0.1.0 at commit `760a5021913bf164bb6242e07a259a7404d7c4a8` **(dirty working tree - not reproducible)** | seed `0` on 2026-09-12T02:40:08+00:00.*
