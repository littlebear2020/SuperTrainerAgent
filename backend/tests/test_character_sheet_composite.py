"""角色三视图合成图（turnaround sheet）改造（2026-08-27 计划 X1~X11）。

计划：docs/plans/2026-08-27-character-sheet-composite-plan.md。
- TC-X1-01/02：CharacterSheet.composite_url 字段（手写 to_dict/from_dict 显式携带）
- TC-X2-01~03：build_composite_image 拼图工具（顺序/标注/单图直出/损坏降级）
- TC-X3-01：生成链路拼图（composite_url 落盘返回）
- TC-X4-01/02：上传链路拼图（3 张/仅 front）
- TC-X5-01/02：composite_path_for_sheet 惰性兜底（首拼缓存/已有直用）
- TC-X6-01：roster 条目携带 composite
- TC-X7-01~05：注入改造（声明帧 composite 1 张/实体 + None 首主角 + 回退 + char_cap_3）
- TC-X8-01/02：seedance 带名方言（角色三视图/道具产品参考图 + names=None 现状逐字）
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# ── X1: CharacterSheet.composite_url 字段 ─────────────────────────────────


def test_tc_x1_01_legacy_sheet_json_without_composite():
    """TC-X1-01：存量 sheet JSON 无 composite_url → from_dict 不崩且落 ""。"""
    from app.gateway.video_gen.course_models import CharacterSheet

    cs = CharacterSheet.from_dict(
        {
            "id": "aaaa1111",
            "name": "小明",
            "front_url": "/f.png",
            "side_url": "/s.png",
            "back_url": "/b.png",
            "features": "x",
        }
    )
    assert cs.composite_url == "", f"存量缺省应落空串，实际={cs.composite_url!r}"
    # 显式携带时读回
    cs2 = CharacterSheet.from_dict({**json.loads(json.dumps(cs.to_dict())), "composite_url": "/c.png"})
    assert cs2.composite_url == "/c.png"


def test_tc_x1_01_handwritten_to_dict_carries_composite():
    """X1 边界注记：CharacterSheet.to_dict/from_dict 是手写显式字段——两处必须各加一行，
    新增 dataclass 字段不会自动携带。此断言防「改了字段忘改手写方法」的回潮。"""
    from app.gateway.video_gen.course_models import CharacterSheet

    cs = CharacterSheet(name="小明", front_url="/f.png", composite_url="/composite/c.png")
    d = cs.to_dict()
    assert d.get("composite_url") == "/composite/c.png", f"手写 to_dict 必须携带 composite_url，实际={d}"
    assert CharacterSheet.from_dict(d).composite_url == "/composite/c.png"


def test_tc_x1_02_put_get_roundtrip_via_store(tmp_path):
    """TC-X1-02：PUT/GET 端到端读写——store 层落盘/读回 composite_url（asdict 自动
    携带 dataclass 字段 + 端点读走 proj.to_dict()）。"""
    from app.gateway.video_gen.course_project_store import CourseProjectStore

    st = CourseProjectStore(base_dir=tmp_path)
    proj = st.create(
        user_id="u1",
        title="t",
        character_sheets=[
            {
                "id": "aaaa1111",
                "name": "小明",
                "front_url": "/f.png",
                "side_url": "/s.png",
                "back_url": "/b.png",
                "features": "",
                "composite_url": "/sheets/c.png",
            }
        ],
    )
    pid = proj.id
    # 盘面 JSON 落盘携带（store._write 走 proj.to_dict() = asdict 递归 dataclass 字段）
    on_disk = json.loads((tmp_path / "course_projects" / "u1" / f"{pid}.json").read_text(encoding="utf-8"))
    assert on_disk["character_sheets"][0].get("composite_url") == "/sheets/c.png", on_disk
    # 读回：from_dict 白名单兼容 + 端点响应（proj.to_dict()）携带
    got = st.get(pid, "u1")
    assert got.character_sheets[0].composite_url == "/sheets/c.png"
    assert got.to_dict()["character_sheets"][0]["composite_url"] == "/sheets/c.png"


# ── X2: build_composite_image 拼图工具 ─────────────────────────────────────


def _solid_png(path: Path, rgb: tuple[int, int, int], w: int = 100, h: int = 150) -> Path:
    """生成纯色测试图（不同宽高比验证等比缩放 + 白底填充）。"""
    from PIL import Image

    Image.new("RGB", (w, h), rgb).save(path, format="PNG")
    return path


def test_tc_x2_01_three_views_layout_order_labels(tmp_path):
    """TC-X2-01：3 视图拼接——尺寸 512×3 宽 / 552 高（512 图区 + 40 标注区）、
    顺序 = views 顺序、每格图区中心像素为对应视图主色（等比缩放保主色）、
    标注区有墨（CJK 字体可用时）。"""
    from app.gateway.video_gen.character_sheet_generator import _COMPOSITE_TAG_LABELS, build_composite_image

    assert _COMPOSITE_TAG_LABELS == {"front": "正面", "side": "侧面", "back": "背面"}
    f = _solid_png(tmp_path / "f.png", (200, 30, 40))
    s = _solid_png(tmp_path / "s.png", (30, 200, 40), w=150, h=100)
    b = _solid_png(tmp_path / "b.png", (40, 30, 200))
    dest = tmp_path / "out" / "composite.png"
    ret = build_composite_image([("front", str(f)), ("side", str(s)), ("back", str(b))], dest)
    assert ret == str(dest.resolve()), f"返回合成图绝对路径，实际={ret}"

    from PIL import Image

    img = Image.open(dest)
    # A（2026-08-31）：1152×1152 方形白底画布（修前 1536×552 比例 2.783 超 i2v
    # 比例硬校验 [0.4,2.5]）；三联条带等比缩放 0.75 后垂直居中（上下白边）。
    assert img.size == (1152, 1152), f"3 视图 → 1152×1152 方形画布，实际={img.size}"
    assert 0.4 <= img.width / img.height <= 2.5, f"比例须落在 i2v 硬校验区间（≈1.0），实际={img.width / img.height}"
    scale = 1152 / (512 * 3)
    y0 = (1152 - round((512 + 40) * scale)) // 2
    for i, rgb in enumerate(((200, 30, 40), (30, 200, 40), (40, 30, 200))):
        px = img.getpixel((round((i * 512 + 256) * scale), round(256 * scale) + y0))
        assert all(abs(a - b) <= 2 for a, b in zip(px, rgb)), f"格 {i} 图区中心应近 {rgb}（等比缩放保主色），实际={px}"


def test_tc_x2_02_single_view_direct_copy(tmp_path):
    """TC-X2-02：1 张视图 → 合成图即该图本身（直接复制，不重复缩放）。"""
    from app.gateway.video_gen.character_sheet_generator import build_composite_image

    f = _solid_png(tmp_path / "front.png", (10, 200, 100), w=640, h=480)
    dest = tmp_path / "composite.png"
    ret = build_composite_image([("front", str(f))], dest)
    assert ret == str(dest.resolve())
    assert dest.read_bytes() == f.read_bytes(), "单视图须直接复制（字节一致，无重编码缩放）"


def test_tc_x2_03_corrupt_view_degrades_all_missing_raises(tmp_path):
    """TC-X2-03：损坏文件降级——该格留白不炸；全部缺失 → ValueError（调用方降级）。"""
    import pytest

    from app.gateway.video_gen.character_sheet_generator import build_composite_image

    f = _solid_png(tmp_path / "f.png", (200, 30, 40))
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"\x89PNG corrupt-not-an-image")
    missing = tmp_path / "gone.png"

    dest = tmp_path / "composite.png"
    build_composite_image([("front", str(f)), ("side", str(bad)), ("back", str(missing))], dest)
    from PIL import Image

    img = Image.open(dest)
    assert img.size == (1152, 1152), f"A（2026-08-31）：方形画布，实际={img.size}"
    scale = 1152 / (512 * 3)
    y0 = (1152 - round((512 + 40) * scale)) // 2
    # 格 0 正常着色；格 1/2 留白（图区中心为白）——探针按条带缩放居中换算
    px0 = img.getpixel((round(256 * scale), round(256 * scale) + y0))
    assert all(abs(a - b) <= 2 for a, b in zip(px0, (200, 30, 40))), f"格 0 应正常着色，实际={px0}"
    assert all(c > 250 for c in img.getpixel((round((512 + 256) * scale), round(256 * scale) + y0))), f"损坏视图格应留白，实际={img.getpixel((round((512 + 256) * scale), round(256 * scale) + y0))}"
    assert all(c > 250 for c in img.getpixel((round((1024 + 256) * scale), round(256 * scale) + y0))), f"缺失视图格应留白，实际={img.getpixel((round((1024 + 256) * scale), round(256 * scale) + y0))}"

    # 全部不可用 → ValueError
    with pytest.raises(ValueError):
        build_composite_image([("front", str(bad)), ("side", str(missing))], tmp_path / "composite2.png")


# ── X3: 生成链路拼图 ───────────────────────────────────────────────────────


def test_tc_x3_01_generate_returns_composite(tmp_path):
    """TC-X3-01：三视图生成产物含 composite_url 且文件存在、布局正确（3 格横排）。"""
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import patch

    from PIL import Image

    from app.gateway.video_gen import character_sheet_generator as csg
    from app.gateway.video_gen.character_sheet_generator import generate_character_sheet

    async def g(prompt, **kw):  # noqa: ANN001,ANN003
        return SimpleNamespace(url="http://x/test.png")

    async def _fake_download(url, dest):  # noqa: ANN001
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (300, 400), (120, 120, 130)).save(dest, format="PNG")

    with patch.object(csg, "_download_image_bytes", _fake_download):
        sheet = asyncio.run(
            generate_character_sheet(
                description="d",
                name="小明",
                features="f",
                image_gen=g,
                storage_dir=str(tmp_path),
                project_id="p1",
            )
        )
    assert sheet.composite_url, "生成产物须含 composite_url"
    p = Path(sheet.composite_url)
    assert p.exists(), f"合成图须落盘：{p}"
    assert "character_sheets/p1/" in p.as_posix(), f"落盘目录拍死 character_sheets/{{pid}}/，实际={p}"
    img = Image.open(p)
    assert img.size == (1152, 1152), f"3 视图 → 1152×1152 方形画布（A 2026-08-31），实际={img.size}"
    # 三视图本体不受影响（增强件语义）
    assert sheet.front_url and sheet.side_url and sheet.back_url


def test_tc_x3_composite_failure_does_not_block(tmp_path):
    """X3 边界：拼图失败不阻断三视图返回（composite_url="" + 不抛）。"""
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import patch

    from app.gateway.video_gen import character_sheet_generator as csg
    from app.gateway.video_gen.character_sheet_generator import generate_character_sheet

    async def g(prompt, **kw):  # noqa: ANN001,ANN003
        return SimpleNamespace(url="http://x/test.png")

    async def _fake_download(url, dest):  # noqa: ANN001
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(b"not really an image")

    with patch.object(csg, "_download_image_bytes", _fake_download):
        sheet = asyncio.run(
            generate_character_sheet(
                description="d",
                name="小明",
                features="f",
                image_gen=g,
                storage_dir=str(tmp_path),
                project_id="p1",
            )
        )
    # 三个视图文件全损坏 → build_composite_image ValueError → 吞掉，composite_url=""
    assert sheet.composite_url == ""
    assert sheet.front_url and sheet.side_url and sheet.back_url, "三视图本体不受拼图失败影响"


# ── X4: 上传链路拼图 ───────────────────────────────────────────────────────


def test_tc_x4_01_three_view_upload_builds_composite(tmp_path):
    """TC-X4-01：3 张上传 → 合成图生成（3 格横排）。"""
    import asyncio

    from PIL import Image

    from app.gateway.video_gen.character_sheet_generator import save_uploaded_character_sheet

    def _png_bytes(rgb: tuple[int, int, int]) -> bytes:
        import io

        buf = io.BytesIO()
        Image.new("RGB", (400, 300), rgb).save(buf, format="PNG")
        return buf.getvalue()

    sheet = asyncio.run(
        save_uploaded_character_sheet(
            name="小明",
            features="",
            front_bytes=_png_bytes((200, 30, 40)),
            side_bytes=_png_bytes((30, 200, 40)),
            back_bytes=_png_bytes((40, 30, 200)),
            storage_dir=str(tmp_path),
            project_id="p1",
        )
    )
    assert sheet.composite_url, "3 张上传须生成合成图"
    p = Path(sheet.composite_url)
    assert p.exists() and "character_sheets/p1/" in p.as_posix()
    img = Image.open(p)
    assert img.size == (1152, 1152), f"3 张上传 → 1152×1152 方形画布（A 2026-08-31），实际={img.size}"


def test_tc_x4_02_front_only_upload_composite_is_copy(tmp_path):
    """TC-X4-02：仅 front 上传 → 合成图 = front 副本（字节一致，单图直出不缩放）。"""
    import asyncio
    import io

    from PIL import Image

    from app.gateway.video_gen.character_sheet_generator import save_uploaded_character_sheet

    buf = io.BytesIO()
    Image.new("RGB", (500, 500), (10, 120, 240)).save(buf, format="PNG")
    raw = buf.getvalue()

    sheet = asyncio.run(
        save_uploaded_character_sheet(
            name="小明",
            features="",
            front_bytes=raw,
            side_bytes=None,
            back_bytes=None,
            storage_dir=str(tmp_path),
            project_id="p1",
        )
    )
    assert sheet.composite_url, "仅 front 上传也须有合成图（=front 副本）"
    assert Path(sheet.composite_url).read_bytes() == raw, "单图合成 = 原图复制（字节一致）"
    assert sheet.composite_url != sheet.front_url, "合成图是独立副本文件（不与 front 同路径）"


# ── X5: composite_path_for_sheet 惰性兜底 ─────────────────────────────────


def _storage_with_views(tmp_path: Path, pid: str = "p1", name: str = "s1") -> dict:
    """种一个存量 sheet：真实三视图文件落 character_sheets/{pid}/，dict 无 composite_url。"""
    from PIL import Image

    d = tmp_path / "character_sheets" / pid
    d.mkdir(parents=True, exist_ok=True)
    entry = {}
    for tag, rgb in (("front", (200, 30, 40)), ("side", (30, 200, 40)), ("back", (40, 30, 200))):
        p = d / f"{name}_{tag}.png"
        Image.new("RGB", (300, 400), rgb).save(p, format="PNG")
        entry[f"{tag}_url"] = str(p)
    return entry


def test_tc_x5_01_lazy_build_and_cache(tmp_path):
    """TC-X5-01：无 composite_url 存量 sheet 首拼成功且文件缓存（下次同参直用）。"""
    from app.gateway.video_gen.service import composite_path_for_sheet

    entry = _storage_with_views(tmp_path)
    assert not entry.get("composite_url")
    got = composite_path_for_sheet(entry, str(tmp_path))
    assert got, "存量 sheet 须能惰性拼出合成图"
    p = Path(got)
    assert p.exists(), f"拼图须落盘缓存：{p}"
    assert "character_sheets/p1/composite_" in p.as_posix(), f"缓存路径拍死 character_sheets/{{pid}}/composite_*，实际={p}"
    from PIL import Image

    assert Image.open(p).size == (1152, 1152), "A（2026-08-31）：惰性拼图同款方形画布"

    # 幂等复用：entry 落 composite_url 后（模拟下次读到缓存）→ 直用不再新拼
    entry2 = {**entry, "composite_url": got}
    again = composite_path_for_sheet(entry2, str(tmp_path))
    assert again == got


def test_tc_x5_02_existing_composite_used_no_rebuild(tmp_path):
    """TC-X5-02：已有 composite_url（文件存在）→ 直用不再拼。"""
    from PIL import Image

    from app.gateway.video_gen.service import composite_path_for_sheet

    d = tmp_path / "character_sheets" / "p2"
    d.mkdir(parents=True)
    c = d / "composite_deadbeef.png"
    Image.new("RGB", (100, 100), (1, 2, 3)).save(c, format="PNG")
    entry = {"front_url": "", "side_url": "", "back_url": "", "composite_url": str(c)}
    assert composite_path_for_sheet(entry, str(tmp_path)) == str(c)
    # 目录内无新增 composite 文件（未触发重拼）
    assert len(list(d.glob("composite_*.png"))) == 1


def test_tc_x5_unusable_returns_none(tmp_path):
    """X5 边界：composite_url 指向不存在文件且无可拼视图 → None；拼图失败 → None。"""
    from app.gateway.video_gen.service import composite_path_for_sheet

    # 无 composite_url、无视图文件 → None
    entry = {"front_url": str(tmp_path / "gone_f.png"), "side_url": "", "back_url": ""}
    assert composite_path_for_sheet(entry, str(tmp_path)) is None
    # composite_url 指向不存在文件 + 视图也缺 → None
    entry2 = {**entry, "composite_url": str(tmp_path / "gone_c.png")}
    assert composite_path_for_sheet(entry2, str(tmp_path)) is None


# ── X6: roster 组装携带 composite ─────────────────────────────────────────


def test_tc_x6_01_roster_entry_carries_composite(tmp_path):
    """TC-X6-01：roster 条目含可用 composite 路径（门控/首主角首键语义不变）。"""
    from app.gateway.video_gen.service import _build_character_roster

    entry = _storage_with_views(tmp_path, pid="p1", name="小明")
    payload = {
        "effective_style_source": "project",
        "course_project_id": "p1",
        "character_sheets": [{"id": "aaaa1111", "name": "小明", "features": "", **entry}],
    }
    roster = _build_character_roster(payload, storage_dir=str(tmp_path))
    assert "小明" in roster
    r = roster["小明"]
    assert r.get("composite") and Path(r["composite"]).exists(), f"roster 条目须带可用 composite，实际={r}"
    # 三视图键保留（回退路径用）
    assert r.get("front_url") == entry["front_url"]

    # 门控不变：非 project → {}；无 storage_dir（窄签名兼容）→ 无 composite 键
    assert _build_character_roster({"effective_style_source": "task"}) == {}
    legacy = _build_character_roster(payload)
    assert legacy["小明"].get("composite", "") == "" or "composite" not in legacy["小明"], "不传 storage_dir 时不解析 composite（回退视图逻辑）"


# ── X7: 注入改造（核心）────────────────────────────────────────────────────

# roster 带 composite 键（X6 组装形态；纯 dict，无盘依赖——文件存在性由 fill 层校验）
_ROSTER_C = {
    "小明": {"front_url": "/sheets/xm_f.png", "side_url": "/sheets/xm_s.png", "back_url": "/sheets/xm_b.png", "composite": "/sheets/xm_c.png"},
    "小红": {"front_url": "/sheets/xh_f.png", "side_url": "/sheets/xh_s.png", "back_url": "/sheets/xh_b.png", "composite": "/sheets/xh_c.png"},
    "老王": {"front_url": "/sheets/lw_f.png", "side_url": "/sheets/lw_s.png", "back_url": "/sheets/lw_b.png", "composite": "/sheets/lw_c.png"},
    "阿七": {"front_url": "/sheets/q7_f.png", "side_url": "/sheets/q7_s.png", "back_url": "/sheets/q7_b.png", "composite": "/sheets/q7_c.png"},
}


def _frame_with(vc=None, vp=None):
    from app.gateway.video_gen.storyboard import StoryboardFrame

    return StoryboardFrame(index=0, narration="n", visible_characters=vc, visible_props=vp)


def test_tc_x7_01_two_declared_one_composite_each():
    """TC-X7-01：声明 2 人 → refs 恰 2 张且均为 composite 路径（每实体 1 张）。"""
    from app.gateway.video_gen.visual_sources.ai_image import _frame_character_ref_paths

    f = _frame_with(vc=[{"name": "小红"}, {"name": "老王"}])
    refs = _frame_character_ref_paths(f, _ROSTER_C)
    assert refs == ["/sheets/xh_c.png", "/sheets/lw_c.png"], f"声明 2 人 → 各 1 张 composite，实际={refs}"


def test_tc_x7_02_three_declared_three_composites():
    """TC-X7-02：声明 3 人 → 3 张 composite（T2⑤ 三人同框根治：3×1 卡工作点 6）。"""
    from app.gateway.video_gen.visual_sources.ai_image import _frame_character_ref_paths

    f = _frame_with(vc=[{"name": "小明"}, {"name": "小红"}, {"name": "老王"}])
    refs = _frame_character_ref_paths(f, _ROSTER_C)
    assert refs == ["/sheets/xm_c.png", "/sheets/xh_c.png", "/sheets/lw_c.png"], f"实际={refs}"


def test_tc_x7_03_none_frame_first_protagonist_one_composite():
    """TC-X7-03：None 帧（存量）→ 首主角 1 张 composite（与旧三视图全传信息等价）。"""
    from app.gateway.video_gen.visual_sources.ai_image import _frame_character_ref_paths

    refs = _frame_character_ref_paths(_frame_with(), _ROSTER_C)
    assert refs == ["/sheets/xm_c.png"], f"None → 首主角（roster 首键）1 张 composite，实际={refs}"
    # 空镜 [] 语义不变
    assert _frame_character_ref_paths(_frame_with(vc=[]), _ROSTER_C) == []


def test_tc_x7_04_no_composite_falls_back_to_views():
    """TC-X7-04：实体无 composite（X5 返回 None / 未解析）→ 回退现视图逻辑
    （默认三视图全传；实验开关仅在此兜底路径生效）。"""
    from app.gateway.video_gen.visual_sources.ai_image import _frame_character_ref_paths

    roster = {name: {k: v for k, v in entry.items() if k != "composite"} for name, entry in _ROSTER_C.items()}
    f = _frame_with(vc=[{"name": "小红"}, {"name": "老王", "view": "back"}])
    refs = _frame_character_ref_paths(f, roster)
    assert refs == ["/sheets/xh_f.png", "/sheets/xh_s.png", "/sheets/xh_b.png", "/sheets/lw_f.png", "/sheets/lw_s.png", "/sheets/lw_b.png"], f"无 composite → 回退三视图全传（view 提示对角色不再生效——合成图恒含全视角），实际={refs}"


@pytest.mark.asyncio
async def test_tc_x7_05_four_declared_truncated_with_sidecar(monkeypatch):
    """TC-X7-05：4 人声明 → 3 张 composite + char_cap_3 留痕（第 4 人纯文本生成属预期）。"""
    import base64
    import tempfile

    from app.gateway.video_gen.providers.base import ImageRef
    from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig
    from app.gateway.video_gen.visual_sources.ai_image import AIImageVisualSource

    captured: list[dict] = []

    class _Cap:
        async def generate_image(self, prompt: str, **kw):
            captured.append(kw)
            return ImageRef(url="data:image/png;base64," + base64.b64encode(b"y" * 700).decode(), width=10, height=10, source="t")

    sb = Storyboard(
        title="t",
        config=StoryboardConfig(video_type="ai_image", width=1080, height=1920),
        frames=[_frame_with(vc=[{"name": n} for n in ("小明", "小红", "老王", "阿七")])],
    )
    src = AIImageVisualSource(_Cap())
    with tempfile.TemporaryDirectory() as td:
        await src.render(sb, task_dir=Path(td), character_roster=_ROSTER_C)
        refs = captured[0].get("reference_image_paths")
        assert refs == ["/sheets/xm_c.png", "/sheets/xh_c.png", "/sheets/lw_c.png"], f"4 人声明 → 3 张 composite，实际={refs}"
        sidecar = json.loads((Path(td) / "sidecar" / "ref_injection.json").read_text(encoding="utf-8"))
        rec = [r for r in sidecar if r.get("frame_index") == 0][0]
        assert any("char_cap_3" in str(t) for t in rec["truncated"]), f"截断须记 char_cap_3，实际={rec}"
        assert any("阿七" in str(t) for t in rec["truncated"]), "被截断实体（第 4 人）须留痕"


def test_tc_x7_video_side_composite_and_prop_unchanged(tmp_path):
    """X7 超卡面追认用例（视频侧同构）：_video_frame_entity_refs 角色分支恒 composite、
    角色 view 提示退役（决策 3）、道具 view 逻辑保留（不改项）、None → 首主角 1 张。"""
    from PIL import Image

    from app.gateway.video_gen.visual_sources.ai_video import _video_frame_entity_refs

    # 道具分支查盘（file_missing 跳过纪律）——须真实文件
    pdir = tmp_path / "props" / "p1"
    pdir.mkdir(parents=True)
    main_p, extra_p = pdir / "main.png", pdir / "extra0.png"
    Image.new("RGB", (60, 60), (9, 9, 9)).save(main_p, format="PNG")
    Image.new("RGB", (60, 60), (8, 8, 8)).save(extra_p, format="PNG")
    prop_roster = {"保温杯": {"main_image": str(main_p), "extra_images": [str(extra_p)]}}

    # 声明帧：角色 composite（view=side 提示对角色不再生效）+ 道具 main_image 照旧
    f = _frame_with(vc=[{"name": "小明", "view": "side"}], vp=[{"name": "保温杯"}])
    injected, declared, truncated, skipped, names = _video_frame_entity_refs(f, _ROSTER_C, prop_roster)
    assert [e["path"] for e in injected] == ["/sheets/xm_c.png", str(main_p)], f"实际={injected}"
    assert names == ["小明", "保温杯"], "names 与注入 1:1 同序（M4 带名方言前提）"

    # 道具 view=1 → extra0（道具 view 逻辑不动）
    f2 = _frame_with(vc=[{"name": "小明"}], vp=[{"name": "保温杯", "view": 1}])
    injected2, *_, names2 = _video_frame_entity_refs(f2, _ROSTER_C, prop_roster)
    assert [e["path"] for e in injected2] == ["/sheets/xm_c.png", str(extra_p)]
    assert names2 == ["小明", "保温杯"]

    # None 帧 → 首主角 1 张 composite（旧三视图全传语义升级）
    injected3, declared3, _, _, names3 = _video_frame_entity_refs(_frame_with(), _ROSTER_C, {})
    assert [e["path"] for e in injected3] == ["/sheets/xm_c.png"], f"None → 首主角 1 张 composite，实际={injected3}"
    assert names3 is None, "None 帧非 1:1 → names=None（现状文案）"

    # 无 composite → 回退视图选择（兜底路径，view 提示在回退路径仍生效）
    roster = {name: {k: v for k, v in entry.items() if k != "composite"} for name, entry in _ROSTER_C.items()}
    injected4, _, _, _, _ = _video_frame_entity_refs(_frame_with(vc=[{"name": "小明", "view": "side"}]), roster, {})
    assert [e["path"] for e in injected4] == ["/sheets/xm_s.png"]


# ── X11: 主链路坏路径守卫 + composite serve（X11 资产页 UX 包后端侧）─────────


def test_tc_x11_02_text_reference_guard(tmp_path):
    """TC-X11-02：character_reference 回退分支加路径形态校验——非既有文件/URL 的
    纯文本（如中文描述）不作 ref 返回（对齐 vimax 桥 _is_image_reference 守卫语义，
    防中文描述流向 360 edits）；合法路径/URL 照常。"""
    from app.gateway.video_gen.service import _character_ref_paths_from_payload

    # 纯中文描述 → None（不作 ref）
    assert _character_ref_paths_from_payload({"character_reference": "一位戴眼镜的年轻女讲师，白色衬衫"}) is None
    # 既有文件路径 → [path]
    f = tmp_path / "ref.png"
    f.write_bytes(b"\x89PNG")
    assert _character_ref_paths_from_payload({"character_reference": str(f)}) == [str(f)]
    # URL / data: → 照常（vimax 同款前缀口径）
    assert _character_ref_paths_from_payload({"character_reference": "https://example.com/c.png"}) == ["https://example.com/c.png"]
    assert _character_ref_paths_from_payload({"character_reference": "data:image/png;base64,xxx"}) == ["data:image/png;base64,xxx"]
    # sheets[0] 三视图优先级不变（守卫只影响回退分支）
    assert _character_ref_paths_from_payload({"character_sheets": [{"front_url": "/f.png", "side_url": "", "back_url": ""}], "character_reference": "中文描述"}) == ["/f.png"]


@pytest.mark.asyncio
async def test_tc_x11_composite_serve_by_id(tmp_path):
    """X11-2：serve 端点扩展 tag=composite（同构既有 by-id 端点）——200 字节回放；
    traversal 守卫（白名单 tag）与归属校验照旧。"""
    from unittest.mock import AsyncMock, MagicMock

    from fastapi import FastAPI
    from PIL import Image

    from app.gateway.video_gen.config import VideoGenConfig
    from app.gateway.video_gen.providers.registry import ProviderRegistry
    from app.gateway.video_gen.service import VideoGenService

    cfg = VideoGenConfig(qihu_api_key="t", script_model="m")
    svc = VideoGenService(repo=MagicMock(), config=cfg, providers=ProviderRegistry(cfg), storage_dir=str(tmp_path), sandbox_provider=AsyncMock())

    from app.gateway.routers.video_gen import router

    app = FastAPI()
    app.include_router(router)
    app.state.video_gen_service = svc

    pid = "cp_x11srv001"
    user_dir = tmp_path / "course_projects" / "test-user-autouse"
    user_dir.mkdir(parents=True)
    sheet_dir = tmp_path / "character_sheets" / pid
    sheet_dir.mkdir(parents=True)
    comp = sheet_dir / "composite_abc12345.png"
    Image.new("RGB", (64, 64), (5, 5, 5)).save(comp, format="PNG")
    (user_dir / f"{pid}.json").write_text(
        json.dumps(
            {
                "id": pid,
                "user_id": "test-user-autouse",
                "title": "t",
                "character_sheets": [{"id": "aaaa1111", "name": "小明", "front_url": "", "side_url": "", "back_url": "", "composite_url": str(comp), "features": ""}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/video/projects/{pid}/character-sheets/aaaa1111/composite")
        assert resp.status_code == 200, resp.text
        assert resp.content == comp.read_bytes()
        # 白名单外 tag 照旧 404（traversal 守卫不松）
        resp2 = await client.get(f"/api/video/projects/{pid}/character-sheets/aaaa1111/../../etc")
        assert resp2.status_code in (404, 422)
        # 无 composite_url 的 sheet → 404
        (user_dir / f"{pid}.json").write_text(
            json.dumps({"id": pid, "user_id": "test-user-autouse", "title": "t", "character_sheets": [{"id": "bbbb2222", "name": "小红", "front_url": "", "side_url": "", "back_url": "", "features": ""}]}, ensure_ascii=False),
            encoding="utf-8",
        )
        resp3 = await client.get(f"/api/video/projects/{pid}/character-sheets/bbbb2222/composite")
        assert resp3.status_code == 404


# ── X8: M4 带名方言文案（用户拍板 2026-08-27：角色/道具区分措辞）──────────────


def test_tc_x8_01_named_copy_distinguishes_character_and_prop():
    """TC-X8-01：带名文案含「图片1为角色A的三视图」逐字；道具条目区分措辞
    「图片j为{道具}的产品参考图」（用户拍板——道具实际只传 1 张主图）。"""
    from app.gateway.video_gen.prompt_translators.vocab import seedance_reference_injection

    s = seedance_reference_injection(2, "character", entity_names=["角色A", "产品B"], prop_count=1)
    assert s == ("参考图分工：图片1为角色A的三视图，图片2为产品B的产品参考图。仅生成被提及的角色与产品，外观与服装保持与对应参考图一致，场景与动作按本镜描述生成。"), f"实际={s}"

    # 纯角色（prop_count 缺省 0，窄签名零改动）
    s2 = seedance_reference_injection(1, "character", entity_names=["角色A"])
    assert s2 == ("参考图分工：图片1为角色A的三视图。仅生成被提及的角色与产品，外观与服装保持与对应参考图一致，场景与动作按本镜描述生成。"), f"实际={s2}"


def test_tc_x8_02_names_none_unchanged():
    """TC-X8-02：names=None / 其他 kind → 与现状逐字一致（兼容铁律）。"""
    from app.gateway.video_gen.prompt_translators.vocab import seedance_reference_injection

    assert seedance_reference_injection(0, "character", entity_names=None) == ""
    assert seedance_reference_injection(3, "character") == "参考图为角色外观参考，角色形象与服装保持一致，场景与动作按本镜描述生成。"
    assert seedance_reference_injection(1, "preview") == "图片1为本镜预览图，作为首帧参考，构图与色调保持一致。"
    assert seedance_reference_injection(1, "group_anchor") == "图片1为同组锚定场景参考，场景与光线保持一致，主体动作按本镜描述执行。"
    assert seedance_reference_injection(2, "character", entity_names=[]) == "参考图为角色外观参考，角色形象与服装保持一致，场景与动作按本镜描述生成。"


def test_tc_x8_prop_truncated_by_reference_count():
    """X8 边界：names 超张数截断（保 1:1）——被截道具不虚报；截断后剩余条目按序标注。"""
    from app.gateway.video_gen.prompt_translators.vocab import seedance_reference_injection

    # 2 角色 + 2 道具，reference_count=3 → 保 2 角色 + 1 道具
    s = seedance_reference_injection(3, "character", entity_names=["甲", "乙", "产品C", "产品D"], prop_count=2)
    assert "图片1为甲的三视图" in s and "图片2为乙的三视图" in s
    assert "图片3为产品C的产品参考图" in s
    assert "产品D" not in s, "被截断道具不得虚报"


def test_tc_x8_translate_passthrough_prop_count():
    """X8：translate_video_prompt → seedance 方言 prop_count 条件传参端到端。"""
    from app.gateway.video_gen.prompt_translators import translate_video_prompt
    from app.gateway.video_gen.storyboard import StoryboardFrame

    fr = StoryboardFrame(
        index=0,
        narration="n",
        duration=5.0,
        image_prompt="p",
        visual_ir={"subject": "一位讲师", "action": "讲解产品", "environment": "教室", "style": "写实"},
    )
    s = translate_video_prompt(fr, provider_name="seedance", reference_count=2, reference_kind="character", entity_names=["角色A", "产品B"], prop_count=1)
    assert "图片1为角色A的三视图，图片2为产品B的产品参考图" in s
    # 不传 prop_count → 全按角色（旧窄签名兼容）
    s2 = translate_video_prompt(fr, provider_name="seedance", reference_count=1, reference_kind="character", entity_names=["角色A"])
    assert "图片1为角色A的三视图" in s2


@pytest.mark.asyncio
async def test_tc_x8_render_level_named_dialect_with_props(tmp_path, monkeypatch):
    """X8（渲染级接线）：roster 模式声明帧（角色 composite + 道具真实文件）→
    seedance 方言 prompt 含区分措辞（角色三视图 / 道具产品参考图）；
    道具文件缺失（names 无道具）→ 不出现产品参考图措辞。"""
    from unittest.mock import MagicMock

    from PIL import Image

    from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame
    from app.gateway.video_gen.visual_sources.ai_video import AIVideoVisualSource

    pdir = tmp_path / "props"
    pdir.mkdir()
    main_p = pdir / "main.png"
    Image.new("RGB", (60, 60), (9, 9, 9)).save(main_p, format="PNG")

    roster = {"小明": {"front_url": "/sheets/xm_f.png", "side_url": "/sheets/xm_s.png", "back_url": "/sheets/xm_b.png", "composite": "/sheets/xm_c.png"}}
    prop_roster = {"保温杯": {"main_image": str(main_p), "extra_images": []}}

    def _frame() -> StoryboardFrame:
        return StoryboardFrame(
            index=0,
            narration="n",
            duration=5.0,
            image_prompt="p",
            visual_ir={"subject": "讲师", "action": "讲解", "environment": "教室", "style": "写实"},
            visible_characters=[{"name": "小明"}],
            visible_props=[{"name": "保温杯"}],
        )

    seen: list[str] = []

    async def fake_gen_video(prompt, *, duration=5.0, image_url=None, image_urls=None, **kw):  # noqa: ANN001,ANN003
        seen.append(prompt)
        return MagicMock(url="http://vid/x")

    async def run(prop_roster_arg):
        sb = Storyboard(title="t", config=StoryboardConfig(video_type="ai_video", width=720, height=1280), frames=[_frame()])
        src = AIVideoVisualSource(MagicMock(generate_video=fake_gen_video, max_video_duration=8.0), concurrency=1, provider_name="seedance")
        await src.render(sb, task_dir=tmp_path, character_roster=roster, prop_roster=prop_roster_arg)

    await run(prop_roster)
    assert any("图片1为小明的三视图，图片2为保温杯的产品参考图" in p for p in seen), f"渲染级方言须区分角色/道具措辞，实际={seen}"

    seen.clear()
    await run({"保温杯": {"main_image": str(pdir / "gone.png"), "extra_images": []}})
    assert any("图片1为小明的三视图" in p for p in seen), f"角色措辞不受道具缺失影响，实际={seen}"
    assert all("产品参考图" not in p for p in seen), f"道具缺失（未注入）不得虚报产品参考图，实际={seen}"


# ── 2026-08-27 复核修补批：X5 缓存复用（3.1）+ 回退方言降级（3.4）─────────────


def test_x5_lazy_cache_reused_across_calls(tmp_path):
    """复核 3.1：存量 sheet（entry 始终无 composite_url）二次调用须复用首拼缓存——
    目录内不产生第 2 个 composite 文件；视图重生成（mtime 更新）→ 旧缓存失效重拼。"""
    import os

    from app.gateway.video_gen.service import composite_path_for_sheet

    entry = _storage_with_views(tmp_path, pid="p9", name="s9")
    first = composite_path_for_sheet(entry, str(tmp_path))
    assert first and Path(first).exists()
    n_files = len(list((tmp_path / "character_sheets" / "p9").glob("composite_*.png")))
    assert n_files == 1

    # 二次调用（entry 仍无 composite_url——项目 JSON 未回写的真实形态）→ 复用同一文件
    second = composite_path_for_sheet(entry, str(tmp_path))
    assert second == first, f"应复用首拼缓存，实际={second}"
    assert len(list((tmp_path / "character_sheets" / "p9").glob("composite_*.png"))) == 1, "不得产生新文件"

    # 视图重生成（mtime 调新）→ 旧缓存失效，重拼新文件
    import time

    future = time.time() + 10
    for tag in ("front", "side", "back"):
        os.utime(entry[f"{tag}_url"], (future, future))
    third = composite_path_for_sheet(entry, str(tmp_path))
    assert third and third != first, "视图更新后须重拼"
    assert len(list((tmp_path / "character_sheets" / "p9").glob("composite_*.png"))) == 2


def test_x7_video_fallback_names_none(tmp_path):
    """复核 3.4：视频侧回退路径（无 composite）→ names=None（方言降通用措辞，
    单张视图不虚标「三视图」）；混合（一角色合成图 + 一角色回退）同样 None。"""
    from app.gateway.video_gen.visual_sources.ai_video import _video_frame_entity_refs

    roster_no_c = {name: {k: v for k, v in entry.items() if k != "composite"} for name, entry in _ROSTER_C.items()}
    # 纯回退：声明 1 人 → 视图路径 + names None
    injected, _, _, _, names = _video_frame_entity_refs(_frame_with(vc=[{"name": "小明", "view": "side"}]), roster_no_c, {})
    assert [e["path"] for e in injected] == ["/sheets/xm_s.png"]
    assert names is None, f"回退路径 names 须 None（方言通用措辞），实际={names}"
    # 混合：小明有 composite + 小红无 → names 也 None（防部分虚标）
    mixed = {"小明": dict(_ROSTER_C["小明"]), "小红": {k: v for k, v in _ROSTER_C["小红"].items() if k != "composite"}}
    injected2, _, _, _, names2 = _video_frame_entity_refs(_frame_with(vc=[{"name": "小明"}, {"name": "小红"}]), mixed, {})
    assert [e["path"] for e in injected2] == ["/sheets/xm_c.png", "/sheets/xh_f.png"]
    assert names2 is None, f"含回退的混合帧 names 须 None，实际={names2}"
    # 全合成图对照：names 正常带名（回守既有语义）
    _, _, _, _, names3 = _video_frame_entity_refs(_frame_with(vc=[{"name": "小明"}]), _ROSTER_C, {})
    assert names3 == ["小明"]


def test_x5_cache_isolated_per_sheet(tmp_path):
    """复核修补④（2026-08-28）：character_sheets/{pid}/ 是多 sheet 共享目录——A 角色的
    合成图缓存不得被 B 角色复用（修前 glob 无 key，B 会错配 A 的合成图 → 全角色
    同脸事故，真实链路实证）。各自独立缓存 + 二次调用各自复用。"""
    from PIL import Image

    from app.gateway.video_gen.service import composite_path_for_sheet

    d = tmp_path / "character_sheets" / "p7"
    d.mkdir(parents=True)

    def _mk(sid: str, rgb: tuple) -> dict:
        entry = {"id": sid}
        for tag in ("front", "side", "back"):
            p = d / f"{sid}_{tag}.png"
            Image.new("RGB", (300, 400), rgb).save(p, format="PNG")
            entry[f"{tag}_url"] = str(p)
        return entry

    a = _mk("aaaa1111", (200, 30, 40))
    b = _mk("bbbb2222", (30, 200, 40))
    ca = composite_path_for_sheet(a, str(tmp_path))
    cb = composite_path_for_sheet(b, str(tmp_path))
    assert ca and cb and ca != cb, f"两角色须各有独立合成图缓存，实际 a={ca} b={cb}"
    assert "composite_aaaa1111_" in ca and "composite_bbbb2222_" in cb
    # 二次调用各自复用（不串号、不新增文件）
    assert composite_path_for_sheet(a, str(tmp_path)) == ca
    assert composite_path_for_sheet(b, str(tmp_path)) == cb
    assert len(list(d.glob("composite_*.png"))) == 2


# ── X12（2026-08-28 用户拍板）：图片侧带名指代声明（同 X8 模板，1:1 纪律）─────────


def _x12_frame(vc=None, vp=None):
    from app.gateway.video_gen.storyboard import StoryboardFrame

    return StoryboardFrame(index=0, narration="n", visible_characters=vc, visible_props=vp)


def test_x12_declaration_wording_positions_and_anchor():
    """声明 2 角色 + 1 道具 + 锚图 → 参考图分工：角色三视图 / 道具产品参考图 /
    图片4为本系列风格参考（位置编号正确）。"""
    from app.gateway.video_gen.visual_sources.ai_image import build_image_ref_declaration

    frame = _x12_frame(vc=[{"name": "小红"}, {"name": "老王"}], vp=[{"name": "保温杯"}])
    decl = build_image_ref_declaration(
        frame,
        {"小红": {"composite": "/c/xh.png"}, "老王": {"composite": "/c/lw.png"}},
        {"保温杯": {"main_image": "/p/main.png"}},
        char_refs=["/c/xh.png", "/c/lw.png"],
        prop_kept=["/p/main.png"],
        has_anchor=True,
        final_refs=["/c/xh.png", "/c/lw.png", "/p/main.png", "/a.png"],
    )
    assert decl.startswith("参考图分工："), f"实际={decl}"
    assert "图片1为小红的三视图" in decl and "图片2为老王的三视图" in decl
    assert "图片3为保温杯的产品参考图" in decl
    assert "图片4为本系列风格参考" in decl, f"锚图须按末位编号，实际={decl}"


def test_x12_no_declaration_when_not_1to1():
    """回退（角色无 composite）/ None 帧 / 名图数不齐（cap 截断）→ 均不加声明。"""
    from app.gateway.video_gen.visual_sources.ai_image import build_image_ref_declaration

    f = _x12_frame(vc=[{"name": "小红"}])
    # 角色无 composite（回退三视图，1 人 3 张非 1:1）
    assert build_image_ref_declaration(f, {"小红": {"front_url": "/f.png"}}, {}, char_refs=["/f.png", "/s.png", "/b.png"], prop_kept=[], has_anchor=False, final_refs=["/f.png", "/s.png", "/b.png"]) == ""
    # None 帧（存量首主角单张）→ 不带名
    assert build_image_ref_declaration(_x12_frame(), {"小红": {"composite": "/c.png"}}, {}, char_refs=["/c.png"], prop_kept=[], has_anchor=False, final_refs=["/c.png"]) == ""
    # cap 截断错位（final_refs 少于 名+锚）→ 不虚报
    assert build_image_ref_declaration(f, {"小红": {"composite": "/c.png"}}, {}, char_refs=["/c.png"], prop_kept=[], has_anchor=True, final_refs=["/c.png"]) == ""
    # 道具缺失（kept 少于声明）→ 不虚报
    f2 = _x12_frame(vc=[{"name": "小红"}], vp=[{"name": "保温杯"}])
    assert build_image_ref_declaration(f2, {"小红": {"composite": "/c.png"}}, {"保温杯": {"main_image": "/p.png"}}, char_refs=["/c.png"], prop_kept=[], has_anchor=False, final_refs=["/c.png"]) == ""


@pytest.mark.asyncio
async def test_x12_fill_end_to_end_prompt_prefixed(tmp_path):
    """fill 端到端：roster 声明帧（2 角色合成图 + 1 道具真实文件 + 锚图）→
    发给 provider 的 prompt 以「参考图分工」开头且 refs 1:1。"""
    import base64

    from PIL import Image

    from app.gateway.video_gen.providers.base import ImageRef
    from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig
    from app.gateway.video_gen.visual_sources.ai_image import AIImageVisualSource

    prop_dir = tmp_path / "props"
    prop_dir.mkdir()
    main_p = prop_dir / "main.png"
    Image.new("RGB", (60, 60), (9, 9, 9)).save(main_p, format="PNG")

    captured: list[dict] = []

    class _Cap:
        async def generate_image(self, prompt: str, **kw):
            captured.append({"prompt": prompt, **kw})
            return ImageRef(url="data:image/png;base64," + base64.b64encode(b"y" * 700).decode(), width=10, height=10, source="t")

    roster = {
        "小红": {"front_url": "/f1", "side_url": "/s1", "back_url": "/b1", "composite": "/c/xh.png"},
        "老王": {"front_url": "/f2", "side_url": "/s2", "back_url": "/b2", "composite": "/c/lw.png"},
    }
    props = {"保温杯": {"main_image": str(main_p), "extra_images": []}}
    frame = _x12_frame(vc=[{"name": "小红"}, {"name": "老王"}], vp=[{"name": "保温杯"}])
    frame.image_prompt = "实验台前两人演示热胀冷缩。"
    sb = Storyboard(title="t", config=StoryboardConfig(video_type="ai_image", width=1080, height=1920), frames=[frame])
    src = AIImageVisualSource(_Cap())
    await src.render(sb, task_dir=tmp_path, character_roster=roster, prop_roster=props, style_anchor_image_paths=["/anchor.png"])
    sent = captured[0]
    assert sent["prompt"].startswith("参考图分工："), f"prompt 须以声明开头，实际={sent['prompt'][:120]}"
    assert "图片1为小红的三视图" in sent["prompt"] and "图片2为老王的三视图" in sent["prompt"]
    assert "图片3为保温杯的产品参考图" in sent["prompt"] and "图片4为本系列风格参考" in sent["prompt"]
    assert "实验台前两人演示热胀冷缩。" in sent["prompt"], "画面描述须保留在声明之后"
    assert sent.get("reference_image_paths") == ["/c/xh.png", "/c/lw.png", str(main_p), "/anchor.png"]
