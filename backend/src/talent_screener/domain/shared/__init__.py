"""Shared Domain Kernel public exports."""

from talent_screener.domain.shared.base import AggregateRoot, DomainEvent, Entity
from talent_screener.domain.shared.exceptions import (
    ConcurrencyConflictError,
    DomainError,
    EntityNotFoundError,
    InvalidStateTransitionError,
    InvariantViolationError,
    ValidationError,
)
from talent_screener.domain.shared.guards import (
    clamp,
    ensure,
    require_finite_float,
    require_float_range,
    require_int_range,
    require_non_empty_str,
    require_non_nil_uuid,
    require_utc_datetime,
)
from talent_screener.domain.shared.uuid7 import generate_uuid7, is_uuid7, utc_now
from talent_screener.domain.shared.value_objects import (
    ConfidenceScore,
    NormalizedScore,
    SHA256Hash,
    TenantId,
    ValueObject,
    VersionNumber,
)

__all__ = [
    "AggregateRoot",
    "ConcurrencyConflictError",
    "ConfidenceScore",
    "DomainError",
    "DomainEvent",
    "Entity",
    "EntityNotFoundError",
    "InvalidStateTransitionError",
    "InvariantViolationError",
    "NormalizedScore",
    "SHA256Hash",
    "TenantId",
    "ValidationError",
    "ValueObject",
    "VersionNumber",
    "clamp",
    "ensure",
    "generate_uuid7",
    "is_uuid7",
    "require_finite_float",
    "require_float_range",
    "require_int_range",
    "require_non_empty_str",
    "require_non_nil_uuid",
    "require_utc_datetime",
    "utc_now",
]
