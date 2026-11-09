# Copilot Onboarding Instructions for TrainerAI 2.0

**约定以根 `AGENTS.md` 为准**（完成定义 / 文档治理 / 边界与禁区 / Git 约定）；碰 `backend/`、`frontend/` 时各自目录的 `AGENTS.md` 同样生效。本文件只保留 Copilot 入口的操作验证笔记，与 AGENTS.md 冲突时以 AGENTS.md 为准。

## 最高频规则（速览）

- backend：`cd backend && make lint && make test`（CI 同口径）
- frontend：`cd frontend && pnpm check`（eslint + tsc，提交前必跑）
- docs：新建/移动文档后 `python3 scripts/check_docs_convention.py`；plan → `docs/plans/`，changelog → `docs/changelog/`（命名规则见 AGENTS.md「文档治理」）
- 禁区（vendored 目录 / registry 生成物 / 机密文件等）：见 AGENTS.md「边界与禁区」
- 名册类信息（agent / 技能 / 模型 / 通道）以代码为准：`backend/seed/agents/`、`skills/`、`config.yaml`

## 仓库速览

- 产品：AI 课程内容智能生产平台，基于 DeerFlow 2.0 多 Agent 框架（能力概览见 `README.md`）
- Backend：Python 3.12 / FastAPI gateway / LangGraph agent runtime（in-process）/ SQLite
- Frontend：Next.js 16 + React 19 + TypeScript + pnpm 10
- 本地入口：根 `Makefile`；Docker：`make docker-*`

## 环境要求（macOS 实测）

Node.js ≥22、pnpm 10、Python ≥3.12、uv、nginx（`make dev` 统一入口需要）。

## 操作验证笔记（2026-10-06 逐条重验）

1. **Bootstrap**：`make check-env`（工具链检查）→ `make install`（先后端后前端）。
2. **本地运行**：`make dev` 起三个常驻服务——gateway :8001 / frontend :3000 / nginx :2026（统一入口 <http://localhost:2026>；有 ffmpeg + 本地 DB 时外加第 4 个：worker :8040）；`/api/langgraph/*` → gateway agent runtime，其余 `/api/*` → gateway REST。`make stop` 清理；中断后再跑一次确保干净。
   - 注：**不存在独立的 langgraph :2024 服务**（旧笔记已失真，2026-10-06 依 `scripts/serve.sh` 修正）；日志在 `logs/`（gateway / frontend / nginx / worker 等）。
3. **`make config` 非幂等**：`config.yaml` 已存在即 abort（设计如此，仅用于干净克隆的首次初始化）。
4. **proxy env 坑**：代理环境变量可能静默打断 `pnpm install` / 前端网络操作——前端装依赖失败先 unset 代理变量重试。
5. **BETTER_AUTH_SECRET 已退役**（D-10）：全仓零代码消费，`pnpm build` 无需设置。
6. **CI parity（PR 强制）**：`quick-checks.yml`（secret scan + docs convention + backend make lint）、`backend-unit-tests.yml`（uv sync --group dev + make test）、`lint-check.yml`（frontend `make check`），另有 E2E 与依赖扫描。开 PR 前至少跑过 backend lint+test 与 frontend `pnpm check`。

## 高价值路径速查

- 编排/配置：`Makefile`、`config.yaml`（本地活跃配置；新增配置项口径见 `config.example.yaml`）、`extensions_config.json`（MCP 配置；模板 `extensions_config.example.json`）
- Backend：`backend/app/gateway/`（FastAPI 应用层）、`backend/packages/harness/deerflow/`（agent 框架：agents / sandbox / subagents / mcp）、`backend/tests/`（测试一律放这里）
- Frontend：`frontend/src/app|components|core`、`frontend/src/env.js`（env schema）、`frontend/tests/`（unit 镜像 src；e2e 在 tests/e2e）
- 技能库：`skills/public/` + `skills/custom/`（各技能以 SKILL.md 定义）

## 本文件与代码冲突时

以代码与 AGENTS.md 为准；发现本文件失真请顺手修正（名册防腐约定见 AGENTS.md「文档治理」）。
