"""Plan B Task 3（2026-10-19）：存量入口付费前拒绝 + 默认试听 API 的 ViMax 参数。

覆盖（plan Step 2/3）：
- ``GET /voices/default`` 无 task_type 的 URL/响应不变；``task_type=vimax`` 返回
  ViMax 默认解析（env speaker > 白名单首项 > Edge 语言默认）——React 消费同一
  ``{"voice_id": ...}`` 契约。
- ViMax 新建（/create）、设置保存（PUT storyboard/config）、准备音频
  （POST generate-audio）的显式/存量 ``preset:*`` 在付费或后台任务启动前可读拒绝。
- 同项目/同请求用于 ai_image 时 preset 仍允许（项目编辑接口不全局拒绝）。
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.gateway.routers.video_gen import get_storyboard, router


def _app(svc) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.state.video_gen_service = svc
    return app


def _fake_svc(*, task_type: str = "ai_image", payload: dict | None = None, status: str = "draft"):
    """最小 service 替身：get_task/submit 可观测，其余不触达。"""
    payload = payload if payload is not None else {}
    task = SimpleNamespace(
        id="t1",
        task_type=task_type,
        status=status,
        payload=json.dumps(payload, ensure_ascii=False),
        result="{}",
        progress=10,
    )
    svc = SimpleNamespace(
        repo=SimpleNamespace(
            get=AsyncMock(return_value=task),
            update=AsyncMock(),
        ),
        get_task=AsyncMock(return_value=task),
        submit=AsyncMock(
            return_value=SimpleNamespace(
                id="new-task",
                user_id="u",
                domain="video",
                task_type="ai_image",
                status="draft",
                progress=0,
                title="t",
                error_message=None,
                retry_count=0,
                dirty_frames=[],
                created_at="2026-10-19T00:00:00Z",
                updated_at="2026-10-19T00:00:00Z",
            )
        ),
        _storage_dir="/tmp/vp-test",
    )
    return svc


class _FakeRequest:
    def __init__(self, svc):
        import types

        self.app = types.SimpleNamespace(state=types.SimpleNamespace(video_gen_service=svc))


def svc_with_storage(svc, tmp_path):
    svc._storage_dir = str(tmp_path)
    return svc


# ── /voices/default ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_voices_default_without_task_type_unchanged(monkeypatch):
    """缺省 query 的旧响应不变：无 env 时 zh → preset:146（共享 resolve_default_voice）。"""
    monkeypatch.delenv("DOUBAO_TTS_DEFAULT_SPEAKER", raising=False)
    app = _app(_fake_svc())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/api/video/voices/default", params={"lang": "zh"})
    assert resp.status_code == 200
    assert resp.json() == {"voice_id": "preset:146"}


@pytest.mark.asyncio
async def test_voices_default_vimax_env_speaker(monkeypatch):
    monkeypatch.setenv("VOLCENGINE_VOICE_ACCESS_KEY", "k")
    monkeypatch.setenv("DOUBAO_TTS_DEFAULT_SPEAKER", "speaker_x")
    monkeypatch.delenv("DOUBAO_TTS_ENABLED_SPEAKERS", raising=False)
    app = _app(_fake_svc())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/api/video/voices/default", params={"lang": "zh", "task_type": "vimax"})
    assert resp.status_code == 200
    assert resp.json() == {"voice_id": "doubao:speaker_x"}


@pytest.mark.asyncio
async def test_voices_default_vimax_whitelist_first(monkeypatch):
    monkeypatch.setenv("VOLCENGINE_VOICE_ACCESS_KEY", "k")
    monkeypatch.delenv("DOUBAO_TTS_DEFAULT_SPEAKER", raising=False)
    monkeypatch.setenv("DOUBAO_TTS_ENABLED_SPEAKERS", "spk_b,spk_c")
    app = _app(_fake_svc())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/api/video/voices/default", params={"lang": "zh", "task_type": "vimax"})
    assert resp.json() == {"voice_id": "doubao:spk_b"}


@pytest.mark.asyncio
async def test_voices_default_vimax_nothing_falls_to_edge(monkeypatch):
    monkeypatch.delenv("DOUBAO_TTS_DEFAULT_SPEAKER", raising=False)
    monkeypatch.delenv("DOUBAO_TTS_ENABLED_SPEAKERS", raising=False)
    app = _app(_fake_svc())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/api/video/voices/default", params={"lang": "zh", "task_type": "vimax"})
    assert resp.json() == {"voice_id": "zh-CN-YunjianNeural"}


# ── 入口付费前拒绝 ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_vimax_preset_rejected_before_submit():
    """ViMax 新建显式 preset：在 svc.submit（后台任务启动）前 422 可读拒绝。"""
    svc = _fake_svc()
    app = _app(svc)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.post(
            "/api/video/create",
            json={"video_type": "vimax", "mode": "generate", "topic": "课程主题", "voice_id": "preset:332"},
        )
    assert resp.status_code == 422, f"ViMax+preset 须可读拒绝，实际 {resp.status_code}: {resp.text}"
    assert "ViMax" in resp.json()["detail"]
    svc.submit.assert_not_awaited(), "preset 不得触达 submit（后台付费任务入口）"


@pytest.mark.asyncio
async def test_create_ai_image_preset_still_allowed():
    """同字段用于 ai_image：preset 照常受理（其他视频类型语义不变）。"""
    svc = _fake_svc()
    app = _app(svc)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.post(
            "/api/video/create",
            json={"video_type": "ai_image", "mode": "generate", "topic": "主题", "voice_id": "preset:332"},
        )
    assert resp.status_code == 200, f"ai_image+preset 须放行，实际 {resp.status_code}: {resp.text}"
    svc.submit.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_vimax_doubao_allowed():
    """ViMax 改选豆包后放行。"""
    svc = _fake_svc()
    app = _app(svc)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.post(
            "/api/video/create",
            json={"video_type": "vimax", "mode": "generate", "topic": "课程主题", "voice_id": "doubao:zh_male_A"},
        )
    assert resp.status_code == 200, f"ViMax+doubao 须放行，实际 {resp.status_code}: {resp.text}"
    svc.submit.assert_awaited_once()


@pytest.mark.asyncio
async def test_generate_audio_vimax_stale_preset_rejected(tmp_path, monkeypatch):
    """准备音频：存量 ViMax 草稿任务（payload voice=preset）在后台 TTS 启动前拒绝。"""
    svc = _fake_svc(task_type="vimax", payload={"voice_id": "preset:332"})
    app = _app(svc)
    # generate-audio 用 asyncio.create_task 派发后台 TTS——拒绝必须发生在派发前
    # （状态码 422 即证明：进入了 handler 但未放行后台任务）。
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.post("/api/video/tasks/t1/generate-audio")
    assert resp.status_code == 422, f"存量 preset 须在后台 TTS 启动前拒绝，实际 {resp.status_code}: {resp.text}"
    assert "ViMax" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_update_config_vimax_preset_rejected(tmp_path):
    """设置保存：ViMax 任务 PUT storyboard/config voice_id=preset → 422；ai_image 不受影响。"""
    from deerflow.runtime.user_context import get_effective_user_id

    svc = _fake_svc(task_type="vimax")
    sb_dir = tmp_path / get_effective_user_id() / "tasks" / "t1"
    sb_dir.mkdir(parents=True)
    (sb_dir / "storyboard.json").write_text(json.dumps({"title": "t", "config": {"video_type": "vimax"}, "frames": []}))
    svc._storage_dir = str(tmp_path)
    app = _app(svc)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.put("/api/video/tasks/t1/storyboard/config", json={"voice_id": "preset:332"})
    assert resp.status_code == 422, f"ViMax 设置保存 preset 须可读拒绝，实际 {resp.status_code}: {resp.text}"
    assert "ViMax" in resp.json()["detail"]


# ── Plan B Task 4：draft storyboard 的 audio_degradation_warning ────────────


@pytest.mark.asyncio
async def test_storyboard_warning_from_settled_voice_fallback_only(tmp_path):
    """audio_degradation_warning 只由带 frame_index 的「实际音色已归位」记录生成。"""
    import json as _json

    from deerflow.runtime.user_context import get_effective_user_id

    svc = _fake_svc(task_type="vimax", payload={})
    task_dir = tmp_path / get_effective_user_id() / "tasks" / "t1"
    task_dir.mkdir(parents=True)
    (task_dir / "storyboard.json").write_text(_json.dumps({"title": "t", "config": {"video_type": "vimax"}, "frames": []}))
    # 两条记录：attempt（无 frame_index，TTS 调用前）+ 归位（带 frame_index 与结构化音色）
    (task_dir / "degradations.json").write_text(
        _json.dumps(
            {
                "items": [
                    {"kind": "voice_fallback", "detail": "豆包 TTS 传输错误，已降级为 Edge 备用音色（zh-CN-YunjianNeural）"},
                    {"kind": "voice_fallback", "detail": "请求 doubao:zh_male_A，实际 zh-CN-YunjianNeural", "frame_index": 3, "requested_voice": "doubao:zh_male_A", "actual_voice": "zh-CN-YunjianNeural"},
                ]
            },
            ensure_ascii=False,
        )
    )
    svc._storage_dir = str(tmp_path)
    app = _app(svc)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/api/video/tasks/t1/storyboard")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body.get("audio_degradation_warning") == "已使用备用音色（请求 doubao:zh_male_A，实际 zh-CN-YunjianNeural）", f"warning 须来自归位记录，实际 {body.get('audio_degradation_warning')!r}"


@pytest.mark.asyncio
async def test_storyboard_no_warning_from_attempt_only_or_clean(tmp_path):
    """只有 attempt 记录（无 frame_index）或无记录 → warning 为 null（不据 attempt 推断成功降级）。"""
    import json as _json

    from deerflow.runtime.user_context import get_effective_user_id

    for sidecar_items, label in (
        ([{"kind": "voice_fallback", "detail": "豆包 TTS 传输错误，已降级…"}], "attempt-only"),
        ([], "clean"),
    ):
        svc = _fake_svc(task_type="vimax", payload={})
        task_dir = tmp_path / label / get_effective_user_id() / "tasks" / "t1"
        task_dir.mkdir(parents=True)
        (task_dir / "storyboard.json").write_text(_json.dumps({"title": "t", "config": {"video_type": "vimax"}, "frames": []}))
        (task_dir / "degradations.json").write_text(_json.dumps({"items": sidecar_items}, ensure_ascii=False))
        svc._storage_dir = str(tmp_path / label)
        app = _app(svc)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            resp = await c.get("/api/video/tasks/t1/storyboard")
        assert resp.json().get("audio_degradation_warning") is None, f"{label} 场景不得产生成功降级提示"


# ── Plan B Task 5：行为级端到端（mock TTS 边界，真实 service/router 链） ─────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "edge_ok,expect",
    [
        (True, "degraded_ready"),  # doubao 429 → Edge 成功 → audioReady + 实际音色警告
        (False, "error_no_warning"),  # Edge 再失败 → audioError（tts 阶段）+ 无成功警告
    ],
)
async def test_vimax_draft_doubao_failure_edge_behavior(tmp_path, edge_ok, expect):
    """同一 ViMax 草稿：doubao 临时故障自动 Edge；audioReady 与实际音色警告一致；
    Edge 失败则阶段化错误且无成功降级提示。"""
    import asyncio
    import json as _json
    from unittest.mock import MagicMock

    from app.gateway._shared.task_framework.schemas import DRAFT
    from app.gateway.video_gen.config import VideoGenConfig
    from app.gateway.video_gen.providers.registry import ProviderRegistry
    from app.gateway.video_gen.service import VideoGenService
    from deerflow.runtime.user_context import get_effective_user_id

    cfg = VideoGenConfig(qihu_api_key="t", script_model="m")
    svc = VideoGenService(repo=MagicMock(), config=cfg, providers=ProviderRegistry(cfg), storage_dir=str(tmp_path), sandbox_provider=AsyncMock())
    task = MagicMock()
    task.task_type = "vimax"
    task.status = DRAFT
    task.payload = "{}"
    svc.repo.get = AsyncMock(return_value=task)
    svc.repo.update = AsyncMock()

    task_dir = tmp_path / get_effective_user_id() / "tasks" / "vg_b5"
    task_dir.mkdir(parents=True)
    (task_dir / "storyboard.json").write_text(
        _json.dumps(
            {
                "title": "t",
                "config": {"video_type": "vimax", "width": 1080, "height": 1920, "voice_id": "doubao:zh_male_A", "tts_speed": 1.2},
                "frames": [{"index": 0, "narration": "课程第一句", "duration": 2.0}],
            }
        )
    )

    from app.gateway.voice_twin.doubao_tts_client import DoubaoTTSClient, DoubaoTTSHTTPError

    async def doubao_429(self, *a, **kw):
        raise DoubaoTTSHTTPError(429, "rate limited")

    def make_tts_result(out, voice):
        from app.gateway.video_gen.orchestration.tts import TTSResult

        Path(out).write_bytes(b"mp3")
        return TTSResult(audio_path=str(out), srt="", duration=2.0, actual_voice_id=voice)

    async def fake_edge(text, voice_id, speed, output_path):
        if not edge_ok:
            raise RuntimeError("edge down")
        return make_tts_result(output_path, voice_id)

    async def fake_dur(p):
        return 2.0

    from app.gateway.video_gen.orchestration import tts as tts_mod

    with (
        patch.object(DoubaoTTSClient, "synthesize_sse", doubao_429),
        patch.object(DoubaoTTSClient, "synthesize_sse_with_timestamps", doubao_429),
        patch.dict("os.environ", {"VOLCENGINE_VOICE_ACCESS_KEY": "k"}),
        patch.object(tts_mod._call_doubao_with_retry.retry, "wait", tts_mod.tenacity.wait_none()),
        patch.object(tts_mod, "_synthesize_edge", fake_edge),
        patch("app.gateway.video_gen.service.run_ffmpeg", new=AsyncMock()),
        patch("app.gateway.video_gen.service._write_storyboard_srt"),
        patch("app.gateway.video_gen.service._get_audio_duration", new=fake_dur),
    ):
        await asyncio.wait_for(svc._generate_draft_audio("vg_b5", task_dir, user_id=get_effective_user_id()), timeout=30)

    pl = json.loads(svc.repo.update.call_args_list[-1].kwargs["payload"])

    if expect == "degraded_ready":
        assert pl.get("audio_ready") is True, f"Edge 兜底成功须 audioReady，实际 payload={pl}"
        assert "audio_error" not in pl
        # storyboard 响应携带实际音色警告（经归位记录）
        resp = await get_storyboard(_FakeRequest(svc_with_storage(svc, tmp_path)), "vg_b5")
        body = json.loads(resp.body) if hasattr(resp, "body") else resp
        assert body.get("audio_degradation_warning") == "已使用备用音色（请求 doubao:zh_male_A，实际 zh-CN-YunjianNeural）", f"实际 {body.get('audio_degradation_warning')!r}"
    else:
        assert "audio_ready" not in pl and pl.get("audio_error_stage") == "tts", f"Edge 失败须阶段化 tts 错误，实际 payload={pl}"
        resp = await get_storyboard(_FakeRequest(svc_with_storage(svc, tmp_path)), "vg_b5")
        body = json.loads(resp.body) if hasattr(resp, "body") else resp
        assert body.get("audio_degradation_warning") is None, "失败轮不得出现成功降级提示"


# ── 推送前对抗评审修复（2026-10-19）：草稿路径接策略 + 门校验 config 源 ────


@pytest.mark.asyncio
async def test_generate_draft_vimax_default_voice_not_preset(tmp_path, monkeypatch):
    """UI 默认草稿路径（_generate_draft）：vimax 缺省音色走 ViMax 解析（zh 兜底
    不再 preset:146——否则后台草稿 TTS 被 reject 策略整体炸成通用错误循环）。"""
    from unittest.mock import AsyncMock, MagicMock

    from app.gateway.video_gen.config import VideoGenConfig
    from app.gateway.video_gen.providers.registry import ProviderRegistry
    from app.gateway.video_gen.service import VideoGenService

    monkeypatch.delenv("DOUBAO_TTS_DEFAULT_SPEAKER", raising=False)
    monkeypatch.delenv("DOUBAO_TTS_ENABLED_SPEAKERS", raising=False)

    cfg = VideoGenConfig(qihu_api_key="t", script_model="m")
    svc = VideoGenService(repo=MagicMock(), config=cfg, providers=ProviderRegistry(cfg), storage_dir=str(tmp_path), sandbox_provider=AsyncMock())

    resolved = []
    orig = svc._resolve_voice_id

    def spy_resolve(payload_voice, project_id, *, user_id="default"):
        resolved.append(payload_voice)
        return orig(payload_voice, project_id, user_id=user_id) if payload_voice else None

    svc._resolve_voice_id = spy_resolve
    # 直接驱动提取出的 vimax 感知解析 helper（_generate_draft 与 _prepare 共用）
    voice_id, source = svc._resolve_voice_with_vimax_gate("vimax", {"lang": "zh"}, user_id="u")
    assert voice_id == "zh-CN-YunjianNeural", f"vimax 缺省不得落 preset:146，实际 {voice_id!r}"
    assert source == "lang_default"
    voice_id2, _ = svc._resolve_voice_with_vimax_gate("ai_image", {"lang": "zh"}, user_id="u")
    assert voice_id2 == "preset:146", "非 vimax 缺省链必须保持共享语义（preset:146）"


@pytest.mark.asyncio
async def test_generate_draft_vimax_inherited_preset_rejected(tmp_path, monkeypatch):
    """vimax + 项目继承/显式 preset → 草稿生成前 ValueError（可读拒绝，先于 LLM）。"""
    from unittest.mock import AsyncMock, MagicMock

    from app.gateway.video_gen.config import VideoGenConfig
    from app.gateway.video_gen.providers.registry import ProviderRegistry
    from app.gateway.video_gen.service import VideoGenService

    cfg = VideoGenConfig(qihu_api_key="t", script_model="m")
    svc = VideoGenService(repo=MagicMock(), config=cfg, providers=ProviderRegistry(cfg), storage_dir=str(tmp_path), sandbox_provider=AsyncMock())
    monkeypatch.setattr(svc, "_resolve_voice_id", lambda pv, pid, *, user_id="default": "preset:332")

    with pytest.raises(ValueError, match="ViMax"):
        svc._resolve_voice_with_vimax_gate("vimax", {}, user_id="u")


@pytest.mark.asyncio
async def test_generate_audio_vimax_config_only_preset_rejected(tmp_path):
    """/generate-audio 门：存量 preset 只在盘面 config（合成事实源）时同样 422。"""
    import json as _json

    from deerflow.runtime.user_context import get_effective_user_id

    svc = _fake_svc(task_type="vimax", payload={})  # payload 无 voice——preset 只在 config
    task_dir = tmp_path / get_effective_user_id() / "tasks" / "t1"
    task_dir.mkdir(parents=True)
    (task_dir / "storyboard.json").write_text(_json.dumps({"title": "t", "config": {"video_type": "vimax", "voice_id": "preset:332"}, "frames": []}))
    svc._storage_dir = str(tmp_path)
    app = _app(svc)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.post("/api/video/tasks/t1/generate-audio")
    assert resp.status_code == 422, f"config-only 存量 preset 须可读拒绝，实际 {resp.status_code}: {resp.text}"
    assert "ViMax" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_storyboard_warning_gated_to_vimax(tmp_path):
    """audio_degradation_warning 仅 vimax 任务发出——其他类型 storyboard 响应零变化
    （评审：结构化归位字段写入不分类型，若 warning 也无门则 ai_image 语义漂移）。"""
    import json as _json

    from deerflow.runtime.user_context import get_effective_user_id

    svc = _fake_svc(task_type="ai_image", payload={})
    task_dir = tmp_path / get_effective_user_id() / "tasks" / "t1"
    task_dir.mkdir(parents=True)
    (task_dir / "storyboard.json").write_text(_json.dumps({"title": "t", "config": {"video_type": "ai_image"}, "frames": []}))
    (task_dir / "degradations.json").write_text(
        _json.dumps(
            {"items": [{"kind": "voice_fallback", "detail": "请求 doubao:a，实际 preset:146", "frame_index": 0, "requested_voice": "doubao:a", "actual_voice": "preset:146"}]},
            ensure_ascii=False,
        )
    )
    svc._storage_dir = str(tmp_path)
    app = _app(svc)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.get("/api/video/tasks/t1/storyboard")
    assert resp.status_code == 200
    assert resp.json().get("audio_degradation_warning") is None, "非 vimax 任务不得出现降级警告字段语义"


# ── 边界收口 Task 3（P1-2）：confirm 对 config-only preset 付费前拒绝 ──────


@pytest.mark.asyncio
async def test_resume_draft_vimax_config_only_preset_rejected(tmp_path):
    """盘面 storyboard.config.voice_id=preset、payload 无 voice → confirm 在
    派发渲染（任何 LLM/付费）前可读拒绝；不静默换默认声。"""
    import json as _json
    from unittest.mock import MagicMock, patch

    from app.gateway._shared.task_framework.schemas import DRAFT
    from app.gateway.video_gen.config import VideoGenConfig
    from app.gateway.video_gen.providers.registry import ProviderRegistry
    from app.gateway.video_gen.service import VideoGenService
    from deerflow.runtime.user_context import get_effective_user_id

    cfg = VideoGenConfig(qihu_api_key="t", script_model="m")
    svc = VideoGenService(repo=MagicMock(), config=cfg, providers=ProviderRegistry(cfg), storage_dir=str(tmp_path), sandbox_provider=AsyncMock())
    task = MagicMock()
    task.task_type = "vimax"
    task.status = DRAFT
    task.payload = "{}"
    svc.repo.get = AsyncMock(return_value=task)
    svc.repo.update = AsyncMock()
    task_dir = tmp_path / get_effective_user_id() / "tasks" / "t1"
    task_dir.mkdir(parents=True)
    (task_dir / "storyboard.json").write_text(_json.dumps({"title": "t", "config": {"video_type": "vimax", "width": 1080, "height": 1920, "voice_id": "preset:332"}, "frames": [{"index": 0, "narration": "正文一句", "duration": 2.0}]}))
    dispatched: list = []

    async def _fake_wrap(*a, **kw):
        dispatched.append(a)

    with patch.object(svc, "_wrap_pipeline", new=_fake_wrap):
        with pytest.raises(ValueError, match="ViMax"):
            await svc.resume_draft("t1", get_effective_user_id())
    assert not dispatched, "拒绝必须发生在渲染管线派发（付费/LLM）之前"


@pytest.mark.asyncio
async def test_resume_draft_vimax_doubao_allowed_and_dispatches(tmp_path):
    """盘面 config doubao + payload 无 voice → 放行并派发（既有主流程不回归）。"""
    import asyncio
    import json as _json
    from unittest.mock import MagicMock, patch

    from app.gateway._shared.task_framework.schemas import DRAFT
    from app.gateway.video_gen.config import VideoGenConfig
    from app.gateway.video_gen.providers.registry import ProviderRegistry
    from app.gateway.video_gen.service import VideoGenService
    from deerflow.runtime.user_context import get_effective_user_id

    cfg = VideoGenConfig(qihu_api_key="t", script_model="m")
    svc = VideoGenService(repo=MagicMock(), config=cfg, providers=ProviderRegistry(cfg), storage_dir=str(tmp_path), sandbox_provider=AsyncMock())
    task = MagicMock()
    task.task_type = "vimax"
    task.status = DRAFT
    task.payload = "{}"
    svc.repo.get = AsyncMock(return_value=task)
    svc.repo.update = AsyncMock()
    task_dir = tmp_path / get_effective_user_id() / "tasks" / "t1"
    task_dir.mkdir(parents=True)
    (task_dir / "storyboard.json").write_text(_json.dumps({"title": "t", "config": {"video_type": "vimax", "width": 1080, "height": 1920, "voice_id": "doubao:zh_male_A"}, "frames": [{"index": 0, "narration": "正文一句", "duration": 2.0}]}))
    dispatched: list = []

    async def _fake_wrap(*a, **kw):
        dispatched.append(a)

    with patch.object(svc, "_wrap_pipeline", new=_fake_wrap):
        await svc.resume_draft("t1", get_effective_user_id())
        await asyncio.sleep(0.05)  # 派发经 create_task 异步起步
    assert dispatched, "合法音色须照常派发渲染"


# ── 边界热修复（2026-10-20 P1）：confirm 门覆盖项目继承音色 ────────────────


@pytest.mark.asyncio
async def test_resume_draft_vimax_project_inherited_preset_rejected_before_dispatch(tmp_path):
    """[Warning③] payload 无音色、项目继承 voice_id=preset:*、盘面 config 无 voice：
    confirm 须在派发渲染前同步可读拒绝——修前只查 payload 显式与盘面 config 两源，
    项目继承 preset 漏拦直到引擎侧才炸。断言「未派发」而非仅最终报错：
    _wrap_pipeline 未被调、repo.update 未发生。"""
    import json as _json
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.gateway._shared.task_framework.schemas import DRAFT
    from app.gateway.video_gen.config import VideoGenConfig
    from app.gateway.video_gen.providers.registry import ProviderRegistry
    from app.gateway.video_gen.service import VideoGenService
    from deerflow.runtime.user_context import get_effective_user_id

    cfg = VideoGenConfig(qihu_api_key="t", script_model="m")
    svc = VideoGenService(repo=MagicMock(), config=cfg, providers=ProviderRegistry(cfg), storage_dir=str(tmp_path), sandbox_provider=AsyncMock())
    task = MagicMock()
    task.task_type = "vimax"
    task.status = DRAFT
    task.payload = _json.dumps({"course_project_id": "proj-1"})  # 无 voice_id——音色只来自项目
    svc.repo.get = AsyncMock(return_value=task)
    svc.repo.update = AsyncMock()
    # 项目继承链真实走 _read_course_project（不打桩 _resolve_voice_id，防测点漂移）
    svc._read_course_project = lambda pid, *, user_id="default": SimpleNamespace(voice_id="preset:332") if pid == "proj-1" else None
    task_dir = tmp_path / get_effective_user_id() / "tasks" / "t1"
    task_dir.mkdir(parents=True)
    # 盘面 config 不带 voice_id——隔离出「项目继承」单一来源
    (task_dir / "storyboard.json").write_text(_json.dumps({"title": "t", "config": {"video_type": "vimax", "width": 1080, "height": 1920}, "frames": [{"index": 0, "narration": "正文一句", "duration": 2.0}]}))
    dispatched: list = []

    async def _fake_wrap(*a, **kw):
        dispatched.append(a)

    with patch.object(svc, "_wrap_pipeline", new=_fake_wrap):
        with pytest.raises(ValueError, match="ViMax"):
            await svc.resume_draft("t1", get_effective_user_id())
    assert not dispatched, "拒绝必须发生在渲染管线派发（付费/LLM）之前"
    assert svc.repo.update.await_count == 0, "拒绝须先于任何 payload 变更/派发登记"


@pytest.mark.asyncio
async def test_resume_draft_vimax_project_inherited_doubao_allowed_and_dispatches(tmp_path):
    """[回归] 项目继承合法 doubao 音色：confirm 放行并照常派发（不误伤主流程）。"""
    import asyncio
    import json as _json
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.gateway._shared.task_framework.schemas import DRAFT
    from app.gateway.video_gen.config import VideoGenConfig
    from app.gateway.video_gen.providers.registry import ProviderRegistry
    from app.gateway.video_gen.service import VideoGenService
    from deerflow.runtime.user_context import get_effective_user_id

    cfg = VideoGenConfig(qihu_api_key="t", script_model="m")
    svc = VideoGenService(repo=MagicMock(), config=cfg, providers=ProviderRegistry(cfg), storage_dir=str(tmp_path), sandbox_provider=AsyncMock())
    task = MagicMock()
    task.task_type = "vimax"
    task.status = DRAFT
    task.payload = _json.dumps({"course_project_id": "proj-1"})
    svc.repo.get = AsyncMock(return_value=task)
    svc.repo.update = AsyncMock()
    svc._read_course_project = lambda pid, *, user_id="default": SimpleNamespace(voice_id="doubao:zh_male_A") if pid == "proj-1" else None
    task_dir = tmp_path / get_effective_user_id() / "tasks" / "t1"
    task_dir.mkdir(parents=True)
    (task_dir / "storyboard.json").write_text(_json.dumps({"title": "t", "config": {"video_type": "vimax", "width": 1080, "height": 1920}, "frames": [{"index": 0, "narration": "正文一句", "duration": 2.0}]}))
    dispatched: list = []

    async def _fake_wrap(*a, **kw):
        dispatched.append(a)

    with patch.object(svc, "_wrap_pipeline", new=_fake_wrap):
        await svc.resume_draft("t1", get_effective_user_id())
        await asyncio.sleep(0.05)  # 派发经 create_task 异步起步
    assert dispatched, "项目继承 doubao 须照常派发渲染"
