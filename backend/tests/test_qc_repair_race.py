"""T-A2（2026-09-09 feedback-round1 R2/R12）：qc_repair 推进竞态与 CAS。

修前病灶：watcher 降级/重生两条路径先 ``repo.update(status="composing")`` 落库
再注册 pipeline——落库瞬间 worker（POLL=2s）即用**旧缺陷 props + qc_skip** 抢跑
渲染并标 ready。修后：CAS 置 running（pipeline 驱动状态翻转，composing 翻转由
_wrap_pipeline 的 Entry-1 以重建后的 props 完成），worker 在 gateway 就绪前无
composing 行可认领；取消的任务（CAS 失败）不再被覆盖。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.gateway._shared.task_framework.models import TaskRow
from app.gateway._shared.task_framework.repository import TaskRepository


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


@pytest.fixture()
def vg_svc(repo, tmp_path, monkeypatch):
    from unittest.mock import MagicMock

    from app.gateway.video_gen.config import VideoGenConfig
    from app.gateway.video_gen.providers.registry import ProviderRegistry
    from app.gateway.video_gen.service import VideoGenService

    cfg = VideoGenConfig(script_model="qwen3.8-flash")
    svc = VideoGenService(
        repo=repo,
        config=cfg,
        providers=ProviderRegistry(cfg),
        storage_dir=str(tmp_path),
        sandbox_provider=MagicMock(),
    )
    registered = []
    monkeypatch.setattr(svc, "_register_active_task", lambda task_id, factory: registered.append(task_id) or True)
    svc._qc_registered = registered
    return svc


def _storyboard_dict() -> dict:
    return {
        "title": "qc-race",
        "config": {"video_type": "remotion_course", "width": 1920, "height": 1080},
        "frames": [
            {"index": 0, "narration": "第一句", "duration": 2.0, "visual_url": "http://cdn/x0.png", "visual_type": "image"},
            {"index": 1, "narration": "第二句", "duration": 2.0, "visual_url": "http://cdn/x1.png", "visual_type": "image"},
        ],
        "scene_groups": [],
        "total_duration": 4.0,
    }


async def _mk_qc_task(repo, tmp_path, *, retry: int, task_id="vg_qc_race") -> str:
    """落一行 qc_repair 任务 + 盘面 storyboard.json（帧 1 带视觉 URL）。"""
    from datetime import timedelta

    await repo.create(id=task_id, user_id="u1", domain="video_gen", task_type="remotion_course", payload=json.dumps({"pipeline_version": "remotion_course"}))
    await repo.update(
        task_id,
        status="qc_repair",
        result=json.dumps(
            {
                "qc_issues": [{"scene": 1, "issue": "black", "start": 0.5, "duration": 1.9}],
                "qc_retry_count": retry,
            }
        ),
    )
    task_dir = tmp_path / "u1" / "tasks" / task_id
    task_dir.mkdir(parents=True)
    (task_dir / "storyboard.json").write_text(json.dumps(_storyboard_dict(), ensure_ascii=False), encoding="utf-8")
    # updated_at 归新（避免命中停滞告警分支）
    async with repo._sf() as session:
        from sqlalchemy import update as sa_update

        await session.execute(sa_update(TaskRow).where(TaskRow.id == task_id).values(updated_at=datetime.now(UTC) - timedelta(seconds=5)))
        await session.commit()
    return task_id


async def _composing_unclaimed_rows(repo) -> list:
    async with repo._sf() as session:
        from sqlalchemy import select

        rows = (await session.execute(select(TaskRow).where(TaskRow.status == "composing", TaskRow.claimed_by.is_(None)))).scalars().all()
        return list(rows)


class TestFirstAttemptRegeneration:
    @pytest.mark.asyncio
    async def test_flips_running_not_composing_worker_cannot_prematurely_claim(self, repo, vg_svc, tmp_path):
        """首次重生：落库后状态必须是 running（非 composing）——worker 轮询
        ``status='composing' AND claimed_by IS NULL``，此时无行可认领，抢跑通道
        关闭；composing 翻转由 pipeline 以重建 props 完成。"""
        tid = await _mk_qc_task(repo, tmp_path, retry=0)
        t = await repo.get(tid, user_id="u1")
        await vg_svc._qc_repair_step(t)

        row = await repo.get(tid, user_id="u1")
        assert row.status == "running", "watcher 不得直接置 composing（R2 抢跑竞态）"
        assert (await _composing_unclaimed_rows(repo)) == [], "gateway 就绪前不得出现可认领行"
        payload = json.loads(row.payload)
        assert payload.get("auto_render") is True
        assert "recompose_only" not in payload, "首次重生走 Entry-1 全路径"
        result = json.loads(row.result)
        assert result.get("qc_retry_count") == 1
        assert row.error_message == "质检发现异常画面，正在重新生成"
        # storyboard 帧视觉已清（Entry-1 见无视觉即重生）
        sb = json.loads((tmp_path / "u1" / "tasks" / tid / "storyboard.json").read_text(encoding="utf-8"))
        assert sb["frames"][1]["visual_url"] is None
        assert vg_svc._qc_registered == [tid], "CAS 成功后必须注册 pipeline"

    @pytest.mark.asyncio
    async def test_first_frame_visual_preserved(self, repo, vg_svc, tmp_path):
        """只清异常场景：非 qc_issues 场景（帧 0）的视觉保留。"""
        tid = await _mk_qc_task(repo, tmp_path, retry=0)
        t = await repo.get(tid, user_id="u1")
        await vg_svc._qc_repair_step(t)
        sb = json.loads((tmp_path / "u1" / "tasks" / tid / "storyboard.json").read_text(encoding="utf-8"))
        assert sb["frames"][0]["visual_url"] == "http://cdn/x0.png"


class TestDegradedDelivery:
    @pytest.mark.asyncio
    async def test_degraded_flips_running_not_composing_with_qc_skip(self, repo, vg_svc, tmp_path):
        """二次异常降级交付：同样 CAS 置 running；qc_skip/recompose_only 进 payload
        （随 merged payload 交棒，worker 拿到的必是新 flags）。"""
        tid = await _mk_qc_task(repo, tmp_path, retry=1, task_id="vg_qc_deg")
        t = await repo.get(tid, user_id="u1")
        await vg_svc._qc_repair_step(t)

        row = await repo.get(tid, user_id="u1")
        assert row.status == "running"
        assert (await _composing_unclaimed_rows(repo)) == []
        payload = json.loads(row.payload)
        assert payload.get("qc_skip") is True and payload.get("recompose_only") is True
        assert json.loads(row.dirty_frames or "[]") == []
        deg = tmp_path / "u1" / "tasks" / tid / "degradations.json"
        assert deg.exists(), "降级交付必须留痕"
        assert vg_svc._qc_registered == [tid]


class TestCasGuard:
    @pytest.mark.asyncio
    async def test_cancelled_task_not_overwritten(self, repo, vg_svc, tmp_path):
        """R12：watcher 读行后任务被取消 → CAS 失败 → 不覆盖状态、不注册 pipeline。"""
        tid = await _mk_qc_task(repo, tmp_path, retry=0, task_id="vg_qc_cxl")
        t = await repo.get(tid, user_id="u1")  # 快照行
        await repo.update(tid, status="cancelled")  # 读-写窗口内用户取消
        await vg_svc._qc_repair_step(t)

        row = await repo.get(tid, user_id="u1")
        assert row.status == "cancelled", "取消态不得被 watcher 覆盖"
        assert vg_svc._qc_registered == [], "CAS 失败不得注册 pipeline"

    @pytest.mark.asyncio
    async def test_cancelled_degraded_path_not_overwritten(self, repo, vg_svc, tmp_path):
        tid = await _mk_qc_task(repo, tmp_path, retry=1, task_id="vg_qc_cxl2")
        t = await repo.get(tid, user_id="u1")
        await repo.update(tid, status="cancelled")
        await vg_svc._qc_repair_step(t)
        assert (await repo.get(tid, user_id="u1")).status == "cancelled"
        assert vg_svc._qc_registered == []

    @pytest.mark.asyncio
    async def test_stalled_warning_uses_cas(self, repo, vg_svc, tmp_path):
        """停滞告警分支同样 CAS：取消的任务不再被写 error_message。"""
        from datetime import timedelta

        tid = await _mk_qc_task(repo, tmp_path, retry=0, task_id="vg_qc_stall")
        t = await repo.get(tid, user_id="u1")
        await repo.update(tid, status="cancelled")
        # 快照行的 updated_at 拉旧以进入停滞分支
        object.__setattr__(t, "updated_at", datetime.now(UTC) - timedelta(seconds=99999))
        await vg_svc._qc_repair_step(t)
        row = await repo.get(tid, user_id="u1")
        assert row.status == "cancelled"
        assert row.error_message is None, "取消任务的 error_message 不得被停滞告警覆盖"


class TestRobustness:
    """T-C1（R11①③④⑤+R13，2026-09-09 feedback-round1）：qc_repair 健壮化。"""

    @pytest.mark.asyncio
    async def test_corrupt_storyboard_degrades_not_stuck(self, repo, vg_svc, tmp_path):
        """R11①：storyboard.json 解析失败不再永卡（修前异常被扫描器吞、每轮重试）
        ——与二次异常同路降级交付。"""
        tid = await _mk_qc_task(repo, tmp_path, retry=0, task_id="vg_qc_corrupt")
        (tmp_path / "u1" / "tasks" / tid / "storyboard.json").write_text("{not json", encoding="utf-8")
        t = await repo.get(tid, user_id="u1")
        await vg_svc._qc_repair_step(t)
        row = await repo.get(tid, user_id="u1")
        assert row.status == "running", "解析失败走降级交付（不永卡 qc_repair）"
        payload = json.loads(row.payload)
        assert payload.get("qc_skip") is True and payload.get("recompose_only") is True
        deg = tmp_path / "u1" / "tasks" / tid / "degradations.json"
        assert deg.exists()

    @pytest.mark.asyncio
    async def test_stall_warning_fires_with_naive_updated(self, repo, vg_svc, tmp_path):
        """R11③：naive updated_at（无 tzinfo）也必须触发停滞告警——修前 TypeError
        被 except 吞成 age=0.0，告警永不触发。"""
        from datetime import timedelta

        tid = await _mk_qc_task(repo, tmp_path, retry=0, task_id="vg_qc_stall2")
        # DB 里 updated_at 拉旧 40min（走 naive ISO——TaskRow 校验会转 datetime，
        # 这里直接 SQL 写 naive 形态模拟存量行）
        async with repo._sf() as session:
            from sqlalchemy import update as sa_update

            await session.execute(sa_update(TaskRow).where(TaskRow.id == tid).values(updated_at=(datetime.now(UTC) - timedelta(seconds=2400)).replace(tzinfo=None)))
            await session.commit()
        t = await repo.get(tid, user_id="u1")
        await vg_svc._qc_repair_step(t)
        row = await repo.get(tid, user_id="u1")
        assert row.error_message == "质检修复长时间无进展，请联系管理员或取消任务"
        assert row.status == "qc_repair", "停滞告警只透出不推进（计划语义）"
        assert vg_svc._qc_registered == []

    @pytest.mark.asyncio
    async def test_metrics_emitted_on_transitions(self, repo, vg_svc, tmp_path, monkeypatch):
        """R11④：推进补 metrics（regenerating/degraded/stall_warned 三态计数）。"""
        from app.gateway._shared.task_framework import metrics as metrics_mod

        calls: list[str] = []
        monkeypatch.setattr(metrics_mod.metrics, "inc", lambda name, labels=None, help_text=None: calls.append(labels.get("status") if labels else name))
        tid = await _mk_qc_task(repo, tmp_path, retry=0, task_id="vg_qc_metrics")
        t = await repo.get(tid, user_id="u1")
        await vg_svc._qc_repair_step(t)
        assert "regenerating" in calls

    @pytest.mark.asyncio
    async def test_recompose_pops_qc_skip(self, repo, vg_svc, tmp_path, monkeypatch):
        """R13：qc_skip 持久化在 payload（降级交付落库），用户手动重新合成必须
        弹掉——修前手动重渲**永久跳过 QC**。"""
        import asyncio as aio

        tid = "vg_r13_recompose"
        await repo.create(id=tid, user_id="u1", domain="video_gen", task_type="ai_image", payload=json.dumps({"qc_skip": True}))
        await repo.update(tid, status="ready", progress=100)
        task_dir = tmp_path / "u1" / "tasks" / tid
        (task_dir / "visuals").mkdir(parents=True)
        (task_dir / "audio.mp3").write_bytes(b"mp3")
        (task_dir / "visuals" / "img_0001.png").write_bytes(b"png")
        (task_dir / "storyboard.json").write_text(
            json.dumps(
                {
                    "title": "t",
                    "config": {"video_type": "ai_image", "width": 1920, "height": 1080},
                    "frames": [{"index": 0, "narration": "n", "local_visual_path": "visuals/img_0001.png", "duration": 2.0}],
                    "scene_groups": [],
                }
            ),
            encoding="utf-8",
        )
        captured: dict = {}

        async def _cap(task_id, user_id, task_type, payload):
            captured["payload"] = payload

        monkeypatch.setattr(vg_svc, "_wrap_pipeline", _cap)

        def _reg(task_id, factory):
            factory()
            return True

        monkeypatch.setattr(vg_svc, "_register_active_task", _reg)
        await vg_svc.recompose_task(tid, "u1")
        await aio.sleep(0)
        assert captured["payload"].get("qc_skip") is None, "手动重合成必须弹掉 qc_skip"
        assert captured["payload"].get("recompose_only") is True
