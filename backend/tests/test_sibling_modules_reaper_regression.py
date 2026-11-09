"""W7（2026-08-24 看门狗活性重构）：voice_twin / PPT 兼容回归。

口径（v3 小项③）：两服务的谓词 C **从未启用**（progress_stall_timeout=None，
不是"走 P5 窗口"）--本文件验证：
① 类属性口径：C 不启用、主谓词阈值未注入（None = 关）、composing 兜底未开；
② 行为口径：无覆盖任务的 A/B 谓词语义与改动前一致（A=updated_at 过期收尸、
   B=progress 冻结收尸；这是两服务唯一的看门狗形态）；
③ 兄弟模块既有套件全绿（tests/test_voice_twin*、tests/test_ppt* 等，跑在门禁里）。
实现时不得给它们配 C 或活性配置（用户已确认）。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.gateway._shared.task_framework.models import TaskRow
from app.gateway._shared.task_framework.repository import TaskRepository
from app.gateway._shared.task_framework.service import BaseTaskService


@pytest_asyncio.fixture(loop_scope="function")
async def async_engine():
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(TaskRow.__table__.create)
    yield engine
    await engine.dispose()


@pytest.fixture()
def session_factory(async_engine):
    return async_sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture()
def repo(session_factory):
    return TaskRepository(session_factory=session_factory)


# ---------------------------------------------------------------------------
# ① 类属性口径：两服务未启用任何新看门狗旋钮
# ---------------------------------------------------------------------------


def test_voice_twin_reaper_semantics_unchanged():
    """ProductionService（voice_twin）：C 不启用（None，非 P5 窗口）、主谓词
    未注入（None = 关）、composing 兜底未开（框架默认 False）。"""
    from app.gateway.voice_twin.production_service import ProductionService

    assert ProductionService.progress_stall_timeout is None, "voice_twin 的 C 从未启用（v3 小项③），不得配置"
    assert ProductionService.liveness_threshold_seconds is None, "不得给 voice_twin 注入主谓词阈值"
    assert ProductionService.composing_backstop_enabled is False, "不得给 voice_twin 开 composing 兜底"
    # GATEWAY_STAGES 与既有口径一致（不含 composing）
    assert "composing" not in ProductionService.GATEWAY_STAGES


def test_ppt_reaper_semantics_unchanged():
    """ContentTaskService（PPT）：同上口径 + 快速 stall 窗口（600s）保留。"""
    from app.gateway.content.ppt_service import ContentTaskService

    assert ContentTaskService.progress_stall_timeout is None
    assert ContentTaskService.liveness_threshold_seconds is None
    assert ContentTaskService.composing_backstop_enabled is False
    import inspect

    sig = inspect.signature(ContentTaskService.__init__)
    assert sig.parameters["stall_timeout"].default == 600, "PPT 的 10min 快速窗口保留"


# ---------------------------------------------------------------------------
# ② 行为口径：无覆盖任务的 A/B 语义与改动前一致
# ---------------------------------------------------------------------------


class _VoiceTwinLikeSvc(BaseTaskService):
    """镜像 ProductionService 的看门狗口径（不实例化重服务即可测谓词语义）。"""

    domain = "voice_production"
    id_prefix = "prod"
    GATEWAY_STAGES = ("pending", "running", "extracting", "converting", "synthesizing")

    async def _run_pipeline(self, *a, **k):
        return None


class _PptLikeSvc(_VoiceTwinLikeSvc):
    domain = "content"
    id_prefix = "ct"


async def _age(repo, task_id, delta: timedelta) -> None:
    old = datetime.now(UTC) - delta
    async with repo._sf() as session:
        await session.execute(sa_update(TaskRow).where(TaskRow.id == task_id).values(updated_at=old))
        await session.commit()


@pytest.mark.asyncio
@pytest.mark.parametrize("svc_cls", [_VoiceTwinLikeSvc, _PptLikeSvc], ids=["voice_twin", "ppt"])
async def test_predicate_a_stale_updated_at_reaped_as_before(repo, tmp_path, svc_cls):
    """A 谓词语义不变：updated_at 过期超窗 -> 收尸（voice_twin 1800s 窗口口径）。"""
    svc = svc_cls(repo, storage_dir=str(tmp_path), stall_timeout=1800)
    await repo.create(id="vt1", user_id="u1", domain=svc.domain, task_type="script_to_audio", payload="{}")
    await repo.update("vt1", status="running", progress=10, retry_count=2)  # 烧尽 -> failed 终态可断言
    await _age(repo, "vt1", timedelta(hours=1))

    assert await svc.reap_stalled_tasks() == 1
    row = await repo.get("vt1")
    assert row.status == "failed"
    assert "Reaped by watchdog (death" in (row.error_message or ""), row.error_message


@pytest.mark.asyncio
@pytest.mark.parametrize("svc_cls", [_VoiceTwinLikeSvc, _PptLikeSvc], ids=["voice_twin", "ppt"])
async def test_predicate_b_frozen_progress_reaped_as_before(repo, tmp_path, svc_cls):
    """B 谓词语义不变：progress 冻结超窗（无覆盖任务）-> 收尸。"""
    svc = svc_cls(repo, storage_dir=str(tmp_path), stall_timeout=1800)
    await repo.create(id="vt2", user_id="u1", domain=svc.domain, task_type="script_to_audio", payload="{}")
    await repo.update("vt2", status="running", progress=42, retry_count=2)  # 烧尽 -> failed 终态可断言
    svc._progress_baseline["vt2"] = (42, datetime.now(UTC) - timedelta(hours=1))

    assert await svc.reap_stalled_tasks() == 1
    assert (await repo.get("vt2")).status == "failed"


@pytest.mark.asyncio
@pytest.mark.parametrize("svc_cls", [_VoiceTwinLikeSvc, _PptLikeSvc], ids=["voice_twin", "ppt"])
async def test_fresh_task_not_reaped(repo, tmp_path, svc_cls):
    """健康任务（updated_at 新鲜 + progress 推进）不收尸。"""
    svc = svc_cls(repo, storage_dir=str(tmp_path), stall_timeout=1800)
    await repo.create(id="vt3", user_id="u1", domain=svc.domain, task_type="ppt_gen", payload="{}")
    await repo.update("vt3", status="running", progress=42)
    assert await svc.reap_stalled_tasks() == 0
    assert (await repo.get("vt3")).status == "running"


@pytest.mark.asyncio
@pytest.mark.parametrize("svc_cls", [_VoiceTwinLikeSvc, _PptLikeSvc], ids=["voice_twin", "ppt"])
async def test_composing_rows_untouched(repo, tmp_path, svc_cls):
    """composing 行 gateway 不碰（兜底未开）--worker 清道夫的唯一责任区。"""
    svc = svc_cls(repo, storage_dir=str(tmp_path), stall_timeout=1800)
    await repo.create(id="vt4", user_id="u1", domain=svc.domain, task_type="ppt_gen", payload="{}")
    await repo.update("vt4", status="composing", progress=95, claimed_by="worker-A", lease_expires_at=datetime.now(UTC) - timedelta(minutes=30))
    await _age(repo, "vt4", timedelta(hours=2))

    assert await svc.reap_stalled_tasks() == 0
    row = await repo.get("vt4")
    assert row.status == "composing"
    assert row.claimed_by == "worker-A"


@pytest.mark.asyncio
@pytest.mark.parametrize("svc_cls", [_VoiceTwinLikeSvc, _PptLikeSvc], ids=["voice_twin", "ppt"])
async def test_draft_exempt_from_predicate_a(repo, tmp_path, svc_cls):
    """DRAFT 豁免对 A 生效（N3② 扩展）；等待态不因空闲被收。"""
    svc = svc_cls(repo, storage_dir=str(tmp_path), stall_timeout=1800)
    await repo.create(id="vt5", user_id="u1", domain=svc.domain, task_type="script_to_audio", payload="{}")
    await repo.update("vt5", status="draft", progress=100)
    await _age(repo, "vt5", timedelta(hours=3))
    assert await svc.reap_stalled_tasks() == 0
    assert (await repo.get("vt5")).status == "draft"
