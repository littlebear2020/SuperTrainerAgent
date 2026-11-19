# PPT 美化 Page Execution Pack 设计

> **状态**：待用户书面评审（尚未进入实施计划或生产代码改动）。
> **TL;DR**：复用现有 `page-context` 的确定性当前页投影，新增执行模式以精确原文片段、token 硬门和输入哈希生成 Page Execution Pack；支持的 flat Default/Beautify 路线在首次 Step 6 与 compacted recovery 中共用该包，不再全量读取核心 references 和全 roster 模块。PR A、PR B、System Prompt、summarization 与其他 PPT 路线不变。

## 1. 背景与已验证问题

PR A 已证明摘要健康、最终确认保留且无重问。当前阻塞发生在 authoring：v1 `6616aa94` 与 v2 `0c942a8f` 都在最终确认后的 100 次工具调用内未生成 `svg_output/p01.svg`。两次真实 checkpoint 都显示首次 Step 6 核心批次和全 roster 模块读取继续执行；v2 还通过 `bash sed/cat` 读取核心大段。故继续叠加“少读一点”的 prompt 不构成可靠修复。

已排除的错误前提：

- 两次运行均未到达 P05，不能把问题归因于每五页锁重读。
- Skill Rescue 基于技能路径与全局配置，不按 Agent Name 隔离。
- `character_tail_fallback` 是 PR A 的有效摘要修复，不是重读根因。
- `read_file` 支持 `start_line` / `end_line`；没有证据证明“文件不完整”是重读原因。
- 首次 Step 6 的强制读取是现有工作流要求，不能仅解释为模型焦虑。

## 2. 现有基础与零付费量测

仓库已有 `project_manager.py page-context`：

- `scripts/project_management/page_context.py` 确定性解析 `design_spec.md` 与 `spec_lock.md`，投影当前页 §IX、锁定的沟通/画布/字体/颜色/图片/结构信息、资源选择与引用 SHA。
- 默认 compact JSON 是只读诊断；`--record-usage` 使用 `o200k_base` 记录 token、输入哈希和陈旧状态。
- 既有目标为 `PAGE_CONTEXT_TOKEN_TARGET=2000`、`LOCK_PROJECTION_TOKEN_TARGET=1000`。
- 当前工作流把它限制为诊断/遥测，不作为逐页执行输入；通用 SVG 规则仍假设来自 always-loaded execution core。

在同一 v2 项目产物上直接运行现有只读命令（无模型调用、无文件写入）得到：

| 页面 | compact 输出 | `o200k_base` tokens | lock/global tokens | 当前页内容 |
|---|---:|---:|---:|---|
| P01 | 4840 bytes | 1442 | 834 | §IX=1247 bytes；含当前页图片选择 |
| P03 | 6464 bytes | 1947 | 834 | §IX=2563 bytes；含 `table/record_table` 与 SVG 引用指纹 |

两页均低于现有页面 2000-token 目标，lock/global 低于 1000-token 目标。这证明**动态项目层的确定性投影已经存在且足够小**，无需另写一套规格摘要器或 System Prompt 硬编码。

同时，简单读取“大章节”仍可能过大：v2 相关 `executor-base` §2 为 15.1KB、shared-core 被选章节为 18.8KB、mode/style 为 10.6KB、semantic 为 5.6KB、preset 为 9.9KB、lock 为 3.6KB，合计约 63.5KB，尚未含当前页正文与其他触发模块。因此执行包必须使用精确规则片段和硬预算，不能只把全文件替换为大章节。

## 3. 目标与非目标

### 目标

1. 支持的 flat Default/Beautify 项目在首次 Step 6、翻页、fresh/restarted/external 和 compacted recovery 中都使用同一个确定性 Page Execution Pack。
2. 消除 P01 前的完整 `executor-base.md`、`shared-standards-core.md` 和全 roster 模块批次，不依赖模型自行选择文件/行号。
3. 保留当前页正文、关系、资源与必要 SVG 规则；精确引用原文，不用 LLM 摘要，不静默截断。
4. 以输入哈希、token 硬门和原子写入保证新鲜、完整、可审计。
5. 用同一冻结输入和 qwen 验证模型自主写出非空 P01，且摘要健康、无重问、无完整核心/全 roster 恢复读取。

### 非目标

- 不修改 PR A 的 summarization 实现或测试，不启动 PR B。
- 不修改 System Prompt、SOUL、Dynamic Context、Skill Rescue、bash/read_file 中间件、trigger/trim/cap 或模型配置。
- 不新增 `system-anchored` 标签，不按 Agent Name 注入全局豁免。
- 不改变 Quick、structured/mirror、Create Template 等其他 PPT 路线的读取行为。
- 不直接生成 `svg_output/p01.svg` 或其他最终页面；执行包只是派生输入，不能替代模型 authoring。
- 不以 P01 冒烟冒充完整 7 页、46 页或生产质量验收。

## 4. 方案比较与选择

| 方案 | 优点 | 主要问题 | 决策 |
|---|---|---|---|
| System Prompt 硬编码少量锚点 | 改动小 | 不撤销强制读取、规则覆盖不足、形成第二事实源 | 不采用为主方案 |
| bash 中间件附加“已加载”提醒 | 无需改技能结构 | 错工具层、增加 token、不能阻止 read_file 或 bash sed/cat | 不采用 |
| static/volatile + summarization 豁免 | 可能保留更多上下文 | 修改 PR A 邻接语义、撞 trigger 上限、基于错误隔离假设 | 不采用 |
| 新建另一套 page pack builder | 概念直接 | 重复现有 page-context 解析、资源选择与遥测 | 不采用 |
| **扩展现有 page-context 为执行投影** | 复用成熟解析和哈希；动态层已实测很小；改动边界清晰 | 需要精确规则选择器、预算和路线门禁 | **采用** |

## 5. 总体架构

```text
design_spec.md + spec_lock.md + selected templates/assets
                         │
                         ▼
             existing build_page_context()
        （当前页 §IX / lock / 资源 / 引用 SHA）
                         │
                         ▼
        new execution-context projection layer
        （精确规则选择器 + 当前页模块路由 + token 硬门）
                         │
                         ▼
       validation/execution-context/P<NN>.json
       （原子派生、输入哈希、规则来源、token 统计）
                         │
                         ▼
       模型 read_file 当前页执行包 → 手写 svg_output/P<NN>.svg
```

原则：**规范原文仍是唯一事实源；执行包是可重建的精确投影，不是第二份手写规范。**

## 6. 组件设计

### 6.1 保留现有 page-context 行为

- `build_page_context()`、默认 `page-context` compact 输出、现有诊断用途和 token 遥测保持兼容。
- 默认诊断输出不得因执行模式新增字段而改变；既有调用方无需修改。
- 现有 `reference_set` 继续记录项目/模板/可视化引用指纹。

### 6.2 新执行投影模块

在 `scripts/project_management/` 新增内聚模块 `execution_context.py`，负责：

1. 调用现有 `build_page_context(project, page)`，不重复实现 §IX、图片、模板或 visualization 解析。
2. 根据锁和当前页投影计算需要加载的规则选择器。
3. 从原始 references 提取精确、无改写的 Markdown 片段。
4. 计算执行包与各类内容的 token 数、来源 SHA 和总哈希。
5. 超预算、选择器缺失/重复、输入过期或路线不支持时 fail closed。

新增 CLI `execution-context`：

```bash
python3 ${SKILL_DIR}/scripts/project_manager.py execution-context <project_path> P01
```

CLI 只输出短回执（产物路径、页码、token 数、SHA），不把大 JSON 塞进 bash stdout。模型随后用一次 `read_file` 读取该执行包。

### 6.3 执行包格式

schema 固定为 `ppt-master.execution-context.v1`，至少包含：

- `page`、`schema`、`route`；
- 现有 `global`、`page_context`、`reference_set`；
- `core_rules`：跨页必需的精确规则片段；
- `module_rules`：当前页实际触发的规则片段；
- 每条规则的 `rule_id`、`source_path`、`source_sha256`、`source_anchor`、`text`；
- `inputs`：Design Spec、lock、模板、references 的路径和 SHA；
- `token_status`、`tokens`、`token_budget`、`context_sha256`。

`context_sha256` 是对除该字段自身外的 canonical projection payload 计算的 SHA-256；读取时可据此检测手改或损坏，同时避免自引用哈希。

执行包写到 `validation/execution-context/P<NN>.json`，与诊断 stdout 区分。它只能由命令原子重建，禁止手改，禁止写入 `svg_output/`。

### 6.4 规则选择器注册表

选择器是代码中的显式映射，不使用容易漂移的硬编码行号：

```text
rule_id → source_path + stable heading/paragraph anchor + requiredness
```

- 优先使用稳定标题/命名 hard-rule 锚点；锚点缺失或重复即失败。
- 只允许精确原文提取，不做 paraphrase、排序改写或 LLM 摘要。
- 需要比整节更细粒度时，使用已命名的段落锚点；不得按“看起来像重点”自由删文。
- reference 标题变化会让测试和命令 fail closed，迫使补丁显式更新，而不是静默漏规则。

首批覆盖当前 flat Beautify 所需类别：

- 蓝图/内容与表达边界、当前页决策链、文件命名与完成约束；
- 画布、flat 根语义、可编辑文本框架、根分组与 bounds、禁止特性；
- 当前锁解析出的 mode/visual-style 所需构图片段；
- 当前页图片、Chart/Table、结构或其他实际触发模块；
- preset contour 名称的紧凑投影及实际选择 contour 的详细规则。

完整 187-name 描述、完整 semantic 手册和全 roster 模块不再作为 P01 前统一批次。未预期能力在真正出现时只加载对应选择器；选择器不存在则停止并报告，不能回退为全 roster 读取。

### 6.5 路线与触发门禁

- 首批仅启用 Default/Beautify 且 `pptx_structure.mode=flat` 的路线。
- structured/mirror/Quick 等继续使用原工作流，不调用执行模式。
- flat 路线遇到未注册的必要触发或歧义时，执行模式 fail closed；不得静默退回旧全量批次来伪装成功。
- 当前页已有图片或 visualization 时，使用现有投影结果触发相应模块，不重新发明另一套资源判断。

## 7. 上下文生命周期

### 首次 Step 6 / fresh / restarted / external

1. 确认 Design Spec 与 lock 存在并通过既有 Gate 1/2。
2. 检查 `svg_output/` 非空产物并确定当前页，禁止重写已完成页面。
3. 运行 `execution-context <project> P<NN>`，读取唯一执行包。
4. 按执行包直接 authoring；不再执行原完整核心/全 roster 批次。

### 同一有效上下文

- 当前页执行包保留复用，不因 bash、统计或局部不确定而重读。
- 只有具体规则缺失时按 rule ID 查询对应原文片段；不得重读整个文件。
- 翻页时生成下一页执行包；已完成非空 SVG 不重建。

### Compacted recovery

1. 先盘点 `svg_output/` 并定位 §IX 顺序中的首个缺失页；0 字节仍按 P22 停止。
2. 根据当前页重建或校验执行包哈希。
3. 只读取该执行包并继续当前页，不重读完整 Design Spec、核心 references 或全 roster 模块。
4. 最终确认仍由 PR A 与 `confirm_state.json` 保证，禁止重问 Stage 1/2。

### 派生产物陈旧

任一输入 SHA 变化即标记执行包陈旧并重建。不得手改执行包中的文本以“修复”过期状态。

## 8. Token 预算与输出边界

- 延续 `o200k_base` 计量；执行模式要求 `token_status=exact`，计量不可用时 fail closed。
- 首批总执行包硬门设为 **8000 tokens**，包含现有 page projection、core_rules、module_rules 和 schema 开销。该值给 32000-token summarization 窗口保留系统提示、摘要、工具结果和 SVG 输出空间。
- 现有 page projection 的 2000-token 目标和 lock/global 的 1000-token 目标继续作为分项遥测，不重复相加解释。
- 任一目标超限即失败并输出各组成项 token，不得静默截断或降级为完整文件读取。
- 零付费探针必须先证明 v2 P01/P03 都在预算内且规则覆盖完整；探针失败则不进入付费运行，也不通过提高预算掩盖选择器问题。
- 执行包必须小于现有 `read_file` 输出上限；8000-token 硬门之外再检查 UTF-8 bytes，避免工具截断。

8000 是本设计的保守初始上限，不是宣称所有页面天然适配。后续调整须基于实测并单独评审，不能为了通过而静默提高。

## 9. 所有权与兼容性更新

需要同步更新 `artifact-ownership.md`、`scripts/docs/project.md` 和相关工作流：

- 原 `page-context` stdout 仍是只读诊断，默认不持久化、不强制逐页调用。
- 新 `validation/execution-context/P<NN>.json` 是允许持久化、可原子重建的派生执行视图；内容权威仍属于原 Design Spec、lock、模板和 references。
- 明确执行包不是第二份 Design Spec，不得手工编辑、不得作为内容新增依据、不得进入导出。
- `generate-pptx.md` 与 `failure-recovery.md` 只修改支持的 flat Default/Beautify 执行路径；其他路线保持原恢复矩阵。
- vendored 改动登记新补丁号 **P31**（#28 的 P30 保留给已记录但失败的 prompt-only 方案，避免两个开放分支复用同一编号）。
- 新运行时代理/技能行为需要 `SEED_VERSION=2026-11-19.1`、fresh home reseed 和精确版本测试。

## 10. 错误处理

| 情况 | 行为 |
|---|---|
| Design Spec / lock 缺失或 Gate 失败 | 保持既有阻断与恢复，不生成执行包 |
| 当前页不存在或 §IX 重复 | 复用现有 `PageContextError`，不写产物 |
| 选择器缺失、重复或必要触发未注册 | fail closed，列出 rule_id/source，不读全文件兜底 |
| token 计量不可用或超预算 | 不写新执行包，打印分项测量，停止 authoring |
| 输入 SHA 不匹配 | 标记陈旧并原子重建；不得沿用旧包 |
| 已有执行包损坏/手改 | 校验失败后从唯一事实源重建 |
| 0 字节 SVG | 保留 P22 停止语义 |
| 写执行包失败 | 不进入页面 authoring，不留下半包 |

原子写入使用同目录临时文件 + replace；失败时保留上一份完整执行包，但其输入 SHA 会阻止继续消费，不能被视为新鲜。

## 11. 验证设计

### 实现级（全部使用合成短夹具）

- 当前页 §IX 精确提取，P01/P03 不互相泄漏；正文、Relationships、图片选择保持。
- lock/global、mode/style、表格、图片和结构触发选择器正确；未触发模块不进入包。
- 标题/段落锚点缺失或重复时 fail closed；不得按行号静默读错章节。
- 包内文本与来源逐字一致，所有 source SHA 和总哈希可复现。
- 默认 `page-context` 输出与既有兼容断言不变。
- token 精确计数、分项/总预算、计量不可用、超预算不截断。
- 输入变化使包陈旧；原子重建不留下半包；手改包不会被视为新鲜。
- flat 以外路线不进入新路径；未注册触发不得回退全 roster。
- 不生成或修改 `svg_output/`；已完成非空页面不重写。
- vendored attribution guard、P31 登记、SEED_VERSION/reseed 与现有技能回归通过。

### 零付费真实产物探针

对 v2 现有项目的 P01/P03 生成候选执行包并记录：

- 总 token 与各组成项，均须满足预算；
- 必需 rule ID 覆盖和来源 SHA；
- P01 图片规则、P03 表格/原生数据规则存在，其他页面专属内容不混入；
- 包 bytes 小于 read_file 上限，CLI stdout 仅为短回执。

若覆盖或预算失败，停止在选择器/投影层修正，不进入第三次付费模型运行。

### 行为级（用户另行明确授权）

保持同一冻结输入 SHA=`16f7f0dbf06a559b8889333a0eae9a80349d39d92132efae7a369fe50ea9ff24`、qwen3.8-flash、PR A、token budget disabled、recursion 2400、fresh home/thread 和正常 Stage 1/2：

- 模型自主写出非空 `svg_output/p01.svg` 即停；100 次调用无 P01 / 新占位 / 重问 / 20 分钟无新产物硬停。
- checkpoint 零占位、确认保留、无 Stage 1/2 重问。
- 审计真实工具参数：首次 Step 6 和 compacted 后均无完整核心文件或全 roster 批次；执行包构建与读取路径、token、SHA 与当前页一致。
- 未通过则保留证据，不宣称修复，不叠加 System Prompt 或 summarization 变更。

P01 通过后，完整 7 页的文字保真、原生表格、质量门和渲染验收仍属于后续入口，不由本设计预先宣称。

## 12. Git、评审与停止边界

- 当前仅从 PR A HEAD `468a7761` 建 `ppt-beautify-executor-context-pack`；#28 保持未合并，失败证据不被覆盖。
- 本文先提交供用户书面评审。只有书面设计获批后才调用 writing-plans；实施计划和执行方式获批后才写生产代码。
- 最终 skills 核心路径改动必须走独立 stacked PR + AI/OCR + CI；不以静态测试代替 P01 行为验收。
- 任何付费 qwen 复测均需单独明确授权；本设计本身不授权第三次付费运行、合并或部署。

## 13. 主要风险与权衡

- **规则覆盖风险**：精确片段可能漏掉隐含依赖。以选择器 fail-closed、P01/P03 覆盖矩阵和后续 7 页质量门控制，不用自由摘要掩盖。
- **预算风险**：8000 tokens 可能容纳不了某些复杂页面。先暴露并修正选择器，不静默加预算或截断。
- **行为风险**：模型仍可能无视新路径去读原文件。工作流会移除强制批次并以真实工具审计验证；若仍失败，不再叠加提示层修补。
- **实现复杂度**：复用现有 page-context 可显著减少解析工作，但选择器和所有权变更仍需完整测试与 vendored 评审。
- **质量权衡**：取消全 roster 预读是刻意改变，不把“读得少”等同于“质量必然不变”；P01 只是入口，后续完整页面验收仍是必要条件。
