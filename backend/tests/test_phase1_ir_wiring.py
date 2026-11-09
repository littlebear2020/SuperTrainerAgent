"""Phase 1 方言层接线（2026-08-17 P1b）：planner IR 产出 -> 搬回 -> fill 方言路由。

覆盖：course 系统提示 IR 指令（仅 ai_video）/ _apply_ir_fields 收窄与降级 /
robust 搬回 / ai_video.fill 按 provider_name+env 路由 / visual_ir 序列化往返 /
手改帧清 IR（service frame_edits 路径）。
"""

from __future__ import annotations

import pytest

from app.gateway.video_gen.orchestration import storyboard_planner as planner
from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame


class _CapLLM:
    def __init__(self, content: str) -> None:
        self._content = content
        self.messages: list = []

    async def ainvoke(self, messages):
        self.messages.append(messages)

        class _Resp:
            content = self._content

        return _Resp()


def _sb(video_type: str, n: int = 1) -> Storyboard:
    return Storyboard(
        title="t",
        config=StoryboardConfig(video_type=video_type, width=1920, height=1080),
        frames=[StoryboardFrame(index=i, narration=f"旁白{i}") for i in range(n)],
    )


# ── T2：系统提示 IR 指令（仅 ai_video）───────────────────────────────────


@pytest.mark.asyncio
async def test_course_ir_instruction_only_for_video():
    llm_v = _CapLLM('{"frames": [{"image_prompt": "p", "visual_source": "ai_video", "ir": {"subject": "s", "action": "a", "environment": "e", "style": "st", "audio": "au"}}]}')
    await planner._generate_course_prompts_enhanced(llm_v, _sb("ai_video"), "")
    sys_v = " ".join(str(m.get("content", "")) for m in llm_v.messages[0])
    assert '"ir"' in sys_v and "subject" in sys_v, "ai_video 契约须含 ir 对象"
    assert "音效描述" in sys_v, "D-A：audio=动作耦合音效"

    llm_i = _CapLLM('{"frames": [{"image_prompt": "p", "visual_source": "ai_image"}]}')
    await planner._generate_course_prompts_enhanced(llm_i, _sb("ai_image"), "")
    sys_i = " ".join(str(m.get("content", "")) for m in llm_i.messages[0])
    assert '"ir"' not in sys_i, "ai_image 不产 IR（图片无方言层，实验 A 定）"


@pytest.mark.asyncio
async def test_course_llm_ir_written_to_frames():
    llm = _CapLLM('{"frames": [{"image_prompt": "p", "visual_source": "ai_video", "ir": {"subject": "金属机械臂", "action": "拾起小球", "environment": "实验室冷白光", "style": "精密工程", "audio": "伺服电机低鸣", "junk": "x"}}]}')
    mini = _sb("ai_video")
    await planner._generate_course_prompts_enhanced(llm, mini, "")
    ir = mini.frames[0].visual_ir
    assert ir == {"subject": "金属机械臂", "action": "拾起小球", "environment": "实验室冷白光", "style": "精密工程", "audio": "伺服电机低鸣"}, "白名单键收窄（junk 丢弃）"


# ── _apply_ir_fields 降级语义 ───────────────────────────────────────────


def test_apply_ir_fields_degrades_safely():
    frames = [StoryboardFrame(index=i, narration="n") for i in range(3)]
    parsed = [
        {"ir": {"subject": "s1"}},  # 合法
        {"ir": "not-a-dict"},  # 坏类型 -> None
        {},  # 未返 -> None
    ]
    planner._apply_ir_fields(frames, parsed)
    assert frames[0].visual_ir == {"subject": "s1"}
    assert frames[1].visual_ir is None
    assert frames[2].visual_ir is None


def test_apply_ir_fields_empty_strings_skipped():
    frames = [StoryboardFrame(index=0, narration="n")]
    planner._apply_ir_fields(frames, [{"ir": {"subject": "  ", "action": ""}}])
    assert frames[0].visual_ir is None, "全空字段 -> None（走 legacy）"


# ── robust 搬回 ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_robust_carries_ir_back(monkeypatch):
    async def fake_enhanced(llm, mini, style_prefix):
        mini.frames[0].visual_ir = {"subject": "s"}
        return [f"bulk-p{f.index}" for f in mini.frames], ["" for _ in mini.frames]

    async def fake_single(llm, narrations, **kwargs):
        return [f"single:{narrations[0]}"]

    monkeypatch.setattr(planner, "_generate_course_prompts_enhanced", fake_enhanced)
    monkeypatch.setattr(planner, "_generate_image_prompts", fake_single)
    sb = _sb("ai_image", 4)
    await planner._fill_image_prompts_robust(None, sb, "", chunk_size=8)  # E8: IR 搬回机制测试走 bulk 路径
    assert sb.frames[0].visual_ir == {"subject": "s"}, "mini -> 真实帧 IR 搬回"


# ── fill 方言路由（env / provider_name / IR 三因子）────────────────────


class _FakeVideoProvider:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def generate_video(self, prompt: str, **kwargs):
        self.calls.append({"prompt": prompt, **kwargs})
        from app.gateway.video_gen.providers.base import VideoRef

        return VideoRef(url="https://v/1.mp4", duration=kwargs.get("duration", 5.0), source="test")


def _video_sb(ir: dict | None) -> Storyboard:
    f = StoryboardFrame(
        index=0,
        narration="n",
        image_prompt="旧式描述",
        duration=4.0,
        shot_size="medium",
        camera_move="push_in",
        visual_ir=ir,
    )
    return Storyboard(title="t", config=StoryboardConfig(video_type="ai_video", width=1920, height=1080), frames=[f])


IR = {"subject": "金属机械臂", "action": "拾起发光小球", "environment": "灰白实验室，冷白侧光", "style": "精密工程", "audio": "电机低鸣"}


@pytest.mark.asyncio
async def test_fill_routes_h3_dialect(monkeypatch):
    monkeypatch.setenv("VIDEO_GEN_PROMPT_DIALECT", "v2")
    from app.gateway.video_gen.visual_sources.ai_video import AIVideoVisualSource

    provider = _FakeVideoProvider()
    src = AIVideoVisualSource(provider, provider_name="minimax-h3")
    await src.render(_video_sb(IR))
    sent = provider.calls[0]["prompt"]
    assert sent.startswith("integrated_multimodal_description: ")
    assert "拾起发光小球" in sent


@pytest.mark.asyncio
async def test_fill_routes_seedance_dialect(monkeypatch):
    monkeypatch.setenv("VIDEO_GEN_PROMPT_DIALECT", "v2")
    from app.gateway.video_gen.visual_sources.ai_video import AIVideoVisualSource

    provider = _FakeVideoProvider()
    src = AIVideoVisualSource(provider, provider_name="seedance")
    await src.render(_video_sb(IR))
    sent = provider.calls[0]["prompt"]
    assert "缓慢推镜" in sent and sent.endswith("保持无字幕，不要生成任何水印或Logo标识，画面中不出现文字。")


@pytest.mark.asyncio
async def test_fill_env_legacy_wins(monkeypatch):
    monkeypatch.setenv("VIDEO_GEN_PROMPT_DIALECT", "legacy")
    from app.gateway.video_gen.visual_sources.ai_video import AIVideoVisualSource

    provider = _FakeVideoProvider()
    src = AIVideoVisualSource(provider, provider_name="minimax-h3")
    await src.render(_video_sb(IR))
    sent = provider.calls[0]["prompt"]
    assert sent.startswith("旧式描述"), "env 默认 legacy -> 现行 compose_video_prompt 语义"


@pytest.mark.asyncio
async def test_fill_no_ir_legacy_even_with_v2_default(monkeypatch):
    """默认已是 v2（2026-08-18 定稿），但无 IR 的帧（旧 storyboard/手改/拆帧）仍走 legacy。"""
    monkeypatch.delenv("VIDEO_GEN_PROMPT_DIALECT", raising=False)
    from app.gateway.video_gen.visual_sources.ai_video import AIVideoVisualSource

    provider = _FakeVideoProvider()
    src = AIVideoVisualSource(provider, provider_name="minimax-h3")
    await src.render(_video_sb(None))
    assert provider.calls[0]["prompt"].startswith("旧式描述")


# ── 序列化往返 ─────────────────────────────────────────────────────────


def test_visual_ir_roundtrip():
    sb = _video_sb(IR)
    d = sb.to_dict()
    sb2 = Storyboard.from_dict(d)
    assert sb2.frames[0].visual_ir == IR
    # 旧数据（无 visual_ir 键）-> None
    old = {"title": "t", "config": {"video_type": "ai_video", "width": 1, "height": 1}, "frames": [{"index": 0, "narration": "n"}]}
    sb3 = Storyboard.from_dict(old)
    assert sb3.frames[0].visual_ir is None


# ── 手改 image_prompt -> 清 IR（router PUT 端点，真 _update_storyboard 语义）──


def test_frame_put_image_prompt_edit_clears_ir(tmp_path):
    import json

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.gateway._shared.task_framework.schemas import DRAFT
    from app.gateway.routers.video_gen import router
    from deerflow.runtime.user_context import get_effective_user_id

    storage = (tmp_path / "storage").resolve()
    task_dir = storage / get_effective_user_id() / "tasks" / "vg_p1"
    task_dir.mkdir(parents=True)
    (task_dir / "storyboard.json").write_text(
        json.dumps(
            {
                "title": "t",
                "config": {},
                "frames": [
                    {"index": 0, "narration": "n", "image_prompt": "旧", "visual_ir": {"subject": "s"}},
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    class _Svc:
        def __init__(self) -> None:
            self._storage_dir = str(storage)

        async def get_task(self, task_id, user_id):
            t = type("T", (), {"id": task_id, "status": DRAFT, "result": "{}", "payload": "{}"})()
            return t

        async def _update_storyboard(self, td, updater):
            data = json.loads((td / "storyboard.json").read_text(encoding="utf-8"))
            updater(data)
            (td / "storyboard.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

        async def touch_task(self, *a, **k):
            return None

    app = FastAPI()
    app.include_router(router)
    app.state.video_gen_service = _Svc()
    resp = TestClient(app).put(
        "/api/video/tasks/vg_p1/storyboard/frames/0",
        json={"image_prompt": "用户手改的新描述"},
    )
    assert resp.status_code == 200, resp.text
    frames = json.loads((task_dir / "storyboard.json").read_text())["frames"]
    assert frames[0]["image_prompt"] == "用户手改的新描述"
    assert frames[0]["visual_ir"] is None, "手改 image_prompt 必须清 IR（P0-5：翻译器不得无视手改）"
