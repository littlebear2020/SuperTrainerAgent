import asyncio
import inspect
import os
from pathlib import Path

from app.gateway.video_gen import character_sheet_generator as csg
from app.gateway.video_gen.providers.seedream import _to_data_uri


def test_generate_character_sheet_localizes_urls(monkeypatch, tmp_path):
    """#7: 方式 A 须把 provider URL 下载落盘，front_url 是本地路径非 TOS URL。"""

    # 桩 image_gen 返回带 .url + .bytes 的假产物
    class FakeImg:
        def __init__(self, tag):
            self.url = f"https://ark-tos.example.com/{tag}.png?expires=24h"
            self.bytes = b"\x89PNG fake"

    async def fake_gen(prompt, *, reference_image=None):
        return FakeImg(prompt.split()[0])

    # 桩下载：把 img.bytes 写到 storage_dir
    async def fake_download(url, dest):
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(b"\x89PNG fake")

    monkeypatch.setattr(csg, "_download_image_bytes", fake_download, raising=False)

    sheet = asyncio.run(csg.generate_character_sheet(description="x", name="n", features="f", image_gen=fake_gen, storage_dir=str(tmp_path), project_id="p1"))
    # front_url 须是本地路径（含 character_sheets/p1），非 http URL
    assert "character_sheets/p1" in sheet.front_url and not sheet.front_url.startswith("http"), f"方式 A 须落盘本地、不存 TOS URL（got {sheet.front_url})"


def test_generate_character_sheet_source_has_localize():
    """#7: generate_character_sheet 源码须含本地落盘逻辑（_download_image_bytes / _save）。"""
    src = inspect.getsource(csg.generate_character_sheet)
    assert "_download_image_bytes" in src or "_save" in src, "须下载 provider URL bytes 落盘"


def test_generate_character_sheet_returns_absolute_resolvable_path(monkeypatch, tmp_path):
    """A1: 方式 A front_url 须是绝对路径且 _to_data_uri 能解析（修前是相对→os.path.exists False→原样塞 Ark→4xx）。"""
    storage = tmp_path / "vg"
    (storage / "character_sheets" / "p1").mkdir(parents=True)

    class FakeImg:
        def __init__(self, t):
            self.url = f"https://tos.example/{t}.png"
            self.bytes = b"\x89PNG fake"

    async def fake_gen(prompt, *, reference_image=None):
        return FakeImg(prompt[:1])

    async def fake_dl(url, dest):
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(b"\x89PNG fake")

    monkeypatch.setattr(csg, "_download_image_bytes", fake_dl, raising=False)

    sheet = asyncio.run(csg.generate_character_sheet(description="x", name="n", features="f", image_gen=fake_gen, storage_dir=str(storage), project_id="p1"))

    # 绝对 + 可解析（关键：修前相对路径这里 os.path.exists=False → _to_data_uri 原样返回非 data-URI）。
    # 下行 _to_data_uri 真转换断言是堵上轮盲区的关键——勿退回 inspect.getsource 字符串断言。
    assert os.path.isabs(sheet.front_url), f"须绝对路径，got {sheet.front_url}"
    assert os.path.exists(sheet.front_url), f"文件须存在，got {sheet.front_url}"
    uri = _to_data_uri(sheet.front_url)
    assert uri is not None and uri.startswith("data:image/png;base64,"), f"_to_data_uri 须转成 data-URI（修前会原样返回相对串），got {uri!r}"
