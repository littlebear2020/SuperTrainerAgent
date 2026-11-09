#!/usr/bin/env bash
#
# deploy.sh - Build, start, or stop DeerFlow production services
#
# Commands:
#   deploy.sh                    — build + start
#   deploy.sh build              — build all images (mode-agnostic)
#   deploy.sh start              — start from pre-built images
#   deploy.sh down               — stop and remove containers
#
# Sandbox mode (local / aio / provisioner) is auto-detected from config.yaml.
#
# Examples:
#   deploy.sh                    # build + start
#   deploy.sh build              # build all images
#   deploy.sh start              # start pre-built images
#   deploy.sh down               # stop and remove containers
#
# Must be run from the repo root directory.

set -e

case "${1:-}" in
    build|start|down)
        CMD="$1"
        if [ -n "${2:-}" ]; then
            echo "Unknown argument: $2"
            echo "Usage: deploy.sh [build|start|down]"
            exit 1
        fi
        ;;
    "")
        CMD=""
        ;;
    *)
        echo "Unknown argument: $1"
        echo "Usage: deploy.sh [build|start|down]"
        exit 1
        ;;
esac

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

DOCKER_DIR="$REPO_ROOT/docker"
COMPOSE_CMD=(docker compose -p deer-flow -f "$DOCKER_DIR/docker-compose.yaml")

# ── Colors ────────────────────────────────────────────────────────────────────

GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

# ── DEER_FLOW_HOME ────────────────────────────────────────────────────────────

if [ -z "$DEER_FLOW_HOME" ]; then
    export DEER_FLOW_HOME="$REPO_ROOT/backend/.deer-flow"
fi
echo -e "${BLUE}DEER_FLOW_HOME=$DEER_FLOW_HOME${NC}"
mkdir -p "$DEER_FLOW_HOME"

# ── DEER_FLOW_REPO_ROOT (for skills host path in DooD) ───────────────────────

export DEER_FLOW_REPO_ROOT="$REPO_ROOT"

# ── Fix skills directory permissions ──────────────────────────────────────────
# Git clone/pull may produce 0700 directories, blocking sandbox containers
# that run as a non-owner user (e.g. uid=1000) from reading skill files.
chmod -R a+rX "$REPO_ROOT/skills/public/" 2>/dev/null || true
chmod -R a+rX "$REPO_ROOT/skills/custom/" 2>/dev/null || true
mkdir -p "$REPO_ROOT/skills/custom"

# ── config.yaml ───────────────────────────────────────────────────────────────

if [ -z "$DEER_FLOW_CONFIG_PATH" ]; then
    export DEER_FLOW_CONFIG_PATH="$REPO_ROOT/config.yaml"
fi

if [ ! -f "$DEER_FLOW_CONFIG_PATH" ]; then
    # Try to seed from repo (config.example.yaml is the canonical template)
    if [ -f "$REPO_ROOT/config.example.yaml" ]; then
        cp "$REPO_ROOT/config.example.yaml" "$DEER_FLOW_CONFIG_PATH"
        echo -e "${GREEN}✓ Seeded config.example.yaml → $DEER_FLOW_CONFIG_PATH${NC}"
        echo -e "${YELLOW}⚠ config.yaml was seeded from the example template.${NC}"
        echo "  Run 'make setup' to generate a minimal config, or edit $DEER_FLOW_CONFIG_PATH manually before use."
    else
        echo -e "${RED}✗ No config.yaml found.${NC}"
        echo "  Run 'make setup' from the repo root (recommended),"
        echo "  or 'make config' for the full template, then set the required model API keys."
        exit 1
    fi
else
    echo -e "${GREEN}✓ config.yaml: $DEER_FLOW_CONFIG_PATH${NC}"
fi

# ── extensions_config.json ───────────────────────────────────────────────────

if [ -z "$DEER_FLOW_EXTENSIONS_CONFIG_PATH" ]; then
    export DEER_FLOW_EXTENSIONS_CONFIG_PATH="$REPO_ROOT/extensions_config.json"
fi

if [ ! -f "$DEER_FLOW_EXTENSIONS_CONFIG_PATH" ]; then
    if [ -f "$REPO_ROOT/extensions_config.json" ]; then
        cp "$REPO_ROOT/extensions_config.json" "$DEER_FLOW_EXTENSIONS_CONFIG_PATH"
        echo -e "${GREEN}✓ Seeded extensions_config.json → $DEER_FLOW_EXTENSIONS_CONFIG_PATH${NC}"
    else
        # Create a minimal empty config so the gateway doesn't fail on startup
        echo '{"mcpServers":{},"skills":{}}' > "$DEER_FLOW_EXTENSIONS_CONFIG_PATH"
        echo -e "${YELLOW}⚠ extensions_config.json not found, created empty config at $DEER_FLOW_EXTENSIONS_CONFIG_PATH${NC}"
    fi
else
    echo -e "${GREEN}✓ extensions_config.json: $DEER_FLOW_EXTENSIONS_CONFIG_PATH${NC}"
fi


# ── BETTER_AUTH_SECRET ───────────────────────────────────────────────────────
# 2026-10-05 审计 D-10：BETTER_AUTH_SECRET 全仓零代码消费（本仓认证走
# AUTH_JWT_SECRET），僵尸基建已删——历史部署若曾写 .better-auth-secret 文件，
# 清理时可直接删除（无任何组件读取）。

# ── PPT_CONVERTER_SECRET ─────────────────────────────────────────────────────
# SEC-H5 (2026-09-04): ppt-converter sidecar is fail-closed (empty secret →
# 401 on every non-health request) and compose now requires the var (`:?`).
# Gateway and converter containers must share ONE value — generated here once
# and persisted, so both services see the same secret across deploys.
# Never generate inside the containers (two different values → all 401).

_ppt_secret_file="$DEER_FLOW_HOME/.ppt-converter-secret"
if [ -z "$PPT_CONVERTER_SECRET" ]; then
    if [ -f "$_ppt_secret_file" ]; then
        export PPT_CONVERTER_SECRET
        PPT_CONVERTER_SECRET="$(cat "$_ppt_secret_file")"
        echo -e "${GREEN}✓ PPT_CONVERTER_SECRET loaded from $_ppt_secret_file${NC}"
    else
        export PPT_CONVERTER_SECRET
        PPT_CONVERTER_SECRET="$(python3 -c 'import secrets; print(secrets.token_hex(32))')"
        echo "$PPT_CONVERTER_SECRET" > "$_ppt_secret_file"
        chmod 600 "$_ppt_secret_file"
        echo -e "${GREEN}✓ PPT_CONVERTER_SECRET generated → $_ppt_secret_file${NC}"
    fi
fi

# ── detect_sandbox_mode ───────────────────────────────────────────────────────

detect_sandbox_mode() {
    local sandbox_use=""
    local provisioner_url=""

    [ -f "$DEER_FLOW_CONFIG_PATH" ] || { echo "local"; return; }

    sandbox_use=$(awk '
        /^[[:space:]]*sandbox:[[:space:]]*$/ { in_sandbox=1; next }
        in_sandbox && /^[^[:space:]#]/ { in_sandbox=0 }
        in_sandbox && /^[[:space:]]*use:[[:space:]]*/ {
            line=$0; sub(/^[[:space:]]*use:[[:space:]]*/, "", line); print line; exit
        }
    ' "$DEER_FLOW_CONFIG_PATH")

    provisioner_url=$(awk '
        /^[[:space:]]*sandbox:[[:space:]]*$/ { in_sandbox=1; next }
        in_sandbox && /^[^[:space:]#]/ { in_sandbox=0 }
        in_sandbox && /^[[:space:]]*provisioner_url:[[:space:]]*/ {
            line=$0; sub(/^[[:space:]]*provisioner_url:[[:space:]]*/, "", line); print line; exit
        }
    ' "$DEER_FLOW_CONFIG_PATH")

    if [[ "$sandbox_use" == *"deerflow.community.aio_sandbox:AioSandboxProvider"* ]]; then
        if [ -n "$provisioner_url" ]; then
            echo "provisioner"
        else
            echo "aio"
        fi
    else
        echo "local"
    fi
}

# ── down ──────────────────────────────────────────────────────────────────────

if [ "$CMD" = "down" ]; then
    # Set minimal env var defaults so docker compose can parse the file without
    # warning about unset variables that appear in volume specs.
    export DEER_FLOW_HOME="${DEER_FLOW_HOME:-$REPO_ROOT/backend/.deer-flow}"
    export DEER_FLOW_CONFIG_PATH="${DEER_FLOW_CONFIG_PATH:-$DEER_FLOW_HOME/config.yaml}"
    export DEER_FLOW_EXTENSIONS_CONFIG_PATH="${DEER_FLOW_EXTENSIONS_CONFIG_PATH:-$DEER_FLOW_HOME/extensions_config.json}"
    export DEER_FLOW_DOCKER_SOCKET="${DEER_FLOW_DOCKER_SOCKET:-/var/run/docker.sock}"
    export DEER_FLOW_REPO_ROOT="${DEER_FLOW_REPO_ROOT:-$REPO_ROOT}"
    "${COMPOSE_CMD[@]}" down
    exit 0
fi

# ── build ────────────────────────────────────────────────────────────────────
# Build produces mode-agnostic images. No --gateway or sandbox detection needed.

if [ "$CMD" = "build" ]; then
    echo "=========================================="
    echo "  DeerFlow — Building Images"
    echo "=========================================="
    echo ""

    # Docker socket is needed for compose to parse volume specs
    if [ -z "$DEER_FLOW_DOCKER_SOCKET" ]; then
        export DEER_FLOW_DOCKER_SOCKET="/var/run/docker.sock"
    fi

    "${COMPOSE_CMD[@]}" build

    echo ""
    echo "=========================================="
    echo "  ✓ Images built successfully"
    echo "=========================================="
    echo ""
    echo "  Next: deploy.sh start"
    echo ""
    exit 0
fi

# ── Banner ────────────────────────────────────────────────────────────────────

echo "=========================================="
echo "  DeerFlow Production Deployment"
echo "=========================================="
echo ""

# ── Detect runtime configuration ────────────────────────────────────────────
# Only needed for start / up — determines whether provisioner is launched.

sandbox_mode="$(detect_sandbox_mode)"
echo -e "${BLUE}Sandbox mode: $sandbox_mode${NC}"

echo -e "${BLUE}Runtime: Gateway embedded agent runtime${NC}"

services="frontend gateway nginx"

if [ "$sandbox_mode" = "provisioner" ]; then
    services="$services provisioner"
fi

# ── DEER_FLOW_DOCKER_SOCKET ───────────────────────────────────────────────────

if [ -z "$DEER_FLOW_DOCKER_SOCKET" ]; then
    export DEER_FLOW_DOCKER_SOCKET="/var/run/docker.sock"
fi

if [ "$sandbox_mode" != "local" ]; then
    if [ ! -S "$DEER_FLOW_DOCKER_SOCKET" ]; then
        echo -e "${RED}⚠ Docker socket not found at $DEER_FLOW_DOCKER_SOCKET${NC}"
        echo "  AioSandboxProvider (DooD) will not work."
        exit 1
    else
        echo -e "${GREEN}✓ Docker socket: $DEER_FLOW_DOCKER_SOCKET${NC}"
    fi
fi

echo ""

# ── Start / Up ───────────────────────────────────────────────────────────────

if [ "$CMD" = "start" ]; then
    echo "Starting containers (no rebuild)..."
    echo ""
    # shellcheck disable=SC2086
    "${COMPOSE_CMD[@]}" up -d --remove-orphans $services
else
    # Default: build + start
    echo "Building images and starting containers..."
    echo ""
    # shellcheck disable=SC2086
    "${COMPOSE_CMD[@]}" up --build -d --remove-orphans $services
fi

# 部署后健康校验：up -d 返回 0 只代表容器被创建，配置错/端口冲突导致
# gateway 起不来时脚本照样打绿对勾。端点是 /health（非 /api/health--
# 后者不存在，nginx 无该 location，轮询会永远 404）。
echo ""
echo "Waiting for gateway to become healthy..."
_healthy=0
for _i in $(seq 1 30); do
    if curl -sf "http://localhost:${PORT:-2026}/health" >/dev/null 2>&1; then
        _healthy=1
        echo -e "${GREEN}✓ Gateway healthy${NC}"
        break
    fi
    sleep 2
done
if [ "$_healthy" -ne 1 ]; then
    echo -e "${RED}✗ Gateway did not become healthy within 60s${NC}"
    echo "  排查："
    echo "    ${COMPOSE_CMD[*]} ps"
    echo "    ${COMPOSE_CMD[*]} logs --tail 100 gateway"
    exit 1
fi

echo ""
echo "=========================================="
echo "  DeerFlow is running!"
echo "=========================================="
echo ""
echo "  🌐 Application: http://localhost:${PORT:-2026}"
echo "  📡 API Gateway: http://localhost:${PORT:-2026}/api/*"
echo "  🤖 Runtime:     Gateway embedded"
echo "  API:            /api/langgraph/* → Gateway"
echo ""
echo "  Manage:"
echo "    make down        — stop and remove containers"
echo "    make docker-logs — view logs"
echo ""
