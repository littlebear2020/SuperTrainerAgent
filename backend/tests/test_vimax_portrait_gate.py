"""Task 3o: ViMax 角色画像质量门禁——portrait 差则标记重生/引导上传。"""

import pytest
from PIL import Image
from vimax.agents.character_portraits_generator import check_portrait_quality, generate_portrait_with_gate


def test_low_resolution_rejected():
    r = check_portrait_quality("/bad.png", width=256, height=256)
    assert r["acceptable"] is False
    assert "resolution" in r["reasons"]


def test_good_portrait_passes():
    r = check_portrait_quality("/good.png", width=1024, height=1024)
    assert r["acceptable"] is True
    assert r["reasons"] == []


def test_missing_url_rejected():
    r = check_portrait_quality("", width=1024, height=1024)
    assert r["acceptable"] is False
    assert "missing_url" in r["reasons"]


def test_bad_aspect_rejected():
    r = check_portrait_quality("/wide.png", width=2048, height=512)
    assert r["acceptable"] is False
    assert "aspect_ratio" in r["reasons"]


# ── 2026-07-22 Task 7: 门禁接线——generate_portrait_with_gate ──────────────


class _FakeImageOutput:
    """Minimal stand-in for vimax.interfaces.ImageOutput（避免 cv2 重依赖的构造）。"""

    def __init__(self, size):
        self._size = size

    def save(self, path: str) -> None:
        Image.new("RGB", self._size, color=(64, 64, 64)).save(path)


@pytest.mark.asyncio
async def test_gate_accepts_good_portrait_first_try(tmp_path, monkeypatch):
    # 人脸校验（T17 vision 调用）在本用例关闭——这里只锁分辨率门禁行为，保持 hermetic
    monkeypatch.setenv("VIMAX_PORTRAIT_FACE_CHECK", "0")
    calls = []

    async def gen():
        calls.append(1)
        return _FakeImageOutput((1024, 1024))

    gate = await generate_portrait_with_gate(gen, str(tmp_path / "front.png"), identifier="小明", view="front")
    assert gate["acceptable"] is True
    assert gate["reasons"] == []
    assert len(calls) == 1  # 一次通过，不多花生图


@pytest.mark.asyncio
async def test_gate_regenerates_once_on_reject_then_accepts(tmp_path, monkeypatch):
    monkeypatch.setenv("VIMAX_PORTRAIT_FACE_CHECK", "0")
    sizes = iter([(256, 256), (1024, 1024)])

    async def gen():
        return _FakeImageOutput(next(sizes))

    gate = await generate_portrait_with_gate(gen, str(tmp_path / "front.png"), identifier="小明", view="front")
    assert gate["acceptable"] is True  # 第二次通过


@pytest.mark.asyncio
async def test_gate_warn_continues_after_max_attempts(tmp_path):
    calls = []

    async def gen():
        calls.append(1)
        return _FakeImageOutput((100, 100))

    gate = await generate_portrait_with_gate(gen, str(tmp_path / "front.png"), identifier="小明", view="front")
    assert gate["acceptable"] is False
    assert "resolution" in gate["reasons"]
    assert len(calls) == 2  # max_attempts 默认 2：拒绝只重生一次，不无限烧生图预算


# ── 2026-07-27 T17：分辨率合规 ≠ 有脸——front 肖像追加 vision 人脸校验 ──────


def _make_test_image(tmp_path, w=1024, h=1024):
    path = tmp_path / "portrait.png"
    Image.new("RGB", (w, h), (200, 200, 200)).save(path)
    return str(path)


def _async_returning(value):
    async def _inner(_path):
        return value

    return _inner


def _copy_out(src):
    class _Out:
        def save(self, path):
            import shutil

            shutil.copy(src, path)

    return _Out


@pytest.mark.asyncio
async def test_gate_retries_when_no_face(tmp_path, monkeypatch):
    """分辨率合规但无人脸 → 按拒绝走重试，reasons 含 no_face（曾只查分辨率直接放行）。"""
    monkeypatch.delenv("VIMAX_PORTRAIT_FACE_CHECK", raising=False)
    from vimax.agents import character_portraits_generator as cpg

    img = _make_test_image(tmp_path)
    monkeypatch.setattr(cpg, "_face_present_async", _async_returning(False))

    calls = []

    async def _gen():
        calls.append(1)
        return _copy_out(img)()

    result = await cpg.generate_portrait_with_gate(_gen, str(tmp_path / "out.png"), identifier="测试角色", view="front")
    assert len(calls) == 2, "无人脸应触发一次重生"
    assert result["acceptable"] is False
    assert "no_face" in result["reasons"]


@pytest.mark.asyncio
async def test_gate_accepts_when_face_check_unavailable(tmp_path, monkeypatch):
    """人脸校验不可用（None）→ 放行 + 不重生（degraded completion 哲学）。"""
    monkeypatch.delenv("VIMAX_PORTRAIT_FACE_CHECK", raising=False)
    from vimax.agents import character_portraits_generator as cpg

    img = _make_test_image(tmp_path)
    monkeypatch.setattr(cpg, "_face_present_async", _async_returning(None))

    calls = []

    async def _gen():
        calls.append(1)
        return _copy_out(img)()

    result = await cpg.generate_portrait_with_gate(_gen, str(tmp_path / "out.png"), identifier="测试角色", view="front")
    assert len(calls) == 1
    assert result["acceptable"] is True


@pytest.mark.asyncio
async def test_face_check_disabled_by_env(tmp_path, monkeypatch):
    """VIMAX_PORTRAIT_FACE_CHECK=0 → 完全不调人脸校验（回退旧行为）。"""
    monkeypatch.setenv("VIMAX_PORTRAIT_FACE_CHECK", "0")
    from vimax.agents import character_portraits_generator as cpg

    called = []

    async def _spy(path):
        called.append(path)
        return False  # 即使会拒也不该被调

    monkeypatch.setattr(cpg, "_face_present_async", _spy)
    img = _make_test_image(tmp_path)

    async def _gen():
        return _copy_out(img)()

    result = await cpg.generate_portrait_with_gate(_gen, str(tmp_path / "out.png"), identifier="测试角色", view="front")
    assert result["acceptable"] is True and called == []
