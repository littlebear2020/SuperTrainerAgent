"""资产中心 ppt_template 注册表测试（Phase 3.5，2026-09-11）。

覆盖：确定性蒸馏器（合成 PPTX）、注册表 CRUD、端点（上传/列表/详情/确认/
logo/删除）、「应用到课程项目」的 trainer_brand → CourseProject 品牌字段映射
（§六.B.2 映射 schema 单一事实源）。
"""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.gateway.assets.distiller import distill_pptx, remotion_theme_heuristic
from app.gateway.assets.store import AssetStore

THEME_XML = """<?xml version="1.0" encoding="UTF-8"?>
<a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" name="T">
 <a:themeElements>
  <a:clrScheme name="Brand">
   <a:dk1><a:srgbClr val="0B1220"/></a:dk1>
   <a:lt1><a:srgbClr val="FFFFFF"/></a:lt1>
   <a:dk2><a:srgbClr val="1E293B"/></a:dk2>
   <a:lt2><a:srgbClr val="F1F5F9"/></a:lt2>
   <a:accent1><a:srgbClr val="2563EB"/></a:accent1>
   <a:accent2><a:srgbClr val="F59E0B"/></a:accent2>
   <a:accent3><a:srgbClr val="10B981"/></a:accent3>
   <a:accent4><a:srgbClr val="EF4444"/></a:accent4>
   <a:accent5><a:srgbClr val="8B5CF6"/></a:accent5>
   <a:accent6><a:srgbClr val="06B6D4"/></a:accent6>
  </a:clrScheme>
  <a:fontScheme name="Brand">
   <a:majorFont><a:latin typeface="Source Han Sans"/><a:ea typeface="思源黑体"/></a:majorFont>
   <a:minorFont><a:latin typeface="Inter"/><a:ea typeface="思源黑体"/></a:minorFont>
  </a:fontScheme>
 </a:themeElements>
</a:theme>
"""

# 标准 Office 主题形状：dk1/lt1 是 sysClr（val=系统色枚举名，RGB 真值在 lastClr）。
# 修前 _hex 对 sysClr 读 val → dk1(windowText, 10 字符) 解析失败、lt1(window,
# 恰 6 字符) 产出非法 "#WINDOW" → 启发式恒落默认分支（review 问题 1）。
SYSCOLOR_THEME_XML = """<?xml version="1.0" encoding="UTF-8"?>
<a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" name="Office">
 <a:themeElements>
  <a:clrScheme name="Office">
   <a:dk1><a:sysClr val="windowText" lastClr="000000"/></a:dk1>
   <a:lt1><a:sysClr val="window" lastClr="FFFFFF"/></a:lt1>
   <a:dk2><a:srgbClr val="44546A"/></a:dk2>
   <a:lt2><a:srgbClr val="E7E6E6"/></a:lt2>
   <a:accent1><a:srgbClr val="4472C4"/></a:accent1>
   <a:accent2><a:srgbClr val="ED7D31"/></a:accent2>
   <a:accent3><a:srgbClr val="A5A5A5"/></a:accent3>
   <a:accent4><a:srgbClr val="FFC000"/></a:accent4>
   <a:accent5><a:srgbClr val="5B9BD5"/></a:accent5>
   <a:accent6><a:srgbClr val="70AD47"/></a:accent6>
  </a:clrScheme>
  <a:fontScheme name="Office">
   <a:majorFont><a:latin typeface="Calibri Light"/><a:ea typeface=""/></a:majorFont>
   <a:minorFont><a:latin typeface="Calibri"/><a:ea typeface=""/></a:minorFont>
  </a:fontScheme>
 </a:themeElements>
</a:theme>
"""

SLIDE_RELS = """<?xml version="1.0"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="../media/image1.png"/>
 <Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/slideLayout" Target="../slideLayouts/slideLayout1.xml"/>
</Relationships>
"""

LAYOUT_XML = """<?xml version="1.0"?>
<p:sldLayout xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" type="title">
 <p:cSld name="标题幻灯片"/>
</p:sldLayout>
"""

PNG_1PX = bytes.fromhex("89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000a49444154789c6360000002000154a24f5d0000000049454e44ae426082")


def build_pptx(theme_xml: str = THEME_XML) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("ppt/theme/theme1.xml", theme_xml)
        zf.writestr("ppt/slideLayouts/slideLayout1.xml", LAYOUT_XML)
        zf.writestr("ppt/slides/slide1.xml", "<sld/>")
        zf.writestr("ppt/slides/_rels/slide1.xml.rels", SLIDE_RELS)
        zf.writestr("ppt/media/image1.png", PNG_1PX)
        zf.writestr("docProps/app.xml", "<app/>")
    return buf.getvalue()


@pytest.fixture()
def pptx_file(tmp_path: Path) -> Path:
    p = tmp_path / "brand-deck.pptx"
    p.write_bytes(build_pptx())
    return p


# ── 蒸馏器 ────────────────────────────────────────────────────────────


def test_distill_extracts_palette_fonts_layouts(pptx_file, tmp_path):
    brand = distill_pptx(pptx_file, tmp_path / "out")
    assert brand["schema_version"] == 1
    assert brand["distiller"] == "ooxml-v1"
    assert brand["palette"]["primary"] == "#2563EB"  # accent1
    assert brand["palette"]["colors"]["dk1"] == "#0B1220"
    assert brand["typography"]["title_font"] == "思源黑体"  # ea 优先
    assert brand["typography"]["body_font"] == "思源黑体"
    assert brand["font_family"] == "思源黑体"
    assert brand["layouts"] == ["标题幻灯片"]


def test_distill_extracts_logo_file(pptx_file, tmp_path):
    out = tmp_path / "out"
    brand = distill_pptx(pptx_file, out)
    assert brand["logo"]["filename"] == "logo.png"
    assert (out / "logo.png").read_bytes() == PNG_1PX


def test_distill_light_deck_maps_to_light_theme(pptx_file, tmp_path):
    # fixture：dk1=#0B1220 深色文字 + lt1=#FFFFFF 白底 = 浅底 deck → light 主题
    # （修前启发式把 dk1 当背景色读，方向反了，浅底 deck 被判成深底）
    brand = distill_pptx(pptx_file, tmp_path / "out")
    assert brand["style_tendency"]["remotion_theme"] == "light"


def test_distill_standard_office_sysclr_theme(tmp_path):
    # 标准 Office 主题（sysClr dk1/lt1 + lastClr）：黑字白底浅色 deck。
    # 修前 dk1 解析失败（恒默认 dark-blue）+ lt1 产出非法 "#WINDOW"。
    p = tmp_path / "office.pptx"
    p.write_bytes(build_pptx(SYSCOLOR_THEME_XML))
    brand = distill_pptx(p, tmp_path / "out")
    assert brand["palette"]["colors"]["dk1"] == "#000000"
    assert brand["palette"]["colors"]["lt1"] == "#FFFFFF"  # 不是 "#WINDOW"
    assert brand["palette"]["primary"] == "#4472C4"
    assert brand["style_tendency"]["remotion_theme"] == "light"  # 不再恒 dark-blue
    assert brand["font_family"] == "Calibri"


def test_hex_sysclr_prefers_lastclr():
    from xml.etree import ElementTree as ET

    from app.gateway.assets.distiller import _hex

    def node(inner: str) -> ET.Element:
        return ET.fromstring(f'<a:dk1 xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">{inner}</a:dk1>')

    assert _hex(node('<a:sysClr val="windowText" lastClr="000000"/>')) == "#000000"
    assert _hex(node('<a:sysClr val="window" lastClr="FFFFFF"/>')) == "#FFFFFF"
    # 兜底：个别生成器把 RGB 直接放 val（合法十六进制才认）
    assert _hex(node('<a:sysClr val="FF0000"/>')) == "#FF0000"
    assert _hex(node('<a:sysClr val="FF44546A"/>')) == "#44546A"  # 8 位 ARGB
    # 枚举名（无 lastClr）不得当色值：6 字符的 "window" 修前产出 "#WINDOW"
    assert _hex(node('<a:sysClr val="window"/>')) is None
    assert _hex(node('<a:sysClr val="windowText"/>')) is None
    assert _hex(node('<a:srgbClr val="0B1220"/>')) == "#0B1220"


def test_distill_corrupt_theme_xml_degrades_to_empty(tmp_path):
    # zip 合法但 theme1.xml 畸形：色板/字体降级为空（同 _layout_names），
    # 不抛 ParseError（ET.ParseError 是 SyntaxError 子类，会穿透端点的
    # except ValueError 变 500 + 孤儿记录——review 问题 3）
    p = tmp_path / "corrupt-theme.pptx"
    p.write_bytes(build_pptx("<a:theme><unclosed"))
    brand = distill_pptx(p, tmp_path / "out")
    assert brand["palette"]["colors"] == {}
    assert brand["typography"] == {}
    assert brand["style_tendency"]["remotion_theme"] == "dark-blue"  # 空色板落默认
    assert brand["layouts"] == ["标题幻灯片"]  # 其余部件照常解析


def test_distill_rejects_non_pptx(tmp_path):
    p = tmp_path / "fake.pptx"
    p.write_bytes(b"PK\x03\x04plain")
    with pytest.raises(ValueError, match="不是有效的 PPTX"):
        distill_pptx(p, tmp_path / "out")
    p2 = tmp_path / "plain.txt"
    p2.write_bytes(b"hello")
    with pytest.raises(ValueError, match="zip"):
        distill_pptx(p2, tmp_path / "out")


def test_theme_heuristic_matrix():
    # dk1 = Text 1（ECMA-376 clrMap tx1→dk1）：深色文字=浅底 deck → light/warm；
    # 浅色文字=深底 deck → dark-blue/dark-green/warm（按 accent1 色相）
    assert remotion_theme_heuristic({"dk1": "#0B1220", "accent1": "#2563EB"})[0] == "light"
    assert remotion_theme_heuristic({"dk1": "#0B1220", "accent1": "#10B981"})[0] == "warm"
    assert remotion_theme_heuristic({"dk1": "#0B1220", "accent1": "#F59E0B"})[0] == "warm"
    assert remotion_theme_heuristic({"dk1": "#F8FAFC", "accent1": "#2563EB"})[0] == "dark-blue"
    assert remotion_theme_heuristic({"dk1": "#F8FAFC", "accent1": "#10B981"})[0] == "dark-green"
    assert remotion_theme_heuristic({"dk1": "#F8FAFC", "accent1": "#F59E0B"})[0] == "warm"
    assert remotion_theme_heuristic({})[0] == "dark-blue"  # 缺主题色落默认


# ── 注册表 CRUD ────────────────────────────────────────────────────────


def test_store_crud_roundtrip(tmp_path):
    store = AssetStore(base_dir=tmp_path)
    rec = store.create(user_id="u1", type="ppt_template", name="测试", brand={"x": 1})
    assert rec.id.startswith("as_")
    assert store.get(rec.id, "u1").to_dict()["name"] == "测试"
    assert store.get(rec.id, "other") is None  # 用户隔离
    rec2 = store.update(rec.id, "u1", name="改名", brand={"y": 2})
    assert rec2.to_dict()["name"] == "改名"
    listing = store.list(user_id="u1", type="ppt_template")
    assert len(listing) == 1
    store.delete(rec.id, "u1")
    assert store.get(rec.id, "u1") is None


def test_store_rejects_unknown_type(tmp_path):
    store = AssetStore(base_dir=tmp_path)
    with pytest.raises(ValueError, match="Unknown asset type"):
        store.create(user_id="u1", type="video_clip")


# ── 端点（独立子应用 + monkeypatch 依赖） ─────────────────────────────


class _State:
    def __init__(self):
        self.video_gen_service = None


@pytest.fixture()
def client(tmp_path, monkeypatch):
    from app.gateway.routers import assets as assets_router

    app = FastAPI()
    app.include_router(assets_router.router)
    monkeypatch.setattr(
        "deerflow.config.paths.get_paths",
        lambda: type("P", (), {"base_dir": tmp_path})(),
    )
    monkeypatch.setattr("deerflow.runtime.user_context.get_effective_user_id", lambda: "u1")
    return TestClient(app)


def test_upload_list_detail_confirm_delete_flow(client, tmp_path):
    resp = client.post(
        "/api/assets/ppt-templates",
        files={"file": ("deck.pptx", build_pptx(), "application/vnd.openxmlformats-officedocument.presentationml.presentation")},
        data={"name": "我的品牌"},
    )
    assert resp.status_code == 200, resp.text
    asset = resp.json()["asset"]
    assert asset["name"] == "我的品牌"
    assert asset["source_filename"] == "deck.pptx"  # 修前 create 未传、update 白名单丢弃，恒 None
    assert asset["brand"]["palette"]["primary"] == "#2563EB"

    listing = client.get("/api/assets/ppt-templates").json()["assets"]
    assert len(listing) == 1
    assert listing[0]["palette"]["primary"] == "#2563EB"

    detail = client.get(f"/api/assets/ppt-templates/{asset['id']}").json()
    assert detail["logo_url"].endswith("/logo")

    logo = client.get(f"/api/assets/ppt-templates/{asset['id']}/logo")
    assert logo.status_code == 200
    assert logo.content == PNG_1PX

    # 确认保存：改名 + 主题订正
    upd = client.put(
        f"/api/assets/ppt-templates/{asset['id']}",
        json={"name": "正式品牌", "brand_overrides": {"remotion_theme": "warm"}},
    )
    assert upd.status_code == 200
    assert upd.json()["asset"]["brand"]["style_tendency"]["remotion_theme"] == "warm"

    bad = client.put(
        f"/api/assets/ppt-templates/{asset['id']}",
        json={"brand_overrides": {"remotion_theme": "neon"}},
    )
    assert bad.status_code == 400

    assert client.delete(f"/api/assets/ppt-templates/{asset['id']}").status_code == 200
    assert client.get(f"/api/assets/ppt-templates/{asset['id']}").status_code == 404


def test_upload_rejects_non_pptx(client):
    resp = client.post(
        "/api/assets/ppt-templates",
        files={"file": ("bad.pptx", b"PK\x03\x04nope", "application/octet-stream")},
        data={"name": "x"},
    )
    assert resp.status_code == 400
    # 400 路径不留孤儿记录
    assert client.get("/api/assets/ppt-templates").json()["assets"] == []


def test_upload_corrupt_theme_xml_degrades_to_empty_palette(client):
    # zip 合法 + theme1.xml 畸形：降级空色板 200（不再 ParseError 穿透 500）
    resp = client.post(
        "/api/assets/ppt-templates",
        files={"file": ("corrupt.pptx", build_pptx("<a:theme><unclosed"), "application/octet-stream")},
        data={"name": "x"},
    )
    assert resp.status_code == 200, resp.text
    asset = resp.json()["asset"]
    assert asset["brand"]["palette"]["colors"] == {}
    assert asset["brand"]["style_tendency"]["remotion_theme"] == "dark-blue"


def test_upload_unexpected_error_cleans_up_record(client, monkeypatch):
    # 非 ValueError 的解析失败（zip CRC 损坏等）：500 + 清理已建记录，
    # 不留 status=ready/brand=None 的孤儿条目挂在模板列表里（review 问题 3）
    def _boom(*args, **kwargs):
        raise RuntimeError("zip CRC broken")

    monkeypatch.setattr("app.gateway.routers.assets.distill_pptx", _boom)
    resp = client.post(
        "/api/assets/ppt-templates",
        files={"file": ("deck.pptx", build_pptx(), "application/octet-stream")},
        data={"name": "x"},
    )
    assert resp.status_code == 500
    assert client.get("/api/assets/ppt-templates").json()["assets"] == []


# ── 应用到课程项目（映射单一事实源） ──────────────────────────────────


def test_apply_to_project_maps_brand_fields(tmp_path, monkeypatch, pptx_file):

    store = AssetStore(base_dir=tmp_path)
    brand = distill_pptx(pptx_file, store.files_dir("as_test") if False else tmp_path / "f")  # noqa: SIM108
    rec = store.create(user_id="u1", type="ppt_template", name="b", brand=brand)
    # 手工把蒸馏产物放进资产目录（端点在 create 时落盘；直连 store 需自放）
    asset_dir = store.files_dir(rec.id)
    asset_dir.mkdir(parents=True, exist_ok=True)
    logo_src = tmp_path / "f" / "logo.png"
    asset_dir.joinpath("logo.png").write_bytes(logo_src.read_bytes())

    import app.gateway.routers.assets as router_mod

    monkeypatch.setattr("deerflow.config.paths.get_paths", lambda: type("P", (), {"base_dir": tmp_path})())
    monkeypatch.setattr("deerflow.runtime.user_context.get_effective_user_id", lambda: "u1")

    # 假 project store：捕获 update 调用
    updated: dict = {}
    import app.gateway.video_gen.course_project_store as cps_mod

    class _Proj:
        color_palette = {"old": "#111111"}
        font_family = None
        remotion_theme = None
        logo_path = None

        def to_dict(self):
            return {"id": "cp_a1b2c3d4e5f6", **updated}

    class _Store:
        def get(self, pid, user_id):
            return _Proj() if pid == "cp_a1b2c3d4e5f6" and user_id == "u1" else None

        def update(self, pid, user_id, **kw):
            updated.update(kw)
            return _Proj()

    monkeypatch.setattr(cps_mod, "CourseProjectStore", lambda base_dir: _Store())

    class _Svc:
        _storage_dir = str(tmp_path / "vg")

    app = FastAPI()
    app.include_router(router_mod.router)
    app.state.video_gen_service = _Svc()
    client = TestClient(app)

    resp = client.post(f"/api/assets/ppt-templates/{rec.id}/apply-to-project", json={"project_id": "cp_a1b2c3d4e5f6"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body["applied_fields"]) == {"color_palette", "font_family", "remotion_theme", "logo_path"}
    # 映射单一事实源（§六.B.2）
    assert updated["color_palette"]["primary"] == "#2563EB"
    assert updated["color_palette"]["old"] == "#111111"  # 既有键保留
    assert updated["font_family"] == "思源黑体"
    assert updated["remotion_theme"] == "light"
    assert updated["logo_path"].startswith("brand_assets/cp_a1b2c3d4e5f6/logo")
    assert (tmp_path / "vg" / updated["logo_path"]).is_file()  # 真拷贝


# ── 2026-09-12 缺陷批条目 4/5/21/22/36/37 ───────────────────────────────────


def _patch_declared_size(zip_bytes: bytes, entry_name: str, new_size: int) -> bytes:
    """把 central directory 里 entry 的未压缩大小改成 new_size（声明造假/超大部件模拟）。"""
    import struct

    idx = 0
    while True:
        idx = zip_bytes.find(b"PK\x01\x02", idx)
        if idx < 0:
            raise AssertionError(f"central dir entry {entry_name} not found")
        name_len = struct.unpack_from("<H", zip_bytes, idx + 28)[0]
        name = zip_bytes[idx + 46 : idx + 46 + name_len]
        if name == entry_name.encode():
            out = bytearray(zip_bytes)
            struct.pack_into("<I", out, idx + 24, new_size)
            return bytes(out)
        idx += 4


def test_distill_rejects_high_ratio_theme_bomb(tmp_path):
    """条目 4：重复空白 XML 高膨胀比 theme 部件（声明 >100MB 单文件上限）→ 蒸馏报错。"""
    from app.gateway.assets.distiller import MAX_PART_BYTES

    bomb_theme = THEME_XML.replace("</a:clrScheme>", "<!--" + " " * (MAX_PART_BYTES + 1024 * 1024) + "--></a:clrScheme>")
    assert len(bomb_theme) > MAX_PART_BYTES
    p = tmp_path / "bomb.pptx"
    p.write_bytes(build_pptx(bomb_theme))
    with pytest.raises(ValueError, match="模板包含异常大的内部部件"):
        distill_pptx(p, tmp_path / "out")


def test_distill_rejects_oversized_declared_logo(tmp_path):
    """条目 4：logo 部件声明 150MB（zip 头可造假）→ 报错，不落盘不限量拷贝。"""
    from app.gateway.assets.distiller import MAX_PART_BYTES

    p = tmp_path / "lying-logo.pptx"
    p.write_bytes(_patch_declared_size(build_pptx(), "ppt/media/image1.png", 150 * 1024 * 1024))
    with pytest.raises(ValueError, match="模板包含异常大的内部部件"):
        distill_pptx(p, tmp_path / "out")
    assert not (tmp_path / "out" / "logo.png").exists()
    assert MAX_PART_BYTES == 100 * 1024 * 1024  # 单文件上限钉死（随上传上限联动）


def test_distill_cumulative_budget(tmp_path):
    """条目 4：累计 ≤200MB——theme 重复读两次都进预算（跨条目共享）。"""
    from app.gateway.assets.distiller import MAX_TOTAL_DECOMPRESSED_BYTES, _DecompressionBudget

    assert MAX_TOTAL_DECOMPRESSED_BYTES == 200 * 1024 * 1024
    budget = _DecompressionBudget()
    budget.total = MAX_TOTAL_DECOMPRESSED_BYTES - 1
    with pytest.raises(ValueError, match="模板包含异常大的内部部件"):
        budget.check_declared(1024)  # 再读 1KB 就越限 → 拒


def test_theme_heuristic_boundary_greys():
    """条目 37：#7F7F7F（lum≈0.498 < 0.5）判浅底、#808080（≈0.502）判深底——
    钉住 `< 0.5` 严格小于的边界决策，零行为变更。"""
    assert remotion_theme_heuristic({"dk1": "#7F7F7F", "accent1": "#2563EB"})[0] == "light"
    assert remotion_theme_heuristic({"dk1": "#808080", "accent1": "#2563EB"})[0] == "dark-blue"


# ── 条目 21：上传两段式（declared + probe）+ 413 ──────────────────────────


@pytest.mark.asyncio
async def test_upload_oversized_declared_413_without_reading(client):
    """>100MB（declared size 命中）→ 413 且**零读**（未把整个文件读进内存）。"""
    from fastapi import HTTPException, UploadFile

    from app.gateway.routers.assets import MAX_PPTX_BYTES, create_ppt_template

    assert MAX_PPTX_BYTES == 100 * 1024 * 1024
    reads = {"n": 0}

    class _Counting(io.BytesIO):
        def read(self, *a):  # noqa: ANN002, ANN003
            reads["n"] += 1
            return super().read(*a)

    upload = UploadFile(filename="big.pptx", file=_Counting(b"x" * 8), size=MAX_PPTX_BYTES + 1)
    with pytest.raises(HTTPException) as exc:
        await create_ppt_template(request=None, file=upload, name="x")
    assert exc.value.status_code == 413
    assert reads["n"] == 0, "declared 命中时不得读文件内容"


@pytest.mark.asyncio
async def test_upload_oversized_probe_413(client):
    """无 declared size（测试替身/直连 gateway）→ probe 读 MAX+1 超限即 413。"""
    from fastapi import HTTPException, UploadFile

    from app.gateway.routers.assets import MAX_PPTX_BYTES, create_ppt_template

    upload = UploadFile(filename="big.pptx", file=io.BytesIO(b"x" * (MAX_PPTX_BYTES + 1)))
    with pytest.raises(HTTPException) as exc:
        await create_ppt_template(request=None, file=upload, name="x")
    assert exc.value.status_code == 413


def test_upload_within_limit_regression(client):
    """≤100MB 正常上传回归（两段式不误伤——fixture 走完整蒸馏链路）。"""
    resp = client.post(
        "/api/assets/ppt-templates",
        files={"file": ("deck.pptx", build_pptx(), "application/vnd.openxmlformats-officedocument.presentationml.presentation")},
        data={"name": "限内"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["asset"]["brand"]["palette"]["primary"] == "#2563EB"


# ── 条目 22：brand_overrides 校验 ─────────────────────────────────────────


def _upload_one(client):
    resp = client.post(
        "/api/assets/ppt-templates",
        files={"file": ("deck.pptx", build_pptx(), "application/octet-stream")},
        data={"name": "x"},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()["asset"]["id"]


def test_put_primary_validation_and_normalization(client):
    asset_id = _upload_one(client)
    for bad in ("#zz", "2563EB", "#12345", "#1234567"):
        resp = client.put(f"/api/assets/ppt-templates/{asset_id}", json={"brand_overrides": {"primary": bad}})
        assert resp.status_code == 400, f"{bad} 应 400: {resp.text}"
    ok = client.put(f"/api/assets/ppt-templates/{asset_id}", json={"brand_overrides": {"primary": "#a1b2c3"}})
    assert ok.status_code == 200, ok.text
    assert ok.json()["asset"]["brand"]["palette"]["primary"] == "#A1B2C3"  # 大写归一


def test_put_font_family_length_validation(client):
    asset_id = _upload_one(client)
    resp = client.put(f"/api/assets/ppt-templates/{asset_id}", json={"brand_overrides": {"font_family": "字" * 121}})
    assert resp.status_code == 400, resp.text
    ok = client.put(f"/api/assets/ppt-templates/{asset_id}", json={"brand_overrides": {"font_family": "字" * 120}})
    assert ok.status_code == 200, ok.text
    assert ok.json()["asset"]["brand"]["font_family"] == "字" * 120


def test_course_project_input_brand_validation():
    """条目 22 项目更新通路（CourseProjectInput，pydantic 惯例 422）。"""
    from pydantic import ValidationError

    from app.gateway.routers.video_gen import CourseProjectInput

    with pytest.raises(ValidationError):
        CourseProjectInput(font_family="字" * 121)
    with pytest.raises(ValidationError):
        CourseProjectInput(color_palette={"primary": "#zz"})
    with pytest.raises(ValidationError):
        CourseProjectInput(color_palette={"primary": "2563EB"})
    m = CourseProjectInput(color_palette={"primary": "#a1b2c3"})
    assert m.color_palette["primary"] == "#A1B2C3"
    assert CourseProjectInput(font_family="思源黑体").font_family == "思源黑体"
    # primary 缺失的 palette 不受影响（仅校验存在的 primary）
    CourseProjectInput(color_palette={"bg": "#111111"})


# ── 条目 5：apply-to-project project_id 白名单 ────────────────────────────


def _apply_app(tmp_path, monkeypatch, pptx_file):
    store = AssetStore(base_dir=tmp_path)
    brand = distill_pptx(pptx_file, tmp_path / "f")
    rec = store.create(user_id="u1", type="ppt_template", name="b", brand=brand)

    import app.gateway.routers.assets as router_mod

    monkeypatch.setattr("deerflow.config.paths.get_paths", lambda: type("P", (), {"base_dir": tmp_path})())
    monkeypatch.setattr("deerflow.runtime.user_context.get_effective_user_id", lambda: "u1")

    import app.gateway.video_gen.course_project_store as cps_mod

    class _Proj:
        color_palette = {}
        font_family = None
        remotion_theme = None
        logo_path = None

        def to_dict(self):
            return {"id": "cp_a1b2c3d4e5f6"}

    class _Store:
        def get(self, pid, user_id):
            return _Proj() if pid == "cp_a1b2c3d4e5f6" and user_id == "u1" else None

        def update(self, pid, user_id, **kw):
            return _Proj()

    monkeypatch.setattr(cps_mod, "CourseProjectStore", lambda base_dir: _Store())

    class _Svc:
        _storage_dir = str(tmp_path / "vg")

    app = FastAPI()
    app.include_router(router_mod.router)
    app.state.video_gen_service = _Svc()
    return TestClient(app), rec.id


def test_apply_to_project_rejects_unsafe_project_id(tmp_path, monkeypatch, pptx_file):
    """条目 5：project_id 穿越/形状不合规 → 404（与未知 id 同口径）。"""
    client, asset_id = _apply_app(tmp_path, monkeypatch, pptx_file)
    for bad in ("../../etc", "cp_x", "cp_ghi", "cp_a1b2c3d4e5f", "foo/bar"):
        resp = client.post(f"/api/assets/ppt-templates/{asset_id}/apply-to-project", json={"project_id": bad})
        assert resp.status_code == 404, f"{bad} 应 404: {resp.text}"


# ── 条目 36 + DEF-01/E0-E1（2026-09-23 批 2）：redistill 端点（逐字段合并 + diff 明示）──


def test_redistill_preserves_user_confirmed_fields(client):
    """E1 合并矩阵：PUT 确认 → _confirmed 落盘 → redistill → 三字段保持 + 其余刷新。"""
    asset_id = _upload_one(client)
    # 人工订正（PUT 改三字段 + 改名）
    upd = client.put(
        f"/api/assets/ppt-templates/{asset_id}",
        json={
            "name": "订正版",
            "brand_overrides": {
                "remotion_theme": "warm",
                "primary": "#0A0A0A",
                "font_family": "用户公司黑体",
            },
        },
    )
    assert upd.status_code == 200
    brand = upd.json()["asset"]["brand"]
    # E0：三字段同步进 _confirmed
    assert set(brand["_confirmed"]) == {"font_family", "palette.primary", "style_tendency.remotion_theme"}

    resp = client.post(f"/api/assets/ppt-templates/{asset_id}/redistill")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    brand = body["asset"]["brand"]
    # 三字段保持用户值
    assert brand["style_tendency"]["remotion_theme"] == "warm"
    assert brand["style_tendency"]["reason"] == "用户确认"
    assert brand["palette"]["primary"] == "#0A0A0A"
    assert brand["font_family"] == "用户公司黑体"
    # _confirmed 随 brand 保留
    assert set(brand["_confirmed"]) == {"font_family", "palette.primary", "style_tendency.remotion_theme"}
    # 辅色刷新回蒸馏值（未被确认 → 不保留旧值，但 fixture 蒸馏值即原值）
    assert brand["palette"]["colors"]["accent1"] == "#2563EB"
    diff = body["brand_diff"]
    assert diff["preserved_user_confirmed"] == ["font_family", "palette.primary", "style_tendency.remotion_theme"]
    # E0：_confirmed 元数据不进 diff 噪声
    assert "_confirmed" not in diff["changed"]
    assert "legacy_overwrite_warning" not in diff


def test_redistill_legacy_asset_without_confirmed_refreshes_all(client):
    """存量无 _confirmed 资产：三字段全量刷新 + legacy_overwrite_warning 明示。"""
    from app.gateway.routers.assets import _get_store

    asset_id = _upload_one(client)
    # 直接改库模拟存量资产（无 _confirmed 键）
    store = _get_store()
    rec = store.get(asset_id, "u1")
    data = rec.to_dict()
    brand = dict(data["brand"])
    brand.pop("_confirmed", None)
    brand["style_tendency"] = {"remotion_theme": "warm", "reason": "存量人工订正"}
    store.update(asset_id, "u1", brand=brand)

    resp = client.post(f"/api/assets/ppt-templates/{asset_id}/redistill")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["asset"]["brand"]["style_tendency"]["remotion_theme"] == "light"  # 蒸馏值（fixture 浅底 deck）
    assert body["asset"]["brand"]["palette"]["primary"] == "#2563EB"
    diff = body["brand_diff"]
    assert diff["legacy_overwrite_warning"]
    assert "历史人工订正" in diff["legacy_overwrite_warning"]
    assert "preserved_user_confirmed" not in diff


def test_redistill_unconfirmed_fields_refresh(client):
    """仅确认主题时：主色/字体按新蒸馏值刷新（fixture 蒸馏主色 #2563EB）。"""
    asset_id = _upload_one(client)
    upd = client.put(f"/api/assets/ppt-templates/{asset_id}", json={"brand_overrides": {"remotion_theme": "warm"}})
    assert upd.status_code == 200
    assert set(upd.json()["asset"]["brand"]["_confirmed"]) == {"style_tendency.remotion_theme"}

    resp = client.post(f"/api/assets/ppt-templates/{asset_id}/redistill")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    brand = body["asset"]["brand"]
    assert brand["style_tendency"]["remotion_theme"] == "warm"  # 保留
    assert brand["palette"]["primary"] == "#2563EB"  # 蒸馏值刷新
    assert diff_preserved(body["brand_diff"]) == ["style_tendency.remotion_theme"]


def diff_preserved(diff):
    return diff.get("preserved_user_confirmed", [])


# ── E0 附带修正（2026-09-23 review #6）：仅改名 PUT 不得写空 `_confirmed` ──


def test_rename_only_put_keeps_asset_legacy_for_redistill(client):
    """存量资产（无 _confirmed）+ 仅改名 PUT（brand_overrides 为空）→
    不得落 ``_confirmed: []``；redistill 仍走 legacy 路径出覆盖警示。
    修前：PUT 无条件写空列表 → legacy_asset 判 False → 警示被静默绕过。"""
    asset_id = _upload_one(client)
    upd = client.put(f"/api/assets/ppt-templates/{asset_id}", json={"name": "仅改名"})
    assert upd.status_code == 200, upd.text
    brand = upd.json()["asset"]["brand"]
    assert "_confirmed" not in brand  # 空列表都不写

    resp = client.post(f"/api/assets/ppt-templates/{asset_id}/redistill")
    assert resp.status_code == 200, resp.text
    diff = resp.json()["brand_diff"]
    assert diff["legacy_overwrite_warning"]
    assert "preserved_user_confirmed" not in diff


def test_rename_only_put_drops_stale_empty_confirmed(client):
    """旧版 PUT 已落盘的 ``_confirmed: []``：仅改名 PUT 摘除（空列表零信息），
    redistill 恢复 legacy 语义。"""
    from app.gateway.routers.assets import _get_store

    asset_id = _upload_one(client)
    store = _get_store()
    rec = store.get(asset_id, "u1")
    brand = dict(rec.to_dict()["brand"])
    brand["_confirmed"] = []  # 模拟旧版行为写入的空列表
    store.update(asset_id, "u1", brand=brand)

    upd = client.put(f"/api/assets/ppt-templates/{asset_id}", json={"name": "再改名"})
    assert upd.status_code == 200, upd.text
    assert "_confirmed" not in upd.json()["asset"]["brand"]


def test_put_confirmed_field_stamps_confirmed_list(client):
    """存量资产 + PUT 确认 font_family → ``_confirmed == ["font_family"]`` 落盘。"""
    asset_id = _upload_one(client)
    upd = client.put(
        f"/api/assets/ppt-templates/{asset_id}",
        json={"brand_overrides": {"font_family": "用户公司黑体"}},
    )
    assert upd.status_code == 200, upd.text
    assert upd.json()["asset"]["brand"]["_confirmed"] == ["font_family"]


def test_redistill_missing_source_409(client):
    """source.pptx 缺失 → 409（可读指引）。"""
    asset_id = _upload_one(client)
    from app.gateway.routers.assets import _get_store

    source = _get_store().files_dir(asset_id) / "source.pptx"
    assert source.is_file()
    source.unlink()
    resp = client.post(f"/api/assets/ppt-templates/{asset_id}/redistill")
    assert resp.status_code == 409, resp.text
    assert "重新蒸馏" in resp.json()["detail"]


# ── 条目 24（2026-09-12 缺陷批）：端点级跨用户隔离（u1 建资产，u2 访问均 404）──


def test_endpoint_cross_user_isolation(tmp_path, monkeypatch):
    """修前隔离断言只在 store 层（store.get(rec.id, "other") is None）；端点层
    5 例全部固定单用户。补端点级 404 断言：GET/PUT/DELETE/logo/apply。"""
    from app.gateway.routers import assets as assets_router

    def _client_as(uid):
        app = FastAPI()
        app.include_router(assets_router.router)
        monkeypatch.setattr("deerflow.config.paths.get_paths", lambda: type("P", (), {"base_dir": tmp_path})())
        monkeypatch.setattr("deerflow.runtime.user_context.get_effective_user_id", lambda: uid)
        return TestClient(app)

    u1 = _client_as("u1")
    resp = u1.post(
        "/api/assets/ppt-templates",
        files={"file": ("deck.pptx", build_pptx(), "application/octet-stream")},
        data={"name": "u1 的模板"},
    )
    assert resp.status_code == 200, resp.text
    asset_id = resp.json()["asset"]["id"]

    u2 = _client_as("u2")
    assert u2.get(f"/api/assets/ppt-templates/{asset_id}").status_code == 404
    assert u2.put(f"/api/assets/ppt-templates/{asset_id}", json={"name": "抢改"}).status_code == 404
    assert u2.delete(f"/api/assets/ppt-templates/{asset_id}").status_code == 404
    assert u2.get(f"/api/assets/ppt-templates/{asset_id}/logo").status_code == 404
    assert u2.post(f"/api/assets/ppt-templates/{asset_id}/apply-to-project", json={"project_id": "cp_a1b2c3d4e5f6"}).status_code == 404
    # u2 列表看不到 u1 的资产
    assert u2.get("/api/assets/ppt-templates").json()["assets"] == []
    # 属主照常访问（不因隔离误伤）——_client_as 是全局 patch，须重拔回 u1 再访问
    u1_again = _client_as("u1")
    assert u1_again.get(f"/api/assets/ppt-templates/{asset_id}").status_code == 200


def test_distill_logo_midwrite_budget_leaves_no_partial_file(tmp_path, monkeypatch):
    """复审二轮（2026-09-13）：logo 流式限量写中途触发 budget → 临时 .part 清除、
    目标 logo 不落盘（修前直接写 logo.ext，截断残留会让 redistill 失败后存量记录
    引用被覆盖的坏 logo）。声明预检旁路以制造「声明合规、实际超限」的中途失败。"""
    from app.gateway.assets import distiller as distiller_mod

    monkeypatch.setattr(distiller_mod, "MAX_PART_BYTES", 1)
    monkeypatch.setattr(distiller_mod._DecompressionBudget, "check_declared", lambda self, declared: None)
    pptx = tmp_path / "midwrite.pptx"
    pptx.write_bytes(build_pptx())
    out = tmp_path / "out"
    with pytest.raises(ValueError, match="模板包含异常大的内部部件"):
        distill_pptx(pptx, out)
    assert not (out / "logo.png").exists()
    assert not (out / "logo.png.part").exists()
    assert list(out.glob("*.part")) == []


def test_redistill_budget_failure_keeps_existing_logo_intact(client, tmp_path, monkeypatch):
    """复审二轮（2026-09-13）端点级：redistill 中途失败不动存量 brand——旧 logo
    字节原样、无 .part 残留（整量覆盖语义只在成功时发生）。"""
    from app.gateway.assets import distiller as distiller_mod
    from app.gateway.routers.assets import _get_store

    asset_id = _upload_one(client)
    asset_dir = _get_store().files_dir(asset_id)
    logo = asset_dir / "logo.png"
    assert logo.is_file()
    good_bytes = logo.read_bytes()

    monkeypatch.setattr(distiller_mod, "MAX_PART_BYTES", 1)
    monkeypatch.setattr(distiller_mod._DecompressionBudget, "check_declared", lambda self, declared: None)
    resp = client.post(f"/api/assets/ppt-templates/{asset_id}/redistill")
    assert resp.status_code == 400, resp.text
    assert "异常大的内部部件" in resp.json()["detail"]
    # 存量 logo 未被截断覆盖；无 .part 残留
    assert logo.read_bytes() == good_bytes
    assert list(asset_dir.glob("*.part")) == []


# ── R11（2026-09-24 批 2）：PUT 清空（null）语义 + 测试证明力 ──


def test_put_null_clears_confirmed_and_redistill_restores_distilled(client):
    """R11 验收用例：篡改未确认字段 → redistill → 回到蒸馏值.

    序列：确认 font_family+primary → PUT null font_family（摘除订正，其余保留）
    → redistill → font_family 回蒸馏值、primary 仍保持用户订正值。
    修前：null 会写进 brand 且被 _confirmed 永久保留，redistill 恢复 None。
    """
    asset_id = _upload_one(client)
    distilled_font = client.get(f"/api/assets/ppt-templates/{asset_id}").json()["brand"]["font_family"]

    upd = client.put(
        f"/api/assets/ppt-templates/{asset_id}",
        json={"brand_overrides": {"font_family": "用户公司黑体", "primary": "#0A0A0A"}},
    )
    assert upd.status_code == 200
    assert set(upd.json()["asset"]["brand"]["_confirmed"]) == {"font_family", "palette.primary"}

    cleared = client.put(
        f"/api/assets/ppt-templates/{asset_id}",
        json={"brand_overrides": {"font_family": None}},
    )
    assert cleared.status_code == 200
    brand = cleared.json()["asset"]["brand"]
    assert set(brand["_confirmed"]) == {"palette.primary"}  # 摘除 font_family
    assert brand["font_family"] == "用户公司黑体"  # 现值不动（非 None）
    assert brand["palette"]["primary"] == "#0A0A0A"  # 相邻订正不受影响

    resp = client.post(f"/api/assets/ppt-templates/{asset_id}/redistill")
    assert resp.status_code == 200, resp.text
    brand = resp.json()["asset"]["brand"]
    assert brand["font_family"] == distilled_font  # 回到蒸馏值
    assert brand["palette"]["primary"] == "#0A0A0A"  # 未清空的订正仍保留
    assert diff_preserved(resp.json()["brand_diff"]) == ["palette.primary"]


def test_put_null_never_writes_none_into_brand(client):
    """null 不落 brand 值：新资产直接 PUT null 三字段 → brand 原样、无 _confirmed。"""
    asset_id = _upload_one(client)
    before = client.get(f"/api/assets/ppt-templates/{asset_id}").json()["brand"]

    resp = client.put(
        f"/api/assets/ppt-templates/{asset_id}",
        json={"brand_overrides": {"font_family": None, "primary": None, "remotion_theme": None}},
    )
    assert resp.status_code == 200
    brand = resp.json()["asset"]["brand"]
    assert "_confirmed" not in brand
    assert brand["font_family"] == before["font_family"]
    assert brand["palette"]["primary"] == before["palette"]["primary"]
    assert brand["style_tendency"] == before["style_tendency"]


def test_put_null_clears_each_confirmed_field_consistently(client):
    """R11：三字段（值字段/色值字段/枚举字段）清空语义一致——逐个 null 摘除各自标记。"""
    asset_id = _upload_one(client)
    upd = client.put(
        f"/api/assets/ppt-templates/{asset_id}",
        json={"brand_overrides": {"font_family": "F", "primary": "#0A0A0A", "remotion_theme": "warm"}},
    )
    assert set(upd.json()["asset"]["brand"]["_confirmed"]) == {"font_family", "palette.primary", "style_tendency.remotion_theme"}

    # 逐字段清空：每轮只摘一个标记，其余保留
    for field, key in (("remotion_theme", "style_tendency.remotion_theme"), ("primary", "palette.primary"), ("font_family", "font_family")):
        resp = client.put(
            f"/api/assets/ppt-templates/{asset_id}",
            json={"brand_overrides": {field: None}},
        )
        assert resp.status_code == 200
        confirmed = set(resp.json()["asset"]["brand"].get("_confirmed", []))
        assert key not in confirmed
    # 三轮后 _confirmed 空 → 键整体消失（E0 review #6 语义）
    assert "_confirmed" not in resp.json()["asset"]["brand"]
