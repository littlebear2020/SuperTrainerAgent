# 我的作品真实交付文件中心

> **TL;DR**：六类作品统一为真实创作记录卡，Agent 对话通过批量摘要 API 展示交付文件，Remotion/ViMax 不再漏出作品墙；共享查看器保留下载、来源跳转、任务状态与确认操作；不改 DB/schema/runtime/worker，不部署。

## 变更摘要

- 新增 `POST /api/works/artifact-summaries`：认证 + `threads:read` + 全批次逐线程 owner 预检，原始 1–50 个 ID、trim/去重、顺序读取 checkpoint；仅返回 outputs 虚拟路径、名称、类型、大小、UTC mtime 与可读状态。非法/损坏 artifacts 整线程 `unknown`，不回显路径、不读目标文件；单文件 `OSError` 不拖垮同线程其他文件。
- 视频 ready 任务新增可选 `delivery`，`artifact_missing`、大小与 mtime 来自同一次 `stat`；非 ready 不下发陈旧交付信息，不新增宿主路径字段。旧后端无 `delivery` 时仍使用原下载 endpoint，文件名明确由下载响应确定。
- 前端以 `WorkSummary` 统一六能力：`course-video` 合并 Agent + `remotion_course/remotion`，`ai_video` 合并 `ai_video/vimax`，`ai_image` 包含 `ai_visual`；任务类型映射编译期穷尽。保留完整线程分页、50 条摘要分批且单 Query 内顺序读取、标题/文件名搜索、稳定排序、每次 6 条本地展示分页，移除旧 100 条截断。
- 真实封面/类型封面与共享 Dialog 查看器支持视频、图片、音频、文档和其他文件；列表不挂 Agent 视频/音频 src，图片 lazy，媒体只在打开后挂载、关闭卸载；多文件可切换，缺失与媒体错误诚实降级，同名文件不伪造版本。
- 统一卡片保留 ready/draft/failed/running/composing/qc_repair/pending/cancelled、详情、分镜、重试与删除确认。`cancelled` 显示「已取消」，中性且不旋转。刷新 pending 由本地状态 + 即时锁控制，使用 `try/finally` 复位；刷新失败保留最近成功作品，Dialog Esc 后焦点返回打开按钮。
- 删除旧 Agent/Completed grid 生产组件与三份旧测试；旧断言迁入模型、hooks、查看器和统一网格。共迁移 **18 个用例块（展开 22 条）**，**3 个块（展开 6 条）不再适用**：Motif 4 条与对话轮数 2 条。既有页面 URL 7 条、六能力/资产回归保留并适配统一查看器。

## 漂移与执行边界

- 以本地 `main@64ef5b52` 为基线，包含计划提交 `df5ebe47`；主 clone 的既有修改和未跟踪文件未 stash、clean、覆盖或提交。计划点名共享文件的漂移为已合入的 locale/E2E 扩展及视频轮询/错误提示，不改变本批接口。
- 未修改 `components/ui/**`、`components/ai-elements/**`、skills、seed agents、Docker、模板、config、鉴权中间件、runtime reducer 或 DB migration；未新增依赖，`config_version` 不涉及。
- 未部署、未访问服务器、未调用付费 API。后端真实行为验收仅使用 pytest 临时 SQLite DB、临时 checkpoint 和 outputs；前端 E2E 全部使用 hermetic mock。

## 验收记录

- Task 0：`git merge-base --is-ancestor df5ebe47 main` + `git log -1 --oneline -- docs/plans/2026-11-24-my-works-delivery-center-plan.md` → `df5ebe47 docs(works): 定稿我的作品交付文件中心计划…`；基线后端 `34 passed`，前端旧作品墙 `32 + 12 passed`。
- Task 1 RED：`PYTHONPATH=. uv run pytest tests/test_works_artifact_summaries.py -q` → `ModuleNotFoundError: app.gateway.works`；GREEN → `23 passed`，Ruff `All checks passed!`。
- Task 2 RED：`tests/test_works_router.py` → `ModuleNotFoundError: app.gateway.routers.works`；GREEN + 权限回归 → `62 passed`。
- Task 3 RED：`tests/test_video_gen_delivery_metadata.py` → 缺少 `delivery`，`9 failed`；GREEN + 视频/缩略图回归 → `16 passed`。
- Task 4 RED：works API/model → 两个模块均不存在；GREEN → `2 files / 29 tests passed`，`tsc --noEmit` 无错误。
- Task 5 RED：hooks → `@/core/works/hooks` 不存在；GREEN + 页面回归 → `2 files / 16 tests passed`；定向 ESLint/Prettier/tsc 通过。
- Task 6 RED：viewer → work-media 模块不存在；GREEN → `9 passed`，包含关闭卸载、媒体错误、缺失门控与作品往返切换重置。
- Task 7 RED：统一 grid 不存在，cancelled 仍为「排队中」且旋转；GREEN → DOM `3 files / 33 passed`、node `2 files / 36 passed`，页面 URL 另 `7 passed`。迁移统计 = 18 块/22 条迁移，3 块/6 条不再适用。
- Task 8 RED：页面新断言 `5 failed`（旧 grid 仍挂载），核心 E2E `8 failed / 1 passed`；GREEN → 页面与 grid `26 passed`、源码守卫 `2 passed`，六能力 + 交付 E2E `18 passed`。
- Task 9 真实后端行为：`tests/test_works_router.py::test_real_asgi_two_users_file_updates_and_50_thread_batch -q -s` → 双用户本人 200、跨用户 404、文件 size/mtime 更新均通过，`1 passed`；真实 50 线程一批 **`WORKS_BATCH_50_MS=69.125`**（本机量级记录，不设性能阈值）。
- Task 9 定向：`backend` 三份交付测试 → `45 passed`；`work-capabilities + works-delivery + video-assets` Playwright → **`38 passed`**，无意外 skip。
- 媒体断言反向验证：临时给列表挂视频 src、移除 lazy、允许 missing 缩略图 → WorkMedia 单测 `2 failed`（视频 src 与 lazy 均被捕获）；E2E 首屏请求收到 `movie.mp4`，lazy 属性缺失，missing 卡图片数 `1 != 0`。还原前后 `git diff | shasum -a 256` 均为 `094c8ab6…8c5bec8a3b`，`work-media.tsx` 无残留 diff；还原后对应单测/E2E 全绿。
- 后端全量首轮：`uv run pytest tests/ -q` → `10407 passed / 91 skipped / 6 failed`，6 个均为新增字段后 MagicMock 自动 `delivery` 属性；3 个 fake-task 工厂显式置空后相关 `21 passed`。复验全量 → **`10413 passed, 91 skipped, 421 warnings in 382.99s`**。
- 前端全量：`pnpm check && pnpm test` → ESLint `0 errors / 120 warnings`（既有 warning），tsc/Prettier 通过；Vitest **`306 files / 2156 tests passed`**。
- 全量 E2E：`pnpm test:e2e` → **`196 passed, 3 skipped (2.5m)`**；3 个 skip 为既有套件基线，本批必要用例零 skip。
- 性能：`pnpm perf:check` → exit 0；`/workspace/video-gen` **JS 3,594,815 B / CSS 255,242 B**，低于 4,100,000 / 300,000 预算，无需调整预算表。
- 根门禁：`make check` → exit 0；backend Ruff + format、frontend ESLint/tsc/Prettier/knip、i18n **707 个叶子全活**均通过；`python3 scripts/check_docs_convention.py` → `OK 仓库治理约定全过`。
- 视觉证据（Playwright 输出目录，评审后清理）：`frontend/test-results/work-capabilities-*/{course-video,ai_image,ai_video,creative-studio,ppt-master,podcast-studio}-{1440,1280,390,320}.png`；暗色英文查看器 `frontend/test-results/works-delivery-*/english-dark.png`。执行侧已抽看 course-video 1440/1280/390 与 english-dark，确认三列/单列、卡片比例、暗色 Dialog 与文案正常。

## 独立评审与最终复验

- 独立全分支评审：无 Critical；3 个 Important 均先 RED 后 GREEN：① A→A+B 摘要失败时从 QueryCache 取最近成功摘要、保留 A，手动刷新按线程刷新返回的新 ID 集 `fetchQuery` 并等待；② 共享 resolver 拒绝日志仅保留脱敏线程与原因类别，不再记录虚拟路径或异常原文；③ 无 delivery 的 unnamed legacy 输出在视频解码失败后尝试图片，GIF 可查看，二次失败才显示通用媒体错误。
- 修复回归：artifact 服务 `24 passed`；hooks `11 passed`、viewer `10 passed`；legacy GIF + 刷新失败浏览器用例 `2 passed`。另补无 delivery GIF 的真实浏览器图片解码断言。
- 后端最终全量：外部代理故障使既有 `test_ai_video_character_wiring` 的假 URL 每帧进行有界 60 秒重试（单文件最终自然 `2 passed`，约 369 秒）；使用仅测试进程的离线代理 `127.0.0.1:9`、localhost 直连，让非 hermetic 外呼立即失败，不跳过用例。`uv run pytest tests/ -q` → **`10414 passed, 91 skipped, 421 warnings in 256.33s`**。
- 前端最终全量：`pnpm check && pnpm test` → exit 0，Vitest **`306 files / 2159 tests passed`**。三组定向 Playwright → **`39 passed`**。
- 全量 Playwright 最终为 `192 passed / 5 failed / 3 skipped`：5 条全部是计划 Step 8 已预告的 `video-gen.spec.ts` `waitForLoadState("networkidle")` 超时。当前分支单独复跑仍 `5 failed`；临时 detached worktree 的本地 `main@64ef5b52` 同命令同样 **`5 failed`**，随后移除基线 worktree。原始失败与 main 对照均保留，本批 39 项必要 E2E 全绿，未修改无关模块。
- 最终性能复核：`pnpm perf:check` → exit 0；`/workspace/video-gen` **JS 3,595,698 B / CSS 255,242 B**，仍在既有预算内。

## 发布与回滚

- 发布顺序：先 additive backend，再 frontend；仅 gateway/frontend 涉及，无 DB migration，worker/remotion-worker/sandbox 不重建。本批未部署。
- 回滚：先 frontend 后 backend，通过 revert PR；不删除用户 outputs 或 checkpoint。
