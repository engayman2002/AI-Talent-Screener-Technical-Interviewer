"""Use Case interactors for Identity & Tenant Governance Bounded Context."""

from __future__ import annotations

from talent_screener.application.common.exceptions import (
    ResourceConflictError,
    ResourceNotFoundError,
)
from talent_screener.application.common.ports.outbound import (
    PiiVaultPort,
    TransactionalUnitOfWorkPort,
)
from talent_screener.application.document_intelligence.ports.outbound import (
    HybridVectorSearchPort,
)
from talent_screener.application.identity_governance.dtos import (
    CandidateErasureReceiptDTO,
    ConsentRecordResponseDTO,
    ExecuteCandidateErasureRequestDTO,
    ProvisionTenantAccountRequestDTO,
    ProvisionTenantAccountResponseDTO,
    RecordCandidateConsentRequestDTO,
    RevokeCandidateConsentRequestDTO,
)
from talent_screener.domain.contexts.identity_governance import (
    ConsentRecord,
    ConsentRecordRepositoryPort,
    QuotaPolicy,
    RetentionPolicy,
    TenantAccount,
    TenantAccountRepositoryPort,
)
from talent_screener.domain.shared.value_objects import TenantId


class ProvisionTenantAccountUseCase:
    """Provisions a new TenantAccount with validated QuotaPolicy and RetentionPolicy."""

    def __init__(
        self,
        *,
        tenant_repo: TenantAccountRepositoryPort,
        uow: TransactionalUnitOfWorkPort,
    ) -> None:
        self._tenant_repo = tenant_repo
        self._uow = uow

    async def execute(
        self, request: ProvisionTenantAccountRequestDTO
    ) -> ProvisionTenantAccountResponseDTO:
        existing_slug = self._tenant_repo.get_by_slug(request.organization_slug.strip().lower())
        if existing_slug is not None:
            raise ResourceConflictError(
                f"Organization slug {request.organization_slug!r} is already registered.",
                code="DUPLICATE_ORG_SLUG",
            )
        tenant_id = request.tenant_id if request.tenant_id is not None else TenantId.generate()
        tenant = TenantAccount(
            tenant_id=tenant_id,
            organization_slug=request.organization_slug,
            display_name=request.display_name,
            quota_policy=QuotaPolicy(
                max_concurrent_batches=request.max_concurrent_batches,
                max_files_per_batch=request.max_files_per_batch,
                max_monthly_interviews=request.max_monthly_interviews,
                max_tokens_per_interview=request.max_tokens_per_interview,
                subscription_tier=request.subscription_tier,
            ),
            retention_policy=RetentionPolicy(
                pii_retention_days=request.pii_retention_days,
                audit_log_retention_days=request.audit_log_retention_days,
                erasure_sla_hours=request.erasure_sla_hours,
            ),
        )
        async with self._uow:
            await self._uow.begin(tenant_id)
            self._tenant_repo.save(tenant)
            self._uow.register_aggregate(tenant)
            await self._uow.commit()

        return ProvisionTenantAccountResponseDTO(
            tenant_id=tenant.id,
            organization_slug=tenant.organization_slug,
            display_name=tenant.display_name,
            subscription_tier=tenant.quota_policy.subscription_tier,
            pii_retention_days=tenant.retention_policy.pii_retention_days,
            is_active=tenant.is_active,
        )


class RecordCandidateConsentUseCase:
    """Records candidate processing consent governed by the tenant's RetentionPolicy."""

    def __init__(
        self,
        *,
        tenant_repo: TenantAccountRepositoryPort,
        consent_repo: ConsentRecordRepositoryPort,
        uow: TransactionalUnitOfWorkPort,
    ) -> None:
        self._tenant_repo = tenant_repo
        self._consent_repo = consent_repo
        self._uow = uow

    async def execute(
        self, request: RecordCandidateConsentRequestDTO
    ) -> ConsentRecordResponseDTO:
        tenant = self._tenant_repo.get_by_id(request.tenant_id)
        retention = tenant.retention_policy if tenant is not None else RetentionPolicy()

        record = ConsentRecord(
            tenant_id=request.tenant_id,
            candidate_id=request.candidate_id,
            granted_scopes=request.granted_scopes,
            retention_policy=retention,
        )
        async with self._uow:
            await self._uow.begin(request.tenant_id)
            self._consent_repo.save(record)
            self._uow.register_aggregate(record)
            await self._uow.commit()

        return ConsentRecordResponseDTO(
            record_id=record.id,
            tenant_id=record.tenant_id,
            candidate_id=record.candidate_id,
            status=record.status,
            granted_scopes=record.granted_scopes,
            expires_at=record.expires_at,
        )


class RevokeCandidateConsentUseCase:
    """Withdraws active candidate processing consent."""

    def __init__(
        self,
        *,
        consent_repo: ConsentRecordRepositoryPort,
        uow: TransactionalUnitOfWorkPort,
    ) -> None:
        self._consent_repo = consent_repo
        self._uow = uow

    async def execute(
        self, request: RevokeCandidateConsentRequestDTO
    ) -> ConsentRecordResponseDTO:
        async with self._uow:
            await self._uow.begin(request.tenant_id)
            record = self._consent_repo.get_by_id(request.tenant_id, request.record_id)
            if record is None:
                raise ResourceNotFoundError(
                    f"ConsentRecord {request.record_id} not found.",
                    code="CONSENT_RECORD_NOT_FOUND",
                )
            record.withdraw()
            self._consent_repo.save(record)
            self._uow.register_aggregate(record)
            await self._uow.commit()

        return ConsentRecordResponseDTO(
            record_id=record.id,
            tenant_id=record.tenant_id,
            candidate_id=record.candidate_id,
            status=record.status,
            granted_scopes=record.granted_scopes,
            expires_at=record.expires_at,
        )


class ExecuteCandidateErasureUseCase:
    """Executes GDPR Right-to-Erasure: crypto-shreds PII vault and purges vector embeddings."""

    def __init__(
        self,
        *,
        consent_repo: ConsentRecordRepositoryPort,
        pii_vault: PiiVaultPort,
        vector_search: HybridVectorSearchPort,
        uow: TransactionalUnitOfWorkPort,
    ) -> None:
        self._consent_repo = consent_repo
        self._pii_vault = pii_vault
        self._vector_search = vector_search
        self._uow = uow

    async def execute(
        self, request: ExecuteCandidateErasureRequestDTO
    ) -> CandidateErasureReceiptDTO:
        record = self._consent_repo.get_by_candidate_id(
            request.tenant_id, request.candidate_id
        )
        if record is None:
            raise ResourceNotFoundError(
                f"ConsentRecord for candidate {request.candidate_id} not found.",
                code="CONSENT_RECORD_NOT_FOUND",
            )

        record.request_erasure(reason=request.reason)
        shred_receipt = await self._pii_vault.crypto_shred(
            tenant_id=request.tenant_id,
            candidate_id=request.candidate_id,
            reason=request.reason,
        )
        await self._vector_search.delete_candidate_vectors(
            tenant_id=request.tenant_id,
            candidate_profile_id=request.candidate_id,
        )
        record.mark_crypto_shredded(shredded_at=shred_receipt.shredded_at)

        async with self._uow:
            await self._uow.begin(request.tenant_id)
            self._consent_repo.save(record)
            self._uow.register_aggregate(record)
            await self._uow.commit()

        assert record.shredded_at is not None
        return CandidateErasureReceiptDTO(
            consent_record_id=record.id,
            candidate_id=record.candidate_id,
            status=record.status,
            shredded_at=record.shredded_at,
            vectors_purged=True,
        )
