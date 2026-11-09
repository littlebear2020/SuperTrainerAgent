"""B1: 角色图生成注入项目 style_prefix。

`generate_character_sheet` 当前完全无视 `style_prefix`，导致「项目视觉风格预设」对生成的
角色三视图无效。注入后，三个视图 prompt 均前置 style_prefix（空则不前置、无前导逗号）。
"""

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.gateway.video_gen import character_sheet_generator as csg
from app.gateway.video_gen.character_sheet_generator import generate_character_sheet


def _cap():
    """image_gen mock：async、返回带 .url 的对象（真实代码 await 它 + 取 .url + _download）。"""
    calls: list[str] = []

    async def g(prompt, **kw):  # noqa: ANN001,ANN003
        calls.append(prompt)
        return SimpleNamespace(url="http://x/test.png")

    return g, calls


def test_style_prefix_prepended_to_all_three_views() -> None:
    g, calls = _cap()

    async def _noop(*a, **k):  # noqa: ANN001,ANN003
        return None

    with patch.object(csg, "_download_image_bytes", _noop):
        asyncio.run(
            generate_character_sheet(
                description="红发骑士",
                name="k",
                features="红发,铠甲",
                image_gen=g,
                storage_dir="/tmp",
                project_id="p1",
                style_prefix="cyberpunk neon, cinematic",
            )
        )
    assert len(calls) == 3
    assert all("cyberpunk neon" in c for c in calls), calls
    assert all("红发骑士" in c for c in calls), calls


def test_empty_style_prefix_has_no_leading_comma() -> None:
    g, calls = _cap()

    async def _noop(*a, **k):  # noqa: ANN001,ANN003
        return None

    with patch.object(csg, "_download_image_bytes", _noop):
        asyncio.run(
            generate_character_sheet(
                description="x",
                name="k",
                features="y",
                image_gen=g,
                storage_dir="/tmp",
                project_id="p1",
                style_prefix="",
            )
        )
    assert calls, "expected image_gen to be called"
    assert not calls[0].startswith(","), calls[0]


def test_generate_unique_filenames_across_calls() -> None:
    """Bug 3: 方式 A 多次生成应产出不同文件名（修前固定 front.png 会互相覆盖）。"""
    import asyncio
    import re
    from types import SimpleNamespace
    from unittest.mock import patch

    from app.gateway.video_gen import character_sheet_generator as csg
    from app.gateway.video_gen.character_sheet_generator import generate_character_sheet

    async def _noop(*a, **k):  # noqa: ANN001,ANN003
        return None

    async def g(prompt, **kw):  # noqa: ANN001,ANN003
        return SimpleNamespace(url="http://x/test.png")

    with patch.object(csg, "_download_image_bytes", _noop):
        s1 = asyncio.run(
            generate_character_sheet(
                description="a",
                name="k",
                features="f",
                image_gen=g,
                storage_dir="/tmp",
                project_id="p1",
                style_prefix="",
            )
        )
        s2 = asyncio.run(
            generate_character_sheet(
                description="a",
                name="k",
                features="f",
                image_gen=g,
                storage_dir="/tmp",
                project_id="p1",
                style_prefix="",
            )
        )
    assert s1.front_url != s2.front_url, f"front_url 应唯一，实际 {s1.front_url} == {s2.front_url}"
    assert s1.side_url != s2.side_url and s1.back_url != s2.back_url
    assert re.search(r"front_[0-9a-f]{8}\.png$", s1.front_url), s1.front_url


def test_save_uploaded_with_only_front(tmp_path) -> None:
    """U1: side/back 可选——仅 front 时 side_url/back_url 为空串。"""
    import asyncio

    from app.gateway.video_gen.character_sheet_generator import save_uploaded_character_sheet

    sheet = asyncio.run(
        save_uploaded_character_sheet(
            name="k",
            features="f",
            front_bytes=b"\x89PNG\r\n",
            storage_dir=str(tmp_path),
            project_id="p1",
        )
    )
    assert sheet.front_url and sheet.front_url.endswith(".png")
    assert sheet.side_url == ""
    assert sheet.back_url == ""


def test_download_image_bytes_handles_data_uri():
    """B3 镜像补全：provider 返回 data: URI（同一链路已实测发生过）→ 就地解码落盘。

    修前裸 httpx.get 遇 data: 抛 UnsupportedProtocol，三视图生成直接 500。
    命名契约维持 .png（serve glob 假定）；bytes 原样写入。
    """
    import base64
    import tempfile
    from pathlib import Path

    payload = base64.b64encode(b"\x89PNG\r\n\x1a\nfakebytes").decode()
    with tempfile.TemporaryDirectory() as td:
        dest = Path(td) / "front_ab12cd34.png"
        asyncio.run(csg._download_image_bytes(f"data:image/png;base64,{payload}", dest))
        assert dest.read_bytes() == b"\x89PNG\r\n\x1a\nfakebytes"

        # jpeg data URI 同样落盘（后缀契约不动，魔数消费方不受影响）
        j = base64.b64encode(b"\xff\xd8\xff\xe0jpeg").decode()
        dest2 = Path(td) / "side_ab12cd35.png"
        asyncio.run(csg._download_image_bytes(f"data:image/jpeg;base64,{j}", dest2))
        assert dest2.read_bytes().startswith(b"\xff\xd8\xff")


# ── 方案 A（2026-08-31）：side/back 并发生成（front 保持串行——被依赖） ──────


def _view_label(prompt: str) -> str:
    if "front view" in prompt:
        return "front"
    return "side" if "side view" in prompt else "back"


def test_side_back_concurrent_and_depend_on_front() -> None:
    """side 与 back 调用区间重叠（并发）且都在 front 完成后启动。

    事件会合断言，不依赖真实时钟 sleep 计时：side/back 互相等到对方**已启动**
    才允许自己结束——并发实现双双到达会合点；若实现退化为串行，先到者在
    wait_for 超时处红（挂死转成干净失败）。同时断言二者 reference 均 = front
    落盘路径（依赖方向不变：并发的是兄弟视图，不是 front）。
    """
    events: list[tuple[str, str]] = []
    started = {"side": asyncio.Event(), "back": asyncio.Event()}
    refs: list[tuple[str, str | None]] = []
    downloads: list[str] = []

    async def g(prompt, *, reference_image=None):  # noqa: ANN001, ANN003
        label = _view_label(prompt)
        events.append(("start", label))
        refs.append((label, reference_image))
        if label in ("side", "back"):
            assert ("end", "front") in events, "side/back 依赖 front 作 reference，必须在 front 完成后启动"
            other = "back" if label == "side" else "side"
            started[label].set()
            # 会合点：等兄弟视图也启动（调用区间因此必然重叠）
            await asyncio.wait_for(started[other].wait(), timeout=2.0)
        events.append(("end", label))
        return SimpleNamespace(url=f"http://x/{label}.png")

    async def _dl(url, dest):  # noqa: ANN001
        downloads.append(str(dest))

    with patch.object(csg, "_download_image_bytes", _dl):
        sheet = asyncio.run(
            generate_character_sheet(
                description="红发骑士",
                name="k",
                features="红发",
                image_gen=g,
                storage_dir="/tmp",
                project_id="p1",
                style_prefix="",
            )
        )
    ends = [label for kind, label in events if kind == "end"]
    assert set(ends) == {"front", "side", "back"} and ends[0] == "front", events
    # front 无 reference；side/back 的 reference 都 = front 落盘路径（首个 download）
    assert refs[0] == ("front", None)
    assert all(ref == downloads[0] for label, ref in refs if label != "front"), refs
    assert sheet.side_url != sheet.back_url, "并发不改变唯一文件名契约"


def test_side_failure_raises_whole_generation() -> None:
    """gather 默认失败语义（一个失败即抛）——与串行现状一致：整体失败，back
    结果不取用（调用已发出属已知计费权衡，见实现注释）。"""
    labels: list[str] = []

    async def g(prompt, *, reference_image=None):  # noqa: ANN001, ANN003
        label = _view_label(prompt)
        labels.append(label)
        if label == "side":
            raise RuntimeError("side boom")
        return SimpleNamespace(url=f"http://x/{label}.png")

    async def _noop(*a, **k):  # noqa: ANN001, ANN003
        return None

    with (
        patch.object(csg, "_download_image_bytes", _noop),
        pytest.raises(RuntimeError, match="side boom"),
    ):
        asyncio.run(
            generate_character_sheet(
                description="d",
                name="k",
                features="f",
                image_gen=g,
                storage_dir="/tmp",
                project_id="p1",
            )
        )
    assert "front" in labels and "side" in labels, labels


def test_front_failure_skips_side_back() -> None:
    """front 失败 → gather 从未到达，side/back 零调用（依赖方向受保护）。"""
    labels: list[str] = []

    async def g(prompt, *, reference_image=None):  # noqa: ANN001, ANN003
        label = _view_label(prompt)
        labels.append(label)
        if label == "front":
            raise RuntimeError("front boom")
        return SimpleNamespace(url=f"http://x/{label}.png")

    async def _noop(*a, **k):  # noqa: ANN001, ANN003
        return None

    with (
        patch.object(csg, "_download_image_bytes", _noop),
        pytest.raises(RuntimeError, match="front boom"),
    ):
        asyncio.run(
            generate_character_sheet(
                description="d",
                name="k",
                features="f",
                image_gen=g,
                storage_dir="/tmp",
                project_id="p1",
            )
        )
    assert labels == ["front"], labels
