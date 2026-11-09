"""Tests for PPT font availability detection gate (analyze_fonts).

TDD: these tests were written BEFORE the implementation. Each test documents
the production change that would make it fail (mutation verification target).

Fixtures use python-pptx to generate real .pptx files. East-Asian (ea) fonts
are written via run._r + qn() because python-pptx doesn't expose <a:ea>.
"""

from __future__ import annotations

import shutil
import zipfile

from lxml import etree
from pptx import Presentation
from pptx.oxml.ns import qn

# Module under test — will fail to import until analyze_fonts is implemented
from app.gateway.voice_twin.ppt_extract import _norm, analyze_fonts

# ---------------------------------------------------------------------------
# fc-match fake: simulates a gateway/ppt-converter container that has
# Liberation/Carlito/Caladea/Noto CJK installed but NOT Microsoft YaHei,
# SimSun (宋体), or DengXian (等线).
# ---------------------------------------------------------------------------

_DEFAULT_FC_MAP = {
    # White-list targets (installed — metric-compatible substitutes)
    "liberation sans": "Liberation Sans",
    "carlito": "Carlito",
    "caladea": "Caladea",
    "noto sans cjk sc": "Noto Sans CJK SC",
    "noto serif cjk sc": "Noto Serif CJK SC",
    "liberation serif": "Liberation Serif",
    "liberation mono": "Liberation Mono",
    # General queries — originals NOT installed, fc-match substitutes Noto.
    # Both English and Chinese names are included because fontconfig resolves
    # either form in a real gateway container.
    "microsoft yahei": "Noto Sans CJK SC",
    "microsoft yahei bold": "Noto Sans CJK SC",
    "微软雅黑": "Noto Sans CJK SC",
    "宋体": "Noto Sans CJK SC",
    "simsun": "Noto Sans CJK SC",
    "等线": "Noto Sans CJK SC",
    "dengxian": "Noto Sans CJK SC",
}


def _install_fake_fc(monkeypatch, mapping=None):
    """Patch _fc_match_family to use *mapping* (default = gateway container)."""
    fc_map = dict(_DEFAULT_FC_MAP) if mapping is None else mapping

    def _fake(name):
        return fc_map.get(name)

    monkeypatch.setattr("app.gateway.voice_twin.ppt_extract._fc_match_family", _fake)
    return fc_map


# ---------------------------------------------------------------------------
# Fixture helpers — build real PPTX files with python-pptx
# ---------------------------------------------------------------------------


def _new_pptx() -> Presentation:
    """Blank presentation (one empty slide)."""
    prs = Presentation()
    prs.slides.add_slide(prs.slide_layouts[5])  # blank layout
    return prs


def _add_text(prs: Presentation, text: str = "test", *, latin: str | None = None, ea: str | None = None):
    """Add a textbox with optional latin/ea fonts on the first run.

    *ea* uses run._r + qn('a:ea') because python-pptx doesn't expose <a:ea>.
    """
    slide = prs.slides[0]
    txBox = slide.shapes.add_textbox(50, 50, 300, 50)
    tf = txBox.text_frame
    p = tf.paragraphs[0]
    run = p.add_run()
    run.text = text

    if latin:
        run.font.name = latin  # sets <a:latin typeface=...>

    if ea:
        rPr = run._r.get_or_add_rPr()
        ea_el = rPr.makeelement(qn("a:ea"), {"typeface": ea})
        rPr.append(ea_el)

    return run


def _save_pptx(prs: Presentation, tmp_path) -> str:
    """Save to tmp_path and return the path."""
    path = str(tmp_path / "test.pptx")
    prs.save(path)
    return path


def _set_theme_hans(prs: Presentation, pptx_path: str, typeface: str):
    """Post-process: replace the theme's <a:font script="Hans"> typeface.

    Needed because python-pptx doesn't expose theme font scripts.
    """
    ns = {
        "a": "http://schemas.openxmlformats.org/drawingml/2006/main",
    }
    tmp_path = pptx_path + ".tmp"
    with zipfile.ZipFile(pptx_path, "r") as zin:
        with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                data = zin.read(item.filename)
                if item.filename.startswith("ppt/theme/theme") and item.filename.endswith(".xml"):
                    tree = etree.fromstring(data)
                    for font_el in tree.iter(f"{{{ns['a']}}}font"):
                        if font_el.get("script") == "Hans":
                            font_el.set("typeface", typeface)
                    data = etree.tostring(tree, xml_declaration=True, encoding="UTF-8", standalone=True)
                zout.writestr(item, data)
    shutil.move(tmp_path, pptx_path)


def _rewrite_zip(pptx_path: str, edit=None, extras: dict[str, bytes] | None = None):
    """Copy pptx_path entry-by-entry; *edit(name, data)* returns replacement
    bytes (None = keep entry). *extras* are appended as new entries."""
    tmp_path = pptx_path + ".tmp"
    with zipfile.ZipFile(pptx_path, "r") as zin:
        with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zout:
            for item in zin.infolist():
                data = zin.read(item.filename)
                new_data = edit(item.filename, data) if edit else None
                zout.writestr(item, new_data if new_data is not None else data)
            for name, payload in (extras or {}).items():
                zout.writestr(name, payload)
    shutil.move(tmp_path, pptx_path)


def _inject_embedded_font(pptx_path: str, *typefaces: str):
    """Declare embedded fonts the way real PowerPoint does (OOXML spec layout).

    ISO/IEC 29500-1 §19.2.1: p:embeddedFontLst's parent is p:presentation, so
    the declaration lives in ppt/presentation.xml, inserted at the CT_Presentation
    spec position (after p:notesSz, before p:defaultTextStyle — matching real
    PowerPoint output). Font binaries go to ppt/fonts/fontN.fntdata, wired
    through ppt/_rels/presentation.xml.rels + a fntdata Default in [Content_Types].xml.
    """
    p_ns = "http://schemas.openxmlformats.org/presentationml/2006/main"
    r_ns = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    pkg_rel_ns = "http://schemas.openxmlformats.org/package/2006/relationships"
    ct_ns = "http://schemas.openxmlformats.org/package/2006/content-types"
    font_rel_type = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/font"

    def edit(name, data):
        if name == "ppt/presentation.xml":
            tree = etree.fromstring(data)
            lst = etree.Element(f"{{{p_ns}}}embeddedFontLst")
            notes_sz = tree.find(f"{{{p_ns}}}notesSz")
            if notes_sz is not None:
                notes_sz.addnext(lst)
            else:
                tree.append(lst)  # notesSz missing — fall back to append
            for i, typeface in enumerate(typefaces, 1):
                ef = etree.SubElement(lst, f"{{{p_ns}}}embeddedFont")
                font_el = etree.SubElement(ef, f"{{{p_ns}}}font")
                font_el.set("typeface", typeface)
                regular = etree.SubElement(ef, f"{{{p_ns}}}regular")
                regular.set(f"{{{r_ns}}}id", f"rIdFont{i}")
            return etree.tostring(tree, xml_declaration=True, encoding="UTF-8", standalone=True)
        if name == "ppt/_rels/presentation.xml.rels":
            tree = etree.fromstring(data)
            for i in range(1, len(typefaces) + 1):
                rel = etree.SubElement(tree, f"{{{pkg_rel_ns}}}Relationship")
                rel.set("Id", f"rIdFont{i}")
                rel.set("Type", font_rel_type)
                rel.set("Target", f"fonts/font{i}.fntdata")
            return etree.tostring(tree, xml_declaration=True, encoding="UTF-8", standalone=True)
        if name == "[Content_Types].xml":
            tree = etree.fromstring(data)
            if not any(el.get("Extension") == "fntdata" for el in tree.findall(f"{{{ct_ns}}}Default")):
                default = etree.SubElement(tree, f"{{{ct_ns}}}Default")
                default.set("Extension", "fntdata")
                default.set("ContentType", "application/x-fontdata")
            return etree.tostring(tree, xml_declaration=True, encoding="UTF-8", standalone=True)
        return None

    _rewrite_zip(pptx_path, edit, {f"ppt/fonts/font{i}.fntdata": b"fake-font-data" for i in range(1, len(typefaces) + 1)})


def _inject_legacy_font_table(pptx_path: str, *typefaces: str):
    """Inject the non-standard ppt/fontTable.xml layout (a WordprocessingML
    concept that some third-party PPT generators write), with matching font
    binaries under ppt/fonts/ — the legacy layout the old code was built for.

    Locks the D3 fallback: analyze_fonts must keep honoring this layout.
    """
    p_ns = "http://schemas.openxmlformats.org/presentationml/2006/main"
    a_ns = "http://schemas.openxmlformats.org/drawingml/2006/main"
    r_ns = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

    ef_parts = "".join(f'<p:embeddedFont><p:font typeface="{typeface}"/><p:regular r:id="rId{i}"/></p:embeddedFont>' for i, typeface in enumerate(typefaces, 1))
    ft_xml = f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><a:fontTable xmlns:a="{a_ns}" xmlns:p="{p_ns}" xmlns:r="{r_ns}"><p:embeddedFontLst>{ef_parts}</p:embeddedFontLst></a:fontTable>'
    _rewrite_zip(pptx_path, None, {"ppt/fontTable.xml": ft_xml, "ppt/fonts/font1.fntdata": b"fake-font-data"})


def _inject_orphan_fntdata(pptx_path: str):
    """Write ppt/fonts/font1.fntdata with NO embedded-font declaration anywhere
    (half-baked/corrupt file). Locks D1: orphan binaries don't prove embedding."""
    _rewrite_zip(pptx_path, None, {"ppt/fonts/font1.fntdata": b"fake-font-data"})


# ---------------------------------------------------------------------------
# _norm unit tests
# ---------------------------------------------------------------------------


class TestNorm:
    """Test _norm: strip style suffix + casefold + strip."""

    def test_plain(self):
        assert _norm("Arial") == "arial"

    def test_casefold_chinese(self):
        assert _norm("微软雅黑") == "微软雅黑"

    def test_strips_bold_suffix(self):
        assert _norm("Microsoft YaHei Bold") == "microsoft yahei"

    def test_strips_light_suffix(self):
        # P2-1: Calibri Light → calibri base
        assert _norm("Calibri Light") == "calibri"

    def test_strips_semibold_suffix(self):
        assert _norm("Source Han Sans CN Semibold") == "source han sans cn"

    def test_strip_whitespace(self):
        assert _norm("  Arial  ") == "arial"


# ---------------------------------------------------------------------------
# analyze_fonts: three-state tests
# ---------------------------------------------------------------------------


class TestAnalyzeFontsBasic:
    """Core three-state: risky / safe-whitelisted / safe-installed."""

    def test_microsoft_yahei_is_risky(self, tmp_path, monkeypatch):
        """PPT with ea font 微软雅黑, no embedding → risky.
        Mutation target: remove the general-path fc-match check → this stays empty.
        """
        _install_fake_fc(monkeypatch)
        prs = _new_pptx()
        _add_text(prs, "测试", ea="微软雅黑")
        path = _save_pptx(prs, tmp_path)

        result = analyze_fonts(path)

        risky_names = [n for n, _ in result.risky]
        assert "微软雅黑" in risky_names

    def test_source_han_sans_is_safe(self, tmp_path, monkeypatch):
        """PPT with ea font 思源黑体 CN → NOT risky (white-listed = same as Noto CJK).

        Default theme also has 宋体 via Hans script → that WILL be risky.
        This test asserts 思源黑体 CN specifically is absent from risky list.
        """
        _install_fake_fc(monkeypatch)
        prs = _new_pptx()
        _add_text(prs, "测试", ea="思源黑体 CN")
        path = _save_pptx(prs, tmp_path)
        # Replace theme Hans to 思源黑体 CN so default theme doesn't drag in 宋体
        _set_theme_hans(prs, path, "思源黑体 CN")

        result = analyze_fonts(path)

        risky_names = [n for n, _ in result.risky]
        assert "思源黑体 CN" not in risky_names

    def test_arial_is_safe(self, tmp_path, monkeypatch):
        """PPT with latin Arial → NOT risky (white-listed = Liberation Sans)."""
        _install_fake_fc(monkeypatch)
        prs = _new_pptx()
        _add_text(prs, "test", latin="Arial")
        path = _save_pptx(prs, tmp_path)
        # Clean theme Hans to 宋体 will be risky, but Arial should not be
        _set_theme_hans(prs, path, "思源黑体 CN")

        result = analyze_fonts(path)

        risky_names = [n for n, _ in result.risky]
        assert "Arial" not in risky_names

    def test_embedded_font_not_risky(self, tmp_path, monkeypatch):
        """PPT with 微软雅黑 embedded via the spec layout (p:embeddedFontLst in
        ppt/presentation.xml) → NOT in risky list.

        Mutation target: skip the embedded subtraction → 微软雅黑 reappears.
        """
        _install_fake_fc(monkeypatch)
        prs = _new_pptx()
        _add_text(prs, "测试", ea="微软雅黑")
        path = _save_pptx(prs, tmp_path)
        # Clean theme so only 微软雅黑 is the potential risky
        _set_theme_hans(prs, path, "思源黑体 CN")
        # Now inject embedded font
        _inject_embedded_font(path, "微软雅黑")

        result = analyze_fonts(path)

        risky_names = [n for n, _ in result.risky]
        assert "微软雅黑" not in risky_names
        assert "微软雅黑" in result.embedded


# ---------------------------------------------------------------------------
# Embedded-font declaration layouts (2026-08-13 fix: embeddedFontLst lives in
# ppt/presentation.xml per ISO/IEC 29500-1 §19.2.1 — NOT ppt/fontTable.xml)
# ---------------------------------------------------------------------------


class TestEmbeddedFontDeclarations:
    """Spec-layout embedding + D1/D3 decision locks (plan T4)."""

    def test_embedded_via_presentation_xml_not_risky(self, tmp_path, monkeypatch):
        """Spec layout (p:embeddedFontLst in ppt/presentation.xml) → 等线 NOT
        risky and present in embedded set.

        Mutation target: remove the ppt/presentation.xml parsing branch →
        等线 reappears in risky (embedded stays empty).
        """
        _install_fake_fc(monkeypatch)
        prs = _new_pptx()
        _add_text(prs, "测试", ea="等线")
        path = _save_pptx(prs, tmp_path)
        _set_theme_hans(prs, path, "思源黑体 CN")
        _inject_embedded_font(path, "等线")

        result = analyze_fonts(path)

        risky_names = [n for n, _ in result.risky]
        assert "等线" not in risky_names
        assert "等线" in result.embedded

    def test_legacy_font_table_still_works(self, tmp_path, monkeypatch):
        """ppt/fontTable.xml fallback layout (third-party generators) → 微软雅黑
        NOT risky.

        Mutation target: delete the fontTable.xml fallback branch → 微软雅黑
        reappears in risky.
        """
        _install_fake_fc(monkeypatch)
        prs = _new_pptx()
        _add_text(prs, "测试", ea="微软雅黑")
        path = _save_pptx(prs, tmp_path)
        _set_theme_hans(prs, path, "思源黑体 CN")
        _inject_legacy_font_table(path, "微软雅黑")

        result = analyze_fonts(path)

        risky_names = [n for n, _ in result.risky]
        assert "微软雅黑" not in risky_names
        assert "微软雅黑" in result.embedded

    def test_orphan_fntdata_still_flagged(self, tmp_path, monkeypatch):
        """fntdata present but NO declaration anywhere → 微软雅黑 still risky
        (D1: orphan binaries don't prove embedding; LibreOffice can't use them).

        Mutation target: treat fntdata presence as "has embedding" (the
        rejected diagnosis's fix) → 微软雅黑 vanishes from risky → red.
        """
        _install_fake_fc(monkeypatch)
        prs = _new_pptx()
        _add_text(prs, "测试", ea="微软雅黑")
        path = _save_pptx(prs, tmp_path)
        _set_theme_hans(prs, path, "思源黑体 CN")
        _inject_orphan_fntdata(path)

        result = analyze_fonts(path)

        risky_names = [n for n, _ in result.risky]
        assert "微软雅黑" in risky_names

    def test_partial_embedding_only_flags_unembedded(self, tmp_path, monkeypatch):
        """Two risky fonts declared, only 微软雅黑 embedded → 等线 still risky,
        微软雅黑 not. (PowerPoint silently skips license-blocked fonts — exact
        per-font matching is the whole point of the gate.)

        Mutation target: any all-or-nothing fix (embedded non-empty → skip
        everything) → 等线 vanishes from risky → red.
        """
        _install_fake_fc(monkeypatch)
        prs = _new_pptx()
        _add_text(prs, "测试一", ea="微软雅黑")
        _add_text(prs, "测试二", ea="等线")
        path = _save_pptx(prs, tmp_path)
        _set_theme_hans(prs, path, "思源黑体 CN")
        _inject_embedded_font(path, "微软雅黑")

        result = analyze_fonts(path)

        risky_names = [n for n, _ in result.risky]
        assert "等线" in risky_names
        assert "微软雅黑" not in risky_names

    def test_embedded_name_normalization(self, tmp_path, monkeypatch):
        """Embedded list spells 'microsoft yahei' while the slide declares
        'Microsoft YaHei Bold' (case + style-suffix diff) → _norm on BOTH
        sides matches → NOT risky.

        Mutation target: compare raw strings (declared - embedded) → the
        normalized variants miss each other → risky again → red.
        """
        _install_fake_fc(monkeypatch)
        prs = _new_pptx()
        _add_text(prs, "test", ea="Microsoft YaHei Bold")
        path = _save_pptx(prs, tmp_path)
        _set_theme_hans(prs, path, "思源黑体 CN")
        _inject_embedded_font(path, "microsoft yahei")

        result = analyze_fonts(path)

        risky_names = [n for n, _ in result.risky]
        assert "Microsoft YaHei Bold" not in risky_names
        assert {_norm(e) for e in result.embedded} == {"microsoft yahei"}


# ---------------------------------------------------------------------------
# P1-1: Theme Hans script scan (the critical regression test)
# ---------------------------------------------------------------------------


class TestThemeHansScan:
    """P1-1 hard acceptance: default-theme PPT with +mn-ea → detects 宋体 via Hans script."""

    def test_default_theme_detects_simsun(self, tmp_path, monkeypatch):
        """A default-theme PPT (slide master has +mn-ea) → theme Hans 宋体
        enters declared → risky (宋体 not whitelisted, substituted by Noto).

        Mutation target: remove the Hans script scan → 宋体 disappears from declared.
        """
        _install_fake_fc(monkeypatch)
        # Default python-pptx template: slideMaster has +mn-ea, theme Hans=宋体
        prs = _new_pptx()
        _add_text(prs, "测试中文")  # no explicit font → uses theme defaults
        path = _save_pptx(prs, tmp_path)

        result = analyze_fonts(path)

        declared_norm = {_norm(n) for n in result.declared}
        assert "宋体" in declared_norm or any("宋体" in n for n in result.declared)
        risky_names = [n for n, _ in result.risky]
        assert any("宋体" in n for n in risky_names)

    def test_hant_not_scanned(self, tmp_path, monkeypatch):
        """v3.1: Hant script (新細明體) must NOT be scanned — it's a default
        theme fixed entry that simplified Chinese PPTs never render.

        Mutation target: re-add Hant scan → 新細明體 appears in declared.
        """
        _install_fake_fc(monkeypatch)
        prs = _new_pptx()
        _add_text(prs, "测试", ea="思源黑体 CN")
        path = _save_pptx(prs, tmp_path)
        # Replace Hans to clean font; Hant stays as default 新細明體
        _set_theme_hans(prs, path, "思源黑体 CN")

        result = analyze_fonts(path)

        declared_raw = list(result.declared)
        assert not any("新細明體" in n for n in declared_raw), "Hant script 新細明體 must not be scanned (v3.1 fix)"


# ---------------------------------------------------------------------------
# P2-1: Calibri Light normalization
# ---------------------------------------------------------------------------


class TestCalibriLight:
    """P2-1: majorFont Calibri Light → _norm → calibri → Carlito → safe."""

    def test_calibri_light_via_theme_not_risky(self, tmp_path, monkeypatch):
        """Theme majorFont latin = "Calibri Light" → _norm strips 'Light' →
        'calibri' → white-listed → target 'carlito' → fc-match → Carlito → safe.

        Mutation target: _norm doesn't strip 'Light' → 'calibri light' not in
        _METRIC_COMPATIBLE → general path → risky.
        """
        _install_fake_fc(monkeypatch)
        prs = _new_pptx()
        # Set an explicit latin font on the run to avoid theme ambiguity
        _add_text(prs, "test", latin="Calibri Light")
        path = _save_pptx(prs, tmp_path)
        _set_theme_hans(prs, path, "思源黑体 CN")

        result = analyze_fonts(path)

        risky_names = [n for n, _ in result.risky]
        assert "Calibri Light" not in risky_names


# ---------------------------------------------------------------------------
# P0-1: data source = slides/layouts/masters (not fontTable.xml)
# ---------------------------------------------------------------------------


class TestDataSource:
    """P0-1: no fontTable.xml → scan must still find fonts from slides."""

    def test_no_font_table_still_scans(self, tmp_path, monkeypatch):
        """A PPTX without fontTable.xml (python-pptx default) → declared is non-empty.

        Mutation target: if the code only read fontTable.xml, declared would be empty.
        """
        _install_fake_fc(monkeypatch)
        prs = _new_pptx()
        _add_text(prs, "test", latin="Times New Roman")
        path = _save_pptx(prs, tmp_path)
        _set_theme_hans(prs, path, "思源黑体 CN")

        # Verify no fontTable.xml
        with zipfile.ZipFile(path) as zf:
            assert "ppt/fontTable.xml" not in zf.namelist()

        result = analyze_fonts(path)

        assert len(result.declared) > 0
        declared_norm = {_norm(n) for n in result.declared}
        assert "times new roman" in declared_norm


# ---------------------------------------------------------------------------
# fc-match unavailable (v3.1: None → pass through, no false positive)
# ---------------------------------------------------------------------------


class TestFcUnavailable:
    """fc-match returns None (local dev without fontconfig) → no false positive."""

    def test_none_fc_no_risky(self, tmp_path, monkeypatch):
        """When fc-match is unavailable (returns None), ALL fonts pass through.

        Mutation target: treat None as 'not installed' → everything becomes risky.
        """
        _install_fake_fc(monkeypatch, mapping={})  # everything returns None

        prs = _new_pptx()
        _add_text(prs, "test", latin="Arial", ea="微软雅黑")
        path = _save_pptx(prs, tmp_path)
        _set_theme_hans(prs, path, "思源黑体 CN")

        result = analyze_fonts(path)

        assert result.risky == [], "fc-match None must not produce false positives"


# ---------------------------------------------------------------------------
# Corrupt file → empty FontAnalysis (graceful degradation)
# ---------------------------------------------------------------------------


class TestCorruptFile:
    def test_bad_zip_returns_empty(self, tmp_path, monkeypatch):
        """Corrupt / non-zip file → empty FontAnalysis (extract stage will report its own error)."""
        _install_fake_fc(monkeypatch)
        path = str(tmp_path / "bad.pptx")
        with open(path, "wb") as f:
            f.write(b"not a zip file")

        result = analyze_fonts(path)

        assert result.declared == set()
        assert result.embedded == set()
        assert result.risky == []


# ---------------------------------------------------------------------------
# WPS style suffix normalization (Microsoft YaHei Bold etc.)
# ---------------------------------------------------------------------------


class TestWpsStyleSuffix:
    def test_yahei_bold_normalized_to_risky(self, tmp_path, monkeypatch):
        """WPS writes 'Microsoft YaHei Bold' — _norm strips Bold → 'microsoft yahei'
        → general path → fc-match → Noto → risky.

        Mutation target: _norm doesn't strip 'Bold' → 'microsoft yahei bold' not in
        fc-map → returns None → not risky (wrong!).
        """
        _install_fake_fc(monkeypatch)
        prs = _new_pptx()
        _add_text(prs, "test", ea="Microsoft YaHei Bold")
        path = _save_pptx(prs, tmp_path)
        _set_theme_hans(prs, path, "思源黑体 CN")

        result = analyze_fonts(path)

        risky_names = [n for n, _ in result.risky]
        assert "Microsoft YaHei Bold" in risky_names


# ---------------------------------------------------------------------------
# P2-2: comma-separated family from fc-match
# ---------------------------------------------------------------------------


class TestCommaFamily:
    def test_comma_separated_resolved_family(self, tmp_path, monkeypatch):
        """fc-match may return 'Noto Sans CJK SC,Noto Sans SC' — only first part compared.

        Setup: a font 'TestFont' that fc-match resolves to 'Noto Sans CJK SC,Noto Sans SC'.
        It should be risky (resolved first part = Noto, not the original).
        Mutation target: don't split on comma → full string comparison fails differently.
        """
        _install_fake_fc(
            monkeypatch,
            mapping={
                **_DEFAULT_FC_MAP,
                "testfont": "Noto Sans CJK SC,Noto Sans SC",
            },
        )
        prs = _new_pptx()
        _add_text(prs, "test", latin="TestFont")
        path = _save_pptx(prs, tmp_path)
        _set_theme_hans(prs, path, "思源黑体 CN")

        result = analyze_fonts(path)

        risky_names = [n for n, _ in result.risky]
        assert "TestFont" in risky_names
