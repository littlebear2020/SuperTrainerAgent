#!/usr/bin/env bash
#
# 本地打包 + 上传 + 远程部署 一键脚本
# 在 Mac Terminal 中运行: bash scripts/local-deploy.sh
#
# 选项:
#   --no-config     排除配置文件（保留服务器上的 .env/config.yaml 等）
#   --no-sandbox    跳过 sandbox 镜像构建
#   --skip-upload   只打包不上传

set -e

# 部署目标必须由环境变量提供，真实主机和账号不得提交到公开仓库。用法：
#   DEPLOY_SERVER=user@example.com bash scripts/local-deploy.sh
SERVER="${DEPLOY_SERVER:?请设置 DEPLOY_SERVER，例如 DEPLOY_SERVER=user@example.com}"
REMOTE_DIR="/opt/trainer-ai"
LOCAL_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PROJECT_NAME="$(basename "$LOCAL_DIR")"
PARENT_DIR="$(dirname "$LOCAL_DIR")"
TMP_FILE="/tmp/deer-flow-deploy.tar.gz"

# ── Colors ──
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

# ── Parse args ──
EXCLUDE_CONFIG=false
NO_SANDBOX=false
SKIP_UPLOAD=false
for arg in "$@"; do
    case "$arg" in
        --no-config)   EXCLUDE_CONFIG=true ;;
        --no-sandbox)  NO_SANDBOX=true ;;
        --skip-upload) SKIP_UPLOAD=true ;;
        *)
            echo "Usage: bash scripts/local-deploy.sh [--no-config] [--no-sandbox] [--skip-upload]"
            exit 1
            ;;
    esac
done

echo -e "${BLUE}=== DeerFlow 部署 ===${NC}"
echo ""

# ── Step 1: 打包 ──
echo -e "${BLUE}[1/3] 打包项目...${NC}"

EXCLUDES=(
    --exclude='backend/.deer-flow'
    --exclude='.git'
    --exclude='node_modules'
    --exclude='__pycache__'
    --exclude='.venv'
    --exclude='.DS_Store'
    --exclude='frontend/.next'
    --exclude='.claude'
    --exclude='logs'
    --exclude='temp'
)

if [ "$EXCLUDE_CONFIG" = true ]; then
    EXCLUDES+=(
        --exclude='config.yaml'
        --exclude='.env'
        --exclude='extensions_config.json'
        --exclude='frontend/.env'
    )
    echo -e "${YELLOW}  模式: 排除配置文件（保留服务器配置）${NC}"
else
    echo -e "${YELLOW}  模式: 包含配置文件（会覆盖服务器配置）${NC}"
fi

tar czf "$TMP_FILE" "${EXCLUDES[@]}" -C "$PARENT_DIR" "$PROJECT_NAME"

SIZE=$(du -sh "$TMP_FILE" | cut -f1)
echo -e "${GREEN}  ✓ 打包完成 ($SIZE)${NC}"
echo ""

if [ "$SKIP_UPLOAD" = true ]; then
    echo -e "${GREEN}=== --skip-upload，只打包不上传 ===${NC}"
    echo "  文件: $TMP_FILE"
    exit 0
fi

# ── Step 2: 上传 + 解压 ──
echo -e "${BLUE}[2/3] 上传并解压到服务器...${NC}"

# 用 scp + ssh 分开执行更可靠
echo -e "${YELLOW}  需要输入服务器密码（可能需要输入两次）${NC}"
scp "$TMP_FILE" "$SERVER:/opt/"
ssh "$SERVER" "mkdir -p $REMOTE_DIR && tar xzf /opt/$(basename "$TMP_FILE") -C $REMOTE_DIR --strip-components=1 && rm -f /opt/$(basename "$TMP_FILE")"
echo -e "${GREEN}  ✓ 上传解压完成${NC}"
echo ""

# ── Step 3: 构建和启动 ──
echo -e "${BLUE}[3/3] 远程构建启动...${NC}"
echo ""

read -p "是否需要重建 sandbox 镜像? (y/n): " sandbox_answer
SANDBOX_CMD=""
if [ "$sandbox_answer" = "y" ] && [ "$NO_SANDBOX" = false ]; then
    SANDBOX_CMD="cd $REMOTE_DIR && docker build -t littlebear2016/deerflow-sandbox:latest -f docker/sandbox/Dockerfile . && echo 'sandbox 镜像构建完成' &&"
fi

read -p "是否立即构建并启动服务? (y/n): " deploy_answer
if [ "$deploy_answer" = "y" ]; then
    ssh "$SERVER" "$SANDBOX_CMD cd $REMOTE_DIR && NPM_REGISTRY=https://registry.npmmirror.com UV_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple APT_MIRROR=mirrors.aliyun.com bash scripts/deploy.sh"
else
    echo ""
    echo "手动启动命令:"
    echo "  ssh $SERVER"
    echo "  cd $REMOTE_DIR && NPM_REGISTRY=https://registry.npmmirror.com UV_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple APT_MIRROR=mirrors.aliyun.com bash scripts/deploy.sh"
fi

rm -f "$TMP_FILE"
echo ""
echo -e "${GREEN}=== 完成 ===${NC}"
echo ""
echo "浏览器访问: http://${SERVER#*@}:2026"
echo "查看日志:   ssh $SERVER \"docker logs deer-flow-gateway --tail 50 -f\""
