"""Gustation: re-fetching the top-N novel candidates.

Contact chemosensation - the fly has to land on the thing to taste it, and it cannot afford
to land on everything. Neither can we, at a hundred thousand requests. So stage one is
metadata only and generates no traffic, and stage two re-requests a handful of candidates.

Hard constraints, enforced here rather than documented and hoped for:

  * **Only URLs already present in the input stream.** The candidate list comes from results
    the operator already generated. Nothing is crawled, guessed, or constructed.
  * **A scope file is mandatory** (see `scope.py`). No default, no wildcard default, no
    implicit same-domain inference.
  * **Redirects are never followed.** A redirect points somewhere that was not in the input
    stream and has not been scope-checked. The Location header is recorded as data.
  * **Rate limited**, conservative by default, configurable down but never up past
    `MAX_RATE`. The ceiling is a constant in the code because a flag is too easy to get
    wrong at three in the morning.
  * **Bodies are not retained** unless explicitly asked for, and never to the default
    database path.

The M5 gate is `tests/test_stage2_scope.py`: an out-of-scope URL present in the input stream
is never requested, proven against a server that records every connection it receives.
"""

from __future__ import annotations

import time
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field

from flypaper.ingest.ffuf import FfufResult
from flypaper.stage2.scope import Scope

__all__ = ["MAX_RATE", "Fetched", "RateLimiter", "refetch"]

#: Requests per second that stage two will never exceed, whatever is passed in. Stage two
#: is a handful of confirmations, not a second scan; if you need more than this you want a
#: fuzzer, and you already have one.
MAX_RATE = 5.0
DEFAULT_RATE = 1.0
DEFAULT_TIMEOUT = 10.0


class RateLimiter:
    """A simple spacing limiter: never start a request sooner than 1/rate after the last.

    Deliberately not a token bucket. A bucket permits a burst, and a burst against a target
    the operator is a guest on is exactly what this must not do.
    """

    def __init__(self, rate: float = DEFAULT_RATE, *, clock=time.monotonic, sleep=time.sleep):
        if rate <= 0:
            raise ValueError("rate must be positive")
        self.rate = min(float(rate), MAX_RATE)
        self.capped = rate > MAX_RATE
        self._interval = 1.0 / self.rate
        self._clock = clock
        self._sleep = sleep
        self._last: float | None = None

    def wait(self) -> None:
        now = self._clock()
        if self._last is not None:
            remaining = self._interval - (now - self._last)
            if remaining > 0:
                self._sleep(remaining)
                now = self._clock()
        self._last = now


@dataclass
class Fetched:
    """The outcome of one stage-two request. Body bytes only when explicitly retained."""

    url: str
    status: int | None = None
    length: int | None = None
    content_type: str = ""
    location: str = ""
    elapsed_ms: float = 0.0
    error: str = ""
    body: bytes | None = None
    headers: dict[str, str] = field(default_factory=dict)


def _candidate_urls(results: Iterable[FfufResult], top: int) -> list[str]:
    """URLs from the input stream, de-duplicated, in the order given, capped at `top`."""
    seen: set[str] = set()
    out: list[str] = []
    for result in results:
        if result.url and result.url not in seen:
            seen.add(result.url)
            out.append(result.url)
            if len(out) >= top:
                break
    return out


def refetch(
    results: Iterable[FfufResult],
    scope: Scope,
    *,
    top: int = 10,
    rate: float = DEFAULT_RATE,
    timeout: float = DEFAULT_TIMEOUT,
    headers: dict[str, str] | None = None,
    retain_bodies: bool = False,
    session=None,
    limiter: RateLimiter | None = None,
) -> Iterator[Fetched]:
    """Re-request in-scope candidates, slowly, following nothing.

    `headers` is passed through verbatim - an operator-supplied `Authorization` or `Cookie`
    for a target they are authorised against. Nothing here parses, stores or derives
    credentials.
    """
    import requests

    if scope is None or not scope.patterns:
        raise ValueError(
            "stage two requires an explicit scope. Pass --scope with host patterns; "
            "there is deliberately no default and no same-domain inference."
        )

    limiter = limiter or RateLimiter(rate)
    http = session or requests.Session()

    for url in _candidate_urls(results, top):
        if not scope.allows(url):
            continue
        limiter.wait()
        started = time.monotonic()
        try:
            # allow_redirects=False is the control that matters, and this is the only
            # place a request is made. `tests/test_stage2_scope.py` proves a 302 is
            # returned as data rather than chased. Setting `max_redirects = 0` on the
            # session as an extra guard does not work: requests raises TooManyRedirects
            # instead of handing back the response, so the redirect target would be lost.
            response = http.get(
                url,
                timeout=timeout,
                allow_redirects=False,
                headers=headers or {},
                stream=not retain_bodies,
            )
            body = response.content if retain_bodies else None
            length = len(response.content) if retain_bodies else _declared_length(response)
            yield Fetched(
                url=url,
                status=response.status_code,
                length=length,
                content_type=response.headers.get("Content-Type", ""),
                location=response.headers.get("Location", ""),
                elapsed_ms=(time.monotonic() - started) * 1000.0,
                body=body,
                headers=dict(response.headers),
            )
        except Exception as exc:  # noqa: BLE001 - one bad target must not end the pass
            yield Fetched(
                url=url,
                error=f"{type(exc).__name__}: {exc}",
                elapsed_ms=(time.monotonic() - started) * 1000.0,
            )
        finally:
            try:
                response.close()  # type: ignore[possibly-undefined]
            except Exception:  # noqa: BLE001
                pass


def _declared_length(response) -> int | None:
    raw = response.headers.get("Content-Length")
    try:
        return int(raw) if raw is not None else None
    except ValueError:
        return None
