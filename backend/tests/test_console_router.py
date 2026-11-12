"""Tests for the operations-console endpoints (T21).

Covers the two behaviors the port added on top of upstream:

1. **The admin dimension.** Upstream filters every query to the calling user
   with no platform-wide view. The local version adds one, so the visibility
   matrix (regular user / admin / admin drilling into one user) needs explicit
   coverage — including that a non-admin passing somebody else's ``user_id``
   gets **403** rather than silently receiving their own rows.
2. **operation_kind filtering.** A non-``run`` row must never reach the
   console's counters or spend.

Uses a real temp SQLite DB rather than mocks: every endpoint is a SQL
aggregation, and stubbing the session would test the stub.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from _router_auth_helpers import make_authed_test_app
from fastapi.testclient import TestClient

from app.gateway.auth.models import User
from app.gateway.routers import console
from deerflow.persistence.run.model import RunRow
from deerflow.persistence.thread_meta.model import ThreadMetaRow
from deerflow.persistence.user.model import UserRow

USER_A = "11111111-1111-1111-1111-111111111111"
USER_B = "22222222-2222-2222-2222-222222222222"
ADMIN_ID = "33333333-3333-3333-3333-333333333333"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
async def db(tmp_path):
    """Init a temp SQLite DB with the ORM schema, seed rows, tear down."""
    import deerflow.persistence.external_api_calls.model  # noqa: F401 — register table in Base.metadata
    from deerflow.persistence.base import Base
    from deerflow.persistence.engine import close_engine, get_engine, get_session_factory, init_engine

    await init_engine("sqlite", url=f"sqlite+aiosqlite:///{tmp_path / 'console.db'}", sqlite_dir=str(tmp_path))
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    sf = get_session_factory()
    now = datetime.now(UTC)
    async with sf() as session:
        session.add_all(
            [
                UserRow(id=USER_A, email="a@example.com", system_role="user"),
                UserRow(id=USER_B, email="b@example.com", system_role="user"),
                UserRow(id=ADMIN_ID, email="admin@example.com", system_role="admin"),
                ThreadMetaRow(thread_id="t-a", user_id=USER_A, display_name="A's thread"),
                ThreadMetaRow(thread_id="t-b", user_id=USER_B, display_name="B's thread"),
            ]
        )
        session.add_all(
            [
                # User A: one success (priced model), one failure.
                RunRow(
                    run_id="run-a1",
                    thread_id="t-a",
                    user_id=USER_A,
                    status="success",
                    operation_kind="run",
                    model_name="priced-model",
                    total_input_tokens=1_000_000,
                    total_output_tokens=500_000,
                    total_tokens=1_500_000,
                    subagent_tokens=200_000,
                    message_count=4,
                    created_at=now - timedelta(hours=2),
                    updated_at=now - timedelta(hours=1),
                    token_usage_by_model={"priced-model": {"input_tokens": 1_000_000, "output_tokens": 500_000, "total_tokens": 1_500_000}},
                ),
                RunRow(
                    run_id="run-a2",
                    thread_id="t-a",
                    user_id=USER_A,
                    status="error",
                    operation_kind="run",
                    model_name="priced-model",
                    total_tokens=10,
                    error="boom " * 200,
                    created_at=now - timedelta(hours=3),
                    updated_at=now - timedelta(hours=3),
                ),
                # User B: one success, must stay invisible to A.
                RunRow(
                    run_id="run-b1",
                    thread_id="t-b",
                    user_id=USER_B,
                    status="success",
                    operation_kind="run",
                    model_name="priced-model",
                    total_input_tokens=2_000_000,
                    total_output_tokens=0,
                    total_tokens=2_000_000,
                    created_at=now - timedelta(hours=1),
                    updated_at=now,
                    token_usage_by_model={"priced-model": {"input_tokens": 2_000_000, "output_tokens": 0, "total_tokens": 2_000_000}},
                ),
                # Not a real run: must be excluded from every endpoint.
                RunRow(
                    run_id="run-a-bookkeeping",
                    thread_id="t-a",
                    user_id=USER_A,
                    status="success",
                    operation_kind="checkpoint_write",
                    model_name="priced-model",
                    total_input_tokens=999_000_000,
                    total_output_tokens=999_000_000,
                    total_tokens=999_000_000,
                    created_at=now - timedelta(hours=1),
                    updated_at=now,
                ),
            ]
        )
        await session.commit()

    yield sf
    await close_engine()


def _make_app(*, user_id: str, system_role: str = "user") -> TestClient:
    def factory() -> User:
        return User(email=f"{user_id}@example.com", password_hash="x", system_role=system_role, id=user_id)

    app = make_authed_test_app(user_factory=factory)
    app.include_router(console.router)
    return TestClient(app)


def _const_pricing(mapping: dict):
    """An async stand-in for ``_build_pricing_map`` returning a fixed map.

    The real builder is async (it reads the ``model_pricing`` table), so a
    plain lambda would hand the handler a dict where it awaits a coroutine.
    """

    async def _stub(_session_factory=None):
        return mapping

    return _stub


@pytest.fixture()
def priced(monkeypatch):
    """Give ``priced-model`` a real rate so cost fields are exercised."""
    entry = console._ModelPricing(input_per_million=10.0, output_per_million=30.0, currency="CNY")
    monkeypatch.setattr(console, "_build_pricing_map", _const_pricing({"priced-model": entry}))


@pytest.fixture()
def no_threshold(monkeypatch):
    monkeypatch.setattr(console, "_attention_threshold", lambda: 0)


# ---------------------------------------------------------------------------
# Cross-user isolation / admin dimension
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_non_admin_requesting_other_user_is_forbidden(db, no_threshold):
    """The headline security property: 403, never a silent downgrade.

    Returning the caller's own data for a foreign ``user_id`` would make a
    cross-user probe indistinguishable from a successful read.
    """
    with _make_app(user_id=USER_A) as client:
        for path in (
            "/api/console/stats",
            "/api/console/runs",
            "/api/console/usage",
            "/api/console/llm-usage",
            "/api/console/external-calls",
        ):
            response = client.get(path, params={"user_id": USER_B})
            assert response.status_code == 403, path
            # And crucially: no data leaked in the error body.
            assert "run-b1" not in response.text


@pytest.mark.anyio
async def test_non_admin_may_pass_own_user_id(db, no_threshold):
    """Explicitly naming yourself is allowed — it is a no-op filter."""
    with _make_app(user_id=USER_A) as client:
        for path in (
            "/api/console/stats",
            "/api/console/runs",
            "/api/console/usage",
            "/api/console/llm-usage",
            "/api/console/external-calls",
        ):
            response = client.get(path, params={"user_id": USER_A})
            assert response.status_code == 200, path
            assert response.json()["scope"] == {"is_admin": False, "all_users": False, "user_id": USER_A}


@pytest.mark.anyio
async def test_non_admin_sees_only_own_runs(db, no_threshold):
    with _make_app(user_id=USER_A) as client:
        response = client.get("/api/console/runs")
    assert response.status_code == 200
    body = response.json()
    ids = {r["run_id"] for r in body["runs"]}
    assert ids == {"run-a1", "run-a2"}
    assert body["scope"]["all_users"] is False
    # Owner columns are admin-only; a regular user would only see themselves.
    assert all(r["user_id"] is None and r["user_email"] is None for r in body["runs"])


@pytest.mark.anyio
async def test_admin_sees_every_users_runs(db, no_threshold):
    with _make_app(user_id=ADMIN_ID, system_role="admin") as client:
        response = client.get("/api/console/runs")
    assert response.status_code == 200
    body = response.json()
    assert {r["run_id"] for r in body["runs"]} == {"run-a1", "run-a2", "run-b1"}
    assert body["scope"] == {"is_admin": True, "all_users": True, "user_id": None}
    emails = {r["user_email"] for r in body["runs"]}
    assert emails == {"a@example.com", "b@example.com"}


@pytest.mark.anyio
async def test_admin_can_drill_into_one_user(db, no_threshold):
    with _make_app(user_id=ADMIN_ID, system_role="admin") as client:
        response = client.get("/api/console/runs", params={"user_id": USER_B})
    assert response.status_code == 200
    body = response.json()
    assert {r["run_id"] for r in body["runs"]} == {"run-b1"}
    assert body["scope"] == {"is_admin": True, "all_users": False, "user_id": USER_B}


@pytest.mark.anyio
async def test_users_endpoint_is_admin_only(db):
    with _make_app(user_id=USER_A) as client:
        assert client.get("/api/console/users").status_code == 403


@pytest.mark.anyio
async def test_users_endpoint_lists_totals_for_admin(db):
    with _make_app(user_id=ADMIN_ID, system_role="admin") as client:
        response = client.get("/api/console/users")
    assert response.status_code == 200
    by_id = {u["user_id"]: u for u in response.json()["users"]}
    assert by_id[USER_A]["total_runs"] == 2  # bookkeeping row excluded
    assert by_id[USER_A]["total_tokens"] == 1_500_010
    assert by_id[USER_B]["total_runs"] == 1
    # A user with no runs still appears, with zeroes (outer join).
    assert by_id[ADMIN_ID]["total_runs"] == 0


# ---------------------------------------------------------------------------
# operation_kind filtering
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_bookkeeping_rows_excluded_everywhere(db, no_threshold):
    """A non-"run" row must not reach counts, tokens, or the listing."""
    with _make_app(user_id=ADMIN_ID, system_role="admin") as client:
        stats = client.get("/api/console/stats").json()
        runs = client.get("/api/console/runs").json()
        usage = client.get("/api/console/usage", params={"days": 7}).json()

    assert stats["total_runs"] == 3
    # 999M-token bookkeeping row would dominate if it leaked through.
    assert stats["total_tokens"] == 1_500_010 + 2_000_000
    assert "run-a-bookkeeping" not in {r["run_id"] for r in runs["runs"]}
    assert usage["total_runs"] == 3


# ---------------------------------------------------------------------------
# Stats / usage content
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_stats_counters_for_own_scope(db, no_threshold):
    with _make_app(user_id=USER_A) as client:
        body = client.get("/api/console/stats").json()
    assert body["total_runs"] == 2
    assert body["failed_runs"] == 1
    assert body["active_runs"] == 0
    assert body["total_tokens"] == 1_500_010


@pytest.mark.anyio
async def test_cost_is_null_when_no_pricing_configured(db, no_threshold, monkeypatch):
    """Unpriced deployment reports null, not 0 — 0 would read as "free"."""
    monkeypatch.setattr(console, "_build_pricing_map", _const_pricing({}))
    with _make_app(user_id=USER_A) as client:
        body = client.get("/api/console/stats").json()
    assert body["total_cost"] is None
    assert body["currency"] is None


@pytest.mark.anyio
async def test_cost_computed_from_per_model_breakdown(db, no_threshold, priced):
    with _make_app(user_id=USER_A) as client:
        body = client.get("/api/console/stats").json()
    # 1M input @10/M + 0.5M output @30/M = 10 + 15
    assert body["total_cost"] == pytest.approx(25.0)
    assert body["currency"] == "CNY"


@pytest.mark.anyio
async def test_unpriced_days_report_null_not_zero(db, no_threshold, monkeypatch):
    """An unpriced window must not claim each day was free.

    ``total_cost`` already returned null here, but every ``day.cost`` stayed
    0.00 — one response contradicting itself, and the frontend renders 0.00 as
    a real amount. Triggered by the shipped placeholder pricing (all zeros).
    """
    monkeypatch.setattr(console, "_build_pricing_map", _const_pricing({}))
    with _make_app(user_id=USER_A) as client:
        body = client.get("/api/console/usage", params={"days": 7}).json()

    assert body["total_cost"] is None
    assert [d["cost"] for d in body["days"]] == [None] * 7
    # Sanity: the window really does contain usage, so this is not vacuous.
    assert body["total_tokens"] > 0


@pytest.mark.anyio
async def test_priced_days_report_numbers_and_empty_days_stay_null(db, no_threshold, priced):
    """With pricing on, days that saw spend carry it; untouched days stay null."""
    # 复审二轮（2026-09-13）确定性：与 test_usage_day_has_chat_and_llm_token_split
    # 同族——fixture 行播在 now-2h/now-3h，UTC 跨日窗口内 a2 独占昨日桶（有 runs
    # 无 priced run → cost None）触发假红。重钉到当前时刻，断言原样。
    from sqlalchemy import select

    from deerflow.persistence.engine import get_session_factory

    _now = datetime.now(UTC)
    _sf = get_session_factory()
    async with _sf() as session:
        _rows = (await session.execute(select(RunRow).where(RunRow.run_id.in_(["run-a1", "run-a2"])))).scalars().all()
        for _r in _rows:
            _r.created_at = _now
            _r.updated_at = _now
        await session.commit()

    with _make_app(user_id=USER_A) as client:
        body = client.get("/api/console/usage", params={"days": 14}).json()

    priced_days = [d for d in body["days"] if d["runs"] > 0]
    assert priced_days, "expected at least one day with runs"
    assert all(isinstance(d["cost"], (int, float)) for d in priced_days)
    # A zero-filled day never had a priced run, so "unknown" is the honest value.
    assert all(d["cost"] is None for d in body["days"] if d["runs"] == 0)


@pytest.mark.anyio
async def test_usage_buckets_by_day_and_model(db, no_threshold, priced):
    with _make_app(user_id=USER_A) as client:
        body = client.get("/api/console/usage", params={"days": 14}).json()
    assert len(body["days"]) == 14  # zero-filled window
    assert body["total_runs"] == 2
    assert body["total_tokens"] == 1_500_010
    assert body["subagent_tokens"] == 200_000
    assert body["by_model"]["priced-model"]["tokens"] == 1_500_000 + 10
    assert sum(d["total_tokens"] for d in body["days"]) == 1_500_010


@pytest.mark.anyio
async def test_run_item_fields(db, no_threshold, priced):
    with _make_app(user_id=USER_A) as client:
        runs = client.get("/api/console/runs").json()["runs"]
    item = next(r for r in runs if r["run_id"] == "run-a1")
    assert item["thread_title"] == "A's thread"
    assert item["duration_seconds"] == pytest.approx(3600, abs=5)
    assert item["subagent_tokens"] == 200_000
    assert item["cost"] == pytest.approx(25.0)
    failed = next(r for r in runs if r["run_id"] == "run-a2")
    # Error text is excerpted, not dumped whole, into a list response.
    assert len(failed["error"]) == console._ERROR_EXCERPT_CHARS


@pytest.mark.anyio
async def test_runs_sorting_and_paging(db, no_threshold):
    with _make_app(user_id=ADMIN_ID, system_role="admin") as client:
        asc = client.get("/api/console/runs", params={"sort_by": "total_tokens", "sort_dir": "asc"}).json()
        page1 = client.get("/api/console/runs", params={"limit": 1}).json()
    assert [r["total_tokens"] for r in asc["runs"]] == [10, 1_500_000, 2_000_000]
    assert asc["has_more"] is False
    assert len(page1["runs"]) == 1
    assert page1["has_more"] is True


@pytest.mark.anyio
async def test_status_filter(db, no_threshold):
    with _make_app(user_id=ADMIN_ID, system_role="admin") as client:
        body = client.get("/api/console/runs", params={"status": "error"}).json()
    assert {r["run_id"] for r in body["runs"]} == {"run-a2"}


# ---------------------------------------------------------------------------
# T20 threshold flag surfacing
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_over_threshold_flag_surfaces_in_console(db, monkeypatch):
    """T20's crossing must be visible in the console, per the plan."""
    monkeypatch.setattr(console, "_attention_threshold", lambda: 1_000_000)
    with _make_app(user_id=USER_A) as client:
        stats = client.get("/api/console/stats").json()
        runs = client.get("/api/console/runs").json()["runs"]
    assert stats["over_threshold_runs"] == 1
    flags = {r["run_id"]: r["over_threshold"] for r in runs}
    assert flags == {"run-a1": True, "run-a2": False}


@pytest.mark.anyio
async def test_threshold_zero_disables_flag(db, monkeypatch):
    monkeypatch.setattr(console, "_attention_threshold", lambda: 0)
    with _make_app(user_id=USER_A) as client:
        stats = client.get("/api/console/stats").json()
        runs = client.get("/api/console/runs").json()["runs"]
    assert stats["over_threshold_runs"] == 0
    assert all(r["over_threshold"] is False for r in runs)


# ---------------------------------------------------------------------------
# Memory backend
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_returns_503_without_sql_backend(monkeypatch):
    """The memory backend keeps no run history, so there is nothing to report."""
    monkeypatch.setattr(console, "get_session_factory", lambda: None)
    with _make_app(user_id=USER_A) as client:
        for path in ("/api/console/stats", "/api/console/runs", "/api/console/usage"):
            assert client.get(path).status_code == 503, path


@pytest.mark.anyio
async def test_users_returns_503_without_sql_backend_for_an_admin(monkeypatch):
    """``/users`` is the fourth endpoint behind ``_session_factory_or_503``.

    It cannot join the loop above, which uses a **non-admin** client: that
    caller gets 403 from ``/users`` regardless of the database backend, so the
    assertion would pass for entirely the wrong reason and keep passing even
    if the 503 guard were deleted.
    """
    monkeypatch.setattr(console, "get_session_factory", lambda: None)
    with _make_app(user_id=ADMIN_ID, system_role="admin") as client:
        assert client.get("/api/console/users").status_code == 503


@pytest.mark.anyio
async def test_users_reports_the_missing_database_before_the_missing_role(monkeypatch):
    """Ordering: ``_session_factory_or_503()`` runs *before* the admin check.

    This is the behavior worth pinning, and it needs a **non-admin** caller on
    a **memory backend** — the one case where the two guards disagree. Both
    the admin-only test above and the 403 test below stay green under a
    swapped order; only this one moves.

    Operationally: an operator who has not switched to a SQL backend should be
    told "this needs a database", not "admin access required", which would
    send them chasing a permissions problem that does not exist.
    """
    monkeypatch.setattr(console, "get_session_factory", lambda: None)
    with _make_app(user_id=USER_A) as client:
        assert client.get("/api/console/users").status_code == 503


@pytest.mark.anyio
async def test_users_still_403s_for_a_non_admin_when_the_db_is_present(db):
    """The companion: with a working DB, the role is what decides.

    Together these three pin that 503 and 403 are each reachable for the
    right reason, so neither check can silently mask the other.
    """
    with _make_app(user_id=USER_A) as client:
        assert client.get("/api/console/users").status_code == 403


# ---------------------------------------------------------------------------
# Pricing helpers
# ---------------------------------------------------------------------------


class TestPricingMap:
    def _cfg(self, name, pricing, model=None):
        from types import SimpleNamespace

        return SimpleNamespace(name=name, model=model, pricing=pricing)

    def _patch_models(self, monkeypatch, models):
        from types import SimpleNamespace

        monkeypatch.setattr(console, "get_app_config", lambda: SimpleNamespace(models=models))

    def test_placeholder_zero_pricing_is_treated_as_unpriced(self, monkeypatch):
        """The shipped config.yaml placeholders must NOT fabricate a cost."""
        self._patch_models(monkeypatch, [self._cfg("m", {"currency": "CNY", "input_per_million": 0, "output_per_million": 0, "input_cache_hit_per_million": 0})])
        assert console._build_yaml_pricing_map() == {}

    def test_keys_on_both_config_name_and_provider_model_id(self, monkeypatch):
        self._patch_models(monkeypatch, [self._cfg("friendly", {"input_per_million": 1, "output_per_million": 2}, model="Provider/Model-1")])
        pricing = console._build_yaml_pricing_map()
        assert console._lookup_pricing(pricing, "friendly") is not None
        assert console._lookup_pricing(pricing, "Provider/Model-1") is not None
        assert console._lookup_pricing(pricing, "provider/model-1") is not None
        assert console._lookup_pricing(pricing, "unknown") is None

    def test_gateway_prefix_peels_to_config_row(self):
        """P1 (2026-08-18): 360 网关回报的厂商前缀 id（alibaba/qwen/qwen3.8-max）
        逐段剥离后命中表里的 config name 行（qwen3.8-max）。"""
        row = console._ModelPricing(12.0, 36.0, "CNY")
        pricing = {"qwen3.8-max": row}
        assert console._lookup_pricing(pricing, "alibaba/qwen/qwen3.8-max") is row
        assert console._lookup_pricing(pricing, "qwen/qwen3.8-max") is row

    def test_exact_match_always_beats_peeled_candidate(self):
        """精确命中优先——DB 里若真有带前缀的行，剥离合成键不得抢匹配。"""
        exact = console._ModelPricing(99.0, 99.0, "CNY")
        short = console._ModelPricing(12.0, 36.0, "CNY")
        pricing = {"alibaba/qwen/qwen3.8-max": exact, "qwen3.8-max": short}
        assert console._lookup_pricing(pricing, "alibaba/qwen/qwen3.8-max") is exact

    def test_lowercase_variant_along_peel_chain(self):
        row = console._ModelPricing(12.0, 36.0, "CNY")
        pricing = {"qwen3.8-max": row}
        assert console._lookup_pricing(pricing, "Alibaba/Qwen/Qwen3.8-MAX") is row

    def test_unknown_model_still_none_after_peeling(self):
        pricing = {"qwen3.8-max": console._ModelPricing(12.0, 36.0, "CNY")}
        assert console._lookup_pricing(pricing, "unknown/model/id") is None
        assert console._lookup_pricing(pricing, "qwen3.8") is None  # 不做子串/后缀猜测

    def test_peeled_segment_can_match_another_models_row(self):
        """计划 §6 已接受的风险：剥离段可能命中另一模型的价格行（精确键优先
        缓解、单货币小目录下可接受）——钉住当前行为防意外漂移。"""
        row = console._ModelPricing(1.0, 2.0, "CNY")
        pricing = {"gemini-3.6-flash": row}
        assert console._lookup_pricing(pricing, "vendor/gemini-3.6-flash") is row

    def test_b2_amount_bucket_with_prefix_bypasses_lookup(self):
        """B2 + P1: 带网关前缀且携带 real-bill amount 的桶直接用 amount，
        剥离逻辑根本不参与。"""
        pricing = {"qwen3.8-max": console._ModelPricing(12.0, 36.0, "CNY")}
        cost = console._run_cost(
            pricing,
            model_name=None,
            total_input_tokens=None,
            total_output_tokens=None,
            token_usage_by_model={
                "alibaba/qwen/qwen3.8-max": {"input_tokens": 1_000_000, "output_tokens": 0, "amount": 5.0},
            },
        )
        assert cost == pytest.approx(5.0)  # 不是 12.0 的估算

    def test_prefixed_bucket_without_amount_estimates_via_peeling(self):
        """P1 主场景：带前缀、无 amount 的桶经剥离命中价目行后正常估算——
        修复前这里返回 None（前端不显示花费）。"""
        pricing = {"qwen3.8-max": console._ModelPricing(12.0, 36.0, "CNY")}
        cost = console._run_cost(
            pricing,
            model_name=None,
            total_input_tokens=None,
            total_output_tokens=None,
            token_usage_by_model={
                "alibaba/qwen/qwen3.8-max": {"input_tokens": 1_000_000, "output_tokens": 0},
            },
        )
        assert cost == pytest.approx(12.0)

    def test_mixed_currencies_disable_cost_reporting(self, monkeypatch):
        self._patch_models(
            monkeypatch,
            [
                self._cfg("a", {"currency": "CNY", "input_per_million": 1, "output_per_million": 1}),
                self._cfg("b", {"currency": "USD", "input_per_million": 1, "output_per_million": 1}),
            ],
        )
        assert console._build_yaml_pricing_map() == {}

    def test_malformed_pricing_is_skipped_not_fatal(self, monkeypatch):
        self._patch_models(
            monkeypatch,
            [
                self._cfg("bad", {"input_per_million": "abc"}),
                self._cfg("good", {"input_per_million": 5, "output_per_million": 5}),
            ],
        )
        pricing = console._build_yaml_pricing_map()
        assert "good" in pricing and "bad" not in pricing

    def test_cache_hits_billed_at_hit_price(self):
        price = console._ModelPricing(input_per_million=10.0, output_per_million=0.0, currency="CNY", input_cache_hit_per_million=1.0)
        # 1M input of which 0.6M cached: 0.4M @10 + 0.6M @1 = 4 + 0.6
        assert console._token_cost(1_000_000, 0, price, 600_000) == pytest.approx(4.6)

    def test_cache_reads_clamped_to_input(self):
        price = console._ModelPricing(input_per_million=10.0, output_per_million=0.0, currency="CNY", input_cache_hit_per_million=0.0)
        # Nonsense cache_read > input must not produce negative spend.
        assert console._token_cost(1_000_000, 0, price, 9_000_000) == pytest.approx(0.0)

    def test_missing_hit_price_bills_cache_at_full_input_price(self):
        price = console._ModelPricing(input_per_million=10.0, output_per_million=0.0, currency="CNY")
        assert console._token_cost(1_000_000, 0, price, 1_000_000) == pytest.approx(10.0)

    def test_run_cost_none_when_model_unpriced(self):
        assert (
            console._run_cost(
                {},
                model_name="whatever",
                total_input_tokens=100,
                total_output_tokens=100,
                token_usage_by_model=None,
            )
            is None
        )

    def test_run_cost_falls_back_to_run_level_totals(self):
        pricing = {"m": console._ModelPricing(10.0, 30.0, "CNY")}
        cost = console._run_cost(pricing, model_name="m", total_input_tokens=1_000_000, total_output_tokens=1_000_000, token_usage_by_model={})
        assert cost == pytest.approx(40.0)

    def test_run_cost_prefers_per_model_breakdown(self):
        """Multi-model runs must not be priced at the single run-level model."""
        pricing = {"cheap": console._ModelPricing(1.0, 1.0, "CNY"), "dear": console._ModelPricing(100.0, 100.0, "CNY")}
        cost = console._run_cost(
            pricing,
            model_name="dear",
            total_input_tokens=1_000_000,
            total_output_tokens=0,
            token_usage_by_model={"cheap": {"input_tokens": 1_000_000, "output_tokens": 0}},
        )
        assert cost == pytest.approx(1.0)

    def test_unpriced_buckets_ignored_but_priced_ones_still_count(self):
        pricing = {"known": console._ModelPricing(10.0, 0.0, "CNY")}
        cost = console._run_cost(
            pricing,
            model_name=None,
            total_input_tokens=None,
            total_output_tokens=None,
            token_usage_by_model={
                "known": {"input_tokens": 1_000_000, "output_tokens": 0},
                "mystery": {"input_tokens": 5_000_000, "output_tokens": 0},
            },
        )
        assert cost == pytest.approx(10.0)

    def test_run_cost_prefers_real_amount_over_estimation(self):
        """B2: when a bucket has ``amount``, use it directly instead of estimating."""
        pricing = {"m": console._ModelPricing(10.0, 30.0, "CNY")}
        cost = console._run_cost(
            pricing,
            model_name="m",
            total_input_tokens=1_000_000,
            total_output_tokens=0,
            token_usage_by_model={"m": {"input_tokens": 1_000_000, "output_tokens": 0, "amount": 5.0}},
        )
        # 5.0 (real bill) instead of 10.0 (estimated from 1M * 10/M)
        assert cost == pytest.approx(5.0)

    def test_run_cost_mixed_amount_and_estimation(self):
        """B2: a mixed run sums real-bill + estimated."""
        pricing = {"with_amount": console._ModelPricing(10.0, 0.0, "CNY"), "estimated": console._ModelPricing(8.0, 0.0, "CNY")}
        cost = console._run_cost(
            pricing,
            model_name=None,
            total_input_tokens=None,
            total_output_tokens=None,
            token_usage_by_model={
                "with_amount": {"input_tokens": 1_000_000, "output_tokens": 0, "amount": 3.0},
                "estimated": {"input_tokens": 1_000_000, "output_tokens": 0},
            },
        )
        # 3.0 (real) + 8.0 (estimated from 1M * 8/M) = 11.0
        assert cost == pytest.approx(11.0)

    def test_run_cost_all_amount_no_pricing_needed(self):
        """B2: all buckets with amount → cost without needing pricing config."""
        cost = console._run_cost(
            {},
            model_name=None,
            total_input_tokens=None,
            total_output_tokens=None,
            token_usage_by_model={"m1": {"input_tokens": 100, "output_tokens": 50, "amount": 1.5}},
        )
        assert cost == pytest.approx(1.5)


# ---------------------------------------------------------------------------
# Malformed per-model buckets (A3)
# ---------------------------------------------------------------------------


@pytest.fixture()
async def malformed_bucket_db(db):
    """Add a run whose ``token_usage_by_model`` value is not a dict.

    The column is provider-fed JSON, so a shape change upstream lands here as
    a string/int/null rather than the expected ``{input_tokens: ...}`` map.
    """
    from deerflow.persistence.engine import get_session_factory

    sf = get_session_factory()
    now = datetime.now(UTC)
    async with sf() as session:
        session.add(
            RunRow(
                run_id="run-a-malformed",
                thread_id="t-a",
                user_id=USER_A,
                status="success",
                operation_kind="run",
                model_name="priced-model",
                total_tokens=0,
                created_at=now - timedelta(hours=1),
                updated_at=now - timedelta(hours=1),
                # Not a dict — the shape the guard at the loop head exists for.
                token_usage_by_model={"priced-model": "not-a-dict"},
            )
        )
        await session.commit()
    return sf


@pytest.mark.anyio
async def test_malformed_bucket_does_not_inflate_model_run_count(malformed_bucket_db, no_threshold):
    """A bucket we cannot read must not be counted as a run of that model.

    ``entry.runs += 1`` used to sit *above* the ``isinstance(usage, dict)``
    guard, so an unreadable bucket still incremented the model's run tally
    while contributing no tokens. The result was a model row reporting more
    runs than the tokens could account for — the operator sees a plausible
    number with no way to tell it is wrong.
    """
    with _make_app(user_id=USER_A) as client:
        response = client.get("/api/console/usage", params={"days": 7})

    assert response.status_code == 200
    by_model = response.json()["by_model"]
    # Two legitimate contributions to priced-model in this window:
    #   run-a1 — a well-formed per-model bucket (1_500_000 tokens)
    #   run-a2 — no bucket at all, counted via the legacy model_name fallback
    # run-a-malformed must contribute neither a run nor a token.
    # Before the fix this read 3.
    assert by_model["priced-model"]["runs"] == 2
    assert by_model["priced-model"]["tokens"] == 1_500_010


@pytest.mark.anyio
async def test_malformed_bucket_still_leaves_other_models_intact(malformed_bucket_db, no_threshold):
    """One bad bucket must not abort the loop over the remaining models."""
    from deerflow.persistence.engine import get_session_factory

    sf = get_session_factory()
    now = datetime.now(UTC)
    async with sf() as session:
        session.add(
            RunRow(
                run_id="run-a-mixed",
                thread_id="t-a",
                user_id=USER_A,
                status="success",
                operation_kind="run",
                model_name="priced-model",
                total_tokens=42,
                created_at=now - timedelta(hours=1),
                updated_at=now - timedelta(hours=1),
                token_usage_by_model={"broken": None, "good-model": {"input_tokens": 42, "output_tokens": 0, "total_tokens": 42}},
            )
        )
        await session.commit()

    with _make_app(user_id=USER_A) as client:
        body = client.get("/api/console/usage", params={"days": 7}).json()

    assert body["by_model"]["good-model"]["runs"] == 1
    assert body["by_model"]["good-model"]["tokens"] == 42
    assert "broken" not in body["by_model"]


# ---------------------------------------------------------------------------
# Removed leak-prone counters (A4)
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_stats_does_not_expose_platform_wide_agent_count(db, no_threshold):
    """``total_agents`` was a cross-user information leak, so it is gone.

    ``list_custom_agents()`` takes no arguments and reads a single global
    ``agents_dir`` — it has no notion of the calling user. The old field
    therefore handed **every** authenticated caller the platform-wide custom
    agent count, while its own schema description claimed "visible to the
    caller". ``agents.py`` gets this right by filtering the same call through
    ``_can_see_agent``; the console never did.

    The field was also never rendered by the frontend, so it is removed
    outright rather than given a scoped implementation nobody asked for.

    Asserted from a **regular user's** response: that is the caller who must
    not learn the platform-wide figure.
    """
    with _make_app(user_id=USER_A) as client:
        response = client.get("/api/console/stats")

    assert response.status_code == 200
    body = response.json()
    assert "total_agents" not in body
    # No field may carry the same count under a different name.
    assert not any("agent" in key.lower() for key in body)
    # And the leaky scan itself is gone from the module, so it cannot be
    # reintroduced by a caller that forgets the scoping rule.
    assert not hasattr(console, "list_custom_agents")


@pytest.mark.anyio
async def test_stats_no_longer_reports_the_unused_thread_counter(db, no_threshold):
    """``total_threads`` was dead weight: correct, but never rendered.

    Kept in the same task as ``total_agents`` because both are unused
    response fields; removing only the leaky one would leave the cost of the
    extra COUNT query for nobody's benefit.
    """
    with _make_app(user_id=USER_A) as client:
        body = client.get("/api/console/stats").json()

    assert "total_threads" not in body
    # The fields the dashboard actually renders must survive.
    assert body["total_runs"] == 2
    assert body["total_tokens"] == 1_500_010


# ---------------------------------------------------------------------------
# DB pricing overrides yaml (B3)
# ---------------------------------------------------------------------------


class _FakeModelCfg:
    """Stands in for a ``ModelConfig`` entry carrying a ``pricing`` dict."""

    def __init__(self, name: str, pricing: dict | None, model: str | None = None):
        self.name = name
        self.pricing = pricing
        self.model = model


@pytest.fixture()
def yaml_models(monkeypatch):
    """Install a fake ``config.models`` list and return a mutator for it."""
    models: list[_FakeModelCfg] = []

    class _Cfg:
        pass

    def _fake_get_app_config():
        cfg = _Cfg()
        cfg.models = models
        return cfg

    monkeypatch.setattr(console, "get_app_config", _fake_get_app_config)
    return models


@pytest.mark.anyio
async def test_db_price_overrides_the_yaml_price(db, yaml_models):
    """The whole point of the table: an admin edit beats the shipped yaml."""
    from deerflow.persistence.engine import get_session_factory
    from deerflow.persistence.model_pricing.model import ModelPricingRow

    yaml_models.append(_FakeModelCfg("m", {"currency": "CNY", "input_per_million": 1.0, "output_per_million": 2.0}))

    sf = get_session_factory()
    async with sf() as session:
        session.add(
            ModelPricingRow(
                model_name="m",
                currency="CNY",
                input_per_million=99.0,
                output_per_million=100.0,
                input_cache_hit_per_million=None,
            )
        )
        await session.commit()

    pricing = await console._build_pricing_map(sf)

    assert pricing["m"].input_per_million == 99.0
    assert pricing["m"].output_per_million == 100.0


@pytest.mark.anyio
async def test_yaml_price_is_used_when_the_db_has_no_row(db, yaml_models):
    """The yaml stays a working offline/bootstrap fallback."""
    from deerflow.persistence.engine import get_session_factory

    yaml_models.append(_FakeModelCfg("m", {"currency": "CNY", "input_per_million": 1.0, "output_per_million": 2.0}))

    pricing = await console._build_pricing_map(get_session_factory())

    assert pricing["m"].input_per_million == 1.0


@pytest.mark.anyio
async def test_unpriced_everywhere_stays_unpriced(db, yaml_models):
    """Neither source configured → no entry, so the console renders "—"."""
    from deerflow.persistence.engine import get_session_factory

    yaml_models.append(_FakeModelCfg("m", None))

    pricing = await console._build_pricing_map(get_session_factory())

    assert "m" not in pricing


@pytest.mark.anyio
async def test_db_null_cache_hit_price_survives_the_merge(db, yaml_models):
    """null must not be flattened to 0.0 on the way through the merge.

    A 0.0 here would bill every cache hit as free — the exact silent
    under-report the nullable column exists to prevent.
    """
    from deerflow.persistence.engine import get_session_factory
    from deerflow.persistence.model_pricing.model import ModelPricingRow

    sf = get_session_factory()
    async with sf() as session:
        session.add(
            ModelPricingRow(
                model_name="m",
                currency="CNY",
                input_per_million=10.0,
                output_per_million=20.0,
                input_cache_hit_per_million=None,
            )
        )
        await session.commit()

    pricing = await console._build_pricing_map(sf)

    assert pricing["m"].input_cache_hit_per_million is None


@pytest.mark.anyio
async def test_db_zero_cache_hit_price_survives_the_merge(db, yaml_models):
    """0.0 means "hits are free" and must reach the cost maths intact."""
    from deerflow.persistence.engine import get_session_factory
    from deerflow.persistence.model_pricing.model import ModelPricingRow

    sf = get_session_factory()
    async with sf() as session:
        session.add(
            ModelPricingRow(
                model_name="m",
                currency="CNY",
                input_per_million=10.0,
                output_per_million=20.0,
                input_cache_hit_per_million=0.0,
            )
        )
        await session.commit()

    pricing = await console._build_pricing_map(sf)

    assert pricing["m"].input_cache_hit_per_million == 0.0


@pytest.mark.anyio
async def test_db_and_yaml_models_are_both_present(db, yaml_models):
    """Merging is per-model, not all-or-nothing."""
    from deerflow.persistence.engine import get_session_factory
    from deerflow.persistence.model_pricing.model import ModelPricingRow

    yaml_models.append(_FakeModelCfg("yaml-only", {"currency": "CNY", "input_per_million": 1.0, "output_per_million": 2.0}))

    sf = get_session_factory()
    async with sf() as session:
        session.add(ModelPricingRow(model_name="db-only", currency="CNY", input_per_million=5.0, output_per_million=6.0))
        await session.commit()

    pricing = await console._build_pricing_map(sf)

    assert pricing["yaml-only"].input_per_million == 1.0
    assert pricing["db-only"].input_per_million == 5.0


@pytest.mark.anyio
async def test_pricing_change_takes_effect_without_a_restart(db, yaml_models):
    """Priced via the endpoint, read back on the very next request.

    Config-file pricing could not do this: Gateway dependencies read a
    startup snapshot (see the Config Hot-Reload Boundary note in CLAUDE.md),
    so a yaml edit would need a process restart.
    """
    from deerflow.persistence.engine import get_session_factory
    from deerflow.persistence.model_pricing.model import ModelPricingRow

    sf = get_session_factory()
    before = await console._build_pricing_map(sf)
    assert "m" not in before

    async with sf() as session:
        session.add(ModelPricingRow(model_name="m", currency="CNY", input_per_million=7.0, output_per_million=8.0))
        await session.commit()

    after = await console._build_pricing_map(sf)
    assert after["m"].input_per_million == 7.0


@pytest.mark.anyio
async def test_build_pricing_map_returns_a_mapping_not_a_currency(db, yaml_models):
    """``_build_pricing_map`` must return the pricing dict, nothing else.

    Regression guard: a stray second ``return`` once sat below the real one,
    left over from splitting ``_pricing_currency`` out of this function. It was
    unreachable so nothing failed — ruff does not flag unreachable code — but
    deleting the line above it would have silently changed the return type from
    ``dict[str, _ModelPricing]`` to a currency string, and every caller
    (``_lookup_pricing``, ``_pricing_currency``, the cost sums) would then read
    a string as a mapping.

    Asserting the *shape* rather than the absence of a line keeps this honest
    if the function is ever restructured again.
    """
    from deerflow.persistence.engine import get_session_factory

    yaml_models.append(_FakeModelCfg("m", {"currency": "CNY", "input_per_million": 8.0, "output_per_million": 32.0}))

    pricing = await console._build_pricing_map(get_session_factory())

    assert isinstance(pricing, dict)
    assert isinstance(pricing["m"], console._ModelPricing)
    # The currency lives on the entries; it is _pricing_currency's job to pick
    # one out, and that function must stay separate from this one.
    assert console._pricing_currency(pricing) == "CNY"


# ---------------------------------------------------------------------------
# C2: Non-conversation LLM usage endpoint
# ---------------------------------------------------------------------------


async def _seed_llm_bookkeeping(sf, domain: str, user_id: str, *, tokens: int = 100, status: str = "success", amount: float | None = None):
    """Insert one llm_bookkeeping row for testing."""
    from uuid import uuid4

    bucket: dict = {"input_tokens": tokens // 2, "output_tokens": tokens // 2, "total_tokens": tokens}
    if amount is not None:
        bucket["amount"] = amount

    async with sf() as session:
        session.add(
            RunRow(
                run_id=f"llm_{uuid4().hex[:12]}",
                thread_id=domain,
                user_id=user_id,
                status=status,
                operation_kind="llm_bookkeeping",
                model_name="test-model",
                total_input_tokens=tokens // 2,
                total_output_tokens=tokens // 2,
                total_tokens=tokens,
                token_usage_by_model={"test-model": bucket},
                metadata_json={"domain": domain, "caller_tag": "test"},
            )
        )
        await session.commit()


@pytest.mark.anyio
async def test_llm_usage_groups_by_domain(db):
    """C2: endpoint groups llm_bookkeeping rows by domain."""
    from deerflow.persistence.engine import get_session_factory

    sf = get_session_factory()
    await _seed_llm_bookkeeping(sf, "video_gen", USER_A, tokens=100)
    await _seed_llm_bookkeeping(sf, "video_gen", USER_A, tokens=200)
    await _seed_llm_bookkeeping(sf, "vimax", USER_A, tokens=50)
    await _seed_llm_bookkeeping(sf, "video_gen", USER_A, tokens=10, status="error")

    with _make_app(user_id=USER_A) as client:
        body = client.get("/api/console/llm-usage?days=14").json()

    domains = {d["domain"]: d for d in body["domains"]}
    assert "video_gen" in domains
    assert "vimax" in domains
    assert domains["video_gen"]["calls"] == 3
    assert domains["video_gen"]["failed_calls"] == 1
    assert domains["video_gen"]["tokens"] == 310
    assert domains["vimax"]["calls"] == 1
    assert domains["vimax"]["failed_calls"] == 0


@pytest.mark.anyio
async def test_llm_usage_returns_503_without_sql(monkeypatch):
    with _make_app(user_id=USER_A) as client:
        resp = client.get("/api/console/llm-usage")
    assert resp.status_code == 503


@pytest.mark.anyio
async def test_llm_usage_admin_sees_all_users(db):
    """Admin scope: sees bookkeeping rows from all users."""
    from deerflow.persistence.engine import get_session_factory

    sf = get_session_factory()
    await _seed_llm_bookkeeping(sf, "video_gen", USER_A, tokens=100)
    await _seed_llm_bookkeeping(sf, "video_gen", USER_B, tokens=200)

    with _make_app(user_id=ADMIN_ID, system_role="admin") as client:
        body = client.get("/api/console/llm-usage?days=14").json()

    domains = {d["domain"]: d for d in body["domains"]}
    assert domains["video_gen"]["tokens"] == 300


@pytest.mark.anyio
async def test_llm_usage_non_admin_scoped_to_self(db):
    """Non-admin: only sees own bookkeeping rows."""
    from deerflow.persistence.engine import get_session_factory

    sf = get_session_factory()
    await _seed_llm_bookkeeping(sf, "video_gen", USER_A, tokens=100)
    await _seed_llm_bookkeeping(sf, "video_gen", USER_B, tokens=200)

    with _make_app(user_id=USER_A) as client:
        body = client.get("/api/console/llm-usage?days=14").json()

    domains = {d["domain"]: d for d in body["domains"]}
    assert domains["video_gen"]["tokens"] == 100


# ---------------------------------------------------------------------------
# F2: External API calls endpoint
# ---------------------------------------------------------------------------


async def _seed_external_call(sf, provider: str, user_id: str, status: str = "success"):
    from uuid import uuid4

    from deerflow.persistence.external_api_calls.model import ExternalApiCallRow

    async with sf() as session:
        session.add(
            ExternalApiCallRow(
                call_id=f"apicall_{uuid4().hex[:12]}",
                user_id=user_id,
                provider=provider,
                method="generate",
                status=status,
                elapsed_ms=1000,
            )
        )
        await session.commit()


@pytest.mark.anyio
async def test_external_calls_aggregates_by_provider(db):
    """F2: endpoint aggregates external_api_calls by provider."""
    from deerflow.persistence.engine import get_session_factory

    sf = get_session_factory()
    await _seed_external_call(sf, "seedream", USER_A)
    await _seed_external_call(sf, "seedream", USER_A)
    await _seed_external_call(sf, "seedance_ark", USER_A)

    with _make_app(user_id=USER_A) as client:
        body = client.get("/api/console/external-calls?days=14").json()

    by_provider = {p["provider"]: p["calls"] for p in body["by_provider"]}
    assert by_provider["seedream"] == 2
    assert by_provider["seedance_ark"] == 1
    assert body["total_calls"] == 3


@pytest.mark.anyio
async def test_external_calls_non_admin_scoped(db):
    """F2: non-admin sees only own calls."""
    from deerflow.persistence.engine import get_session_factory

    sf = get_session_factory()
    await _seed_external_call(sf, "seedream", USER_A)
    await _seed_external_call(sf, "seedream", USER_B)

    with _make_app(user_id=USER_A) as client:
        body = client.get("/api/console/external-calls?days=14").json()

    assert body["total_calls"] == 1


# ---------------------------------------------------------------------------
# B1: /usage includes llm_bookkeeping rows
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_usage_includes_llm_bookkeeping_tokens(db, no_threshold):
    """B1: /usage must aggregate llm_bookkeeping rows alongside chat runs.

    The trend chart was chat-only; now it reflects all platform LLM usage.
    chat_tokens + llm_tokens must equal total_tokens.
    """
    from deerflow.persistence.engine import get_session_factory

    sf = get_session_factory()
    # 2 chat runs (already seeded in db fixture: run-a1=1.5M, run-a2=10)
    # 3 llm_bookkeeping rows across 2 days
    await _seed_llm_bookkeeping(sf, "video_gen", USER_A, tokens=500)
    await _seed_llm_bookkeeping(sf, "vimax", USER_A, tokens=300)
    await _seed_llm_bookkeeping(sf, "writer_assist", USER_A, tokens=200)

    with _make_app(user_id=USER_A) as client:
        body = client.get("/api/console/usage", params={"days": 7}).json()

    # Chat tokens from fixture: 1,500,000 + 10 = 1,500,010
    assert body["total_chat_tokens"] == 1_500_010
    # LLM tokens: 500 + 300 + 200 = 1,000
    assert body["total_llm_tokens"] == 1_000
    # Total = chat + llm
    assert body["total_tokens"] == 1_500_010 + 1_000
    assert body["total_llm_calls"] == 3


@pytest.mark.anyio
async def test_usage_llm_row_with_amount_uses_real_cost(db, no_threshold, priced):
    """B1: llm rows carrying amount contribute real-bill cost."""
    from deerflow.persistence.engine import get_session_factory

    sf = get_session_factory()
    await _seed_llm_bookkeeping(sf, "video_gen", USER_A, tokens=100, amount=0.42)

    with _make_app(user_id=USER_A) as client:
        body = client.get("/api/console/usage", params={"days": 14}).json()

    # The 0.42 amount from the llm row must be in the total.
    assert body["total_cost"] is not None
    assert body["total_cost"] >= 0.42


@pytest.mark.anyio
async def test_usage_error_llm_row_counts_calls_not_tokens(db, no_threshold):
    """B1: status=error llm rows count as llm_calls but contribute 0 tokens."""
    from deerflow.persistence.engine import get_session_factory

    sf = get_session_factory()
    await _seed_llm_bookkeeping(sf, "video_gen", USER_A, tokens=0, status="error")

    with _make_app(user_id=USER_A) as client:
        body = client.get("/api/console/usage", params={"days": 7}).json()

    assert body["total_llm_calls"] == 1
    assert body["total_llm_tokens"] == 0


@pytest.mark.anyio
async def test_usage_llm_scope_filter(db, no_threshold):
    """B1: llm_bookkeeping rows respect user scope."""
    from deerflow.persistence.engine import get_session_factory

    sf = get_session_factory()
    await _seed_llm_bookkeeping(sf, "video_gen", USER_A, tokens=100)
    await _seed_llm_bookkeeping(sf, "video_gen", USER_B, tokens=200)

    with _make_app(user_id=USER_A) as client:
        body = client.get("/api/console/usage", params={"days": 7}).json()

    assert body["total_llm_tokens"] == 100
    assert body["total_llm_calls"] == 1


@pytest.mark.anyio
async def test_usage_llm_outside_window_excluded(db, no_threshold):
    """B1: llm rows outside the time window must not be aggregated."""
    from uuid import uuid4

    from deerflow.persistence.engine import get_session_factory

    sf = get_session_factory()
    # Insert a row far in the past (beyond 7-day window)
    old_time = datetime.now(UTC) - timedelta(days=30)
    async with sf() as session:
        session.add(
            RunRow(
                run_id=f"llm_old_{uuid4().hex[:12]}",
                thread_id="video_gen",
                user_id=USER_A,
                status="success",
                operation_kind="llm_bookkeeping",
                model_name="test-model",
                total_input_tokens=50,
                total_output_tokens=50,
                total_tokens=100,
                token_usage_by_model={"test-model": {"input_tokens": 50, "output_tokens": 50, "total_tokens": 100}},
                metadata_json={"domain": "video_gen", "caller_tag": "test"},
                created_at=old_time,
                updated_at=old_time,
            )
        )
        await session.commit()

    with _make_app(user_id=USER_A) as client:
        body = client.get("/api/console/usage", params={"days": 7}).json()

    assert body["total_llm_tokens"] == 0
    assert body["total_llm_calls"] == 0


@pytest.mark.anyio
async def test_usage_by_model_llm_rows_dont_inflate_runs(db, no_threshold):
    """B1: by_model must not count llm rows in `runs` - only in `llm_calls`.

    This is the key guard from the v3 review: llm_bookkeeping rows are
    individual LLM calls, not agent runs. Counting them as runs would make
    the model breakdown's runs column jump to call-count levels, tearing
    the口径 from the stats card's total_runs (chat run count).
    """
    from deerflow.persistence.engine import get_session_factory

    sf = get_session_factory()
    # Chat runs in fixture use "priced-model". Add llm rows for a different model.
    await _seed_llm_bookkeeping(sf, "video_gen", USER_A, tokens=100)
    await _seed_llm_bookkeeping(sf, "video_gen", USER_A, tokens=200)

    with _make_app(user_id=USER_A) as client:
        body = client.get("/api/console/usage", params={"days": 14}).json()

    # "test-model" comes from the llm_bookkeeping seed; its runs must be 0.
    test_model = body["by_model"].get("test-model")
    assert test_model is not None, "llm rows should appear in by_model"
    assert test_model["runs"] == 0, "llm rows must not inflate runs"
    assert test_model["llm_calls"] == 2, "llm rows counted as llm_calls"
    assert test_model["tokens"] == 300

    # "priced-model" from chat runs: runs = 2 (run-a1 + run-a2), llm_calls = 0.
    priced_model = body["by_model"]["priced-model"]
    assert priced_model["runs"] == 2
    assert priced_model.get("llm_calls", 0) == 0


@pytest.mark.anyio
async def test_usage_day_has_chat_and_llm_token_split(db, no_threshold):
    """B1: each ConsoleUsageDay carries chat_tokens/llm_tokens/llm_calls."""
    from deerflow.persistence.engine import get_session_factory

    sf = get_session_factory()
    await _seed_llm_bookkeeping(sf, "video_gen", USER_A, tokens=500)

    # 复审二轮（2026-09-13）确定性：fixture 的 chat 行播在 now-2h/now-3h——
    # UTC 00:00 后跨日落进昨日桶，当日桶 chat_tokens==0 必红（晨跑 flaky，
    # 窗口随分桶时间戳为 UTC [00:00, ~01:00/02:00)）。重钉到当前时刻，
    # 四条断言原样保留。
    from sqlalchemy import select

    _now = datetime.now(UTC)
    async with sf() as session:
        _rows = (await session.execute(select(RunRow).where(RunRow.run_id.in_(["run-a1", "run-a2"])))).scalars().all()
        for _r in _rows:
            _r.created_at = _now
            _r.updated_at = _now
        await session.commit()

    with _make_app(user_id=USER_A) as client:
        body = client.get("/api/console/usage", params={"days": 7}).json()

    # Find the day with llm activity (today)
    today_str = datetime.now(UTC).date().isoformat()
    today_bucket = next(d for d in body["days"] if d["date"] == today_str)

    assert today_bucket["chat_tokens"] > 0  # from chat runs
    assert today_bucket["llm_tokens"] == 500
    assert today_bucket["llm_calls"] == 1
    assert today_bucket["total_tokens"] == today_bucket["chat_tokens"] + today_bucket["llm_tokens"]
