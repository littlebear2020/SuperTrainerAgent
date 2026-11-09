"""Task 9: 相邻镜运镜连贯后处理。"""

from app.gateway.video_gen.orchestration.storyboard_planner import smooth_camera_continuity
from app.gateway.video_gen.storyboard import StoryboardFrame


def _f(i: int, shot: str | None = None, move: str | None = None) -> StoryboardFrame:
    return StoryboardFrame(index=i, narration=f"n{i}", image_prompt=f"p{i}", shot_size=shot, camera_move=move)


def test_breaks_repeated_camera_move() -> None:
    """连续三镜同一运镜 → 中间那镜换掉，避免呆板。"""
    frames = [_f(0, "medium", "push_in"), _f(1, "medium", "push_in"), _f(2, "medium", "push_in")]
    smooth_camera_continuity(frames)
    assert frames[0].camera_move == "push_in"
    assert frames[1].camera_move != "push_in", "连续重复运镜应被打断"


def test_avoids_opposing_moves_back_to_back() -> None:
    """推近紧接拉远 = 观感跳脱 → 后者改静止。"""
    frames = [_f(0, "medium", "push_in"), _f(1, "medium", "pull_out")]
    smooth_camera_continuity(frames)
    assert frames[1].camera_move == "static"


def test_opening_frame_prefers_wide() -> None:
    frames = [_f(0, "close", "static"), _f(1, "medium", "push_in")]
    smooth_camera_continuity(frames)
    assert frames[0].shot_size == "wide", "开场镜应建立环境"


def test_none_fields_untouched() -> None:
    """旧 storyboard（无镜头语言）不得被凭空填值。"""
    frames = [_f(0), _f(1)]
    smooth_camera_continuity(frames)
    assert frames[0].camera_move is None
    assert frames[1].shot_size is None


def test_single_frame_no_crash() -> None:
    frames = [_f(0, "medium", "push_in")]
    smooth_camera_continuity(frames)
    assert frames[0].camera_move == "push_in"


def test_empty_list_no_crash() -> None:
    smooth_camera_continuity([])


def test_no_repeats_left_after_smoothing() -> None:
    """收敛后不得残留任何相邻重复——替换值本身不能又撞上前一镜。"""
    frames = [_f(i, "medium", "pan") for i in range(6)]
    smooth_camera_continuity(frames)
    moves = [f.camera_move for f in frames]
    assert all(moves[i] != moves[i - 1] for i in range(1, len(moves))), f"仍有相邻重复：{moves}"


def test_no_opposing_pairs_left_after_smoothing() -> None:
    frames = [_f(0, "medium", "push_in"), _f(1, "medium", "pull_out"), _f(2, "medium", "push_in"), _f(3, "medium", "pull_out")]
    smooth_camera_continuity(frames)
    moves = [f.camera_move for f in frames]
    opposing = {("push_in", "pull_out"), ("pull_out", "push_in"), ("pan", "tilt"), ("tilt", "pan")}
    assert all((moves[i - 1], moves[i]) not in opposing for i in range(1, len(moves))), f"仍有相反运镜紧邻：{moves}"


def test_all_static_preserved() -> None:
    """BUG-C（2026-08-03）：全 static 序列不得被强行注入运动。

    稳定机位连续多镜完全合法（访谈/图表讲解/标题卡）。修前 [static]×5 会被改成
    [static, push_in, static, push_in, static]——给静止画面强加运镜，
    既违反「不凭空造值」契约，也把 LLM 的正确选择改坏。
    """
    frames = [_f(i, "medium", "static") for i in range(5)]
    smooth_camera_continuity(frames)
    assert all(fr.camera_move == "static" for fr in frames), f"全 static 应原样保留，实际 {[fr.camera_move for fr in frames]}"


def test_non_static_repeats_still_broken() -> None:
    """反向锚：static 豁免不得殃及真正需要打断的重复运镜。"""
    frames = [_f(i, "medium", "push_in") for i in range(3)]
    smooth_camera_continuity(frames)
    moves = [fr.camera_move for fr in frames]
    assert moves[1] != "push_in", f"连续 push_in 仍应被打断，实际 {moves}"


def test_wired_into_planner() -> None:
    """后处理必须真的接进规划流程，否则只是个没人调的函数。

    E8（2026-08-21）：收敛调用抽到 _writeback_and_smooth（逐帧/批量两路径共用），
    接线断言改为：共用收尾真调 smooth_camera_continuity + 两条路径都经过它。"""
    import inspect

    from app.gateway.video_gen.orchestration import storyboard_planner as sp

    assert "smooth_camera_continuity" in inspect.getsource(sp._writeback_and_smooth)
    assert "_writeback_and_smooth" in inspect.getsource(sp._fill_image_prompts_bulk)
    assert "_writeback_and_smooth" in inspect.getsource(sp._fill_image_prompts_per_frame)
    assert "smooth_camera_continuity" in inspect.getsource(sp._plan_course_batched)
