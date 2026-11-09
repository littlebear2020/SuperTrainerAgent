"""Tests for WriterDocService sharing: permission param removal + ShareResponse shape.

Covers Task W25 (§7): `permission` is dead weight — the UI always sends "view",
backend stores but never enforces it.  Remove from CreateShareRequest +
ShareResponse + create_share signature.  DB column kept (defaults "view").

Tests:
1. create_share succeeds WITHOUT permission param (signature no longer accepts it)
2. DB row still defaults permission="view" (column default intact)
3. ShareResponse does NOT include `permission` field
4. CreateShareRequest does NOT include `permission` field
5. get_by_share_token returns the doc regardless (unchanged behavior)
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.gateway.content.models import WriterDoc
from app.gateway.content.models_share import WriterDocShare
from app.gateway.content.schemas import CreateShareRequest, ShareResponse
from app.gateway.content.writer_service import WriterDocService

# ── Shared fixture: in-memory engine + session factory ──────────────────


@pytest_asyncio.fixture()
async def engine():
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as conn:
        await conn.run_sync(WriterDoc.metadata.create_all)
        # WriterDocShare shares the same Base, but ensure it's created
        await conn.run_sync(WriterDocShare.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture()
def svc(engine) -> WriterDocService:
    sf = async_sessionmaker(engine, expire_on_commit=False)
    return WriterDocService(sf)


# ── 1. create_share succeeds without permission param ──────────────────


@pytest.mark.asyncio
async def test_create_share_no_permission_param(svc: WriterDocService):
    """create_share must accept a call WITHOUT permission (param removed)."""
    doc = await svc.create(user_id="user1", title="Test", content_json="# hi")
    share = await svc.create_share(doc.id, "user1", expires_days=7)
    assert share is not None
    assert share.share_token  # non-empty token
    assert share.doc_id == doc.id


# ── 2. DB row still defaults permission="view" ──────────────────────────


@pytest.mark.asyncio
async def test_share_db_row_defaults_permission_view(svc: WriterDocService):
    """Even without setting permission, the DB column defaults to 'view'."""
    doc = await svc.create(user_id="user1", title="Test", content_json="# hi")
    share = await svc.create_share(doc.id, "user1")
    # The DB column has default="view" in the model definition
    assert share.permission == "view", "DB column default must still be 'view'"


# ── 3. ShareResponse does NOT include permission ────────────────────────


def test_share_response_excludes_permission():
    """ShareResponse schema must NOT have a `permission` field."""
    fields = set(ShareResponse.model_fields.keys())
    assert "permission" not in fields, f"ShareResponse must not include permission; got fields: {fields}"
    # Core fields still present
    assert "share_token" in fields
    assert "doc_id" in fields
    assert "expires_at" in fields


# ── 4. CreateShareRequest does NOT include permission ───────────────────


def test_create_share_request_excludes_permission():
    """CreateShareRequest schema must NOT have a `permission` field."""
    fields = set(CreateShareRequest.model_fields.keys())
    assert "permission" not in fields, f"CreateShareRequest must not include permission; got fields: {fields}"
    # expires_days still present
    assert "expires_days" in fields


def test_create_share_request_accepts_expires_days_only():
    """CreateShareRequest accepts expires_days without permission."""
    req = CreateShareRequest(expires_days=30)
    assert req.expires_days == 30


def test_create_share_request_defaults_expires_days_none():
    """CreateShareRequest defaults expires_days to None."""
    req = CreateShareRequest()
    assert req.expires_days is None


# ── 5. ShareResponse round-trips from a share object ────────────────────


@pytest.mark.asyncio
async def test_share_response_validates_from_share_object(svc: WriterDocService):
    """ShareResponse.model_validate works on a WriterDocShare (from_attributes)."""
    doc = await svc.create(user_id="user1", title="Test", content_json="# hi")
    share = await svc.create_share(doc.id, "user1", expires_days=7)
    response = ShareResponse.model_validate(share, from_attributes=True)
    assert response.share_token == share.share_token
    assert response.doc_id == doc.id
    assert response.expires_at is not None
    # permission should NOT be a field on the response
    assert not hasattr(response, "permission") or response.__class__.model_fields.get("permission") is None


# ── 6. get_by_share_token returns doc (unchanged — never enforced) ──────


@pytest.mark.asyncio
async def test_get_by_share_token_returns_doc(svc: WriterDocService):
    """get_by_share_token returns the full doc (never enforced permission)."""
    doc = await svc.create(user_id="user1", title="Test", content_json="# content")
    share = await svc.create_share(doc.id, "user1")
    result = await svc.get_by_share_token(share.share_token)
    assert result is not None
    assert result.id == doc.id
    assert result.content_json == "# content"


# ── P0-1: expires_at naive/aware comparison must not 500 ────────────────


@pytest.mark.asyncio
async def test_get_by_share_token_with_future_expiry_returns_doc(svc: WriterDocService):
    """A share created with expires_days=7 must be retrievable (was TypeError)."""
    doc = await svc.create(user_id="user1", title="T", content_json="# hi")
    share = await svc.create_share(doc.id, "user1", expires_days=7)
    got = await svc.get_by_share_token(share.share_token)
    assert got is not None
    assert got.id == doc.id


@pytest.mark.asyncio
async def test_get_by_share_token_with_past_expiry_returns_none(svc: WriterDocService):
    """An already-expired share returns None (404 path), not TypeError."""
    from datetime import datetime, timedelta

    from sqlalchemy import update

    from app.gateway.content.models_share import WriterDocShare

    doc = await svc.create(user_id="user1", title="T", content_json="# hi")
    share = await svc.create_share(doc.id, "user1", expires_days=7)
    # Force the share into the past (DB stores naive datetime)
    async with svc._sf() as session:
        await session.execute(update(WriterDocShare).where(WriterDocShare.id == share.id).values(expires_at=datetime.now() - timedelta(days=1)))
        await session.commit()
    got = await svc.get_by_share_token(share.share_token)
    assert got is None


# ── P1-1: duplicate share rows must not break get/revoke ────────────────


@pytest.mark.asyncio
async def test_create_share_twice_is_idempotent(svc: WriterDocService):
    """Second create returns the EXISTING share instead of inserting a row."""
    doc = await svc.create(user_id="user1", title="T", content_json="# hi")
    s1 = await svc.create_share(doc.id, "user1", expires_days=7)
    s2 = await svc.create_share(doc.id, "user1", expires_days=30)
    assert s1.share_token == s2.share_token


@pytest.mark.asyncio
async def test_get_and_revoke_survive_legacy_duplicate_rows(svc: WriterDocService):
    """Pre-fix DBs may already hold duplicate rows: get must not 500,
    revoke must clear ALL of them (old tokens die too)."""
    import uuid

    doc = await svc.create(user_id="user1", title="T", content_json="# hi")
    s1 = await svc.create_share(doc.id, "user1")
    # Simulate a legacy duplicate row inserted directly
    from app.gateway.content.models_share import WriterDocShare

    async with svc._sf() as session:
        dup = WriterDocShare(
            id=f"shr_{uuid.uuid4().hex[:12]}",
            doc_id=doc.id,
            owner_id="user1",
            share_token="legacydup-token-1",
            expires_at=None,
        )
        session.add(dup)
        await session.commit()

    share = await svc.get_share(doc.id, "user1")  # must NOT raise MultipleResultsFound
    assert share is not None

    assert await svc.revoke_share(doc.id, "user1") is True
    # Both tokens are dead after revoke
    assert await svc.get_by_share_token(s1.share_token) is None
    assert await svc.get_by_share_token("legacydup-token-1") is None
    assert await svc.get_share(doc.id, "user1") is None


# ── 审查跟进(2026-07-27 批次A review): expired share must be refreshable ──


@pytest.mark.asyncio
async def test_create_share_refreshes_expired_share_keeping_token(svc: WriterDocService):
    """An expired share is a dead link (get_by_share_token → None). Re-creating
    the share must re-anchor its expiry — otherwise the doc can never be shared
    again (T3 idempotency regression: pre-T3 every create inserted a fresh row).
    The token stays stable (one active share per doc)."""
    from datetime import datetime, timedelta

    from sqlalchemy import update

    doc = await svc.create(user_id="user1", title="T", content_json="# hi")
    s1 = await svc.create_share(doc.id, "user1", expires_days=7)
    # Force expiry into the past (DB stores naive datetime)
    async with svc._sf() as session:
        await session.execute(update(WriterDocShare).where(WriterDocShare.id == s1.id).values(expires_at=datetime.now() - timedelta(days=1)))
        await session.commit()
    assert await svc.get_by_share_token(s1.share_token) is None  # dead link

    s2 = await svc.create_share(doc.id, "user1", expires_days=30)
    assert s2.share_token == s1.share_token  # stable token
    assert await svc.get_by_share_token(s1.share_token) is not None  # link alive again
    got = await svc.get_share(doc.id, "user1")
    assert got.expires_at is not None
    assert got.expires_at > datetime.now() + timedelta(days=29)  # re-anchored to ~30d


@pytest.mark.asyncio
async def test_create_share_expired_refresh_with_none_makes_permanent(svc: WriterDocService):
    """Re-creating an expired share with expires_days=None (永久) clears the
    expiry instead of leaving the link dead."""
    from datetime import datetime, timedelta

    from sqlalchemy import update

    doc = await svc.create(user_id="user1", title="T", content_json="# hi")
    s1 = await svc.create_share(doc.id, "user1", expires_days=7)
    async with svc._sf() as session:
        await session.execute(update(WriterDocShare).where(WriterDocShare.id == s1.id).values(expires_at=datetime.now() - timedelta(days=1)))
        await session.commit()

    s2 = await svc.create_share(doc.id, "user1")  # expires_days=None
    assert s2.share_token == s1.share_token
    assert s2.expires_at is None
    assert await svc.get_by_share_token(s1.share_token) is not None


@pytest.mark.asyncio
async def test_create_share_unexpired_keeps_original_expiry(svc: WriterDocService):
    """A still-valid share is returned as-is: the second call's expires_days is
    intentionally ignored (one active share per doc, T3 documented intent) —
    expiry must NOT be extended."""
    doc = await svc.create(user_id="user1", title="T", content_json="# hi")
    s1 = await svc.create_share(doc.id, "user1", expires_days=7)
    s2 = await svc.create_share(doc.id, "user1", expires_days=30)
    assert s2.share_token == s1.share_token
    assert s2.expires_at == s1.expires_at  # unchanged — not extended to 30d


# ── 审查跟进(2026-07-27 批次A review): same-second duplicates order ──────


@pytest.mark.asyncio
async def test_list_shares_same_second_duplicates_deterministic_order(svc: WriterDocService):
    """created_at has 1-second precision (SQLite CURRENT_TIMESTAMP); legacy
    duplicate rows created within the same second must still order
    deterministically (id tiebreaker) so create_share's keep-pick is stable
    across calls instead of depending on the DB's unspecified tie order."""
    import uuid
    from datetime import datetime

    doc = await svc.create(user_id="user1", title="T", content_json="# hi")
    same_second = datetime(2026, 7, 27, 12, 0, 0)
    ids = sorted(f"shr_{uuid.uuid4().hex[:12]}" for _ in range(3))
    async with svc._sf() as session:
        for sid in ids:  # inserted in ascending id order, identical created_at
            session.add(
                WriterDocShare(
                    id=sid,
                    doc_id=doc.id,
                    owner_id="user1",
                    share_token=f"tok-{sid}",
                    expires_at=None,
                    created_at=same_second,
                )
            )
        await session.commit()

    first = await svc._list_shares(doc.id, "user1")
    second = await svc._list_shares(doc.id, "user1")
    assert [s.id for s in first] == [s.id for s in second]
    assert [s.id for s in first] == sorted(ids, reverse=True)  # id desc tiebreaker
