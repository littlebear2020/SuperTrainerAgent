#!/bin/bash
#
# DeerFlow 服务器部署脚本
# 使用 tar+scp 传输（服务器无 git），SSH 端口 2222
#
# 用法:
#   1. 本地执行: ./deploy_on_server.sh pack     # 打包
#   2. 本地执行: ./deploy_on_server.sh upload   # 上传到服务器
#   3. SSH 到服务器后执行: ./deploy_on_server.sh deploy  # 部署
#
set -e

SERVER="${DEPLOY_SERVER:?请设置 DEPLOY_SERVER}"
SSH_PORT=2222
PROJECT_DIR="/opt/trainer-ai"
LOCAL_DIR="$(cd "$(dirname "$0")" && pwd)"

# ============================================================
# pack: 本地打包（包含沙箱缓存）
# ============================================================
pack() {
    echo "============================================"
    echo "  打包项目文件..."
    echo "============================================"

    cd "$LOCAL_DIR"

    # --no-sandbox-cache: 跳过沙箱缓存（服务器已有）
    SANDBOX_OPT=''
    if [ "${2:-}" = "--no-sandbox" ]; then
        SANDBOX_OPT='--exclude=docker/sandbox/cache'
    fi

    tar czf ../deer-flow-update.tar.gz \
      --exclude='node_modules' \
      --exclude='.venv' \
      --exclude='frontend/.next' \
      --exclude='__pycache__' \
      --exclude='.git' \
      --exclude='.claude' \
      --exclude='.DS_Store' \
      --exclude='docs/plans' \
      --exclude='logs' \
      --exclude='temp' \
      --exclude='fix-config.py' \
      --exclude='workspace-redesign.html' \
      --exclude='backend/.deer-flow/data' \
      --exclude='backend/.deer-flow/users' \
      --exclude='backend/.deer-flow/channels' \
      --exclude='backend/.deer-flow/*.db' \
      --exclude='backend/.deer-flow/*.json' \
      --exclude='deer-flow-update.tar.gz' \
      $SANDBOX_OPT \
      .

    echo "✅ 打包完成: $(ls -lh ../deer-flow-update.tar.gz | awk '{print $5}')"
    echo "   文件位置: ../deer-flow-update.tar.gz"
}

# ============================================================
# upload: 上传到服务器
# ============================================================
upload() {
    echo "============================================"
    echo "  上传到服务器 ${SERVER}..."
    echo "============================================"

    if [ ! -f ../deer-flow-update.tar.gz ]; then
        echo "❌ 未找到打包文件，先执行: ./deploy_on_server.sh pack"
        exit 1
    fi

    scp -P $SSH_PORT ../deer-flow-update.tar.gz ${SERVER}:${PROJECT_DIR}/
    echo "✅ 上传完成"
}

# ============================================================
# deploy: 在服务器上执行部署
# ============================================================
deploy() {
    cd "$PROJECT_DIR"

    echo "============================================"
    echo "  步骤 1/6: 备份关键数据"
    echo "============================================"
    cp -r backend/.deer-flow backend/.deer-flow.bak.$(date +%Y%m%d%H%M) 2>/dev/null || true
    cp .env .env.bak.$(date +%Y%m%d%H%M) 2>/dev/null || true
    cp config.yaml config.yaml.bak.$(date +%Y%m%d%H%M) 2>/dev/null || true
    cp extensions_config.json extensions_config.json.bak.$(date +%Y%m%d%H%M) 2>/dev/null || true

    echo ""
    echo "============================================"
    echo "  步骤 2/6: 停止服务并解压"
    echo "============================================"
    ./scripts/deploy.sh down
    tar xzf deer-flow-update.tar.gz

    echo ""
    echo "============================================"
    echo "  步骤 3/6: 恢复配置（如被覆盖）"
    echo "============================================"
    # 检查 .env 是否被覆盖（简单比较行数）
    BAK_ENV=$(ls -t .env.bak.* 2>/dev/null | head -1)
    if [ -n "$BAK_ENV" ]; then
        if ! diff -q "$BAK_ENV" .env > /dev/null 2>&1; then
            echo "⚠️  .env 被覆盖，正在恢复..."
            cp "$BAK_ENV" .env
        fi

        BAK_CFG=$(ls -t config.yaml.bak.* 2>/dev/null | head -1)
        if [ -n "$BAK_CFG" ] && ! diff -q "$BAK_CFG" config.yaml > /dev/null 2>&1; then
            echo "⚠️  config.yaml 被覆盖，正在恢复..."
            cp "$BAK_CFG" config.yaml
        fi
    fi
    echo "✅ 配置已确认"

    echo ""
    echo "============================================"
    echo "  步骤 3.5/6: 修复 skills 目录权限"
    echo "============================================"
    chmod -R a+rX "$PROJECT_DIR/skills/public/"
    chmod -R a+rX "$PROJECT_DIR/skills/custom/" 2>/dev/null || true
    echo "✅ skills 目录权限已修复"

    echo ""
    echo "============================================"
    echo "  步骤 4/6: 检查沙箱缓存 & 构建沙箱镜像"
    echo "============================================"

    # 检查沙箱缓存文件是否为真实二进制（非 LFS 指针）
    CHROME_BIN="docker/sandbox/cache/chrome-linux/chrome-headless-shell-linux64/chrome-headless-shell"

    CACHE_OK=true
    if [ ! -f "$CHROME_BIN" ] || [ $(stat -c%s "$CHROME_BIN" 2>/dev/null || echo 0) -lt 1000000 ]; then
        echo "⚠️  Chrome headless shell 缺失或为 LFS 指针文件（< 1MB）"
        CACHE_OK=false
    fi

    if [ "$CACHE_OK" = true ]; then
        echo "✅ 沙箱缓存文件完整，开始构建..."
        # 关键：必须从项目根目录构建（最后的 . 不能少）
        docker build -t littlebear2016/deerflow-sandbox:latest -f docker/sandbox/Dockerfile .
    else
        echo "❌ 沙箱缓存不完整，需要单独上传缓存文件："
        echo "   本地执行:"
        echo "     tar czf sandbox-cache.tar.gz docker/sandbox/cache/"
        echo "     scp -P $SSH_PORT sandbox-cache.tar.gz ${SERVER}:${PROJECT_DIR}/"
        echo "   然后重新运行此脚本"
        exit 1
    fi

    echo ""
    echo "============================================"
    echo "  步骤 5/6: 构建并启动所有服务"
    echo "============================================"

    # 清理旧 Docker 资源（避免磁盘不足）
    # F-B31-001: 收窄为无 -a 无 --volumes——只清停止容器/悬空镜像/未用网络，
    # 不动 tagged 镜像与匿名卷（-a --volumes 违反 AGENTS.md 红线：误删在用
    # 卷/镜像）。旧 tagged 镜像由人工定期清理。
    docker system prune -f 2>/dev/null || true

    ./scripts/deploy.sh

    echo ""
    echo "============================================"
    echo "  ✅ 部署完成！"
    echo "============================================"
    echo ""
    echo "  验证命令："
    echo "    docker ps                          # 查看容器状态"
    echo "    docker logs deer-flow-gateway --tail 50  # 查看 Gateway 日志"
    echo "    curl http://localhost:2026/health   # 健康检查"
    echo ""
    echo "  访问地址: http://${SERVER#*@}:2026"
    echo ""

    # 清理备份（确认成功后）
    echo "  确认正常后，清理备份文件："
    echo "    rm -f deer-flow-update.tar.gz .env.bak.* config.yaml.bak.* extensions_config.json.bak.*"
    echo "    rm -rf backend/.deer-flow.bak.*"
}

# ============================================================
# 主入口
# ============================================================
case "${1:-}" in
    pack)
        pack "$@"
        ;;
    upload)
        upload
        ;;
    deploy)
        deploy
        ;;
    *)
        echo "用法: $0 {pack|upload|deploy}"
        echo ""
        echo "  pack    - 本地打包项目文件"
        echo "            可选: --no-sandbox 跳过沙箱缓存（减小 ~700MB）"
        echo "  upload  - 上传到服务器"
        echo "  deploy  - 在服务器上执行部署"
        echo ""
        echo "完整流程:"
        echo "  1. ./deploy_on_server.sh pack [--no-sandbox]"
        echo "  2. ./deploy_on_server.sh upload"
        echo "  3. ssh -p $SSH_PORT $SERVER"
        echo "  4. cd $PROJECT_DIR && ./deploy_on_server.sh deploy"
        ;;
esac
