"""Scope-gated, rate-limited re-fetch. M5.

Hard constraints, not preferences:

  * Re-requests only URLs already present in the input stream. Never crawls, never
    guesses, never follows a redirect to a new host, never expands scope.
  * A `--scope` file is mandatory. Explicit host patterns only - no implicit same-domain
    inference, no wildcard default. Anything not matching is skipped and logged.
  * Rate limited, default conservative, configurable down but never up past a hard
    ceiling.

The gate for M5 is a test proving an out-of-scope URL present in the input stream is
never requested.
"""

from __future__ import annotations

__all__ = ["refetch"]


def refetch(*args, **kwargs):
    raise NotImplementedError("M5: stage-two re-fetch is not implemented yet.")
