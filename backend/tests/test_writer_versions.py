"""Tests for WriterDocService versioning: force-snapshot, throttle, prune, cleanup.

Covers Task W11 (§9.3): destructive-op force-snapshot bypasses the 30s
auto-throttle; "destructive" reason is prunable (like auto); _auto_version_timestamps
is instance-level + periodically cleaned (bounded growth).

Mirrors the setup in test_writer_doc_type.py: in-memory SQLite +
async_sessionmaker + WriterDocService(sf).  WriterDocVersion shares the
same declarative Base so WriterDoc.metadata.create_all creates both tables.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.gateway.content.models import WriterDoc
from app.gateway.content.schemas import WriterDocUpdate
from app.gateway.content.writer_service import WriterDocService

# ── Shared fixture: in-memory engine + session factory ──────────────────


@pytest_asyncio.fixture()
async def engine():
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as conn:
        await conn.run_sync(WriterDoc.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture()
def svc(engine) -> WriterDocService:
    sf = async_sessionmaker(engine, expire_on_commit=False)
    return WriterDocService(sf)


# ── 1. force-snapshot bypasses throttle ─────────────────────────────────


@pytest.mark.asyncio
async def test_force_snapshot_bypasses_throttle(svc: WriterDocService):
    """Two destructive snapshots within 30s both create versions (NOT throttled)."""
    doc = await svc.create(user_id="user1", title="Test", content_json="# original")
    assert doc is not None

    v1 = await svc.create_version(doc.id, "user1", reason="destructive")
    v2 = await svc.create_version(doc.id, "user1", reason="destructive")

    assert v1 is not None, "first destructive snapshot must create a version"
    assert v2 is not None, "second destructive snapshot within 30s must NOT be throttled"
    assert v1.id != v2.id
    assert v2.version_number > v1.version_number


# ── 2. auto still throttled (regression) ────────────────────────────────


@pytest.mark.asyncio
async def test_auto_still_throttled(svc: WriterDocService):
    """Two auto-snapshots within 30s -> only 1 created (throttle intact for auto)."""
    doc = await svc.create(user_id="user1", title="Test", content_json="# original")
    assert doc is not None

    v1 = await svc.create_version(doc.id, "user1", reason="auto")
    v2 = await svc.create_version(doc.id, "user1", reason="auto")

    assert v1 is not None, "first auto snapshot must create a version"
    assert v2 is None, "second auto snapshot within 30s must be throttled (return None)"


# ── 3. PUT handler passes reason="destructive" when force_snapshot ───────


@pytest.mark.asyncio
async def test_put_force_snapshot_uses_destructive_reason():
    """PUT with force_snapshot=true + content_json -> create_version called with reason='destructive'."""
    from app.gateway.routers.writer import update_doc

    mock_svc = AsyncMock()
    mock_svc.create_version = AsyncMock(return_value=None)
    mock_svc.update = AsyncMock(return_value=MagicMock())  # truthy — not 404

    request = MagicMock()
    request.app.state.writer_doc_service = mock_svc

    body = WriterDocUpdate(content_json="# new content", force_snapshot=True)
    await update_doc(request, "doc_test", body)

    mock_svc.create_version.assert_called_once()
    call = mock_svc.create_version.call_args
    reason = call.kwargs.get("reason", call.args[-1] if call.args else None)
    assert reason == "destructive", f"force_snapshot=True must pass reason='destructive', got {reason!r}"


@pytest.mark.asyncio
async def test_put_without_force_snapshot_uses_auto_reason():
    """PUT without force_snapshot -> create_version called with reason='auto' (regression)."""
    from app.gateway.routers.writer import update_doc

    mock_svc = AsyncMock()
    mock_svc.create_version = AsyncMock(return_value=None)
    mock_svc.update = AsyncMock(return_value=MagicMock())

    request = MagicMock()
    request.app.state.writer_doc_service = mock_svc

    body = WriterDocUpdate(content_json="# new content")
    await update_doc(request, "doc_test", body)

    mock_svc.create_version.assert_called_once()
    call = mock_svc.create_version.call_args
    reason = call.kwargs.get("reason", call.args[-1] if call.args else None)
    assert reason == "auto"


# ── 4. destructive reason is prunable (restore kept preferentially) ─────


@pytest.mark.asyncio
async def test_destructive_reason_is_prunable(svc: WriterDocService):
    """Versions beyond max(20) are pruned; restore capped at max_restore=5 (P2-1).

    Pre-P2-1 restore escaped pruning entirely (all 10 survived). Now restore is
    capped at 5 — oldest restores die first; destructive only pruned when still
    over the total cap after restore trimming.
    """
    doc = await svc.create(user_id="user1", title="Test", content_json="# original")
    assert doc is not None

    # 15 destructive (bypass throttle) + 10 restore (bypass throttle) = 25 total
    for _ in range(15):
        await svc.create_version(doc.id, "user1", reason="destructive")
    for _ in range(10):
        await svc.create_version(doc.id, "user1", reason="restore")

    versions = await svc.list_versions(doc.id, "user1")
    assert len(versions) <= 20, "prune must cap at 20"

    # P2-1: restore now capped at max_restore=5 (oldest 5 of 10 pruned)
    restore_count = sum(1 for v in versions if v.snapshot_reason == "restore")
    assert restore_count == 5, f"restore capped at 5, got {restore_count}"

    # Total cap=20, restore occupies 5 → 15 destructive all survive (no destructive pruned)
    destructive_count = sum(1 for v in versions if v.snapshot_reason == "destructive")
    assert destructive_count == 15, f"restore filled the cap, got {destructive_count}"


# ── 5. _auto_version_timestamps is instance-level ───────────────────────


def test_auto_version_timestamps_is_instance_level():
    """Two WriterDocService instances have separate _auto_version_timestamps."""
    sf = MagicMock()
    svc1 = WriterDocService(sf)
    svc2 = WriterDocService(sf)

    # Not the same dict object (instance-level, not class-level)
    assert svc1._auto_version_timestamps is not svc2._auto_version_timestamps

    # Mutating one does NOT affect the other
    svc1._auto_version_timestamps["doc_a"] = 100.0
    assert "doc_a" not in svc2._auto_version_timestamps
    assert svc2._auto_version_timestamps == {}


# ── 6. timestamp cleanup bounds growth ──────────────────────────────────


def test_timestamp_cleanup_bounds_growth():
    """After many doc_ids, _auto_version_timestamps is pruned (bounded)."""
    sf = MagicMock()
    svc = WriterDocService(sf)

    # Stuff 1100 entries — all recent (1 second ago, within 5 min cutoff)
    now = 10000.0
    for i in range(1100):
        svc._auto_version_timestamps[f"doc_{i}"] = now - 1.0

    svc._cleanup_auto_timestamps(now)

    # Size cap: max 1000 entries
    assert len(svc._auto_version_timestamps) <= 1000
    # Oldest entries removed (stable sort keeps insertion order for equal timestamps)
    assert "doc_0" not in svc._auto_version_timestamps
    assert "doc_1099" in svc._auto_version_timestamps


def test_timestamp_cleanup_removes_stale_entries():
    """Entries older than 5 minutes are dropped."""
    sf = MagicMock()
    svc = WriterDocService(sf)

    now = 10000.0
    svc._auto_version_timestamps["old_doc"] = now - 400.0  # > 300s (5 min) -> stale
    svc._auto_version_timestamps["recent_doc"] = now - 10.0  # within 5 min -> kept

    svc._cleanup_auto_timestamps(now)

    assert "old_doc" not in svc._auto_version_timestamps
    assert "recent_doc" in svc._auto_version_timestamps


# ── P2-1: restore versions no longer escape pruning ─────────────────────


@pytest.mark.asyncio
async def test_restore_versions_are_pruned_beyond_cap(svc):
    doc = await svc.create(user_id="user1", title="T", content_json="# v0")
    for i in range(12):
        await svc.create_version(doc.id, "user1", reason="restore")
    versions = await svc.list_versions(doc.id, "user1", limit=100)
    restores = [v for v in versions if v.snapshot_reason == "restore"]
    assert len(restores) <= 5
    assert len(versions) <= 20


# ── P2-4: restore keeps the (manually renamed) current title ─────────────


@pytest.mark.asyncio
async def test_restore_recovers_title_and_type(svc):
    """B2-T3: restore now restores title + content + doc_type (decision B-3).
    Old P2-4 behavior (title NOT restored) is superseded -- B-3 explicitly
    requires title restoration via version snapshots."""
    doc = await svc.create(user_id="user1", title="原始标题", content_json="# v1")
    # 先留下 v1 快照（含原始标题），再改名+改内容
    await svc.create_version(doc.id, "user1", reason="manual")
    await svc.update(doc.id, "user1", title="改名后", content_json="# v2")
    versions = await svc.list_versions(doc.id, "user1")
    snap = next(v for v in versions if v.title == "原始标题")
    restored = await svc.restore_version(snap.id, doc.id, "user1")
    assert restored is not None
    assert restored.content_json == "# v1"
    # B2-T3: title IS now restored (B-3 overrides old P2-4)
    assert restored.title == "原始标题"


# ── P2-8: failed snapshot must not consume the 30s auto throttle ─────────


@pytest.mark.asyncio
async def test_failed_snapshot_does_not_consume_throttle(svc, monkeypatch):
    doc = await svc.create(user_id="user1", title="T", content_json="# a")
    real_sf = svc._sf
    calls = {"n": 0}

    class _Flaky:
        async def __aenter__(self):
            self._ctx = real_sf()
            sess = await self._ctx.__aenter__()
            calls["n"] += 1
            if calls["n"] == 2:  # 1st = get(); 2nd = write session → boom
                await self._ctx.__aexit__(None, None, None)
                raise RuntimeError("boom")
            return sess

        async def __aexit__(self, *args):
            return await self._ctx.__aexit__(*args)

    monkeypatch.setattr(svc, "_sf", _Flaky)
    import pytest as _pytest

    with _pytest.raises(RuntimeError):
        await svc.create_version(doc.id, "user1", reason="auto")
    monkeypatch.setattr(svc, "_sf", real_sf)
    # Immediate retry must succeed (throttle consumed only AFTER a real commit)
    v = await svc.create_version(doc.id, "user1", reason="auto")
    assert v is not None


# ── P2-2: concurrent create_version must not duplicate version_number ────


@pytest.mark.asyncio
async def test_create_version_retries_on_unique_collision(svc, monkeypatch):
    """P2-2: a UNIQUE collision on (doc_id, version_number) must trigger a fresh
    max recompute + retry, not propagate. Deterministic stand-in for a real
    race — in-memory SQLite shares ONE connection across sessions, so true
    concurrent sessions leak connection-level tx semantics (one session's
    commit/rollback hits another's rows); real-concurrency sanity covered by
    the file-DB gather test below."""
    from sqlalchemy.exc import IntegrityError as _IE

    doc = await svc.create(user_id="user1", title="T", content_json="# a")
    v1 = await svc.create_version(doc.id, "user1", reason="restore")
    assert v1 is not None
    real_sf = svc._sf
    state = {"collided": False}

    class _CollideOnceSession:
        """Session proxy: first commit raises the SQLite-style UNIQUE error."""

        def __init__(self, sess):
            self._sess = sess

        def __getattr__(self, name):
            return getattr(self._sess, name)

        async def commit(self):
            if not state["collided"]:
                state["collided"] = True
                raise _IE(
                    "INSERT INTO writer_doc_versions ...",
                    (),
                    Exception("UNIQUE constraint failed: writer_doc_versions.doc_id, writer_doc_versions.version_number"),
                )
            await self._sess.commit()

    class _CollideOnceFactory:
        def __call__(self):
            ctx = real_sf()

            class _Ctx:
                async def __aenter__(self):
                    return _CollideOnceSession(await ctx.__aenter__())

                async def __aexit__(self, *args):
                    return await ctx.__aexit__(*args)

            return _Ctx()

    monkeypatch.setattr(svc, "_sf", _CollideOnceFactory())
    v2 = await svc.create_version(doc.id, "user1", reason="restore")
    assert state["collided"] is True  # retry path actually exercised
    assert v2 is not None
    assert v2.version_number == v1.version_number + 1


@pytest.mark.asyncio
async def test_concurrent_create_version_no_duplicate_numbers(tmp_path):
    """Real-concurrency sanity on a FILE DB (separate connections). 3 writers:
    worst case each collides N-1=2 times, within the 3-attempt retry cap."""
    import asyncio

    db = tmp_path / "conc.db"
    eng = create_async_engine(f"sqlite+aiosqlite:///{db}")
    async with eng.begin() as conn:
        await conn.run_sync(WriterDoc.metadata.create_all)
    svc = WriterDocService(async_sessionmaker(eng, expire_on_commit=False))
    doc = await svc.create(user_id="user1", title="T", content_json="# a")
    results = await asyncio.gather(*[svc.create_version(doc.id, "user1", reason="restore") for _ in range(3)])
    numbers = sorted(v.version_number for v in results if v is not None)
    assert len(numbers) == len(set(numbers))  # 无重复版本号
    assert len(numbers) == 3
    await eng.dispose()
