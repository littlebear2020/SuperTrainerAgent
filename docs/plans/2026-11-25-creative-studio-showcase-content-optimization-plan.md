# 创意工作室 10 件视觉案例内容优化实施计划

> **状态**：已执行（2026-11-25 定稿并当日执行；范围 = Task 1 与 Task 2.1–2.8，实现及离线验收全绿；2.9 PR/OCR 为交付步骤，完成后勾选；无拆出部分）。changelog：`docs/changelog/changelog-2026-11-25-creative-studio-showcase-content-optimization.md`。
> **基准 commit**：`e8803fe5832ba3da3715698c07ccfe9c370f0b43`（定稿时 `origin/main`；执行前先运行 `git diff e8803fe5832ba3da3715698c07ccfe9c370f0b43 --stat`）。
> **TL;DR**：按已审 Demo 一次性更新 `creative-studio` 的 10 件案例——替换 7 件素材、保留 3 件，文案、素材、尺寸、格式和提示词在同一任务变绿后提交，杜绝中间错位。卡片统一为“内容类型 + 使用场景”；四分类、顺序、布局与只填不发交互不变。仅影响 frontend showcase、素材和测试，不改 backend、skills、config、docker 或部署。

> **For agentic workers**：REQUIRED SUB-SKILL：使用 `executing-plans` 单执行者串行完成；逐项维护 checkbox。实现任务只允许在全部素材、文案、提示词和 unit 测试同时变绿后提交一次，禁止先提交新文案配旧图的中间状态。

**Goal**：把已确认的 10 件视觉案例 Demo 落入正式 `creative-studio` showcase，使用户能按真实使用场景选择案例，并获得项目现有工具可交付的 PNG / JPG / 可编辑 SVG 示例。

**Architecture**：继续以 `frontend/src/core/agents/showcase.ts` 为单一事实源，以静态目录交付图片和 SVG。VisualCase 用 localized `formatLabel` 取代对用户无决策价值的内部风格名；配色、材质与构图仅进入 prompt。两张海报的 prompt 直接指定自包含 HTML+CSS+SVG 与现有 Chrome headless 命令，不调用 `course-poster` 固定模板，也不借本批改技能或 agent。

**Tech Stack**：Next.js、React、TypeScript、Tailwind 4、静态 PNG/JPG/WebP/SVG、Vitest、Playwright、现有 Chrome headless / cwebp / ImageMagick；不新增运行时依赖。

**Spec / 需求来源**：
- 已审 Demo：`temp/2026-11-25-creative-studio-poster-demo/index.html` 与 `assets/`；只作素材来源和视觉对照，不入库、不作运行时依赖。
- 用户逐轮确认：10 件；保留「课程海报 / 知识图解 / 知识漫画 / 结构图」；卡片文案表达使用场景而非颜色、材质或内部风格名。
- 2026-11-25 Cursor 评审：采纳路由钉死、附录自包含、原子提交、旧断言清单和首屏预算；SVG 固定宽高建议不采纳，理由见评审决策。
- 本计划附录已收录全部 fields、spec、英文 description、formatLabel、note 和 recipeNote；执行者无需打开 Demo HTML 即可复现文案与提示词。

## Global Constraints

- 默认 10 件；分类与数量：课程海报 2、知识图解 4、知识漫画 2、结构图 2；顺序按附录 A 的 ID 数组，瀑布流依赖该顺序。
- 保留筛选键 `all / poster / card / comic / diagram`；`card + infographic` 仍合并到「知识图解」，不得新增或删除筛选。
- 保留 `columns-1 gap-4 sm:columns-2 xl:columns-3` 与筛选后的 `grid gap-4 sm:grid-cols-2 xl:grid-cols-3`；不得修改 Hero、composer、HOW IT WORKS、断点、卡片宽度或弹窗布局。
- 保留 CTA「按此风格创作」、草稿保护、附件不变、只填模板不自动发送、原图/SVG 下载和焦点恢复。
- 只改 VisualCard / VisualDialog 的格式信息；Video、Deck、Podcast 的卡片和弹窗行为不变。
- 示例课程周期、项目和服务均为演示设定；不得加入就业、薪资、提效比例、师资头衔或培训效果保证。
- 不自动附参考图，不改 agent runtime，不运行付费 live。测试只证明提示词文本与既有填充链路正确，**不得宣称点击 CTA 会生成与示例同版的海报或漫画**。
- 执行期间不修改用户已有工作区改动，不读取或提交秘密文件；`temp/` Demo、截图和一次性转换脚本不提交。
- 本批 >5 文件，走独立分支 + PR + OCR；推送前全量前端测试与检查必须绿。

## 评审决策记录

- **grilling 豁免**：既有案例素材与文案替换，无新功能、新产品面或配置变更；以用户逐轮 Demo 审版和 Cursor 评审为记录。
- **findings backlog**：已检查，无相关条目需并入；其他模块问题不顺手处理。
- **海报路线冲突**：`course-poster` 默认 Systemic Pulse，不能直接承接两张新海报；旧 prompt 的 `trainerai-imagine` 也不等于 Demo 的精确排版。附录 B 两张海报因此明确“直接写自包含 HTML+CSS+SVG + 给出 Chrome 命令 + 不调用固定模板 + 不套 Systemic Pulse”。本批只落地该文本，不做实时生成验收。
- **内部 ID**：4 个换题案例改名：`visual-poster-llm → visual-poster-manager`、`visual-comic-agent → visual-comic-customer`、`visual-diagram-stack → visual-diagram-talent`、`visual-diagram-rag → visual-diagram-addie`；其余 6 个不变。ID 不参与路由或持久化。
- **格式信息**：VisualCase 删除 `styleLabel`，新增 `formatLabel: LocalizedText`；VideoCase / DeckCase 的 styleLabel 不动。
- **风格信息去向**：只在 prompt 的「风格与版式」中；卡片、弹窗元信息与 recipeNote 不展示内部风格名。
- **SVG 尺寸**：两份静态示例 SVG 遵循 `trainerai-diagram` 输出规则，根节点只保留 `viewBox="0 0 1600 1000"`，不得增加固定 width/height；showcase 卡片数据仍写 width=1600、height=1000，这是版式元数据。用户新图的 prompt 不固定 viewBox 数值，而是按实际内容与 30px 边距计算，内容超出时扩大 viewBox。
- **原子提交**：10 件案例的数据、7 件替换素材、格式展示、prompt 和 unit 测试同属 Task 1；3 件保留案例的文案也包含在该 commit，任何中间提交都被禁止。

## 文件地图与路径边界

| 类别 | 路径 |
|---|---|
| 允许：案例数据 | `frontend/src/core/agents/showcase.ts`（VisualCase、visualFields、creative-studio、共享 visual recipeNote/海报输出指令） |
| 允许：展示 | `agent-showcase/showcase-cards.tsx`（仅 VisualCard）；`showcase-dialogs.tsx`（仅 VisualDialog） |
| 允许：素材 | `frontend/public/agent-showcase/visual/{previews,originals,diagrams}/` 中本计划列出的文件 |
| 允许：测试 | `frontend/tests/unit/core/agents/showcase.test.ts`；`frontend/tests/unit/components/workspace/agent-showcase.dom.test.tsx`；`frontend/tests/e2e/agent-new-chat-guide.spec.ts`；`frontend/tests/e2e/agent-showcase-style-workflow.spec.ts` |
| 允许：文档 | 本计划；执行日 `docs/changelog/changelog-<YYYY-MM-DD>-creative-studio-showcase-content-optimization.md` |
| 禁改 | `agent-showcase.tsx` 筛选逻辑、`agent-showcase.module.css`、`agent-new-chat-guide.tsx`、其他 Agent 数据、registry、backend、skills、config、docker、部署 |
| 共享基座 | showcase-cards/dialogs 只允许 Visual 分支；评审逐行确认其他 kind 无变化 |

## 已确认的可见文案

formatLabel 固定映射：`PNG 图片 / PNG image`、`JPG 图片 / JPG image`、`可编辑 SVG / Editable SVG`。下表 title、tag、description 均为精确值，不再留给执行者改写。

| ID | title.zh / title.en | tag.zh / tag.en | description.zh / description.en | 格式 |
|---|---|---|---|---|
| `visual-poster-agent` | AI Agent 实战训练营 / AI Agent Bootcamp | 课程海报 · 训练营招生 / Course poster · Bootcamp recruitment | 把学习成果、实战项目、适合人群和报名方式集中在一张竖版海报中。 / Present learning outcomes, hands-on projects, target learners, and enrollment details in one portrait poster. | PNG |
| `visual-poster-manager` | 新任管理者 90 天加速营 / 90-Day New Manager Accelerator | 课程海报 · 企业内训 / Course poster · Corporate training | 用 90 天成长路径说明课程安排，帮助培训负责人展示企业内训项目。 / Explain the program through a 90-day learning path and help training leaders present an internal management course. | PNG |
| `visual-card-rag` | RAG：让模型先查再答 / RAG: Retrieve Before Answering | 知识图解 · 技术概念科普 / Knowledge graphic · Technical concept explainer | 用检索、增强、生成三步，讲清 RAG 如何得到有依据的回答。 / Explain how RAG produces grounded answers through retrieval, augmentation, and generation. | JPG |
| `visual-card-prompt` | 提示词的四个要素 / The Four Elements of a Prompt | 知识图解 · 方法模板讲解 / Knowledge graphic · Reusable method template | 用角色、任务、上下文、格式四项，帮助学员写出可复用的提示词。 / Help learners write reusable prompts with four elements: role, task, context, and format. | JPG |
| `visual-comic-customer` | 客户投诉处理四步法 / Four Steps for Handling Customer Complaints | 知识漫画 · 客服情景演练 / Knowledge comic · Customer service role-play | 通过四个连续对话场景，练习倾听、复述、给出方案和确认结果。 / Practice listening, paraphrasing, proposing a solution, and confirming the outcome through four connected conversations. | PNG |
| `visual-comic-gradient` | 梯度下降：下山的路 / Gradient Descent: The Way Downhill | 知识漫画 · 抽象概念入门 / Knowledge comic · Abstract concept primer | 用下山比喻解释梯度、学习率和参数更新，降低初学者的理解门槛。 / Use a downhill metaphor to explain gradients, learning rate, and parameter updates for beginners. | JPG |
| `visual-infographic-training` | 大模型训练与上线 / LLM Training and Deployment | 知识图解 · 技术流程梳理 / Knowledge graphic · Technical workflow overview | 从数据清洗到部署，快速看清大模型训练与上线的关键环节。 / See the key stages from data cleaning to deployment in one LLM training and serving overview. | JPG |
| `visual-infographic-roadmap` | 企业 AI Agent 落地路线图 / Enterprise AI Agent Adoption Roadmap | 知识图解 · 企业落地规划 / Knowledge graphic · Enterprise adoption planning | 用评估、试点、扩展、治理四阶段，讨论企业 Agent 项目如何推进。 / Discuss enterprise Agent adoption through assessment, pilot, scale, and governance. | JPG |
| `visual-diagram-talent` | 企业 AI 人才能力模型 / Enterprise AI Talent Competency Model | 结构图 · 人才能力分层 / Diagram · Talent competency layers | 把业务场景、AI 协作、学习资源和评价标准组织成一套培养体系。 / Organize business scenarios, AI collaboration, learning resources, and assessment into one development framework. | SVG |
| `visual-diagram-addie` | ADDIE 课程开发流程 / ADDIE Course Development Process | 结构图 · 课程开发设计 / Diagram · Course development design | 展示从分析到评估的课程开发闭环，以及每个阶段需要交付的成果。 / Show the course development loop from analysis to evaluation and the deliverables expected at each stage. | SVG |

共享文案：
- recipeNote.zh：`保留：示例的画幅、构图、配色与版式。替换：主题、文案和知识点。` + 现有 `recipeFooter`。
- recipeNote.en：`Kept: the example's aspect ratio, composition, palette, and layout. Replace: the topic, copy, and knowledge points. ` + 现有 `recipeFooterEn`。
- note.zh：`10 个案例覆盖课程海报、知识图解、知识漫画与结构图，用于招生推广、概念讲解、情景演练、流程梳理和可编辑结构图；实际作品按用户内容选择生图、精确排版或 SVG。`
- note.en：`Ten examples across course posters, knowledge graphics, knowledge comics, and diagrams, covering recruitment, concept explainers, role-play, workflow overviews, and editable diagrams; each output uses image generation, precise layout, or SVG as appropriate for the user's content.`
- Hero 与三步说明不改；英文 UI 不得新增中文。

## 素材迁移与预算

`D` = `temp/2026-11-25-creative-studio-poster-demo/assets`，`V` = `frontend/public/agent-showcase/visual`。

| 动作 | 来源 | 正式产物 |
|---|---|---|
| 海报 A | `D/poster-agent.png` | `V/originals/poster-agent-bootcamp.png` + `V/previews/poster-agent-bootcamp.webp`；1080×1920 |
| 海报 B | `D/poster-manager.png` | `V/originals/poster-manager-90-days.png` + `V/previews/poster-manager-90-days.webp`；1080×1920 |
| 客服漫画 | `D/comic-customer.png` | `V/originals/comic-customer-service.png` + `V/previews/comic-customer-service.webp`；2048×1152 |
| 水墨漫画 | `D/comic-gradient.png` | 覆盖 `V/originals/comic-gradient.jpg` 与 `V/previews/comic-gradient.webp`；2048×1152 |
| 训练图解 | `D/infographic-training.png` | 覆盖 `V/originals/infographic-training.jpg` 与 `V/previews/infographic-training.webp`；2048×1152 |
| 人才模型 | `D/diagram-talent.svg` + `.png` | `V/diagrams/diagram-talent-model.svg` + `V/previews/diagram-talent-model.webp`；viewBox 1600×1000 |
| ADDIE | `D/diagram-addie.svg` + `.png` | `V/diagrams/diagram-addie-flow.svg` + `V/previews/diagram-addie-flow.webp`；viewBox 1600×1000 |
| 保留 | 当前 RAG、Prompt、企业路线图 | preview/original/download 不变 |

下载与结构图引用固定如下；未列出的 3 件保留案例 downloadName 不变：

| ID | downloadName | original / vector |
|---|---|---|
| `visual-poster-agent` | `visual-poster-agent.png` | original 指向新 PNG |
| `visual-poster-manager` | `visual-poster-manager.png` | original 指向新 PNG |
| `visual-comic-customer` | `visual-comic-customer.png` | original 指向新 PNG |
| `visual-comic-gradient` | `visual-comic-gradient.jpg` | original 沿用同名替换 JPG |
| `visual-infographic-training` | `visual-infographic-training.jpg` | original 沿用同名替换 JPG |
| `visual-diagram-talent` | `visual-diagram-talent.svg` | original 与 vector 均指向 `diagram-talent-model.svg` |
| `visual-diagram-addie` | `visual-diagram-addie.svg` | original 与 vector 均指向 `diagram-addie-flow.svg` |

两张结构图用于转 WebP 的 PNG 只作转换输入，不复制到 `public`；卡片 width/height 仍为 1600/1000，SVG 根节点不加固定宽高。

转换规则：PNG 原图复制；JPG 用 `magick <source.png> -quality 92 <target.jpg>`；**所有替换 preview 必须由对应 PNG 以 `cwebp -q 88 <source.png> -out <target.webp>` 生成**，不得用 JPG 二次压缩生成 WebP。

删除旧无引用文件：`poster-agent-dark-v2.jpg`、`poster-agent-dark.webp`、`poster-llm-deep-blue.jpg`、`poster-llm-deep-blue.webp`、`comic-agent.jpg`、`comic-agent.webp`、`diagram-agent-stack.svg`、`diagram-agent-stack.webp`、`diagram-rag-architecture.svg`、`diagram-rag-architecture.webp`（按原目录）。不得放宽“无未引用资产”断言。

预算门槛：总引用仍为 58、视觉 preview 仍为 10、全目录 ≤20,000,000 bytes；冷缓存创意工作室首屏媒体 ≤1,500,000 bytes，且不得请求 `/originals/`。评审试转估计首屏约 1,244,766 bytes、全目录约 17.49MB，只作参考，执行时以实际测量为准。若首屏超限，只降低 WebP 预览质量或尺寸，**不得提高 1.5MB 上限，也不得延迟/屏蔽请求来制造通过**。

## Review Focus

1. **结构回归**：筛选、瀑布流与断点被误改 → 既有布局基线不改，Task 2 四视口复验。
2. **格式/文件错位**：扩展名、formatLabel、original/vector 不一致 → Task 1 一致性断言。
3. **内部风格名漏到 UI**：只查卡片与弹窗，不查 prompt；禁用词清单见 Task 1。
4. **首屏与全目录膨胀**：Task 1 资产闭包 + Task 2 既有 1.5MB 冷缓存门禁；原图只在弹窗加载。
5. **提示词污染或路由漂移**：附录 B 逐字固定字段/spec；海报固定模板、漫画错误预设和旧案例事实均有正/负向断言。

---

### Task 1：10 件案例原子实现（文案 + 素材 + 格式 + prompt + unit）

**Files**：文件地图中 showcase 数据、VisualCard、VisualDialog、全部迁移素材和两个 unit 测试文件。

**Interfaces**：VisualCase 新增 `formatLabel: LocalizedText`；VisualCard/VisualDialog 用 `localized(item.formatLabel, locale)`。`visualFields.poster` 服务海报 A，新增 `visualFields.workshop` 服务海报 B；`visualTemplate` 与首行模式标记不变。

- [x] **1.1 写完整失败测试**：固定附录 A 的 10 个 ID 顺序、附录 B 的 fields/spec、可见文案、formatLabel、资产路径和尺寸。测试格式与扩展名一致、总资产 58、preview 10、总量 ≤20MB。
- [x] **1.2 固定旧断言修改范围**：dom 弹窗说明由 `9:16` 改为新 recipeNote + `1080 × 1920`；海报比例由 `1152 / 2048` 改为 `1080 / 1920`；更新 4 个改名 ID 的 testid、SVG 路径与下载名；弹窗格式行固定为 `宽 × 高 · formatLabel`。布局 class 与筛选数量断言不改。
- [x] **1.3 固定可见文案负向断言**：只检查卡片和弹窗，不检查 prompt。禁用词：`深空科技风、瑞士编辑风、深蓝瑞士风、3D 玻璃三幕、3D 玻璃叙事、Prompt 手稿、日系漫画、靛蓝水墨、数据管线、深绿路线图、石墨结构图、石墨暗色、冷白蓝图、钴蓝`。
- [x] **1.4 固定 SVG 断言**：两个 SVG 均为单文件自包含，除标准 xmlns 外无 http(s) 外链、`<image`、`@import`；根节点无 width/height，`viewBox` 必须等于 `0 0 1600 1000`。
- [x] **1.5 运行 RED**：`cd frontend && pnpm exec vitest run --project node tests/unit/core/agents/showcase.test.ts && pnpm exec vitest run --project dom tests/unit/components/workspace/agent-showcase.dom.test.tsx`；记录因旧文案、旧资产、旧比例和缺字段产生的预期失败。
- [x] **1.6 迁移素材**：按素材表一次完成复制、JPG 转换、`cwebp -q 88` 预览和旧文件删除；3 件保留案例二进制不变。
- [x] **1.7 一次更新全部数据与 Visual 展示**：10 件文案、7 件资产引用/尺寸、formatLabel、note、recipeNote、附录 B fields/spec、4 个 ID 同步落地。海报 prompt 必须逐字包含输出尺寸、HTML/CSS/SVG、Chrome 命令和“不调用固定模板/不套 Systemic Pulse”；客服漫画只用 `--art ligne-claire --tone warm --layout four-panel --aspect 16:9`，禁止 `--style four-panel`；梯度下降保持 ink-brush + 16:9 四格叙事，禁止套极简四格预设。
- [x] **1.8 运行 GREEN**：重跑 1.5；首行仍为结构图 `[模式: 结构图]`、其余 `[模式: 视觉创作]`。沿用现有模板无污染断言：不含完整 title、URL、`/agent-showcase/` 路径和来源名；不得新增会误伤附录 B 合法 fields/spec 的宽泛“固定示例文案”断言。`【请填写，如社群招募】` 保持原样，现有光标规则可以识别。
- [x] **1.9 原子提交前审查**：`git diff` 必须同时包含 10 件文案、7 件素材、格式展示、prompt 和测试，不得存在新标题配旧图或新图配旧 prompt。确认后仅提交一笔：`feat(showcase): 原子更新十件视觉案例素材文案与提示词`。

### Task 2：行为验收、首屏门禁与交付

**Files**：两个 E2E 文件；本计划；执行日 changelog。

- [x] **2.1 更新 E2E 新事实**：同步 4 个改名 ID、新 SVG 路径/下载名和新标题；E2E 中 `9 / 16` 显示比例可以保留，因为 1080×1920 仍为 9:16。既有布局 class、CTA 和筛选数量断言不改。
- [x] **2.2 十件原图行为断言【行为级】**：在 `/workspace/agents/creative-studio/chats/new` mock 环境逐一打开 10 件；PNG/JPG 的 naturalWidth/naturalHeight 必须等于 showcase 的 width/height；SVG 可渲染且 naturalWidth > 0，并另有 viewBox 与下载断言，不用 naturalWidth 推断 1600×1000。
- [x] **2.3 保留交互行为【行为级】**：默认 10；筛选 2/4/2/2；再次点击回全部；卡片与弹窗 CTA 填同一 prompt、附件不变、thread/run POST 为 0；Escape/焦点恢复及草稿取消/追加/替换不变。
- [x] **2.4 保留并运行首屏门禁【行为级】**：`creative-studio cold-cache initial media stays within budget without errors or deferred originals` 必须 ≤1,500,000 bytes，且无 `/originals/` 请求；若失败只调预览质量，不改上限或请求规则。
- [x] **2.5 定向 E2E**：`cd frontend && pnpm exec playwright test tests/e2e/agent-new-chat-guide.spec.ts tests/e2e/agent-showcase-style-workflow.spec.ts`；全绿、无 pageerror、无 404。
- [x] **2.6 四视口验收【行为级】**：1440 / 1280 / 1024 / 390 检查首屏、瀑布流与筛选 grid；缩略图可辨识，弹窗中文清晰，无横向溢出；截图放 temp，不入库。
- [x] **2.7 全量门禁**：后台运行 `cd frontend && pnpm test` 与 `pnpm check`；仓库根运行 `python3 scripts/check_docs_convention.py`。相关失败不得通过缩小断言解决。
- [x] **2.8 文档**：计划移回 `docs/plans/` 并勾选实际完成项；changelog 含 TL;DR、「验收记录」（命令 → 实际关键行）、首屏 bytes 与全目录 bytes。全绿后把唯一状态头改为已执行并链接 changelog；提交 `docs(showcase): 记录十件视觉案例优化验收`。
- [ ] **2.9 PR/OCR**：从最新 main 推送，`GH_REPO=littlebear2020/SuperTrainerAgent gh pr create`；无 Critical/Important 且 CI 绿后 rebase merge。部署不在范围。

## 完成定义

- 10 件案例与已审 Demo 一致；四分类、布局与交互零变化；7 件替换、3 件保留。
- 文案回答“适合什么任务、如何讲清内容、交付什么格式”；内部风格名不出现在卡片或弹窗。
- 所有图片/SVG 可打开；58 个资产引用闭包、10 个 preview、全目录 ≤20MB、首屏 ≤1.5MB 且不预载 originals。
- prompt 文本与附录 B 一致；只证明文本和填充链路，未跑真实生成，不承诺“同款生成”。
- 定向测试、全量 `pnpm test`、`pnpm check`、docs convention 全绿；changelog 有可审计记录。

## 附录 A：固定案例顺序

```text
visual-poster-agent
visual-poster-manager
visual-card-rag
visual-card-prompt
visual-comic-customer
visual-comic-gradient
visual-infographic-training
visual-infographic-roadmap
visual-diagram-talent
visual-diagram-addie
```

## 附录 B：10 件 fields 与 spec（精确值）

以下 fields 每行一项；均进入现有 `visualTemplate`，最后一段“仅参考视觉风格……”不变。

| ID | fields | spec |
|---|---|---|
| `visual-poster-agent` | 使用场景：【请填写，如社群招募】<br>目标受众与基础：【请填写】<br>核心学习成果：【请填写】<br>三个实战项目：【请填写】<br>课程周期与服务：【请填写】<br>行动号召：【请填写】 | `输出 1080×1920 PNG。直接编写自包含 HTML+CSS+SVG，并使用 /opt/chrome-headless-shell/chrome-headless-shell --headless=new --disable-gpu --no-sandbox --screenshot=OUTPUT_PATH.png --window-size=1080,1920 file:///INPUT_PATH.html 截图；不调用 course-poster 固定模板，不套 Systemic Pulse 默认配色或固定区块。深空黑背景、冷青网络拓扑、琥珀强调；依次呈现小眉题、大标题、成果副标题、Agent 工作流、三个项目、受众与课程设置、CTA；不使用无来源数据。` |
| `visual-poster-manager` | 使用场景：【请填写，如企业内训】<br>目标受众：【请填写】<br>核心学习成果：【请填写】<br>三个阶段与内容：【请填写】<br>工作坊与情境演练：【请填写】<br>行动号召：【请填写】 | `输出 1080×1920 PNG。直接编写自包含 HTML+CSS+SVG，并使用 /opt/chrome-headless-shell/chrome-headless-shell --headless=new --disable-gpu --no-sandbox --screenshot=OUTPUT_PATH.png --window-size=1080,1920 file:///INPUT_PATH.html 截图；不调用 course-poster 固定模板，不套 Systemic Pulse 默认配色或固定区块。采用暖白底、亮橙、钴蓝与黑色大字的瑞士编辑网格；依次呈现课程标题、大号周期标识、适合人群、三阶段时间轴、工作坊与情境演练、CTA；阶段名称与周期使用用户填写的内容，示例里的 30/60/90 只说明位置，不写进新海报；不使用装饰性伪数据图表。` |
| `visual-card-rag` | 主题：【请填写】<br>核心知识点：【请填写】<br>必须准确呈现的文字：【请填写】 | `深靛紫底、半透明 3D 玻璃、青绿高光、三幕横向构图与 16:9 画幅；信息结构按用户内容重新组织` |
| `visual-card-prompt` | 主题：【请填写】<br>核心知识点：【请填写】<br>必须准确呈现的文字：【请填写】 | `深色终端手稿、正文与四段批注、蓝 / 珊瑚 / 绿 / 紫标注，16:9 画幅；手稿内容全部由用户替换` |
| `visual-comic-customer` | 主题：【请填写】<br>角色与四格情节：【请填写】<br>必须保留的对白：【请填写】 | `使用 trainerai-comic 现有 --art ligne-claire --tone warm --layout four-panel --aspect 16:9；不要使用 --style four-panel。严格四格起承转合，统一人物造型与正常比例，表情清晰，包含职场环境和关键道具，对白简洁且逻辑连续。` |
| `visual-comic-gradient` | 主题：【请填写】<br>角色与四格情节：【请填写】<br>必须保留的对白：【请填写】 | `使用 trainerai-comic 的 ink-brush 画风，16:9 四格连续叙事、克制留白；不要套用极简 four-panel 预设。比喻需附带必要技术边界，不承诺全局最优。` |
| `visual-infographic-training` | 主题：【请填写】<br>核心知识点：【请填写】<br>必须准确呈现的文字：【请填写】 | `冷白数据管线、横向节点、连续箭头和输入 / 输出标注，16:9 画幅；阶段数量与内容由用户决定，简化流程须明确标注。` |
| `visual-infographic-roadmap` | 主题：【请填写】<br>核心知识点：【请填写】<br>必须准确呈现的文字：【请填写】 | `深绿时间线、S 形路径、阶段节点与停止条件，16:9 画幅；阶段与说明由用户重新填写` |
| `visual-diagram-talent` | 主题：【请填写】<br>节点与层级：【请填写】<br>连接关系：【请填写】 | `使用 trainerai-diagram 手写自包含横版 SVG；石墨暗色底、四层组织与蓝 / 绿 / 琥珀 / 紫分组，层级、节点与关系由用户内容决定；根节点不设置固定 width/height，viewBox 根据实际内容和 30px 边距计算，内容超出时扩大 viewBox。` |
| `visual-diagram-addie` | 主题：【请填写】<br>节点与层级：【请填写】<br>连接关系：【请填写】 | `使用 trainerai-diagram 手写自包含横版 SVG；钴蓝暗色网格、五阶段流程、阶段产物与评估回流，节点与拓扑由用户内容决定；根节点不设置固定 width/height，viewBox 根据实际内容和 30px 边距计算，内容超出时扩大 viewBox。` |
