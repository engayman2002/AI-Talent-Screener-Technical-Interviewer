"""ADR-013 W3C TraceContext OpenTelemetry GenAI Tracer with Automatic PII Redaction."""

from __future__ import annotations

import hashlib
import math
import os
import re
import time
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from types import TracebackType
from uuid import UUID

from talent_screener.application.common.dtos import TokenUsageRecordDTO
from talent_screener.domain.shared.value_objects import TenantId

_EMAIL_RE = re.compile(r"\b[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+\b")
_BEARER_JWT_RE = re.compile(
    r"\b(?:Bearer\s+)?[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{8,}\b",
    re.I,
)
_PHONE_RE = re.compile(
    r"(?<!\d)(?:\+?\d{1,3}[\s.-]?)?(?:\(\d{3}\)|\d{3})[\s.-]?\d{3}[\s.-]?\d{4}(?!\d)"
)
_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_SENSITIVE_KEY_TOKENS: frozenset[str] = frozenset(
    {
        "email",
        "phone",
        "telephone",
        "mobile",
        "full_name",
        "candidate_name",
        "ssn",
        "authorization",
        "bearer",
        "auth_token",
        "access_token",
        "refresh_token",
        "ticket_token",
        "secret",
        "password",
        "api_key",
        "raw_cv",
    }
)

_TRACEPARENT_RE = re.compile(
    r"^00-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})$",
    re.I,
)


@dataclass(frozen=True, slots=True)
class W3CTraceContext:
    """Parsed or generated W3C ``traceparent`` context (version 00)."""

    trace_id: str
    span_id: str
    trace_flags: str = "01"

    def to_traceparent(self) -> str:
        return f"00-{self.trace_id}-{self.span_id}-{self.trace_flags}"


def parse_traceparent(header_value: str | None) -> W3CTraceContext | None:
    """Parse a valid W3C ``traceparent`` header; reject all-zero trace/span IDs."""
    if not header_value:
        return None
    match = _TRACEPARENT_RE.fullmatch(header_value.strip())
    if match is None:
        return None
    trace_id = match.group(1).lower()
    span_id = match.group(2).lower()
    flags = match.group(3).lower()
    if trace_id == "0" * 32 or span_id == "0" * 16:
        return None
    return W3CTraceContext(trace_id=trace_id, span_id=span_id, trace_flags=flags)


def _derive_trace_id(correlation_id: str | None = None) -> str:
    if correlation_id:
        cleaned = re.sub(r"[^0-9a-fA-F]", "", correlation_id).lower()
        if len(cleaned) == 32 and cleaned != "0" * 32:
            return cleaned
        return hashlib.sha256(correlation_id.encode("utf-8")).hexdigest()[:32]
    return os.urandom(16).hex()


def redact_pii_text(raw: str) -> str:
    """Scrub emails, Bearer/JWT tokens, phone numbers, and SSNs with deterministic hash tags."""
    def _tag(kind: str, val: str) -> str:
        digest = hashlib.sha256(val.encode("utf-8")).hexdigest()[:8]
        return f"[REDACTED_{kind}:{digest}]"

    cleaned = _BEARER_JWT_RE.sub(lambda m: _tag("TOKEN", m.group(0)), raw)
    cleaned = _EMAIL_RE.sub(lambda m: _tag("EMAIL", m.group(0)), cleaned)
    cleaned = _SSN_RE.sub(lambda m: _tag("SSN", m.group(0)), cleaned)
    cleaned = _PHONE_RE.sub(lambda m: _tag("PHONE", m.group(0)), cleaned)
    return cleaned


def _sanitize_value(key: str, value: object) -> object:
    lower_key = key.lower()
    if any(tok in lower_key for tok in _SENSITIVE_KEY_TOKENS):
        digest = hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:8]
        return f"[REDACTED_KEY:{digest}]"
    if isinstance(value, str):
        return redact_pii_text(value)
    if isinstance(value, Mapping):
        return {str(k): _sanitize_value(str(k), v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_sanitize_value(key, item) for item in value]
    return value


def _compute_percentile(sorted_vals: Sequence[float], q: float) -> float:
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return round(sorted_vals[0], 4)
    idx = q * float(len(sorted_vals) - 1)
    lo = math.floor(idx)
    hi = math.ceil(idx)
    frac = idx - float(lo)
    val = sorted_vals[lo] * (1.0 - frac) + sorted_vals[hi] * frac
    return round(val, 4)


@dataclass(frozen=True, slots=True)
class LatencyPercentiles:
    """P50/P95/P99 latency summary for finished OpenTelemetry spans."""

    count: int
    p50_ms: float
    p95_ms: float
    p99_ms: float
    max_ms: float


@dataclass(frozen=True, slots=True)
class RecordedSpanSnapshot:
    """Immutable exported span record adhering to W3C TraceContext and OTel GenAI conventions."""

    name: str
    trace_id: str
    span_id: str
    parent_span_id: str | None
    tenant_id: UUID
    correlation_id: str
    started_at_monotonic_ms: float
    duration_ms: float
    status: str
    error_message: str | None
    attributes: Mapping[str, object]

    def to_dict(self) -> dict[str, object]:
        return {
            "name": self.name,
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_span_id": self.parent_span_id,
            "tenant_id": str(self.tenant_id),
            "correlation_id": self.correlation_id,
            "duration_ms": self.duration_ms,
            "status": self.status,
            "error_message": self.error_message,
            "attributes": dict(self.attributes),
        }


class StructuredTraceSpan(AbstractContextManager["StructuredTraceSpan"]):
    """Active parent-child OpenTelemetry span automatically enforcing zero-PII redaction."""

    def __init__(
        self,
        *,
        tracer: StructuredOpenTelemetryTracerAdapter,
        name: str,
        trace_id: str,
        span_id: str,
        parent_span_id: str | None,
        tenant_id: TenantId,
        correlation_id: str,
        attributes: Mapping[str, object] | None,
        clock_fn: Callable[[], float],
    ) -> None:
        self._tracer = tracer
        self.name = name
        self.trace_id = trace_id
        self.span_id = span_id
        self.parent_span_id = parent_span_id
        self.tenant_id = tenant_id
        self.correlation_id = correlation_id
        self._clock = clock_fn
        self._start_ms = self._clock() * 1000.0
        self.attributes: dict[str, object] = {}
        if attributes is not None:
            for k, v in attributes.items():
                self.set_attribute(k, v)
        self.attributes["tenant_id"] = str(tenant_id.value)
        self.attributes["correlation_id"] = redact_pii_text(correlation_id)
        self._ctx_token: Token[tuple[str, str] | None] | None = None
        self._closed = False

    def traceparent(self) -> str:
        return W3CTraceContext(trace_id=self.trace_id, span_id=self.span_id).to_traceparent()

    def __enter__(self) -> StructuredTraceSpan:
        self._ctx_token = self._tracer._push_active_span(self.trace_id, self.span_id)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        _ = traceback
        if not self._closed:
            end_ms = self._clock() * 1000.0
            duration_ms = max(0.0, round(end_ms - self._start_ms, 4))
            status = "ERROR" if exc_type is not None else "OK"
            err_msg = redact_pii_text(str(exc_value)) if exc_value is not None else None
            if err_msg is not None:
                self.attributes["error.message"] = err_msg
            self._tracer._record_completed_span(
                RecordedSpanSnapshot(
                    name=self.name,
                    trace_id=self.trace_id,
                    span_id=self.span_id,
                    parent_span_id=self.parent_span_id,
                    tenant_id=self.tenant_id.value,
                    correlation_id=redact_pii_text(self.correlation_id),
                    started_at_monotonic_ms=round(self._start_ms, 4),
                    duration_ms=duration_ms,
                    status=status,
                    error_message=err_msg,
                    attributes=dict(self.attributes),
                )
            )
            self._closed = True
        if self._ctx_token is not None:
            self._tracer._pop_active_span(self._ctx_token)
            self._ctx_token = None

    def set_attribute(self, key: str, value: object) -> None:
        """Store span attribute only after zero-PII scrambling."""
        self.attributes[key] = _sanitize_value(key, value)


class StructuredOpenTelemetryTracerAdapter:
    """W3C-aware OpenTelemetry GenAI Tracer fulfilling ObservabilityTracerPort."""

    def __init__(self, *, clock_fn: Callable[[], float] | None = None) -> None:
        self._clock: Callable[[], float] = (
            clock_fn if clock_fn is not None else time.perf_counter
        )
        self._active_span_var: ContextVar[tuple[str, str] | None] = ContextVar(
            f"otel_active_span_{id(self)}", default=None
        )
        self._completed_spans: list[RecordedSpanSnapshot] = []
        self._token_usages: list[TokenUsageRecordDTO] = []
        self._metrics: list[tuple[str, float, dict[str, str]]] = []

    @property
    def spans(self) -> tuple[RecordedSpanSnapshot, ...]:
        return tuple(self._completed_spans)

    @property
    def token_usages(self) -> tuple[TokenUsageRecordDTO, ...]:
        return tuple(self._token_usages)

    @property
    def metrics(self) -> tuple[tuple[str, float, dict[str, str]], ...]:
        return tuple(self._metrics)

    def _push_active_span(
        self, trace_id: str, span_id: str
    ) -> Token[tuple[str, str] | None]:
        return self._active_span_var.set((trace_id, span_id))

    def _pop_active_span(self, token: Token[tuple[str, str] | None]) -> None:
        self._active_span_var.reset(token)

    def _record_completed_span(self, span: RecordedSpanSnapshot) -> None:
        self._completed_spans.append(span)

    def start_span(
        self,
        *,
        name: str,
        tenant_id: TenantId,
        correlation_id: str,
        attributes: Mapping[str, object] | None = None,
        traceparent: str | None = None,
    ) -> StructuredTraceSpan:
        """Create a W3C parent-linked OTel span with automatic PII scrubbing."""
        parsed_parent = parse_traceparent(traceparent)
        active = self._active_span_var.get()

        if parsed_parent is not None:
            trace_id = parsed_parent.trace_id
            parent_span_id: str | None = parsed_parent.span_id
        elif active is not None:
            trace_id, parent_span_id = active
        else:
            trace_id = _derive_trace_id(correlation_id)
            parent_span_id = None

        span_id = os.urandom(8).hex()
        return StructuredTraceSpan(
            tracer=self,
            name=name,
            trace_id=trace_id,
            span_id=span_id,
            parent_span_id=parent_span_id,
            tenant_id=tenant_id,
            correlation_id=correlation_id,
            attributes=attributes,
            clock_fn=self._clock,
        )

    def record_token_usage(self, usage: TokenUsageRecordDTO) -> None:
        self._token_usages.append(usage)

    def record_metric(
        self,
        *,
        name: str,
        value: float,
        tags: Mapping[str, str] | None = None,
    ) -> None:
        safe_tags = (
            {str(k): str(_sanitize_value(str(k), v)) for k, v in tags.items()}
            if tags is not None
            else {}
        )
        self._metrics.append((name, float(value), safe_tags))

    def compute_latency_percentiles(
        self, *, span_name: str | None = None
    ) -> LatencyPercentiles:
        """Compute exact P50, P95, and P99 latencies across completed spans."""
        durations = sorted(
            s.duration_ms
            for s in self._completed_spans
            if span_name is None or s.name == span_name
        )
        if not durations:
            return LatencyPercentiles(count=0, p50_ms=0.0, p95_ms=0.0, p99_ms=0.0, max_ms=0.0)
        return LatencyPercentiles(
            count=len(durations),
            p50_ms=_compute_percentile(durations, 0.50),
            p95_ms=_compute_percentile(durations, 0.95),
            p99_ms=_compute_percentile(durations, 0.99),
            max_ms=round(durations[-1], 4),
        )

    def summarize_operations(self) -> dict[str, object]:
        """Serialize latency percentiles & span breakdown for `/v1/ops/metrics`."""
        overall = self.compute_latency_percentiles()
        span_names = sorted({s.name for s in self._completed_spans})
        by_span = {
            s_name: {
                "count": p.count,
                "p50_ms": p.p50_ms,
                "p95_ms": p.p95_ms,
                "p99_ms": p.p99_ms,
                "max_ms": p.max_ms,
            }
            for s_name in span_names
            for p in (self.compute_latency_percentiles(span_name=s_name),)
        }
        return {
            "total_spans": len(self._completed_spans),
            "overall_latency_ms": {
                "count": overall.count,
                "p50_ms": overall.p50_ms,
                "p95_ms": overall.p95_ms,
                "p99_ms": overall.p99_ms,
                "max_ms": overall.max_ms,
            },
            "by_span": by_span,
            "total_metrics_recorded": len(self._metrics),
        }
