"""Cross-Store Anti-Entropy Reconciliation Saga (SQL <-> Vector Store <-> PII Vault)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID

from talent_screener.application.common.exceptions import UnauthorizedTenantAccessError
from talent_screener.application.common.ports.outbound import PiiVaultPort
from talent_screener.application.document_intelligence.ports.outbound import (
    HybridVectorSearchPort,
)
from talent_screener.domain.contexts.identity_governance import ConsentStatus
from talent_screener.domain.shared.uuid7 import utc_now
from talent_screener.domain.shared.value_objects import TenantId
from talent_screener.infrastructure.persistence.relational.repositories import (
    SqlCandidateProfileRepository,
    SqlConsentRecordRepository,
)
from talent_screener.infrastructure.persistence.relational.schema import SqlDatabaseManager
from talent_screener.infrastructure.persistence.vector_store.outbox_relay import (
    lookup_requisition_for_profile,
    profile_to_dossier,
)
from talent_screener.infrastructure.persistence.vector_store.quantized_backend import (
    QuantizedHybridVectorStoreBackend,
)


class ReconciliationActionKind(StrEnum):
    """Concrete cross-store drift repair actions taken during anti-entropy sweep."""

    REINDEXED_MISSING = "REINDEXED_MISSING"
    REINDEXED_STALE = "REINDEXED_STALE"
    PURGED_ORPHAN_VECTOR = "PURGED_ORPHAN_VECTOR"
    COMPLETED_ERASURE_SHRED = "COMPLETED_ERASURE_SHRED"
    COMPLETED_ERASURE_VECTOR_PURGE = "COMPLETED_ERASURE_VECTOR_PURGE"


@dataclass(frozen=True, slots=True)
class ReconciliationActionItem:
    """Single audited healing action performed by CrossStoreAntiEntropyReconciler."""

    action: ReconciliationActionKind
    tenant_id: TenantId
    candidate_profile_id: UUID
    expected_version: int | None
    observed_version: int | None


@dataclass(frozen=True, slots=True)
class ReconciliationDriftReport:
    """Auditable summary of SQL/Vector/Vault reconciliation pass for a tenant."""

    tenant_id: TenantId
    checked_at: datetime
    missing_healed: int
    stale_healed: int
    orphans_purged: int
    erasures_completed: int
    actions: tuple[ReconciliationActionItem, ...]

    @property
    def total_drift_repaired(self) -> int:
        return (
            self.missing_healed
            + self.stale_healed
            + self.orphans_purged
            + self.erasures_completed
        )


class CrossStoreAntiEntropyReconciler:
    """Reconciles missing, stale, orphaned, and un-erased candidate projections across stores."""

    def __init__(
        self,
        *,
        db: SqlDatabaseManager,
        profile_repo: SqlCandidateProfileRepository,
        consent_repo: SqlConsentRecordRepository,
        vector_backend: QuantizedHybridVectorStoreBackend,
        vector_search: HybridVectorSearchPort,
        pii_vault: PiiVaultPort | None = None,
    ) -> None:
        self._db = db
        self._profile_repo = profile_repo
        self._consent_repo = consent_repo
        self._vector_backend = vector_backend
        self._vector_search = vector_search
        self._pii_vault = pii_vault

    def _erased_watermark_ids(self, tenant_id: TenantId) -> set[UUID]:
        rows = self._db.fetchall(
            "SELECT candidate_profile_id FROM vector_sync_watermarks "
            "WHERE tenant_id = ? AND is_erased = 1",
            (str(tenant_id.value),),
        )
        return {UUID(str(row["candidate_profile_id"])) for row in rows}

    async def reconcile_tenant(
        self,
        tenant_id: TenantId,
        *,
        default_requisition_id: UUID | None = None,
    ) -> ReconciliationDriftReport:
        """Detect and heal SQL<->Vector<->PII Vault drift strictly scoped to ``tenant_id``."""
        if not isinstance(tenant_id, TenantId) or not isinstance(tenant_id.value, UUID):
            raise UnauthorizedTenantAccessError(
                "Valid TenantId is required for anti-entropy reconciliation.",
                code="MISSING_TENANT_CONTEXT",
            )

        consents = self._consent_repo.list_by_tenant(tenant_id)
        profiles = self._profile_repo.list_by_tenant(tenant_id)
        indexed_versions = dict(self._vector_backend.list_indexed_candidates(tenant_id))
        erased_watermarks = self._erased_watermark_ids(tenant_id)

        erasure_candidate_ids: set[UUID] = set(erased_watermarks)
        for consent in consents:
            if consent.status in (ConsentStatus.ERASURE_REQUESTED, ConsentStatus.CRYPTO_SHREDDED):
                erasure_candidate_ids.add(consent.candidate_id)

        actions: list[ReconciliationActionItem] = []
        missing_healed = 0
        stale_healed = 0
        orphans_purged = 0
        erasures_completed = 0

        # 1. Enforce GDPR erasure completeness first across Vault + Vector Store + Consent status.
        for consent in consents:
            if consent.status not in (
                ConsentStatus.ERASURE_REQUESTED,
                ConsentStatus.CRYPTO_SHREDDED,
            ):
                continue
            cid = consent.candidate_id
            healed_erasure = False
            if self._pii_vault is not None:
                receipt = await self._pii_vault.crypto_shred(
                    tenant_id=tenant_id,
                    candidate_id=cid,
                    reason="Anti-entropy GDPR erasure reconciliation",
                )
                if not receipt.already_shredded:
                    healed_erasure = True
                    actions.append(
                        ReconciliationActionItem(
                            action=ReconciliationActionKind.COMPLETED_ERASURE_SHRED,
                            tenant_id=tenant_id,
                            candidate_profile_id=cid,
                            expected_version=None,
                            observed_version=None,
                        )
                    )
            if cid in indexed_versions:
                removed = await self._vector_backend.delete_by_candidate(
                    tenant_id=tenant_id,
                    candidate_profile_id=cid,
                )
                if removed > 0:
                    healed_erasure = True
                    actions.append(
                        ReconciliationActionItem(
                            action=ReconciliationActionKind.COMPLETED_ERASURE_VECTOR_PURGE,
                            tenant_id=tenant_id,
                            candidate_profile_id=cid,
                            expected_version=0,
                            observed_version=indexed_versions.get(cid),
                        )
                    )
                indexed_versions.pop(cid, None)
            if consent.status is ConsentStatus.ERASURE_REQUESTED:
                consent.mark_crypto_shredded()
                self._consent_repo.save(consent)
                healed_erasure = True
            if healed_erasure:
                erasures_completed += 1

        # Purge any residual vectors marked erased in watermarks.
        for cid in erasure_candidate_ids:
            if cid in indexed_versions:
                removed = await self._vector_backend.delete_by_candidate(
                    tenant_id=tenant_id,
                    candidate_profile_id=cid,
                )
                if removed > 0:
                    erasures_completed += 1
                    actions.append(
                        ReconciliationActionItem(
                            action=ReconciliationActionKind.COMPLETED_ERASURE_VECTOR_PURGE,
                            tenant_id=tenant_id,
                            candidate_profile_id=cid,
                            expected_version=0,
                            observed_version=indexed_versions.get(cid),
                        )
                    )
                indexed_versions.pop(cid, None)

        # 2. Heal missing or stale SQL -> Vector Store candidate projections.
        active_profile_ids: set[UUID] = set()
        for profile in profiles:
            if profile.id in erasure_candidate_ids:
                continue
            active_profile_ids.add(profile.id)
            expected_ver = int(profile.version)
            observed_ver = indexed_versions.get(profile.id)
            if observed_ver is not None and observed_ver >= expected_ver:
                continue

            req_id = (
                default_requisition_id
                or lookup_requisition_for_profile(
                    self._db,
                    tenant_id=tenant_id,
                    candidate_profile_id=profile.id,
                )
                or profile.id
            )
            await self._vector_search.index_candidate_dossier(
                tenant_id=tenant_id,
                requisition_id=req_id,
                candidate_profile_id=profile.id,
                dossier=profile_to_dossier(profile),
                sync_version=expected_ver,
            )
            if observed_ver is None:
                missing_healed += 1
                kind = ReconciliationActionKind.REINDEXED_MISSING
            else:
                stale_healed += 1
                kind = ReconciliationActionKind.REINDEXED_STALE
            actions.append(
                ReconciliationActionItem(
                    action=kind,
                    tenant_id=tenant_id,
                    candidate_profile_id=profile.id,
                    expected_version=expected_ver,
                    observed_version=observed_ver,
                )
            )

        # 3. Purge orphaned vectors absent from authoritative SQL candidate_profiles.
        for indexed_cid, observed_ver in list(indexed_versions.items()):
            if indexed_cid not in active_profile_ids and indexed_cid not in erasure_candidate_ids:
                await self._vector_backend.delete_by_candidate(
                    tenant_id=tenant_id,
                    candidate_profile_id=indexed_cid,
                )
                orphans_purged += 1
                actions.append(
                    ReconciliationActionItem(
                        action=ReconciliationActionKind.PURGED_ORPHAN_VECTOR,
                        tenant_id=tenant_id,
                        candidate_profile_id=indexed_cid,
                        expected_version=None,
                        observed_version=observed_ver,
                    )
                )

        return ReconciliationDriftReport(
            tenant_id=tenant_id,
            checked_at=utc_now(),
            missing_healed=missing_healed,
            stale_healed=stale_healed,
            orphans_purged=orphans_purged,
            erasures_completed=erasures_completed,
            actions=tuple(actions),
        )
