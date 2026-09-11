"""Channel definitions, versioned.

The fly gets 50 receptor types shaped by evolution. Here the channels are a design choice,
and a bad one makes the whole system noise - so the set is versioned, a baseline records
the version that built it, and a baseline built under one set refuses to load under
another rather than silently producing garbage.

Two sets are defined, per section 6 of the brief, and both are reported:

`v1-raw`
    The naive encoding: raw numeric fields, min-max normalised against running extremes,
    plus a status one-hot. What someone would write first. It is here as the control, not
    as a straw man - it is a reasonable first attempt and it fails in an instructive way.

`v2-log`
    The designed encoding from section 6 of the brief, including the candidate input-word
    character profile. Log scales the heavy-tailed size fields, adds ratio features
    invariant to overall response size, gives response time its own z-scored channel,
    encodes the redirect target's similarity to the request, and profiles the input word.

`v3-response`
    `v2-log` with the `word.*` block removed, and the default. Measuring the within-cluster
    variance of `v2-log` on a wildcard host showed **98% of it coming from the input-word
    channels** - unsurprising in hindsight, because the fuzzed word is different on every
    single request by construction, so encoding it makes every response unique and destroys
    exactly the collapse the tool depends on. `v2-log` fails both invariance properties for
    that reason and `v3-response` passes them; see reports/m2-encoder.md.

    The word is not worthless - a `.bak` answering 200 is more interesting than a random
    string answering 200 - but that is a ranking concern for M4, not a clustering signal
    for stage one, and mixing it in here costs more than it pays.

Every channel is a pure function of one result plus the running statistics seen so far, so
encoding stays single-pass and unsupervised. No labels, no training corpus, no second look
at data already emitted.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import urlparse

from flypaper.ingest.ffuf import FfufResult

__all__ = [
    "CHANNEL_SETS",
    "CHANNEL_SET_VERSION",
    "Channel",
    "ChannelSet",
    "RunningStats",
    "channel_names",
    "get_channel_set",
    "n_channels",
]

# The set used unless one is named. Bumped whenever a channel is added, removed or
# redefined; written into every baseline and printed in every ranked record.
CHANNEL_SET_VERSION = "v3-response"


class RunningStats:
    """Welford's algorithm, plus running extremes.

    Single-pass by construction: a channel sees the statistics of everything before it and
    nothing after, which is what the fly gets too.
    """

    __slots__ = ("n", "mean", "m2", "lo", "hi")

    def __init__(self) -> None:
        self.n = 0
        self.mean = 0.0
        self.m2 = 0.0
        self.lo = math.inf
        self.hi = -math.inf

    def update(self, x: float) -> None:
        self.n += 1
        delta = x - self.mean
        self.mean += delta / self.n
        self.m2 += delta * (x - self.mean)
        self.lo = min(self.lo, x)
        self.hi = max(self.hi, x)

    @property
    def std(self) -> float:
        return math.sqrt(self.m2 / (self.n - 1)) if self.n > 1 else 0.0

    def z(
        self, x: float, *, rel_floor: float = 0.0, abs_floor: float = 0.0, clip: float = 4.0
    ) -> float:
        """Z-score squashed to [0, 1]. Returns 0.5 - "unremarkable" - before there is data.

        **A z-score needs a noise floor or it amplifies noise without bound.** When a
        quantity is near-constant its standard deviation collapses toward zero, and then any
        jitter at all - a rotating token moving Content-Length by six bytes, a millisecond
        of network scheduling - divides by almost nothing and fills the channel's whole
        range. The floor states, in the units of the quantity itself, how large a difference
        has to be before it is worth calling a difference:

          `rel_floor`  as a fraction of the running mean
          `abs_floor`  in absolute units, for when the mean is small

        These are domain statements, not fitted constants. A page whose size moves by under
        2% has not changed; a request whose duration moves by under half its own average has
        not been slow.
        """
        if self.n < 2:
            return 0.5
        scale = max(self.std, rel_floor * abs(self.mean), abs_floor)
        if scale == 0.0:
            return 0.5
        return _clamp01(0.5 + (x - self.mean) / (scale * 2 * clip))

    def minmax(self, x: float) -> float:
        if self.n == 0 or self.hi <= self.lo:
            return 0.5
        return _clamp01((x - self.lo) / (self.hi - self.lo))


@dataclass
class Context:
    """Running statistics the channels draw on. One per encoder, updated per record."""

    length: RunningStats = field(default_factory=RunningStats)
    words: RunningStats = field(default_factory=RunningStats)
    lines: RunningStats = field(default_factory=RunningStats)
    duration: RunningStats = field(default_factory=RunningStats)
    log_length: RunningStats = field(default_factory=RunningStats)

    def observe(self, r: FfufResult) -> None:
        self.length.update(float(r.length))
        self.words.update(float(r.words))
        self.lines.update(float(r.lines))
        self.duration.update(float(r.duration_ms))
        self.log_length.update(math.log1p(max(r.length, 0)))


ChannelFn = Callable[[FfufResult, Context], float]


@dataclass(frozen=True)
class Channel:
    """One receptor. Name is documentation; the function is the definition."""

    name: str
    fn: ChannelFn


@dataclass(frozen=True)
class ChannelSet:
    version: str
    description: str
    channels: tuple[Channel, ...]

    def __len__(self) -> int:
        return len(self.channels)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.channels)


def _clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else x


def _log_scaled(value: float, ceiling: float) -> float:
    """log1p compressed against a fixed ceiling. Size distributions are heavy-tailed:
    the difference between 200 and 400 bytes matters, between 200 kB and 400 kB much less."""
    return _clamp01(math.log1p(max(value, 0.0)) / math.log1p(ceiling))


# --- input-word analysis --------------------------------------------------------------------

_EXT_CLASSES = {
    "php": ("php", "phtml", "php5", "asp", "aspx", "jsp"),
    "markup": ("html", "htm", "xml", "xhtml"),
    "data": ("json", "csv", "yaml", "yml", "txt"),
    "archive": ("bak", "old", "zip", "tar", "gz", "sql", "swp"),
}
_WORD_SPLIT = re.compile(r"[/\\]")


def _extension(word: str) -> str:
    tail = _WORD_SPLIT.split(word)[-1]
    return tail.rsplit(".", 1)[1].lower() if "." in tail[1:] else ""


def _entropy(text: str) -> float:
    if not text:
        return 0.0
    counts: dict[str, int] = {}
    for ch in text:
        counts[ch] = counts.get(ch, 0) + 1
    n = len(text)
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


def _fraction(text: str, predicate: Callable[[str], bool]) -> float:
    return sum(1 for ch in text if predicate(ch)) / len(text) if text else 0.0


def _path_of(url: str) -> str:
    try:
        return urlparse(url).path or "/"
    except ValueError:
        return "/"


def _token_overlap(a: str, b: str) -> float:
    """Jaccard over path segments. Cheap, order-free, and enough to say whether a redirect
    points somewhere related to what was asked for."""
    ta = {t for t in _WORD_SPLIT.split(a) if t}
    tb = {t for t in _WORD_SPLIT.split(b) if t}
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _content_type_class(ct: str) -> str:
    ct = ct.split(";")[0].strip().lower()
    if not ct:
        return "none"
    if "html" in ct:
        return "html"
    if "json" in ct:
        return "json"
    if "xml" in ct:
        return "xml"
    if ct.startswith("image/"):
        return "image"
    if ct.startswith("text/"):
        return "text"
    if "javascript" in ct or "ecmascript" in ct:
        return "script"
    if "octet-stream" in ct or ct.startswith("application/"):
        return "binary"
    return "other"


_CT_CLASSES = ("html", "json", "xml", "text", "script", "image", "binary", "none", "other")
_STATUS_EXACT = (200, 204, 301, 302, 307, 401, 403, 404, 405, 500)


# --- v1-raw ------------------------------------------------------------------------------------


def _v1_channels() -> tuple[Channel, ...]:
    out: list[Channel] = [
        Channel("raw.length", lambda r, c: c.length.minmax(float(r.length))),
        Channel("raw.words", lambda r, c: c.words.minmax(float(r.words))),
        Channel("raw.lines", lambda r, c: c.lines.minmax(float(r.lines))),
        Channel("raw.duration", lambda r, c: c.duration.minmax(float(r.duration_ms))),
        Channel("raw.status", lambda r, c: _clamp01(r.status / 600.0)),
        Channel("raw.has_redirect", lambda r, c: 1.0 if r.redirect_location else 0.0),
        Channel("raw.word_length", lambda r, c: _clamp01(len(r.word) / 32.0)),
    ]
    for cls in (2, 3, 4, 5):
        out.append(
            Channel(f"raw.status_{cls}xx", lambda r, c, k=cls: 1.0 if r.status // 100 == k else 0.0)
        )
    return tuple(out)


# --- v2-log --------------------------------------------------------------------------------------


def _v2_channels() -> tuple[Channel, ...]:
    out: list[Channel] = []

    # Size, log scaled. Heavy-tailed distributions do not belong on a linear axis.
    out += [
        Channel("size.log_length", lambda r, c: _log_scaled(r.length, 1e6)),
        Channel("size.log_words", lambda r, c: _log_scaled(r.words, 1e5)),
        Channel("size.log_lines", lambda r, c: _log_scaled(r.lines, 1e4)),
        # A rotating token moves Content-Length by a handful of bytes on a page of a
        # thousand. 2% is comfortably above that and far below any real difference.
        Channel(
            "size.length_z", lambda r, c: c.length.z(float(r.length), rel_floor=0.02, abs_floor=2)
        ),
        Channel(
            "size.words_z", lambda r, c: c.words.z(float(r.words), rel_floor=0.02, abs_floor=1)
        ),
        Channel(
            "size.lines_z", lambda r, c: c.lines.z(float(r.lines), rel_floor=0.02, abs_floor=1)
        ),
    ]

    # Ratios. Invariant to overall size, which is exactly what a rotating token moves.
    out += [
        Channel(
            "ratio.bytes_per_word",
            lambda r, c: _log_scaled(r.length / max(r.words, 1), 1e3),
        ),
        Channel(
            "ratio.words_per_line",
            lambda r, c: _log_scaled(r.words / max(r.lines, 1), 1e3),
        ),
        Channel(
            "ratio.bytes_per_line",
            lambda r, c: _log_scaled(r.length / max(r.lines, 1), 1e4),
        ),
        Channel(
            "ratio.length_per_input_char",
            lambda r, c: _log_scaled(r.length / max(len(r.word), 1), 1e4),
        ),
    ]

    # Status: class and the exact codes worth their own receptor.
    for cls in (2, 3, 4, 5):
        out.append(
            Channel(f"status.{cls}xx", lambda r, c, k=cls: 1.0 if r.status // 100 == k else 0.0)
        )
    for code in _STATUS_EXACT:
        out.append(Channel(f"status.{code}", lambda r, c, k=code: 1.0 if r.status == k else 0.0))
    out.append(
        Channel(
            "status.uncommon",
            lambda r, c: 0.0 if r.status in _STATUS_EXACT or r.status == 0 else 1.0,
        )
    )

    # Content type.
    for cls in _CT_CLASSES:
        out.append(
            Channel(
                f"ctype.{cls}",
                lambda r, c, k=cls: 1.0 if _content_type_class(r.content_type) == k else 0.0,
            )
        )

    # Redirects: whether, where, and how far from what was asked for.
    out += [
        Channel("redir.present", lambda r, c: 1.0 if r.redirect_location else 0.0),
        Channel(
            "redir.same_host",
            lambda r, c: (
                1.0
                if r.redirect_location
                and urlparse(r.redirect_location).netloc in ("", urlparse(r.url).netloc)
                else 0.0
            ),
        ),
        Channel(
            "redir.path_similarity",
            lambda r, c: (
                _token_overlap(_path_of(r.url), _path_of(r.redirect_location))
                if r.redirect_location
                else 0.0
            ),
        ),
        Channel("redir.log_length", lambda r, c: _log_scaled(len(r.redirect_location), 512)),
    ]

    # Response time, its own channel rather than buried in the numeric block.
    out += [
        # Response time is the noisiest thing here: scheduling, connection reuse and the
        # target's own load move it far more than anything we care about. A difference has
        # to be large in relative terms before it is signal.
        Channel(
            "time.z", lambda r, c: c.duration.z(float(r.duration_ms), rel_floor=0.5, abs_floor=2.0)
        ),
        Channel("time.log", lambda r, c: _log_scaled(r.duration_ms, 3e4)),
    ]

    # The input word. What was asked for is a feature of the response.
    out += [
        Channel("word.log_length", lambda r, c: _log_scaled(len(r.word), 64)),
        Channel("word.depth", lambda r, c: _clamp01(len(_WORD_SPLIT.findall(r.word)) / 6.0)),
        Channel("word.has_extension", lambda r, c: 1.0 if _extension(r.word) else 0.0),
        Channel("word.digit_fraction", lambda r, c: _fraction(r.word, str.isdigit)),
        Channel("word.upper_fraction", lambda r, c: _fraction(r.word, str.isupper)),
        Channel(
            "word.punct_fraction",
            lambda r, c: _fraction(r.word, lambda ch: not ch.isalnum() and ch not in "/\\"),
        ),
        Channel("word.entropy", lambda r, c: _clamp01(_entropy(r.word) / 6.0)),
    ]
    for cls, members in _EXT_CLASSES.items():
        out.append(
            Channel(
                f"word.ext_{cls}",
                lambda r, c, m=members: 1.0 if _extension(r.word) in m else 0.0,
            )
        )
    out.append(
        Channel(
            "word.ext_other",
            lambda r, c: (
                1.0
                if _extension(r.word)
                and not any(_extension(r.word) in m for m in _EXT_CLASSES.values())
                else 0.0
            ),
        )
    )
    return tuple(out)


#: Channels that read the fuzzed word rather than the response. Excluded from v3.
_WORD_DEPENDENT = ("word.", "ratio.length_per_input_char")


def _v3_channels() -> tuple[Channel, ...]:
    """v2 without any channel that reads the input word. See the module docstring."""
    return tuple(c for c in _v2_channels() if not c.name.startswith(_WORD_DEPENDENT))


CHANNEL_SETS: dict[str, ChannelSet] = {
    "v1-raw": ChannelSet(
        version="v1-raw",
        description="raw numeric fields, min-max normalised against running extremes",
        channels=_v1_channels(),
    ),
    "v2-log": ChannelSet(
        version="v2-log",
        description=(
            "log-scaled sizes, size-invariant ratios, status and content-type receptors, "
            "redirect similarity, z-scored response time, input-word character profile"
        ),
        channels=_v2_channels(),
    ),
    "v3-response": ChannelSet(
        version="v3-response",
        description=(
            "v2-log with the input-word channels removed: response properties only, "
            "because the fuzzed word is unique per request and swamps the clustering"
        ),
        channels=_v3_channels(),
    ),
}


def get_channel_set(version: str | None = None) -> ChannelSet:
    version = version or CHANNEL_SET_VERSION
    try:
        return CHANNEL_SETS[version]
    except KeyError:
        known = ", ".join(sorted(CHANNEL_SETS))
        raise KeyError(f"unknown channel set {version!r}; known sets: {known}") from None


def channel_names(version: str | None = None) -> tuple[str, ...]:
    return get_channel_set(version).names


def n_channels(version: str | None = None) -> int:
    return len(get_channel_set(version))
