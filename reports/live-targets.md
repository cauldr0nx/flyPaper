# Ranking on real targets

Every report before this one says the same thing in its limitations: the corpus is
synthetic, three of its six surfaces are served by a target written to contain the scenarios
being tested, and that can show a property holds but not how often it matters. This is the
first measurement on traffic nobody arranged.

**Hosts are not named here, and no capture is committed.** Three in-scope hosts from one
public bug bounty program were probed; the program publishes its scope, the assets used were
the ones it marks eligible for submission, and the scope file was generated from that table
by `fly scope` rather than typed. Nothing found is a vulnerability and nothing is presented
as one - what follows is about the ranker's behaviour, which is legible without naming
anyone's infrastructure. Captures stay in `bench/corpus/live/`, which is gitignored.

## 1. How it was run

| | |
|---|---|
| Hosts | 3, all exact identifiers in the program's scope, `eligible_for_submission = true` |
| Requests | 146 per host, 438 total |
| Rate | 10/s, 4 threads |
| Wordlist | meaningful path names only - no generated junk, which would be wasted requests against someone's production |
| Guard | abort on a 50% jump in refusal responses against the run's own opening baseline |

The guard did not trip on any host, and all three runs completed. That is worth stating
because the last live run this project did - at the same 10/s - tripped the target's edge
protection and got 403 to everything including its front page, with nothing watching the
responses to notice. `bench/live_probe.py` now watches.

## 2. What the ranker did

Every host answered the same way structurally: a large wall of 404s, a handful of 302s and
a handful of 403s. Response-size distributions per host:

| Host | 404s | Other | Baseline 404 size |
|---|---:|---|---:|
| A | 143 | 3 &times; 403 | 21 b |
| B | 139 | 4 &times; 302, 3 &times; 403 | 33,254 b |
| C | 141 | 3 &times; 403, 2 &times; 302 | 13,328 b |

Host A's 404 is 21 bytes and host B's is 33 kB, which is the point of not having a fixed
size filter.

**Live, single-pass, the top of the ranking is cold start.** On all three hosts the
first-ranked response was the first word in the wordlist, scoring 1.000 because nothing was
familiar yet. On a 146-request run the cold start is a third of the scan, so it dominates.

**Offline, two-pass, it is all signal.** Re-ranking the same captures with the leave-one-out
pass `fly rank` uses on a completed file:

| Rank | Host A | Host B | Host C |
|---|---|---|---|
| 1 | 403, 118 b | 302, 0 b | 302, 0 b |
| 2 | 403, 118 b | 302, 0 b | 302, 0 b |
| 3 | 403, 118 b | 302, 0 b | 403, 118 b |
| 4 | 404, 21 b | 302, 0 b | 403, 118 b |
| 5 | 404, 21 b | 403, 118 b | 403, 118 b |
| 6 | 404, 21 b | 403, 118 b | **404, 9,413 b** |

Every top-ranked item is a response that differs structurally from its host's wall, and the
cold-start artifacts are gone entirely. Nothing was configured: no size filter, no status
filter, no per-host tuning, and the three hosts have 404 pages that differ by three orders
of magnitude in size.

The last cell is the one worth looking at. On host C the sixth-ranked item is a **404 of the
wrong size** - 9,413 bytes against a 13,328-byte wall. A status filter cannot see it, and a
size filter tuned to the wall would have hidden it. It is almost certainly nothing; the
point is that joint similarity across fields is what puts it in front of a human at all.

## 3. A second target, chosen because it should break a size filter

The first three hosts were conventional front ends. The case section 4 called untested was a
target whose baseline is not a constant, so a fourth host was picked for exactly that: a
public API, in a different program's published scope, which answers unknown paths with a
JSON error **that quotes the path back**. Its 404 body is therefore `192 + len(path)` bytes
and no two responses of different path length are the same size.

That is the rotating-token scenario the synthetic `bench-token` surface models, except real,
and arrived at by classifying candidate hosts with three requests each rather than by
building it.

146 requests. 145 were 404s of 15 distinct sizes; one was a 200. What each approach leaves
for a human to read:

| Approach | Left to review | Comment |
|---|---:|---|
| `-fs 196` (the modal size) | **122 of 146** | the instinctive filter, and it collapses |
| `-fc 404` (status) | 1 of 146 | misses three of the four real ones |
| `-fw 29` (modal words) | 4 of 146 | works - if you thought to filter words |
| `-fl 7` (modal lines) | 4 of 146 | works - if you thought to filter lines |
| **ffuf `-ac`**, measured | **4 of 146** | correct, and it chose the fields itself |
| **flypaper**, top 4 | **4 of 146** | the same four, at ranks 1-4 |

The four are the single 200, and three 404s of a different shape - 146 b / 5 words / 8 lines
against a baseline of ~200 b / 29 words / 7 lines. A different error, not a different page.

**`-ac` and flypaper agree exactly here, and that is the honest headline.** Autocalibration
is not defeated by a jittering size, because it does not only filter on size: it derived
stable word and line filters and got the right answer. What flypaper adds is not a better
answer but the absence of a decision - no field had to be chosen, and the one field an
operator would have reached for first would have left them 122 responses to read.

That is the same conclusion M4 reached on the synthetic corpus, now measured somewhere
nobody arranged it: **flypaper matches the incumbent rather than beating it, and removes the
per-target configuration the incumbent needs to be pointed at.**

## 4. What this does and does not establish

**Does:** the encoder collapses a real 404 wall into a familiar cluster, across three hosts
with very different response shapes, with no configuration. The ranker then puts the
structurally distinct responses on top. That is the claim M2 and M4 make, measured for the
first time on traffic that was not arranged to make it true.

**Does not:** anything about finding vulnerabilities. Nothing here is one. The top-ranked
responses are a server correctly refusing to serve dotfiles and a few redirects - which is
exactly what "ranks, does not detect" means in practice. A human reads the top of the list
and moves on in about ten seconds, which is the whole product.

**And it sharpens a known weakness.** The live single-pass ordering was useless on a
146-request run. The cold start is a fixed cost of roughly the first fifty responses, so it
is negligible on a 10,000-word scan and overwhelming on a short one. `bench/live_probe.py`
now reports the offline ranking as well as the live one, and the dashboard already
suppresses the first fifty. Anyone reading a live ranking on a short scan is reading noise
at the top, and the tool should say so more loudly than it does.

## 5. Limitations

- **Three hosts, one program, 438 requests.** This is a demonstration, not a survey.
- **No labels.** There is no ground truth on a real target, so there is no precision figure
  here and there cannot be one. Everything in section 2 is a description of ordering, not a
  score against known answers.
- **Short wordlist, meaningful words only.** A real content-discovery run uses tens of
  thousands of words, most of them junk, and the familiar cluster would be far denser. That
  should help the ranker, and it is untested.
- **Four hosts across two programs.** Three conventional front ends and one API. A
  single-page app that returns 200 and the same HTML shell to every path is still untested,
  and is the case most likely to defeat the encoder, since status, size, words and lines
  would all be constant and only the body differs - which stage one never sees. That is
  what stage two exists for, and it has not been run against a real target at all.
- **`-ac` was not beaten on either shape of target.** On the synthetic corpus and on both
  real ones it produced the same answers. Any claim that flypaper finds things ffuf's
  autocalibration misses is unsupported by everything measured so far.
