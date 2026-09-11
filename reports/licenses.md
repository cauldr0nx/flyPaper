# License audit

**Gate: no unknown licenses. Met** — every upstream below resolves to a concrete answer,
including the four that resolve to "no license file published", which is itself a concrete
answer and a real constraint rather than a blank.

Audited 2026-09-11. The matrix in [README.md](../README.md) is the machine-checked version
of this document; `tests/test_licenses.py` fails the suite if an entry goes unresolved, if
a GPL upstream loses its exclusion marker, or if an unlicensed upstream stops stating how
it is handled.

## 1. This repository

MIT. `LICENSE` at the root.

## 2. The tool we read the output of

**ffuf** — <https://github.com/ffuf/ffuf> — **MIT**, Copyright (c) 2021 Joona Hoikkala.

Not vendored, not forked, no code copied. The operator runs ffuf; `fly` reads what it
prints. Cited in the README credits. The installed build is `2.1.0-dev`.

## 3. The practice target

**ffufme** — <https://github.com/BuildHackSecure/ffufme>, by Adam Langley —
**no license file published**. Verified via the GitHub API on 2026-09-11: the repository
has no `LICENSE` file and GitHub reports no detected license.

This is a real constraint, so the handling is explicit rather than assumed:

- The source is cloned to a scratch directory **outside this repository**, built, and run
  as a local container. It is never vendored into the tree and never redistributed.
- Running software locally is not redistribution. Copying its source into an MIT
  repository would be, so we do not.
- Captured corpora hold **response metadata only** — status, length, words, lines,
  content-type, duration, the input word — never response bodies, and are never committed.
- Credited by name in the README.

The container in use was built from commit `8814611cca35e824ea70f257684604c2c422258a`.

## 4. Prior art we cite but do not copy

All three were checked on 2026-09-11 and none publishes a license file. Each is cited as
prior art in the design; **no code from any of them is reused**.

| Project | Author | License | Why it matters |
|---|---|---|---|
| [ffufPostprocessing](https://github.com/dsecuredcom/ffufPostprocessing) | dsecuredcom | none published | Closest prior art — recalculates response sizes to strip dynamic content before filtering. The hand-built version of what flypaper does statistically. |
| [ffufw](https://github.com/puzzlepeaches/ffufw) | puzzlepeaches | none published | Wrapper showing the workflow operators actually want. |
| [FFUF-Workflow-Tool](https://github.com/nullenc0de/FFUF-Workflow-Tool) | nullenc0de | none published | Evidence that pipe-not-fork is the community norm. |

## 5. The connectome data

**MaleCNS v1.0** — <https://male-cns.janelia.org/> — **CC BY 4.0**.

The homepage states, verbatim:

> The Male CNS dataset is licensed under CC-BY.

and links <https://creativecommons.org/licenses/by/4.0/>, so the version is stated upstream
rather than inferred by us.

These terms govern the data regardless of this repository's MIT license. They are recorded
verbatim in `data/manifest.json` alongside the required attribution, and
`tests/test_licenses.py::test_manifest_carries_the_data_terms_verbatim` asserts they are
still there.

- **Attribution:** FlyEM Project Team (HHMI Janelia); University of Cambridge, Dept. of
  Zoology; MRC Laboratory of Molecular Biology; Google Research.
- **Citation:** Berg et al., *Cell*, 2026. doi:10.1016/j.cell.2026.08.015
- Bulk data is never committed. `data/fetch.py` fetches it over plain HTTPS from the public
  bucket, verifies every byte against a pinned SHA256 and the upstream MD5, and hard-fails
  without leaving an unverified file in place.

## 6. Reference material

| Project | License | Handling |
|---|---|---|
| [sjcabs/fly_connectome_data_tutorial](https://github.com/sjcabs/fly_connectome_data_tutorial) | MIT | Orientation material, read for method. |
| [philshiu/Drosophila_brain_model](https://github.com/philshiu/Drosophila_brain_model) | **MIT** | The brief recorded this as unverified. Verified via the GitHub API on 2026-09-11: MIT. Read for method. |
| [eonsystemspbc/fly-brain](https://github.com/eonsystemspbc/fly-brain) | **GPL-2.0-or-later** | **Excluded.** Never vendored, never copied. Confirmed GPL-2.0 on 2026-09-11. A test asserts this row keeps its exclusion marker. |
| flyDash (same author, `~/dev/flyDash`) | MIT | The MaleCNS fetch, manifest and table-loading pipeline is ported from it. Each ported module says so in its docstring. |

## 7. Vendored in the dashboard

Two third-party assets are committed to this repository rather than fetched, so the
dashboard works with no network connection. Both are permissive and both keep their notices.

| Asset | Path | License | Handling |
|---|---|---|---|
| three.js | `flypaper/web/static/three.module.js` | MIT, Copyright 2010-2023 Three.js Authors | Vendored unmodified; the `@license` header is retained in the file. |
| NeuroMechFly v2 body mesh | `flypaper/web/static/fly.bin`, `fly.json` | Apache-2.0, NeLy-EPFL/flygym | Vendored as a quantised vertex buffer derived from the published meshes. `fly.json` carries the source and citation inline. |

The NeuroMechFly model is cited as Lobato-Rios et al., *Nature Methods*, 2024. It appears in
the dashboard as anatomical staging: it is not simulated by anything in this project and is
not spatially registered to the connectome coordinates, which is why it sits in a separate
panel from the measured synapse cloud and is labelled as such on screen.

## 8. Benchmark datasets

Fetched on demand by `bench/datasets.py` into `data/benchmarks/`, verified against pinned
SHA256, and never committed.

| Dataset | License | Attribution |
|---|---|---|
| MNIST | CC BY-SA 3.0 | Yann LeCun and Corinna Cortes |
| Fashion-MNIST | MIT | Zalando SE |

## 9. Runtime and development dependencies

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

All permissive and compatible with MIT distribution. No copyleft dependency is present, and
none should be added without revisiting this document.

## 10. What is not here

No GPL code. No vendored third-party source of any license. No committed bulk data, no
committed corpora, and no response bodies anywhere in the tree.
