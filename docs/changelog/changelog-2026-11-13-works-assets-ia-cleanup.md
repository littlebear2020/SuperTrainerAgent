# 我的作品 / 我的资产信息架构与卡片统一

> **状态**：实施与验收完成；PR #7 的 CI/OCR 已通过，符合 rebase merge 条件。
> **TL;DR**：移除页内重复作品/资产导航与标题，资产项目改为受控选择；统一成品、对话、项目卡片的 16:9 媒体区与等高底部动作布局；对话新增四种纯 CSS/Lucide Motif 和真实用户轮数。真实封面、路由、创建、分镜、详情、播放、下载、重试、删除与状态行为均保留。

## 完成内容

- 侧栏作为作品/资产唯一一级入口；页面按现有 `route.tab` 显示单一 h1，保留六能力 Tab、URL canonicalization、无关 query 与键盘 ARIA。
- `AssetsView` 仅在资产路由挂载，受控管理项目创建、打开、返回及离开后的列表重置；项目详情隐藏列表 CTA。
- 成品卡继续消费真实 `thumbnail_url`，项目卡保留锚图 → 风格预览 → 主色 fallback；三类卡片统一网格、16:9 媒体区、纵向 flex 与底部动作对齐。
- 四种对话能力分别使用文字行、海报、幻灯片、声波 Motif；仅统计真实 `type === "human"` 消息，缺省、空数组或无用户消息时不显示轮数。
- E2E 增加真实资产详情卸载重置及六能力 + 资产的双视口几何检查；为本地下载事件补充可控真实附件上游，Next 与上游使用 Playwright 独占的专用端口和进程，避免复用外部服务导致环境漂移；不改生产下载逻辑。

## 视觉复核

- 1440px：六能力与资产均为三列，无页面横向溢出；媒体区实测 16:9，同一行卡片等高，动作区底边对齐。
- 390px：六能力与资产均为单列，无页面横向溢出；媒体区实测 16:9，长标题、真实错误和不同操作数量保留自然行高差异。
- 14 张 E2E 截图已逐张复核；胶囊 Tab、Indigo、圆角、阴影、按钮与状态徽标沿用现有样式，未采用 demo 样式。

## 验收记录

- `cd frontend && pnpm exec vitest run --project dom tests/unit/components/workspace/video-gen/video-gen-page-url.dom.test.tsx tests/unit/components/workspace/video-gen/course-project-page.dom.test.tsx tests/unit/components/workspace/video-gen/completed-video-grid.dom.test.tsx tests/unit/components/workspace/video-gen/agent-conversation-grid.dom.test.tsx` → `Test Files 4 passed (4)`，`Tests 36 passed (36)`。
- `cd frontend && pnpm exec vitest run --project node tests/unit/components/workspace/video-gen/completed-video-grid.test.ts tests/unit/components/workspace/video-gen/course-project-cover.test.ts tests/unit/core/workspace/video-assets-route.test.ts` → `Test Files 3 passed (3)`，`Tests 16 passed (16)`。
- `cd frontend && pnpm exec playwright test tests/e2e/video-assets.spec.ts tests/e2e/work-capabilities.spec.ts` → rebase 到最新 `origin/main` 后 `23 passed (25.3s)`，包含真实播放、下载事件及全部状态行为。
- `cd frontend && pnpm exec playwright test tests/e2e/video-assets.spec.ts tests/e2e/work-capabilities.spec.ts -g "card geometry|project cards keep"` → `7 passed (3.1s)`，覆盖六能力与资产的 1440px/390px 几何断言和截图。
- `cd frontend && pnpm test` → `Test Files 263 passed (263)`，`Tests 1714 passed (1714)`。
- `cd frontend && pnpm check` → 退出 0；`0 errors`，`All matched files use Prettier code style!`（121 条存量 ESLint warning）。
- `make check` → 退出 0；后端 `All checks passed!`、`1317 files already formatted`，前端 knip 完成，rebase 后 `i18n key 存活检测通过：606 个叶子全活`，文档约定检查通过。
- `python3 scripts/check_docs_convention.py` → `OK 仓库治理约定全过`。
- `cd frontend && pnpm exec playwright test` → `97 passed (49.1s)`，`3 skipped`，完整 E2E 套件无失败。
- 3000 端口被无关 HTTP 服务占用时运行 `cd frontend && pnpm exec playwright test tests/e2e/video-assets.spec.ts -g "ready non-missing work can preview real media and download"` → `1 passed (6.1s)`，验证专用 Next/下载上游不受外部服务影响。
- 下载上游缺文件探针 → 修复前进程崩溃并出现 `curl: (52) Empty reply from server`；修复后 `HTTP 500` 且 `/health` 继续通过，随后双 E2E `23 passed (22.7s)`。
- `GH_REPO=littlebear2020/SuperTrainerAgent gh pr checks 7 --watch --interval 20` → `e2e-tests`、`frontend-unit-tests`、`lint-frontend`、`perf-budgets`、`quick-checks`、`review` 全部 `pass`；最新 OCR 仅 4 条 low，无 Critical/Important/medium。
