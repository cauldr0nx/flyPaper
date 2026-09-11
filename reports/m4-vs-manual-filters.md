# M4 - novelty ranking vs. ffuf's own filters

Three approaches, same corpus, same labels: ffuf's `-ac` autocalibration, a
competent operator's hand-tuned filter, and flypaper's ranking. The ffuf runs are
real - the surviving results are whatever the installed ffuf actually printed.

**No flypaper threshold is tuned against this corpus.** The defaults come from the
papers and from M3's measurements; tuning them here would invalidate the corpus.

## 1. How to read the table

A filter produces an unordered set, so the operator reviews all of it. A ranking
produces an order, so the operator reviews from the top and stops when they choose.
To keep that fair, flypaper is given the **same review budget** as whichever filter
demanded less work; `reviewed to first hit` is the metric that needs no such
adjustment and is the one to read if you only read one.

## bench-token

*token rotation: identical page, variable-length CSRF token, defeating -fs* - 1,998 responses, 8 labelled hits.

Junk probe saw `status=200 size=980 words=80 lines=12`, so the hand-tuned filter is `-mc all -fs 980`.

| Approach | Reviewed | Found | Recall | Reviewed to first hit |
|---|---:|---:|---:|---:|
| ffuf `-ac` |      8 | 8/8 | 100% | 1 |
| hand-tuned |   1789 | 8/8 | 100% | 470 |
| **flypaper** |      8 | 8/8 | 100% | 1 |

flypaper precision@10 = 80%, precision@50 = 16%, recall@50 = 100%.

Subtle hits on this surface (shaped within a few percent of the noise baseline): `.git`, `config.json`, `internal`. flypaper found all of them within budget.

## bench-calib

*ffuf issue #387: the hit's word count collides with the autocalibrated filter* - 1,998 responses, 6 labelled hits.

Junk probe saw `status=404 size=1372 words=100 lines=20`, so the hand-tuned filter is `-mc all -fc 404`.

| Approach | Reviewed | Found | Recall | Reviewed to first hit |
|---|---:|---:|---:|---:|
| ffuf `-ac` |      6 | 6/6 | 100% | 1 |
| hand-tuned |      6 | 6/6 | 100% | 1 |
| **flypaper** |      6 | 6/6 | 100% | 1 |

flypaper precision@10 = 60%, precision@50 = 12%, recall@50 = 100%.

Subtle hits on this surface (shaped within a few percent of the noise baseline): `legacy`, `staging`. flypaper found all of them within budget.

## bench-collide

*ffuf issue #387, faithfully: noise answers 200 and every response shares the hits' word count, so autocalibration has only size/words/lines to work with* - 1,998 responses, 6 labelled hits.

Junk probe saw `status=200 size=1372 words=100 lines=20`, so the hand-tuned filter is `-mc all -fs 1372`.

| Approach | Reviewed | Found | Recall | Reviewed to first hit |
|---|---:|---:|---:|---:|
| ffuf `-ac` |      6 | 6/6 | 100% | 1 |
| hand-tuned |      6 | 6/6 | 100% | 1 |
| **flypaper** |      6 | 6/6 | 100% | 1 |

flypaper precision@10 = 60%, precision@50 = 12%, recall@50 = 100%.

Subtle hits on this surface (shaped within a few percent of the noise baseline): `legacy`, `staging`. flypaper found all of them within budget.

## bench-stable

*control: byte-identical 404s, nothing jitters* - 1,998 responses, 6 labelled hits.

Junk probe saw `status=404 size=573 words=60 lines=10`, so the hand-tuned filter is `-mc all -fc 404`.

| Approach | Reviewed | Found | Recall | Reviewed to first hit |
|---|---:|---:|---:|---:|
| ffuf `-ac` |      6 | 6/6 | 100% | 1 |
| hand-tuned |      6 | 6/6 | 100% | 1 |
| **flypaper** |      6 | 6/6 | 100% | 1 |

flypaper precision@10 = 60%, precision@50 = 12%, recall@50 = 100%.

Subtle hits on this surface (shaped within a few percent of the noise baseline): `.env`, `old`. flypaper found all of them within budget.

## ffufme-no404

*wildcard / soft-404 host: every unknown path answers 200* - 1,998 responses, 1 labelled hits.

Junk probe saw `status=200 size=669 words=126 lines=23`, so the hand-tuned filter is `-mc all -fs 669`.

| Approach | Reviewed | Found | Recall | Reviewed to first hit |
|---|---:|---:|---:|---:|
| ffuf `-ac` |    135 | 1/1 | 100% | 116 |
| hand-tuned |    135 | 1/1 | 100% | 116 |
| **flypaper** |    135 | 1/1 | 100% | 1 |

flypaper precision@10 = 10%, precision@50 = 2%, recall@50 = 100%.

## ffufme-basic

*ordinary surface: real 404s, one real hit* - 1,998 responses, 1 labelled hits.

Junk probe saw `status=404 size=669 words=126 lines=23`, so the hand-tuned filter is `-mc all -fc 404`.

| Approach | Reviewed | Found | Recall | Reviewed to first hit |
|---|---:|---:|---:|---:|
| ffuf `-ac` |    135 | 1/1 | 100% | 72 |
| hand-tuned |      1 | 1/1 | 100% | 1 |
| **flypaper** |      1 | 1/1 | 100% | 1 |

flypaper precision@10 = 10%, precision@50 = 2%, recall@50 = 100%.

## Interpretation

**Recall is a tie. Ordering is not.** At equal review budget flypaper found every
labelled hit on every surface, and so did `-ac`, and so did the hand-tuned filter.
On recall alone there is nothing to choose between the three, and a report that
stopped there would be hiding the only interesting column.

**The difference is where the first real result sits.** flypaper's first genuine hit
is at rank 1 on every surface. `-ac` needs up to 116 results
reviewed before the operator sees one, and the hand-tuned filter up to
470. A filter returns an unordered set, so even a small set is read in
wordlist order and the real thing can be anywhere in it. That is the whole of
flypaper's advantage here, and it is worth exactly as much as ordering is worth to
the operator - which on a six-item set is very little, and on a 135-item one is
real.

**Neither filter is bad everywhere, but each is bad somewhere.** The hand-tuned
`-fs` collapses on the token surface: the CSRF token moves Content-Length by a few
bytes, so an exact size filter matches almost nothing and 1,789 of 1,998 responses
survive it. `-ac` collapses on ffufme's ordinary surface, where it shows 135 results
that a plain `-fc 404` reduces to one. flypaper is the only one of the three that is
not badly wrong on any surface here - not because it is cleverer on any single one,
but because it needs no per-target decision to be made correctly in advance.

### The ffuf issue #387 case did not reproduce, and that is a result about `-ac`

The brief names ffuf issue #387 - `-ac` derives size, word and line filters and
applies them with OR logic, so a valid result matching any one is hidden - as the
cleanest demonstration of why independent thresholds fail. Two surfaces were built
to trigger it. `bench-calib` pins every response on the surface to the same word
count, hits included. `bench-collide` does the same and additionally answers 200 to
unknown paths, so the status code hands autocalibration no discriminator at all.

**Against ffuf 2.1.0-dev, `-ac` found every hit on both surfaces, every run.** It
did not derive the word filter the collision was built to defeat. The brief
notes that autocalibration has been revamped over several releases; on this evidence
the revamp addressed this, and a claim that flypaper beats `-ac` by surviving #387
would be false on the current build.

An earlier draft of the M2 report asserted the opposite, on the strength of a
single-word probe whose output was misread. That is corrected there and recorded
here, because a benchmark that quietly drops its inconvenient case is not a
benchmark.

### What may and may not be claimed

May: *at equal review budget, novelty ranking matched ffuf's autocalibration and a
competent hand-tuned filter on recall across six surfaces, put a genuine result
first on all six, and was the only one of the three not to fail badly on at least
one of them - with no per-target configuration.*

May not: that it finds things the filters miss. On this corpus it does not. Every
labelled hit was reachable by both incumbents at the budgets shown.

### What this corpus cannot settle

- **Six surfaces, 21 labelled hits, three of them served by a target written to
  contain the scenarios being tested.** A synthetic corpus can show a property holds;
  it cannot show how often the property matters in the field.
- **The hits are mostly obvious.** They differ from their baseline by large factors
  in size. The subtle ones - shaped within a few percent of the noise - are the ones
  worth watching, and they are a minority here.
- **Every surface has exactly one noise cluster.** Real targets mix several, and a
  filter tuned for one is wrong for the others. That is the case where ranking should
  pull ahead, and this corpus does not contain it.
- **Nothing here tests temporal decay**, which needs two scans separated in time.

---

*Generated by flypaper 0.1.0 at commit `f7ceb78092bae13d20f261b631e5798c4a5d99da` **(dirty working tree - not reproducible)** on 2026-09-11T19:44:34+00:00.*
