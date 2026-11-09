"""End-to-end per-model token accounting, journal → DB → console (A2).

Why this exists
---------------

The per-model write path had **never been exercised by a real run**. The
production database holds 869 runs and *every one* has
``token_usage_by_model = '{}'`` and ``model_name IS NULL``, so no production
row has ever demonstrated the feature working. Investigation established the
reason, and it is not a code defect:

* ``_record_model_usage`` was introduced **2026-06-19** (``e7a03e524``) and
  has been present and correct ever since.
* The ``runs.token_usage_by_model`` **column** was only created
  **2026-08-03** (``8a89d8b4c``, the alembic bootstrap batch), with
  ``DEFAULT '{}' NOT NULL``.
* The newest run in that database is **2026-07-22**.

So every historical row was backfilled with the column default: the code had
nowhere to write, not a bug in writing. ``model_name``'s writer
(``update_model_name``, called from the worker) likewise only appeared
2026-08-03, and no run has been recorded since.

That leaves a real risk the plan named explicitly: five green unit tests can
coexist with a feature that never fires in production (see this repo's
``provider-capability-must-test-through-wrappers`` lesson, where a capability
was declared on a class but lost through two wrapper layers). These tests
therefore drive the **whole chain** — real ``AIMessage`` objects through the
real ``RunJournal`` callback, through ``RunManager``/``RunRepository`` into a
real SQLite ``runs`` row created by the real schema, and finally out of the
real console endpoint — with no stand-in for any component under test.
"""

from __future__ import annotations

import pytest
from _router_auth_helpers import make_authed_test_app
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from app.gateway.auth.models import User
from app.gateway.routers import console
from deerflow.persistence.run import RunRepository
from deerflow.runtime.journal import RunJournal
from deerflow.runtime.runs.manager import RunManager
from deerflow.runtime.runs.schemas import RunStatus

ADMIN_ID = "33333333-3333-3333-3333-333333333333"


class _FakeEventStore:
    """Event sink only — not part of the path under test."""

    def __init__(self):
        self.batches = []

    async def put_batch(self, batch):
        self.batches.append(batch)


class _LLMResult:
    """Minimal ``LLMResult`` shape; ``RunJournal`` only walks ``.generations``."""

    def __init__(self, messages):
        self.generations = [[type("Gen", (), {"message": m})() for m in messages]]


def _real_ai_message(model: str, *, input_tokens: int, output_tokens: int, cache_read: int | None = None, msg_id: str = "m") -> AIMessage:
    """A genuine LangChain ``AIMessage`` shaped like a live provider reply.

    ``usage_metadata`` / ``input_token_details.cache_read`` is exactly where
    LangChain normalizes 360 and Ark usage — verified against 1124 real
    ``llm.ai.response`` events in the production database, where doubao shows
    an 80.3%% cache-hit rate. The earlier claim that these providers'
    non-standard ``prompt_cache_hit_tokens`` never reaches ``cache_read`` was
    disproven by that data.
    """
    usage = {"input_tokens": input_tokens, "output_tokens": output_tokens, "total_tokens": input_tokens + output_tokens}
    if cache_read is not None:
        usage["input_token_details"] = {"cache_read": cache_read}
    return AIMessage(content="hello", id=msg_id, usage_metadata=usage, response_metadata={"model_name": model})


@pytest.fixture()
async def repo(tmp_path):
    """A real SQLite database carrying the real ``runs`` schema."""
    from deerflow.persistence.base import Base
    from deerflow.persistence.engine import close_engine, get_engine, get_session_factory, init_engine

    await init_engine("sqlite", url=f"sqlite+aiosqlite:///{tmp_path / 'a2.db'}", sqlite_dir=str(tmp_path))
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield RunRepository(get_session_factory())
    await close_engine()


async def _run_through_the_real_chain(repo, messages, *, thread_id: str = "t1") -> str:
    """Drive journal → completion → row exactly as ``worker.py`` does.

    Mirrors the worker's ``finally`` block: ``get_completion_data()`` splatted
    into ``update_run_completion``, then ``primary_model_name()`` stamped via
    ``update_model_name`` when the record has no model yet. Those two calls are
    the part that had no production evidence.
    """
    manager = RunManager(store=repo)
    record = await manager.create(thread_id)

    journal = RunJournal(record.run_id, thread_id, _FakeEventStore(), track_token_usage=True)
    for i, message in enumerate(messages):
        journal.on_llm_end(_LLMResult([message]), run_id=f"lc-{i}")

    await manager.update_run_completion(record.run_id, status=RunStatus.success.value, **journal.get_completion_data())

    if record.model_name is None:
        primary = journal.primary_model_name()
        if primary:
            await manager.update_model_name(record.run_id, primary)

    return record.run_id


class TestPerModelBucketReachesTheDatabase:
    @pytest.mark.anyio
    async def test_bucket_is_written_and_is_not_empty(self, repo):
        """The exact assertion no production row has ever satisfied."""
        run_id = await _run_through_the_real_chain(repo, [_real_ai_message("anthropic/claude-sonnet-5-5", input_tokens=1000, output_tokens=200)])

        row = await repo.get(run_id)

        assert row["token_usage_by_model"] != {}
        assert row["token_usage_by_model"]["anthropic/claude-sonnet-5-5"]["total_tokens"] == 1200

    @pytest.mark.anyio
    async def test_model_name_column_is_not_null(self, repo):
        """``model_name`` was NULL on all 869 historical rows (no writer)."""
        run_id = await _run_through_the_real_chain(repo, [_real_ai_message("anthropic/claude-sonnet-5-5", input_tokens=1000, output_tokens=200)])

        row = await repo.get(run_id)

        assert row["model_name"] == "anthropic/claude-sonnet-5-5"

    @pytest.mark.anyio
    async def test_cache_read_tokens_survive_into_the_row(self, repo):
        """The cache figure has to reach the DB, not just the accumulator."""
        run_id = await _run_through_the_real_chain(
            repo,
            [_real_ai_message("anthropic/claude-sonnet-5-5", input_tokens=10_000, output_tokens=500, cache_read=8_000)],
        )

        row = await repo.get(run_id)

        assert row["token_usage_by_model"]["anthropic/claude-sonnet-5-5"]["cache_read_tokens"] == 8_000

    @pytest.mark.anyio
    async def test_multi_model_run_keeps_both_buckets(self, repo):
        """A subagent on another model must not be folded into the lead's."""
        run_id = await _run_through_the_real_chain(
            repo,
            [
                _real_ai_message("anthropic/claude-sonnet-5-5", input_tokens=1000, output_tokens=100, msg_id="a"),
                _real_ai_message("qiniu/google/gemini-3.5-flash", input_tokens=5000, output_tokens=400, msg_id="b"),
            ],
        )

        row = await repo.get(run_id)

        assert row["token_usage_by_model"]["anthropic/claude-sonnet-5-5"]["total_tokens"] == 1100
        assert row["token_usage_by_model"]["qiniu/google/gemini-3.5-flash"]["total_tokens"] == 5400
        # Primary = biggest consumer, not the first call.
        assert row["model_name"] == "qiniu/google/gemini-3.5-flash"

    @pytest.mark.anyio
    async def test_no_cache_hits_leaves_the_key_absent(self, repo):
        """Sparse by design: "no hits" and "provider does not report" differ."""
        run_id = await _run_through_the_real_chain(repo, [_real_ai_message("m", input_tokens=100, output_tokens=10)])

        row = await repo.get(run_id)

        assert "cache_read_tokens" not in row["token_usage_by_model"]["m"]


class TestConsoleSeesTheNewRun:
    """The last leg: a written bucket must actually surface in the console."""

    @pytest.mark.anyio
    async def test_new_run_appears_in_the_per_model_breakdown(self, repo, monkeypatch):
        from deerflow.persistence.engine import get_session_factory
        from deerflow.persistence.thread_meta.model import ThreadMetaRow
        from deerflow.persistence.user.model import UserRow

        sf = get_session_factory()
        async with sf() as session:
            session.add_all([UserRow(id=ADMIN_ID, email="admin@example.com", system_role="admin"), ThreadMetaRow(thread_id="t1", user_id=ADMIN_ID)])
            await session.commit()

        await _run_through_the_real_chain(
            repo,
            [_real_ai_message("anthropic/claude-sonnet-5-5", input_tokens=1_000_000, output_tokens=200_000, cache_read=800_000)],
        )

        monkeypatch.setattr(console, "_attention_threshold", lambda: 0)

        def factory() -> User:
            return User(email="admin@example.com", password_hash="x", system_role="admin", id=ADMIN_ID)

        app = make_authed_test_app(user_factory=factory)
        app.include_router(console.router)
        with TestClient(app) as client:
            usage = client.get("/api/console/usage", params={"days": 7}).json()

        bucket = usage["by_model"]["anthropic/claude-sonnet-5-5"]
        assert bucket["runs"] == 1
        assert bucket["tokens"] == 1_200_000
        assert bucket["cache_read_tokens"] == 800_000

    @pytest.mark.anyio
    async def test_cache_discount_is_applied_to_the_new_run(self, repo, monkeypatch):
        """Cache-aware pricing end to end, using the real cost maths.

        1M input of which 800k cached, priced 10/M miss and 1/M hit:
        200k @10/M + 800k @1/M = 2.0 + 0.8 = 2.8, plus 200k output @30/M = 6.0.
        """
        from deerflow.persistence.engine import get_session_factory
        from deerflow.persistence.thread_meta.model import ThreadMetaRow
        from deerflow.persistence.user.model import UserRow

        sf = get_session_factory()
        async with sf() as session:
            session.add_all([UserRow(id=ADMIN_ID, email="admin@example.com", system_role="admin"), ThreadMetaRow(thread_id="t1", user_id=ADMIN_ID)])
            await session.commit()

        await _run_through_the_real_chain(
            repo,
            [_real_ai_message("m", input_tokens=1_000_000, output_tokens=200_000, cache_read=800_000)],
        )

        priced = console._ModelPricing(input_per_million=10.0, output_per_million=30.0, currency="CNY", input_cache_hit_per_million=1.0)

        async def _pricing(_sf=None):
            return {"m": priced}

        monkeypatch.setattr(console, "_build_pricing_map", _pricing)
        monkeypatch.setattr(console, "_attention_threshold", lambda: 0)

        def factory() -> User:
            return User(email="admin@example.com", password_hash="x", system_role="admin", id=ADMIN_ID)

        app = make_authed_test_app(user_factory=factory)
        app.include_router(console.router)
        with TestClient(app) as client:
            usage = client.get("/api/console/usage", params={"days": 7}).json()

        assert usage["by_model"]["m"]["cost"] == pytest.approx(8.8)
