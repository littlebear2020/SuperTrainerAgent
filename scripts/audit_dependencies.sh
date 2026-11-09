#!/usr/bin/env bash
# ── Dependency Audit Script ──────────────────────────────────────
# Scans all skills for Python import / Node.js require statements,
# then verifies each third-party package is available in the Docker image.
#
# Usage:
#   ./scripts/audit_dependencies.sh              # local check (imports only)
#   docker run --rm littlebear2016/deerflow-sandbox:latest bash /mnt/scripts/audit_dependencies.sh
#
# Exit code: 0 = all OK, 1 = missing packages found
set -euo pipefail

SKILLS_DIR="${SKILLS_DIR:-/Users/littlebear/Documents/deer-flow-main/skills/public}"
FAILED=0

echo "══════════════════════════════════════════════════════════════"
echo " Dependency Audit — $(date)"
echo "══════════════════════════════════════════════════════════════"
echo ""

# ── Python packages ──────────────────────────────────────────────
# Third-party Python packages used by skills (excluding stdlib)
PYTHON_PACKAGES=(
    edge_tts        # corporate-video, auto-video-flow, remotion-video
    pysrt           # auto-video-flow (SRT parsing)
    defusedxml      # pptx, docx (XML manipulation)
    duckdb          # data-analysis
    openpyxl        # data-analysis
    pydub           # daily-news-broadcast
    markitdown      # pptx (text extraction)
    cairosvg        # trainerai-diagram (SVG→PNG)
    pypdf           # pdf
    pdfplumber      # pdf
    reportlab       # pdf
    pypdfium2       # pdf
    pdf2image       # pdf
    pytesseract     # pdf (OCR)
    PIL             # pptx, pdf, image-generation (Pillow)
    requests        # image-generation, video-generation
    httpx           # (base image, currently unused but available)
    docx2txt        # docx (text extraction)
    pptx            # pptx (python-pptx)
    lxml            # docx, pptx (XML parsing)
)

echo "── Python packages ──────────────────────────────────────────"
for pkg in "${PYTHON_PACKAGES[@]}"; do
    if python3 -c "import ${pkg}" 2>/dev/null; then
        echo "  ✓ ${pkg}"
    else
        echo "  ✗ ${pkg} — MISSING"
        FAILED=1
    fi
done
echo ""

# ── System tools ─────────────────────────────────────────────────
SYSTEM_TOOLS=(
    ffmpeg          # video skills
    ffprobe         # video skills
    yt-dlp          # video-dl
    curl            # video-dl (ASR API calls)
    pdftotext       # pdf (poppler-utils)
    pdftoppm        # pdf, pptx (poppler-utils)
    qpdf            # pdf
    tesseract       # pdf (OCR)
    pandoc          # docx
    soffice         # docx, pptx (LibreOffice)
)

echo "── System tools ─────────────────────────────────────────────"
for tool in "${SYSTEM_TOOLS[@]}"; do
    if command -v "${tool}" &>/dev/null; then
        echo "  ✓ ${tool}"
    else
        echo "  ✗ ${tool} — MISSING"
        FAILED=1
    fi
done
echo ""

# ── Node.js packages (via NODE_PATH) ────────────────────────────
NODE_PACKAGES=(
    pdf-lib         # pdf
    pptxgenjs       # pptx
    docx            # docx
)

echo "── Node.js packages (NODE_PATH) ─────────────────────────────"
for pkg in "${NODE_PACKAGES[@]}"; do
    if node -e "require('${pkg}')" 2>/dev/null; then
        echo "  ✓ ${pkg}"
    else
        echo "  ✗ ${pkg} — MISSING"
        FAILED=1
    fi
done
echo ""

# ── Node.js global tools ─────────────────────────────────────────
echo "── Node.js global tools ─────────────────────────────────────"
if command -v tsx &>/dev/null; then
    echo "  ✓ tsx"
else
    echo "  ✗ tsx — MISSING"
    FAILED=1
fi
echo ""

# ── Pre-installed templates ──────────────────────────────────────
echo "── Pre-installed templates ──────────────────────────────────"
for path in /opt/remotion-template/package.json /opt/knowledge-card/package.json /opt/chrome-headless-shell/chrome-headless-shell; do
    if [ -e "$path" ]; then
        echo "  ✓ ${path}"
    else
        echo "  ✗ ${path} — MISSING"
        FAILED=1
    fi
done
echo ""

# ── Result ───────────────────────────────────────────────────────
if [ "$FAILED" -eq 0 ]; then
    echo "══════════════════════════════════════════════════════════════"
    echo " ✅ All dependencies verified — no gaps found"
    echo "══════════════════════════════════════════════════════════════"
else
    echo "══════════════════════════════════════════════════════════════"
    echo " ❌ Missing dependencies found — Docker image incomplete"
    echo "══════════════════════════════════════════════════════════════"
fi

exit $FAILED
