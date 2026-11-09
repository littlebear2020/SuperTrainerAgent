"""Inbound message dedupe store.

Guards an agent run / final answer against provider redeliveries: a Feishu (or
other IM) event whose ack window expired while the agent was still running gets
re-posted, which without dedupe means a second full agent run — duplicate billing
and a duplicate reply.

Only the in-process store is implemented. Upstream also ships a Postgres-backed
variant for multi-pod deployments; this deployment is sqlite + a single gateway
worker, so process-local state is sufficient.
"""

from __future__ import annotations

import logging
import time
from collections import OrderedDict

logger = logging.getLogger(__name__)

INBOUND_DEDUPE_TTL_SECONDS = 10 * 60
INBOUND_DEDUPE_MAX_ENTRIES = 4096

# Key tuple matches ChannelManager._inbound_dedupe_key:
# (channel_name, workspace_id, chat_id, message_id).
InboundDedupeKey = tuple[str, str, str, str]


class MemoryInboundDedupeStore:
    """Process-local ``OrderedDict`` store with TTL eviction and a capacity bound.

    ``try_record`` returns ``True`` when the key was already present (duplicate ->
    drop) and ``False`` when it was newly recorded or its prior entry had expired
    (proceed).
    """

    def __init__(
        self,
        ttl_seconds: int = INBOUND_DEDUPE_TTL_SECONDS,
        max_entries: int = INBOUND_DEDUPE_MAX_ENTRIES,
    ) -> None:
        self._ttl = ttl_seconds
        self._max = max_entries
        # Insertion order == chronological (keys are never re-inserted), so an
        # OrderedDict lets us evict expired/overflow entries from the front in
        # O(k) instead of scanning all entries on every inbound message.
        self._store: OrderedDict[InboundDedupeKey, float] = OrderedDict()

    async def try_record(self, key: InboundDedupeKey) -> bool:
        now = time.monotonic()
        # Expired entries cluster at the front: pop until we hit a live one.
        while self._store:
            _, oldest_at = next(iter(self._store.items()))
            if now - oldest_at > self._ttl:
                self._store.popitem(last=False)
            else:
                break
        while len(self._store) > self._max:
            self._store.popitem(last=False)

        if key in self._store:
            return True

        self._store[key] = now
        return False

    async def release(self, key: InboundDedupeKey) -> None:
        self._store.pop(key, None)
