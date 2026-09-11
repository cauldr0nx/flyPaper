"""The tool ranks; it does not detect.

That is a constraint on what the code is allowed to say, so it is a test rather than a
convention. Output, documentation and reports say "novel" — never "vulnerable", never
"finding", never "issue".

The check runs on sentences rather than lines, because the sentences that legitimately use
these words are the ones denying the claim ("a novel response is a statistical outlier, not
a vulnerability") and prose wraps wherever it wraps.
"""

from __future__ import annotations

import re

import pytest

from flypaper import REPO_ROOT

# Words that would assert something the tool cannot support.
BANNED = re.compile(r"\b(vulnerab\w*|findings?|issues?|exploit\w*)\b", re.IGNORECASE)

# A sentence that denies the claim rather than making it. Keeping this list short is the
# point: each entry is a place the documentation explicitly refuses to overclaim.
DENIAL = re.compile(
    r"\b(not a vulnerability"
    r"|does not detect"
    r"|is not the same as"
    r"|much further still"
    r"|no exploitation"
    r"|never \"?(?:vulnerable|finding|issue)"
    r"|ranks; it does not"
    r")\b",
    re.IGNORECASE,
)

SEARCHED = [
    *sorted((REPO_ROOT / "flypaper").rglob("*.py")),
    REPO_ROOT / "README.md",
    *sorted((REPO_ROOT / "reports").glob("*.md")),
]


def sentences(text: str) -> list[str]:
    """Split into sentences with wrapping removed, keeping markdown blocks apart."""
    out: list[str] = []
    for block in re.split(r"\n\s*\n", text):
        flat = " ".join(block.split())
        out.extend(s for s in re.split(r"(?<=[.!?])\s+", flat) if s)
    return out


@pytest.mark.parametrize("path", SEARCHED, ids=lambda p: str(p.relative_to(REPO_ROOT)))
def test_no_detection_claims(path):
    for sentence in sentences(path.read_text(encoding="utf-8")):
        if DENIAL.search(sentence):
            continue
        # A URL is not a claim, and ffuf's issue tracker is cited by number.
        stripped = re.sub(r"https?://\S+", "", sentence)
        stripped = re.sub(r"\bffuf issue #\d+\b", "", stripped, flags=re.IGNORECASE)
        match = BANNED.search(stripped)
        assert not match, (
            f"{path.relative_to(REPO_ROOT)} says {match.group(0)!r} in: {sentence[:120]!r}\n"
            f"flypaper ranks; it does not detect. Say 'novel'."
        )


def test_the_check_would_actually_catch_something():
    """A guard that cannot fail is not a guard."""
    bad = "This response is vulnerable to path traversal."
    assert BANNED.search(bad) and not DENIAL.search(bad)
