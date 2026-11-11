"""Use Case interactors for Document Intelligence & Semantic Matching."""

from __future__ import annotations

from talent_screener.application.common.dtos import (
    IdempotencyStatus,
    RealtimeProgressEventDTO,
    TaskDispatchDTO,
)
from talent_screener.application.common.exceptions import (
    ExternalAdapterFailureError,
    IdempotencyConflictError,
    QuotaExceededError,
    ResourceConflictError,
    ResourceNotFoundError,
    UnauthorizedTenantAccessError,
)
from talent_screener.application.common.ports.outbound import (
    IdempotencyStorePort,
    ObjectStoragePort,
    ObservabilityTracerPort,
    PiiVaultPort,
    RealtimeProgressPublisherPort,
    TaskQueueDispatcherPort,
    TransactionalUnitOfWorkPort,
    VirusScannerPort,
)
from talent_screener.application.document_intelligence.dtos import (
    CompetencyQueryDescriptorDTO,
    DocumentExtractionCommandDTO,
    EpistemicSkillGapDTO,
    ExecuteHybridMatchingRequestDTO,
    GetScreeningBatchStatusQueryDTO,
    HybridMatchResponseDTO,
    InitiateScreeningBatchRequestDTO,
    InitiateScreeningBatchResponseDTO,
    PresignedUploadInstructionDTO,
    ProcessDocumentIngestionTaskRequestDTO,
    ProcessDocumentIngestionTaskResponseDTO,
    ScreeningBatchStatusDTO,
    TaskStatusReadDTO,
)
from talent_screener.application.document_intelligence.ports.outbound import (
    DocumentParserPort,
    HybridVectorSearchPort,
)
from talent_screener.domain.contexts.document_intelligence import (
    CandidateProfile,
    CandidateProfileRepositoryPort,
    ChunkEvidenceSample,
    CompetencyEvaluationSpec,
    DocumentIngestionTask,
    FileContentHash,
    HybridMatchingDomainService,
    MatchThresholds,
    PiiRedactionMap,
    ProjectComplexityRecord,
    RecencyDecayFactor,
    ScreeningBatch,
    ScreeningBatchRepositoryPort,
    SemanticMatchResultRepositoryPort,
    TaskIngestionStatus,
    WorkExperienceEntry,
)
from talent_screener.domain.contexts.identity_governance import TenantAccountRepositoryPort
from talent_screener.domain.contexts.recruitment import (
    JobRequisitionRepositoryPort,
    RequisitionStatus,
)
from talent_screener.domain.shared.exceptions import DomainError, InvariantViolationError
from talent_screener.domain.shared.value_objects import SHA256Hash


class InitiateScreeningBatchUseCase:
    """Enforces tenant quota & idempotency, creates ScreeningBatch, and queues tasks."""

    def __init__(
        self,
        *,
        tenant_repo: TenantAccountRepositoryPort,
        requisition_repo: JobRequisitionRepositoryPort,
        batch_repo: ScreeningBatchRepositoryPort,
        idempotency_store: IdempotencyStorePort,
        object_storage: ObjectStoragePort,
        task_dispatcher: TaskQueueDispatcherPort,
        uow: TransactionalUnitOfWorkPort,
    ) -> None:
        self._tenant_repo = tenant_repo
        self._requisition_repo = requisition_repo
        self._batch_repo = batch_repo
        self._idempotency_store = idempotency_store
        self._object_storage = object_storage
        self._task_dispatcher = task_dispatcher
        self._uow = uow

    def _compute_payload_hash(self, request: InitiateScreeningBatchRequestDTO) -> str:
        joined = "|".join(
            f"{f.filename}:{f.sha256_hex}:{f.file_size_bytes}:{f.mime_type}"
            for f in request.files
        )
        raw = f"{request.tenant_id}:{request.requisition_id}:{request.batch_label}:{joined}"
        return SHA256Hash.from_text(raw).value

    async def execute(
        self, request: InitiateScreeningBatchRequestDTO
    ) -> InitiateScreeningBatchResponseDTO:
        payload_hash = self._compute_payload_hash(request)
        existing_idem = await self._idempotency_store.check_and_reserve(
            tenant_id=request.tenant_id,
            operation="initiate_screening_batch",
            idempotency_key=request.idempotency_key,
            payload_hash=payload_hash,
        )
        if existing_idem is not None:
            if existing_idem.status is IdempotencyStatus.IN_PROGRESS:
                raise IdempotencyConflictError(
                    "ScreeningBatch initiation is already in progress for this idempotency key.",
                )
            if existing_idem.resource_id is not None:
                batch = self._batch_repo.get_by_id(request.tenant_id, existing_idem.resource_id)
                if batch is not None:
                    replay_list: list[PresignedUploadInstructionDTO] = []
                    for task in batch.tasks:
                        obj_key = f"quarantine/{batch.tenant_id}/{batch.id}/{task.id}"
                        url = await self._object_storage.generate_presigned_upload_url(
                            tenant_id=batch.tenant_id,
                            object_key=obj_key,
                            content_sha256=task.file_hash.sha256,
                            content_type=task.file_hash.mime_type,
                        )
                        replay_list.append(
                            PresignedUploadInstructionDTO(
                                task_id=task.id,
                                filename=task.filename,
                                object_key=obj_key,
                                presigned_upload_url=url,
                            )
                        )
                    return InitiateScreeningBatchResponseDTO(
                        batch_id=batch.id,
                        requisition_id=batch.requisition_id,
                        status=batch.status,
                        total_files=batch.total_files,
                        upload_instructions=tuple(replay_list),
                        replayed_from_idempotency=True,
                    )

        try:
            requisition = self._requisition_repo.get_by_id(
                request.tenant_id, request.requisition_id
            )
            if requisition is None:
                raise ResourceNotFoundError(
                    f"JobRequisition {request.requisition_id} not found.",
                    code="REQUISITION_NOT_FOUND",
                )
            if requisition.status is not RequisitionStatus.PUBLISHED:
                raise ResourceConflictError(
                    f"JobRequisition must be PUBLISHED to accept CV batches; "
                    f"got {requisition.status.value}.",
                    code="REQUISITION_NOT_PUBLISHED",
                )

            tenant = self._tenant_repo.get_by_id(request.tenant_id)
            if tenant is not None:
                try:
                    tenant.register_batch_started(len(request.files))
                except InvariantViolationError as exc:
                    if exc.code == "TENANT_SUSPENDED":
                        raise UnauthorizedTenantAccessError(exc.message, code=exc.code) from exc
                    raise QuotaExceededError(exc.message, code=exc.code) from exc

            tasks = [
                DocumentIngestionTask(
                    filename=file_spec.filename,
                    file_hash=FileContentHash(
                        sha256=SHA256Hash(file_spec.sha256_hex),
                        file_size_bytes=file_spec.file_size_bytes,
                        mime_type=file_spec.mime_type,
                    ),
                )
                for file_spec in request.files
            ]
            batch = ScreeningBatch(
                tenant_id=request.tenant_id,
                requisition_id=request.requisition_id,
                batch_label=request.batch_label,
                tasks=tasks,
            )
            batch.queue()

            async with self._uow:
                await self._uow.begin(request.tenant_id)
                if tenant is not None:
                    self._tenant_repo.save(tenant)
                    self._uow.register_aggregate(tenant)
                self._batch_repo.save(batch)
                self._uow.register_aggregate(batch)
                await self._uow.commit()

            instructions: list[PresignedUploadInstructionDTO] = []
            dispatch_items: list[TaskDispatchDTO] = []
            for task in batch.tasks:
                obj_key = f"quarantine/{request.tenant_id}/{batch.id}/{task.id}"
                upload_url = await self._object_storage.generate_presigned_upload_url(
                    tenant_id=request.tenant_id,
                    object_key=obj_key,
                    content_sha256=task.file_hash.sha256,
                    content_type=task.file_hash.mime_type,
                )
                instructions.append(
                    PresignedUploadInstructionDTO(
                        task_id=task.id,
                        filename=task.filename,
                        object_key=obj_key,
                        presigned_upload_url=upload_url,
                    )
                )
                dispatch_items.append(
                    TaskDispatchDTO(
                        tenant_id=request.tenant_id,
                        queue_name="pool_parse_pii",
                        task_name="process_document_ingestion_task",
                        correlation_id=request.correlation_id,
                        payload={
                            "batch_id": str(batch.id),
                            "task_id": str(task.id),
                            "object_key": obj_key,
                        },
                    )
                )

            await self._task_dispatcher.dispatch_batch(dispatch_items)
            await self._idempotency_store.mark_completed(
                tenant_id=request.tenant_id,
                operation="initiate_screening_batch",
                idempotency_key=request.idempotency_key,
                resource_id=batch.id,
            )
            return InitiateScreeningBatchResponseDTO(
                batch_id=batch.id,
                requisition_id=batch.requisition_id,
                status=batch.status,
                total_files=batch.total_files,
                upload_instructions=tuple(instructions),
                replayed_from_idempotency=False,
            )
        except Exception:
            await self._idempotency_store.release_reservation(
                tenant_id=request.tenant_id,
                operation="initiate_screening_batch",
                idempotency_key=request.idempotency_key,
            )
            raise


class ProcessDocumentIngestionTaskUseCase:
    """Orchestrates the 6-stage CV ingestion pipeline per document task."""

    def __init__(
        self,
        *,
        batch_repo: ScreeningBatchRepositoryPort,
        profile_repo: CandidateProfileRepositoryPort,
        object_storage: ObjectStoragePort,
        virus_scanner: VirusScannerPort,
        document_parser: DocumentParserPort,
        pii_vault: PiiVaultPort,
        vector_search: HybridVectorSearchPort,
        progress_publisher: RealtimeProgressPublisherPort,
        tracer: ObservabilityTracerPort,
        uow: TransactionalUnitOfWorkPort,
    ) -> None:
        self._batch_repo = batch_repo
        self._profile_repo = profile_repo
        self._object_storage = object_storage
        self._virus_scanner = virus_scanner
        self._document_parser = document_parser
        self._pii_vault = pii_vault
        self._vector_search = vector_search
        self._progress_publisher = progress_publisher
        self._tracer = tracer
        self._uow = uow

    async def execute(
        self, request: ProcessDocumentIngestionTaskRequestDTO
    ) -> ProcessDocumentIngestionTaskResponseDTO:
        with self._tracer.start_span(
            name="document_ingestion.process_task",
            tenant_id=request.tenant_id,
            correlation_id=request.correlation_id,
            attributes={"batch_id": str(request.batch_id), "task_id": str(request.task_id)},
        ):
            batch = self._batch_repo.get_by_id(request.tenant_id, request.batch_id)
            if batch is None:
                raise ResourceNotFoundError(
                    f"ScreeningBatch {request.batch_id} not found.",
                    code="BATCH_NOT_FOUND",
                )
            matching_tasks = [t for t in batch.tasks if t.id == request.task_id]
            if not matching_tasks:
                raise ResourceNotFoundError(
                    f"DocumentIngestionTask {request.task_id} not found.",
                    code="TASK_NOT_FOUND",
                )
            task = matching_tasks[0]

            raw_bytes = await self._object_storage.get_bytes(
                tenant_id=request.tenant_id,
                object_key=request.object_key,
            )
            batch.begin_task_stage(task.id, TaskIngestionStatus.SCANNING)
            scan_result = await self._virus_scanner.scan_bytes(
                tenant_id=request.tenant_id,
                filename=task.filename,
                payload=raw_bytes,
                expected_sha256=task.file_hash.sha256,
            )
            if not scan_result.is_clean:
                err_code = scan_result.reason_code or "MALWARE_DETECTED"
                async with self._uow:
                    await self._uow.begin(request.tenant_id)
                    batch.mark_task_failed(
                        task.id,
                        error_code=err_code,
                        error_message=f"Quarantined by virus/signature scanner ({err_code}).",
                        quarantined=True,
                    )
                    self._batch_repo.save(batch)
                    self._uow.register_aggregate(batch)
                    await self._uow.commit()
                await self._progress_publisher.publish_progress(
                    RealtimeProgressEventDTO(
                        tenant_id=request.tenant_id,
                        channel=f"batch:{batch.id}",
                        event_type="TASK_QUARANTINED",
                        payload={
                            "task_id": str(task.id),
                            "error_code": err_code,
                            "batch_status": batch.status.value,
                        },
                    )
                )
                return ProcessDocumentIngestionTaskResponseDTO(
                    batch_id=batch.id,
                    task_id=task.id,
                    task_status=task.status,
                    batch_status=batch.status,
                    error_code=err_code,
                )

            batch.begin_task_stage(task.id, TaskIngestionStatus.PARSING)
            try:
                dossier = await self._document_parser.parse_and_extract(
                    DocumentExtractionCommandDTO(
                        tenant_id=request.tenant_id,
                        batch_id=batch.id,
                        batch_file_id=task.id,
                        document_stream_uri=request.object_key,
                        mime_type=task.file_hash.mime_type,
                        expected_sha256=task.file_hash.sha256,
                        skill_taxonomy_version=request.skill_taxonomy_version,
                    )
                )

                batch.begin_task_stage(task.id, TaskIngestionStatus.REDACTING)
                vault_outcome = await self._pii_vault.tokenize_and_vault(
                    tenant_id=request.tenant_id,
                    candidate_profile_id=dossier.anonymized_candidate_token,
                    raw_text=dossier.raw_redacted_text,
                )
                merged_tokens = dict(dossier.pii_redaction_map.as_mapping())
                merged_tokens.update(vault_outcome.pii_redaction_map.as_mapping())
                redaction_map = PiiRedactionMap.from_mapping(
                    vault_reference_id=vault_outcome.vault_reference_id,
                    token_categories=merged_tokens,
                )

                batch.begin_task_stage(task.id, TaskIngestionStatus.EXTRACTING)
                work_entries = [
                    WorkExperienceEntry(
                        role_title=we.role_title,
                        normalized_role=we.normalized_role,
                        company_token=we.company_token,
                        start_year=we.start_year,
                        start_month=we.start_month,
                        end_year=we.end_year,
                        end_month=we.end_month,
                        is_current=we.is_current,
                        employment_type=we.employment_type,
                    )
                    for we in dossier.work_experiences
                ]
                project_records = [
                    ProjectComplexityRecord(
                        project_name=proj.project_name,
                        scale_tier=proj.scale_tier,
                        summary=proj.summary,
                        source_quote=proj.source_quote,
                        impact_metrics=proj.impact_metrics,
                    )
                    for proj in dossier.projects
                ]
                profile = CandidateProfile(
                    tenant_id=request.tenant_id,
                    anonymized_token=dossier.anonymized_candidate_token,
                    file_hash=task.file_hash,
                    pii_redaction_map=redaction_map,
                    work_experiences=work_entries,
                    projects=project_records,
                    skill_claims=dossier.skill_claims,
                    seniority_estimate=dossier.seniority_estimate,
                    source_document_text=vault_outcome.anonymized_text,
                    profile_id=dossier.anonymized_candidate_token,
                )

                batch.begin_task_stage(task.id, TaskIngestionStatus.INDEXING)
                await self._vector_search.index_candidate_dossier(
                    tenant_id=request.tenant_id,
                    requisition_id=batch.requisition_id,
                    candidate_profile_id=profile.id,
                    dossier=dossier,
                    sync_version=int(profile.version),
                )
            except (ExternalAdapterFailureError, DomainError) as exc:
                if isinstance(exc, ExternalAdapterFailureError) and exc.retryable:
                    raise
                err_code = exc.code[:64]
                async with self._uow:
                    await self._uow.begin(request.tenant_id)
                    batch.mark_task_failed(
                        task.id,
                        error_code=err_code,
                        error_message=str(exc)[:500],
                        quarantined=False,
                    )
                    self._batch_repo.save(batch)
                    self._uow.register_aggregate(batch)
                    await self._uow.commit()
                await self._progress_publisher.publish_progress(
                    RealtimeProgressEventDTO(
                        tenant_id=request.tenant_id,
                        channel=f"batch:{batch.id}",
                        event_type="TASK_FAILED",
                        payload={
                            "task_id": str(task.id),
                            "error_code": err_code,
                            "batch_status": batch.status.value,
                        },
                    )
                )
                raise

            async with self._uow:
                await self._uow.begin(request.tenant_id)
                self._profile_repo.save(profile)
                self._uow.register_aggregate(profile)
                batch.mark_task_succeeded(task.id, profile.id)
                self._batch_repo.save(batch)
                self._uow.register_aggregate(batch)
                await self._uow.commit()

            await self._progress_publisher.publish_progress(
                RealtimeProgressEventDTO(
                    tenant_id=request.tenant_id,
                    channel=f"batch:{batch.id}",
                    event_type="TASK_COMPLETED",
                    payload={
                        "task_id": str(task.id),
                        "candidate_profile_id": str(profile.id),
                        "completed_count": batch.completed_count,
                        "total_files": batch.total_files,
                        "batch_status": batch.status.value,
                    },
                )
            )
            return ProcessDocumentIngestionTaskResponseDTO(
                batch_id=batch.id,
                task_id=task.id,
                task_status=task.status,
                batch_status=batch.status,
                candidate_profile_id=profile.id,
            )


class ExecuteHybridMatchingUseCase:
    """Computes deterministic hybrid semantic match and epistemic skill gaps."""

    def __init__(
        self,
        *,
        requisition_repo: JobRequisitionRepositoryPort,
        profile_repo: CandidateProfileRepositoryPort,
        match_repo: SemanticMatchResultRepositoryPort,
        vector_search: HybridVectorSearchPort,
        uow: TransactionalUnitOfWorkPort,
    ) -> None:
        self._requisition_repo = requisition_repo
        self._profile_repo = profile_repo
        self._match_repo = match_repo
        self._vector_search = vector_search
        self._uow = uow
        self._matching_service = HybridMatchingDomainService()

    async def execute(self, request: ExecuteHybridMatchingRequestDTO) -> HybridMatchResponseDTO:
        requisition = self._requisition_repo.get_by_id(request.tenant_id, request.requisition_id)
        if requisition is None:
            raise ResourceNotFoundError(
                f"JobRequisition {request.requisition_id} not found.",
                code="REQUISITION_NOT_FOUND",
            )
        profile = self._profile_repo.get_by_id(request.tenant_id, request.candidate_profile_id)
        if profile is None:
            raise ResourceNotFoundError(
                f"CandidateProfile {request.candidate_profile_id} not found.",
                code="PROFILE_NOT_FOUND",
            )

        gatekeeper_by_skill = {
            rule.canonical_skill_id: rule for rule in requisition.gatekeepers.rules
        }
        queries: list[CompetencyQueryDescriptorDTO] = []
        specs: list[CompetencyEvaluationSpec] = []
        for criterion in requisition.criteria:
            gk_rule = gatekeeper_by_skill.get(criterion.canonical_skill_id)
            min_months = gk_rule.min_applied_months if gk_rule is not None else 0
            strict_gk = gk_rule.strict_disqualification if gk_rule is not None else True
            is_gk = criterion.is_gatekeeper or gk_rule is not None
            queries.append(
                CompetencyQueryDescriptorDTO(
                    competency_id=criterion.id,
                    canonical_skill_id=criterion.canonical_skill_id,
                    ideal_anchor_descriptor=criterion.ideal_anchor_descriptor or criterion.name,
                    weight=criterion.weight,
                    is_gatekeeper=is_gk,
                    min_applied_months=min_months,
                    strict_gatekeeper=strict_gk,
                )
            )
            specs.append(
                CompetencyEvaluationSpec(
                    competency_id=criterion.id,
                    canonical_skill_id=criterion.canonical_skill_id,
                    weight=criterion.weight,
                    target_depth=criterion.target_depth,
                    is_gatekeeper=is_gk,
                    min_applied_months=min_months,
                    strict_gatekeeper=strict_gk,
                )
            )

        bundle = await self._vector_search.search_competency_evidence(
            tenant_id=request.tenant_id,
            requisition_id=request.requisition_id,
            candidate_profile_id=request.candidate_profile_id,
            queries=queries,
        )
        evidence_map = {
            item.competency_id: [
                ChunkEvidenceSample(
                    dense_similarity=chunk.dense_similarity,
                    sparse_bm25_score=chunk.sparse_bm25_score,
                    recency_months_delta=chunk.recency_months_delta,
                    evidentiary_depth=chunk.evidentiary_depth,
                    verbatim_excerpt=chunk.verbatim_excerpt,
                )
                for chunk in item.retrieved_chunks
            ]
            for item in bundle.competency_evidence
        }

        thresholds = MatchThresholds(
            fast_track_threshold=requisition.tier_thresholds.fast_track_threshold,
            interview_threshold=requisition.tier_thresholds.interview_threshold,
            review_threshold=requisition.tier_thresholds.review_threshold,
        )
        match_result = self._matching_service.score_candidate(
            tenant_id=request.tenant_id,
            requisition_id=requisition.id,
            rubric_version=requisition.rubric_version,
            candidate_profile=profile,
            competencies=specs,
            evidence_by_competency=evidence_map,
            thresholds=thresholds,
            alpha_dense=request.alpha_dense,
            beta_sparse=request.beta_sparse,
            gamma_recency=request.gamma_recency,
            recency_decay=RecencyDecayFactor.from_half_life_years(request.half_life_years),
        )

        async with self._uow:
            await self._uow.begin(request.tenant_id)
            self._match_repo.save(match_result)
            self._uow.register_aggregate(match_result)
            await self._uow.commit()

        return HybridMatchResponseDTO(
            match_result_id=match_result.id,
            requisition_id=match_result.requisition_id,
            candidate_profile_id=match_result.candidate_profile_id,
            composite_score=match_result.composite_score,
            gatekeeper_passed=match_result.match_vector.gatekeeper_multiplier == 1.0,
            failed_gatekeepers=match_result.failed_gatekeepers,
            tier=match_result.tier,
            skill_gaps=tuple(
                EpistemicSkillGapDTO(
                    competency_id=gap.competency_id,
                    canonical_skill_id=gap.canonical_skill_id,
                    gap_type=gap.gap_type,
                    probe_priority=gap.probe_priority,
                    suspect_claim_summary=gap.suspect_claim_summary,
                )
                for gap in match_result.skill_gaps
            ),
        )


class GetScreeningBatchStatusUseCase:
    """Query interactor returning ScreeningBatch task counters and statuses."""

    def __init__(self, *, batch_repo: ScreeningBatchRepositoryPort) -> None:
        self._batch_repo = batch_repo

    async def execute(self, query: GetScreeningBatchStatusQueryDTO) -> ScreeningBatchStatusDTO:
        batch = self._batch_repo.get_by_id(query.tenant_id, query.batch_id)
        if batch is None:
            raise ResourceNotFoundError(
                f"ScreeningBatch {query.batch_id} not found.",
                code="BATCH_NOT_FOUND",
            )
        return ScreeningBatchStatusDTO(
            batch_id=batch.id,
            requisition_id=batch.requisition_id,
            batch_label=batch.batch_label,
            status=batch.status,
            total_files=batch.total_files,
            completed_count=batch.completed_count,
            failed_count=batch.failed_count,
            pending_count=batch.pending_count,
            tasks=tuple(
                TaskStatusReadDTO(
                    task_id=task.id,
                    filename=task.filename,
                    status=task.status,
                    candidate_profile_id=task.candidate_profile_id,
                    error_code=task.error_code,
                )
                for task in batch.tasks
            ),
        )
