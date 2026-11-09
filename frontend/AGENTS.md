# Frontend Conventions

前端 = Next.js App Router + React + TypeScript + Tailwind 4 + pnpm（具体版本以 `package.json` 为准）。

## Commands

```bash
pnpm dev          # Dev server（Turbopack，http://localhost:3000）
pnpm build        # Production build
pnpm check        # Lint + type check + prettier --check（提交前必跑；2026-10-08 CI 优化批 A1 扩语义）
pnpm lint:fix     # ESLint 自动修
pnpm test         # 单元测试（Vitest 双 project：node 纯逻辑 + dom happy-dom；tests/unit/ 镜像 src/ 布局，用 @/ 别名引源码；快循环 `vitest run --project node`）
pnpm test:e2e     # E2E（Playwright Chromium，page.route() mock 全部后端 API）
pnpm typecheck    # tsc --noEmit
pnpm check:dead   # knip 死导出/死依赖检测（CI 挂；knip.json 配置）
pnpm perf:check   # 路由资产预算门禁（build→start→测五路由 JS/CSS 字节比对 performance-budgets.json；CI 独立 job）
```

## 架构要点

- 路由：`/` landing、`/(auth)` 登录/初始化、`/workspace/*` 主工作区（chats/agents/console/gallery/assets/video-gen/writer/twins/voice）、`/share/*` 公开分享；全集以 `src/app/**/page.tsx` 为准。
- 状态：TanStack Query 管 server state；thread hooks（`core/threads/hooks.ts`：`useThreadStream`/`useSubmitThread`/`useThreads`）是主 API 接口；LangGraph client 单例 `getAPIClient()`（`core/api/`）。
- Server Components 默认，`"use client"` 只给交互组件。
- **vitest 分层（2026-10-17 PR-1）**：双 project——node（默认档，纯逻辑 `*.test.*`）+ dom（happy-dom，`*.dom.test.*` 中缀；需 document / renderHook / 组件渲染者落 dom 档，不 render 的不得进——DOM 环境约 3x 开销）；inline project 不自动继承根配置，vitest.config.ts 内显式 `extends: true` 拿 alias/coverage。
- **路由资产预算（2026-10-17 PR-1）**：`pnpm perf:check` 测 `/`、`/workspace/chats`、`/workspace/chats/[id]`、`/workspace/video-gen`、`/workspace/writer` 五路由的 JS/CSS 静态资产字节，比对 `performance-budgets.json`（初值 = 干净 HEAD 实测 ×1.15 向上取整到 50,000 字节）。超预算 = PR 说明写归因 + 预算表随 PR 评审调整，**不得无声抬线**；测量明细落 `.next/performance-results.json`。
- **streamdown 渲染层（2026-10-17 PR-2）**：streamdown 2.5 + `@streamdown/code`（shiki 插件化高亮）/`@streamdown/mermaid`；消息渲染统一走 `MarkdownContent`（content+isLoading 契约）或 `core/streamdown` 的 SafeStreamdown 系（防崩壳 + 深嵌套封顶 + 泄漏系统标签剥离）；上游 `ai-elements/streamdown.tsx` 的 ClipboardSafeStreamdown 等效件按规划侧拍板落 `core/streamdown/clipboard-safe-streamdown.tsx`（**不落 registry 禁区**），剪贴板兜底走 `core/streamdown/clipboard-fallback.ts` 的内联 textarea/execCommand 路径（**不可委托 `lib/utils` 的 `copyToClipboard`**——其 `if (navigator.clipboard)` 首查会与补丁互指成无限递归，评审 critical 订正）。词级动画为 streamdown 原生 `animated` prop（自研 rehype 词动画已随 PR-2 退役）。
- 环境校验：`@t3-oss/env-nextjs` + Zod（`src/env.js`），`SKIP_ENV_VALIDATION=1` 跳过。
- 职责归属（改动前先认准 owner）：composer busy 态接线 = `src/app/workspace/chats/[thread_id]/page.tsx`；提交前上传态与 thread 提交 = `src/core/threads/hooks.ts`。
- **Radix 依赖口径（2026-10-05 C6 定案）**：只用单包 `@radix-ui/react-*`（27 文件引用面），元包 `radix-ui` 已移除（零引用）；`pnpm.overrides` 硬钉 `@radix-ui/react-dismissable-layer@1.1.13` 的原因 = **P1-5 合帧接线事故**（`radix-dismissable-layer-duplicate-copies`：新版 dismissable-layer 多副本致 Dialog 内浮层 pointer-events 失效），升级前必须先清偿该记忆条目所述问题。JSON 无法承载注释，原因记此处。
- **react-hooks ratchet（2026-10-17 PR-1）**：7 条规则（v6 编译器 6 条 + `exhaustive-deps`）全 error 档；存量 100 处收编进根目录 `eslint-suppressions.json`（set-state-in-effect 56 / refs 35 / globals 7 / immutability 2；exhaustive-deps、purity、preserve-manual-memoization 已随 PR-1 机械修清零）。suppressions 是 **CLI-only** 机制：文件在 frontend/ 根即被 eslint 自动加载（config 无「引用」项）、只收编 error 档条目；新增命中 = 新 error 直接红；清偿存量后必须跑 `pnpm exec eslint . --ext .ts,.tsx --prune-suppressions` 收尾（残留不再出现的条目会 exit 2 炸门禁）。

## 代码风格

- import 排序强制：builtin → external → internal → parent → sibling，组间空行、字母序；类型用 inline import（`import { type Foo }`）。
- 未用变量前缀 `_`。
- 条件类名用 `cn()`（`@/lib/utils`）。
- 路径别名 `@/*` → `src/*`。
- `components/ui/` 与 `components/ai-elements/` 是 registry 生成物（ESLint 已豁免）——**勿手改**，见根 `AGENTS.md`「边界与禁区」。
- **registry 移除登记（2026-10-06，一次性决策性移除，registry sync 不得回加）**：`ai-elements/` 12 件（canvas/checkpoint/connection/controls/edge/image/node/open-in-chat/panel/plan/toolbar/web-preview）+ `ui/` 7 件（avatar/carousel/number-ticker/sonner/spotlight-card/word-rotate/magic-bento 含 magic-bento.css）。全部为 2026-05-05 上游整包导入（`5cbe28b99`）带入的零引用死件，随件移除的依赖：`@xyflow/react`/`embla-carousel-react`/`@radix-ui/react-avatar`/`gsap`(+`@types/gsap`)；`sonner` 库保留（业务直用）。**追记（2026-10-06 第 5 批）**：knip 接入时再证死 `ai-elements/context`（连带 `ui/progress`）与 6 个零用依赖（`@radix-ui/react-icons`/`@radix-ui/react-progress`/`@tiptap/extension-link`/`best-effort-json-parser`/`rough-notation`/`tokenlens`），同口径移除——registry sync 不得回加。**追记（2026-10-17 PR-2，死导出登记非整件移除）**：`ai-elements/message.tsx` 的 `MessageResponse` 导出随 streamdown 2.5 渲染层升级（消息渲染改走 `core/streamdown/components.tsx` 的 SafeMessageResponse）沦为零引用——**文件本身保留**（`Message`/`MessageContent`/`MessageToolbar` 仍被 message-list-item 使用，不手改 registry 生成物），registry sync 不得回加对 `MessageResponse` 的引用；下次上游同步 message.tsx 时可顺手断根。**未来退路**（2026-10-06 与用户确认）：若有 node 图/编排画布需求（如 agent 委派可视化），重装 `@xyflow/react` + 最新 AI Elements canvas 套件即可。

- **CSV 表格预览（2026-10-17 PR-3；TSV 待 files.tsx 补 tsv 映射后启用）**：`papaparse` **只准经 `core/artifacts/delimited-preview.ts` 进入（该模块仅被 `delimited-preview.worker.ts` 引用，worker-only，防进主包——主包代码不得 import 该两件）**；解析上限 202 记录/50 列 + 前端 1MiB 裁剪 + 5s 超时在 `use-delimited-preview.ts`；表格预览 UI 文案暂为组件内中文常量（i18n 化留后续批次）。子任务步骤时间线（`core/tasks/steps.ts`）为 live-only：合并必须走 `mergeSteps`（禁 spread），无历史回填（刷新后退回终态是既定行为，非回退）；后端 ai_messages 只收 AIMessage，Wrench 工具行 live 暂不可达（休眠分支，后端补 emit 即活）。

## 环境变量

`NEXT_PUBLIC_BACKEND_BASE_URL` / `NEXT_PUBLIC_LANGGRAPH_BASE_URL` 均可不设——默认走 nginx 反代（`/api/langgraph/*` → gateway:8001，其余 API → gateway，页面 → frontend:3000）。仅调试直连场景才设。

## 模块详情

模块细节（路由分区/视频工作台组件族/状态管理模式）按需查 `docs/FRONTEND_MODULE_NOTES.md`（镜像 backend/AGENTS.md 末节形态；本文件保持约定与坑）。

<!-- BEGIN:nextjs-agent-rules -->

# This is NOT the Next.js you know

This version has breaking changes — APIs, conventions, and file structure may all differ from your training data. Read the relevant guide in `node_modules/next/dist/docs/` (resolved from this file's directory; in monorepos the `next` package may not be visible from the repo root) before writing any code. Heed deprecation notices.

This block is written and re-added by `next dev` — verify at `node_modules/next/dist/server/lib/generate-agent-files.js`. Removing it from a diff only re-creates the uncommitted change; committing it with your work keeps the tree clean.

<!-- END:nextjs-agent-rules -->
