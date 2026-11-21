# 个人「我的用量」优化

> **TL;DR**：普通用户「我的用量」已重构为六类能力、7/14/30 天、跨分页总账、任务三态与可展开模型明细；新增只读 `/api/usage`，复用现有计价且不改表、不改采集；执行基准从 `b2378833` 比较后 re-pin 至 `8c78dace`，不涉及 `config_version`。

## 变更摘要

- 后端新增独立个人用量聚合包，按 owned thread / task 隔离用户，Agent 当前状态取全时间最新有效 run，消耗严格限本地自然日窗口；视频直接查询 `TaskRow`，不使用默认 100 条列表接口充当全量聚合。
- LLM Token 统一由输入 + 输出派生；逐 bucket 合并供应商回传、价目估算与 mixed 来源；缓存缺失、无模型拆分、无价目、真实 0、媒体未核算及混币均按计划保留语义。
- 新增 `GET /api/usage`，无 `user_id` 参数，管理员调用也仅看本人；原 `/api/console/*` 和管理员运营台逻辑未改。
- 前端新增角色边界、用户隔离 query、canonical 原任务链接、两张总览卡、六类筛选、三态任务、键盘展开、模型表格与 20 条分页；普通用户不再挂载旧 ConsolePage。
- 新增专用 auth-enabled Playwright lane（3127/3137）和固定普通 SSR 测试身份；默认 E2E 套件不会收集 `.e2e.ts`。

## 漂移与执行边界

- 任务 0 先比较 `b2378833..main`：漂移来自模型模式阶梯批次；三份 i18n 有增量，但 `courseItems` 六键及本批数据模型、计价、角色、视频路由和 E2E 基建契约未变，随后 re-pin 到 main `8c78dace`。
- 全部实现位于 `personal-usage-optimization` 分支的独立 worktree；未修改数据库 schema/迁移、运行采集链、provider/middleware、价格算法、管理员 console 组件或 registry 生成物。
- 未新增生产依赖，未调用 create 接口、真实付费 API 或开发/用户数据库，未部署。

## 已知数据边界

- 仅披露已核算部分：Agent 媒体只接受 `external_api_calls.task_id` 可关联且属于本人的行；无归因、已归因未计价、免费或失败等不可计价情况统一返回 `media_cost=null`，UI 显示“媒体费用未核算”。
- 无 task 上下文、以 domain 字符串回落到 `thread_id` 的 `llm_bookkeeping` 行不纳入个人任务；本页不宣称覆盖全部 LLM 调用。
- 历史估算按当前价目重算，不建设价格快照；ViMax amount/usage 透传与 Doubao 字幕版 TTS 字段缺口仍由 findings backlog 跟踪，本批未修采集链。

## 验收记录

- `cd backend && PYTHONPATH=. uv run pytest tests/test_personal_usage.py tests/test_retired_agent_alias.py -q` → `57 passed`。
- `cd backend && PYTHONPATH=. uv run pytest tests/test_personal_usage_agent.py tests/test_personal_usage.py tests/test_retired_agent_alias.py -q` → `64 passed`。
- `cd backend && PYTHONPATH=. uv run pytest tests/test_personal_usage.py tests/test_personal_usage_agent.py tests/test_personal_usage_video.py tests/test_retired_agent_alias.py -q` → `69 passed`。
- `cd backend && PYTHONPATH=. uv run pytest tests/test_personal_usage.py tests/test_personal_usage_agent.py tests/test_personal_usage_video.py tests/test_personal_usage_router.py tests/test_retired_agent_alias.py tests/test_console_router.py -q` → `161 passed`。
- 前端任务 5/6 定向 Vitest（usage、角色边界、console role transition/query scope）→ `9 files passed / 25 tests passed`；`pnpm exec tsc --noEmit` → exit 0。
- 反向验证：临时令 `ConsoleRolePage` 对 regular 也返回旧页面，运行专用 E2E → `personal-usage-page` locator 失败且捕获 `17` 次旧 console 请求；随后立即恢复角色边界。
- 整分支独立 review 后修复 zero-token/amount-only 模型完整性与小额正费用显示：两个后端回归及一个前端金额断言均 RED→GREEN；个人用量/console 定向回归 → backend `163 passed`、frontend `26 passed`。
- PR #38 OCR 三项 medium 修复：异常 bucket 不再触发 500、最新状态改为 `ROW_NUMBER()` 每线程仅返回一行、缺失有效用户上下文返回 401；三个回归均 RED→GREEN，定向 backend `166 passed`。NULL run 纳入和固定 20 条 query key 按计划契约在线程中说明，不改。
- 第二轮 OCR 可维护性项：query key 改用类型固定为 20 的 `options.limit`，页面共享 `PERSONAL_USAGE_PAGE_SIZE`；SSR gateway 与 E2E 共用 JSON 普通用户夹具；测试 task key 三元展开。`pnpm check`、`1805 tests` 与专用 E2E `2 passed` 全过。
- 第三轮 OCR：`isPlaceholderData` 期间保留旧数据但显示“正在加载用量”状态，新增 DOM 回归 RED→GREEN；E2E 日期/任务夹具消除嵌套三元。最终 frontend `279 files / 1806 tests`、专用 E2E `2 passed`。固定 20 条与六类路由两条建议按计划契约回复，不改。
- `cd backend && env -u RUN_LIVE uv run pytest tests/ -q`（review/OCR fix 后最终复跑）→ `10273 passed, 95 skipped in 391.17s`。
- `cd frontend && pnpm check && pnpm test` → 静态检查 exit 0（ESLint `0 errors`，121 条存量 warnings）；`279 files passed / 1806 tests passed`。
- `cd frontend && CI=1 E2E_PORT=3107 pnpm test:e2e` → `119 passed, 3 skipped`。
- `cd frontend && CI=1 E2E_PORT=3127 E2E_GATEWAY_PORT=3137 pnpm exec playwright test --config playwright.personal-usage.config.ts` → `2 passed`，覆盖 SSR regular、周期/筛选对账、双模型、21 任务分页、键盘、未知媒体及 320/390px 溢出。
- 隔离临时 SQLite + `make_authed_test_app` 真实 `/api/usage` 验收 → `days=7 tasks=6 cost=2.2 tokens=330`；`days=14 tasks=8 summary-unchanged`；六类齐全；最新状态、NULL run、污染 run 排除、派生 Token、null cache、无模型明细、真实 CNY 和未计价媒体断言全过；非法 days 返回 422。
- 根目录 `make check` → exit 0；Ruff、ESLint/TypeScript/Prettier、knip、i18n（659 个叶子全活）与 docs convention 全过。
- `python3 scripts/check_docs_convention.py` → `OK 仓库治理约定全过`；`git diff --check` → exit 0。

## 未验证项

- 未运行 `RUN_LIVE=1` 付费测试，未在真实部署或真实用户数据库上验收；专用前端 E2E 使用 Playwright API mock，真实 API 行为验收使用一次性临时 SQLite。
- PR/OCR 与远端 CI 结果将在交付阶段记录。

## 配置与回滚

- `config_version`：**不涉及**；无 schema、依赖或部署变更。
- 前后端需同批回滚；回滚后 `/workspace/console` 恢复旧 ConsolePage 对所有角色渲染，`/api/usage` 随本批路由一并移除。
