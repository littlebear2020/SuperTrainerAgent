# 首页与视频资产页重构执行记录

> **状态**：代码已执行，任务 1–7 已完成；部署待进行。
> **TL;DR**：首页改为视频课/图文课双入口与六个创作入口，视频资产页改为“我的作品/我的资产”双视图；复用既有 Agent、创建框与项目 CRUD，不改 API 或数据结构；全部前端静态、单测、E2E、性能与仓库门禁通过，未部署。

## 变更摘要

- 首页新增六个固定创作入口；四个 Agent 入口进入既有新会话路由，图片动效与 AI 视频分别挂载带 `initialTaskType` 的全新创建框实例。
- 首页样例使用三个 10 秒、960px、24fps、无音轨的 H.264 MP4 与一个 WebP；仅展开面板播放，页面隐藏、面板切换及 reduced motion 会停止播放，失败不阻断入口。
- 侧边栏只显示首页、视频资产与运营后台，保留最近对话、设置及所有既有深链。
- 我的作品只筛选 ready 的 `ai_image/ai_video`，按 `created_at` 确定性排序，首屏 6 个、每次增加 6 个、最多最近 100 个；missing 作品保留但禁止查看与下载。
- 我的资产复用课程项目新建、编辑、保存、删除与详情能力；封面按风格锚图、风格预览图、色块依次降级。
- 视频资产原页头、类别 Tabs 与“新建任务”入口移除；tabs 为组件本地状态，不写 URL 或持久化。
- 经用户两次明确授权，仅额外清理 `frontend/eslint-suppressions.json` 的一条失效抑制，并移除 `workflow-grid.tsx` 的死 `export` 标记；`WorkflowGrid` 组件与 `WORKFLOWS` 均保留。

## 影响面与部署

- 仅前端组件、测试、i18n、媒体与文档发生变化；无 backend、v3 API、数据库、Docker、Skills、video-templates、锁文件或真实服务器配置改动。
- 无数据迁移、无服务端分页、无新增统计/时长/分辨率/完成时间/系统健康字段。
- 尚未部署；后续如需发布，只构建并替换 frontend，按既有流程进行浏览器冒烟。

## 验收记录

- `pnpm exec vitest … video-gen-create-dialog-initial-type…` → 3 条初始类型测试通过；相邻 DOM 9/9、payload 9/9。
- `pnpm exec vitest … home-course-catalog… workspace-home.test.ts` → 12/12；首页 DOM/i18n 9/9。
- `pnpm exec playwright test tests/e2e/sidebar.spec.ts` → 1/1；导航单测与角色文案 8/8。
- `pnpm exec vitest … task-thumbnail…` → 新旧缩略图 DOM 8/8；recompose/source guard 17/17。
- `pnpm exec vitest … completed-video-grid…` → 纯函数/source guard 4/4；DOM 与缩略图 12/12，105 条 fixture 验证最近 100 上限。
- `pnpm exec vitest … course-project…` → 封面/详情纯函数 10/10；页面、dirty guard、删除确认与 X11 DOM 14/14。
- `pnpm exec playwright test tests/e2e/home-course-entry.spec.ts tests/e2e/video-assets.spec.ts …` → 任务 7 定向行为与兼容套件 21/21；新用例监听 `pageerror`，结果为空。
- `ffprobe public/course-samples/*.mp4` → 三段均为 `h264`、`960×540`、`24/1`、`yuv420p`、10.000 秒、无音轨；大小依次为 ai-video 1,280,521 B、image-motion 1,360,080 B、remotion 1,842,807 B；首/中/尾抽帧人工查看通过。
- `ffprobe public/course-samples/knowledge-card.webp` → `webp`，960×960。
- `cd frontend && pnpm check` → 退出码 0；ESLint 0 errors，TypeScript 通过，Prettier `All matched files use Prettier code style!`。
- `cd frontend && pnpm test` → `Test Files 245 passed (245)`，`Tests 1554 passed (1554)`。
- `cd frontend && pnpm test:e2e` → `55 passed`，退出码 0。
- `cd frontend && pnpm perf:check` → 退出码 0；五路由均在既有预算内，未修改 `performance-budgets.json`。
- `cd frontend && pnpm check:dead` → 退出码 0，无未使用导出。
- `node scripts/check_i18n_keys.mjs` → `588 个叶子全活`。
- 仓库根 `make check` → 退出码 0；backend ruff、frontend 静态/knip/i18n 与 docs convention 全过。
- `python3 scripts/check_docs_convention.py` → `OK 仓库治理约定全过`。
