"""Plan A Task 1（2026-10-18）：_concat_chunks 输入与产物统一解码时长口径。

事故 vg_da6419f0c0e6（2026-10-17，failed@99%）：19 段对齐 chunk 用容器口径
（ffprobe format=duration，含 LAME encoder delay/padding）求和得 153.86s，拼接产物
容器口径 152.64s，差 1.22s > 0.5s 阈值 → 完整旁白被误判截断。服务器只读双口径
对账（ffmpeg 5.1.9）：

- 容器口径：Σchunk=153.864 vs output=152.640 → 差 1.224s（复现事故误杀）
- 解码口径：Σchunk=152.510 vs output=152.560 → 差 0.050s（产物完整）

即分片容器口径每段虚高 ~60-78ms（解码会裁掉 delay/padding），19 段累积 1.35s
幻影时长——产物本身是完整的。修复 = expected/actual 双侧统一用解码口径
（orchestration.tts._get_audio_duration 语义）。

本机 ffmpeg 9 合成样本双口径零漂移（2026-10-16 backlog 现象），无法靠本地编码器
自然产生 gapless 差异，故用 **Xing 头帧计数篡改** 构造「容器 > 解码」分片——与事故
属同一差异类：容器口径声明的内容多于解码实际产出的内容。篡改分片带前置断言
（每段 gap ≥ 阈值），若未来 ffmpeg 在 probe 层归一化，测试会因前置失败而显式报错，
而不是静默变成永真用例。
"""

import json
import struct
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.gateway.video_gen.engines.vimax import narration_generator as ng


def _has_ffmpeg() -> bool:
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
        return True
    except (OSError, subprocess.CalledProcessError):
        return False


pytestmark = pytest.mark.skipif(not _has_ffmpeg(), reason="需要 ffmpeg")


# ── 测试侧双口径探针（断言用；生产口径由被测代码自身调用） ──────────────────


def _container_duration(path: str) -> float:
    proc = subprocess.run(
        ["ffprobe", "-v", "quiet", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True,
        text=True,
        timeout=10,
    )
    return float(proc.stdout.strip()) if proc.returncode == 0 else 0.0


def _decode_duration(path: str) -> float:
    """ffmpeg -f null 解码计时（与 orchestration.tts._get_audio_duration 同语义）。"""
    proc = subprocess.run(["ffmpeg", "-i", path, "-f", "null", "-"], capture_output=True, text=True, timeout=30)
    last = None
    for line in proc.stderr.replace("\r", "\n").split("\n"):
        if "time=" in line:
            t = line.split("time=")[1].split()[0]
            try:
                h, m, s = t.split(":")
                last = int(h) * 3600 + int(m) * 60 + float(s)
            except (ValueError, IndexError):
                continue
    return last or 0.0


# ── MP3 矩阵构造 ────────────────────────────────────────────────────────────


def _xing_frame_count_offset(data: bytes) -> tuple[int, int]:
    """定位首帧 Xing 头的帧计数字段偏移；返回 (offset, sample_rate)。

    mp3 帧头 4 字节 + side_info（MPEG1: mono 17 / stereo 32；MPEG2: mono 9 /
    stereo 17）+ 'Xing' 4 字节 + flags 4 字节（bit0=帧数存在）→ 帧计数 4 字节 BE。
    """
    i = 0
    if data[:3] == b"ID3":
        i = 6 + (int.from_bytes(data[6:10], "big") & 0x0FFFFFFF)
    while i < len(data) - 4:
        if data[i] == 0xFF and (data[i + 1] & 0xE0) == 0xE0:
            break
        i += 1
    h = data[i : i + 4]
    assert len(h) == 4, "找不到 mp3 帧同步字"
    ver = (h[1] >> 3) & 3  # 3=MPEG1, 2=MPEG2/2.5
    sr_idx = (h[2] >> 2) & 3
    rates = {3: [44100, 48000, 32000], 2: [22050, 24000, 16000]}
    sr = rates[ver][sr_idx]
    mono = ((h[3] >> 6) & 3) == 3
    if ver == 3:
        side_info = 17 if mono else 32
    else:
        side_info = 9 if mono else 17
    x_off = i + 4 + side_info
    assert data[x_off : x_off + 4] == b"Xing", f"Xing 头不在预期偏移: {data[x_off : x_off + 8]!r}"
    flags = struct.unpack(">I", data[x_off + 4 : x_off + 8])[0]
    assert flags & 1, "Xing flags 未声明帧数"
    return x_off + 8, sr


def _inflate_xing_frame_count(path: Path, extra_frames: int) -> None:
    """把 Xing 头声明的帧数 +extra_frames（原地改写文件）。

    ffprobe 的 format=duration 信任 Xing 帧数 → 容器口径虚增
    extra×spf/sr；解码器按实际帧解码 → 解码口径不变。这正是事故分片的
    差异形态：容器口径 > 解码口径。
    """
    data = bytearray(path.read_bytes())
    cnt_off, _sr = _xing_frame_count_offset(bytes(data))
    frames = struct.unpack(">I", data[cnt_off : cnt_off + 4])[0]
    data[cnt_off : cnt_off + 4] = struct.pack(">I", frames + extra_frames)
    path.write_bytes(bytes(data))


def _make_tone(path: Path, *, sample_rate: int, seconds: float, freq: int, xing: bool = True) -> None:
    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", f"sine=frequency={freq}:duration={seconds}", "-ar", str(sample_rate), "-c:a", "libmp3lame", "-q:a", "4"]
    if not xing:
        cmd += ["-write_xing", "0"]
    cmd.append(str(path))
    subprocess.run(cmd, capture_output=True, check=True)


def _build_gap_matrix(tmp_path: Path, count: int = 19, sample_rate: int = 44100) -> list[str]:
    """可重复生成的 MP3 矩阵：Xing 篡改帧计数（多数段）+ non-Xing（少数段）。

    与事故同量级（19 段）。篡改后每段容器−解码 gap：44.1k +8 帧 ≈ 0.209s、
    24k +8 帧 ≈ 0.192s（MPEG2 每帧 576 样本）。**单次 concat 内采样率统一**：
    本机 ffmpeg 9 对 concat 流中途变采样率会触发滤镜重配 bug
    （inadequate AVFrame plane padding → rc≠0），矩阵的采样率多样性靠跨用例
    覆盖（44.1k/24k 两个用例）。
    """
    chunks: list[str] = []
    for i in range(count):
        p = tmp_path / f"narration_chunk_{i:04d}_aligned.mp3"
        if i % 5 == 4:
            _make_tone(p, sample_rate=sample_rate, seconds=2.0, freq=520 + i * 31, xing=False)
        else:
            _make_tone(p, sample_rate=sample_rate, seconds=2.0, freq=440 + i * 53, xing=True)
            _inflate_xing_frame_count(p, 8)
        chunks.append(str(p))
    return chunks


# ── 1. 完整拼接：容器/解码口径分叉不得误杀 ────────────────────────────────


def test_complete_chunks_container_decode_gap_not_falsely_truncated(tmp_path):
    """事故回归：容器口径 Σchunk−output 差 >0.5s 但解码口径一致 → 不得 raise。"""
    chunks = _build_gap_matrix(tmp_path)

    # 前置：矩阵确实构造出了「容器 > 解码」差异（防 ffmpeg 未来归一化后测试空心化）
    gaps = [_container_duration(p) - _decode_duration(p) for p in chunks]
    inflated = [g for i, g in enumerate(gaps) if i % 5 != 4]
    assert sum(inflated) > 1.0, f"篡改分片的容器−解码 gap 之和应 >1.0s，实测 Σ={sum(inflated):.3f}s"

    output = str(tmp_path / "vimax_narration.mp3")
    ng._concat_chunks(chunks, output)  # 旧容器口径下这里 raise → RED；修复后不 raise

    assert Path(output).stat().st_size > 0
    # 后置断言按生产契约（单侧）：完整输入不 raise + 产物解码不短于 Σchunk−0.5
    # （截断守卫语义）+ 偏长上限防荒谬值。**不得用双侧等值断言**：CI 的 ffmpeg
    # 6/7 在 concat 路径不逐段裁 LAME delay/padding（standalone 解码裁），19 段
    # 可差 ~0.58s；ffmpeg 5.1.9（生产）与 9（macOS）两侧都裁、差 ~0.05s。方向上
    # concat 产物只会偏长（overshoot=告警不 raise），截断守卫（偏短才 raise）不
    # 受该版本差影响——版本差不会让生产误杀（CI 实测：Σ=37.710 output=38.290）。
    expected = sum(_decode_duration(p) for p in chunks)
    actual = _decode_duration(output)
    assert actual >= expected - 0.5, f"产物解码时长不得明显短于分片之和：Σchunk={expected:.3f}s output={actual:.3f}s"
    assert actual <= expected + 1.5, f"产物解码时长异常偏长：Σchunk={expected:.3f}s output={actual:.3f}s"


def test_concat_output_decode_matches_chunk_decode_sum(tmp_path):
    """拼接产物解码时长 ≈ 分片解码时长之和（不是「跳过校验换通过」）。

    用 24k 采样率覆盖矩阵的第二种采样率（单次 concat 内统一，见 _build_gap_matrix）。
    """
    chunks = _build_gap_matrix(tmp_path, count=6, sample_rate=24000)
    output = str(tmp_path / "vimax_narration.mp3")
    ng._concat_chunks(chunks, output)
    expected = sum(_decode_duration(p) for p in chunks)
    actual = _decode_duration(output)
    assert actual == pytest.approx(expected, abs=0.5)


# ── 2. 真实损坏仍然 loud-fail ─────────────────────────────────────────────


def test_true_byte_truncation_still_raises(tmp_path):
    """真实半截输入（concat demuxer rc=0 静默截断场景）必须 raise。"""
    chunks = []
    for i in range(3):
        p = tmp_path / f"chunk_{i}.mp3"
        _make_tone(p, sample_rate=44100, seconds=2.0, freq=440 + i * 100)
        chunks.append(str(p))
    # 字节级截断最后一段到 40%——ffmpeg concat rc=0 但丢 ~1.2s 音频（P1-6 场景）
    last = Path(chunks[-1])
    data = last.read_bytes()
    last.write_bytes(data[: int(len(data) * 0.4)])

    output = str(tmp_path / "output.mp3")
    with pytest.raises(RuntimeError, match="truncated"):
        ng._concat_chunks(chunks, output)


def test_zero_byte_input_still_raises(tmp_path):
    """零字节输入必须 raise（与既有 test_vimax_silence_never_empty 同语义，中间位）。"""
    chunks = []
    for i in range(4):
        p = tmp_path / f"chunk_{i}.mp3"
        if i == 2:
            p.write_bytes(b"")
        else:
            _make_tone(p, sample_rate=24000, seconds=2.0, freq=440)
        chunks.append(str(p))
    output = str(tmp_path / "output.mp3")
    with pytest.raises(RuntimeError):
        ng._concat_chunks(chunks, output)


# ── 3. 校验失败先于 chunk cleanup / SRT 写出 ──────────────────────────────


@pytest.mark.asyncio
async def test_concat_raise_precedes_chunk_cleanup_and_srt(tmp_path):
    """concat 异常必须发生在分片清理与 SRT 写出之前（调用序结构断言）。"""
    working = tmp_path / "vimax_working"
    working.mkdir()
    (working / "storyboard.json").write_text(
        json.dumps(
            [
                {"idx": 0, "is_last": False, "cam_idx": 0, "visual_desc": "远景", "audio_desc": "第一句旁白"},
                {"idx": 1, "is_last": True, "cam_idx": 0, "visual_desc": "近景", "audio_desc": "第二句旁白"},
            ]
        )
    )

    async def fake_synthesize(text, *, voice_id, speed, output_path, **kwargs):
        # 写真实可解码 mp3：让对齐等前序 ffmpeg 步骤跑真路径，唯独 concat 被引爆
        _make_tone(Path(output_path), sample_rate=24000, seconds=1.0, freq=440)
        return MagicMock(audio_path=output_path)

    with patch("app.gateway.video_gen.engines.vimax.narration_generator.tts_mod.synthesize", side_effect=fake_synthesize):
        with patch.object(ng, "_concat_chunks", side_effect=RuntimeError("narration concat truncated: boom")):
            with pytest.raises(RuntimeError, match="concat truncated"):
                await ng.generate_vimax_narration(
                    working_dir=working,
                    task_dir=tmp_path,
                    original_script="原始故事",
                    llm=MagicMock(),
                    voice_id="doubao:zh_male_A",
                )

    chunk_files = list(tmp_path.glob("narration_chunk_*"))
    assert chunk_files, "concat 失败时分片必须仍在磁盘上（cleanup 未执行）"
    assert not (tmp_path / "subtitle.srt").exists(), "SRT 只能在拼接成功后写出"


# ── 4. 无法探测不得当成功 ─────────────────────────────────────────────────


def test_probe_failure_not_treated_as_success(tmp_path, monkeypatch):
    """探测失败（返回 0）不得被当成校验通过——expected 不可得时拒绝交付。"""
    chunks = []
    for i in range(2):
        p = tmp_path / f"chunk_{i}.mp3"
        _make_tone(p, sample_rate=44100, seconds=1.0, freq=440 + i * 100)
        chunks.append(str(p))
    output = str(tmp_path / "output.mp3")
    # 双口径探针全部「失明」：模拟探测不可得
    monkeypatch.setattr(ng, "_container_duration", lambda p: 0.0)
    monkeypatch.setattr(ng.tts_mod, "_get_audio_duration", lambda p: 0.0)
    with pytest.raises(RuntimeError):
        ng._concat_chunks(chunks, output)


# ── Plan A Task 4：行为级验收——完整 19 段真实 concat 后 SRT 正常写出 ────────


@pytest.mark.asyncio
async def test_full_19_chunk_real_concat_then_srt_written(tmp_path):
    """19 段真实 mp3（真实 ffmpeg 对齐+拼接，不 mock run_ffmpeg）→ 产物 + SRT 均落盘。

    事故形态的反面：完整旁白不再在 concat 校验被误杀，后续 SRT/交付继续走。
    """
    import json as _json

    working = tmp_path / "vimax_working"
    working.mkdir()
    shots = []
    for i in range(19):
        shots.append(
            {
                "idx": i,
                "is_last": i == 18,
                "cam_idx": 0,
                "visual_desc": f"画面 {i}",
                "audio_desc": (f'[Speaker] 主讲人: "第 {i} 点。"' if i % 2 == 0 else f"过渡旁白 {i}。"),
            }
        )
    (working / "storyboard.json").write_text(_json.dumps(shots))
    characters = [{"identifier_in_scene": "主讲人", "static_features": "年轻女性"}]

    async def fake_synthesize(text, *, voice_id, speed, output_path, **kwargs):
        # 真实可解码 mp3——对齐/拼接走真实 ffmpeg
        _make_tone(Path(output_path), sample_rate=44100, seconds=0.6, freq=440)
        return MagicMock(audio_path=output_path)

    from unittest.mock import AsyncMock as _AsyncMock

    fake_llm = _AsyncMock()
    with patch("app.gateway.video_gen.engines.vimax.narration_generator.tts_mod.synthesize", side_effect=fake_synthesize):
        result = await ng.generate_vimax_narration(
            working_dir=working,
            task_dir=tmp_path,
            original_script="课程脚本",
            llm=fake_llm,
            voice_id="doubao:zh_male_A",
            characters=characters,
        )

    assert result == tmp_path / "vimax_narration.mp3", "完整旁白必须成功交付（不再被 concat 校验误杀）"
    narration = tmp_path / "vimax_narration.mp3"
    assert narration.exists() and narration.stat().st_size > 0
    srt = tmp_path / "subtitle.srt"
    assert srt.exists() and srt.stat().st_size > 0, "SRT 必须在拼接成功后写出"
    assert _decode_duration(str(narration)) > 19 * 0.5, "旁白应包含全部镜头音频"


# ── 边界收口 Task 6（P2）：零字节 chunk 任意位置 raise ─────────────────────


@pytest.mark.parametrize("pos", ["first", "middle", "last"])
def test_zero_byte_chunk_any_position_raises(tmp_path, pos):
    """[P2] 首位/末位零字节不再依赖 ffmpeg 位置容忍度——「无法校验不得放行」。"""
    chunks = []
    for i in range(3):
        p = tmp_path / f"chunk_{i}.mp3"
        if (pos == "first" and i == 0) or (pos == "middle" and i == 1) or (pos == "last" and i == 2):
            p.write_bytes(b"")
        else:
            _make_tone(p, sample_rate=44100, seconds=1.0, freq=440 + i * 100)
        chunks.append(str(p))
    output = str(tmp_path / "out.mp3")
    with pytest.raises(RuntimeError):
        ng._concat_chunks(chunks, output)
