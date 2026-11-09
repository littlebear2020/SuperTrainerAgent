"""Task 7 (C9): CAS 音频不得被原地篡改 / 跨帧污染。

两个独立缺陷叠加：

1. **原地覆写**：``_pad_audio_to_duration`` 用 ``padded.replace(path)`` 直接
   **覆盖 CAS 文件本体**。
2. **键不含 spf**：``_audio_cache_key = md5("v2|narration|voice|speed")`` ——
   **没有 seconds_per_frame**。

叠加后果：同一句旁白在 spf=8 的任务里被补静音到 8s 并**写回缓存**；之后 spf=4
的任务命中同一 key，拿到**被污染的 8s 音频** → 画面 4s / 音频 8s，音画错位且
**永久残留**（除非改文案/音色/语速）。

修法（采用计划推荐方案）：padding 产物**另存**为 ``{hash}_pad{spf}.mp3``，CAS
原件只存「纯 TTS 产物」不被回写；padding 是消费侧变换，本就不该进缓存。
"""

import subprocess
from pathlib import Path

import pytest

from app.gateway.video_gen import service as svc


def _has_ffmpeg() -> bool:
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
        return True
    except (OSError, subprocess.CalledProcessError):
        return False


pytestmark = pytest.mark.skipif(not _has_ffmpeg(), reason="需要 ffmpeg")


def _make_audio(path: Path, seconds: float):
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", "-acodec", "libmp3lame", "-q:a", "9", str(path)],
        capture_output=True,
        check=True,
    )
    return path


def test_padding_does_not_mutate_cas_original(tmp_path):
    """补静音不得改动 CAS 原件字节——它是别的 spf 任务要复用的缓存。"""
    cas = _make_audio(tmp_path / "abc123.mp3", 2.0)
    original_bytes = cas.read_bytes()

    padded = svc._pad_audio_to_duration(cas, 8.0)

    assert cas.read_bytes() == original_bytes, "CAS 原件被原地覆写 —— 其他 spf 的任务会拿到被污染的音频"
    assert padded is not None, "补静音应返回新文件路径"
    assert Path(padded) != cas, "补静音产物必须另存，不得覆盖原件"
    assert Path(padded).exists()


def test_padded_variant_has_target_duration(tmp_path):
    """另存的补静音产物时长必须等于目标 spf。"""
    cas = _make_audio(tmp_path / "def456.mp3", 2.0)
    padded = svc._pad_audio_to_duration(cas, 6.0)

    dur = svc._get_audio_duration(str(padded))
    assert dur == pytest.approx(6.0, abs=0.2), f"补静音后 {dur}s != 6.0s"


def test_different_spf_get_independent_files(tmp_path):
    """同一 CAS 原件、两个不同 spf → 各自拿到正确时长，互不污染。

    这是 C9 的核心用例：修前第二个 spf 会命中被第一个 spf 污染的文件。
    """
    cas = _make_audio(tmp_path / "same_narration.mp3", 2.0)

    pad8 = svc._pad_audio_to_duration(cas, 8.0)
    pad4 = svc._pad_audio_to_duration(cas, 4.0)

    assert Path(pad8) != Path(pad4), "不同 spf 必须落到不同文件"
    d8 = svc._get_audio_duration(str(pad8))
    d4 = svc._get_audio_duration(str(pad4))
    assert d8 == pytest.approx(8.0, abs=0.2), f"spf=8 实得 {d8}s"
    assert d4 == pytest.approx(4.0, abs=0.2), f"spf=4 实得 {d4}s（被 spf=8 污染则会是 8s）"


def test_padded_filename_encodes_spf(tmp_path):
    """产物命名须带 spf，使不同 spf 天然不撞。"""
    cas = _make_audio(tmp_path / "hash789.mp3", 1.0)
    padded = Path(svc._pad_audio_to_duration(cas, 5.0))
    assert "hash789" in padded.name, "应保留原 CAS hash 便于追溯"
    assert "5" in padded.name, f"文件名未体现 spf：{padded.name}"


def test_pad_failure_returns_original(tmp_path):
    """补静音失败时返回原件路径（不阻断出片），且原件仍完好。"""
    bad = tmp_path / "not_audio.mp3"
    bad.write_bytes(b"this is not audio")

    result = svc._pad_audio_to_duration(bad, 5.0)

    assert Path(result) == bad, "失败应回落原件，让出片继续"
    assert bad.read_bytes() == b"this is not audio", "失败不得破坏原件"
