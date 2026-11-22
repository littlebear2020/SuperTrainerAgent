# 按模式分化 Summarization 触发线方案研究

> **状态**：已批准立项，未执行（排期于 harness 升级之后；执行前先复检漂移，再另写实施任务与 changelog）；研究定稿日 2026-11-21；批准日 2026-11-22；基准 commit `8c78dace519377d81e1aa72b8765765091368f84`（GitHub rebase merge 后的 `main`，与原 PR #37 HEAD `9b42a8d9` tree 相同）；本地配置基线 `config_version: 32`。
> **TL;DR**：三档分化在技术上可行，最小面是 `summarization.modes.{flash,standard,ultra}` 覆盖 `trigger/keep/trim_tokens_to_summarize`，复用已透传的 `context.mode`，不改模型花名册、前端或 `/models`。实测 32k 只直接约束 inference messages，不含独立 system 与工具 schema；三档固定开销代理值约 10.4k/11.4k/13.0k o200k。当前 trim 会让部分被移出窗口的旧消息未进入摘要输入，且上游 2.2.0 已更换摘要与 durable-context 机制，因此本文只给实验候选值，不宣称任何一组已更优；先基准、后立项、上线前按 harness 版本复测。
> **For agentic workers**：本文是只读研究稿，不是实施授权；不得据此改代码、配置、harness、价格或密钥，不得运行付费任务。用户批准立项后，另建独立 worktree/分支并先写失败测试。

**Goal**：在不牺牲任务连续性与交付质量的前提下，判断闪速 / 标准 / Ultra 是否应使用不同摘要触发、保留与摘要输入预算，并给出可证伪的验证路径。

**推荐结论**：**方案可行，建议立项做“离线基准 + 受控实验”，不建议现在直接改三档默认数值。** 若评审要求立即选择实现形态，推荐本文 §4 的单层 `summarization.modes` 覆盖；若 2.2.0 harness 升级已排期，优先把策略解析做成可迁移的薄层，并在升级后重新标定。

---

## 一、研究边界与证据等级

### 1.1 本次做了什么

- 只读检查当前 summarization 配置、lead-agent 装配、LangChain 1.2.15 请求构造与摘要实现。
- 只读投影本地 `config.yaml` 的 `config_version`、首模型、`mode_defaults` 与 `summarization`，未输出或修改任何密钥、价格、视频配置。
- 复用 `skills/public/ppt-master/scripts/prompt_audit.py` 的 o200k_base 编码器，对当前实际配置离线构造 system prompt、技能目录、规划提示和 OpenAI-compatible 工具 schema；没有模型调用。
- 用 fake model / 本地 model 对象验证 32k 边界、provider-reported 分支和 trim 行为；没有网络推理或付费任务。
- 阅读上游 DeerFlow `v2.2.0` release、摘要文档、配置、durable context、tool output 与基准代码作为一手证据。

### 1.2 没有做什么

- 未改代码、配置或 harness，未部署，未运行 `RUN_LIVE=1`。
- 未测真实摘要答案质量、生产摘要触发率、provider 精确计费 token、prompt-cache 命中率或真实模型上下文上限。
- 未把 o200k_base 当作 Claude/Qwen 的计费 tokenizer；本文所有“固定开销”均是**统一尺度的结构代理值**，不是账单数值。
- 未使用真实用户对话内容做样本，未形成生产统计结论。

### 1.3 证据措辞

- **已实测**：本次离线探针直接观察到的计数或布尔行为。
- **代码事实**：当前 commit 或上游 tag 可直接定位的实现契约。
- **候选 / 待测**：需要基准或真实 provider 验证，不能当成优化结论。

---

## 二、现状基线：全局 32k 的真实机制

### 2.1 当前有效配置

本地 `config_version: 32` 的只读投影与 `config.example.yaml` 一致：

| 配置 | 当前值 | 语义 |
|---|---:|---|
| `summarization.enabled` | `true` | lead-agent 装配摘要中间件 |
| `model_name` | `null` | 当前代码取 `models[0]`；本地首模型已核实为 `qwen3.8-flash`，不是运行模型本身 |
| `trigger` | `[{type: tokens, value: 32000}]` | 单条件；多条件时是 OR |
| `keep` | `{type: messages, value: 10}` | 尽量保留最近 10 条，并保护 AI/Tool 配对 |
| `trim_tokens_to_summarize` | `30000` | 只限制摘要模型看到的旧消息源，不限制主模型保留窗口 |
| `resummarize_min_new_messages` | `20` | checkpoint 后新增消息达到 20 会允许新鲜摘要 |
| `resummarize_min_new_tokens` | `30000` | checkpoint 后近似 token 增长达到 30k 会允许新鲜摘要 |
| skill rescue | 最近 5 个 / 总 25k / 每个 5k | 可额外保留技能读取原文，故 `keep=10` 不是窗口硬上限 |
| `skill_ledger_max_entries` | `8` | 未获救技能读取保留名称与路径指针 |

装配入口：`backend/packages/harness/deerflow/agents/lead_agent/agent.py:60-126`；配置字段：`backend/packages/harness/deerflow/config/summarization_config.py:21-91`。

### 2.2 每次模型调用的判定顺序

当前 `DeerFlowSummarizationMiddleware.wrap_model_call/awrap_model_call`：

1. 对 `request.messages` 确保 ID并计算 `token_counter(messages)`。
2. `_should_summarize` 检查 trigger；未达到直接调用原 handler。
3. 达到 trigger 后计算 cutoff；若 `cutoff <= 0`，仍直接放行。因此即使单条消息极大，**只有不超过 keep 的少量消息时也可能没有可摘要分区**。
4. 有可切分区时，先尝试复用 checkpoint；否则生成新摘要。
5. 新鲜路径把旧消息经 skill rescue / dynamic-context rescue / latest-user rescue 分区，再构造摘要调用。
6. 主模型看到的是临时 inference window；当前实现**不删除** `state["messages"]` 原文。checkpoint 在 `after_model` 持久化。

代码位置：`backend/packages/harness/deerflow/agents/middlewares/summarization_middleware.py:399-526`。

### 2.3 32k 到底数什么

`ModelRequest.messages` 的类型声明明确写着 `excluding system message`，独立 `system_message` 到 `_execute_model_*` 内才 prepend 给 provider：

- `backend/.venv/lib/python3.12/site-packages/langchain/agents/middleware/types.py:89-155`
- `backend/.venv/lib/python3.12/site-packages/langchain/agents/factory.py:1269-1283`

因此：

| 内容 | 是否直接进入当前近似 messages 32k |
|---|---|
| 静态 role / 工作目录 / clarification / response-style 等 system 模板 | 否 |
| SOUL | 否，它在 system 内 |
| 可用技能名称、描述、路径目录 | 否，它在 system 内 |
| 工具 JSON schema | 否 |
| plan 中间件独立 system prompt | 否 |
| 用户 / AI / Tool 消息历史 | 是 |
| `read_file` 读取的技能正文 ToolMessage | 是 |
| DynamicContext 注入到 message area 的日期、memory reminder | 是；它不是静态 system_message |
| summary / skill ledger / 被保留的技能原文 | 会在相应 inference window 中出现，不能忽略其窗口占用 |

默认 token counter 是 LangChain `count_tokens_approximately(use_usage_metadata_scaling=True)`；非 Anthropic 通常按约 4 chars/token 估计并可用 usage metadata 校准，Anthropic chat 分支使用 3.3 chars/token。它不是 provider 精确 tokenizer。当前摘要模型由首模型构造，counter 跟随**摘要中间件 model**，不能想当然认为它跟随用户所选运行模型。

### 2.4 近似 32k 边界实测

离线构造 20 条 Human/AI 消息，以首条消息 ASCII 填充长度二分搜索边界；fake model 走 generic approximate counter、无 usage metadata：

| 首条填充字符数 | messages 近似 tokens | `_should_summarize` |
|---:|---:|---|
| 127408 | 31999 | `false` |
| 127409 | 32000 | `true` |

同一个探针另构造独立 SystemMessage，其单独近似值为 32505，但它不在 `request.messages` 中；以上边界不随该 system 加入而改变。工具 schema 同样不在 `token_counter(messages)` 输入中。

这证明的是当前代码的近似边界，不是任何 provider 的真实 32k 计费点。

### 2.5 provider-reported total tokens 也会触发

LangChain 1.2.15 的 tokens trigger 还有第二条路径：最后一条 AI 消息的 `usage_metadata.total_tokens` 达到阈值，且 `response_metadata.model_provider` 与摘要中间件 model 的 provider 相同，也可触发。

本地构造低近似消息历史的实测：

| 最后 AI reported total | provider | messages 近似值 | 结果 |
|---:|---|---:|---|
| 32000 | `openai`（匹配） | 15 | 触发 |
| 31999 | `openai`（匹配） | 15 | 不触发 |
| 99999 | `other`（不匹配） | 15 | 不触发 |

该路径可能把 system/tools 已反映到 provider total 的历史纳入判断，但比较字段是 **`total_tokens`（输入 + 输出），不是纯输入容量**；且是否触发依赖 provider 元数据存在且匹配。不能把它当作三档统一、可靠的固定开销补偿。当前各 360 自定义 provider 在真实响应中的字段填充率仍待测。

### 2.6 keep、fresh 与 reuse 的实际行为

- `keep=messages:10` 是消息数保留策略，不是 token 上限；单个大 ToolMessage 可远大于预算。
- AI/Tool 配对保护会移动 cutoff；latest user、dynamic context 和技能 rescue 还可增加保留内容。
- 新鲜摘要写入 `summarization_checkpoint{summary,message_count,approx_tokens,cutoff_index,created_at}`，主请求窗口为 `[summary, optional skill ledger, preserved...]`。
- 后续调用仍需先满足 trigger。checkpoint 复用要求消息增长和 token 增长**同时小于** 20 / 30k；任一达到门槛就走 fresh。
- reuse 冻结旧 cutoff C0，窗口为旧 summary + C0 之后全部消息，而不是再次只取最近 10 条；因此 tail 会随 reuse 增长。
- 摘要失败会 pass through 原始请求，并丢弃本次待持久化 checkpoint；不能把日志中的“触发”当成“压缩成功”。

### 2.7 trim=30k 的实测覆盖缺口

当前 `_build_summary_prompt` 先对 `messages_to_summarize` 做 `strategy="last"`、30k 近似 token 裁剪，再调用摘要模型；被裁剪的旧消息仍属于主窗口中被 summary 替代的分区。

本次构造 10 条旧消息、每条约 40k 字符，再加保留尾：

| 指标 | 实测 |
|---|---:|
| 旧分区消息数 | 10 |
| 旧分区近似 tokens | 100065 |
| trim 后消息数 | 2 |
| trim 后近似 tokens | 20013 |
| trim 后首条 | `old-8-...` |
| 最早 `old-0` 是否进入摘要源 | 否 |
| 配置上限 | 30000 |

由于消息边界与 `start_on=human`，结果不必恰好贴近 30k。**这已证实“摘要看不到全部被选旧历史”的覆盖缺口；是否造成最终任务质量下降仍待基准。** 当前 state 原文仍在，但主 inference window 不再直接携带未获救旧原文。

摘要静态模板本身另测为 600 o200k tokens，不含 `{messages}` 正文。

---

## 三、固定开销实测：单次调用各部分量级

### 3.1 测量口径

- 当前实际 config v32、默认 lead agent、无自定义 SOUL、默认启用技能。
- system 由真实 `apply_prompt_template` 构造；标准/Ultra 加真实 TodoList system；Ultra 加真实 subagent section。
- 工具由 `get_available_tools(include_mcp=False)` 构造，再经 `convert_to_openai_tool` 序列化；plan/Ultra 的中间件工具按真实装配补齐。
- 文本统一用 `prompt_audit` 的 o200k_base 编码器计数；`combined` 是对实际拼接文本再计数，不是把不同 tokenizer 的 32k 与其相加。
- 不含动态 MCP/ACP、日期、用户 memory、上传清单、用户消息、provider envelope、图片与响应；自定义 agent 的工具/技能/SOUL会改变结果。

### 3.2 默认 lead agent 三档结果

| 模式 | 工具数 | 基础 system | 技能目录块 | 完整 system | plan system | 工具 schema | 合计固定代理值 |
|---|---:|---:|---:|---:|---:|---:|---:|
| 闪速 | 15 | 2450 | 4245 | 6695 | 0 | 3710 | **10405** |
| 标准 | 16 | 2450 | 4245 | 6695 | 316 | 4436 | **11446** |
| Ultra | 17 | 2450 | 4245 | 7818 | 316 | 4875 | **13008** |

单位均为 o200k tokens。Ultra 完整 system 比另两档多 1123，对应 subagent section；标准比闪速多 plan system 与 `write_todos` schema；Ultra 再增加 `task` schema。

**解释**：当前 32k messages trigger 没有为这批约 10.4k / 11.4k / 13.0k 的固定结构显式留预算。由于计数器与 provider 序列化不同，不能写成“真实总输入恰为 32k + 表中值”；正确结论是**统一 32k 对三档意味着不同的实际窗口余量**。

### 3.3 SOUL

默认 lead agent 本次实测无 SOUL，块大小为 0。预置 agent 的 SOUL 独立块实测：

| Agent | o200k tokens |
|---|---:|
| creative-studio | 989 |
| podcast-studio | 998 |
| course-video | 2043 |
| co-writer | 2219 |
| vimax-video | 2317 |
| ppt-master | 2579 |

这些值不能直接加到默认 agent 的总表后冒充该专家 agent 总开销，因为其 skill 白名单、tool groups、plan/subagent 形态也可能不同；专家 agent 的完整单次调用矩阵待测。

### 3.4 技能“目录”与“正文”必须分开

本次 4245 是 system 内可用技能的名称、描述、路径等目录，不是各 `SKILL.md` 正文。正文经工具读取后进入消息历史，既可能推动 32k trigger，又可能受到最近 5 / 25k / 5k rescue 影响。故“降低 trigger”不能替代技能正文预算治理。

---

## 四、方案设计与最小改动面

### 4.1 选项比较

| 方案 | 优点 | 主要问题 | 建议 |
|---|---|---|---|
| A. 继续全局值，只把 32k 改成另一个全局数 | 改动最小 | 不解决三档固定开销和任务形态差异 | 仅作为对照臂 |
| B. 把摘要策略挂到每个 `ModelConfig` | 看似可按容量定制 | Claude 跨标准/Ultra、DeepSeek 跨闪速/标准，model 无法唯一决定 mode；花名册与策略耦合 | 不推荐 |
| C. `summarization.modes` 按三档覆盖三项策略 | 语义直接、花名册不动、可保留全局回退 | 需要一个纯策略解析与装配接缝 | **推荐** |
| D. 第一版就改 `fraction` | 理论上随窗口缩放 | 当前 fraction 依赖摘要中间件 model profile；第三方 OpenAI-compatible profile 不完整，且不是运行模型容量 | 暂缓；上游升级后重评 |
| E. 第一版就按“完整实际输入”动态计算 trigger | 最精确地处理固定开销 | provider tokenizer、tools、cache、动态块口径复杂，改动和验证面大 | 后续演进，不作最小批 |

### 4.2 推荐 schema（仅设计，未写入配置）

保留现有全局字段作为缺省与旧客户端回退，新增可选字典：

```yaml
summarization:
  # 现有 enabled/model_name/trigger/keep/trim/reuse/skill 配置保持不变
  modes:
    flash:
      trigger:
        - {type: tokens, value: 24000}
      keep: {type: tokens, value: 4000}
      trim_tokens_to_summarize: 16000
    standard:
      trigger:
        - {type: tokens, value: 32000}
      keep: {type: tokens, value: 6000}
      trim_tokens_to_summarize: 30000
    ultra:
      trigger:
        - {type: tokens, value: 48000}
      keep: {type: tokens, value: 8000}
      trim_tokens_to_summarize: 48000
```

**以上数字仅是 §5 的首轮实验臂，不是建议上线默认值。** schema 设计本身不要求同时启用三档；空 `modes: {}` 完全保持现状。

建议接口形态：

- 新增 `SummarizationModePolicy`，每档若出现则显式给出 `trigger`、`keep`、`trim_tokens_to_summarize`，避免“字段未写 = 继承”与“null = 禁用 trim”的三态歧义。
- `SummarizationConfig.modes: dict[ModeName, SummarizationModePolicy]` 默认空字典；复用 `ModelConfig` 已有的 `ModeName = flash/standard/ultra`。
- 一个纯函数根据 `global config + runtime mode` 返回 effective `trigger/keep/trim`；其余 enabled、摘要模型、reuse、skill rescue、ledger 全部沿用全局。
- `mode` 缺失或未知时使用现有全局策略；不要按 `model_name` 反推，不要因旧客户端缺 mode 而静默改成 standard 新策略。
- 当前 trim override 先限定为正整数；全局 `null` 的现有行为另案处理，不在最小批顺手改语义。

### 4.3 为什么无需前端或 `/models` 改动

前端已经在 `frontend/src/core/threads/hooks.ts:489-531` 发送 `context.mode`；gateway 已在 `backend/app/gateway/services.py:750-770` 的白名单透传 `mode`。lead-agent `_get_runtime_config` 已能读取它。

因此最小实现面（**仅立项后的预测，当前未改**）为：

| 文件 | 预期责任 |
|---|---|
| `backend/packages/harness/deerflow/config/summarization_config.py` | mode policy schema、校验与纯解析 |
| `backend/packages/harness/deerflow/agents/lead_agent/agent.py` | 从已有 runtime config 取 mode，构造 effective middleware 参数 |
| `backend/tests/test_summarization_middleware.py` 与一个聚焦 config 测试文件 | schema、回退、装配和边界回归 |
| `config.example.yaml` | 仅立项后加示例；结构性变更按约定 `config_version 32 → 33`，部署端走 `make config-upgrade` |

不需要改 `ModelConfig.modes`、`mode_defaults`、模型 API、前端提交字段、价格、密钥或数据库。

### 4.4 暂不扩张的范围

- 不同时按模式切换摘要模型；当前 `model_name:null → models[0]=qwen3.8-flash` 保持为控制变量。
- 不同时修改 reuse 20/30k、skill rescue 25k、summary prompt、token budget 或 tool output 截断。
- 不把 lead-only 的现状顺便扩成 subagent 全面摘要；上游 durable context 对 subagent 的衔接需独立评审。
- 不把“固定开销代理值”硬编码进 trigger；先取得 provider 真实 request/usage 观测。

---

## 五、三档策略建议与候选实验臂

### 5.1 方向性建议

| 模式 | 目标 | trigger 方向 | keep 方向 | trim 方向 | 主要风险 |
|---|---|---|---|---|---|
| 闪速 | 短任务响应与低成本 | 比 32k 更早做实验，减少主模型长历史 | 较小 token keep，避免 10 条消息内藏巨大工具输出 | 较小摘要输入预算 | 更早摘要可能多一次调用，反而更慢更贵；更小 trim 扩大覆盖缺口 |
| 标准 | 质量 / 成本平衡 | 先保留 32k 作锚点 | 中等 token keep，与消息数方案对照 | 先保持 30k | 改动过多会失去可归因性 |
| Ultra | 长程、多步骤、产物连续性 | 尝试更晚触发，保留更多直接上下文 | 较大 token keep，保护近期工具链 | 较大摘要输入预算，减少旧历史未入摘要比例 | 主模型输入与摘要输入都更贵；较晚触发可能撞上下文或提高 p95 |

token keep 仍是目标值而非绝对窗口上限：AI/Tool 配对、latest user 和技能 rescue 可使其超出。任何高档 trigger 必须先核对**该档全部可选模型**与摘要模型的实际输入容量，不得只看单一默认模型或宣传参数。

### 5.2 首轮候选值（全部待测）

| 模式 | trigger | keep | trim | 对照 |
|---|---|---|---|---|
| 闪速 | 24k tokens | 4k tokens | 16k | 当前 32k / 10 messages / 30k |
| 标准 | 32k tokens | 6k tokens | 30k | 当前 32k / 10 messages / 30k |
| Ultra | 48k tokens | 8k tokens | 48k | 当前 32k / 10 messages / 30k |

这些值只用于把假设变成可运行实验，不具有“实测更优”地位。若只做一轮全组合对比，即使赢了也无法知道是哪一项起效，所以应按 §7 分阶段调参。

### 5.3 不建议现在拍板固定数值的原因

- 没有真实摘要质量与任务成功率基线。
- 固定开销仅是 o200k 代理，不是 provider 账单或窗口占用真值。
- 当前 trim 有覆盖缺口，单独提高 trigger 或降低 trim 可能放大质量问题。
- 当前 reuse tail 增长、skill rescue 与单条超大 ToolMessage 都会让实际窗口偏离简单阈值直觉。
- 上游 tool-output externalization 与 durable references 会改变输入分布，升级前的最优值可能升级后不再最优。

---

## 六、与上游 harness 2.2.0 的衔接

### 6.1 不是可直接平移内部实现的同构升级

上游一手资料：[DeerFlow 2.2.0 release](https://github.com/bytedance/deer-flow/discussions/7896)、[v2.2.0 summarization 文档](https://github.com/bytedance/deer-flow/blob/v2.2.0/backend/docs/summarization.md)、[DurableContextMiddleware](https://github.com/bytedance/deer-flow/blob/v2.2.0/backend/packages/harness/deerflow/agents/middlewares/durable_context_middleware.py)。

| 维度 | 本仓当前 | 上游 v2.2.0 | 对本方案影响 |
|---|---|---|---|
| 摘要状态 | inference-window-only；完整 `state.messages` 保留；`summarization_checkpoint` | `ThreadState.summary_text`；原始消息通道会 compaction；durable context 再投影 summary | 不能把当前 checkpoint 内部结构当升级后稳定 API |
| 技能连续性 | rescue 最近原文 + 临时 skill ledger | checkpointed `skill_context` 引用，不再使用 `preserve_recent_skill_*` | 三档首版不要改 skill 数值；升级后删除旧映射假设 |
| 其他 durable 信息 | 当前无等价完整层 | goal、delegation ledger、artifact handles、task notes/history 与 summary 一起投影 | Ultra 质量收益可能主要来自 durable references，而非单纯提高 trigger |
| trim | 对选区取尾部，一次摘要调用；30k | 原始历史按时间顺序有界分块，carry 最多一半；覆盖所选 source；每 pass 最多 64 chunks | `trim` 的语义会变，升级前后结果不可直接横比 |
| `model_name:null` | 当前取 `models[0]` | 取实际 run model，并有配置摘要模型失败时 run-model fallback | 成本与 token counter 归属都会改变，需重测 |
| fraction | 基于摘要中间件 model profile | 基于实际 run model 的声明 `context_window`，缺 profile 时降级 | 升级后可重新评估 fraction，当前不采用 |
| no-op 优化 | 当前 reuse 20/30k | 另有 exact-rendered-prompt no-op LRU 与 skip telemetry | 摘要调用成本口径不同，需记录 skipped/no-op |

### 6.2 工具输出瘦身的影响

上游 v2.2.0 `tool_output` 默认开启：超过 12k 字符可外置完整结果，模型只看有界 preview + 引用；持久化不可用时按 30k fallback 截断。结构化 synopsis 也做了聚合预算；superseded-write elision 默认关闭，以避免重写旧请求前缀、破坏 prompt cache。

一手资料：[tool_output_config.py](https://github.com/bytedance/deer-flow/blob/v2.2.0/backend/packages/harness/deerflow/config/tool_output_config.py)、[PR #6422](https://github.com/bytedance/deer-flow/pull/6422)、[PR #7455](https://github.com/bytedance/deer-flow/pull/7455)。

对本方案的实际影响：

- 单条工具结果进入 messages 的体量会下降，达到同一 trigger 的时间可能延后。
- “更少摘要”不一定来自更高 trigger，也可能来自工具输出治理；基准必须记录 tool-output 版本与配置。
- durable artifact handles 减少路径、任务 ID 在摘要后丢失的风险；这可能比增加 keep 更有效。
- 默认不重写旧前缀意味着“原始 token 更少”与“prompt cache 更好”要分别计量，不能只报总 token。

### 6.3 推荐衔接次序

1. **策略数据保持可迁移**：无论当前还是 2.2.0，effective policy 都只产出 `trigger/keep/trim`，不要把 `summarization_checkpoint` 或 skill rescue 内部字段塞进 mode schema。
2. **若先升级 harness**：在新 `SummarizationConfig` 上接同一个 mode resolver；删除旧 preserve 配置假设，重跑全部边界、固定开销与质量基准。
3. **若先做本方案**：只作为实验配置落地；harness 升级时必须重审 adapter，不宣称配置值无需复测。
4. **观测优先采用上游思路**：上游 [PR #6236](https://github.com/bytedance/deer-flow/pull/6236) 已把 request message/tools chars、caller、provider、model 与 usage 分到每次 LLM 调用；本仓后续基准则应记录同等维度。
5. **不夹带 durable-context backport**：它是一套独立状态与安全契约，不属于“三档阈值最小批”。

---

## 七、验证计划：怎样证明某组阈值更优

### 7.1 先离线，再决定是否值得付费

#### A. 实现级回归（立项后 RED → GREEN）

- [ ] 空 `modes`、缺失/未知 mode 完全沿用全局；三档精确命中各自策略。
- [ ] Claude 与 DeepSeek 跨档时仍按显式 mode，而非 model_name 推断。
- [ ] 每档 trigger 在阈值 ±1 的 approximate 与 provider-reported 路径行为明确；provider mismatch 不误触发。
- [ ] system、工具 schema 不被静默重复计数；DynamicContext / ToolMessage 的计数口径有测试。
- [ ] message/token keep 的 AI/Tool 配对、latest user、skill rescue、≤keep 巨大消息均有覆盖。
- [ ] fresh/reuse/pass-through、冻结 cutoff、20/30k 增长边界与摘要失败不残留 checkpoint。
- [ ] trim 前后 source coverage 被记录；最早/中间/最近预声明事实的覆盖可检查，不只断言“调用了 summary model”。
- [ ] 配置 schema 拒绝非正数、非法 fraction、bool-as-int；若实施则验证 v32→v33 正式迁移。

#### B. 免费合成 replay 基准

建立可重复消息夹具，不调用模型即可先淘汰坏策略：

- 文本形态：ASCII、中文、代码、JSON、单行长工具输出、多条中等输出、单个超大输出。
- 事实位置：最早、中部、最近各放置预声明约束；另放产物路径、工具调用 ID、用户最新修订与拒绝项。
- 历史长度：trigger 前、刚越线、2×、4×；reuse 增长前后；不足 keep 但 token 巨大。
- 技能形态：目录不进 messages、正文进 messages、rescue 预算内/外、ledger 保留指针。
- 断言：trigger 时点、cutoff、进入摘要源的消息集合、保留窗口、state 原文、pair 完整性与窗口 token 曲线。

这一层能证明机制与 source coverage，**不能证明生成摘要的语义质量**。

### 7.2 真实模型配对基准（另行授权，当前未运行）

#### 样本分层

每个模式内部做同任务、同模型、同工具、同历史、同推理参数的 baseline vs candidate；不把闪速和 Ultra 的绝对成绩直接互比。

| 任务族 | 主要检验 |
|---|---|
| 轻量问答 / 改写 / 抽取 | 闪速是否因额外资要调用反而变慢；短历史是否无意义触发 |
| 多来源研究 | 引用、用户约束、已拒绝选项在多次压缩后是否保留 |
| PPT / 长文档 / 代码产物 | 绝对路径、已完成步骤、当前版本、下一步是否连续；是否重复劳动 |
| 多工具与 Ultra 委派 | delegation 结果、产物引用、AI/Tool 配对、子代理成本与 p95 延迟 |

建议 pilot：每模式 6 个配对任务 × 2 次重复 × 2 臂，三档共 **72 runs**。pilot 只用于估计方差、失败模式和成本，**不得宣称统计证明**。确认样本量应根据 pilot 的主指标方差与希望检测的效果量做 power analysis；关键质量差异还需足够离散成败样本，不能用 6 个任务推出 2 个百分点结论。

上游 [context_snapshot benchmark](https://github.com/bytedance/deer-flow/blob/v2.2.0/backend/scripts/benchmark/context_snapshot/README.md) 可复用其“预声明检查、配对运行、完整计入失败成本、缺 usage 不当 0”的方法，但它的历史样本只有 8 对、一个模型，而且明确不是通用质量证明，不能直接当本方案证据。

### 7.3 指标

| 类别 | 必记指标 |
|---|---|
| 质量 | 预声明关键检查全过率、`clean_success`、关键约束/产物引用丢失数、重复完成步骤数、人工盲评（仅抽样） |
| 上下文安全 | provider context-length 错误、orphan tool calls、latest user 丢失、pass-through 次数、post-window tokens、source coverage |
| 摘要行为 | fresh/reuse/no-op/skip/failure 次数、每 run 摘要调用数、距首次 trigger 的 messages/tokens、reuse tail 峰值 |
| 成本 | 主模型 + 摘要 + 子代理 + fallback + 重试的 input/output/cache tokens；按当前价目快照换算费用 |
| 延迟 | 首 token 延迟、run 总时长 p50/p95、摘要附加耗时；失败与 capped run 也计入 |
| 缓存 | provider 可用时记录 cache read/write 与前缀失效；不可得写未知，不填 0 |

成本公式：`run_cost = Σ each_llm_call(provider_usage × applicable_price)`，按模型和输入/输出/缓存类型分别计；usage 或价目缺失时该分量为 `null`，不能按 0 宣称更便宜。除货币成本外同时报告总 tokens，避免价目变化伪装成算法收益。

### 7.4 实验控制与判定

- 同一 pair 固定历史、模型、工具、skill 集、temperature/seed、输出预算和并发；随机化两臂执行顺序。
- 记录 git SHA、config snapshot 哈希、依赖版本、harness/tool-output 版本；不同版本结果不混池。
- 每模式独立比较，报告 paired delta 与置信区间；离散成败用配对方法，token/时延报告均值之外的中位数与尾部。
- **推荐验收门槛（待评审拍板）**：所有关键安全检查不回归；质量非劣边界在跑确认实验前写明；若质量非劣，配对中位总 tokens 或费用至少下降 10%，且 p95 总时延不增加超过 10%，才称“有实际收益”。10% 是产品验收建议，不是本次实测效果。
- 若只省摘要 tokens、却增加主模型输入/重试或降低成功率，不判优；若只更快但关键事实丢失，也不判优。
- 多模式、多候选比较需控制多重比较；pilot 后冻结一个 champion，再用独立样本确认，避免反复挑最好的一次。

### 7.5 成本控制

1. 离线 replay 与 mock summary 全绿后才申请 live 预算。
2. 72-run pilot 前先按每类任务的最大输入/输出预算和当时价目算**最坏费用上限**，由用户确认总额与每 run cap；本文没有核验价目，故不伪造金额。
3. 分模式串行解锁，先闪速/标准，再 Ultra；出现关键质量回归、usage 不完整或费用 cap 即停。
4. provider retries、summary chunks、subagents、失败/capped runs 全计入，不能删差样本。
5. 最终上线默认值需要独立确认集与灰度回滚值；pilot champion 不是自动默认。

---

## 八、评审决策记录（grilling 后的推荐答案）

| Frontier 问题 | 推荐答案 | 当前状态 |
|---|---|---|
| 首要目标是“最少摘要”还是“任务连续”？ | 质量与关键安全非劣在先，其次才是成本/时延 | 待用户确认 |
| 按 model 还是按 mode？ | 按显式 mode；共享模型使 model 推断有歧义 | 代码事实支持，方案推荐 |
| 旧客户端无 mode 怎么办？ | 回退现有全局策略，不静默套 standard 新值 | 方案推荐 |
| 是否现在采用 24k/32k/48k 等固定默认值？ | 否，只作实验臂 | **待基准** |
| keep 是否从 messages 改 tokens？ | 把 token keep 作为候选，与 10 messages 对照；不是无条件替换 | 待基准 |
| 是否同时改摘要模型、reuse、skill rescue？ | 否，先固定控制变量 | 方案边界 |
| 是否第一版直接计完整 provider 输入？ | 否，先观测 request/usage；最小批保留现有计数机制 | 后续演进 |
| 当前实现还是 2.2.0 上落地？ | resolver 保持薄且可迁移；harness 升级后实施并重测 | **已拍板：harness 升级先行** |
| 工具输出瘦身是否意味着无需分档？ | 不一定；它改变历史分布，需作为实验协变量重新测 | 待基准 |
| 多少 live 样本和预算？ | 先 72-run pilot 估方差与失败模式，再 power analysis；预算另行批准 | 待用户批准 |

立项与 harness 升级先后已闭合：本方案已批准，排在 harness 升级之后。剩余 frontier 是质量非劣边界、最小有意义收益与 live 预算。事实问题不需要用户代替代码或实验回答。

---

## 九、建议的评审后流程

- [x] 用户已批准立项，并拍板 harness 升级先行；实施前复检漂移。
- [ ] 冻结质量非劣边界、最小收益与 live 预算；未批准付费前只做离线。
- [ ] 先完成免费 replay 和观测基线，淘汰有 source coverage / pair / pass-through 问题的候选。
- [ ] 单变量阶段：固定 keep=10 messages、trim=30k，逐档比较 trigger；再调 keep；最后调 trim。
- [ ] 组合 champion 与全局 32k / 10 messages / 30k 在独立配对样本确认。
- [ ] 若组合未达预设收益，保留全局策略并关闭实验，不为“已投入成本”强行上线。
- [ ] 若达标，再另写实施计划：独立 worktree、RED→GREEN、config_version 32→33、全量测试与 PR；不部署、不动价格密钥。
- [ ] harness 或 tool-output 版本变化后重跑基准，不继承旧“最优值”结论。

---

## 十、证据索引

### 当前仓

- `backend/packages/harness/deerflow/config/summarization_config.py`
- `backend/packages/harness/deerflow/agents/lead_agent/agent.py`
- `backend/packages/harness/deerflow/agents/middlewares/summarization_middleware.py`
- `backend/tests/test_summarization_middleware.py`
- `backend/.venv/lib/python3.12/site-packages/langchain/agents/middleware/summarization.py`
- `backend/.venv/lib/python3.12/site-packages/langchain/agents/middleware/types.py`
- `backend/.venv/lib/python3.12/site-packages/langchain/agents/factory.py`
- `skills/public/ppt-master/scripts/prompt_audit.py`
- `frontend/src/core/threads/hooks.ts`、`backend/app/gateway/services.py`

### 上游一手资料

- [DeerFlow 2.2.0 release](https://github.com/bytedance/deer-flow/discussions/7896)
- [v2.2.0 summarization.md](https://github.com/bytedance/deer-flow/blob/v2.2.0/backend/docs/summarization.md)
- [v2.2.0 summarization_config.py](https://github.com/bytedance/deer-flow/blob/v2.2.0/backend/packages/harness/deerflow/config/summarization_config.py)
- [v2.2.0 durable_context_middleware.py](https://github.com/bytedance/deer-flow/blob/v2.2.0/backend/packages/harness/deerflow/agents/middlewares/durable_context_middleware.py)
- [v2.2.0 tool_output_config.py](https://github.com/bytedance/deer-flow/blob/v2.2.0/backend/packages/harness/deerflow/config/tool_output_config.py)
- [PR #4929：artifact handles](https://github.com/bytedance/deer-flow/pull/4929)
- [PR #6236：per-call telemetry](https://github.com/bytedance/deer-flow/pull/6236)
- [PR #6318：summary no-op skip](https://github.com/bytedance/deer-flow/pull/6318)
- [PR #6422：bounded structured previews](https://github.com/bytedance/deer-flow/pull/6422)
- [PR #7455：preserve prompt prefixes](https://github.com/bytedance/deer-flow/pull/7455)
- [context_snapshot benchmark](https://github.com/bytedance/deer-flow/blob/v2.2.0/backend/scripts/benchmark/context_snapshot/README.md)
