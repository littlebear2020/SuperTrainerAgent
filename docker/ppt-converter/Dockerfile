# PPT Converter Sidecar
# Lightweight container for LibreOffice-based PPT → image conversion.
# Keeps LibreOffice (~600MB-1GB) out of the Gateway image.
#
# Build context MUST be the repository root (not docker/ppt-converter/) so that
# COPY docker/sandbox/cache/fonts/... works. See docker-compose-dev.yaml.

FROM hub.rat.dev/library/python:3.12-slim-bookworm

# Install LibreOffice + poppler + fontconfig + CJK font deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    libreoffice-impress-nogui \
    poppler-utils \
    fonts-noto-cjk \
    fonts-liberation \
    fontconfig \
    && rm -rf /var/lib/apt/lists/*

# Install Flask + python-pptx
RUN pip install --no-cache-dir flask python-pptx

WORKDIR /app
# context is repo root → app.py lives under docker/ppt-converter/
COPY docker/ppt-converter/app.py .

# PPT font gate: vendor Carlito/Caladea/Noto CJK + fontconfig alias rules
# (same assets as the gateway image)
COPY docker/sandbox/cache/fonts/fonts/ /usr/share/fonts/truetype/ppt-fallback/
COPY docker/sandbox/cache/fonts/fonts/99-ms-fonts-fallback.conf /etc/fonts/conf.d/
RUN fc-cache -fv

# Build-time gate: verify metric-compatible fallbacks resolve (P2 fix).
RUN fc-match -f "%{family}" "Arial" | grep -qi "Liberation Sans" \
 && fc-match -f "%{family}" "Calibri" | grep -qi "Carlito" \
 && fc-match -f "%{family}" "noto sans cjk sc" | grep -qi "noto sans cjk" \
 && fc-list :lang=zh | grep -q .

ENV PORT=8030
EXPOSE 8030

CMD ["python", "app.py"]
