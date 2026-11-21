# DeepSeek DSML 工具协议守卫与子代理假成功治理 Implementation Plan

> **状态**：未执行（不执行：2026-11-20 用户决策；修复需改动 lead 与 subagent 核心中间件链及子代理 run_id 记账，风险高于当前收益；缺陷保留，改以模型规避承接——`2026-11-18-model-mode-ladder-plan.md` 不将 DeepSeek 设为任何档位默认，仅保留为可选。本文件留作事故分析与后续参考；若恢复执行，须重新评审并按 Task 1→4 串行，且另行授权 push/部署/live）。
> **TL;DR**：已确认现有守卫不识别 DSML、子代理链未装守卫且可把最终 DSML 正文包装成成功；采用“lead + subagent 保守识别、首次恢复、二次明确失败”的应用层防线，绝不执行正文命令。公司服务器实际模型/网关来源仍需原始 run 证据，TTS 缺键只作为后续条件性风险处理。2026-11-20 用户决定不执行本计划，改以模式阶梯的 DeepSeek 非默认策略规避。

> **For agentic workers：** 按 Task 1→4 串行执行并勾选；Task 5 只在另获部署与真实调用授权后执行。每步先写失败测试再实现，命中 Harness 中间件核心路径，必须走独立分支 + PR + OCR；不要并行修改共享基座。

**Goal：** 当模型把受支持的 DSML 工具调用写进 assistant 正文时，不再静默当作最终答案或子任务成功结果；保持原生 `tool_calls` 是唯一可执行通道，并让失败状态对用户和父 Agent 可见。

**Architecture：** 扩展现有 `ToolCallProtocolMiddleware`，不新增正文命令解析器或执行器。lead 保持现有装配位置与两阶段终止；subagent 在每次执行新建的中间件链末尾安装同类守卫，以 task ID 作为 run 级记账键。子代理二次违规映射为 `SubagentStatus.FAILED + stop_reason=tool_protocol_error`，由现有 `task` 失败分支返回 `Task failed`，不改变父 run 的工具异常恢复语义。

**调研基准：** 当前工作树 HEAD `09ef5c60`；当前工作树有多项无关未提交改动，实施时必须从确认后的最新 `main` 建独立 worktree，不携带现有脏文件。原始导出只作为脱敏回归样本来源，不把用户稿件、真实主机、账号、密钥、内部任务 ID 或运行数据库内容提交进仓库。

## 一、逐项核验结论

| 原反馈或推断 | 核验结果 | 证据与边界 |
|---|---|---|
| 导出中两条 assistant 都是 DSML 文本 | **确认** | 原始导出第 16–33、45–58 行分别为多 `invoke` 和单 `invoke` 形态；两轮之间有新用户消息，不能当作“同一 run 连续二次违规”的证据。 |
| 没有工具结果，所以工具一次都没执行 | **不能仅凭该导出确认** | 默认导出主动过滤 ToolMessage，也默认不输出原生 `tool_calls`：`frontend/src/core/threads/export.ts:15-45`、`:88-121`。必须查完整 thread state、run/journal 和脱敏执行日志。 |
| 项目没有 DSML 处理，现有守卫只认 `<tool_call>` | **确认代码能力缺口** | `backend/packages/harness/deerflow/agents/middlewares/tool_call_protocol_middleware.py:51`、`:125-160` 只接受字符串正文整段 `<tool_call>` JSON，且要求 `name`、对象 `arguments`，无原生/无效工具调用；backend/frontend 当前无 DSML 处理实现。 |
| DSML 是 DeepSeek V4 的工具调用文本格式 | **有官方依据，但“原生”需限定** | DeepSeek 官方 V4 encoding 文档有 “Tool Calling (DSML Format)”；这证明模型编码格式，不证明所有托管 API 都会返回结构化 `tool_calls`，也不能仅凭导出证明本次底层模型。 |
| 本次样本是 mangled DSML | **与第三方定义吻合** | 样本为双竖线且带空格：`<｜｜DSML｜｜ calls>`；它不同于官方 V4 的单竖线 `tool_calls`，也不同于官方 V4.1 的单竖线 spaced `calls`。Canonical/Mangled 是 `pi-dsml` 的分类词，不应称为 DeepSeek 官方术语。 |
| 守卫只装在 lead，子代理没有 | **确认** | lead 注册：`backend/packages/harness/deerflow/agents/lead_agent/agent.py:378-399`；subagent 装配：`backend/packages/harness/deerflow/agents/middlewares/tool_error_handling_middleware.py:158-185`。 |
| 子代理会把 DSML 包装成 `Task Succeeded` | **确认代码可达，未确认本次实际发生** | 非空 DSML 可正常出图，executor 提取最终正文并置 COMPLETED：`backend/packages/harness/deerflow/subagents/executor.py:720-786`；`task` 成功包装：`backend/packages/harness/deerflow/tools/builtins/task_tool.py:286-295`。附件本身不能证明该次走了 subagent。 |
| 本地历史 run 有同款事故和 9 条 DSML 事件 | **确认，但需解释计数口径** | 指定本地历史 run 的全部序列化事件中 9 条含 DSML：2 条直接模型响应、2 条工具结果、5 条链输出快照；不是 9 次模型生成。4 次 lead 响应中 2 次直接含 DSML。 |
| 该历史 run 证明本次播客事故 | **不成立** | 历史 run 的线程元数据为 `co-writer`，时间早于 podcast-studio 首次入库，且未与附件建立内容或 run ID 映射；它只能证明同类机制曾在本地出现。 |
| 同日 Tencent/NC 没 DSML，证明上游路由是根因 | **只有有限统计支持，不能定因** | 已持久化 lead 响应中 Tencent 98、NC 33 条未检出 DSML；直接 DSML 响应集中于金山云的单个历史 run。subagent 原始响应未被完整持久化，样本也不足以排除模型随机性或请求差异。 |
| 公司服务器配置有 DeepSeek，播客跟随所选模型 | **配置与代码路径确认** | 本地与 11-13 部署候选的 `config.yaml:75-86` 均含 `deepseek-v4.1-flash`，走 360 入口；`backend/seed/agents/podcast-studio/config.yaml:1-3` 为 `model: null`。后端按请求模型 → Agent 模型 → 默认模型解析。 |
| 前端模型选择会粘住，新线程继承 | **代码确认，故障浏览器未核实** | `frontend/src/core/settings/store.ts:127-149` 同时写全局与线程覆盖；`frontend/src/core/settings/hooks.ts:31-58` 线程覆盖优先，新线程通常继承全局选择。这是浏览器 localStorage 行为，不是跨浏览器/账号级证明。 |
| 测试服务器结果可解释本次事故 | **不采纳为证据** | 本轮未访问或核验任何真实服务器；不同机器的配置、版本和运行记录不能互相替代。计划不记录真实主机信息。 |
| 11-13 部署缺 11-16 TTS 密钥注入修复 | **版本风险确认，是否故障未确认** | 部署快照 `fba6cf98` 为 2026-11-13，仍只有全局 AUTOBIND；受限自动注入修复于 11-16 在功能分支 `e5a75cf3`、主线 `a33df251` 落地。附件没有 render 调用、缺键输出或真实 TTS 错误。 |
| 自建 vLLM 只需加 `deepseek_v4` | **需按模型版本区分** | vLLM `v0.34.0` 注册了 `deepseek_v4` 与 `deepseek_v41`；官方模型指南同时展示 `--enable-auto-tool-choice` 和相应 tokenizer/reasoning parser。它是自建服务端配置，不是 360 客户端参数，也未证明能解析本次双竖线变体。 |

### 根因结论的准确边界

**已确认的代码缺陷：** 对“正文只有完整 DSML、没有原生 `tool_calls`”的响应，现有 lead 守卫不会拦截，subagent 还存在最终结果被包装为成功的第二条路径。这与导出症状高度一致，值得立即做确定性修复。

**仍未定案的事故根因：** 公司服务器这次是否实际选择 DeepSeek、原始 AIMessage 是否同时携带原生调用、工具是否曾执行、DSML 双竖线在模型生成/tokenizer 解码/网关转换中的哪一层产生，都没有原始 run 证据。不能把“部署后才出现”“360 网关未解析”或“工具零执行”写成已经证明的事实。

## 二、方案取舍

| 方案 | 优点 | 局限与决策 |
|---|---|---|
| A. 临时切 Qwen，或把 podcast-studio 固定到 Qwen | 最快恢复测试，改动小 | 只绕过触发条件，不能修其他 Agent/子代理的协议假成功；固定 seed 还需重新 seed。**仅把 composer 切 Qwen作为应急，不在本批永久绑模型。** |
| B. 扩展应用层协议守卫，覆盖 lead + subagent | 确定性、可回归、不依赖模型再次犯错；保持正文命令不可执行 | 不能让未解析的正文调用恢复成真实工具能力，也不能撤回已流出的过程文本。**作为本批根治假成功的推荐方案。** |
| C. 在上游/自建 vLLM 启用匹配 parser | 从服务端恢复结构化 `tool_calls`，体验最完整 | 依赖上游控制权、模型和 vLLM 版本；对 360 路由不可直接实施。**作为有服务端控制权时的补充，不替代应用层防线。** |

## 三、评审决策记录

- **修复目标是什么？** 本批保证“不再把正文伪工具调用静默当成功”，不承诺把 DSML 文本转换成可执行工具调用，也不承诺修复所有上游网关。
- **支持哪些形态？** 只支持有真实或官方依据的三组：V4 canonical（单竖线、`tool_calls`）、V4.1 spaced（单竖线、`calls`/`invoke`/`parameter` 前空格）、本次 mangled（双竖线、spaced `calls`）。不泛化支持任意 `<invoke>`、大小写变体或未知包装名。
- **怎样避免误伤文档？** 忽略 fenced code 内的 DSML；fence 外允许普通前后叙述，但必须有完整闭合 wrapper 和至少一个完整闭合 `invoke`。不能只搜 `DSML` 子串。残缺标记保持不解析、不执行，本批不另造 malformed-protocol 终态。
- **是否改变 `<tool_call>` 旧语义？** 不改变其整段 JSON 检测边界；本批只新增 DSML 识别。list-content、多模态块和 generic `<invoke>` 不扩张，保留反例测试，待有真实样本再单独立项。
- **命中后如何处理？** 复用每 run 一次恢复：删除整条伪 AIMessage、请求级注入通用恢复提示；同 run 第二次命中进入 `tool_protocol_error`。一个 wrapper 内多个 `invoke` 只计一次违规。
- **原生调用是否优先？** 有原生 `tool_calls` 或 `invalid_tool_calls` 时本守卫不接管；绝不执行正文里的另一条命令。原生工具仍只能由真实 tools 节点执行。
- **子代理二次违规是否让父 run 整体 error？** 否。保持子任务异常隔离：executor 专用分支返回 FAILED 和 `tool_protocol_error`，现有 `task` 返回 `Task failed`；父 Agent 可诚实处理失败。若未来要求父 run 同步终断，需另行设计 executor、ToolErrorHandling 和 worker 的完整穿透。
- **子代理恢复预算如何隔离？** context 增加 `run_id=result.task_id`，且保持每次 `_create_agent()` 新建中间件实例；不改 `_clear_other_runs`，不引入跨 task 单例。独立实例 + task run ID 同时保留，防止同线程并发任务互清状态。
- **是否顺便过滤流式 UI？** 不在本批。after_model 删除不能撤回已流出的过程 chunk；若服务器现场证明 UI 仍展示 DSML 且需要隐藏，再单独做流式过滤设计，不能与 checkpoint 守卫混成一个正则补丁。
- **是否改模型持久化或播客 seed？** 不改。模型继承是现有产品设计，DSML 防线应覆盖所有 Agent；Qwen 只是应急对照。
- **TTS 如何处理？** 不重复实现 11-16 沙箱修复，不在本批开启全局 AUTOBIND。服务器验收时先确认运行版本和密钥注入，再进入正式 render。
- **findings backlog：** 沙箱密钥授权项继续由 `docs/plans/2026-11-16-agent-media-channel-and-sandbox-secrets-plan.md` 承接；旧模型探针硬编码项本批不改，复用现有 opt-in roster probe 做人工对照；其余条目明确推迟。

## 四、完成后的行为

| 场景 | 必须得到的结果 |
|---|---|
| 完整受支持 DSML，无原生调用，首次命中 | 不执行正文命令；删除伪消息；只重试一次模型并注入恢复提示；伪消息和提示不进入持久上下文。 |
| 同 run 第二次命中，lead | 不再调用模型或工具；run 最终 `status=error`、`stop_reason=tool_protocol_error`；不得被 fallback/summarization 吞成 success。 |
| 同 task 第二次命中，subagent | 子任务 FAILED、`stop_reason=tool_protocol_error`；只发失败生命周期；`task` 不得返回 `Task Succeeded` 或 `task_completed`。 |
| fenced code、普通 DSML 讨论、残缺标记 | 不被新检测器误判；其中文本永远不执行。 |
| 正文 DSML + 原生 tool call | 守卫不接管；只执行原生调用，正文命令不执行。 |
| 播客正常链路 | 仍先完成公开资料研究和完整口播稿确认；确认前不 TTS，确认后才 render；协议故障不得产出假成功 MP3。 |

## 五、文件范围与执行边界

| 项目 | 范围 |
|---|---|
| 允许路径（allow） | `backend/packages/harness/deerflow/agents/middlewares/tool_call_protocol_middleware.py`；`tool_error_handling_middleware.py`；`backend/packages/harness/deerflow/subagents/executor.py`；`backend/tests/test_tool_call_protocol_middleware.py`；`test_tool_error_handling_middleware.py`；`test_stop_reason_stamping.py`；`test_subagent_executor.py`；`test_subagent_stop_reason.py`；`test_subagent_turn_budget.py`；`test_task_tool_core_logic.py`；`backend/AGENTS.md`；本计划；本主题 changelog。 |
| 禁改路径（ban） | `config*.yaml`；`backend/seed/agents/**`；`skills/**`；`docker/**`；provider 包裹链；frontend；`task_tool.py`；`llm_error_handling_middleware.py`；`summarization_middleware.py`；其他中间件；现有无关未提交文件。若实现证明必须改禁改文件，停止并回规划侧，不自行扩权。 |
| 共享基座触达 | 是；`ToolCallProtocolMiddleware`、subagent builder 与 executor 由本计划串行独占，不并行派发。 |

外部参考资料：

- DeepSeek V4 官方 DSML 编码说明：https://huggingface.co/deepseek-ai/DeepSeek-V4-Flash/blob/main/encoding/README.md
- DeepSeek V4.1 官方 spaced DSML 说明：https://huggingface.co/deepseek-ai/DeepSeek-V4.1-Flash/blob/main/encoding/README.md
- 第三方 mangled 形态说明：https://cdn.jsdelivr.net/npm/pi-dsml@0.0.1/README.md
- vLLM V4/V4.1 parser 注册（v0.34.0）：https://github.com/vllm-project/vllm/blob/v0.34.0/vllm/tool_parsers/__init__.py#L33-L48
- vLLM 自动工具调用前提：https://docs.vllm.ai/en/stable/features/tool_calling/#automatic-function-calling

---

### Task 1：以脱敏样本扩展 DSML 检测器

- [ ] **Step 1：建立失败测试。** 在 `test_tool_call_protocol_middleware.py` 使用合成无害 fixture 覆盖：V4 canonical、V4.1 spaced、本次双竖线 mangled、单/多 `invoke`、fence 外前后叙述；反例覆盖 fence-only、普通提及 DSML、缺 wrapper/invoke 闭合、generic `<invoke>`、list content、原生/invalid tool calls。保留所有既有 `<tool_call>` 断言。
- [ ] **Step 2：确认红灯有效。** 跑 `cd backend && uv run pytest tests/test_tool_call_protocol_middleware.py -q`；新增 DSML 阳性用例必须因当前不识别而失败，不得通过删除旧反例或放宽 native-call 条件造绿。
- [ ] **Step 3：实现最小结构识别。** 在现有中间件内增加独立 DSML 判定，不执行、不反序列化命令参数。检测前先排除 fenced code 区域，只识别约定的精确 marker 和完整闭合结构；多 `invoke` 返回一个判定结果。字符串正文、无原生/无效调用的前置条件保持。
- [ ] **Step 4：通用化提示与日志。** 将恢复提示和 `ToolProtocolError` 文案从 `<tool_call>` 专用改为“正文伪工具调用”；日志只记录变体、thread/run 和命中阶段，不记录原始正文、命令或参数。
- [ ] **Step 5：跑检测回归。** 重跑 Step 2，并用一个会在真实文件系统留下 sentinel 的无害假命令验证：检测、恢复和终断全过程都不会创建 sentinel。

### Task 2：钉住 lead 的恢复、终断和真实装配语义

- [ ] **Step 1：先扩展行为失败用例。** 让受支持 DSML 至少覆盖真实图的首次恢复、二次终断、checkpoint 清除、跨新 run 预算重置；worker 用例断言第二次违规为 `status=error + tool_protocol_error`，且无 fallback AIMessage 假成功。
- [ ] **Step 2：验证生产顺序而非只测私有函数。** 保留同步/异步 `@hook_config(can_jump_to=["model"])`、TerminalResponse 之后的注册位置、两阶段删除后终止、LLMErrorHandling/Summarization 异常放行、模型瞬时失败后的提示回队。增加一条直接消费真实 `_build_middlewares()` 的装配/行为断言，避免最小手工链漏掉拓扑问题。
- [ ] **Step 3：运行定向与相关回归。** 跑 `cd backend && uv run pytest tests/test_tool_call_protocol_middleware.py tests/test_stop_reason_stamping.py -q`；任何旧语义变化都要回到计划评审，不得直接改旧断言接受。

### Task 3：给子代理装上守卫并消除 Task 假成功

- [ ] **Step 1：先写子代理失败测试。** 覆盖 builder 中守卫位于 TerminalResponse 之后、每次构建得到独立实例；`_aexecute` context 的 `run_id` 等于 task ID；同线程两个独立子代理交错时恢复提示与预算互不清理。
- [ ] **Step 2：接线但保持现有拓扑。** 在 `build_subagent_runtime_middlewares()` 末尾新建 `ToolCallProtocolMiddleware()`，不前移到共享基座，不改成单例；在 `_aexecute` 的 context 增加 task run ID，保留 thread ID、父沙箱继承和 `release_on_exit=False`。
- [ ] **Step 3：实现专用异常映射。** 在通用 `except Exception` 前捕获 `ToolProtocolError`，复用 FAILED 结果路径并写 `stop_reason=tool_protocol_error`。不改 `task_tool.py`，不重抛给父 run；确认只发 `task_failed`，不发 `task_completed`/`present_files`。
- [ ] **Step 4：做真实 executor → task 行为验收。** 至少一条测试走真实 `_aexecute`，不能只靠 DummyExecutor：首次 DSML 可恢复到原生调用；二次 DSML 最终 `Task failed`，正文命令零执行，伪消息不留 checkpoint。两个同线程 task 各自只有一次恢复，互不因对方二次违规而终止。
- [ ] **Step 5：更新受装配影响的既有测试。** 仅在链长/递归预算变化有明确原因时更新 `test_tool_error_handling_middleware.py`、`test_subagent_turn_budget.py`；跑 `cd backend && uv run pytest tests/test_tool_error_handling_middleware.py tests/test_subagent_executor.py tests/test_subagent_stop_reason.py tests/test_subagent_turn_budget.py tests/test_task_tool_core_logic.py -q`。

### Task 4：文档、全量门禁与 PR 评审

- [ ] **Step 1：同步现行约定。** 更新 `backend/AGENTS.md` 的 ToolCallProtocol 节：受支持 DSML 形态、fence/prose 边界、lead 与 subagent 装配、子代理 FAILED 而非父 run 强制终断、task run ID 与独立实例约束；不改动无关约定。
- [ ] **Step 2：核对实现与计划的每一行对应关系。** 特别检查没有把正文命令解析执行、没有把播客固定模型、没有改前端/技能/config/docker、没有夹带流式 UI 过滤。
- [ ] **Step 3：运行后端全量门禁。** 后台执行 `cd backend && make test`，冻结被测输入并记录退出码；若环境不支持 xdist，再按仓库既有 `make test-serial` 兜底，不能以减少测试代替。
- [ ] **Step 4：运行根门禁与文档检查。** 后台执行根 `make check`；随后跑 `python3 scripts/check_docs_convention.py` 和 `git diff --check`。新建/移动文档违例必须归零。
- [ ] **Step 5：写 changelog 并勾选计划。** 按实际执行日创建 `docs/changelog/changelog-YYYY-MM-DD-deepseek-dsml-tool-protocol-hardening.md`，含 ≤5 行 TL;DR 和「验收记录」，每行写命令与真实关键输出；未做真实供应商调用就明确写“确定性假模型/mock 验收，未证明上游已修复”。
- [ ] **Step 6：走核心路径 PR。** 从最新 main 建主题分支，逐笔提交；推送前确认 `git status --short` 只有本批文件。按仓库约定创建 PR、等待 CI 与 OCR，Critical/Important 清零且 CI 绿后才可 rebase merge；本计划本身不授权自动合并。

### Task 5：公司服务器分阶段验收（需另行授权）

- [ ] **Step 1：先保全原事故现场。** 只读取得事故 thread/run 的完整 AIMessage 字段、ToolMessage、run 状态、stop_reason、请求模型和 provider response metadata；记录 `content` 类型、`tool_calls`/`invalid_tool_calls` 数量和脱敏 model ID。默认 Markdown 导出不得再作为工具未执行的唯一证据。
- [ ] **Step 2：做无副作用模型路由对照。** 在新测试线程中，用相同 `probe_echo` 工具分别测 Qwen 与 DeepSeek，不触发正式播客 render；记录内部模型名、实际上游 model ID、结构化调用是否出现。DeepSeek 没复现 DSML 不能否定本地修复，也不能证明上游已修复。
- [ ] **Step 3：部署后验收协议行为。** 若 DeepSeek 再出现完整 DSML，首次应恢复为真实工具调用，二次应明确协议错误/子任务失败，不得静默成功；若未复现，只报告“真实路由未复现 + 本地确定性重放已通过”。
- [ ] **Step 4：在 render 前核查 TTS 条件。** 确认 gateway 镜像 digest、实际代码 commit 和技能挂载版本；只检查 `VOLCENGINE_VOICE_ACCESS_KEY` 与 AUTOBIND 的存在性，不打印值、长度或完整环境。优先使用已包含受限白名单注入修复的新版本，不在旧版直接开启全局 AUTOBIND。
- [ ] **Step 5：最后做真实播客端到端。** 使用同一 `interview_two` 提示，先完成资料与完整口播稿，确认前断言零 TTS；用户确认后再 render，核对双人音色、MP3 可播放、run/任务状态和产物归属。Qwen 用于恢复业务验证，DeepSeek 用于协议对照，两者结论分别记录。

## 六、回退与未覆盖项

- 代码只增加守卫与失败映射，无数据库/config 迁移；出现误判或拓扑回归时按 PR revert，不能以执行正文命令作为兜底。
- 本批不解决上游为什么不返回结构化工具调用，不保证任意 DSML 拼写都能识别，不处理已流出文本的 UI 撤回，不扩张 list-content 或 malformed 终态。
- 若自建 vLLM，另按实际模型选择 `deepseek_v4` 或 `deepseek_v41`，同时核对 `--enable-auto-tool-choice`、tokenizer/reasoning parser 与目标版本源码；不得把该建议直接套到 360 网关。
