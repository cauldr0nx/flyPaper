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

Early. The scaffold and the ingest path are built; nothing ranks anything yet.

| Milestone | What it is | State |
|---|---|---|
| M0 | Scaffold, licenses recorded | done |
| M1 | Ingest spike — how ffuf actually streams, schema-tolerant parser | done, see [reports/m1-ingest.md](reports/m1-ingest.md) |
| M2 | Encoder and replay corpus | not started |
| M3 | Connectome FlyHash vs. random projection | not started |
| M4 | Ranking, measured against ffuf's own filters | not started |
| M5 | Stage two — scope-gated, rate-limited re-fetch | not started |
| M6 | Ergonomics | not started |

Each milestone is a binary gate. A failed gate is written up in `reports/` and stopped on,
never loosened by moving the threshold.

## Install

```bash
uv venv && uv pip install -e ".[dev]"
make ci          # ruff + pytest, offline, no dataset required
```

There is deliberately no hosted CI workflow yet; `make ci` is the gate.

## Usage today

```bash
# live, from a pipe
ffuf -mc all -json -u http://host/FUZZ -w list.txt | fly ingest

# from a finished results file, either shape
fly ingest --file results.json

# follow a file still being written
fly ingest --file results.jsonl --follow
```

`fly ingest` parses and normalises; it does not filter, threshold or score. `fly rank`
exists as a command and tells you it arrives at M4.

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
| MaleCNS v1.0 connectome data | the measured PN→KC wiring | CC BY 4.0 | Governs the data regardless of this repository's MIT license. Verbatim terms and attribution in `data/manifest.json`. Never committed. |
| [fly_connectome_data_tutorial](https://github.com/sjcabs/fly_connectome_data_tutorial) (sjcabs) | orientation material | MIT | Read for method. |
| [philshiu/Drosophila_brain_model](https://github.com/philshiu/Drosophila_brain_model) | whole-brain LIF model | MIT | Verified 2026-09-11 — the brief recorded this as unverified. Read for method. |
| [eonsystemspbc/fly-brain](https://github.com/eonsystemspbc/fly-brain) | adjacent implementation | GPL-2.0-or-later | **Excluded.** Never vendored, never copied into this MIT repository. |
| flyDash (same author) | source of the MaleCNS fetch and load pipeline | MIT | Ported with attribution in each ported module's docstring. |

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
