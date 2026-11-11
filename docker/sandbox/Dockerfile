# Custom sandbox image for all Skills
#
# Base image (all-in-one-sandbox) already includes:
#   Node.js v22, FFmpeg, Python 3.10, pip
#   Pre-installed Python: numpy, fastapi, scipy, xlsxwriter, seaborn,
#   httpx, docx2txt, python-pptx, uvicorn, lxml, Pillow, requests
#
# This layer adds:
#   - edge-tts:        auto-video-flow (Microsoft Edge TTS)
#   - pysrt:           SRT subtitle parsing
#   - duckdb:          data-analysis (in-process SQL analytics)
#   - openpyxl:        data-analysis + xlsx (Excel file reading)
#   - python-docx:     sandbox agents (Word document parsing/creation)
#   - pydub:           （2026-09-09 起已无技能消费者——daily-news-broadcast 已删；
#                      安装行待 Dockerfile 重建窗口清理，见延期清单）
#   - markitdown[pptx]: pptx skill (PPT text extraction)
#   - yt-dlp:          video download from Bilibili（2026-09-09 起技能层仅支持 Bilibili）
#   - cairosvg:        trainerai-diagram (SVG→PNG conversion)
#   - pypdf:           pdf skill (PDF form filling, field extraction)
#   - pdfplumber:      pdf skill (PDF structure extraction)
#   - reportlab:       pdf skill (PDF creation)
#   - pypdfium2:       pdf skill (PDF rendering)
#   - pdf2image:       pdf skill (PDF→image conversion)
#   - pytesseract:     pdf skill (OCR for scanned documents)
#   - poppler-utils:   pdf/docx/pptx skills (pdftotext, pdftoppm, pdfimages)
#   - qpdf:            pdf skill (PDF merge/split/encrypt)
#   - tesseract-ocr:   pdf skill (OCR engine + Chinese language data)
#   - imagemagick:     pdf skill (image crop/convert for form fields)
#   - pandoc:          docx skill (DOCX text extraction)
#   - libreoffice:     docx/pptx/xlsx skills (format conversion, formula recalc)
#   - docx (npm):      docx skill (JavaScript Word document creation)
#   - /etc/pip.conf + PIP_INDEX_URL：系统级清华源（2026-10-23 事故：原只配给 root）
#   - python3.10-venv: `python3 -m venv` 可用（基座缺此包）
#
# Build:
#   docker build -t littlebear2016/deerflow-sandbox:latest -f docker/sandbox/Dockerfile .
#
# Usage in config.yaml:
#   sandbox:
#     use: deerflow.community.aio_sandbox:AioSandboxProvider
#     image: littlebear2016/deerflow-sandbox:latest

# 2026-10-13 钉版本：浮动 latest 在 Volcano 镜像侧已 10 个月未更新（v1.0.0.x 时代），
# 服务端缺 /v1/bash/*（上游 v1.9.3/2026-05-29 才加入），而 agent-sandbox 客户端 0.0.30
# 对 required-secrets 注入命令走该路由 → 404（公司服务器「沙箱不可用」事故根因）。
# 升级基座须重验：/v1/shell/exec + /v1/bash/exec 双路由探针（1.11.0 已验 200）。
ARG BASE_IMAGE=enterprise-public-cn-beijing.cr.volces.com/vefaas-public/all-in-one-sandbox:1.11.0
FROM ${BASE_IMAGE}

# pip source: use Chinese mirror for reliability on CN servers
# Build arg allows override: --build-arg PIP_INDEX=https://pypi.org/simple
ARG PIP_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple
# 2026-10-23 公司服务器事故修正：原 `pip3 config set global.index-url` 只写 root 的
# ~/.config/pip/pip.conf（user 作用域），任务用户（uid 1000）与 venv 内 pip 全部回退
# pypi.org——公司网络国外静默丢包，下载永不完成，agent sleep 空等十余分钟。
# 改为双保险：/etc/pip.conf 系统级落盘（所有用户、所有 pip 版本都读）+ PIP_INDEX_URL
# 环境变量（优先级高于配置文件，venv 内 pip / ensurepip 自举的 pip 同样命中）。
# 运行期装包仍属禁区（SKILL.md「沙箱离线纪律」）——本修正只保证真要装时走国内源快速
# 成功/失败，而不是挂死。
RUN printf '[global]\nindex-url = %s\n' "${PIP_INDEX}" > /etc/pip.conf
ENV PIP_INDEX_URL=${PIP_INDEX}

# python3.10-venv：基座缺此包，`python3 -m venv` 报「ensurepip is not available」
# （2026-10-23 事故中 agent 手动 ensurepip 凑出半残 venv，且默认隔离系统包）。
# 补上后 venv 可正常创建；SKILL.md 仍要求不要在沙箱里用 venv（系统 python3 预装 cv2/Pillow）。
RUN apt-get update && apt-get install -y --no-install-recommends python3.10-venv \
    && rm -rf /var/lib/apt/lists/*

# Ensure setuptools is available (some packages like pysrt need it for building)
# --break-system-packages: Python 3.10 on Ubuntu 22.04 is externally managed
# 1.11.0 基座自带 pip 22.x 不识 --break-system-packages（pip ≥23.0.1 才有该选项）——先自升级
RUN pip3 install --no-cache-dir --upgrade pip && \
    pip3 install --no-cache-dir --break-system-packages setuptools wheel && \
    pip3 install --no-cache-dir --break-system-packages \
    edge-tts \
    pysrt \
    defusedxml \
    duckdb \
    openpyxl \
    python-docx \
    pydub \
    'markitdown[pptx]'


# Install yt-dlp via pip (avoids GitHub connectivity issues on Chinese servers)
RUN pip3 install --no-cache-dir --break-system-packages yt-dlp

RUN echo "=== Skills dependency check ===" \
    && node --version \
    && npm --version \
    && ffmpeg -version 2>&1 | head -1 \
    && ffprobe -version 2>&1 | head -1 \
    && yt-dlp --version \
    && python3 -c "import pysrt; print('pysrt OK')" \
    && python3 -c "import edge_tts; print('edge-tts OK')" \
    && python3 -c "import duckdb; print('duckdb OK')" \
    && python3 -c "import openpyxl; print('openpyxl OK')" \
    && python3 -c "import docx; print('python-docx OK')" \
    && python3 -c "import pydub; print('pydub OK')" \
    && python3 -c "import markitdown; print('markitdown OK')" \
    && echo "=== All dependencies verified ==="

# Pre-install Remotion project template for auto-video-flow skill
# Chrome headless shell is pre-downloaded locally and copied in (avoids Google download on server)
COPY docker/sandbox/cache/chrome-linux/chrome-headless-shell-linux64/ /opt/chrome-headless-shell/
RUN chmod +x /opt/chrome-headless-shell/chrome-headless-shell
COPY skills/public/auto-video-flow/templates/remotion-base/ /opt/remotion-template/
# TEMPLATE_VERSION anti-drift stamp (2026-09-08 T9): content hash of the template
# source as copied into the image. Sandbox agents compare this against the same
# one-liner run over /mnt/skills/public/auto-video-flow/templates/remotion-base/
# to detect a stale image-baked template before copying /opt/remotion-template
# (template changes REQUIRE an image rebuild — see deployment guide K16).
RUN cd /opt/remotion-template \
    && find . -type f -not -path "./node_modules/*" -not -name ".DS_Store" -not -name "TEMPLATE_VERSION" | sort | xargs md5sum | md5sum | awk '{print $1}' > /opt/remotion-template/TEMPLATE_VERSION \
    && echo "TEMPLATE_VERSION: $(cat /opt/remotion-template/TEMPLATE_VERSION)"
RUN cd /opt/remotion-template \
    && npm install --no-audit --no-fund \
    && node -e "try { require('esbuild'); console.log('esbuild OK') } catch(e) { console.error('esbuild FAILED:', e.message); process.exit(1) }" \
    && PUPPETEER_CHROMIUM_REVISION=skip npx remotion browser ensure --browser-executable=/opt/chrome-headless-shell/chrome-headless-shell || echo "⚠ Remotion browser setup skipped" \
    && rm -rf node_modules/.cache \
    && echo "=== Remotion template ready ==="

# Fix: npm may silently skip @rspack/binding optional deps (npm/cli#4828).
# Explicitly install the platform-specific native binding for Remotion's bundler.
RUN ARCH=$(uname -m) \
    && if [ "$ARCH" = "aarch64" ] || [ "$ARCH" = "arm64" ]; then \
         RSPACK_BINDING="@rspack/binding-linux-arm64-gnu"; \
       else \
         RSPACK_BINDING="@rspack/binding-linux-x64-gnu"; \
       fi \
    && cd /opt/remotion-template \
    && npm install --no-save "$RSPACK_BINDING" --no-audit --no-fund || echo "⚠ $RSPACK_BINDING install skipped (may need manual fix)" \
    && node -e "try { require('@rspack/binding'); console.log('rspack binding OK') } catch(e) { console.error('rspack binding FAILED:', e.message); process.exit(1) }" \
    && echo "=== rspack native binding verified ==="

# Pre-install knowledge-card-auto-generator dependencies (Remotion-based card rendering)
COPY skills/public/knowledge-card-auto-generator/ /opt/knowledge-card/
RUN cd /opt/knowledge-card \
    && npm install --no-audit --no-fund \
    && rm -rf node_modules/.cache \
    && echo "=== Knowledge card generator ready ==="

# TrainerAI skills dependencies (image generation skills)
# tsx: TypeScript runner for Node.js (replaces bun) — CLI tool, global install is fine
RUN npm install -g tsx

# npm library packages for skills (pdf-lib, pptxgenjs, docx)
# Installed to a dedicated directory instead of global, because:
#   1. Skills run from /mnt/skills/ which has no node_modules (deleted before deploy)
#   2. Node.js require() does NOT search npm global directory by default
#   3. NODE_PATH tells Node.js where to find these packages
RUN mkdir -p /opt/node-libs && cd /opt/node-libs && npm init -y --silent \
    && npm install --no-audit --no-fund pdf-lib pptxgenjs docx \
    && rm -rf node_modules/.cache \
    && echo "=== npm library packages installed at /opt/node-libs/node_modules ==="
ENV NODE_PATH=/opt/node-libs/node_modules

# SVG→PNG conversion for trainerai-diagram (lightweight alternative to sharp)
# libcairo2 is required by cairosvg at the system level
RUN apt-get update && apt-get install -y --no-install-recommends libcairo2 && rm -rf /var/lib/apt/lists/*
RUN pip3 install --no-cache-dir --break-system-packages cairosvg \
    && python3 -c "import cairosvg; print('cairosvg OK')"

# ── Document skill dependencies (PDF / DOCX / PPTX / XLSX) ──────────────────
# These packages enable full document processing capabilities in skills.

# System packages: PDF tools, OCR engine, document converter, office conversion
# NOTE: LibreOffice adds ~250-350MB to image size. Remove libreoffice-* packages
# if you don't need DOCX/PPTX→PDF conversion or XLSX formula recalculation.
RUN apt-get update && apt-get install -y --no-install-recommends \
    poppler-utils \
    qpdf \
    tesseract-ocr \
    tesseract-ocr-chi-sim \
    fonts-noto-cjk \
    imagemagick \
    pandoc \
    libreoffice-writer \
    libreoffice-impress \
    && rm -rf /var/lib/apt/lists/* \
    && echo "=== System document tools installed ==="

# ── PPT rendering fonts (LibreOffice font fallback fix) ──────────────────
# Pre-downloaded font files to replace Windows core Chinese/Western fonts.
# Without these, LibreOffice falls back to wider fonts causing text wrapping
# and element overlap in PPT rendering.
# See: docker/sandbox/cache/fonts/download-fonts.sh
COPY docker/sandbox/cache/fonts/fonts/ /usr/share/fonts/truetype/ppt-fallback/
RUN fc-cache -fv && echo "=== PPT fallback fonts installed ==="

# Font alias mapping: Windows Chinese fonts → Noto Sans/Serif CJK
# This ensures LibreOffice uses metric-compatible substitutes instead of
# random fallback, preventing text reflow and element overlap.
RUN mkdir -p /etc/fonts/conf.d && cat > /etc/fonts/conf.d/99-ms-fonts-fallback.conf << 'FONTCONFIG'
<?xml version="1.0"?>
<!DOCTYPE fontconfig SYSTEM "fonts.dtd">
<fontconfig>
  <!-- Microsoft YaHei / 微软雅黑 → Noto Sans SC -->
  <alias binding="strong">
    <family>Microsoft YaHei</family>
    <accept><family>Noto Sans SC</family></accept>
  </alias>
  <alias binding="strong">
    <family>微软雅黑</family>
    <accept><family>Noto Sans SC</family></accept>
  </alias>
  <!-- DengXian / 等线 → Noto Sans SC -->
  <alias binding="strong">
    <family>DengXian</family>
    <accept><family>Noto Sans SC</family></accept>
  </alias>
  <alias binding="strong">
    <family>等线</family>
    <accept><family>Noto Sans SC</family></accept>
  </alias>
  <!-- SimHei / 黑体 → Noto Sans SC (Bold, via VF) -->
  <alias binding="strong">
    <family>SimHei</family>
    <prefer><family>Noto Sans SC</family></prefer>
  </alias>
  <alias binding="strong">
    <family>黑体</family>
    <prefer><family>Noto Sans SC</family></prefer>
  </alias>
  <!-- SimSun / 宋体 → Noto Serif CJK SC -->
  <alias binding="strong">
    <family>SimSun</family>
    <accept><family>Noto Serif CJK SC</family></accept>
  </alias>
  <alias binding="strong">
    <family>宋体</family>
    <accept><family>Noto Serif CJK SC</family></accept>
  </alias>
  <!-- KaiTi / 楷体 → Noto Serif CJK SC -->
  <alias binding="strong">
    <family>KaiTi</family>
    <accept><family>Noto Serif CJK SC</family></accept>
  </alias>
  <alias binding="strong">
    <family>楷体</family>
    <accept><family>Noto Serif CJK SC</family></accept>
  </alias>
  <!-- Calibri → Carlito (metric-compatible) -->
  <alias binding="strong">
    <family>Calibri</family>
    <accept><family>Carlito</family></accept>
  </alias>
  <!-- Cambria → Caladea (metric-compatible) -->
  <alias binding="strong">
    <family>Cambria</family>
    <accept><family>Caladea</family></accept>
  </alias>
</fontconfig>
FONTCONFIG
RUN fc-cache -fv && echo "=== Font alias mapping configured ==="

# Python packages for PDF skill (form filling, text extraction, creation, OCR)
RUN pip3 install --no-cache-dir --break-system-packages \
    pypdf \
    pdfplumber \
    reportlab \
    pypdfium2 \
    pdf2image \
    pytesseract \
    && echo "=== PDF Python packages installed ==="

# Python packages for ppt-master skill (source_to_md: PDF/DOCX/EPUB/IPYNB/web → Markdown)
# 💡 Iter 1.3 — numpy/openpyxl/reportlab/cairosvg already installed above.
# 📌 2026-09-11 ppt-master 6.3.0 upgrade:
#   - skia-pathops / uharfbuzz (C 扩展, 布尔形状/文字轮廓) — 函数级懒加载, 缺失仅两特性
#     友好降级 (Phase 0.1b 实测), 但镜像侧补齐解除禁用; 双架构 wheel (linux amd64/aarch64)。
#   - PyYAML (register_template 模板注册) / XlsxWriter (原生图表数据工作簿; 基底注释声称
#     预装但无安装行 — 显式 pin 幂等保险)。
#   - svglib 保留不删: 6.3.0 media.py 仍以 svglib 作 cairosvg 缺席时的兜底 PNG 渲染器
#     (cairosvg 因 trainerai-diagram 在装且优先, svglib 实际休眠 — 留一个部署窗口再评估)。
#   - LibreOffice calc 不需要: 6.3.0 全库无 soffice 重算引用 (图表数据走 XlsxWriter 内嵌)。
RUN pip3 install --no-cache-dir --break-system-packages \
    PyMuPDF \
    mammoth \
    svglib \
    beautifulsoup4 \
    markdownify \
    curl_cffi \
    ebooklib \
    nbconvert \
    skia-pathops \
    uharfbuzz \
    PyYAML \
    XlsxWriter \
    && echo "=== ppt-master source_to_md packages installed ===" \
    && python3 -c "import fitz; print('PyMuPDF OK')" \
    && python3 -c "import mammoth; print('mammoth OK')" \
    && python3 -c "import svglib; print('svglib OK')" \
    && python3 -c "import bs4; print('beautifulsoup4 OK')" \
    && python3 -c "import markdownify; print('markdownify OK')" \
    && python3 -c "import curl_cffi; print('curl_cffi OK')" \
    && python3 -c "import ebooklib; print('ebooklib OK')" \
    && python3 -c "import nbconvert; print('nbconvert OK')" \
    && python3 -c "import pathops; print('skia-pathops OK')" \
    && python3 -c "import uharfbuzz; print('uharfbuzz OK')" \
    && python3 -c "import yaml; print('PyYAML OK')" \
    && python3 -c "import xlsxwriter; print('XlsxWriter OK')"

# Verify document skill dependencies
RUN echo "=== Document dependencies check ===" \
    && python3 -c "import pypdf; print('pypdf OK')" \
    && python3 -c "import pdfplumber; print('pdfplumber OK')" \
    && python3 -c "import reportlab; print('reportlab OK')" \
    && python3 -c "import pypdfium2; print('pypdfium2 OK')" \
    && python3 -c "from pdf2image import convert_from_path; print('pdf2image OK')" \
    && python3 -c "import pytesseract; print('pytesseract OK')" \
    && pdftotext -v 2>&1 | head -1 \
    && qpdf --version \
    && tesseract --version 2>&1 | head -1 \
    && pandoc --version | head -1 \
    && soffice --version 2>&1 | head -1 \
    && fc-list :lang=zh family | head -3 \
    && echo "--- Font alias verification ---" \
    && fc-match "Microsoft YaHei" \
    && fc-match "SimHei" \
    && fc-match "SimSun" \
    && fc-match "KaiTi" \
    && fc-match "DengXian" \
    && fc-match "Calibri" \
    && echo "=== All document dependencies verified ==="

# Verify npm library packages are resolvable via NODE_PATH
RUN echo "=== npm library packages check ===" \
    && node -e "require('pdf-lib'); console.log('pdf-lib OK')" \
    && node -e "require('pptxgenjs'); console.log('pptxgenjs OK')" \
    && node -e "require('docx'); console.log('docx OK')" \
    && echo "=== All npm library packages verified ==="

# Rosetta on Apple Silicon: eager shell/Jupyter pool warmup blocks the event loop.
# Leave OTel, uvloop and MCP filtering intact; sessions are still created on demand.
RUN sed -i 's/warmup_task = asyncio.create_task(_warmup_pools())/warmup_task = None/' /opt/python3.12/lib/python3.12/site-packages/app/server.py \
    && grep -q 'warmup_task = None' /opt/python3.12/lib/python3.12/site-packages/app/server.py

# C4（2026-10-12）：摘除基座继承的 HEALTHCHECK。基座 all-in-one-sandbox 的
# 健康检查面向常驻服务形态，与按需任务沙箱（每任务起停）不符——实测 FDE 会话
# 沙箱 FailingStreak 274（每 10s 误报一次），容器功能完全正常，纯监控噪音。
# HEALTHCHECK NONE 显式覆盖继承指令；需重建 sandbox 镜像随部署批次上线（K16）。
HEALTHCHECK NONE
