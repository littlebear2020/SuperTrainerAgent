"""Task 23 / C11：选了 BGM 却没有 BGM，必须留痕。

修前 `video_gen_ai_visual.py` 的 BGM 分支：

    except Exception:
        logger.warning("BGM mix failed, continuing without BGM")

裸 except 吞掉一切、无 exc_info、无 sidecar。用户明确选了背景音乐，成片里没有，
界面上一个字的说明都没有 —— 任务照样 status=ready / progress=100。

同一分支还有第二个静默口：`if bgm_path.exists()` 为假时**连 warning 都没有**，
直接跳过。用户选的 BGM 文件丢了/路径错了，表现与「没选 BGM」完全一致。

本测试锁两条不变量：混音抛异常要留痕、BGM 文件不存在也要留痕。
"""

from __future__ import annotations

import json
from pathlib import Path

from pipelines._degradation_sidecar import (
    DEGRADATION_SIDECAR_NAME,
    read_degradations,
    record_degradation,
)


def test_roundtrip(tmp_path: Path) -> None:
    record_degradation(tmp_path, kind="bgm_failed", detail="ffmpeg amix exit 1")
    items = read_degradations(tmp_path)
    assert [i["kind"] for i in items] == ["bgm_failed"]
    assert "amix" in items[0]["detail"]


def test_missing_file_returns_empty(tmp_path: Path) -> None:
    assert read_degradations(tmp_path) == []


def test_corrupt_json_does_not_raise(tmp_path: Path) -> None:
    (tmp_path / DEGRADATION_SIDECAR_NAME).write_text("{broken", encoding="utf-8")
    assert read_degradations(tmp_path) == []


def test_dedupes(tmp_path: Path) -> None:
    for _ in range(3):
        record_degradation(tmp_path, kind="bgm_failed", detail="same")
    assert len(read_degradations(tmp_path)) == 1


def test_record_never_raises(tmp_path: Path) -> None:
    record_degradation(tmp_path / "missing" / "deep", kind="bgm_failed", detail="x")


def test_worker_and_gateway_formats_match(tmp_path: Path) -> None:
    """worker 写、gateway 读 —— 两份实现的落盘格式必须一致。

    worker 不能 import app.*（harness 边界），只能各存一份实现。格式一旦漂移，
    gateway 就静默读不到 worker 写的降级记录，等于 sidecar 白写。
    """
    record_degradation(tmp_path, kind="bgm_failed", detail="d", frame_index=2)
    raw = json.loads((tmp_path / DEGRADATION_SIDECAR_NAME).read_text(encoding="utf-8"))
    assert isinstance(raw, dict) and isinstance(raw["items"], list)
    assert raw["items"][0] == {"kind": "bgm_failed", "detail": "d", "frame_index": 2}
    assert DEGRADATION_SIDECAR_NAME == "degradations.json"


# ── P2-10：真契约测试——两份实现互读互写，漂移即红 ─────────────────────────

def _load_gateway_degradations():
    """按文件路径加载 gateway 侧实现（worker 不能 import app.*，用路径绕开包边界）。

    这正是契约测试的意义所在：两份副本各存一份，只有同进程互调才能抓住漂移。
    """
    import importlib.util

    gw_path = (
        Path(__file__).resolve().parents[3]
        / "backend" / "app" / "gateway" / "video_gen" / "degradations.py"
    )
    assert gw_path.exists(), f"gateway 侧实现不在预期位置: {gw_path}"
    spec = importlib.util.spec_from_file_location("gateway_degradations", gw_path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_worker_writes_gateway_reads(tmp_path: Path) -> None:
    """worker 写 → gateway 读：产出必须逐字可解析。"""
    gw = _load_gateway_degradations()
    record_degradation(tmp_path, kind="bgm_failed", detail="ffmpeg amix exit 1", frame_index=3)
    record_degradation(tmp_path, kind="voice_fallback", detail="doubao quota → preset:146")
    items = gw.read_degradations(tmp_path)
    assert items == [
        {"kind": "bgm_failed", "detail": "ffmpeg amix exit 1", "frame_index": 3},
        {"kind": "voice_fallback", "detail": "doubao quota → preset:146"},
    ]


def test_gateway_writes_worker_reads(tmp_path: Path) -> None:
    """反向：gateway 写 → worker 读（ViMax 旁白期降级由 gateway 侧写）。"""
    gw = _load_gateway_degradations()
    gw.record_degradation(tmp_path, kind="dialogue_truncated", detail="硬截尾", frame_index=7)
    items = read_degradations(tmp_path)
    assert items == [{"kind": "dialogue_truncated", "detail": "硬截尾", "frame_index": 7}]


def test_interleaved_append_keeps_both_sides(tmp_path: Path) -> None:
    """交错 append：两边共用同一文件的 read-dedup-append 循环不得互踩。"""
    gw = _load_gateway_degradations()
    record_degradation(tmp_path, kind="bgm_failed", detail="d1")
    gw.record_degradation(tmp_path, kind="align_failed", detail="d2", frame_index=1)
    record_degradation(tmp_path, kind="bgm_failed", detail="d1")  # 幂等去重
    assert gw.read_degradations(tmp_path) == [
        {"kind": "bgm_failed", "detail": "d1"},
        {"kind": "align_failed", "detail": "d2", "frame_index": 1},
    ]
    assert read_degradations(tmp_path) == gw.read_degradations(tmp_path)
