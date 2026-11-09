"""In-memory stream bridge backed by an in-process event log."""

from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from .base import END_SENTINEL, HEARTBEAT_SENTINEL, StreamBridge, StreamEvent

logger = logging.getLogger(__name__)


@dataclass
class _RunStream:
    events: list[StreamEvent] = field(default_factory=list)
    condition: asyncio.Condition = field(default_factory=asyncio.Condition)
    ended: bool = False
    start_offset: int = 0


class MemoryStreamBridge(StreamBridge):
    """Per-run in-memory event log implementation.

    Events are retained for a bounded time window per run so late subscribers
    and reconnecting clients can replay buffered events from ``Last-Event-ID``.

    The event log lives in this process's heap, so a subscriber in another
    worker cannot see it — hence ``cross_process = False``.
    """

    cross_process = False

    def __init__(self, *, queue_maxsize: int = 256, terminal_cap: int = 10_000) -> None:
        self._maxsize = queue_maxsize
        self._streams: dict[str, _RunStream] = {}
        self._counters: dict[str, int] = {}
        # run_ids that have reached END. Survives cleanup() so a late reconnect
        # (sessionStorage still holds the run id) terminates instead of hanging
        # on an empty freshly-created stream. Bounded by an LRU cap so a
        # long-lived gateway doesn't leak run_id strings forever.
        self._terminal_cap = terminal_cap
        self._terminal: OrderedDict[str, None] = OrderedDict()

    # -- helpers ---------------------------------------------------------------

    def _mark_terminal(self, run_id: str) -> None:
        """Record *run_id* as ended (idempotent), LRU-evicting oldest past the cap."""
        if run_id in self._terminal:
            self._terminal.move_to_end(run_id)
        else:
            self._terminal[run_id] = None
            while len(self._terminal) > self._terminal_cap:
                self._terminal.popitem(last=False)

    def _get_or_create_stream(self, run_id: str, *, for_subscribe: bool = False) -> _RunStream:
        if run_id not in self._streams:
            stream = _RunStream()
            # A subscriber arriving for a run that already ended (its stream was
            # cleaned up, or this worker never hosted it) must see END right away
            # instead of looping on heartbeats from an empty non-ended stream.
            if for_subscribe and run_id in self._terminal:
                stream.ended = True
            self._streams[run_id] = stream
            self._counters[run_id] = 0
        return self._streams[run_id]

    def _next_id(self, run_id: str) -> str:
        self._counters[run_id] = self._counters.get(run_id, 0) + 1
        ts = int(time.time() * 1000)
        seq = self._counters[run_id] - 1
        return f"{ts}-{seq}"

    def _resolve_start_offset(self, stream: _RunStream, last_event_id: str | None) -> int:
        if last_event_id is None:
            return stream.start_offset

        for index, entry in enumerate(stream.events):
            if entry.id == last_event_id:
                return stream.start_offset + index + 1

        if stream.events:
            logger.warning(
                "last_event_id=%s not found in retained buffer; replaying from earliest retained event",
                last_event_id,
            )
        return stream.start_offset

    # -- StreamBridge API ------------------------------------------------------

    async def publish(self, run_id: str, event: str, data: Any) -> None:
        stream = self._get_or_create_stream(run_id)
        entry = StreamEvent(id=self._next_id(run_id), event=event, data=data)
        async with stream.condition:
            stream.events.append(entry)
            if len(stream.events) > self._maxsize:
                overflow = len(stream.events) - self._maxsize
                del stream.events[:overflow]
                stream.start_offset += overflow
            stream.condition.notify_all()

    async def publish_end(self, run_id: str) -> None:
        self._mark_terminal(run_id)
        stream = self._get_or_create_stream(run_id)
        async with stream.condition:
            stream.ended = True
            stream.condition.notify_all()

    async def ensure_terminated(self, run_id: str) -> None:
        # Cross-worker: the router calls this from authoritative run status when
        # this worker may never have hosted the run (multi-worker / post-restart).
        self._mark_terminal(run_id)
        stream = self._streams.get(run_id)
        if stream is not None and not stream.ended:
            async with stream.condition:
                stream.ended = True
                stream.condition.notify_all()

    async def subscribe(
        self,
        run_id: str,
        *,
        last_event_id: str | None = None,
        heartbeat_interval: float = 15.0,
    ) -> AsyncIterator[StreamEvent]:
        stream = self._get_or_create_stream(run_id, for_subscribe=True)
        async with stream.condition:
            next_offset = self._resolve_start_offset(stream, last_event_id)

        while True:
            async with stream.condition:
                if next_offset < stream.start_offset:
                    logger.warning(
                        "subscriber for run %s fell behind retained buffer; resuming from offset %s",
                        run_id,
                        stream.start_offset,
                    )
                    next_offset = stream.start_offset

                local_index = next_offset - stream.start_offset
                if 0 <= local_index < len(stream.events):
                    entry = stream.events[local_index]
                    next_offset += 1
                elif stream.ended:
                    entry = END_SENTINEL
                else:
                    try:
                        await asyncio.wait_for(stream.condition.wait(), timeout=heartbeat_interval)
                    except TimeoutError:
                        entry = HEARTBEAT_SENTINEL
                    else:
                        continue

            if entry is END_SENTINEL:
                yield END_SENTINEL
                return
            yield entry

    async def cleanup(self, run_id: str, *, delay: float = 0) -> None:
        if delay > 0:
            await asyncio.sleep(delay)
        self._streams.pop(run_id, None)
        self._counters.pop(run_id, None)
        # NOTE: intentionally keep run_id in self._terminal so a reconnect after
        # cleanup yields END instead of hanging on an empty stream.

    async def close(self) -> None:
        self._streams.clear()
        self._counters.clear()
        self._terminal.clear()
