# M5 - stage two: gustation

**Gate: scope enforcement has a test proving an out-of-scope URL in the input stream is
never requested. Met.** `tests/test_stage2_scope.py` starts a real HTTP server that records
every path it is asked for, seeds the input stream with in-scope and out-of-scope URLs all
pointing at that same server, and fails if it hears anything it should not have.

The rate limiter is verified against a fake clock: 100 requests at the ceiling must span at
least 99 full intervals, and no burst is permitted at any arrival pattern.

## Why two stages

Stage one is olfaction: remote, cheap, no certainty. Metadata only, every response, and
**`fly` generates no traffic at all** - it reads results the operator already produced.

Stage two is contact chemosensation. The fly has to land on the thing to taste it, and it
cannot afford to land on everything. Neither can we, at a hundred thousand requests. So a
handful of the most novel candidates are re-requested and read properly.

That is the answer to "why not just parse bodies in the first place".

## What stage two will not do

These are enforced, not documented and hoped for.

| Constraint | Where it lives | What proves it |
|---|---|---|
| Only URLs already present in the input stream | `_candidate_urls` | `test_only_urls_from_the_input_stream_are_requested` |
| A scope file is mandatory - no default, no wildcard default, no same-domain inference | `refetch` raises without one | `test_nothing_is_requested_without_a_scope` |
| A bare `example.com` does not authorise subdomains or lookalikes | `Scope.decide` | `test_bare_domain_does_not_authorise_subdomains_or_lookalikes` |
| `*.example.com` matches subdomains but not the bare domain | `Scope.decide` | `test_wildcard_matches_subdomains_but_not_the_bare_domain` |
| Only http and https | `ALLOWED_SCHEMES` | `test_non_http_schemes_are_refused` |
| Redirects recorded, never followed | `allow_redirects=False` | `test_redirects_are_recorded_but_not_followed` |
| Rate capped below a ceiling in the code | `MAX_RATE` | `test_rate_is_capped_and_cannot_be_raised_past_the_ceiling` |
| Bodies not retained by default | `retain_bodies=False` | `test_bodies_are_not_retained_by_default` |

A URL that does not parse is out of scope. A scheme we do not recognise is out of scope. A
scope file containing a path is a hard error rather than a silent reinterpretation. The
default in every ambiguous case is to refuse.

### On the redirect guard

Setting `max_redirects = 0` on the session looked like sensible belt-and-braces and is
actively wrong: `requests` then raises `TooManyRedirects` instead of returning the
response, so the redirect target is lost rather than recorded. `allow_redirects=False` at
the single call site is the control, and the test asserts a 302 comes back as data with its
`Location` intact.

## Body features

Structural, not semantic. Nothing looks for a pattern that means trouble - that would be
detection, and this tool ranks. What is measured is shape: entropy, tag density, distinct
tag count, text ratio, line geometry, title, and whether the bytes are binary. Two responses
of identical length can be a directory listing and an error page, and stage one cannot tell
them apart.

`shared_title` marks a candidate whose title also appears on another candidate. A title
shared across candidates is the host's furniture - a framework's default error page - and is
evidence a candidate is ordinary, not that it is interesting.

Where the operator ran ffuf with `-scrapers`, that output is already in the stage-one stream
and is carried through without a re-fetch. The cheapest stage two is the one not made.

## Credentials

An operator-supplied header is passed through verbatim and nothing else happens to it. It is
not parsed, not stored, not logged, and not derived from. `--header 'Authorization: ...'` is
the whole of the feature.

## Limitations

- **Stage two has not been run against any third-party host.** Every test and every
  end-to-end run in this repository targets loopback.
- **`shared_title` needs several candidates to mean anything.** With `--top 3` it will
  usually be empty, which is not the same as "nothing is boilerplate".
- **Re-ranking on body features is not implemented.** Stage two currently reports body
  features alongside the stage-one novelty score; it does not yet fold them back into a
  second-stage score. That needs body-level channels and a channel-set version of its own,
  and inventing them without a corpus to test against would be guessing.
