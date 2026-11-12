"""End-to-end Phase 8 integration tests: WS Backplane, Graceful Drain, Autoscaler & Ops API."""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any
from uuid import UUID

import pytest

from talent_screener.application.common.exceptions import ExternalAdapterFailureError
from talent_screener.composition_root import build_production_container
from talent_screener.domain.contexts.evaluation_analytics import HiringRecommendationBand
from talent_screener.domain.contexts.identity_governance import ConsentScope
from talent_screener.domain.contexts.live_interview import DifficultyLevel
from talent_screener.domain.shared import SHA256Hash, TenantId, generate_uuid7
from talent_screener.infrastructure.messaging import (
    QueueDepthWorkerAutoscaler,
    WorkerPoolScalingPolicy,
    compute_ws_gateway_replicas,
)
from talent_screener.presentation.middleware import RbacScope
from talent_screener.presentation.schemas import HttpRequest


def _run[T](coro: Coroutine[Any, Any, T]) -> T:
    return asyncio.run(coro)


@pytest.mark.integration
def test_cross_node_ws_pubsub_and_graceful_drain_failover_with_l2_checkpoint() -> None:
    async def _run_test() -> None:
        bundle = build_production_container(ws_node_id="ws-node-1")
        node_a = bundle.ws_gateway
        node_b = bundle.create_peer_ws_gateway(node_id="ws-node-2")

        tenant_id = TenantId.generate()
        actor_id = generate_uuid7()
        hdr = {
            "Authorization": (
                "Bearer "
                + bundle.authenticator.issue_token(
                    tenant_id=tenant_id,
                    actor_id=actor_id,
                    scopes=(
                        RbacScope.RECRUITMENT_WRITE,
                        RbacScope.RECRUITMENT_READ,
                        RbacScope.SCREENING_WRITE,
                        RbacScope.SCREENING_READ,
                        RbacScope.INTERVIEW_CANDIDATE,
                        RbacScope.INTERVIEW_MANAGE,
                        RbacScope.EVALUATION_READ,
                        RbacScope.GOVERNANCE_ADMIN,
                    ),
                    ttl_seconds=3600,
                )
            )
        }

        # Seed tenant, published requisition, parsed candidate CV, match & consent via REST API
        t_res = await bundle.rest_router.handle(
            HttpRequest(
                method="POST",
                path="/v1/tenants",
                headers=hdr,
                body={
                    "tenant_id": str(tenant_id.value),
                    "organization_slug": "phase8-ha-org",
                    "display_name": "Phase 8 HA Enterprise",
                },
            )
        )
        assert t_res.status_code == 201

        req_res = await bundle.rest_router.handle(
            HttpRequest(
                method="POST",
                path="/v1/requisitions",
                headers={**hdr, "Idempotency-Key": "p8-req-1"},
                body={
                    "title": "Staff Systems Engineer",
                    "description": (
                        "Architect Python asyncio consensus pipelines and Raft clusters."
                    ),
                    "seniority_level": "STAFF",
                    "criteria": [
                        {
                            "name": "Python & Distributed Consensus",
                            "canonical_skill_id": "SKILL_LANG_PYTHON",
                            "weight": 1.0,
                            "target_depth": 4,
                            "is_gatekeeper": True,
                            "min_applied_months": 24,
                            "ideal_anchor_descriptor": (
                                "Raft quorum logs and monotonic fencing leases."
                            ),
                        }
                    ],
                },
            )
        )
        assert req_res.status_code == 201
        req_id = str(req_res.body["data"]["requisition_id"])  # type: ignore[index]

        pub_res = await bundle.rest_router.handle(
            HttpRequest(method="POST", path=f"/v1/requisitions/{req_id}/publish", headers=hdr)
        )
        assert pub_res.status_code == 200

        cv_bytes = (
            b"%PDF-1.7\nName: Alice HA\nEmail: alice.ha@example.com\n"
            b"EXPERIENCE\nStaff Engineer 2019-01 to Present\n"
            b"Architected Python asyncio consensus engines and Raft WAL replication at 50k QPS.\n"
        )
        batch_res = await bundle.rest_router.handle(
            HttpRequest(
                method="POST",
                path="/v1/screening-batches",
                headers={**hdr, "Idempotency-Key": "p8-batch-1"},
                body={
                    "requisition_id": req_id,
                    "batch_label": "p8-batch",
                    "files": [
                        {
                            "filename": "alice_ha.pdf",
                            "sha256_hex": SHA256Hash.from_bytes(cv_bytes).value,
                            "file_size_bytes": len(cv_bytes),
                            "mime_type": "application/pdf",
                        }
                    ],
                },
            )
        )
        assert batch_res.status_code == 202
        batch_data = batch_res.body["data"]  # type: ignore[index]
        assert isinstance(batch_data, dict)
        batch_id = str(batch_data["batch_id"])
        for slot in batch_data["upload_instructions"]:
            await bundle.adapters.object_storage.put_bytes(
                tenant_id=tenant_id,
                object_key=str(slot["object_key"]),
                payload=cv_bytes,
                content_type="application/pdf",
            )

        summary = await bundle.batch_coordinator.process_pending_tasks()
        assert summary.succeeded_count == 1

        st_res = await bundle.rest_router.handle(
            HttpRequest(method="GET", path=f"/v1/screening-batches/{batch_id}", headers=hdr)
        )
        assert st_res.status_code == 200
        cand_id = str(st_res.body["data"]["tasks"][0]["candidate_profile_id"])  # type: ignore[index]

        m_res = await bundle.rest_router.handle(
            HttpRequest(
                method="POST",
                path=f"/v1/requisitions/{req_id}/matches",
                headers=hdr,
                body={"candidate_profile_id": cand_id},
            )
        )
        assert m_res.status_code == 201

        c_res = await bundle.rest_router.handle(
            HttpRequest(
                method="POST",
                path="/v1/consents",
                headers=hdr,
                body={
                    "candidate_id": cand_id,
                    "granted_scopes": [
                        ConsentScope.AI_SCREENING.value,
                        ConsentScope.VOICE_INTERVIEW_RECORDING.value,
                        ConsentScope.INTEGRITY_TELEMETRY.value,
                    ],
                },
            )
        )
        assert c_res.status_code == 201

        init_res = await bundle.rest_router.handle(
            HttpRequest(
                method="POST",
                path="/v1/interviews",
                headers={**hdr, "Idempotency-Key": "p8-init-1"},
                body={
                    "requisition_id": req_id,
                    "candidate_profile_id": cand_id,
                    "initial_difficulty": 3,
                },
            )
        )
        assert init_res.status_code == 201
        init_data = init_res.body["data"]  # type: ignore[index]
        assert isinstance(init_data, dict)
        session_id = UUID(str(init_data["session_id"]))
        opening_q = str(init_data["opening_question"])
        ckpt_v1 = str(init_data["checkpoint_version"])

        cand_token = bundle.authenticator.issue_token(
            tenant_id=tenant_id,
            actor_id=UUID(cand_id),
            scopes=(RbacScope.INTERVIEW_CANDIDATE,),
            ttl_seconds=3600,
        )

        # 1. Candidate connects to Gateway Node A
        conn_a, ready_frames = await node_a.connect(
            session_id=session_id,
            ticket_token=cand_token,
            initial_question=opening_q,
            current_difficulty=DifficultyLevel.L3,
            checkpoint_version=ckpt_v1,
        )
        assert ready_frames[0].event_type == "session.ready"
        assert node_a.active_connection_count == 1

        # 2. Cross-node Pub/Sub: Peer Node B publishes advisory -> delivered to Node A socket
        delivered = await node_b.publish_cross_node_event(
            tenant_id=tenant_id,
            session_id=session_id,
            event_type="interviewer.advisory",
            payload={"notice": "Peer broadcast delivery test"},
        )
        assert delivered == 1
        inbox_a = node_a.get_backplane_inbox(session_id)
        assert len(inbox_a) == 1
        assert inbox_a[0].event_type == "interviewer.advisory"
        assert inbox_a[0].payload["notice"] == "Peer broadcast delivery test"

        # 3. Complete Turn 1 on Node A cleanly
        turn1_frames = await node_a.receive_frame(
            conn_a.connection_id,
            {
                "event_type": "candidate.utterance",
                "payload": {
                    "turn_number": 1,
                    "checkpoint_version": ckpt_v1,
                    "utterance": (
                        "Raft uses monotonic term numbers, WAL durability, quorum majorities, "
                        "and fencing tokens to guarantee linearizability tradeoffs at 4ms p99."
                    ),
                    "remaining_seconds": 1740,
                    "synthesize_voice": False,
                },
            },
        )
        turn1_events = [f.event_type for f in turn1_frames]
        assert "turn.evaluation_ack" in turn1_events
        assert "interviewer.question" in turn1_events
        snap_a = node_a.get_session_snapshot(session_id)
        assert snap_a is not None
        last_seq_before_drain = snap_a.server_seq
        ckpt_after_turn1 = snap_a.checkpoint_version
        turn2_q = snap_a.active_question

        # 4. Trigger Graceful Node Drain on Node A -> 503 readiness, notice emitted, lock released
        drain_summary = await bundle.ws_backplane.initiate_graceful_drain(
            node_id="ws-node-1", retry_after_ms=150
        )
        assert drain_summary.emitted_notices_count == 1
        assert session_id in drain_summary.drained_session_ids
        assert node_a.is_draining
        assert node_a.active_connection_count == 0

        # New connections to draining Node A are rejected with WS_NODE_DRAINING
        with pytest.raises(ExternalAdapterFailureError) as drain_exc:
            await node_a.connect(session_id=session_id, ticket_token=cand_token)
        assert drain_exc.value.code == "WS_NODE_DRAINING"

        # Readiness endpoint reflects drain -> 503 Service Unavailable
        ready_draining = await bundle.rest_router.handle(
            HttpRequest(method="GET", path="/health/ready")
        )
        assert ready_draining.status_code == 503
        assert ready_draining.headers["Retry-After"] == "5"

        # 5. Candidate seamlessly reconnects to Peer Node B -> hydrates L2 SQL checkpoint
        conn_b, sync_frames = await node_b.connect(
            session_id=session_id,
            ticket_token=cand_token,
            last_server_seq=last_seq_before_drain + 1,
        )
        assert sync_frames[0].event_type == "session.state_sync"
        assert sync_frames[0].payload["checkpoint_version"] == ckpt_after_turn1
        assert sync_frames[0].payload["active_question"] == turn2_q
        assert conn_b.lease.fencing_token >= 2

        # Execute Turn 2 on Node B seamlessly with zero lost progress
        turn2_frames = await node_b.receive_frame(
            conn_b.connection_id,
            {
                "event_type": "candidate.utterance",
                "payload": {
                    "turn_number": 2,
                    "checkpoint_version": ckpt_after_turn1,
                    "utterance": (
                        "Fencing tokens increment on every leader lease renewal so storage rejects "
                        "stale zombie writes even under 150ms asymmetric network partitions."
                    ),
                    "remaining_seconds": 1680,
                    "synthesize_voice": False,
                },
            },
        )
        assert "turn.evaluation_ack" in [f.event_type for f in turn2_frames]

    _run(_run_test())


@pytest.mark.unit
def test_worker_autoscaler_cooldown_and_stabilization_hysteresis() -> None:
    now = 1_000.0

    def _clock() -> float:
        return now

    scaler = QueueDepthWorkerAutoscaler(clock_fn=_clock)
    scaler.register_pool(
        WorkerPoolScalingPolicy(
            pool_name="pool_llm_extract",
            min_replicas=1,
            max_replicas=20,
            concurrency_per_pod=4,
            p95_processing_seconds=3.0,
            sla_target_seconds=60.0,
            scale_up_cooldown_seconds=15.0,
            scale_down_stabilization_seconds=60.0,
        )
    )

    # Burst of 400 queued CVs -> ceil((400*3)/(60*4)) = 5 replicas
    up_1 = scaler.evaluate_pool(
        pool_name="pool_llm_extract",
        queue_depth=400,
        max_token_dispatch_per_sec=10.0,
    )
    assert up_1.direction == "SCALE_UP"
    assert up_1.applied_replicas == 5

    # Immediate second spike within 15s cooldown -> held at 5
    now += 5.0
    up_held = scaler.evaluate_pool(
        pool_name="pool_llm_extract",
        queue_depth=1200,
        max_token_dispatch_per_sec=20.0,
    )
    assert up_held.direction == "HOLD_SCALE_UP_COOLDOWN"
    assert up_held.applied_replicas == 5

    # Brief queue drop -> scale-down stabilization window starts, does NOT flap downward
    now += 20.0
    down_hold_1 = scaler.evaluate_pool(pool_name="pool_llm_extract", queue_depth=0)
    assert down_hold_1.direction == "HOLD_SCALE_DOWN_STABILIZATION"
    assert down_hold_1.applied_replicas == 5

    now += 30.0
    down_hold_2 = scaler.evaluate_pool(pool_name="pool_llm_extract", queue_depth=0)
    assert down_hold_2.direction == "HOLD_SCALE_DOWN_STABILIZATION"
    assert down_hold_2.applied_replicas == 5

    # Full 60s stabilization elapses -> safely scales down to min_replicas=1
    now += 35.0
    down_done = scaler.evaluate_pool(pool_name="pool_llm_extract", queue_depth=0)
    assert down_done.direction == "SCALE_DOWN"
    assert down_done.applied_replicas == 1

    # WS Gateway capacity law (30% headroom + Multi-AZ minimum 3)
    assert compute_ws_gateway_replicas(active_sessions=10, invited_online=0) == 3
    assert compute_ws_gateway_replicas(active_sessions=420, invited_online=60) >= 5


@pytest.mark.integration
def test_operational_health_readiness_and_admin_metrics_endpoints() -> None:
    async def _run_test() -> None:
        bundle = build_production_container(ws_node_id="ops-node-1")
        tenant_id = TenantId.generate()
        actor_id = generate_uuid7()

        # Populate OTel, FinOps & Rubric Drift metrics
        with bundle.otel_tracer.start_span(
            name="eval.turn",
            tenant_id=tenant_id,
            correlation_id="ops-trace",
            attributes={"email": "hidden@example.com"},
        ):
            pass

        bundle.finops_controller.record_usage(
            tenant_id=tenant_id,
            session_id=generate_uuid7(),
            node_name="EvaluationNode",
            cached_input_tokens=500,
            uncached_input_tokens=100,
            output_tokens=80,
        )
        bundle.rubric_drift_monitor.evaluate_psi(
            competency_label="SQL",
            baseline_distribution=[0.2, 0.3, 0.3, 0.1, 0.1],
            current_distribution=[0.2, 0.3, 0.3, 0.1, 0.1],
        )
        bundle.rubric_drift_monitor.evaluate_kappa(
            label="benchmark",
            ai_verdicts=[HiringRecommendationBand.HIRE.value] * 5,
            reference_verdicts=[HiringRecommendationBand.HIRE.value] * 5,
        )

        # 1. Public /health/live & /health/ready work without JWT auth
        live = await bundle.rest_router.handle(HttpRequest(method="GET", path="/health/live"))
        assert live.status_code == 200
        assert live.body is not None and live.body["status"] == "alive"

        ready = await bundle.rest_router.handle(HttpRequest(method="GET", path="/health/ready"))
        assert ready.status_code == 200
        assert ready.body is not None and ready.body["status"] == "ready"

        # 2. /v1/ops/metrics strictly enforces governance:admin RBAC scope
        unauth_metrics = await bundle.rest_router.handle(
            HttpRequest(method="GET", path="/v1/ops/metrics")
        )
        assert unauth_metrics.status_code == 401

        non_admin_token = bundle.authenticator.issue_token(
            tenant_id=tenant_id,
            actor_id=actor_id,
            scopes=(RbacScope.RECRUITMENT_READ,),
        )
        forbidden_metrics = await bundle.rest_router.handle(
            HttpRequest(
                method="GET",
                path="/v1/ops/metrics",
                headers={"Authorization": f"Bearer {non_admin_token}"},
            )
        )
        assert forbidden_metrics.status_code == 403

        admin_token = bundle.authenticator.issue_token(
            tenant_id=tenant_id,
            actor_id=actor_id,
            scopes=(RbacScope.GOVERNANCE_ADMIN,),
        )
        ok_metrics = await bundle.rest_router.handle(
            HttpRequest(
                method="GET",
                path="/v1/ops/metrics",
                headers={"Authorization": f"Bearer {admin_token}"},
            )
        )
        assert ok_metrics.status_code == 200
        data = ok_metrics.body["data"]  # type: ignore[index]
        assert isinstance(data, dict)
        assert data["finops_ledger"]["total_tokens"] == 680
        assert data["latency_percentiles"]["total_spans"] >= 1
        assert "SQL" in data["rubric_drift"]["psi_reports"]

        # 3. Closing SQL DB degrades /health/ready cleanly to HTTP 503
        bundle.db_manager.close()
        unhealthy = await bundle.rest_router.handle(
            HttpRequest(method="GET", path="/health/ready")
        )
        assert unhealthy.status_code == 503
        assert unhealthy.headers["Retry-After"] == "5"

    _run(_run_test())
