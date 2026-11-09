# 首页与视频资产页重构实施计划

> **状态**：已执行（任务 1–7 完成，代码已提交；部署待进行；[执行记录](../changelog/changelog-2026-11-09-home-entry-and-video-assets-redesign.md)）
> **TL;DR**：将首页改为视频课/图文课双入口与六张创作卡，将视频页改为“我的作品/我的资产”；AI 创建框按入口初始化且关闭即卸载，作品仅前端筛选 ready 的 `ai_image/ai_video`、每次加载 6 个且最多最近 100 个；只改前端与 `public/course-samples/` 优化媒体，不改后端、API、数据库或部署数据结构。

> **For agentic workers**：必须使用 `subagent-driven-development`（推荐）或 `executing-plans` 逐任务实施；所有步骤用 checkbox 顺序勾选，禁止跳步、并行改共享文件或先写实现后补失败证据。

**Goal**：在不改变现有视频生成、课程项目 CRUD 与任务详情能力的前提下，交付与已确认 Demo 一致的信息架构、真实数据驱动的视频资产页及可验证的媒体降级体验。

**Architecture**：首页由静态课程目录、双入口展示组件和组合层构成；`course-video/creative-studio/ppt-master/co-writer` 进入既有 Agent 新会话，`ai_image/ai_video` 直接打开首页条件挂载的 `CreateTaskDialog`。视频资产页继续消费现有 `useVideoTasks()`、`useCourseProjects()` 与课程项目详情 CRUD，以本地 tabs、前端筛选/排序/分页和可复用缩略图完成重构，不引入服务端分页或新字段。

**Tech Stack**：Next.js 16 App Router、React 19、TypeScript、Tailwind CSS 4、TanStack Query、Vitest（node + happy-dom 双 project）、Playwright、ffmpeg/ffprobe；不新增 npm 依赖。

**Spec / 只读设计参考**：
- `/Users/littlebear/Documents/trainer ai/temp/supertraineragent-home-demo/index.html`
- `/Users/littlebear/Documents/trainer ai/temp/supertraineragent-home-demo/assets.html`
- 本计划「评审决策记录」是产品契约；Demo 只提供布局、命名与交互参考，不提供可照搬的生产代码、虚构数据或原始媒体。

**基准 commit**：`2f4848b840bd14b60511bb92d2b10f59b4f1a0db`（定稿时已核对 `HEAD == origin/main`）。

**执行方式**：单执行者、单 worktree、任务 1→7 串行。开始前从基准建 `home-entry-video-assets` 分支，先运行 `git diff 2f4848b840bd14b60511bb92d2b10f59b4f1a0db --stat` 评估漂移；若基准之后相关文件已有变化，先停下来更新计划中的接口与测试，不得直接套旧行号。此批次超过 5 个文件，完成后走分支 + PR + OCR，不直推 main。

## 目标

1. 首页提供“视频课 / 图文课”两个可展开入口，默认展开视频课，共六个真实创作入口，并保留真实近期项目的标题、类型、更新时间。
2. `/workspace/video-gen` 更名“视频资产”，以本地状态提供“我的作品 / 我的资产”两个 tabs，默认“我的作品”，不写 URL、localStorage 或服务端状态。
3. 我的作品展示真实任务标题、类型、ready 状态、更新时间、封面及查看/下载；只收 `ready + ai_image/ai_video`，首屏 6 个、每次多 6 个、仅最近 100 个。
4. 我的资产复用课程项目列表、新建、详情编辑、保存与删除，卡片展示标题、风格、更新时间与三级封面降级。
5. 侧边栏仅保留首页、视频资产、运营后台（保留现有角色文案逻辑）、最近对话和设置；隐藏入口对应的路由与深链继续可用。
6. 样例媒体轻量、可播放、可降级；正常模式仅当前展开面板自动播放，reduced motion 下不自动播放，播放失败不阻断创作入口。

## 非目标

- 不修改 `backend/**`，不新增或修改 `/api/v3/**`、任何后端 API、数据库 schema、迁移、数据修复或服务端分页；不需要运行 backend pytest。
- 不做项目统计、视频时长、分辨率、完成时间、系统健康状态，不做项目级视频预览聚合；课程项目详情现有的关联任务列表不属于本批新增聚合能力，保持原样。
- 不移动或重写 `video-gen-page.tsx` 中 `TaskCard` 的 recompose 逻辑，不改 `WORKFLOWS`，不删除 `matchAgent` / `ROUTING_RULES` 导出，不删除旧 Agent/对话/灵感库等 App Router 路由。
- 不重构视频生成 payload、分镜、渲染、重合成、音色、供应商或 worker，不调整 Remotion 模板。
- 不引入新状态库、测试库、媒体库或 npm 依赖，不手改 registry 生成的 `components/ui/`、`components/ai-elements/`。
- 不把 Demo 原始 MP4/PNG 直接复制进生产，不在 `frontend/public/course-samples/` 之外新增本批媒体。
- 不静默提高 `performance-budgets.json` 预算，不改 `config_version`，不重建 gateway、worker、nginx 或其他服务。

## 评审决策记录（六项已确认，不得在执行中重新发明）

| Frontier 问题 | 用户确认的推荐答案 |
| --- | --- |
| **Q1：AI 生图/AI 生视频从何处打开？** | 首页直接复用 `CreateTaskDialog`，新增可选 `initialTaskType` 并按需条件挂载；分别预选 `ai_image` / `ai_video`，不新增后端接口或页面路由。关闭即卸载，交替打开时类型、标题、脚本、风格、供应商和高级设置均为全新实例。 |
| **Q2：我的作品是否保留按需预览？** | 保留，但只在 `ready && artifact_missing !== true` 时启用；`preload="none"`，悬停或键盘聚焦才加载，不预取整页视频。missing 卡片仍可见，仅预览/下载禁用。 |
| **Q3：作品历史展示多大范围？** | 首屏 6 个，每次“加载更多”增加 6 个；只在现有接口最近 100 条内继续展示，不承诺第 100 条以后的历史，不增加服务端分页。 |
| **Q4：我的资产封面从哪里来？** | 仅使用项目自身数据，依次为 `styleAnchorImageUrl(project.id)`（有锚图时）→ `stylePreviewImage(...)` → 项目主色/中性色块；不做项目与视频的关联聚合，静态预览只标记为风格示意。 |
| **Q5：两个资产视图是否需要 URL 状态？** | 本批只做组件本地状态，默认我的作品；不加 query、深链或 localStorage，刷新后回到默认视图。 |
| **Q6：样例视频如何处理体积与播放？** | 三张卡保留持续循环播放，素材压缩为 8–12 秒、960px、无音轨、H.264、约 1–2MiB；仅当前展开面板加载/播放，切换、页面隐藏或 reduced motion 时停止。PPT/文章案例使用静态 JSX/CSS 装饰。 |

其余已确认产品契约：
- 六入口名称与目标固定为 `Remotion动效 → course-video`、`海报制作 → creative-studio`、`PPT制作 → ppt-master`、`文章写作 → co-writer`、`图片动效 → ai_image`、`AI视频 → ai_video`。
- 侧边栏“工作台”改“首页”，“视频”改“视频资产”，隐藏对话/智能体/灵感库/配音/写作/原资产中心，保留运营后台/最近对话/设置。近期项目只保留标题/类型/更新时间。
- 我的资产只保留标题/风格/更新/新建编辑；我的作品只保留标题/类型/ready/更新/封面/查看下载。兼容既有导出、深链和仍适用测试，不为过期 UI 断言隐藏旧 DOM。
- 视频资产原页头按图四整体隐藏：原标题、说明、类别 Tabs 和“新建任务”按钮不再显示；创建入口统一由首页 AI 卡承担，既有弹窗与提交能力保留。
- 新增外壳与卡片需要中英文 i18n；不把整个旧项目详情页、旧编辑器或全部 legacy 组件的翻译改造纳入本批。

## 路径边界（allow / ban / 共享基座）

| 允许路径（allow） | 禁改路径（ban） | 共享基座触达（否 / 需规划侧独占） |
| --- | --- | --- |
| 本计划七个任务 Files 列出的 `frontend/src/components/workspace/{workspace-home,workspace-header,home-course-catalog,home-course-entry}.tsx`、`frontend/src/components/workspace/video-gen/**` 指定文件、`frontend/src/core/workspace/nav-routes.ts`、`frontend/src/core/i18n/locales/{types,zh-CN,en-US}.ts`、对应 `frontend/tests/unit/**` 与 `frontend/tests/e2e/**`、新增 `frontend/public/course-samples/**`、本计划及执行 changelog | `backend/**`、`docker/**`、`skills/**`、`video-templates/**`、根 `config*.yaml`、`.env*`、`frontend/src/components/ui/**`、`frontend/src/components/ai-elements/**`、`frontend/src/core/v3/**`、`frontend/package.json`、`frontend/pnpm-lock.yaml`、`frontend/performance-budgets.json`、`frontend/src/components/workspace/workflow-grid.tsx`、未列出的 App Router 页面 | **是，单执行者串行触达**：`workspace-home.tsx`、`video-gen-page.tsx`、`video-gen-create-dialog.tsx`、三个 locale 文件、`mock-video-api.ts` 和本计划。不得派第二个执行 agent 并行修改；发现必须改 ban 路径即停工回规划侧。 |
| 允许在 `video-gen-page.tsx` 内仅替换 `TaskThumbnail` 定义/导入与 `VideoGenPage` 主组合区 | `TaskCard`、`StatusBadge`、`getActiveStatusLabel`、`useRecomposeTask` / `recomposeActionFor` / `RecomposeConfirmDialog` 代码区；`video-gen-detail*.tsx`、分镜与 recompose 相关文件 | recompose 区域是只读共享基座；任务 4 的源码静态守卫和旧行为测试必须证明未移动。 |
| 执行收尾可更新本计划原状态行、勾选任务，并按实际执行日新增一份 `docs/changelog/changelog-YYYY-MM-DD-home-entry-and-video-assets-redesign.md` | 不新增其他设计稿、报告、测试脚手架或 README 名册；不顺手改 findings backlog（当前无相关项） | 文档也由同一执行者收尾，遵守唯一状态头与 changelog「验收记录」节。 |

## 全局实现约束

- 所有新增组件测试使用仓库现有的 `createRoot + act` happy-dom harness；不得添加 `@testing-library/react`。纯函数落 `*.test.ts`，需要 `document`、DOM render 或 media event 的落 `*.dom.test.tsx`，Playwright 只落 `tests/e2e/*.spec.ts`。
- 旧 `workspace-home.test.ts`（`matchAgent`）、`video-gen-page-thumbnail.dom.test.tsx`（原路径 re-export）、`task-card-retry*.test.*`、recompose、课程项目 dirty guard/delete confirm 测试保持有效。因首页/导航产品契约变化而必须改的断言，要原地更新测试意图并说明原因；不得保留不可见旧 UI 来骗取通过。
- `workspace-home.tsx` 中 `ROUTING_RULES` 与 `matchAgent` 原样保留，即使新首页不再渲染自由输入框；`workflow-grid.tsx` 完全不改。近期项目继续用真实 `useThreads()` + `ProjectList`，不将 Demo 的三条假项目带入生产。
- 日期：作品排序用 `created_at`，展示更新用 `updated_at ?? created_at`；空日期显示“更新时间未知”，无效/空日期排最后，不显示 `Invalid Date`。项目列表按 `updated_at ?? created_at` 新到旧展示，但不做服务端排序参数。
- 标题：作品空标题显示“未命名作品”，项目使用真实标题；不得从时长、分辨率、费用、帧数或 Demo 文案虚构标题/统计。下载使用现有 `getVideoDownloadUrl(task.id)`，不猜输出格式、码率或完成时间。
- “查看”指展开该作品自己的内联播放器，不新增项目级聚合预览。下载用同源 `<a href={getVideoDownloadUrl(id)} download>`，让服务端/浏览器决定真实文件名。
- 更新源码附近仍描述旧 hero、旧 tabs、旧缩略图位置的注释；不改 `frontend/AGENTS.md` 或模块笔记中的真实路由清单，因为本批只隐藏导航、不删除路由。
- 每个任务必须先看到指定新测试因缺少该任务能力而失败，再做最小实现；失败若是 unrelated mock 缺失，先修测试 harness，不能把它当作功能失败证据。
- 预计超过 120 秒的全量测试/build/perf 命令按根约定后台执行并等待完成通知；冻结被测代码，不轮询、不并行共享 `.next` 或同一 E2E 端口。

## Review Focus

1. **ready 但产物已清理、同时还有陈旧 thumbnail URL**：用户应看见“产物已清理”且不发缩略图/预览/下载请求；由 Task 5 `missing artifact stays visible...`、Task 4 missing 不请求图片和 Task 7 missing E2E 共同钉扎。
2. **超过 100 个作品、空/非法时间戳与同时间 ID**：用户只能浏览确定的最近 100 个，不能看到 `Invalid Date` 或每次刷新乱序；由 Task 5 `sorts created_at descending...` 与 105 条 fixture 钉扎。
3. **锚图 404、风格图也 404、历史/未知风格 key**：封面应逐级退到色块，风格文字不得错指为另一风格；由 Task 6 `anchor error falls back...`、`never exposes local anchor path...` 钉扎。
4. **reduced motion、页面隐藏、play() reject 与媒体 error**：当前面板之外不得继续播放，失败不得禁用创作入口；由 Task 2 media spies 与 Task 7 reduced-motion/playback-failure E2E 钉扎。
5. **AI 创建框反复打开并切换类型**：新入口必须是新实例，旧标题/脚本/供应商/高级设置不得泄漏；由 Task 1 非受控初值测试、Task 2 条件卸载测试和 Task 7 真实表单交替测试钉扎。

## 文件职责图

| 文件 | 动作 | 单一职责与下游关系 |
| --- | --- | --- |
| `frontend/src/components/workspace/home-course-catalog.tsx` | Create | 六入口 ID、所属课程、动作类型、Agent 名/AI 初始类型、`course-samples` 路径的静态 SoT；不存 React 状态、不存虚构项目。 |
| `frontend/src/components/workspace/home-course-entry.tsx` | Create | 双入口展开、六案例卡、当前面板媒体播放/reduced motion/visibility/失败降级；通过 `onSelect` 只发动作，不直接路由。 |
| `frontend/src/components/workspace/workspace-home.tsx` | Modify | 组合 hero、`HomeCourseEntry`、真实 `ProjectList`；Agent 路由与 AI 对话框条件挂载；保留 `matchAgent/ROUTING_RULES`。 |
| `frontend/src/components/workspace/video-gen/video-gen-create-dialog.tsx` | Modify | 新增 `initialTaskType?: "ai_image" \| "ai_video"` 并传入既有表单初始 state，其余创建/提交逻辑不变。 |
| `frontend/src/core/workspace/nav-routes.ts` | Modify | 可见导航与 ⌘K 页面组收敛为视频资产和 console；首页仍由 header 的精确 `/workspace` 链接负责。 |
| `frontend/src/components/workspace/workspace-header.tsx` | Modify | 保留首页链接精确高亮，通过更新后的 i18n 显示“首页”；不改品牌、最近对话或设置。 |
| `frontend/src/core/i18n/locales/{types,zh-CN,en-US}.ts` | Modify | 新增首页双入口/六卡片文案并更新 dashboard/videoGen；保证 en-US 无中文残片。旧翻译键可保留，但不能渲染旧 UI。 |
| `frontend/src/components/workspace/video-gen/task-thumbnail.tsx` | Create | 从旧页面原样抽出封面、missing、onError 与 URL 变化复位；增加可选 `className` 供作品大卡复用。 |
| `frontend/src/components/workspace/video-gen/completed-video-grid.tsx` | Create | 作品过滤/排序/最近 100、本地 6 个分页、卡片、内联查看、下载与播放失败；只消费 `VideoTaskBrief[]`。 |
| `frontend/src/components/workspace/video-gen/course-project-page.tsx` | Modify | 我的资产卡片网格与三级封面，保留 `selectedId → CourseProjectDetailPage` 和 `CourseProjectDialog` CRUD。 |
| `frontend/src/components/workspace/video-gen/video-gen-page.tsx` | Modify | re-export `TaskThumbnail`；主页面组合“视频资产 + 双 tabs”，隐藏原标题、说明、类别 Tabs 和新建任务按钮。旧 `TaskCard/StatusBadge/recompose` 留在原文件供详情与旧测试使用，创建入口由首页承担。 |
| `frontend/tests/e2e/utils/mock-video-api.ts` | Modify | 可播种真实字段、课程项目 CRUD、缩略图/锚图与有效静态 MP4；不依赖后端。 |
| `frontend/public/course-samples/` | Create | 仅三个优化 MP4 与 `knowledge-card.webp`；同时供首页与 E2E 有效媒体使用，不加入已被文章案例淘汰的 `course-poster.webp`。 |

---

### Task 1：为 CreateTaskDialog 增加可测的 initialTaskType

**Files:**
- Modify: `frontend/src/components/workspace/video-gen/video-gen-create-dialog.tsx`（`useCreateTaskForm` 与 `CreateTaskDialog` props；不改 payload）
- Test: `frontend/tests/unit/components/workspace/video-gen/video-gen-create-dialog-initial-type.dom.test.tsx`

**Interfaces:**
- Consumes: 既有 `CreateTaskDialog({ open, onOpenChange })`、Radix `Select` 与 `TYPE_CONFIG`。
- Produces:
  - `export type InitialVideoTaskType = "ai_image" | "ai_video"`。
  - `CreateTaskDialog` 新增可选 `initialTaskType?: InitialVideoTaskType`，缺省 `ai_image`。
  - `useCreateTaskForm` 接收同名可选参数，只用于 `taskType` 的 `useState` 初值；组件挂载后用户仍可通过既有 `handleTypeChange` 切换全部既有类型。
- Later: Task 2 首页只传 `ai_image` 或 `ai_video`，并依赖关闭卸载来获得全新表单 state。

- [x] **Step 1：先写失败测试**

新建 `video-gen-create-dialog-initial-type.dom.test.tsx`，沿用现有 create-dialog 测试的完整 hooks mock，至少覆盖：
- `initializes ai_video from initialTaskType`：以 `open={true} initialTaskType="ai_video"` 渲染，视频类型 trigger 显示 `TYPE_CONFIG.ai_video.label`，且不显示 `ai_image` 的选中态。
- `initializes ai_image and defaults to ai_image`：分别显式传 `ai_image` 与不传 prop 两次全新 mount，均显示 `TYPE_CONFIG.ai_image.label`。
- `does not rewrite task type on ordinary rerender`：同一 mount 内模拟用户切到另一既有类型后，以相同 `initialTaskType` 重渲染，断言用户选择未被 prop 重置；本计划只要求“初始值”，不得做成受控 `taskType`。

- [x] **Step 2：运行并确认因接口缺失而失败**

Run: `cd frontend && pnpm exec vitest run --project dom tests/unit/components/workspace/video-gen/video-gen-create-dialog-initial-type.dom.test.tsx`

Expected: FAIL；TypeScript/运行时尚不接受 `initialTaskType`，AI 视频用例仍显示默认 `AI 生图`。若失败是 mock 缺导出，先补齐 harness 再重跑，直到看到上述功能原因。

- [x] **Step 3：最小实现**

在 `video-gen-create-dialog.tsx` 定义并导出上述联合类型，给 `CreateTaskDialog` 与 `useCreateTaskForm` 的参数增加 `initialTaskType = "ai_image"`，将现有 `useState<...>("ai_image")` 改为使用该初值并向下传递 prop。不要加 effect 监听 prop，不改 `handleTypeChange`、提交 payload、表单重置或其他类型标签。

- [x] **Step 4：运行定向与相邻回归并确认通过**

Run:
```bash
cd frontend
pnpm exec vitest run --project dom tests/unit/components/workspace/video-gen/video-gen-create-dialog-initial-type.dom.test.tsx tests/unit/components/workspace/video-gen/video-gen-create-dialog-voice.dom.test.tsx tests/unit/components/workspace/video-gen/draft-parity.dom.test.tsx
pnpm exec vitest run --project node tests/unit/video-gen-create-payload.test.ts
```

Expected: 全部 PASS；旧创建框调用方无需修改，payload 测试无变化。

- [x] **Step 5：提交**

```bash
git add frontend/src/components/workspace/video-gen/video-gen-create-dialog.tsx frontend/tests/unit/components/workspace/video-gen/video-gen-create-dialog-initial-type.dom.test.tsx
git commit -m "feat(video-gen): 支持创建对话框指定初始 AI 任务类型"
```

---

### Task 2：首页双入口、六卡片与优化样例媒体

**Files:**
- Create: `frontend/src/components/workspace/home-course-catalog.tsx`
- Create: `frontend/src/components/workspace/home-course-entry.tsx`
- Modify: `frontend/src/components/workspace/workspace-home.tsx`
- Modify: `frontend/src/core/i18n/locales/types.ts`
- Modify: `frontend/src/core/i18n/locales/zh-CN.ts`
- Modify: `frontend/src/core/i18n/locales/en-US.ts`
- Create: `frontend/public/course-samples/remotion.mp4`
- Create: `frontend/public/course-samples/image-motion.mp4`
- Create: `frontend/public/course-samples/ai-video.mp4`
- Create: `frontend/public/course-samples/knowledge-card.webp`

- Test: `frontend/tests/unit/components/workspace/home-course-catalog.test.ts`
- Test: `frontend/tests/unit/components/workspace/home-course-entry.dom.test.tsx`
- Test: `frontend/tests/unit/components/workspace/workspace-home-course-entry.dom.test.tsx`
- Modify: `frontend/tests/unit/components/workspace/workspace-home-i18n.dom.test.tsx`
- Unchanged guard: `frontend/tests/unit/workspace/workspace-home.test.ts`
- Read-only inputs: Demo 两份 HTML 与 `temp/supertraineragent-home-demo/assets/*`，不得修改或直接复制。

**Interfaces:**
- Consumes: Task 1 `CreateTaskDialog.initialTaskType`；既有 `useThreads`、`ProjectList`、`useRouter`、`useI18n`。
- Produces:
  - `CourseEntryId = "video" | "text"`。
  - `CourseItemId = "course-video" | "ai_image" | "ai_video" | "creative-studio" | "ppt-master" | "co-writer"`。
  - `HomeCourseItem` 为可辨识联合：`{ kind: "agent"; agentName: "course-video" | "creative-studio" | "ppt-master" | "co-writer" }` 或 `{ kind: "video-task"; initialTaskType: InitialVideoTaskType }`，并共同包含 `id`、`entry`、`media`。
  - `HOME_COURSE_ITEMS: readonly HomeCourseItem[]` 顺序固定为上述六 ID；视频媒体路径仅允许 `/course-samples/{remotion,image-motion,ai-video}.mp4`，海报用 `/course-samples/knowledge-card.webp`，PPT/文章为 `kind: "ppt" | "article"` 的无外链装饰媒体，不得再引用 `course-poster.webp`。
  - `HomeCourseEntry({ onSelect }: { onSelect: (item: HomeCourseItem) => void })`。
  - `t.workspace.home.courseItems[id]` 提供 `title/description/badge/mediaAlt`；`videoCourse/textCourse` 与 hero 新键在三种 locale 文件中同形。
- Later: Task 7 E2E 以六 ID、Agent URL 和 AI 初始类型为行为契约。

- [x] **Step 1：先写失败测试**

`home-course-catalog.test.ts`：
- `exposes exactly six confirmed routes in stable order`：`HOME_COURSE_ITEMS.map(i => i.id)` 精确等于六 ID 顺序；视频课前三、图文课后三。
- `maps agent entries and AI entries without inventing remotion_course route`：四个 Agent 的 `agentName` 精确相等；两个 AI 项 `initialTaskType` 分别为 `ai_image/ai_video`；`course-video` 必须是 Agent，不得误开 Remotion 创建框。
- `uses only optimized course sample media`：每个外部 `src/poster` 均以 `/course-samples/` 开头，且不包含 Demo `assets/`、原 PNG 或外链。

`home-course-entry.dom.test.tsx`（stub `matchMedia`、`HTMLMediaElement.play/pause`）：
- `defaults to video course and exposes three cards`：视频按钮 `aria-expanded=true`，显示三个指定标题，图文 panel 隐藏。
- `switches to text course and pauses hidden panel video`：点击图文课后三个图文标题可见，视频 panel 隐藏，旧视频 `pause` 被调用；切回只恢复当前面板。
- `reduced motion never autoplays and change event pauses current video`：reduce 初始为 true 时 `play` 零调用，后续偏好变化与 `visibilitychange` 都执行停止逻辑。
- `play rejection and media error show fallback without disabling entry`：让 `play()` reject，再派发 `error`，出现“视频暂时无法播放”，点击对应卡片仍 `onSelect` 一次。

`workspace-home-course-entry.dom.test.tsx`：
- `agent cards navigate to existing new-chat routes`：逐项断言四个 `router.push` 为 `/workspace/agents/<agent>/chats/new`。
- `AI dialog is conditionally unmounted between alternating entries`：mock 对话框捕获 props；打开 `ai_image` 后关闭，断言组件从树消失，再打开 `ai_video` 收到新 prop 且不是旧实例；反向再开 `ai_image` 同样成立。
- `keeps real recent project list`：`ProjectList` 收到 `useThreads` 的真实种子，页面没有 Demo 假标题。

将 `workspace-home-i18n.dom.test.tsx` 的过期 hero/专家网格断言更新为新契约：en-US 下双入口、六卡片、hero 与近期项目自有文案无 CJK，zh-CN 显示指定命名；保留 `workspace-home.test.ts` 原样，验证 `matchAgent/ROUTING_RULES` 未删。

- [x] **Step 2：运行并确认失败**

Run:
```bash
cd frontend
pnpm exec vitest run --project node tests/unit/components/workspace/home-course-catalog.test.ts tests/unit/workspace/workspace-home.test.ts
pnpm exec vitest run --project dom tests/unit/components/workspace/home-course-entry.dom.test.tsx tests/unit/components/workspace/workspace-home-course-entry.dom.test.tsx tests/unit/components/workspace/workspace-home-i18n.dom.test.tsx
```

Expected: 新 catalog 文件/组件不存在，旧首页没有六入口、条件创建框与新 i18n，故新用例 FAIL；旧 `matchAgent` 用例应 PASS，并作为后续兼容基线。

- [x] **Step 3：最小实现与媒体转码**

先实现静态目录和双入口：
- `home-course-catalog.tsx` 只保存上述配置；`home-course-entry.tsx` 本地 `activeEntry` 默认 `video`，两个入口用 `aria-expanded/aria-controls`，panel 用 `role=region`，卡片用真实 `<button>`。视频不设无条件 `autoPlay`，由 effect 根据当前 panel、`document.hidden`、`prefers-reduced-motion` 调 `play/pause`；监听 media preference 与 visibility 变化并清理。失败 overlay 用 `role=status`，不拦截卡片点击。
- `workspace-home.tsx` 渲染新 hero、双入口和真实近期项目：保留主标题，移除未核验的系统就绪徽标、副标题和自由输入框，不再渲染旧专家网格；保留 `matchAgent/ROUTING_RULES` 导出。Agent 项走既有新会话 URL；AI 项保存 `createTaskType`，仅非 null 时渲染 `<CreateTaskDialog open initialTaskType={createTaskType} ... />`，`onOpenChange(false)` 清 null。不要在 VideoGenPage 里转跳打开对话框。
- locale 增加新文案，更新仍描述旧 hero 的注释；卡片响应式为移动端 1 列、较宽屏 3 列，近期项目字段不改。

再从只读 Demo 源生成媒体（以下为每个 MP4 的固定参数，`SRC` 分别为 `remotion.mp4/image-motion.mp4/ai-video.mp4`，`OUT` 为同名目标；执行时给变量赋绝对源路径，不写脚本文件）：
```bash
mkdir -p frontend/public/course-samples
ffmpeg -hide_banner -y -ss 00:00:05 -i "$SRC" -t 10 \
  -vf "scale=960:-2:flags=lanczos,fps=24" -an \
  -c:v libx264 -preset slow -b:v 1200k -maxrate 1500k -bufsize 3000k \
  -pix_fmt yuv420p -movflags +faststart "$OUT"
ffmpeg -hide_banner -y -i "/Users/littlebear/Documents/trainer ai/temp/supertraineragent-home-demo/assets/knowledge-card.png" \
  -vf "scale=960:-2:flags=lanczos" -frames:v 1 -c:v libwebp -quality 82 \
  frontend/public/course-samples/knowledge-card.webp
```
不得使用 `cp` 复制 MP4/PNG。若三个 5 秒起点不具代表性，只可调整 `-ss`，仍须保持 10 秒输出并人工看过首/中/尾帧；不得放宽编码、尺寸和体积门槛。

- [x] **Step 4：运行功能测试与 ffprobe 验收并确认通过**

Run:
```bash
cd frontend
pnpm exec vitest run --project node tests/unit/components/workspace/home-course-catalog.test.ts tests/unit/workspace/workspace-home.test.ts
pnpm exec vitest run --project dom tests/unit/components/workspace/home-course-entry.dom.test.tsx tests/unit/components/workspace/workspace-home-course-entry.dom.test.tsx tests/unit/components/workspace/workspace-home-i18n.dom.test.tsx
for f in public/course-samples/*.mp4; do
  ffprobe -v error -select_streams v:0 \
    -show_entries stream=codec_name,codec_type,width,height,pix_fmt,r_frame_rate \
    -show_entries format=filename,duration,size -of json "$f"
  test -z "$(ffprobe -v error -select_streams a -show_entries stream=index -of csv=p=0 "$f")"
done
ffprobe -v error -show_entries stream=codec_name,width,height -of json public/course-samples/knowledge-card.webp
```

Expected: 测试全部 PASS；每段 MP4 `codec_name=h264`、`width=960`、`pix_fmt=yuv420p`、`duration` 在 8–12 秒、`size` 在 1,048,576–2,097,152 字节，音轨检查为空且每个 `test` 退出 0；知识卡 WebP 宽不超过 960。用浏览器/抽帧人工确认画面不是黑帧、不是重复错误段，三个样例与各自入口语义相符；`git status --short` 不得出现 Demo 文件改动或 `course-samples` 之外的新媒体。

- [x] **Step 5：提交**

```bash
git add frontend/src/components/workspace/home-course-catalog.tsx frontend/src/components/workspace/home-course-entry.tsx frontend/src/components/workspace/workspace-home.tsx frontend/src/core/i18n/locales/types.ts frontend/src/core/i18n/locales/zh-CN.ts frontend/src/core/i18n/locales/en-US.ts frontend/public/course-samples frontend/tests/unit/components/workspace/home-course-catalog.test.ts frontend/tests/unit/components/workspace/home-course-entry.dom.test.tsx frontend/tests/unit/components/workspace/workspace-home-course-entry.dom.test.tsx frontend/tests/unit/components/workspace/workspace-home-i18n.dom.test.tsx
git commit -m "feat(workspace): 重构首页双入口并加入优化课程样例"
```

---

### Task 3：侧边栏与首页命名收敛

**Files:**
- Modify: `frontend/src/core/workspace/nav-routes.ts`
- Modify: `frontend/src/components/workspace/workspace-header.tsx`
- Modify: `frontend/src/core/i18n/locales/zh-CN.ts`
- Modify: `frontend/src/core/i18n/locales/en-US.ts`
- Modify: `frontend/tests/unit/components/workspace/agents-nav-entry.dom.test.tsx`
- Unchanged guard: `frontend/tests/unit/components/workspace/workspace-header-active.dom.test.tsx`
- Modify: `frontend/tests/e2e/sidebar.spec.ts`
- Do not modify: `workspace-sidebar.tsx`、`recent-chat-list.tsx`、`workspace-nav-menu.tsx`、任何 App Router 页面。

**Interfaces:**
- Consumes: `WorkspaceNavChatList` 仍从 `WORKSPACE_NAV_ITEMS` map；`WorkspaceNavMenu` 继续提供设置；`RecentChatList` 继续提供最近对话。
- Produces: `WORKSPACE_NAV_ITEMS.map(i => i.href) === ["/workspace/video-gen", "/workspace/console"]`，两者保留 `includeInPalette: true`，因此 `PALETTE_NAV_ITEMS` 同样只含这两个可见页面；`videoGen` 中文为“视频资产”，`dashboard` 中文为“首页”。
- Compatibility: `WorkspaceNavItem`、`isNavItemActive` 签名不变，console 的 `labelRole` 与管理员/普通用户文案不变；隐藏页深链不删。

- [x] **Step 1：先写失败测试（更新旧导航断言）**

将 `agents-nav-entry.dom.test.tsx` 的套件改为“侧边栏可见入口收敛”，断言：
- `only video assets and console remain in route navigation`：两个 href 精确存在，`/workspace/chats`、`/workspace/agents`、`/workspace/gallery`、`/workspace/twins/voice`、`/workspace/writer`、`/workspace/assets` 均不存在。
- `home keeps exact active matching and Chinese label`：header 的 `/workspace` 链接文本为“首页”，现有 `/workspace` active 与 Agent 新会话不 active 两个测试保持原义。
- `palette follows the same visible page set`：`PALETTE_NAV_ITEMS` 精确为视频资产与 console。
- `sidebar.spec.ts` 改为真实浏览器断言首页、视频资产、console 可见，六个隐藏入口不可见，最近对话区域和“设置和更多”仍存在；不得只测数组而漏实际侧栏。

- [x] **Step 2：运行并确认旧导航导致失败**

Run:
```bash
cd frontend
pnpm exec vitest run --project dom tests/unit/components/workspace/agents-nav-entry.dom.test.tsx tests/unit/components/workspace/workspace-header-active.dom.test.tsx
pnpm exec playwright test tests/e2e/sidebar.spec.ts
```

Expected: FAIL，旧数组仍渲染 chats/agents 等入口且文案为“工作台/视频”；header 精确高亮基线应继续 PASS。

- [x] **Step 3：最小实现**

删除 `nav-routes.ts` 中六个隐藏项及已不再使用的 lucide import，保留 `videoGen`、console 和既有类型/匹配函数；不要添加另一份硬编码侧栏。locale 只改 `sidebar.dashboard = 首页/Home`、`sidebar.videoGen = 视频资产/Video assets`，不删除其他翻译键。`workspace-header.tsx` 保持精确高亮与链接，只更新过时的“工作台恢复”注释。不得改最近对话、设置和路由文件。

- [x] **Step 4：运行定向回归并确认通过**

Run:
```bash
cd frontend
pnpm exec vitest run --project dom tests/unit/components/workspace/agents-nav-entry.dom.test.tsx tests/unit/components/workspace/workspace-header-active.dom.test.tsx tests/unit/components/workspace/console-nav-label.dom.test.tsx
pnpm exec playwright test tests/e2e/sidebar.spec.ts
node scripts/check_i18n_keys.mjs
```

Expected: 全部 PASS；管理员/普通用户 console 文案测试不退化，i18n key 对照无缺失。

- [x] **Step 5：提交**

```bash
git add frontend/src/core/workspace/nav-routes.ts frontend/src/components/workspace/workspace-header.tsx frontend/src/core/i18n/locales/zh-CN.ts frontend/src/core/i18n/locales/en-US.ts frontend/tests/unit/components/workspace/agents-nav-entry.dom.test.tsx frontend/tests/e2e/sidebar.spec.ts
git commit -m "feat(workspace): 收敛侧边栏为首页与视频资产"
```

---

### Task 4：抽出 TaskThumbnail，保留原导出与 recompose 静态守卫

**Files:**
- Create: `frontend/src/components/workspace/video-gen/task-thumbnail.tsx`
- Modify: `frontend/src/components/workspace/video-gen/video-gen-page.tsx`（只动缩略图定义、必要 import 和 re-export）
- Test: `frontend/tests/unit/components/workspace/video-gen/task-thumbnail.dom.test.tsx`
- Test: `frontend/tests/unit/components/workspace/video-gen/video-gen-page-source-guard.test.ts`
- Unchanged guards: `video-gen-page-thumbnail.dom.test.tsx`、`task-card-retry.test.ts`、`task-card-retry-confirm.dom.test.tsx`。

**Interfaces:**
- Produces:
  - `TaskThumbnailProps = { url: string | null; artifactMissing?: boolean; className?: string }`。
  - `TaskThumbnail(props: TaskThumbnailProps)` 保留原 `data-testid=thumbnail-img/thumbnail-placeholder/thumbnail-artifact-missing`、missing 不请求图片、onError 降级、URL 变化复位。
  - `video-gen-page.tsx` 内部从 `./task-thumbnail` import 供 `TaskCard` 使用，并 `export { TaskThumbnail } from "./task-thumbnail"`；旧导入路径不变。
  - 可选 `className` 通过 `cn()` 合并三种分支的默认尺寸，供 Task 5 传大封面；缺省 DOM 外观与旧测试一致。
- Must not produce: 新 recompose 文件、新 `useRecomposeTask` 调用点或任何 `TaskCard` props 改动。

- [x] **Step 1：先写失败测试**

`task-thumbnail.dom.test.tsx`：
- `supports large card class without changing fallback semantics`：直接从新路径渲染，传大尺寸 `className`，断言 img/missing/placeholder 三分支和 `onError → placeholder`、`url` 变化复位；这些断言可与旧测试共用 harness，但不能删除旧路径测试。
- `video-gen-page-source-guard.test.ts` 用 `readFileSync` 断言：
  - 页面包含 `export { TaskThumbnail } from "./task-thumbnail"`，不再包含 `export function TaskThumbnail(`。
  - 页面仍包含 `useRecomposeTask(task.id`、`recomposeActionFor(task)`、`recomposeMut.mutate()`、`RecomposeConfirmDialog`，且这些字符串仍位于 `export function TaskCard` 之后的源码区。
  - 新 `task-thumbnail.tsx` 不含 `recompose`，防止以抽组件为名搬走重合成。

- [x] **Step 2：运行并确认失败**

Run: `cd frontend && pnpm exec vitest run --project dom tests/unit/components/workspace/video-gen/task-thumbnail.dom.test.tsx && pnpm exec vitest run --project node tests/unit/components/workspace/video-gen/video-gen-page-source-guard.test.ts`

Expected: 新模块不存在或 re-export/静态断言 FAIL；不要用删除静态用例来通过。

- [x] **Step 3：最小实现**

把旧 `TaskThumbnail` 实现移到新文件，只增加 `className` 与 `cn()`；页面保留 `TaskCard` 的 `<TaskThumbnail url={task.thumbnail_url} artifactMissing={task.artifact_missing} />`，添加 import/re-export。逐字保留 `TaskCard` 内 recompose hook、retry action、确认框和注释，不调整其位置到其他文件。

- [x] **Step 4：运行新旧缩略图与 recompose 回归并确认通过**

Run:
```bash
cd frontend
pnpm exec vitest run --project dom tests/unit/components/workspace/video-gen/task-thumbnail.dom.test.tsx tests/unit/components/workspace/video-gen/video-gen-page-thumbnail.dom.test.tsx tests/unit/components/workspace/video-gen/task-card-retry-confirm.dom.test.tsx
pnpm exec vitest run --project node tests/unit/components/workspace/video-gen/task-card-retry.test.ts tests/unit/components/workspace/video-gen/video-gen-page-source-guard.test.ts tests/unit/core/video-gen/resume-actions.test.ts
```

Expected: 全部 PASS，尤其旧文件仍从 `video-gen-page` 成功导入 `TaskThumbnail`，重合成确认前零副作用。

- [x] **Step 5：提交**

```bash
git add frontend/src/components/workspace/video-gen/task-thumbnail.tsx frontend/src/components/workspace/video-gen/video-gen-page.tsx frontend/tests/unit/components/workspace/video-gen/task-thumbnail.dom.test.tsx frontend/tests/unit/components/workspace/video-gen/video-gen-page-source-guard.test.ts
git commit -m "refactor(video-gen): 抽出任务缩略图并保留重合成兼容面"
```

---

### Task 5：实现真实数据驱动的 CompletedVideoGrid

**Files:**
- Create: `frontend/src/components/workspace/video-gen/completed-video-grid.tsx`
- Test: `frontend/tests/unit/components/workspace/video-gen/completed-video-grid.test.ts`
- Test: `frontend/tests/unit/components/workspace/video-gen/completed-video-grid.dom.test.tsx`
- Consumes unchanged: `core/video-gen/types.ts`、`api.ts`、`hooks.ts`、`task-type-label.ts`、`task-status-label.ts`、`core/utils/datetime.ts`、Task 4 `TaskThumbnail`。

**Interfaces:**
- Produces:
  - `COMPLETED_WORK_PAGE_SIZE = 6`、`COMPLETED_WORK_LIMIT = 100`。
  - `selectCompletedWorks(tasks: readonly VideoTaskBrief[]): VideoTaskBrief[]`：纯函数、不改输入；过滤 ready + 两类 AI，按有效 `created_at` 降序、无效/空最后、同时间按 id 稳定决胜，返回前 100。
  - `canPreviewOrDownload(task: Pick<VideoTaskBrief, "status" | "artifact_missing">): boolean`：仅 `ready && artifact_missing !== true`。
  - `CompletedVideoGrid({ tasks, isLoading, isError, onRetry }: { tasks: VideoTaskBrief[]; isLoading?: boolean; isError?: boolean; onRetry?: () => void })`。
- Local state: `visibleCount` 初始 6，`previewTaskId: string | null`，播放失败按 task id 记录；不请求新分页 API。
- Later: Task 7 从 `useVideoTasks()` 传 `data ?? []`、`isLoading`、`isError`、`refetch`。

- [x] **Step 1：先写失败测试**

`completed-video-grid.test.ts`：
- `filters only ready ai_image and ai_video`：混种 `remotion_course/vimax/failed/draft/running`，结果只含指定 ready AI 两类。
- `sorts created_at descending without mutating input and caps at 100`：构造 105 个乱序任务，输出 100、首项最新、输入 ID 顺序不变；空/坏日期最后，稳定 tie-break 可重复。
- `gates preview and download on ready and non-missing`：ready+missing/缺省/false 与 failed 四组断言精确，缺省 missing 按非 missing 兼容旧后端。

`completed-video-grid.dom.test.tsx`：
- `renders six then loads six more without another request`：12 个合格任务首屏 6 张 `completed-work-card`，点击“加载更多”后 12，按钮消失；组件没有 fetch，mock 的列表 refetch 不被调用。网格同时断言 `grid-cols-1 sm:grid-cols-2 xl:grid-cols-3`，两张作品卡左对齐且与资产卡同宽。
- `shows only honest title type ready update and cover`：fixture 同时带 `total_duration/frame_count/shot_count/cost/video_count` 等干扰字段，DOM 有真实标题、`图片动效` 或 `AI视频`、ready 的“已完成”语义、`最近更新` 与 thumbnail src；无“时长/分辨率/完成时间/项目统计/花费/系统健康”，不把 created_at 标成完成时间。
- `missing artifact stays visible but cannot view or download`：卡片显示“产物已清理”，查看/下载 disabled 或不渲染；断言不能触发 preview，也没有 download href。
- `view expands only the selected ready work and download uses existing endpoint`：查看后出现一个 `video[src="/api/video/tasks/<id>/download"]`，另一卡片无播放器；下载 anchor 指向同一既有 URL 且有 `download` 属性。
- `playback error replaces player with honest fallback`：dispatch `error` 后出现“视频预览暂不可用”，页面不崩溃、下载仍可点；空标题/空日期显示“未命名作品/更新时间未知”，无 `Invalid Date`。
- loading、empty、error+retry 各有明确状态，不把 error 当空列表。

- [x] **Step 2：运行并确认模块缺失导致失败**

Run:
```bash
cd frontend
pnpm exec vitest run --project node tests/unit/components/workspace/video-gen/completed-video-grid.test.ts
pnpm exec vitest run --project dom tests/unit/components/workspace/video-gen/completed-video-grid.dom.test.tsx
```

Expected: FAIL，`completed-video-grid` 尚不存在。

- [x] **Step 3：最小实现**

在新组件中先写两个纯函数再写 UI：筛选完成后立即截最近 100，本地分页只切已筛选数组。作品网格与 `CourseProjectPage` 共用 `grid-cols-1 sm:grid-cols-2 xl:grid-cols-3`，保证我的作品与我的资产同卡宽，两个作品左对齐、第三列自然留空。卡片用 `TaskThumbnail`，类型映射固定 `ai_image → 图片动效`、`ai_video → AI视频`，ready 标签复用既有状态文案，更新时间用 `formatDateTime(updated_at ?? created_at)`。查看仅对 actionable 卡片展开该 id 的 controls 播放器；下载不猜扩展名。missing 卡片保留但操作不可用，播放失败以状态块替代播放器。加载更多按 `min(visibleCount + 6, 100)` 更新，不显示额外统计面板。

- [x] **Step 4：运行定向与缩略图回归并确认通过**

Run:
```bash
cd frontend
pnpm exec vitest run --project node tests/unit/components/workspace/video-gen/completed-video-grid.test.ts tests/unit/components/workspace/video-gen/video-gen-page-source-guard.test.ts
pnpm exec vitest run --project dom tests/unit/components/workspace/video-gen/completed-video-grid.dom.test.tsx tests/unit/components/workspace/video-gen/task-thumbnail.dom.test.tsx tests/unit/components/workspace/video-gen/video-gen-page-thumbnail.dom.test.tsx
```

Expected: 全部 PASS；没有修改 API/types/hooks，没有 recompose diff。

- [x] **Step 5：提交**

```bash
git add frontend/src/components/workspace/video-gen/completed-video-grid.tsx frontend/tests/unit/components/workspace/video-gen/completed-video-grid.test.ts frontend/tests/unit/components/workspace/video-gen/completed-video-grid.dom.test.tsx
git commit -m "feat(video-gen): 新增我的作品网格与本地加载更多"
```

---

### Task 6：我的资产复用课程项目 CRUD 与三级封面

**Files:**
- Modify: `frontend/src/components/workspace/video-gen/course-project-page.tsx`
- Test: `frontend/tests/unit/components/workspace/video-gen/course-project-cover.test.ts`
- Test: `frontend/tests/unit/components/workspace/video-gen/course-project-page.dom.test.tsx`
- Unchanged guards: `course-project-detail.test.ts`、`course-project-dirty-guard.dom.test.tsx`、`course-project-delete-confirm.dom.test.tsx`、`course-project-x11-ux.dom.test.tsx`。
- Consumes unchanged: `styleAnchorImageUrl`、`stylePreviewImage`、`resolveStyleValue`、`carryLabelFor`、`useCourseProjects`、`CourseProjectDialog`、`CourseProjectDetailPage`。

**Interfaces:**
- Produces:
  - `courseProjectCoverUrls(project: CourseProject): string[]`：有序候选；`style_anchor_image` 有值时首项为 `styleAnchorImageUrl(project.id)`，第二项为 `stylePreviewImage(resolveStyleValue(project.visual_style_preset))`（若 null 则省略），绝不把服务器本地 `style_anchor_image` 字符串直接当浏览器 URL。
  - `courseProjectFallbackColor(project: CourseProject): string`：`color_palette.primary` 为非空 string 时用它，否则固定中性色 `#eef2ff`。
  - `courseProjectStyleLabel(project)`：已知/存量 key 经 `resolveStyleValue + carryLabelFor`，空值显示“未设置风格”，未知值原样透出而不猜。
- Cover component：按 candidates 顺序渲染，当前图 `onError` 推进下一张，全部失败后显示色块；project id/candidates 改变时重置失败状态。
- Later: Task 7 E2E mock 必须支持锚图、项目 CRUD 和更新后的列表。

- [x] **Step 1：先写失败测试**

`course-project-cover.test.ts`：
- `prioritizes anchor then style preview then color`：有锚图+已知风格时数组精确为锚图 serve URL、风格预览 URL；无锚图时首项风格预览；未知风格无预览；主色缺省返回 `#eef2ff`。
- `never exposes local anchor path and labels legacy/unknown styles honestly`：src 不含 `/private/...` 本地路径，存量风格显示既有中文 label，未知 key 不映射成其他风格。

`course-project-page.dom.test.tsx`：
- `anchor error falls back to style image then color`：首图 dispatch error 后 src 变风格预览，再次 error 后出现主色块；无循环请求、无破图。
- `shows title style update and new/edit actions only`：项目 fixture 带 `video_count: 99`、虚构时长等干扰字段，页面有真实标题/风格/最近更新/“新建项目”，不出现 99、项目统计、时长、分辨率、完成时间或系统健康。
- `opens existing detail for edit and passes onSelect for create`：点卡片渲染 `CourseProjectDetailPage(projectId)`；新建对话框 `onSelect(project)` 后选择新项目 id，从而复用创建后关闭并进入详情的既有能力。
- loading、empty、error 状态诚实；detail 的 dirty guard/delete confirm 旧测试不删。

- [x] **Step 2：运行并确认失败**

Run:
```bash
cd frontend
pnpm exec vitest run --project node tests/unit/components/workspace/video-gen/course-project-cover.test.ts
pnpm exec vitest run --project dom tests/unit/components/workspace/video-gen/course-project-page.dom.test.tsx
```

Expected: FAIL，当前页面只有色条与“点击编辑详情”，没有封面候选、更新时间和新测试导出。

- [x] **Step 3：最小实现**

只重构 `CourseProjectPage` 列表层：保留 `selectedId` 和 `CourseProjectDetailPage` 原调用；头部改“我的资产”，使用与作品网格一致的 `grid-cols-1 sm:grid-cols-2 xl:grid-cols-3` 和三级封面，保证两个视图同卡宽。卡片显示真实标题、风格 label、`updated_at ?? created_at` 和“编辑项目”。`CourseProjectDialog` 增加 `onSelect={(project) => setSelectedId(project.id)}`，不修改其内部创建/删除逻辑。按 `updated_at ?? created_at` 在前端新到旧排序，空日期最后；不新增统计、关联视频封面或预览聚合。同步更新旧的“课程项目/色条”局部注释。

- [x] **Step 4：运行定向与项目 CRUD 回归并确认通过**

Run:
```bash
cd frontend
pnpm exec vitest run --project node tests/unit/components/workspace/video-gen/course-project-cover.test.ts tests/unit/components/workspace/video-gen/course-project-detail.test.ts
pnpm exec vitest run --project dom tests/unit/components/workspace/video-gen/course-project-page.dom.test.tsx tests/unit/components/workspace/video-gen/course-project-dirty-guard.dom.test.tsx tests/unit/components/workspace/video-gen/course-project-delete-confirm.dom.test.tsx tests/unit/components/workspace/video-gen/course-project-x11-ux.dom.test.tsx
```

Expected: 全部 PASS；编辑未保存内容不被 refetch 覆盖，删除仍需二次确认。

- [x] **Step 5：提交**

```bash
git add frontend/src/components/workspace/video-gen/course-project-page.tsx frontend/tests/unit/components/workspace/video-gen/course-project-cover.test.ts frontend/tests/unit/components/workspace/video-gen/course-project-page.dom.test.tsx
git commit -m "feat(video-gen): 将课程项目网格重构为我的资产"
```

---

### Task 7：组合双 tabs、扩充 mock、完成行为级 E2E 与全量验收

**Files:**
- Modify: `frontend/src/components/workspace/video-gen/video-gen-page.tsx`（仅 `TABS` 与 `VideoGenPage` 主组合及因此失效的 import；不动 TaskCard/recompose）
- Modify: `frontend/tests/e2e/utils/mock-video-api.ts`
- Modify: `frontend/tests/e2e/video-gen-create-flow.spec.ts`（创建入口改为首页 AI 卡，保留原有提交与分镜断言）
- Modify: `frontend/tests/e2e/video-gen-create-dialog.spec.ts`（同步隐藏旧“新建任务”后的入口）
- Modify: `frontend/tests/e2e/video-gen.spec.ts`（同步旧任务列表与新建按钮相关断言，不删除创建能力覆盖）
- Create: `frontend/tests/e2e/home-course-entry.spec.ts`
- Create: `frontend/tests/e2e/video-assets.spec.ts`
- Modify: `docs/plans/2026-11-09-home-entry-and-video-assets-redesign-plan.md`（勾选、回填原状态行）
- Create on execution date: `docs/changelog/changelog-YYYY-MM-DD-home-entry-and-video-assets-redesign.md`（若执行日为 2026-11-09，则使用该日期；含「验收记录」）
- Regression only: 其余既有 `video-gen*.spec.ts`、`sidebar.spec.ts`、全部 unit tests。

**Interfaces:**
- Consumes: Tasks 1–6 的全部公开接口，现有 `useVideoTasks()`、`getVideoDownloadUrl`、`CourseProjectPage`。
- Produces:
  - `VideoGenPage` 本地 `tab: "works" | "assets"`，默认 `works`；tabs 使用 `role=tablist/tab/tabpanel`、`aria-selected/aria-controls` 与左右方向键，切换时条件卸载另一 panel。
  - 页面 h1 为“视频资产”，原标题、说明、类别 Tabs 和“新建任务”按钮整体隐藏；`CreateTaskDialog` 保持可复用，但创建入口统一由首页 AI 卡承担。`works` 渲染 `CompletedVideoGrid`，`assets` 渲染 `CourseProjectPage`。
  - `MockVideoTask` 增加 `title?: string | null`、`created_at?: string`、`updated_at?: string`、`thumbnail_url?: string | null`、`artifact_missing?: boolean`；`brief()` 消费播种值，不再每次响应重写时间。
  - `MockCourseProject` 字段为 `id: string`、`title: string`、`visual_style_preset?: string | null`、`color_palette?: Record<string, string | number | undefined> | null`、`style_anchor_image?: string | null`、`created_at?: string`、`updated_at?: string`；`MockVideoAPI` 增加 `seedProject(project: MockCourseProject): MockCourseProject`、`project(id: string): MockCourseProject | undefined`、`projectRequests: RecordedRequest[]`。支持 `GET/POST /api/video/projects`、`GET/PUT/DELETE /api/video/projects/:id`。
  - mock 的 `GET .../thumbnail` 与 `GET .../style-anchor-image` 返回 `public/course-samples/knowledge-card.webp`，missing/无锚图时 404；`GET .../download` 返回 `public/course-samples/image-motion.mp4` 的真实字节，不再返回 `Buffer.from("mock mp4")`。

- [x] **Step 1：先写失败的行为测试与 mock 契约断言**

`home-course-entry.spec.ts` 至少包含以下 Playwright 测试（用 `mockLangGraphAPI` + `mockVideoAPI`，Agent 新页只断言 URL/既有页面壳，不提交付费任务）：
1. `six confirmed entries open the right agent or AI dialog`：默认视频课三卡，切图文课三卡；四个 Agent URL 精确，两个 AI 对话框初始类型精确。六入口都必须实际点击，不能只读配置。
2. `alternating AI entries starts clean after close`：图片动效中填标题/脚本并改一项高级设置，关闭；打开 AI视频，断言类型为 AI 生视频且标题/脚本为空、高级设置回默认；再反向打开图片动效同样无残留。
3. `only expanded panel plays and reduced motion stops playback`：init script spy `play/pause`，正常模式切 panel 验证旧停新播；`page.emulateMedia({ reducedMotion: "reduce" })` 后 `play` 不调用且当前视频 paused。
4. `sample playback failure keeps the creation action usable`：拦截一条 `/course-samples/*.mp4` 为失败或让 play reject，出现失败提示，点击该卡仍进入正确 Agent/AI 创建框。
5. `mobile home has no horizontal overflow and keeps both entries operable`：390×844，`scrollWidth <= innerWidth + 1`，双入口、六卡与 AI 对话框可操作；同时覆盖 1440px 基本布局。

`video-assets.spec.ts` 至少包含：
1. `defaults to works and filters ready AI types`：播种 ready 两类 + ready remotion/vimax + failed/draft/running，默认只显示合格真实标题；切“我的资产”再刷新，默认仍“我的作品”，tabs 不写 URL。
2. `loads six more locally and never exceeds latest 100`：E2E 至少验证 6→12 且 `/api/video/tasks` 请求次数不增加；100 上限由 Task 5 纯函数与 105 条 DOM 数据集证明，不要求浏览器点 16 次。
3. `ready non-missing work can preview real media and download`：查看后 video src 正确，`loadedmetadata` 的 duration 在 8–12 秒、width 960（证明 mock 不再是假 MP4）；点击下载记录一次既有 download 请求。
4. `artifact_missing is visible but preview and download are blocked`：卡片有“产物已清理”，无可用操作，download 请求为 0。
5. `works cards and project cards contain no fabricated fields`：断言规定字段存在，时长/分辨率/完成时间/项目统计/系统健康及干扰 fixture 值不存在；近期项目另有真实 title/type/update 断言。
6. `project cover falls through and existing create edit delete flow works`：锚图失败→风格图→色块在 unit 覆盖；E2E 走完新建项目→进入详情→改标题保存→回列表看到新标题→删除二次确认→列表移除，全部经现有 `/api/video/projects` mock。
7. `video preview error and mobile tabs degrade honestly`：播放器 error 后出现“视频预览暂不可用”；390×844 无水平溢出，两个 tabs、加载更多、新建/编辑按钮可用。
8. 更新既有创建入口契约：`/workspace/video-gen` 不再显示旧“新建任务”按钮；`video-gen-create-flow.spec.ts` 等相关用例改为从首页 AI 卡进入创建框，随后仍验证原有 payload、提交、分镜与错误状态，不通过保留旧按钮规避测试变更。

- [x] **Step 2：运行并确认集成前失败**

Run:
```bash
cd frontend
pnpm exec playwright test tests/e2e/home-course-entry.spec.ts tests/e2e/video-assets.spec.ts
pnpm exec vitest run --project node tests/unit/components/workspace/video-gen/video-gen-page-source-guard.test.ts
```

Expected: 新 E2E FAIL：旧 VideoGenPage 无双 tabs/作品网格，mock 无项目 CRUD/真实媒体；首页六入口测试若有个别选择器或 mock 问题，先修测试直到能区分真实功能失败。静态 recompose 守卫应 PASS。

- [x] **Step 3：最小实现**

- `VideoGenPage` 用本地双 tabs 组合两个既有/新组件，隐藏原标题、说明、类别 Tabs 和旧“新建任务”按钮，不再显示原页头；`CreateTaskDialog` 仍保留可复用，创建入口由首页承担。删除主函数不再使用的旧列表过滤、previewTaskId/deleteMut 代码，但不删除导出 `TaskCard` 及其 recompose。切换 tab 条件卸载，避免后台播放与隐藏面板查询；不用 URL query 或持久化。
- 扩充 `mock-video-api.ts` 上述字段与路由。项目路由放在 task 路由之前，POST 创建确定 id、PUT 更新同一 map、DELETE 二次请求后移除；React Query invalidate 后 GET 返回最新值。`brief()` 使用播种的 title/thumbnail/timestamps/missing，静态媒体用 `route.fulfill({ path, contentType })` 读取 `process.cwd()/public/course-samples/...`。
- 不修改现有 API/types/hooks，不加查询参数，不用 mock 统计数据迎合 UI；既有 `vg-created-e2e` processing 任务在新作品中自然被过滤，但创建后跳分镜逻辑不变。

- [x] **Step 4：运行定向行为与兼容回归并确认通过**

Run:
```bash
cd frontend
pnpm exec playwright test tests/e2e/home-course-entry.spec.ts tests/e2e/video-assets.spec.ts tests/e2e/sidebar.spec.ts tests/e2e/video-gen-create-dialog.spec.ts tests/e2e/video-gen-create-flow.spec.ts tests/e2e/video-gen-error-states.spec.ts tests/e2e/video-gen-storyboard-edit.spec.ts
pnpm exec vitest run --project node tests/unit/components/workspace/video-gen/video-gen-page-source-guard.test.ts tests/unit/components/workspace/video-gen/task-card-retry.test.ts
pnpm exec vitest run --project dom tests/unit/components/workspace/video-gen/task-card-retry-confirm.dom.test.tsx tests/unit/components/workspace/video-gen/video-gen-page-thumbnail.dom.test.tsx
```

Expected: 全部 PASS；六入口、双 tabs、AI 交替无残留、ready 过滤、6→12、查看/下载、missing 阻断、无虚构字段、移动端和播放失败均有真实浏览器结果，旧创建/分镜/recompose 兼容不退化。

- [x] **Step 5：运行全量门禁**

按根长命令规范依次执行（不得让多个 build 共用同一 `.next`）：
```bash
cd frontend
pnpm check
pnpm test
pnpm test:e2e
pnpm perf:check
pnpm check:dead
node scripts/check_i18n_keys.mjs
cd ..
make check
python3 scripts/check_docs_convention.py
```

Expected:
- `pnpm check` 的 ESLint、TypeScript、Prettier 全过；`pnpm test` node+dom 全绿；`pnpm test:e2e` Chromium 全绿；`perf:check` 五路由均在现有预算内；knip/i18n 全过。
- 根 `make check` 通过（其中 backend 只跑既有 ruff 静态门禁，**不运行也不需要 backend pytest**，因为本批没有 backend diff）；docs convention 输出 `OK 仓库治理约定全过...`。
- 若 perf 超预算，先优化本批 import/条件挂载；仍必须改预算时停止并带测量结果回用户评审，不得直接抬线。若出现基准已有的 unrelated 红，记录原始失败与 `git diff`，不得顺手修范围外问题。
- 检查 `git status --short` 和 `git diff --name-only 2f4848b840bd14b60511bb92d2b10f59b4f1a0db`（包含尚未提交的工作树改动）：无 backend、v3、数据库、docker、锁文件、Demo 改动，无测试生成的 report/log 被暂存；删除本次运行产生且交付不需要的未跟踪输出，保留 `.next/performance-results.json` 仅作本地证据，不提交。

- [x] **Step 6：写执行 changelog 并更新计划**

- 按实际执行日创建唯一 changelog，TL;DR 不超过 5 行，写清影响面、部署仅需 frontend、无迁移；「验收记录」逐行使用 `命令 → 实际输出关键行`，包括 ffprobe、定向测试、全量 `pnpm check/test/test:e2e/perf:check`、根 `make check`、docs convention 与行为级用例，禁止预写“全绿”或粘贴臆测数字。
- 勾选本计划已完成步骤，只修改顶部原状态行为 `已执行（任务 1–7...）` 并链接 changelog，不新增第二个状态头。若部署尚未执行，状态与 changelog 必须明确“代码已执行、部署待进行”，不能把未部署写成已上线。
- 再跑 `python3 scripts/check_docs_convention.py` 确认唯一状态头、TL;DR、命名和 changelog 验收记录形态通过。

- [x] **Step 7：提交**

```bash
git add frontend/src/components/workspace/video-gen/video-gen-page.tsx frontend/tests/e2e/utils/mock-video-api.ts frontend/tests/e2e/home-course-entry.spec.ts frontend/tests/e2e/video-assets.spec.ts docs/plans/2026-11-09-home-entry-and-video-assets-redesign-plan.md "docs/changelog/changelog-$(date +%F)-home-entry-and-video-assets-redesign.md"
git commit -m "feat(video-gen): 完成视频资产双视图与行为验收"
```

---

## 行为级验收矩阵（交付必须逐项有证据）

| ID | 用户可感知行为 | 实现级/行为级证据 |
| --- | --- | --- |
| B1 | 视频课/图文课双入口与六张卡均到正确目标 | `home-course-catalog.test.ts` + `home-course-entry.spec.ts::six confirmed entries...` 实际六次点击 |
| B2 | AI 类型交替打开不残留 | Task 1 初始值单测 + Task 2 卸载单测 + `alternating AI entries starts clean after close` 真实表单断言 |
| B3 | 视频资产双 tabs 默认我的作品，切换不持久化 | `video-assets.spec.ts::defaults to works...`，含刷新与 URL 断言 |
| B4 | 仅 ready 的 AI 两类出现，首屏 6、加载更多、最多最近 100 | Task 5 纯函数 105 条 + DOM 12 条 + E2E 6→12 且不新增列表请求 |
| B5 | ready 且非 missing 才查看/下载 | Task 5 gating 单测 + E2E 有效 MP4 预览、下载请求与 missing 零请求 |
| B6 | 页面只有真实标题/类型/ready/更新/封面，近期项目与我的资产字段不膨胀 | 两类 DOM “no fabricated fields” 测试 + E2E 干扰字段断言 |
| B7 | 我的资产封面按锚图→风格图→色块，CRUD 复用现有能力 | Task 6 候选/onError 测试 + 既有 dirty/delete 回归 + E2E 新建编辑保存删除 |
| B8 | 当前面板才播放，reduced motion/页面隐藏停止 | Task 2 media spies + Playwright `reducedMotion` 与 panel 切换 |
| B9 | 播放失败有反馈且创作/下载不被误阻断，移动端无横向溢出 | 两个 E2E spec 的 failure/mobile 用例，390×844 与桌面布局 |
| B10 | 旧能力未误伤 | 原 `matchAgent`、TaskThumbnail 原路径、TaskCard recompose、创建/分镜、dirty/delete 测试全绿；源码静态守卫留在 CI |

## 部署（合入后，仅 frontend）

1. 仅在全部检查通过、PR 合入后部署。记录待部署 commit 与上一个 frontend 镜像 tag；不得读取或打印 `.env`。构建上下文必须为仓库根，Mac 构建 Linux 镜像时显式 `--platform linux/amd64`：
   ```bash
   git rev-parse HEAD
   docker build --platform linux/amd64 \
     -t littlebear2016/deerflow-frontend:home-entry-<短commit> \
     -f frontend/Dockerfile .
   ```
2. 将镜像按现有部署方式送到目标环境，在既有 compose 部署目录只重建/替换 frontend，不改 compose、nginx、gateway、worker 或数据卷：
   ```bash
   FRONTEND_IMAGE=littlebear2016/deerflow-frontend:home-entry-<短commit> \
     docker compose up -d --no-deps frontend
   docker compose ps frontend
   ```
   这里的环境变量只用于本次命令，不写回任何环境文件；若目标环境已有等价的 frontend-only 发布脚本，可使用该标准脚本，但不得扩大为全栈 `up -d`。
3. 通过现有 nginx 入口做部署侧冒烟：`/workspace` 首页六入口与双入口可用，`/workspace/video-gen` 默认我的作品、tabs/查看下载可用，`/course-samples/remotion.mp4` 返回 200 且支持 Range；检查浏览器 console 无新增异常。不得为冒烟重建 nginx 或调用真实付费生成。
4. 无数据库迁移、无 backend 配置、无 worker 镜像、无 Remotion `TEMPLATE_VERSION` 变化。changelog 记录实际镜像 tag、commit、部署冒烟结果；未执行部署则明确写待部署。

## 回滚（纯 revert，无数据回滚）

1. 发现回归后，对合入提交执行新的 `git revert --no-edit <合入commit>`；如为多个任务提交且无法单次 revert merge，则按相反顺序 revert 本批 Task 7→1 的提交。禁止 `git reset --hard`、删除用户数据或写数据库补偿脚本。
2. 在 revert 后的 commit 上重新跑 `cd frontend && pnpm check && pnpm test`，再按上节**只构建和替换 frontend**；旧任务/项目数据保持原样，因为本批从未改变 schema 或写入语义。
3. 验证旧首页/旧视频页可用后，在 changelog 追加实际 revert commit、镜像 tag 和验证命令。媒体新增文件随 revert 从代码树移除即可，不触碰任何服务器任务产物。

## 完成前最终检查单

- [x] 七个任务均按“失败测试 → 确认失败 → 最小实现 → 确认通过 → 提交”顺序勾选，每次失败原因属于该任务。
- [x] `matchAgent/ROUTING_RULES` 与 `WORKFLOWS` 保留，recompose 未移动，旧深链未删；没有用隐藏旧 DOM 维持过期测试。
- [x] 只新增四个优化媒体到 `frontend/public/course-samples/`，ffprobe 与体积全部合格，Demo 目录零修改。
- [x] 六入口、双 tabs、AI 无残留、ready 过滤、6/100、missing 操作门控、下载/查看、字段诚实、移动端、reduced motion、播放失败均有行为证据。
- [x] `pnpm check`、`pnpm test`、`pnpm test:e2e`、`pnpm perf:check`、根 `make check`、docs convention 全部实际通过；无 backend pytest 要求，无 backend diff。
- [x] changelog 验收记录是真实命令输出，计划只有唯一状态头；部署与回滚均只涉及 frontend 镜像，回滚为纯 revert、无迁移。
