"""Pure stdlib RFC 9562 monotonic UUIDv7 generator and UTC clock helpers."""

from __future__ import annotations

import os
import threading
import time
from datetime import UTC, datetime
from uuid import UUID

_UUID7_LOCK = threading.Lock()
_LAST_TS_MS: int = 0
_LAST_RAND_A: int = 0


def generate_uuid7() -> UUID:
    """Generate a monotonic, time-ordered RFC 9562 UUIDv7 using Python stdlib only."""
    global _LAST_TS_MS, _LAST_RAND_A

    with _UUID7_LOCK:
        ts_ms = time.time_ns() // 1_000_000
        if ts_ms <= _LAST_TS_MS:
            ts_ms = _LAST_TS_MS
            _LAST_RAND_A += 1
            if _LAST_RAND_A > 0x0FFF:
                ts_ms += 1
                _LAST_RAND_A = 0
        else:
            # Reserve top headroom in 12-bit rand_a for intra-millisecond monotonic increments.
            _LAST_RAND_A = int.from_bytes(os.urandom(2), "big") & 0x03FF
        _LAST_TS_MS = ts_ms
        rand_a = _LAST_RAND_A

    rand_b = int.from_bytes(os.urandom(8), "big") & ((1 << 62) - 1)
    value_int = (
        ((ts_ms & 0xFFFFFFFFFFFF) << 80)
        | (0x7 << 76)
        | ((rand_a & 0x0FFF) << 64)
        | (0b10 << 62)
        | rand_b
    )
    return UUID(int=value_int)


def is_uuid7(value: UUID) -> bool:
    """Return True if the UUID conforms to RFC 9562 version 7."""
    return isinstance(value, UUID) and value.version == 7


def utc_now() -> datetime:
    """Return the current timezone-aware UTC timestamp."""
    return datetime.now(tz=UTC)
