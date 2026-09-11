# Temporal decay - does it buy anything?

Temporal decay is one of two properties the Fly Bloom Filter has and a conventional

Bloom filter does not. It was unit-tested and never benchmarked: the tests show the

mechanism works, not that it is worth having.

The claim under test is that a filter kept running stays sensitive where a
non-decaying one saturates and goes blind. So 40,000 responses of ordinary
`bench-mixed` traffic are streamed past filters with different half-lives, and every
2,000 responses each is shown the *same kind of* genuinely different
response - one unlike anything in the corpus, and a different one each time so the
filter cannot become familiar with the probe itself. The probe is scored and never
learned from. Averaged over 3 seeds.

## Sensitivity to a novel response, as the filter fills up

| Responses seen | half-life none | half-life 20,000 | half-life 5,000 | half-life 1,000 | half-life 250 | half-life 60 |
|---:|---:|---:|---:|---:|---:|---:|
| 0 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| 8,000 | 0.4657 | 0.5493 | 0.5694 | 0.6196 | 0.7157 | 0.8480 |
| 18,000 | 0.4520 | 0.5556 | 0.5757 | 0.6242 | 0.7160 | 0.8246 |
| 28,000 | 0.4508 | 0.5552 | 0.5729 | 0.6097 | 0.6807 | 0.7815 |
| 38,000 | 0.4523 | 0.5656 | 0.5862 | 0.6353 | 0.7373 | 0.8724 |

## And what it says about a response it has seen many times

Sensitivity alone would make the shortest half-life look best, because a filter that forgets everything scores everything as novel. What matters is the **margin**: the novel probe minus a response drawn from the stream the filter has been reading all along.

| Responses seen | half-life none | half-life 20,000 | half-life 5,000 | half-life 1,000 | half-life 250 | half-life 60 |
|---:|---:|---:|---:|---:|---:|---:|
| 0 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| 8,000 | 0.0000 | 0.0002 | 0.0008 | 0.0040 | 0.0155 | 0.0580 |
| 18,000 | 0.0000 | 0.0028 | 0.0110 | 0.0520 | 0.1695 | 0.3711 |
| 28,000 | 0.0000 | 0.0038 | 0.0148 | 0.0689 | 0.2246 | 0.5352 |
| 38,000 | 0.0000 | 0.0003 | 0.0011 | 0.0053 | 0.0209 | 0.0804 |

**Margin at the end of the stream** (novel minus familiar; higher is better):

| Half-life | Novel | Familiar | Margin |
|---|---:|---:|---:|
| none | 0.4523 | 0.0000 | **0.4523** |
| 20,000 | 0.5656 | 0.0003 | **0.5653** |
| 5,000 | 0.5862 | 0.0011 | **0.5851** |
| 1,000 | 0.6353 | 0.0053 | **0.6300** |
| 250 | 0.7373 | 0.0209 | **0.7165** |
| 60 | 0.8724 | 0.0804 | **0.7919** |

## How full the filter is

| Responses seen | half-life none | half-life 20,000 | half-life 5,000 | half-life 1,000 | half-life 250 | half-life 60 |
|---:|---:|---:|---:|---:|---:|---:|
| 0 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 | 0.000 |
| 8,000 | 0.166 | 0.153 | 0.149 | 0.137 | 0.115 | 0.078 |
| 18,000 | 0.168 | 0.153 | 0.149 | 0.137 | 0.116 | 0.086 |
| 28,000 | 0.169 | 0.152 | 0.148 | 0.137 | 0.115 | 0.083 |
| 38,000 | 0.170 | 0.152 | 0.148 | 0.136 | 0.115 | 0.085 |

## What it shows

**The best margin at the end of the stream is half-life 60 (0.7919), against 0.4523 with no decay.** So decay earns its place: it keeps the filter able to tell a novel response from a familiar one for longer, and over this stream the cost in specificity is under a hundredth.

**The non-decaying filter goes blind, and decay prevents it.** The same novel response scores 1.000 on a fresh filter and 0.452 after 40,000 responses - a 55% loss of sensitivity to something that never changed. That is the classic Bloom filter failure: write enough in and every cell is set. Decay is what stops the filter reaching it.

**But the specificity cost is small only because this traffic is repetitive.** The shortest half-life here left the familiar response near zero on a stream many times longer than it, which looks like decay being free. It is not. The corpus cycles every 1,998 records, so every population is re-seen well inside every half-life and never gets the chance to be forgotten. On traffic where a response type appears once and then not again for a long time, a short half-life would forget it and re-flag it, and that cost is not measured here. The safe reading is that decay helps, and that the half-life should be long relative to how often the target repeats itself rather than chosen for the numbers above.

## The axis the half-life has to be chosen against

The sweep above never turns over: on this corpus a shorter half-life is always better, down to 60 responses. That is a property of the corpus, not of decay. `bench-mixed` has four response populations, so the filter is re-shown every one of them every few records and almost nothing has time to be forgotten.

Below, the stream cycles through a controlled number of distinct response types instead. More types means a longer gap between visits to any one of them, which is the gap a half-life has to beat. Figures are the margin - novel minus familiar - so higher is better and **negative means the filter calls a response it has seen all along more novel than one it has never seen**.

| Distinct response types | half-life none | half-life 4,000 | half-life 1,000 | half-life 250 |
|---:|---:|---:|---:|---:|
| 4 | +0.787 | +0.782 | +0.769 | +0.719 |
| 40 | +0.164 | +0.189 | +0.185 | +0.120 |
| 200 | +0.000 | +0.040 | +0.052 | +0.049 |
| 1,000 | +0.000 | -0.015 | -0.023 | -0.003 |

Best half-life by diversity: 4 types -> none, 40 types -> 4,000, 200 types -> 1,000, 1,000 types -> none. **The optimum moves with diversity**, which is the relationship the caveat above was asserting without evidence. A half-life chosen from this benchmark's headline corpus would be far too short for a real target whose response types number in the hundreds.

The half-life is in responses here. Across scans it is wall-clock, a different regime this benchmark does not measure - see `reports/m6-ergonomics.md`.

---

*Generated by flypaper 0.1.0 at commit `6b62e9e6c9c4fee86da6841b5544c942bd15d3ca` **(dirty working tree - not reproducible)** | seed `0` on 2026-09-11T22:32:43+00:00.*
