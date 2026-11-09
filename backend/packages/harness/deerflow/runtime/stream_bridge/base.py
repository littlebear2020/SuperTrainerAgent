"""Abstract stream bridge protocol.

StreamBridge decouples agent workers (producers) from SSE endpoints
(consumers), aligning with LangGraph Platform's Queue + StreamManager
architecture.
"""

from __future__ import annotations

import abc
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, ClassVar


@dataclass(frozen=True)
class StreamEvent:
    """Single stream event.

    Attributes:
        id: Monotonically increasing event ID (used as SSE ``id:`` field,
            supports ``Last-Event-ID`` reconnection).
        event: SSE event name, e.g. ``"metadata"``, ``"updates"``,
            ``"events"``, ``"error"``, ``"end"``.
        data: JSON-serialisable payload.
    """

    id: str
    event: str
    data: Any


HEARTBEAT_SENTINEL = StreamEvent(id="", event="__heartbeat__", data=None)
END_SENTINEL = StreamEvent(id="", event="__end__", data=None)


class StreamBridge(abc.ABC):
    """Abstract base for stream bridges."""

    #: Whether this bridge can deliver a run's events to a subscriber living in
    #: a *different* process. ``False`` means run state is confined to the
    #: producing process, so the gateway must run a single worker — see
    #: :func:`app.gateway.deps.langgraph_runtime`'s startup guard. Implementations
    #: backed by shared infrastructure (Redis Streams, Postgres LISTEN/NOTIFY)
    #: set this to ``True``, which retires the guard automatically.
    cross_process: ClassVar[bool] = False

    @abc.abstractmethod
    async def publish(self, run_id: str, event: str, data: Any) -> None:
        """Enqueue a single event for *run_id* (producer side)."""

    @abc.abstractmethod
    async def publish_end(self, run_id: str) -> None:
        """Signal that no more events will be produced for *run_id*."""

    @abc.abstractmethod
    def subscribe(
        self,
        run_id: str,
        *,
        last_event_id: str | None = None,
        heartbeat_interval: float = 15.0,
    ) -> AsyncIterator[StreamEvent]:
        """Async iterator that yields events for *run_id* (consumer side).

        Yields :data:`HEARTBEAT_SENTINEL` when no event arrives within
        *heartbeat_interval* seconds.  Yields :data:`END_SENTINEL` once
        the producer calls :meth:`publish_end`.
        """

    @abc.abstractmethod
    async def cleanup(self, run_id: str, *, delay: float = 0) -> None:
        """Release resources associated with *run_id*.

        If *delay* > 0 the implementation should wait before releasing,
        giving late subscribers a chance to drain remaining events.
        """

    async def ensure_terminated(self, run_id: str) -> None:
        """Mark *run_id* as terminal so subscribers receive END.

        Idempotent. Called by the router when the authoritative run status is
        terminal but this worker may never have hosted the run (multi-worker /
        post-restart / post-cleanup). Ensures a late reconnect terminates
        instead of hanging on heartbeats from an empty freshly-created stream.

        Non-abstract (raises ``NotImplementedError`` by default) so existing
        subclasses keep working; concrete bridges used in production must
        override it.
        """
        raise NotImplementedError

    async def close(self) -> None:
        """Release backend resources.  Default is a no-op."""
