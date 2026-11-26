# 用量删除快照、GLM 5.4 接入、To-dos 运行中提醒与 Ultra 场景化委派

> **状态**：执行中（用量快照）。计划：`docs/plans/2026-11-26-usage-ultra-todos-glm-remediation-plan.md`；执行基线 commit `2d89bae7`（计划入库）。
> **TL;DR**：四个串行 PR——① 删除作品/会话前写用量快照（新表 `usage_tombstones`），「我的用量」继续计入并标「作品已删除」；② 接入 360 `z-ai/glm-5.4`（标准 + Ultra 可选、非默认，¥8 / ¥28 / ¥2 每百万 token）；③ `TodoMiddleware` 在停滞 K 轮后注入一条不上屏的提醒（只提醒、不改状态）；④ Ultra 提示词改为「≥2 条相互独立的工作即并行委派」，run 元数据落 `run_mode`，文案删除「更长执行预算」。影响面 = backend 用量/删除/中间件/提示词/run 元数据 + 1 个 alembic 迁移 + 前端用量页与文案 + `config.example.yaml`；不部署。

## 执行基线与本机环境

- 计划与诊断报告入库提交 `2d89bae7`（纯 docs，直推 main）；PR 1 分支从当时的 `origin/main` 切出，执行 worktree 在仓库外同级目录。
- 本机 gitignored `config.yaml` 由 v32 对齐到 v33（只改版本号一行，v32 原件留在证据目录）；示例 v33（`88c1485a`）相对 v32 只改了版本号与 TTS 注释，本机没有 `voice_twin` 段，无键可迁。
- 基线全量测试首跑 19 个失败，全部是本机环境而非代码：18 个因进程里没有 `QIHU_API_KEY`（配置里 `$QIHU_API_KEY` 展开失败），1 个（`test_bash_secret_masking`）要求本机 config 保留 P1-S2「回退杆」注释块。测试命令内补占位密钥、本机 config 补回该注释块（仅注释，`sandbox.environment` 仍为空）后 `cd backend && make test` → `10476 passed, 30 skipped`。占位密钥只进测试进程，live 用例仍按 `RUN_LIVE` 跳过。
- 本机 config 版本断言（`test_360_new_models_roster.py`）要求「本机 == 示例 == 32」而示例已是 33，本机有 config 时恒红（CI 没有 config，跳过），随 PR 1 单独提交对齐到 33。

## PR 1：删除快照保住历史用量（BUG-01）

- **新表 `usage_tombstones`**（迁移 `q0d1e2f3a4b5`，接在 `p9c0d1e2f3a4` 之后，回滚 = `alembic downgrade p9c0d1e2f3a4`）：主键 `(kind, subject_id)`，只记类型、标题、状态、创建时间与删除时间；费用与 Token 不落快照，仍从账本现算。
- **删除前先写快照，写失败就拒绝删除：** `BaseTaskService.delete_task`（所有任务域共用，只有 `ai_image` / `ai_video` 写快照）在取消任务与清理目录之前写；`delete_thread_data` 的第一条语句写。普通对话、`user_id` 为空的线程不写（本来不进任何人的用量）；对同一线程重复 DELETE（`useDeleteThread` 会连发两次）不覆盖首个快照；删除时仍在进行的条目记为「未完成」，之后不再更新。
- **聚合 = 现存对象 ∪ 没有现存行的快照：** 同一 id 现存行优先、不重复计；快照对象只有窗口内确有账本行才列出；删除后才落库的晚到账本照常计入；`summary` 与 `total_tasks` 仍在分页前对全量计算。Agent 会话的能力归属抽成 `resolve_agent_capability`，聚合与快照共用同一口径。
- **接口与前端：** `UsageTaskResponse.deleted`；用量明细对已删除条目标「作品已删除」、标题缺省显示「已删除的作品」、隐藏「打开原任务」（旧后端不带该字段时按未删除处理）。
- **执行中的小处置：** 任务快照的标题截断到 256 字符（`unified_tasks.title` 是 500 字符列，Postgres 下超长标题会让写快照失败、进而拒绝删除）；其余与计划一致。
- **只对上线后的删除生效，不回填。** 修复前已删除的作品没有可靠的类型来源，保持原样；窗口内没有任何账本行的作品删除后会从列表消失，`total_tasks` 随之减少。

## PR 2：接入 360 GLM 5.4（FEAT-01）

待执行。

## PR 3：To-dos 停滞时提醒模型（BUG-03）

待执行。

## PR 4：Ultra 场景化委派与 run 执行模式（BUG-02）

待执行。

## 证据摘要

- **测试服只读取证（Q6）：`SERVER_EVIDENCE_NOT_VERIFIED`。** 执行环境的权限分类器拒绝了在测试服上运行只读统计脚本（`ssh` + 脚本经 stdin 执行），按计划「服务器不可达或样本不足」分支处理：K = 6，PR 3 照常实施；提醒只注入文字、不改任何状态。没有更换通道重试。可在用户放行后补跑，若 `batch_share < 20%` 则按计划改拆 pending。
- Ultra 基线计数（按 Agent 分组的 `task` 调用 run 数）同样未取得，仅作背景信息，不影响达标判定。

## 验收记录

按 PR 逐行补充，每行 = 命令 → 实际关键输出。

- `cd backend && PYTHONPATH=. uv run pytest tests/test_model_modes_config.py tests/test_360_new_models_roster.py tests/test_config_example_parity.py -q` → `39 passed`（本机 config 对齐 v33 后）
- `cd backend && make test`（基线，补占位密钥与本机 config 注释块后）→ `10476 passed, 30 skipped, 0 failed`
- PR 1 先红后绿：`pytest tests/test_personal_usage_deletion.py tests/test_usage_tombstones_migration.py -q` 首跑 → `ModuleNotFoundError: No module named 'app.gateway._shared.personal_usage.tombstones'`；单跑迁移用例 → `2 failed`（`'usage_tombstones' in {...}` 不成立）；写完统计口径用例后、实现聚合前 → `6 failed, 8 passed`
- PR 1 定向：`pytest tests/test_personal_usage_deletion.py tests/test_usage_tombstones_migration.py -q` → 全部通过（快照写入与迁移 9 例，统计口径 7 例）
- PR 1 回归：`pytest tests/test_task_framework*.py tests/test_thread_delete_event_store.py tests/test_content_tasks_delete.py tests/test_voice_twin_production.py tests/test_persistence_bootstrap.py tests/test_persistence_bootstrap_legacy_columns.py tests/test_harness_boundary.py tests/test_personal_usage_agent.py -q` → `243 passed`；`pytest tests/test_personal_usage_deletion.py tests/test_personal_usage.py tests/test_personal_usage_agent.py tests/test_personal_usage_router.py tests/test_personal_usage_video.py tests/test_core_media_usage_e2e.py -q` → `124 passed`
- PR 1 前端：`pnpm exec vitest run tests/unit/components/workspace/usage/usage-task-list.dom.test.tsx` → 先 `1 failed | 5 passed`，实现后 `6 passed`
- PR 1 全量门禁：`make check` → exit 0（eslint 0 errors、tsc、prettier、knip、`i18n key 存活检测通过：711 个叶子全活`、`OK 仓库治理约定全过`）；`cd backend && make test` → `10492 passed, 30 skipped`；`cd frontend && pnpm test` → `Test Files 306 passed, Tests 2171 passed`

## 未验证清单

- 测试服只读取证：`SERVER_EVIDENCE_NOT_VERIFIED`（见上）。
- 部署侧各项（gateway/前端发布、`usage_tombstones` 迁移上线、服务器 config 与价目录入、部署后行为验收）：未部署，需用户另行下达指令。
