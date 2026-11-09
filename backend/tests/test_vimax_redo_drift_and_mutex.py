"""终审 fix pass（Lane 2, 2026-10-15）：redo 时长漂移对齐 + 同任务并发 redo 互斥。

Important 1：替换镜时长与原镜有差（provider 量化/换通道）→ 重拼成片总长漂移，
但 narration/字幕仍按旧时间轴 → redo 边界之后全片音画错位且尾部语音被切。
修复：delta 超 0.1s 容差 → SRT cue 按边界平移 + narration 在边界插/删静音 +
redo trail 与 degradations 留痕「时长漂移」。

Important 2：同任务两个 redo 并发（用户连点两镜）→ 无人互斥：双 ffmpeg 写同名
交付物、互相清 final、result 全量覆写丢更新。修复：service 层 per-task
asyncio.Lock 包全程。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.gateway.video_gen.engines.vimax.engine as ve
from app.gateway.video_gen.engines.vimax.engine import _shift_srt_cues, build_vimax_shots_manifest

# ── Important 1a：SRT 文本平移（纯函数）────────────────────────────────────


def test_shift_srt_cues_shifts_only_after_boundary():
    """boundary 之后的 cue 整体平移 delta；之前的 cue 不动；毫秒精度。"""
    srt = "1\n00:00:02,000 --> 00:00:03,000\nshot0 台词\n\n2\n00:00:06,000 --> 00:00:07,500\nshot1 台词\n\n3\n00:00:08,000 --> 00:00:09,000\nshot2 台词\n"
    out = _shift_srt_cues(srt, boundary=5.5, delta=-0.5)
    assert "00:00:02,000 --> 00:00:03,000" in out, "boundary 前的 cue 不得动"
    assert "00:00:05,500 --> 00:00:07,000" in out, f"shot1 cue 应 -0.5，实际：{[line for line in out.splitlines() if '-->' in line]}"
    assert "00:00:07,500 --> 00:00:08,500" in out, "shot2 cue 应 -0.5"


def test_shift_srt_cues_positive_delta_and_clamp():
    """正向漂移同理；钳制不为负。"""
    srt = "1\n00:00:00,100 --> 00:00:00,200\nx\n"
    out = _shift_srt_cues(srt, boundary=0.0, delta=0.25)
    assert "00:00:00,350 --> 00:00:00,450" in out


# ── Important 1b：redo 全链的漂移对齐 + 留痕 ────────────────────────────────


def _seed_shot(working_dir: Path, scene: Path, idx: int) -> None:
    shot_dir = scene / "shots" / str(idx)
    shot_dir.mkdir(parents=True, exist_ok=True)
    (shot_dir / "video.mp4").write_bytes(f"OLD{idx}".encode())
    (shot_dir / "first_frame.png").write_bytes(f"PNG{idx}".encode())


def _seed_task_disk(tmp_path: Path, task_id: str = "vg_drift", user: str = "test-user-autouse") -> tuple[Path, Path]:
    task_dir = tmp_path / user / "tasks" / task_id
    working_dir = task_dir / "vimax_working"
    for i in range(3):
        _seed_shot(working_dir, working_dir, i)
    (working_dir / "storyboard.json").write_text(json.dumps([{"idx": i, "visual_desc": f"v{i}"} for i in range(3)]), encoding="utf-8")
    (task_dir / "vimax_narration.mp3").write_bytes(b"AUDIO")
    # 字幕：shot0 窗口一条（不动）+ shot1/2 窗口各一条（应平移）
    (task_dir / "subtitle.srt").write_text(
        "1\n00:00:02,000 --> 00:00:03,000\nzero\n\n2\n00:00:06,000 --> 00:00:07,500\none\n\n3\n00:00:08,000 --> 00:00:09,000\ntwo\n",
        encoding="utf-8",
    )
    (task_dir / "subtitle.ass").write_text("[Script Info]\nPlayResX: 1080\n", encoding="utf-8")
    return task_dir, working_dir


class _FakeVideoRef:
    def __init__(self, url: str):
        self.url = url


def _make_service(tmp_path, task_result: dict, payload: dict | None = None):
    from app.gateway.video_gen.config import VideoGenConfig
    from app.gateway.video_gen.providers.registry import ProviderRegistry
    from app.gateway.video_gen.service import VideoGenService

    cfg = VideoGenConfig(qihu_api_key="t")
    repo = MagicMock()
    task = MagicMock()
    task.task_type = "vimax"
    task.status = "ready"
    task.payload = json.dumps(payload or {"aspect": "9:16"}, ensure_ascii=False)
    task.result = json.dumps(task_result, ensure_ascii=False)
    repo.get = AsyncMock(return_value=task)
    repo.update = AsyncMock(return_value=None)
    return VideoGenService(repo=repo, config=cfg, providers=ProviderRegistry(cfg), storage_dir=str(tmp_path), sandbox_provider=AsyncMock()), repo, task


@pytest.mark.asyncio
async def test_redo_duration_drift_retimes_and_notes(tmp_path, monkeypatch):
    """新镜 5.0s vs 旧镜 5.5s（delta=-0.5）→ SRT 平移 + narration 边界调整 + trail/降级留痕。"""
    task_dir, working_dir = _seed_task_disk(tmp_path)

    def fake_probe(video_path, *a, **kw):
        p = Path(video_path)
        if p.exists() and p.read_bytes() == b"NEWVIDEO":
            return 5.0  # 替换后的新镜实测
        return 5.5

    monkeypatch.setattr(ve, "_get_shot_duration", fake_probe)
    manifest = await build_vimax_shots_manifest(working_dir, task_dir)

    async def fake_generate_video(prompt, *, duration=30.0, **kwargs):
        return _FakeVideoRef("http://cdn/new.mp4")

    audio_adjusts: list[tuple[float, float]] = []

    async def fake_adjust(audio_path, *, at_seconds, delta):
        audio_adjusts.append((at_seconds, delta))

    def fake_concat(paths, output, **kwargs):
        Path(output).write_bytes(b"RECONCAT")

    async def fake_overlay(video_path, task_dir, *, audio_override=None, burn_subtitles=False, subtitle_style="default"):
        out = Path(task_dir) / "vimax_final_loudnorm.mp4"
        out.write_bytes(b"X")
        return out

    async def fake_download(url, **kwargs):
        return b"NEWVIDEO"

    monkeypatch.setattr(ve, "_adjust_narration_tail", fake_adjust)

    svc, repo, _task = _make_service(tmp_path, task_result={"vimax_shots": manifest})
    monkeypatch.setattr(svc._providers, "ai_video", MagicMock(generate_video=fake_generate_video))
    monkeypatch.setattr(ve, "concatenate_video_files", fake_concat)
    monkeypatch.setattr(ve, "overlay_audio_track", fake_overlay)
    monkeypatch.setattr(ve, "safe_download", fake_download)

    resp = await svc.redo_vimax_shot(task_id="vg_drift", user_id="test-user-autouse", shot_id=1)

    assert resp["regenerated_shot_id"] == 1
    # narration 在边界（shot0 时长 5.5s）被平移 -0.5
    assert audio_adjusts == [(5.5, -0.5)], f"narration 边界调整参数错，实际 {audio_adjusts}"
    # SRT：boundary 后 cue 平移 -0.5；boundary 前 cue 不动
    srt_text = (task_dir / "subtitle.srt").read_text(encoding="utf-8")
    assert "00:00:05,500 --> 00:00:07,000" in srt_text, "shot1 cue 应 -0.5"
    assert "00:00:07,500 --> 00:00:08,500" in srt_text, "shot2 cue 应 -0.5"
    assert "00:00:02,000 --> 00:00:03,000" in srt_text, "shot0 cue 不得动"
    # 旧 .ass（派生、时间轴已失真）清除——overlay 烧录回落用校正后的 .srt
    assert not (task_dir / "subtitle.ass").exists()
    # trail + 降级留痕
    merged = json.loads(repo.update.call_args.kwargs["result"])
    assert merged["vimax_shots"]["1"]["redo"]["duration_drift"] == -0.5, "漂移值须留痕"
    degr = json.loads((task_dir / "degradations.json").read_text(encoding="utf-8"))["items"]
    assert any(i.get("kind") == "vimax_redo_duration_drift" and i.get("frame_index") == 1 for i in degr), f"须留时长漂移降级记录，实际 {degr}"


@pytest.mark.asyncio
async def test_redo_within_tolerance_no_retime(tmp_path, monkeypatch):
    """delta 在 0.1s 容差内（对齐 narration 容差口径）→ 不平移不留痕。"""
    task_dir, working_dir = _seed_task_disk(tmp_path)
    srt_before = (task_dir / "subtitle.srt").read_text(encoding="utf-8")

    def fake_probe(video_path, *a, **kw):
        return 5.45  # delta = -0.05 < 0.1

    monkeypatch.setattr(ve, "_get_shot_duration", fake_probe)
    manifest = await build_vimax_shots_manifest(working_dir, task_dir)

    async def fake_generate_video(prompt, *, duration=30.0, **kwargs):
        return _FakeVideoRef("http://cdn/new.mp4")

    called = []

    async def fake_adjust(audio_path, *, at_seconds, delta):
        called.append((at_seconds, delta))

    def fake_concat(paths, output, **kwargs):
        Path(output).write_bytes(b"R")

    async def fake_overlay(video_path, task_dir, *, audio_override=None, burn_subtitles=False, subtitle_style="default"):
        out = Path(task_dir) / "vimax_final_loudnorm.mp4"
        out.write_bytes(b"X")
        return out

    async def fake_download(url, **kwargs):
        return b"NEWVIDEO"

    monkeypatch.setattr(ve, "_adjust_narration_tail", fake_adjust)
    svc, repo, _task = _make_service(tmp_path, task_result={"vimax_shots": manifest})
    monkeypatch.setattr(svc._providers, "ai_video", MagicMock(generate_video=fake_generate_video))
    monkeypatch.setattr(ve, "concatenate_video_files", fake_concat)
    monkeypatch.setattr(ve, "overlay_audio_track", fake_overlay)
    monkeypatch.setattr(ve, "safe_download", fake_download)

    await svc.redo_vimax_shot(task_id="vg_drift", user_id="test-user-autouse", shot_id=1)

    assert called == [], "容差内不得动 narration"
    assert (task_dir / "subtitle.srt").read_text(encoding="utf-8") == srt_before, "容差内不得动 SRT"
    merged = json.loads(repo.update.call_args.kwargs["result"])
    assert "duration_drift" not in merged["vimax_shots"]["1"]["redo"], "容差内不留漂移痕"


# ── Important 2：并发 redo 互斥 ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_concurrent_redo_on_same_task_serialized(tmp_path, monkeypatch):
    """同任务两个 redo 并发 → 必须串行（否则双 ffmpeg 写同名交付物 + result 互相覆写）。"""
    task_dir, working_dir = _seed_task_disk(tmp_path, task_id="vg_race")

    def fake_probe(video_path, *a, **kw):
        return 5.5

    monkeypatch.setattr(ve, "_get_shot_duration", fake_probe)
    manifest = await build_vimax_shots_manifest(working_dir, task_dir)

    overlap = {"cur": 0, "max": 0}

    async def fake_generate_video(prompt, *, duration=30.0, **kwargs):
        overlap["cur"] += 1
        overlap["max"] = max(overlap["max"], overlap["cur"])
        await asyncio.sleep(0.05)  # 拉开并发窗口
        overlap["cur"] -= 1
        return _FakeVideoRef("http://cdn/new.mp4")

    def fake_concat(paths, output, **kwargs):
        Path(output).write_bytes(b"R")

    async def fake_overlay(video_path, task_dir, *, audio_override=None, burn_subtitles=False, subtitle_style="default"):
        out = Path(task_dir) / "vimax_final_loudnorm.mp4"
        out.write_bytes(b"X")
        return out

    async def fake_download(url, **kwargs):
        return b"NEWVIDEO"

    async def fake_adjust(audio_path, *, at_seconds, delta):
        pass

    monkeypatch.setattr(ve, "_adjust_narration_tail", fake_adjust)
    svc, _repo, _task = _make_service(tmp_path, task_result={"vimax_shots": manifest}, payload={"task_id_prefix": "x"})
    # 覆盖 task_id（_make_service 固定 repo mock 与 task_id 无关——重新设 repo 返回同一 task）
    monkeypatch.setattr(svc._providers, "ai_video", MagicMock(generate_video=fake_generate_video))
    monkeypatch.setattr(ve, "concatenate_video_files", fake_concat)
    monkeypatch.setattr(ve, "overlay_audio_track", fake_overlay)
    monkeypatch.setattr(ve, "safe_download", fake_download)

    await asyncio.gather(
        svc.redo_vimax_shot(task_id="vg_race", user_id="test-user-autouse", shot_id=1),
        svc.redo_vimax_shot(task_id="vg_race", user_id="test-user-autouse", shot_id=2),
    )

    assert overlap["max"] == 1, f"同任务 redo 必须串行（max concurrent = {overlap['max']}）——双写交付物/result 互覆风险"


# ── 真实 ffmpeg：_adjust_narration_tail 命令形态 ────────────────────────────


def _has_ffmpeg() -> bool:
    import shutil

    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def _dur(path: Path) -> float:
    import subprocess as sp

    out = sp.run(["ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    return float(out.stdout.strip())


pytestmark_ffmpeg = pytest.mark.skipif(not _has_ffmpeg(), reason="需要 ffmpeg/ffprobe")


@pytest.mark.asyncio
@pytestmark_ffmpeg
async def test_adjust_narration_tail_real_ffmpeg(tmp_path):
    """插静音 +1s → 总长 +1；截除 -0.5s → 总长 -0.5（命令形态与时长方向的实证）。"""
    import subprocess as sp

    from app.gateway.video_gen.engines.vimax.engine import _adjust_narration_tail

    audio = tmp_path / "narration.mp3"
    sp.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=3", "-c:a", "libmp3lame", "-q:a", "4", str(audio)], capture_output=True, check=True)
    base = _dur(audio)

    await _adjust_narration_tail(audio, at_seconds=1.5, delta=1.0)
    assert abs(_dur(audio) - (base + 1.0)) < 0.15, f"插 1s 静音后应 ~{base + 1:.2f}s，实际 {_dur(audio):.2f}s"

    await _adjust_narration_tail(audio, at_seconds=1.5, delta=-0.5)
    assert abs(_dur(audio) - (base + 0.5)) < 0.15, f"截 0.5s 后应 ~{base + 0.5:.2f}s，实际 {_dur(audio):.2f}s"


# ── 规划侧返修（P1×2 + 小修）：失败恢复 / 幂等 / 毫秒进位 ─────────────────────


@pytest.mark.asyncio
async def test_adjust_narration_tail_failure_restores_original(tmp_path, monkeypatch):
    """P1-1：ffmpeg 失败 → 原音频字节不变（降级承诺「沿用原音频」），异常上浮，无残留。"""
    import subprocess as sp

    audio = tmp_path / "vimax_narration.mp3"
    original = b"ORIGINAL-NARRATION-BYTES"
    audio.write_bytes(original)

    async def failing_run_ffmpeg(fn, *args, **kwargs):
        # 模拟 ffmpeg rc=1 且不留产物（更狠的变体：半截产物由 unlink 兜住）
        return sp.CompletedProcess(args[0] if args else [], 1, b"", b"boom")

    monkeypatch.setattr(ve, "run_ffmpeg", failing_run_ffmpeg)

    with pytest.raises(RuntimeError, match="drift adjust failed"):
        await ve._adjust_narration_tail(audio, at_seconds=1.5, delta=1.0)

    assert audio.read_bytes() == original, "P1：ffmpeg 失败不得丢失/改写原旁白——调用方承诺沿用原音频"
    assert not (tmp_path / "vimax_narration.mp3.drift-tmp").exists(), "失败路径不得残留 tmp（原件须已归位）"


@pytest.mark.asyncio
async def test_redo_drift_not_reapplied_on_retry_after_concat_failure(tmp_path, monkeypatch):
    """P1-2：对齐后 concat 失败 → 重试同镜 → SRT/旁白只被平移一次（幂等防重）。"""
    task_dir, working_dir = _seed_task_disk(tmp_path, task_id="vg_retry")

    def fake_probe(video_path, *a, **kw):
        p = Path(video_path)
        return 5.0 if p.exists() and p.read_bytes() == b"NEWVIDEO" else 5.5

    monkeypatch.setattr(ve, "_get_shot_duration", fake_probe)
    manifest = await build_vimax_shots_manifest(working_dir, task_dir)

    async def fake_generate_video(prompt, *, duration=30.0, **kwargs):
        return _FakeVideoRef("http://cdn/new.mp4")

    adjust_calls: list[float] = []

    async def fake_adjust(audio_path, *, at_seconds, delta):
        adjust_calls.append(delta)

    concat_calls = {"n": 0}

    def flaky_concat(paths, output, **kwargs):
        concat_calls["n"] += 1
        if concat_calls["n"] == 1:
            raise RuntimeError("concat exploded (injected)")
        Path(output).write_bytes(b"RECONCAT")

    async def fake_overlay(video_path, task_dir, *, audio_override=None, burn_subtitles=False, subtitle_style="default"):
        out = Path(task_dir) / "vimax_final_loudnorm.mp4"
        out.write_bytes(b"X")
        return out

    async def fake_download(url, **kwargs):
        return b"NEWVIDEO"

    monkeypatch.setattr(ve, "_adjust_narration_tail", fake_adjust)
    monkeypatch.setattr(ve, "concatenate_video_files", flaky_concat)
    monkeypatch.setattr(ve, "overlay_audio_track", fake_overlay)
    monkeypatch.setattr(ve, "safe_download", fake_download)

    svc, repo, _task = _make_service(tmp_path, task_result={"vimax_shots": manifest})
    monkeypatch.setattr(svc._providers, "ai_video", MagicMock(generate_video=fake_generate_video))

    # 第一次：对齐已应用 → concat 注入失败 → 整体 raise，result 未回写
    with pytest.raises(RuntimeError, match="concat exploded"):
        await svc.redo_vimax_shot(task_id="vg_retry", user_id="test-user-autouse", shot_id=1)
    repo.update.reset_mock()

    # 重试同镜：manifest 时长未变 → delta 相同 → 幂等跳过，不得二次平移
    resp = await svc.redo_vimax_shot(task_id="vg_retry", user_id="test-user-autouse", shot_id=1)
    assert resp["regenerated_shot_id"] == 1

    assert adjust_calls == [-0.5], f"旁白只应被调整一次（首次尝试），实际 {adjust_calls}"
    srt_text = (task_dir / "subtitle.srt").read_text(encoding="utf-8")
    assert "00:00:05,500 --> 00:00:07,000" in srt_text, "SRT 恰平移一次"
    assert "00:00:05,000 --> 00:00:06,500" not in srt_text, "SRT 不得被二次平移"
    degr = [i for i in json.loads((task_dir / "degradations.json").read_text(encoding="utf-8"))["items"] if i.get("kind") == "vimax_redo_duration_drift"]
    assert len(degr) == 1, f"漂移留痕恰一条（degradation 幂等 + 应用幂等双保险），实际 {len(degr)}"


def test_shift_srt_cues_millisecond_carry():
    """小修：浮点四舍五入不得产出非法毫秒段 ",1000"（评审复现 01,118 - 0.118）。"""
    srt = "1\n00:00:01,118 --> 00:00:02,118\nx\n"
    out = _shift_srt_cues(srt, boundary=0.0, delta=-0.118)
    ts_line = [line for line in out.splitlines() if "-->" in line][0]
    assert ts_line == "00:00:01,000 --> 00:00:02,000", f"1.118-0.118=1.0s 应进位为 01,000，实际 {ts_line}"
    assert ",1000" not in out, "非法毫秒段 ,1000 必须杜绝（libass/SRT 解析风险）"


# ── PR#26 OCR 返修（三条 bug·medium）────────────────────────────────────────


@pytest.mark.asyncio
async def test_single_reference_downgrade_skips_duplicate_tier(tmp_path, monkeypatch):
    """OCR-1：单参考时 tier-0 与 tier-1 同请求——审核拒绝后不得原样重打（白烧一单）。"""
    task_dir, working_dir = _seed_task_disk(tmp_path, task_id="vg_tier")

    def fake_probe(video_path, *a, **kw):
        return 5.5

    monkeypatch.setattr(ve, "_get_shot_duration", fake_probe)
    manifest = await build_vimax_shots_manifest(working_dir, task_dir)

    calls: list[dict] = []

    async def fake_generate_video(prompt, *, duration=30.0, **kwargs):
        calls.append({"prompt": prompt, **kwargs})
        if kwargs.get("image_urls"):
            raise RuntimeError("green net check failed")
        return _FakeVideoRef("http://cdn/t2v.mp4")

    def fake_concat(paths, output, **kwargs):
        Path(output).write_bytes(b"R")

    async def fake_overlay(video_path, task_dir, *, audio_override=None, burn_subtitles=False, subtitle_style="default"):
        out = Path(task_dir) / "vimax_final_loudnorm.mp4"
        out.write_bytes(b"X")
        return out

    async def fake_download(url, **kwargs):
        return b"T2V"

    async def fake_adjust(audio_path, *, at_seconds, delta):
        pass

    monkeypatch.setattr(ve, "_adjust_narration_tail", fake_adjust)
    svc, repo, _task = _make_service(tmp_path, task_result={"vimax_shots": manifest})
    monkeypatch.setattr(svc._providers, "ai_video", MagicMock(generate_video=fake_generate_video))
    monkeypatch.setattr(ve, "concatenate_video_files", fake_concat)
    monkeypatch.setattr(ve, "overlay_audio_track", fake_overlay)
    monkeypatch.setattr(ve, "safe_download", fake_download)

    # redo shot 0：无前镜，仅后镜 shot1 一个本地参考 → references 恰 1 个
    resp = await svc.redo_vimax_shot(task_id="vg_tier", user_id="test-user-autouse", shot_id=0)

    assert len(calls) == 2, f"单参考：tier-0(带图) + tier-2(纯 prompt) 恰两次调用，不得原样重打 tier-1，实际 {len(calls)} 次：{calls}"
    assert calls[0].get("image_urls"), "首次调用带参考图"
    assert not calls[1].get("image_urls"), "降级后纯 prompt"
    assert resp["reference_mode"] == "prompt_only"
    merged = json.loads(repo.update.call_args.kwargs["result"])
    assert "纯 prompt" in merged["vimax_shots"]["0"]["redo"]["note"]


@pytest.mark.asyncio
async def test_resolve_ref_passes_url_forms_verbatim(tmp_path, monkeypatch):
    """OCR-2：character_front/visual_path 为 http(s):// 或 data: URL 时原样透传，不得拼 task_dir 垃圾路径。"""
    from types import SimpleNamespace as _NS

    task_dir, working_dir = _seed_task_disk(tmp_path, task_id="vg_url")

    def fake_probe(video_path, *a, **kw):
        return 5.5

    monkeypatch.setattr(ve, "_get_shot_duration", fake_probe)
    manifest = await build_vimax_shots_manifest(working_dir, task_dir)

    seen_urls: list = []

    async def fake_generate_video(prompt, *, duration=30.0, **kwargs):
        seen_urls.extend(kwargs.get("image_urls") or [])
        return _FakeVideoRef("http://cdn/new.mp4")

    def fake_concat(paths, output, **kwargs):
        Path(output).write_bytes(b"R")

    async def fake_overlay(video_path, task_dir, *, audio_override=None, burn_subtitles=False, subtitle_style="default"):
        out = Path(task_dir) / "vimax_final_loudnorm.mp4"
        out.write_bytes(b"X")
        return out

    async def fake_download(url, **kwargs):
        return b"NEWVIDEO"

    async def fake_adjust(audio_path, *, at_seconds, delta):
        pass

    monkeypatch.setattr(ve, "_adjust_narration_tail", fake_adjust)
    monkeypatch.setattr(
        "app.gateway.video_gen.service._read_course_project_character_sheets",
        lambda *a, **kw: [_NS(front_url="https://example.com/char_front.png")],
    )
    svc, repo, _task = _make_service(tmp_path, task_result={"vimax_shots": manifest}, payload={"course_project_id": "proj_x"})
    monkeypatch.setattr(svc._providers, "ai_video", MagicMock(generate_video=fake_generate_video))
    monkeypatch.setattr(ve, "concatenate_video_files", fake_concat)
    monkeypatch.setattr(ve, "overlay_audio_track", fake_overlay)
    monkeypatch.setattr(ve, "safe_download", fake_download)

    await svc.redo_vimax_shot(task_id="vg_url", user_id="test-user-autouse", shot_id=1)

    assert "https://example.com/char_front.png" in seen_urls, f"URL 形态参考图须原样透传，实际收到 {seen_urls}"
    assert not any(u.startswith("/") and "https:" in u for u in seen_urls), f"不得出现 task_dir 拼接的垃圾路径：{seen_urls}"


@pytest.mark.asyncio
async def test_drift_marker_partial_application_retries_narration_only(tmp_path, monkeypatch):
    """OCR-3：narration 首次对齐失败 → 同参重试 → narration 补试一次且 SRT 不二次平移。"""
    task_dir, working_dir = _seed_task_disk(tmp_path, task_id="vg_partial")
    srt_before = (task_dir / "subtitle.srt").read_text(encoding="utf-8")

    def fake_probe(video_path, *a, **kw):
        p = Path(video_path)
        return 5.0 if p.exists() and p.read_bytes() == b"NEWVIDEO" else 5.5

    monkeypatch.setattr(ve, "_get_shot_duration", fake_probe)
    manifest = await build_vimax_shots_manifest(working_dir, task_dir)

    async def fake_generate_video(prompt, *, duration=30.0, **kwargs):
        return _FakeVideoRef("http://cdn/new.mp4")

    adjust_calls: list[float] = []

    async def flaky_adjust(audio_path, *, at_seconds, delta):
        adjust_calls.append(delta)
        if len(adjust_calls) == 1:
            raise RuntimeError("ffmpeg boom (injected narration failure)")

    concat_calls = {"n": 0}

    def flaky_concat(paths, output, **kwargs):
        concat_calls["n"] += 1
        if concat_calls["n"] == 1:
            raise RuntimeError("concat exploded (injected)")
        Path(output).write_bytes(b"RECONCAT")

    async def fake_overlay(video_path, task_dir, *, audio_override=None, burn_subtitles=False, subtitle_style="default"):
        out = Path(task_dir) / "vimax_final_loudnorm.mp4"
        out.write_bytes(b"X")
        return out

    async def fake_download(url, **kwargs):
        return b"NEWVIDEO"

    monkeypatch.setattr(ve, "_adjust_narration_tail", flaky_adjust)
    monkeypatch.setattr(ve, "concatenate_video_files", flaky_concat)
    monkeypatch.setattr(ve, "overlay_audio_track", fake_overlay)
    monkeypatch.setattr(ve, "safe_download", fake_download)

    svc, repo, _task = _make_service(tmp_path, task_result={"vimax_shots": manifest})
    monkeypatch.setattr(svc._providers, "ai_video", MagicMock(generate_video=fake_generate_video))

    # 首次：narration 对齐失败（留痕降级）→ concat 注入失败 → 整体 raise
    with pytest.raises(RuntimeError, match="concat exploded"):
        await svc.redo_vimax_shot(task_id="vg_partial", user_id="test-user-autouse", shot_id=1)
    repo.update.reset_mock()

    # 同参重试：SRT 已平移（防二次）→ narration 补试一次（首次失败的那次不算成功）
    await svc.redo_vimax_shot(task_id="vg_partial", user_id="test-user-autouse", shot_id=1)

    assert adjust_calls == [-0.5, -0.5], f"narration 恰补试一次（共两次调用：首败 + 补试），实际 {adjust_calls}"
    srt_after = (task_dir / "subtitle.srt").read_text(encoding="utf-8")
    assert srt_after != srt_before, "SRT 首试已平移"
    assert "00:00:05,500 --> 00:00:07,000" in srt_after, "SRT 恰平移一次"
    assert "00:00:05,000 --> 00:00:06,500" not in srt_after, "SRT 不得二次平移"
    marker = json.loads((task_dir / "redo_drift_marker.json").read_text(encoding="utf-8"))
    assert marker.get("narration_applied") is True, f"补试成功后标记须记 narration_applied，实际 {marker}"


# ── T4（2026-10-16 收口批）：漂移标记增量写——部件成功即落盘 ─────────────────


@pytest.mark.asyncio
async def test_drift_marker_incremental_write_survives_crash_between_components(tmp_path, monkeypatch):
    """SRT 平移成功后、narration 调整前的进程崩溃（BaseException 穿出，不走
    收尾写）→ 标记必须已在盘上（srt_applied=True）——否则同参重试二次平移 SRT。"""
    task_dir, working_dir = _seed_task_disk(tmp_path)

    async def crash_adjust(audio_path, *, at_seconds, delta):
        raise KeyboardInterrupt("process killed between SRT shift and narration adjust")

    monkeypatch.setattr(ve, "_adjust_narration_tail", crash_adjust)

    with pytest.raises(KeyboardInterrupt):
        await ve._apply_redo_drift_alignment(task_dir, boundary=5.5, delta=-0.5, shot_id=1, old_duration=5.5, new_duration=5.0)

    marker = json.loads((task_dir / "redo_drift_marker.json").read_text(encoding="utf-8"))
    assert marker["srt_applied"] is True, f"崩溃前 SRT 已平移，标记必须落盘，实际 {marker}"
    assert marker["narration_applied"] is False
    assert marker["applied_delta"] == -0.5

    # 同参重试（narration 恢复）：SRT 不二次平移、narration 全量补试
    calls: list[tuple[float, float]] = []

    async def ok_adjust(audio_path, *, at_seconds, delta):
        calls.append((at_seconds, delta))

    monkeypatch.setattr(ve, "_adjust_narration_tail", ok_adjust)
    await ve._apply_redo_drift_alignment(task_dir, boundary=5.5, delta=-0.5, shot_id=1, old_duration=5.5, new_duration=5.0)

    srt_text = (task_dir / "subtitle.srt").read_text(encoding="utf-8")
    assert "00:00:05,500 --> 00:00:07,000" in srt_text, "SRT 恰平移一次"
    assert "00:00:05,000 --> 00:00:06,500" not in srt_text, "SRT 不得二次平移"
    assert calls == [(5.5, -0.5)], f"narration 全量补试一次，实际 {calls}"
