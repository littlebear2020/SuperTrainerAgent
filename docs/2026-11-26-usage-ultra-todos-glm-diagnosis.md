# 个人用量、Ultra、To-dos 与 GLM 5.4 问题诊断报告

> **日期**：2026-11-26  
> **来源**：用户测试反馈 + 当前工作区代码走查 + 无密钥最小复现/定向测试  
> **用途**：交给 Claude 继续研究并制定实施计划；本文是诊断交接，不是已批准的实施计划，也不代表问题已修复。

> **TL;DR**：共三项缺陷与一项模型接入需求。删除作品漏计历史费用已通过临时数据库复现；Ultra 开关链路正常，但子代理策略默认直接执行，与“多子代理协作”预期不一致；To-dos 实时流式链路经真实 Agent 图探针验证可用，实测 run 是否批量补写仍需补证；`z-ai/glm-5.4` 需单独明确档位、能力与定价后接入。

## 一、问题总览

| 编号 | 类型 | 用户可见现象 | 当前诊断状态 | 建议优先级 |
|---|---|---|---|---|
| BUG-01 | 费用统计完整性 | 删除【我的作品】任务后，【我的用量】同步扣除该任务费用与 Token | **根因已确认，最小数据库复现完成** | P1 |
| BUG-02 | 模式策略与产品预期 | 多个任务开启 Ultra 后均未看到子代理协作，Ultra 感知不明显 | **开关与工具链正常；委派策略偏保守已确认，实测 run 配置仍需补证** | P2 |
| BUG-03 | 执行进度可观测性 | 任务执行期间 To-dos 长期不更新，run 结束时才刷新 | **实时更新机制可用；实测 run 的 `write_todos` 时机尚未取得，不能直接归因 UI** | P2 |
| FEAT-01 | 模型接入需求 | 希望加入 360 通道的 `z-ai/glm-5.4` | **需求已记录，档位、能力与定价待明确** | 待评估 |

对应 backlog：`docs/findings-backlog.md:83-86`。

---

## 二、BUG-01：删除作品后历史费用与 Token 被漏计

### 2.1 用户反馈

用户可以在【我的作品】中删除任务，删除本身符合预期；但删除后【我的用量】的总费用也随之减少，已经实际发生的花费从统计中消失，导致费用维度不完整。

### 2.2 期望与实际

**期望：**

- 删除作品可以清理作品展示与产物，不应撤销已发生的费用。
- 历史总费用、总 Token 应保持不变。
- 推荐用量明细保留并标记“作品已删除”；至少总账必须继续计入。

**实际：**

- 任务从用量列表消失。
- 总费用扣除该任务花费，Token 同步减少。
- 如果窗口内只有该任务，费用和 Token 会从有值变为 `null`。

### 2.3 根因链路

1. 前端删除视频任务：`frontend/src/core/video-gen/api.ts:91`。
2. 后端进入任务删除服务：`backend/app/gateway/_shared/task_framework/service.py:337`。
3. Repository 物理删除 `unified_tasks` 主行：`backend/app/gateway/_shared/task_framework/repository.py:213`。
4. 个人用量却先从当前仍存在的 `TaskRow` 出发：`backend/app/gateway/_shared/personal_usage/video.py:21`。
5. 再用这些存活任务 ID 过滤 LLM 与媒体明细：`backend/app/gateway/_shared/personal_usage/video.py:35-64`。
6. 汇总只累加前面返回的任务：`backend/app/gateway/_shared/personal_usage/service.py:18-35`、`:48-59`。

因此实际过程为：

```text
删除作品
→ unified_tasks 主行被物理删除
→ 用量聚合失去任务入口
→ 仍存在的 LLM/媒体明细不再被归组
→ 总费用、Token、任务数一起下降
```

### 2.4 最小复现证据

使用真实 `get_personal_usage` 与 `TaskRepository`，在临时 SQLite 中构造一项含 LLM 费用和媒体费用的 AI 生图任务：

| 指标 | 删除前 | 删除后 |
|---|---:|---:|
| 用量任务数 | 1 | 0 |
| 总费用 | 0.504 CNY | `null` |
| 总 Token | 200 | `null` |
| `unified_tasks` 行数 | 1 | 0 |
| LLM 用量明细行数 | 1 | **1** |
| 媒体费用明细行数 | 1 | **1** |

**结论：费用明细没有被删除，是统计查询把它们过滤掉了。**这不是正常“同步”，也不是前端缓存问题。

### 2.5 影响范围与恢复边界

- 已直接确认 AI 生图/AI 视频任务受影响。
- Agent 用量同样先读取 `threads_meta`：`backend/app/gateway/_shared/personal_usage/agent.py:247-290`；删除会话会删除该元数据：`backend/app/gateway/routers/threads.py:263-269`，存在同构漏计风险。
- 在已验证的直接作品删除链路中，费用明细仍保留，历史费用有恢复统计的基础。
- 已删任务的标题、状态、类型等主表元数据不可直接依赖，修复需处理孤儿账本 ID、能力筛选与降级展示，不能只简单去掉 `TaskRow` 查询。

### 2.6 Claude 研究与计划需回答

- 采用“账本优先 + 任务元数据补充”，还是“任务软删除 + 所有作品/调度查询过滤删除态”，或二者组合？需评估核心任务仓库的 blast radius。
- 已删除任务在用量明细中如何展示与筛选？任务类型快照从哪里获得？
- 无费用但处于窗口内的现存任务是否继续显示，避免修复后改变 `total_tasks` 语义？
- 如何防止 LLM/媒体账本重复计数，并保持分页时 summary 与全量一致？
- Agent 会话是否纳入同批修复与回归？

### 2.7 验收底线

- 创建有费用的任务 → 记录用量 → 删除作品 → 再查询，总费用与 Token 不变。
- AI 生图、AI 视频、Agent 会话均有删除后的行为级回归。
- 已删除任务的能力筛选仍正确，不能因主表缺失而变成不可分类或重复统计。
- 作品列表仍不展示已删除作品，产物删除行为不被迫回退。

---

## 三、BUG-02：Ultra 多次未出现子代理协作

### 3.1 用户反馈

多个任务均开启 Ultra，但执行过程中每次都未看到 Ultra 的多子代理能力被使用，怀疑触发条件异常。

### 3.2 已确认的开关链路

- 前端 Ultra 会发送 `subagent_enabled: true`：`frontend/src/core/threads/hooks.ts:883-930`。
- 后端白名单转发该参数：`backend/app/gateway/services.py:754-770`。
- Agent 读取并用于工具装配：`backend/packages/harness/deerflow/agents/lead_agent/agent.py:434`、`:546-550`。
- `subagent_enabled=true` 时确实挂载 `task` 工具：`backend/packages/harness/deerflow/tools/tools.py:91-94`。

无密钥探针结果：

```text
标准模式包含 task 工具：false
Ultra 模式包含 task 工具：true
```

因此，当前代码没有显示“Ultra 开关断了”或“工具完全没挂载”的证据。

### 3.3 真正限制委派的条件

Ultra 只保证“可以调用子代理”，不保证“每个任务都调用”。提示词明确要求：

- `Subagents are optional. Default to direct execution.`
- 不能仅因任务复杂、步骤多、输出长而委派。
- 只有预期收益明显大于成本时才委派。
- 不确定时直接执行。

位置：`backend/packages/harness/deerflow/agents/lead_agent/prompt.py:266-397`，核心约束在 `:352-364`。

现有测试还明确固定了“默认直接执行”的策略：`backend/tests/test_lead_agent_prompt.py:251-272`。这说明它更像**既有编排策略与新产品文案未对齐**，而非偶然布尔条件写错。

### 3.4 Ultra 感知不明显的放大因素

当前提交参数中：

| 参数 | 标准 | Ultra |
|---|---|---|
| `reasoning_effort` | `high` | `high` |
| `is_plan_mode` | `true` | `true` |
| `subagent_enabled` | `false` | `true` |
| `recursion_limit` | 2500 | 2500 |

位置：`frontend/src/core/threads/hooks.ts:919-930`。

模型花名册可以不同，但执行机制上 Ultra 独有的是“允许子代理”。若模型不委派，标准与 Ultra 的用户可见行为非常接近。设计文档 `docs/plans/2026-11-18-model-mode-ladder-plan.md:17-34` 提到 Ultra“更长执行预算”，当前提交链却统一为 2500，需要核对是未落地还是设计已变更。

### 3.5 已完成的验证

- 后端参数、Agent 配置、提示词与任务事件定向测试：**5 passed**。
- 前端 Ultra 提交参数测试：**6 passed**。
- 无密钥探针：Ultra 工具存在，保守委派条件也同时存在。
- 未调用真实付费模型；未取得用户那几次测试 run 的原始上下文与工具调用日志。

因此可以确认“策略允许多次零委派”，但不能在没有 run 记录时断言每次测试都一定是同一原因，仍需排除部署版本或运行参数差异。

### 3.6 Claude 研究与计划需回答

产品必须先二选一或分层定义：

1. **按需协作**：UI 明确“根据任务需要调用子代理”，不承诺每次使用；补充“已启用/本次未调用”的可观测反馈。
2. **明确场景稳定委派**：定义可验证的触发场景，例如两个以上相互独立的工作分支、可并行调研、重上下文隔离、特定工作流明确委派；满足条件时至少产生一次有效 `task` 调用。

还需决定：

- 是否保留简单任务直接执行，避免为了展示 Ultra 而强制所有任务增加费用和耗时。
- “更长执行预算”是补实现还是修订文档。
- 如何区分“未开启、已开启但未委派、已委派但失败、已委派但 UI 未展示”。
- 是否需要按模型分别评估触发率，而不是只测参数。

### 3.7 验收底线

- 对一个明确包含多个独立子任务的测试场景，目标模型在 Ultra 下实际产生符合预期的 `task` 调用，而非仅断言 `subagent_enabled=true`。
- 简单任务仍可直接执行，不强制无意义委派。
- UI 文案与实际“按需/稳定委派”语义一致。
- 日志或运行记录能解释本次 Ultra 是否开启及为何没有子代理。
- 若保留“更长执行预算”设计，需有模式级参数断言；否则删除过时口径。

---

## 四、BUG-03：To-dos 执行中不更新，结束后才刷新

### 4.1 用户反馈与截图现象

截图中，任务已经执行了多轮工具调用并进入后续场景处理，但 To-dos 第一项仍保持原状态。用户观察到：只有任务执行完后，To-dos 才集中更新。

### 4.2 数据链路

- To-dos UI 直接读取 `thread.values.todos`：`frontend/src/app/workspace/chats/[thread_id]/page.tsx:267-272`；Agent 聊天页也有同样形态：`frontend/src/app/workspace/agents/[agent_name]/chats/[thread_id]/page.tsx:343-349`。
- 组件只按 `pending/in_progress/completed` 渲染：`frontend/src/components/workspace/todo-list.tsx:75-89`，自身不推断真实工具进度。
- 状态由模型调用 `write_todos` 改变，不是后端自动根据每个业务工具完成而推进。
- 提示词已要求“每步完成立即更新、不要批量补完成、实时更新”：`backend/packages/harness/deerflow/agents/lead_agent/agent.py:141-150`、`:199-234`。
- 后端 `values` 模式逐个快照发布：`backend/packages/harness/deerflow/runtime/runs/worker.py:310-339`。

### 4.3 真实 Agent 图探针

使用按顺序调用 `write_todos` 的假模型，运行真实 `create_agent + TodoMiddleware + ThreadState`，在最终回复前已经收到：

```text
[in_progress, pending]
→ [completed, in_progress]
→ [completed, completed]
```

这证明：**当模型及时调用 `write_todos` 时，底层 Agent 图能够在终态前产生中间 To-dos 快照。**前端 SDK 也会在每次 `values` 事件时更新流状态，并非只在 `onFinish` 更新。

### 4.4 当前诊断边界

当前最可能的机制是：实测 run 中模型直到后期/结束才批量调用 `write_todos`，UI 只是忠实显示最后一次收到的状态。

但本次没有取得截图对应 run 的原始事件序列，**不能仅凭截图 100% 排除部署端 SSE、序列化或前端更新异常**。在制定修复计划前，应补齐以下同一时间轴证据：

```text
模型 write_todos 调用
→ ToolMessage / state 更新
→ 后端 values SSE 发布时间
→ 浏览器收到事件时间
→ React 渲染时间
```

若中间 `write_todos` 早已发生，再转查 SSE/前端；若未发生，修复应落在 Agent 行为保障与测试，而不是强行增加结束外刷新。

### 4.5 Claude 研究与计划需回答

- 如何取得截图对应 run 的工具调用和 values 事件，先完成事件源归因？
- 对“每步完成立即更新”仅靠提示词是否足够？是否需要可观测提醒、中间件约束或技能工作流配合？
- 如何定义一步“完成”，避免把未完成步骤提前标记 completed？
- 并行任务是否允许多个 `in_progress`，与当前提示词和 UI 是否一致？
- 普通聊天页与 Agent 聊天页是否都覆盖行为级回归？

### 4.6 验收底线

- 多步骤任务在最终回复前，界面能依次展示至少一个中间状态迁移，而非只在结束时全部完成。
- 状态更新与实际步骤完成对应，不提前伪报完成。
- 非并行时保持合理的单 `in_progress`；并行场景按明确规则展示。
- 测试同时覆盖 `write_todos` 时机、`values` 中间事件和 UI 渲染，而非只测 reducer 或最终状态。

---

## 五、FEAT-01：接入 360 通道 `z-ai/glm-5.4`

### 5.1 用户需求

在可选模型中加入 `z-ai/glm-5.4`，用户说明它也是 360 通道模型。本项是新增接入需求，不是上述三项缺陷的附属修复。

### 5.2 当前不可跳过的事实核查

“360 通道”来自用户提供的信息；当前调查尚未通过真实 360 清单或付费调用验证该上游 ID 的可用性。计划中应明确验证，不应仅凭模型名推断能力。

需确认：

- 上游 ID 是否精确为 `z-ai/glm-5.4`，内部模型名与展示名分别是什么。
- 使用哪个已有 360 OpenAI 兼容 factory/配置形态，是否需要额外参数适配；不得假设新依赖已存在。
- 放入 `flash/standard/ultra` 中哪些模式，是可选模型还是某档默认；用户尚未指定，不应擅自设为默认。
- `supports_thinking`、`supports_reasoning_effort`、`supports_vision` 的真实能力，以及 thinking 开关注入参数是否被上游接受。
- 流式输出、原生工具调用、超长上下文、错误与超时形态。
- 模型计价：数据库 `model_pricing` 是运行时定价来源；`config.yaml` 的 `models[*].pricing` 只是启动兜底，避免“可选但长期未计价”。
- 配置同步：`config.yaml` 与 `config.example.yaml` 保持一致；**只有结构性配置改动才按仓库约定递增 `config_version`**，不要把新增模型与 schema 变更混为一谈。

### 5.3 主要代码与测试指针

- 当前模式与模型设计：`docs/plans/2026-11-18-model-mode-ladder-plan.md`。
- 实际配置：`config.yaml`、`config.example.yaml`。
- 模式配置测试：`backend/tests/test_model_modes_config.py`。
- 前端过滤/回落：`frontend/src/core/models/modes.ts` 及对应 `frontend/tests/unit/core/models/modes.test.ts`、`frontend/tests/unit/components/workspace/input-box-mode-models.dom.test.tsx`。
- 提交参数：`frontend/src/core/threads/hooks.ts:883-930`。

### 5.4 验收底线

- 模型只出现在指定档位，切档时合法模型保留、非法模型正确回落，默认徽标与 `mode_defaults` 一致。
- 实际调用使用预期 360 上游 ID，不发生静默回退到其他模型。
- thinking/effort 参数与模型能力匹配，不出现不支持参数导致的 400。
- 流式响应与至少一次真实工具调用成功；真实付费验证须遵守 `RUN_LIVE=1` 显式 opt-in，不能默认执行。
- Token 与费用能入账；若无法定价，必须显式可见，不能静默按零成本处理。
- 配置、API 清单、前端选择器和测试口径一致。

---

## 六、验证证据汇总与限制

| 项目 | 已执行验证 | 关键结果 |
|---|---|---|
| BUG-01 | 临时 SQLite + 真实个人用量聚合 + Repository 删除 | 删除后任务/费用/Token 消失，但 LLM 与媒体明细各保留 1 行 |
| BUG-02 | 后端 5 条定向测试 | `5 passed` |
| BUG-02 | 前端 `thread-submit-mode-context.dom.test.tsx` | `6 passed` |
| BUG-02 | 无密钥工具与提示词探针 | Ultra 有 `task`；提示词同时包含默认直接执行等保守条件 |
| BUG-03 | 真实 Agent 图 + 假模型顺序调用 `write_todos` | 终态前出现两种中间状态，最终才全部完成 |

**限制：**

- 未运行全量 backend/frontend 测试，因为本次没有修改业务实现。
- 未调用真实付费 API，未做测试服 run 级日志取证。
- 未读取或修改任何密钥配置。
- 定向测试证明现有接线符合其测试契约，不等于用户实测场景已完成端到端归因。

---

## 七、交给 Claude 的计划制定输入

### 7.1 建议先补的关键证据

1. BUG-02：抽取至少一个“Ultra 未使用”的真实 run，核对请求 context、Agent 创建日志、工具清单和最终工具调用。
2. BUG-03：抽取截图对应 run 的 `write_todos` 与 values 事件时间轴，先判断模型行为还是传输/渲染问题。
3. FEAT-01：通过受控配置或显式 live 探针确认 360 上游模型与能力，不先做默认档切换。

### 7.2 计划必须显式决策的产品问题

- 删除作品后，用量列表是保留“已删除”明细还是仅保留总账？
- Ultra 是“按需协作”还是“明确场景稳定协作”？哪些场景必须委派？
- Ultra 是否需要区别于标准档的执行预算？
- To-dos 的“及时”如何定义，如何防止提前完成与最终批量补写？
- GLM 5.4 属于哪些模式，是否仅作可选模型，定价与能力来源是什么？

### 7.3 实施拆分建议

四项不要混成一笔提交：

- 用量账本与删除语义：后端统计与数据模型风险最高，独立批次。
- Ultra 委派策略与可观测性：涉及 Agent 提示词/中间件与行为评估，独立批次。
- To-dos 实时性：先取证再决定 Agent、SSE 或前端改动，独立批次。
- GLM 5.4：模型配置、能力、定价与 live 验收独立批次。

若形成并行计划，需遵守仓库 `AGENTS.md` 的 allow/ban/共享基座三栏与路径重叠检查；功能类计划定稿前按约定执行 grilling。计划本身应另行放入 `docs/plans/` 或 `docs/plans/pending/`，使用复选框维护执行状态，本文不替代该计划。

### 7.4 共同回归要求

- 每项至少包含一条用户可感知的行为级断言，不能只验证配置值或函数签名。
- 真实付费 API 只在显式 opt-in 后运行，不能以 mock 通过冒充 live 验收。
- 记录实际命令与关键输出；明确哪些已证实、哪些仍待测试服验证。
- 不通过禁止删除作品、强制所有任务委派或伪造 To-dos 完成态来规避根因。
