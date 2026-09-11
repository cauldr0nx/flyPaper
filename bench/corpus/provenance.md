# Corpus provenance

Every corpus records where it came from, when, and on what authorization. **No corpus
captured from a third-party target is ever committed**, and no corpus holds response
bodies — metadata and feature vectors only.

Corpora themselves are gitignored. This file is the record of what exists locally.

## Authorization basis

The only target used so far is a container built from public source and run on loopback on
the operator's own machine. No third-party host has been contacted by any part of this
project.

## Captures

| Corpus | Source | Date | Authorization | Committed |
|---|---|---|---|---|
| M1 spike capture | ffufme container, `127.0.0.1:8099` | 2026-09-11 | Locally hosted by the operator, bound to loopback | No — a 9-record slice lives in `tests/fixtures/` as parser fixtures |

The M1 capture is a 10,000-request run used to settle how ffuf streams, not a labelled
corpus. The labelled replay corpus is M2 and does not exist yet.

## What M2 must contain

Per §8 of the brief, the corpus must include or be constructed to include:

- a wildcard / soft-404 host — ffufme's `/cd/no404` lesson
- a token-rotating endpoint
- the ffuf issue #387 scenario: a valid result whose size, words or lines individually
  match an autocalibrated filter
- at least one combinatorial multi-wordlist run

ffufme covers the first directly and the fourth by construction. The second and third are
not among its lessons and will need a target we own; that decision is deferred to M2.

## Target provenance

| | |
|---|---|
| Image | `ffufme:8814611`, id `94d864f9f5da` |
| Built | 2026-09-11 from <https://github.com/BuildHackSecure/ffufme> commit `8814611cca35e824ea70f257684604c2c422258a` |
| Binding | `127.0.0.1:8099` |
| License | No license file published — run locally only, source never vendored. See `reports/licenses.md`. |
