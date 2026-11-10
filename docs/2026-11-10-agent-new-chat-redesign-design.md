# Agent 新会话引导页重构设计

> **状态**：已批准（2026-11-10，含第二轮评审修订）；实施任务与执行状态以 `plans/2026-11-10-agent-new-chat-redesign-plan.md` 为准。
> **TL;DR**：为 course-video、ppt-master、creative-studio、co-writer 的 `chats/new` 建立页面级双语任务引导，以 16 张案例预填取代原始 Skill 名；本批只改前端，并剥离 Co-writer 阶段内部标记；已有对话、提交链路、Agent 技能与后端种子均不变。

## 1. 背景与问题

当前 Agent 新会话页由通用 Bot 图标、Agent 原始 `name/description`、绝对定位的输入框和 Skill 按钮组成。主要问题不是单纯样式，而是信息架构没有围绕用户任务组织：

- 原始名称和技术描述不能告诉用户应该先提供什么。
- `/skill ...` 预填机制虽然存在，但技术名称本身没有引导价值。
- 四个 Agent 共享同样的空泛结构，缺少可理解的案例和下一步预期。
- 当前欢迎区是输入框的 absolute 覆盖层，不能通过增加内容高度承载完整 Hero、案例和 Tips。
- Co-writer 的从零创作工作流会返回 `[[STAGE:OUTLINE]]` / `[[STAGE:DRAFT]]`；通用 Agent 消息目前不会像 Writer 面板一样剥离它们。

本设计的用户目标是：进入目标 Agent 后，无需理解 Skill、Remotion、内部模式名或阶段标记，就能判断“能做什么、该提供什么、点击案例后会发生什么”。

## 2. 目标与非目标

### 目标

1. 四个目标 Agent 的新会话页显示专属标题、真实案例、现有输入框和简短上手提示。
2. 案例点击后预填真实提示词并聚焦输入框，但不自动发送。
3. 原始 Skill 名不再承担四个目标新会话页的用户引导职责。
4. 顶栏徽章与 Hero 使用一致的用户可读名称，不再同屏显示 raw agent ID。
5. Co-writer 保留从零创作工作流，但 Agent 对话与导出中不显示内部阶段标记。
6. 既有对话、消息流、发送/停止、上传、模型选择、导出、Tokens 和历史路由保持兼容。
7. 页面延续首页现有的淡紫、白底、细边框、柔和阴影与响应式布局，中英文内容完整。

### 非目标

- 不重写消息流、Agent 执行器、工具权限或提示词运行时。
- 不新增后端 API、数据库字段或付费生成调用。
- 本批不修改任何 `backend/**`、Agent seed、SOUL、`SEED_VERSION` 或技能白名单。
- 不删除 Creative Studio 的研究写作或视频能力，只让其新会话案例聚焦图片、海报和知识卡片。
- 不修改其他 Agent 的新会话内容；共享代码必须保留其现有缺省行为。
- 不扩展性能预算路由，不调整既有预算数值，不新增部署或 reseed 工作。

## 3. 方案选择

### 方案 A：仅调整样式

保留原始描述和 Skill 按钮，只改颜色、间距和卡片边框。成本最低，但无法解决任务引导与信息架构问题，不采用。

### 方案 B：共享页面级引导 + 专属双语内容 + 内部标记清理（采用）

在 AgentChatPage 的新会话布局层接入共享引导组件，四个 Agent 使用各自的 i18n 内容；图标复用现有 appearance SoT，点击案例复用现有 prompt controller。Co-writer 的 `[[STAGE:…]]` 在现有 AI 消息/导出清理入口剥离。只在前端替换目标新会话的引导与建议区。

### 方案 C：同时迁移技能归属

将研究写作技能从 Creative Studio 转给 Co-writer，并修改 seed/SOUL。核验发现 Co-writer 当前只有 `web` 工具组，而标准技能加载需要 `read_file`；在不扩大工具权限的前提下迁移会形成“列出技能但无法读取正文”的断链。该方案还涉及全局 `SEED_VERSION` 重播，不属于本批。

## 4. 页面范围与布局

仅归一化后的 `course-video`、`ppt-master`、`creative-studio`、`co-writer` 在 `chats/new` 且仍为全新会话时显示新引导页。Agent ID 归一继续复用 `resolveAgentName`，因此旧别名路由保持兼容；已有会话不得再次显示新引导页。

### 目标新会话

使用 page 级正常文档流和可纵向滚动容器，不再把完整引导塞进 `InputBox.extraHeader`，也不沿用新会话输入框的固定 `translate` 居中。页面顺序为：

1. **任务型 Hero**：专属图标、用户可读标题、一句话价值说明。
2. **案例卡片区**：每个 Agent 4 张任务卡；每张包含名称、简短说明和完整预填提示词。
3. **现有 InputBox**：保持原提交、停止、上传、模型和上下文能力。
4. **上手 Tips**：说明需要准备的内容及下一步，不暴露内部 Skill 名或实现细节。

桌面采用紧凑居中容器与两列卡片，移动端单列；顶栏保留空间。案例区、InputBox、Tips 必须形成不重叠的纵向文档流，整页可滚动。目标新会话不使用初始 `autoFocus`，避免移动端跳过 Hero/案例；点击案例后再聚焦输入框。

提交开始、URL 经原生 `history.replaceState` 切到真实线程后，立即使用已有会话布局，不用 Next 路由替换导致 remount。handoff 状态变化不得通过 key 重挂 InputBox，避免丢失附件、模型弹窗、草稿 effect 或焦点。

### 其他页面

- 已有会话的消息列表、底部输入区和 follow-up 建议不变。
- 非目标 Agent 继续渲染 `AgentWelcome` 和现有建议区，保留原布局。
- `AgentWelcome` 因仍有非目标 Agent 的真实消费者而保留，不创建无人引用的“兼容导出”。
- 不通过隐藏旧 DOM 制造测试通过；目标新会话直接采用新的布局分支。

## 5. Agent 专属内容

| Agent | 顶栏短名 | Hero 主标题 | 4 张案例卡 | 上手提示重点 |
|---|---|---|---|---|
| course-video | 课程视频 | 把知识讲成一堂视频课 | 知识科普、产品教学、原理动画、已有讲稿转视频 | 提供主题/受众/时长或讲稿；先确认讲稿与分镜，再生成视频 |
| ppt-master | PPT 制作 | 从资料到可编辑的 PPT | 从零生成、改造现有 PPT、美化排版、品牌模板 | 可上传源文档或既有 PPT；强调原生可编辑 PPTX |
| creative-studio | 视觉设计 | 让知识成为好看的图片 | 知识卡片、课程海报、信息图、系列视觉卡片 | 提供主题、核心信息、受众、尺寸与风格 |
| co-writer | 协同写作 | 把想法写成好内容 | 公众号文章、商业案例、视频脚本、播客文稿 | 选择内容类型；按既有 SOUL 经历大纲/全文阶段 |

案例文案必须描述用户任务，不出现 auto-video-flow、ppt-master Skill、deep-research 等内部技能名。PPT 的“改造/美化”案例只预填说明，不伪称已上传文件；用户仍需通过现有上传入口提供文件。

PPT 新案例必须有意识地承接现有两条 `suggestion_prompts` 的真实语义：“改造现有 PPT”保留原文/原设计边界，“从零生成”保留“快速生成课件、跳过策略确认直接出稿”的既有快速路径。旧 chips 不再显示不等于丢失这两个工作流；后端种子字段本批不删除。

Co-writer 的预填提示词必须携带现有 `[类型: article|case|script|podcast]` 与 `[从零创作]` 语义标记，确保与当前 SOUL 匹配。标记进入输入框是可接受的，因为它们是当前真实路由契约；案例按钮和说明不暴露内部 Skill 名。英文案例只翻译自然语言正文，保留这些字面标记。

Course-video 不在界面展示 auto-video-flow；其现有 SOUL 和技能配置不修改。

## 6. 内容归属与 i18n

不新增“Agent ID → 标题/说明/图标/案例/Tips”的全局第五元数据表，按既有职责拆分：

- **图标与配色**：继续调用 `agentAppearance()`，不得复制图标或颜色映射。
- **顶栏短名、Hero、案例名称/说明、Tips、预填 prompt**：统一进入 `zh-CN` / `en-US` 及对应 `Translations` 类型，以目标新会话专用的 i18n key 为内容 SoT；配置只使用 Agent ID 和稳定案例 ID 取词。
- **首页入口与媒体**：继续由 `home-course-catalog.tsx` 和首页 `courseItems` 负责。首页动作卡名称与 Agent 新会话 Hero 不是同一语义，本批不做无关统一。
- **Agent 运行时元数据**：后端 `name/description/skills/suggestion_prompts` 不变。

现有 `suggestion_prompts` 链路已核实，但只有 PPT Master 配置了两条 `{label, prompt}`，其他三个目标 Agent 没有，且该字段没有案例说明、稳定 ID 或双语维度。为四个 Agent 把它扩成统一内容源会要求修改更多后端 seed，违背本批范围。因此本批复用的是“整句预填、聚焦、不自动提交”的交互契约和 PPT 既有任务语义，而不是把该字段扩成新后端内容表。

所有可见 UI 文案和 prompt 均进入双语言包与类型系统。现有 `check_i18n_keys.mjs` 对 `Record<AgentNewChatGuideId, …>` 不会递归到每条案例叶子，因此**案例完整性、双语言 ID 对齐、prompt 非空和禁用内部技能名由专门单元测试负责**；不得声称现有 i18n 门禁会自动拦截案例删除。语言切换只影响之后点击的案例，不自动改写已预填或已编辑的输入框文本。

## 7. 案例点击与状态契约

### 空输入框

点击案例后写入该案例当前语言的完整 prompt，聚焦输入框，不创建线程、不提交、不调用生成。

### 已有文本

点击案例先显示“替换 / 追加 / 取消”三选一：

- **替换**：以完整案例 prompt 替换当前草稿。
- **追加**：保留草稿，以换行分隔后追加完整案例 prompt。
- **取消**：不改变草稿。

三个分支都不自动提交；替换或追加完成后聚焦输入框。这里只借鉴 follow-up 的三选一交互，不复用其“确认后自动发送”行为，也不采用旧 suggestion chip 的静默追加。

### handoff 优先级

现有页面 effect 是 `?msg=` / sessionStorage 的唯一消费者，并会替换输入框内容。新增显式完成状态，而不是用 `useMounted()` 代替 handoff 契约：

- 页面计算当前 `handoffKey = prefillSearchParams.toString()`，并记录 `handoffDoneKey`；只有两者相等时才允许案例点击。
- 首次挂载及搜索参数变化时，现有 effect 完成 handoff 读取和必要预填后，将 `handoffDoneKey` 置为当前 key。key 变化时旧完成值自然不匹配，无需额外重置或 remount。
- 这一处 effect-setState 是将已消费的外部 handoff 状态同步给 React 的必要接线，允许单点、带原因的内联 lint 处理；不得加入全局 suppression 文件，也不得改用隐式 mounted 时序。
- `?msg=` 继续优先于 sessionStorage；现有 `prefillRunRef` 去重与消费规则不变。
- handoff 写入后，案例点击同样遵循“已有文本三选一”，不静默覆盖。
- 引导组件挂载时不得写入默认 prompt，也不重复消费 handoff。

## 8. 旧建议区与非目标回归

四个目标 Agent 的新会话由新案例区完整取代旧 `SuggestionList`，以下入口均不再在该状态显示：

| 旧入口 | 四个目标新会话 | 其他 Agent / 已有会话 |
|---|---|---|
| 原始 Skill chips / 全站分类 Skill 列表 | 不显示 | 保持现状 |
| “惊喜一下” | 不显示 | 保持现状 |
| `agentSuggestionPrompts` chips | 不显示，由承接原语义的新案例替代 | 保持现状 |
| 通用 suggestions 与“+ 创建”菜单 | 不显示 | 保持现状 |
| 已有会话 follow-up 建议 | 不适用 | 保持现状 |

InputBox 需要显式的建议区开关，仅在四个目标新会话启用；禁止通过传空 `skills` 数组实现，因为当前空数组会回退为全站 Skill 分类列表。

`vimax-video` 等非目标 Agent 不改变现有空技能数组行为。该语义疑点属于范围外发现，已登记 findings backlog，不在本批顺手修改。

## 9. 技能归属、消息标记与后端边界

本批不做技能迁移，也不修改 Creative Studio / Co-writer 的 seed 或 SOUL：

- Creative Studio 的研究写作、视频和文件相关技能全部保留，只从新会话案例区移除研究写作与视频入口。
- Co-writer 继续使用现有内容类型、`[从零创作]` 与 `web` 搜索能力，不新增 `deep-research` 或 `consulting-analysis`。
- 通用 AI 消息清理入口 `stripInternalMarkers` 复用 `parseStageSignal`，仅剥离开头的 `[[STAGE:OUTLINE]]` / `[[STAGE:DRAFT]]`；正文中间出现的相同文字不误删。现有消息组件和导出调用方保持复用，Writer 面板逻辑不改。
- 不存在顶层 Agent 自动 handoff；本批不暗示请求会自动转交。用户可从首页或既有 Agent 入口自行切换。
- 因不修改 seed，本批不需要也不允许 bump 全局 `SEED_VERSION`，不会触发六个 Agent 的重播或 `.bak.<ts>` 归档。

若未来仍需真实迁移写作技能，必须另开独立批次，先解决 `file:read` 权限、SOUL 路由与引用格式冲突，再评估全局 reseed 对全部 Agent 运行时定制的影响。该工作不属于本设计的验收范围。

## 10. 加载、错误与可用性

- Agent 信息加载期间，案例与提交入口不可操作，使用明确加载态；不得把 `undefined` 当成空技能并显示全站 Skill。
- Agent 加载失败或未知 Agent 时显示真实错误与重试入口，重试只重新执行现有 `getAgent` 请求；不渲染伪案例或假装可提交。
- 不改变 `useAgent` 的全局自动重试策略。行为测试必须在用户点击重试前持续失败，证明恢复来自手动操作而非 React Query 自动重试。
- 静态 Demo 禁用态沿用现有提示，不因案例预填而绕过提交禁用。
- 桌面与 390px 移动端无横向或纵向遮挡；长内容可滚动，键盘焦点可见，案例卡和对话框可用键盘操作。
- 页面不自动播放媒体，不新增自动付费调用。

## 11. 验收标准

### 行为级（自动化）

1. 四个 `chats/new` 路由均显示正确短名、Hero、4 张案例和 Tips，无原始 Skill 名或通用 Bot 占位。
2. 每张案例实际点击后，输入框获得该案例当前语言的正确 prompt，聚焦且不自动提交。
3. 空草稿直接预填；非空草稿的替换、追加、取消均符合 §7，三个分支均不发送。
4. `?msg=` 与长文本 sessionStorage handoff 优先且不被覆盖；当前 handoff 未完成前案例不可点击，参数变化不重挂 InputBox，语言切换不改写已有草稿。
5. 已有对话不显示新引导页；历史消息、发送、停止、上传、模型选择、导出和 Tokens 不受影响。
6. 四个目标新会话不显示 §8 的任何旧建议；非目标 Agent（含 `vimax-video`）保持原建议行为。
7. Creative Studio 新页面不出现研究写作/视频案例；Co-writer 每张案例与真实 SOUL 类型标记匹配；PPT 两条旧建议的真实任务语义仍在。
8. Co-writer 模型返回的开头阶段标记在 Agent 对话与导出中不可见，正文不受误删。
9. Creative Studio 与 PPT Master 旧别名、四个 Agent 的历史线程继续可用。
10. 加载、失败、静态 Demo 禁用状态诚实可恢复；手动重试前持续失败，点击后恢复。
11. 桌面、390px 移动端和键盘流程无 JavaScript 页面错误；案例区.bottom ≤ InputBox.top 且 InputBox.bottom ≤ Tips.top。
12. 自动化测试全部 mock 后端，不触发真实付费生成。

### 实现级与回归

- 双语言内容测试覆盖四个 ID、各 4 个稳定案例 ID、非空 prompt、Co-writer 字面标记、PPT 旧任务语义及禁用内部 Skill 名。
- DOM 测试覆盖预填、替换、追加、取消、聚焦、handoff 门槛、加载/错误、已有会话不显示和非目标缺省行为；页面级测试允许重 stub，只负责分支与 props 接线。
- 消息工具测试覆盖 OUTLINE/DRAFT 开头剥离、正文中间标记保留和普通文本不变。
- Playwright 覆盖四个新会话路由、16 张案例实际点击、顶栏命名、旧 chips 消失、阶段标记、真实 mock 提交、非重叠布局和历史路由回归。
- `pnpm check`、`pnpm test`、`pnpm test:e2e`、`pnpm perf:check`、根 `make check`（含 knip/i18n）与文档检查全部通过。
- `perf:check` 当前只测五个既有路由，只能捕获共享包等间接影响，**不能声称直接覆盖四个 Agent 新会话页**。本批不修改性能预算；如需直接路由预算，另开性能门禁批次。

### 真实链路人工验收（不在默认自动化中付费）

实现完成后，每个 Agent 至少选 1 张案例做一次真实链路冒烟，检查提示词能否进入预期工作流，而非仅检查“非空”。Course-video 可停在讲稿确认、PPT Master 可停在策略确认，避免自动渲染；涉及图片或其他付费生成的步骤须由用户明确授权。真实链路未执行时，changelog 不得宣称该项已验证。

## 12. 评审决策记录

| 评审议题 | 本设计决策 |
|---|---|
| seed 修改需要 `SEED_VERSION` | 事实成立；通过移除本批全部 seed/SOUL 修改消解，不在前端批次 bump 全局版本 |
| 复用 `suggestion_prompts` | 复用交互与 PPT 任务语义，不扩成四 Agent 后端内容源 |
| `AgentWelcome` 与 knip | 保留给非目标 Agent 使用，不保留无消费者的导出 |
| `extraHeader` 布局 | 改为 page 级正常文档流；已有会话和非目标 Agent 不变；E2E 断言纵向不重叠 |
| Creative 研究模式断链 | 不删模式、不裁技能，只调整新会话展示，因此无运行能力断链 |
| 追加或替换 | 非空草稿三选一；只预填，不沿用 follow-up 的自动发送 |
| i18n | 所有文案与 prompt 双语；Record 内案例完整性由专门测试守护，Co-writer 契约标记保持字面值 |
| 旧 chips、顶栏、handoff | 分别按 §8、§4、§7 固化；handoff 用显式完成 key，不 remount InputBox |
| Co-writer 阶段标记 | 用户确认保留从零创作；扩展前端 `stripInternalMarkers` 复用 `parseStageSignal`，不改后端 |
| Agent 查询重试 | 不改全局 retry；测试持续失败到人工点击，排除自动重试假绿 |
| Playwright 内容 SoT | 使用相对路径导入纯 locale，并以 `playwright test --list` 验证编译；不复制 32 条 prompt |
| perf 与技能行为验收 | 性能门禁只作间接回归；本批无技能变更，真实工作流采用单独授权的人工冒烟 |
| 批次与文档 | 当前仅前端批次；设计复审和 grilling 已完成，正式 plan 为唯一执行清单 |
| 基准漂移 | 保留 `fe4654c1`；实际 diff 同时包含五个旧批次脏文件，具备漂移信号，不改基准 |

## 13. 执行前置与范围控制

当前工作区另有创建框优化批次的未提交修改，且最近一轮 E2E 存在失败。新批次实施前必须先完成该批次，不得把两批代码混入同一提交。

正式计划记录基准 commit、逐项 TDD 任务、allow/ban 路径、评审决策、changelog 与回滚方式。按用户当前边界只提交本地 commits，不 push、不建 PR；若未来批准推送且触及核心路径，再遵守仓库 PR/OCR 规则。部署不属于本批。
