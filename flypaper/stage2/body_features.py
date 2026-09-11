"""Body-level features from a stage-two re-fetch.

Stage one has only metadata, which is what makes it free. Stage two has the body, and the
question is what to read off it that stage one could not see.

These are structural, not semantic. Nothing here looks for a pattern that means trouble -
that would be detection, and this tool ranks. What it measures is *shape*: how much markup,
how much entropy, how repetitive, how much of it is boilerplate shared with everything else
on the host. Two responses of identical length can be a directory listing and an error page,
and stage one cannot tell them apart.

ffuf's own scraper output is used where present, because a feature already in the stage-one
stream is a request that need not be sent at all.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable
from dataclasses import dataclass, field

__all__ = ["BodyFeatures", "body_features", "from_scraper"]

_TAG = re.compile(rb"<[a-zA-Z/!][^>]*>")
_WHITESPACE = re.compile(rb"\s+")
_TITLE = re.compile(rb"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)


def _entropy(data: bytes, sample: int = 65536) -> float:
    """Shannon entropy per byte, 0-8. Distinguishes text from compressed or encrypted bytes."""
    chunk = data[:sample]
    if not chunk:
        return 0.0
    counts = [0] * 256
    for byte in chunk:
        counts[byte] += 1
    n = len(chunk)
    return -sum((c / n) * math.log2(c / n) for c in counts if c)


@dataclass(frozen=True, slots=True)
class BodyFeatures:
    """Structural description of a response body. Never the body itself."""

    length: int = 0
    entropy: float = 0.0
    tag_count: int = 0
    tag_ratio: float = 0.0
    distinct_tags: int = 0
    text_ratio: float = 0.0
    line_count: int = 0
    max_line_length: int = 0
    title: str = ""
    is_probably_binary: bool = False
    scraper: dict[str, list[str]] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "length": self.length,
            "entropy": round(self.entropy, 4),
            "tag_count": self.tag_count,
            "tag_ratio": round(self.tag_ratio, 4),
            "distinct_tags": self.distinct_tags,
            "text_ratio": round(self.text_ratio, 4),
            "line_count": self.line_count,
            "max_line_length": self.max_line_length,
            "title": self.title,
            "is_probably_binary": self.is_probably_binary,
            "scraper_keys": sorted(self.scraper),
        }


def body_features(
    body: bytes | None, *, scraper: dict[str, list[str]] | None = None
) -> BodyFeatures:
    """Describe a body structurally. `None` yields the empty description, not an error."""
    if not body:
        return BodyFeatures(scraper=dict(scraper or {}))

    tags = _TAG.findall(body)
    tag_bytes = sum(len(t) for t in tags)
    stripped = _WHITESPACE.sub(b" ", _TAG.sub(b" ", body)).strip()
    lines = body.split(b"\n")

    title = ""
    match = _TITLE.search(body)
    if match:
        title = match.group(1).decode("utf-8", "replace").strip()[:120]

    # A NUL in the first kilobyte is the oldest and still the most reliable binary test.
    binary = b"\x00" in body[:1024]

    return BodyFeatures(
        length=len(body),
        entropy=_entropy(body),
        tag_count=len(tags),
        tag_ratio=tag_bytes / len(body),
        distinct_tags=len({t.split()[0].lower().rstrip(b">") for t in tags}) if tags else 0,
        text_ratio=len(stripped) / len(body),
        line_count=len(lines),
        max_line_length=max((len(line) for line in lines), default=0),
        title=title,
        is_probably_binary=binary,
        scraper=dict(scraper or {}),
    )


def from_scraper(scraper: dict[str, list[str]]) -> BodyFeatures:
    """Features from ffuf's scraper output alone, with no re-fetch at all.

    The cheapest stage two is the one you do not make: if the operator ran ffuf with
    scrapers, some body-level information is already sitting in the stage-one stream.
    """
    return BodyFeatures(scraper=dict(scraper or {}))


def shared_boilerplate(features: Iterable[BodyFeatures]) -> set[str]:
    """Titles that appear on more than one candidate.

    A title shared across candidates is the host's furniture - a generic error page, a
    framework's default - and is evidence a candidate is ordinary, not evidence it is
    interesting.
    """
    seen: dict[str, int] = {}
    for item in features:
        if item.title:
            seen[item.title] = seen.get(item.title, 0) + 1
    return {title for title, count in seen.items() if count > 1}
