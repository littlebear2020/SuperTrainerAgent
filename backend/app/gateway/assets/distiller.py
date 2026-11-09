"""PPT 品牌蒸馏器（确定性 OOXML 提取，v1 = `ooxml-v1`）。

Phase 3.5（§六.B.2）：上传 PPT → 解析品牌要素（主题色板/字体/logo/版式清单）
→ 产出 `trainer_brand.json` 形状的 dict（映射 schema 单一事实源）：

- palette → CourseProject `color_palette.primary`
- typography → `font_family`
- logo → `logo_path`（应用时拷贝进项目资产目录）
- style_tendency → `remotion_theme` 四主题之一

纯 stdlib（zipfile + ElementTree），无 LLM、确定性、可单测。计划推荐方案是
"表单上传后触发 ppt-master 后台蒸馏 run（create-template 工作流）"；实施时定
为确定性提取（偏离记录）：表单预览实际需要的是配色/字体/logo/版式清单，
OOXML 全部承载且零成本零延迟；agent 工作流路线保留在 chat（Create Template
路线）供深度蒸馏，注册表 schema 的 `distiller` 字段留替换位。
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

# OOXML 命名空间（仅本模块用到的最小集）
_NS = {
    "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    "p": "http://schemas.openxmlformats.org/presentationml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}

# 主题色槽位 → trainer_brand palette 语义（dk1/lt1 是文字/背景轴，accent1 是主色惯例）
_COLOR_SLOTS = ("dk1", "lt1", "dk2", "lt2", "accent1", "accent2", "accent3", "accent4", "accent5", "accent6")

# 浏览器可渲染的 logo 候选扩展（wmf/emf 落盘但不可预览，只进 candidates 不作默认）
_LOGO_DISPLAYABLE = (".png", ".jpg", ".jpeg", ".svg", ".webp", ".gif")


_HEX_DIGITS = set("0123456789abcdefABCDEF")

# 条目 4（2026-09-12 缺陷批）：OOXML 蒸馏 zip bomb 防御。上传入口有压缩包上限
# （routers/assets.py MAX_PPTX_BYTES + nginx dedicated location 110M）双层，但高压缩
# 比 XML 可膨胀至数十 GB——解压路径加双保险：条目**声明大小**（info.file_size）
# 预检 + ``zf.open`` 流式限量读防声明造假。
# - 单文件 ≤100MB：随 PPT 上传上限（50→100MB）联动上调，否则 100MB 包内含
#   >50MB 单图的合法模板被误伤；正常模板主题/版式 XML 通常 KB 级，无误伤。
# - 累计 ≤200MB：= 上传上限 2 倍膨胀冗余（资源基准 = 生产 64C/128G/1T、<20 用户；
#   合法模板 ~1.05 倍膨胀绝无命中，GB 级炸弹照样截死）。
MAX_PART_BYTES = 100 * 1024 * 1024
MAX_TOTAL_DECOMPRESSED_BYTES = 200 * 1024 * 1024
_PART_TOO_LARGE = "模板包含异常大的内部部件"


class _DecompressionBudget:
    """跨条目累计解压预算（蒸馏全程共享一个实例）。"""

    def __init__(self) -> None:
        self.total = 0

    def check_declared(self, declared_size: int) -> None:
        if declared_size > MAX_PART_BYTES or self.total + declared_size > MAX_TOTAL_DECOMPRESSED_BYTES:
            raise ValueError(_PART_TOO_LARGE)

    def read(self, zf: zipfile.ZipFile, name: str) -> bytes:
        """双保险读取：先查声明大小，再流式限量读（zip 头的 file_size 可造假）。"""
        self.check_declared(zf.getinfo(name).file_size)
        chunks: list[bytes] = []
        read = 0
        with zf.open(name) as f:
            while True:
                chunk = f.read(1024 * 1024)
                if not chunk:
                    break
                read += len(chunk)
                if read > MAX_PART_BYTES:
                    raise ValueError(_PART_TOO_LARGE)
                chunks.append(chunk)
        data = b"".join(chunks)
        self.total += len(data)
        if self.total > MAX_TOTAL_DECOMPRESSED_BYTES:
            raise ValueError(_PART_TOO_LARGE)
        return data


def _hex(color_node: ET.Element | None) -> str | None:
    """Extract #RRGGBB from a DrawingML color element (srgbClr/sysClr).

    srgbClr 的 val 本身就是 RGB；sysClr 按 ECMA-376 CT_SystemColor 的 val 是
    系统色枚举名（windowText/window 等），RGB 真值在 lastClr——读 val 会把
    "window"（恰 6 字符）当色值产出非法 "#WINDOW"、dk1(windowText) 则解析失败
    → 浅色 deck 全部落默认主题。兜底：个别生成器把 RGB 直接放 sysClr val，
    只认合法 6/8 位十六进制。
    """
    if color_node is None:
        return None
    for tag in ("srgbClr", "sysClr"):
        child = color_node.find(f"a:{tag}", _NS)
        if child is None:
            continue
        raw_values = (child.get("lastClr"), child.get("val")) if tag == "sysClr" else (child.get("val"),)
        for raw in raw_values:
            val = (raw or "").strip()
            if len(val) not in (6, 8) or not all(c in _HEX_DIGITS for c in val):
                continue
            return f"#{(val[2:] if len(val) == 8 else val).upper()}"  # 8 位 = ARGB
    return None


def _theme_colors(zf: zipfile.ZipFile, budget: _DecompressionBudget) -> dict[str, str]:
    """Read ppt/theme/theme1.xml clrScheme (first theme part wins)."""
    names = [n for n in zf.namelist() if n.startswith("ppt/theme/theme") and n.endswith(".xml")]
    if not names:
        return {}
    try:
        root = ET.fromstring(budget.read(zf, sorted(names)[0]))
    except ET.ParseError:
        # 同 _layout_names：畸形 XML 降级为空色板，不让上传变 500
        return {}
    scheme = root.find("a:themeElements/a:clrScheme", _NS)
    if scheme is None:
        return {}
    out: dict[str, str] = {}
    for slot in _COLOR_SLOTS:
        color = _hex(scheme.find(f"a:{slot}", _NS))
        if color:
            out[slot] = color
    return out


def _theme_fonts(zf: zipfile.ZipFile, budget: _DecompressionBudget) -> dict[str, str]:
    """Read major/minor fonts (latin + east-asian priority for zh decks).

    条目 4：第二次读同一 theme 部件（与 _theme_colors 分开的第二处 ``zf.read``）
    同走限量读——两处任一都可被高膨胀比 XML 打爆内存。
    """
    names = [n for n in zf.namelist() if n.startswith("ppt/theme/theme") and n.endswith(".xml")]
    if not names:
        return {}
    try:
        root = ET.fromstring(budget.read(zf, sorted(names)[0]))
    except ET.ParseError:
        return {}
    scheme = root.find("a:themeElements/a:fontScheme", _NS)
    if scheme is None:
        return {}
    out: dict[str, str] = {}
    for key, tag in (("title_font", "majorFont"), ("body_font", "minorFont")):
        node = scheme.find(f"a:{tag}", _NS)
        if node is None:
            continue
        ea = node.find("a:ea", _NS)
        latin = node.find("a:latin", _NS)
        # 中文 deck 场景东亚字体优先；缺 ea 落 latin
        face = (ea.get("typeface") if ea is not None and ea.get("typeface") else None) or (latin.get("typeface") if latin is not None else None)
        if face and face not in ("", "+mn-ea", "+mj-ea", "+mn-lt", "+mj-lt"):
            out[key] = face
    return out


def _layout_names(zf: zipfile.ZipFile, budget: _DecompressionBudget, limit: int = 12) -> list[str]:
    """Slide layout display names (dedup, order of part index)."""
    names = sorted(n for n in zf.namelist() if n.startswith("ppt/slideLayouts/slideLayout") and n.endswith(".xml"))
    out: list[str] = []
    for name in names:
        try:
            root = ET.fromstring(budget.read(zf, name))
        except ET.ParseError:
            continue
        cSld = root.find("p:cSld", _NS)
        display = cSld.get("name") if cSld is not None else None
        if display and display not in out:
            out.append(display)
        if len(out) >= limit:
            break
    return out


def _media_refs(zf: zipfile.ZipFile, budget: _DecompressionBudget) -> tuple[dict[str, int], dict[str, int]]:
    """(refs by media part, bytes by media part) — count r:embed targets in
    slides/masters/layouts rels; a logo is typically referenced on many pages.

    条目 4：rels 部件也走限量读（条目数无上限——只靠读处防御）。sizes 用声明值
    仅作排序展示，真正落盘的 logo 在 distill_pptx 里再过预算。
    """
    refs: dict[str, int] = {}
    sizes: dict[str, int] = {}
    for info in zf.infolist():
        n = info.filename
        if n.startswith("ppt/media/"):
            sizes[n] = info.file_size
    rel_prefixes = ("ppt/slides/_rels/", "ppt/slideMasters/_rels/", "ppt/slideLayouts/_rels/")
    for name in zf.namelist():
        if not name.startswith(rel_prefixes) or not name.endswith(".rels"):
            continue
        try:
            root = ET.fromstring(budget.read(zf, name))
        except ET.ParseError:
            continue
        for rel in root:
            target = rel.get("Target") or ""
            if "media/" not in target:
                continue
            part = "ppt/media/" + target.rsplit("media/", 1)[-1]
            refs[part] = refs.get(part, 0) + 1
    return refs, sizes


def _pick_logo(refs: dict[str, int], sizes: dict[str, int]) -> tuple[str | None, list[dict]]:
    """Default logo = most-referenced displayable image; keep top candidates."""
    ranked = sorted(
        ((part, refs.get(part, 0), sizes.get(part, 0)) for part in sizes),
        key=lambda x: (-x[1], -x[2]),
    )
    # 全量候选（含 thumbnail 等），不按媒体文件名过滤；[:8] 只截断展示长度
    candidates = [{"part": part, "refs": n, "bytes": size, "displayable": Path(part).suffix.lower() in _LOGO_DISPLAYABLE} for part, n, size in ranked][:8]
    default = next((p for p, n, _ in ranked if n >= 2 and Path(p).suffix.lower() in _LOGO_DISPLAYABLE), None)
    if default is None:
        # 次选：引用最高的可显示图（refs==1 也认——小 deck 只出现一次的 logo）
        default = next((p for p, n, _ in ranked if Path(p).suffix.lower() in _LOGO_DISPLAYABLE), None)
    return default, candidates


def _luminance(hex_color: str) -> float:
    r, g, b = (int(hex_color[i : i + 2], 16) for i in (1, 3, 5))
    return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255.0


def _hue_class(hex_color: str) -> str:
    r, g, b = (int(hex_color[i : i + 2], 16) for i in (1, 3, 5))
    mx, mn = max(r, g, b), min(r, g, b)
    if mx == mn or mx == 0:
        return "neutral"
    d = mx - mn
    if mx == r:
        hue = (60 * (g - b) / d) % 360
    elif mx == g:
        hue = 60 * (b - r) / d + 120
    else:
        hue = 60 * (r - g) / d + 240
    if hue < 20 or hue >= 330:
        return "warm"  # 红/橙
    if hue < 70:
        return "warm"  # 黄
    if hue < 170:
        return "green"
    return "blue"  # 青~紫


def remotion_theme_heuristic(colors: dict[str, str]) -> tuple[str, str]:
    """Map a theme palette onto the four remotion_course themes.

    dark-blue (默认) / light / dark-green / warm（与 video_gen.brand_pack.VALID_THEMES
    同集）。依据：dk1 = Text 1（ECMA-376 clrMap：tx1→dk1、bg1→lt1）——dk1 是主
    文字色，深色文字=浅底 deck、浅色文字=深底 deck；accent1 色相决定色系。
    （修前方向写反：把 dk1 当背景色读，标准 Office 黑字白底 deck 被判成深底。）

    边界决策（条目 37，2026-09-12 钉死）：阈值 0.5 用**严格小于**（``lum < 0.5``）。
    近边界色值（#7F7F7F lum≈0.498 vs #808080 lum≈0.502，1/255 之差主题方向翻转）
    是已知理论边界：真实 deck 的 dk1 几乎都贴近纯黑或近白，中灰文字色触发率极低，
    故**钉住现状零行为变更**，不做死区（死区方案需产品确认且会移动所有非灰色
    实际阈值）。#7F7F7F/#808080 两个边界用例已在测试中锁行为，改动此处必红。
    """
    dark = colors.get("dk1")
    accent = colors.get("accent1")
    lum = _luminance(dark) if dark else None
    hue = _hue_class(accent) if accent else "neutral"
    if lum is None:
        return "dark-blue", "无 dk1 主题色，落平台默认"
    if lum < 0.5:
        # 深色文字 = 浅底 deck（含边界 #7F7F7F：lum≈0.498 < 0.5 严格小于成立）
        return ("light", "dk1 为深色文字（浅底 deck），accent 偏中性/冷色") if hue in ("neutral", "blue") else ("warm", "dk1 为深色文字（浅底 deck），accent 偏暖色")
    # 浅色文字 = 深底 deck
    if hue == "green":
        return "dark-green", "dk1 为浅色文字（深底 deck），accent 偏绿"
    if hue == "warm":
        return "warm", "dk1 为浅色文字（深底 deck），accent 偏暖"
    return "dark-blue", "dk1 为浅色文字（深底 deck），accent 偏冷/中性"


def distill_pptx(pptx_path: Path, out_dir: Path) -> dict:
    """Distill a PPTX into the trainer_brand payload; extract the logo file.

    Returns the trainer_brand dict (schema_version/distiller/palette/typography/
    logo/layouts). Raises ValueError on a non-zip / non-presentation payload.
    """
    pptx_path = Path(pptx_path)
    data = pptx_path.read_bytes()
    if len(data) < 4 or data[:2] != b"PK":
        raise ValueError("不是有效的 PPTX 文件（缺 zip 魔数）")
    try:
        zf = zipfile.ZipFile(pptx_path)
    except zipfile.BadZipFile as exc:
        raise ValueError("不是有效的 PPTX 文件（zip 结构损坏）") from exc
    with zf:
        names = set(zf.namelist())
        if not any(n.startswith("ppt/") for n in names):
            raise ValueError("不是有效的 PPTX 演示文稿（缺 ppt/ 部件）")
        budget = _DecompressionBudget()
        colors = _theme_colors(zf, budget)
        fonts = _theme_fonts(zf, budget)
        layouts = _layout_names(zf, budget)
        refs, sizes = _media_refs(zf, budget)
        default_logo, candidates = _pick_logo(refs, sizes)
        logo_filename: str | None = None
        if default_logo:
            out_dir.mkdir(parents=True, exist_ok=True)
            ext = Path(default_logo).suffix.lower()
            logo_filename = f"logo{ext}"
            # 条目 4：logo 复制同样限量（声明预检 + 流式限量写——copyfileobj 无上限，
            # 高膨胀比媒体部件可打爆磁盘）。copyfileobj 未 import 语义不变，仅换流式循环。
            # 复审二轮（2026-09-13）：临时文件 + 原子改名——限量写中途触发 budget 时
            # 不留半截 logo（redistill 失败分支不动存量 brand，截断残留会让存量记录
            # 引用被覆盖的坏 logo）。
            budget.check_declared(zf.getinfo(default_logo).file_size)
            written = 0
            part_path = out_dir / f"{logo_filename}.part"
            try:
                with zf.open(default_logo) as src, open(part_path, "wb") as dst:
                    while True:
                        chunk = src.read(1024 * 1024)
                        if not chunk:
                            break
                        written += len(chunk)
                        if written > MAX_PART_BYTES:
                            raise ValueError(_PART_TOO_LARGE)
                        dst.write(chunk)
            except BaseException:
                part_path.unlink(missing_ok=True)
                raise
            part_path.replace(out_dir / logo_filename)
            budget.total += written

    theme, reason = remotion_theme_heuristic(colors)
    primary = colors.get("accent1") or next(iter(colors.values()), None)
    return {
        "schema_version": 1,
        "distiller": "ooxml-v1",
        "palette": {"primary": primary, "colors": colors},
        "typography": fonts,
        "font_family": fonts.get("body_font") or fonts.get("title_font") or None,
        "logo": {"filename": logo_filename, "source_part": default_logo, "candidates": candidates},
        "style_tendency": {"remotion_theme": theme, "reason": reason},
        "layouts": layouts,
    }
