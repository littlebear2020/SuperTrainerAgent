#!/bin/bash
#
# DeerFlow 测试服务器部署脚本
# 使用 tar+scp 传输，与生产部署流程一致
#
# 用法:
#   1. 本地执行: ./deploy_to_test_server.sh pack     # 打包
#   2. 本地执行: ./deploy_to_test_server.sh upload   # 上传到测试服务器
#   3. SSH 到测试服务器后执行: ./deploy_to_test_server.sh deploy  # 部署
#
# 完整流程:
#   ./deploy_to_test_server.sh pack
#   ./deploy_to_test_server.sh upload
#   ssh root@<测试服务器IP>
#   cd /opt/trainer-ai-test && ./deploy_to_test_server.sh deploy
#
set -e

# ════════════════════════════════════════════════════════════
# 配置：修改这里指向你的测试服务器
# ════════════════════════════════════════════════════════════
SERVER="${TEST_SERVER:-root@<测试服务器IP>}"
SSH_PORT="${TEST_SSH_PORT:-22}"
PROJECT_DIR="${TEST_PROJECT_DIR:-/opt/trainer-ai-test}"
# ════════════════════════════════════════════════════════════

LOCAL_DIR="$(cd "$(dirname "$0")" && pwd)"
TAR_FILE="$LOCAL_DIR/../deer-flow-test-update.tar.gz"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# ============================================================
# pack: 本地打包
# ============================================================
release_sha() {
    tar -xOf "$1" release-manifest.json | python3 -c 'import json, sys; print(json.load(sys.stdin)["source_sha"])'
}

pack() {
    echo -e "${BLUE}============================================${NC}"
    echo -e "${BLUE}  从指定 Git 提交打包项目文件（测试环境）${NC}"
    echo -e "${BLUE}============================================${NC}"
    echo ""

    cd "$LOCAL_DIR"
    REF="${TEST_DEPLOY_REF:-origin/main}"
    ARCHIVE_ARGS=(--ref "$REF" --base-ref origin/main --output "$TAR_FILE")
    if [ "${TEST_ALLOW_UNMERGED:-0}" = "1" ]; then
        ARCHIVE_ARGS+=(--allow-unmerged)
        echo -e "${YELLOW}  显式允许测试未合入 ref: $REF${NC}"
    fi
    if [ "${TEST_INCLUDE_LOCAL_CONFIG:-0}" = "1" ]; then
        ARCHIVE_ARGS+=(--include-local-config)
        echo -e "${YELLOW}  显式追加本地测试配置${NC}"
    else
        echo "提示：本包不含 config.yaml/.env，依赖服务器现有配置（首次部署需设 TEST_INCLUDE_LOCAL_CONFIG=1）"
    fi
    if [ "${1:-}" = "--no-sandbox" ]; then
        ARCHIVE_ARGS+=(--exclude docker/sandbox/cache/chrome-linux)
        echo -e "${YELLOW}  跳过 Chrome 缓存，保留字体、Python wheels 与 BPE 缓存${NC}"
    fi

    python3 scripts/release_archive.py "${ARCHIVE_ARGS[@]}" || { echo -e "${RED}❌ 打包失败，旧包未替换${NC}"; return 1; }

    RELEASE_SHA="$(release_sha "$TAR_FILE")" || { echo -e "${RED}❌ 发布包缺少有效 manifest，请检查打包错误${NC}"; return 1; }
    SIZE=$(ls -lh "$TAR_FILE" | awk '{print $5}')
    echo ""
    echo -e "${GREEN}✅ 打包完成: $SIZE${NC}"
    echo -e "   文件位置: $TAR_FILE"
    echo "   源码 SHA: $RELEASE_SHA"
}

# ============================================================
# upload: 上传到测试服务器
# ============================================================
upload() {
    echo -e "${BLUE}============================================${NC}"
    echo -e "${BLUE}  上传到测试服务器 ${SERVER}${NC}"
    echo -e "${BLUE}============================================${NC}"
    echo ""

    if [ ! -f "$TAR_FILE" ]; then
        echo -e "${RED}❌ 未找到打包文件，先执行: ./deploy_to_test_server.sh pack${NC}"
        exit 1
    fi

    # 确保目标目录存在
    ssh -p "$SSH_PORT" "$SERVER" "mkdir -p $PROJECT_DIR"

    RELEASE_SHA="$(release_sha "$TAR_FILE")" || { echo -e "${RED}❌ 压缩包缺少 release-manifest.json，请用新流程重新 pack${NC}"; return 1; }
    EXPECTED_SHA="$(git rev-parse --verify --end-of-options "${TEST_DEPLOY_REF:-origin/main}^{commit}")" || { echo -e "${RED}❌ 无法解析预期测试发布 ref${NC}"; return 1; }
    if [ "$RELEASE_SHA" != "$EXPECTED_SHA" ]; then
        echo -e "${RED}❌ 发布包 SHA 与 ${TEST_DEPLOY_REF:-origin/main} 不一致，禁止上传旧包，请重新 pack${NC}"
        return 1
    fi
    echo "  上传源码 SHA: $RELEASE_SHA"
    echo "  正在上传..."
    scp -P "$SSH_PORT" "$TAR_FILE" "${SERVER}:${PROJECT_DIR}/"
    scp -P "$SSH_PORT" "$LOCAL_DIR/deploy_to_test_server.sh" "${SERVER}:${PROJECT_DIR}/"

    echo ""
    echo -e "${GREEN}✅ 上传完成${NC}"
    echo ""
    echo "  下一步:"
    echo "    ssh -p $SSH_PORT $SERVER"
    echo "    cd $PROJECT_DIR && bash deploy_to_test_server.sh deploy"
}

# ============================================================
# deploy: 在测试服务器上执行部署
# ============================================================
deploy() {
    cd "$PROJECT_DIR"

    echo -e "${BLUE}============================================${NC}"
    echo -e "${BLUE}  DeerFlow 测试环境部署${NC}"
    echo -e "${BLUE}  目标目录: $PROJECT_DIR${NC}"
    echo -e "${BLUE}============================================${NC}"
    echo ""

    # ── 步骤 1: 备份现有数据 ──
    echo -e "${YELLOW}步骤 1/6: 备份现有数据${NC}"
    TIMESTAMP=$(date +%Y%m%d%H%M)
    if [ -d "backend/.deer-flow" ]; then
        cp -r backend/.deer-flow "backend/.deer-flow.bak.${TIMESTAMP}" 2>/dev/null || true
        echo "  ✓ backend/.deer-flow → backend/.deer-flow.bak.${TIMESTAMP}"
    fi
    if [ -f ".env" ]; then
        cp .env ".env.bak.${TIMESTAMP}" 2>/dev/null || true
        echo "  ✓ .env → .env.bak.${TIMESTAMP}"
    fi
    if [ -f "config.yaml" ]; then
        cp config.yaml "config.yaml.bak.${TIMESTAMP}" 2>/dev/null || true
        echo "  ✓ config.yaml → config.yaml.bak.${TIMESTAMP}"
    fi
    if [ -f "extensions_config.json" ]; then
        cp extensions_config.json "extensions_config.json.bak.${TIMESTAMP}" 2>/dev/null || true
        echo "  ✓ extensions_config.json → extensions_config.json.bak.${TIMESTAMP}"
    fi
    if [ -f "frontend/.env" ]; then
        cp frontend/.env "frontend/.env.bak.${TIMESTAMP}" 2>/dev/null || true
        echo "  ✓ frontend/.env → frontend/.env.bak.${TIMESTAMP}"
    fi

    # ── 步骤 2: 停止服务并解压 ──
    echo ""
    echo -e "${YELLOW}步骤 2/6: 停止旧服务并解压新代码${NC}"
    RELEASE_SHA="$(release_sha deer-flow-test-update.tar.gz)" || { echo -e "${RED}❌ 压缩包缺少 release-manifest.json，已停止部署${NC}"; return 1; }

    # 停止旧容器（如果存在）
    if [ -f "scripts/deploy.sh" ]; then
        bash scripts/deploy.sh down 2>/dev/null || true
    else
        docker stop deer-flow-nginx deer-flow-frontend deer-flow-gateway 2>/dev/null || true
        docker rm deer-flow-nginx deer-flow-frontend deer-flow-gateway 2>/dev/null || true
    fi

    # 解压新代码（覆盖）
    echo "  解压 deer-flow-test-update.tar.gz ..."
    tar xzf deer-flow-test-update.tar.gz
    echo "  ✓ 解压完成"
    echo "  部署源码 SHA: $RELEASE_SHA"

    # ── 步骤 3: 恢复配置 ──
    echo ""
    echo -e "${YELLOW}步骤 3/6: 恢复配置文件${NC}"

    # .env
    BAK_ENV=$(ls -t .env.bak.* 2>/dev/null | head -1)
    if [ -n "$BAK_ENV" ]; then
        if ! diff -q "$BAK_ENV" .env > /dev/null 2>&1; then
            echo "  ⚠️  .env 被覆盖，正在恢复..."
            cp "$BAK_ENV" .env
        fi
    fi
    echo "  ✓ .env 已确认"

    # config.yaml
    BAK_CFG=$(ls -t config.yaml.bak.* 2>/dev/null | head -1)
    if [ -n "$BAK_CFG" ]; then
        if ! diff -q "$BAK_CFG" config.yaml > /dev/null 2>&1; then
            echo "  ⚠️  config.yaml 被覆盖，正在恢复..."
            cp "$BAK_CFG" config.yaml
        fi
    fi
    echo "  ✓ config.yaml 已确认"

    # extensions_config.json
    BAK_EXT=$(ls -t extensions_config.json.bak.* 2>/dev/null | head -1)
    if [ -n "$BAK_EXT" ]; then
        if ! diff -q "$BAK_EXT" extensions_config.json > /dev/null 2>&1; then
            echo "  ⚠️  extensions_config.json 被覆盖，正在恢复..."
            cp "$BAK_EXT" extensions_config.json
        fi
    fi
    echo "  ✓ extensions_config.json 已确认"

    # frontend/.env
    BAK_FE=$(ls -t frontend/.env.bak.* 2>/dev/null | head -1)
    if [ -n "$BAK_FE" ]; then
        if ! diff -q "$BAK_FE" frontend/.env > /dev/null 2>&1; then
            echo "  ⚠️  frontend/.env 被覆盖，正在恢复..."
            cp "$BAK_FE" frontend/.env
        fi
    fi
    echo "  ✓ frontend/.env 已确认"

    # ── 步骤 4: 修复权限 ──
    echo ""
    echo -e "${YELLOW}步骤 4/6: 修复目录权限${NC}"
    chmod -R a+rX "$PROJECT_DIR/skills/public/" 2>/dev/null || true
    chmod -R a+rX "$PROJECT_DIR/skills/custom/" 2>/dev/null || true
    mkdir -p "$PROJECT_DIR/skills/custom"
    echo "  ✓ skills 目录权限已修复"

    # ── 步骤 5: 构建沙箱镜像 ──
    echo ""
    echo -e "${YELLOW}步骤 5/6: 检查并构建沙箱镜像${NC}"

    CHROME_BIN="docker/sandbox/cache/chrome-linux/chrome-headless-shell-linux64/chrome-headless-shell"
    FONT_DIR="docker/sandbox/cache/fonts/fonts"
    DOCKERFILE="docker/sandbox/Dockerfile"
    CACHE_OK=true
    NEED_REBUILD=false

    # 检查是否有 Dockerfile 改动需要重建（如新增了字体配置）
    if [ -f "$DOCKERFILE" ] && grep -q "ppt-fallback" "$DOCKERFILE" 2>/dev/null; then
        NEED_REBUILD=true
        echo "  ✓ Dockerfile 包含字体配置，需要构建沙箱"
    fi

    if [ -f "$CHROME_BIN" ] && [ $(stat -c%s "$CHROME_BIN" 2>/dev/null || echo 0) -lt 1000000 ]; then
        echo "  ⚠️  Chrome headless shell 为 LFS 指针文件（< 1MB）"
        CACHE_OK=false
    elif [ ! -f "$CHROME_BIN" ]; then
        echo "  ⚠️  Chrome 缓存文件不存在"
        CACHE_OK=false
    fi

    # 检查字体文件是否存在
    FONT_COUNT=$(ls "$FONT_DIR"/*.ttf "$FONT_DIR"/*.otf 2>/dev/null | wc -l)
    if [ "$FONT_COUNT" -gt 0 ]; then
        echo "  ✓ 发现 ${FONT_COUNT} 个字体缓存文件"
        NEED_REBUILD=true
    fi

    if [ "$CACHE_OK" = false ] && [ "$NEED_REBUILD" = false ]; then
        echo "  ⚠️  沙箱缓存文件不完整且无新配置，跳过沙箱构建"
        echo "  如需沙箱功能，请使用不带 --no-sandbox 的 pack 重新打包"
    else
        echo "  ✓ 开始构建沙箱镜像..."
        docker build -t littlebear2016/deerflow-sandbox:latest -f docker/sandbox/Dockerfile .
        echo "  ✓ 沙箱镜像构建完成"
    fi

    # ── 步骤 6: 构建并启动所有服务 ──
    echo ""
    echo -e "${YELLOW}步骤 6/6: 构建并启动服务${NC}"

    # 清理旧 Docker 资源（避免磁盘不足）
    # F-B31-001: 收窄为无 -a 无 --volumes——只清停止容器/悬空镜像/未用网络，
    # 不动 tagged 镜像与匿名卷（-a --volumes 违反 AGENTS.md 红线：误删在用
    # 卷/镜像）。旧 tagged 镜像由人工定期清理。
    docker system prune -f 2>/dev/null || true

    # 启动服务
    bash scripts/deploy.sh

    echo ""
    echo -e "${GREEN}============================================${NC}"
    echo -e "${GREEN}  ✅ 测试环境部署完成！${NC}"
    echo -e "${GREEN}============================================${NC}"
    echo ""
    echo "  验证命令:"
    echo "    docker ps --filter 'name=deer-flow'"
    echo "    docker logs deer-flow-gateway --tail 50"
    echo "    curl http://localhost:2026/api/health"
    echo ""
    echo "  确认正常后，清理备份文件:"
    echo "    rm -f deer-flow-test-update.tar.gz .env.bak.* config.yaml.bak.* extensions_config.json.bak.* frontend/.env.bak.*"
    echo "    rm -rf backend/.deer-flow.bak.*"
}

# ============================================================
# 主入口
# ============================================================
case "${1:-}" in
    pack)
        pack "${2:-}"
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
        echo "  pack [--no-sandbox]  - 本地打包项目文件"
        echo "  upload               - 上传到测试服务器"
        echo "  deploy               - 在测试服务器上执行部署"
        echo ""
        echo "环境变量（覆盖默认值）:"
        echo "  TEST_SERVER          测试服务器地址（默认: root@<测试服务器IP>）"
        echo "  TEST_SSH_PORT        SSH 端口（默认: 22）"
        echo "  TEST_PROJECT_DIR     部署目录（默认: /opt/trainer-ai-test）"
        echo "  TEST_DEPLOY_REF      测试发布 ref（默认: origin/main）"
        echo "  TEST_ALLOW_UNMERGED=1 允许测试未合入 ref"
        echo "  TEST_INCLUDE_LOCAL_CONFIG=1 显式追加本地测试配置"
        echo ""
        echo "完整流程:"
        echo "  1. 编辑此脚本，修改 SERVER 为测试服务器 IP"
        echo "  2. ./deploy_to_test_server.sh pack --no-sandbox"
        echo "  3. ./deploy_to_test_server.sh upload"
        echo "  4. ssh root@<测试服务器IP>"
        echo "  5. cd /opt/trainer-ai-test && bash deploy_to_test_server.sh deploy"
        ;;
esac
