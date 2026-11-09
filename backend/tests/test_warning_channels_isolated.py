"""行为锁：一路质量告警读取失败，不得连累其余各路。

routers/video_gen.py 里一个 `except Exception: pass` 包住块内全部六路告警。
同文件顶部的注释记录了这已经发生过一次全面失明（payload 未定义 → NameError
被吞 → 所有质量告警静默丢失）。

另：ai_image_i2i_warning 由 service.py 的 _warn_if_image_provider_not_i2i
写入 sidecar，但全仓零读取——从写下的第一天起就从未到达用户。
"""

import json
import types

from app.gateway.routers.video_gen import _safe_warning, _task_with_thumbnail


def test_exception_in_one_channel_does_not_affect_others():
    results: list[str] = []

    _safe_warning("ok1", lambda: results.append("ok1"))
    _safe_warning("bad", lambda: (_ for _ in ()).throw(NameError("simulated")))
    _safe_warning("ok2", lambda: results.append("ok2"))

    assert results == ["ok1", "ok2"], "ok2 不应因 bad 通道异常而跳过"


def test_no_exception_passes_through_normally():
    called = []
    _safe_warning("x", lambda: called.append(1))
    assert called == [1]


def test_exception_is_logged_not_swallowed(caplog):
    import logging

    with caplog.at_level(logging.WARNING):
        _safe_warning("failing", lambda: 1 / 0)
    assert "failing" in caplog.text.lower() or "ZeroDivision" in caplog.text


def _fake_task(tmp_path, result: dict, status: str = "ready"):
    return types.SimpleNamespace(
        id="vg_iso1",
        user_id="default",
        domain="video_gen",
        task_type="ai_image",
        status=status,
        progress=100,
        result=json.dumps(result, ensure_ascii=False),
        payload=json.dumps({"topic": "t"}, ensure_ascii=False),
    )


def test_i2i_warning_reaches_the_api(tmp_path):
    """ai_image_i2i_warning sidecar 必须被读出——此前写了但零读取。"""
    task_dir = tmp_path / "default" / "tasks" / "vg_iso1"
    task_dir.mkdir(parents=True)
    (task_dir / "ai_image_i2i_warning.json").write_text(
        json.dumps({"warning": "当前 provider 不支持参考图（i2i），角色一致性未生效"}, ensure_ascii=False),
        encoding="utf-8",
    )

    brief = _task_with_thumbnail(_fake_task(tmp_path, {}), storage_dir=str(tmp_path), user_id="default")

    assert "i2i" in (brief.result.get("ai_image_i2i_warning") or "")


def test_one_corrupt_sidecar_does_not_blind_other_channels(tmp_path):
    """真实路径的隔离验证：坏 sidecar 不得吞掉其余通道。

    只测 _safe_warning 本身不够——那只证明 helper 可用，
    不证明 _task_with_thumbnail 真的逐通道调用了它。
    """
    task_dir = tmp_path / "default" / "tasks" / "vg_iso1"
    task_dir.mkdir(parents=True)
    # 坏 JSON：该通道必抛
    (task_dir / "ai_image_i2i_warning.json").write_text("{ not json", encoding="utf-8")
    # 另一路完好：必须仍然读出
    (task_dir / "voice_mode_warning.txt").write_text("单音色旁白", encoding="utf-8")

    brief = _task_with_thumbnail(_fake_task(tmp_path, {}), storage_dir=str(tmp_path), user_id="default")

    assert "单音色旁白" in (brief.result.get("voice_mode_warning") or ""), "完好通道被坏通道连累"
    assert brief.result.get("ai_image_i2i_warning") is None


def test_all_channels_coexist(tmp_path):
    """多路同时有信号时全部读出（不是只读到第一路）。"""
    task_dir = tmp_path / "default" / "tasks" / "vg_iso1"
    task_dir.mkdir(parents=True)
    (task_dir / "ai_image_i2i_warning.json").write_text(json.dumps({"warning": "i2i 未生效"}, ensure_ascii=False), encoding="utf-8")
    (task_dir / "voice_mode_warning.txt").write_text("单音色旁白", encoding="utf-8")
    (task_dir / "placeholder_frames.json").write_text(json.dumps({"indices": [0], "total": 2}), encoding="utf-8")

    brief = _task_with_thumbnail(_fake_task(tmp_path, {}), storage_dir=str(tmp_path), user_id="default")

    assert brief.result.get("ai_image_i2i_warning")
    assert brief.result.get("voice_mode_warning")
    assert brief.result.get("placeholder_warning")
