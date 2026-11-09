"""Task 8: 镜头语言字段 + 提示词拼装。"""

from app.gateway.video_gen.orchestration.storyboard_planner import compose_video_prompt
from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame


def test_camera_fields_default_none() -> None:
    f = StoryboardFrame(index=0, narration="n")
    assert f.shot_size is None
    assert f.camera_move is None


def test_camera_fields_roundtrip() -> None:
    cfg = StoryboardConfig(video_type="ai_video", width=720, height=1280)
    f = StoryboardFrame(index=0, narration="n", shot_size="medium", camera_move="push_in")
    back = Storyboard.from_dict(Storyboard(title="t", config=cfg, frames=[f]).to_dict())
    assert back.frames[0].shot_size == "medium"
    assert back.frames[0].camera_move == "push_in"


def test_compose_prompt_appends_camera_terms() -> None:
    f = StoryboardFrame(index=0, narration="n", image_prompt="一位工程师在白板前讲解", shot_size="medium", camera_move="push_in")
    out = compose_video_prompt(f)
    assert "一位工程师在白板前讲解" in out
    assert "中景" in out
    assert "缓慢推近" in out


def test_compose_prompt_without_camera_is_unchanged() -> None:
    f = StoryboardFrame(index=0, narration="n", image_prompt="一位工程师在白板前讲解")
    assert compose_video_prompt(f) == "一位工程师在白板前讲解"


def test_compose_prompt_ignores_unknown_terms() -> None:
    """LLM 吐出不在词表里的值 → 忽略，不把生词塞进提示词。"""
    f = StoryboardFrame(index=0, narration="n", image_prompt="p", shot_size="bogus", camera_move="nope")
    assert compose_video_prompt(f) == "p"


def test_compose_prompt_strips_trailing_comma() -> None:
    """BUG-D（2026-08-03）：用户在分镜编辑器手改 image_prompt 留尾逗号 → 双逗号。"""
    f = StoryboardFrame(index=0, narration="n", image_prompt="场景描述，", shot_size="medium", camera_move="push_in")
    out = compose_video_prompt(f)
    assert "，，" not in out, f"不得出现双逗号：{out}"
    assert out == "场景描述，中景，缓慢推近"


def test_compose_prompt_handles_halfwidth_comma() -> None:
    f = StoryboardFrame(index=0, narration="n", image_prompt="scene,", shot_size="wide")
    assert compose_video_prompt(f) == "scene，远景"


def test_compose_prompt_empty_base_has_no_leading_comma() -> None:
    """空 image_prompt + 有镜头语言 → 不得产出前导逗号。"""
    f = StoryboardFrame(index=0, narration="n", image_prompt="", shot_size="medium", camera_move="push_in")
    out = compose_video_prompt(f)
    assert not out.startswith("，"), f"不得有前导逗号：{out}"
    assert out == "中景，缓慢推近"


def test_compose_prompt_without_camera_preserves_user_text_verbatim() -> None:
    """无镜头语言时原样返回——不做与本功能无关的清洗（用户的尾逗号是他自己的事）。"""
    f = StoryboardFrame(index=0, narration="n", image_prompt="场景描述，")
    assert compose_video_prompt(f) == "场景描述，"


def test_llm_camera_fields_written_back_to_frames() -> None:
    """LLM 产出的 shot_size/camera_move 必须落到真实 storyboard 帧上。

    只扩 JSON 契约不写回 = 又一个静默失效的旋钮：提示词里要了字段，
    模型给了，代码却扔掉。
    """
    import asyncio
    import json as _json
    from types import SimpleNamespace

    from app.gateway.video_gen.orchestration import storyboard_planner as sp

    payload = {
        "frames": [
            {"image_prompt": "p0", "shot_size": "wide", "camera_move": "static", "visual_source": "ai_video"},
            {"image_prompt": "p1", "shot_size": "close", "camera_move": "push_in", "visual_source": "ai_video"},
        ]
    }

    class _LLM:
        async def ainvoke(self, messages):  # noqa: ANN001
            return SimpleNamespace(content=_json.dumps(payload, ensure_ascii=False))

    cfg = StoryboardConfig(video_type="ai_video", width=720, height=1280)
    sb = Storyboard(
        title="t",
        config=cfg,
        frames=[StoryboardFrame(index=0, narration="n0"), StoryboardFrame(index=1, narration="n1")],
    )
    asyncio.run(sp._fill_image_prompts_robust(_LLM(), sb, "", chunk_size=8))  # E8: 镜头语言搬回机制测试走批量路径
    assert sb.frames[0].shot_size == "wide", f"首镜景别应写回，实际 {sb.frames[0].shot_size}"
    assert sb.frames[1].camera_move == "push_in", f"运镜应写回，实际 {sb.frames[1].camera_move}"


def test_count_mismatch_logs_warning_and_leaves_extra_frames_none(caplog) -> None:
    """LLM 返回条数 < 帧数 → 未匹配帧保持 None，且必须留 warning（2026-08-03 评审修）。

    修前 zip(strict=False) 静默截断，「尾帧没有镜头语言」无从排查。
    """
    from app.gateway.video_gen.orchestration import storyboard_planner as sp

    frames = [StoryboardFrame(index=i, narration=f"n{i}", image_prompt=f"p{i}") for i in range(8)]
    parsed = [{"shot_size": "medium", "camera_move": "push_in"} for _ in range(7)]

    with caplog.at_level("WARNING"):
        sp._apply_camera_fields(frames, parsed)

    assert frames[6].camera_move == "push_in", "前 7 帧应正常写回"
    assert frames[7].camera_move is None, "第 8 帧无对应条目，应保持 None"
    assert frames[7].shot_size is None
    msgs = " ".join(r.getMessage() for r in caplog.records)
    assert "count mismatch" in msgs and "7" in msgs and "8" in msgs, f"应记条数不符 warning，实际：{msgs}"


def test_count_match_logs_nothing(caplog) -> None:
    """条数相符时不得刷日志（噪音）。"""
    from app.gateway.video_gen.orchestration import storyboard_planner as sp

    frames = [StoryboardFrame(index=i, narration=f"n{i}") for i in range(3)]
    parsed = [{"shot_size": "wide"} for _ in range(3)]
    with caplog.at_level("WARNING"):
        sp._apply_camera_fields(frames, parsed)
    assert not any("count mismatch" in r.getMessage() for r in caplog.records)


def test_llm_prompt_asks_for_camera_fields() -> None:
    """JSON 契约必须真的要求这两个字段，否则 LLM 不会产出。"""
    import inspect

    from app.gateway.video_gen.orchestration import storyboard_planner as sp

    src = inspect.getsource(sp._generate_course_prompts_enhanced)
    assert "shot_size" in src and "camera_move" in src
