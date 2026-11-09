"""remotion_course 独立管线 T7/T8：create payload 两字段 + PUT frame 校验 + reparse 端点。

T7（§7）：
- create：course_parse_mode/script_extract_id 透传；remotion_course fixed 模式（无 topic）
  提交正常（决策 12 回归验证）
- PUT frame：layout_content 合法写入/显式 null 清除；visual_source 校验
  （VideoClipPage+ai_video 接受；非 VideoClipPage+ai_video 拒；非法枚举拒）
- frame_edits（render_from_draft setattr 机制）携带 layout_content 生效

T8（§7）：
- reparse：draft 限定（ready/running → 409）；整量覆盖后 storyboard 按新档重建；
  嵌入图映射从 task_dir 重建（staging 已删仍可）；payload["course_parse_mode"] 更新
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.gateway.routers.video_gen import router
from app.gateway.video_gen.schemas import CreateVideoRequest

_TEST_USER = "test-user-autouse"


# ── T7：create schema ──────────────────────────────────────────────────────


def test_create_schema_new_fields():
    req = CreateVideoRequest(video_type="remotion_course", mode="fixed", script="脚本内容", course_parse_mode="rewrite", script_extract_id="01JTESTULID0001")
    d = req.model_dump()
    assert d["course_parse_mode"] == "rewrite"
    assert d["script_extract_id"] == "01JTESTULID0001"
    # 默认值
    req2 = CreateVideoRequest(video_type="remotion_course", mode="fixed", script="脚本内容")
    assert req2.model_dump()["course_parse_mode"] == "faithful"


def test_create_schema_invalid_mode_rejected():
    with pytest.raises(Exception):
        CreateVideoRequest(video_type="remotion_course", mode="fixed", script="x", course_parse_mode="pure_split")


def test_create_fixed_mode_no_topic_ok():
    """决策 12 回归：remotion_course 无 topic/mode=generate 提交正常（话题模式移除后）。"""
    req = CreateVideoRequest(video_type="remotion_course", mode="fixed", script="一段课程脚本内容足够。")
    assert req.video_type == "remotion_course"


# ── PUT frame 端点 ─────────────────────────────────────────────────────────


def _put_app(tmp_path, *, task_type="remotion_course", task_status="draft", frames=None, layout="ConceptPage"):
    app = FastAPI()
    app.include_router(router)
    storage_root = (tmp_path / "storage").resolve()
    task_dir = storage_root / _TEST_USER / "tasks" / "vg_put1"
    task_dir.mkdir(parents=True, exist_ok=True)
    frames = frames if frames is not None else [{"index": 0, "narration": "旁白。", "layout": layout}]
    (task_dir / "storyboard.json").write_text(
        json.dumps({"title": "t", "config": {"video_type": task_type, "width": 1920, "height": 1080}, "frames": frames}, ensure_ascii=False),
        encoding="utf-8",
    )
    mock_svc = MagicMock()
    mock_task = MagicMock()
    mock_task.status = task_status
    mock_task.task_type = task_type
    mock_svc.get_task = AsyncMock(return_value=mock_task)
    mock_svc._storage_dir = str(storage_root)
    mock_svc._update_storyboard = AsyncMock()
    mock_svc.touch_task = AsyncMock()
    mock_svc.repo = MagicMock()
    app.state.video_gen_service = mock_svc
    client = TestClient(app)
    return client, task_dir


def test_put_frame_layout_content_written(tmp_path):
    client, task_dir = _put_app(tmp_path)
    resp = client.put(
        "/api/video/tasks/vg_put1/storyboard/frames/0",
        json={"layout_content": {"title": "新标题", "bullets": ["要点一"]}},
    )
    assert resp.status_code == 200, resp.text
    # updater 被调用且 edits 含 layout_content
    _, kwargs = client.app.state.video_gen_service._update_storyboard.call_args
    updater = client.app.state.video_gen_service._update_storyboard.call_args.args[1]
    data = {"frames": [{"index": 0, "narration": "旁白。", "layout": "ConceptPage"}]}
    updater(data)
    assert data["frames"][0]["layout_content"] == {"title": "新标题", "bullets": ["要点一"]}


def test_put_frame_layout_content_explicit_null_clears(tmp_path):
    client, task_dir = _put_app(tmp_path, frames=[{"index": 0, "narration": "旁白。", "layout": "ConceptPage", "layout_content": {"title": "旧标题"}}])
    resp = client.put("/api/video/tasks/vg_put1/storyboard/frames/0", json={"layout_content": None})
    assert resp.status_code == 200, resp.text
    assert resp.json()["updated"]["layout_content"] is None


def test_put_frame_visual_source_matrix(tmp_path):
    # VideoClipPage + ai_video：接受
    client, _ = _put_app(tmp_path, layout="VideoClipPage")
    resp = client.put("/api/video/tasks/vg_put1/storyboard/frames/0", json={"visual_source": "ai_video"})
    assert resp.status_code == 200, resp.text
    # 非 VideoClipPage + ai_video：拒
    client, _ = _put_app(tmp_path, layout="ConceptPage")
    resp = client.put("/api/video/tasks/vg_put1/storyboard/frames/0", json={"visual_source": "ai_video"})
    assert resp.status_code == 400
    assert "VideoClipPage" in resp.json()["detail"]
    # 同请求切版式到 VideoClipPage + ai_video：接受
    client, _ = _put_app(tmp_path, layout="ConceptPage")
    resp = client.put("/api/video/tasks/vg_put1/storyboard/frames/0", json={"layout": "VideoClipPage", "visual_source": "ai_video"})
    assert resp.status_code == 200, resp.text
    # 非法枚举：拒
    client, _ = _put_app(tmp_path, layout="ConceptPage")
    resp = client.put("/api/video/tasks/vg_put1/storyboard/frames/0", json={"visual_source": "graphic"})
    assert resp.status_code == 400
    # ai_image：接受（图槽帧由前端引导，服务端不重复限制）
    resp = client.put("/api/video/tasks/vg_put1/storyboard/frames/0", json={"visual_source": "ai_image"})
    assert resp.status_code == 200, resp.text


def test_put_frame_visual_source_other_types_unchanged(tmp_path):
    """回归：非 remotion_course 任务 visual_source 校验不新增（graphic 仍可写）。"""
    client, _ = _put_app(tmp_path, task_type="ai_image", layout="ConceptPage")
    resp = client.put("/api/video/tasks/vg_put1/storyboard/frames/0", json={"visual_source": "graphic"})
    assert resp.status_code == 200, resp.text


# ── frame_edits → _render_from_draft setattr ──────────────────────────────


@pytest.mark.asyncio
async def test_render_from_draft_frame_edits_layout_content(tmp_path, hermetic_tts):
    from app.gateway.video_gen.config import VideoGenConfig
    from app.gateway.video_gen.providers.registry import ProviderRegistry
    from app.gateway.video_gen.service import VideoGenService

    cfg = VideoGenConfig(qihu_api_key="t", script_model="m")
    svc = VideoGenService(repo=MagicMock(), config=cfg, providers=ProviderRegistry(cfg), storage_dir=str(tmp_path), sandbox_provider=AsyncMock())
    sb_dict = {
        "title": "t",
        "config": {"video_type": "remotion_course", "width": 1920, "height": 1080},
        "frames": [{"index": 0, "narration": "旁白。", "layout": "ConceptPage"}],
    }
    (tmp_path / "storyboard.json").write_text(json.dumps(sb_dict, ensure_ascii=False), encoding="utf-8")

    class _Stop(Exception):
        pass

    async def _progress(**kw):
        pass

    # 在视觉源选择处拦停（避免跑完 TTS/渲染全链）
    with patch.object(VideoGenService, "_make_visual_source", side_effect=_Stop("stop-here")):
        with pytest.raises(_Stop):
            await svc._render_from_draft(
                "vg_t",
                "remotion_course",
                tmp_path,
                _progress,
                {"frame_edits": {"0": {"layout_content": {"title": "编辑器写入"}, "visual_source": "ai_image"}}},
            )
    # frame_edits 已生效（setattr 机制天然支持 layout_content）
    disk = json.loads((tmp_path / "storyboard.json").read_text(encoding="utf-8"))
    assert disk["frames"][0]["layout_content"] == {"title": "编辑器写入"}
    assert disk["frames"][0]["visual_source"] == "ai_image"


# ── T8：reparse 端点 ──────────────────────────────────────────────────────


def _reparse_app(tmp_path, *, task_status, task_payload=None, embedded=None):
    app = FastAPI()
    app.include_router(router)
    storage_root = (tmp_path / "storage").resolve()
    task_dir = storage_root / _TEST_USER / "tasks" / "vg_rp1"
    task_dir.mkdir(parents=True, exist_ok=True)
    sb = {
        "title": "t",
        "config": {"video_type": "remotion_course", "width": 1920, "height": 1080},
        "frames": [
            {"index": 0, "narration": "旧帧旁白一。", "layout": "CoverPage", "visual_source": "ai_image", "image_prompt": "旧词"},
            {"index": 1, "narration": "旧帧旁白二。", "layout": "ConceptPage"},
        ],
        "scene_groups": [{"index": 0, "title": "旧章", "frame_indices": [0, 1]}],
    }
    (task_dir / "storyboard.json").write_text(json.dumps(sb, ensure_ascii=False), encoding="utf-8")
    (task_dir / "degradations.json").write_text(json.dumps({"items": []}), encoding="utf-8")
    if embedded:
        emb = task_dir / "visuals" / "embedded"
        emb.mkdir(parents=True, exist_ok=True)
        for name, data in embedded.items():
            (emb / name).write_bytes(data)

    # 真 service（reparse 复用 _build_course_storyboard——MagicMock 不可 await）
    from types import SimpleNamespace

    from app.gateway.video_gen.config import VideoGenConfig
    from app.gateway.video_gen.providers.registry import ProviderRegistry
    from app.gateway.video_gen.service import VideoGenService

    _long_script = (
        "机器学习是人工智能的分支，通过数据训练模型获得预测能力。已在金融、医疗等多个行业落地，"
        "成为数字化转型的核心引擎。模型训练依赖大规模数据集与算力基础设施，评估指标覆盖准确率与召回率。"
        "工程实践中需要持续监控数据漂移，定期重新训练以维持线上效果稳定可靠。"
    )
    cfg = VideoGenConfig(qihu_api_key="t", script_model="m")
    repo = MagicMock()
    repo.get = AsyncMock(return_value=SimpleNamespace(status=task_status, task_type="remotion_course", payload=json.dumps(task_payload or {"mode": "fixed", "script": _long_script, "course_parse_mode": "faithful"}, ensure_ascii=False)))
    repo.update = AsyncMock()
    repo.touch = AsyncMock()
    svc = VideoGenService(repo=repo, config=cfg, providers=ProviderRegistry(cfg), storage_dir=str(storage_root), sandbox_provider=AsyncMock())
    app.state.video_gen_service = svc
    return TestClient(app), task_dir, svc


def _reparse_units():
    from app.gateway.video_gen.orchestration.course_script import CourseUnit

    return [
        CourseUnit(layout="CoverPage", narration="重建后的帧一。"),
        CourseUnit(layout="ConceptPage", narration="重建后的帧二。", content={"title": "新内容"}),
        CourseUnit(layout="ImageTextLeft", narration="带图单元。", image_ref="img_1"),
    ]


def test_reparse_draft_only(tmp_path):
    for status in ("ready", "running", "composing"):
        client, _, _ = _reparse_app(tmp_path, task_status=status)
        resp = client.post("/api/video/tasks/vg_rp1/reparse", json={"course_parse_mode": "rewrite"})
        assert resp.status_code == 409, f"{status} 须 409，got {resp.status_code}"


def test_reparse_rebuilds_storyboard_and_updates_mode(tmp_path):
    client, task_dir, mock_svc = _reparse_app(tmp_path, task_status="draft")
    with (
        patch("app.gateway.video_gen.orchestration.course_script.parse_course_script", new=AsyncMock(return_value=_reparse_units())),
        patch("app.gateway.video_gen.service.create_script_llm", return_value=MagicMock()),
    ):
        resp = client.post("/api/video/tasks/vg_rp1/reparse", json={"course_parse_mode": "rewrite"})
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["course_parse_mode"] == "rewrite"
    assert data["n_frames"] == 3
    # 整量覆盖：磁盘 storyboard 按 parser 输出重建（旧帧/旧组/旧 visual_source 全部消失）
    disk = json.loads((task_dir / "storyboard.json").read_text(encoding="utf-8"))
    assert [f["narration"] for f in disk["frames"]] == ["重建后的帧一。", "重建后的帧二。", "带图单元。"]
    assert disk["frames"][0].get("visual_source") is None
    assert disk["frames"][1]["layout_content"] == {"title": "新内容"}


def test_reparse_embedded_images_from_task_dir(tmp_path):
    """嵌入图映射从 task_dir/visuals/embedded/ 重建（staging 已删仍可关联不丢）。"""
    client, task_dir, _ = _reparse_app(tmp_path, task_status="draft", embedded={"img_1.png": b"png-bytes"})
    with (
        patch("app.gateway.video_gen.orchestration.course_script.parse_course_script", new=AsyncMock(return_value=_reparse_units())),
        patch("app.gateway.video_gen.service.create_script_llm", return_value=MagicMock()),
    ):
        resp = client.post("/api/video/tasks/vg_rp1/reparse", json={"course_parse_mode": "rewrite"})
    assert resp.status_code == 200, resp.text
    disk = json.loads((task_dir / "storyboard.json").read_text(encoding="utf-8"))
    assert disk["frames"][2]["local_visual_path"] == "visuals/embedded/img_1.png"


def test_reparse_non_remotion_course_404(tmp_path):
    app = FastAPI()
    app.include_router(router)
    storage_root = (tmp_path / "storage2").resolve()
    task_dir = storage_root / _TEST_USER / "tasks" / "vg_rp2"
    task_dir.mkdir(parents=True)
    (task_dir / "storyboard.json").write_text(json.dumps({"title": "t", "config": {"video_type": "ai_image", "width": 1080, "height": 1920}, "frames": []}), encoding="utf-8")
    mock_svc = MagicMock()
    mock_task = MagicMock()
    mock_task.status = "draft"
    mock_task.task_type = "ai_image"
    mock_task.payload = "{}"
    mock_svc.get_task = AsyncMock(return_value=mock_task)
    mock_svc._storage_dir = str(storage_root)
    app.state.video_gen_service = mock_svc
    client = TestClient(app)
    resp = client.post("/api/video/tasks/vg_rp2/reparse", json={"course_parse_mode": "rewrite"})
    assert resp.status_code in (404, 400)


# ── 2026-09-11 审查修复回归 ───────────────────────────────────────────────


def test_put_frame_visual_source_explicit_null_clears(tmp_path):
    """B3：显式 null = 取消 AI 生成（修前被 `if val is not None` 吞掉，取消端到端失效）。"""
    client, _ = _put_app(tmp_path, frames=[{"index": 0, "narration": "旁白。", "layout": "ImageTextLeft", "visual_source": "ai_image", "image_prompt": "旧词"}])
    resp = client.put("/api/video/tasks/vg_put1/storyboard/frames/0", json={"visual_source": None})
    assert resp.status_code == 200, resp.text
    assert resp.json()["updated"]["visual_source"] is None
    # updater 把 None 落盘（不是只改内存响应）
    updater = client.app.state.video_gen_service._update_storyboard.call_args.args[1]
    data = {"frames": [{"index": 0, "narration": "旁白。", "visual_source": "ai_image"}]}
    updater(data)
    assert data["frames"][0]["visual_source"] is None


def test_put_frame_visual_source_absent_untouched(tmp_path):
    """B3 对侧：请求体不带 visual_source 键 → 不动（区分「未传」与「显式 null」）。"""
    client, _ = _put_app(tmp_path, frames=[{"index": 0, "narration": "旁白。", "layout": "ImageTextLeft", "visual_source": "ai_image"}])
    resp = client.put("/api/video/tasks/vg_put1/storyboard/frames/0", json={"image_prompt": "只改词"})
    assert resp.status_code == 200, resp.text
    assert "visual_source" not in resp.json()["updated"]


def test_create_schema_extract_id_length_cap():
    """Minor：max_length=40 与认领白名单对齐（超长 422 而非认领时静默判非法）。"""
    with pytest.raises(Exception):
        CreateVideoRequest(video_type="remotion_course", mode="fixed", script="x", script_extract_id="A" * 41)
    ok = CreateVideoRequest(video_type="remotion_course", mode="fixed", script="x", script_extract_id="01JCOURSEEXTRACTID0001")
    assert ok.script_extract_id == "01JCOURSEEXTRACTID0001"


def test_reparse_stale_extract_id_keeps_embedded(tmp_path):
    """B1 端点回归：payload 残留 script_extract_id（create 认领后 staging 已删）时，
    reparse 不得重认领——嵌入图映射保留、无 embedded_images_expired 假留痕。"""
    client, task_dir, svc = _reparse_app(
        tmp_path,
        task_status="draft",
        task_payload={"mode": "fixed", "script": "占位脚本" * 30, "course_parse_mode": "faithful", "script_extract_id": "01JSTALEEXTRACT0001"},
        embedded={"img_1.png": b"png-bytes"},
    )
    with (
        patch("app.gateway.video_gen.orchestration.course_script.parse_course_script", new=AsyncMock(return_value=_reparse_units())),
        patch("app.gateway.video_gen.service.create_script_llm", return_value=MagicMock()),
        patch("app.gateway.video_gen.orchestration.course_staging.claim_script_images") as m_claim,
    ):
        resp = client.post("/api/video/tasks/vg_rp1/reparse", json={"course_parse_mode": "rewrite"})
    assert resp.status_code == 200, resp.text
    m_claim.assert_not_called()
    disk = json.loads((task_dir / "storyboard.json").read_text(encoding="utf-8"))
    assert disk["frames"][2]["local_visual_path"] == "visuals/embedded/img_1.png"
    deg = json.loads((task_dir / "degradations.json").read_text(encoding="utf-8"))
    assert not any(i["kind"] == "embedded_images_expired" for i in deg["items"])
    # payload 落库时已摘除残留 extract_id（repo.update 还会被 touch_task 再调一次，
    # call_args 是 touch 那次——从调用列表里找带 payload 的那条）
    _payload_calls = [c for c in svc.repo.update.call_args_list if "payload" in (c.kwargs or {})]
    assert _payload_calls, svc.repo.update.call_args_list
    assert "script_extract_id" not in json.loads(_payload_calls[-1].kwargs["payload"])


def test_reparse_race_status_flip_409(tmp_path):
    """Minor：入口查 draft 后、覆盖写盘前状态翻转（用户点了渲染）→ 409 不覆盖。"""
    client, task_dir, _svc = _reparse_app(tmp_path, task_status="draft")
    from types import SimpleNamespace

    # 第一次 get_task（入口）draft；第二次（覆盖前复查）已 rendering
    task_draft = SimpleNamespace(status="draft", task_type="remotion_course", payload=json.dumps({"mode": "fixed", "script": "占位脚本" * 30}))
    task_rendering = SimpleNamespace(status="composing", task_type="remotion_course", payload="{}")
    client.app.state.video_gen_service.repo.get = AsyncMock(side_effect=[task_draft, task_rendering])
    with (
        patch("app.gateway.video_gen.orchestration.course_script.parse_course_script", new=AsyncMock(return_value=_reparse_units())),
        patch("app.gateway.video_gen.service.create_script_llm", return_value=MagicMock()),
    ):
        resp = client.post("/api/video/tasks/vg_rp1/reparse", json={"course_parse_mode": "rewrite"})
    assert resp.status_code == 409
    disk = json.loads((task_dir / "storyboard.json").read_text(encoding="utf-8"))
    assert disk["frames"][0]["narration"] == "旧帧旁白一。"  # 未被覆盖


def test_reparse_clears_course_degradations(tmp_path):
    """m6（2026-09-11 二轮审查）：整量覆盖重建 → 解析类旧留痕清除（append-only
    语义下旧「已回落/顺延/溢出」警告永续误导）；跨阶段留痕（bgm 等）保留。"""
    client, task_dir, _svc = _reparse_app(tmp_path, task_status="draft")
    (task_dir / "degradations.json").write_text(
        json.dumps(
            {
                "items": [
                    {"kind": "course_parse_fidelity_fallback", "detail": "旧解析失败"},
                    {"kind": "embedded_image_deferred", "detail": "旧顺延"},
                    {"kind": "layout_overflow_risk", "detail": "旧溢出"},
                    {"kind": "embedded_images_expired", "detail": "旧过期"},
                    {"kind": "bgm_failed", "detail": "BGM 未混上（跨阶段，保留）"},
                ]
            }
        ),
        encoding="utf-8",
    )
    with (
        patch("app.gateway.video_gen.orchestration.course_script.parse_course_script", new=AsyncMock(return_value=_reparse_units())),
        patch("app.gateway.video_gen.service.create_script_llm", return_value=MagicMock()),
    ):
        resp = client.post("/api/video/tasks/vg_rp1/reparse", json={"course_parse_mode": "rewrite"})
    assert resp.status_code == 200, resp.text
    deg = json.loads((task_dir / "degradations.json").read_text(encoding="utf-8"))
    kinds = [i["kind"] for i in deg["items"]]
    assert kinds == ["bgm_failed"]


# ── 条目 3（2026-09-12 缺陷批）：残留旧图帧「重新生成视频」router 同步预检 ──


def _regen_app(tmp_path, *, frames, task_type="remotion_course", task_status="ready"):
    app = FastAPI()
    app.include_router(router)
    storage_root = (tmp_path / "storage").resolve()
    task_dir = storage_root / _TEST_USER / "tasks" / "vg_regen1"
    task_dir.mkdir(parents=True, exist_ok=True)
    (task_dir / "storyboard.json").write_text(
        json.dumps({"title": "t", "config": {"video_type": task_type, "width": 1920, "height": 1080}, "frames": frames}, ensure_ascii=False),
        encoding="utf-8",
    )
    mock_svc = MagicMock()
    mock_task = MagicMock()
    mock_task.status = task_status
    mock_task.task_type = task_type
    mock_svc.get_task = AsyncMock(return_value=mock_task)
    mock_svc._storage_dir = str(storage_root)
    mock_svc._active_tasks = {}
    mock_svc._expire_stale_running_state = AsyncMock()
    app.state.video_gen_service = mock_svc
    return TestClient(app)


def _vf(**kw):
    """VideoClipPage 视频帧模板（条目 3 矩阵用）。"""
    frame = {"index": 0, "narration": "旁白。", "layout": "VideoClipPage", "visual_source": "ai_video", "image_prompt": "提示词"}
    frame.update(kw)
    return [frame]


def test_regenerate_residual_preview_image_400(tmp_path):
    """矩阵·缺陷 A：ai_video + 残留预览图（visual_type=="image"）→ 400 + 文案。"""
    client = _regen_app(tmp_path, frames=_vf(local_visual_path="visuals/img_0001.png", visual_type="image"))
    resp = client.post("/api/video/tasks/vg_regen1/frames/0/regenerate")
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"] == "请先移除旧配图再生成视频"


def test_regenerate_variant_b_residual_ai_image_400(tmp_path):
    """矩阵·变体 B：ai_image 残留 AI 图（切了版式没切源）→ 400（修前静默清素材）。"""
    client = _regen_app(tmp_path, frames=_vf(visual_source="ai_image", local_visual_path="visuals/img_0002.png"))
    resp = client.post("/api/video/tasks/vg_regen1/frames/0/regenerate")
    assert resp.status_code == 400, resp.text
    assert resp.json()["detail"] == "请先移除旧配图再生成视频"


def test_regenerate_localized_video_allowed(tmp_path):
    """矩阵·正常视频重生：已本地化视频（visual_type=="video"）→ 放行（202）。"""
    client = _regen_app(tmp_path, frames=_vf(local_visual_path="visuals/vid_0001.mp4", visual_type="video"))
    with patch("app.gateway.routers.video_gen._regenerate_frame_bg", new=AsyncMock()):
        resp = client.post("/api/video/tasks/vg_regen1/frames/0/regenerate")
    assert resp.status_code == 202, resp.text
    assert resp.json()["status"] == "started"


def test_regenerate_audio_only_not_blocked_by_residual_guard(tmp_path):
    """audio_only 不触画面：残留图帧重配音不受守卫拦截（与 service 层 mode 分支对齐）。"""
    client = _regen_app(tmp_path, frames=_vf(local_visual_path="visuals/img_0001.png", visual_type="image"))
    with patch("app.gateway.routers.video_gen._regenerate_frame_bg", new=AsyncMock()):
        resp = client.post("/api/video/tasks/vg_regen1/frames/0/regenerate", json={"mode": "audio_only"})
    assert resp.status_code == 202, resp.text


# ── P1 / BUG-008：recompose 视觉审计按类型跳过（remotion_course） ──────────


def _recompose_svc(tmp_path, *, frames=None, task_type="remotion_course", with_audio=True, storyboard_text=None):
    """BUG-008 服务级 stub：走真实 recompose_task（__new__ 绕开 __init__ 依赖）。"""
    _task_type = task_type  # class 体读不到函数形参——别名列（同 test_recompose_missing_files 惯例）
    from app.gateway.video_gen.service import VideoGenService

    storage_root = (tmp_path / "storage").resolve()
    svc = VideoGenService.__new__(VideoGenService)
    svc._active_tasks = {}
    svc._storage_dir = str(storage_root)
    task_dir = storage_root / _TEST_USER / "tasks" / "vg_rc1"
    task_dir.mkdir(parents=True, exist_ok=True)
    if with_audio:
        (task_dir / "audio.mp3").write_bytes(b"au")
    if storyboard_text is not None:
        (task_dir / "storyboard.json").write_text(storyboard_text, encoding="utf-8")
    elif frames is not None:
        (task_dir / "storyboard.json").write_text(
            json.dumps({"title": "t", "config": {"video_type": task_type}, "frames": frames}, ensure_ascii=False),
            encoding="utf-8",
        )

    class _Task:
        status = "ready"
        task_type = _task_type
        payload = "{}"
        dirty_frames = []

    class _Repo:
        async def get(self, task_id, user_id=None):
            return _Task()

    svc.repo = _Repo()
    return svc


def _fake_wrap_sink(svc, captured):
    async def _fake_wrap(*a, **k):
        captured["called"] = True
        return None

    svc._wrap_pipeline = _fake_wrap


@pytest.mark.asyncio
async def test_recompose_remotion_course_placeholder_frames_pass(tmp_path):
    """BUG-008 主锚：ready remotion_course 全占位帧（合法无素材）→ 放行进入 composing。

    修前：视觉审计对全部类型无差别执行 → 全帧判 missing → ValueError 409（100% 复现）。"""
    import asyncio

    svc = _recompose_svc(
        tmp_path,
        frames=[
            {"index": 0, "narration": "第一句。", "layout": "ConceptPage"},
            {"index": 1, "narration": "第二句。", "layout": "ChapterDivider"},
        ],
    )
    captured = {}
    _fake_wrap_sink(svc, captured)
    result = await svc.recompose_task("vg_rc1", user_id=_TEST_USER)
    assert result["status"] == "composing"
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_recompose_remotion_course_missing_audio_still_blocked(tmp_path):
    """E13 保留：remotion_course 缺 audio.mp3 → 仍 ValueError（中文「缺音频」文案）。"""
    svc = _recompose_svc(
        tmp_path,
        frames=[{"index": 0, "narration": "一。", "layout": "ConceptPage"}],
        with_audio=False,
    )
    with pytest.raises(ValueError, match="缺音频"):
        await svc.recompose_task("vg_rc1", user_id=_TEST_USER)


@pytest.mark.asyncio
async def test_recompose_remotion_course_pending_regen_still_blocked(tmp_path):
    """M1 保留：旁白已改未重生（pending_regen_frames）→ 仍拦（remotion 合成依赖盘面音频时长）。"""
    svc = _recompose_svc(
        tmp_path,
        frames=[{"index": 0, "narration": "一。", "layout": "ConceptPage"}],
        storyboard_text=json.dumps(
            {
                "title": "t",
                "config": {"video_type": "remotion_course"},
                "frames": [{"index": 0, "narration": "一。", "layout": "ConceptPage"}],
                "pending_regen_frames": [0],
            },
            ensure_ascii=False,
        ),
    )
    with pytest.raises(ValueError, match="旁白已修改"):
        await svc.recompose_task("vg_rc1", user_id=_TEST_USER)


@pytest.mark.asyncio
async def test_recompose_remotion_course_regen_error_frame_passes(tmp_path):
    """放行决策回归锚（2026-09-14 拍板）：remotion_course 帧 regen_state=error → 放行。

    remotion 帧级配图重生失败不阻塞模板合成（占位/缺图由 worker QC 三扫描兜底，D19）。"""
    import asyncio

    svc = _recompose_svc(
        tmp_path,
        frames=[
            {"index": 0, "narration": "一。", "layout": "ConceptPage", "regen_state": "error", "regen_error": "boom"},
            {"index": 1, "narration": "二。", "layout": "ConceptPage"},
        ],
    )
    captured = {}
    _fake_wrap_sink(svc, captured)
    result = await svc.recompose_task("vg_rc1", user_id=_TEST_USER)
    assert result["status"] == "composing"
    await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_recompose_ai_image_missing_visual_still_blocked(tmp_path, monkeypatch):
    """对照锚：ai_image 无素材帧仍被视觉审计拦截——跳过仅对 remotion_course 生效。"""
    import app.gateway.video_gen.visual_availability as va

    async def _alive(url, timeout):
        return True

    monkeypatch.setattr(va, "_head_ok", _alive)
    svc = _recompose_svc(
        tmp_path,
        task_type="ai_image",
        frames=[
            {"index": 0, "narration": "一。", "visual_url": "https://cdn/0.png"},
            {"index": 1, "narration": "二。"},  # 无素材 → missing
        ],
    )
    with pytest.raises(ValueError, match="帧"):
        await svc.recompose_task("vg_rc1", user_id=_TEST_USER)


# ── P3-3 / BUG-006（2026-09-14）：插入/删除帧拒绝文案分场景中文化 ──────────────


def test_add_frame_reject_detail_by_type(tmp_path):
    """功能状态维持（已渲染仍拒），文案按 task_type 分版——remotion_course 给
    「重新解析」重建出路，segments 型给「重新创建任务」指引。"""
    # remotion_course ready → 409 + 重建分镜出路
    client = _regen_app(tmp_path, frames=_vf(), task_type="remotion_course", task_status="ready")
    resp = client.post("/api/video/tasks/vg_regen1/storyboard/frames", json={"narration": "新帧旁白。"})
    assert resp.status_code == 409, resp.text
    d = resp.json()["detail"]
    assert "仅草稿状态可增删分镜" in d
    assert "重新解析" in d, "remotion_course 版须含分镜页重建出路"
    assert "重新创建任务" not in d

    # ai_image ready → 409 + 重新创建任务指引
    client = _regen_app(tmp_path, frames=_vf(), task_type="ai_image", task_status="ready")
    resp = client.post("/api/video/tasks/vg_regen1/storyboard/frames", json={"narration": "新帧旁白。"})
    assert resp.status_code == 409, resp.text
    d = resp.json()["detail"]
    assert "仅草稿状态可增删分镜" in d
    assert "重新创建任务" in d
    assert "重新解析" not in d


def test_delete_frame_reject_detail_by_type(tmp_path):
    """删除帧同口径（两端点共用 _frame_edit_reject_detail）。"""
    client = _regen_app(tmp_path, frames=_vf(), task_type="remotion_course", task_status="ready")
    resp = client.delete("/api/video/tasks/vg_regen1/storyboard/frames/0")
    assert resp.status_code == 409, resp.text
    assert "仅草稿状态可增删分镜" in resp.json()["detail"]
    assert "重新解析" in resp.json()["detail"]

    client = _regen_app(tmp_path, frames=_vf(), task_type="ai_video", task_status="ready")
    resp = client.delete("/api/video/tasks/vg_regen1/storyboard/frames/0")
    assert resp.status_code == 409, resp.text
    assert "重新创建任务" in resp.json()["detail"]
