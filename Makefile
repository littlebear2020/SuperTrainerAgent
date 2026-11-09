# DeerFlow - Unified Development Environment

.PHONY: help config config-upgrade check check-env install setup doctor dev dev-daemon start start-daemon stop up down clean docker-init docker-start docker-stop docker-logs docker-logs-frontend docker-logs-gateway build-sandbox setup-sandbox deps-upgrade pre-deploy

BASH ?= bash
BACKEND_UV_RUN = cd backend && uv run

# Detect OS for Windows compatibility
ifeq ($(OS),Windows_NT)
    SHELL := cmd.exe
    PYTHON ?= python
    # Run repo shell scripts through Git Bash when Make is launched from cmd.exe / PowerShell.
    RUN_WITH_GIT_BASH = call scripts\run-with-git-bash.cmd
else
    PYTHON ?= python3
    RUN_WITH_GIT_BASH =
endif

help:
	@echo "DeerFlow Development Commands:"
	@echo "  make setup           - Interactive setup wizard (recommended for new users)"
	@echo "  make doctor          - Check configuration and system requirements"
	@echo "  make config          - Generate local config files (aborts if config already exists)"
	@echo "  make config-upgrade  - Merge new fields from config.example.yaml into config.yaml"
	@echo "  make check           - Quality gates: backend lint + frontend checks + docs convention"
	@echo "  make pre-deploy      - 部署前检查单（在途 PR/worktree/未同步 main + 近期部署动作 + config_version）"
	@echo "  make check-env       - Check if all required tools are installed"
	@echo "  make install         - Install all dependencies (frontend + backend + pre-commit hooks)"
	@echo "  make setup-sandbox   - Pre-pull sandbox container image (recommended)"
	@echo "  make build-sandbox   - Build the local sandbox image"
	@echo "  make deps-upgrade    - uv lock --upgrade + 回归提示（根治 floor 卡版，见 E6）"
	@echo "  make dev             - Start all services in development mode (with hot-reloading)"
	@echo "  make dev-daemon      - Start dev services in background (daemon mode)"
	@echo "  make start           - Start all services in production mode (optimized, no hot-reloading)"
	@echo "  make start-daemon    - Start prod services in background (daemon mode)"
	@echo "  make stop            - Stop all running services"
	@echo "  make clean           - Clean up processes and temporary files"
	@echo ""
	@echo "Docker Production Commands:"
	@echo "  make up              - Build and start production Docker services (localhost:2026)"
	@echo "  make down            - Stop and remove production Docker containers"
	@echo ""
	@echo "Docker Development Commands:"
	@echo "  make docker-init     - Pull the sandbox image"
	@echo "  make docker-start    - Start Docker services (mode-aware from config.yaml, localhost:2026)"
	@echo "  make docker-stop     - Stop Docker development services"
	@echo "  make docker-logs     - View Docker development logs"
	@echo "  make docker-logs-frontend - View Docker frontend logs"
	@echo "  make docker-logs-gateway - View Docker gateway logs"

## Setup & Diagnosis
setup:
	@$(BACKEND_UV_RUN) python ../scripts/setup_wizard.py

doctor:
	@$(BACKEND_UV_RUN) python ../scripts/doctor.py

config:
	@$(PYTHON) ./scripts/configure.py

config-upgrade:
	@$(RUN_WITH_GIT_BASH) ./scripts/config-upgrade.sh

# Check required tools（上游环境检查器；原 check 目标，2026-10-08 CI 优化批 A1 让名给质量门禁）
check-env:
	@$(PYTHON) ./scripts/check.py

# 质量门禁统一入口（2026-10-08 CI 优化批 A1）：CI 静态检查的本地逐字对应物——
# backend ruff / frontend eslint+tsc+prettier-check+knip+i18n / docs convention。
# CI 与本地唯一差异 = CI 额外跑 pytest/vitest/E2E 全量。
check:
	$(MAKE) -C backend lint
	$(MAKE) -C frontend check
	$(PYTHON) scripts/check_docs_convention.py

# 部署前检查单（2026-10-14）：在途 PR/worktree/未同步 main 为阻断项；
# 近 14 天 changelog 部署动作 + config_version 为提示项。部署公司服务器/测试机前跑。
pre-deploy:
	@$(PYTHON) scripts/pre_deploy_check.py

# Install all dependencies
install:
	@echo "Installing backend dependencies..."
	@cd backend && uv sync
	@echo "Installing frontend dependencies..."
	@cd frontend && pnpm install
	@echo "Installing pre-commit hooks..."
	@$(BACKEND_UV_RUN) --with pre-commit pre-commit install
	@echo "✓ All dependencies installed"
	@echo ""
	@echo "=========================================="
	@echo "  Optional: Pre-pull Sandbox Image"
	@echo "=========================================="
	@echo ""
	@echo "If you plan to use Docker/Container-based sandbox, you can pre-pull the image:"
	@echo "  make setup-sandbox"
	@echo ""

# Build custom sandbox image with video generation dependencies (FFmpeg, Node.js v22, edge-tts)
build-sandbox:
	@echo "Building custom sandbox image..."
	@docker build -t littlebear2016/deerflow-sandbox:latest -f docker/sandbox/Dockerfile .
	@echo ""
	@echo "Custom sandbox image built: littlebear2016/deerflow-sandbox:latest"
	@echo "To use it, update config.yaml:"
	@echo "  sandbox:"
	@echo "    use: deerflow.community.aio_sandbox:AioSandboxProvider"
	@echo "    image: littlebear2016/deerflow-sandbox:latest"

# Pre-pull sandbox Docker image (optional but recommended)
setup-sandbox:
	@echo "=========================================="
	@echo "  Pre-pulling Sandbox Container Image"
	@echo "=========================================="
	@echo ""
	@IMAGE=$$(grep -A 20 "# sandbox:" config.yaml 2>/dev/null | grep "image:" | awk '{print $$2}' | head -1); \
	if [ -z "$$IMAGE" ]; then \
		IMAGE="enterprise-public-cn-beijing.cr.volces.com/vefaas-public/all-in-one-sandbox:latest"; \
		echo "Using default image: $$IMAGE"; \
	else \
		echo "Using configured image: $$IMAGE"; \
	fi; \
	echo ""; \
	if command -v container >/dev/null 2>&1 && [ "$$(uname)" = "Darwin" ]; then \
		echo "Detected Apple Container on macOS, pulling image..."; \
		container image pull "$$IMAGE" || echo "⚠ Apple Container pull failed, will try Docker"; \
	fi; \
	if command -v docker >/dev/null 2>&1; then \
		echo "Pulling image using Docker..."; \
		if docker pull "$$IMAGE"; then \
			echo ""; \
			echo "✓ Sandbox image pulled successfully"; \
		else \
			echo ""; \
			echo "⚠ Failed to pull sandbox image (this is OK for local sandbox mode)"; \
		fi; \
	else \
		echo "✗ Neither Docker nor Apple Container is available"; \
		echo "  Please install Docker: https://docs.docker.com/get-docker/"; \
		exit 1; \
	fi

# Start all services in development mode (with hot-reloading)
dev:
	@$(PYTHON) ./scripts/check.py
	@$(RUN_WITH_GIT_BASH) ./scripts/serve.sh --dev

# Start all services in production mode (with optimizations)
start:
	@$(PYTHON) ./scripts/check.py
	@$(RUN_WITH_GIT_BASH) ./scripts/serve.sh --prod

# Start all services in daemon mode (background)
dev-daemon:
	@$(PYTHON) ./scripts/check.py
	@$(RUN_WITH_GIT_BASH) ./scripts/serve.sh --dev --daemon

# Start prod services in daemon mode (background)
start-daemon:
	@$(PYTHON) ./scripts/check.py
	@$(RUN_WITH_GIT_BASH) ./scripts/serve.sh --prod --daemon

# Stop all services
stop:
	@$(RUN_WITH_GIT_BASH) ./scripts/serve.sh --stop

# Clean up
clean: stop
	@echo "Cleaning up..."
	@-rm -rf backend/.deer-flow 2>/dev/null || true
	@-rm -rf backend/.langgraph_api 2>/dev/null || true
	@-rm -rf logs/*.log 2>/dev/null || true
	@echo "✓ Cleanup complete"

# ==========================================
# Docker Development Commands
# ==========================================

# Initialize Docker containers and install dependencies
docker-init:
	@$(RUN_WITH_GIT_BASH) ./scripts/docker.sh init

# Start Docker development environment
docker-start:
	@$(RUN_WITH_GIT_BASH) ./scripts/docker.sh start

# Stop Docker development environment
docker-stop:
	@$(RUN_WITH_GIT_BASH) ./scripts/docker.sh stop

# View Docker development logs
docker-logs:
	@$(RUN_WITH_GIT_BASH) ./scripts/docker.sh logs

# View Docker development logs
docker-logs-frontend:
	@$(RUN_WITH_GIT_BASH) ./scripts/docker.sh logs --frontend
docker-logs-gateway:
	@$(RUN_WITH_GIT_BASH) ./scripts/docker.sh logs --gateway

# ==========================================
# Production Docker Commands
# ==========================================

# Build and start production services
up:
	@$(RUN_WITH_GIT_BASH) ./scripts/deploy.sh

# Stop and remove production containers
down:
	@$(RUN_WITH_GIT_BASH) ./scripts/deploy.sh down

# E6（2026-10-05 审计 §6-8）：定期依赖升级节点——uv lock --upgrade 刷新 floor 卡版，
# 随后必须跑后端全量回归（make test）+ frontend pnpm check；升级失败/回归红即回退 lock。
deps-upgrade:
	@cd backend && uv lock --upgrade
	@echo ">>> lock 已升级。现在执行：cd backend && make test && cd ../frontend && pnpm check"
	@echo ">>> 任何红 → cd backend && git checkout uv.lock 回退（frontend 同理 pnpm-lock.yaml）"
