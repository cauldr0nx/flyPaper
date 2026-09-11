# M6 - ergonomics

**Gate: a full pipe works end to end on the corpus and on one authorized live target. Met**,
with a caveat about the live run that is recorded rather than glossed - see section 3.

## 1. The pipe, on the corpus

```
$ ffuf -mc all -json -u http://127.0.0.1:8110/token/FUZZ -w corpus-words.txt \
    -noninteractive -t 20 -rate 400 2>/dev/null \
  | fly rank --baseline bench-token

flypaper: ranking by novelty  [channels v3-response, projection random,
                               showing top 0.5% most novel for this target]
          novel means structurally unusual. It is not a vulnerability.
0.307 part-new [Status: 200, Size: 1108,  Words:   95, Lines:  14] .git
0.258 part-new [Status: 200, Size: 11749, Words:  700, Lines:  90] admin
0.056 trace    [Status: 200, Size: 501,   Words:   60, Lines:   9] config.json
0.219 part-new [Status: 200, Size: 5534,  Words:  450, Lines:  61] metrics
0.014 trace    [Status: 200, Size: 1221,  Words:   88, Lines:  13] internal
0.199 trace    [Status: 200, Size: 3362,  Words:  240, Lines:  46] account
0.158 trace    [Status: 200, Size: 3231,  Words:  300, Lines:  30] debug
0.144 trace    [Status: 200, Size: 15498, Words: 1500, Lines: 160] backup.sql
flypaper: showed 8 of 1998 responses, 50 suppressed during warm-up; baseline 7% saturated.
flypaper: baseline 'bench-token' updated (1998 responses, 7% saturated).
```

**Eight lines out of 1,998 responses, and all eight are the labelled hits** - including the
three shaped to sit within a few percent of the noise baseline. No filter was configured and
no threshold was chosen; the cutoff is the top half-percent for this target, calibrated from
the run itself.

This is the surface the token jitter is on, where an exact `-fs` filter leaves 1,789 of
1,998 responses for the operator to read.

## 2. Baselines

A baseline is the Bloom filter's synaptic weights plus everything needed to say what they
mean. Scoring the same target twice does what it should:

```
first  scan:  backup  0.370   trace  0.318   ...  baseline 'stable' updated (1998 responses)
second scan:  backup  0.119   trace  0.115   ...  baseline 'stable' updated (3996 responses)
```

**Every mismatch is refused, not warned about.** A baseline restored under the wrong channel
set, projection, random seed, Kenyon cell count or time base would produce numbers that look
fine and mean nothing, which is worse than a crash. `tests/test_store.py` asserts each
refusal.

### Temporal decay needed a real fix, not a flag

Decay across scans was written before it worked. Within one run, "time" is how many responses
have gone past, and `last_seen` holds a record counter. Persist that, reload it in a new
process whose counter starts at zero, and the elapsed time is negative - clamped to zero, so
**the baseline silently never ages**. The store's own docstring claimed wall-clock
timestamps while the code stored record counts.

The fix is a `time_base` on the ranker, wall-clock whenever a baseline is used, recorded in
the store and refused on mismatch, since record counts and epoch seconds are not comparable.
Measured against a saved baseline with a one-day half-life:

| Time since the scan | Novelty of a response that was in it |
|---|---:|
| 0 | 0.000 |
| 1 day | 0.500 |
| 7 days | 0.992 |
| 30 days | 1.000 |

That is the property a hash set cannot have, and it now actually happens.

## 3. The live target, and what it cost

The gate calls for one authorized live target. `www.mcware.org`, authorized by the operator
on 2026-09-11, was used. The pipe ran end to end at the 10 requests/second live ceiling and
reported nothing novel, which was the correct answer: every unknown path returned a
constant-size rendered 404.

**Then a second capture was run with the full 2,000-word corpus list rather than the small
confirmation intended, and the target's edge protection blocked us.** After it, every
response - including the front page, which had returned 200 - was a 403 of about 32 KB. No
further traffic was sent.

Two things follow, both recorded in `bench/corpus/provenance.md` rather than tidied away:

- **A rate ceiling is not a volume ceiling.** 10/s is polite and still tripped protection
  after two hundred seconds of it. `bench/capture.py` now enforces `LIVE_MAX_REQUESTS = 250`
  on any surface marked live, truncating the wordlist rather than trusting the caller to
  pass a short one. The ceiling belongs in the code because a flag is too easy to get wrong.
- **The block is itself the habituation case from the brief.** A WAF that starts blocking
  mid-scan is a change in the baseline and exactly what should be surfaced. The two distinct
  403 body lengths, 32,225 and 32,215 bytes, look like a request id embedded in an otherwise
  fixed page - the rotating-token shape, on a real target.

The live capture is kept as a record of a blocked scan. It is not a useful corpus surface
and is not treated as one.

## 4. Interface notes

- `fly ingest` parses and normalises. It never scores or filters.
- `fly rank` takes a file or a pipe. A completed file is scored in two passes with exact
  leave-one-out; a pipe is scored in one, as a running scan must be.
- `fly taste` is stage two, and refuses to run without an explicit `--scope`.
- `fly baselines` lists what is stored and how stale it is.
- `--jsonl` on any of them emits one object per line for piping onward, each carrying the
  channel-set version that produced it. A score without its channel set is not interpretable.
- Colour is used only when stdout is a terminal.

## 5. What is still missing

- **Stage two does not re-rank on body features.** It reports them beside the stage-one
  score. Folding them in needs body-level channels with a channel-set version of their own.
- **No interactive console integration**, so no adaptive scan rate and no novelty-triggered
  recursion. Those are M7.
- **Baselines are per-name, not per-host.** Nothing stops an operator pointing one baseline
  at two unrelated targets and getting a meaningless blend; the tool will not notice.
- **The live end-to-end evidence is one blocked run.** The pipe demonstrably works against a
  real host over real TLS; it has not been shown to produce a useful ranking on one.
