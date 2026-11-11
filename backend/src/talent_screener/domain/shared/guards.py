"""Reusable domain invariant guard functions."""

from __future__ import annotations

import math
from datetime import UTC, datetime
from uuid import UUID

from talent_screener.domain.shared.exceptions import (
    DomainError,
    InvariantViolationError,
    ValidationError,
)


def ensure(
    condition: bool,
    message: str,
    *,
    code: str = "INVARIANT_VIOLATION",
    error_cls: type[DomainError] = InvariantViolationError,
) -> None:
    """Assert a domain invariant or raise a typed DomainError."""
    if not condition:
        raise error_cls(message, code=code)


def require_non_empty_str(
    value: object,
    field_name: str,
    *,
    min_len: int = 1,
    max_len: int | None = None,
    code: str = "INVALID_STRING",
) -> str:
    """Validate that value is a non-blank string within length bounds."""
    if not isinstance(value, str):
        raise ValidationError(f"{field_name} must be a string.", code=code)
    stripped = value.strip()
    if len(stripped) < min_len:
        raise ValidationError(
            f"{field_name} must contain at least {min_len} non-whitespace character(s).",
            code=code,
        )
    if max_len is not None and len(stripped) > max_len:
        raise ValidationError(
            f"{field_name} must not exceed {max_len} characters (got {len(stripped)}).",
            code=code,
        )
    return stripped


def require_finite_float(
    value: object,
    field_name: str,
    *,
    code: str = "INVALID_FLOAT",
) -> float:
    """Validate that value is a finite real number (excluding bool, NaN, inf)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValidationError(f"{field_name} must be a finite number.", code=code)
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValidationError(f"{field_name} must be finite (not NaN or infinity).", code=code)
    return numeric


def require_float_range(
    value: object,
    field_name: str,
    min_val: float,
    max_val: float,
    *,
    inclusive_min: bool = True,
    inclusive_max: bool = True,
    code: str = "OUT_OF_RANGE",
) -> float:
    """Validate that a numeric value is finite and within [min_val, max_val]."""
    numeric = require_finite_float(value, field_name, code=code)
    low_ok = numeric >= min_val if inclusive_min else numeric > min_val
    high_ok = numeric <= max_val if inclusive_max else numeric < max_val
    if not (low_ok and high_ok):
        left = "[" if inclusive_min else "("
        right = "]" if inclusive_max else ")"
        raise ValidationError(
            f"{field_name} must be in {left}{min_val}, {max_val}{right}; got {numeric}.",
            code=code,
        )
    return numeric


def require_int_range(
    value: object,
    field_name: str,
    min_val: int,
    max_val: int | None = None,
    *,
    code: str = "OUT_OF_RANGE",
) -> int:
    """Validate that value is an integer (not bool) within bounds."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(f"{field_name} must be an integer.", code=code)
    if value < min_val or (max_val is not None and value > max_val):
        bound_desc = f"[{min_val}, {max_val}]" if max_val is not None else f">= {min_val}"
        raise ValidationError(
            f"{field_name} must satisfy {bound_desc}; got {value}.",
            code=code,
        )
    return value


def require_non_nil_uuid(
    value: object,
    field_name: str,
    *,
    code: str = "INVALID_UUID",
) -> UUID:
    """Validate that value is a non-nil UUID instance."""
    if not isinstance(value, UUID) or value.int == 0:
        raise ValidationError(f"{field_name} must be a non-nil UUID.", code=code)
    return value


def require_utc_datetime(
    value: object,
    field_name: str,
    *,
    code: str = "INVALID_UTC_DATETIME",
) -> datetime:
    """Validate that a datetime is timezone-aware and normalize to UTC."""
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValidationError(
            f"{field_name} must be a timezone-aware UTC datetime.",
            code=code,
        )
    return value.astimezone(UTC)


def clamp(value: float, min_val: float, max_val: float) -> float:
    """Clamp a float to the closed interval [min_val, max_val]."""
    return max(min_val, min(max_val, value))
