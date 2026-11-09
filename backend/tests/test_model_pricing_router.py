"""Admin-only model pricing endpoints (B4).

``GET``/``PUT /api/admin/model-pricing`` let an operator set per-model prices
without a redeploy. Two properties are load-bearing:

1. **Admin-only.** Prices drive every spend figure on the console, so a
   regular user must not be able to edit them. The gate is the shared
   ``require_admin`` dependency from C1 — *not* a wrapper over
   ``@require_permission``, which cannot express "admin" at all
   (``authz._authenticate`` grants every logged-in user the whole
   ``_ALL_PERMISSIONS`` list, and that list has no admin entry).

2. **Validated writes.** Every rejection in ``pricing_schema`` has to survive
   the trip through the endpoint as a 422, not get flattened into a 500 or —
   worse — silently accepted.

Tests drive the real router over TestClient against a real temp SQLite DB.
Per this repo's ``provider-capability-must-test-through-wrappers`` lesson, a
mock that bypasses the routing/auth layer would be testing the mock.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from _router_auth_helpers import make_authed_test_app
from fastapi.testclient import TestClient

from app.gateway.auth.models import User
from app.gateway.routers import console, model_pricing
from deerflow.persistence.model_pricing.model import ModelPricingRow
from deerflow.persistence.run.model import RunRow

ADMIN_ID = "33333333-3333-3333-3333-333333333333"
USER_ID = "11111111-1111-1111-1111-111111111111"


@pytest.fixture()
async def db(tmp_path):
    from deerflow.persistence.base import Base
    from deerflow.persistence.engine import close_engine, get_engine, init_engine

    await init_engine("sqlite", url=f"sqlite+aiosqlite:///{tmp_path / 'pricing.db'}", sqlite_dir=str(tmp_path))
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    await close_engine()


def _client(*, user_id: str, system_role: str = "user", router=None) -> TestClient:
    def factory() -> User:
        return User(email=f"{user_id}@example.com", password_hash="x", system_role=system_role, id=user_id)

    app = make_authed_test_app(user_factory=factory)
    app.include_router(router if router is not None else model_pricing.router)
    return TestClient(app)


_VALID = {"pricing": {"m": {"currency": "CNY", "input_per_million": 8.0, "output_per_million": 32.0}}}


class TestAdminGate:
    @pytest.mark.anyio
    async def test_regular_user_cannot_read_pricing(self, db) -> None:
        with _client(user_id=USER_ID) as client:
            assert client.get("/api/admin/model-pricing").status_code == 403

    @pytest.mark.anyio
    async def test_regular_user_cannot_write_pricing(self, db) -> None:
        with _client(user_id=USER_ID) as client:
            assert client.put("/api/admin/model-pricing", json=_VALID).status_code == 403

    @pytest.mark.anyio
    async def test_refused_write_stores_nothing(self, db) -> None:
        """403 must happen before the DB write, not after."""
        from sqlalchemy import select

        from deerflow.persistence.engine import get_session_factory

        with _client(user_id=USER_ID) as client:
            client.put("/api/admin/model-pricing", json=_VALID)

        sf = get_session_factory()
        async with sf() as session:
            rows = (await session.execute(select(ModelPricingRow))).scalars().all()
        assert rows == []

    @pytest.mark.anyio
    async def test_admin_may_read(self, db) -> None:
        with _client(user_id=ADMIN_ID, system_role="admin") as client:
            assert client.get("/api/admin/model-pricing").status_code == 200

    @pytest.mark.anyio
    async def test_admin_may_write(self, db) -> None:
        with _client(user_id=ADMIN_ID, system_role="admin") as client:
            assert client.put("/api/admin/model-pricing", json=_VALID).status_code == 200


class TestRoundTrip:
    @pytest.mark.anyio
    async def test_written_prices_come_back_on_read(self, db) -> None:
        with _client(user_id=ADMIN_ID, system_role="admin") as client:
            client.put("/api/admin/model-pricing", json=_VALID)
            body = client.get("/api/admin/model-pricing").json()

        assert body["pricing"]["m"]["input_per_million"] == 8.0
        assert body["pricing"]["m"]["currency"] == "CNY"

    @pytest.mark.anyio
    async def test_null_cache_hit_price_round_trips_as_null(self, db) -> None:
        """null must not come back as 0 — they bill differently."""
        payload = {"pricing": {"m": {"currency": "CNY", "input_per_million": 8.0, "output_per_million": 32.0, "input_cache_hit_per_million": None}}}
        with _client(user_id=ADMIN_ID, system_role="admin") as client:
            client.put("/api/admin/model-pricing", json=payload)
            body = client.get("/api/admin/model-pricing").json()

        assert body["pricing"]["m"]["input_cache_hit_per_million"] is None

    @pytest.mark.anyio
    async def test_zero_cache_hit_price_round_trips_as_zero(self, db) -> None:
        payload = {"pricing": {"m": {"currency": "CNY", "input_per_million": 8.0, "output_per_million": 32.0, "input_cache_hit_per_million": 0.0}}}
        with _client(user_id=ADMIN_ID, system_role="admin") as client:
            client.put("/api/admin/model-pricing", json=payload)
            body = client.get("/api/admin/model-pricing").json()

        assert body["pricing"]["m"]["input_cache_hit_per_million"] == 0.0

    @pytest.mark.anyio
    async def test_rewriting_a_model_replaces_rather_than_duplicates(self, db) -> None:
        with _client(user_id=ADMIN_ID, system_role="admin") as client:
            client.put("/api/admin/model-pricing", json=_VALID)
            client.put("/api/admin/model-pricing", json={"pricing": {"m": {"currency": "CNY", "input_per_million": 99.0, "output_per_million": 1.0}}})
            body = client.get("/api/admin/model-pricing").json()

        assert body["pricing"]["m"]["input_per_million"] == 99.0
        # Only one DB row for "m" (no duplicate).  Yaml fallback entries may
        # also be present, so filter by source="db".
        db_entries = {k: v for k, v in body["pricing"].items() if v.get("source") == "db"}
        assert len(db_entries) == 1

    @pytest.mark.anyio
    async def test_records_who_made_the_change(self, db) -> None:
        """Prices are money; the audit column must actually be populated."""
        with _client(user_id=ADMIN_ID, system_role="admin") as client:
            client.put("/api/admin/model-pricing", json=_VALID)
            body = client.get("/api/admin/model-pricing").json()

        assert body["pricing"]["m"]["updated_by"] == ADMIN_ID


class TestSourceField:
    """A0: GET returns source="db" for stored rows and source="yaml" for
    config.yaml fallback entries."""

    @pytest.mark.anyio
    async def test_db_rows_have_source_db(self, db) -> None:
        with _client(user_id=ADMIN_ID, system_role="admin") as client:
            client.put("/api/admin/model-pricing", json=_VALID)
            body = client.get("/api/admin/model-pricing").json()

        assert body["pricing"]["m"]["source"] == "db"

    @pytest.mark.anyio
    async def test_yaml_only_models_have_source_yaml(self, db, monkeypatch) -> None:
        """Models with a pricing annotation in config.yaml but no DB row
        appear with source="yaml"."""

        yaml_entries = {
            "yaml-model": {
                "currency": "CNY",
                "input_per_million": 5.0,
                "output_per_million": 20.0,
                "input_cache_hit_per_million": 0.5,
            },
        }
        monkeypatch.setattr("app.gateway.routers.model_pricing.get_yaml_pricing_entries", lambda: yaml_entries)

        with _client(user_id=ADMIN_ID, system_role="admin") as client:
            # Write a DB row for a different model
            client.put("/api/admin/model-pricing", json=_VALID)
            body = client.get("/api/admin/model-pricing").json()

        # yaml-only model present with source="yaml"
        assert "yaml-model" in body["pricing"]
        assert body["pricing"]["yaml-model"]["source"] == "yaml"
        assert body["pricing"]["yaml-model"]["input_per_million"] == 5.0

    @pytest.mark.anyio
    async def test_put_overrides_yaml_to_db(self, db, monkeypatch) -> None:
        """A model present in both yaml and PUT payload gets source="db"."""
        yaml_entries = {
            "m": {
                "currency": "CNY",
                "input_per_million": 5.0,
                "output_per_million": 20.0,
                "input_cache_hit_per_million": None,
            },
        }
        monkeypatch.setattr("app.gateway.routers.model_pricing.get_yaml_pricing_entries", lambda: yaml_entries)

        with _client(user_id=ADMIN_ID, system_role="admin") as client:
            client.put("/api/admin/model-pricing", json=_VALID)
            body = client.get("/api/admin/model-pricing").json()

        assert body["pricing"]["m"]["source"] == "db"
        assert body["pricing"]["m"]["input_per_million"] == 8.0


class TestValidationSurfacesAs422:
    @pytest.mark.parametrize(
        ("label", "entry"),
        [
            ("typo key", {"currency": "CNY", "input_per_milion": 8.0, "output_per_million": 32.0}),
            ("negative input", {"currency": "CNY", "input_per_million": -8.0, "output_per_million": 32.0}),
            ("negative output", {"currency": "CNY", "input_per_million": 8.0, "output_per_million": -32.0}),
            ("negative cache hit", {"currency": "CNY", "input_per_million": 8.0, "output_per_million": 32.0, "input_cache_hit_per_million": -1.0}),
        ],
    )
    @pytest.mark.anyio
    async def test_malformed_entry_is_rejected(self, db, label: str, entry: dict) -> None:
        with _client(user_id=ADMIN_ID, system_role="admin") as client:
            response = client.put("/api/admin/model-pricing", json={"pricing": {"m": entry}})
        assert response.status_code == 422, label

    @pytest.mark.anyio
    async def test_mixed_currency_payload_is_rejected(self, db) -> None:
        """Mixing currencies zeroes platform-wide spend on the read side."""
        payload = {
            "pricing": {
                "a": {"currency": "CNY", "input_per_million": 8.0, "output_per_million": 32.0},
                "b": {"currency": "USD", "input_per_million": 1.0, "output_per_million": 4.0},
            }
        }
        with _client(user_id=ADMIN_ID, system_role="admin") as client:
            response = client.put("/api/admin/model-pricing", json=payload)
        assert response.status_code == 422

    @pytest.mark.anyio
    async def test_a_rejected_write_leaves_previous_prices_intact(self, db) -> None:
        """Validation failure must not half-apply the batch."""
        with _client(user_id=ADMIN_ID, system_role="admin") as client:
            client.put("/api/admin/model-pricing", json=_VALID)
            client.put(
                "/api/admin/model-pricing",
                json={"pricing": {"m": {"currency": "CNY", "input_per_million": 5.0, "output_per_million": 5.0}, "bad": {"input_per_milion": 1.0}}},
            )
            body = client.get("/api/admin/model-pricing").json()

        assert body["pricing"]["m"]["input_per_million"] == 8.0
        assert "bad" not in body["pricing"]


class TestConsoleReflectsNewPrices:
    """The acceptance criterion: change a price, console spend follows."""

    @pytest.mark.anyio
    async def test_console_cost_changes_after_a_price_write(self, db) -> None:
        from deerflow.persistence.engine import get_session_factory

        sf = get_session_factory()
        now = datetime.now(UTC)
        async with sf() as session:
            session.add(
                RunRow(
                    run_id="r1",
                    thread_id="t1",
                    user_id=ADMIN_ID,
                    status="success",
                    operation_kind="run",
                    model_name="m",
                    total_input_tokens=1_000_000,
                    total_output_tokens=0,
                    total_tokens=1_000_000,
                    created_at=now - timedelta(hours=1),
                    updated_at=now,
                    token_usage_by_model={"m": {"input_tokens": 1_000_000, "output_tokens": 0, "total_tokens": 1_000_000}},
                )
            )
            await session.commit()

        # No prices yet → the console must say "unpriced", not 0.
        # （2026-09-14 基线债修复：C3 后 _build_pricing_map 会叠加 config.yaml
        # fallback——本机 config.yaml 带 pricing 时 map 非空、断言漂移成 0.0。
        # 测试意图是「无任何价目 → unpriced」，把 yaml fallback 钉成空以保持
        # 确定性；DB 侧覆盖行为由后半段 PUT 后 10.0 断言守护。）
        from app.gateway.routers import console as console_mod

        orig_yaml = console_mod._build_yaml_pricing_map
        console_mod._build_yaml_pricing_map = lambda: {}
        try:
            with _client(user_id=ADMIN_ID, system_role="admin", router=console.router) as client:
                assert client.get("/api/console/stats").json()["total_cost"] is None
        finally:
            console_mod._build_yaml_pricing_map = orig_yaml

        with _client(user_id=ADMIN_ID, system_role="admin") as client:
            client.put("/api/admin/model-pricing", json={"pricing": {"m": {"currency": "CNY", "input_per_million": 10.0, "output_per_million": 0.0}}})

        # Same process, no restart: 1M input tokens @ 10/M = 10.0.
        with _client(user_id=ADMIN_ID, system_role="admin", router=console.router) as client:
            stats = client.get("/api/console/stats").json()

        assert stats["total_cost"] == pytest.approx(10.0)
        assert stats["currency"] == "CNY"


class TestPricingAliases:
    """P0 (2026-08-13): a ``model_pricing`` row written under the config
    ``name`` must price runs whose usage buckets carry the provider-reported
    model id (and vice versa). Without aliases the journal bucket key never
    hits the row and vendor-id runs show no spend."""

    NAME = "claude-sonnet-5-5"
    MODEL = "anthropic/claude-sonnet-5-5"

    @staticmethod
    def _fake_config(models: list[tuple[str, str]], pricing: dict | None = None):
        from types import SimpleNamespace

        return SimpleNamespace(models=[SimpleNamespace(name=n, model=m, pricing=pricing) for n, m in models])

    def _patch_config(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "app.gateway.routers.console.get_app_config",
            lambda: self._fake_config([(self.NAME, self.MODEL)]),
        )

    async def _write_pricing_row(self, model_name: str) -> None:
        from deerflow.persistence.engine import get_session_factory

        sf = get_session_factory()
        async with sf() as session:
            session.add(ModelPricingRow(model_name=model_name, currency="CNY", input_per_million=1.0, output_per_million=2.0))
            await session.commit()

    @pytest.mark.anyio
    async def test_row_under_config_name_also_keys_vendor_model_id(self, db, monkeypatch) -> None:
        self._patch_config(monkeypatch)
        await self._write_pricing_row(self.NAME)

        from deerflow.persistence.engine import get_session_factory

        pricing = await console._build_pricing_map(get_session_factory())
        assert self.MODEL in pricing, f"厂商 model id 应为别名 key：{sorted(pricing)}"
        assert pricing[self.MODEL].input_per_million == 1.0

    @pytest.mark.anyio
    async def test_row_under_vendor_model_id_also_keys_config_name(self, db, monkeypatch) -> None:
        self._patch_config(monkeypatch)
        await self._write_pricing_row(self.MODEL)

        from deerflow.persistence.engine import get_session_factory

        pricing = await console._build_pricing_map(get_session_factory())
        assert self.NAME in pricing, f"config name 应为别名 key：{sorted(pricing)}"

    @pytest.mark.anyio
    async def test_uppercase_row_covers_all_case_variants(self, db, monkeypatch) -> None:
        self._patch_config(monkeypatch)
        await self._write_pricing_row("CLAUDE-SONNET-5-5")

        from deerflow.persistence.engine import get_session_factory

        pricing = await console._build_pricing_map(get_session_factory())
        for key in (self.NAME, self.NAME.lower(), self.MODEL, self.MODEL.lower()):
            assert key in pricing, f"缺少别名 key：{key!r}"

    @pytest.mark.anyio
    async def test_console_stats_prices_claude_bucket_via_name_row(self, db, monkeypatch) -> None:
        """验收口径：按 config name 写价，bucket key 为厂商 id 的 run 成本非零。"""
        self._patch_config(monkeypatch)
        from deerflow.persistence.engine import get_session_factory

        sf = get_session_factory()
        now = datetime.now(UTC)
        async with sf() as session:
            session.add(ModelPricingRow(model_name=self.NAME, currency="CNY", input_per_million=10.0, output_per_million=0.0))
            session.add(
                RunRow(
                    run_id="r-claude",
                    thread_id="t-claude",
                    user_id=ADMIN_ID,
                    status="success",
                    operation_kind="run",
                    model_name=self.MODEL,
                    total_input_tokens=1_000_000,
                    total_output_tokens=0,
                    total_tokens=1_000_000,
                    created_at=now - timedelta(hours=1),
                    updated_at=now,
                    token_usage_by_model={self.MODEL: {"input_tokens": 1_000_000, "output_tokens": 0, "total_tokens": 1_000_000}},
                )
            )
            await session.commit()

        with _client(user_id=ADMIN_ID, system_role="admin", router=console.router) as client:
            stats = client.get("/api/console/stats").json()

        assert stats["total_cost"] == pytest.approx(10.0)
        assert stats["currency"] == "CNY"

    # ── C (2026-08-14): DB 层确定性 + 别名碰撞检测 ────────────────────────

    @pytest.mark.anyio
    @pytest.mark.parametrize("insert_order", [(NAME, MODEL), (MODEL, NAME)])
    async def test_exact_key_row_wins_over_alias_contender(self, db, monkeypatch, insert_order) -> None:
        """name 行与 id 行并存不同价：精确 key 行必须赢，与插入顺序无关。

        旧实现单遍遍历、每个行把整组别名键直接覆写——谁后处理谁赢，
        结果随 DB 行序漂移。新实现两遍：精确 key 直接赋值（并列时 ORDER BY
        末位赢）；别名键守卫赋值 + alias_claimed 先到先得（ORDER BY 靠前行赢）。
        """
        self._patch_config(monkeypatch)
        from deerflow.persistence.engine import get_session_factory

        sf = get_session_factory()
        async with sf() as session:
            for model_name in insert_order:
                session.add(
                    ModelPricingRow(
                        model_name=model_name,
                        currency="CNY",
                        input_per_million=1.0 if model_name == self.NAME else 2.0,
                        output_per_million=0.0,
                    )
                )
            await session.commit()

        pricing = await console._build_pricing_map(get_session_factory())
        assert pricing[self.NAME].input_per_million == 1.0, "精确 name 行必须赢过别名波及"
        assert pricing[self.MODEL].input_per_million == 2.0, "精确 id 行必须赢过别名波及"

    @pytest.mark.anyio
    async def test_alias_collision_warns_and_drops_key(self, db, monkeypatch, caplog) -> None:
        """A.name == B.model 时该 key 弃权：warning + 两模型各自查价不串。"""
        monkeypatch.setattr(
            "app.gateway.routers.console.get_app_config",
            lambda: self._fake_config([("foo", "v-foo"), ("bar", "foo")]),
        )
        from deerflow.persistence.engine import get_session_factory

        sf = get_session_factory()
        async with sf() as session:
            for model_name, price in (("foo", 1.0), ("v-foo", 2.0), ("bar", 3.0)):
                session.add(
                    ModelPricingRow(
                        model_name=model_name,
                        currency="CNY",
                        input_per_million=price,
                        output_per_million=0.0,
                    )
                )
            await session.commit()

        pricing = await console._build_pricing_map(get_session_factory())
        assert any("foo" in r.message for r in caplog.records), "别名碰撞必须留 warning"
        assert pricing["foo"].input_per_million == 1.0
        assert pricing["v-foo"].input_per_million == 2.0, "foo 行不得通过别名污染 v-foo"
        assert pricing["bar"].input_per_million == 3.0, "foo 行不得通过别名污染 bar"

    @pytest.mark.anyio
    async def test_same_name_different_models_detects_collision(self, db, monkeypatch, caplog) -> None:
        """P2-3: 同名不同 model 的两个 config 必须各自成组检出碰撞。

        修前内层键是 cfg_name——owners["dup"] 只剩一个 entry → len(groups)==1
        漏检 → 写在 "dup" 的 DB 行把价格经别名扩张到后写入者的厂商 id 上。
        """
        monkeypatch.setattr(
            "app.gateway.routers.console.get_app_config",
            lambda: self._fake_config([("dup", "vendor-m1"), ("dup", "vendor-m2")]),
        )
        from deerflow.persistence.engine import get_session_factory

        sf = get_session_factory()
        async with sf() as session:
            session.add(
                ModelPricingRow(
                    model_name="dup",
                    currency="CNY",
                    input_per_million=1.0,
                    output_per_million=0.0,
                )
            )
            await session.commit()

        with caplog.at_level("WARNING"):
            pricing = await console._build_pricing_map(get_session_factory())
        assert any("dup" in r.message for r in caplog.records), "同名碰撞必须 warning"
        assert "vendor-m1" not in pricing and "vendor-m2" not in pricing, "碰撞 key 的别名展开必须弃权——dup 行价格不得扩张到任一厂商 id"
        assert "dup" in pricing  # 行自身精确 key 照常生效

    @pytest.mark.anyio
    async def test_duplicate_identical_config_entries_collide(self, db, monkeypatch, caplog) -> None:
        """2026-08-19（反馈1-项3）：config.yaml 出现两条 name+model 完全相同的重复
        条目（配置错误）→ 1452d43fe（内层键改下标）后判为碰撞弃权别名扩张；修前
        静默合并为一个 owner、别名照常扩张。方向正确（重复条目本属配置错误），
        此处补回归保护。"""
        monkeypatch.setattr(
            "app.gateway.routers.console.get_app_config",
            lambda: self._fake_config([("dup", "vendor-dup"), ("dup", "vendor-dup")]),
        )
        from deerflow.persistence.engine import get_session_factory

        sf = get_session_factory()
        async with sf() as session:
            session.add(
                ModelPricingRow(
                    model_name="dup",
                    currency="CNY",
                    input_per_million=1.0,
                    output_per_million=0.0,
                )
            )
            await session.commit()

        with caplog.at_level("WARNING"):
            pricing = await console._build_pricing_map(get_session_factory())
        assert any("dup" in r.message for r in caplog.records), "完全相同的重复条目必须报碰撞 warning"
        assert "vendor-dup" not in pricing, "碰撞 key 的别名展开必须弃权"
        assert "dup" in pricing, "行自身精确 key 照常生效"

    @pytest.mark.anyio
    async def test_case_fold_collision_warns_and_later_row_wins(self, db, monkeypatch, caplog) -> None:
        """P2-4: "FOO" 与 "foo" 两行并存 → warning + 确定性后者赢（SQLite BINARY
        序下小写行赢）；PG 本地化 collation 可能翻转——warning 让运维可行动。"""
        monkeypatch.setattr(
            "app.gateway.routers.console.get_app_config",
            lambda: self._fake_config([]),
        )
        from deerflow.persistence.engine import get_session_factory

        sf = get_session_factory()
        async with sf() as session:
            for model_name, price in (("FOO", 1.0), ("foo", 2.0)):
                session.add(
                    ModelPricingRow(
                        model_name=model_name,
                        currency="CNY",
                        input_per_million=price,
                        output_per_million=0.0,
                    )
                )
            await session.commit()

        with caplog.at_level("WARNING"):
            pricing = await console._build_pricing_map(get_session_factory())
        assert any("foo" in r.message.lower() and "case" in r.message.lower() for r in caplog.records), "大小写并存必须留 warning"
        assert pricing["foo"].input_per_million == 2.0, "ORDER BY 末位（小写行）赢"
        assert pricing["FOO"].input_per_million == 1.0

    @pytest.mark.anyio
    async def test_lowercase_row_resolves_all_case_variants(self, db, monkeypatch) -> None:
        """C2 两遍写入防回归：小写行经 _lookup_pricing 覆盖全部大小写变体。"""
        self._patch_config(monkeypatch)
        await self._write_pricing_row(self.NAME)  # NAME 本身全小写

        from deerflow.persistence.engine import get_session_factory

        pricing = await console._build_pricing_map(get_session_factory())
        for key in (self.NAME, self.NAME.upper(), self.MODEL, self.MODEL.upper()):
            found = console._lookup_pricing(pricing, key)
            assert found is not None and found.input_per_million == 1.0, f"lookup({key!r}) 必须命中小写行"

    @pytest.mark.anyio
    async def test_db_row_overrides_yaml_fallback_on_alias_keys(self, db, monkeypatch) -> None:
        """C2 修订（2026-08-14 审查）：别名 key 也必须由 DB 行覆盖 yaml 兜底价。

        yaml 兜底价（99.0）注册在 name+model id 两个 key 上；DB 行写在 config
        name 下（1.0）。精确 key 与别名 key（厂商 model id）都必须取 DB 价——
        否则按厂商 id 记账的 run 会静默沿用 yaml 旧价（08-13 P0 的镜像场景）。
        """
        monkeypatch.setattr(
            "app.gateway.routers.console.get_app_config",
            lambda: self._fake_config(
                [(self.NAME, self.MODEL)],
                pricing={"input_per_million": 99.0, "output_per_million": 0.0, "currency": "CNY"},
            ),
        )
        await self._write_pricing_row(self.NAME)  # 1.0，写在 config name 下

        from deerflow.persistence.engine import get_session_factory

        pricing = await console._build_pricing_map(get_session_factory())
        assert pricing[self.NAME].input_per_million == 1.0, "精确 key 必须取 DB 价"
        assert pricing[self.MODEL].input_per_million == 1.0, "别名 key 必须取 DB 价，而非 yaml 兜底 99.0"
