"""Channel definitions, versioned.

The fly gets 50 receptor types shaped by evolution; here the 50 channels are a design
choice, and a bad one makes the whole system noise. That is why the set is versioned: a
baseline built under one channel set must refuse to load under another rather than
silently produce garbage.

Nothing is defined yet. M2 builds at least two competing encodings and reports both.
"""

from __future__ import annotations

__all__ = ["CHANNEL_SET_VERSION", "channel_names", "n_channels"]

# Bumped whenever a channel is added, removed or redefined. Written into every saved
# baseline and printed in every ranked output.
CHANNEL_SET_VERSION = "v0-unset"


def channel_names() -> tuple[str, ...]:
    raise NotImplementedError(
        "M2: the channel set is not defined yet. See README.md section 6 of the brief for "
        "the candidate encodings to build and compare."
    )


def n_channels() -> int:
    raise NotImplementedError("M2: the channel set is not defined yet.")
