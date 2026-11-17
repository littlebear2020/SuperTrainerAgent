# Agent Showcase 视觉保真修复计划（P0/P1）

> **状态**：已执行（2026-11-17；P0 PR #15（合入 commit `62f1e1b4`）+ P1 PR #16；变更日志：`docs/changelog/changelog-2026-11-17-agent-showcase-visual-fidelity.md`）。追加：2026-11-17 用户本地预览反馈后执行 polish 批次（Hero 按首页比例缩小、composer 按 demo 860px、移除 tips/badge/note），见 `docs/changelog/changelog-2026-11-17-agent-showcase-visual-polish.md`；2026-11-17 第二轮预览反馈（人物列固定 180px、删除 hero 标签、统一 indigo 强调色、creative-studio 更名「课程海报」、筛选计数同步/再点回全部）见 `docs/changelog/changelog-2026-11-17-agent-showcase-visual-polish-2.md`；2026-11-17 第三轮预览反馈（Hero 与 composer 同宽 860 中心列、人物 240/200/190、顶部 ambient 柔化、composer 140px 卡片化，并处理 #18 review 的筛选不变量/accent 对齐）见 `docs/changelog/changelog-2026-11-17-agent-showcase-hero-composer-design.md`。原定稿说明保留在本行之后的 TL;DR。
> **TL;DR**：PR #12/#13 已把案例、媒体和交互接入 4 个 Agent 新会话页，但当前实现把 demo 的“透明人物 + 连续左对齐构图”改成了“头像卡 + 三段不同宽度 + 空胶囊输入框”，所以功能可用但视觉离 demo 很远。本计划不重写数据/媒体逻辑，也不替换项目侧栏、顶栏或真实 `InputBox`；只在现有项目壳内做视觉修复：人物去卡片、统一 1180 容器、输入框补 Agent 专属 placeholder 与任务信息、去掉全宽背景补丁、提高卡片层级，并补视觉回归验收。P0 先拉回骨架，P1 再修质感。
> **Spec / 需求来源**：用户 2026-11-17 对 PR #12/#13 的反馈；当前 `origin/main` 截图 `.screenshots/agent-showcase/refinement/*`；当前实现 `frontend/src/components/workspace/agent-showcase/*`；demo `temp/agent-listenhub-demo-v2/bundle.html` 与 `temp/agent-listenhub-demo-v2/.work/shots/hero-viewport-*.png`。

**定稿代码基线**：`origin/main` = `41b724c2`（PR #12/#13 已合入）。当前 `home-plan` worktree 有无关未提交修改，实施必须从 `origin/main` 新建干净 worktree，不在当前分支直接改。

**执行方式**：单执行者、前端视觉修复；不重写媒体数据/交互，不改 backend/skills/Agent runtime。P0 与 P1 分两个 PR，P0 先合入并完成截图验收。

---

## 一、问题结论与证据

### 结论

PR #12/#13 实现了“有案例、能打开、能回填、测试全绿”，但**没有实现 demo 的视觉构图**。当前页面更像“项目里的资源列表”，不是“有角色、有主标题、有案例墙的展示页”。

### 证据

| 问题 | 当前实现 | 与 demo 的差异 |
|---|---|---|
| Hero 人物 | `showcase-hero.tsx` 给 `<img>` 加 `rounded-[24px] border border-indigo-200/50 bg-indigo-50 shadow-sm` | demo 是透明人物 + 柔光，没有头像卡边框；当前像个人资料卡 |
| 内容宽度 | Hero `max-w-4xl`（896）、输入 `max-w-5xl`（1024）、Showcase `max-w-7xl`（1280） | demo 是一条连续左对齐构图；当前三块各自居中，左右边缘不齐 |
| 输入框 | 全局 placeholder“今天我能为你做些什么？”；上传按钮只有图标；模型/模式是动态文字但 mock 下可能为空 | demo 是 Agent 专属任务提示；当前 placeholder 泛化，上传入口没有可见文字，整体像空胶囊 |
| 背景 | `agent-showcase.module.css` 用 `background: var(--background) + 0 0 0 100vmax + clip-path` 盖全宽米色 | 项目画布是 `WorkspaceContent` 的 `FloatingBlobsBackground` + `--blob-root-bg`；当前把 surface token 当整页背景，出现米色块和接缝 |
| 卡片层级 | 视频标题 15px、视觉标题 13px、标签 10px、圆角 13–15px、较多边框 | demo 图片更主导、标题更有分量、信息更克制 |
| 左栏 | `lg:grid-cols-[220px...] xl:[260px...]` | demo 左栏更宽（约 280–300），步骤不挤 |

### 根因

1. **把“项目风格”理解成了“给 demo 组件套项目 token”**，但没有保留 demo 的空间关系（左对齐、统一宽度、透明人物、连续背景）。
2. **上一批把白底人物放进了浅色圆角卡**：这是当时的设计选择，但新反馈不再接受；本批正式反转该视觉决策，改为透明人物/无边框柔光。
3. **三段 max-width 是补丁式布局**：Hero 用 896 居中、输入用 1024、案例用 1280，缺少单一内容容器。
4. **输入框只改了圆角，没有补任务上下文**：placeholder 仍是全局句，Agent 专属信息丢失。
5. **全宽背景补丁制造了接缝**：为了模拟 demo 的整页白底，盖了一块 `100vmax` 的背景，和项目壳脱节。
6. **验收只看行为/对比度/首屏，没有视觉构图门**：测试无法发现“头像卡、宽度不齐、placeholder 泛化”。

## 二、目标与非目标

### 目标

1. **Hero 去卡片**：透明人物（或至少无边框柔光底） + 小号 Agent 标签 + 两行大字；左对齐，不再像头像卡。保留项目现有侧栏/顶栏。
2. **单一内容容器**：Hero、输入框、案例区共用 `max-w-[1180px]`（或项目可用宽度），左右边缘偏差 ≤8px。
3. **输入框保留项目 InputBox/glass 逻辑**，但补 Agent 专属 placeholder；保留现有动态模式/模型文字，上传按钮文字作为 P1 可选；圆角 24–28px，宽度 100%，不再像空胶囊。
4. **去掉全宽背景补丁**：让项目页面背景/环境光透出，案例卡自身用 `bg-card`，没有内容块接缝。
5. **提高案例卡视觉权重**：视频/PPT 标题 18–20px、视觉标题 15px、正文 13px；减少 10px 标签和多余边框；图片更主导。
6. **补视觉回归**：1440/1024/390、亮/暗色参考截图；左边缘、输入宽度/placeholder、首卡可见、Hero 无卡片的硬断言。

### 非目标

- 不重写 `showcase.ts` 数据、媒体 Dialog、音频播放器、transcript、下载逻辑。
- 不 revert PR #12/#13；保留其功能、i18n、测试。
- 不改 backend、skills/public/ppt-master、Agent runtime、thread/stream、路由、鉴权。
- 不改其他 Agent（co-writer 等）的旧 header/案例；只修 4 个 showcase Agent 的视觉层。
- **不替换/隐藏项目侧栏、顶栏；不替换真实 `InputBox`**。P0/P1 的目标是在现有项目壳内最大化接近 demo，不把项目页面改成 demo 单页。
- 不新增素材类型、不重做 PPT/视频/播客/视觉产物；只允许替换 4 张 hero 透明资产。
- 不改全局 `InputBox` 行为、全局 `globals.css`、`components/ui/**`、`ai-elements/**`。

## 三、目标视觉规格

### 1. 内容容器

- 统一类：`showcase-shell` 或 Tailwind `mx-auto w-full max-w-[1180px] px-4 sm:px-6`。
- 1440 视口（有侧栏）实际内容宽度约 1115–1180；1920 视口上限 1180。
- Hero、输入框 frame、Showcase section 的左边缘偏差 ≤8px，右边缘同样。
- 删除 Hero `max-w-4xl`、输入 `max-w-5xl`、Showcase `max-w-7xl` 的混用；容器只保留一个。
- Showcase 左栏：≥1280 桌面 280px；1024 保持约 240/按截图实测，不与卡片抢宽；平板/移动单列。

### 2. Hero

- 人物：桌面高 **230–250px**，移动 **170–190px**；优先透明 WebP。
- 人物外层：
  - **必须**：无边框、无圆角卡片、无 `bg-indigo-50`、无 `shadow-sm`；
  - 允许：无边框的柔和径向光晕（使用页面背景色/Agent 色 6–10% 透明度）；
  - 兜底：浅色底板必须是无边框、与页面背景同色系的柔光，不得出现白色矩形。
- 大字：桌面 **32–34px**、移动 **24–26px**；固定两行；左对齐；行高 1.3–1.4。
- 小号 Agent 标签：11–12px，Agent 强调色，配一条 24–28px 短线；不要再使用 10px 全大写 tracking 过宽的“仪表盘 eyebrow”。
- 布局：人物在左、文字在右；桌面 gap 32–40；移动堆叠、文字居中或左对齐均可，但不要出现头像卡。
- 垂直间距：Hero 顶部与 guide 顶部之间 12–20px，Hero 与输入框 16–24px；不要大段空白。

### 3. 输入框

- 保留 `InputBox` 的真实逻辑和 `glass-input-box`；只通过 `guide-composer-frame` 控制宽度/圆角。
- 宽度：内容容器的 100%（约 1024–1180），移动 100%。
- 圆角：桌面 24–28px，移动 22–24px；不要做成完全胶囊。
- 高度：保持项目现有高度，约 96–110px；不额外加高。
- Placeholder：**按 Agent 专属**：
  - 课程视频：“粘贴讲稿，或输入课程主题；也可以上传 docx、pdf、md 文稿”
  - PPT：“输入 PPT 主题，或上传文档、现有 PPT”
  - 播客：“输入主题、粘贴公开网页链接，或上传文档”
  - 视觉设计：“输入课程主题、核心知识点和想要的风格”
  - 中文/英文各一份；放在 `ShowcaseAgent.inputPlaceholder`。

| Agent | 中文 placeholder | English placeholder |
|---|---|---|
| 课程视频 | 粘贴讲稿，或输入课程主题；也可以上传 docx、pdf、md 文稿 | Paste a script or topic; upload DOCX, PDF, or MD. |
| PPT | 输入 PPT 主题，或上传文档、现有 PPT | Enter a topic, or upload a document or existing PPT. |
| 播客 | 输入主题、粘贴公开网页链接，或上传文档 | Enter a topic, paste a public URL, or upload a document. |
| 视觉设计 | 输入课程主题、核心知识点和想要的风格 | Enter a topic, key points, and desired style. |

- 390px 下长 placeholder 必须正常换行，不得与工具栏重叠、不得把输入框异常加高；以截图与 boundingBox 断言验收。
- 工具信息：当前 `InputBox` 已动态显示所选模型名，模式菜单也是真实当前值；**不要**为了仿 demo 固定写“Pro / 默认模型”。真正缺少的是上传按钮文字。P0 只交付 placeholder；P1 可选增加 `showAttachmentLabel`（默认 false，仅 showcase 新会话开启，390px 隐藏文字），不新增 `showXxx`/`compact` 这类不存在的配置。
- 聚焦环/玻璃边框沿用项目；不要为 guide 单独改颜色。

### 4. 背景与卡片

- 删除 `.showcaseGuide` 的 `background`、`box-shadow: 0 0 0 100vmax`、`clip-path`；guide 根节点改为透明，让 `WorkspaceContent` 的 `FloatingBlobsBackground` 透出。
- 页面画布单一事实源：浅色 `--blob-root-bg: #f1f3f9` + indigo/pink/cyan ambient；暗色 `--blob-root-bg: #040810` + 玻璃 ambient。**不要**再用 `var(--background)` 铺整块内容区；`--background` 只是 surface token。
- 需要层次时用透明边框、局部 `bg-card/30` 或玻璃卡片，不新增第二个页面背景色。
- Showcase 不要整块铺 `--background`；卡片用 `bg-card/70` / `dark:bg-white/5`。
- 卡片：
  - 视频/PPT 标题 18–20px，正文 13px；视觉标题 15px，正文 12.5–13px。
  - 标签 10.5–11px；减少 10px 小标签数量；正文最多 2–3 行。
  - 卡片边框更轻；媒体圆角 10–12px；只有 hover 时加强边框/阴影。
  - 视频/PPT 保持图片主导；视觉保持自然比例瀑布流，不裁切。
- 左栏标题 26–28px，正文 13px；不要缩小到 12px。

## 四、参考资料准备（T0 前置，必须先做）

> 干净 worktree 里默认没有 demo 和截图，不能直接引用相对路径。

- demo 目录 `temp/agent-listenhub-demo-v2/` 被 `.gitignore` 的 `temp/` 排除；从 `origin/main` 新建 worktree 后不存在。
- `.screenshots/agent-showcase/refinement/` 是当前工作区的 **未跟踪证据**；新 worktree 也不会自动带过去。
- 现有 `capture-verification.mjs` 里硬编码了已删除的验收 worktree：`/Users/littlebear/Documents/SuperTrainerAgent-agent-showcase/frontend`，不能直接复用。

准备项：

- [x] 将 demo 与参考截图留在原工作区，按**绝对路径只读**访问：
  - `/Users/littlebear/Documents/SuperTrainerAgent-home-plan/temp/agent-listenhub-demo-v2/`
  - `/Users/littlebear/Documents/SuperTrainerAgent-home-plan/.screenshots/agent-showcase/refinement/`
- [x] 或在 worktree 外建立固定参考目录，不提交进 git；不要复制 15MB 素材进仓库。
- [x] 参数化 capture 脚本：`FRONTEND_DIR`、`BASE_URL`、`OUT_DIR`；默认值不得指向已删除目录。
- [x] 截图是评审证据，不要求提交；如需入库，按 `.screenshots` 的证据约定处理。
- [x] 从 `origin/main` 新建干净 worktree，确认 demo/截图通过绝对路径可读，再开始 P0。

**验收**：干净 worktree 内能读到参考 demo/截图；capture 脚本用显式参数运行成功；不依赖已删除 worktree。

## 五、P0 任务（先做，单独 PR）

### P0-1 Hero 去卡片 + 透明资产

**主方案**：四张 hero 全部使用透明 WebP，人物轮廓外完全透明；这是默认完成标准。

- [x] 检查 `frontend/public/agent-showcase/hero/*.webp` 的 alpha；没有则离线处理（GrabCut/抠图工具，运行时不做抠图）。
- [x] `showcase-hero.tsx` 移除 `rounded-*`、`border-*`、`bg-indigo-50`、`shadow-*`；改为无卡片布局。
- [x] 需要氛围时加伪元素径向光晕；光晕不形成矩形边界。
- [x] Hero 改为左对齐（`justify-start`），删除 `mx-auto max-w-4xl`；人物与文字共享内容容器。
- [x] Hero 字号/高度按第三节规格；移动端人物 170–190px。

**资产验收（主方案）**：
- 四张 WebP 四角 alpha = 0；人物轮廓外没有不透明近白像素；
- 在暗色背景合成后无白边、无矩形底板；
- 资产测试不能只断言“存在透明像素”（圆角裁切也会产生透明像素），必须检查四角/背景区域 alpha 与暗色截图。

**兜底触发条件**：只有离线透明处理一次后仍无法得到干净轮廓，才允许四张统一改成“无边框柔光底”；不得部分透明、部分柔光；不得恢复浅色圆角卡片。兜底验收：暗色无白矩形、无边框、与页面背景同色系。

### P0-2 统一内容容器

- [x] 在 `agent-new-chat-guide.tsx` 的 showcase 分支定义唯一的 `showcase-shell`（`max-w-[1180px] mx-auto w-full`）。
- [x] `ShowcaseHero`、`GuideComposerFrame`、`AgentShowcase` 内部只保留 `w-full`，**不要**各自再套一次 shell/padding，避免双重缩进。
- [x] 给 `AgentShowcase` 最外层 section 增加稳定测试标识 `data-testid="agent-showcase"`。
- [x] 通过 Playwright 断言 **Hero 根节点、输入 frame、Showcase 根节点** 三者 `boundingBox().x` 最大差 ≤8px；不要用 `agent-guide-cases` 做整区对齐（它是右栏卡片网格，左边缘本来就在步骤栏之后）。
- [x] 左右边缘同样检查；无横向滚动。
- [x] 左栏宽度留给 P1-2（不要在 P0/P1 重复设置；1024 下不强行 280）。

**验收**：1440 下 Hero、输入、Showcase 根节点左右边缘对齐；无重复缩进；无横向滚动。

### P0-3 输入框任务上下文（实现契约）

- [x] `ShowcaseAgent` 增加 `inputPlaceholder: LocalizedText`；四个 Agent 填入上表中英 placeholder。
- [x] `InputBox` 增加可选 `placeholder?: string`；`PromptInputTextarea` 使用 `placeholder ?? t.inputBox.placeholder`，默认行为不变。**不能**把 `LocalizedText` 对象直接传给 `InputBox`。
- [x] 页面层（`page.tsx`）新增 `const showcase = showcaseFor(agent_name)`；`const guideShowcase = showGuidedNewChat && showcase`；按 `locale` 取 `showcase.inputPlaceholder.zh/en` 得到 **string**，仅在 `guideShowcase` 为 true 时传给 `InputBox`。已有会话/co-writer 不传，保持全局 placeholder。
- [x] 不新增 `showXxx`/`compact` 配置；不把模型名/模式固定写成“Pro / 默认模型”。当前模型名与模式是动态值，保持现状。
- [ ] 上传按钮文字：P0 不做；P1 可选 `showAttachmentLabel?: boolean`（默认 false），仅 `guideShowcase` 开启，390px 隐藏文字。文件边界同步允许 `input-box.tsx` 的这一可选 prop。
- [x] `guide-composer-frame` 保持 24–28px 圆角、100% 宽、glass 背景；不设 `overflow:hidden`。
- [x] 390px 下长 placeholder 正常换行，不与工具栏重叠，不异常加高。

**验收**：
- 四个 showcase 新会话 placeholder 分别正确；已有会话与 co-writer 仍是全局“今天我能为你做些什么？”；
- `input-box-*.dom.test.tsx` 默认 placeholder 不变、显式覆盖生效；
- `agent-chat-page.dom.test.tsx` 覆盖 showcase 新会话收到 string、已有会话/co-writer 不接收；
- 1440 输入框宽度 ≥900px；模型/上传下拉不被裁切；390 无重叠。

### P0-4 去掉全宽背景补丁

- [x] 删除 `agent-showcase.module.css` 中 `.showcaseGuide` 的 `background/box-shadow/clip-path`；guide 根节点 `bg-transparent`。
- [x] 确认页面画布仍由 `WorkspaceContent` 的 `FloatingBlobsBackground` + `--blob-root-bg` 提供；不要新增/覆盖页面背景。
- [x] 如需分隔，用 `border-t`/`bg-card/30` 的局部卡片，不铺整块背景。
- [x] 与 `/workspace` 首页/其他 chat 页并排截图，背景应为同一套玻璃/ambient，而不是米色内容块。
- [x] 检查内容区顶部/左右没有硬接缝；亮/暗色截图对照。

**验收**：1440 亮/暗色截图与 `/workspace` 首页/其他 chat 页同背景；无米色内容块/硬接缝；guide 无额外 background；卡片仍在环境背景上可读。

## 六、P1 任务（P0 合入后）

### P1-1 卡片层级与密度

- [x] 视频标题 18–20px，正文 13px；视频卡在 1180 容器下 2–3 列实测，优先保证图片和标题可读。
- [x] PPT 标题 19–20px；预览列 280–300px，2×2 缩略图；减少 10px 元信息。
- [x] 视觉标题 15px、正文 12.5–13px；标签 10.5–11px；保持瀑布流自然比例。
- [x] 播客标题 15–16px；音波/播放/来源层级不抢标题。
- [x] 减少卡片边框/标签数量；hover 才强化边框/阴影。
- [x] 同步修改/删除 `agent-showcase.module.css` 里 `.showcase-card { background: var(--card) }` 与暗色覆盖，避免只加 `bg-card/70` 被 CSS Module 优先级压住；卡片背景只保留一个来源。

**验收**：1440 首屏能看到第一张卡的标题（不只是海报）；卡片文字不小于 12px（元信息 10.5+）。

### P1-2 左栏与节奏（首屏基线）

**现状基线（PR #13 验收记录）**：

| 视口 | 第一张卡 top | 当前阈值 | 新阈值（基线 +30px） |
|---|---:|---:|---:|
| 1440×900 | 551 | <700（过松） | ≤581 |
| 1024×768 | 515 | 仅标题 bottom ≤768 | 首卡标题 bottom ≤768；top ≤545 |
| 390×844 | 587.59 | <844（过松） | ≤618 |

- [x] 左栏：≥1280 视口 280px；1024 保持较窄（约 240）或按截图实测，不与卡片抢宽；标题 26–28px、正文 13px、步骤间距 18–22px。
- [x] 缩小 Hero→输入→Showcase 的垂直空隙；目标为基线 +30px 以内，不允许明显倒退。
- [x] 1440/390 不仅断言首卡 `top`，还要断言第一张卡 `h3` 的 **bottom ≤ 视口高度**；1024 保持标题 bottom ≤768。
- [x] 如果确实要牺牲首屏换更大 Hero，必须在计划/PR 写明设计取舍并更新基线，不能用宽松阈值隐式放宽。
- [x] 移动端：Hero 堆叠、输入 100%、案例在步骤前；390 无横向滚动。

**验收**：首屏基线+30 内；首卡标题可见；左栏不与卡片重叠；移动无横向滚动。

## 七、跨阶段测试与评审（P0 必须同步带）

> **P2 改名**：不把测试放到 P1 之后；P0 需要的 placeholder、容器、背景、首屏回归必须在 **P0 PR 同步加入**。现有 `agent-new-chat-guide.spec.ts` 已覆盖 1440/1024/390、输入宽度/圆角、暗色、四 Agent 与媒体行为，本批优先修改它，不再另建一套 24 条自动化 E2E。

### T6-1 更新现有 E2E（P0 同步）

- [x] 修改 `frontend/tests/e2e/agent-new-chat-guide.spec.ts`：
  - 现有三视口循环的 `firstCardTop` 从 820/764 收紧到基线+30（1440 ≤581、1024 title bottom ≤768 + top ≤545、390 ≤618）；
  - 1440/390 增加第一张卡 `h3` 的 bottom ≤ 视口高度；
  - 新增 Hero/输入/`agent-showcase` 根节点左边缘差 ≤8px；
  - 新增四个 Agent 的专属 placeholder 断言（不是全局句）；
  - 新增 390 长 placeholder 不与工具栏重叠/不异常加高；
  - 现有 width/radius/dark/media 断言保留。
- [x] 给 `AgentShowcase` 外层加 `data-testid="agent-showcase"`；对齐断言用它，不用 `agent-guide-cases`。
- [x] 不新建 24 条“4 Agent × 3 视口 × 2 主题”的 E2E；截图由独立 capture 脚本产出，交给人工评审。

### T6-2 单元测试（P0 同步）

- [x] `agent-new-chat-guide.dom.test.tsx`：showcase 新会话收到 `inputPlaceholder` 对应 string；已有会话/co-writer 不传。（实现落在 page 级测试——placeholder 由 page 传给 `InputBox`，guide 壳只渲染 children，不再重复断言。）
- [x] `agent-chat-page.dom.test.tsx`：`showGuidedNewChat && showcase` 时传 placeholder；已有会话/co-writer 不传。
- [x] `input-box-*.dom.test.tsx`：默认 `t.inputBox.placeholder` 不变；显式 `placeholder` 覆盖生效。
- [x] `showcase.test.ts`：四个 Agent 的 `inputPlaceholder.zh/en` 非空；现有本地化字段/资产测试保留。
- [x] `agent-showcase.dom.test.tsx` 只证明案例区行为，不用于证明 InputBox placeholder；不要把它当 page 级验收。

### T6-3 视觉证据与人工评审

- [x] 复用/参数化 `.screenshots/agent-showcase/refinement/capture-verification.mjs`；**不要**沿用硬编码已删除的 `.../SuperTrainerAgent-agent-showcase/frontend`。
- [x] 参数：`FRONTEND_DIR`、`BASE_URL`、`OUT_DIR`；截图输出到 worktree 外或 `.screenshots/agent-showcase/visual-fidelity/`（证据产物，不要求进 git）。
- [x] 每个 PR 附“demo vs 当前”并排截图，至少 1440 亮色 + 390 移动；P1 再补暗色/1024。
- [x] 评审清单：人物是否透明/无卡、三块边缘是否对齐、输入 placeholder 是否专属、背景是否连续、首卡标题是否可见、卡片字号是否达标。
- [x] 不把“测试全绿”当作视觉验收；必须有截图人工确认。

### T6-4 Hero 资产 alpha

- [x] 资产测试检查四张 hero 的四角 alpha=0、背景区域无近白不透明像素；暗色合成无白边/矩形。
- [x] 兜底方案单独验收，不能和透明方案混用；不得只断言“存在透明像素”。

## 八、文件边界（allow / ban / 共享基座）

| 类型 | 路径 |
|---|---|
| allow | `frontend/src/components/workspace/agent-showcase/showcase-hero.tsx`、`agent-showcase.tsx`、`showcase-cards.tsx`、`agent-showcase.module.css`、`guide-composer.module.css`、`frontend/src/components/workspace/agent-new-chat-guide.tsx`、`frontend/src/components/workspace/input-box.tsx`（可选 `placeholder`，P1 可选 `showAttachmentLabel`）、`frontend/src/app/workspace/agents/[agent_name]/chats/[thread_id]/page.tsx`（仅按 locale 取 string 并传 placeholder）、`frontend/src/core/agents/showcase.ts`（仅 `inputPlaceholder`）、`frontend/public/agent-showcase/hero/**`（仅透明替换） |
| allow（测试/文档） | `frontend/tests/e2e/agent-new-chat-guide.spec.ts`、`frontend/tests/unit/components/workspace/agent-new-chat-guide.dom.test.tsx`、`frontend/tests/unit/components/workspace/agent-showcase.dom.test.tsx`、`frontend/tests/unit/app/workspace/agents/agent-chat-page.dom.test.tsx`、`frontend/tests/unit/components/workspace/input-box-*.dom.test.tsx`、`frontend/tests/unit/core/agents/showcase.test.ts`、本计划、`docs/changelog/changelog-2026-11-17-agent-showcase-visual-fidelity.md`（实现后） |
| ban | `frontend/src/components/ui/**`、`frontend/src/components/ai-elements/**`、`frontend/src/styles/globals.css`、backend/**、gateway/**、skills/public/ppt-master/**、Agent runtime、thread/stream、auth、config、lockfiles、其他 Agent 业务、`showcase.ts` 的媒体数据/`showcase-dialogs.tsx`/`showcase-audio-player.tsx`（视觉修复不需要改） |
| 共享基座 | `input-box.tsx`、agent chat page、`agent-new-chat-guide.tsx`、i18n/agent data；单执行者独占，与其它前端批次串行 |

### 与 PPT-master session 的路径重叠表

| 批次 | allow | ban | 共享基座 |
|---|---|---|---|
| Showcase 视觉修复 | 本计划 allow 表中的 frontend 文件/测试/文档/hero | backend、skills/public/ppt-master、Agent runtime、其他前端批次 | 无（单执行者独占 frontend guide/input/page） |
| PPT-master 优化（当前 worktree） | `backend/**`、`backend/seed/agents/ppt-master/**`、`skills/public/ppt-master/**`、对应 PPT docs | frontend/** | 无 |
| 重叠结果 | **空**（frontend vs backend/skills/docs） | — | — |

- 当前 PPT worktree `ppt-beautify-long-deck-quick-delivery` 改的是 `backend/**`、`backend/seed/agents/ppt-master/**`、`skills/public/ppt-master/**`、对应 docs，**不碰 frontend**。
- 可并行，但必须：独立 worktree/branch；showcase 分支不碰 PPT 路径；PPT 分支若后续开始改 frontend guide/i18n，立即停并串行；两个 PR 串行合入，showcase rebase 后再合。

## 九、测试与验收命令

```bash
cd frontend
pnpm check
pnpm test -- \
  tests/unit/components/workspace/agent-new-chat-guide.dom.test.tsx \
  tests/unit/components/workspace/agent-showcase.dom.test.tsx \
  tests/unit/app/workspace/agents/agent-chat-page.dom.test.tsx \
  tests/unit/components/workspace/input-box-mode-decouple.dom.test.tsx \
  tests/unit/core/agents/showcase.test.ts
pnpm test:e2e -- tests/e2e/agent-new-chat-guide.spec.ts
# 截图（参数化，不依赖已删除 worktree）：
# FRONTEND_DIR=<clean-worktree>/frontend BASE_URL=http://localhost:3177 \
# OUT_DIR=/abs/path/.screenshots/agent-showcase/visual-fidelity \
# node --experimental-strip-types <reference>/capture-verification.mjs
```

- `pnpm check`：eslint + tsc + prettier。
- 单测：4 类卡/Dialog/prompt、page 级 placeholder 条件、InputBox 默认/覆盖、showcase 数据完整性不回归。
- E2E：修改现有 `agent-new-chat-guide.spec.ts`，覆盖三视口、对齐、专属 placeholder、首卡标题、暗色、移动、下载、无 404；不新增重复的 24 条视觉 E2E。
- 截图：参数化 capture 脚本，输出到 worktree 外/证据目录；与 demo 并排人工评审。
- 文档：`python3 scripts/check_docs_convention.py`。

### 最终验收

- [x] Hero 主方案：四张透明 WebP，四角 alpha=0、暗色无白边/矩形；或按触发条件统一无边框柔光，不混用。
- [x] Hero、输入框、`agent-showcase` 根节点左边缘偏差 ≤8px；无三段宽度/双重缩进。
- [x] 输入框 1440 宽度 ≥900px；四个 Agent 专属 placeholder（string）；已有会话/co-writer 仍用全局句；glass 保留；下拉不裁切；390 无重叠。
- [x] 内容区背景与项目壳连续，无全宽米色块/硬接缝；`showcaseGuide` 无 background/clip-path。
- [x] 首屏不倒退：1440 首卡 top ≤581 且 h3 bottom ≤900；1024 首卡 top ≤545 且 h3 bottom ≤768；390 首卡 top ≤618 且 h3 bottom ≤844。
- [x] 视频/PPT/视觉标题字号达标；卡片不再像后台列表。
- [x] 亮/暗色、四 Agent、移动/桌面截图通过人工设计评审。（执行侧审图：4 Agent × 1440/1024/390 × 亮/暗 + /workspace 同背景对照，证据见 changelog；留待用户抽检。）
- [x] P0 同步包含 placeholder/对齐/背景/首屏测试；不是 P1 后再补。
- [x] 现有功能测试全绿；无 console error/404；co-writer/其他 Agent 不受影响。

## 十、风险与回滚

| 风险 | 处理 |
|---|---|
| 透明 hero 资产难处理 | 主方案透明 WebP；只有一次离线处理仍不干净才统一无边框柔光，不混用、不恢复卡片。 |
| `InputBox` 加 placeholder prop 影响全局 | prop 可选、默认原值；只有 `showGuidedNewChat && showcase` 传 string；已有会话/co-writer 不传；加回归测试。 |
| 统一 1180 容器在窄屏/侧栏展开时溢出 | `w-full max-w-[1180px]` + 项目 padding；390/1024/1440 实测无横向滚动；Hero/输入/Showcase 不重复 shell。 |
| CSS Module 卡片背景覆盖 utility | 同步修改/删除 `.showcase-card` 的 `background` 与暗色覆盖；卡片背景只保留一个来源。 |
| 视觉断言依赖截图环境 | 只硬断言可计算的 boundingBox/宽度/placeholder/首卡 h3；构图用人工评审。 |
| 卡片放大导致首屏更差 | P1 以 1440/1024/390 实测调列数/字号；首卡 top 不得超基线+30，h3 bottom 必须可见。 |
| 参考材料在干净 worktree 不可见 | T0 用绝对路径读 demo/截图；capture 参数化；不把 15MB 素材/截图塞进新 worktree。 |
| P0 测试延迟到 P1 | T6-1/T6-2 必须与 P0 同 PR；P2 已改名“跨阶段测试与评审”，不设可延后测试。 |
| 与 PPT session 并行冲突 | 路径重叠表为空；独立 worktree；PPT 若碰 frontend 立即串行。 |

**回滚**：本计划只改视觉层与可选 placeholder，回滚 PR 即回到 PR #13 现状；不改数据/媒体/路由，不需要数据迁移。

## 十一、文档收尾（实现后，T7）

- [x] 将本计划状态头改为 `已执行`，写明执行范围、PR/commit、changelog 链接。
- [x] 新增 `docs/changelog/changelog-2026-11-17-agent-showcase-visual-fidelity.md`：改动、视觉前后对比、测试命令实际输出、首屏三视口数据、与 PPT 批次并行/合入说明。
- [x] 记录参考截图路径（worktree 外/证据目录），不把截图/大素材提交进仓库。
- [x] 运行 `python3 scripts/check_docs_convention.py`，通过后再收尾。
- [x] 若透明 hero 走了兜底，在 changelog 写明触发原因和统一方案，不写成“全部透明”。（未走兜底：四张透明 WebP 一次成功，changelog 已注明。）

## 十二、执行顺序与交付

1. **T0**：参考资料准备 + 从 `origin/main` 新建干净 worktree。
2. **P0 PR（含测试）**：P0-1 Hero 去卡片 + P0-2 统一容器/`data-testid` + P0-3 placeholder 契约 + P0-4 背景 + T6-1/T6-2 测试。先合入，产出 before/after 截图。
3. **P1 PR**：卡片层级/左栏/首屏节奏 + T6-3 截图与人工评审。
4. **T7**：changelog、计划状态、docs 检查、最终证据归档。
5. 如时间紧，P0 单独就能解决“最像 demo”的 70%；P1 是精修。

> **建议**：不要继续在现有三段宽度上叠 CSS；先把 P0 的“一个容器 + 透明人物 + 真 placeholder”做掉，再判断卡片是否需要重排。
