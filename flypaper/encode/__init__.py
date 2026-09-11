"""Response metadata -> the receptor vector.

The encoder is where the skill lives: the neural code downstream is comparatively simple,
and a poor channel set degrades the whole system to noise. Channel sets are versioned for
that reason - see `channels.py`.
"""

from flypaper.encode.channels import (
    CHANNEL_SET_VERSION,
    CHANNEL_SETS,
    channel_names,
    get_channel_set,
    n_channels,
)
from flypaper.encode.features import Encoder, encode, encode_all

__all__ = [
    "CHANNEL_SETS",
    "CHANNEL_SET_VERSION",
    "Encoder",
    "channel_names",
    "encode",
    "encode_all",
    "get_channel_set",
    "n_channels",
]
