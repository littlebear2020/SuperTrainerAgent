# Agent 新会话引导页重构实施计划

> **状态**：待执行（设计已批准；须先收尾创建框批次，任务 1–7 均未执行）。
> **TL;DR**：仅为四个目标 Agent 的 `chats/new` 增加页面级双语任务引导、16 张案例卡与明确的预填状态机；不改后端、seed、SOUL、技能或性能预算；单执行者按 TDD 串行提交，真实链路留作另行授权的人工验收。

> **For agentic workers:** REQUIRED SUB-SKILL: Use `executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. 本批明确采用单执行者串行，不使用 subagent-driven-development，不并行修改共享文件。

**Goal:** 让 course-video、ppt-master、creative-studio、co-writer 的新会话页以用户任务和真实案例取代 raw Agent/Skill 引导，同时保持既有对话、提交、handoff、上传、模型与历史路由兼容。

**Architecture:** `AgentChatPage` 仅为四个目标新会话切换到正常文档流、可滚动的页面级布局；共享 `AgentNewChatGuide` 从 i18n 读取内容、从 `agentAppearance()` 读取图标，并通过现有 `usePromptInputController()` 预填。`InputBox` 只增加显式的新会话建议区开关，其他 Agent 与已有会话沿用原分支。

**Tech Stack:** Next.js 16 App Router、React 19、TypeScript、Tailwind CSS 4、TanStack Query、Vitest（node + happy-dom）、Playwright；不新增依赖。

**Spec:** `docs/2026-11-10-agent-new-chat-redesign-design.md`（已批准）；计划负责执行分解，设计文档不再平行扩写实现细节。

**基准 commit:** `fe4654c15c9afd023cf35a000b0bcb5d0ee779fc`（设计评审修订提交）。执行前运行 `git diff fe4654c15c9afd023cf35a000b0bcb5d0ee779fc --stat`，相关接口若已漂移，先更新计划，不机械套用行号。

**执行方式:** 用户已选择 `executing-plans`、单执行者、单工作区、任务 1→7 串行；只提交本地 commits，不 push、不建 PR、不部署。

## Global Constraints

- 本批只改 allow 列表中的前端代码、测试与文档；不得修改 `backend/**`、seed、SOUL、`SEED_VERSION`、API、数据库、Docker、Skills、video-templates、锁文件或真实服务器配置。
- 不修改 `frontend/src/components/ui/**`、`frontend/src/components/ai-elements/**`；尤其不得为聚焦或预填去改 registry 文件 `prompt-input.tsx`。
- 不新增 npm 依赖、Testing Library、状态库、性能预算路由或后端内容表；不修改 `performance-budgets.json`。
- 四个目标 ID 固定为 `course-video`、`ppt-master`、`creative-studio`、`co-writer`；每个恰好 4 张案例，共 16 张。Hero 标题、短名、案例方向、Co-writer 字面标记以设计 §5 为准。
- 案例点击只预填并聚焦，不自动提交、不创建线程、不调用生成；非空草稿必须“替换 / 追加 / 取消”三选一。
- handoff 继续由页面现有 effect 唯一消费；`?msg=` 优先于 sessionStorage，案例不得提前点击或覆盖 handoff。
- 目标新会话不渲染旧 Skill、全站 Skill 分类、惊喜、旧 Agent prompts、通用建议或“+ 创建”；不得通过传空技能数组或隐藏旧 DOM 实现。
- 非目标 Agent 与已有会话保留原布局、`AgentWelcome`、建议区和消息流；`history.replaceState` 的线程切换机制不变。
- 所有新增 UI 文案和 prompt 均进入 `zh-CN` / `en-US` / `Translations`；Co-writer 的 `[类型: …]`、`[从零创作]` 在英文 prompt 中也保持原字面值。
- 新测试沿用 `createRoot + act` happy-dom harness；纯函数放 `*.test.ts`，DOM 放 `*.dom.test.tsx`，E2E 放 `tests/e2e/*.spec.ts`。不删除或弱化旧测试。
- 每个任务严格按“失败测试 → 确认功能原因失败 → 最小实现 → 定向与相邻回归通过 → 本地提交”执行，不先写实现后补红灯。
- 预计超过 120 秒的命令后台运行并等待完成通知；冻结被测输入，不共享 `.next` 或 E2E 端口并行跑门禁。
- 默认自动化全部 mock 后端，不触发真实付费生成。真实链路不是本批代码交付硬门槛，只有用户另行明确授权才执行；未执行时 changelog 必须如实写“未执行”。

## 实施前置（不是本批任务）

当前工作区有五个创建框批次的未提交文件，上一轮 E2E 为 44 通过、11 失败。用户已确认：先由同一执行者逐项区分真实回归与测试/负载问题，完成该旧批次并提交后，才开始本计划 Task 1。

- 不 stash、不重置、不另开 worktree，不把旧批次改动混入本计划提交。
- 若旧批次必须修改 ban 路径或出现无法区分的基准失败，立即停止并报告，不开始本批实现。
- 旧批次收尾后重新确认 `git status`、HEAD 和相关测试基线，再勾选 Task 1 的第一步。

## 评审决策记录

| Frontier 问题 | 用户确认或设计复审结论 |
| --- | --- |
| 外部评审要求 bump `SEED_VERSION` | 只有修改 seed 才需要；本批已收窄为纯前端，不迁移技能、不 bump 全局版本。 |
| 是否复用后端 `suggestion_prompts` | 复用“预填不发送”的交互契约，不扩成四 Agent 后端内容源；新页文案与 prompt 走 i18n，图标走 appearance。 |
| 完整引导放在哪里 | page 级正常文档流；不使用 absolute `extraHeader`，不沿用固定 translate 居中。 |
| 已有草稿点击案例 | 替换 / 追加 / 取消；全部只预填，不复用 follow-up 的自动发送。 |
| Creative 研究写作模式 | 不删模式、不裁技能，只不在其新会话案例中宣传研究写作或视频。 |
| 旧 chips、顶栏、i18n、handoff | 分别按设计 §4–§8 固化；非目标 Agent 不改空技能数组的现有回退。 |
| 性能门禁 | `perf:check` 仅作共享包间接回归，不声称直接覆盖目标路由，不改预算。 |
| **Grilling Q1：真实链路是否为硬门槛** | 用户确认不是；默认测试全部 mock，真实链路另行授权，Course/PPT 优先停在确认阶段。 |
| **Grilling Q2：旧创建框批次如何处理** | 用户确认先收尾旧批次，再实施本计划；不 stash、不重置、不混提交。 |

至此 frontier 已清空；执行中不得重新发明上述决策。若新事实与决策冲突，停下回规划侧。

## 路径边界（allow / ban / 共享基座）

| 允许路径（allow） | 禁改路径（ban） | 共享基座触达 |
| --- | --- | --- |
| `frontend/src/core/agents/{new-chat.ts,index.ts,hooks.ts}` | `backend/**`、`docker/**`、`skills/**`、`video-templates/**`、根 `config*.yaml`、`.env*` | **是，单执行者串行**：`core/agents/index.ts` 与 `hooks.ts` 只做本计划定义的导出和 `refetch` 透传。 |
| `frontend/src/core/i18n/locales/{types,zh-CN,en-US}.ts` | 其他 locale/运行时配置；不绕过 i18n 类型门禁 | **是**：三个 locale 文件只允许增加 `agents.newChatGuide(s)` 内容。 |
| `frontend/src/components/workspace/{agent-new-chat-guide.tsx,input-box.tsx}` | `frontend/src/components/ui/**`、`frontend/src/components/ai-elements/**`、其他 InputBox 功能分支 | **是**：InputBox 只增加 `showNewThreadSuggestions` 门控，不重写上传、模型、follow-up 或提交。 |
| `frontend/src/app/workspace/agents/[agent_name]/chats/[thread_id]/page.tsx` | 未列出的 App Router 页面、通用 `workspace/chats/[thread_id]/page.tsx` | **是**：仅目标 Agent 新会话布局、短名、加载/错误和 handoff 门槛；已有会话保持原分支。 |
| 本计划 Files 列出的 `frontend/tests/unit/**`、`frontend/tests/e2e/agent-new-chat-guide.spec.ts`、`frontend/tests/e2e/utils/mock-api.ts` | 当前旧批次五个脏文件：`video-gen-create-dialog.tsx`、其 initial-type 单测、`home-course-entry.spec.ts`、`video-gen-create-flow.spec.ts`、`video-gen-error-states.spec.ts`；其他无关测试 | **是**：`mock-api.ts` 只扩展 Agent fixture 字段，不改变既有默认响应。 |
| 本计划、已批准设计的状态头；执行时新增 `docs/changelog/changelog-YYYY-MM-DD-agent-new-chat-redesign.md` | 不新增第二份设计/报告；不修改 README、AGENTS.md 或性能预算 | 文档由同一执行者收尾，计划逐项勾选，changelog 必须含真实「验收记录」。 |

发现必须修改 ban 路径时立即停止，不扩大 allow，不用临时绕过替代正常门禁。

## Review Focus

1. **handoff 尚未落地、已有草稿或搜索参数变化时点击案例**：不得覆盖草稿或自动发送；Task 3 的三选一 DOM 测试、Task 5 的 keyed gate、Task 6 的 URL/storage E2E 共同钉扎。
2. **目标 Co-writer 与非目标 vimax-video 都是 `skills: []`**：前者不显示全站 Skill，后者保持原回退；Task 4 两个方向的 DOM 测试与 Task 6 非目标 E2E 钉扎。
3. **长 Hero、16 张案例、390px 与已有线程切换**：新页可滚动且无横向溢出，已有线程不被新布局挤压；Task 2 布局测试、Task 5 分支测试、Task 6 移动端与历史线程 E2E 钉扎。
4. **英文 UI 与 Co-writer 中文契约标记、语言切换后旧草稿**：内容双语但标记不翻译，已填文本不被重写；Task 1 内容测试、Task 3 语言切换测试、Task 6 英文路由测试钉扎。
5. **Agent 加载失败、未知 Agent、旧别名与真实发送**：错误可重试且不渲染伪案例，别名仍可用，只有用户明确提交才创建一次线程；Task 5 状态测试与 Task 6 retry/alias/send 计数测试钉扎。

## 文件职责图

| 文件 | 动作 | 单一职责 |
| --- | --- | --- |
| `frontend/src/core/agents/new-chat.ts` | Create | 四个目标 ID、`AgentNewChatGuideId` 与类型守卫；不含文案、图标或 React。 |
| `frontend/src/core/agents/index.ts` | Modify | 导出上述目标 ID 契约。 |
| `frontend/src/core/agents/hooks.ts` | Modify | `useAgent` 透传 TanStack Query 的 `refetch`，供现有 getAgent 请求重试。 |
| `frontend/src/core/i18n/locales/types.ts` | Modify | 定义四案例 tuple、每 Agent 引导 copy 与共享状态/对话框文案类型。 |
| `frontend/src/core/i18n/locales/{zh-CN,en-US}.ts` | Modify | 四个 Agent 的双语短名、Hero、案例、prompt、Tips 和共享 UI 文案。 |
| `frontend/src/components/workspace/agent-new-chat-guide.tsx` | Create | Hero、案例、InputBox children、Tips、草稿确认对话框、加载/错误状态；只写现有 prompt controller。 |
| `frontend/src/components/workspace/input-box.tsx` | Modify | 以 `showNewThreadSuggestions?: boolean` 显式门控旧新会话建议区，默认 true。 |
| `frontend/src/app/workspace/agents/[agent_name]/chats/[thread_id]/page.tsx` | Modify | 目标新会话 page 级布局、短名、Agent 状态、handoff gate；已有会话/非目标分支不变。 |
| `frontend/tests/unit/core/agents/new-chat.test.ts` | Create | ID、别名、双语言案例完整性和 Co-writer 标记。 |
| `frontend/tests/unit/components/workspace/agent-new-chat-guide.dom.test.tsx` | Create | 展示、布局、预填、三选一、聚焦、语言与可用状态。 |
| `frontend/tests/unit/components/workspace/input-box-new-thread-suggestions.dom.test.tsx` | Create | 目标关闭旧建议、非目标默认行为及空技能回退。 |
| `frontend/tests/unit/app/workspace/agents/agent-chat-page.dom.test.tsx` | Create | 页面分支、短名、已有会话、加载/错误/retry 和 handoff gate 接线。 |
| `frontend/tests/e2e/agent-new-chat-guide.spec.ts` | Create | 四路由、16 卡、无自动发送、handoff、历史、别名、移动端、错误与真实 mock 提交。 |
| `frontend/tests/e2e/utils/mock-api.ts` | Modify | 允许 Agent fixture 携带 `skills` / `suggestion_prompts`，既有默认值不变。 |

---

### Task 1：建立四 Agent 的双语内容契约

**Files:**
- Create: `frontend/src/core/agents/new-chat.ts`
- Modify: `frontend/src/core/agents/index.ts`
- Modify: `frontend/src/core/i18n/locales/types.ts`
- Modify: `frontend/src/core/i18n/locales/zh-CN.ts`
- Modify: `frontend/src/core/i18n/locales/en-US.ts`
- Test: `frontend/tests/unit/core/agents/new-chat.test.ts`

**Interfaces:**
- Produces `AGENT_NEW_CHAT_GUIDE_IDS`、`AgentNewChatGuideId`、`isAgentNewChatGuideId(value: string): value is AgentNewChatGuideId`。
- Produces `Translations.agents.newChatGuides: Record<AgentNewChatGuideId, AgentNewChatGuideCopy>`；每份 copy 含 `displayName/title/description`、固定 4 项 `cases` tuple、固定 3 条 `tips`。
- 每个 case 含稳定 `id/title/description/prompt`；共享 `Translations.agents.newChatGuide` 含案例区、Tips、加载、失败、重试和草稿三选一文案。
- `types.ts` 可从 `core/agents/new-chat` import 类型；`new-chat.ts` 不得反向 import i18n。

- [ ] **Step 1：写失败测试**

在 node 测试中断言：四个 ID 顺序与设计表一致；`resolveAgentName("card-poster")` 与 `resolveAgentName("course-ppt")` 经守卫分别命中 Creative/PPT；`test-agent` 不命中。对 zh/en 两语言逐 Agent 断言恰好 4 个非空案例、3 条 Tips、案例 ID 集合一致；Creative IDs 固定为 `knowledge-card/course-poster/infographic/series-card`，Co-writer IDs 固定为 `article/case/script/podcast`，其 prompt 分别含对应 `[类型: …]` 和 `[从零创作]`。四个中文 Hero 标题逐字等于设计 §5。

- [ ] **Step 2：确认因模块和翻译键缺失而失败**

Run: `cd frontend && pnpm exec vitest run --project node tests/unit/core/agents/new-chat.test.ts`

Expected: FAIL，原因是 `new-chat.ts` 尚不存在；若先出现 import harness 拼写错误，修正后必须看到这一功能原因。

- [ ] **Step 3：最小实现内容与类型**

创建无 React 依赖的 ID 常量/守卫并从 agents index 导出；扩展 i18n 类型和两个语言包。案例自然语言围绕设计 §5，英文 prompt 翻译自然语言但保留 Co-writer 标记；不得写入 raw Skill 名、虚构能力或自动提交指令。

- [ ] **Step 4：确认内容测试和静态门禁通过**

Run:
```bash
cd frontend
pnpm exec vitest run --project node tests/unit/core/agents/new-chat.test.ts
pnpm exec tsc --noEmit
pnpm exec eslint src/core/agents/new-chat.ts src/core/agents/index.ts src/core/i18n/locales/types.ts src/core/i18n/locales/zh-CN.ts src/core/i18n/locales/en-US.ts
```
Expected: 全部 exit 0。

- [ ] **Step 5：提交**

```bash
git add frontend/src/core/agents/new-chat.ts frontend/src/core/agents/index.ts frontend/src/core/i18n/locales/types.ts frontend/src/core/i18n/locales/zh-CN.ts frontend/src/core/i18n/locales/en-US.ts frontend/tests/unit/core/agents/new-chat.test.ts
git commit -m "feat(agent): 添加新会话双语案例内容契约"
```

---

### Task 2：实现共享页面级引导展示组件

**Files:**
- Create: `frontend/src/components/workspace/agent-new-chat-guide.tsx`
- Test: `frontend/tests/unit/components/workspace/agent-new-chat-guide.dom.test.tsx`

**Interfaces:**
- Consumes Task 1 的 `AgentNewChatGuideId`、i18n copy 和现有 `agentAppearance()`。
- Produces `AgentNewChatGuide({ agentName, handoffReady, status, disabled, onRetry, children })`。
- `agentName: AgentNewChatGuideId`；`handoffReady: boolean`；`status: "loading" | "error" | "ready"`；`disabled?: boolean`；`onRetry?: () => void`；`children: ReactNode` 为现有 InputBox。
- 组件顺序固定 Hero → 4 案例 → children/InputBox → 3 Tips；loading/error 不渲染案例，error 显示重试。

- [ ] **Step 1：写展示失败测试**

用真实 I18nProvider 和简单 `<textarea name="message">` child 渲染。断言：中文 Hero/短名/4 卡/3 Tips；图标来自 `agentAppearance` 而非 Bot；案例 button 的 accessible name 为案例标题；组件含正常文档流容器而非 absolute/固定 translate；`handoffReady=false` 或 `disabled=true` 时按钮禁用；loading/error 无案例，error retry 调用一次 `onRetry`。

- [ ] **Step 2：确认组件不存在导致失败**

Run: `cd frontend && pnpm exec vitest run --project dom tests/unit/components/workspace/agent-new-chat-guide.dom.test.tsx`

Expected: FAIL，原因是组件模块不存在。

- [ ] **Step 3：实现最小展示**

实现上述 props、语义 heading/button、两列桌面/单列移动端类、可滚动容器和状态分支；从 i18n 与 appearance 取词/图标，不建立复制表。此时案例只负责展示，预填由 Task 3 接入；不得添加自动提交。

- [ ] **Step 4：确认展示测试与格式通过**

Run:
```bash
cd frontend
pnpm exec vitest run --project dom tests/unit/components/workspace/agent-new-chat-guide.dom.test.tsx
pnpm exec prettier --check src/components/workspace/agent-new-chat-guide.tsx tests/unit/components/workspace/agent-new-chat-guide.dom.test.tsx
```
Expected: 全部 PASS / exit 0。

- [ ] **Step 5：提交**

```bash
git add frontend/src/components/workspace/agent-new-chat-guide.tsx frontend/tests/unit/components/workspace/agent-new-chat-guide.dom.test.tsx
git commit -m "feat(agent): 新增任务型新会话引导组件"
```

---

### Task 3：实现案例预填、草稿三选一与聚焦

**Files:**
- Modify: `frontend/src/components/workspace/agent-new-chat-guide.tsx`
- Test: `frontend/tests/unit/components/workspace/agent-new-chat-guide.dom.test.tsx`

**Interfaces:**
- Consumes 现有 `usePromptInputController().textInput: { value, setInput }` 和 registry Dialog 的只读使用；不修改 `prompt-input.tsx`。
- Task 2 的对外 props 不变；组件内部管理 pending prompt 和确认框。
- 空/纯空白草稿直接 set prompt；非空草稿经确认后 replace 为 prompt，或 append 为“原草稿 + 必要的一个换行 + prompt”；取消不改值。所有路径只聚焦，不提交。

- [ ] **Step 1：增加行为失败测试**

在 PromptInputProvider 下覆盖：空草稿点击写入当前 locale 的精确 prompt；非空点击先开框且值不变；取消不变；替换精确等于 prompt；追加保留原草稿并以换行连接；替换/追加后 textarea 聚焦且光标在末尾，不选中 Co-writer 类型标记；handoff 未 ready/disabled 时无变化；locale 切换只改变后续案例，不改已经预填的值。

- [ ] **Step 2：确认因缺少预填和确认框而失败**

Run: `cd frontend && pnpm exec vitest run --project dom tests/unit/components/workspace/agent-new-chat-guide.dom.test.tsx`

Expected: 新行为用例 FAIL（textarea 未取得 prompt / 确认框不存在），Task 2 展示用例仍 PASS。

- [ ] **Step 3：实现最小状态机**

在组件内使用现有 controller、Dialog 和 i18n 文案实现三选一；状态提交后用一次短任务聚焦现有 `textarea[name='message']`，不引入全局输入状态、不自动 `requestSubmit`、不调用 `onSubmit`。追加不得删除原草稿内容。

- [ ] **Step 4：确认全部组件测试通过**

Run: `cd frontend && pnpm exec vitest run --project dom tests/unit/components/workspace/agent-new-chat-guide.dom.test.tsx`

Expected: 展示与全部行为用例 PASS。

- [ ] **Step 5：提交**

```bash
git add frontend/src/components/workspace/agent-new-chat-guide.tsx frontend/tests/unit/components/workspace/agent-new-chat-guide.dom.test.tsx
git commit -m "feat(agent): 完善案例预填与草稿确认交互"
```

---

### Task 4：显式门控目标新会话的旧建议区

**Files:**
- Modify: `frontend/src/components/workspace/input-box.tsx`
- Test: `frontend/tests/unit/components/workspace/input-box-new-thread-suggestions.dom.test.tsx`

**Interfaces:**
- Produces `InputBox` 可选 prop `showNewThreadSuggestions?: boolean`，默认 `true`。
- 唯一行为变化：现有 `isNewThread && mounted && mode !== "skill"` 渲染条件 additionally 要求 `showNewThreadSuggestions`。
- 非目标调用方不传 prop；不得改 `SuggestionList`、follow-up、上传、模型或提交逻辑。

- [ ] **Step 1：写两个方向的失败/回归测试**

沿用现有 InputBox harness。传 `false + isNewThread=true` 时，分别用有专属 skills/prompts 与 `skills=[] + 全站 enabled skills` 两种 fixture，断言 raw Skill、惊喜、Agent chip、通用建议、创建菜单和分类列表均不在 DOM。默认不传 prop 时，原有专属 chips 和 `skills=[]` 的全站回退仍显示，覆盖 vimax 语义。

- [ ] **Step 2：确认新 prop 尚未生效**

Run: `cd frontend && pnpm exec vitest run --project dom tests/unit/components/workspace/input-box-new-thread-suggestions.dom.test.tsx`

Expected: `showNewThreadSuggestions=false` 用例 FAIL，旧建议仍在；默认行为回归用例 PASS。

- [ ] **Step 3：添加单点门控**

增加默认 true 的可选 prop，并只修改新会话 SuggestionList 的渲染谓词。不得通过 CSS 隐藏、空数组或删除 SuggestionList 实现。

- [ ] **Step 4：运行定向和 InputBox 相邻回归**

Run:
```bash
cd frontend
pnpm exec vitest run --project dom tests/unit/components/workspace/input-box-new-thread-suggestions.dom.test.tsx tests/unit/components/workspace/input-box-polish.dom.test.tsx tests/unit/components/workspace/input-box-thinking-unavailable.dom.test.tsx tests/unit/components/workspace/input-box-mode-decouple.dom.test.tsx
```
Expected: 全部 PASS。

- [ ] **Step 5：提交**

```bash
git add frontend/src/components/workspace/input-box.tsx frontend/tests/unit/components/workspace/input-box-new-thread-suggestions.dom.test.tsx
git commit -m "feat(agent): 支持新会话显式关闭旧建议区"
```

---

### Task 5：接入页面布局并完成真实行为级验收

**Files:**
- Modify: `frontend/src/core/agents/hooks.ts`
- Modify: `frontend/src/app/workspace/agents/[agent_name]/chats/[thread_id]/page.tsx`
- Test: `frontend/tests/unit/app/workspace/agents/agent-chat-page.dom.test.tsx`
- Modify: `frontend/tests/e2e/utils/mock-api.ts`
- Create: `frontend/tests/e2e/agent-new-chat-guide.spec.ts`

**Interfaces:**
- Consumes Tasks 1–4 的守卫、guide、`showNewThreadSuggestions`。
- `useAgent(name)` 增加返回既有 query 的 `refetch`；其他字段和请求不变。
- 目标判定为 `isNewThread && isAgentNewChatGuideId(resolveAgentName(agentNameParam))`；已有线程和非目标 Agent 走原布局。
- 页面内非导出 wrapper 使用现有 `useMounted()` 得出 `handoffReady`，并以 `prefillSearchParams.toString()` 为 guide wrapper key；不新增 effect 内 setState 或 ESLint suppression。
- `MockAgent` 允许可选 `skills?: string[] | null`、`suggestion_prompts?: { label: string; prompt: string }[] | null`；响应原样透传，既有 fixture 不变。

- [ ] **Step 1：在实现前写页面 DOM 与完整行为级 E2E**

页面 DOM 用可控 mocks 渲染真实组合，覆盖：目标新会话显示专属 Hero、顶栏短名且无 raw ID；旧别名命中对应 guide；已有目标线程不显示 guide 且保留消息；非目标新会话仍显示 `AgentWelcome`；loading/error 不显示案例，retry 调用 `refetch`；`?msg=` 仍由页面 effect 写入且 guide 只在 mounted/ready 后开放；静态 Demo 继续禁用输入。

新 E2E 使用真实页面、真实 i18n copy 和全 mock API，收集 `pageerror` 并记录 `/threads` POST 与 `/runs/stream` 请求数，至少覆盖：
- 参数化四 Agent：短名/Hero/4 卡/Tips，逐一真实点击全部 16 卡并断言精确 prompt 与聚焦；后续点击走替换，发送计数始终 0；无 raw Skill/旧 chips/raw ID。
- Co-writer 非空草稿取消与追加；Creative 不出现研究写作/视频案例。
- `?msg=` 和 sessionStorage 长文本优先，点击案例先出现三选一且取消后 handoff 原文不变，不自动发送。
- 英文 locale 显示英文自然语言 prompt，Co-writer 标记仍为原字面值；已填草稿不因重新渲染改变。
- 案例预填后由用户按 Enter 才恰好创建一次线程/发送一次，guide 消失并进入既有消息流。
- 已有目标线程无 guide；`card-poster` / `course-ppt` 别名可用；非目标 test-agent 保持原欢迎区，vimax-video 空技能仍显示 mock 全站 Skill。
- Agent 首次 500 后 retry 成功；loading 时无案例且不可提交。
- 390px 无横向溢出，键盘可聚焦/激活案例；每个测试结束 `pageErrors=[]`。

- [ ] **Step 2：确认旧页面同时不能通过 DOM 与 E2E 行为断言**

Run:
```bash
cd frontend
pnpm exec vitest run --project dom tests/unit/app/workspace/agents/agent-chat-page.dom.test.tsx
pnpm exec playwright test tests/e2e/agent-new-chat-guide.spec.ts --workers=1
```

Expected: 目标 Hero/短名/page 布局/retry 与四路由案例 E2E FAIL；已有线程和非目标回归用例 PASS。若 mock 无法渲染页面，先修 harness，不把它计作红灯证据。不得在实现后才首次编写这些 E2E。

- [ ] **Step 3：实现最小页面分支**

从 `useAgent` 取 `isLoading/error/refetch`，目标新会话以正常文档流渲染 `AgentNewChatGuide` 和同一个 InputBox，传 `showNewThreadSuggestions=false`；加载/失败时禁用输入，失败 retry 只调用 `refetch`。目标新会话顶栏使用 `displayName`。保留 handoff 原 effect、prefillRunRef、原提交与原生 history 替换；非目标和已有会话保留原 absolute 布局与 AgentWelcome。产品修改不得超出 Tasks 1–5 allow 文件。

- [ ] **Step 4：确认 DOM、完整 E2E 与静态检查通过**

Run:
```bash
cd frontend
pnpm exec vitest run --project dom tests/unit/app/workspace/agents/agent-chat-page.dom.test.tsx tests/unit/components/workspace/agent-new-chat-guide.dom.test.tsx tests/unit/components/workspace/input-box-new-thread-suggestions.dom.test.tsx
pnpm exec playwright test tests/e2e/agent-new-chat-guide.spec.ts --workers=1
pnpm exec eslint src/core/agents/hooks.ts 'src/app/workspace/agents/[agent_name]/chats/[thread_id]/page.tsx'
pnpm exec tsc --noEmit
```
Expected: 全部 PASS / exit 0；16 次案例点击、发送计数和 pageerror 断言实际执行，不得新增 react-hooks suppression。若失败，先区分选择器、mock 缺失与产品回归，不用固定延时、隐藏 DOM 或删除断言制造通过。

- [ ] **Step 5：提交**

```bash
git add frontend/src/core/agents/hooks.ts 'frontend/src/app/workspace/agents/[agent_name]/chats/[thread_id]/page.tsx' frontend/tests/unit/app/workspace/agents/agent-chat-page.dom.test.tsx frontend/tests/e2e/utils/mock-api.ts frontend/tests/e2e/agent-new-chat-guide.spec.ts
git commit -m "feat(agent): 接入四个新会话任务引导页"
```

---

### Task 6：运行相邻历史与非目标浏览器回归

**Files:**
- Read-only: `frontend/tests/e2e/agent-new-chat-guide.spec.ts`
- Read-only: `frontend/tests/e2e/agent-chat.spec.ts`
- Read-only: `frontend/tests/e2e/thread-history.spec.ts`

**Interfaces:**
- Consumes Task 5 已转绿的行为 spec；本任务是纯验证检查点，不新增实现，不伪造失败测试，不产生空提交。

- [ ] **Step 1：运行新引导与相邻旧 E2E**

Run:
```bash
cd frontend
pnpm exec playwright test tests/e2e/agent-new-chat-guide.spec.ts tests/e2e/agent-chat.spec.ts tests/e2e/thread-history.spec.ts --workers=1
```
Expected: 全部 PASS；输出无 skipped/failed，发送计数与 pageerror 断言实际执行。

- [ ] **Step 2：核对回归结果与测试产物**

检查原始输出和 `git status`，确认非目标 Agent、历史消息与别名路由均真实运行；若出现失败，返回拥有该行为的任务补红灯并修复，未完成前不得进入全量门禁。测试报告不提交。

---

### Task 7：全量门禁、计划勾选与执行 changelog

**Files:**
- Modify: `docs/plans/2026-11-10-agent-new-chat-redesign-plan.md`
- Create: `docs/changelog/changelog-2026-11-10-agent-new-chat-redesign.md`（若跨日则使用实际执行日）
- Read-only: 全部实现与测试文件

**Interfaces:**
- Consumes Tasks 1–6 的本地提交和真实命令输出。
- 不改变产品行为，不在收尾阶段顺手引入新功能。

- [ ] **Step 1：先跑静态、单元与文档门禁**

Run:
```bash
cd frontend && pnpm check
cd frontend && pnpm test
cd frontend && pnpm check:dead
cd .. && python3 scripts/check_docs_convention.py
```
Expected: 全部 exit 0；记录实际测试文件/用例数，不写估计值。预计超过阈值的命令按全局约束后台执行。

- [ ] **Step 2：跑完整 E2E 与既有性能门禁**

Run:
```bash
cd frontend && pnpm test:e2e
cd frontend && pnpm perf:check
```
Expected: 全部 exit 0。不得把 perf 结果描述为目标 Agent 路由的直接性能测量，不改预算。

- [ ] **Step 3：跑仓库根总门禁**

Run: `make check`

Expected: exit 0，包含 backend 静态检查、frontend check/knip/i18n 与 docs convention；本批未改 backend，因此不新增 backend pytest 要求，但根门禁不得跳过。

- [ ] **Step 4：核对交付与干净范围**

运行 `git status --short --branch`、 `git diff --check` 和基准以来的 `git log --oneline`。确认无 ban 路径、无测试产物/日志入库、无后台任务、无真实付费请求；逐项勾选 Tasks 1–6，只有实际完成的步骤才能勾选。

- [ ] **Step 5：写 changelog 并提交文档收尾**

changelog 必须含「验收记录」，每行写真实命令与关键输出；行为级覆盖四入口/16 卡、草稿三选一、handoff、历史/别名、非目标回退、错误重试、移动端和零 pageerror。真实链路若未获授权，明确写“未执行，不作为本次通过项”。更新计划状态后运行文档检查，再提交：

```bash
git add docs/plans/2026-11-10-agent-new-chat-redesign-plan.md docs/changelog/changelog-2026-11-10-agent-new-chat-redesign.md
git commit -m "docs(agent): 记录新会话引导页验收结果"
```

最终只报告本地 commits，不 push、不建 PR、不部署；向用户汇总修改文件、测试证据、真实链路未执行项和建议审查重点。
