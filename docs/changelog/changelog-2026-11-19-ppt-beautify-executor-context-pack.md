# PPT 美化 Page Execution Pack 实施记录

> **状态**：部分执行（P01 PASS+EXERCISED，但首次采用执行包前仍有完整核心读取，严格“首次也无全量”要求未完全满足；PR 仅评审，不合并）。
> **TL;DR**：复用现有 `page-context` 新增确定性当前页执行包，含精确规则片段、来源/内容 SHA、原子写入、16000-token/64000-byte 双硬门和最多两个 `<48000-byte` read_ranges。真实 qwen 已写出 P01（`01_cover.svg`，2304 bytes、XML 可解析），且采用执行包后无无界核心 read_file；但 call 31/35 在首次执行包前仍完整读取核心，故不宣称全部读取约束通过。

## 1. 改动范围

- 新增 `execution_context.py`：唯一标题/段落锚点切片、187 个 preset 名称精确投影、当前页 mode/style/image/table/native 模块路由、o200k token、输入/内容 SHA、16000-token/64000-byte 双硬门。
- 新增 `execution-context` CLI：原子生成 `validation/execution-context/P<NN>.json`，短回执包含 `path/page/tokens/sha256/read_ranges`；顶层字段逐行序列化，最多两个 `<48000-byte` 有界读取覆盖全文。
- flat Default/Beautify 首次、翻页和 compacted 改用执行包；非 flat 保留原完整 references 批次，默认诊断 `page-context` 输出不变。
- 登记 P31（#28/P30 保留为失败证据），`SEED_VERSION=2026-11-19.1`，fresh home 实际 reseed 6 个 agent 并核对运行时邮票。
- 未修改 PR A summarization、System Prompt/SOUL、Dynamic Context、Skill Rescue、bash/read_file 中间件、trigger/trim/cap、模型配置、Quick/structured/mirror 或 PR B。

## 2. 设计修订与真实探针

### 初版 8000：停止

- P01=12297、P03=12726 tokens，超过 8000；按用户确认的硬门停止，未进入 Task 2。
- 原型移至 SDD 证据区，未形成生产提交。

### 修订 A（14000）：仍不足

- 修正 Markdown `**Native-ready**:` 解析后，P03 纳入完整 native 表格规则，达到 14562 tokens / 58323 bytes。
- P03 同时超过 14000-token 与 48000-byte 双硬门，A 单独加预算不足，再次停止。

### 修订 A2（通过）

采用用户批准的 **16000 tokens + 64000 bytes + 最多两个 `<48000-byte` 有界读取**，规则内容与选择器不变；序列化按顶层字段换行，避免单行 JSON 无法用行范围切分。

最终真实 v2 探针（`temp/qwen-execution-pack-smoke/probe.json`）：

| 页面 | tokens | bytes | context SHA | read_ranges |
|---|---:|---:|---|---|
| P01 | 12297 | 49752 | `2c2c7a76ae500c5617779e9c48b8c013ee9901d9ab62b1dedab7ef1c73dff4a5` | 1–8=35532B；9–15=14220B |
| P03 | 14562 | 58337 | `4ec0c92a7be8edfd1d549a1acf75be301d15cf8fa43edf49648c85605ead5f32` | 1–8=37156B；9–15=21181B |

P01 含 `module.image`；P03 含 `module.table`、`module.visualization_reference` 和全部 `module.native_table_*`。187 个 preset 名称顺序/去重通过，非 flat 拒绝、输入/内容 SHA、手改与过期检测、原子失败、CLI 短回执、默认诊断兼容均通过。

## 3. 实现与免费验证

- Task 1 RED：缺少执行投影模块；最终合成测试 `13 passed`，真实 A2 探针通过。
- Task 2 RED：缺少分段序列化/写入接口；修复 CLI 先构建 `PageContextResult` 以及 validator 重算当前 payload 自哈希后，`21 passed`。
- Task 3 RED：工作流/所有权/P31/seed 共 5 项失败；接线后 `53 passed`，fresh preflight 为 `seeded=6`、runtime=`2026-11-19.1`、`execution_context_present=True`。
- 最终相邻回归：`326 passed`。
- 最终全量后端：`10101 passed, 92 skipped in 58.21s`。
- lint：Ruff `All checks passed!`，`1318 files already formatted`；唯一问题是新测试 import 排序，已机械修复并重跑。
- 根 `make check`：exit 0；前端 `0 errors`，docs convention `OK`，`git diff --check` 干净。
- summarization 中间件及测试 diff 为零；真实 PPTX/SQLite/log/执行包/SVG 均未入库。

## 4. 第三次付费 qwen 真实行为

共同条件：HEAD=`531600ce`、同一冻结输入 SHA=`16f7f0dbf06a559b8889333a0eae9a80349d39d92132efae7a369fe50ea9ff24`、7 页/P03 原生表格、qwen3.8-flash、PR A、token budget disabled、recursion 2400、fresh home/thread、正常 Stage 1/2。

- Turn 1：547.7s / 38 calls；3 个有效摘要，`bad_count=0`。从 checkpoint 的完整 clarification args 确认 observed 橙红体系、正文 20px、free design。
- Turn 2：287.7s / 18 calls；确认模型推荐的方案 A「乐刻橙 · 教练白板」及默认生产项。
- 最终轮：98 个已落盘工具调用；无 Stage 1/2 重问。累计 `fallback=13 / fresh=15 / reuse=107 / pass_through=1`，15 个有效摘要、`bad_count=0`、最终确认保留。
- **P01 实际产出**：`svg_output/01_cover.svg`，2304 bytes，XML 可解析，`viewBox="0 0 1280 720"`，含首页标题、HIRE RIGHT · FIRE RIGHT 标语和培训信息。call 88 写入，call 96 重写，call 97 局部替换。
- 进程一小时超时不是缺失 P01，而是驱动只查 `p01.svg`，未识别技能既有 `01_*.svg` 命名。驱动已修正并重审为 `P01_PRODUCED`；同时保留 XML/内容验证，避免仅凭文件名判成功。
- 执行包实际调用 43/57/74；`read_file` 59 使用 `start_line=1,end_line=8`，60 使用 `start_line=9,end_line=15`，与 CLI `read_ranges` 完全一致。
- **采用执行包后**：没有无界 `read_file` 读取 `executor-base.md` 或 `shared-standards-core.md`；call 85/90/98 仅为定向 sed/grep 片段。
- **首次缺口**：call 31/35 在首次 `execution-context`（call 43）之前仍完整读取两个核心文件。因此结果记为 **P01 PASS + 首次全量读取缺口**，严格“首次也无全量核心”完成定义未全部满足，不能把 P01 通过等同于全部读取约束通过。

`result.json` 保留 `status=PASS`（P01 已产出），并附 `compliance=P01_PASS_WITH_INITIAL_FULL_READ_CAVEAT`、`initial_full_core_reads=[31,35]`、`post_adoption_unbounded_core_read_file=[]`。本批不启动 PR B，不合并 PR A/#28/新 PR，不宣称完整 7 页或 46 页。

## 验收记录

- `cd backend && PYTHONPATH=. uv run pytest tests/test_ppt_master_execution_context.py -q`（初版 RED）→ `ModuleNotFoundError: project_management.execution_context`。
- 同一命令（A2 GREEN）→ `13 passed`。
- 同一命令（Task 2 GREEN）→ `21 passed`。
- `PYTHONPATH=. uv run pytest tests/test_ppt_master_execution_context.py tests/test_ppt_master_skill_vendored.py tests/test_seed_agents.py -q`（Task 3 RED）→ `5 failed, 48 passed`。
- 同一命令（Task 3 GREEN）→ `53 passed`。
- `PYTHONPATH=. uv run pytest tests/test_ppt_master*.py tests/test_seed_*.py tests/test_sandbox_read_file_range.py tests/test_skillscan_native.py -q` → `326 passed`。
- `cd backend && make test` → `10101 passed, 92 skipped in 58.21s`。
- `cd backend && make lint` → `All checks passed!`；`1318 files already formatted`。
- 根 `make check` → exit 0；`0 errors`，docs convention `OK`。
- fresh preflight → hash match / 7 slides / P03 native table / seeded=6 / runtime=`2026-11-19.1` / execution module+CLI present / `PREFLIGHT OK (no model call made)`。
- 真实 CLI P01 → `tokens=12297`，`read_ranges=1-8,9-15`，分段 35532B + 14220B。
- 真实 CLI P03 → `tokens=14562`，`read_ranges=1-8,9-15`，分段 37156B + 21181B；`svg_unchanged=true`。
- 最终 qwen audit → P01=`01_cover.svg`，15 个有效摘要、`bad_count=0`、`final_confirmed=true`；结果为 `PASS+EXERCISED`，另附首次全量读取缺口。
- `gh pr checks 29 --watch` → quick-checks `pass 29s`、backend-unit-tests `pass 4m10s`、review `pass 2m50s`。
- `gh pr view 29 --comments` → 最新 OCR 3 项：2 个 low + 1 个私有 helper 维护性 medium；另有三个 medium 已修复，P31 high 为误报并附证据驳回，无未处理 Critical/Important。
- `git diff --exit-code 468a7761 -- backend/.../summarization_middleware.py backend/tests/test_summarization_middleware.py` → exit 0。

## 5. PR 与后续边界

- 实现提交：`8eddcebb`、`a64ba498`、`3ac2289f`；lint 提交：`531600ce`。
- stacked PR：[#29](https://github.com/littlebear2020/SuperTrainerAgent/pull/29)，base=`ppt-beautify-summarization-context-integrity`（PR A #27）；标题与正文明确 P01 通过及首次全量读取缺口，仅评审、不合并。远程 quick-checks/backend-unit-tests/review 全绿；读取段数、重叠锚点、非法引用/回退三个 medium 已修复，P31 未登记的 high 为误报并已附证据驳回；私有 helper 复用与其他 low 均 deferred。
- 首次全量读取缺口必须保留在 PR 标题与评审重点中；即使 P01 通过，也不自动合并，不启动 PR B，不部署。

## 6. 收尾修复轮（2026-11-19，PR #29 后续）

本轮只修接手时的三个失败测试并补护栏；未扩展架构，未改 summarization/SOUL/核心 references/PR B，未开新的付费模型运行。均先 RED 后实现。

- **预算错误被单字段读取上限抢先**：`build_execution_context` 内部调用 `render_execution_context`，后者先查 48000-byte 单字段上限，超大输入于是报「单字段超限」而不是预算错误。现在预算只对整份渲染文件检查，顺序固定为 token 总预算 → 字节总预算；48000 单字段上限**未放宽、未删除**，仍由 `render_execution_context` 在 `write_execution_context` 写盘前强制，并由独立测试在放开总预算后单独钉住（测试用 monkeypatch 隔离，不靠放宽断言）。
- **`tokens.total` 差 1**：`total` 的数字位本身就在被计数的文件里，单遍计数必然滞后（13340 vs 13341）。改为对写盘文本迭代至定点（真实 P03 三遍收敛），5 遍不收敛则 fail closed；`context_sha256` 排除 `tokens` 测量字段，不与 total 形成自引用。
- **`native_ready` 为 None**：未提交的解析初稿不识别全角冒号、`= yes` 空格、反引号键与行尾说明。真实 v2 P03 行是 ``**Native-ready**: `industry-background-fit-table=yes`（table/record_table 原生表格对象导出）``——HEAD 的正则对该行为真，但初稿返回 None，按初稿提交会让真实 P03 静默丢掉 5 个 `module.native_table_*` 规则。现已覆盖并恢复；`=no`、`无` 与未声明仍不加载 native_table。
- **token 口径更正**：旧回执（P01=12297、P03=14562）用 HEAD 公式作用于同一 payload 可精确复现，它少计了 `context_sha256`、`tokens.total` 与逐行布局换行；新口径 = 模型实际读取文件的 o200k 精确数——P01=**12353**（+56）、P03=**14627**（+65，含新增 `native_ready` 行 21B）。仍 ≤16000 tokens、<64000 bytes。§2 表中的旧 SHA/token 为历史探针值，不再作为预期值。
- 随同提交的未提交改动：写盘改用同目录唯一临时文件（并发不互相覆盖）。

### 本轮验收记录

- `cd backend && PYTHONPATH=. uv run pytest tests/test_ppt_master_execution_context.py -q`（接手时复现 RED）→ `3 failed, 29 passed`：`field 'page_context' exceeds single-read limit 48000`、`13340 == 13341`、`native_ready is None`。
- 同一命令（native_ready 修复后、定点/预算实现前，含新增护栏测试）→ `4 failed, 49 passed`。
- 同一命令（GREEN）→ `53 passed`。
- `PYTHONPATH=. uv run pytest tests/test_ppt_master*.py tests/test_seed_*.py tests/test_sandbox_read_file_range.py tests/test_skillscan_native.py -q` → `354 passed`。
- `cd backend && make test` → `10132 passed, 89 skipped in 68.42s`。
- `cd backend && make lint` → `All checks passed!`、`1318 files already formatted`（首次发现新测试 1 处格式差异，已 `ruff format` 修复后重跑）。
- 根 `make check` → exit 0；`All matched files use Prettier code style!`、`i18n key 存活检测通过：630 个叶子全活`、docs convention `OK`。
- 真实 v2 P01（临时副本，经 `project_manager.py execution-context` CLI）→ `tokens=12353 … read_ranges=1-8,9-15`；文件 49752B（35532B + 14220B）；对写盘文件独立 o200k 计数 = 12353，与回执、payload 一致；core 15 + module 5（mode×2、style×2、image），无 table/native_table。
- 真实 v2 P03 → `tokens=14627 … read_ranges=1-8,9-16`；文件 58358B（37156B + 21202B）；独立计数 = 14627；`native_ready=true`，含 `module.table`、`module.visualization_reference` 与 5 个 `module.native_table_*`。
- 同一探针 → 回执 SHA = payload SHA；每个 read_range <48000B、段数 ≤2、行覆盖完整；v2 的 `svg_output` 本就为 0 个文件，故「未改 SVG」在真实探针上是空检查，非空覆盖由 `test_execution_context_cli_writes_short_receipt_without_touching_svg` 承担。
- HEAD 公式（`count(compact_json(payload 去掉 context_sha256/total/native_ready))`）作用于同一 payload → P01=12297、P03=14562，与旧回执一致。
- 受控对比（同 umask 022）→ HEAD 写法 `-rw-r--r--`；唯一临时文件写法 `-rw-------`。
- 变异检查（临时副本，不入库）：单遍 total、定点不收敛不报错、token/字节检查顺序互换、删除单字段上限、build 先查读取上限、native_ready 恒真、哈希含 tokens 共 7 个变异 → 各有测试失败，无存活。
- `git diff --check` → 无输出；`python3 scripts/check_docs_convention.py` → `OK 仓库治理约定全过`。
- CI/OCR 结果以 PR #29 的 checks 与评论为准，不回填本文件（回填会再触发一轮评审）。

### 本轮发现、未修改的遗留

- **产物权限 0644 → 0600**：唯一临时文件写法（本批未提交改动，已随本轮提交）使执行包文件变为 owner-only。默认 `LocalSandboxProvider` 与网关同 uid、CLI 与 `read_file` 同在沙箱内，且同 CLI 的 `_write_json_atomic` 已有 `mkstemp` 先例，风险判断为低，但是行为变化且未在真实跨 uid（Docker/AIO 挂载）环境验证。按「范围外不顺手改」与交接约束未动；若需恢复，写盘后对临时文件按 umask 设 `0o644` 即可，并补同口径测试。已登记 backlog。
- **`Native-ready` 多键 map 只取首个条目**：文档语法允许 `a=no; b=yes`，当前（与 HEAD 正则一致）按首项判定，该页不加载 native_table。已登记 backlog。
- `validate_execution_context` 不校验 `tokens`（哈希按设计排除测量字段）；CLI 只做 write+render，当前无生产调用方，deferred。
- **首次全量读取缺口不变**：call 31/35 仍在首次 `execution-context` 之前完整读取核心；本轮零付费修复不改变该事实，严格完成定义仍未满足，不合并、不启动 PR B。
