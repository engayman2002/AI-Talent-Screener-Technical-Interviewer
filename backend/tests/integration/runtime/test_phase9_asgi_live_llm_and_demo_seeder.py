"""Phase 9 Verification: Live ASGI/WS Server, Demo Seeder, OpenAPI, & Live LLM Bridge."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import struct
import urllib.error
import urllib.request
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest

from talent_screener.composition_root import (
    build_runtime_container_from_env,
    seed_interactive_demo_environment,
)
from talent_screener.composition_root.main import (
    load_dotenv_into_environ,
    run_server_async,
)
from talent_screener.domain.contexts.evaluation_analytics import (
    HiringRecommendationBand,
)
from talent_screener.domain.contexts.live_interview.value_objects import (
    DifficultyLevel,
    PromptHash,
)
from talent_screener.domain.shared import SHA256Hash
from talent_screener.domain.shared.uuid7 import utc_now
from talent_screener.domain.shared.value_objects import TenantId, VersionNumber
from talent_screener.infrastructure.ai_adapters.langchain_parser.adapter import (
    LangChainDocumentParserAdapter,
)
from talent_screener.infrastructure.ai_adapters.langgraph_interview.nodes import (
    EvaluationInput,
    QuestionGeneratorInput,
    SocraticProbeInput,
)
from talent_screener.infrastructure.ai_adapters.langgraph_interview.state import (
    AdaptiveRoutingPlane,
    DialogueLedgerPlane,
    EvaluatedTurnRecord,
    InterviewGraphPhase,
    InterviewGraphState,
    RubricContextPlane,
    SessionMetadataPlane,
)
from talent_screener.infrastructure.ai_adapters.live_llm_provider import (
    LiveLlmConfig,
    OpenAiCompatibleJsonLlmClient,
    build_live_llm_client_from_env,
)
from talent_screener.presentation.server import AsyncStdlibHttpWsServer


def _http_call(
    url: str,
    *,
    method: str = "GET",
    headers: Mapping[str, str] | None = None,
    json_body: Mapping[str, object] | str | None = None,
) -> tuple[int, dict[str, str], str]:
    data: bytes | None = None
    req_headers = dict(headers or {})
    if isinstance(json_body, Mapping):
        data = json.dumps(json_body).encode("utf-8")
        req_headers.setdefault("Content-Type", "application/json")
    elif isinstance(json_body, str):
        data = json_body.encode("utf-8")
        req_headers.setdefault("Content-Type", "application/json")

    req = urllib.request.Request(url=url, data=data, method=method)
    for hk, hv in req_headers.items():
        req.add_header(hk, hv)
    try:
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            resp_headers = {k.lower(): v for k, v in resp.headers.items()}
            return resp.status, resp_headers, resp.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        resp_headers = {k.lower(): v for k, v in exc.headers.items()}
        return exc.code, resp_headers, exc.read().decode("utf-8")


def _encode_masked_client_ws_text(text: str) -> bytes:
    payload = text.encode("utf-8")
    mask = b"\x37\xfa\x21\x3d"
    head = bytearray([0x81])
    length = len(payload)
    if length < 126:
        head.append(0x80 | length)
    elif length <= 0xFFFF:
        head.append(0x80 | 126)
        head.extend(struct.pack("!H", length))
    else:
        head.append(0x80 | 127)
        head.extend(struct.pack("!Q", length))
    masked = bytes(payload[i] ^ mask[i % 4] for i in range(length))
    return bytes(head) + mask + masked


async def _read_server_ws_frame(reader: asyncio.StreamReader) -> tuple[int, bytes]:
    b1, b2 = await reader.readexactly(2)
    opcode = b1 & 0x0F
    length = b2 & 0x7F
    if length == 126:
        length = struct.unpack("!H", await reader.readexactly(2))[0]
    elif length == 127:
        length = struct.unpack("!Q", await reader.readexactly(8))[0]
    payload = await reader.readexactly(length) if length > 0 else b""
    return opcode, payload


@pytest.mark.integration
def test_phase9_live_llm_config_bridge_and_graceful_fallback() -> None:
    asyncio.run(_verify_live_llm_provider())


async def _verify_live_llm_provider() -> None:
    cfg_default = LiveLlmConfig.from_env({})
    assert cfg_default.provider == "deterministic"
    assert cfg_default.is_live_enabled is False

    cfg_groq = LiveLlmConfig.from_env({"GROQ_API_KEY": "gsk_test_123", "LLM_TIMEOUT_SECONDS": "12"})
    assert cfg_groq.provider == "groq"
    assert cfg_groq.model == "llama-3.3-70b-versatile"
    assert cfg_groq.is_live_enabled is True
    assert cfg_groq.timeout_seconds == 12.0

    cfg_gemini = LiveLlmConfig.from_env({"GEMINI_API_KEY": "gem_test_456"})
    assert cfg_gemini.provider == "gemini"
    assert cfg_gemini.model == "gemini-2.0-flash"

    cfg_custom = LiveLlmConfig.from_env(
        {
            "LLM_PROVIDER": "custom",
            "LLM_BASE_URL": "https://llm.internal.local/v1",
            "LLM_API_KEY": "sk-internal",
            "LLM_MODEL": "qwen-2.5-coder",
        }
    )
    assert cfg_custom.base_url == "https://llm.internal.local/v1/chat/completions"
    assert cfg_custom.model == "qwen-2.5-coder"

    # Offline mock HTTP transport testing structured extraction, Q-gen, eval, probe & synthesis
    def _mock_transport(
        url: str,
        headers: Mapping[str, str],
        body: Mapping[str, object],
        timeout: float,
    ) -> Mapping[str, object]:
        assert url.endswith("/chat/completions")
        assert headers["Authorization"].startswith("Bearer ")
        assert timeout > 0
        messages = body["messages"]
        assert isinstance(messages, list)
        sys_msg = str(messages[0]["content"])

        if "dossier extractor" in sys_msg:
            content = json.dumps(
                {
                    "work_experiences": [
                        {
                            "role_title": "Staff Systems Engineer",
                            "start_year": 2020,
                            "start_month": 1,
                            "is_current": True,
                        }
                    ],
                    "projects": [],
                    "skill_claims": [
                        {
                            "canonical_skill_id": "SKILL_LANG_PYTHON",
                            "raw_label": "Python",
                            "source_quote": "Built Python asyncio services",
                            "claimed_depth": 5,
                            "applied_months": 48,
                            "recency_months": 0,
                            "confidence": 0.96,
                        }
                    ],
                    "seniority_level": "STAFF",
                    "extraction_confidence": 0.95,
                }
            )
        elif "question" in sys_msg.lower():
            # Include markdown fences to verify stripping parser
            content = (
                '```json\n{"question_text": "How do you prevent head-of-line blocking in '
                'asyncio pools?"}\n```'
            )
        elif "evaluator" in sys_msg.lower():
            content = json.dumps(
                {
                    "accuracy_norm": 0.92,
                    "depth_norm": 0.88,
                    "tradeoff_norm": 0.84,
                    "specificity_norm": 0.90,
                    "relevance_norm": 0.91,
                    "stylometric_suspicion": 0.05,
                    "rationale": "Strong grasp of bounded semaphores and tail-latency tradeoffs.",
                }
            )
        elif "socratic" in sys_msg.lower():
            content = json.dumps(
                {
                    "probe_prompt": (
                        "What backpressure status code and circuit threshold do you enforce "
                        "under overload?"
                    )
                }
            )
        else:
            content = json.dumps(
                {
                    "summary_text": (
                        "Candidate demonstrated L4/L5 distributed Python architecture mastery "
                        "with clean authenticity."
                    )
                }
            )

        return {
            "choices": [{"message": {"content": content}}],
            "usage": {"total_tokens": 210},
        }

    client = build_live_llm_client_from_env(
        {"OPENAI_API_KEY": "sk-mock-key", "LLM_MODEL": "gpt-4o-mini"},
        http_transport=_mock_transport,
    )
    extracted = await client.ainvoke(
        {
            "redacted_text": "Built Python asyncio services at scale.",
            "delimited_prompt": (
                "<untrusted_cv_content>Built Python asyncio services.</untrusted_cv_content>"
            ),
        }
    )
    raw_claims = extracted["skill_claims"]
    assert isinstance(raw_claims, list) and len(raw_claims) == 1

    from talent_screener.application.live_interview.dtos import CompetencyBriefingTargetDTO

    comp = CompetencyBriefingTargetDTO(
        competency_id=UUID("01930000-0000-7000-8000-000000000010"),
        canonical_skill_id="SKILL_LANG_PYTHON",
        weight=0.5,
        target_depth=4,
        is_gatekeeper=True,
        probe_priority=0.8,
        ideal_anchor_descriptor="asyncio backpressure WAL outbox",
    )
    q_res = await client.generate_question(
        QuestionGeneratorInput(
            competency=comp,
            difficulty=DifficultyLevel.L4,
            project_highlights=("100k rps Python gateway",),
            turn_number=1,
        )
    )
    assert "head-of-line blocking" in q_res.question_text
    assert q_res.tokens_used == 210

    eval_res = await client.evaluate_response(
        EvaluationInput(
            competency=comp,
            difficulty=DifficultyLevel.L4,
            turn_number=1,
            active_question=q_res.question_text,
            xml_delimited_response=(
                "<untrusted_candidate_response>Use Semaphore and queues."
                "</untrusted_candidate_response>"
            ),
            project_highlights=("100k rps",),
        )
    )
    assert eval_res.accuracy_norm == pytest.approx(0.92)
    assert eval_res.turn_evaluation.technical_accuracy > 4.0

    probe_res = await client.generate_probe(
        SocraticProbeInput(
            competency=comp,
            difficulty=DifficultyLevel.L4,
            probe_index=1,
            strategy="SHALLOW_BUZZWORD_PROBE",
            target_concept="backpressure",
            sanitized_candidate_utterance="We used queues.",
            rationale="Missing failure thresholds.",
        )
    )
    assert "backpressure" in probe_res.probe_prompt

    tid = TenantId.generate()
    dummy_state = InterviewGraphState(
        metadata=SessionMetadataPlane(
            session_id=UUID("01930000-0000-7000-8000-000000000020"),
            tenant_id=tid,
            requisition_id=UUID("01930000-0000-7000-8000-000000000040"),
            candidate_profile_id=UUID("01930000-0000-7000-8000-000000000030"),
            rubric_version=VersionNumber(1),
            started_at=utc_now(),
            max_wall_clock_seconds=1800,
        ),
        rubric=RubricContextPlane(
            competency_queue=(comp,),
            candidate_project_highlights=("100k rps",),
        ),
        routing=AdaptiveRoutingPlane(
            phase=InterviewGraphPhase.COMPLETED,
            active_competency_id=comp.competency_id,
            current_difficulty=DifficultyLevel.L4,
            questions_asked_in_active_competency=1,
            probes_used_in_active_competency=0,
            mastery_by_competency={comp.competency_id: 4.2},
            rolling_authenticity_index=0.94,
            total_tokens_consumed=210,
            completed_turns=1,
            active_prompt_text=q_res.question_text,
            active_prompt_hash=PromptHash(sha256=SHA256Hash.from_bytes(b"q1")),
        ),
        ledger=DialogueLedgerPlane(),
        checkpoint_version="ckpt-1",
        fencing_token=1,
    )
    synth = await client.synthesize(
        state=dummy_state,
        evaluations=(
            EvaluatedTurnRecord(
                turn_number=1,
                competency_id=comp.competency_id,
                difficulty=DifficultyLevel.L4,
                evaluation=eval_res.turn_evaluation,
                weighted_turn_score=eval_res.weighted_turn_score,
                synthetic_likelihood_score=0.05,
                probe_index=0,
                rationale="Strong distributed architecture answer.",
            ),
        ),
    )
    assert "L4/L5 distributed Python" in synth.summary_text
    assert client.telemetry_counts["live_calls"] == 5

    # Verify automatic fallback when upstream network raises ConnectionError
    def _failing_transport(
        _u: str, _h: Mapping[str, str], _b: Mapping[str, object], _t: float
    ) -> Mapping[str, object]:
        raise ConnectionError("Simulated Groq 503 outage")

    resilient_client = OpenAiCompatibleJsonLlmClient(
        config=LiveLlmConfig(provider="groq", api_key="gsk_fail", model="llama-3.3-70b-versatile"),
        http_transport=_failing_transport,
    )
    fb_q = await resilient_client.generate_question(
        QuestionGeneratorInput(
            competency=comp,
            difficulty=DifficultyLevel.L3,
            project_highlights=(),
            turn_number=1,
        )
    )
    assert "SKILL_LANG_PYTHON" in fb_q.question_text
    assert resilient_client.telemetry_counts["fallback_calls"] == 1
    assert LangChainDocumentParserAdapter is not None


@pytest.mark.integration
def test_phase9_idempotent_seeder_live_http_sse_and_websocket(tmp_path: Path) -> None:
    asyncio.run(_verify_full_runtime_e2e(tmp_path))


async def _verify_full_runtime_e2e(tmp_path: Path) -> None:
    db_path = tmp_path / "phase9_runtime.db"
    env_file = tmp_path / ".env"
    env_file.write_text(
        f'DB_DSN="{db_path}"\nJWT_SECRET="phase9-fixed-signing-key-2026"\nLLM_PROVIDER=deterministic\n',
        encoding="utf-8",
    )
    prev_env = dict(os.environ)
    try:
        loaded = load_dotenv_into_environ((env_file,))
        assert loaded == env_file
        await run_server_async(host="127.0.0.1", port=0, seed_only=True)
    finally:
        os.environ.clear()
        os.environ.update(prev_env)

    # Boot second runtime instance pointing at the same seeded SQLite file -> must be idempotent!
    bundle = build_runtime_container_from_env(
        {
            "DB_DSN": str(db_path),
            "JWT_SECRET": "phase9-fixed-signing-key-2026",
            "DEMO_SEED_ENABLED": "true",
        }
    )
    try:
        manifest_first = await seed_interactive_demo_environment(bundle)
        manifest_second = await seed_interactive_demo_environment(bundle)
        assert manifest_second.tenant_id == manifest_first.tenant_id
        assert manifest_second.requisition_id == manifest_first.requisition_id
        assert manifest_second.batch_id == manifest_first.batch_id
        assert manifest_second.active_session_id == manifest_first.active_session_id
        assert len(manifest_second.shortlisted_candidate_ids) == 3
        assert manifest_second.rejected_candidate_id is not None
        assert len(manifest_second.report_ids) == 3

        asgi_app = bundle.create_asgi_app()

        # Verify ASGI lifespan protocol directly
        sent_lifespan: list[Mapping[str, Any]] = []
        ls_msgs = iter([{"type": "lifespan.startup"}, {"type": "lifespan.shutdown"}])

        async def _recv_ls() -> Mapping[str, Any]:
            return next(ls_msgs)

        async def _send_ls(msg: Mapping[str, Any]) -> None:
            sent_lifespan.append(msg)

        await asgi_app({"type": "lifespan"}, _recv_ls, _send_ls)
        assert [m["type"] for m in sent_lifespan] == [
            "lifespan.startup.complete",
            "lifespan.shutdown.complete",
        ]

        # Start live TCP server on ephemeral port (127.0.0.1:0)
        async with AsyncStdlibHttpWsServer(asgi_app, host="127.0.0.1", port=0) as srv:
            base = f"http://127.0.0.1:{srv.bound_port}"

            # 1. Landing, OpenAPI, Swagger Docs, CORS & Malformed JSON
            st, hdrs, body = await asyncio.to_thread(_http_call, f"{base}/")
            assert st == 200
            assert json.loads(body)["docs_url"] == "/docs"

            st, hdrs, _ = await asyncio.to_thread(
                _http_call,
                f"{base}/v1/ops/demo-manifest",
                method="OPTIONS",
                headers={"Origin": "http://localhost:3000"},
            )
            assert st == 204
            assert hdrs["access-control-allow-origin"] == "http://localhost:3000"

            st, _, oa_body = await asyncio.to_thread(_http_call, f"{base}/openapi.json")
            assert st == 200
            oa = json.loads(oa_body)
            assert oa["openapi"] == "3.1.0"
            assert "/v1/ops/demo-manifest" in oa["paths"]
            assert "/v1/interviews/{session_id}/ws" in oa["paths"]

            st, hdrs, docs_html = await asyncio.to_thread(_http_call, f"{base}/docs")
            assert st == 200
            assert "swagger-ui" in docs_html.lower()

            st, _, bad_body = await asyncio.to_thread(
                _http_call,
                f"{base}/v1/requisitions",
                method="POST",
                headers={"Authorization": f"Bearer {manifest_second.recruiter_jwt}"},
                json_body="{bad-json",
            )
            assert st == 400
            assert json.loads(bad_body)["error_code"] == "INVALID_JSON_BODY"

            # 2. Readiness, Demo Manifest, Metrics, Comparison Matrix, Recruiter Override
            st, _, ready_body = await asyncio.to_thread(_http_call, f"{base}/health/ready")
            assert st == 200
            assert json.loads(ready_body)["status"] == "ready"

            st, _, manifest_body = await asyncio.to_thread(
                _http_call, f"{base}/v1/ops/demo-manifest"
            )
            assert st == 200
            api_manifest = json.loads(manifest_body)["data"]
            assert api_manifest["requisition_id"] == manifest_second.requisition_id
            by_name = {item["display_name"]: item for item in api_manifest["candidate_summaries"]}
            assert 88.0 <= by_name["Elena Vance"]["match_score"] <= 100.0
            assert by_name["Elena Vance"]["tier"] == "FAST_TRACK"
            assert by_name["Elena Vance"]["recommendation_band"] == "STRONG_HIRE"
            assert 75.0 <= by_name["Marcus Chen"]["match_score"] <= 88.0
            assert by_name["Marcus Chen"]["recommendation_band"] == "HIRE"
            assert by_name["Devon Brooks"]["tier"] == "GATEKEEPER_REJECTED"
            assert by_name["Devon Brooks"]["match_score"] == 0.0

            recruiter_hdrs = {"Authorization": f"Bearer {api_manifest['recruiter_jwt']}"}

            st, _, metrics_body = await asyncio.to_thread(
                _http_call, f"{base}/v1/ops/metrics", headers=recruiter_hdrs
            )
            assert st == 200
            metrics_data = json.loads(metrics_body)["data"]
            assert metrics_data["finops_ledger"]["total_tokens"] > 0

            st, _, matrix_body = await asyncio.to_thread(
                _http_call,
                f"{base}/v1/requisitions/{api_manifest['requisition_id']}/comparison-matrix",
                method="POST",
                headers=recruiter_hdrs,
                json_body={"candidate_profile_ids": api_manifest["shortlisted_candidate_ids"]},
            )
            assert st == 200
            matrix_data = json.loads(matrix_body)["data"]
            assert len(matrix_data["candidates"]) == 3

            target_report_id = api_manifest["report_ids"][0]
            st, _, ovr_body = await asyncio.to_thread(
                _http_call,
                f"{base}/v1/evaluations/{target_report_id}/overrides",
                method="POST",
                headers={**recruiter_hdrs, "Idempotency-Key": "phase9-ovr-1"},
                json_body={
                    "new_band": HiringRecommendationBand.HIRE.value,
                    "justification": "Verified architecture depth during live panel review.",
                },
            )
            assert st == 200
            assert json.loads(ovr_body)["data"]["override_count"] == 1

            # 3. Simulate Live Batch & Verify SSE Streaming
            st, _, sim_body = await asyncio.to_thread(
                _http_call,
                f"{base}/v1/ops/demo-simulate-batch",
                method="POST",
            )
            assert st == 202
            sim_data = json.loads(sim_body)["data"]
            sim_batch_id = sim_data["batch_id"]

            # Allow background worker to emit completion events cleanly
            for _ in range(30):
                st_b, _, batch_raw = await asyncio.to_thread(
                    _http_call,
                    f"{base}/v1/screening-batches/{sim_batch_id}",
                    headers=recruiter_hdrs,
                )
                assert st_b == 200
                if json.loads(batch_raw)["data"]["status"] == "COMPLETED":
                    break
                await asyncio.sleep(0.05)

            st_sse, hdr_sse, sse_wire = await asyncio.to_thread(
                _http_call,
                f"{base}/v1/screening-batches/{sim_batch_id}/events",
                headers=recruiter_hdrs,
            )
            assert st_sse == 200
            assert "text/event-stream" in hdr_sse["content-type"]
            assert "data:" in sse_wire

            # 4. Real TCP RFC 6455 WebSocket Turn Roundtrip on Active IN_PROGRESS Session
            ws_path = (
                f"/v1/interviews/{api_manifest['active_session_id']}/ws"
                f"?ticket={api_manifest['candidate_jwt']}"
            )
            reader, writer = await asyncio.open_connection("127.0.0.1", srv.bound_port)
            try:
                sec_key = base64.b64encode(b"phase9-ws-key-16").decode("ascii")
                handshake = (
                    f"GET {ws_path} HTTP/1.1\r\n"
                    f"Host: 127.0.0.1:{srv.bound_port}\r\n"
                    "Upgrade: websocket\r\n"
                    "Connection: Upgrade\r\n"
                    f"Sec-WebSocket-Key: {sec_key}\r\n"
                    "Sec-WebSocket-Version: 13\r\n\r\n"
                ).encode("ascii")
                writer.write(handshake)
                await writer.drain()

                resp_head = (await reader.readuntil(b"\r\n\r\n")).decode("latin-1")
                assert "101 Switching Protocols" in resp_head
                expected_accept = base64.b64encode(
                    hashlib.sha1(
                        (sec_key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")
                    ).digest()
                ).decode("ascii")
                assert expected_accept in resp_head

                # Receive initial session.ready frame hydrated from Checkpointer
                op1, p1 = await _read_server_ws_frame(reader)
                assert op1 == 0x1
                ready_frame = json.loads(p1.decode("utf-8"))
                assert ready_frame["event_type"] == "session.ready"
                assert ready_frame["turn_number"] == 1

                # Send Turn 1 candidate utterance over RFC 6455 masked frame
                utterance_msg = json.dumps(
                    {
                        "event_type": "candidate.utterance",
                        "payload": {
                            "utterance": (
                                "We combine bounded asyncio Semaphores with SQL transactional "
                                "outbox tables and monotonic fencing tokens so concurrent workers "
                                "avoid split-brain writes with <40ms tail latency tradeoff."
                            ),
                            "remaining_seconds": 1550,
                            "telemetry": {
                                "focus_loss_count": 0,
                                "focus_loss_duration_ms": 0,
                                "external_paste_detected": False,
                                "paste_char_count": 0,
                                "keystroke_anomaly_detected": False,
                                "response_latency_ms": 1100,
                                "speech_cadence_wpm": 140.0,
                                "pause_entropy": 0.25,
                            },
                        },
                    }
                )
                writer.write(_encode_masked_client_ws_text(utterance_msg))
                await writer.drain()

                prompt_events = {"interviewer.question", "session.completed"}
                observed_events: list[dict[str, Any]] = []
                for _ in range(10):
                    op_n, p_n = await _read_server_ws_frame(reader)
                    assert op_n == 0x1
                    frame_n = json.loads(p_n.decode("utf-8"))
                    observed_events.append(frame_n)
                    if frame_n["event_type"] in prompt_events:
                        break

                event_types = [ev["event_type"] for ev in observed_events]
                assert "interviewer.thinking" in event_types
                assert "turn.evaluation_ack" in event_types
                prompt_frames = [
                    ev for ev in observed_events if ev["event_type"] in prompt_events
                ]
                assert len(prompt_frames) == 1
                next_frame = prompt_frames[0]
                assert next_frame["turn_number"] >= 2 or "question_text" in next_frame["payload"]
            finally:
                writer.close()

            # 5. Verify edge cases on ASGI app (non-object JSON 400, WS invalid route/token)
            st_arr, _, arr_body = await asyncio.to_thread(
                _http_call,
                f"{base}/v1/requisitions",
                method="POST",
                headers={"Authorization": f"Bearer {api_manifest['recruiter_jwt']}"},
                json_body="[1, 2, 3]",
            )
            assert st_arr == 400
            assert json.loads(arr_body)["error_code"] == "INVALID_JSON_BODY"

            ws_bad_route_msgs: list[Mapping[str, Any]] = []

            async def _recv_connect() -> Mapping[str, Any]:
                return {"type": "websocket.connect"}

            async def _send_ws(msg: Mapping[str, Any]) -> None:
                ws_bad_route_msgs.append(msg)

            await asgi_app(
                {"type": "websocket", "path": "/v1/unknown/ws", "query_string": b""},
                _recv_connect,
                _send_ws,
            )
            assert ws_bad_route_msgs[0]["code"] == 4404

            ws_bad_auth_msgs: list[Mapping[str, Any]] = []

            async def _send_ws_auth(msg: Mapping[str, Any]) -> None:
                ws_bad_auth_msgs.append(msg)

            await asgi_app(
                {
                    "type": "websocket",
                    "path": f"/v1/interviews/{api_manifest['active_session_id']}/ws",
                    "query_string": b"ticket=invalid.jwt.token",
                },
                _recv_connect,
                _send_ws_auth,
            )
            assert ws_bad_auth_msgs[0]["code"] == 4401

        # 6. Verify DEMO_SEED_ENABLED=false disables demo endpoints cleanly (HTTP 404)
        no_demo_bundle = build_runtime_container_from_env({"DEMO_SEED_ENABLED": "false"})
        try:
            async with AsyncStdlibHttpWsServer(
                no_demo_bundle.create_asgi_app(), host="127.0.0.1", port=0
            ) as srv2:
                base2 = f"http://127.0.0.1:{srv2.bound_port}"
                st_nd1, _, _ = await asyncio.to_thread(
                    _http_call, f"{base2}/v1/ops/demo-manifest"
                )
                st_nd2, _, _ = await asyncio.to_thread(
                    _http_call, f"{base2}/v1/ops/demo-simulate-batch", method="POST"
                )
                assert st_nd1 == 404
                assert st_nd2 == 404
        finally:
            no_demo_bundle.db_manager.close()
    finally:
        bundle.db_manager.close()
