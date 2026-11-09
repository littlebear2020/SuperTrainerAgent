FROM hub.rat.dev/library/python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    fonts-noto-cjk \
    fontconfig \
    && rm -rf /var/lib/apt/lists/* \
    && fc-cache -f

# Verify ffmpeg has drawtext filter (graceful degradation at runtime,
# but fail-fast at build time if the base image changes)
RUN ffmpeg -filters 2>&1 | grep -q 'drawtext' || (echo "FATAL: ffmpeg missing drawtext" && exit 1)
RUN ffmpeg -filters 2>&1 | grep -q 'subtitles' || (echo "FATAL: ffmpeg missing subtitles filter (needs libass)" && exit 1)

# T6 (2026-08-13): Verify CJK Bold font exists — ASS Style Bold=-1 needs NotoSansCJK-Bold
RUN fc-list | grep -qi "Noto Sans CJK.*Bold" || (echo "FATAL: fonts-noto-cjk missing Bold variant (ASS subtitle needs it)" && exit 1)

WORKDIR /app

# Copy Worker code
COPY worker.py pipeline.py ./
COPY pipelines/ ./pipelines/

EXPOSE 8040

CMD ["python", "worker.py"]
