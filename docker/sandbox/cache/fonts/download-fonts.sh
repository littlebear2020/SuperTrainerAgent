#!/bin/bash
# 下载 PPT 渲染所需的字体文件
# 这些字体用于替代 Windows 中文字体，解决 LibreOffice 在沙箱中渲染 PPT 的文字折行/重叠问题
#
# 用法: bash download-fonts.sh
# 需要在有外网的开发机上运行，下载后 commit 到 git
#
# 字体对应关系:
#   NotoSansCJKsc-VF.otf       → 替代 Microsoft YaHei / DengXian / SimHei (含全部字重)
#   NotoSerifCJKsc-Regular.otf  → 替代 SimSun / KaiTi
#   NotoSerifCJKsc-Bold.otf     → SimSun/KaiTi 粗体
#   Carlito-Regular/Bold.ttf    → 替代 Calibri (度量兼容)
#   Caladea-Regular/Bold.ttf    → 替代 Cambria (度量兼容)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FONTS_DIR="${SCRIPT_DIR}/fonts"
mkdir -p "${FONTS_DIR}"

echo "=== 下载 PPT 渲染字体到 ${FONTS_DIR} ==="

# --- Noto Sans CJK SC Variable Font (替代微软雅黑/等线/黑体，含全部字重) ---
NOTO_SANS_VF="${FONTS_DIR}/NotoSansCJKsc-VF.otf"
if [ ! -f "${NOTO_SANS_VF}" ]; then
    echo "下载 NotoSansCJKsc-VF.otf (微软雅黑/等线/黑体 替代)..."
    curl -fSL -o "${NOTO_SANS_VF}" \
        "https://raw.githubusercontent.com/notofonts/noto-cjk/main/Sans/Variable/OTF/NotoSansCJKsc-VF.otf" \
        || echo "WARNING: NotoSansCJKsc-VF 下载失败，请手动下载"
else
    echo "已存在: NotoSansCJKsc-VF.otf"
fi

# --- Noto Serif CJK SC Regular (替代 SimSun/宋体, KaiTi/楷体) ---
NOTO_SERIF_REG="${FONTS_DIR}/NotoSerifCJKsc-Regular.otf"
if [ ! -f "${NOTO_SERIF_REG}" ]; then
    echo "下载 NotoSerifCJKsc-Regular.otf (SimSun/KaiTi 替代)..."
    curl -fSL -o "${NOTO_SERIF_REG}" \
        "https://github.com/googlefonts/noto-cjk/raw/main/Serif/OTF/SimplifiedChinese/NotoSerifCJKsc-Regular.otf" \
        || echo "WARNING: NotoSerifCJKsc-Regular 下载失败，请手动下载"
else
    echo "已存在: NotoSerifCJKsc-Regular.otf"
fi

# --- Noto Serif CJK SC Bold ---
NOTO_SERIF_BOLD="${FONTS_DIR}/NotoSerifCJKsc-Bold.otf"
if [ ! -f "${NOTO_SERIF_BOLD}" ]; then
    echo "下载 NotoSerifCJKsc-Bold.otf..."
    curl -fSL -o "${NOTO_SERIF_BOLD}" \
        "https://github.com/googlefonts/noto-cjk/raw/main/Serif/OTF/SimplifiedChinese/NotoSerifCJKsc-Bold.otf" \
        || echo "WARNING: NotoSerifCJKsc-Bold 下载失败，请手动下载"
else
    echo "已存在: NotoSerifCJKsc-Bold.otf"
fi

# --- Carlito (Calibri 度量兼容替代，Office 默认西文字体) ---
CARLITO_REG="${FONTS_DIR}/Carlito-Regular.ttf"
if [ ! -f "${CARLITO_REG}" ]; then
    echo "下载 Carlito-Regular.ttf (Calibri 替代)..."
    curl -fSL -o "${CARLITO_REG}" \
        "https://github.com/googlefonts/carlito/raw/main/fonts/ttf/Carlito-Regular.ttf" \
        || echo "WARNING: Carlito-Regular 下载失败，请手动下载"
else
    echo "已存在: Carlito-Regular.ttf"
fi

CARLITO_BOLD="${FONTS_DIR}/Carlito-Bold.ttf"
if [ ! -f "${CARLITO_BOLD}" ]; then
    echo "下载 Carlito-Bold.ttf..."
    curl -fSL -o "${CARLITO_BOLD}" \
        "https://github.com/googlefonts/carlito/raw/main/fonts/ttf/Carlito-Bold.ttf" \
        || echo "WARNING: Carlito-Bold 下载失败，请手动下载"
else
    echo "已存在: Carlito-Bold.ttf"
fi

# --- Caladea (Cambria 度量兼容替代，Office 衬线西文字体) ---
CALADEA_REG="${FONTS_DIR}/Caladea-Regular.ttf"
if [ ! -f "${CALADEA_REG}" ]; then
    echo "下载 Caladea-Regular.ttf (Cambria 替代)..."
    curl -fSL -o "${CALADEA_REG}" \
        "https://raw.githubusercontent.com/googlefonts/caladea/master/fonts/ttf/Caladea-Regular.ttf" \
        || echo "WARNING: Caladea-Regular 下载失败，请手动下载"
else
    echo "已存在: Caladea-Regular.ttf"
fi

CALADEA_BOLD="${FONTS_DIR}/Caladea-Bold.ttf"
if [ ! -f "${CALADEA_BOLD}" ]; then
    echo "下载 Caladea-Bold.ttf..."
    curl -fSL -o "${CALADEA_BOLD}" \
        "https://raw.githubusercontent.com/googlefonts/caladea/master/fonts/ttf/Caladea-Bold.ttf" \
        || echo "WARNING: Caladea-Bold 下载失败，请手动下载"
else
    echo "已存在: Caladea-Bold.ttf"
fi

echo ""
echo "=== 下载完成 ==="
echo "字体文件列表："
ls -lh "${FONTS_DIR}/"
echo ""
echo "下一步："
echo "  1. git add docker/sandbox/cache/fonts/"
echo "  2. 重新构建沙箱镜像: docker build -t littlebear2016/deerflow-sandbox:latest -f docker/sandbox/Dockerfile ."
