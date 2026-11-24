# 核心产品前端可靠性 PR1：视频编辑安全

> **TL;DR**：AI 生图 / AI 生视频 / ai_visual 的帧面板在画面提示词或视觉来源未保存时禁用付费的「重生画面」（E5），cancelled 终态停止任务详情轮询（E4），帧视觉来源 / 版式 / 一键关闭图文镜三处乐观编辑失败后回滚并各提示一次真实错误（E7）。只改 `core/video-gen`、帧操作面板及其测试，后端、registry、认证零改动；Vitest 与 mock Playwright 验收，零付费调用。视频跨代轮询（E1）不在本批，未部署。计划：`docs/plans/2026-11-17-core-product-frontend-reliability-plan.md`（Task 0、Task 1）。

## 变更摘要

- 轮询：`videoTaskRefetchInterval` 对 `cancelled` 无条件返回 false。此前 cancelled 落到活动态分支，取消后的任务详情页每 2 秒永久轮询。ready/failed 的 legacy recompose grace 与其余活动态不变，详情页的 `recomposeGraceUntilRef` 保留。
- 付费前守卫：帧操作面板新增 `visualDirty`（画面提示词或视觉来源与已保存值不同），为真时「重生画面」禁用并以 title 提示先保存。类型门控 `supportsVisualDirtyGuard(videoType)` 只对 `ai_image` / `ai_video` / `ai_visual` 启用；与既有 `narrationDirty` 互不误伤（旁白未保存只挡「重生配音」），ViMax 与未传 `videoType` 的既有调用保持原按钮规则。
- 失败反馈：`useUpdateFrameVisualSource` / `useUpdateFrameLayout` / `useBulkDisableGraphic` 在各自 `onError` 先恢复 `ctx.prev`，再 `toast.error` 一次真实错误；调用组件（分镜编辑器、remotion_course 帧卡）不带 per-call `onError`，不会二次提示。
- 明确未做：不引入 `accepted-operation`、本地 operationId 轮询归属、sessionStorage 恢复记录；E1 旧 ready 停机与超长队列恢复由 `docs/plans/pending/2026-11-24-video-operation-generation-plan.md` 承接。

## 漂移与执行边界

- 基线：计划基线提交 `f44073a1` 已在 origin/main；执行分支 `video-gen-edit-safety` 自 main `73e97e68` 切出（`f44073a1..73e97e68` 为 10 个无关的 `*(usage)` 提交，backend 零变动）。Task 0 Step 2 契约复核：`GET /threads/{id}/runs` 不声明 `limit`、`list_by_thread` 默认 100；run-messages 首页最新、支持 `before_seq`；视频 mutation / list / detail 无操作代际。
- 全部实现位于隔离 worktree（原生 worktree 工具，`.claude/worktrees/video-gen-edit-safety`）；依赖 `pnpm install --offline --frozen-lockfile`。
- 改动文件：`frontend/src/core/video-gen/{hooks.ts,visual-dirty-guard.ts,hooks-invalidation.dom.test.tsx}`、`frontend/src/components/workspace/video-gen/video-gen-frame-ops-panel.tsx`、`frontend/tests/unit/core/video-gen/{video-task-refetch-interval.test.ts,visual-dirty-guard.test.ts}`、`frontend/tests/unit/components/workspace/video-gen/video-gen-frame-ops-panel.dom.test.tsx`、`frontend/tests/e2e/video-gen-edit-safety.spec.ts`、`frontend/tests/e2e/utils/mock-video-api.ts`，均在 PR1 的 allow 内。
- 未新增依赖、未改 `components/ui/**`、`components/ai-elements/**`、remotion/ViMax 专属实现；未部署、未调用付费 API、未跑 live 套件。

## 执行修正（计划文本与实现的差异）

- `supportsVisualDirtyGuard` 落在新文件 `core/video-gen/visual-dirty-guard.ts`（计划 Files 未列，但在 allow `core/video-gen/**` 内）：面板测试整体 mock `@/core/video-gen/hooks`，放进 hooks 会让既有的旁白守卫测试崩，且类型门控不是 hook。
- `recomposeGraceUntilRef` 实际在 `video-gen-detail.tsx`（`pollWhile` 的调用方），不在 `hooks.ts`；本批未改该文件，只用源码锁钉住「宽限仍在」。
- 三处乐观编辑的回滚（恢复 `ctx.prev`）旧实现已具备，缺的是提示；红阶段的失败项即 toast 0 次。
- E2E 的 ViMax 非目标断言：ViMax 详情页本就不渲染帧条与帧面板（`isSegmentedEngine` 排除 vimax），所以断言为「详情页无 `storyboard-strip-section` 与重生画面按钮」；组件级的 ViMax 按钮规则由 dom 测试覆盖。
- E2E 驱动细节：详情页帧条目是带 dnd-kit `aria-disabled` 的 div，SSR 首屏即出现但 `onClick` 要等 React 水合，用例先等到节点挂上 React props 再 force 点击；remotion_course 编辑器的帧卡不是 `#frame-card-N`，以首个「画面版式」切换器定位；running 态详情页不渲染帧条，轮询停机用例只认任务详情 GET。
- mock 的 regenerate 响应把该帧 `regen_state` 置为 running（对齐 202 异步受理语义）；任务详情 GET 记录响应时的 status，供「读到 cancelled 后不再有请求」断言。

## 验收记录

- 纵切 A RED：`cd frontend && pnpm exec vitest run tests/unit/core/video-gen/video-task-refetch-interval.test.ts` → `1 failed | 17 passed (18)`，失败项 `cancelled 立即停轮询，pollWhile 为真也不继续`：`AssertionError: expected 2000 to be false`；GREEN → `18 passed (18)`；`pnpm typecheck` → exit 0。
- 纵切 B RED：`pnpm exec vitest run src/core/video-gen/hooks-invalidation.dom.test.tsx tests/unit/components/workspace/video-gen/video-gen-frame-ops-panel.dom.test.tsx tests/unit/core/video-gen/visual-dirty-guard.test.ts` → `13 failed | 9 passed (22)`：面板 9 项 `expected false to be true`（「重生画面」未禁用）、回滚提示 3 项 `expected "vi.fn()" to be called 1 times, but got 0 times`、类型门控 `Cannot find package '@/core/video-gen/visual-dirty-guard'`；非目标 5 项（仅旁白未保存、仅提示词未保存时配音可点、vimax、不传 videoType、对照组）在旧实现上通过。GREEN → 含 video-gen 既有回归 9 文件 `81 passed (81)`；`pnpm typecheck` → exit 0。
- E2E 首跑：`pnpm exec playwright test tests/e2e/video-gen-edit-safety.spec.ts` → `2 passed | 6 failed`（均为定位与页面形态，见「执行修正」）；修定位后第二跑 `6 passed | 2 failed`（水合前点击丢失、remotion 帧卡 id）；第三跑 `8 passed (18.5s)`；稳定性 `--repeat-each=2` → `16 passed (21.9s)`。用例断言：三类任务未保存提示词 / 视觉来源时「重生画面」disabled 且 `api.regenerateRequests` 长度 0，全部恢复后点击恰 1 次 `POST …/frames/0/regenerate` `{ mode: "visual_only" }`；三处失败各 toast 文本 `toHaveCount(1)`、请求体确实带新值到达 mock、UI 弹回原值；running→cancelled 后 5 秒内任务详情 GET 计数不再增长。
- video-gen 既有回归：`pnpm exec vitest run tests/unit/core/video-gen tests/unit/components/workspace/video-gen src/core/video-gen src/components/workspace/video-gen` → `Test Files 93 passed (93)`、`Tests 608 passed (608)`。
- `make -C frontend check` → exit 0：ESLint `0 errors, 120 warnings`（存量 warning，与本批前一致）、`tsc --noEmit` 通过、prettier `All matched files use Prettier code style!`、knip 无死导出、i18n `665 个叶子全活`。
- 每个代码提交前 `pre-commit run --files <改动文件>` → eslint / prettier / gitleaks 全 Passed（本机未装 hook，手工执行）。

## 未验证项

- 未运行真实后端或付费生成；「零付费请求」以 mock 记录的 regenerate 请求计数证明，不是真实计费侧证据。
- E1（旧 ready 误停机、A/B 操作代际归属、超长队列恢复）未修复，去向 `docs/plans/pending/2026-11-24-video-operation-generation-plan.md`。
- 详情页「项目已更新，重新生成画面」入口不读帧面板未保存状态，本批未拦，已登记 findings backlog。
- 轮询停机只在 ai_image 的 running→cancelled 实测（5 秒观察窗）；其余类型与 failed 等 legacy grace 行为仅单测覆盖。
- 未部署，未跑 `tests/e2e/live/**`。

## 配置与回滚

- `config_version`：**不涉及**；无 schema、依赖或部署变更。
- 回滚 = revert 本分支的 `fix(video-gen)` / `test(e2e)` 提交，帧面板恢复为未保存也可重生、cancelled 继续轮询、乐观编辑失败静默回滚。
