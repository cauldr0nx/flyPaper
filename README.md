# flypaper

A novelty-ranking filter for web fuzzing output.

`fly` reads ffuf results and ranks them by how *structurally unusual* they are, replacing
hand-tuned `-fs` / `-fc` / `-fw` / `-fl` filters with a similarity-graded novelty score.
The ranking is computed by the fruit fly's olfactory circuit — a published
locality-sensitive hash and Bloom filter — wired from the measured MaleCNS connectome
rather than the random projection the original papers assumed.

**It ranks; it does not detect.** A novel response is a statistical outlier, not a
vulnerability. Output says *novel*, and nothing else.

```
ffuf -mc all -json -u https://target/FUZZ -w list.txt | fly ingest
```

`-mc all` is deliberate: you want every response, including the 404 sea, because the noise
*is* the baseline.

## Status

The pipeline is built end to end. Each milestone is a binary gate; a failed gate is written
up in `reports/` and stopped on, never loosened by moving the threshold.

| Milestone | What it is | Outcome |
|---|---|---|
| M0 | Scaffold, licenses recorded | passed |
| M1 | Ingest spike | passed — [reports/m1-ingest.md](reports/m1-ingest.md) |
| M2 | Encoder and replay corpus | passed — [reports/m2-encoder.md](reports/m2-encoder.md) |
| M3 | Connectome FlyHash vs. random projection | **split** — [reports/m3-flyhash-benchmark.md](reports/m3-flyhash-benchmark.md) |
| M3b | The same comparison, on this workload instead of MNIST | **split** — [reports/connectome-on-workload.md](reports/connectome-on-workload.md), [reports/claw-degrees.md](reports/claw-degrees.md) |
| M4 | Ranking vs. ffuf's own filters | passed — [reports/m4-vs-manual-filters.md](reports/m4-vs-manual-filters.md) |
| M5 | Stage two, scope-gated and rate-limited | passed — [reports/m5-stage-two.md](reports/m5-stage-two.md) |
| M6 | Ergonomics | passed — [reports/m6-ergonomics.md](reports/m6-ergonomics.md) |
| M7 | Adaptive foraging, delayed reward | not started |

Three benchmarks sit outside the milestone sequence:
[reports/partitioned.md](reports/partitioned.md) measures per-host baselines,
[reports/decay.md](reports/decay.md) measures whether temporal decay is worth having, and
[reports/live-targets.md](reports/live-targets.md) runs the whole thing against real hosts.

### What the measurements actually said

**The connectome does not beat random projection.** Wired from the measured MaleCNS
connectivity, FlyHash lands inside the spread of random draws for novelty detection —
beating 9 to 11 of 15 seeds, which is a coin flip — and is clearly worse at nearest-neighbour
retrieval. That is a real result about the published model and a reassuring one: Dasgupta,
Stevens & Navlakha assumed a random projection because that is what the biology looked like
statistically, and the measured wiring says the simplification costs nothing on this task.
flypaper ships on random projection because the two are indistinguishable here and random
needs no 508 MB download.

**But that was measured on MNIST, and this is not MNIST.** M3 used the datasets the FlyHash
papers used, which is right for comparability and wrong for the workload. Re-run on
flypaper's own corpus
([reports/connectome-on-workload.md](reports/connectome-on-workload.md)), the connectome
does beat a plain random projection — and still does not beat its own degree-preserving
null, or a version of itself with the input channels shuffled. Two controls agreeing means
the advantage is not in *which* glomerulus reaches which Kenyon cell. It is in how unevenly
the claws are spread: the published model gives every cell the same fan-in, the measured
circuit gives them 1 to 29.

That part is 13 numbers rather than 508 MB, so it ships as `--projection degree-sampled`
([reports/claw-degrees.md](reports/claw-degrees.md)). On a surface whose noise is several
response populations rather than one, it cuts the median rank of the hardest hit from 29 to
9. On another it gives up a single position of median and removes the tail instead — the
worst seed goes from rank 482 to 18, which is the difference between a hit nobody scrolls to
and one on the first screen. On surfaces where every projection already puts every hit at
the top, it does nothing. It is not a general improvement and it is not the default. Running that comparison at all first required admitting the connectome had never
been usable with the real encoder — it needs exactly 55 input channels and the default
encoder produced 39, so every earlier measurement of it was made on reduced image data.

**ffuf's `-ac` is a strong incumbent.** The ffuf issue #387 scenario did not reproduce
against ffuf 2.1.0-dev on either surface built to trigger it. At equal review budget, novelty
ranking matched `-ac` and a competent hand-tuned filter on recall across six surfaces. What
it did better was ordering: a genuine result at rank 1 on all six, where the filters needed
up to 470 results reviewed first. And it was the only one of the three not to fail badly on
at least one surface, with no per-target configuration.

**The encoder is where the skill lives, and the obvious channels were the wrong ones.** An
input-word character profile — one of the candidate encodings — turned out to supply 98% of
the within-cluster variance and destroy exactly the collapse the tool depends on, because the
fuzzed word differs on every request by construction.

**One baseline per host is the thing a filter cannot have.** ffuf derives one filter and
applies it to the whole run; from its own tracker, on scanning several targets at once,
*"would be impossible to put correct flag for each host"*. A Fly Bloom Filter is 2,045
floats, so flypaper keeps one per host — and one per directory if the scan recursed, which
is what feroxbuster gets from per-directory wildcard detection and ffuf has no equivalent
for. On a 9,990-response sweep across five hosts, a shared baseline finds 20 of 27 hits and
buries the worst at rank 6,762; one baseline per host finds all 27 by rank 33
([reports/partitioned.md](reports/partitioned.md)). On a constructed worst case — each
host's real page shaped exactly like the other host's noise — the shared baseline scores
both hits at 0.0000, which is not a low rank but no signal at all.

**It behaves the same on real targets.** Seven hosts across three public bug bounty
programs, 1,022 requests ([reports/live-targets.md](reports/live-targets.md)). The encoder
collapsed 404 walls of 21 b, 13 kB and 33 kB with no configuration and put the structurally
distinct responses on top. On an API whose 404 body quotes the path back — so no two
responses are the same size — the size filter an operator would reach for first leaves 122
of 146 responses to read, while `-ac` and flypaper both leave the same 4. `-ac` was not
beaten on any target, synthetic or real; what flypaper removes is having to pick the right
field in advance.

## Install

```bash
uv tool install git+https://github.com/cauldr0nx/flyPaper     # or: pipx install
fly --version
```

Pure Python, no build step, and nothing it needs at runtime beyond numpy and scipy. The
MaleCNS tables are only needed from M3 onward and the tool runs without them.

To work on it:

```bash
uv venv && uv pip install -e ".[dev]"
make ci          # ruff + pytest, offline, no dataset required
```

There is deliberately no hosted CI workflow yet; `make ci` is the gate.

## Usage

```bash
# live, straight off the pipe
ffuf -mc all -json -u http://host/FUZZ -w list.txt | fly rank

# a sweep across many hosts, one baseline each - the case ffuf cannot handle
ffuf -mc all -json -u https://HOST/FUZZ -w list.txt:FUZZ -w hosts.txt:HOST | fly rank --per host

# a recursive scan, one baseline per directory
ffuf -mc all -json -recursion -u http://host/FUZZ -w list.txt | fly rank --per dir

# a completed results file, scored in two passes
fly rank results.json --top 20

# against a saved baseline, so a second scan of the same target is not novel again
fly rank results.json --baseline acme --decay-halflife 604800

# a weekly sweep: one saved baseline per host, each ageing on its own
fly rank results.json --per host --baseline acme --decay-halflife 604800

# stage two: re-fetch the most novel candidates, scope-gated and slow
fly taste results.json --scope scope.txt --top 10 --rate 1

# parse only, no scoring
fly ingest --file results.json

# turn a program's published scope table into a scope file, without widening it
fly scope program-scope.csv --out scope.txt

# monitoring: what is new on this target since last time
fly watch results.json --baseline acme --per host

fly baselines            # what is stored, and how stale
```

`fly rank --baseline` learns as it goes, so a second scan of the same target reports that
nothing is surprising — true, and useless. `fly watch` holds the baseline still and answers
*what is here now that was not here then*, leaving it untouched unless you pass `--update`.
It reports **structurally** new, not newly-seen: a new URL serving a page much like one the
target already had will not be flagged, which is right for monitoring at scale and wrong if
what you wanted was a URL diff.

`-mc all` is deliberate: you want every response, including the 404 sea, because the noise
*is* the baseline. Filtering upstream destroys the thing the filter needs — and it fails
quietly, because the ranking still looks fine. `fly rank` checks the stream and tells you if
it looks pre-filtered, too short to have learned anything, or if so much of it is surfacing
that the baseline cannot be describing it.

```bash
# follow a scan that is still running
ffuf -mc all -of json -o out.json -u http://host/FUZZ -w list.txt &
fly rank out.json --follow
```

There is no novelty threshold to choose. A fixed one cannot work — baseline noise sits at
0.000 on every surface measured, but the weakest genuine hit ranged from 0.001 to 0.318
across them — so the cutoff is a review budget instead: `--percentile 99.5` shows the most
novel half-percent *for this target*, calibrated from the run itself.

On a 1,998-response scan of a target whose every response carries a rotating CSRF token,
that prints eight lines, and all eight are the planted hits.

### The dashboard

A page showing what the circuit is doing: the run's log on a monitor, the mushroom body's
**measured** synapses lighting up as tags fire, and the novelty traces with the cutoff the
run actually used.

```bash
python -m flypaper.web.export                     # circuit geometry (needs the MaleCNS tables)
python -m flypaper.web.server --tailscale         # then open the printed address
```

It records a run from `bench/corpus/` on first start, so a capture has to exist. Standard
library only, with a vendored three.js, so it works with no internet connection.

`--tailscale` binds the tailnet interface, so the page is reachable from your other devices
at `http://<machine>.<tailnet>.ts.net:8770/`. It is **never** `tailscale funnel`: nothing is
published to the internet, and a test asserts that. For HTTPS on a name with no port number,
`tailscale serve` needs rights it does not have by default — run `sudo tailscale set
--operator=$USER` once, then `--tailscale` will set it up.

Two panels, deliberately separate, because only one of them is data. The mushroom body is
measured: every point is a location in MaleCNS EM space, and the α′3 synapses it lights are
the Bloom filter's own weights. The workstation is staging: the fly is a real anatomical
model but nothing about its pose is computed, and it is not spatially registered to the
connectome. The page says so on the page.

### The connectome data

The MaleCNS tables are needed from M3 onward, not before.

```bash
python data/fetch.py --tier core      # ~522 MB, verified against pinned SHA256
```

If you already have the tables from another checkout, point at them instead of downloading
a second copy — `data/fetch.py --verify-only` then proves the bytes match:

```bash
export FLYPAPER_RAW_DIR=/path/to/malecns/raw
python data/fetch.py --verify-only
```

## Known limitations

**Baseline poisoning.** An attacker who floods benign-looking variants first makes them
familiar, letting a real payload ride in below threshold. Temporal decay partly mitigates
this — waiting makes things *more* novel, not less — but the real evasion is staying under
the encoder's resolution, and nothing in the design prevents that. If you are ranking
output from a target that knows it is being ranked, this is the hole.

**Encoder sensitivity.** Results depend entirely on the channel set. The neural code is
comparatively simple; the encoder is where all the skill lives, and a poor one degrades the
whole system to noise. Every baseline records the channel-set version that built it and
refuses to load under a different one, and every ranked record names that version, because
comparing scores across channel sets is meaningless.

**Ranking, not detection.** Restated because it matters: a high novelty score means *this
response is unlike the others*, which is not the same as *this response is interesting*,
and is much further still from *this response is a vulnerability*. The tool reorders a
review queue. A human does the reviewing.

## Authorization

`fly` never initiates a request in stage one — it reads results the operator already
generated. Stage-two re-fetch (M5) will re-request only URLs already present in the input
stream, under a mandatory explicit `--scope` file, rate-limited: no crawling, no guessing,
no following a redirect to a new host, no implicit same-domain inference, no wildcard
default. Response bodies are not stored by default.

## Licenses

This repository is MIT. Several things it depends on, cites or runs against are not, and
the MaleCNS data carries its own terms regardless of our code license. The full audit is in
[reports/licenses.md](reports/licenses.md); the matrix below is enforced by
`tests/test_licenses.py`.

<!-- LICENSE-MATRIX-START -->

| Component | Role | License | Handling |
|---|---|---|---|
| flypaper (this repository) | the tool | MIT | — |
| [ffuf](https://github.com/ffuf/ffuf) (Joona Hoikkala and contributors) | produces the input we read | MIT | Not vendored, not forked. Invoked by the operator; cited. |
| [ffufme](https://github.com/BuildHackSecure/ffufme) (Adam Langley) | local practice target for the replay corpus | No license file published | Built and run locally as a target only. Source never vendored, never redistributed. Captured corpora hold metadata, never response bodies, and are never committed. |
| [ffufPostprocessing](https://github.com/dsecuredcom/ffufPostprocessing) (dsecuredcom) | closest prior art — strips dynamic content before filtering | No license file published | Cited as prior art. No code reuse. |
| [ffufw](https://github.com/puzzlepeaches/ffufw) (puzzlepeaches) | wrapper; source of feature ideas | No license file published | Cited as prior art. No code reuse. |
| [FFUF-Workflow-Tool](https://github.com/nullenc0de/FFUF-Workflow-Tool) (nullenc0de) | evidence for the pipe-not-fork architecture | No license file published | Cited as prior art. No code reuse. |
| MaleCNS v1.0 connectome data | the measured PN→KC wiring | CC BY 4.0 | Governs the data regardless of this repository's MIT license. Verbatim terms and attribution in `data/manifest.json`. The tables themselves are never committed. One **derived** summary is: `flypaper/brain/claw-degrees.json`, a 13-number histogram of how many glomeruli each Kenyon cell listens to, which CC BY permits with attribution and which is what `--projection degree-sampled` needs instead of the 508 MB download. |
| [fly_connectome_data_tutorial](https://github.com/sjcabs/fly_connectome_data_tutorial) (sjcabs) | orientation material | MIT | Read for method. |
| [philshiu/Drosophila_brain_model](https://github.com/philshiu/Drosophila_brain_model) | whole-brain LIF model | MIT | Verified 2026-09-11 — the brief recorded this as unverified. Read for method. |
| [eonsystemspbc/fly-brain](https://github.com/eonsystemspbc/fly-brain) | adjacent implementation | GPL-2.0-or-later | **Excluded.** Never vendored, never copied into this MIT repository. |
| flyDash (same author) | source of the MaleCNS fetch and load pipeline, and the dashboard's fly mesh | MIT | Ported with attribution in each ported module's docstring. |
| [three.js](https://github.com/mrdoob/three.js) | 3D rendering in the dashboard | MIT | **Vendored** at `flypaper/web/static/three.module.js` so the dashboard works offline. Copyright notice retained in the file. |
| [NeuroMechFly v2](https://github.com/NeLy-EPFL/flygym) (Lobato-Rios et al., *Nature Methods* 2024) | the anatomical fly body in the dashboard | Apache-2.0 | **Vendored** as a quantised mesh at `flypaper/web/static/fly.bin`. Shown as staging only: not simulated, and not spatially registered to the connectome. |
| [MNIST](https://yann.lecun.com/exdb/mnist/) (LeCun & Cortes) | M3 benchmark dataset | CC BY-SA 3.0 | Fetched on demand to `data/benchmarks/`, never committed. |
| [Fashion-MNIST](https://github.com/zalandoresearch/fashion-mnist) (Zalando SE) | M3 benchmark dataset | MIT | Fetched on demand to `data/benchmarks/`, never committed. |

| Package | License |
|---|---|
| numpy | BSD-3-Clause |
| scipy | BSD-3-Clause |
| pandas | BSD-3-Clause |
| pyarrow | Apache-2.0 |
| requests | Apache-2.0 |
| PyYAML | MIT |
| pytest | MIT |
| ruff | MIT |
| hatchling | MIT |

<!-- LICENSE-MATRIX-END -->

The Male CNS dataset is licensed under CC-BY. See
<https://creativecommons.org/licenses/by/4.0/>.

## Citations

| Result | Citation |
|---|---|
| FlyHash / olfactory locality-sensitive hashing | Dasgupta, Stevens & Navlakha, *Science* 358(6364):793–796, 2017 |
| Fly Bloom Filter / novelty detection | Dasgupta, Sheehan, Stevens & Navlakha, *PNAS* 115(51):13093–13098, 2018 |
| MaleCNS connectome | Berg et al., *Cell*, 2026 — doi:10.1016/j.cell.2026.08.015 |
| Whole-brain LIF model | Shiu et al., *Nature* 634:210–219, 2024 |

## Credits

ffuf is by Joona Hoikkala (@joohoi) and contributors. ffufme is by Adam Langley.
ffufPostprocessing is by dsecuredcom. The MaleCNS reconstruction is a collaboration between
FlyEM (HHMI Janelia), the University of Cambridge (Dept. of Zoology), the MRC Laboratory of
Molecular Biology, and Google Research.
