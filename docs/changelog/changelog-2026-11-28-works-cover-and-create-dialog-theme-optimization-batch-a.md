# 作品封面与创建弹窗深色体验优化（Batch A：纯前端）

> **状态**：已执行（2026-11-28；PR #69 合入 main，合入 hash 由 Task 8 收尾补记；未部署，发布须用户另行下达指令）。计划：`docs/plans/2026-11-27-works-cover-and-create-dialog-theme-optimization-plan.md`（Batch A = Task 1–4）；执行基线 commit `dd24a2e9`（计划入库）。
> **TL;DR**：纯前端批。① 新增纯函数 `selectWorkCoverFile`：HTML/SVG+PNG 的 Agent 作品卡面显示同作品交付的光栅图，`primaryFile`、文件顺序、查看/下载不变；② 新增共享 `WorkTypeCover`（CSS + 内联 SVG，零请求）替换「灰底 + 单图标 + 长文件名」，`WorkMedia` 按 URL 记录加载失败并回落类型封面；③ 专用创建弹窗与首页六张共享卡片的固定浅色类改为语义 token / 明暗成对类，补显式焦点环。关键决策：封面与主交付文件解耦；类型封面零请求；不改 registry 与全局主题。影响面 = `frontend/`（works 模型、卡片、查看器、创建弹窗、首页卡片、三份 locale、单测与 E2E）；无后端/DB/worker 变更；不部署。

## 执行基线与本机环境

- 计划入库 `dd24a2e9`（纯 docs，直推 main）；执行 worktree 在仓库外同级目录、分支 `works-cover-create-dialog-theme`，frontend 依赖 `pnpm install --frozen-lockfile --offline`，不复用主 clone 的 `node_modules`。
- 基线测试（修改前）：`cd frontend && pnpm test` → `Test Files 307 passed (307) / Tests 2172 passed (2172)`；`cd backend && make test` → `10444 passed, 98 skipped`。
- 本机未装 git hooks：每笔提交前手动 `pre-commit run --files <改动文件>`，先于后台测试。

## Task 1：独立封面选择（`selectWorkCoverFile`）

- `frontend/src/core/works/model.ts` 新增纯函数：按后端现有文件顺序取首个 `exists && mediaKind === "image" && previewUrl !== null` 的文件；封面资格只看 `mediaKind`，SVG/HTML 归 `other`，夹具错误地带 `previewUrl` 也不选。
- `primaryFile`（首个存在文件）、`files` 顺序、查看器默认文件、下载与卡片文件摘要都不变；卡片可以出现「PNG 封面 + HTML 主文件摘要」，点击封面仍打开主文件。

## Task 2：统一类型封面、加载失败回退与查看器

- 新建 `work-type-cover.tsx`：根元素 `data-testid="work-type-cover"` + `data-cover-state`（default/empty/missing/unknown）；中央 48px 圆角图标 medallion + 能力名称 + 格式徽标（有文件名取扩展名 MP4/PPTX/HTML/SVG…，无文件名按 mediaKind 给通用标签）；图标/配色复用 `core/agents/appearance.ts` 口径，`ai_image`/`ai_video` 用 Image/Film 静态映射；Tailwind 颜色类全部是静态字符串 map，装饰 `aria-hidden`，不含 `<img>`/`src`。
- `work-media.tsx`：封面来源 = 任务缩略图（ready 且未 artifact_missing，`object-cover`）→ `selectWorkCoverFile`（`object-contain`，`loading="lazy"` `decoding="async"`）→ 类型封面；`failedUrl` 按 URL 记录失败，同一 URL 不重试、URL 变化后重新加载；ready 且 `artifact_missing` 在计算任何 URL 之前拦截；按钮焦点环 `focus-visible:ring-ring` → `focus-visible:ring-indigo-500`（加 `ring-inset`，卡片 `overflow-hidden` 会裁外环）；导出 `MEDIA_ICONS`。
- `work-viewer-dialog.tsx`：document/other 的文件详情、音频（controls 上方）、missing/failed/empty 态复用 `WorkTypeCover`，保留 PPT 的 PowerPoint/WPS 提示、HTML/SVG 的不支持内联提示与下载按钮；多文件列表按 `mediaKind` 取 `MEDIA_ICONS`（原先按扩展名会把 SVG 显示成图片图标）；`aria-pressed`、文件切换、日期格式不变。
- i18n：三份 locale 各补 `coverImageAlt(title)`（Agent 图片封面的 alt，文件名缺失时）与 `coverVideo/coverAudio/coverDocument/coverImage/coverOther`（无文件名时的通用格式标签），中英完整。

## Task 3：深色创作入口与专用弹窗

- `video-gen-create-dialog.tsx` focused 分支：外层保留 `bg-background`，描边补 `dark:border-indigo-500/30`；头部 `bg-white/75 border-indigo-100/70` → `bg-card text-card-foreground border-border`；图标底座补 `dark:bg-indigo-500/10`；标题输入去掉 focused 的 `bg-white/80` 覆盖，基础类显式 `bg-background text-foreground placeholder:text-muted-foreground focus-visible:ring-indigo-500`（基础类两套弹窗共用，普通弹窗的标题输入同时获得显式前景/placeholder 色）；脚本 `Textarea` 删除 `bg-white/80`，传 `placeholder:text-muted-foreground` 覆盖 registry 的 `/60`，浅靛边框补 dark 变体。`initialTaskType`、fixed 脚本、payload、高级设置、上传、音色/语言弹层、草稿与提交跳转不变。
- `home-course-entry.tsx`：卡片描边 `border-border`、箭头/标签/分组图标/重试按钮补 `dark:` 配对或改 `text-violet-700 dark:text-violet-300`，分隔线 `border-border`，媒体徽章 `bg-background/90 text-foreground border-border`，分组 kicker `text-slate-400` → `text-muted-foreground`；卡片按钮补 `focus-visible:outline-indigo-500`（inset outline，article `overflow-hidden`）。六卡统一处理，不为 `ai_image`/`ai_video` 写特判。

## 证据摘要

- 修改前截图（worktree `temp/works-cover-evidence/before/`，gitignored）：`works-creative-studio-light.png`（HTML/SVG 显示文件图标）、`works-{course-video,ppt-master,podcast-studio}-light.png`（灰底 + 文件名）、`dialog-ai_image-dark.png`（白色头部 + 白色标题框 + 浅字）、`home-dark.png`。
- 修改后截图（`temp/works-cover-evidence/after/`，同名）：HTML/SVG+PNG 卡面显示 PNG；纯 MP4/PPTX/MP3 显示类型封面 + 格式徽标；深色弹窗头部/输入框为深色表面。E2E 另存 `test-results/**/english-dark-type-cover.png`、`english-dark-real-cover.png`、`home-cards-{light,dark}.png`、`dialog-ai_image-{light,dark}.png`。
- 对比度实测（`home-course-entry.spec.ts`，1×1 canvas 解析 oklch → sRGB，沿祖先链 alpha 合成）：

| 元素 | 浅色 | 深色 |
|---|---|---|
| 专用弹窗头部标题 | 19.71:1 | 16.98:1 |
| 头部描述（`text-muted-foreground` on `bg-card`） | 4.72:1 | 6.83:1 |
| 标题输入文字 | 18.99:1 | 17.94:1 |
| 标题输入 `::placeholder` | 4.55:1 | 7.22:1 |
| 首页卡片标签文字 | 6.65:1 | 9.81:1 |

  深色下头部/输入框合成背景亮度 ≤ 0.008（无白块）。

## 验收记录

每行 = 命令 → 实际输出关键行。

- `cd frontend && pnpm exec vitest run --project node tests/unit/core/works/model.test.ts`（Task 1 RED，实现前）→ `Tests 10 failed | 25 passed (35)`，失败原因 `TypeError: selectWorkCoverFile is not a function`（首轮有 8 条因夹具 agentName 与 capability 不匹配报 `work not built`，修夹具后重跑）
- 同命令（Task 1 GREEN）→ `Tests 35 passed (35)`
- `pnpm exec vitest run --project dom tests/unit/components/workspace/video-gen/work-viewer-dialog.dom.test.tsx tests/unit/components/workspace/video-gen/work-grid.dom.test.tsx`（Task 2 RED）→ `Tests 16 failed | 32 passed (48)`（`expected undefined to be 'PPTX'`、`expected <img …> to be null` 等）；GREEN → `Tests 48 passed (48)`；相关回归 `vitest run tests/unit/components/workspace/video-gen tests/unit/core/works tests/unit/core/i18n home-course-entry.dom.test.tsx` → `Test Files 55 passed (55) / Tests 362 passed (362)`；`pnpm exec tsc --noEmit` → 无输出
- `pnpm exec vitest run --project dom video-gen-create-dialog-initial-type.dom.test.tsx home-course-entry.dom.test.tsx`（Task 3 RED）→ `Tests 3 failed | 7 passed (10)`（`not to contain 'bg-white/75'`、`not to match /\bbg-white\//`）；GREEN + 交互回归 `vitest run tests/unit/components/workspace/video-gen home-course-entry.dom.test.tsx workspace-home.dom.test.tsx` → `Test Files 48 passed (48) / Tests 292 passed (292)`（含 `create-dialog-honesty`、`video-gen-create-dialog-voice`）
- `pnpm exec playwright test tests/e2e/works-delivery.spec.ts tests/e2e/home-course-entry.spec.ts tests/e2e/work-capabilities.spec.ts` 首跑 → `42 passed / 4 failed`：两条对比度用例对 SVG 元素取 `className.split`（用例 bug）、网络门禁用例在切到 ai_video 后才断言 creative-studio 卡片（用例 bug）、既有「avoids eager Agent media」夹具给 `cover.png` 回 `text/plain` 正文（一直是破图，旧的失败回退把它卸载）——给该夹具补真实 PNG 路由、断言不动；`work-capabilities.spec.ts` 1440/1280/390/320 几何用例 7 条全绿
- 同命令第二跑 → `36 passed / 1 failed`（静态资源 `/images/trainer-ai-mark.svg` 误入监听，改为只看 `/api/` 路径）；第三跑 `works-delivery + home-course-entry` → `37 passed (58.6s)`，对比度实测值即上表
- 行为级断言：HTML/SVG+PNG 卡片 `img` 的 `naturalWidth>0`、`src` 为 PNG artifact URL、`loading="lazy"` `decoding="async"`，「查看」默认打开 HTML/SVG 文件详情、下载链接仍指向主文件、HTML/SVG 正文零请求；PPT/MP3/纯 MP4/任务无缩略图/缩略图 404 均显示 `work-type-cover` 且无 `img`；列表阶段 MP4/MP3/HTML/SVG/download 请求为 0，只允许 PNG 与任务缩略图
- 修改前证据：`pnpm exec playwright test tests/e2e/zz-evidence-before.spec.ts`（临时用例，未入库）→ `4 passed (1.1m)`，截图 9 张
- `cd frontend && pnpm check` → `✖ 120 problems (0 errors, 120 warnings)`（warnings 既有）、`tsc --noEmit` 无输出、prettier 通过，`CHECK_EXIT=0`
- `cd frontend && pnpm test` → `Test Files 307 passed (307) / Tests 2198 passed (2198)`（基线 2172 + 本批新增 26），`TEST_EXIT=0`
- `cd frontend && pnpm test:e2e --reporter=list`（全量）→ `203 passed, 3 skipped, 1 failed (2.8m)`。唯一失败 = `tests/e2e/chat-thread-recovery.spec.ts:961`（chat 滚动锚定：70 轮线程加载旧页，`older question 0` 5s 内未挂载），与本批无关（chat 页面未改）；隔离复跑 `pnpm exec playwright test tests/e2e/chat-thread-recovery.spec.ts` → `18 passed (28.4s)`。backlog 2026-11-21 记录的三条 video-gen `networkidle` 用例本次全量通过
- 根 `make check` → `MAKE_CHECK_EXIT=0`：`ruff check .` `All checks passed!`、eslint 0 errors、`knip` 通过、`i18n key 存活检测通过：717 个叶子全活`、`OK 仓库治理约定全过`
- `python3 scripts/check_docs_convention.py` → `OK 仓库治理约定全过（docs 命名/位置/月度归档/根目录白名单/CLAUDE 指针/测试落位/plan 状态头/backend/docs 形态/二进制禁区门禁/禁区规则镜像一致性）`
- `pnpm perf:check` → 以 PR CI 的 perf-budgets job 为准（新组件 `work-type-cover.tsx` 进入 `/workspace/video-gen` 路由包体）

## 范围外发现（由 Batch B 统一登记 findings backlog）

- 浅色主题 `--muted-foreground` 压在 `--background` 上只有 4.55:1（placeholder 场景实测），贴着门槛；本批没动全局 token。
- `chat-thread-recovery.spec.ts:961` 在全量并发下偶发超时（隔离通过），疑似负载敏感的时序用例；本批不改。
- 既有 backlog 项（artifact GET 整文件读取/Range、全局透明 `--ring`、video-gen `networkidle`）按计划不顺手改，不重复登记。

## 发布注意项

- 只需前端发布；没有 API 契约变化、后端/DB/worker 变更。回滚 = revert PR A。
- 历史任务缩略图的生成侧与回填在 Batch B；Batch A 合入时不得宣称任务视频缩略图的生成已修复，也不得宣称 Agent 视频/PPT 已支持内容级预览（类型封面不是内容预览）。
