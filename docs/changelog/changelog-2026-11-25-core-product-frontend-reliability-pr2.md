# 核心产品前端可靠性 PR2：输入受理契约与上传会话

> **TL;DR**：聊天提交改为以 SDK `onCreated`（线程与 run 已创建）为受理点——`sendMessage` 返回的 Promise 就是受理 Promise，受理前失败 / 停止 / busy 保留输入与草稿，受理后即使 SSE 仍开着输入才清空；错误提示集中在 hook、每次失败只提示一次。上传增加 120 秒空闲超时与跨线程取消；取消请求可等待且不再成为未处理拒绝；新建 Agent 页的输入改为受控。只改 `core/threads`、`core/uploads`、InputBox、三个页面与 `core/api/api-client.ts` 的兼容层，后端、registry、认证零改动；Vitest + mock Playwright 验收，零付费调用，未部署。计划：`docs/plans/2026-11-17-core-product-frontend-reliability-plan.md`（Task 2）。

## 变更摘要

- **受理 / 完成双生命周期**（新 `core/threads/submission.ts`，纯逻辑 node 档可测）：每次 submit 一个带递增 id 的 submission，持有 `accepted` 与 `completion` 两个只 settle 一次的 Promise。`accepted` 在上传完成且 SDK `onCreated` 后 resolve；busy、创建前失败、受理前停止以 `SendRejectedError(kind)` reject。`completion` 只在 hook 内部，由 SDK onFinish / onError / onStop 与取消结果驱动，永不 reject。
- **回调归属**：任一时刻最多一个 submission 收回调——无 meta 的回调归当前 submission，`onCreated` 之后按 run_id；`handleStreamStart`、错误提示、optimistic 清理只在归属当前 submission 时执行，不属于任何在途 submission 的回调（如 mount rejoin）走既有路径。busy 判定并入 `thread.isLoading`：SDK 队列未收尾时的提交按 busy 拒绝、不提示、不再入队。
- **调用方**：InputBox 把 `onSubmit` 的 Promise 原样交给 registry PromptInput（Provider 模式，resolve 后才清空）；两个 chat page 直接返回 `sendMessage` 的 Promise，auto-send 的 rejection 被观察并把提示词放回输入框；`agents/new` 的 PromptInput 包进 `PromptInputProvider`，bootstrap / 普通聊天 / 保存三个调用点都观察 rejection，保存失败只恢复状态、不重复 toast。InputBox 纯附件可发，空白正文且无附件不发送；模型自动校正分支只延迟一拍。
- **上传**：`uploadFilesWithProgress` 增加第四参 `{ signal, idleTimeoutMs }`——上传与等待响应共用 120 秒无网络进展即失败的空闲计时，每次上行 / 下行 progress 重置；外部取消中止 XHR。附件 URL fallback 转换也接收 signal。hook 用递增 upload operation + AbortController 管理上传所有者，切线程 / 卸载 / 受理前停止都会取消旧上传；`withAbort` 让转换 / 上传在取消时立即返回，不悬挂到实现自己结束。
- **URL 时机**：`onSend` 后移到上传完成、提交 run 前，只把预生成 UUID 写进地址栏，不提前翻 controlled threadId / isNewThread（那是 `onStart` 的事）；新增 `onSendError(requestedThreadId, { wasNewThread })`，新线程创建前失败 / 停止回退到 `chats/new`，已有线程保持原 URL，地址栏已不是本次线程时不回退。
- **可等待的取消**（`core/api/api-client.ts`）：cancel 包装永不 reject 并返回 `CancelOutcome`（成功 `cancelled`、终态 409 `not_cancellable` 并清 reconnect key、其余 `failed` 保留错误）；同一 runId 的并发取消合并为一次请求。hook 的停止路径在 `thread.stop()` 后 `await cancelRun`，结果写入 completion：受理记 `stopped`，失败记 `cancelFailed` 并 toast 一次真实错误（不伪造 interrupted，重试入口由 PR3 提供）；受理前停止尚无 runId，不发 cancel。
- **e2e 夹具**：`mock-api` 的线程创建、PATCH、token-usage、run stream 的 metadata 与 Content-Location 改为回显请求里的线程 id（与后端 `ThreadCreateRequest.thread_id`「Optional thread ID」一致），并暴露 `createdThreadIds`；`held-open-stream` 可在响应头回显 Content-Location 并立即 flush。

## 漂移与执行边界

- 分支 `chat-submission-acceptance` 基于 PR1 合入后的 main `831d2cad`；全部改动在 PR2 的 allow 内：`core/threads/{hooks,submission,errors}.ts`、`core/uploads/{api,prompt-input-files}.ts`、`components/workspace/input-box.tsx`、三个页面、`core/api/api-client.ts`、对应测试与 e2e 夹具。
- `core/api/api-client.ts` 的改动超出计划原文「仅 cancel 包装」：见「执行修正」第 1 条，计划文件范围与决策文本已同步。
- 未新增依赖、未改 `components/ui/**`、`components/ai-elements/**`、认证、QueryClient；未部署、未调用付费 API、未跑 live 套件。`config_version` 不涉及。

## 执行修正（计划文本与实现的差异）

1. **中止错误归一化**（e2e 实测发现）：SDK 自带的 AsyncCaller 只按 message 前缀识别中止（`AbortError` / `Cancel`），而 Chromium 对 `AbortController.abort()` 的拒绝消息是 `signal is aborted without reason`（name 才是 AbortError）。受理前停止时，已中止的 `threads.create` 被当成普通失败退避重试（默认 4 次、约 15 秒），`thread.isLoading` 迟迟不回落。compat 层通过 `callerOptions.fetch` 把中止 rejection 归一成 `AbortError` 前缀，重试立即结束。同一文件、同一兼容层、不涉及认证；TDD 红灯为「client 未注入 callerOptions.fetch」。
2. 上传中止（`core/uploads/api.ts` 的 signal 参数）与 onSend 时机、`onSendError` 的 URL 测试落在纵切 A 而不是 B：onSend 的新时机就在受理流程里，拆开无法单独成立；空闲超时仍在纵切 B。
3. 纵切 B 的「A 上传切 B」「URL fallback 转换中取消」放在 `stream-submission-lifecycle`（hook 层）而不是 InputBox 测试：它们验证的是 hook 的上传所有者，InputBox 测试里 `onSubmit` 只是替身。
4. completion 不对外暴露：通过可观察效果断言（toast 次数、cancel 调用、迟到回调不重复清理），不为测试新增公开选项。
5. 既有线程上的 run 结束后，SDK 用最新 history 头替换流值；共享 mock 的 history 是静态的，所以「切线程后 B 正常发送」以 run stream 响应 200 与输入清空为断言，不以回复文本为断言。
6. 创建失败用 422：SDK 对 5xx 会退避重试，422 才是即时失败。

## 验收记录

- 纵切 A RED：`cd frontend && pnpm exec vitest run tests/unit/core/threads/submission.test.ts tests/unit/core/threads/stream-submission-lifecycle.dom.test.tsx tests/unit/components/workspace/input-box-submission.dom.test.tsx tests/unit/app/workspace/agents/agent-chat-page.dom.test.tsx tests/unit/app/workspace/agents/new-agent-submission.dom.test.tsx tests/unit/app/workspace/chats/chat-page-autosend.dom.test.tsx tests/unit/core/threads/thread-submit-mode-context.dom.test.tsx` → `12 failed | 13 passed (25)`，另有 4 个文件因 `@/core/threads/submission` 不存在无法加载；失败项为 `sendMessage` 过早 resolve（`expected 'resolved' to be 'pending'`）、不 reject（`promise resolved "undefined" instead of rejecting`）、`api().stop is not a function`、`onSend is not a function`。GREEN → 连同 `input-box-stop-draft` `8 files / 47 passed`；周边回归（app / components / threads / uploads / tasks / messages）`159 files / 994 passed`；`pnpm typecheck` exit 0。
- 纵切 B RED：`pnpm exec vitest run tests/unit/core/uploads/api.dom.test.ts` → `2 failed | 3 passed`（空闲超时尚不存在）。变异验证：不装计时器 → 超时用例红灯（等待 reject 超时）；进展不重置计时 → 重置用例红灯（`Upload stalled: no network progress for 120s`）。GREEN → `uploads` `3 files / 15 passed`。e2e：`chat / agent-chat / agent-new-chat-guide / thread-history / gallery-auto-send / vimax-chat / chat-render-loop` 7 个 spec `68 tests → 67 passed`（1 条既有 skip）。
- 纵切 C RED：`pnpm exec vitest run tests/unit/core/api/api-client-cancel.dom.test.ts tests/unit/core/threads/stream-submission-lifecycle.dom.test.tsx` → `13 failed | 13 passed`：cancel 包装仍 reject（`promise rejected "Error: HTTP 500: boom" instead of resolving`）、`cancelRun is not a function`、hook 未发 cancel。GREEN → `27 passed`；回归 `27 files / 162 passed`。
- 中止归一化：同文件新增用例 RED `client 未注入 callerOptions.fetch: expected undefined to be type of 'function'` → GREEN `27 passed`。e2e 侧对照：归一化前受理前停止后 `thread.isLoading` 8 秒内不回落、重试的提交被当成停止；归一化后 `chat-submission` 的停止用例通过。
- 纵切 D：`pnpm exec playwright test tests/e2e/chat-submission.spec.ts --repeat-each=2` → `10 passed (10.3s)`。用例实际经过：受理前停止（创建挂起时点停止 → 地址栏回退 `/chats/new`、草稿保留、无 toast；放行后重试成功）；受理后长流（真实 SSE 服务保持连接并回显 Content-Location：`onCreated` 后输入清空、流式期间的新草稿在流结束后不被清空）；创建前失败（toast 恰好一次、草稿保留、回退；重试成功后不再提示）；纯附件与新会话首条附件消息（上传用的就是随后被 create 回显的线程 id）；A 上传切 B（A 的上传 `net::ERR_ABORTED`、B 无上传态、B 的 run 正常发起）。全部用例断言 `pageerror` 为空。
- 每个代码提交前 `pre-commit run --files <改动文件>` → eslint / prettier / gitleaks 全 Passed。
- `make -C frontend check` → exit 0：ESLint `0 errors, 120 warnings`（存量 warning，与 PR1 前一致）、`tsc --noEmit` 通过、prettier `All matched files use Prettier code style!`、knip 无死导出、i18n `665 个叶子全活`。全量 `cd frontend && pnpm test` → `Test Files 292 passed (292)`、`Tests 1967 passed (1967)`。

## 未验证项

- 未接真实后端或付费模型：受理时序、取消与上传取消均由 Playwright mock 与可控 SSE 服务证明；真实 SSE 的响应头形态（Content-Location）只按 SDK 源码与 mock 对齐。
- 线程创建返回 5xx 时 SDK 会退避重试约 15 秒后才提示，本批未改 SDK 重试策略，也未在 UI 上缩短该窗口。
- 取消失败后的重试入口、`running→interrupted` 的有界同步由 PR3 提供；本批只保证取消结果可等待、失败提示一次、不伪造终态。
- 上传空闲超时只在 vitest 用假 XHR 与假计时器验证 120 秒边界，未在真实慢网络下实测。
- 未部署，未跑 `tests/e2e/live/**`。

## 配置与回滚

- `config_version`：**不涉及**；无 schema、依赖或部署变更。
- 回滚 = revert 本分支的 `fix(chat)` / `fix(uploads)` / `test(e2e)` 提交：输入框回到提交即清空、受理前失败丢草稿、取消失败成为未处理拒绝、上传无超时。
