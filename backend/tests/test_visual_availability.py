"""行为锁：渲染前预检视觉素材，不可用时报错而非产出黑屏片。

线上实测（2026-07-29）：两天前的任务改台词后重渲，provider 临时 URL
（TOS 签名 X-Tos-Expires=86400，即 24h）已过期且无本地副本，
worker 把每帧都降级成黑场占位（ai_visual.py:500-504），
产出一条「没有画面只有字幕」的视频，状态仍是 ready（worker.py:265）。

recompose_task 此前已预检 audio.mp3 缺失、预检旁白未重生成，
却完全不检查视觉素材——本模块补上这一层。

关键：纯磁盘判断不够。过期 URL 的帧磁盘上「有 URL」，
必须实际探测才知死活——这正是初版方案漏掉用户 case 的原因。
"""

import asyncio

import pytest

from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame
from app.gateway.video_gen.visual_availability import (
    audit_visual_availability,
    classify_frame_visual,
    format_unavailable_error,
    frames_in_regen_error,
    probe_remote_liveness,
)


def _sb(frames: list[StoryboardFrame]) -> Storyboard:
    return Storyboard(
        title="t",
        config=StoryboardConfig(video_type="ai_image", width=1152, height=2048),
        frames=frames,
    )


def test_local_file_present_is_local(tmp_path):
    (tmp_path / "visuals").mkdir()
    (tmp_path / "visuals" / "img_0000.png").write_bytes(b"x")
    f = StoryboardFrame(index=0, narration="a", local_visual_path="visuals/img_0000.png")
    assert classify_frame_visual(f, tmp_path) == "local"


def test_local_path_recorded_but_file_gone_is_missing(tmp_path):
    """记录了本地路径但文件已不在——「分镜图片直接没有了」的情形。"""
    f = StoryboardFrame(index=0, narration="a", local_visual_path="visuals/gone.png")
    assert classify_frame_visual(f, tmp_path) == "missing"


def test_local_wins_over_remote(tmp_path):
    """本地文件在时优先本地（worker 也是 local 优先）。"""
    (tmp_path / "visuals").mkdir()
    (tmp_path / "visuals" / "a.png").write_bytes(b"x")
    f = StoryboardFrame(index=0, narration="a", local_visual_path="visuals/a.png", visual_url="https://cdn/x.png")
    assert classify_frame_visual(f, tmp_path) == "local"


def test_remote_only_is_remote(tmp_path):
    f = StoryboardFrame(index=0, narration="a", visual_url="https://cdn/x.png")
    assert classify_frame_visual(f, tmp_path) == "remote"


def test_no_visual_at_all_is_missing(tmp_path):
    assert classify_frame_visual(StoryboardFrame(index=0, narration="a"), tmp_path) == "missing"


def test_path_traversal_is_missing(tmp_path):
    """越界路径不得被当成可用素材。"""
    f = StoryboardFrame(index=0, narration="a", local_visual_path="../../etc/passwd")
    assert classify_frame_visual(f, tmp_path) == "missing"


def test_audit_counts_all_categories(tmp_path):
    (tmp_path / "visuals").mkdir()
    (tmp_path / "visuals" / "ok.png").write_bytes(b"x")
    audit = audit_visual_availability(
        _sb(
            [
                StoryboardFrame(index=0, narration="a", local_visual_path="visuals/ok.png"),
                StoryboardFrame(index=1, narration="b", visual_url="https://cdn/b.png"),
                StoryboardFrame(index=2, narration="c"),
            ]
        ),
        tmp_path,
    )
    assert (audit.total, audit.local, audit.remote, audit.missing) == (3, 1, 1, [2])
    assert audit.all_missing is False


def test_audit_all_missing_flags_true(tmp_path):
    audit = audit_visual_availability(
        _sb(
            [
                StoryboardFrame(index=0, narration="a"),
                StoryboardFrame(index=1, narration="b", local_visual_path="visuals/gone.png"),
            ]
        ),
        tmp_path,
    )
    assert audit.all_missing is True
    assert audit.missing == [0, 1]
    assert audit.missing_ratio == 1.0


def test_audit_empty_storyboard_is_safe(tmp_path):
    audit = audit_visual_availability(_sb([]), tmp_path)
    assert (audit.total, audit.all_missing, audit.missing_ratio) == (0, False, 0.0)


@pytest.mark.asyncio
async def test_probe_flags_expired_url_as_dead(tmp_path, monkeypatch):
    """403「Request has expired」的帧必须被判死——这是用户 case 的核心。"""
    import app.gateway.video_gen.visual_availability as va

    async def _fake_head(url: str, timeout: float) -> bool:
        return "good" in url

    monkeypatch.setattr(va, "_head_ok", _fake_head)

    dead = await probe_remote_liveness(
        _sb(
            [
                StoryboardFrame(index=0, narration="a", visual_url="https://cdn/good.png"),
                StoryboardFrame(index=1, narration="b", visual_url="https://cdn/expired.png"),
            ]
        ),
        tmp_path,
    )
    assert dead == [1]


@pytest.mark.asyncio
async def test_probe_skips_local_frames(tmp_path, monkeypatch):
    """有本地副本的帧不探测——省网络且本地优先。"""
    import app.gateway.video_gen.visual_availability as va

    calls: list[str] = []

    async def _fake_head(url: str, timeout: float) -> bool:
        calls.append(url)
        return False

    monkeypatch.setattr(va, "_head_ok", _fake_head)
    (tmp_path / "visuals").mkdir()
    (tmp_path / "visuals" / "a.png").write_bytes(b"x")

    dead = await probe_remote_liveness(
        _sb([StoryboardFrame(index=0, narration="a", local_visual_path="visuals/a.png", visual_url="https://cdn/x.png")]),
        tmp_path,
    )
    assert dead == []
    assert calls == []


@pytest.mark.asyncio
async def test_probe_empty_is_noop(tmp_path):
    assert await probe_remote_liveness(_sb([]), tmp_path) == []


@pytest.mark.asyncio
async def test_probe_gives_up_at_deadline_and_fails_open(tmp_path, monkeypatch):
    """整体 deadline：超时即放行（返回空），不得把渲染入口拖到 nginx 504。

    并发 6 + 单请求 8s，96 帧全黑洞 = 16 批 x 8s = 128s > nginx proxy_read_timeout 120s。
    那样用户等两分钟只换来一个无解释的 504，比不做预检更糟。
    探不出来不等于素材坏了，故 fail-open：让渲染继续，worker 侧仍有黑场占位
    + placeholder_frames 上报兜底。
    """
    import app.gateway.video_gen.visual_availability as va

    async def _hang(url: str, timeout: float) -> bool:
        await asyncio.sleep(30)  # 模拟 CDN 黑洞
        return False

    monkeypatch.setattr(va, "_head_ok", _hang)

    frames = [StoryboardFrame(index=i, narration="a", visual_url=f"https://cdn/{i}.png") for i in range(20)]

    started = asyncio.get_running_loop().time()
    dead = await probe_remote_liveness(_sb(frames), tmp_path, deadline=0.3)
    elapsed = asyncio.get_running_loop().time() - started

    assert dead == [], "超时应放行（fail-open），不得把全部帧误判为死链"
    assert elapsed < 5, f"必须在 deadline 处放弃，实际耗时 {elapsed:.1f}s"


@pytest.mark.asyncio
async def test_probe_deadline_does_not_cut_fast_probes(tmp_path, monkeypatch):
    """正常速度的探测不受 deadline 影响——死链仍要被如实报出。"""
    import app.gateway.video_gen.visual_availability as va

    async def _fast(url: str, timeout: float) -> bool:
        return "good" in url

    monkeypatch.setattr(va, "_head_ok", _fast)

    dead = await probe_remote_liveness(
        _sb(
            [
                StoryboardFrame(index=0, narration="a", visual_url="https://cdn/good.png"),
                StoryboardFrame(index=1, narration="b", visual_url="https://cdn/dead.png"),
            ]
        ),
        tmp_path,
        deadline=5.0,
    )
    assert dead == [1]


def test_error_message_is_1_based_and_truncated():
    """错误文案对用户用 1-based 帧号，超长截断。"""
    msg = format_unavailable_error(list(range(20)), total=30)
    # 帧号列表沿用仓库既有文案格式（service.py:1116 的 f"帧 {[i + 1 ...]}"）：
    # index 0 显示为 1 即证明 1-based；超 10 个截断并以「…」标示。
    assert "帧 1、2、" in msg, "帧号应用中文顿号连接，不得暴露 Python list 的方括号"
    assert "[" not in msg and "]" not in msg, "用户文案里不该出现 list repr"
    assert "等" in msg, "超 10 个需标示还有更多"
    assert "11" not in msg, "超过 10 个应截断，不得列出第 11 个帧号"
    assert "20/30" in msg
    assert "重新生成" in msg


@pytest.mark.asyncio
async def test_dead_and_missing_are_merged(tmp_path, monkeypatch):
    """复现 service.py 预检的合并判据：missing ∪ dead。"""
    import app.gateway.video_gen.visual_availability as va

    async def _fake_head(url: str, timeout: float) -> bool:
        return False

    monkeypatch.setattr(va, "_head_ok", _fake_head)

    sb = _sb(
        [
            StoryboardFrame(index=0, narration="a"),  # missing
            StoryboardFrame(index=1, narration="b", visual_url="https://cdn/x.png"),  # dead
            StoryboardFrame(index=2, narration="c", local_visual_path="visuals/ok.png"),
        ]
    )
    (tmp_path / "visuals").mkdir()
    (tmp_path / "visuals" / "ok.png").write_bytes(b"x")

    audit = audit_visual_availability(sb, tmp_path)
    dead = await probe_remote_liveness(sb, tmp_path)
    bad = sorted(set(audit.missing) | set(dead))

    assert bad == [0, 1]
    msg = format_unavailable_error(bad, audit.total)
    assert "帧 1、2 " in msg


@pytest.mark.asyncio
async def test_all_local_passes_precheck(tmp_path, monkeypatch):
    """全本地的任务不触发预检报错，也不发任何网络请求。"""
    import app.gateway.video_gen.visual_availability as va

    async def _boom(url: str, timeout: float) -> bool:
        raise AssertionError("不应发起网络探测")

    monkeypatch.setattr(va, "_head_ok", _boom)
    (tmp_path / "visuals").mkdir()
    (tmp_path / "visuals" / "a.png").write_bytes(b"x")

    sb = _sb([StoryboardFrame(index=0, narration="a", local_visual_path="visuals/a.png")])
    audit = audit_visual_availability(sb, tmp_path)
    dead = await probe_remote_liveness(sb, tmp_path)

    assert not (set(audit.missing) | set(dead))


@pytest.mark.asyncio
async def test_re_render_precheck_excludes_frames_that_will_regenerate(tmp_path, monkeypatch):
    """re_render 的判据必须减去 _frames_to_regenerate——否则堵死恢复路径。

    re_render 会重生「无 visual_url」的帧，把这些缺失帧也算进不可用会让
    「项目已更新 → 重新生成」对失败任务永久报错，正是该按钮要解决的场景。
    """
    import app.gateway.video_gen.visual_availability as va
    from app.gateway.video_gen.service import _frames_to_regenerate

    async def _fake_head(url: str, timeout: float) -> bool:
        return False

    monkeypatch.setattr(va, "_head_ok", _fake_head)

    sb = _sb(
        [
            # 无任何视觉 → 会被重生，不该拦
            StoryboardFrame(index=0, narration="a"),
            # 有 URL 且类型正确 → 会被跳过；URL 已死 → 必须拦
            StoryboardFrame(index=1, narration="b", visual_url="https://cdn/x.png", visual_type="image"),
            # 记了 local 但文件已丢 → P0-1 守卫会 continue 跳过 → 必须拦
            StoryboardFrame(index=2, narration="c", local_visual_path="visuals/gone.png"),
        ]
    )

    will_regen = set(_frames_to_regenerate(sb.frames, "ai_image"))
    audit = audit_visual_availability(sb, tmp_path)
    dead = await probe_remote_liveness(sb, tmp_path)
    bad = sorted((set(audit.missing) | set(dead)) - will_regen)

    assert 0 in will_regen, "无视觉的帧本应被重生"
    assert bad == [1, 2]


# ── frames_in_regen_error ─────────────────────────────────────────────────


def test_frames_in_regen_error_from_dict():
    """dict storyboard -- regen_state 是帧级 dict 的额外键（非 dataclass 字段）。"""
    sb = {
        "frames": [
            {"index": 0, "narration": "a"},
            {"index": 1, "narration": "b", "regen_state": "error", "regen_error": "timeout"},
            {"index": 2, "narration": "c", "regen_state": "done"},
        ]
    }
    assert frames_in_regen_error(sb) == [1]


def test_frames_in_regen_error_from_storyboard_objects(tmp_path):
    """StoryboardFrame 对象不携带 regen_state（非 dataclass 字段）--返空。
    这是为什么调用者必须对原始 dict 调用、不能对 _frame_objs 调用。"""
    sb = _sb(
        [
            StoryboardFrame(index=0, narration="a"),
            StoryboardFrame(index=1, narration="b"),
        ]
    )
    assert frames_in_regen_error(sb) == []


def test_frames_in_regen_error_position_fallback():
    """帧缺 index 字段时用位置兜底。"""
    sb = {
        "frames": [
            {"narration": "a"},
            {"narration": "b", "regen_state": "error"},
        ]
    }
    assert frames_in_regen_error(sb) == [1]


def test_frames_in_regen_error_empty():
    assert frames_in_regen_error({"frames": []}) == []
    assert frames_in_regen_error({"frames": [{"index": 0, "regen_state": "done"}]}) == []


def test_frames_in_regen_error_multiple():
    sb = {
        "frames": [
            {"index": 0, "regen_state": "error"},
            {"index": 1, "regen_state": "running"},
            {"index": 2, "regen_state": "error"},
            {"index": 3},
        ]
    }
    assert frames_in_regen_error(sb) == [0, 2]


# ── format_unavailable_error with regen_failed ────────────────────────────


def test_format_error_regen_failed_all():
    """全部帧都是重生失败 -> 主因写「上次重新生成失败」。"""
    msg = format_unavailable_error([0, 2], total=3, regen_failed=[0, 2])
    assert "帧 1、3 " in msg
    assert "上次重新生成失败" in msg
    assert "沿用失败前的旧画面" in msg
    assert "重新生成" in msg
    assert "远程链接已过期" not in msg, "全是 regen 失败帧不应提死链"


def test_format_error_regen_failed_mixed():
    """混合场景：部分死链 + 部分重生失败。"""
    msg = format_unavailable_error([0, 1, 2], total=5, regen_failed=[1])
    assert "帧 1、2、3 " in msg
    assert "远程链接已过期" in msg
    assert "其中帧 2 是上次重新生成失败" in msg
    assert "重新生成" in msg


def test_format_error_no_regen_failed_unchanged():
    """regen_failed=None -> 文案与改前等价（仅「已失效」->「不可用」用词调整）。"""
    msg = format_unavailable_error([0], total=2)
    assert "帧 1 " in msg
    assert "不可用" in msg
    assert "远程链接已过期" in msg
    assert "重新生成" in msg
