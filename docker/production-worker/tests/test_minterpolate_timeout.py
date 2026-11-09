"""C5 P2-F（2026-08-14）：minterpolate 超时动态核算——租约不变量。

单阻塞 subprocess 的 timeout 必须 ≤ 剩余租约 − 300（缓冲），且夹在 [60, 300]：
- 无租约信息（lease_remaining=None）→ 旧行为 300（向后兼容）
- lease_remaining=400 → 100
- 上限 300（租约足够长时不放大超时）
- 下限 60（租约将尽时仍给一个友好的超时而非秒退）
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from pipelines import _ffmpeg_helpers as H  # noqa: E402


def test_no_lease_info_returns_legacy_300() -> None:
    assert H._interp_timeout(None) == 300


def test_zero_lease_is_not_none() -> None:
    """评审 Minor #2：0.0 是「租约已耗尽」而非「无信息」——不得回退 300。"""
    assert H._interp_timeout(0.0) == 60


def test_lease_400_yields_100() -> None:
    assert H._interp_timeout(400) == 100


def test_long_lease_caps_at_300() -> None:
    assert H._interp_timeout(1200) == 300


def test_short_lease_floors_at_60() -> None:
    assert H._interp_timeout(330) == 60
    assert H._interp_timeout(300) == 60  # 租约−300=0 → 下限兜底


def test_extend_clip_passes_interp_timeout_to_subprocess(monkeypatch, tmp_path) -> None:
    """B-3: lease_remaining=400 → minterpolate 的 subprocess.run 真收到 timeout=100。

    透传链此前只靠签名审查保证（_interp_timeout 纯函数测试测不到 :1202 的调用点）。
    构造：actual=5 < target=6 → gap_ratio=1/6≤0.50 且 slowmo_ratio=0.2>0.10
    （跳过 Tier 1）→ 直接进 minterpolate 分支。
    """
    from types import SimpleNamespace

    clip = tmp_path / "clip.mp4"
    clip.write_bytes(b"x")
    captured: list[dict] = []

    def fake_run(cmd, **kwargs):
        captured.append({"cmd": list(cmd), **kwargs})
        return SimpleNamespace(returncode=1, stdout=b"", stderr=b"")

    monkeypatch.setattr(H.subprocess, "run", fake_run)
    monkeypatch.setattr(H, "_probe_duration", lambda p: 5.0)

    try:
        H._ffmpeg_extend_clip_to_duration(
            clip,
            6.0,
            tmp_path / "out.mp4",
            config={"interp_enabled": True},
            lease_remaining=400,
        )
    except RuntimeError:
        pass  # 全 Tier 失败会抛（docstring 契约）——本用例只关心 interp 调用参数

    interp_calls = [c for c in captured if any("minterpolate" in str(a) for a in c["cmd"])]
    assert interp_calls, "应走到 minterpolate 分支（interp_enabled 且 gap_ratio<=0.50）"
    assert all(c["timeout"] == 100 for c in interp_calls), [c["timeout"] for c in interp_calls]
