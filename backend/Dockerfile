# Backend Dockerfile — multi-stage build
# Stage 1 (builder): compiles native Python extensions with build-essential
# Stage 2 (dev):     retains toolchain for dev containers (uv sync at startup)
# Stage 3 (runtime): clean image without compiler toolchain for production

# UV source image (override for restricted networks that cannot reach ghcr.io)
ARG UV_IMAGE=ghcr.nju.edu.cn/astral-sh/uv:0.7.20
FROM ${UV_IMAGE} AS uv-source

# ── Stage 1: Builder ──────────────────────────────────────────────────────────
FROM hub.rat.dev/library/python:3.12-slim-bookworm AS builder

ARG NODE_MAJOR=22
ARG APT_MIRROR
ARG UV_INDEX_URL
# Optional extras to install (e.g. "postgres" for PostgreSQL support)
# Usage: docker build --build-arg UV_EXTRAS=postgres ...
ARG UV_EXTRAS

# Optionally override apt mirror for restricted networks (e.g. APT_MIRROR=mirrors.aliyun.com)
RUN if [ -n "${APT_MIRROR}" ]; then \
      sed -i "s|deb.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list.d/debian.sources 2>/dev/null || true; \
      sed -i "s|deb.debian.org|${APT_MIRROR}|g" /etc/apt/sources.list 2>/dev/null || true; \
    fi

# Install build tools + Node.js (build-essential needed for native Python extensions)
RUN apt-get update && apt-get install -y \
    curl \
    build-essential \
    gnupg \
    ca-certificates \
    && mkdir -p /etc/apt/keyrings \
    && curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg \
    && echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_${NODE_MAJOR}.x nodistro main" > /etc/apt/sources.list.d/nodesource.list \
    && apt-get update \
    && apt-get install -y nodejs \
    && rm -rf /var/lib/apt/lists/*

# Install uv (source image overridable via UV_IMAGE build arg)
COPY --from=uv-source /uv /uvx /usr/local/bin/

# Set working directory
WORKDIR /app

# Copy backend source code
COPY backend ./backend

# Install dependencies with cache mount
# When UV_EXTRAS is set (e.g. "postgres"), installs optional dependencies.
RUN --mount=type=cache,target=/root/.cache/uv \
    sh -c "cd backend && UV_INDEX_URL=${UV_INDEX_URL:-https://pypi.org/simple} uv sync ${UV_EXTRAS:+--extra $UV_EXTRAS}"

# ── Stage 2: Dev ──────────────────────────────────────────────────────────────
# Retains compiler toolchain from builder so startup-time `uv sync` can build
# source distributions in development containers.
FROM builder AS dev

# Install Docker CLI (for DooD: allows starting sandbox containers via host Docker socket)
COPY --from=hub.rat.dev/library/docker:cli /usr/local/bin/docker /usr/local/bin/docker

EXPOSE 8001 2024

CMD ["sh", "-c", "cd backend && PYTHONPATH=. uv run uvicorn app.gateway.app:app --host 0.0.0.0 --port 8001"]

# ── Stage 3: Runtime ──────────────────────────────────────────────────────────
# Clean image without build-essential — reduces size (~200 MB) and attack surface.
FROM hub.rat.dev/library/python:3.12-slim-bookworm

# Copy Node.js runtime from builder (provides npx for MCP servers)
COPY --from=builder /usr/bin/node /usr/bin/node
COPY --from=builder /usr/lib/node_modules /usr/lib/node_modules
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/bin/npm \
    && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/bin/npx

# Pre-install ACP agent adapters (avoids runtime npm download in restricted networks)
# Set NPM_REGISTRY to a mirror for China: docker build --build-arg NPM_REGISTRY=https://registry.npmmirror.com
ARG NPM_REGISTRY
RUN if [ -n "${NPM_REGISTRY}" ]; then npm config set registry "${NPM_REGISTRY}"; fi \
    && npm install -g @zed-industries/claude-agent-acp \
    && npm cache clean --force \
    && echo "=== claude-agent-acp installed ==="

# Install Docker CLI (for DooD: allows starting sandbox containers via host Docker socket)
COPY --from=hub.rat.dev/library/docker:cli /usr/local/bin/docker /usr/local/bin/docker

# Install uv (source image overridable via UV_IMAGE build arg)
COPY --from=uv-source /uv /uvx /usr/local/bin/

# ffmpeg for voice clone webm→wav transcoding (~80MB) + CJK 字体/fontconfig for ViMax subtitle burn (B6d)
# fonts-liberation: Arial/Times/Courier metric-compatible equivalents (PPT font gate)
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg fonts-noto-cjk fonts-liberation fontconfig && rm -rf /var/lib/apt/lists/*
# B6d: verify ffmpeg has libass subtitles filter — ViMax burns SRT subtitles in the gateway process
# (worker image already has this; gateway needed it too once vimax_burn_subtitles defaulted True)
RUN ffmpeg -filters 2>&1 | grep -q 'subtitles' || (echo "FATAL: ffmpeg missing subtitles filter (needs libass)" && exit 1)

# LibreOffice Impress (headless) + poppler for PPT→PDF→images
# NOTE: pulls in libreoffice-core + Java runtime (~600MB-1GB increase).
RUN apt-get update && apt-get install -y --no-install-recommends \
    libreoffice-impress-nogui \
    poppler-utils \
    && rm -rf /var/lib/apt/lists/*

# PPT font gate: vendor Carlito (Calibri metric-compatible), Caladea (Cambria),
# Noto Sans/Serif CJK SC, and fontconfig alias rules from the sandbox asset cache.
# Ensures LibreOffice renders PPT with the right metrics instead of substituting
# width-mismatched Noto CJK for 微软雅黑/宋体/等线 → text wrap errors.
COPY docker/sandbox/cache/fonts/fonts/ /usr/share/fonts/truetype/ppt-fallback/
COPY docker/sandbox/cache/fonts/fonts/99-ms-fonts-fallback.conf /etc/fonts/conf.d/
RUN fc-cache -fv

# Build-time gate: verify metric-compatible fallbacks actually resolve (P2 fix).
# fc-match always exits 0, so check the resolved family name explicitly.
RUN fc-match -f "%{family}" "Arial" | grep -qi "Liberation Sans" \
 && fc-match -f "%{family}" "Calibri" | grep -qi "Carlito" \
 && fc-match -f "%{family}" "noto sans cjk sc" | grep -qi "noto sans cjk" \
 && fc-list :lang=zh | grep -q .

# B4-T1: WeasyPrint system dependencies (pango/harfbuzz/ffi for PDF rendering)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b libffi8 \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy backend with pre-built virtualenv from builder
COPY --from=builder /app/backend ./backend

# B4-T1: verify weasyprint can import (build-time gate) — must run AFTER copying
# the backend directory (which contains .venv with weasyprint installed by builder)
RUN cd backend && uv run --no-sync python -c "import weasyprint" || (echo "FATAL: weasyprint import failed" && exit 1)

# Expose ports (gateway: 8001, langgraph: 2024)
EXPOSE 8001 2024

# Default command (can be overridden in docker-compose)
CMD ["sh", "-c", "cd backend && PYTHONPATH=. uv run --no-sync uvicorn app.gateway.app:app --host 0.0.0.0 --port 8001"]
