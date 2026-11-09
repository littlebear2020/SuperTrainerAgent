"""E7: _resolve_bgm 在后台任务中须尊重显式传入的 user_id（custom BGM 不再静默丢）。"""

from pathlib import Path
from types import SimpleNamespace

import pytest


def _isolate_deer_flow_home(tmp_path, monkeypatch):
    """C8 后 BGM 目录锚定 get_paths().base_dir——DEER_FLOW_HOME + 单例重置隔离。"""
    from deerflow.config import paths as paths_mod

    monkeypatch.setenv("DEER_FLOW_HOME", str(tmp_path))
    monkeypatch.setattr(paths_mod, "_paths", None)


def _seed_user_bgm(root: Path, user: str, stem: str, ext: str = "mp3") -> Path:
    d = root / "video-gen" / "user_bgm" / user  # C8：BGM 目录锚定 DEER_FLOW_HOME（基址即 env 值）
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{stem}.{ext}"
    f.write_bytes(b"bgm")
    return f


def test_resolve_bgm_custom_uses_explicit_user_id(tmp_path, monkeypatch):
    """显式 user_id='u1' → 查 u1 目录（不依赖 contextvar）；其他 user 查不到。"""
    _isolate_deer_flow_home(tmp_path, monkeypatch)
    _seed_user_bgm(tmp_path, "u1", "custom_x")

    from app.gateway.video_gen.service import _resolve_bgm

    # 关键：传入 user_id="u1" 必须命中 u1 的 custom BGM（修前忽略入参、走 contextvar → 丢）
    got = _resolve_bgm("custom_x", user_id="u1")
    assert got is not None, "显式 user_id='u1' 必须解析到 u1 的 custom BGM"
    assert Path(got).parent.name == "u1"
    # 另一用户查不到（不跨用户泄漏）
    assert _resolve_bgm("custom_x", user_id="u2") is None


@pytest.mark.no_auto_user
def test_resolve_bgm_custom_falls_back_to_contextvar(tmp_path, monkeypatch):
    """user_id='default' → 回退 contextvar（保持旧行为，前台请求仍可用）。"""
    from deerflow.runtime.user_context import reset_current_user, set_current_user

    _isolate_deer_flow_home(tmp_path, monkeypatch)
    _seed_user_bgm(tmp_path, "ctxuser", "custom_y")

    token = set_current_user(SimpleNamespace(id="ctxuser", email="c@local"))
    try:
        from app.gateway.video_gen.service import _resolve_bgm

        got = _resolve_bgm("custom_y", user_id="default")
        assert got is not None, "user_id='default' 应回退 contextvar 解析"
        assert Path(got).parent.name == "ctxuser"
    finally:
        reset_current_user(token)
