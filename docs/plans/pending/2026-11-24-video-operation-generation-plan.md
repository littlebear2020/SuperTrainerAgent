# 视频操作代际与长队列恢复 Implementation Plan

> **状态**：未执行（拆分自 `docs/plans/2026-11-17-core-product-frontend-reliability-plan.md` 的视频 accepted-operation / 跨代轮询部分，拆分时原计划进度：未执行；定稿日 2026-11-24；基准 commit `ab92a52797fbe6b2487c9e3687990c8da79b6002`（拆分时的 HEAD；执行前先 `git diff <基准> --stat` 评估漂移面））。主计划不因本计划而延迟关闭。
> **TL;DR**：为视频任务补服务端操作代际，解决 E1 旧 ready 误停机、A/B 操作归属与超长队列恢复。后端先出契约，前端 accepted store 只在契约落地后设计。本件目前只有设计约束与必须覆盖的反例，任务拆解留到执行前补齐；含后端改动，不随纯前端可靠性批次执行。

**Goal：** 让 AI 生图 / AI 生视频 / ai_visual 的任务轮询按服务端操作代际可靠结案：旧操作的 ready 不误停新操作，快速终态不永轮询，刷新后可恢复；remotion_course / ViMax 的现有轮询保护不变。

**Architecture：** mutation 受理时由服务端生成并返回代际，任务详情与列表回传同一代际，前端以代际而非本地状态或客户端时间判断归属。纯前端无法同时证明“旧 ready 不误完成”和“快速终态不永轮询”，所以后端契约先行。

**Tech Stack：** 沿用现有 Gateway 视频任务接口与 `frontend/src/core/video-gen`；其余待任务拆解时确定。

**Spec / 证据来源**：主计划「明确不做」中 E1 的结论与三次契约复核；`docs/2026-11-16-live-e2e-bug-handoff.md` E 系列（按月归档后位于 `docs/archive/2026-11/`）。

## 设计约束（执行时必须全部满足，或在本计划显式修订）

- **后端契约：** mutation 受理时写入并返回服务端 operationId/generation/version，任务详情与列表回传同一代际；属于当前代际的 ready/failed/cancelled 可直接结案，不要求前端曾观察到活动态。不能仅返回本地 operationId 或用客户端 acceptedAt 与服务器 updated_at 比较。
- **前端接入：** 在新契约落地后再设计 accepted store；受理前于 onMutate 捕获不可变快照，详情缓存缺失时回退列表同 id 项。若增加 sessionStorage，记录必须带 ownerUserId 并严格匹配恢复，不因此恢复全局认证清理。
- **非目标保护：** 只有 ai_image/ai_video/ai_visual 删除 legacy grace；remotion_course/ViMax 保留原轮询。ready 终态和缩略图版本按本次服务端 generation 刷新，不能只依赖 composing→ready 边沿。
- **必须有的反例：** ready→直接新 ready/failed（中间无活动态）、A/B 连续操作且 A 迟到、刷新恢复 seenActive=false、仅列表缓存入口、仅编辑导致 updated_at 前进、旧 cancelled 不清新操作。
- **原事故入口矩阵：** `useRecomposeTask/useReRenderTask/useRenderFromDraft` 各测 mark/invalidate；音频自动 recompose、详情 recompose、预览转正式、列表 retry、分镜 retry，以及详情/分镜 re-render（含项目更新入口）均有真实接线行为用例。列表提示落在现役 CompletedVideoGrid，不只修改无生产消费者的 TaskCard。
- **夹具先行：** 须把 `frontend/tests/e2e/utils/mock-video-api.ts` 列为正式改动，支持严格推进 updated_at/generation、延迟旧响应、跳过活动态和直接终态；测试不得伪造生产 API 没有的字段。

## 执行前待定的设计问题

来源是 2026-11-24 的外部评审，尚未对视频管线代码核实，拆解时逐条回答，结论写回上面的「设计约束」。

- **受理原子性**：mutation 是否在同一事务写入新 generation 与 pending/running 状态，避免把新 generation 贴在旧 ready 上，或直接写成 worker 可立即认领的 composing。
- **写入 fencing**：generation 是否贯穿 Gateway、worker、取消与 reaper；状态、结果与认领是否按 generation 条件更新，旧代写入是否失效。
- **产物隔离**：新旧 worker 是否会写同一固定 MP4/缩略图路径；是否需要代际临时目录加当前代原子发布，或以单活跃锁证明两次操作的写入不并存。
- **决定性测试**：A 延迟到 B ready 后才结束，最终数据库状态、result、实际 MP4 与缩略图都属于 B；旧 cancelled 不能覆盖新代。

## 执行前待补

- [ ] 回答上述待定设计问题，过 grill 前置，补齐 allow/ban 三栏、改动清单与验证清单；将本文件移回 `docs/plans/` 并在本头部标注执行日期与 changelog 链接。
