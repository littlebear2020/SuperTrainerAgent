# PPT 美化长链路上下文压缩修复实施计划

> **状态**：未执行（执行中：PR A 已开 `#27`，Task 1/2 与 Task 3 Step 1–6 已完成；经用户 2026-11-17 明确授权，改用从 46 页原稿一次性制作并冻结的新 7 页输入，hash=`16f7f0dbf06a559b8889333a0eae9a80349d39d92132efae7a369fe50ea9ff24`；PR A 未合并、PR B 未开始；不授权部署或 46 页付费回放）。
> **TL;DR**：先修 DeerFlow 空/退化摘要、旧占位 checkpoint 复用和最新用户消息丢失，再补跨代摘要连续性；ppt-master 保持 P29 不变，用同一 qwen、同一冻结的新 7 页输入做单变量复测。影响面仅限 summarization 中间件、测试与文档；不实施 P30、不改 seed/config、不宣称 46 页长链已验证。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. 两个 PR 必须串行，禁止并行改动同一中间件。

**Goal：** 消除长工具链压缩后“占位摘要替代真实历史、最终确认离开推理窗口、模型反复重读而不写页”的框架级缺陷，并用默认模型干净交付同一份经修订授权冻结的 7 页可编辑 PPTX 验证最终组合代码。

**Architecture：** 保留现有“完整 state/checkpoint 不改写，只替换单次推理窗口”的架构和 frozen-cutoff 复用机制。PR A 在统一 partition/摘要输入层增加有效源校验、有界字符级尾部重试、最新真实用户消息保留、旧占位 checkpoint 失效与无正文日志；PR B 只在 fresh 摘要中携带有效的上一代摘要。任何摘要失败都沿用现有透传语义，不持久化无信息 checkpoint，不新增通用 guardrail 或状态字段。

**Tech Stack：** Python 3.12、`langchain 1.2.15` / `langchain-core 1.4.8`、LangGraph checkpoint、pytest、Ruff、现有 ppt-master P29、qwen3.8-flash、`python-pptx` / `ppt_to_md.py` / LibreOffice。

**Spec：** `docs/2026-11-16-ppt-beautify-long-deck-research-brief.md`（早期判断，需用本文证据修正）、`docs/plans/2026-11-16-ppt-beautify-long-deck-quick-delivery-plan.md`、`docs/changelog/changelog-2026-11-16-ppt-beautify-long-deck-quick-delivery.md`。旧本地证据 `temp/ppt-beautify-7p/qwen-retest-result.md`、`qwen-turn-05.log` 与原 7 页副本在执行时已缺失，仅保留本文已核实的历史计数作背景，不作为新验收证据。`/tmp/pptan/` 脚本仅是探针，不是正式测试或长期依赖。

## 1. 已核实的根因与证据边界

### 1.1 确定性机制

1. 上游裁剪使用 `strategy="last"`、`start_on="human"`、`allow_partial=True`。最终确认后若只有一条用户消息，后续 AI/Tool 尾部超过 `trim_tokens_to_summarize=30000`，可能找不到符合要求的片段。
2. 默认 partial splitter 主要按换行拆分；仅删掉 `start_on="human"` 仍无法覆盖超长单行 JSON/文本，因此正式修复必须同时覆盖字符级尾部拆分。
3. `backend/packages/harness/deerflow/agents/middlewares/summarization_middleware.py:485-487` 与 `:506-508` 在裁剪为空时直接返回 `Previous conversation was too long to summarize.`，不会调用摘要模型。
4. 该占位句仍在 `:399-404` / `:455-460` 被 stash，并由 `after_model` 持久化；`_can_reuse_checkpoint` 当前只检查非空，所以后续按 frozen cutoff 继续复用。
5. 最新真实用户消息没有通用直接保留机制；`keep=("messages", 10)` 只能保最近十条，长工具链会把最终确认推入压缩区。
6. 技能清单在 `:631` 要求 `re-read the file`，`skills/public/ppt-master/workflows/generate-pptx.md:248-254` 也要求 compacted context 重读规格和触发模块。占位摘要使模型不知道已经读过/写过什么，重读又推动下一次压缩，形成循环。
7. 占位路径不调用摘要模型，所以 `run_events` 没有对应摘要响应；压缩是否退化必须查 `summarization_checkpoint`，不能只看日志或 `run_events`。

### 1.2 真实事故复核

| 证据 | 已确认事实 | 结论边界 |
|---|---|---|
| qwen 7 页 turn 5 | 9 次压缩 = 3 次真实 + 6 次占位；首个占位位于 `message_count=379 / cutoff=369` | 最终确认在 msg 379 的重建窗口中消失；这是确定性窗口重建，不是 provider 原始请求抓包 |
| qwen 工具行为 | turn 5 实际 91 次调用；占位前 32 次含 6 次 `write_file` + 1 次 `str_replace`，占位后 59 次无任何写操作、含 36 次 `read_file` | `274` 是五个用户轮次累计调用 `41+32+86+24+91`，不是 turn 5；流式分片序号也不能作调用数 |
| 46 页 `c685805c` | `run_events` 证实两次真实摘要；现存 checkpoint 证实末段至少连续四次占位；最终 `ask_clarification` 复用了 `message_count=248 / cutoff=238` 的占位摘要 | Claude 所述第五次很可能已被每 thread 400 条 checkpoint 保留策略裁掉，当前无法独立复核，不写成确定数字 |
| Doubao 7 页 | 38 次摘要状态转换中 22 次占位、15 次真实、1 次空状态；最终在人工纠正后交付 | 占位缺陷不是“不交付”的单独充分条件；模型从落盘产物恢复进度的能力仍是变量 |
| token 数字 | 48.4M 不是 turn 5 单轮、也不是纯 input，而是 thread 累计 usage 的一种错误转述 | 不用该数字设阈值；provider usage、近似 token counter、摘要模型用量和计费口径另行核查 |

**修正后的责任排序：**

1. **DeerFlow summarization 上下文损坏是本次 qwen 失败和 46 页末段失忆的主因。**它不是唯一充分条件，但必须先修。
2. **ppt-master 读-heavy 与“压缩后重读”规则是放大器；P29 的确认短路仍是必要保留的独立修复。**不能再用早期“模型忽略了真实摘要”解释最终重问，因为末段实际窗口已是占位摘要。
3. **qwen 的失忆后自恢复能力弱于 Doubao 是第三因素。**换模型不能修框架 bug。

## 2. 评审决策记录（对应原 §7）

| 问题 | 决策 |
|---|---|
| 是否先做“Gate 2 后先写 P01 + 8 次读预算” | **否。**占位摘要会让模型无法执行“只读一次/读计数”；必需 pre-P01 读取、校准和 Gate 1/2 也不能被预算跳过。Doubao 成功链存在合法的超过 8 次无写入区段，8 没有依据。 |
| 条件模块是否改成按页触发 | **本批不改。**`executor-base.md:5` 要求 P01 前按全 roster 一次读齐触发模块，`:309` 要求 preset vocabulary 与核心规则常驻；直接按页触发会冲突并影响 P02/P03/P05 质量。 |
| 8 次/10 分钟阈值 | **删除。**测试硬停改为新占位 checkpoint、真实工具调用数和实际产物；100 次是参照既有成功链的宽松成本上限，不是“第 100 次必须强行写页”的产品规则。时间只作安全兜底，不作根因判据。 |
| 只改 prompt、直接 guardrail 或换模型 | **先修代码。**通用“无产出停止”只能省钱，不能恢复上下文；换模型也不能消除占位路径。二者均不进入本批。 |
| SEED_VERSION / P30 / config_version | 本批不动 ppt-master，故 **不 bump `SEED_VERSION`、不 reseed、不登记 P30**；不新增配置结构，故 **不递增 `config_version`**。P30 编号还可能与其他计划冲突，后续不得默认占用。 |
| 兼容与评审 | 中间件命中核心路径，两个 PR 均须 AI/OCR + CI；旧占位/空 checkpoint 精确识别为不可复用，无数据库迁移；生产生效需另授权重建 gateway 镜像并重启。 |
| 7 页输入缺失（2026-11-17 用户修订授权） | 允许从现存 46 页原稿一次性制作新输入，选原页 `[1, 7, 10, 20, 24, 36, 46]`，冻结 SHA-256=`16f7f0dbf06a559b8889333a0eae9a80349d39d92132efae7a369fe50ea9ff24`；两轮复测共用该新输入。输入准备后不得再删页、补字或改版；不宣称恢复原输入或做 46 页回放。 |

## 3. 全局约束与不变量

- 执行时从最新 `origin/main` 建独立 worktree/分支；调研时 `origin/main=c8b39ee7` 已包含上一批 `41b724c2` 和 P29 / `SEED_VERSION=2026-11-16.4`。只带入本计划，不携带当前脏工作区的无关改动。
- `state["messages"]` 与完整 checkpoint 历史永不由本修复改写；只改变单次 `ModelRequest.messages` 和既有 `summarization_checkpoint` 的值。
- checkpoint 仍只有 `summary/message_count/approx_tokens/cutoff_index/created_at` 五字段；不新增状态 schema、配置项、脚本、中间件或依赖。
- 每次 fresh 摘要必须包含至少一条本轮原始非 `SystemMessage`；否则摘要模型零调用、原请求透传、无新 checkpoint、cutoff 不推进。
- 摘要模型返回空文本或两个历史占位句时视为失败，不得 stash；旧占位/空 checkpoint 永不复用，也不作为 PR B 的有效前次摘要。
- 最新真实用户消息在 fresh 与 frozen-cutoff reuse 窗口中都直接存在且仅一次；不得误选 `hide_from_ui`、动态上下文 reminder、summary/skill ledger 等框架消息。
- 保留 AI/Tool 配对、动态上下文三元组、技能 rescue、before-summarization hook、TAG_NOSTREAM、ToolProtocolError 穿透及 stash 清理语义。
- sync/async 共用裁剪、校验和 prompt 构建逻辑；只允许模型调用本身分别使用 `invoke` / `ainvoke`。
- 日志只记模式、计数、cutoff、近似 token、thread/agent 标识和布尔值；不得记录用户正文、工具结果、摘要全文或配置秘密。
- 输入准备阶段按上述修订授权一次性从 46 页原稿选取 7 页并冻结 hash；真实复测只允许正常 Stage 1/2 与 P05 后“继续”，不得再删页、补字、改版或纠正工具顺序。人工验收和目检可以存在，但任何人工修复都使“干净自主交付”失败。
- 两轮真实 qwen 复测是执行期的显式 opt-in 付费验证；额度、401、网络、沙箱故障归类为基础设施结果，不作模型行为结论。
- 不部署、不做 46 页回放、不改模型、token budget、recursion、seed、技能或前端；本计划完成也不等于 46 页已验证。

## 4. Review Focus

- **超长单行工具输出**：字符级 fallback 必须仍在原 30K 原始消息预算内 → Task 1 单行 JSON 测试。
- **重试仍只有 System/空结果**：不得生成外观正常的无信息 checkpoint → Task 1 透传与 stash 测试。
- **旧 thread 已带占位 checkpoint**：不得等增长阈值后才自愈 → Task 2 旧 checkpoint 立即 fresh 测试。
- **最新用户已在尾部或是框架 HumanMessage**：不得重复或误救 → Task 2 fresh/reuse/identity 测试。
- **第二次 fresh 摘要只复述旧摘要**：必须先取得本轮原始非 System 内容，旧摘要不得挤占该硬条件 → Task 5 多代摘要与失败不推进 cutoff 测试。
- **摘要失败后的终止语义**：`ToolProtocolError` 不得被通用回落吞掉 → Task 1/5 sync+async 相关回归。

## 5. 文件范围与串行边界

| 批次 | 允许路径（allow） | 禁改路径（ban） | 共享基座触达 |
|---|---|---|---|
| PR A：上下文完整性 | `backend/packages/harness/deerflow/agents/middlewares/summarization_middleware.py`；`backend/tests/test_summarization_middleware.py`；必要时 `backend/tests/test_tool_call_protocol_middleware.py`；`backend/docs/summarization.md`；本计划；`docs/changelog/changelog-2026-11-17-ppt-beautify-summarization-fix-batch-a.md`；`docs/findings-backlog.md` 仅 token 口径一条 | 其他运行代码、配置、seed、技能、前端、docker、真实数据库/夹具 | 是，单执行者独占 summarization；不得与其他中间件批并行 |
| PR B：跨代摘要连续性 | 同上一中间件与测试；`backend/docs/summarization.md`；本计划；`docs/changelog/changelog-2026-11-17-ppt-beautify-summarization-fix-batch-b.md` | 同 PR A，且不回改 PR A 已验收语义 | 是，必须在 PR A 合入后从最新 main 新建 |

**禁止入库：** 真实 PPTX、SQLite/checkpoint、run log、用户正文、 `/tmp/pptan/` 探针、生成的 SVG/导出 PPTX。正式测试全部使用合成短消息。

---

## Batch A / PR A：先阻止上下文损坏

### Task 1：用合成回归固定“有效摘要源”，修空裁剪与超长单行

**Files：**
- Modify: `backend/packages/harness/deerflow/agents/middlewares/summarization_middleware.py:476-516`
- Test: `backend/tests/test_summarization_middleware.py`

**Interfaces：**
- `_trim_messages_for_summary(messages: list[AnyMessage]) -> list[AnyMessage]`：先走 `super()`，空或仅 System 时用同一 `trim_tokens_to_summarize` 做无 `start_on`、字符级 splitter 的尾部重试。
- `_build_summary_prompt(messages_to_summarize: list[AnyMessage]) -> str`：统一裁剪、非 System 校验和 `{messages}` 格式化。
- `_validated_summary_text(text: str) -> str`：拒绝空文本及精确匹配的两个历史占位句。
- `_INVALID_SUMMARY_SENTINELS`：只含 `Previous conversation was too long to summarize.` 与 `No previous conversation history.`，不做模糊匹配。

- [x] **Step 1：扩展测试 helper，不写大型夹具。** 给 `_middleware` 增加可选 `trim_tokens_to_summarize` 与 `token_counter` 参数，默认保持现状。用短的内联单行长字符串和确定性字符计数器构造“最终确认标记 + AI/Tool 尾部”，不得读取真实数据库。
- [x] **Step 2：先写失败测试。** 覆盖 `test_trim_fallback_summarizes_single_line_tool_tail`、`test_trim_fallback_rejects_system_only_source`、`test_summary_source_failure_passes_through_without_checkpoint`、`test_summary_model_placeholder_response_is_not_persisted`，并各补 sync/async 等价路径；另补 sync/async `ToolProtocolError` 穿透与 stash 清理。断言摘要模型调用次数、prompt 中合成工作标记、handler 收到原请求或有效窗口、`after_model/aafter_model` 是否产生 checkpoint；旧代码必须因占位句/零摘要调用/错误持久化而红。
- [x] **Step 3：运行定向测试确认 RED。** 执行 `cd backend && PYTHONPATH=. uv run pytest tests/test_summarization_middleware.py -q`；把新增用例的关键失败行记入 Batch A changelog，不接受“未运行所以未失败”。
- [x] **Step 4：实现最小修复。** 正常裁剪结果保持不变；只有空或仅 System 才重试。重试仍无原始非 System 内容时抛内部 `ValueError`，复用现有外层透传和 stash 清理。`_create_summary/_acreate_summary` 删除占位句正常返回路径，只保留模型调用差异。
- [x] **Step 5：运行同一测试确认 GREEN。** 执行 `PYTHONPATH=. uv run pytest tests/test_summarization_middleware.py tests/test_tool_call_protocol_middleware.py -q`，确认现有 109 项基线与新增用例全部通过；不得通过放宽断言或删除旧测试转绿。
- [x] **Step 6：提交 Task 1。** 将测试与最小实现作为一笔提交：`fix(summarization): 阻止无效摘要源写入占位 checkpoint`。

### Task 2：旧占位 checkpoint 立即失效，并直接保留最新真实用户消息

**Files：**
- Modify: `backend/packages/harness/deerflow/agents/middlewares/summarization_middleware.py:319-355`、`:525-576`
- Test: `backend/tests/test_summarization_middleware.py`

**Interfaces：**
- `_read_usable_checkpoint_summary(checkpoint: dict[str, Any] | None) -> str | None`：仅返回非空且非 sentinel 的摘要文本。
- `_preserve_latest_user_message(original_messages, messages_to_summarize, preserved_messages) -> tuple[list[AnyMessage], list[AnyMessage]]`：反向定位最新真实用户消息，以对象身份去重；在统一 partition 末尾调用，使 fresh/reuse、sync/async 自动共享。

- [x] **Step 1：先写旧 checkpoint 失败测试。** 参数化两个 sentinel 与空字符串；在消息/token 增长低于复用阈值时仍必须走 fresh，成功后覆盖旧 checkpoint，失败时透传且不得继续复用旧值。
- [x] **Step 2：先写用户保留失败测试。** 最终确认位于 cutoff 前时，fresh 和 frozen-cutoff reuse 的 handler 窗口都含该原始 HumanMessage 且仅一次；另覆盖它本已在 preserved、隐藏 Human、动态 triplet、重复内容不同对象、AI/Tool 配对及原 state 不变。
- [x] **Step 3：增加行为级公共链断言。** 复用 `_model_request` / `_recording_handler` / `_run_wrap_and_after`，跑 `wrap_model_call → after_model → 增长后的 wrap_model_call`，断言主模型实际收到有效摘要和最新合成确认，而不是只测私有 helper 或常量。
- [x] **Step 4：实现最小修复。** `_can_reuse_checkpoint` 改用 usable-summary helper；最新用户筛选参考 `subagent_limit_middleware.py:57-72` 的隐藏消息语义，但不跨中间件导入私有函数。不得改变 cutoff，不得复制消息进 state。
- [x] **Step 5：跑定向与相邻回归。** 执行 summarization、dynamic-context、view-image、tool-call-protocol 四个测试文件；确认 hook、技能 ledger/rescue 和 checkpoint 五字段精确形状测试继续通过。
- [x] **Step 6：提交 Task 2。** `fix(summarization): 保留最新用户指令并淘汰历史占位摘要`。

### Task 3：可观测性、文档、完整门禁与 PR A

**Files：**
- Modify: 同一中间件、`backend/docs/summarization.md`、`docs/findings-backlog.md`
- Create: `docs/changelog/changelog-2026-11-17-ppt-beautify-summarization-fix-batch-a.md`

- [x] **Step 1：补日志及 caplog 测试。** fresh 成功、reuse、旧 sentinel 拒绝各记录一条可检索结果；空/仅 System 重试和最终透传记 WARNING。PR A 字段至少含 `mode`、`message_count`、`cutoff_index`、`approx_tokens`；用合成秘密标记断言正文/摘要/工具内容未入日志。
- [x] **Step 2：更新常青文档。** `backend/docs/summarization.md` 写明有效源硬条件、字符级 fallback、最新用户保留、旧 sentinel 失效、透传不推进 cutoff 和日志口径；不得再把“No messages silently leave”写成超出这些保证的绝对承诺。
- [x] **Step 3：登记范围外 token 疑点。** 只在 `docs/findings-backlog.md` 增加一行：核对 qwen/Doubao `usage_metadata`、流式累计和近似 token 的口径，指针到 `token_attention_middleware.py:107-143` 与 `runtime/journal.py:379-388`；本批不修 provider/token 统计。
- [x] **Step 4：写 Batch A changelog。** 必含 ≤5 行 TL;DR、RED→GREEN 证据和 `## 验收记录`；逐行采用“命令 → 实际关键输出”，明确尚未做 46 页与默认模型完整交付。
- [x] **Step 5：跑提交前门禁。** 定向/相邻测试后，依次以后台任务运行 `cd backend && make test`、`cd backend && make lint` 与根 `make check`，前一项完成且输入未变再启动下一项；再跑 `python3 scripts/check_docs_convention.py` 和 `git diff --check`。全部通过才可 push；不设置 `RUN_LIVE=1`。
- [x] **Step 6：提交并开 PR A。** 分支 `ppt-beautify-summarization-context-integrity`，文档提交可用 `docs(summarization): 记录上下文完整性与复测边界`。显式设置 `GH_REPO` 为 origin 仓库，PR 正文链接本计划与 Batch A changelog，等待 AI/OCR + CI；Critical/Important 必须先修。
- [ ] **Step 7：完成 Task 4 冒烟后再决定合并。** 新占位、确认仍从重建窗口消失或 checkpoint 误推进 = 修复未成立，禁止合并；仅 qwen  authoring 行为失败不掩盖框架修复，可合入已证明正确的代码，但计划状态只能写“部分执行/默认模型未通过”。

### Task 4：PR A 单变量 qwen 冒烟，只验证到 P01

- [ ] **Step 1：冻结对照条件。** 按修订授权从 46 页原稿选取 `[1, 7, 10, 20, 24, 36, 46]`，一次性生成并冻结 `temp/ppt-beautify-7p/店长招聘与汰换-7页代表页.pptx`；验证 7 页、P03 原生表格与 SHA-256=`16f7f0dbf06a559b8889333a0eae9a80349d39d92132efae7a369fe50ea9ff24`。固定同一 qwen3.8-flash、P29 / `.4` SOUL 与技能、相同确认回复、`token_budget.enabled=false`、`recursion_limit=2400`。使用全新 `DEER_FLOW_HOME=temp/qwen-summary-pr1-home`、thread、project、sandbox 前缀；不得复用旧 stub checkpoint。
- [ ] **Step 2：只改变 PR A。** 不改 P30、prompt、模型、skill rescue 参数或参考文档。运行期间冻结代码与配置输入，保存日志和 checkpoint 于 `temp/`，不入库。
- [ ] **Step 3：按真实事件外部硬停。** 最终确认后出现任何一个新占位/空摘要 checkpoint、再次询问 Stage 1/2、或 100 次真实工具调用仍无 `svg_output/p01.svg`，立即停止并保存证据；另设 20 分钟无新产物的纯成本安全兜底，但不把超时本身归为模型根因。调用数从真实消息/tool call 还原，不用流式行数。
- [ ] **Step 4：判定是否命中目标路径。** 若出现字符级 fallback WARNING 且随后生成非占位 checkpoint、重建窗口保留最终确认并写出 P01，记 `PASS + EXERCISED`；若未触发 fallback 但 P01 正常出现，记 `PASS + NOT_EXERCISED`，只能声称行为冒烟通过，确定性回归才是 bug 修复证据。不得为追求命中反复烧 token。
- [ ] **Step 5：P01 后即停并分类。** 本冒烟不要求 PPTX。若摘要健康、确认/规格/锁可见但仍反复重读，记录实际文件和 checkpoint，不在本 PR 加 prompt 或调 5000 skill cap，并暂停在进入 Batch B 之前按 §6 决策；若仍有占位/确认丢失，返回 Task 1/2。只有 P01 冒烟通过才默认进入 PR B。
- [ ] **Step 6：回填 changelog 与计划。** 记录输入 hash、代码 commit、thread、起止时间、压缩/占位数、是否 EXERCISED、真实调用数、P01 结果和停止原因；更新 PR A，满足 Task 3 Step 7 后 `gh pr merge --rebase`。不部署。

## Batch B / PR B：补跨代摘要连续性并完成 7 页验收

### Task 5：fresh 摘要携带有效上一代摘要，reuse 不重复拼接

**Files：**
- Modify: `backend/packages/harness/deerflow/agents/middlewares/summarization_middleware.py`
- Test: `backend/tests/test_summarization_middleware.py`
- Modify: `backend/docs/summarization.md`
- Create: `docs/changelog/changelog-2026-11-17-ppt-beautify-summarization-fix-batch-b.md`

**Interfaces：**
- `_build_summary_prompt(..., *, previous_summary: str | None = None) -> str`
- `_create_summary(..., *, previous_summary: str | None = None) -> str`
- `_acreate_summary(..., *, previous_summary: str | None = None) -> str`
- 私有 checkpoint helper：除 usable summary 外，仅接受非 bool 整数且满足 `0 < old_cutoff <= old_message_count <= 当前消息数`、`old_cutoff <= 新 cutoff`；legacy/rollback/非法字段不携带。

- [ ] **Step 1：确认入口门并从 PR A 合入后的最新 main 建分支。** 默认入口是 Task 4 已写出 P01，且没有新占位/确认丢失；其他结果先按 §6 决策，不自动叠加 PR B。分支 `ppt-beautify-summarization-continuity`；确认 Task 1–4 的代码、测试和文档均在基线，禁止 cherry-pick 当前其他工作区改动。
- [ ] **Step 2：先写多代 fresh 失败测试。** 摘要模型依次返回 `summary-1` / `summary-2`；分别由消息增长和 token 增长触发第二次 fresh。断言第二次 prompt 同时含 `summary-1` 与本轮新原始标记，reuse 调用不重复附加旧摘要，最终 checkpoint 推进到 `summary-2`。覆盖 async 等价路径。
- [ ] **Step 3：先写兼容与预算边界测试。** 参数化 legacy、stub、空、错误类型/bool cutoff、rollback checkpoint，均不得携带旧摘要；自定义 `summary_prompt` 仍只要求 `{messages}`。无论旧摘要多长，本轮原始消息先独立裁剪且必须有非 System 内容；第二次摘要模型失败时原请求透传、无新 checkpoint、cutoff 不推进。
- [ ] **Step 4：实现最小 PR B。** 只对 fresh 路径传有效前次摘要；先将原始消息按现有 30K 上限独立裁剪，再把旧摘要作为明确标记的已压缩上下文与 formatted raw tail 拼接。30K 继续表示“本轮原始历史预算”，不引入主观 8K/22K 分配；总输入超 provider 限制时沿用异常透传，不生成只复述旧摘要却推进 cutoff 的 checkpoint。
- [ ] **Step 5：补日志与文档。** 增加 `has_previous_summary`、旧摘要近似 token、总摘要输入近似 token 计数，不记正文；文档明确“旧摘要是 30K 原始消息预算之外的附加上下文”，避免误判为无限输入或总预算保证。
- [ ] **Step 6：跑定向、相邻回归与 mutation check。** 暂时撤回“携带旧摘要”这一行，新增第二次 fresh 测试必须变红；恢复后转绿。再跑 summarization、dynamic-context、view-image、tool-call-protocol。
- [ ] **Step 7：提交 Task 5。** `fix(summarization): 刷新摘要时延续有效历史决策`，包含测试、日志、常青文档和 Batch B changelog 初稿。
- [ ] **Step 8：跑完整门禁并开 PR B。** 依次以后台任务执行 `cd backend && make test`、`make lint` 与根 `make check`，再跑 docs convention、`git diff --check`。Batch B changelog 含 TL;DR、验收记录、mutation 结果；push 后等待 AI/OCR + CI，Critical/Important 先修。Task 6 最终行为结果回填后才可按门槛合并，不部署。

### Task 6：最终组合代码的 qwen 干净 7 页交付与决策门

- [ ] **Step 1：重建最终对照环境。** 使用 PR B 最终 HEAD、同一输入 hash/qwen/P29/配置/确认文本；新建 `DEER_FLOW_HOME=temp/qwen-summary-final-home`、thread、project 和容器前缀。不得沿用 PR A home 或既有 Doubao 交付副本。
- [ ] **Step 2：完整跑 Beautify。** 最终确认后不得重问；规格、锁与 Gate 1/2 完成后按序产生 P01–P05，early gate 前无 P06。P05 early gate 和一次 consolidated repair 后报告 `P01–P05 / 7` 并停住；用户只回复一次“继续”。
- [ ] **Step 3：验证恢复和导出。** 继续后首个新页面为 P06，再写 P07；不得重跑源解析、初始化、身份抽取、重写 P01–P05 或逐页跑 checker。P07 后直接 final gate 与导出。沿用 Task 4 的新占位/100 次无 P01/成本安全硬停；任何人工内容或版式修复都判干净交付失败。
- [ ] **Step 4：检查摘要链而非仅 run=success。** 从 checkpoint 统计 fresh/reuse、fallback、旧 sentinel 拒绝和本次新 sentinel（必须为 0）；“继续”之后若发生压缩，确认其有效摘要仍携带最终确认、已产页面和下一页断点。窗口结论注明来自 checkpoint 重建，不冒充 provider 抓包。
- [ ] **Step 5：验收实际 PPTX。** final gate 必须 `7/7 passed, 0 errors`；导出非空且 POSTFLIGHT 通过。用 `ppt_to_md.py` 做逐页非空行有序文本相等，而非仅字符多重集；若形状顺序因布局改变，则用逐逻辑块/表格单元格的一一对应证明内容不增不改不丢，并明确保存映射证据。用 `python-pptx` 递归 group 验证每页原生文本形状、P03 原生表格，用 LibreOffice 渲染 7 页目检阅读顺序与明显溢出。
- [ ] **Step 6：回填并按结果合并。** Batch B changelog 记录所有命令关键行、最终 HEAD、输入 hash、thread、摘要统计、gate/导出/文字/原生形状/渲染结果和交付路径；勾选计划并更新唯一状态头。仅当 AI/OCR + CI 绿、无 Critical/Important、最终行为验收通过时 `gh pr merge --rebase`；否则保留“部分执行/未通过”事实，不宣称完成。
- [ ] **Step 7：清理临时执行物。** 停止本批 preview/server/容器，确认无在途后台任务；保留必要 `temp/` 证据供用户检查但不提交，不覆盖或删除上一批 Doubao 交付。生产部署另开授权。

## 6. 复测后的分支条件

| 最终结果 | 下一步 |
|---|---|
| 7 页干净通过且目标压缩路径有确定性/实际证据 | 本计划完成；不实施 P30。仅宣称该 qwen + 7 页 + 当前 HEAD 通过。 |
| 摘要健康、确认/产物状态保留，P01 仍因 `executor-base` / `shared-standards-core` 重读而失败 | 另开单变量计划：先量测两个文件相对 `preserve_recent_skill_tokens_per_skill=5000` 的裁剪/救援记录，再选择调整保留上限，或把 compacted recovery 改为“先查盘，只重读 lock 与当前页模块”；本批不预选。 |
| 摘要健康且必要 pre-P01 读取均完成，仍无 P01 | 才评审窄版 authoring-first：只约束“既有强制准备完成后的下一动作”，不带 8 次预算、不跳过 Gate、不逐页 checker；另走 vendored patch/SEED_VERSION/reseed。 |
| 仍生成占位、复用坏 checkpoint、确认丢失或 cutoff 错进 | 继续修 summarization；禁止用 prompt、换模型或 guardrail 掩盖。 |
| 页面已写完但文字/视觉质量门失败 | 归为独立 authoring/fidelity 问题，不反推摘要修复无效，也不做人工修复后宣称干净通过。 |
| 需要控制异常成本 | 可另立通用“无新产物停止”guardrail 计划；它是成本保护，不是本事故成功路径。 |
| 需要 46 页结论 | 7 页通过后另开人工 E2E/预算授权；不得通过增加 P10/P15 暂停来绕框架 bug。 |

## 7. 回滚与部署边界

- PR B 出现跨代摘要回归时优先 revert PR B，保留 PR A 的确定性保护；PR A 自身回归则 revert 对应 PR。不得删除数据库或重写完整消息历史。
- 旧占位 checkpoint 无需迁移；新代码会拒绝复用。回滚旧代码可能重新接受旧占位，因此回滚后应使用新 thread 验证，不能拿已损坏 thread 的成功作证据。
- 本地 `make dev` reload 或重启 gateway 即可加载 Python 改动；不需要 reseed、sandbox/PPT converter/worker 镜像重建。
- 生产需另行授权重建 gateway 镜像并重建容器；本计划不执行部署，不修改 `config_version`。

## 8. 完成定义

- 两个 PR 的测试均先证明旧行为 RED、修复后 GREEN；sync/async、旧 checkpoint、最新用户、跨代摘要、单行/仅 System、失败不推进 cutoff 和 ToolProtocolError 均有覆盖。
- 全量 backend、lint、根 `make check`、docs convention、`git diff --check` 通过；AI/OCR 与 PR CI 无未处理 Critical/Important。
- 每次压缩可从日志/checkpoint 审计，日志不含正文；占位路径不再产生可复用 checkpoint。
- 最终组合 HEAD 上，同一 qwen 干净跑出 7 页 PPTX，P05→继续→P06/P07、文字保真、原生可编辑和渲染验收全部通过，无人工修复。
- 计划勾选、状态头、Batch A/B changelog 与真实命令关键输出一致；临时产物未入库。
- 明确不宣称 46 页长链、其他模型或生产部署已验证。

## 9. 明确不做

- 不实施 P30 的 8 次读预算、强制跳过 pre-P01 reference 或按页触发改造。
- 不新增通用无产出中间件、数据库迁移、配置字段、token budget/recursion 调整或模型切换。
- 不改 `executor-base.md` / `shared-standards-core.md`、seed、技能、前端、docker；不需要 `SEED_VERSION` / `config_version` bump。
- 不提交真实用户数据/数据库/PPTX/log，不把 `/tmp` 探针原样入库，不做 46 页付费回放或生产部署。

## Assumptions

- 用户已明确授权执行、两轮 qwen 复测及 2026-11-17 的新输入修订；执行中仍受各停止条件约束。
- 原 7 页副本不可恢复时，使用修订授权制作并冻结的新 7 页输入；若 qwen 额度或基础设施不可用，记录阻塞，不改用 Doubao 冒充对照。
- 调研中的 Git 基线会漂移；执行以最新 main 为准，但必须确认 P29 和 PR A/B 的实际祖先关系。
- 46 页是否回放、是否部署以及复测失败后是否进入 prompt/skill-cap 实验，均由后续明确决策开启。
