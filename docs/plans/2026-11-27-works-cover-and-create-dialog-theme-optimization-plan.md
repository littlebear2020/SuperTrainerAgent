# 「我的作品」封面与创建弹窗深色体验优化计划

> **状态**：未执行（2026-11-27 定稿；基准 commit `10290cda`；同日两轮评审优化。用户确认 Batch A、Batch B 都做：先 A 后 B，两个串行 PR，一次 `/goal` 端到端执行；ViMax 不在范围内；尚未修改业务代码）。
> **TL;DR**：两批串行、一次执行。Batch A（纯前端）：独立封面选择让 HTML/SVG+PNG 作品显示 PNG；统一类型封面替换「灰底 + 图标 + 文件名」并补加载失败回退；清理首页创作卡与 AI 专用弹窗的固定浅色类。Batch B（worker/离线工具）：抽帧 helper 失败时不留旧图并回退首帧，Remotion 复用它；新增默认 dry-run 的历史回填工具。关键决策：封面与主交付文件解耦；类型封面零请求；回填只写 `result.thumbnail_path`，抽帧失败不阻断 ready。影响面：`frontend/`、`docker/production-worker/pipelines/`、新回填脚本与测试；不部署，不调用付费 API。
> **For agentic workers：** REQUIRED SUB-SKILL：使用 `superpowers:executing-plans` 按 Task 0→8 串行执行并逐项勾选，先 RED 后 GREEN。执行指令下达后端到端完成，批次之间不停下等评审（评审在各自 PR 内完成），只在「全局约束」列出的四处停下。状态头只由 Task 8 改一次，不得另起状态头。勾选框之外的小节是说明，不构成待办。

**Goal**：六类「我的作品」有安全的真实图片（任务缩略图，或同作品交付的光栅图）时显示真实图片；没有时显示统一、精致、主题感知的类型封面；图片加载失败不破图。首页「图片动效」「AI视频」创作卡片及其专用创建弹窗在深色模式下不再出现白块和低对比文字。新完成的 remotion_course、ai_image/ai_visual、ai_video 任务在能抽帧时稳定得到真实缩略图，抽帧失败时不留旧图冒充新成片；上述类型的历史任务有离线补图工具。

**Architecture**：Batch A 只改前端展示层：新增安全图片封面选择纯函数与共享 `WorkTypeCover`；`primaryFile`、文件排序、查看器默认文件和下载行为不变；深色修复只在业务组件里使用语义 token 或完整的明暗成对类，不改 UI registry 和全局主题。Batch B 只改 worker 缩略图生成侧与离线工具：加固共享抽帧 helper，Remotion 改用它；历史任务用默认 dry-run 的离线脚本回填，复用 gateway 现有同步 `_extract_thumbnail`，不修改 gateway 生产代码。两批代码路径不重叠，前端无需为 Batch B 再改。

**Tech Stack**：Next.js App Router、React、TypeScript、Tailwind CSS 4、Lucide、Vitest、Playwright；Python 3.12、FastAPI、pytest、FFmpeg；不新增依赖。

**Spec / 需求来源**：用户 2026-11-27 提供的六张截图与两项反馈；既有交付契约 `docs/plans/2026-11-24-my-works-delivery-center-plan.md`；前端约定 `frontend/AGENTS.md`；视频管线约定 `docs/remotion-course-pipeline.md`、`backend/docs/VIDEO_GEN_NOTES.md`。

---

## 全局约束（执行前必读）

- **执行顺序**：Task 0 → Batch A（Task 1–4，PR A，分支 `works-cover-create-dialog-theme`）→ Batch B（Task 5–7，PR B，分支 `task-video-thumbnail-reliability`）→ Task 8 收尾。单执行者在一个执行 worktree 里串行完成；主 clone 恒停 main。
- **只在四处停下**：① Task 0 的基准漂移改动了 §六 里的文件，使本计划的定位失效；② 必须改动禁改路径（ban）才能完成；③ 某个 PR 的 OCR 3 轮后仍有 Critical/Important；④ 基线测试在本批要改的文件或用例上就已经红。与本批无关的既有失败只记录、按 main 对照，不停。其余情况一律继续。
- **PR 门禁（两个 PR 同一套）**：
  - 本机未装 git hooks：提交前手动 `pre-commit run --files <本次改动>`，并先于后台测试运行（hook 可能改文件）。
  - 本批门禁见 Task 4（A）与 Task 7（B）；>120s 的命令后台运行，等完成通知，不轮询。
  - 推送：`git push -u origin <分支>`；HTTPS 超时改用 SSH 远端，SSH 被代理断开再改回 HTTPS。
  - 建 PR：`GH_REPO=littlebear2020/SuperTrainerAgent gh pr create`，正文链接本计划与本批 changelog。
  - 等待：后台 `gh pr checks --watch` 等 CI 与 AI 评审的完成通知，再读 `gh pr view --comments` 与 reviews。
  - 修复：有发现就修复后重推；每个 PR 的 OCR 最多 3 轮，每轮处置写成一条 PR 评论。
  - 合并：无 Critical/Important 且 CI 绿 → `gh pr merge --rebase`。
- **合并被拒的处理**：`gh pr merge` 若被权限分类器拒绝，不重试、不绕路（不改用 API 或 auto-merge）。写好 OCR 处置评论、确认 `mergeStateStatus` 为 `CLEAN` 后继续下一步。B 照常从最新 `origin/main` 切分支；A 未合入也可以，两批代码路径不重叠。最终汇报写明「先合 A 再合 B，合并后补做 Task 8 Step 1 与 Step 3」。
- **计划文件编辑归属**（保证 A 未合入时，B 也能在 A 之后无冲突地合入）：
  - Task 0 把本文件单独提交入库 main。
  - A 分支只勾 Task 0 至 Task 4 Step 7。
  - B 分支只勾 Task 5 至 Task 7 Step 4，并统一登记两批的 findings backlog 行（A 期间的范围外发现先写进 A 的 changelog）。
  - 两个 PR 都不改状态头。两个「PR 与合入」步骤的勾选、Task 8 的勾选和状态头，都由 Task 8 的一笔 docs 提交完成；这样也不会为了勾选而推 PR 分支、触发 OCR 重跑。
- **运行外事项（需用户另行授权，本次执行不做）**：测试服运行时取证（§2.6）、回填工具的真实环境 dry-run/apply、A/B 部署（§九）。本计划不调用付费 API，不读写开发库、真实用户数据或测试服。

---

## 一、排查范围、证据边界与成功标准

### 1.1 本次已做与未做

已做：逐张对照截图；追踪 Agent artifact 摘要、统一作品模型、卡片与查看器；追踪任务型视频缩略图从 worker/gateway 到前端的链路；检查创建弹窗与首页创作卡片样式；核对相关 Git 引入/修复提交及现有测试。2026-11-27 评审时逐条复核了本文的代码引用，行号已校正。

未做：未读取开发或真实用户数据库，未读取用户 outputs，未调用付费 API，未启动真实生成任务，未对截图中的线上任务做运行时取证。

「文字动效无封面」的主因，代码已能说明：首页「文字动效」入口是 Agent（`frontend/src/components/workspace/home-course-catalog.tsx:40-48`，`kind: "agent"`），而 course-video 的 SOUL 规定只交付 `output.mp4` 一个文件（`backend/seed/agents/course-video/SOUL.md:118`）。所以从首页新建的文字动效作品没有任何图片可选，也没有缩略图字段，必然落入占位；Batch A 的类型封面就是对它的修复。截图里如果还有任务型（`remotion_course`/旧 `remotion`）卡片，生成侧缺口由 Batch B 修；某个具体历史任务为什么没有缩略图，要靠运行时取证区分（§2.6，运行外）。

### 1.2 用户可感知成功标准

Batch A：

1. 同一 Agent 作品已交付 PNG/JPG/JPEG/WebP/GIF 时（典型是课程海报），卡片显示该图片，即使 HTML/SVG 在交付文件列表中排在前面。
2. HTML/SVG 仍只能切换和下载，不通过 `iframe/object/embed/srcDoc` 嵌入，也不改判为 `image`。
3. 文字动效 Agent、PPT、语音播客等没有真实图片的作品，不再显示「灰底 + 单图标 + 长文件名」，而是统一视觉体系的类型封面。
4. 任务型视频有有效 `thumbnail_url` 时照常显示真实缩略图；没有缩略图或图片加载失败时显示类型封面，不出现破图。
5. 主交付文件、卡片文件名/大小、默认查看文件、默认下载文件、文件顺序、搜索、状态操作和继续对话行为都不变。
6. 图片动效与 AI 视频创作卡片及专用弹窗在浅色/深色下都没有突兀白块；标题、说明、输入文字和 placeholder 的文字对比度 ≥4.5:1；本批触碰的可聚焦元素使用显式 `focus-visible:ring-indigo-500`（两套主题的 `--ring` 都是 transparent）。
7. 既有响应式与网络用例保持绿：作品卡仍是 16:9、同行等高、底部操作对齐，1440/1280/390/320 无元素级横向溢出。弹窗的打开、关闭和焦点行为不变（本批对弹窗只改 className）。
8. 列表仍不挂载 Agent MP4/MP3，不自动播放，不预载整段视频/音频或 HTML/SVG 内容；类型封面本身不发出任何网络请求。

Batch B：

9. 新完成的 remotion_course、ai_image/ai_visual、ai_video 任务在能抽帧时得到真实缩略图；抽帧失败时任务照常 ready，且不留旧图冒充新成片。
10. 上述类型的历史 ready 任务有离线回填工具（默认 dry-run，只补 `result.thumbnail_path`）；真实环境运行须用户另行授权，不在本次执行内。

---

## 二、问题一逐项定位：作品封面与预览

### 2.1 当前六类作品的实际封面分支

| 分类 / 来源 | 当前真实封面来源 | 无来源时的表现 | 直接原因 |
|---|---|---|---|
| 文字动效 Agent（`course-video`） | 只有 `primaryFile` 本身是光栅图时才显示图片 | Video 图标 + 文件名 | SOUL 只交付一个 MP4；没有缩略图字段，卡片刻意不挂视频 URL |
| 文字动效任务（`remotion_course/remotion`） | ready 且未缺失时的 `task.thumbnail_url` | Video 图标 + 文件名 | 没有 URL 时不从 MP4 取帧；旧 `remotion` 没有缩略图管线 |
| 图片动效 / AI 视频任务 | 同上 | 类型图标 + 文件名 | 缩略图未生成或文件不可见时无真实封面（§2.5） |
| 课程海报 Agent | 只检查 `primaryFile` | HTML/SVG 常为 File 图标 + 文件名 | 不继续寻找同作品后续 PNG |
| PPT Agent | 只检查 `primaryFile` 是否图片 | FileText 图标 + 文件名 | 不支持 PPT 首页预览，首版类型封面过于简化 |
| 播客 Agent | 只检查 `primaryFile` 是否图片 | AudioLines 图标 + 文件名 | 不支持音频封面元数据，首版类型封面过于简化 |

前端唯一入口在 `frontend/src/components/workspace/video-gen/work-media.tsx:45-53`：任务缩略图与主图片二选一；`:67-94` 把其他情况都压成单图标加文件名。任务卡片即使有 MP4 `previewUrl`，也不会用 `<video>` 生成首帧。这是必须保留的流量保护（artifact GET 整文件读取、不支持 Range，已登记 findings backlog），不是漏写了一个 `src`。

### 2.2 课程海报已有 PNG 却显示 HTML/SVG：已确认的前端选择缺陷

1. 后端摘要把 PNG/JPG/JPEG/WebP/GIF 归为 `image`，SVG/HTML/XHTML 因主动内容安全策略归为 `other`，见 `backend/app/gateway/works/service.py:14-24`。
2. 文件按「存在优先 → 修改时间倒序 → 完整路径字典序」稳定排序，见 `backend/app/gateway/works/service.py:84-90`。
3. 前端保持该顺序，取首个存在文件为 `primaryFile`，且只给 video/image/audio 生成 `previewUrl`，见 `frontend/src/core/works/model.ts:19-36`、`:57-70`。
4. `WorkMedia` 只检查这个主文件是不是图片（`work-media.tsx:45-53`），不扫描后面的 PNG。
5. 查看器默认文件、卡片文件摘要和下载按钮都消费同一个 `primaryFile`，见 `frontend/src/components/workspace/video-gen/work-viewer-dialog.tsx:147-153`、`frontend/src/components/workspace/video-gen/work-card.tsx:60`、`:104`、`:294`。

因此，只要 HTML/SVG 因更新时间或路径排序排在 PNG 前面，截图中的问题就必然出现。没有真实摘要响应，无法断言具体是 mtime 还是字典序让它排首；但「排首后无视后续 PNG」已由代码完整证实。

HTML/SVG 不内联是既有安全契约。不得通过把 SVG 改判为 `image`、给 HTML 加 iframe 或抓取主动内容来修复。

### 2.3 文字动效与任务视频无封面：A 修展示，B 修生成

- **Agent 文字动效**：见 §1.1。它只交付 MP4，没有缩略图字段；首版计划明确不为 Agent 视频生成首帧（`docs/plans/2026-11-24-my-works-delivery-center-plan.md:32`、`:55`）。Batch A 用类型封面解决观感，不在 artifact GET 上新增首帧服务。
- **任务型视频（展示侧）**：worker 抽帧成功才写 `result.thumbnail_path`（`docker/production-worker/worker.py:563-583`）；gateway 只在该字段非空且文件存在时下发 `thumbnail_url`，否则强制为 `null`（`backend/app/gateway/routers/video_gen.py:243-250`）。所以「ready 但无缩略图」是允许的终态，前端必须优雅降级。当前卡片的 `<img>` 没有 `onError`（`work-media.tsx:76-86`），URL 存在但请求失败时会破图，由 Batch A 修。
- **任务型视频（生成侧）**：Remotion 吞异常、各管线忽略返回值、重新抽帧失败时旧图残留、历史任务无图，由 Batch B 修，依据见 §2.5。生成侧修好后，前端无需再改就会显示真实缩略图。

### 2.4 PPT、语音播客及查看器的 demo 感：设计简化而非数据缺失

- 首版计划规定 Agent 视频/音频/PPT 用「带真实文件名的类型封面」（同上 `:55`）。本批改为文件名只由卡片下方的 `WorkFileSummary` 显示，封面换成能力图标加格式徽标，有意替换首版的这一条。
- 现在的类型封面只有 `bg-muted`、`h-9 w-9` 图标和一行 `text-xs` 文件名（`work-media.tsx:67-94`），没有品牌层、格式徽标、状态区分或装饰层次。
- 查看器的 document/other 都复用同一个 `FileText` 块（`work-viewer-dialog.tsx:23-44`）；音频只有原生 controls（`:109-119`）；多文件列表全部使用 `FileText`（`:191-213`，图标在 `:204`）。
- 已有 `TaskThumbnail` 的「记录失败 URL、URL 改变后重试」模式可复用，见 `frontend/src/components/workspace/video-gen/task-thumbnail.tsx:19-20`、`:34-45`。

结论：不需要先建设 PPT 渲染或音频转码。统一类型封面、格式徽标、错误回退和查看器一致性，就能解决展示侧的主要问题。

### 2.5 Batch B 依据：任务型视频缩略图生成侧（2026-11-27 复核）

1. **列表与缩略图 GET 只认 `result.thumbnail_path`。** 列表经 `_task_with_thumbnail` 构建响应，只有该字段非空且文件存在时才下发 URL，否则强制为 `null`（`backend/app/gateway/routers/video_gen.py:243-250`，列表调用在 `:435`）。缩略图 GET 直接读该字段（`:710-723`）。DB 的 `thumbnail_url` 列会被覆盖，`backend/tests/test_thumbnail.py:132-142` 已钉住「DB 脏列不下发」。所以**写回只需 `result.thumbnail_path`，不必双写。**
2. Worker 完成写回时，按 `task_dir/thumbnail.jpg` 是否存在决定是否写 `thumbnail_path`（`docker/production-worker/worker.py:563-583`）；`prior` 是读-合并-写取到的旧 result（`:548-555`）。
3. 共享 helper `_ffmpeg_extract_thumbnail`（`docker/production-worker/pipelines/_ffmpeg_helpers.py:978-993`）会检查返回码与文件是否存在，但它用 `-y` 直接写目标路径。抽帧失败时旧图原样留下，worker 的存在性判断会把旧图当成新图（重合成场景）；超时被杀还可能留下半个文件。三条 AI 管线调用它但忽略返回值（`video_gen_segmented.py:390-391`、`video_gen_mixed.py:313-314`、`video_gen_ai_visual.py:790-791`）。
4. Remotion 用自己的 `_extract_thumbnail`（`docker/production-worker/pipelines/compose_remotion.py:325-339`，调用在 `:445-447`）：吞掉异常、不看返回码，同样原地覆盖。
5. 两个抽帧实现都 seek 到 `max(1.0, duration × 比例)`。时长 <1s 的视频会 seek 越界、拿不到帧。正常课程视频不会触发，只作为 t=0 回退的边界用例。
6. 旧 `remotion` 的 gateway 渲染路径（`backend/app/gateway/video_gen/service.py:5735-5812`）不抽帧；但 `/api/video/create` 已拒绝 `video_type=remotion`（见 `:2670-2677` 注释），只影响历史任务，交给回填工具。回填可复用 gateway 现有同步 `_extract_thumbnail`（`:240-262`），本批不修改该函数或任何 gateway 生产调用。
7. Worker 独立套件 `docker/production-worker/tests/` 不在 CI 里，CI 只跑 backend 的 `make test`（`.github/workflows/backend-unit-tests.yml:97-99`）。不过 `backend/tests/test_thumbnail.py:71-83` 会按路径加载 worker helper 并用真实 ffmpeg 测试，所以 helper 的回归用例放在这里就会进 CI。
8. 两个 worker 镜像都 COPY 整个 `pipelines/`（`docker/production-worker/Dockerfile:22`、`docker/production-worker/Dockerfile.remotion:44`），改 helper 后两者都要重建。

### 2.6 历史任务取证矩阵（运行外，需用户授权）

截图中具体某个历史任务为什么没有缩略图，代码无法断言。交付后若用户提供测试环境授权，只记录任务的 `id/task_type/status`、`result.thumbnail_path` 是否存在、文件是否存在与 HTTP 状态；不提交标题、用户内容、宿主路径或日志原文。

| 脱敏现场证据 | 定性 |
|---|---|
| `result` 无 `thumbnail_path`，`thumbnail.jpg` 不存在 | 历史版本未生成、当次抽帧失败，或旧 remotion 无图；结合 task_type 与部署版本区分 |
| `result` 有路径，但 gateway 看不到该文件 | 文件被清理，或挂载路径不一致 |
| 文件存在但 `result` 无路径 | 生成后未写回；核对外部写入或旧版本的完成接缝 |
| 列表有 URL 且 GET 200 | 应正常显示；再查浏览器解码或前端部署版本 |
| 列表 URL 请求失败/解码失败 | Batch A 已回落类型封面；查文件是否损坏 |

取证结果用来决定回填工具在真实环境跑不跑、跑哪些 task_type，不影响本次执行。

---

## 三、问题二逐项定位：深色创作入口与弹窗

### 3.1 用户所指 UI 的真实组件

首页「图片动效」「AI视频」是 `HomeCourseEntry` 中的创作卡片按钮，不是 Radix Tabs。两张卡在 `frontend/src/components/workspace/home-course-catalog.tsx:50-71` 里定义 `initialTaskType="ai_image" / "ai_video"`，由 `frontend/src/components/workspace/workspace-home.tsx:105`、`:143` 传给 `CreateTaskDialog`。后者以 `initialTaskType !== undefined` 判定 `focused`，两种类型复用同一个专用分支（`frontend/src/components/workspace/video-gen/video-gen-create-dialog.tsx:1624-1634`）。

专用弹窗内没有类型选项卡，类型选择器在 `focused` 时隐藏（`:1761-1782`）。因此截图中的白块与浅字应修专用弹窗和首页卡片，不改「我的作品」顶部的六能力 tabs。

### 3.2 白块与低对比文字的精确来源

| 症状 | 代码证据 | 根因 |
|---|---|---|
| 头部白色框 | `video-gen-create-dialog.tsx:1734-1752`，尤其 `:1737` | 专用分支写死 `bg-white/75` 与 `border-indigo-100/70`，无 dark 对应类 |
| 标题和描述浅字 | 同上；`frontend/src/styles/globals.css:290-307` | 深色主题的浅色前景落在半透明白底上；按 token 合成估算，标题约 1.6:1，描述约 1.5:1 |
| 标题输入白框 | `video-gen-create-dialog.tsx:1789-1799` | 原生 input 的 `bg-background` 被 `cn()`（tailwind-merge）用 focused 的 `bg-white/80` 覆盖，两种主题下都是白底 |
| 标题输入文字/placeholder 看不清 | 同上 | 未设 `text-foreground` 和 placeholder 色。深色下浅字落在白底上；Tailwind 4 默认 placeholder 是 50% currentColor，浅色主题下也只有约 3:1 |
| 图标浅色方块 | `:1741-1744` | 固定 `bg-indigo-100`，与深色表面不协调 |
| 外层浅色描边 | `:1727-1733` | focused 的 `border-indigo-200/70` 没有 dark 变体 |
| 脚本区 | `:1482-1491` | 深色下，registry 的 `dark:bg-input/30` 因 `@custom-variant dark (&:is(.dark *))`（`globals.css:77`）特异性更高而胜出，脚本区**不是**白块。实际问题有三：浅靛描边无 dark 变体；registry 的 placeholder 用 `text-muted-foreground/60`，两种主题下都低于 4.5:1；浅色下多了一个固定 `bg-white/80` |
| 首页卡片浅色徽章/标签/描边 | `frontend/src/components/workspace/home-course-entry.tsx:102-108`、`:161`、`:184-203`、`:213-219`、`:260-268` | `bg-white/90`、`bg-violet-100`、`bg-violet-50`、`bg-teal-50`、`border-slate-200/80`、`border-slate-100` 等固定浅色类没有 dark 配对 |
| 首页卡片焦点不可见 | `home-course-entry.tsx:162-168` | 卡片按钮没有任何显式 focus-visible 样式 |

上述对比度是按 token 做的静态估算，执行验收时必须用计算样式重新测量。

### 3.3 Git 历史与测试缺口

- `dc1eb3d2 feat(workspace): 优化首页 AI 专用创建框` 引入了专用白色渐变、白色头部及白色输入覆盖，是问题引入提交。
- `cee995a7 fix(workspace): 修复 AI 创建框透明背景` 只把最外层改为 `bg-background`，没有处理头部和输入框，属于部分修复。
- 当前单测只检查外层 `bg-background`、无渐变，见 `frontend/tests/unit/components/workspace/video-gen/video-gen-create-dialog-initial-type.dom.test.tsx:181-211`；E2E 也只检查外层不透明，见 `frontend/tests/e2e/home-course-entry.spec.ts:420-440`。
- `frontend/tests/unit/components/workspace/home-course-entry.dom.test.tsx:122-128` 反而把 `bg-white/90`、`bg-violet-50` 等固定浅色类钉成了契约，需要改为语义 token 和主题行为断言。

根因在业务组件，不需要也不允许通过手改 `frontend/src/components/ui/**` 或 `frontend/src/components/ai-elements/**` 解决。

---

## 四、方案比较与评审决策记录

本计划是既有页面缺陷、视觉一致性与缩略图可靠性修复，不新增产品面，属于 grilling 强制前置的豁免范围。若后续另起「通用 Agent 视频首帧服务 / PPT 首页渲染服务」，作为新能力单独完成 grilling 和计划。定稿前已检查 `docs/findings-backlog.md`：artifact GET 整文件读取/Range、全局透明 `--ring`、既有 video-gen `networkidle` 测试问题都不顺手改；本计划只保留局部显式焦点环与既有网络门禁。

| Frontier | 可选方案与成本 | 决策与理由 |
|---|---|---|
| Q1 PNG 如何优先 | A. 改 `primaryFile`：成本低，但会改变默认查看/下载；B. 独立封面选择：低—中；C. 后端重排文件：中，且破坏契约 | **选 B**。封面与主交付文件解耦，避免辅助 PNG 抢走 PPT/MP3/HTML 的主文件语义 |
| Q2 无预览时默认封面 | A. CSS/token/图标组件：低—中；B. 静态营销样例图：低，但容易冒充用户内容；C. 全类型真实缩略图服务：高 | **选 A**。统一、主题感知、无额外图片请求，不伪装成作品实际画面 |
| Q3 安全图片选择范围 | 仅课程海报：规则较窄；所有 Agent 能力：一致，但可能选到辅助图片 | **所有 Agent 能力共用**，但只影响卡面。按现有文件顺序选首个安全图片，不宣称它是 PPT 首页或视频首帧 |
| Q4 文字动效真实视频封面 | 列表 `<video preload>`：看似便宜，实际会读大文件；修任务 thumbnail 生成链：中；Agent 首帧服务：高 | **任务型视频由 Batch B 修生成链，Agent 视频用类型封面**；不引入列表视频预载 |
| Q5 PPT/HTML/SVG 在线预览 | iframe/第三方渲染：高，且有安全与部署成本；类型封面 + 下载：低 | **保持不内联**，把「不可预览」做成完整设计，而非工程占位 |
| Q6 深色修复位置 | 改 registry/全局 theme：影响面大；业务组件语义 token：低—中 | **只改业务组件**。保留外层 `bg-background`，头部/输入/脚本/卡片全部去掉固定浅色类 |
| Q7 首页共享卡片范围 | 只给两个 AI 卡特判：会产生双轨；共享组件统一 token 化：影响六卡 | **共享组件统一处理**，六卡都做明暗截图，避免同类问题留在另外四卡 |
| Q8 类型封面与既有「零 `src`」断言 | A. 放宽为「无远程媒体 URL」；B. 类型封面只用 CSS 与 lucide 内联 SVG，原断言不动 | **选 B**。类型封面不需要任何图片资源；放宽断言只会削弱「列表零请求」保障 |
| Q9 图片加载失败的观感 | A. 单独的 error 态与文案；B. 回落到与「无缩略图」相同的类型封面 | **选 B**。用户关心的是不破图；文件仍可查看和下载，不需要额外状态和 i18n key |
| Q10 Batch A 验收范围 | A. 新建 2 入口 × 2 主题 × 3 宽度矩阵、键盘全套与变异回改；B. 只为本批新行为加断言，几何/键盘/网络沿用既有用例 | **选 B**。本批对弹窗只改 className；颜色不随视口变化；Radix 焦点行为未改；TDD 的 RED 已证明新断言能失败 |
| Q11 缩略图失败是否让任务失败 | 阻断 ready：损害合法 MP4 交付；记录并回落：更可靠 | **不阻断 ready**；失败写 warning 日志（含任务目录），旧图不得冒充新成片 |
| Q12 写回哪些字段 | 双写 `result` 与 DB 列；只写 `result.thumbnail_path` | **只写 `result.thumbnail_path`**；DB 的 `thumbnail_url` 不是显示真源（§2.5-1） |
| Q13 worker.py 是否要改 | 改完成写回逻辑；不改 | **不改**：helper 保证失败时不留旧图后，现有的存在性判断就是正确的；旧 result 里残留的 `thumbnail_path` 指向已删除的文件，列表与 GET 都会按存在性返回 null/404 |
| Q14 Remotion 与历史回填的抽帧实现 | Remotion 改调 worker 共享 helper；回填另写实现；回填复用 gateway 现有函数 | Remotion 改调共享 helper，并传 `seek_ratio=0.25` 保持现在的取帧位置（helper 默认 10%，直接委托会把课程视频封面换成另一帧）；回填复用 gateway 现有同步 `_extract_thumbnail`（目前只有测试调用），不修改 gateway 生产代码 |
| Q15 历史任务回填 | 列表/GET 时现场抽帧：并发与 I/O 放大；离线 dry-run/apply：中 | **离线脚本，默认 dry-run**；本次执行只实现并用隔离环境验证，真实环境运行另行授权 |
| Q16 缩略图 URL 缓存 | URL 带 `?v=<mtime>`；不处理 | **本计划不处理**：URL 恒为 `/api/video/tasks/{id}/thumbnail`，重合成后浏览器可能按启发式缓存短时显示旧帧；在 B 的 PR 里登记 backlog 候选 |

### 4.1 本次评审优化记录（2026-11-27）

- 原 Batch B 曾按「未执行计划物理隔离」约定拆至 pending；用户确认 A、B 都做且一次执行，已并回。B 的设计按复核结果修正：只写 `result.thumbnail_path`（Q12）；worker.py 不改（Q13）；helper 的回归测试放进 CI 会跑的 `backend/tests/test_thumbnail.py`；回填工具本次实现，真实运行另行授权（Q15）。
- 新增「全局约束」：四个停点、合并被拒时继续、计划文件编辑归属，保证一次 `/goal` 能端到端跑完。
- 撤回「放宽零 `src`/零 `<img>` 断言」（Q8），删除 error 封面态（Q9），删除 2×2×3 视觉矩阵、键盘全套和变异回改步骤（Q10）。
- 校正：脚本区在深色下不是白块（§3.2）；多文件列表改用 `MEDIA_ICONS`，不用 `getFileIcon()`（后者按扩展名把 SVG 显示成图片图标，见 `frontend/src/core/utils/files.tsx:245-247`）；`work-card.tsx` 行号改为 `:104`、`:294`，catalog 行号改为 `:50-71`，并补上 `workspace-home.tsx` 的传参点。
- 补漏：`work-viewer-dialog.dom.test.tsx:312` 断言封面里有文件名 `lesson.mp4`，文件名移出封面后要同步改断言；`WorkMedia` 按钮现用的 `focus-visible:ring-ring` 不可见，本批既然重写该组件，就改为显式 indigo 环；浅色 token 是 `oklch()`，E2E 对比度 helper 须先把计算色解析成 sRGB（Task 4 Step 4）。
- 二轮（Kimi 修订后复核）：用户确认 ViMax 不是项目需要的能力，B 删去 ViMax 完成/重做与 gateway 改动，回填限定五种任务类型，任务重编为 Task 0–8。复核补正：
  - Remotion 的委托测试改放 worker 套件：`backend/tests/test_worker_incremental_manifest.py:11-13` 在导入时把 `sys.modules["pipelines"]` 换成空包桩，backend 进程里导入 `pipelines.compose_remotion` 会随收集顺序失败。
  - Remotion 保持 25% 取帧（Q14）。
  - 回填按 `backend/scripts/reconcile_video_tasks.py` 的先例写（sqlite3、`--db-path`、dry-run 只读连接、在 gateway 容器内运行，容器里有 ffmpeg）。
  - Task 7 的行为验收写明经回填工具与加固后的 helper 两条路径，并补上提交步骤。
  - Task 8 改为先清理再提交状态头。

---

## 五、目标设计契约

### 5.1 封面选择与文件语义（A）

新增纯函数 `selectWorkCoverFile(work): WorkFile | null`，放在 `frontend/src/core/works/model.ts`。不改 `WorkSummary` DTO，不新增后端字段：

```text
任务 ready、未 artifact_missing、thumbnail_url 非空 → 任务缩略图（object-cover）
否则 selectWorkCoverFile(work) 非空               → 该图片（object-contain）
否则                                              → WorkTypeCover
selectWorkCoverFile = work.files 中按现有顺序第一个满足
                      exists && mediaKind === "image" && previewUrl 非空 的文件
```

`primaryFile` 就是首个存在的文件，所以它是图片时自然会被选中，不需要单独的分支。

约束：

- SVG/HTML 即使测试夹具错误地带了 `previewUrl`，也必须因 `mediaKind !== "image"` 被排除；不得只凭 URL 是否存在判断封面资格。
- 跳过缺失的图片；有多张图片时保持后端现有排序，选第一张，不在前端按文件名猜测。
- `primaryFile`、`files` 顺序、卡片文件摘要、直接下载和查看器初始选中项完全不变。卡片可以出现「PNG 封面 + HTML 主文件摘要」，这是有意的封面/主文件分离；点击封面仍打开主文件。
- 图片 `alt` 使用封面文件的实际文件名，或「作品标题 + 交付图片预览」，不得把辅助图片称为视频首帧或 PPT 首页。
- 任务缩略图继续 `object-cover`；Agent 海报等原图继续 `object-contain`，避免裁掉竖版文字。
- 图片加 `loading="lazy"`、`decoding="async"`；每张卡最多一张真实图片。原图懒加载不等于服务端缩略图，本批不宣称解决了大图流量。

### 5.2 统一 `WorkTypeCover` 视觉（A）

新建 `frontend/src/components/workspace/video-gen/work-type-cover.tsx`，卡片与查看器共用。输入：`capability`、`file`（用于格式徽标，可为空）、`state: "default" | "empty" | "missing" | "unknown"`。根元素带 `data-testid="work-type-cover"` 与 `data-cover-state`（仓内没有 `work-` 前缀的计数选择器，不会撞上既有断言）。

视觉契约：

- 保持 16:9 和现有外层按钮/accessible name。`WorkMedia` 根元素仍带 `aspect-video`，`work-grid.dom.test.tsx:343` 依赖它。封面内部不再以长文件名为主视觉。
- 只用 CSS（渐变、细边框、低透明光晕或网格线）和 lucide 内联 SVG，**不得出现 `<img>` 或任何 `src`**；装饰保持静态，加 `aria-hidden="true"`。
- 中央是约 48px 的圆角图标 medallion，下方是能力名称和格式徽标（MP4、MP3、PPTX、HTML、SVG 等；没有文件名时按 mediaKind 显示通用标签）。正文使用明确的前景色，不在复杂背景上叠低透明的 muted 文本。
- 图标/颜色复用 `frontend/src/core/agents/appearance.ts:35-79`：文字动效 Clapperboard/indigo，课程海报 Palette/fuchsia，PPT Presentation/teal，播客 AudioLines/cyan。`ai_image`/`ai_video` 两个任务能力不在该表中，用 Image/Film 的静态映射。
- 能力名称复用 `t.workspace.home.courseItems[capability].title`（`work-card.tsx:251` 已同样使用）。只有缺少「交付图片预览」这类语义时，才在 `frontend/src/core/i18n/locales/{types,zh-CN,en-US}.ts` 追加完整的中英 key。
- missing 用 FileWarning + amber 和明确的状态文字；empty/unknown 用中性样式，沿用现有 `fileMissing`/`emptyFiles`/`fileInfoUnavailable` 文案；不能只靠颜色区分状态。
- Tailwind 颜色类写成完整的静态字符串或完整类名 map，禁止运行时拼接 `bg-${color}-*`。
- 不用 `/course-samples/*` 营销样例图冒充用户封面，不新增静态假作品图。

### 5.3 错误回退与查看器一致性（A）

- `WorkMedia` 按 URL 记录加载失败（复用 `TaskThumbnail` 的 failedUrl 模式）：同一 URL 不循环重试，URL 变化后允许重新加载，失败时回落到 `WorkTypeCover`（default 态）。
- ready 且 `artifact_missing` 时，在计算任何图片 URL 之前提前拦截：显示 missing 封面，远程 thumbnail/artifact/download 请求为 0。
- `WorkMedia` 按钮把现有的 `focus-visible:ring-ring`（`work-media.tsx:104`，因 `--ring` 透明而不可见）换成 `focus-visible:ring-indigo-500`。
- document/other 的 `FileDetails` 使用同一套类型封面和真实的格式说明，保留 PPT 的 PowerPoint/WPS 提示、HTML/SVG 的不支持内联提示和下载按钮。
- 音频查看器在原生 controls 上方显示音频类型封面；controls 不 autoplay，`preload="metadata"` 只用在 Dialog 内。
- 多文件列表按 `mediaKind` 复用 `work-media.tsx` 的 `MEDIA_ICONS`（导出后复用），不用按扩展名猜的 `getFileIcon()`；`aria-pressed`、文件切换和日期格式不变。
- 视频/音频仍只在查看器打开后挂载，关闭即卸载；媒体出错时保留明确提示和下载能力。

### 5.4 深色创作卡片与专用弹窗（A）

`video-gen-create-dialog.tsx`：

- 外层保留 `bg-background`；focused 描边改为带 dark 变体的类或 `border-border`；不恢复白色渐变。
- 头部改为 `bg-card text-card-foreground border-border`（或语义等价的 `bg-muted/30 text-foreground`），保留圆角与品牌层次，不再使用 `bg-white/75`。
- 标题输入保留原生 input，显式写 `bg-background text-foreground border-input placeholder:text-muted-foreground`；focused 只控制品牌边框（含 dark 变体），不再覆盖背景。
- 脚本 `Textarea` 调用处删除 `bg-white/80`，浅靛边框补 dark 变体，并传入 `placeholder:text-muted-foreground` 覆盖 registry 的 `/60`（tailwind-merge 同组时后者胜出），不手改 registry。
- 图标底座使用 secondary/card/accent 等语义表面，或完整的明暗成对类（如 `bg-indigo-100 dark:bg-indigo-500/10`，与 `appearance.ts` 同口径）。
- 标题输入和本批直接触碰的按钮使用显式 `focus-visible:ring-indigo-500`；不顺手改全局主题。

`home-course-entry.tsx`：

- 卡片描边、媒体徽章、箭头、分隔线、标签、分组图标和重试按钮，统一改用语义 token 或完整的明暗成对类；不为 `ai_image`/`ai_video` 写类型特判。
- 卡片按钮补 `focus-visible:ring-2 focus-visible:ring-indigo-500`（或 outline 的等价写法）。
- 保留媒体图、视频播放、失败重试、标题/描述/tags 文案、ARIA 和选择回调。
- 改动覆盖六张共享卡片，明暗截图也覆盖六卡。媒体图片区域不参与「禁止白块」判定，只检查信息表面与文字。

### 5.5 缩略图生成契约（B）

- **共享 helper** `_ffmpeg_extract_thumbnail(video_path, output_path, timeout=30) -> bool` 的位置参数与返回值不变（多处测试用 `*a, **k` 或位置参数桩掉它），只新增仅关键字参数 `seek_ratio: float = 0.1`（默认即现有行为）。流程：先写同目录且保留 `.jpg` 后缀的临时文件，校验返回码与文件非空；代表时点失败（含 ffprobe 取不到时长）就以 `-ss 0` 重试；成功则 `os.replace` 到目标并返回 True；最终失败则删除临时文件和已有的目标文件，打 warning（含路径），返回 False。helper 模块保持只依赖标准库（`test_thumbnail.py` 按文件路径加载它）。
- **Remotion**：`compose_remotion._extract_thumbnail(video, task_dir)` 改为 `from pipelines._ffmpeg_helpers import _ffmpeg_extract_thumbnail`（同 `from pipelines import net_guard` 的写法）后调用它，传 `seek_ratio=0.25, timeout=60` 保持现有取帧位置与超时；目标仍是 `task_dir / "thumbnail.jpg"`，任何失败都不抛出。三条 AI 管线的调用点不改。
- **回填脚本** `backend/scripts/backfill_video_thumbnails.py`，按 `backend/scripts/reconcile_video_tasks.py` 的先例写（同为 `unified_tasks` 运维脚本，测试见 `backend/tests/test_reconcile_video_tasks.py`）：
  - 参数：`--db-path`（默认自动探测，同 reconcile 的 `find_db`）；默认 dry-run，且 dry-run 用只读连接（`mode=ro`）；`--apply` 才写入；支持 `--task-type`（只接受下列五种）、`--task-id`、`--limit`。
  - 范围：只处理 `remotion_course/remotion/ai_image/ai_visual/ai_video` 中 ready、`output_path` 存在、且 `result.thumbnail_path` 缺失或其文件不存在的任务；默认扫描也仅限这些类型。
  - 抽帧：直接复用 gateway 现有同步 `_extract_thumbnail(output_path, Path(output_path).parent)`，缩略图落在成片同目录的 `thumbnail.jpg`（worker 任务即 `task_dir/thumbnail.jpg`）；不修改该函数或任何 gateway 生产调用；失败计入 failed，由前端类型封面继续兜底。
  - 写回：每个任务一条 `UPDATE unified_tasks SET result = json_set(result, '$.thumbnail_path', ?) WHERE id = ? AND status = 'ready'`，只动这一个键，不覆盖并发写入的其他字段；单任务失败不中断批次；输出 scanned/generated/skipped/failed 计数。
  - 边界：不重新渲染、不调用 provider、不删除任务或 outputs；在 gateway 容器内运行（`docker exec <gateway> …`，写入的路径前缀与 gateway 一致；容器里有 ffmpeg，见 `backend/Dockerfile:91`），用法写在 `--help`。

---

## 六、文件地图与路径边界

### 6.1 Batch A

| 文件 | 职责 |
|---|---|
| `frontend/src/core/works/model.ts` | 新增 `selectWorkCoverFile`；primaryFile 与文件顺序不变 |
| `frontend/src/components/workspace/video-gen/work-type-cover.tsx`（新） | 统一类型封面 |
| `frontend/src/components/workspace/video-gen/work-media.tsx` | 接入封面选择、失败回退、懒加载与显式焦点环；导出 `MEDIA_ICONS` |
| `frontend/src/components/workspace/video-gen/work-viewer-dialog.tsx` | 文档/音频/文件列表视觉一致性，媒体行为不变 |
| `frontend/src/components/workspace/video-gen/video-gen-create-dialog.tsx` | 专用弹窗语义 token、输入/placeholder/焦点 |
| `frontend/src/components/workspace/home-course-entry.tsx` | 六卡共享浅色残留清理与焦点态 |
| `frontend/src/core/i18n/locales/{types,zh-CN,en-US}.ts` | 仅在需要时补封面语义文案 |
| `frontend/tests/unit/core/works/model.test.ts`、`frontend/tests/unit/components/workspace/video-gen/{work-viewer-dialog,work-grid,video-gen-create-dialog-initial-type}.dom.test.tsx`、`frontend/tests/unit/components/workspace/home-course-entry.dom.test.tsx` | 单测 RED/GREEN 与既有断言更新 |
| `frontend/tests/e2e/{works-delivery,home-course-entry}.spec.ts` | 行为级验收（`work-capabilities.spec.ts` 只需保持绿） |

| 允许路径（allow） | 禁改路径（ban） | 共享基座触达 |
|---|---|---|
| 上表文件、本计划（仅 A 的勾选）、A 的 changelog | `backend/**`、`docker/**`、`skills/**`、`config*.yaml`、`frontend/src/components/ui/**`、`frontend/src/components/ai-elements/**`、`frontend/src/styles/globals.css`（全局主题）、`docs/findings-backlog.md`（统一由 B 登记）、Agent runtime/artifact GET | 三份 locale 是多方共享文件；单执行者串行 |

### 6.2 Batch B

| 文件 | 职责 |
|---|---|
| `docker/production-worker/pipelines/_ffmpeg_helpers.py` | 加固共享抽帧 helper |
| `docker/production-worker/pipelines/compose_remotion.py` | Remotion 抽帧改调共享 helper |
| `backend/scripts/backfill_video_thumbnails.py`（新） | 指定任务类型的历史离线回填 |
| `backend/tests/test_thumbnail.py`、`backend/tests/test_backfill_video_thumbnails.py`（新） | 后端行为与边界（进 CI） |
| `docker/production-worker/tests/test_remotion_thumbnail.py`（新）及相关用例 | Remotion 委托测试与 worker 独立套件回归 |

| 允许路径（allow） | 禁改路径（ban） | 共享基座触达 |
|---|---|---|
| 上表文件、本计划（仅 B 的勾选）、B 的 changelog、`docs/findings-backlog.md` | `frontend/**`、`backend/app/gateway/video_gen/**`、`backend/packages/vimax/**`、`docker/production-worker/worker.py`、三条 AI 管线文件、`video-templates/**`、`skills/**`、`backend/seed/agents/**`、auth/middleware/provider 包裹链、`config*.yaml`、DB migration、artifact GET/Range | 是：命中核心路径 `docker/`，必须走分支 + PR + OCR，不可直推 |

两批的路径重叠只有本计划文件，按「全局约束」的编辑归属分块修改，不会冲突。执行中如果发现必须触碰 ban 路径，按停点 ② 停下修订计划，不私自扩权。

---

## 七、实施任务

### Task 0：基线、计划入库与执行环境

**Files：** 本计划（入库）；无业务代码改动。

- [x] **Step 1：基线与漂移。** 记录 `git status --short --branch`；`git fetch origin` 后执行 `git diff 10290cda..origin/main --stat`，重点看 §六 的文件。漂移使本计划的定位失效 → 停点 ①。主 clone 里他人的未跟踪文件（drama-studio、其他 pending 计划等）一律不 stash、不 clean、不覆盖。
- [x] **Step 2：计划入库。** 在主 clone 只 `git add docs/plans/2026-11-27-works-cover-and-create-dialog-theme-optimization-plan.md`，提交 `docs(plans): 作品封面与创建弹窗深色优化计划入库` 并推 main（纯 docs，允许直推）；记下短 hash 作为执行基线，写入 A 的 changelog。
- [x] **Step 3：执行 worktree 与环境。** `git worktree add ../SuperTrainerAgent-exec-works-cover -b works-cover-create-dialog-theme origin/main`。worktree 内执行 `cd frontend && pnpm install --frozen-lockfile --offline` 和 `cd backend && uv sync --frozen --offline`，不复用主 clone 的 node_modules 与 venv。worktree 在仓库目录之外，每条命令用绝对路径或 `cd <worktree> && …`。
- [x] **Step 4：基线测试。** worktree 内跑 `cd frontend && pnpm test`，后台跑 `cd backend && make test`，结果写入 A 的 changelog。失败落在本批要改的文件或用例上 → 停点 ④；其他既有失败只记录，门禁时按 main 对照。
- [x] **Step 5：留修改前证据。** 用现有 Playwright mock 构造 HTML+PNG、纯 MP4/PPT/MP3、任务 `thumbnail_url=null`、深色 ai_image 弹窗，截图存 Playwright 输出目录或 worktree 的 `temp/`（不新建 docs 子目录），供 changelog 做前后对比。不用真实用户数据做夹具。

## Batch A：前端封面与深色体验（PR A）

### Task 1：独立封面选择，修复 HTML/SVG+PNG

**Files：** `frontend/src/core/works/model.ts`、`frontend/tests/unit/core/works/model.test.ts`。

- [x] **Step 1：先写失败测试。** 覆盖：`[HTML,PNG]`、`[PNG,HTML]`、`[SVG,PNG]`、`[PNG,SVG]` 都选 PNG；`primaryFile` 和 `files` 原顺序不变；多张图稳定选第一张；跳过缺失的图；只有缺失的 PNG 加存在的 HTML 时返回 `null`；夹具里 SVG 错误地带 `previewUrl` 时仍不选；任务来源（video 文件）返回 `null`。
- [x] **Step 2：运行并确认 RED。** `cd frontend && pnpm exec vitest run --project node tests/unit/core/works/model.test.ts`。失败原因必须是缺少封面选择结果，而不是夹具错误。
- [x] **Step 3：实现最小纯函数。** 只接受 `exists && mediaKind === "image" && previewUrl !== null`；不修改输入，不改 `primaryFile`，不改 API/schema/后端排序。
- [x] **Step 4：GREEN 与旧契约复核。** 重跑上述测试。`model.test.ts:204-239` 的 PPTX 主文件断言保持不变，并用同一夹具新增「封面是 PNG、主文件仍是 PPTX」的双断言。
- [x] **Step 5：提交。** `fix(works): 封面优先使用同作品光栅图，主交付文件不变`。

### Task 2：统一类型封面、加载失败回退与查看器

**Files：** `work-type-cover.tsx`（新）、`work-media.tsx`、`work-viewer-dialog.tsx`、必要的 locale；`work-viewer-dialog.dom.test.tsx`、`work-grid.dom.test.tsx`。

- [x] **Step 1：先写组件失败测试。** 覆盖：纯 PPT/MP3/Agent MP4/无 PNG 的海报显示 `[data-testid=work-type-cover]`，带格式徽标和能力名称；missing/empty/unknown 三种状态的文案；任务有 thumbnail 时真实图优先；`<img>` 触发 error 后切到类型封面，同一 URL 不再请求，URL 变化后重新出现 `<img>`；ready missing 不渲染任何 `[src]`；查看器的 document/other/audio 显示类型封面，audio 不 autoplay；文件列表图标按 mediaKind 显示，`aria-pressed` 正确。
- [x] **Step 2：处理既有断言。** `work-viewer-dialog.dom.test.tsx:282`、`:311` 和 `work-grid.dom.test.tsx:416` 的零 `[src]`/零 `<img>` 断言**保持不变**（类型封面不含图片）。`work-viewer-dialog.dom.test.tsx:312` 的 `toContain("lesson.mp4")` 改为断言格式徽标 `MP4`；文件名已移出封面，仍由卡片的 `WorkFileSummary` 显示，`work-grid.dom.test.tsx:322` 继续覆盖。
- [x] **Step 3：确认 RED。** `cd frontend && pnpm exec vitest run --project dom tests/unit/components/workspace/video-gen/work-viewer-dialog.dom.test.tsx tests/unit/components/workspace/video-gen/work-grid.dom.test.tsx`，确保因新封面/回退尚不存在而失败。
- [x] **Step 4：实现 `WorkTypeCover` 并接入卡片。** 按 §5.1–5.3 落地；不改 WorkCard 操作逻辑，不新增静态假作品图，不挂 Agent MP4/MP3。
- [x] **Step 5：接入查看器。** document/other/audio/错误态复用同一视觉语言；文件列表使用 `MEDIA_ICONS`；当前文件、键盘、焦点和下载契约不变。
- [x] **Step 6：GREEN 与格式检查。** 跑相关 dom/node 测试及 Prettier；检查没有动态拼接的 Tailwind 颜色类，也没有新增 registry 修改。
- [x] **Step 7：提交。** `fix(works): 统一作品默认封面与加载失败回退`。

### Task 3：清理深色创作入口与专用弹窗

**Files：** `video-gen-create-dialog.tsx`、`home-course-entry.tsx`、对应 DOM 测试；不改提交逻辑。

- [x] **Step 1：先更新失败测试。** `video-gen-create-dialog-initial-type.dom.test.tsx` 对 `ai_image`/`ai_video` 做参数化断言：头部、标题 input、脚本 textarea 不含 `bg-white/75`、`bg-white/80`；标题 input 含 `text-foreground` 与 `placeholder:text-muted-foreground`；外层仍是 `bg-background` 且无渐变。`home-course-entry.dom.test.tsx:122-128` 改为断言：媒体徽章与标签不含没有 dark 配对的固定浅色类（含对应 `dark:` 类或改用语义 token），卡片按钮含显式焦点类。
- [x] **Step 2：确认 RED 后做最小 className 修复。** 按 §5.4 改业务组件，保留 `initialTaskType`、fixed 脚本、payload、高级设置、上传、音色/语言弹层、草稿和提交跳转。
- [x] **Step 3：统一首页六张共享卡片。** 清理媒体徽章、箭头、分隔线、标签、分组图标、重试按钮和卡片描边的固定浅色残留；补卡片按钮的显式焦点态，不全局修改 `--ring`。
- [x] **Step 4：运行定向与交互回归。** `video-gen-create-dialog-initial-type.dom.test.tsx`、`home-course-entry.dom.test.tsx`、`create-dialog-honesty.dom.test.tsx`、`video-gen-create-dialog-voice.dom.test.tsx` 全绿；确认普通（非 focused）创建弹窗行为未变。
- [x] **Step 5：提交。** `fix(workspace): 修复深色创作入口与专用弹窗配色`。

### Task 4：Batch A 行为验收、门禁与 PR A

**Files：** `frontend/tests/e2e/works-delivery.spec.ts`、`frontend/tests/e2e/home-course-entry.spec.ts`、A 的 changelog、本计划（A 的勾选）。

- [x] **Step 1：真实图片封面 E2E（`works-delivery.spec.ts`）。** HTML+PNG、SVG+PNG 两组用有效的 PNG Buffer 和 `image/png` 响应。断言卡片 `img` 的 `naturalWidth>0`、src 为 PNG 的 artifact URL、`loading="lazy"`；「查看」默认打开 HTML/SVG 的文件详情，下载链接仍指向主文件；HTML/SVG 的 artifact 请求为 0。现有 `output()` 把非 mp4/png 一律映射成 `document`（`works-delivery.spec.ts:19-23`），SVG/HTML 夹具须显式传 `media_kind: "other"`，MP3 传 `"audio"`。
- [x] **Step 2：默认封面与网络门禁。** PPT/MP3/纯 Agent MP4/任务无 thumbnail/thumbnail 404 都显示 `work-type-cover`，没有破图。在既有「avoids eager Agent media」用例（`:496`）旁新增同形态用例，不改动原用例（它固定了 6 张卡，请求监听只跟踪 mp4/mp3）。新用例的请求监听扩到 MP4/MP3/HTML/SVG/download：列表阶段只允许 PNG 与任务缩略图请求，其余为 0。
- [x] **Step 3：深色作品页。** 扩展既有的「renders English delivery copy in dark mode」用例（`:659`），覆盖类型封面与真实图片封面并截图。`work-capabilities.spec.ts` 的 1440/1280/390/320 几何用例（`:253`）不改，保持绿。
- [x] **Step 4：创建入口对比度（`home-course-entry.spec.ts`）。** 浅、深两种主题各打开一次 `ai_image` 专用弹窗：两个入口共用 focused 分支，颜色也不随视口变化，1280 一档即可。测头部标题、描述、标题输入文字和 `::placeholder` 的对比度 ≥4.5:1。实现要点：先用 1×1 canvas 把计算色解析成 sRGB（浅色 token 是 `oklch()`，计算值不是 `rgb()`），再沿祖先链做 alpha 合成（深色 `--card`/`--input` 是半透明的）。`agent-new-chat-guide.spec.ts:92` 的现成 helper 两者都不处理，在本 spec 内写局部 helper。深色下再测首页卡片标签文字的对比度 ≥4.5:1；媒体徽章压在图片上，不做机械判定，只看截图。保存六卡与弹窗的明暗截图。既有的 390/320 溢出（`:403`）和弹窗不透明（`:420`）用例保持绿。
- [x] **Step 5：A 的门禁。** 跑完定向 Vitest/Playwright 后，后台运行 `cd frontend && pnpm check && pnpm test` 和 `pnpm test:e2e`，再跑根 `make check` 与 `python3 scripts/check_docs_convention.py`。`pnpm perf:check` 可本地跑，也可以 PR CI 的 perf-budgets job 为准；新组件会进入作品页包体，超预算须在 PR 里写归因。冻结测试输入，不并行共享 `.next`/端口。backlog 里既有的 video-gen `networkidle` 超时保留 main 对照，不顺手修。
- [x] **Step 6：写 A 的 changelog。** 新建 `docs/changelog/changelog-<执行日>-works-cover-and-create-dialog-theme-optimization-batch-a.md`，含 TL;DR 和「验收记录」（逐行写 命令 → 实际关键输出），包括执行基线 hash、基线测试结果、Task 1–3 的 RED 输出、对比度实测值、修改前后截图路径；A 期间的范围外发现写进「范围外发现」小节（由 B 统一登记 backlog）。
- [x] **Step 7：提交。** 两笔：`test(works): 补封面与深色对比度行为验收`（两个 E2E spec）、`docs(changelog): 记录作品封面与创建弹窗深色优化的验收`（A 的 changelog + 本计划中 Task 0 至 Task 4 Step 7 的勾选）。
- [ ] **Step 8：PR A 与合入。** 按「全局约束」的 PR 门禁执行：标题为批次摘要，正文链接本计划与 A 的 changelog，并注明「只需前端发布，无后端/DB/worker 变更，部署须用户另行下达指令」。合并被拒按「全局约束」处理后继续 Task 5。本步的勾选由 Task 8 补。

## Batch B：任务型视频缩略图可靠性（PR B）

### Task 5：加固共享抽帧 helper，Remotion 改用它

**Files：** `_ffmpeg_helpers.py`、`compose_remotion.py`、`backend/tests/test_thumbnail.py`、`docker/production-worker/tests/test_remotion_thumbnail.py`（新）。

- [ ] **Step 1：切 B 分支。** 在同一 worktree 执行 `git fetch origin`，再 `git switch -c task-video-thumbnail-reliability origin/main`。A 已合入时这个 `origin/main` 包含 A；A 的合并被拒时也照样从 `origin/main` 切（两批代码路径不重叠）。
- [ ] **Step 2：先写失败测试。** helper 用例写进 `backend/tests/test_thumbnail.py`（真实 ffmpeg，进 CI），沿用该文件按路径加载 helper 的写法。覆盖：目标位置已有旧图、本次抽帧失败（如输入不是视频）时，返回 False 且旧图被删除；时长 0.5s 的视频在代表时点失败后，t=0 回退成功（已实测：现有命令 seek 到 1s 时 ffmpeg 失败且不出图，seek 到 0 成功）；成功时目标文件非空，且没有残留的临时文件。Remotion 用例写进 worker 套件的 `test_remotion_thumbnail.py`（`import pipelines.compose_remotion as cr`，同 `test_staged_public_dir.py`）：桩掉 helper，断言以 `task_dir/thumbnail.jpg` 和 `seek_ratio=0.25` 调用，helper 返回 False 或抛异常时都不向外抛。不放进 backend/tests：那里的 `sys.modules["pipelines"]` 桩会让导入随收集顺序失败（§4.1）。
- [ ] **Step 3：确认 RED。** `cd backend && PYTHONPATH=. uv run pytest tests/test_thumbnail.py -q`；`cd docker/production-worker && ../../backend/.venv/bin/python -m pytest tests/test_remotion_thumbnail.py -q`。
- [ ] **Step 4：实现。** 按 §5.5 加固 helper（含仅关键字参数 `seek_ratio`）；Remotion 的 `_extract_thumbnail` 改为调用 helper。两个 worker 镜像都 COPY 整个 `pipelines/`，helper 只用标准库，remotion-worker 内可直接导入。三条 AI 管线的调用点和 `worker.py` 不改。
- [ ] **Step 5：GREEN 与回归。** 跑 `tests/test_thumbnail.py`，以及桩掉 helper 的 `tests/test_worker_ai_visual_payload.py`、`tests/test_ai_video_duration_alignment.py`、`tests/test_worker_local_visual_misalign.py`、`tests/test_worker_incremental_manifest.py`（确认签名兼容）。再跑 worker 套件：`cd docker/production-worker && ../../backend/.venv/bin/python -m pytest tests/ -q`。
- [ ] **Step 6：提交。** `fix(video-gen): 抽帧失败不留旧图并回退首帧，Remotion 复用共享实现`。

### Task 6：历史任务离线回填工具（默认 dry-run）

**Files：** `backend/scripts/backfill_video_thumbnails.py`（新）、`backend/tests/test_backfill_video_thumbnails.py`（新）。

- [ ] **Step 1：先写失败测试**（临时目录 + `--db-path` 指向临时 SQLite，写法参照 `test_reconcile_video_tasks.py`，不碰开发库）。覆盖：dry-run 零写入；默认扫描和 `--apply` 只处理 §5.5 列出的五种任务类型，其他类型一律跳过，`--task-type` 传其他值被拒；只处理 ready、`output_path` 存在、且 `result.thumbnail_path` 缺失或其文件不存在的任务；只写 `thumbnail_path` 一个键，result 其余字段不变；重复执行幂等；单个任务失败不中断批次；输出 scanned/generated/skipped/failed 计数；真实小 MP4 生成有效 JPEG。
- [ ] **Step 2：确认 RED 后实现。** 按 §5.5 的回填契约实现，复用 gateway 现有同步 `_extract_thumbnail`，不修改 gateway 生产代码。
- [ ] **Step 3：GREEN 并提交。** `feat(video-gen): 增加历史视频缩略图离线回填工具`。真实环境的 dry-run/apply 不在本次执行内，见 §九。

### Task 7：Batch B 行为验收、门禁与 PR B

**Files：** `backend/tests/test_backfill_video_thumbnails.py`、`backend/tests/test_thumbnail.py`、B 的 changelog、`docs/findings-backlog.md`、本计划（B 的勾选）。

- [ ] **Step 1：行为级验收（API 级，pytest 内完成）。** 两条，都用真实小 MP4，不调用付费生成，不修改 gateway 生产代码：
  - 回填路径（写进 `test_backfill_video_thumbnails.py`）：临时库里一个 ready `ai_video` 任务没有缩略图，跑回填 `--apply` 后，`_task_with_thumbnail` 下发 `thumbnail_url`，缩略图 GET 返回可解码的 JPEG（PIL 或 ffprobe 校验）。
  - 旧图路径（写进 `test_thumbnail.py`）：任务目录有旧 `thumbnail.jpg`、result 也指向它；对损坏的成片调用加固后的 helper 失败后，旧图被删，`_task_with_thumbnail` 返回 `thumbnail_url=None`（列表不再下发旧图）。
- [ ] **Step 2：B 的门禁。** 先 `pre-commit run --files <本批改动>`；后台 `cd backend && make test`（全量）；worker 套件全量 `cd docker/production-worker && ../../backend/.venv/bin/python -m pytest tests/ -q`；根 `make check` 与 `python3 scripts/check_docs_convention.py`。worker 套件缺依赖时修环境，不缩小验证范围。
- [ ] **Step 3：写 B 的 changelog 与登记 backlog。** 新建 `docs/changelog/changelog-<执行日>-works-cover-and-create-dialog-theme-optimization-batch-b.md`，含 TL;DR、「验收记录」（命令 → 关键输出）与发布注意项（§九）；不把夹具成功率写成线上改善比例。在 `docs/findings-backlog.md` 按格式（`- [ ] YYYY-MM-DD 一句话 + 指针`）登记 A changelog「范围外发现」与 B 期间的范围外问题，以及 Q16 的缓存候选。
- [ ] **Step 4：提交。** 两笔：`test(video-gen): 补缩略图 API 级行为验收`（Step 1 的两个测试文件）、`docs(changelog): 记录任务视频缩略图加固的验收`（B 的 changelog + backlog + 本计划中 Task 5 至 Task 7 Step 4 的勾选）。
- [ ] **Step 5：PR B 与合入。** 按「全局约束」的 PR 门禁执行（命中核心路径，不可直推），正文链接本计划与 B 的 changelog，并写明发布只需重建 production-worker 与 remotion-worker 两个镜像，不需要 gateway 部署，部署须用户另行下达指令。合并顺序在 A 之后；A 尚未合入时不合 B，按「合并被拒的处理」交接。本步的勾选由 Task 8 补。

### Task 8：收尾

- [ ] **Step 1：最终门禁。** 两个 PR 都已合入后，在 worktree 执行 `git fetch origin && git switch --detach origin/main`，跑根 `make check`、后台 `cd backend && make test`、`cd frontend && pnpm check && pnpm test`、`python3 scripts/check_docs_convention.py`，全部通过。
- [ ] **Step 2：清理。** 确认没有在途的后台任务；本机起过的服务用 TaskStop 停掉。worktree 与两条远程分支**不移除**（移除会删掉 ignored 证据），列入最终汇报交用户决定。
- [ ] **Step 3：回填计划状态并交接。** 回到主 clone 执行 `git pull --ff-only`；补勾 Task 4 Step 8、Task 7 Step 5 与本 Task 全部；把状态头原行改为 `已执行（<日期>；PR #A → #B 串行合入 main <hash>；changelog 见 batch-a / batch-b；未部署，发布见 §九）`；单独提交 `docs(plans): 作品封面与缩略图两批收尾` 并推 main。最终汇报：两个 PR 链接、门禁结果、worktree 与分支清单，以及交付后待用户决定的事项（§九 的部署与回填、§2.6 的取证、清理）。

两个 PR 中任一尚未合入（例如合并被拒）时，Step 1 与 Step 3 的状态提交无法执行：Step 2 照常完成，最终汇报写明「先合 A 再合 B，合并后补做 Task 8 Step 1 与 Step 3」，Task 8 的勾选到那时一起补。

---

## 八、验收矩阵与完成定义

| 用户反馈 | 实现级证据 | 行为级证据 |
|---|---|---|
| HTML/SVG+PNG 应展示 PNG | `selectWorkCoverFile` 单测；primaryFile 不变 | 真实 PNG `naturalWidth>0`；HTML/SVG 请求为 0；默认查看/下载不变 |
| 文字动效/PPT/播客无封面、有 demo 感 | `WorkTypeCover` 结构、格式徽标、三种状态、零 `src` | 六类明暗截图；查看器与下载可用 |
| 任务无缩略图/图片破图（展示侧） | failedUrl 复位；missing 提前拦截 | 404 回落类型封面；missing 远程请求为 0 |
| 深色白块与浅字 | 无固定白底；语义 token；显式前景/placeholder/焦点类 | 明暗两种主题下计算样式对比度 ≥4.5:1，并附截图 |
| A 不回退 | 无列表 video/audio；无主动内容嵌入 | 既有网络门禁、几何（1440/1280/390/320）与键盘用例全绿 |
| 任务缩略图生成不可靠（生成侧） | helper：临时文件、t=0 回退、失败清除旧图；Remotion 委托 helper 且保持 25% 取帧 | `test_thumbnail.py` 真实 ffmpeg 用例（进 CI）：抽帧失败后列表不再下发旧图；worker 套件全绿 |
| 历史任务无图 | 回填 dry-run 零写入、apply 幂等、单键写回、只处理五种类型 | 临时库 + 真实 MP4：回填后列表下发 URL、GET 返回有效 JPEG；真实环境运行交用户授权 |

只有对应批次的实现级与行为级证据同时通过、门禁绿、changelog 验收记录真实完整，才可勾选该批完成。Batch A 合入时，不得宣称任务视频缩略图的生成已修复；Batch B 合入后，才可宣称新任务的缩略图生成已加固。历史任务要等回填在真实环境运行后才有真实缩略图。任何时候都不得宣称 Agent 视频/PPT 已支持内容级预览。

---

## 九、发布清单与回滚（需用户另行下达部署指令）

本次执行到两个 PR 合入为止，以下全部留到交付后由用户决定。

1. **Batch A**：只需前端发布，没有 API 契约变化；回滚即 revert PR A。
2. **Batch B**：只需重建 production-worker、remotion-worker 两个镜像（共享 `pipelines/`），不需要 gateway 部署。模板未改，TEMPLATE_VERSION 不变；公司服务器按部署手册 K16 流程。回滚即回退镜像或 revert PR B，不删除已生成的 outputs/thumbnails。
3. **顺序**：两批运行时互不依赖，建议先 A 后 B。
4. **发布后核对**：由部署方按既有测试流程确认 remotion_course、ai_image/ai_visual、ai_video 新任务能生成真实缩略图；不新增其他任务类型的验收。
5. **历史回填**：脚本随下一次 gateway 镜像进入容器（`backend/Dockerfile` 复制整个 `backend/`）；B 本身不需要 gateway 部署，所以在那之前要跑就先把脚本 `docker cp` 进 gateway 容器。在容器内运行，先在用户授权下跑 dry-run，把脱敏计数交用户；`--apply` 须用户明确指令，执行前备份数据库并记录回滚方式。需要定性时先按 §2.6 取证。

---

## 十、明确不做与后续独立候选

1. 不给 HTML/SVG/XHTML 增加 iframe/object/embed/srcDoc 预览，不改后端的 `other` 分类和强制下载策略。
2. 不在作品列表挂 Agent MP4/MP3，不用 `<video preload="metadata">` 代替缩略图服务，不改 artifact GET 的 Range/整文件读取问题。
3. 不引入 PPT 首页渲染、Office 在线预览、音频封面提取或通用 Agent 视频首帧服务；这些需要独立的媒体管线、缓存、权限和失效设计。
4. 候选（未立项）：让 course-video Agent 交付时附带一张封面 PNG，Batch A 的安全图片选择会自动显示它。立项前注意两点：SOUL 现在规定只交付一个 mp4（`backend/seed/agents/course-video/SOUL.md:118`）；`primaryFile` 取的是最新的存在文件，封面若晚于 mp4 生成，就会变成默认查看和下载的文件，所以须同时处理主文件语义（例如让封面先于成片写出，或主文件按媒体类型优先）。
5. 不改主文件、文件版本、同名快照、收藏、分享、批量下载、跨来源分页和搜索语义。
6. 不改 `components/ui/**`、`components/ai-elements/**`、全局 `--ring`、config schema、DB migration、技能、seed agent、provider/middleware/auth。
7. 不改 worker.py 的完成写回和 Remotion 模板；不用缩略图失败阻断合法视频交付；不在 API 列表或缩略图 GET 中同步 FFmpeg；不自动对真实历史任务执行 apply。
8. 不处理缩略图 URL 的浏览器缓存失效（Q16，登记 backlog 候选）。
9. 不把营销样例图当用户的默认封面，不新增无意义的静态假作品资产。
10. 不顺手修 findings backlog 里既有的 video-gen `networkidle`、全局焦点 token、Range 等无关问题；新发现的范围外问题按格式登记（统一在 B 的 PR 里），不夹带实现。
11. ViMax 不是本项目需要的能力（用户 2026-11-27 确认）：本计划不改任何 ViMax 代码、测试与部署，回填也不处理它的任务；执行中不得把它加回来。
