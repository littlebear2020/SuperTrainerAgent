# 单人多 Agent 研发流程优化 Implementation Plan

> **状态**：未执行（实施中）；定稿日 2026-11-22；基准 `0e84b148`；工作区分支 `multi-agent-flow-guardrails`。
> **TL;DR**：新增会话基线与不可变发布工具，改造部署前检查以阻断 `release-blocker`，并补齐研发基座 CI 路径；保留单负责人和现有 PR + OCR，不部署、不跑付费测试。
> **For agentic workers:** REQUIRED SUB-SKILL: 使用 `executing-plans` 在本工作区逐任务实施；每项先 RED 后 GREEN，不得修改其他 worktree 或未提交文件。

**Goal：**让多个 Agent 的验收基线可追溯、发布内容可复现、未验证例外在正式发布前必然显形。

**Architecture：**两个标准库 Python CLI 分别生成会话上下文和 Git archive 发布包；现有 pre-deploy 与包装脚本复用同一 ref 语义。pytest 在临时 Git 仓库验证真实行为，CI 只对相关研发基座增加后端回归，不引入依赖。

**Tech Stack：**Python 3.12 标准库、Git、tarfile、pytest、Bash、GitHub Actions。

**Spec：**`docs/2026-11-22-multi-agent-development-flow-design.md`。

## Global Constraints

- 不自动部署、不触发付费 API、不读取或提交真实 `.env` / `config.yaml` / 账号 / 主机信息。
- 不修改其他 worktree；当前工作区提交只添加本计划列出的路径。
- 默认发布 ref = `origin/main`；未合入 ref 只能由测试包装脚本显式允许。
- 其他 Agent 的 worktree/PR 是提示，未勾选 `[release-blocker]` 是正式发布阻断。
- 新测试只放 `backend/tests/`；不新增生产依赖。
- 长测试按根 AGENTS.md 后台执行，运行期间冻结输入。

## 文件地图

- `scripts/agent_context.py`：只读 Git 会话基线与计划哈希。
- `scripts/release_archive.py`：从提交流式生成带 manifest 的 tar.gz。
- `scripts/pre_deploy_check.py`：发布 ref 与 release-blocker 阻断，其他在途工作提示。
- `deploy_on_server.sh`、`deploy_to_test_server.sh`：保留入口，调用不可变打包。
- `backend/tests/test_agent_context.py`、`test_release_archive.py`、`test_pre_deploy_check.py`：真实临时仓库行为测试。
- `.github/workflows/backend-unit-tests.yml`、`quick-checks.yml`：研发基座路径覆盖。
- `.github/pull_request_template.md`、`frontend/docs/LIVE_E2E_RUNBOOK.md`、`AGENTS.md`：多 Agent 操作契约。
- `docs/findings-backlog.md`：已有未验证核心行为登记为发布阻断。
- `docs/changelog/changelog-2026-11-22-multi-agent-development-flow.md`：最终验收证据。

## Review Focus

- 分支 SHA 不同但补丁已合入：不得误判为仍有独立补丁；Task 1 用 patch-equivalent 与真实分叉双例验证。
- 本地修改和未跟踪敏感占位文件：不得进入发布包；Task 2 用 A/B 版本探针验证。
- 配置符号链接或 `..` 路径：只允许仓库内配置，失败不得留下半包；Task 2 覆盖。
- 其他 Agent 在途工作与未验证例外的语义不能混淆：前者提示、后者阻断；Task 3 覆盖。
- 本地 HEAD 与服务器实际版本不同：报告必须分别记录，不得相互冒充；Task 4 检查 runbook 与 PR 模板。

---

### Task 1：会话基线工具

**Files：**
- Create：`scripts/agent_context.py`
- Test：`backend/tests/test_agent_context.py`

**Interfaces：**
- `build_context(root: Path, base_ref: str, spec: str | None) -> dict`
- CLI：`python3 scripts/agent_context.py [--base origin/main] [--spec PATH] [--require-clean] [--require-head] [--expect-spec-sha256 HEX] [--json]`

- [x] **Step 1：写真实临时仓库失败测试。** 覆盖完整 SHA、ahead/behind、脏路径、worktree 列表、计划 blob/sha256；构造一个 cherry-pick 等价分支和一个真实分叉分支；分别断言 `patch_equivalent_to_base=True/False`。
- [x] **Step 2：运行 pytest 确认 RED。** 命令：`cd backend && uv run pytest tests/test_agent_context.py -q`；预期因脚本不存在而失败。
- [x] **Step 3：实现只读上下文与失败语义。** 所有 Git 失败非零；`--require-clean` 拒绝任何 tracked/untracked 变更，`--require-head` 拒绝 HEAD 偏离 base，计划缺失和预期哈希不符非零；默认只报告，不限制开发中的脏树。
- [x] **Step 4：重跑测试确认 GREEN，并运行 CLI 探针。** 断言 JSON 可解析、人可读模式含 HEAD/base/spec 关键行。
- [x] **Step 5：提交。** `feat(agents): 增加会话基线与计划哈希护栏`

### Task 2：不可变发布包与包装脚本

**Files：**
- Create：`scripts/release_archive.py`
- Modify：`deploy_on_server.sh`、`deploy_to_test_server.sh`
- Test：`backend/tests/test_release_archive.py`

**Interfaces：**
- `build_archive(root: Path, ref: str, output: Path, *, base_ref: str = "origin/main", allow_unmerged: bool = False, include_local_config: bool = False, excludes: Sequence[str] = ()) -> dict`
- CLI 同名参数加 `--output`、`--allow-unmerged`、`--include-local-config`、可重复 `--exclude`。
- manifest：`source_sha/requested_ref/base_ref/base_sha/excludes/includes_local_config/config_files`。

- [x] **Step 1：写发布内容失败测试。** 临时仓库提交内容 A 后改成 B，并添加未跟踪敏感占位文件；打包后断言文件内容为 A，B 与占位文件均不存在，manifest 指向完整提交 SHA。
- [x] **Step 2：补参数和原子性失败测试。** 覆盖未合入 ref 默认拒绝、显式允许、exclude 前缀、缺必需配置、仓库内配置符号链接、越界配置、输出已存在时失败不替换。
- [x] **Step 3：运行 pytest 确认 RED。** 命令：`cd backend && uv run pytest tests/test_release_archive.py -q`。
- [x] **Step 4：实现流式 git archive、manifest 与原子输出。** 普通文件只来自 Git；本地配置仅在显式选项下追加；所有路径归一化，失败清理临时文件。
- [x] **Step 5：改造两个包装脚本。** 保持 `pack/upload/deploy` 和产物名；默认 ref=`origin/main`，测试可用 `TEST_DEPLOY_REF` + `TEST_ALLOW_UNMERGED=1`；`--no-sandbox` 只排除 chrome-linux；正式 pack 先跑 pre-deploy；本地配置只经显式环境开关追加；部署后显示 manifest SHA。
- [x] **Step 6：重跑测试并做本仓真实打包探针。** 断言 tar 包含 `release-manifest.json` 且 SHA 等于指定提交，不包含当前未跟踪文件；`--no-sandbox` 探针保留 wheels/BPE/fonts。
- [x] **Step 7：提交。** `feat(release): 从已审提交生成不可变发布包`

### Task 3：部署前风险闭环

**Files：**
- Modify：`scripts/pre_deploy_check.py`
- Modify：`docs/findings-backlog.md`
- Test：`backend/tests/test_pre_deploy_check.py`

**Interfaces：**
- `find_release_blockers(root: Path) -> list[str]`
- CLI 增加 `--ref REF`；保留原无参数入口。

- [x] **Step 1：写失败测试。** 样例 backlog 未勾选 `[release-blocker]` 返回 1，勾选后返回 0；模拟开放 PR、其他 worktree、main ahead/behind 均只进入 notes，不再阻断；ref 解析失败和未合入 ref 阻断。
- [x] **Step 2：运行 pytest 确认 RED。** 命令：`cd backend && uv run pytest tests/test_pre_deploy_check.py -q`。
- [x] **Step 3：重构 pre-deploy 判定。** 正式发布 fetch 失败阻断；校验 ref 为 `origin/main` 祖先；gh 调用从 origin URL 设置 `GH_REPO`；保留 changelog/config_version 提示。
- [x] **Step 4：登记现有例外。** 将 PPT 最终 SOUL 行为未复测、沙箱完整基座重建未执行分别登记为 `[release-blocker]`，指针到现有 changelog；不宣称它们已修复。
- [x] **Step 5：重跑测试与真实 CLI 探针。** 确认当前仓库因两条例外阻断，其他 worktree 只显示提示。
- [x] **Step 6：提交。** `feat(release): 将未验证例外纳入部署阻断`

### Task 4：CI、协作契约与交付

**Files：**
- Modify：`.github/workflows/backend-unit-tests.yml`、`.github/workflows/quick-checks.yml`
- Create：`.github/pull_request_template.md`
- Modify：`frontend/docs/LIVE_E2E_RUNBOOK.md`、`AGENTS.md`
- Modify：本计划
- Create：`docs/changelog/changelog-2026-11-22-multi-agent-development-flow.md`

- [x] **Step 1：先补静态失败断言。** 在 `test_release_archive.py` / `test_pre_deploy_check.py` 检查 CI 路径、包装脚本 pre-deploy 入口、PR 模板三项 SHA、runbook 的本地期望与部署 SHA 分离。
- [x] **Step 2：运行定向测试确认 RED，再实现 CI 与文档。** backend 测试覆盖 `skills/**`、`docker/**`、根 config、三件流程脚本和两件包装脚本；quick-checks 使用同一路径集合；文档写入机械命令与活跃任务≤3、已合入 worktree 即清理的口径。
- [x] **Step 3：运行相关测试和格式检查。** `cd backend && uv run pytest tests/test_agent_context.py tests/test_release_archive.py tests/test_pre_deploy_check.py -q`、`git diff --check`、`python3 scripts/check_docs_convention.py`。
- [ ] **Step 4：运行交付门禁。** `cd backend && make test`、根 `make check`；记录真实关键输出，不运行部署或 live 付费测试。
- [ ] **Step 5：更新计划勾选与 changelog。** 包含设计决策、发布兼容变化、两条现存 release-blocker、验收记录和未验证边界。
- [ ] **Step 6：自审 diff 后按核心路径流程交付。** 核对远端 main，必要时 rebase 并重跑相关门禁；push、PR + OCR，CI 无红且无 Critical/Important 后才 rebase merge。
