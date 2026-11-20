# PPT 美化 flat 首次读取顺序修复记录

> **状态**：静态修复与免费验证已完成；**行为级验证未执行**——付费 P01 复测冻结（待用户授权并提供配置），首次读取缺口不宣称已关闭。
> **TL;DR**：#29 的 P01 虽通过，但首次 `execution-context` 之前 call 31/35 仍完整读取了 `executor-base.md` 与 `shared-standards-core.md`；本批只改指令文案，把 flat 执行包规则升为 Step 6 首条阻塞规则（含 SKILL.md role-switch 读取在内，执行包读完前禁止完整读核心/Design Spec/lock），`resume-execute.md` 拆 flat/non-flat 分支；关键决策 = 不加代码门/中间件，先收口指令顺序并以静态断言钉住（成因为推断，生效须付费复测证实）；影响面 = `skills/public/ppt-master/` 4 个文档 + `SEED_VERSION=2026-11-20.1`（部署后触发 6 个 agent reseed），无 schema/config 改动。

## 1. 背景与改动范围

- 背景：[#29 changelog](changelog-2026-11-19-ppt-beautify-executor-context-pack.md) §4——P01 PASS，但 call 31/35 在首次 `execution-context`（call 43）前完整读取两个核心文件，严格“首次也无全量”未满足。
- **成因（推断，未经付费复测证实）**：① Step 6 里旧“完整参考批次”排在 flat 规则之前，读起来像默认路径；② P22 句子写 `before the required reads`，预设读取已发生；③ `SKILL.md:190` 的通用规则“Before switching roles, read the corresponding role reference”会在进入 Step 6 前把 `executor-base.md` 拉进来。
- `workflows/generate-pptx.md`：flat 规则升为 Step 6 第一条阻塞规则并新增 **Blocking order**——执行包生成并读完之前，禁止完整读取 `executor-base.md`、`shared-standards-core.md`（含 SKILL.md role-switch 读取）、完整 Design Spec、完整 lock；P22 `before the required reads and first SVG` → `before the first SVG`；旧批次标 `(non-flat only)`。
- `workflows/stages/resume-execute.md`：Step 1 的完整读取限定 non-flat；Step 2 拆 Flat branch（P31）/ Non-flat branch；依赖表补 `(flat: from the pack and those bounded fragments)`。
- `workflows/governance/failure-recovery.md`：flat 行加 `before any core reference`。
- `TRAINERAI_PATCHES.md`：P31 补 `resume-execute.md`、读取顺序约束，并写明“行为尚未验证，须付费复测”。
- `backend/app/gateway/seed.py`：`SEED_VERSION` `2026-11-19.1` → `2026-11-20.1`。
- 测试：`test_ppt_master_execution_context.py` 新增 7 个用例、加强 1 个（`non_flat_workflow_keeps_legacy_full_reference_recovery`）；`test_seed_agents.py` 版本钉。
- 未改动：`execution_context.py`/CLI、summarization、SOUL/System Prompt、中间件、模型配置、非 flat 路线行为、PR B。
- 部署注意：版本邮票不符，部署后首次启动 `seed_agents()` 对 6 个 agent 各 reseed 一次（旧目录改名 `<agent>.bak.<ts>`，`memory.json` 保留）——既有 SEED_VERSION 机制，无需额外操作。

## 2. 未验证与残余风险

- 静态断言只证明文案顺序与禁令存在，**不证明 qwen 会遵守**；因此本批**不宣称**首次读取缺口已关闭。关闭条件 = 付费 P01 复测中首次 `execution-context` 之前无完整核心读取（沿用 #29 的 checkpoint 审计口径）。
- 残余风险：`SKILL.md:190` 的通用 role-switch 规则本批未改，仅在 flat 阻塞规则里点名覆盖；若复测仍首读，首选下一步是在 SKILL.md 侧加 flat 豁免（先评估其预算，见 §3）。
- 行为级断言本批缺席，属“生效验证（付费复测）”延后项，依据完成定义对重型/付费探针的延后口径。
- plan 状态头不改：首次读取缺口仍未经复测关闭，原状态头依然准确。

## 3. 本批发现、未修改的遗留

- **prompt_audit 预算失守且无人执行**（已登记 `docs/findings-backlog.md`）：manifest 声明 `failure-recovery.md` 预算 2750；o200k 实测（审计工具与直接计数交叉一致）origin/main=2799（基线已超）→ #27 HEAD `95c8a12b`=2908 → 本批=2912（本批 +4）。同一审计另报 `SKILL.md` 4073>2500、`topic-research.md` 2100>2000，均为存量、本批前后不变。审计手册自述不接 CI/pre-commit；全仓 grep 仅技能目录内自引用，无任何测试/CI 执行它；本仓布局下直接运行报 `AUDIT_SETUP_ERROR`（manifest 期望 `AGENTS.md` + `skills/ppt-master/` 的上游布局，需 `--root` 指向 scratch 副本）。scratch 副本还报 `REFERENCE_MISSING`×4、`AUTHORITY_EDGE_UNREFERENCED`×2、`LOAD_COVERAGE_GAP`×1，未分诊（可能含 scratch 布局伪影）。

## 验收记录

- `cd backend && PYTHONPATH=. uv run pytest tests/test_ppt_master_execution_context.py -q`（RED：scratch 副本，技能文档取 `git archive 95c8a12b`、仅放新测试）→ `8 failed, 64 passed`（7 个新增 + 加强后的 `test_non_flat_workflow_keeps_legacy_full_reference_recovery`）。
- 同一命令（GREEN：scratch 内叠加 4 个修复文档）→ `72 passed`。
- `PYTHONPATH=. uv run pytest tests/test_ppt_master_execution_context.py tests/test_seed_agents.py -q` → `93 passed`。
- `PYTHONPATH=. uv run pytest tests/test_ppt_master*.py tests/test_seed_*.py tests/test_sandbox_read_file_range.py tests/test_skillscan_native.py -q` → `373 passed in 11.00s`。
- `cd backend && make test` → `10151 passed, 89 skipped, 436 warnings in 103.16s`，EXIT=0（#29 收尾轮为 `10144 passed`，差值 = 本批新增 7 个）。
- 根 `make check`（工作树含 7 个修复文件时运行）→ EXIT=0；`All checks passed!`、`1318 files already formatted`、eslint `0 errors`（121 条 warning 为既有，本批未改 frontend）、`All matched files use Prettier code style!`、`i18n key 存活检测通过：630 个叶子全活`、docs convention `OK`。
- fresh home reseed（`DEER_FLOW_HOME=<临时目录> uv run python`，调用 `seed_agents()`）→ `seeded=6 SEED_VERSION=2026-11-20.1`，6 个 agent 邮票全等于 SEED_VERSION，二次调用返回 `0`；临时目录已删除。
- prompt_audit（scratch 上游布局，`--root`）→ `BUDGET_FILE failure-recovery.md: File has 2912 tokens; budget is 2750`；直接 o200k 计数 origin/main=2799、95c8a12b=2908、工作树=2912。
- 行为级验证 → **未执行**（付费 P01 复测冻结，见 §2）。
- `python3 scripts/check_docs_convention.py`（含本 changelog 与 backlog 新增行）→ `OK 仓库治理约定全过`，exit 0；`git diff --check` → 无输出，exit 0。

## 4. PR 与后续边界

- stacked PR：head=`ppt-master-first-read-order`，base=`ppt-beautify-summarization-context-integrity`（#27 的 head 分支，沿用 #29 的堆叠方式）；#27 分支本身未改动。
- CI/OCR 结果与合并状态以 PR 的 checks 与评论为准，不回填本文件（回填会再触发一轮评审）。
- 后续：付费 P01 复测需用户授权（沙箱 `tiktoken` 预装为独立批次）；复测前不宣称首次读取约束已满足；本批不部署、不合并 #27。
