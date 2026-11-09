"""PPT extraction: extract slides content (title, body, notes) and convert to images."""

from __future__ import annotations

import functools
import logging
import os
import re
import shutil
import subprocess
import zipfile
from pathlib import Path

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


class SlideData:
    """Extracted data for a single slide."""

    __slots__ = ("slide_number", "title", "body", "notes", "has_notes")

    def __init__(self, slide_number: int, title: str, body: str, notes: str) -> None:
        self.slide_number = slide_number
        self.title = title
        self.body = body
        self.notes = notes
        self.has_notes = bool(notes.strip())


class NotesAnalysis:
    """Result of notes sufficiency analysis."""

    __slots__ = ("total_slides", "slides_with_notes", "slides_with_content", "is_sufficient", "avg_length")

    def __init__(self, total_slides: int, slides_with_notes: int, avg_length: float, is_sufficient: bool, slides_with_content: int = 0) -> None:
        self.total_slides = total_slides
        self.slides_with_notes = slides_with_notes
        self.slides_with_content = slides_with_content
        self.avg_length = avg_length
        self.is_sufficient = is_sufficient


class FontAnalysis:
    """Result of PPT font availability analysis.

    *declared*: all font names found in slides/layouts/masters/theme.
    *embedded*: font names from the embedded-font declarations
        (p:embeddedFontLst in ppt/presentation.xml; ppt/fontTable.xml fallback).
    *risky*: list of (declared_name, resolved_or_hint) for fonts that are
        NOT embedded and NOT available (or only via width-mismatched substitute).
    """

    __slots__ = ("declared", "embedded", "risky")

    def __init__(self, declared: set[str], embedded: set[str], risky: list[tuple[str, str]]) -> None:
        self.declared = declared
        self.embedded = embedded
        self.risky = risky


# ---------------------------------------------------------------------------
# Text extraction (python-pptx)
# ---------------------------------------------------------------------------


def extract_pptx_content(pptx_path: str | Path) -> list[SlideData]:
    """Extract title, body, and notes from each slide using python-pptx."""
    from pptx import Presentation

    prs = Presentation(str(pptx_path))
    slides: list[SlideData] = []

    for i, slide in enumerate(prs.slides, 1):
        title = ""
        body_parts: list[str] = []

        for shape in slide.shapes:
            if not shape.has_text_frame:
                continue
            text = shape.text_frame.text.strip()
            if not text:
                continue

            # Detect placeholder type
            is_placeholder = False
            ph_type = None
            try:
                if shape.placeholder_format is not None:
                    is_placeholder = True
                    ph_type = shape.placeholder_format.type
            except (ValueError, AttributeError):
                pass

            if is_placeholder:
                # Title = 1, CenterTitle = 3
                if ph_type in (1, 3):
                    title = text
                    continue
                # Body = 2, Object = 7
                if ph_type in (2, 7):
                    body_parts.append(text)
                    continue

            # Non-placeholder: first text block becomes title if none yet
            if not title:
                title = text
            else:
                body_parts.append(text)

        # Extract notes
        notes = ""
        if slide.has_notes_slide:
            notes_tf = slide.notes_slide.notes_text_frame
            if notes_tf:
                notes = re.sub(r"\s+", " ", notes_tf.text).strip()

        slides.append(
            SlideData(
                slide_number=i,
                title=title,
                body="\n".join(body_parts),
                notes=notes,
            )
        )

    return slides


# ---------------------------------------------------------------------------
# Notes analysis
# ---------------------------------------------------------------------------


# 充分性阈值（D8d，2026-08-09 提为模块常量；依据注释）：
# - 30 字 ≈ 一句完整讲解的最小长度；50 字 ≈ 两句，覆盖率要求可放宽
# - 0.6 / 0.4 覆盖率档：过半有备注=有稿演讲；放宽档要求更长均值
_NOTES_RATIO_STRICT = 0.6
_NOTES_AVG_STRICT = 30
_NOTES_RATIO_RELAXED = 0.4
_NOTES_AVG_RELAXED = 50
# 正文兜底：正文 ≥20 字（约一个要点句）即视为可朗读内容。gate 原本只看备注，
# 「正文充足但备注稀疏」的课件被整单拒绝，而叙述本就有 body fallback
# （production_service 取 slide.body）——gate 与执行不一致，fallback 不可达。
_BODY_FALLBACK_MIN_CHARS = 20
_CONTENT_RATIO_MIN = 0.6


def analyze_notes(slides: list[SlideData]) -> NotesAnalysis:
    """Check whether the PPT has sufficient narration material (notes OR body)."""
    total = len(slides)
    with_notes = sum(1 for s in slides if s.has_notes)
    lengths = [len(s.notes) for s in slides if s.has_notes]
    avg_length = sum(lengths) / len(lengths) if lengths else 0.0
    ratio = with_notes / total if total > 0 else 0.0

    notes_sufficient = (ratio >= _NOTES_RATIO_STRICT and avg_length >= _NOTES_AVG_STRICT) or (ratio >= _NOTES_RATIO_RELAXED and avg_length >= _NOTES_AVG_RELAXED)

    # 正文兜底档：≥60% 页面有「达标备注或达标正文」即放行（body fallback 可达）。
    # 备注也要过同一字数下限——否则「短备注」两字也算内容，gate 形同虚设。
    with_content = sum(1 for s in slides if len(s.notes.strip()) >= _BODY_FALLBACK_MIN_CHARS or len(s.body.strip()) >= _BODY_FALLBACK_MIN_CHARS)
    content_ratio = with_content / total if total > 0 else 0.0
    content_sufficient = content_ratio >= _CONTENT_RATIO_MIN

    return NotesAnalysis(
        total_slides=total,
        slides_with_notes=with_notes,
        avg_length=avg_length,
        is_sufficient=notes_sufficient or content_sufficient,
        slides_with_content=with_content,
    )


# ---------------------------------------------------------------------------
# Font availability analysis (font gate)
# ---------------------------------------------------------------------------

_A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
_P_NS = "http://schemas.openxmlformats.org/presentationml/2006/main"
_THEME_PLACEHOLDERS = {"+mn-lt", "+mj-lt", "+mn-ea", "+mj-ea", "+mn-cs", "+mj-cs"}

# Declared font name (_norm'd) → same-font / official metric-compatible actual family (_norm'd).
# 思源黑体 = Noto Sans CJK SC (same font, same metrics); Calibri→Carlito etc.
_METRIC_COMPATIBLE: dict[str, str] = {
    "思源黑体 cn": "noto sans cjk sc",
    "source han sans cn": "noto sans cjk sc",
    "思源黑体": "noto sans cjk sc",
    "source han sans": "noto sans cjk sc",
    "思源宋体 cn": "noto serif cjk sc",
    "source han serif cn": "noto serif cjk sc",
    "arial": "liberation sans",
    "calibri": "carlito",
    "cambria": "caladea",
    "times new roman": "liberation serif",
    "courier new": "liberation mono",
}

_STYLE_SUFFIXES = (" Semibold", " Bold", " Italic", " Regular", " Light", " Medium", " Thin", " Heavy", " Black")


def _norm(name: str) -> str:
    """Normalize a font name: strip style suffix (WPS 'Microsoft YaHei Bold', 'Calibri Light')
    → base family, then casefold + strip."""
    for suffix in _STYLE_SUFFIXES:
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    return name.casefold().strip()


@functools.lru_cache(maxsize=128)
def _fc_match_family(name: str) -> str | None:
    """Query fc-match for the actual resolved family; return None if fc-match
    is unavailable / errors (so the caller does NOT produce false positives)."""
    try:
        p = subprocess.run(
            ["fc-match", "-f", "%{family}", name],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if p.returncode != 0 or not p.stdout.strip():
            return None
        return p.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def analyze_fonts(pptx_path: str | Path) -> FontAnalysis:
    """Analyze a PPTX for fonts that would be substituted (→ text wrap errors).

    Scans slides/layouts/masters for ``<a:latin|ea|cs>`` declarations, then
    conditionally reads the theme for placeholder references (``+mn-ea`` etc.).
    Cross-references against embedded fonts and fc-match to determine *risky*
    fonts — those not embedded and only available via a width-mismatched substitute.
    """
    from lxml import etree  # python-pptx bundles lxml

    declared: set[str] = set()
    embedded: set[str] = set()
    use_latin_ph = use_ea_ph = False  # whether placeholders are referenced (→ read theme)

    try:
        with zipfile.ZipFile(str(pptx_path)) as zf:
            names = zf.namelist()

            # Pass 1: slides/layouts/masters — direct declarations + placeholder flags
            scan = [n for n in names if n.endswith(".xml") and (n.startswith("ppt/slides/slide") or n.startswith("ppt/slideLayouts/slide") or n.startswith("ppt/slideMasters/slideMaster"))]
            for sname in scan:
                try:
                    tree = etree.fromstring(zf.read(sname))
                except Exception:
                    continue
                for tag in (f"{{{_A_NS}}}latin", f"{{{_A_NS}}}ea", f"{{{_A_NS}}}cs"):
                    for el in tree.iter(tag):
                        tf = el.get("typeface")
                        if not tf:
                            continue
                        if tf in _THEME_PLACEHOLDERS:
                            if tf in ("+mn-lt", "+mj-lt"):
                                use_latin_ph = True
                            if tf in ("+mn-ea", "+mj-ea"):
                                use_ea_ph = True
                        else:
                            declared.add(tf)

            # Pass 2: theme — ONLY when placeholders are referenced
            # (avoids false-flagging 맑은 고딕/Nyala etc. in the script table that
            #  the PPT never actually renders).
            if use_latin_ph or use_ea_ph:
                for tname in [n for n in names if n.startswith("ppt/theme/theme") and n.endswith(".xml")]:
                    try:
                        tree = etree.fromstring(zf.read(tname))
                    except Exception:
                        continue
                    for fk in (f"{{{_A_NS}}}majorFont", f"{{{_A_NS}}}minorFont"):
                        fkel = tree.find(f".//{fk}")
                        if fkel is None:
                            continue
                        if use_latin_ph:  # theme western font (Calibri / Calibri Light)
                            el = fkel.find(f"{{{_A_NS}}}latin")
                            if el is not None and el.get("typeface"):
                                declared.add(el.get("typeface"))
                        if use_ea_ph:  # CJK: ea is often empty; actual font is in <a:font script="Hans">
                            for font_el in fkel.iter(f"{{{_A_NS}}}font"):
                                # Only scan Hans — default theme has Hant=新細明體 which
                                # simplified-Chinese PPTs never render; scanning it
                                # would false-flag every +mn-ea PPT (v3.1 fix).
                                if font_el.get("script") == "Hans":
                                    tf = font_el.get("typeface")
                                    if tf:
                                        declared.add(tf)

            # Embedded fonts: 规范位置是 ppt/presentation.xml 的 p:embeddedFontLst
            # (ISO/IEC 29500-1 §19.2.1, 父元素 p:presentation §19.2.1.26)。
            # ppt/fontTable.xml 非 PPTX 标准部件（那是 Word 的概念），仅作第三方生成器回退。
            for decl_part in ("ppt/presentation.xml", "ppt/fontTable.xml"):
                if decl_part not in names:
                    continue
                try:
                    tree = etree.fromstring(zf.read(decl_part))
                    for ef in tree.iter(f"{{{_P_NS}}}embeddedFont"):
                        fel = ef.find(f"{{{_P_NS}}}font")
                        if fel is not None and fel.get("typeface"):
                            embedded.add(fel.get("typeface"))
                except Exception:
                    # 解析失败 → 无法确认嵌入 → 照常按未嵌入判定（与 LibreOffice
                    # 行为一致：它同样依赖该声明）。不静默当作"已嵌入"放行。
                    logger.warning("Failed to parse embedded font list from %s", decl_part)

    except (zipfile.BadZipFile, OSError):
        return FontAnalysis(set(), set(), [])  # corrupt → let extract stage report its own error

    # Judgment: normalize to base first, then fc-match the *target* (not original name)
    risky: list[tuple[str, str]] = []
    embedded_norm = {_norm(e) for e in embedded}
    for name in sorted(declared):
        if _norm(name) in embedded_norm:
            continue  # embedded (双侧归一化: 大小写/样式后缀差异不误判)

        base = _norm(name)
        if base in _METRIC_COMPATIBLE:
            target = _METRIC_COMPATIBLE[base]
            resolved = _fc_match_family(target)  # check if target is installed
            if resolved is None:
                continue  # fc unavailable → don't false-positive (local dev, v3.1)
            if _norm(resolved.split(",")[0].strip()) == target:
                continue  # target installed → safe
            risky.append((name, f"{target}(未安装)"))
            continue
        resolved = _fc_match_family(base)  # general font: check if base is installed as-is
        if resolved is None:
            continue  # fc unavailable → don't false-positive
        resolved_first = _norm(resolved.split(",")[0].strip())  # fc-match may return comma-separated families
        if resolved_first == base:
            continue  # original font installed → safe
        risky.append((name, resolved.split(",")[0].strip()))

    return FontAnalysis(declared, embedded, risky)


# ---------------------------------------------------------------------------
# Image conversion (LibreOffice → PDF → JPEG)
# ---------------------------------------------------------------------------


def convert_pptx_to_images(pptx_path: str | Path, output_dir: str | Path, dpi: int = 200) -> list[Path]:
    """Convert PPTX to slide images: pptx → LibreOffice → PDF → pdftoppm → JPEG."""
    pptx_path = Path(pptx_path)
    output_dir = Path(output_dir)
    slides_dir = output_dir / "slides"
    slides_dir.mkdir(parents=True, exist_ok=True)

    # Step 1: PPTX → PDF via LibreOffice
    pdf_dir = output_dir / "_tmp_pdf"
    pdf_dir.mkdir(exist_ok=True)

    # Give each invocation its own LibreOffice user profile dir. soffice
    # --headless refuses to start a second instance sharing a profile
    # (profile lock), so concurrent conversions (max_concurrent_tasks>1)
    # would otherwise fail. A unique UserInstallation per call avoids it.
    profile_dir = output_dir / f"_lo_profile_{os.getpid()}_{id(pptx_path)}"

    try:
        soffice_cmd = _find_soffice()
        user_install = f"file://{profile_dir.resolve()}"
        result = subprocess.run(
            [
                *soffice_cmd,
                f"-env:UserInstallation={user_install}",
                "--headless",
                "--convert-to",
                "pdf",
                "--outdir",
                str(pdf_dir),
                str(pptx_path.resolve()),
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            raise RuntimeError(f"LibreOffice conversion failed: {result.stderr[:500]}")

        pdf_files = list(pdf_dir.glob("*.pdf"))
        if not pdf_files:
            raise RuntimeError("LibreOffice did not produce a PDF file")
        pdf_path = pdf_files[0]

        # Step 2: PDF → JPEG via pdftoppm
        img_prefix = str(slides_dir / "slide")
        result = subprocess.run(
            ["pdftoppm", "-jpeg", "-r", str(dpi), str(pdf_path), img_prefix],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if result.returncode != 0:
            raise RuntimeError(f"pdftoppm failed: {result.stderr[:500]}")
    finally:
        shutil.rmtree(pdf_dir, ignore_errors=True)
        shutil.rmtree(profile_dir, ignore_errors=True)

    # pdftoppm outputs slide-1.jpg, slide-2.jpg → rename to slide-01.jpg
    image_files = sorted(slides_dir.glob("slide-*.jpg"))
    renamed: list[Path] = []
    for img_path in image_files:
        match = re.search(r"slide-(\d+)\.jpg$", img_path.name)
        if match:
            num = int(match.group(1))
            new_path = img_path.parent / f"slide-{num:02d}.jpg"
            if img_path != new_path:
                img_path.rename(new_path)
            renamed.append(new_path)

    return renamed


def _find_soffice() -> list[str]:
    """Find LibreOffice soffice binary."""
    for cmd in ("soffice", "libreoffice", "/usr/bin/soffice", "/usr/bin/libreoffice"):
        if shutil.which(cmd):
            return [cmd]
    return ["soffice"]


# ---------------------------------------------------------------------------
# Sidecar converter (HTTP fallback when LibreOffice is not installed locally)
# ---------------------------------------------------------------------------

SIDECAR_URL = os.environ.get("PPT_CONVERTER_URL", "http://ppt-converter:8030")
SIDECAR_SECRET = os.environ.get("PPT_CONVERTER_SECRET", "")


def convert_pptx_to_images_via_sidecar(
    pptx_path: str | Path,
    output_dir: str | Path,
    dpi: int = 200,
) -> list[Path]:
    """Convert PPTX to images via the ppt-converter sidecar service.

    Falls back to this when LibreOffice is not installed in the Gateway container.
    """
    import io

    try:
        import requests
    except ImportError:
        # requests not available, cannot use sidecar
        raise RuntimeError("Sidecar conversion requires the 'requests' library")

    pptx_path = Path(pptx_path)
    output_dir = Path(output_dir)
    slides_dir = output_dir / "slides"
    slides_dir.mkdir(parents=True, exist_ok=True)

    headers = {}
    if SIDECAR_SECRET:
        headers["X-Converter-Token"] = SIDECAR_SECRET

    with open(pptx_path, "rb") as f:
        resp = requests.post(
            f"{SIDECAR_URL}/convert",
            files={"file": (pptx_path.name, f)},
            data={"dpi": str(dpi)},
            headers=headers,
            timeout=180,
        )
    resp.raise_for_status()

    # Unzip the response. Validate each member path to guard against zip-slip
    # (entries with ../ or absolute paths escaping the target dir), then extract.
    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    _safe_extractall(zf, slides_dir)

    return sorted(slides_dir.glob("slide-*.jpg"))


def _safe_extractall(zf: zipfile.ZipFile, target_dir: Path) -> None:
    """Extract a zip into target_dir, rejecting entries that escape it (zip-slip)."""
    target = target_dir.resolve()
    for member in zf.infolist():
        # Skip directory entries (names end with /) — their parents are created
        # implicitly when files are extracted.
        if member.is_dir():
            continue
        member_path = (target / member.filename).resolve()
        # Ensure the resolved path stays inside target_dir.
        if target != member_path and target not in member_path.parents:
            raise RuntimeError(f"Refusing to extract zip entry outside target dir: {member.filename}")
    zf.extractall(target_dir)


def convert_pptx_to_images_auto(
    pptx_path: str | Path,
    output_dir: str | Path,
    dpi: int = 200,
) -> list[Path]:
    """Convert PPTX to images, trying local LibreOffice first, then sidecar."""
    try:
        return convert_pptx_to_images(pptx_path, output_dir, dpi)
    except (FileNotFoundError, RuntimeError, subprocess.TimeoutExpired) as e:
        # subprocess.TimeoutExpired (120s soffice/pdftoppm cap) is recoverable
        # via the sidecar — previously it propagated and aborted the task.
        logger.info("Local LibreOffice not available (%s), trying sidecar", e)
        return convert_pptx_to_images_via_sidecar(pptx_path, output_dir, dpi)
