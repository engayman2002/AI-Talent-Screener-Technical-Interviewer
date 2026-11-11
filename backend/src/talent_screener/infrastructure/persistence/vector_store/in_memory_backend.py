"""Tenant-isolated multi-vector store backend protocol and in-memory Qdrant-like engine."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol, runtime_checkable
from uuid import UUID

from talent_screener.application.common.exceptions import UnauthorizedTenantAccessError
from talent_screener.domain.shared.value_objects import TenantId


def _require_valid_tenant(tenant_id: object) -> TenantId:
    if not isinstance(tenant_id, TenantId) or not isinstance(tenant_id.value, UUID):
        raise UnauthorizedTenantAccessError(
            "Valid TenantId is required for every vector store operation.",
            code="MISSING_TENANT_CONTEXT",
        )
    return tenant_id


@dataclass(frozen=True, slots=True)
class ParentChunkPayload:
    """Stored parent container chunk metadata scoped strictly by tenant_id."""

    parent_chunk_id: UUID
    tenant_id: str
    candidate_id: str
    requisition_id: str
    section_type: str
    heading: str
    content: str
    skill_tags: tuple[str, ...]
    last_used_year: int | None
    sync_version: int


@dataclass(frozen=True, slots=True)
class ChildVectorPoint:
    """Stored child chunk combining dense embedding, sparse BM25 weights, and payload."""

    point_id: UUID
    parent_chunk_id: UUID
    tenant_id: str
    candidate_id: str
    requisition_id: str
    section_type: str
    skill_tags: tuple[str, ...]
    last_used_year: int | None
    recency_months_delta: int
    evidentiary_depth: int
    sync_version: int
    raw_excerpt: str
    contextualized_text: str
    dense_vector: tuple[float, ...]
    sparse_vector: tuple[tuple[str, float], ...]


@runtime_checkable
class VectorStoreBackendProtocol(Protocol):
    """Protocol abstracting tenant-partitioned hybrid vector collections (Qdrant / pgvector)."""

    async def upsert_candidate_chunks(
        self,
        *,
        tenant_id: TenantId,
        candidate_profile_id: UUID,
        sync_version: int,
        parent_chunks: Sequence[ParentChunkPayload],
        child_points: Sequence[ChildVectorPoint],
    ) -> None: ...

    async def list_candidate_points(
        self,
        *,
        tenant_id: TenantId,
        requisition_id: UUID,
        candidate_profile_id: UUID,
    ) -> tuple[ChildVectorPoint, ...]: ...

    async def get_parent_chunk(
        self,
        *,
        tenant_id: TenantId,
        parent_chunk_id: UUID,
    ) -> ParentChunkPayload | None: ...

    async def delete_by_candidate(
        self,
        *,
        tenant_id: TenantId,
        candidate_profile_id: UUID,
    ) -> int: ...


class InMemoryHybridVectorStoreBackend:
    """Multi-tenant in-memory VectorStoreBackendProtocol supporting version-guarded upserts."""

    def __init__(self) -> None:
        # Partitioned strictly by (tenant_id_uuid, point_id).
        self._parents: dict[tuple[UUID, UUID], ParentChunkPayload] = {}
        self._points: dict[tuple[UUID, UUID], ChildVectorPoint] = {}

    async def upsert_candidate_chunks(
        self,
        *,
        tenant_id: TenantId,
        candidate_profile_id: UUID,
        sync_version: int,
        parent_chunks: Sequence[ParentChunkPayload],
        child_points: Sequence[ChildVectorPoint],
    ) -> None:
        """Idempotently upsert parent & child chunks for one candidate under tenant partition."""
        valid_tenant = _require_valid_tenant(tenant_id)
        t_str = str(valid_tenant.value)
        c_str = str(candidate_profile_id)

        # Guard against cross-tenant payload mismatch before writing.
        for parent in parent_chunks:
            if parent.tenant_id != t_str or parent.candidate_id != c_str:
                raise UnauthorizedTenantAccessError(
                    "Parent chunk tenant/candidate metadata mismatch.",
                    code="CROSS_TENANT_VECTOR_WRITE",
                )
        for point in child_points:
            if point.tenant_id != t_str or point.candidate_id != c_str:
                raise UnauthorizedTenantAccessError(
                    "Child vector point tenant/candidate metadata mismatch.",
                    code="CROSS_TENANT_VECTOR_WRITE",
                )

        # Skip stale out-of-order sync version if newer sync_version is already indexed.
        existing_max_ver = max(
            (
                pt.sync_version
                for (tid, _), pt in self._points.items()
                if tid == valid_tenant.value and pt.candidate_id == c_str
            ),
            default=0,
        )
        if existing_max_ver > sync_version:
            return

        # Replace prior candidate chunks atomically when advancing version.
        stale_child_keys = [
            key
            for key, pt in self._points.items()
            if key[0] == valid_tenant.value
            and pt.candidate_id == c_str
            and pt.sync_version < sync_version
        ]
        for key in stale_child_keys:
            del self._points[key]

        stale_parent_keys = [
            key
            for key, par in self._parents.items()
            if key[0] == valid_tenant.value
            and par.candidate_id == c_str
            and par.sync_version < sync_version
        ]
        for key in stale_parent_keys:
            del self._parents[key]

        for parent in parent_chunks:
            self._parents[(valid_tenant.value, parent.parent_chunk_id)] = parent
        for point in child_points:
            self._points[(valid_tenant.value, point.point_id)] = point

    async def list_candidate_points(
        self,
        *,
        tenant_id: TenantId,
        requisition_id: UUID,
        candidate_profile_id: UUID,
    ) -> tuple[ChildVectorPoint, ...]:
        """Return candidate child vector points scoped strictly to tenant and requisition."""
        valid_tenant = _require_valid_tenant(tenant_id)
        req_str = str(requisition_id)
        cand_str = str(candidate_profile_id)
        t_str = str(valid_tenant.value)

        return tuple(
            pt
            for (tid, _), pt in self._points.items()
            if tid == valid_tenant.value
            and pt.tenant_id == t_str
            and pt.requisition_id == req_str
            and pt.candidate_id == cand_str
        )

    async def get_parent_chunk(
        self,
        *,
        tenant_id: TenantId,
        parent_chunk_id: UUID,
    ) -> ParentChunkPayload | None:
        """Retrieve a parent section chunk strictly within caller's tenant partition."""
        valid_tenant = _require_valid_tenant(tenant_id)
        return self._parents.get((valid_tenant.value, parent_chunk_id))

    async def delete_by_candidate(
        self,
        *,
        tenant_id: TenantId,
        candidate_profile_id: UUID,
    ) -> int:
        """Delete all parent & child points for candidate inside caller's tenant partition."""
        valid_tenant = _require_valid_tenant(tenant_id)
        cand_str = str(candidate_profile_id)

        child_keys = [
            key
            for key, pt in self._points.items()
            if key[0] == valid_tenant.value and pt.candidate_id == cand_str
        ]
        for key in child_keys:
            del self._points[key]

        parent_keys = [
            key
            for key, par in self._parents.items()
            if key[0] == valid_tenant.value and par.candidate_id == cand_str
        ]
        for key in parent_keys:
            del self._parents[key]

        return len(child_keys)
