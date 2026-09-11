"""Scope enforcement for stage-two re-fetch.

This module exists to make it *impossible* to touch something the operator is not
authorised to test, not merely unlikely. Everything it does is a refusal by default:

  * A scope file is mandatory. There is no default, no wildcard default, and no implicit
    "same domain as the input" inference. If no scope is given, nothing is in scope.
  * Only exact hosts and explicit `*.example.com` subdomain patterns are accepted. A bare
    `example.com` does **not** authorise `evil-example.com`, `example.com.attacker.net`, or
    any subdomain.
  * Only http and https. No file://, no gopher://, no schemeless guessing.
  * Anything that does not parse is out of scope. A URL we cannot understand is not a URL
    we may request.

Every decision is logged with its reason, because "it was skipped" and "it was requested"
must both be answerable after the fact.
"""

from __future__ import annotations

import fnmatch
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

__all__ = ["Scope", "ScopeDecision"]

ALLOWED_SCHEMES = frozenset({"http", "https"})


@dataclass(frozen=True, slots=True)
class ScopeDecision:
    url: str
    allowed: bool
    reason: str


@dataclass
class Scope:
    """Explicit host patterns. Nothing else is in scope.

    A scope file is one pattern per line; `#` starts a comment. A pattern is either an exact
    host (`api.example.com`, optionally with `:port`) or a subdomain wildcard
    (`*.example.com`), which matches subdomains **but not the bare domain** - list that
    separately if you mean it. Saying what you mean is cheap; a scan outside scope is not.
    """

    patterns: tuple[str, ...] = ()
    skipped: list[ScopeDecision] = field(default_factory=list)

    @classmethod
    def from_file(cls, path: str | Path) -> Scope:
        lines = Path(path).read_text(encoding="utf-8").splitlines()
        patterns = []
        for raw in lines:
            line = raw.split("#", 1)[0].strip().lower()
            if not line:
                continue
            if "/" in line or "*" in line.rstrip() and not line.startswith("*."):
                raise ValueError(
                    f"{path}: {raw.strip()!r} is not a host pattern. Use an exact host, or "
                    f"'*.example.com' for subdomains. Paths and bare wildcards are refused."
                )
            patterns.append(line)
        if not patterns:
            raise ValueError(f"{path} contains no host patterns; that authorises nothing")
        return cls(patterns=tuple(patterns))

    def decide(self, url: str) -> ScopeDecision:
        """Whether this exact URL may be requested, and why."""
        if not self.patterns:
            return ScopeDecision(url, False, "no scope configured")
        try:
            parts = urlsplit(url)
        except ValueError:
            return ScopeDecision(url, False, "unparseable URL")
        if parts.scheme.lower() not in ALLOWED_SCHEMES:
            return ScopeDecision(url, False, f"scheme {parts.scheme!r} is not http or https")
        try:
            host = (parts.hostname or "").lower()
        except ValueError:
            return ScopeDecision(url, False, "unparseable host")
        if not host:
            return ScopeDecision(url, False, "no host in URL")

        port = f":{parts.port}" if parts.port else ""
        for pattern in self.patterns:
            if pattern.startswith("*."):
                # `*.example.com` matches a subdomain, never the bare domain, and never a
                # host that merely ends with the same characters.
                if fnmatch.fnmatch(host, pattern) and host.endswith(pattern[1:]):
                    return ScopeDecision(url, True, f"matches {pattern}")
            elif host == pattern or f"{host}{port}" == pattern:
                return ScopeDecision(url, True, f"matches {pattern}")
        return ScopeDecision(url, False, f"host {host!r} matches no scope pattern")

    def allows(self, url: str) -> bool:
        decision = self.decide(url)
        if not decision.allowed:
            self.skipped.append(decision)
        return decision.allowed
