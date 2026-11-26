# 用量删除快照、GLM 5.4 接入、To-dos 运行中提醒与 Ultra 场景化委派

> **状态**：执行中（用量快照）。计划：`docs/plans/2026-11-26-usage-ultra-todos-glm-remediation-plan.md`；执行基线 commit `2d89bae7`（计划入库）。
> **TL;DR**：四个串行 PR——① 删除作品/会话前写用量快照（新表 `usage_tombstones`），「我的用量」继续计入并标「作品已删除」；② 接入 360 `z-ai/glm-5.4`（标准 + Ultra 可选、非默认，¥8 / ¥28 / ¥2 每百万 token）；③ `TodoMiddleware` 在停滞 K 轮后注入一条不上屏的提醒（只提醒、不改状态）；④ Ultra 提示词改为「≥2 条相互独立的工作即并行委派」，run 元数据落 `run_mode`，文案删除「更长执行预算」。影响面 = backend 用量/删除/中间件/提示词/run 元数据 + 1 个 alembic 迁移 + 前端用量页与文案 + `config.example.yaml`；不部署。

## 执行基线与本机环境

- 计划与诊断报告入库提交 `2d89bae7`（纯 docs，直推 main）；PR 1 分支从当时的 `origin/main` 切出，执行 worktree 在仓库外同级目录。
- 本机 gitignored `config.yaml` 由 v32 对齐到 v33（只改版本号一行，v32 原件留在证据目录）；示例 v33（`88c1485a`）相对 v32 只改了版本号与 TTS 注释，本机没有 `voice_twin` 段，无键可迁。
- 基线全量测试首跑 19 个失败，全部是本机环境而非代码：18 个因进程里没有 `QIHU_API_KEY`（配置里 `$QIHU_API_KEY` 展开失败），1 个（`test_bash_secret_masking`）要求本机 config 保留 P1-S2「回退杆」注释块。测试命令内补占位密钥、本机 config 补回该注释块（仅注释，`sandbox.environment` 仍为空）后 `cd backend && make test` → `10476 passed, 30 skipped`。占位密钥只进测试进程，live 用例仍按 `RUN_LIVE` 跳过。
- 本机 config 版本断言（`test_360_new_models_roster.py`）要求「本机 == 示例 == 32」而示例已是 33，本机有 config 时恒红（CI 没有 config，跳过），随 PR 1 单独提交对齐到 33。

## PR 1：删除快照保住历史用量（BUG-01）

待执行。

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

待各 PR 合入后逐行补充（命令 → 实际关键输出）。

- `cd backend && PYTHONPATH=. uv run pytest tests/test_model_modes_config.py tests/test_360_new_models_roster.py tests/test_config_example_parity.py -q` → `39 passed`（本机 config 对齐 v33 后）
- `cd backend && make test`（基线，补占位密钥与本机 config 注释块后）→ `10476 passed, 30 skipped, 0 failed`

## 未验证清单

- 测试服只读取证：`SERVER_EVIDENCE_NOT_VERIFIED`（见上）。
- 部署侧各项（gateway/前端发布、`usage_tombstones` 迁移上线、服务器 config 与价目录入、部署后行为验收）：未部署，需用户另行下达指令。
