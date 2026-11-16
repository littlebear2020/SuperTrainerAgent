# Agent 新会话案例展示落地计划

> **状态**：已执行（2026-11-17 T0–T5 实现与验收完成；T6 changelog 已落地，PR 评审与合入待完成；[changelog](../changelog/changelog-2026-11-17-agent-showcase-integration.md)）。
> **TL;DR**：把 demo 的“人物 + 大字 + HOW IT WORKS + 真实案例”迁入 4 个 Agent 的新会话引导页，保留真实 `InputBox`、草稿确认和 `co-writer` 旧体验。素材放 `frontend/public/agent-showcase`，UI 与案例元数据双语、提示词中文且保留 Agent 工作流标记。实施先过人物图技术验证，再跑通 `course-video` 垂直切片，随后扩展其余三类；验收定向优先、按风险补充全量或专项测试，禁止无新信号的重复空跑。

**Spec / 需求来源**：用户 2026-11-17 截图与三轮补充说明；当前 demo `temp/agent-listenhub-demo-v2/bundle.html`；项目现有 `AgentNewChatGuide`、`InputBox`、`agentAppearance`、i18n 与设计 token；2026-11-17 计划评审及用户对“前端改动、杜绝不必要测试”的补充约束。已确认的决策见下表。

**执行方式**：单执行者、单工作区；前端改动，后端/Agent runtime/路由不动。计划完成后由用户确认再进入实现。先完成 `course-video` 的可验收切片，不在四个 Agent、四类 Dialog 全部写完后才首次验收。

---

## 一、目标与非目标

### 目标

1. 四个 Agent 的**新会话引导页**用 demo 的 hero：人物立绘 + 小号 Agent 标签 + 一句大字，**替换**现有 guide header（图标/大标题/描述），避免两套标题重复。
2. 四个 Agent 的**新会话引导页**用真实案例展示替换现有简单案例按钮：课程视频、PPT、播客、视觉设计。
3. 首屏优先：hero + 项目输入框 + 案例区标题和第一行卡片可见（桌面 1440×900；移动 390×844 至少看到第一张卡）。
4. 视觉融入真实项目：使用 `--background`/`--card`/`--border`/`--muted-foreground` 等 token、现有玻璃/暗色体系、`agentAppearance` 的 Agent 色，不复制 demo 的白色单页背景。
5. 四类真实产物可打开：视频播放、图片大图、PPT 预览/下载、播客试听/文稿/来源。
6. 现有 `InputBox` 外面加一层 **scoped 大圆角玻璃 frame**：桌面 `max-w-5xl`、radius 28px、移动 24px；保留项目 glass 背景/边框/交互逻辑，不改全局 `InputBox`。
7. 案例卡“用此提示词”走现有 composer 回填逻辑，保留已有草稿确认弹窗。
8. 保持可维护：案例数据与 UI 分离；素材有清单、路径校验、懒加载和体积预算。

### 非目标（明确不迁）

- **不迁 demo 的顶栏、Agent tabs、独立页面背景/单页壳**。真实项目保留自己的导航与页面布局。
- **不复制 demo composer 的内部结构/非功能输入框**。保留项目真实 `InputBox` 的玻璃质感、上传/模型/发送逻辑；只在 guide 外层加一个 scoped 大圆角 frame 调整宽度与 radius。
- **人物立绘只用于新会话 hero**，不铺到工作台首页、已有会话页或其他 Agent 页面；也不使用 `media.ts` base64/`bundle.html`，素材以静态文件落 `frontend/public`。
- 不改 backend、gateway、Agent runtime、thread/stream API、路由、鉴权、模型/技能配置。
- 不改“协同写作”（co-writer）等无 showcase 的 Agent；它们继续使用现有简单案例卡。
- 不做新的 Agent 页面、不做工作台首页案例墙、不做已有会话页插入。
- 不做视频转码服务、CDN 上传、PPTX 在线编辑；首版只消费已生成的静态产物。

## 二、已确认决策

| 决策 | 选择 | 影响 |
|---|---|---|
| 落地页面 | 仅 Agent 新会话引导页 | 只改 `AgentNewChatGuide` 的展示路径；已有会话页不动。 |
| 首屏核心区 | 人物 + 小号 Agent 标签 + 大字**替换**现有 guide header | 四个 showcase Agent 不再渲染现有图标/标题/描述；co-writer 保留原 header。 |
| 输入框外观 | 保留项目真实 `InputBox` 的 glass 逻辑，只加 scoped 大圆角 frame：桌面 `max-w-5xl`、radius 28px，移动 radius 24px、宽 100% | 不复制 demo composer；不改全局 `InputBox`/已有会话页；只影响 4 个 showcase Agent 的新会话 guide。 |
| 现有案例卡 | 四个 Agent 直接替换为富媒体案例 | 课程视频 3、PPT 3、播客 4、视觉 10；co-writer 保持原 4 个简单卡。 |
| 素材托管 | `frontend/public/agent-showcase` 静态托管 | 15.32MB 二进制不进 JS bundle；hero/首行可 eager，其余懒加载，用 `preload` 控制首屏流量。 |
| 中英文 | UI 与案例元数据双语；提示词中文 | 英文界面不出现中文 UI 标签；媒体产物本身可保持中文；示例 prompt 保持中文以匹配 Agent 与产物。 |
| 360 模板素材 | 允许进入 `frontend/public` 随项目分发 | 第三套 PPT 案例保留；`README.md` 记录用户授权与来源。 |
| 人物背景 | 先用一张图验证透明化；若效果不达标，再统一采用有意设计的浅色底板 | 四个 Agent 不混用方案；不允许直接把当前白底图放进暗色页面。 |
| 提示词迁移 | 合并 demo 案例主题与现有 Agent 工作流约束，不照抄 demo prompt | 播客保留四个 `[模式: …]` 首行；PPT/视觉/课程保留对应流程语义。 |
| 测试策略 | 定向测试优先，按共享面、失败信号和兼容风险升级 | 保留既有 PR CI；不机械新增 CI/浏览器项目/像素基线，不排除必要的全量或专项测试。 |

## 三、现状与集成点

### 真实页面链路

- 路由：`frontend/src/app/workspace/agents/[agent_name]/chats/[thread_id]/page.tsx`
- 新会话判定：`isNewThread && isAgentNewChatGuideId(agent_name)` 时渲染 `AgentNewChatGuide`，并把 `inputBox` 作为 `children` 传入。
- 组件：`frontend/src/components/workspace/agent-new-chat-guide.tsx`
  - 当前顺序：header → `guide.cases`（简单按钮，2 列）→ `children`（输入框）→ tips。
  - 已有：`usePromptInputController().textInput.setInput` 回填、非空草稿确认 Dialog、`handoffReady/status/disabled` 门控。
- 四个 Agent 的 guide id：`course-video`、`ppt-master`、`podcast-studio`、`creative-studio`；`co-writer` 也在 guide 名单内，但不在本批 showcase 范围。
- Agent 颜色/图标：`frontend/src/core/agents/appearance.ts` 的 `agentAppearance()`。
- 设计 token：`frontend/src/styles/globals.css`，浅色 `--background/--card/--border/--muted-foreground`，暗色玻璃/`#080b14`，indigo 主强调。
- 现有测试：
  - `frontend/tests/unit/components/workspace/agent-new-chat-guide.dom.test.tsx`
  - `frontend/tests/e2e/agent-new-chat-guide.spec.ts`
  - `frontend/tests/unit/app/workspace/agents/agent-chat-page.dom.test.tsx`（mock guide，预计不受影响）

### 关键现状约束

- 现有单测断言：`agent-guide-cases` 在 `textarea` 之前、`textarea` 在 tips 之前；showcase 改为输入框在前，`co-writer` 旧顺序不动。
- 现有 E2E 对五个 Agent 统一遍历 `guide.cases`，别名恢复、loading retry、暗色对比度也引用旧标题。更新时拆成 legacy/showcase/shared 三组，不得为迁就新 UI 删除草稿、显式提交、别名和错误恢复断言。
- 现有课程视频 guide 有 4 个简单案例；showcase 只有 3 个真实案例。showcase 的提示词在英文 UI 仍为中文；现有“locale 切换后换成英文 prompt”的课程单测要改为“语言切换不重写草稿，showcase prompt 始终中文”，`co-writer` 的英文 prompt 行为不变。
- 现有 guide header 是图标 + displayName + h1 + description；四个 showcase Agent 改为 hero 后，h1 变成大字 heroLine，现有标题/描述不再渲染；co-writer 保留原 header。
- `AgentNewChatGuide` 的 `children` 是真实输入框（还可能带 demoNotice），不能复制 demo 的 composer；只调整其在文档流中的位置，并在外层加 scoped frame 调宽度/圆角。
- 2026-11-17 实测四张源 `hero-*.webp` 的 alpha 全部为 255、透明像素为 0、四角为不透明近白底。它们是待处理素材，不是现成透明图。
- demo 播客 prompt 没有四个 `[模式: …]` 标记；现有 guide 与 `podcast-studio` Agent 约定有。迁移时必须保留工作流语义，而非只复制展示文案。
- 页面外层已有滚动容器（`size-full overflow-y-auto pt-12`），showcase 只负责自身布局，不接管整页滚动。

### 测试分层与防重复（本批约束）

- **默认测试落点**：`showcase.test.ts`（node 数据/资产）、现有 `agent-new-chat-guide.dom.test.tsx`、新增 `agent-showcase.dom.test.tsx`、现有 `agent-new-chat-guide.spec.ts`。优先在既有文件补最小断言；只有出现明确的新职责/新风险时才增加文件，不为每个案例、语言或视口各建一套。
- **数据与行为分工**：20 个案例的数量、双语字段、prompt 工作流标记、媒体路径和文件大小走廉价 node 断言；E2E 每类媒体跑一条代表路径，外加 `co-writer` 回退和草稿/提交等共享关键行为。不把 20 个案例逐一做成浏览器测试，但媒体类型特有交互必须有至少一条真实浏览器断言。
- **迭代期**：每个垂直切片只跑新增/直接受影响的 node、DOM 和 E2E 用例；未触及的套件不反复跑。静态检查在交付前统一跑一次，不在每个小改动后重复整套。
- **交付前**：跑一次 `make -C frontend check`（lint、tsc、prettier、knip、i18n）、本计划定向 Vitest、现有 `agent-new-chat-guide.spec.ts` 整个文件，以及 `python3 scripts/check_docs_convention.py`。该 E2E 文件覆盖本功能共享链路，属于相关回归，不视为无意义全量。
- **既有 CI 保留**：PR 的现有 frontend unit/E2E/static/perf 检查按仓库流程执行并通过，不为本批默认新增 CI job，也不在本地预先重复模拟整套 CI。CI 失败要按日志修复真实原因，不能靠无差别重跑碰绿。
- **必要时升级测试**：若定向测试暴露共享 guide/i18n 的跨模块回归，补全量 Vitest；若意外触及路由、导航、全局 InputBox 或出现跨页回归，补相关乃至全量 Playwright；若真实媒体兼容失败，补最小 Safari/WebKit 专项；若 CI 报路由资产预算问题，再跑 `pnpm perf:check`。这些是按信号触发，不是每轮默认动作。
- **不机械扩测**：本批不改 backend，不跑 backend 测试；不默认新增浏览器矩阵、axe 依赖、像素基线或路由性能专项。若后续实际改动越出前端边界，先停止并调整计划，而不是用扩测掩盖 scope 漂移。
- **禁止空跑**：同一命令在未改代码、无环境变化、无新失败信号时不重复执行；失败后先定位，再只重跑失败目标，修复后按上述分层恢复验证。
- **命令目录**：`pnpm` 命令均在 `frontend/` 下执行；`make -C frontend check` 与 `python3 scripts/check_docs_convention.py` 在仓库根执行。

## 四、目标页面结构

### 四个 Agent（有 showcase）

```
AgentNewChatGuide
├─ ShowcaseHero（新，仅 showcase Agent）
│  ├─ 人物立绘（T0 选定透明图或浅色底板）
│  ├─ 小号 Agent 标签
│  └─ 大字 heroLine（两行，桌面 26–30px / 移动 20–22px）
├─ GuideComposerFrame（scoped，max-w-5xl 居中）
│  └─ InputBox（项目现有 children；保留 glass/上传/模型/发送逻辑；radius 28）
├─ AgentShowcase（新）
│  ├─ 左列：HOW IT WORKS + 3/4 步说明（桌面 sticky/静态均可，首版静态）
│  └─ 右列：案例标题/数量/badge + 筛选（视觉）+ 案例卡网格
└─ tips（项目现有，保留在 showcase 下方）
```

- **替换**现有 header，不叠加：四个 showcase Agent 不再渲染图标/大标题/描述；小号 Agent 标签用于保留身份。
- 人物高度控制在桌面 **180–200px**、移动 **140–160px**；不要直接复用 demo 的 250px，否则首屏会被挤掉。
- 输入框宽度桌面 **max-w-5xl（1024px）**，移动 100%；radius 28px/24px；只覆盖圆角与宽度，不覆盖 `glass-input-box` 的背景/模糊/边框。
- 不要给 frame 设 `overflow:hidden`：`InputBox` 的模型/技能/上传下拉依赖 `overflow-visible`，裁切会切掉弹层。

### Composer frame 实现口径

- 在 `AgentNewChatGuide` 现有的 `<div data-testid="agent-guide-input">{children}</div>` 上使用 `className={cn(styles.guideComposerFrame, "mx-auto w-full max-w-5xl")}`；不要同时发明 `guide-composer-frame` 与 `.guideComposerFrame` 两套类名。
- 只新增 `agent-showcase/guide-composer.module.css`，选择器必须是 `.guideComposerFrame :global(.glass-input-box)` 与 `.guideComposerFrame :global(.glass-input-box [data-slot='input-group'])`，只覆盖 `border-radius: 28px`；移动端 24px。
- 不覆盖 `glass-input-box` 的 `background`/`backdrop-filter`/`border`/`box-shadow`；玻璃质感由项目现有 `glassStyle()` 提供。
- 不设置 `overflow: hidden`；`InputBox` 自带 `overflow-visible!`，下拉/附件弹层必须保持可溢出。
- 如果嵌套 `data-slot='input-group'` 的 16px 圆角仍覆盖外层，使用 scoped CSS 的同等/更高 specificity 修正，不回写全局 `globals.css`。
- `max-w-5xl` 是桌面目标；1440 视口实际渲染宽度必须 ≥900px。若实拍超过内容区可读宽度，回退到 `max-w-4xl` 只改一个 class，并同步首屏断言。
- 圆角断言直接读取 `.glass-input-box` 与内部 `[data-slot='input-group']` 的 computed style，不断言没有圆角样式的 wrapper。
- 不渲染 `guide.cases` 的简单按钮。
- `AgentNewChatGuide` 的草稿确认 Dialog 保留；showcase 的“用此提示词”调用同一个 `selectCase`。
- 首版左列不 sticky，避免在 `overflow-y-auto` 容器里产生双滚动/遮挡；如实现后发现首屏比例合适再加 sticky。

### 其他 Agent（无 showcase）

保持现状：header → 简单 cases → `children` → tips；只做必要的共享组件抽取，不改 UX。

### 首屏目标

| 视口 | 目标 |
|---|---|
| 1440×900 | hero/输入框 `bottom ≤ 900`；showcase 标题完整可见；第一张卡 `top ≤ 820`（至少露出 80px，而非只露出 1px）。 |
| 1024×768 | hero/输入框 `bottom ≤ 768`；第一张案例卡标题 `bottom ≤ 768`。 |
| 390×844 | hero/输入框 `bottom ≤ 844`；案例区标题完整可见；第一张卡 `top ≤ 764`；左列步骤下移/折叠。 |

首屏测量固定为 ready、空草稿、无附件的初始状态。实现后在现有 E2E 文件中用一个参数化测试覆盖三个视口，以 `boundingBox()` 断言；英文长文案只抽一个 Agent，不做语言 × Agent × 视口的笛卡尔积。

## 五、组件与数据设计

### 目录

```
frontend/src/core/agents/showcase.ts                  # 类型 + 四 Agent 数据 + 本地化字段
frontend/src/components/workspace/agent-showcase/
  showcase-hero.tsx                                   # 人物 + 小号 Agent 标签 + 大字
  agent-showcase.tsx                                  # 区块布局、步骤、筛选、卡片分发
  showcase-cards.tsx                                  # Video/Visual/Deck/Podcast 卡片
  showcase-dialogs.tsx                                # 四类媒体 Dialog
  showcase-audio-player.tsx                           # 试听/进度/暂停
  guide-composer.module.css                           # 仅覆盖 guide composer 的宽度/圆角
frontend/public/agent-showcase/                       # 静态素材（见第六节）
  hero/                                               # 4 张人物 WebP，背景方案按 T0
```

如实现时单文件过大，可拆 `showcase-video-card.tsx` 等；计划不强制文件数量。

### 类型（建议）

```ts
type ShowcaseAgentId =
  | "course-video"
  | "ppt-master"
  | "podcast-studio"
  | "creative-studio";

type LocalizedText = { zh: string; en: string };

type ShowcaseAgent = {
  id: ShowcaseAgentId;
  label: LocalizedText;                    // 小号 Agent 标签
  heroLine: { zh: [string, string]; en: [string, string] }; // 固定两行
  character: string;                       // /agent-showcase/hero/*.webp
  cases: ShowcaseCase[];
};

type ShowcaseCaseBase = {
  id: string;
  kind: "video" | "visual" | "deck" | "podcast";
  title: LocalizedText;
  tag: LocalizedText;
  description: LocalizedText;
  prompt: string;               // 中文示例提示词，含必要工作流标记
};

type VideoCase = ShowcaseCaseBase & {
  kind: "video";
  poster: string; video: string;
  durationSeconds: number;
  voice: LocalizedText;
  styleLabel: LocalizedText;
  sources?: { label: LocalizedText; url: string }[];
};

type VisualCase = ShowcaseCaseBase & {
  kind: "visual";
  category: "poster" | "card" | "comic" | "infographic" | "diagram";
  preview: string;             // WebP
  original: string;            // JPG / SVG
  downloadName: string;
  vector?: string;             // SVG 原图
  width: number; height: number;
};

type DeckCase = ShowcaseCaseBase & {
  kind: "deck";
  slides: string[];            // 7 张预览 WebP
  pptx: string;
  pptxBytes: number;           // 与文件 stat 同步，由资产测试校验
  pageCount: number;
  styleLabel: LocalizedText;
  features: LocalizedText[];
};

type PodcastCase = ShowcaseCaseBase & {
  kind: "podcast";
  audio: string;
  durationSeconds: number;
  voices: LocalizedText[];
  sources: { label: LocalizedText; url: string }[];
  transcript: { speaker: LocalizedText; text: string }[]; // text 是中文媒体内容
};

type ShowcaseCase = VideoCase | VisualCase | DeckCase | PodcastCase;
```

- `showcaseFor(agentName: string): ShowcaseAgent | null`；未知/co-writer 返回 `null`，guide 回退现有 header + 简单卡。
- `showcase.ts` 是唯一媒体清单：文件内部用私有 `asset(relativePath)` 添加 `/agent-showcase/` 前缀，组件只消费数据里的 URL，不另建重复路径常量文件。
- `heroLine` 固定两行，避免自动换行把词拆开；中英文各一组，长度以桌面不超两行、移动不超三行为准。
- 数据文件不引入 React，不读 `window`；组件统一用 `locale === "zh-CN" ? "zh" : "en"` 选择 `LocalizedText`，不直接用 `zh-CN/en-US` 索引数据。
- 展示 meta 从 `durationSeconds/pageCount/width/height/pptxBytes` 派生，不再手抄一份 `meta`。时长用生成数据时的一次性 `ffprobe` 结果（视频 32.320/29.162667/39.082667 秒；音频 48.816/87.792/86.304/114.240 秒），不把 ffprobe 引入前端测试或 CI。
- 文件大小统一用十进制 KB/MB 展示，三份 PPTX 约 201/309/465 KB；资产预算测试统一比较 bytes，避免 KB/KiB 混用。

### 案例数量

| Agent | 类型 | 数量 |
|---|---|---|
| 课程视频 | video | 3 |
| PPT | deck | 3（快速出稿 / 标准流程 / 现有 PPT 改造） |
| 播客 | podcast | 4（原文朗读 / 单人播报 / 双人访谈 / 双人辩论） |
| 视觉设计 | visual | 10（5 类 × 2 风格） |
| 协同写作 | 无 showcase | 保留原 4 个简单卡 |

### 国际化

- `frontend/src/core/i18n/locales/{zh-CN,en-US,types}.ts` 增加 `agents.showcase`：
  - `howItWorks`、`casesTitle`、`casesDescription`、`usePrompt`、`openCase`、`download`、`downloadOriginal`、`downloadSvg`、`downloadPptx`、`downloadMp3`、`listen`、`transcript`、`sources`、`close`、`filterAll`、`slides`、`previous/next`、`mediaUnavailable` 等。
- 案例 `title/tag/description/styleLabel/features/source labels/voice/voices` 用 `LocalizedText`；hero 的 `label` 与 `heroLine` 也双语；`prompt` 中文。
- `locale` 从 `useI18n()` 读取；媒体产物和 transcript 正文中的中文不翻译，这是内容产物而非 UI。
- 英文验收：英文 UI 下不出现中文按钮/标题/描述；媒体内容与示例 prompt 可以为中文。

### 提示词语义契约

- 播客四个 case 的 `prompt` 首行依次为 `[模式: read_original]`、`[模式: news_single]`、`[模式: interview_two]`、`[模式: debate_two]`；demo 的主题和风格描述接在标记之后。
- PPT 三个 case 分别保留 Quick（跳过 Stage 1/2）、Default（先确认结构与方案）、Edit Native（保留原设计、未编辑对象不重建）的明确文字，不能只写“快速”“标准”“原生改造”。
- 视觉两张结构图保留 `[模式: 结构图]`，其余视觉案例保留 `[模式: 视觉创作]`；课程视频保留 `auto-video-flow`、真实素材和确认边界。
- 上述契约在 node 数据测试里做一次静态断言即可；不为 prompt 增加 backend/live 测试，也不自动提交。

## 六、素材迁移与性能

### 来源 → 目标

| 来源（demo `.work/assets`） | 目标（`frontend/public/agent-showcase`） | 用途 |
|---|---|---|
| `videos/v1-3.mp4` + `poster-v1-3.webp` | `videos/` | 课程视频卡/播放 |
| `visual/web/*.webp` | `visual/previews/` | 10 个预览 |
| `visual/*.jpg` | `visual/originals/` | 原图下载 |
| `visual/web/*.svg` | `visual/diagrams/` | 结构图下载/展示 |
| `ppt/previews/{quick,standard,template}/slide-*.webp` | `ppt/previews/{deck}/` | PPT 预览 |
| `ppt/{quick-prompt-v2,standard-llm-app-v2,template-360-ai-v3}.pptx` | `ppt/` | PPTX 下载 |
| `podcast/ep1-4.mp3` | `podcast/` | 试听/下载 |
| demo 生成的 `characters/hero-video/ppt/podcast/visual.webp`（当前为不透明白底 WebP） | `hero/` | 新会话 hero；T0 选定并验证透明化或浅色底板方案后再批量落地 |

- 不复制：`visual/test-*`、`visual/poster-agent-dark.jpg`（旧版）、`ppt-art/`、`.work/remotion`、`bundle.html`、`media.ts`。
- 人物图在构建前离线处理，运行时不抠图。T0 先做一张样图：透明方案要求 alpha 与暗色边缘都合格；若改选浅色底板，四 Agent 统一使用有明确圆角/边框的容器，不能保留无设计的图片白边。
- 已核实目标清单为 **58 个文件、15,318,890 bytes（15.32MB / 14.61MiB）**：54 个案例媒体 + 4 个 hero。结构图只带 SVG，不额外带 PNG 原图；实施时以数据清单与实际文件集合相等为准，不再使用“源目录约 71 个”的模糊口径。
- 视频保持 H.264/AAC MP4 + WebP poster；现有三个文件均为 1280×720 且小于 3MB，首版不转码。
- 图片用 WebP 预览（1600×900 / 900×1600）；原图/ SVG 只在下载时取。
- MP3 64kbps；PPTX 按十进制约 201/309/465KB，数据记录原始 bytes 供校验。
- 加 `frontend/public/agent-showcase/README.md`：素材来源（项目生成/TTS/Remotion/用户提供模板）、生成日期、压缩口径、版权/使用范围；不写密钥。

### 清单与校验

- `showcase.ts` 同时是案例数据与媒体路径单一事实源，禁止组件手写素材 URL，也不再维护第二份路径常量。
- 新增 `frontend/tests/unit/core/agents/showcase.test.ts`（node 档，一次覆盖数据与资产）：
  - 遍历 `character/poster/video/preview/original/vector/slides/pptx/audio`，断言引用路径与实际文件的相对路径集合完全相等（排除 README），同时发现缺失、未引用和大小写不一致；
  - 断言所有本地媒体以 `/agent-showcase/` 开头，无 `..`、`data:`、`base64`、`bundle.html` 引用；
  - 断言总大小 `≤20,000,000 bytes`、`pptxBytes` 与文件 stat 一致；数量 = 58，PPT 预览 = 7×3、视觉预览 = 10、MP3 = 4、hero = 4；
  - 断言案例数 3/3/4/10、必要 LocalizedText 字段和上一节的 prompt 工作流契约。
- 人物 alpha/暗色边缘只在 T0 和素材落地时做一次生成侧检查与截图，不把图片解码工具或 ffprobe 加进前端 CI。
- 运行时 `onError` 隐藏/替换损坏媒体，不显示空白破图；正常素材下控制台无 404。资产完整性由 node 清单测试保证，E2E 不重复请求全部 58 个文件。

### 加载策略

- hero 与首行海报可 eager/`fetchPriority="high"`；其余卡片海报/预览用 `loading="lazy" decoding="async"`，并写明 `width/height` 减少 CLS。
- 视频：卡片只显示 poster；`<video controls preload="metadata">` 仅在 Dialog 打开时挂载，关闭即卸载。
- 音频：卡片只显示播放按钮/时长；`<audio preload="none">`，点击播放才请求；同一时间只允许一个音频播放。
- PPT：卡片只加载视口附近预览，Dialog 打开才挂载完整缩略图与当前页；PPTX 点击下载才请求。
- 首屏不加载视频/音频/原图/PPTX。用一次冷缓存 E2E 测量媒体最重的 `creative-studio` 初始 showcase 响应字节，预算 `≤1,500,000 bytes`；其余 Agent 只断言不请求视频/音频/原图/PPTX，不为每条路由另建性能测试或 CI job。

## 七、交互

### 提示词回填

- `AgentShowcase` 接收 `onUsePrompt: (prompt: string) => void`，由 `AgentNewChatGuide` 传入现有 `selectCase`。
- 保留现有非空草稿确认 Dialog（追加/替换/取消）；showcase 不复制这套逻辑。
- 按钮禁用条件与现有 cases 一致：`!handoffReady || disabled || status !== "ready"`。
- 回填后聚焦真实 `textarea[name='message']`，不自动发送。
- 卡片本身打开媒体；提示词按钮独立，避免嵌套按钮。

### 四类媒体

- 视频：点击 poster 打开 Dialog；`<video controls>`；显示时长/音色/来源；无自动播放；关闭时暂停并卸载。
- 图片：点击预览打开大图；显示尺寸/风格；下载原图（JPG）或 SVG；结构图提供 SVG 按钮。
- PPT：点击预览打开 deck Dialog；7 页缩略图 + 上一页/下一页 + 当前页；下载 PPTX；不在前端解析 PPTX。切换 deck 时重置到第 1 页，首页/末页边界禁用对应翻页按钮。
- 播客：卡片内播放/暂停 + 进度 + 时长；打开文稿 Dialog 显示说话人/文本/来源链接；来源 `target="_blank" rel="noreferrer noopener"`。
- 播放器处理 `play()` 拒绝、快速切换的过期 Promise、`ended`、加载错误和组件卸载；旧音频的异步结果不得覆盖新音频状态。参考现有 `voice-preview-controller` 的竞态处理，但在 showcase 内实现所需进度能力，不扩大改造 voice-twin。

### 筛选与空态

- 视觉设计保留 5 类筛选（全部/课程海报/知识卡片/知识漫画/信息图/结构图），`aria-pressed`；其他 Agent 不显示筛选。
- 媒体缺失/加载失败：卡片显示 `mediaUnavailable`，不阻塞 prompt 回填。
- loading/error：沿用 guide 现有 loading/error；showcase 不渲染假数据。

## 八、视觉与主题

### 必须使用项目 token

| demo 用法 | 项目落地 |
|---|---|
| 白底单页 | 保留 `--blob-root-bg` / 页面现有玻璃背景；showcase 不铺白底 |
| `#17171b` 标题 | `text-foreground` |
| `#777` 描述 | `text-muted-foreground` |
| `#fff` 卡片 | `bg-card/70` + `border-border` + `backdrop-blur-sm` |
| 紫色强调 | 沿用 `indigo` / `agentAppearance` 的 Agent 色 |
| 固定浅色阴影 | `shadow-sm` + 暗色 `dark:border-white/10` |
| 媒体黑底 | `bg-neutral-950` + `border-white/10`，两主题一致 |

- 卡片圆角/间距按项目 `rounded-2xl`、`gap-3/4`，不使用 demo 的独立 CSS 类名。
- Agent 色只用于 icon/badge/短线/筛选选中态；不要整页四色。
- 标题字号：区块 h2 与现有 guide title 协调，不新增比 guide h1 更大的标题。
- 暗色：文本、边框、卡片、筛选、Dialog 均需 `dark:`；媒体黑底可复用。
- 动效：只保留 hover 轻微抬升/边框，不做 demo 的入场动画；尊重 `prefers-reduced-motion`。

### 响应式

- 桌面：showcase `grid lg:grid-cols-[280px_minmax(0,1fr)]`；案例区 2 列（视频/PPT/播客）/ 2–3 列（视觉）。
- 平板 <1024：单列；案例在前、步骤在后（保证首屏先看到案例）；输入框仍在其上。
- 移动：卡片 1 列；筛选横向滚动；Dialog 全宽/近全屏；视频 16:9；图片 max-h 80vh。
- 外层 guide 宽度从 `max-w-4xl` 调整为 `max-w-6xl`，输入框 frame 单独 `max-w-5xl mx-auto`；不改变页面滚动容器。

## 九、任务清单

### T0 基线与单点技术验证

- [x] 记录当前 HEAD 与工作区状态；确认没有与 allow 路径重叠的未提交修改。
- [x] 核对目标清单 = 54 个案例媒体 + 4 个 hero = 58 个文件、15,318,890 bytes；不复制旧版/test/PNG 结构图原图。
- [x] 先处理一张 hero 样图并做亮/暗色实拍：透明方案同时检查 alpha 和白边；若效果不达标，确定四 Agent 统一的浅色底板样式。两种方案都不可接受时停止实施并回到设计决策，不直接批量开发。
- [x] 将现有测试标记为 legacy（co-writer）、showcase（四 Agent）、shared（草稿/提交/别名/错误恢复）三组，明确需要更新的旧标题与旧顺序断言。

**验收**：人物方案有一张真实样图和暗色证据；资产口径明确；本阶段不跑测试套件、不改业务代码。

### T1 数据、素材与静态契约

- [x] 新增 `frontend/src/core/agents/showcase.ts`：类型、私有 `asset()`、`showcaseFor()`、四 Agent 数据（3/3/4/10）及 `label/heroLine/character`；不再新增重复的路径清单文件。
- [x] 按第五节的类型与提示词契约录入数据：双语元数据、中文 prompt、真实时长/页数/尺寸/`pptxBytes`；prompt 保留播客/PPT/视觉/课程工作流语义。
- [x] 新增 `agents.showcase` i18n 标签（zh-CN/en-US/types）。
- [x] 按 T0 选定方案处理剩余 hero，复制/压缩其余素材到 `frontend/public/agent-showcase/`；新增 README 记录来源、授权、生成与压缩口径。
- [x] 新增 `showcase.test.ts`，一次性覆盖路径集合、58 文件、20,000,000 bytes 预算、3/3/4/10 数量、双语必填字段、prompt 标记和 `showcaseFor("co-writer") === null`。

**定向验证**：`pnpm exec vitest run --project node tests/unit/core/agents/showcase.test.ts`。

### T2 course-video 垂直切片

- [x] 新增通用 `ShowcaseHero`、composer CSS module 和 `AgentShowcase` 布局；本切片只接 video card/dialog，其他类型在后续任务扩展。
- [x] 在 `AgentNewChatGuide` 接入条件结构：showcase = hero → 输入框 → showcase → tips；非 showcase 保持 header → cases → 输入框 → tips。loading/error 继续显示现有状态卡，不渲染假案例。
- [x] 复用 `selectCase`、草稿确认和禁用条件；保留 `agent-guide-cases` testid，media button 与 prompt button 分离。
- [x] 完成 video Dialog 的打开、关闭暂停/卸载和焦点返回；composer 只改 scoped width/radius，不动全局 InputBox。
- [x] 更新 guide DOM 的 course-video/co-writer/shared 断言；新建 `agent-showcase.dom.test.tsx` 覆盖 video Dialog/prompt；在现有 E2E 文件加入 course-video 代表路径、composer computed radius/宽度和弹层不裁切断言。

**定向验证**：证明首个切片端到端可用，不跑其他浏览器套件。

```bash
pnpm exec vitest run --project dom \
  tests/unit/components/workspace/agent-new-chat-guide.dom.test.tsx \
  tests/unit/components/workspace/agent-showcase.dom.test.tsx
pnpm exec playwright test tests/e2e/agent-new-chat-guide.spec.ts \
  --grep "course-video|co-writer|Co-writer|shared"
```

### T3 PPT 与视觉案例扩展

- [x] 增加 deck card/dialog：缩略图、前后翻页、页码、边界禁用、切换 deck 重置第 1 页、PPTX 下载。
- [x] 增加 visual card/dialog：大图、原图/SVG 下载、5 类筛选、`aria-pressed`、媒体错误兜底。
- [x] 补齐这两类的响应式、暗色、键盘和 Dialog title/description；沿用 T2 的数据与展示容器，不改路由。
- [x] 在 `agent-showcase.dom.test.tsx` 增加 deck 翻页/重置/下载 href、visual 筛选/下载/Dialog 焦点等最小断言；E2E 各补一条代表路径，留到 T5 与整个相关文件一起跑。

**定向验证**：`pnpm exec vitest run --project dom tests/unit/components/workspace/agent-showcase.dom.test.tsx`；不为 3 套 PPT、10 张图逐一跑浏览器测试。

### T4 播客案例扩展

- [x] 增加 podcast card、音频播放/暂停/进度/时长、单音频互斥、transcript Dialog 和来源安全链接。
- [x] 处理 `play()` 拒绝、快速切换过期 Promise、`ended`、错误与卸载清理；关闭/离开页面后不残留播放。
- [x] 在 DOM 测试覆盖单音频、切换竞态、播放失败、transcript 和禁用态；在现有 E2E 文件补一条真实试听 + 文稿代表路径，留到 T5 统一跑。

**定向验证**：`pnpm exec vitest run --project dom tests/unit/components/workspace/agent-showcase.dom.test.tsx`；不新增 voice-twin 套件或 backend/live 测试。

### T5 相关回归与交付验证

- [x] 完成现有 E2E 文件的 legacy/showcase/shared 拆分：保留 co-writer 英文 prompt、草稿与显式提交；更新 showcase 中文 prompt、别名、retry、暗色 hero 选择器，不删除共享回归。
- [x] 用一个参数化用例覆盖 1440×900 / 1024×768 / 390×844 首屏与横向溢出；用一个英文用例抽查长 hero/UI；用一个暗色用例检查 hero、卡片和 Dialog。
- [x] 用一次冷缓存请求记录验证最重 creative-studio 初始 showcase 媒体 ≤1,500,000 bytes，并确认首屏不请求视频/音频/原图/PPTX；正常素材下无 console error/404。
- [x] 实际执行以下命令一次；失败时按第三节的分层规则定位、修复和重跑失败目标：

```bash
make -C frontend check
pnpm exec vitest run \
  tests/unit/core/agents/showcase.test.ts \
  tests/unit/components/workspace/agent-new-chat-guide.dom.test.tsx \
  tests/unit/components/workspace/agent-showcase.dom.test.tsx
pnpm exec playwright test tests/e2e/agent-new-chat-guide.spec.ts
```

- [x] 仅在出现既定升级信号时补全量 Vitest/Playwright、WebKit 或 perf 专项；没有新信号时不重复全量测试。

**验收**：相关静态门禁与定向回归通过；四类媒体各有一条真实浏览器行为证据；首屏、暗色、双语、键盘和草稿契约满足第五节至第八节要求。

### T6 文档与交付

- [x] 必写 `docs/changelog/changelog-2026-11-17-agent-showcase-integration.md`，包含 TL;DR、资产来源/58 文件/实际体积/压缩口径、验收记录（命令 → 实际关键输出）和首屏截图路径。
- [x] 更新本计划勾选与状态头，链接 changelog；执行 `python3 scripts/check_docs_convention.py` 一次。
- [ ] 按仓库约定走分支 + PR + AI 评审，既有 CI 通过后再合并；不默认新增本批专用 CI。若后续迁 CDN，只在 changelog 留指针，不在本批实现。

## 十、验证清单（实现级 / 行为级）

### 实现级（主要走 node/DOM）

- [x] `showcaseFor` 覆盖 4 个 Agent，返回 `null` 给 co-writer；案例数 3/3/4/10，prompt 工作流标记符合第五节契约。
- [x] 58 个媒体引用（含 `character`）与实际文件集合相等；PPT 预览 21、视觉预览 10、MP3 4、hero 4，总量 ≤20,000,000 bytes。
- [x] 四个 showcase Agent 不渲染原 guide h1/description 和简单 cases；co-writer 原 header/cases/输入框/tips 顺序不变。
- [x] 人物采用 T0 选定的统一方案；透明方案无白边，浅色底板方案呈现为明确设计容器，暗色无意外图片白块。
- [x] prompt 回填走 `PromptInputController`，不自动提交；locale 切换不重写已有草稿。
- [x] composer frame 仅影响四个 showcase 新会话 guide；1440 宽 ≥900px，内外两层 radius = 28px，移动 = 24px；全局 InputBox/已有会话不变。

### 行为级（现有 E2E 文件中的代表路径）

- [x] course-video：3 卡；prompt 填入；海报打开并播放；关闭后暂停/卸载；模型/技能/上传弹层不裁切。
- [x] ppt-master：打开一套 deck，完成翻页与一次真实 PPTX 下载；其余数量/路径由 node 测试覆盖。
- [x] creative-studio：验证一类筛选和一次图片或 SVG 下载；冷缓存初始媒体字节达标。
- [x] podcast-studio：真实试听一个节目、切换到另一个时前者停止、打开文稿/来源；播放竞态与卸载清理由 DOM 覆盖。
- [x] 非空草稿出现追加/替换/取消，取消不改草稿；URL/sessionStorage handoff 不被覆盖；只有显式提交创建 run。
- [x] loading/error 不出现假案例；disabled 禁止 prompt；旧别名、重试和已有会话行为保持。
- [x] 三个目标视口首屏达标、无横向溢出；暗色可读；键盘可打开/关闭 Dialog 并回填，焦点返回触发元素。

> 全量或专项测试不是禁止项：出现第三节所列共享回归、跨页回归、媒体兼容或资产预算信号时必须补做；没有新信号时不以重复全量运行代替定位。

## 十一、路径边界（allow / ban / 共享基座）

| 类型 | 路径 |
|---|---|
| allow（前端展示） | `frontend/src/components/workspace/agent-new-chat-guide.tsx`、`frontend/src/components/workspace/agent-showcase/**`（新）、`frontend/src/core/agents/showcase.ts`（新）、`frontend/src/core/i18n/locales/{zh-CN,en-US,types}.ts`、`frontend/public/agent-showcase/**`（新） |
| allow（测试/文档） | `frontend/tests/unit/components/workspace/agent-new-chat-guide.dom.test.tsx`、`frontend/tests/unit/core/agents/showcase.test.ts`（新）、`frontend/tests/unit/components/workspace/agent-showcase.dom.test.tsx`（新）、`frontend/tests/e2e/agent-new-chat-guide.spec.ts`、本计划、实现后的 changelog |
| ban | `frontend/src/components/ui/**`、`frontend/src/components/ai-elements/**`、`frontend/src/app/workspace/agents/**/page.tsx`（本批不需要改路由/stream）、backend/**、gateway/**、Agent runtime、thread/stream API、auth、config、package-lock/pnpm-lock、其他 Agent 业务、现有媒体素材（只新增 showcase 目录） |
| 共享基座 | `AgentNewChatGuide`、i18n locale/types、agent data；单执行者独占，不与其它前端批次并行改同一文件 |

## 十二、风险与开放问题

| 风险/问题 | 处理 |
|---|---|
| 15.32MB 二进制进 git 是否可接受 | 已选 public 静态；若仓库体积策略不允许，fallback 到对象存储/CDN，不改数据模型。 |
| 英文界面案例媒体为中文 | 已确认接受；UI/元数据双语，prompt/媒体保持中文。 |
| demo prompt 丢失 Agent 工作流 | 不照抄；node 测试固定播客四模式及 PPT/视觉/课程关键语义，E2E 不自动发送。 |
| 现有 guide 测试断言旧顺序/旧标题 | 拆 legacy/showcase/shared；co-writer 与共享草稿/提交回归保留，不只改断言数量。 |
| 定向不足或测试重复两个极端 | 默认相关套件 + 既有 PR CI；出现共享/跨页/兼容信号时升级全量或专项，无新信号不空跑。 |
| 首屏仍不够高 | 优先压缩 hero（人物 180–200px、字 26–30px）和输入区间距，不删除案例；以三个视口的 Playwright 边界断言验收。 |
| 人物源图实际不透明 | T0 先验一张；透明边缘不达标则统一浅色底板，禁止未处理白底图直接进入暗色页面。 |
| composer frame 裁切弹层 | frame 不设 `overflow:hidden`；只改 `glass-input-box`/`input-group` 的 radius，保留 `overflow-visible!`；E2E 打开模型/技能下拉验证。 |
| 只改圆角导致“看着还是窄” | 桌面 frame `max-w-5xl`（1024px），实际渲染 ≥900px；用 Playwright 量宽，不靠目测。 |
| 360 模板素材归属 | 用户已确认允许随项目分发；`README.md` 记录来源/授权；不提交密钥。 |
| 视频浏览器兼容 | 已确认 H.264/AAC 720p；Chromium 跑真实播放代表路径，若发现兼容问题再补最小 Safari/WebKit 专项，不默认扩浏览器矩阵。 |
| 大图/PPT 首屏流量 | 懒加载 + Dialog 挂载；首行海报可 eager，其余 lazy；最重路由冷缓存媒体请求 ≤1,500,000 bytes，CI 报预算再补 perf 专项。 |
| 现有 `guide.cases` 保留造成重复文案 | 四 Agent 不再渲染；保留为 fallback/其他 Agent，后续可单独清理。 |

### 已确认事项

- 360 模板 deck 的素材已确认允许进入 `frontend/public` 随项目分发，第三套 PPT 案例按计划保留。

## 十三、回滚

- 数据/组件独立：`showcaseFor` 返回 null 或删除 `agent-showcase/` 引用即可回退到现有 `guide.cases`。
- 不删除现有 `guide.cases`/tips/i18n，回滚不需要恢复被覆盖的文案。
- 素材目录独立，回滚可整体移除 `frontend/public/agent-showcase`；不影响其他 public 资产。
- 不改路由/stream/InputBox，回滚不涉及会话状态迁移。
