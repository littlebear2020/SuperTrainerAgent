#!/usr/bin/env bash
# Run Production Worker locally (for `make dev` mode).
# Usage: cd backend && bash ../docker/production-worker/run-local.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BACKEND_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"

# Resolve DB and storage paths relative to project root
DB_PATH="${BACKEND_DIR}/.deer-flow/data/deerflow.db"
STORAGE_DIR="${BACKEND_DIR}/.deer-flow/voice-twins"

if [ ! -f "$DB_PATH" ]; then
    echo "ERROR: DB not found at $DB_PATH"
    exit 1
fi

if ! command -v ffmpeg &>/dev/null; then
    echo "ERROR: ffmpeg not found in PATH"
    exit 1
fi

# Subtitle burning needs the `subtitles` filter, which requires libass. Warn
# (do not abort) when it is missing so the worker still starts and P0-1's
# degrade produces subtitle-less videos instead of crashing. Production Docker
# keeps the hard fail in the Dockerfile.
if ! ffmpeg -filters 2>&1 | grep -q 'subtitles'; then
    echo "WARN: ffmpeg missing subtitles filter (needs libass)."
    echo "  字幕将被跳过（视频仍可生成，但无字幕）。建议修复："
    echo "    brew install libass && brew reinstall ffmpeg"
    echo "    验证: ffmpeg -buildconf | grep libass"
    echo "    或改用 Docker Worker: make docker-start"
fi

echo "=== Production Worker (local mode) ==="
echo "  DB:       $DB_PATH"
echo "  Storage:  $STORAGE_DIR"
echo "  WorkerID: local-worker-$(hostname)"
echo ""

cd "$SCRIPT_DIR"

HOSTNAME="local-worker-$(hostname)" \
DB_PATH="$DB_PATH" \
STORAGE_DIR="$STORAGE_DIR" \
POLL_INTERVAL=2 \
LEASE_DURATION=1800 \
python3 worker.py
