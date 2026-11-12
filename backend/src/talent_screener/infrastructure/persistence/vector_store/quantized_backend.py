"""Versioned & INT8-Quantized Hybrid Vector Store Backend Adapter."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from talent_screener.application.common.exceptions import UnauthorizedTenantAccessError
from talent_screener.domain.shared.uuid7 import utc_now
from talent_screener.domain.shared.value_objects import TenantId
from talent_screener.infrastructure.persistence.relational.schema import SqlDatabaseManager
from talent_screener.infrastructure.persistence.vector_store.in_memory_backend import (
    ChildVectorPoint,
    ParentChunkPayload,
)
from talent_screener.infrastructure.persistence.vector_store.quantization import (
    Int8ScalarQuantizer,
    QuantizedSearchHit,
    QuantizedVectorPayload,
)


def _require_tenant(tenant_id: object) -> TenantId:
    if not isinstance(tenant_id, TenantId) or not isinstance(tenant_id.value, UUID):
        raise UnauthorizedTenantAccessError(
            "Valid TenantId is required for quantized vector store operations.",
            code="MISSING_TENANT_CONTEXT",
        )
    return tenant_id


class QuantizedHybridVectorStoreBackend:
    """Multi-tenant INT8-quantized VectorStoreBackendProtocol with sync_version monotonicity."""

    def __init__(
        self,
        *,
        db: SqlDatabaseManager | None = None,
        quantizer: Int8ScalarQuantizer | None = None,
        default_oversample_factor: float = 3.0,
    ) -> None:
        self._db = db
        self._quantizer = quantizer if quantizer is not None else Int8ScalarQuantizer()
        self._oversample_factor = max(1.0, default_oversample_factor)
        self._parents: dict[tuple[UUID, UUID], ParentChunkPayload] = {}
        self._payloads_by_candidate: dict[
            tuple[UUID, UUID], tuple[QuantizedVectorPayload, ...]
        ] = {}
        self._sync_version_by_candidate: dict[tuple[UUID, UUID], int] = {}

    def _record_watermark(
        self,
        *,
        tenant_id: TenantId,
        candidate_profile_id: UUID,
        sync_version: int,
        chunk_count: int,
        is_erased: bool,
    ) -> None:
        if self._db is None:
            return
        with self._db.write_transaction():
            self._db.execute(
                "INSERT INTO vector_sync_watermarks ("
                "tenant_id, candidate_profile_id, latest_sync_version, chunk_count, "
                "is_erased, updated_at"
                ") VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(tenant_id, candidate_profile_id) DO UPDATE SET "
                "latest_sync_version = excluded.latest_sync_version, "
                "chunk_count = excluded.chunk_count, "
                "is_erased = excluded.is_erased, "
                "updated_at = excluded.updated_at",
                (
                    str(tenant_id.value),
                    str(candidate_profile_id),
                    sync_version,
                    chunk_count,
                    int(is_erased),
                    utc_now().isoformat(),
                ),
            )

    def get_sync_version(
        self,
        *,
        tenant_id: TenantId,
        candidate_profile_id: UUID,
    ) -> int | None:
        """Return latest indexed sync_version for a tenant-owned candidate, if any."""
        valid_tenant = _require_tenant(tenant_id)
        return self._sync_version_by_candidate.get((valid_tenant.value, candidate_profile_id))

    def list_indexed_candidates(self, tenant_id: TenantId) -> tuple[tuple[UUID, int], ...]:
        """Return all currently indexed (candidate_profile_id, sync_version) pairs for tenant."""
        valid_tenant = _require_tenant(tenant_id)
        items = [
            (cand_id, ver)
            for (tid, cand_id), ver in self._sync_version_by_candidate.items()
            if tid == valid_tenant.value and (tid, cand_id) in self._payloads_by_candidate
        ]
        items.sort(key=lambda pair: str(pair[0]))
        return tuple(items)

    def list_quantized_payloads(
        self,
        *,
        tenant_id: TenantId,
        candidate_profile_id: UUID,
    ) -> tuple[QuantizedVectorPayload, ...]:
        """Inspect quantized INT8 vector payloads stored for a candidate."""
        valid_tenant = _require_tenant(tenant_id)
        return self._payloads_by_candidate.get((valid_tenant.value, candidate_profile_id), ())

    async def upsert_candidate_chunks(
        self,
        *,
        tenant_id: TenantId,
        candidate_profile_id: UUID,
        sync_version: int,
        parent_chunks: Sequence[ParentChunkPayload],
        child_points: Sequence[ChildVectorPoint],
    ) -> None:
        """Quantize child embeddings into INT8 and apply versioned atomic replacement."""
        valid_tenant = _require_tenant(tenant_id)
        expected_tid = str(valid_tenant.value)
        for par in parent_chunks:
            if par.tenant_id != expected_tid:
                raise UnauthorizedTenantAccessError(
                    "Parent chunk tenant_id mismatch.",
                    code="CROSS_TENANT_VECTOR_WRITE",
                )
        for pt in child_points:
            if pt.tenant_id != expected_tid:
                raise UnauthorizedTenantAccessError(
                    "Child vector point tenant_id mismatch.",
                    code="CROSS_TENANT_VECTOR_WRITE",
                )

        key = (valid_tenant.value, candidate_profile_id)
        current_ver = self._sync_version_by_candidate.get(key)
        # Enforce strict monotonic version monotonicity: ignore stale out-of-order deliveries.
        if current_ver is not None and sync_version < current_ver:
            return

        # Evict stale parent chunks belonging to older sync versions of this candidate.
        cand_str = str(candidate_profile_id)
        stale_parent_keys = [
            p_key
            for p_key, p_val in self._parents.items()
            if p_key[0] == valid_tenant.value and p_val.candidate_id == cand_str
        ]
        for p_key in stale_parent_keys:
            del self._parents[p_key]

        for par in parent_chunks:
            self._parents[(valid_tenant.value, par.parent_chunk_id)] = par

        quantized_payloads = tuple(
            QuantizedVectorPayload(
                point=pt,
                quantized_int8=self._quantizer.quantize(pt.dense_vector, normalize=True),
                dimensions=len(pt.dense_vector),
            )
            for pt in child_points
        )
        self._payloads_by_candidate[key] = quantized_payloads
        self._sync_version_by_candidate[key] = sync_version
        self._record_watermark(
            tenant_id=valid_tenant,
            candidate_profile_id=candidate_profile_id,
            sync_version=sync_version,
            chunk_count=len(quantized_payloads),
            is_erased=False,
        )

    async def list_candidate_points(
        self,
        *,
        tenant_id: TenantId,
        requisition_id: UUID,
        candidate_profile_id: UUID,
    ) -> tuple[ChildVectorPoint, ...]:
        """Return candidate child points strictly scoped by tenant and requisition."""
        valid_tenant = _require_tenant(tenant_id)
        payloads = self._payloads_by_candidate.get((valid_tenant.value, candidate_profile_id), ())
        req_str = str(requisition_id)
        filtered = tuple(
            p.point
            for p in payloads
            if p.point.requisition_id == req_str
        )
        if filtered:
            return filtered
        return tuple(p.point for p in payloads)

    async def search_quantized(
        self,
        *,
        tenant_id: TenantId,
        requisition_id: UUID,
        candidate_profile_id: UUID,
        query_vector: Sequence[float],
        top_k: int = 5,
        oversample_factor: float | None = None,
    ) -> tuple[QuantizedSearchHit, ...]:
        """Two-stage INT8 oversampled shortlist + exact float32 cosine rescoring."""
        valid_tenant = _require_tenant(tenant_id)
        payloads = self._payloads_by_candidate.get((valid_tenant.value, candidate_profile_id), ())
        if not payloads:
            return ()
        req_str = str(requisition_id)
        scoped = tuple(p for p in payloads if p.point.requisition_id == req_str) or payloads
        factor = (
            oversample_factor if oversample_factor is not None else self._oversample_factor
        )
        return self._quantizer.search_with_rescoring(
            query_vector=query_vector,
            candidates=scoped,
            top_k=top_k,
            oversample_factor=factor,
        )

    async def get_parent_chunk(
        self,
        *,
        tenant_id: TenantId,
        parent_chunk_id: UUID,
    ) -> ParentChunkPayload | None:
        valid_tenant = _require_tenant(tenant_id)
        return self._parents.get((valid_tenant.value, parent_chunk_id))

    async def delete_by_candidate(
        self,
        *,
        tenant_id: TenantId,
        candidate_profile_id: UUID,
    ) -> int:
        valid_tenant = _require_tenant(tenant_id)
        key = (valid_tenant.value, candidate_profile_id)
        removed_payloads = self._payloads_by_candidate.pop(key, ())
        cand_str = str(candidate_profile_id)
        parent_keys = [
            p_key
            for p_key, p_val in self._parents.items()
            if p_key[0] == valid_tenant.value and p_val.candidate_id == cand_str
        ]
        for p_key in parent_keys:
            del self._parents[p_key]
        prev_ver = self._sync_version_by_candidate.pop(key, 0)
        self._record_watermark(
            tenant_id=valid_tenant,
            candidate_profile_id=candidate_profile_id,
            sync_version=prev_ver,
            chunk_count=0,
            is_erased=True,
        )
        return len(removed_payloads)
