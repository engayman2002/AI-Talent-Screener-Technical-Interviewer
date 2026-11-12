"""Zero-Dependency ASGI 3.0 Application & Stdlib HTTP/SSE/RFC-6455 WebSocket Server.

Supports deployment under any standard ASGI server (Uvicorn/Hypercorn) AND self-hosted
pure-Python execution via ``AsyncStdlibHttpWsServer`` with zero external dependencies.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import struct
from collections.abc import Awaitable, Callable, Mapping
from http import HTTPStatus
from typing import Any
from urllib.parse import parse_qs

from talent_screener.domain.shared.uuid7 import generate_uuid7
from talent_screener.presentation.exceptions import (
    AuthenticationError,
    MissingRbacScopeError,
)
from talent_screener.presentation.openapi import build_openapi_spec, render_swagger_ui_html
from talent_screener.presentation.rest.router import RestApiRouter
from talent_screener.presentation.schemas import HttpRequest, ProblemDetailsResponse
from talent_screener.presentation.websocket.interview_ws_gateway import WebSocketInterviewGateway

AsgiReceiveFn = Callable[[], Awaitable[Mapping[str, Any]]]
AsgiSendFn = Callable[[Mapping[str, Any]], Awaitable[None]]
AsgiAppFn = Callable[[Mapping[str, Any], AsgiReceiveFn, AsgiSendFn], Awaitable[None]]

_WS_MAGIC_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"


def _cors_headers(
    origin: str | None, allowed_origins: tuple[str, ...]
) -> list[tuple[bytes, bytes]]:
    allow_origin = "*"
    if "*" not in allowed_origins and origin:
        if origin in allowed_origins:
            allow_origin = origin
        elif allowed_origins:
            allow_origin = allowed_origins[0]
    elif origin and "*" in allowed_origins:
        allow_origin = origin

    return [
        (b"access-control-allow-origin", allow_origin.encode("latin-1")),
        (b"access-control-allow-methods", b"GET, POST, PUT, PATCH, DELETE, OPTIONS"),
        (
            b"access-control-allow-headers",
            b"Authorization, Content-Type, Idempotency-Key, Last-Event-ID, "
            b"X-Request-ID, X-Trace-ID",
        ),
        (
            b"access-control-expose-headers",
            b"X-Request-ID, X-Trace-ID, X-RateLimit-Limit, X-RateLimit-Remaining, "
            b"X-RateLimit-Reset",
        ),
        (b"access-control-max-age", b"86400"),
    ]


def _decode_asgi_headers(raw_headers: Any) -> dict[str, str]:
    decoded: dict[str, str] = {}
    if not isinstance(raw_headers, list | tuple):
        return decoded
    for item in raw_headers:
        if isinstance(item, list | tuple) and len(item) == 2:
            k_raw, v_raw = item
            k = k_raw.decode("latin-1") if isinstance(k_raw, bytes | bytearray) else str(k_raw)
            v = v_raw.decode("latin-1") if isinstance(v_raw, bytes | bytearray) else str(v_raw)
            decoded[k] = v
    return decoded


def _header_lookup(headers: Mapping[str, str], target: str) -> str | None:
    lowered = target.lower()
    for key, value in headers.items():
        if key.lower() == lowered:
            return value
    return None


def _decode_query_params(raw_qs: Any) -> dict[str, str]:
    if isinstance(raw_qs, bytes | bytearray):
        qs_str = raw_qs.decode("utf-8", errors="replace")
    elif isinstance(raw_qs, str):
        qs_str = raw_qs
    else:
        return {}
    parsed = parse_qs(qs_str, keep_blank_values=True)
    return {k: v[-1] if v else "" for k, v in parsed.items()}


def create_asgi_app(
    *,
    rest_router: RestApiRouter,
    ws_gateway: WebSocketInterviewGateway,
    cors_allowed_origins: tuple[str, ...] = ("*",),
    app_title: str = "AI Talent Screener & Technical Interviewer API",
    app_version: str = "1.0.0",
) -> AsgiAppFn:
    """Create a standards-compliant ASGI 3.0 application wrapping REST, SSE, OpenAPI, & WS."""
    openapi_document = build_openapi_spec(
        rest_router.routes,
        title=app_title,
        version=app_version,
    )
    openapi_bytes = json.dumps(openapi_document, indent=2).encode("utf-8")
    swagger_bytes = render_swagger_ui_html(
        openapi_url="/openapi.json",
        title=app_title,
    ).encode("utf-8")

    async def _send_json_bytes(
        send: AsgiSendFn,
        status: int,
        payload: bytes,
        extra_headers: list[tuple[bytes, bytes]],
        content_type: bytes = b"application/json; charset=utf-8",
    ) -> None:
        headers: list[tuple[bytes, bytes]] = [
            (b"content-type", content_type),
            (b"content-length", str(len(payload)).encode("ascii")),
            *extra_headers,
        ]
        await send({"type": "http.response.start", "status": status, "headers": headers})
        await send({"type": "http.response.body", "body": payload, "more_body": False})

    async def _handle_lifespan(receive: AsgiReceiveFn, send: AsgiSendFn) -> None:
        while True:
            event = await receive()
            ev_type = event.get("type")
            if ev_type == "lifespan.startup":
                await send({"type": "lifespan.startup.complete"})
            elif ev_type == "lifespan.shutdown":
                await send({"type": "lifespan.shutdown.complete"})
                return

    async def _handle_http(
        scope: Mapping[str, Any], receive: AsgiReceiveFn, send: AsgiSendFn
    ) -> None:
        method = str(scope.get("method", "GET")).upper()
        raw_path = str(scope.get("path", "/"))
        headers = _decode_asgi_headers(scope.get("headers", ()))
        origin = _header_lookup(headers, "Origin")
        cors = _cors_headers(origin, cors_allowed_origins)

        if method == "OPTIONS":
            await send(
                {
                    "type": "http.response.start",
                    "status": 204,
                    "headers": [(b"content-length", b"0"), *cors],
                }
            )
            await send({"type": "http.response.body", "body": b"", "more_body": False})
            return

        normalized_meta_path = raw_path.rstrip("/") or "/"
        if normalized_meta_path.startswith("/api/"):
            normalized_meta_path = normalized_meta_path.removeprefix("/api") or "/"

        if method == "GET" and normalized_meta_path == "/":
            landing = json.dumps(
                {
                    "service": app_title,
                    "version": app_version,
                    "docs_url": "/docs",
                    "openapi_url": "/openapi.json",
                    "health_url": "/health/ready",
                    "demo_manifest_url": "/v1/ops/demo-manifest",
                }
            ).encode("utf-8")
            await _send_json_bytes(send, 200, landing, cors)
            return

        if method == "GET" and normalized_meta_path == "/openapi.json":
            await _send_json_bytes(send, 200, openapi_bytes, cors)
            return

        if method == "GET" and normalized_meta_path == "/docs":
            await _send_json_bytes(
                send,
                200,
                swagger_bytes,
                cors,
                content_type=b"text/html; charset=utf-8",
            )
            return

        chunks: list[bytes] = []
        while True:
            msg = await receive()
            if msg.get("type") == "http.disconnect":
                return
            chunk = msg.get("body", b"")
            if isinstance(chunk, bytes | bytearray) and chunk:
                chunks.append(bytes(chunk))
            if not msg.get("more_body", False):
                break

        raw_body = b"".join(chunks)
        parsed_body: Mapping[str, object] | None = None
        if raw_body.strip():
            try:
                decoded_obj = json.loads(raw_body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                problem = ProblemDetailsResponse(
                    type="urn:ai-screener:error:validation",
                    title="Invalid JSON Body",
                    status=400,
                    detail=f"Malformed JSON request body: {exc}",
                    instance=raw_path,
                    error_code="INVALID_JSON_BODY",
                    trace_id=str(generate_uuid7()),
                )
                err_bytes = json.dumps(problem.to_dict()).encode("utf-8")
                await _send_json_bytes(
                    send,
                    400,
                    err_bytes,
                    cors,
                    content_type=b"application/problem+json; charset=utf-8",
                )
                return

            if not isinstance(decoded_obj, Mapping):
                problem = ProblemDetailsResponse(
                    type="urn:ai-screener:error:validation",
                    title="Invalid JSON Object",
                    status=400,
                    detail="JSON request body must be an object.",
                    instance=raw_path,
                    error_code="INVALID_JSON_BODY",
                    trace_id=str(generate_uuid7()),
                )
                err_bytes = json.dumps(problem.to_dict()).encode("utf-8")
                await _send_json_bytes(
                    send,
                    400,
                    err_bytes,
                    cors,
                    content_type=b"application/problem+json; charset=utf-8",
                )
                return
            parsed_body = decoded_obj

        query_params = _decode_query_params(scope.get("query_string", b""))
        http_req = HttpRequest(
            method=method,
            path=raw_path,
            headers=headers,
            query_params=query_params,
            body=parsed_body,
        )
        http_res = await rest_router.handle(http_req)

        if http_res.raw_text is not None:
            res_bytes = http_res.raw_text.encode("utf-8")
            ctype = f"{http_res.content_type}; charset=utf-8"
        elif http_res.body is not None:
            res_bytes = json.dumps(http_res.body).encode("utf-8")
            ctype = (
                "application/problem+json; charset=utf-8"
                if http_res.status_code >= 400 and "error_code" in http_res.body
                else f"{http_res.content_type}; charset=utf-8"
            )
        else:
            res_bytes = b""
            ctype = f"{http_res.content_type}; charset=utf-8"

        out_headers: list[tuple[bytes, bytes]] = [
            (b"content-type", ctype.encode("latin-1")),
            (b"content-length", str(len(res_bytes)).encode("ascii")),
            *cors,
        ]
        for hk, hv in http_res.headers.items():
            if hk.lower() not in {"content-type", "content-length"}:
                out_headers.append((hk.lower().encode("latin-1"), str(hv).encode("latin-1")))

        await send(
            {
                "type": "http.response.start",
                "status": http_res.status_code,
                "headers": out_headers,
            }
        )
        await send({"type": "http.response.body", "body": res_bytes, "more_body": False})

    async def _handle_websocket(
        scope: Mapping[str, Any], receive: AsgiReceiveFn, send: AsgiSendFn
    ) -> None:
        raw_path = str(scope.get("path", "/"))
        if raw_path.startswith("/api/"):
            raw_path = raw_path.removeprefix("/api")

        session_id = ws_gateway.match_ws_path(raw_path)
        if session_id is None:
            await send({"type": "websocket.close", "code": 4404, "reason": "Unknown WS route"})
            return

        headers = _decode_asgi_headers(scope.get("headers", ()))
        query = _decode_query_params(scope.get("query_string", b""))
        auth_header = _header_lookup(headers, "Authorization")
        ticket = query.get("ticket") or query.get("token")
        last_seq_raw = query.get("last_server_seq")
        last_server_seq: int | None = None
        if last_seq_raw:
            try:
                last_server_seq = int(last_seq_raw)
            except ValueError:
                last_server_seq = None

        connect_event = await receive()
        if connect_event.get("type") != "websocket.connect":
            await send({"type": "websocket.close", "code": 1002})
            return

        try:
            conn_ctx, initial_frames = await ws_gateway.connect(
                session_id=session_id,
                authorization_header=auth_header,
                ticket_token=ticket,
                last_server_seq=last_server_seq,
            )
        except AuthenticationError:
            await send({"type": "websocket.close", "code": 4401, "reason": "Unauthorized"})
            return
        except MissingRbacScopeError:
            await send({"type": "websocket.close", "code": 4403, "reason": "Forbidden"})
            return
        except Exception as exc:
            await send(
                {
                    "type": "websocket.close",
                    "code": 4409,
                    "reason": str(exc)[:110] or "Handshake rejected",
                }
            )
            return

        await send({"type": "websocket.accept"})
        try:
            for frame in initial_frames:
                await send({"type": "websocket.send", "text": json.dumps(frame.to_dict())})

            while True:
                msg = await receive()
                msg_type = msg.get("type")
                if msg_type == "websocket.disconnect":
                    break
                if msg_type == "websocket.receive":
                    raw_text = msg.get("text")
                    if raw_text is None and isinstance(msg.get("bytes"), bytes | bytearray):
                        raw_text = bytes(msg["bytes"]).decode("utf-8", errors="replace")
                    if not isinstance(raw_text, str):
                        continue
                    try:
                        emitted = await ws_gateway.receive_frame(conn_ctx.connection_id, raw_text)
                        for frame in emitted:
                            await send(
                                {"type": "websocket.send", "text": json.dumps(frame.to_dict())}
                            )
                    except Exception as exc:
                        err_payload = {
                            "event_type": "error",
                            "session_id": str(session_id),
                            "seq": 0,
                            "turn_number": 0,
                            "payload": {"detail": str(exc), "error_code": "WS_FRAME_ERROR"},
                            "trace_id": str(generate_uuid7()),
                        }
                        await send({"type": "websocket.send", "text": json.dumps(err_payload)})
        finally:
            await ws_gateway.disconnect(conn_ctx.connection_id)

    async def asgi_app(
        scope: Mapping[str, Any], receive: AsgiReceiveFn, send: AsgiSendFn
    ) -> None:
        scope_type = str(scope.get("type", ""))
        if scope_type == "lifespan":
            await _handle_lifespan(receive, send)
        elif scope_type == "websocket":
            await _handle_websocket(scope, receive, send)
        elif scope_type == "http":
            await _handle_http(scope, receive, send)
        else:
            raise NotImplementedError(f"Unsupported ASGI scope type: {scope_type!r}")

    return asgi_app


def _encode_ws_frame(opcode: int, payload: bytes) -> bytes:
    header = bytearray()
    header.append(0x80 | (opcode & 0x0F))
    length = len(payload)
    if length < 126:
        header.append(length)
    elif length <= 0xFFFF:
        header.append(126)
        header.extend(struct.pack("!H", length))
    else:
        header.append(127)
        header.extend(struct.pack("!Q", length))
    return bytes(header) + payload


async def _read_ws_frame(reader: asyncio.StreamReader) -> tuple[int, bytes] | None:
    try:
        head = await reader.readexactly(2)
    except (asyncio.IncompleteReadError, ConnectionError):
        return None
    b1, b2 = head[0], head[1]
    opcode = b1 & 0x0F
    masked = (b2 & 0x80) != 0
    length = b2 & 0x7F
    if length == 126:
        ext = await reader.readexactly(2)
        length = struct.unpack("!H", ext)[0]
    elif length == 127:
        ext = await reader.readexactly(8)
        length = struct.unpack("!Q", ext)[0]
    mask_key = await reader.readexactly(4) if masked else b""
    payload = bytearray(await reader.readexactly(length)) if length > 0 else bytearray()
    if masked:
        for idx in range(length):
            payload[idx] ^= mask_key[idx % 4]
    return opcode, bytes(payload)


class AsyncStdlibHttpWsServer:
    """Zero-dependency asyncio HTTP/1.1 + SSE + RFC-6455 WebSocket runner for ASGI apps."""

    def __init__(
        self,
        asgi_app: AsgiAppFn,
        *,
        host: str = "127.0.0.1",
        port: int = 8000,
    ) -> None:
        self._app = asgi_app
        self._host = host
        self._port = port
        self._server: asyncio.AbstractServer | None = None
        self._bound_port: int = port

    @property
    def bound_port(self) -> int:
        return self._bound_port

    async def start(self) -> None:
        """Bind socket and begin serving HTTP, SSE, and WebSocket upgrades."""
        if self._server is not None:
            return
        self._server = await asyncio.start_server(self._handle_client, self._host, self._port)
        sockets = self._server.sockets
        if sockets:
            self._bound_port = int(sockets[0].getsockname()[1])

    async def stop(self) -> None:
        """Close the listening server cleanly."""
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def serve_forever(self) -> None:
        """Start server if needed and run until cancelled."""
        await self.start()
        assert self._server is not None
        async with self._server:
            await self._server.serve_forever()

    async def __aenter__(self) -> AsyncStdlibHttpWsServer:
        await self.start()
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.stop()

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        try:
            raw_head = await reader.readuntil(b"\r\n\r\n")
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, ConnectionError):
            writer.close()
            return

        head_text = raw_head.decode("latin-1")
        lines = head_text.split("\r\n")
        request_line = lines[0].split(" ")
        if len(request_line) < 2:
            writer.close()
            return
        method = request_line[0].upper()
        target = request_line[1]
        raw_path, _, raw_qs = target.partition("?")

        headers_list: list[tuple[bytes, bytes]] = []
        headers_dict: dict[str, str] = {}
        content_length = 0
        for hdr_line in lines[1:]:
            if not hdr_line or ":" not in hdr_line:
                continue
            k, v = hdr_line.split(":", 1)
            k_clean = k.strip()
            v_clean = v.strip()
            headers_list.append((k_clean.lower().encode("latin-1"), v_clean.encode("latin-1")))
            headers_dict[k_clean.lower()] = v_clean
            if k_clean.lower() == "content-length":
                try:
                    content_length = max(0, int(v_clean))
                except ValueError:
                    content_length = 0

        if headers_dict.get("upgrade", "").lower() == "websocket":
            await self._handle_ws_upgrade(
                raw_path=raw_path,
                raw_qs=raw_qs,
                headers_list=headers_list,
                headers_dict=headers_dict,
                reader=reader,
                writer=writer,
            )
            return

        body_bytes = b""
        if content_length > 0:
            try:
                body_bytes = await reader.readexactly(content_length)
            except asyncio.IncompleteReadError:
                writer.close()
                return

        await self._handle_http_request(
            method=method,
            raw_path=raw_path,
            raw_qs=raw_qs,
            headers_list=headers_list,
            body_bytes=body_bytes,
            writer=writer,
        )

    async def _handle_http_request(
        self,
        *,
        method: str,
        raw_path: str,
        raw_qs: str,
        headers_list: list[tuple[bytes, bytes]],
        body_bytes: bytes,
        writer: asyncio.StreamWriter,
    ) -> None:
        scope: dict[str, Any] = {
            "type": "http",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": raw_path,
            "query_string": raw_qs.encode("latin-1"),
            "headers": headers_list,
        }
        consumed = False

        async def _receive() -> Mapping[str, Any]:
            nonlocal consumed
            if not consumed:
                consumed = True
                return {"type": "http.request", "body": body_bytes, "more_body": False}
            return {"type": "http.disconnect"}

        response_status = 500
        response_headers: list[tuple[bytes, bytes]] = []
        body_chunks: list[bytes] = []

        async def _send(msg: Mapping[str, Any]) -> None:
            nonlocal response_status, response_headers
            if msg.get("type") == "http.response.start":
                response_status = int(msg.get("status", 200))
                response_headers = list(msg.get("headers", []))
            elif msg.get("type") == "http.response.body":
                chunk = msg.get("body", b"")
                if isinstance(chunk, bytes | bytearray) and chunk:
                    body_chunks.append(bytes(chunk))

        try:
            await self._app(scope, _receive, _send)
            payload = b"".join(body_chunks)
            try:
                reason = HTTPStatus(response_status).phrase
            except ValueError:
                reason = "OK"
            header_lines = [f"HTTP/1.1 {response_status} {reason}".encode("latin-1")]
            seen_cl = False
            for hk, hv in response_headers:
                if hk.lower() == b"content-length":
                    seen_cl = True
                if hk.lower() == b"connection":
                    continue
                header_lines.append(hk + b": " + hv)
            if not seen_cl:
                header_lines.append(f"content-length: {len(payload)}".encode("latin-1"))
            header_lines.append(b"connection: close")
            writer.write(b"\r\n".join(header_lines) + b"\r\n\r\n" + payload)
            await writer.drain()
        finally:
            writer.close()

    async def _handle_ws_upgrade(
        self,
        *,
        raw_path: str,
        raw_qs: str,
        headers_list: list[tuple[bytes, bytes]],
        headers_dict: dict[str, str],
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        ws_key = headers_dict.get("sec-websocket-key", "").strip()
        if not ws_key:
            writer.write(b"HTTP/1.1 400 Bad Request\r\nConnection: close\r\n\r\n")
            await writer.drain()
            writer.close()
            return

        accept_token = base64.b64encode(
            hashlib.sha1((ws_key + _WS_MAGIC_GUID).encode("ascii")).digest()
        ).decode("ascii")

        scope: dict[str, Any] = {
            "type": "websocket",
            "asgi": {"version": "3.0", "spec_version": "2.3"},
            "scheme": "ws",
            "path": raw_path,
            "query_string": raw_qs.encode("latin-1"),
            "headers": headers_list,
        }

        inbound_queue: asyncio.Queue[Mapping[str, Any]] = asyncio.Queue()
        await inbound_queue.put({"type": "websocket.connect"})
        accepted_event = asyncio.Event()
        closed = False

        async def _receive() -> Mapping[str, Any]:
            return await inbound_queue.get()

        async def _send(msg: Mapping[str, Any]) -> None:
            nonlocal closed
            mtype = msg.get("type")
            if mtype == "websocket.accept" and not accepted_event.is_set():
                handshake = (
                    "HTTP/1.1 101 Switching Protocols\r\n"
                    "Upgrade: websocket\r\n"
                    "Connection: Upgrade\r\n"
                    f"Sec-WebSocket-Accept: {accept_token}\r\n\r\n"
                ).encode("ascii")
                writer.write(handshake)
                await writer.drain()
                accepted_event.set()
            elif mtype == "websocket.send":
                text = msg.get("text")
                data_bytes = (
                    text.encode("utf-8")
                    if isinstance(text, str)
                    else bytes(msg.get("bytes", b""))
                )
                writer.write(_encode_ws_frame(0x1, data_bytes))
                await writer.drain()
            elif mtype == "websocket.close" and not closed:
                closed = True
                if not accepted_event.is_set():
                    writer.write(b"HTTP/1.1 403 Forbidden\r\nConnection: close\r\n\r\n")
                    await writer.drain()
                else:
                    code = int(msg.get("code", 1000))
                    writer.write(_encode_ws_frame(0x8, struct.pack("!H", code)))
                    await writer.drain()

        async def _pump_socket_reads() -> None:
            await accepted_event.wait()
            while not closed:
                frame = await _read_ws_frame(reader)
                if frame is None:
                    break
                opcode, payload = frame
                if opcode == 0x8:
                    await inbound_queue.put({"type": "websocket.disconnect", "code": 1000})
                    break
                if opcode == 0x9:
                    writer.write(_encode_ws_frame(0xA, payload))
                    await writer.drain()
                elif opcode in {0x1, 0x2}:
                    await inbound_queue.put(
                        {
                            "type": "websocket.receive",
                            "text": payload.decode("utf-8", errors="replace"),
                        }
                    )
            await inbound_queue.put({"type": "websocket.disconnect", "code": 1001})

        async def _run_app() -> None:
            await self._app(scope, _receive, _send)

        app_task: asyncio.Task[None] = asyncio.create_task(_run_app())
        pump_task: asyncio.Task[None] = asyncio.create_task(_pump_socket_reads())
        try:
            await app_task
        finally:
            closed = True
            pump_task.cancel()
            writer.close()


__all__ = [
    "AsgiAppFn",
    "AsgiReceiveFn",
    "AsgiSendFn",
    "AsyncStdlibHttpWsServer",
    "create_asgi_app",
]
