"""WriterDoc CRUD service — synchronous operations for document management."""

from __future__ import annotations

import logging
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from .models import WriterDoc
from .models_share import WriterDocShare
from .models_version import WriterDocVersion

logger = logging.getLogger(__name__)

# B2-T2: sentinel returned by update() when CAS version mismatch occurs.
# Distinct from None (doc not found) so the router can return 409 vs 404.
_CONFLICT: WriterDoc = WriterDoc.__new__(WriterDoc)  # type: ignore[assignment]


class WriterDocService:
    """Manages user documents for the TipTap Writer."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory
        # Instance-level throttle state (was class variable — multi-worker
        # instances shared one dict).  Per-instance so each process has its own.
        self._auto_version_timestamps: dict[str, float] = {}

    async def create(self, *, user_id: str, title: str = "未命名文档", content_json: str = "{}", doc_type: str = "general") -> WriterDoc:
        doc = WriterDoc(
            id=f"doc_{uuid.uuid4().hex[:12]}",
            user_id=user_id,
            title=title,
            content_json=content_json,
            doc_type=doc_type,
        )
        async with self._sf() as session:
            session.add(doc)
            await session.commit()
            await session.refresh(doc)
        return doc

    async def get(self, doc_id: str, user_id: str) -> WriterDoc | None:
        async with self._sf() as session:
            stmt = select(WriterDoc).where(
                WriterDoc.id == doc_id,
                WriterDoc.user_id == user_id,
                WriterDoc.deleted_at.is_(None),
            )
            return (await session.execute(stmt)).scalar_one_or_none()

    async def list_docs(
        self,
        user_id: str,
        *,
        limit: int = 20,
        offset: int = 0,
        search: str | None = None,
        doc_type: str | None = None,
        sort_by: str = "updated_at",
        sort_dir: str = "desc",
    ) -> tuple[list[WriterDoc], bool]:
        """B5-T1: paginated list with search/filter/sort.

        Returns (items, has_more). Uses limit+1 strategy: fetch one extra row
        to determine has_more without a separate count query. Stable sort via
        id tiebreaker (matches console.py:629-631 pattern).
        """
        _SORT_COLUMNS = {
            "updated_at": WriterDoc.updated_at,
            "created_at": WriterDoc.created_at,
            "title": WriterDoc.title,
        }
        sort_col = _SORT_COLUMNS.get(sort_by, WriterDoc.updated_at)
        sort_expr = sort_col.desc() if sort_dir == "desc" else sort_col.asc()

        async with self._sf() as session:
            stmt = select(WriterDoc).where(
                WriterDoc.user_id == user_id,
                WriterDoc.deleted_at.is_(None),
            )
            if search:
                stmt = stmt.where(WriterDoc.title.ilike(f"%{search}%"))
            if doc_type:
                stmt = stmt.where(WriterDoc.doc_type == doc_type)

            stmt = stmt.order_by(sort_expr, WriterDoc.id.desc() if sort_dir == "desc" else WriterDoc.id.asc())
            stmt = stmt.limit(limit + 1).offset(offset)

            rows = list((await session.execute(stmt)).scalars().all())
            has_more = len(rows) > limit
            items = rows[:limit]
            return (items, has_more)

    async def update(self, doc_id: str, user_id: str, *, expected_version: int | None = None, **kwargs: Any) -> WriterDoc | None:
        """Update a document with optional CAS (compare-and-swap) version check.

        When ``expected_version`` is provided, the UPDATE only succeeds if the
        stored version matches. On success, version is incremented atomically.
        Returns ``_CONFLICT`` sentinel when the version doesn't match (caller
        distinguishes 404 vs 409 by checking the return value).
        """
        async with self._sf() as session:
            version_filter = []
            if expected_version is not None:
                version_filter = [WriterDoc.version == expected_version]

            stmt = (
                update(WriterDoc)
                .where(
                    WriterDoc.id == doc_id,
                    WriterDoc.user_id == user_id,
                    WriterDoc.deleted_at.is_(None),
                    *version_filter,
                )
                .values(**kwargs, updated_at=datetime.now(UTC), version=WriterDoc.version + 1)
            )
            result = await session.execute(stmt)
            if expected_version is not None and result.rowcount == 0:
                # Version mismatch (or doc doesn't exist / deleted).
                # Caller will get() to distinguish 404 vs 409.
                await session.rollback()
                return _CONFLICT
            await session.commit()
        return await self.get(doc_id, user_id)

    async def delete(self, doc_id: str, user_id: str) -> bool:
        from datetime import UTC, datetime

        doc = await self.get(doc_id, user_id)
        if doc is None:
            return False
        async with self._sf() as session:
            stmt = update(WriterDoc).where(WriterDoc.id == doc_id, WriterDoc.user_id == user_id).values(deleted_at=datetime.now(UTC), updated_at=datetime.now(UTC))
            await session.execute(stmt)
            await session.commit()
        return True

    # ── Sharing ──────────────────────────────────────────────────────────

    async def _list_shares(self, doc_id: str, owner_id: str) -> list[WriterDocShare]:
        """All share rows for (doc_id, owner_id), newest first.

        Legacy DBs may hold duplicates (pre-idempotency); callers must never
        assume at most one row. owner_id 恒等于 doc owner（当前无文档转手
        场景）；若未来引入转手，revoke 的按 (doc_id, owner_id) 删全部行
        需要重新评估旧 owner 残留行。
        """
        async with self._sf() as session:
            stmt = (
                select(WriterDocShare)
                .where(
                    WriterDocShare.doc_id == doc_id,
                    WriterDocShare.owner_id == owner_id,
                )
                .order_by(WriterDocShare.created_at.desc(), WriterDocShare.id.desc())
            )
            return list((await session.execute(stmt)).scalars().all())

    async def create_share(
        self,
        doc_id: str,
        owner_id: str,
        expires_days: int | None = None,
    ) -> WriterDocShare:
        doc = await self.get(doc_id, owner_id)
        if doc is None:
            raise ValueError("Document not found")
        existing = await self._list_shares(doc_id, owner_id)
        if existing:
            # Idempotent: one active share per doc. Return the newest; self-heal
            # legacy duplicate rows so get_share can never 500 again (P1-1).
            keep = existing[0]
            # An expired share is a dead link (get_by_share_token → None); re-anchor
            # its expiry on re-create, otherwise the doc can never be shared again
            # (批次A review follow-up — pre-T3 every create inserted a fresh row).
            # expires_at column is naive (SQLite); normalize before comparing (P0-1).
            expired = keep.expires_at is not None and keep.expires_at.replace(tzinfo=UTC) < datetime.now(UTC)
            if len(existing) > 1 or expired:
                async with self._sf() as session:
                    for stale in existing[1:]:
                        await session.delete(stale)
                    if expired:
                        new_expires_at = datetime.now(UTC) + timedelta(days=expires_days) if expires_days is not None else None
                        await session.execute(update(WriterDocShare).where(WriterDocShare.id == keep.id).values(expires_at=new_expires_at))
                        # Mirror the naive value a DB re-read would return (SQLite
                        # strips tzinfo), matching the fresh-create path.
                        keep.expires_at = new_expires_at.replace(tzinfo=None) if new_expires_at is not None else None
                    await session.commit()
            return keep
        share_token = secrets.token_urlsafe(16)
        expires_at = None
        if expires_days is not None:
            expires_at = datetime.now(UTC) + timedelta(days=expires_days)
        share = WriterDocShare(
            id=f"shr_{uuid.uuid4().hex[:12]}",
            doc_id=doc_id,
            owner_id=owner_id,
            share_token=share_token,
            expires_at=expires_at,
        )
        async with self._sf() as session:
            session.add(share)
            await session.commit()
            await session.refresh(share)
        return share

    async def get_share(self, doc_id: str, owner_id: str) -> WriterDocShare | None:
        shares = await self._list_shares(doc_id, owner_id)
        return shares[0] if shares else None

    async def revoke_share(self, doc_id: str, owner_id: str) -> bool:
        shares = await self._list_shares(doc_id, owner_id)
        if not shares:
            return False
        async with self._sf() as session:
            for share in shares:
                await session.delete(share)
            await session.commit()
        return True

    async def get_by_share_token(self, share_token: str) -> WriterDoc | None:
        async with self._sf() as session:
            stmt = select(WriterDocShare).where(
                WriterDocShare.share_token == share_token,
            )
            share = (await session.execute(stmt)).scalar_one_or_none()
            if share is None:
                return None
            if share.expires_at:
                # expires_at column is naive (SQLite); normalize before comparing
                # with aware datetime.now(UTC) — naive < aware raises TypeError (P0-1).
                expires_at = share.expires_at.replace(tzinfo=UTC)
                if expires_at < datetime.now(UTC):
                    return None
            doc_stmt = select(WriterDoc).where(
                WriterDoc.id == share.doc_id,
                WriterDoc.deleted_at.is_(None),
            )
            return (await session.execute(doc_stmt)).scalar_one_or_none()

    # ── Version History ───────────────────────────────────────────────

    async def create_version(self, doc_id: str, user_id: str, reason: str = "auto") -> WriterDocVersion | None:
        import time

        now = time.monotonic()
        # Periodic cleanup: bound _auto_version_timestamps growth so doc_ids
        # edited once then never again don't accumulate indefinitely.
        self._cleanup_auto_timestamps(now)

        doc = await self.get(doc_id, user_id)
        if doc is None:
            return None

        # Throttle auto versions: skip if last auto version was created within 30 seconds.
        # "destructive" and "restore" bypass the throttle — destructive ops must always
        # get an undo point (§9.3); restore is an explicit user action.
        if reason == "auto":
            last_ts = self._auto_version_timestamps.get(doc_id, 0)
            if now - last_ts < 30:
                return None
            # NOTE: timestamp recorded only after a successful commit below —
            # a failed insert must not eat the next 30s of snapshots (P2-8).

        from sqlalchemy.exc import IntegrityError

        version = None
        for _attempt in range(3):
            async with self._sf() as session:
                stmt = select(func.max(WriterDocVersion.version_number)).where(
                    WriterDocVersion.doc_id == doc_id,
                )
                max_ver = (await session.execute(stmt)).scalar() or 0
                candidate = WriterDocVersion(
                    id=f"ver_{uuid.uuid4().hex[:12]}",
                    doc_id=doc_id,
                    version_number=max_ver + 1,
                    title=doc.title,
                    content_json=doc.content_json,
                    snapshot_reason=reason,
                    # B2-T3: snapshot the doc_type so restore can recover it
                    doc_type=doc.doc_type,
                )
                session.add(candidate)
                try:
                    await session.commit()
                except IntegrityError as e:
                    # Postgres reports the constraint name (uq_wdv_doc_version);
                    # SQLite reports the columns instead (UNIQUE constraint
                    # failed: writer_doc_versions.doc_id, ...). Match both
                    # (执行中实证：SQLite 消息不含约束名，计划原匹配串失效).
                    if "uq_wdv_doc_version" not in str(e) and "writer_doc_versions.doc_id" not in str(e):
                        raise
                    continue  # concurrent insert won the number — recompute max
                await session.refresh(candidate)
                version = candidate
                break
        if version is None:
            raise RuntimeError("create_version: version_number contention after 3 attempts")
        if reason == "auto":
            self._auto_version_timestamps[doc_id] = now
        # Prune old versions (cap total + cap restore separately)
        await self._prune_versions(doc_id)
        return version

    async def _prune_versions(self, doc_id: str, max_versions: int = 20, max_restore: int = 5) -> None:
        """Cap version rows: at most ``max_restore`` restore snapshots, and at
        most ``max_versions`` total (oldest auto/destructive pruned first).

        restore rows used to escape pruning entirely (P2-1) — unbounded growth
        on every rollback, and they squeezed real history out.
        """
        async with self._sf() as session:
            stmt = select(WriterDocVersion).where(WriterDocVersion.doc_id == doc_id).order_by(WriterDocVersion.version_number.asc())
            rows = list((await session.execute(stmt)).scalars().all())
            restores = [v for v in rows if v.snapshot_reason == "restore"]
            deletable: list[WriterDocVersion] = []
            # 1) oldest restores beyond max_restore
            if len(restores) > max_restore:
                deletable.extend(restores[: len(restores) - max_restore])
            # 2) still over total cap → oldest auto/destructive
            remaining = len(rows) - len(deletable)
            if remaining > max_versions:
                non_restore = [v for v in rows if v.snapshot_reason != "restore" and v not in deletable]
                deletable.extend(non_restore[: remaining - max_versions])
            # 3) paranoia: still over (restore-heavy) → oldest remaining restores
            remaining = len(rows) - len(deletable)
            if remaining > max_versions:
                extra = [v for v in restores if v not in deletable]
                deletable.extend(extra[: remaining - max_versions])
            for v in deletable:
                await session.delete(v)
            await session.commit()

    def _cleanup_auto_timestamps(self, now: float) -> None:
        """Bound ``_auto_version_timestamps`` growth.

        Strategy (dual guard):
        1. **Stale removal** — drop entries whose ``time.monotonic()`` timestamp
           is older than 5 minutes (300s). Doc_ids edited once then never again
           eventually expire.
        2. **Size cap** — if more than 1000 entries remain, keep the newest 1000
           by timestamp (drop the oldest). This is a hard ceiling against
           pathological growth from many distinct doc_ids in a short window.
        """
        stale_cutoff = now - 300
        self._auto_version_timestamps = {k: v for k, v in self._auto_version_timestamps.items() if v >= stale_cutoff}
        if len(self._auto_version_timestamps) > 1000:
            sorted_items = sorted(
                self._auto_version_timestamps.items(),
                key=lambda x: x[1],
            )
            self._auto_version_timestamps = dict(sorted_items[-1000:])

    async def list_versions(self, doc_id: str, user_id: str, limit: int = 50) -> list[WriterDocVersion]:
        doc = await self.get(doc_id, user_id)
        if doc is None:
            return []
        async with self._sf() as session:
            stmt = select(WriterDocVersion).where(WriterDocVersion.doc_id == doc_id).order_by(WriterDocVersion.version_number.desc()).limit(limit)
            return list((await session.execute(stmt)).scalars().all())

    async def get_version(self, version_id: str, doc_id: str, user_id: str) -> WriterDocVersion | None:
        doc = await self.get(doc_id, user_id)
        if doc is None:
            return None
        async with self._sf() as session:
            stmt = select(WriterDocVersion).where(
                WriterDocVersion.id == version_id,
                WriterDocVersion.doc_id == doc_id,
            )
            return (await session.execute(stmt)).scalar_one_or_none()

    async def restore_version(self, version_id: str, doc_id: str, user_id: str) -> WriterDoc | None:
        version = await self.get_version(version_id, doc_id, user_id)
        if version is None:
            return None
        # Save current state as version before restoring
        await self.create_version(doc_id, user_id, reason="restore")
        # B2-T3: restore title + content + doc_type (when the snapshot has one).
        # Old versions (doc_type=None) don't restore type -- avoids reverting
        # an article back to general.
        restore_kwargs: dict[str, Any] = {"content_json": version.content_json}
        restore_kwargs["title"] = version.title
        if version.doc_type is not None:
            restore_kwargs["doc_type"] = version.doc_type
        return await self.update(doc_id, user_id, **restore_kwargs)
