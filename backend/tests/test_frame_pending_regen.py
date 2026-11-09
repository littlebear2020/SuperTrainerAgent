"""M1: ready/failed 态改旁白后未重新生成 → recompose/re-render 409 明确指引。

修复前：PUT 只暂存文本，recompose 复用旧 audio.mp3（CAS 键不匹配也不查）→
全量重渲一遍与编辑前完全相同的视频（白烧一次渲染，用户困惑"修改没生效"）。
显式 pending_regen_frames 态（非 CAS 文件名推断——旧任务 CAS 键不含 voice+speed 会误判）。
"""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.gateway.routers.video_gen import UpdateFrameRequest, update_storyboard_frame


def _make_request(svc):
    class _Req:
        class app:
            class state:
                video_gen_service = svc

    return _Req()


def _make_service(tmp_path):
    from app.gateway.video_gen.config import VideoGenConfig
    from app.gateway.video_gen.providers.registry import ProviderRegistry
    from app.gateway.video_gen.service import VideoGenService

    cfg = VideoGenConfig(qihu_api_key="t")
    providers = ProviderRegistry(cfg)
    repo = MagicMock()
    # T4(ee042bc03) 起帧编辑路由末尾 await svc.touch_task → repo.update——绑定 async mock
    repo.update = AsyncMock(return_value=None)
    # B5（2026-09-28）：draft 改旁白 → _invalidate_audio_artifacts 内 await repo.get
    # （返回 None = 无 payload 可清，不影响断言；个别用例自行覆写返回值）
    repo.get = AsyncMock(return_value=None)
    return VideoGenService(
        repo=repo,
        config=cfg,
        providers=providers,
        storage_dir=str(tmp_path),
        sandbox_provider=AsyncMock(),
    )


def _seed_task(tmp_path, task_id="vg_pd", status="ready"):
    task_dir = tmp_path / "test-user-autouse" / "tasks" / task_id
    task_dir.mkdir(parents=True)
    (task_dir / "storyboard.json").write_text(
        json.dumps(
            {
                "title": "t",
                "config": {},
                # 2026-07-29：recompose 新增视觉素材预检 → 帧必须有真实本地素材，
                # 否则被判「素材已失效」（无素材重合成只会产出黑屏片）
                "frames": [
                    {
                        "index": 0,
                        "narration": "旧旁白",
                        "audio_path": "audio/abc.mp3",
                        "local_visual_path": "visuals/img_0000.png",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    (task_dir / "visuals").mkdir()
    (task_dir / "visuals" / "img_0000.png").write_bytes(b"img")
    (task_dir / "audio.mp3").write_bytes(b"concat")
    task = MagicMock()
    task.status = status
    task.task_type = "ai_image"
    task.payload = json.dumps({})
    task.dirty_frames = []
    return task_dir, task


@pytest.mark.asyncio
async def test_put_narration_marks_pending_in_ready(tmp_path):
    svc = _make_service(tmp_path)
    task_dir, task = _seed_task(tmp_path)
    svc.get_task = AsyncMock(return_value=task)

    await update_storyboard_frame(
        _make_request(svc),
        "vg_pd",
        0,
        UpdateFrameRequest(narration="新旁白"),
    )

    data = json.loads((task_dir / "storyboard.json").read_text(encoding="utf-8"))
    assert data["pending_regen_frames"] == [0]  # 修复前：无此字段


@pytest.mark.asyncio
async def test_put_narration_in_draft_does_not_mark(tmp_path):
    """draft 态不标（首渲重 TTS 重建 audio.mp3，无陈旧问题）。"""
    svc = _make_service(tmp_path)
    task_dir, task = _seed_task(tmp_path, status="draft")
    svc.get_task = AsyncMock(return_value=task)

    await update_storyboard_frame(
        _make_request(svc),
        "vg_pd",
        0,
        UpdateFrameRequest(narration="新旁白"),
    )

    data = json.loads((task_dir / "storyboard.json").read_text(encoding="utf-8"))
    assert "pending_regen_frames" not in data


@pytest.mark.asyncio
async def test_put_image_prompt_only_does_not_mark(tmp_path):
    """仅改 image_prompt 不标（re-render 重跑全视觉本就会拾取新 prompt，不应被 409 误伤）。"""
    svc = _make_service(tmp_path)
    task_dir, task = _seed_task(tmp_path)
    svc.get_task = AsyncMock(return_value=task)

    await update_storyboard_frame(
        _make_request(svc),
        "vg_pd",
        0,
        UpdateFrameRequest(image_prompt="新提示词"),
    )

    data = json.loads((task_dir / "storyboard.json").read_text(encoding="utf-8"))
    assert "pending_regen_frames" not in data


@pytest.mark.asyncio
async def test_recompose_blocked_by_pending(tmp_path):
    svc = _make_service(tmp_path)
    task_dir, task = _seed_task(tmp_path)
    data = json.loads((task_dir / "storyboard.json").read_text(encoding="utf-8"))
    data["pending_regen_frames"] = [0]
    (task_dir / "storyboard.json").write_text(json.dumps(data), encoding="utf-8")
    svc.repo.get = AsyncMock(return_value=task)

    with pytest.raises(ValueError, match="旁白"):
        await svc.recompose_task("vg_pd", "test-user-autouse")


@pytest.mark.asyncio
async def test_re_render_blocked_by_pending(tmp_path):
    svc = _make_service(tmp_path)
    task_dir, task = _seed_task(tmp_path)
    data = json.loads((task_dir / "storyboard.json").read_text(encoding="utf-8"))
    data["pending_regen_frames"] = [0]
    (task_dir / "storyboard.json").write_text(json.dumps(data), encoding="utf-8")
    svc.repo.get = AsyncMock(return_value=task)

    with pytest.raises(ValueError, match="旁白"):
        await svc.re_render_task("vg_pd", "test-user-autouse")


@pytest.mark.asyncio
async def test_recompose_ok_when_pending_empty(tmp_path):
    """无 pending → 守卫放行（不破坏既有 recompose 流程）。"""
    svc = _make_service(tmp_path)
    task_dir, task = _seed_task(tmp_path)
    svc.repo.get = AsyncMock(return_value=task)
    svc._register_active_task = MagicMock(return_value=True)

    result = await svc.recompose_task("vg_pd", "test-user-autouse")
    assert result["status"] == "composing"


@pytest.mark.asyncio
async def test_regen_bg_keeps_pending_when_narration_changed_mid_flight(tmp_path):
    """final-review finding 1: regen 途中 PUT 改了旁白 → bg 成功也不得清 pending
    （本次音频属旧文本，守卫须继续 409 防音画错位）。"""
    from app.gateway.routers.video_gen import _regenerate_frame_bg

    svc = _make_service(tmp_path)
    sb_dict = {
        "title": "t",
        "config": {"video_type": "ai_image", "width": 1080, "height": 1920},
        "frames": [{"index": 0, "narration": "v1 旁白", "image_prompt": "p0"}],
        "pending_regen_frames": [0],
    }
    task_dir = tmp_path / "u1" / "tasks" / "vg_race"
    task_dir.mkdir(parents=True)
    sb_path = task_dir / "storyboard.json"
    sb_path.write_text(json.dumps(sb_dict, ensure_ascii=False), encoding="utf-8")
    svc.repo.add_dirty_frame = AsyncMock()

    async def fake_regen(task_id, user_id, frame_index, td, mode="all"):  # mode: 2026-07-27 起随 bg 透传（all/audio_only/visual_only）
        # 模拟 regen 途中用户 PUT 新旁白（真实路径：PUT 落盘 + updater 标 pending）
        data = json.loads(sb_path.read_text(encoding="utf-8"))
        data["frames"][0]["narration"] = "v2 新旁白"
        sb_path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return {"frame_index": 0}

    svc._regenerate_frame = fake_regen
    await _regenerate_frame_bg(svc, "vg_race", 0, "u1")

    data = json.loads(sb_path.read_text(encoding="utf-8"))
    assert data.get("pending_regen_frames") == [0]  # 保留——修复前被无条件清除
    assert data["frames"][0]["regen_state"] == "done"


# ── content-match T8⑤：visual_source 切换 → ready 态 warning，不标 pending ──


@pytest.mark.asyncio
async def test_put_visual_source_warns_but_never_marks_pending(tmp_path):
    """T8⑤（已拍板）：visual_source 变更只加 warning 指引「重新生成」——
    pending_regen_frames 是 recompose/re-render 的 409 守卫依据，切源后立即
    recompose（重排既有素材）是合法操作，标 pending 会把它误伤成 409。"""
    svc = _make_service(tmp_path)
    task_dir, task = _seed_task(tmp_path)
    svc.get_task = AsyncMock(return_value=task)

    resp = await update_storyboard_frame(
        _make_request(svc),
        "vg_pd",
        0,
        UpdateFrameRequest(visual_source="graphic"),
    )

    data = json.loads((task_dir / "storyboard.json").read_text(encoding="utf-8"))
    assert data["frames"][0]["visual_source"] == "graphic"
    assert "pending_regen_frames" not in data, "切源不标 pending（recompose 不得被 409 拦）"
    assert resp["warning"], "ready 态切源必须返回 warning（现状只看 narration/image_prompt）"
    assert "重新生成" in resp["warning"], f"warning 须指引重新生成：{resp['warning']!r}"


@pytest.mark.asyncio
async def test_put_visual_source_in_draft_no_warning(tmp_path):
    """draft 态无 warning（首渲本就按新源渲染）。"""
    svc = _make_service(tmp_path)
    task_dir, task = _seed_task(tmp_path, status="draft")
    svc.get_task = AsyncMock(return_value=task)

    resp = await update_storyboard_frame(
        _make_request(svc),
        "vg_pd",
        0,
        UpdateFrameRequest(visual_source="graphic"),
    )
    assert not resp.get("warning")


@pytest.mark.asyncio
async def test_put_layout_explicit_null_resets_to_auto(tmp_path):
    """T-E3（R17）：前端发 {"layout": null} = 切回自动——显式 null 必须生效
    （修前 `if val is not None` 吞掉 null，手选→切回自动永远不落盘）。"""
    svc = _make_service(tmp_path)
    task_dir, task = _seed_task(tmp_path, status="draft")
    svc.get_task = AsyncMock(return_value=task)
    sb = json.loads((task_dir / "storyboard.json").read_text(encoding="utf-8"))
    sb["frames"][0]["layout"] = "QuotePage"
    (task_dir / "storyboard.json").write_text(json.dumps(sb, ensure_ascii=False), encoding="utf-8")

    await update_storyboard_frame(
        _make_request(svc),
        "vg_pd",
        0,
        UpdateFrameRequest(layout=None),
    )

    data = json.loads((task_dir / "storyboard.json").read_text(encoding="utf-8"))
    assert data["frames"][0].get("layout") is None, "显式 null 清除手选，切回自动"


@pytest.mark.asyncio
async def test_put_without_layout_key_leaves_layout_untouched(tmp_path):
    """未传 layout 键 ≠ 显式 null——改旁白不得清掉用户手选版式。"""
    svc = _make_service(tmp_path)
    task_dir, task = _seed_task(tmp_path, status="draft")
    svc.get_task = AsyncMock(return_value=task)
    sb = json.loads((task_dir / "storyboard.json").read_text(encoding="utf-8"))
    sb["frames"][0]["layout"] = "QuotePage"
    (task_dir / "storyboard.json").write_text(json.dumps(sb, ensure_ascii=False), encoding="utf-8")

    await update_storyboard_frame(
        _make_request(svc),
        "vg_pd",
        0,
        UpdateFrameRequest(narration="新的旁白内容"),
    )

    data = json.loads((task_dir / "storyboard.json").read_text(encoding="utf-8"))
    assert data["frames"][0]["layout"] == "QuotePage", "未传 layout 键不得动版式"
