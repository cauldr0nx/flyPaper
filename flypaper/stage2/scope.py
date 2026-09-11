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
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlsplit

__all__ = ["Scope", "ScopeDecision", "ScopeSyntaxError", "check_pattern"]

ALLOWED_SCHEMES = frozenset({"http", "https"})

#: A host pattern: labels of letters, digits and hyphens, optionally prefixed `*.`, and
#: optionally suffixed `:port`. Deliberately strict - see `check_pattern`.
_HOST_PATTERN = re.compile(
    r"^(?:\*\.)?(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)*[a-z0-9](?:[a-z0-9-]*[a-z0-9])?(?::\d{1,5})?$"
)


class ScopeSyntaxError(ValueError):
    """A scope line is not a host pattern. Always fatal - see `check_pattern`."""


def check_pattern(raw: str) -> str:
    """Validate one scope line, or raise with what is wrong and what to do instead.

    Every rejection here is a real shape found in published bug bounty scopes, and the
    reason each is refused rather than best-guessed is the same: **a scope file must not be
    able to authorise more, or less, than the operator meant.**

    A pattern that silently never matches is as bad as one that matches too much. The
    operator believes an asset is covered, stage two quietly skips it, and they conclude it
    was tested. So anything that cannot be a host is an error, not a no-op.
    """
    line = raw.strip().lower()
    if not line:
        raise ScopeSyntaxError("empty pattern")

    if line.startswith(("http://", "https://", "//")):
        raise ScopeSyntaxError(
            f"{raw.strip()!r} is a URL, not a host pattern. Give the host alone - but check "
            f"first whether the scope really covers the whole host or only that path, "
            f"because this file cannot express a path."
        )
    if re.search(r"/\d{1,3}$", line):
        raise ScopeSyntaxError(
            f"{raw.strip()!r} looks like a CIDR range. Host patterns only: list the hosts "
            f"you mean, or resolve the range yourself first."
        )
    if "/" in line:
        raise ScopeSyntaxError(
            f"{raw.strip()!r} contains a path. This file scopes hosts, and dropping the "
            f"path would authorise the whole host - which is wider than the scope says."
        )
    if "," in line:
        raise ScopeSyntaxError(
            f"{raw.strip()!r} contains a comma. One pattern per line, or only the first "
            f"host would ever match and the rest would be silently out of scope."
        )
    if " " in line or "\t" in line:
        raise ScopeSyntaxError(f"{raw.strip()!r} contains whitespace; it is not a host")
    if any(ch in line for ch in "{}<>[]()"):
        raise ScopeSyntaxError(
            f"{raw.strip()!r} contains a placeholder. Expand it to the hosts you actually "
            f"mean; a literal placeholder can never match anything."
        )
    if "*" in line and not line.startswith("*."):
        raise ScopeSyntaxError(
            f"{raw.strip()!r} has a wildcard that is not a leading '*.' label. Only whole "
            f"subdomain wildcards are supported, because a partial one is too easy to read "
            f"as wider or narrower than it is."
        )
    if line.count("*") > 1:
        raise ScopeSyntaxError(f"{raw.strip()!r} has more than one wildcard")
    if not _HOST_PATTERN.match(line):
        raise ScopeSyntaxError(f"{raw.strip()!r} is not a valid host pattern")
    return line


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
        for number, raw in enumerate(lines, 1):
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            try:
                patterns.append(check_pattern(line))
            except ScopeSyntaxError as exc:
                raise ScopeSyntaxError(f"{path}:{number}: {exc}") from None
        if not patterns:
            raise ScopeSyntaxError(f"{path} contains no host patterns; that authorises nothing")
        return cls(patterns=tuple(dict.fromkeys(patterns)))

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
