"""Stateless Horizontal WebSocket Pub/Sub Backplane & Graceful Drain Coordinator."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from uuid import UUID

from talent_screener.domain.shared.uuid7 import generate_uuid7, utc_now
from talent_screener.domain.shared.value_objects import TenantId


@dataclass(frozen=True, slots=True)
class BackplanePubSubMessage:
    """Cross-node WebSocket event message routed across horizontally scaled gateways."""

    message_id: UUID
    channel: str
    tenant_id: TenantId
    session_id: UUID
    event_type: str
    payload: Mapping[str, object]
    source_node_id: str
    trace_id: str
    published_at: datetime = field(default_factory=utc_now)

    def to_dict(self) -> dict[str, object]:
        return {
            "message_id": str(self.message_id),
            "channel": self.channel,
            "tenant_id": str(self.tenant_id.value),
            "session_id": str(self.session_id),
            "event_type": self.event_type,
            "payload": dict(self.payload),
            "source_node_id": self.source_node_id,
            "trace_id": self.trace_id,
            "published_at": self.published_at.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class DrainExecutionSummary:
    """Receipt summarizing session checkpoint flush, lock release & client reconnect notice."""

    node_id: str
    drained_session_ids: tuple[UUID, ...]
    emitted_notices_count: int
    retry_after_ms: int
    completed_at: datetime


PubSubSubscriberCallback = Callable[[BackplanePubSubMessage], Awaitable[None] | None]
NodeDrainCallback = Callable[[int], Awaitable[tuple[object, ...]]]


class StatelessWebSocketPubSubBackplane:
    """Shared cross-node (tenant_id, session_id) Pub/Sub broker and node drain registry."""

    def __init__(self) -> None:
        self._nodes_draining: dict[str, bool] = {}
        self._subscriptions: dict[tuple[UUID, UUID], dict[str, PubSubSubscriberCallback]] = {}
        self._drain_handlers: dict[str, NodeDrainCallback] = {}
        self._published_history: list[BackplanePubSubMessage] = []

    @staticmethod
    def channel_name(*, tenant_id: TenantId, session_id: UUID) -> str:
        return f"interview:ws:{tenant_id.value}:{session_id}"

    @property
    def published_messages(self) -> tuple[BackplanePubSubMessage, ...]:
        return tuple(self._published_history)

    def register_node(
        self,
        node_id: str,
        *,
        drain_handler: NodeDrainCallback | None = None,
    ) -> None:
        """Register a WebSocket gateway node (or update its graceful drain callback)."""
        self._nodes_draining.setdefault(node_id, False)
        if drain_handler is not None:
            self._drain_handlers[node_id] = drain_handler

    def unregister_node(self, node_id: str) -> None:
        self._nodes_draining.pop(node_id, None)
        self._drain_handlers.pop(node_id, None)
        for subscribers in self._subscriptions.values():
            subscribers.pop(node_id, None)

    def mark_draining(self, node_id: str, draining: bool = True) -> None:
        self._nodes_draining[node_id] = draining

    def is_node_draining(self, node_id: str) -> bool:
        return self._nodes_draining.get(node_id, False)

    def subscribe_session(
        self,
        *,
        node_id: str,
        tenant_id: TenantId,
        session_id: UUID,
        callback: PubSubSubscriberCallback,
    ) -> str:
        """Subscribe ``node_id`` to receive cross-node frames for ``(tenant_id, session_id)``."""
        self.register_node(node_id)
        key = (tenant_id.value, session_id)
        subs = self._subscriptions.setdefault(key, {})
        subs[node_id] = callback
        return self.channel_name(tenant_id=tenant_id, session_id=session_id)

    def unsubscribe_session(
        self,
        *,
        node_id: str,
        tenant_id: TenantId,
        session_id: UUID,
    ) -> None:
        key = (tenant_id.value, session_id)
        subs = self._subscriptions.get(key)
        if subs is None:
            return
        subs.pop(node_id, None)
        if not subs:
            del self._subscriptions[key]

    async def publish_session_event(
        self,
        *,
        tenant_id: TenantId,
        session_id: UUID,
        event_type: str,
        payload: Mapping[str, object],
        source_node_id: str,
        trace_id: str | None = None,
        include_source_node: bool = False,
    ) -> int:
        """Broadcast an outbound WS event across all peer gateways subscribed to the session."""
        channel = self.channel_name(tenant_id=tenant_id, session_id=session_id)
        msg = BackplanePubSubMessage(
            message_id=generate_uuid7(),
            channel=channel,
            tenant_id=tenant_id,
            session_id=session_id,
            event_type=event_type,
            payload=MappingProxyType(dict(payload)),
            source_node_id=source_node_id,
            trace_id=trace_id or str(generate_uuid7()),
        )
        self._published_history.append(msg)

        subs = dict(self._subscriptions.get((tenant_id.value, session_id), {}))
        delivered = 0
        for sub_node_id, callback in subs.items():
            if not include_source_node and sub_node_id == source_node_id:
                continue
            maybe_coro = callback(msg)
            if inspect.isawaitable(maybe_coro):
                await maybe_coro
            delivered += 1
        return delivered

    async def broadcast_force_evict(
        self,
        *,
        tenant_id: TenantId,
        session_id: UUID,
        new_owner_node_id: str,
        new_fencing_token: int,
    ) -> int:
        """Emit split-brain eviction control signal to peer gateway nodes holding stale sockets."""
        return await self.publish_session_event(
            tenant_id=tenant_id,
            session_id=session_id,
            event_type="control.force_evict_socket",
            payload={
                "new_owner_node_id": new_owner_node_id,
                "new_fencing_token": new_fencing_token,
            },
            source_node_id=new_owner_node_id,
            include_source_node=False,
        )

    async def initiate_graceful_drain(
        self,
        *,
        node_id: str,
        retry_after_ms: int = 250,
    ) -> DrainExecutionSummary:
        """Trigger node drain: refuse new WS upgrades, checkpoint sessions & emit notices."""
        self.mark_draining(node_id, True)
        handler = self._drain_handlers.get(node_id)
        notices: tuple[object, ...] = ()
        if handler is not None:
            notices = await handler(retry_after_ms)
        else:
            await asyncio.sleep(0)

        drained_ids: list[UUID] = []
        for notice in notices:
            sid = getattr(notice, "session_id", None)
            if isinstance(sid, UUID):
                drained_ids.append(sid)

        return DrainExecutionSummary(
            node_id=node_id,
            drained_session_ids=tuple(drained_ids),
            emitted_notices_count=len(notices),
            retry_after_ms=retry_after_ms,
            completed_at=utc_now(),
        )

    def summarize(self) -> dict[str, object]:
        """Serialize registered gateway nodes, draining states & active channel counts."""
        return {
            "registered_nodes": {
                node: {"draining": draining}
                for node, draining in sorted(self._nodes_draining.items())
            },
            "active_channel_count": len(self._subscriptions),
            "published_message_count": len(self._published_history),
        }
