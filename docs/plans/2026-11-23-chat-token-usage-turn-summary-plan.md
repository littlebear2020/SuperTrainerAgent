# 对话任务 Token 明细按轮汇总修复计划

> **状态**：已执行（离线验收；测试服验收待批准部署）；定稿日 2026-11-23（同日经评审修订一轮，决策见 §二「评审决策记录」）；基准 commit `52c316763dc15e60649f1dc6eb0faf52b50013d4`；执行日 2026-11-23；[changelog](../changelog/changelog-2026-11-23-chat-token-usage-turn-summary.md)。未部署，prepend 像素滚动锚定既有失败及口径限制见 changelog。
> **TL;DR**：多行明细的直接原因是前端对同一任务中的每条 AI 消息各渲染一行 usage（`token_usage.enabled` 默认开启，全员可见），而非必须依赖后端重复事件。修复只改共享消息展示层：以可见 human 消息划分一轮，运行结束后对该轮各组内的 AI 消息按对象引用去重求和，经 `renderAfterGroup` 在该轮最后一个可渲染组后只渲染一行；关键决策 = 单一渲染出口 + 流式期不计算 + 口径不换（仅主线程可见消息，不含子代理与中间件调用，列为已知局限）；影响面 = `frontend/` 消息展示层与测试，不动后端、数据库、价格、顶部总会话徽章与部署。
> **For agentic workers：** REQUIRED SUB-SKILL：用户批准后使用 `executing-plans` 单执行者按 Task 0→4 串行执行并逐项勾选，先以 `test-driven-development` 保存真实 RED，再做最小实现。只允许一个分支和一个 PR；不得并行派发，不得自动部署或重新运行付费制作。

**Goal：** 同一轮 Agent 任务无论发生多少次模型调用、产生多少个工具/产物消息组，完成后都只显示一行 Token 汇总；不同用户任务轮次仍分别显示，数值等于修复前该轮各行已返回的原始数值之和（同一条消息跨组只计一次）；部分调用没返回 usage 时在汇总行末标出次数，不静默偏低；汇总行不遮挡消息工具栏，流式期间不增加计算开销。

**Architecture：** 保留现有 `usage_metadata`、消息分组合并和历史/流式数据链。在 `core/messages/usage.ts` 新增纯函数 `summarizeTurnUsage(groups)`：按 `human` 组划轮，轮内遍历各组的 AI 消息，按**对象引用**去重后用 `getUsageMetadata` 求和，返回 `Map<原始组索引, TurnUsage>`（锚点 = 轮内最后一个可渲染组）。`MessageList` 仅在 `tokenUsageEnabled && !thread.isLoading` 时计算，否则复用同一个冻结的空 Map；结果经 `VirtualMessageList.renderAfterGroup` 查表渲染一次。空 clarification 判定抽成共享谓词 `isEmptyClarificationGroup`，由 `renderGroup` 与汇总函数共用，保证锚点与渲染同契约。删除各消息组和普通回复气泡内的逐条明细。`accumulateUsage`（按 id 去重）保持不动，仍只服务顶部总会话徽章。

**Tech Stack：** Next.js、React 19、TypeScript、TanStack React Virtual、Vitest（node + happy-dom）、Playwright；不新增依赖，不修改 Shadcn/AI Elements registry 生成物。

## 一、根因与证据

### 已确认的直接原因

1. `frontend/src/components/workspace/messages/message-token-usage.tsx:72-88` 先过滤出所有 AI 消息，再用 `aiMessages.map(...)` 为每条消息渲染一个 `MessageTokenUsage`。因此一个 processing 组内有多少条 AI 消息，就必然出现多少行（缺 usage 的显示“未返回用量”）。
2. 一次用户任务会经历多次模型调用，每次调用都有独立 AI 消息和 `usage_metadata`。`frontend/src/core/messages/utils.ts:126-170` 只把连续中间消息放进同一 processing 组，并没有把它们合并成一条消息；`present-files`、subagent、storyboard 和普通最终回复还会形成不同组。
3. `frontend/src/components/workspace/messages/message-list.tsx:198-353` 在多个组类型后分别调用 `MessageTokenUsageList`；普通最终回复又由 `frontend/src/components/workspace/messages/message-list-item.tsx:328-332`、`:361-365`、`:405-409` 单独渲染 usage。因此只把单个 processing 组改成汇总仍会跨组残留多行，不是完整修复。
4. 可复用的单条提取已存在：`frontend/src/core/messages/usage.ts:13-28` 的 `getUsageMetadata`；`accumulateUsage`（`:38-66`）会累加输入/输出/总 Token 并按消息 ID 去重，但目前只有顶部总会话组件 `frontend/src/components/workspace/token-usage-indicator.tsx:43-45` 使用，任务明细没有使用。
5. 截图中的连续多行与上述代码路径一一对应；无需假设后端重复写入即可确定该展示症状的来源。本批没有证据把多行 UI 等同于重复计费，也不顺带审计供应商或后端 token 统计口径。
6. **影响面**：`token_usage.enabled` 在 `config.example.yaml:108-109` 默认为 `true`（本地 `config.yaml` 同），逐条明细对全员可见，不是仅测试服现象。渲染面只有 `workspace/chats` 与 `workspace/agents/[agent_name]/chats` 两页；`workspace/agents/new` 页未传 `tokenUsageEnabled`，不受影响；`MessageListItem` 的唯一消费者是 `MessageList`。

### 评审核对出的实现约束（2026-11-23，均为代码事实，除第 4 项为推断）

1. **旧行覆盖面决定数据源。** 按 `getMessageGroups`，每条进组的 AI 消息恰好对应一行旧明细（processing 组用 `hasToolCalls ? true : !hasContent` 过滤，reasoning+content 的消息改由其 `assistant` 组渲染）；既无内容、无 reasoning、无 tool_calls 的 AI 消息不进任何组，旧版不渲染行。所以“等于旧各行之和”的数据源必须是 `groups`，而非原始 `thread.messages`。
2. **去重键不能用 `message.id`。** `frontend/src/core/messages/utils.ts:48-50` 的注释与 `frontend/tests/unit/core/messages/group-messages-stability.test.ts:141`（以 `ai("dup-id","a1")`、`ai("dup-id","a2")` 建模）都表明 LangGraph 可能让**不同** AI 消息复用同一 `lc_run--` id。`accumulateUsage` 按 id 去重（`usage.ts:53-58`），这类消息若带 usage 会被漏算，而旧逐行显示不会——与本计划“等于旧各行之和”的承诺冲突。同一条消息跨组出现时是**同一个对象**（组内持有的是 `thread.messages` 的引用），按对象引用去重即可精确覆盖 reasoning+content 的双组场景。真实数据里是否出现同 id 异消息，由 Task 0 Step 3 核对。
3. **轮次边界成立。** 动态上下文提醒（日期 SystemMessage、`<memory>` HumanMessage）均带 `hide_from_ui`（`backend/packages/harness/deerflow/agents/middlewares/dynamic_context_middleware.py:251`、`:267`），被 `getMessageGroups` 过滤，不会把一轮切成两段；以 `groups` 中的 `human` 组作边界即等于“可见 human”。
4. **汇总行落点与复制/反馈工具栏冲突（代码推断，未在浏览器量过）。** 旧行在回复气泡内部（`message-list-item.tsx:405-409`）；`renderAfterGroup` 的内容落在 `AIElementMessage` 之外、紧贴其下（`frontend/src/components/workspace/messages/virtual-message-list.tsx:321`、`:347`）。工具栏是 `absolute -bottom-8 z-20` 的整行（`message-list-item.tsx:154-178`），`CopyButton` 无 hover 门控、`!isLoading` 即常显，会落在汇总行的预期位置上并盖住它。需在实现中让位，并以 boundingBox 断言加变异验证钉死（Task 1 Step 3、Task 2 Step 5）。
5. **流式热路径红线。** 流式期间 `messages` 数组引用必然变、`groups` 的 `useMemo` 随之重算（`utils.ts:177-190` 的 `reuseMessageGroups` 注释）；仓库对“每 token O(n)”有红线（`message-list-item.tsx:285-289`、`frontend/src/core/threads/token-usage.ts:8`）。汇总只在 `isLoading=false` 时显示，计算必须同样门控，否则 `useMemo` 每 token 全量求和而结果根本不显示。

### 历史与测试现状

- `git blame` 显示逐 AI 消息渲染和 `accumulateUsage` 均来自基线提交 `2f4848b8`，不是近期 `message-list.tsx` 改动新引入的回归。
- 当前没有针对 `MessageTokenUsageList`、`accumulateUsage` 或任务级 Token 行数的单元/DOM 行为测试；现有消息分组与虚拟列表测试也未覆盖 Token 汇总锚点，所以该行为长期未被门禁发现。
- `VirtualMessageList` 已提供 `renderAfterGroup`，且当前没有生产消费者，可在不破坏消息组虚拟化键和滚动锚定的情况下承载每轮汇总。
- 仓库内没有任何测试挂载真实 `MessageList`（`frontend/tests/unit/app/workspace/agents/agent-chat-page.dom.test.tsx:96` 反而把它 mock 掉）；其重子组件（streamdown、`ArtifactFileList`、`SubtaskCard`、`useSyncSubtaskStatus`、i18n）须按 `frontend/tests/unit/components/workspace/messages/message-group-memo.dom.test.tsx` 的 `vi.mock` 写法隔离。≥60 组虚拟分支在 happy-dom 无真实布局、窗口可能为空（`virtual-message-list-empty-groups.dom.test.tsx` 已注明），长会话类断言只能由 Playwright 承担，且 e2e 中暂无现成长会话夹具。
- e2e mock 现状：`/api/models` 硬编码 `token_usage: { enabled: false }`（`frontend/tests/e2e/utils/mock-api.ts:243`）；`handleRunStream`（`:303-340`）被多个 spec 共用且固定返回 `msg-ai-1`——直接改会波及其他 spec，且第二轮因同 id 会被合并成同一轮，需要独立的带状态 handler。

### 测试服只读核验

- 有效会话恢复后，已在截图对应真实页面数出 **17 行**任务 Token 明细：处理步骤后连续 15 行、产物卡后 1 行、最终回复后 1 行。这三个位置与上述 `MessageTokenUsageList`、产物组出口和普通回复出口一一对应，已在测试服复现，不再只是截图推断。
- 同一线程的 runs API 只返回一个成功 run，记录 **18 次 LLM 调用**；顶部徽章与该 run 的 `total_tokens` 一致，而 run 总量还包含未形成可见明细的 `middleware_tokens`。这证明“17 行可见明细”不是完整模型调用清单，也不是后端重复写了 17 个 run。
- 本批只合并现有可见 AI 消息明细，所以汇总行不要求等于包含 middleware 的 run/会话顶部总量；若产品后续要求两者严格一致，应另开按 `run_id` 关联权威 run 汇总的设计，不在本批暗中换口径。
- **子代理口径（按结构推断，待 Task 0 实测）**：子代理 token 经 `task_tool` 的 `_transfer_subagent_tokens` 转交 RunJournal 的 run 级桶（`backend/packages/harness/deerflow/tools/builtins/task_tool.py:21-33`），不挂在主线程 AI 消息的 `usage_metadata` 上。上述样本只含 1 次 middleware 调用，不代表大量派发子任务的 Agent（如 course-video、PPT）；这类任务的汇总行会明显低于顶部徽章，口径决策见 D2，Task 0 Step 2 须找含子代理的会话核对差距并记录。
- 本轮未核验测试服部署 commit。执行 Task 0 仍须核对部署版本及修复前原始数值；若测试服代码与 main 不一致，先更新本计划，不能直接套用行号。

## 二、范围与决策

### Scope — In

- 同时覆盖通用聊天页和 Agent 聊天页共同使用的 `MessageList`，不逐页复制修复。
- **轮次**：从一条可见 human 组开始，到下一条可见 human 组之前（“可见”= 经 `getMessageGroups` 过滤后仍在 `groups` 中的 `human` 组）；列表**首部**没有前置 human 的消息（如开场白、仅加载到部分历史）归入首个隐式轮次。一次续作或新的用户指令另算一轮、不跨轮合并——含中断后点“重试（发送「继续」）”（`message-list-item.tsx:356`）产生的新 human 消息，因此同一个用户感知的任务可能显示两行，属接受的取舍（D4）。没有任何 AI 消息的轮次不渲染汇总行。
- **数据源与去重**：数据源 = `groups`（只统计进了组的 AI 消息，覆盖面与旧逐行一致，不用原始 `thread.messages`）；每条 AI 消息用 `getUsageMetadata` 取用量，同一条消息（同一对象）跨组只计一次，同 id 的不同消息各计一次（D5、D6）；`accumulateUsage` 保持不动，只服务顶部徽章。
- **缺失标注**：全部 AI 消息都缺 usage → 一行 `unavailableShort`，不伪造为 0；部分缺 → 数值为已返回部分之和，行末追加 `unavailableShort ×N`（N = 轮内缺 usage 的不同 AI 消息数；复用现有文案 key，不新增词条，D3）。
- **锚点与位置**：汇总行挂在该轮最后一个**实际可渲染**组之后；空 clarification 判定抽成共享谓词 `isEmptyClarificationGroup`（`core/messages/utils.ts`，只新增导出、不改分组逻辑），`renderGroup` 与汇总函数共用，不再两处各写一份。锚点组是 `assistant`（带复制/反馈工具栏）时汇总行加顶部让位间距，显示顺序为“回复 → 工具栏 → Tokens 行”（D1）；具体类名以实现时的 boundingBox 断言为准。
- **门控**：`tokenUsageEnabled=false` 或 `isLoading=true` 时不遍历消息、不调用汇总函数，直接复用同一个冻结的空 Map（D7）；结束后显示完整一轮汇总。

### Scope — Out

- 不改后端模型调用、RunJournal、run 汇总字段、数据库、API、价格、个人用量页或控制台。
- 不改顶部 `TokenUsageIndicator` 的总会话总量、告警阈值、刷新机制或后端优先语义；单轮汇总不要求等于整个会话徽章。也不改 `accumulateUsage`——若 Task 0 证实存在“同 id 异消息”，徽章回落口径是否一并修另行向用户确认，默认不并入本批（D9）。
- **汇总行口径 = 主线程可见 AI 消息 `usage_metadata` 之和，不含子代理与 middleware 调用**；文案不变、不加 tooltip，已知局限写进 changelog 并登记 backlog，产品反馈被读成“总花费”时再另开 run 权威口径的设计（D2）。
- 不合并不同用户轮次，不新增逐次调用展开/折叠、模型维度明细或新文案体系（“未返回用量 ×N”复用现有 key，不算新词条）。
- 不修 findings backlog 中供应商 `usage_metadata`、流式累计和近似 Token 的独立统计问题（2026-11-17 条目），不顺带调整 `getUsageMetadata` 的字段缺省口径。
- 不修改 `frontend/src/components/ui/`、`frontend/src/components/ai-elements/`、`config*.yaml`、部署配置或真实用户数据；不新增 i18n 词条（不改 `frontend/src/core/i18n/locales/`）。

### 路径边界

| 允许路径（allow） | 禁改路径（ban） | 共享基座触达 |
| --- | --- | --- |
| `frontend/src/components/workspace/messages/{message-list.tsx,message-list-item.tsx,message-token-usage.tsx}`、`frontend/src/core/messages/{usage.ts,utils.ts}`（`utils.ts` 仅新增 `isEmptyClarificationGroup` 导出，不改分组逻辑）、对应 `frontend/tests/unit/` 新增或既有测试、`frontend/tests/e2e/{chat.spec.ts,utils/mock-api.ts}`（`mock-api.ts` 只追加新选项与独立 handler，不改 `handleRunStream` 默认行为）、本计划与执行 changelog、`docs/findings-backlog.md`（仅追加条目） | 所有 backend 产品代码、registry 组件、其他业务页面、`frontend/src/core/i18n/locales/`、后端 API/schema、部署文件、真实配置和任务数据；`VirtualMessageList` 本体、`accumulateUsage`、`token-usage-indicator.tsx`、`getMessageGroups` 分组逻辑 | **需单执行者串行**：`MessageList`、`utils.ts` 与 usage helper 被两类聊天页和多处测试共享；不改 `VirtualMessageList`，只消费现有 `renderAfterGroup` |

### 评审决策记录（2026-11-23，一轮拷问 + 代码核对）

D1–D4 经用户确认采纳推荐答案；D5–D9 为代码核对后的规划侧结论，其中 D9 待 Task 0 数据。

| 编号 | 决策点 | 定稿答案 | 依据 |
| --- | --- | --- | --- |
| D1 | 汇总行与复制/反馈工具栏的位置关系 | 保持 `renderAfterGroup` 单一出口；终稿为轮次末组时汇总行让位，排在工具栏**下方**（回复 → 工具栏 → Tokens 行）。不把汇总塞回气泡内（会变两处出口），不改工具栏定位。 | 本计划核心不变量是“同一轮只有一个渲染出口”；代价只是一条让位间距，由 boundingBox 断言钉死。 |
| D2 | 汇总行口径 | 接受“仅主线程可见 AI 消息，不含子代理与 middleware 调用”；文案不变；changelog 写已知局限并登记 backlog。被读成“总花费”再走 run 权威口径。 | 计划已明确不在本批暗中换口径；换成 run 总量需后端/API，超出本批范围。 |
| D3 | 部分调用缺 usage | 汇总行末加“未返回用量 ×N”，复用 `unavailableShort`，不新增 key；全缺时仅显示 `unavailableShort`。 | backlog 2026-11-17 条目显示 qwen/Doubao 的 `usage_metadata` 口径存疑，部分缺失并不罕见；旧逐行能看出哪几次缺，静默偏低会丢信息。 |
| D4 | “继续”是否另起一轮 | 是，每条可见 human 消息一轮；不识别“继续”类续作文本。 | 规则简单可测；识别续作文本是脆弱的语义判断，两行也各自对应真实数值。 |
| D5 | 去重键 | 按消息**对象引用**去重，不按 `message.id`；`accumulateUsage` 不动。 | 仓库自述 LangGraph 会让不同消息复用 `lc_run--` id（见 §一 约束 2）；按 id 会漏算，破坏守恒。 |
| D6 | 数据源 | 遍历 `groups`，不遍历原始 `thread.messages`。 | 不进组的 AI 消息旧版不渲染行（§一 约束 1）。 |
| D7 | 计算门控 | `tokenUsageEnabled && !thread.isLoading` 才计算，否则复用冻结空 Map。 | 每 token O(n) 红线（§一 约束 5）。 |
| D8 | 测试分层 | 纯函数（node）+ 展示组件（dom）+ `MessageList` 接线（dom，`vi.mock` 隔离重子组件）+ Playwright；≥60 组与测高/滚动只在 Playwright/部署验收层承担。 | 仓库无真实 `MessageList` 挂载先例；happy-dom 无真实布局（§一 历史与测试现状）。 |
| D9 | 待定：顶部徽章回落口径 | 若 Task 0 证实存在同 id 异消息，是否一并修 `accumulateUsage` 的按 id 去重，另行向用户确认；默认不并入本批。 | 该回落只在后端 thread 聚合不可用时生效，且属范围外。 |

## 三、实施任务

### Task 0：漂移检查、真实数据基线与实现前提核对

- [x] **Step 1：确认最新代码与计划映射。** 用户批准后运行 `git fetch origin main`、`git diff --stat 52c316763dc15e60649f1dc6eb0faf52b50013d4..origin/main`，重点核对 `MessageList`、`MessageTokenUsageList`、`accumulateUsage`、`VirtualMessageList.renderAfterGroup`，以及工具栏定位类（`absolute -bottom-8`）和 `renderAfterGroup` 调用点是否仍如 §一 所述；从最新 main 建单一工作分支，只把顶部原状态行改为 `未执行（执行中）`。
- [x] **Step 2：保存脱敏的修复前基线（可降级）。** 有有效测试服会话时：记录部署版本、该轮可见 Token 行数、各行原始输入/输出/总计及总量；再找一个多轮会话，以及一个**含子代理派发**的会话，记录“汇总行 vs 顶部徽章”的差距。拿不到有效会话时记 `SERVER_BASELINE_NOT_VERIFIED`，用 Step 3 的离线夹具继续，不得卡在本步。证据只存 gitignored `temp/`——若在 worktree 内执行，证据目录指向**主 clone** 的 `temp/2026-11-23-chat-token-usage-turn-summary/`（或在 `git worktree remove` 前先归档），不得提交账号、主机、真实 `thread_id/run_id`、任务正文或完整响应。
- [x] **Step 3：离线夹具与口径核对。** 用脱敏后的真实消息**形状**（消息类型序列、id 置换、各条 usage 数值，不含正文）手工构造入库夹具：1 条 human + processing 组内 15 个 AI/tool 对 + 1 个 present-files 组 + 1 条终稿，旧实现下可数出 17 行。在同一份数据上对比并记录三个量：旧各行之和、按对象引用去重之和、`accumulateUsage` 结果；同时核对 AI 消息是否存在“同 id 异消息”。三者一致 → 按计划继续；不一致 → 记入 changelog，汇总函数仍按对象引用（本计划默认），并把 D9 带回用户确认，不在本批擅自改 `accumulateUsage`。若出现“同 id 且 usage 完全相同的重复对象”（疑似 merge 漏洞）→ 停下修订计划，不得悄悄改成按 id 合并。
- [x] **Step 4：确认实现边界仍成立。** 若真实消息证明同一行并非来自逐 AI 消息渲染，或部署版本缺少本计划依赖的 hook，停止实施并修订根因/计划；不得为赶进度直接换成另一套后端总量。

### Task 1：先写失败测试（分层）

- [x] **Step 1：纯函数 RED（node 档）。** 在 `frontend/tests/unit/core/messages/usage.test.ts` 为 `summarizeTurnUsage(groups)` 覆盖（`isEmptyClarificationGroup` 的用例放既有 `utils.test.ts`）：
  - 多条 AI 的输入/输出/总计求和；
  - 同一消息对象同时出现在 processing 与 assistant 两组（reasoning+content）只计一次；
  - 同 id 的两条**不同** AI 消息各计一次（钉死口径，与 `accumulateUsage` 的按 id 去重刻意不同）；
  - 既无内容/reasoning/tool_calls、不进任何组的 AI 消息不计入；
  - 全部缺 usage → `usage=null` 且 `missingCount=N`；部分缺 → 已返回部分求和且 `missingCount=k`；合法 0 值；
  - 两个 human 轮次互不合并；列表首部无前置 human 的隐式首轮；无 AI 的轮次无条目；
  - 锚点 = 轮内最后一个可渲染组的**原始**索引，末尾空 clarification 组被跳过；
  - **守恒性**：测试内用旧过滤规则（`hasToolCalls ? true : !hasContent` 等）重算“旧各行之和”，在 Task 0 的 17 行夹具与上述各用例上断言新汇总与之相等。
- [x] **Step 2：展示与接线 RED（dom 档）。**
  - `frontend/tests/unit/components/workspace/messages/message-token-usage.dom.test.tsx`：`TurnTokenUsage` 渲染输入/输出/总计与 `data-testid="turn-token-usage"`；全缺 → 仅 `unavailableShort`；部分缺 → 数值 + `unavailableShort ×N`。
  - `frontend/tests/unit/components/workspace/messages/message-list-token-usage.dom.test.tsx`：沿用 `message-group-memo.dom.test.tsx` 的 `vi.mock`（i18n、markdown-content、artifacts、message-group、subtask-card 等重子组件）挂载真实 `MessageList`。同一轮 processing + present-files + 终稿：旧实现多行，目标为 1 行且位于终稿组之后；两个 human 轮次各 1 行；`tokenUsageEnabled=false`、`isLoading=true` 均为 0 行；末尾空 clarification 不吞汇总。**门控用例**：`isLoading=true` 或 `tokenUsageEnabled=false` 时 `summarizeTurnUsage` 一次都不被调用（`vi.fn` 计数），且 `renderAfterGroup` 取到的空 Map 引用稳定。
- [x] **Step 3：Playwright 行为 RED。**
  - 新增独立的带状态 SSE handler：第二次请求返回两轮累计 messages，id 不与首轮冲突；用后注册的 `page.route` 覆盖 `mockLangGraphAPI` 内的同路径 route（Playwright 后注册者优先）；不改 `handleRunStream` 默认行为，避免波及其他 spec。`mockLangGraphAPI` 追加 `tokenUsageEnabled` 选项（现硬编码 false）；mock `/api/threads/*/token-usage` 让顶部徽章值确定。
  - 断言：一次任务返回多条带 `usage_metadata` 的 AI 消息（processing 多条 + present-files + 终稿），任务结束后消息区只有 1 个汇总且数值正确；run 进行中（可复用 `tests/e2e/utils/held-open-stream.ts` 保持流打开）为 0 个；第二轮后共 2 个，每轮各一；顶部徽章不计入。
  - **RED 阶段**用文本定位（英文 locale 下 `getByText(/^Input:/)`，新旧实现渲染同一文案）数行数，证明旧实现得到 N≠1，并保存失败输出；GREEN 阶段再补 `data-testid` 与位置断言。
  - **位置断言**（GREEN 守卫，无旧实现对照）：终稿为末组时，汇总行 `boundingBox().y` ≥ 工具栏 `boundingBox().y + height`。
  - 用例标题须含 `token`（Task 3 用 `-g token` 过滤）。

### Task 2：实现每轮一次的汇总

- [x] **Step 1：共享谓词。** `frontend/src/core/messages/utils.ts` 新增并导出 `isEmptyClarificationGroup(group)`，`message-list.tsx` 的 clarification 分支改用它（行为等价，既有测试保持绿）。
- [x] **Step 2：纯函数实现。** `frontend/src/core/messages/usage.ts` 新增 `TurnUsage = { usage: TokenUsage | null; missingCount: number }`、冻结空 Map 常量与 `summarizeTurnUsage(groups)`：遍历 `groups`，遇 `human` 组开启新轮，首个 human 之前的组属隐式首轮；轮内对每个组的每条 AI 消息按对象引用去重（`Set<Message>`），`getUsageMetadata` 非空则累加、为空则 `missingCount+1`；锚点取轮内最后一个 `!isEmptyClarificationGroup` 的组的原始索引；轮内无 AI 消息则不写入 Map。不调用 `accumulateUsage`，不改它。
- [x] **Step 3：展示组件。** `message-token-usage.tsx` 的 `MessageTokenUsage` 与 `MessageTokenUsageList` 换成 `TurnTokenUsage({ usage, missingCount, className })`：保留现有视觉结构（`CoinsIcon`、`t.tokenUsage.*`、`text-[11px]`、`border-t` 等），加 `data-testid="turn-token-usage"`；`enabled/isLoading` 判定上移到 `MessageList` 的计算门控，组件只管展示。删除失效导出，避免 knip 死导出。
- [x] **Step 4：接线与出口清理。** `message-list.tsx`：`useMemo` 计算 `turnUsage`，条件为 `tokenUsageEnabled && !thread.isLoading` 时调用 `summarizeTurnUsage(groups)`，否则直接返回冻结空 Map（短路，零遍历）；`renderAfterGroup` 以 `useCallback` 查表渲染 `TurnTokenUsage`，锚点组 `type === "assistant"` 时加让位间距，传给 `VirtualMessageList`；删除各组后的 5 处旧 `MessageTokenUsageList`，并把 `tokenUsageEnabled` 从 `renderGroup` 依赖中去掉。`message-list-item.tsx`：删除 3 处 `MessageTokenUsage`，以及 `MessageListItem`/`MessageContent_` 的 `tokenUsageEnabled` prop 与 import。不得修改 registry 组件，不改组 ID、虚拟化阈值、prepend 或滚动行为。
- [x] **Step 5：GREEN + 变异验证。** Task 1 全部用例转绿后做两处变异验证：①撤掉汇总行的让位间距 → 位置断言必须变红；②去掉 `isLoading` 门控 → 门控用例必须变红。两次红灯输出记入 changelog，随后恢复实现。

### Task 3：回归与本地门禁

- [x] **Step 1：运行定向测试并确认 RED→GREEN。** 在 `frontend/` 执行：
  - `pnpm exec vitest run --project node tests/unit/core/messages/usage.test.ts tests/unit/core/messages/utils.test.ts`；
  - `pnpm exec vitest run --project dom tests/unit/components/workspace/messages/message-token-usage.dom.test.tsx tests/unit/components/workspace/messages/message-list-token-usage.dom.test.tsx tests/unit/components/workspace/messages/virtual-message-list-empty-groups.dom.test.tsx`；
  - `pnpm exec playwright test tests/e2e/chat.spec.ts -g token`；
  确认新用例及原有消息分组/虚拟列表行为全绿。
- [x] **Step 2：长会话与边界回归（分档）。** ≥60 组虚拟分支在 happy-dom 无真实布局，**只能由 Playwright 承担**：用带状态 handler 程序化生成 ≥60 组的长会话（如 32 轮 × 2 组），断言窗口内每轮至多 1 个汇总、末轮汇总可见且不与工具栏重叠、流式结束后出现完整汇总。若 e2e 夹具成本超预期，降级为 Task 4 Step 3 的部署后手测项，并在 changelog 标 `NOT_VERIFIED`，不得宣称已验证；历史 prepend 后不错位、测高、滚动锚定同理。另验证缺 usage、同 id 异消息、多个产物组、错误/停止后的已完成消息不重复。发现空组锚点问题只修本批映射，不重写虚拟列表。
- [x] **Step 3：运行完整门禁。** 在 `frontend/` 运行 `pnpm test`、`pnpm test:e2e`、`pnpm check`，在仓库根运行 `make check` 和 `python3 scripts/check_docs_convention.py`；若本机未装 pre-commit hooks，commit 前手动 `pre-commit run --files <改动文件>`；不新增 `eslint-suppressions.json` 条目（react-hooks ratchet）。预计超过 120 秒的命令按仓库规范后台执行；检查 `git status`，删除测试生成物，不提交证据或日志。

### Task 4：PR、文档与测试服验收

- [x] **Step 1：完成单 PR 评审。** 提交 `fix(chat): 按任务轮次汇总 Token 明细`，因属于 plan 驱动且预计超过 5 个文件，走分支 + PR + CI/OCR，不直推 main。**OCR 评审轮次上限 3 轮**：OCR 每次推送都会全量重审、结论不单调且有误报（PR #29 的记录），每轮先对着推送后的文件核对再判定；误报或延后项用 `gh pr comment --body-file` 记录处置，不为此追加 docs-only commit；第 3 轮仍有 Critical/Important 才带回用户拍板。无 Critical/Important 且 CI 全绿后才 rebase 合入。
- [x] **Step 2：记录可审计结果与 backlog。** 按实际执行日新增 `docs/changelog/changelog-YYYY-MM-DD-chat-token-usage-turn-summary.md`：含 TL;DR；「验收记录」逐行写命令与真实关键输出（RED→GREEN、两处变异验证、完整门禁、PR CI/OCR、任何 `NOT_VERIFIED`）；「已知局限」节写明口径不含子代理与 middleware、“继续”另起一行、Task 0 Step 3 三个量的对比结果。同步向 `docs/findings-backlog.md` 追加（格式 `- [ ] YYYY-MM-DD 一句话 + 指针`）：①汇总行不含子代理/middleware，按 `run_id` 的权威口径设计；②（仅当 Task 0 证实存在）`accumulateUsage` 按 id 去重与同 id 异消息的冲突及顶部徽章回落口径。只更新本计划顶部原状态行与对应 checkbox。
- [ ] **Step 3：获批后部署并做零付费验收。** 部署必须另行获得用户批准。优先重开已有任务，不重新运行 Agent：原一轮多行应变为一行，原始未四舍五入数值之和应与修复前一致；多轮会话每轮各一行，顶部总会话徽章保持不变；含子代理的会话记录汇总行与徽章的差距；汇总行不遮挡复制/反馈工具栏（截图脱敏后只存 `temp/`）。再验证刷新、历史加载和长会话滚动；若不能部署或有效会话不可用，对应项保持未勾选并明确记录，不宣称测试服已验收。

## 四、全局 Review Focus 与完成定义

1. **不能只修一半**：把单个 processing 组内的 `map` 改成求和但仍保留其他组/普通回复的逐条组件，不合格；同一轮必须只有一个渲染出口。
2. **不能改变计量语义**：本批只合并现有展示值，不拿后端 run 总量、顶部会话总量或另一套 token 估算替换原行之和，不顺带修供应商统计；口径局限（不含子代理与 middleware）须在 changelog 写明。
3. **不能重复、漏算或跨轮计数**：同一条消息（同一对象）跨组只计一次；同 id 的不同消息各计一次，不得按 id 合并；不同可见 human 轮次不得合并；历史与流式重叠消息沿用现有 merge 去重。
4. **不能破坏长会话**：汇总必须进入虚拟列表测高和滚动锚定，末尾空组不能吞掉汇总，prepend 不得造成错轮或重复行；真实布局的验证只在 Playwright 与部署验收层承担，不以 happy-dom 结果宣称通过。
5. **不能遮挡既有控件**：汇总行不得与复制/反馈工具栏重叠，由 boundingBox 断言加变异验证钉死。
6. **不能给流式热路径加开销**：`isLoading=true` 与开关关闭时不遍历消息、不调用汇总函数，由门控用例加变异验证钉死。
7. **完成标准分两层**：
   - **离线完成**：纯函数、DOM、Playwright 行为断言全绿（含两处变异验证），frontend 全量测试/check 与根门禁通过，单 PR CI/OCR 通过并合入，changelog 与 backlog 登记完成。达成后顶部状态行可写 `已执行（离线验收；测试服验收待批准部署）`。
   - **部署验收**：另需用户批准；实际部署后复核原任务一行、数值守恒、多轮各一行、顶部徽章不变、汇总行不遮挡工具栏，才可勾选对应项并去掉“待批准”字样。

## 五、假设

- “一个任务”按用户可见的一轮提交理解，而不是整个 thread；后续“继续”（含错误卡片“重试”发送的「继续」）或新的指令是新一轮，仍有各自一行（D4 已确认）。
- 产品要保留输入/输出/总计三个现有字段，只减少重复行，不增加逐调用详情；仅新增“未返回用量 ×N”这一极简标记，复用现有文案 key（D3 已确认）。
- “汇总行会被工具栏遮挡”和“子代理 token 不在主线程消息 usage 上”两项为代码推断，已分别在 Task 1 Step 3 / Task 2 Step 5 与 Task 0 Step 2 / Task 4 Step 3 设置实测核对点，实测推翻时先修订本计划。
- 本次交付仅为计划；测试服真实页面和 run 摘要已只读核验，但部署 commit 尚未核验，执行时的 Task 0 仍是强制前置门禁，不是可选补证（有效会话不可用时降级为 `SERVER_BASELINE_NOT_VERIFIED` 并走离线夹具）。
