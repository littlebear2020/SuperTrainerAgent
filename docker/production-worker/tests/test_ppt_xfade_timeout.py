"""§2.4 单测：PPT 转视频 xfade 超时修复（止血层）。

三组测试覆盖：
  1. 动态 timeout 边界（保底600 / 随规模递增 / 不超 _LEASE_DURATION−300）
  2. _run_ffmpeg 异常链（TimeoutExpired → RuntimeError(中文) + __cause__ 保留）
  3. cmd 含 ultrafast（xfade / 字幕烧录 / seg 生成 三处）

⚠️ _ffmpeg_slide_to_video / _ffmpeg_burn_srt 内部先调 ffprobe（取 duration）再调 ffmpeg，
fake subprocess.run 需按 cmd[0] 分派，否则测试假绿。
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import pipeline  # noqa: E402


# ---------------------------------------------------------------------------
# 1. 动态 timeout 边界
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "durations, transition, expected",
    [
        # 保底 600：3段40s → raw=max(600, 360+60)=600
        ([40.0, 40.0, 40.0], 0.5, 600),
        # 随规模递增：9段45s → raw=max(600, 1080+202)=1282
        ([45.0] * 9, 0.5, 1282),
        # 钳到租约上限：15段120s → raw=max(600, 1800+900)=2700 → capped=1500
        ([120.0] * 15, 0.5, 1500),
        # 段多但短：20段10s → raw=max(600, 2400+100)=2500 → capped=1500
        ([10.0] * 20, 0.5, 1500),
    ],
    ids=["baseline-600", "scales-up", "capped-by-lease", "many-segments-capped"],
)
def test_xfade_timeout_dynamic_bounds(monkeypatch, durations, transition, expected):
    """xfade timeout: 保底600、随 n/total_dur 单调递增、不超 _LEASE_DURATION−300。"""
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["timeout"] = kwargs.get("timeout")
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(pipeline.subprocess, "run", fake_run)

    segs = [Path(f"/tmp/seg-{i}.mp4") for i in range(len(durations))]
    pipeline._ffmpeg_xfade_with_durations(
        segs, Path("/tmp/out.mp4"), "fade", transition, durations
    )

    assert captured["timeout"] == expected, (
        f"durations={durations} → expected timeout={expected}, got {captured['timeout']}"
    )


def test_xfade_timeout_never_exceeds_lease_minus_300(monkeypatch):
    """无论段数/时长多大，xfade_timeout ≤ _LEASE_DURATION − 300。"""
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["timeout"] = kwargs.get("timeout")
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(pipeline.subprocess, "run", fake_run)

    # 极端：50段、每段300s
    segs = [Path(f"/tmp/seg-{i}.mp4") for i in range(50)]
    pipeline._ffmpeg_xfade_with_durations(
        segs, Path("/tmp/out.mp4"), "fade", 0.5, [300.0] * 50
    )
    assert captured["timeout"] <= pipeline._LEASE_DURATION - 300


# ---------------------------------------------------------------------------
# 2. _run_ffmpeg 异常链
# ---------------------------------------------------------------------------


def test_run_ffmpeg_timeout_chains_exception(monkeypatch):
    """TimeoutExpired → RuntimeError(中文)，__cause__ 是原始 TimeoutExpired 实例。

    验证 from exc 生效（非 from None 抑制异常链）。
    """
    original_timeout = 5

    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout", original_timeout))

    monkeypatch.setattr(pipeline.subprocess, "run", fake_run)

    with pytest.raises(RuntimeError) as exc_info:
        pipeline._run_ffmpeg(
            ["ffmpeg", "-y"], timeout=original_timeout, friendly_timeout_msg="合成超时"
        )

    # 友好消息进 DB/前端
    assert "合成超时" in str(exc_info.value)
    # 异常链保留（from exc 生效）
    assert isinstance(exc_info.value.__cause__, subprocess.TimeoutExpired), (
        "__cause__ 必须是 TimeoutExpired（from exc 生效）；若为 None 则是 from None 抑制了异常链"
    )


def test_run_ffmpeg_normal_return(monkeypatch):
    """正常完成 → 返回 CompletedProcess，不抛异常。"""
    expected = subprocess.CompletedProcess(["ffmpeg"], 0, b"", b"")

    def fake_run(cmd, **kwargs):
        return expected

    monkeypatch.setattr(pipeline.subprocess, "run", fake_run)

    result = pipeline._run_ffmpeg(["ffmpeg"], timeout=60, friendly_timeout_msg="unreachable")
    assert result is expected


# ---------------------------------------------------------------------------
# 3. cmd 含 ultrafast
# ---------------------------------------------------------------------------


def _make_dispatch_fake(captured_cmds: list[list[str]]):
    """按 cmd[0] 分派：ffprobe 返回 duration 文本，ffmpeg 记录 cmd 并返回成功。"""

    def fake_run(cmd, **kwargs):
        if cmd[0] == "ffprobe":
            return subprocess.CompletedProcess(cmd, 0, "30.0\n", "")
        captured_cmds.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    return fake_run


def test_cmd_ultrafast_in_slide_to_video(monkeypatch, tmp_path):
    """_ffmpeg_slide_to_video: ffmpeg cmd 必须含 -preset ultrafast。"""
    captured: list[list[str]] = []
    monkeypatch.setattr(pipeline.subprocess, "run", _make_dispatch_fake(captured))

    img = tmp_path / "img.jpg"
    img.write_bytes(b"jpg")
    aud = tmp_path / "aud.mp3"
    aud.write_bytes(b"mp3")
    pipeline._ffmpeg_slide_to_video(img, aud, tmp_path / "seg.mp4")

    ffmpeg_cmds = [c for c in captured if c[0] == "ffmpeg"]
    assert len(ffmpeg_cmds) == 1
    _assert_ultrafast(ffmpeg_cmds[0])


def test_cmd_ultrafast_in_xfade(monkeypatch, tmp_path):
    """_ffmpeg_xfade_with_durations: ffmpeg cmd 必须含 -preset ultrafast。"""
    captured: list[list[str]] = []
    monkeypatch.setattr(pipeline.subprocess, "run", _make_dispatch_fake(captured))

    seg1 = tmp_path / "s1.mp4"; seg1.write_bytes(b"mp4")
    seg2 = tmp_path / "s2.mp4"; seg2.write_bytes(b"mp4")
    pipeline._ffmpeg_xfade_with_durations(
        [seg1, seg2], tmp_path / "out.mp4", "fade", 0.5, [30.0, 30.0]
    )

    ffmpeg_cmds = [c for c in captured if c[0] == "ffmpeg"]
    assert len(ffmpeg_cmds) == 1
    _assert_ultrafast(ffmpeg_cmds[0])


def test_cmd_ultrafast_in_burn_srt(monkeypatch, tmp_path):
    """_ffmpeg_burn_srt: ffmpeg cmd（至少第一路尝试）必须含 -preset ultrafast。"""
    captured: list[list[str]] = []
    monkeypatch.setattr(pipeline.subprocess, "run", _make_dispatch_fake(captured))

    video = tmp_path / "v.mp4"; video.write_bytes(b"mp4")
    srt = tmp_path / "s.srt"; srt.write_text("1\n0-->1\nhi\n")
    pipeline._ffmpeg_burn_srt(video, srt, tmp_path / "burned.mp4")

    ffmpeg_cmds = [c for c in captured if c[0] == "ffmpeg"]
    assert len(ffmpeg_cmds) >= 1, "至少应有一次 ffmpeg 调用"
    _assert_ultrafast(ffmpeg_cmds[0])


def _assert_ultrafast(cmd: list[str]):
    """断言 cmd 含 -preset ultrafast 相邻对。"""
    assert "-preset" in cmd, f"cmd 缺 -preset: {cmd}"
    idx = cmd.index("-preset")
    assert cmd[idx + 1] == "ultrafast", f"preset 值不是 ultrafast: cmd[{idx+1}]={cmd[idx+1]}"

# ---------------------------------------------------------------------------
# 4. progress_callback 续租调用验证（问题③）
# ---------------------------------------------------------------------------


def test_progress_callback_95_during_subtitle_burning_and_before_xfade(monkeypatch, tmp_path):
    """progress_callback(95) 须在字幕烧录每段后 + xfade 前调用，触发 renew_lease 续租。

    3 页 PPT + 字幕 + smooth 预设：
      step 1 (slide-to-video):  3 次 callback(88/91/95)
      step 1.5 (字幕烧录):      3 次 callback(95)  ← 续租
      step 2 (xfade 前):        1 次 callback(95)  ← 续租
    共 7 次，其中 95 至少 4 次（3 烧录 + 1 xfade 前）。
    """
    calls: list[int] = []

    def cb(pct: int) -> None:
        calls.append(pct)

    def fake_run(cmd, **kwargs):
        if cmd[0] == "ffprobe":
            return subprocess.CompletedProcess(cmd, 0, "30.0\n", "")
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(pipeline.subprocess, "run", fake_run)

    # 准备 3 页 slide + audio + subtitles.json
    task_dir = tmp_path / "task"
    (task_dir / "slides").mkdir(parents=True)
    (task_dir / "audio").mkdir(parents=True)
    for i in range(3):
        (task_dir / "slides" / f"slide-{i + 1:02d}.jpg").write_bytes(b"jpg")
        (task_dir / "audio" / f"slide-{i + 1:02d}.mp3").write_bytes(b"mp3")
    subtitles_data = [{"slide": i + 1, "text": f"测试字幕第{i + 1}页。"} for i in range(3)]
    (task_dir / "subtitles.json").write_text(json.dumps(subtitles_data), encoding="utf-8")

    pipeline.compose_ppt_video(task_dir, "smooth", subtitle=True, progress_callback=cb)

    # step 1 产生 3 次调用（88/91/95），step 1.5 + step 2 须额外产生 >= 4 次 95
    count_95 = calls.count(95)
    assert count_95 >= 4, (
        f"Expected >= 4 calls with 95 (3 subtitle burning + 1 before xfade), got {count_95}: {calls}"
    )

    # 末次调用须为 95（xfade 前续租）
    assert calls[-1] == 95, (
        f"Last call should be 95 (before xfade lease renewal), got {calls[-1]}: {calls}"
    )

    # 总调用数 = 3 (step1) + 3 (step1.5) + 1 (step2) = 7
    assert len(calls) == 7, f"Expected 7 total calls, got {len(calls)}: {calls}"


def test_progress_callback_95_before_xfade_no_subtitle(monkeypatch, tmp_path):
    """无字幕时 progress_callback(95) 仍须在 xfade 前调用（续租）。"""
    calls: list[int] = []

    def cb(pct: int) -> None:
        calls.append(pct)

    def fake_run(cmd, **kwargs):
        if cmd[0] == "ffprobe":
            return subprocess.CompletedProcess(cmd, 0, "30.0\n", "")
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(pipeline.subprocess, "run", fake_run)

    task_dir = tmp_path / "task"
    (task_dir / "slides").mkdir(parents=True)
    (task_dir / "audio").mkdir(parents=True)
    for i in range(3):
        (task_dir / "slides" / f"slide-{i + 1:02d}.jpg").write_bytes(b"jpg")
        (task_dir / "audio" / f"slide-{i + 1:02d}.mp3").write_bytes(b"mp3")

    pipeline.compose_ppt_video(task_dir, "smooth", subtitle=False, progress_callback=cb)

    # step 1: 3 次 (88/91/95), step 2: 1 次 (95) = 共 4 次
    assert len(calls) == 4, f"Expected 4 total calls, got {len(calls)}: {calls}"
    assert calls[-1] == 95, f"Last call should be 95 (before xfade), got {calls[-1]}: {calls}"
    # 95 至少 2 次（step1 末页 + xfade 前）
    assert calls.count(95) >= 2, f"Expected >= 2 calls with 95, got {calls.count(95)}: {calls}"


# ---------------------------------------------------------------------------
# 5. seg_timeout 钳租约上限（缺口1修复验证）
# ---------------------------------------------------------------------------


def test_seg_timeout_clamped_to_lease(monkeypatch, tmp_path):
    """_ffmpeg_slide_to_video: seg_timeout 须钳到 _LEASE_DURATION − 300。

    单页音频 >750s 时 duration*2 > 1500s，不钳会超租约（与 xfade 对称自洽）。
    """
    captured = {}

    def fake_run(cmd, **kwargs):
        if cmd[0] == "ffprobe":
            return subprocess.CompletedProcess(cmd, 0, "800.0\n", "")
        captured["timeout"] = kwargs.get("timeout")
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(pipeline.subprocess, "run", fake_run)

    img = tmp_path / "img.jpg"; img.write_bytes(b"jpg")
    aud = tmp_path / "aud.mp3"; aud.write_bytes(b"mp3")
    pipeline._ffmpeg_slide_to_video(img, aud, tmp_path / "seg.mp4")

    # duration=800 -> max(120, 1600)=1600, but clamped to 1800-300=1500
    assert captured["timeout"] == pipeline._LEASE_DURATION - 300, (
        f"seg_timeout should be clamped to _LEASE_DURATION-300={pipeline._LEASE_DURATION - 300}, "
        f"got {captured['timeout']}"
    )


def test_seg_timeout_normal_below_lease(monkeypatch, tmp_path):
    """_ffmpeg_slide_to_video: 正常时长（30s）-> max(120, 60)=120，不触发钳制。"""
    captured = {}

    def fake_run(cmd, **kwargs):
        if cmd[0] == "ffprobe":
            return subprocess.CompletedProcess(cmd, 0, "30.0\n", "")
        captured["timeout"] = kwargs.get("timeout")
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(pipeline.subprocess, "run", fake_run)

    img = tmp_path / "img.jpg"; img.write_bytes(b"jpg")
    aud = tmp_path / "aud.mp3"; aud.write_bytes(b"mp3")
    pipeline._ffmpeg_slide_to_video(img, aud, tmp_path / "seg.mp4")

    assert captured["timeout"] == 120, f"30s audio -> timeout=120, got {captured['timeout']}"


# ---------------------------------------------------------------------------
# 6. 分批 xfade 批间续租（缺口2修复验证）
# ---------------------------------------------------------------------------


def test_batched_xfade_inter_batch_lease_renewal(monkeypatch, tmp_path):
    """_ffmpeg_xfade_batched: 批间须调 progress_callback(95) 续租。

    25 段 -> 3 批（10+10+5），应有 3 次批间 callback(95)。
    compose_ppt_video 在进入 xfade 前已调一次，此处验证批间补充续租。
    """
    calls: list[int] = []

    def cb(pct: int) -> None:
        calls.append(pct)

    def fake_run(cmd, **kwargs):
        if cmd[0] == "ffprobe":
            return subprocess.CompletedProcess(cmd, 0, "30.0\n", "")
        # ffmpeg: create the output file so concat existence check passes
        out = Path(cmd[-1])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"mp4")
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(pipeline.subprocess, "run", fake_run)

    segs = [tmp_path / f"seg-{i}.mp4" for i in range(25)]
    for s in segs:
        s.write_bytes(b"mp4")

    pipeline._ffmpeg_xfade_batched(
        segs, tmp_path / "out.mp4", "fade", 1.0, [30.0] * 25, progress_callback=cb
    )

    # 3 batches (10+10+5) -> 3 inter-batch callbacks
    count_95 = calls.count(95)
    assert count_95 >= 3, (
        f"Expected >= 3 inter-batch callbacks(95) for 3 batches, got {count_95}: {calls}"
    )


def test_batched_xfade_no_callback_when_none(monkeypatch, tmp_path):
    """_ffmpeg_xfade_batched: progress_callback=None 时不报错（向后兼容）。"""
    def fake_run(cmd, **kwargs):
        if cmd[0] == "ffprobe":
            return subprocess.CompletedProcess(cmd, 0, "30.0\n", "")
        out = Path(cmd[-1])
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"mp4")
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(pipeline.subprocess, "run", fake_run)

    segs = [tmp_path / f"seg-{i}.mp4" for i in range(25)]
    for s in segs:
        s.write_bytes(b"mp4")

    # 不传 progress_callback，不应抛异常
    pipeline._ffmpeg_xfade_batched(
        segs, tmp_path / "out.mp4", "fade", 1.0, [30.0] * 25
    )


# ---------------------------------------------------------------------------
# 7. _run_ffmpeg 逐阶段耗时日志（优化建议2验证）
# ---------------------------------------------------------------------------


def test_run_ffmpeg_logs_elapsed(monkeypatch, caplog):
    """_run_ffmpeg 正常完成时记 info 日志含 elapsed/timeout/rc。"""
    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(pipeline.subprocess, "run", fake_run)

    with caplog.at_level("INFO", logger="pipeline"):
        pipeline._run_ffmpeg(["ffmpeg", "-y"], timeout=60, friendly_timeout_msg="unreachable")

    assert any("ffmpeg stage elapsed=" in r.message for r in caplog.records), (
        f"Expected elapsed log, got: {[r.message for r in caplog.records]}"
    )


def test_run_ffmpeg_logs_timeout_elapsed(monkeypatch, caplog):
    """_run_ffmpeg 超时时记 warning 日志含 TIMEOUT/elapsed/timeout。"""
    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, kwargs.get("timeout", 5))

    monkeypatch.setattr(pipeline.subprocess, "run", fake_run)

    with caplog.at_level("WARNING", logger="pipeline"):
        with pytest.raises(RuntimeError):
            pipeline._run_ffmpeg(["ffmpeg", "-y"], timeout=5, friendly_timeout_msg="超时")

    assert any("TIMEOUT" in r.message and "elapsed=" in r.message for r in caplog.records), (
        f"Expected TIMEOUT elapsed log, got: {[r.message for r in caplog.records]}"
    )
