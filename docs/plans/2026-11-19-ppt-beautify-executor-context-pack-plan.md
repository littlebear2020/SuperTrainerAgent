# PPT 美化 Page Execution Pack 实施计划

> **状态**：未执行（执行中：用户已批准修订 A，将执行包预算从 8000 调整为 14000 tokens；Task 1 恢复，Task 2–6 未执行；原型从 SDD 证据区恢复后重验；第三次付费 qwen 运行未授权）。
> **TL;DR**：扩展现有 `page-context`，用精确规则选择器、14000-token 硬门和输入哈希生成当前页执行包；先证明真实 v2 P01/P03 可在预算内完整投影，再改 flat Default/Beautify 的首次与压缩恢复路径。PR A、PR B、summarization、System Prompt 和其他 PPT 路线不变。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. 任务必须串行，禁止并行修改同一技能与共享测试。

**Goal：** 让 qwen 在同一冻结 7 页输入上通过确定性当前页执行包开始 authoring，自主写出非空 P01，同时保持摘要健康、最终确认和不重问，不再执行完整核心/全 roster 预读。

**Architecture：** 保留现有 `build_page_context()` 及默认诊断输出，新增独立 `execution_context.py` 负责精确原文选择器、当前页模块路由、token 计量、输入/内容哈希和原子写入。新 `execution-context` CLI 生成 `validation/execution-context/P<NN>.json`；只有 flat Default/Beautify 在首次 Step 6、翻页和 compacted recovery 中消费该包，其他路线继续原流程。

**Tech Stack：** Python 3.12、标准库 `dataclasses/json/hashlib/pathlib`、现有 `page_context.py` / `project_management.cli`、`tiktoken(o200k_base)`（现有可选依赖，执行模式强制可用）、pytest、Ruff、vendored ppt-master、既有 qwen 冒烟驱动。

**Spec：** `docs/2026-11-19-ppt-beautify-executor-context-pack-design.md`（书面设计已获用户确认）。

## Global Constraints

- 执行包 schema 固定为 `ppt-master.execution-context.v1`；CLI 固定为 `project_manager.py execution-context <project_path> P<NN>`。
- 总执行包使用 `o200k_base` 精确计量并不得超过 **14000 tokens**；`token_status` 必须为 `exact`，不可用或超限即失败，禁止静默截断、加预算或全量回退。
- 现有 `PAGE_CONTEXT_TOKEN_TARGET=2000`、`LOCK_PROJECTION_TOKEN_TARGET=1000` 保持为分项遥测，不能重复相加解释；执行包另须 `<414000 UTF-8 bytes`，低于当前 50000 字符读取上限。
- 默认 `page-context` 的调用、compact 输出和诊断语义保持兼容；新字段只进入独立执行包。
- 规则只能按唯一标题/段落锚点精确提取，不得用 LLM 摘要、人工另写精简规范或硬编码行号；缺失/重复锚点必须 fail closed。
- 首批仅启用 `pptx_structure.mode=flat` 的 Default/Beautify；Quick、structured/mirror、Create Template 等路线不变。flat 新路径遇未注册触发不得回退完整核心/全 roster。
- 执行包是派生视图，不是第二事实源；只能原子重建，禁止手改，禁止写入 `svg_output/`，不得直接生成 P01。
- 不修改 PR A summarization 实现/测试、System Prompt、SOUL、Dynamic Context、Skill Rescue、bash/read_file 中间件、trigger/trim/cap 或模型配置；PR B 不启动。
- vendored 补丁登记 **P31**；#28 的 P30 留给已失败的 prompt-only 方案。`SEED_VERSION=2026-11-19.1`，fresh home reseed 后验证运行时邮票。
- 行为验收固定同一输入 SHA-256=`16f7f0dbf06a559b8889333a0eae9a80349d39d92132efae7a369fe50ea9ff24`、7 页/P03 原生表格、qwen3.8-flash、PR A、token budget disabled、recursion limit 2400、fresh home/thread 和正常 Stage 1/2。
- 任何付费 qwen 复测均需用户单独明确授权；本计划不授权合并、部署、完整 7 页或 46 页验证。

## 评审决策记录（用户已按推荐确认）

| Frontier 问题 | 已确认决策 |
|---|---|
| 超过 14000 tokens | 停止 authoring，输出组成项 token 并修正选择器；不得自动加预算、全量回退或删规则，不进入付费运行。 |
| 必要规则无选择器/触发歧义 | flat 新路径 fail closed，列出 `rule_id/source`；补选择器和测试后才继续，禁止旧全量批次隐式兜底。 |
| 规则内容形态 | 只用原 references 的精确原文与来源锚点/SHA；不另写规范、不用 LLM 摘要；装不下就回到选择器修正。 |
| 执行包形态 | 原子写入 `validation/execution-context/P<NN>.json` 并校验哈希；不作为第二事实源，不直接写 P01。 |
| 路线与合并门 | 首批只覆盖 flat Default/Beautify；P01 未通过不合并。通过后再推进 PR 处置和 PR B，完整 7 页另验收。 |
| 修订 A（2026-11-19 用户批准） | 原 8000 探针实测 P01=12297、P03=12726 后，将预算调整为实测最大值约 110% 的 **14000 tokens**；选择器与精确规则内容不变，32000 trigger 不变，超限仍 fail closed。 |

## Review Focus

- **真实 v2 P01/P03 超预算或规则缺失**：合理结果是零付费阶段即停止，不修改运行工作流；由 Task 1 的真实产物探针覆盖。
- **选择器锚点缺失/重复或 references 标题漂移**：必须 fail closed 而非读错章节；由 Task 1 单元测试覆盖。
- **过期/手改执行包、写入中断、token 计量不可用**：不得被当作新鲜完整输入；由 Task 2 的哈希、原子写入和错误路径测试覆盖。
- **非 flat 路线或默认诊断调用**：不得被新执行模式改变；由 Task 1/2/3 的路线与兼容断言覆盖。
- **模型绕过执行包继续全量读取**：静态通过不算成功；由 Task 5 的真实 checkpoint 工具参数审计覆盖。
- **P01 通过但后续页面质量下降**：本批只把 P01 当入口，不宣称完整质量；完整 7 页保真/原生表格/质量门留在后续验收，由 Task 6 的边界断言和文档记录覆盖。

## 文件范围与串行边界

| 批次 | 允许路径（allow） | 禁改路径（ban） | 共享基座触达 |
|---|---|---|---|
| Page Execution Pack | `skills/public/ppt-master/scripts/project_management/{execution_context.py,cli.py,page_context.py}`（page_context 仅必要复用，默认输出不变）、`scripts/project_manager.py`、`scripts/docs/project.md`、`references/artifact-ownership.md`、`workflows/generate-pptx.md`、`workflows/governance/failure-recovery.md`、`TRAINERAI_PATCHES.md`；`backend/tests/{test_ppt_master_execution_context.py,test_ppt_master_skill_vendored.py,test_seed_agents.py}`；`backend/app/gateway/seed.py`；本计划与对应 changelog；`temp/` 忽略的驱动和证据 | summarization、System Prompt/SOUL、其他中间件、config、前端、docker、Quick/structured/mirror 工作流、核心 references 正文、PR B | 以 PR A HEAD `468a7761` 为只读祖先；所有任务串行，不与 #28 或其他技能批并行 |

真实 PPTX、SQLite/checkpoint、日志、生成的执行包/SVG/PPTX 与 `temp/` 证据禁止入库。正式 pytest 只用合成短夹具；真实 v2 项目仅作本机只读探针输入。

---

### Task 1：精确规则选择器与真实 P01/P03 探针

**Files：**
- Create: `skills/public/ppt-master/scripts/project_management/execution_context.py`
- Create: `backend/tests/test_ppt_master_execution_context.py`

**Interfaces：**
- `RuleSelector(rule_id: str, source_path: str, start_anchor: str, end_anchor: str | None = None)`（frozen dataclass）。
- `ExecutionContextError(RuntimeError)`：选择器、路线、投影或预算错误的统一异常。
- `extract_rule_fragment(selector: RuleSelector, *, skill_dir: Path = SKILL_DIR) -> dict[str, str]`：返回 `rule_id/source_path/source_sha256/source_anchor/text`；起止锚点均须唯一，start 在 end 前。
- `extract_preset_names(*, skill_dir: Path = SKILL_DIR) -> tuple[str, ...]`：从 preset 原表精确读取反引号名称，保持原顺序、去重并返回 187 项；对应 `core.preset_names` fragment 的 `source_anchor="exact-preset-name-cells"`。
- `select_rule_fragments(result: PageContextResult) -> tuple[dict[str, str], ...]`：按当前页投影返回 core/module 片段，非 flat 立即抛错。
- `build_execution_context(result: PageContextResult, *, token_budget: int = 14000) -> dict[str, object]`：生成不含磁盘副作用的 v1 payload，并强制精确 token/bytes 预算；payload 包含 `schema/page/route/global/page_context/reference_set/core_rules/module_rules/inputs/token_status/tokens/token_budget/context_sha256`，其中 `context_sha256` 是对除自身字段外的 canonical payload 计算的 SHA-256。
- 后续 Task 2 消费本任务的 payload 与 `ExecutionContextError`，不得改写选择器语义。

**首批核心选择器（精确锚点）：**

| rule_id | source / start_anchor → end_anchor |
|---|---|
| `core.blueprint_binding` | `executor-base.md`：`**Hard rule — binding selection vs realization**` → `**Reference — planning sketches are adjusted freely**` |
| `core.content_expression` | 同文件：`**Hard rule — content vs expression**` → `**Execution anchors and contextual values**` |
| `core.per_page_decisions` | 同文件：`### 2.2 Per-page decision chain` → `## 3. Execution Guidelines` |
| `core.flat_structure` | 同文件：`**Hard rule — Shape-first page authority**` → `**Style workspace under flat structure**`（包含 Shape-first 与 flat 两条 hard rule） |
| `core.file_naming` | 同文件：`### SVG File Naming Convention` → `## 4. Icon Usage` |
| `core.xml_foundation` | `shared-standards-core.md`：`### 1.0 Text characters: must be well-formed XML` → `### 1.1 Line-end Markers (Conditional Contract)` |
| `core.page_closure` | 同文件：`### 4.0 Complete Page-Design Contract` → `### 4.1 Semantic SVG Marker Contract` |
| `core.canvas_flat` | 同文件：`### 4.1 Semantic SVG Marker Contract` → `### 4.2 Editability, Package Promotion, and Text Leading` |
| `core.text_frames` | 同文件：`| One editable PPT text frame with mixed formatting or multiline prose |` → `| Stable object grouping or object-level animation anchor |` |
| `core.text_leading` | 同文件：`**Default — leading by role and density` → `**Hard rule — supported shape conversion**` |
| `core.root_grouping` | 同文件：`**Hard rule — root groups protect body-text layout**` → `Wrap each logical Slide-local body unit` |
| `core.grouping_units` | 同文件：`Wrap each logical Slide-local body unit` → `**Structural atoms and slots are excluded automatically.**` |
| `core.grouping_forbidden` | 同文件：`**Forbidden**:\n\n- One giant` → 随后的 XML 代码围栏（不含围栏本身） |
| `core.preset_boundaries` | `preset-shape-vocabulary.md`：`**Hard rule — registered identities and behavior boundaries**` → `**When to run**`；另以 `extract_preset_names()` 生成 `core.preset_names` fragment，从原表精确投影全部 187 个反引号名称，保持原顺序并断言数量，不复制整张描述表 |

**当前页模块选择器：**
- `module.image`：当前页 `page_context.images` 非空时，完整 `executor-image.md`；无图片不得出现。
- `module.table`：`page_context.visualization` 以 `table/` 开头时，完整 `executor-table.md`。
- `module.visualization_reference`：任一 `page_context.visualization` 存在时，只取 `executor-visualization.md` 的 `**Hard rule — reference, not lock**` → 文件尾。
- `module.native_table_*`：`table/` 且 §IX 对当前对象声明 `Native-ready=yes` 时，分别提取 `native-data-interface.md` 的 selected-object、activation、bounds、project-by-authority 和 Table schema 段；缺声明时不得擅自推断 native-ready。
- `module.mode.<id>` / `module.style.<id>`：从 lock 的 `mode_references` / `visual_style_references` 精确解析 id（直接读取 lock，不改变默认 page-context 输出），分别提取对应文件的 `## 1. Narrative skeleton` / `## 1. Shape & decoration` 至下一 `## 2.`。引用缺失、重复或文件不存在即失败。
- 其他实际触发只在出现时使用已注册选择器；未注册触发必须抛 `ExecutionContextError`，不得扩大为全 roster。

- [x] **Step 1：先写选择器和投影失败测试。** 覆盖：唯一锚点精确切片；start/end 缺失、重复、逆序均抛错；187 个 preset 名称数量/顺序/去重；P01 含 image 且无 table；P03 含 table/reference/native_table 且不混入 P01 图片模块；P01/P03 的 `brief_markdown` 不含对方正文；非 flat 抛错；缺 `Native-ready` 声明不推断。断言 pack 文本与来源逐字一致、每条规则含四类来源字段、schema 为 v1、`token_status=exact`、`tokens.total<=14000`、UTF-8 bytes `<414000`。
- [x] **Step 2：运行测试确认 RED。** 执行 `cd backend && PYTHONPATH=. uv run pytest tests/test_ppt_master_execution_context.py -q`；预期因 `project_management.execution_context` 不存在而失败，记录关键错误，不能用空实现或跳过真实 v2 路径转绿。
- [x] **Step 3：实现选择器、模块路由和预算构建。** 复用 `build_page_context()`、其 lock/template preflight、package 内现有 JSON/SHA/token helpers；不得改变默认 page-context 输出。实现起止锚点唯一定位、当前页模块选择、精确 o200k 计数及 `ExecutionContextError`。预算错误必须带各组成项 token，禁止截断。
- [ ] **Step 4：运行同一 pytest 确认 GREEN。** 所有合成夹具必须短小且通过 `validate_project_artifacts(..., include_design=False)` 的 lock 预检；断言默认 `build_page_context()` 输出在调用执行投影前后完全相同。
- [x] **Step 5：运行真实 v2 零付费探针。** 用 `backend/.venv/bin/python` 直接调用 `build_page_context()` + `build_execution_context()`，分别测量 `temp/qwen-compacted-recovery-v2-home/users/default/threads/56d43057-aa36-48ff-93a7-f0fbdc029efe/user-data/workspace/projects/leike_store_manager_beautify_ppt169_20261118` 的 P01/P03，打印页码、总/组成 token、rule_id 列表和 context SHA。**初次结果：P01 `page_projection=1289 / core=7481 / module=2859 / total=12297`，P03 `1706 / 7481 / 2669 / 12726`，均超过原 8000；已按约定停止，未进入 Task 2、未删除规则、未付费。修订 A 获批后，以 14000 重跑；预期两页均 `exact`、`total<=14000`、bytes `<48000`，P01 含图片规则、P03 含表格与 native 数据规则。若仍失败，停止并回到设计评审。**
- [ ] **Step 6：提交 Task 1。** `feat(ppt-master): 增加当前页执行规则确定性投影`。

### Task 2：执行包哈希、原子写入与 `execution-context` CLI

**Files：**
- Modify: `skills/public/ppt-master/scripts/project_management/execution_context.py`
- Modify: `skills/public/ppt-master/scripts/project_management/cli.py`（imports、parser、command dispatch）
- Modify: `skills/public/ppt-master/scripts/project_manager.py`（入口 docstring）
- Test: `backend/tests/test_ppt_master_execution_context.py`

**Interfaces：**
- `write_execution_context(result: PageContextResult, *, token_budget: int = 14000) -> tuple[Path, dict[str, object]]`：原子写 `validation/execution-context/P<NN>.json`，返回路径与 payload。
- `validate_execution_context(payload: dict[str, object], result: PageContextResult) -> None`：重建期望 payload 并校验 `context_sha256`，过期/手改抛 `ExecutionContextError`。
- `context_sha256` 是对除自身字段外的 canonical projection payload 计算的 SHA-256；不得出现自引用。
- CLI 成功回执仅一行：`EXECUTION_CONTEXT path=<path> page=<PNN> tokens=<N> sha256=<hex>`；失败沿用 CLI 非零异常路径，不输出大包。

- [ ] **Step 1：先写持久化失败测试。** 覆盖：重复构建哈希一致；手改 `core_rules[0].text` 后 validate 失败；Design Spec/lock/reference 变化使旧包过期；token helper 返回 `(None, "unavailable")` 时不写文件；14001-token 合成输入与 bytes 超限均不写；临时文件异常不留下半包；损坏旧包只能从源重建；CLI stdout 只有短回执；`svg_output/` 在命令前后文件集合与 SHA 不变；默认 page-context 输出不变。
- [ ] **Step 2：运行新增测试确认 RED。** 预期 `write_execution_context`、`validate_execution_context` 和 CLI subcommand 不存在。
- [ ] **Step 3：实现原子写入、校验和 CLI。** 使用同目录临时文件 + `replace`；写失败不得让旧包被当成本次成功结果。CLI 先构建/预算校验再写包，模型之后只需一次 `read_file`；不得经 bash stdout 传大 JSON。
- [ ] **Step 4：运行 Task 1+2 全部测试确认 GREEN。** `PYTHONPATH=. uv run pytest tests/test_ppt_master_execution_context.py -q`。
- [ ] **Step 5：对真实 v2 P01/P03 运行 CLI。** 确认两页短回执、精确 token、SHA 与 read_file 可完整读取；再次检查预算和规则覆盖，且 `svg_output/` 无新增/修改。失败则停止，不进入工作流接线。
- [ ] **Step 6：提交 Task 2。** `feat(ppt-master): 增加当前页执行包原子生成命令`。

### Task 3：flat 工作流接线、所有权、P31 与 reseed

**Files：**
- Modify: `skills/public/ppt-master/workflows/generate-pptx.md` Step 6
- Modify: `skills/public/ppt-master/workflows/governance/failure-recovery.md`
- Modify: `skills/public/ppt-master/references/artifact-ownership.md`
- Modify: `skills/public/ppt-master/scripts/docs/project.md`
- Modify: `skills/public/ppt-master/TRAINERAI_PATCHES.md`（新增 P31）
- Modify: `backend/app/gateway/seed.py`
- Modify: `backend/tests/test_ppt_master_execution_context.py`、`backend/tests/test_ppt_master_skill_vendored.py`、`backend/tests/test_seed_agents.py`
- Modify: `temp/qwen-smoke/driver.py`（git-ignored，仅 preflight）

- [ ] **Step 1：先写工作流和版本失败测试。** 断言 flat 的首次 Step 6/翻页/compacted 均调用 `execution-context` 并只读当前页包；不再含 flat 的完整核心/全 roster 批次；同上下文不因 bash 重读；未注册触发 fail closed；非 flat 原完整恢复不变；诊断 page-context 与派生执行包所有权不同；P31 存在且说明 #28/P30 边界；`SEED_VERSION == "2026-11-19.1"`。
- [ ] **Step 2：运行定向测试确认 RED。** `PYTHONPATH=. uv run pytest tests/test_ppt_master_execution_context.py tests/test_ppt_master_skill_vendored.py tests/test_seed_agents.py -q`，记录旧工作流/版本导致的失败。
- [ ] **Step 3：接入 flat 工作流与所有权。** 首次、翻页和 compacted 都先盘点已有非空 SVG、确定当前页、构建并读取执行包；缺规格/锁、0 字节和 Gate 失败仍走原门禁。明确禁止手改包、直接生成 P01、未注册触发回退全量。同步 failure-recovery、artifact-ownership、project.md、P31 和 seed 注释/版本。
- [ ] **Step 4：更新零调用 preflight。** 用独立 `temp/qwen-execution-pack-home` 与 `temp/qwen-execution-pack-smoke`；把旧 P30 `compacted_rule_present` 检查改为检查 `execution_context.py` 与 CLI `execution-context`，继续验证实际 seed 邮票、工作树技能来源、输入 hash/7 页/P03 表格/qwen/config，不读取或打印密钥。
- [ ] **Step 5：运行测试与 fresh home reseed 确认 GREEN。** 先跑 Step 2 命令，再执行 driver `preflight`；预期 `seeded=6`、`SEED_VERSION=2026-11-19.1`、runtime stamp 相同、execution module/CLI 均存在、`PREFLIGHT OK (no model call made)`。不得发起模型调用。
- [ ] **Step 6：提交 Task 3。** `feat(ppt-master): 用当前页执行包替代全量执行预读`。

### Task 4：最终零付费验证与完整门禁

- [ ] **Step 1：重跑最终真实产物探针。** 以最终 HEAD 对 v2 P01/P03 再验 rule coverage、14000-token/414000-byte 双上限、原子产物和 SHA；保存输出到 `temp/qwen-execution-pack-smoke/probe.json`，不入库。
- [ ] **Step 2：跑定向与相邻回归。** 先跑 Task 3 命令，再跑 `tests/test_ppt_master*.py tests/test_seed_*.py tests/test_sandbox_read_file_range.py tests/test_skillscan_native.py`；全部通过。
- [ ] **Step 3：顺序运行完整门禁。** 输入冻结，依次后台运行 `cd backend && make test`、`make lint`、根 `make check`，再跑 `python3 scripts/check_docs_convention.py` 和 `git diff --check`；不得把 warnings 当 errors 隐瞒，也不得并行修改被测输入。
- [ ] **Step 4：确认范围。** `git diff 468a7761...HEAD` 只含 allow 路径和本计划文档；summarization 零改动，真实证据未入库，工作区无测试生成的多余文件。
- [ ] **Step 5：停在付费门。** 向用户报告真实探针 token、rule coverage、reseed 与全部免费结果，单独请求第三次 qwen 付费授权；未明确同意不得进入 Task 5。

### Task 5：用户另行授权后的 qwen P01 行为验收

- [ ] **Step 1：重建对照环境。** 使用最终 HEAD、fresh `DEER_FLOW_HOME`/evidence/thread/project/sandbox，同一冻结输入/qwen/PR A/config；preflight 再次验证 hash、seed 和 execution module/CLI。不得复用 v1/v2 home 或失败 checkpoint。
- [ ] **Step 2：正常完成 Stage 1/2。** 有明确推荐则确认推荐项；若仍未推荐，按首个列出项并记录 ruling。不得添加内容、布局或工具顺序指导。
- [ ] **Step 3：运行最终 authoring 监控。** 最终确认后，非空 `svg_output/p01.svg` 即停；100 次真实工具调用无 P01、新占位/空 checkpoint、Stage 1/2 重问或 20 分钟无新产物立即硬停。不要求完整 PPTX。
- [ ] **Step 4：从 checkpoint 审计真实行为。** 驱动 turn args 可能为空，必须以持久化 checkpoint 参数为准：零占位、确认保留、无重问；首次和 compacted 后均无完整核心/全 roster；执行包构建/读取路径、页码、token、SHA 与 probe 一致，未重写已有非空 SVG。
- [ ] **Step 5：按结果处置。** PASS 只表示同一 qwen+输入+HEAD 的 P01 入口通过；FAIL 则保存证据并停止，不再叠加 System Prompt、bash 提醒、summarization 或 cap 修改。任何额外付费运行都需要新授权。

### Task 6：记录、完整自审、stacked PR 与 OCR

- [ ] **Step 1：写 changelog 并回填计划。** 新建 `docs/changelog/changelog-2026-11-19-ppt-beautify-executor-context-pack.md`，含 ≤5 行 TL;DR、RED→GREEN、真实 probe token/rule IDs、reseed、完整门禁、P01 真实结果和 `## 验收记录`（每行命令→关键输出）。更新本计划唯一状态头和勾选；P01 未通过则保持未执行/部分执行，禁止宣称完整 7 页或 46 页。
- [ ] **Step 2：全分支自审。** 检查 spec 每项覆盖、默认 page-context 兼容、非 flat 不变、选择器无静默漏规则、summarization diff 为零；无获授权子代理时明确记录 self-review，不伪称独立评审。
- [ ] **Step 3：提交并开 stacked PR。** 推送 `ppt-beautify-executor-context-pack`，以 PR A 分支为 base 开 PR，正文链接 spec/plan/changelog，说明 #28 为失败 prompt-only 分支且未合并、PR B 未开始。P01 未通过时标题/正文必须明确“行为未通过，仅评审”。
- [ ] **Step 4：等待 AI/OCR 与 CI。** 读取实际评论与 OCR 日志，不只信绿色状态；Critical/Important 必须先按 TDD 修复并重跑门禁。远程 OCR 不依赖本地 OCR 凭据，本地失败不绕过。
- [ ] **Step 5：保持合并边界并清理。** P01 未通过不合并；即使 P01 通过，PR A/#28/新 PR 的处置也按用户后续门槛执行，PR B 不自动启动。停止沙箱容器，确认无后台任务，保留 temp 证据但不提交，不部署。

## 完成定义

- 所有生产任务均有先 RED 后 GREEN 证据；选择器、预算、哈希、原子写入、CLI、路线兼容和 seed 回归通过。
- 真实 v2 P01/P03 的精确 token 均 ≤14000、bytes <414000，必需 rule ID 全覆盖；未通过则不能修改运行工作流。
- flat 首次/翻页/compacted 共用执行包，其他路线与默认 page-context 不变；P31、SEED_VERSION/reseed 完整。
- 免费全量测试、lint、根 check、docs convention、diff check 通过；summarization 零改动。
- 经单独授权的真实 qwen 冒烟以 checkpoint 证明 P01、摘要健康、无重问和无完整核心/全 roster 批次；未达成则明确未完成，不用静态绿代替。
- PR/OCR/CI 无未处理 Critical/Important；合并、PR B、完整 7 页、46 页和部署均不被本计划自动授权。
