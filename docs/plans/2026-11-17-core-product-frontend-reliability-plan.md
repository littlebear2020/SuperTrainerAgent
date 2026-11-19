# 核心产品前端可靠性优化 Implementation Plan

> **状态**：未执行（2026-11-17 定稿并根据 Trae 评审逐项复核修订；范围、5 个串行 PR、验收与部署边界均已确认；不授权部署）。
> **TL;DR**：只覆盖 course-video、ppt-master、creative-studio、podcast-studio、AI 生图/AI 生视频、课程资产、我的作品和运营后台；按输入上传→线程恢复→产物引用→账号隔离→视频任务分 5 个串行 PR，严格前端改动，以 mock 行为验收为主，必要时允许最小付费 API 验证。

> **For agentic workers：** 推荐 `superpowers:executing-plans` 在当前会话顺序执行；若用户另行选择，才使用 `superpowers:subagent-driven-development`。执行前用 `superpowers:using-git-worktrees` 建立隔离工作区，按 checkbox 逐项勾选，不得并行派发这 5 个 PR。本计划不授权自动 push、自动合并或部署，执行时的提交与 PR 按仓库 Git 约定另行确认。

**Goal：** 消除现役核心产品前端中会导致草稿丢失、聊天/产物不可用、跨账号数据暴露、旧参数付费生成以及长任务结果无法恢复的问题。

**Architecture：** 不重写页面，围绕五个共享边界做小步修复：受理与后台运行分离的提交事务、带代际和真实 run 状态的线程恢复、结构化 write-file 引用、认证代际 QueryClient 与统一 unauthorized 清理、带操作身份和类型门控的视频已受理状态。每个 PR 独立完成 red→green→行为验收，后一批基于合入后的 main。

**Tech Stack：** Next.js 16、React 19、TypeScript、TanStack Query、LangGraph SDK、Vitest（node + happy-dom）、Playwright（mock API）；不新增依赖。

**Spec：** `docs/2026-11-16-live-e2e-bug-handoff.md` 提供缺陷线索；本计划的「评审决策记录」和「评审复核记录」是最终范围与实现决策的单一事实源，原缺陷清单及外部评审中已证伪或超出当前产品边界的项目不得回流。

## 评审决策记录

- **产品范围**：仅包含 course-video、ppt-master、creative-studio、podcast-studio 四类 Agent 的共用聊天体验，AI 生图/AI 生视频两条 video-gen 管线，以及课程资产、我的作品、我的用量（运营后台）和其必需的登录/登出状态。
- **严格前端**：不修改 Gateway、worker、Harness、Docker、compose、config schema、技能或预置 Agent。若探针证明纯前端无法闭环，停止对应任务并另写后端计划，不在本批夹带。
- **5 个串行 PR**：PR1 输入与上传；PR2 长任务线程恢复；PR3 产物引用；PR4 私有状态隔离；PR5 AI 生图/AI 生视频。输入与线程共享提交 Promise、onSend/onStart、threads hooks 和两个 page，必须相邻执行；后一批在前一批合入后 rebase。
- **执行方式**：推荐当前会话顺序执行，不为每批重复建立共享上下文；不拆给多个子代理并行执行。
- **验收边界**：Vitest + mock Playwright 为默认；只有真实 SSE/队列/浏览器行为无法由 mock 替代时才允许最小付费 API 验证，不循环烧 token、不跑全量付费套件、不部署。
- **兼容现有数据**：保留历史线程、作品、课程资产、草稿和合法已有产物引用；不清库、不做数据迁移。普通查询缓存可以在登出/401 时清除，这不等于删除服务端数据。
- **确认卡不纳入**：现役 course-video 仅绑定 `web/file:read/file:write/bash`，没有 `video_engine`；后端 `plan_storyboard` 当前只服务 ViMax。本批不修、不删共用确认卡，也不纳入 C2/C4/C5/C11。
- **已证伪项不回流**：B3（消息内已有下载卡）、B6（创建框关闭即卸载）、B8（已有 SSR 鉴权）、P0-3（SDK 并未按原文所述必然 clear 首流）不安排修复。
- **低优先项不夹带**：D3 导出、反馈按钮、document.title、Token 瞬时旧值、C8/C9、E6 死 UI、E7 资产重试按钮均不在本批。
- **findings backlog 复核**：沙箱密钥授权与诊断继续由 `docs/plans/2026-11-16-agent-media-channel-and-sandbox-secrets-plan.md` 承接；其余 backlog 项属于 ViMax、voice、模型探针或非本批范围，全部显式推迟。

## 评审复核记录

- **PR1 Promise 时序——部分采纳，纠正评审原因**：当前 SDK `submit()` 内的 `await stream.start()` 并不等待流结束；`StreamManager.start()` 只接入队列便返回。因此问题是**过早 resolve、无法代表受理成功**，不是长任务期间输入一直不能清空。采用受理 Promise + 后台运行生命周期，以 `onCreated` 为受理点，防止创建前失败或停止后发生错误清空。
- **401 清理——采纳**：`AuthProvider.refreshUser`、上传 XHR、LangGraph SDK 都绕过共享 fetcher。四处统一 single-flight unauthorized 处理；allow/Files 增加上传 API 和 SDK client。`writer_draft_` 只纳入中央清理前缀，不修改 writer 功能。
- **迟到 mutation——采纳测试，调整实现**：当前 TanStack Mutation 没有可统一等待的公开 `mutation.promise`。不等待旧 mutation，也不只 `clear()`；改为认证代际 QueryClient，reset 切换新实例，旧回调只能写入废弃实例。
- **线程终态——采纳**：不再把非流式等同于失败，使用 runStatus、interrupt、history loading、可恢复断线联合判定。后端实际状态为 `pending/running/success/error/timeout/interrupted`，没有独立 `cancelled` 字符串；`interrupted` 必须结合 `thread.interrupt` 区分等待澄清与取消。
- **history 幂等——采纳**：先按 run_id 记录已加载分片，再允许 onFinish 失效 runs；同一 run 重取不重复，新 run 能加载且分页不回退。
- **accepted-operation——采纳并修正时间比较**：增加 operationId、任务类型白名单、baselineUpdatedAt、sessionStorage 同标签页恢复和等待反馈。acceptedAt 只用于提示/降频，不与服务器 updated_at 直接比较；响应归属以 operationId + baselineUpdatedAt 判定。
- **write-file 状态与 Hook——采纳**：区分 invalid/pending/not_found/ready，合法空文件也是 ready；auto-open 放进独立、无条件渲染的 `WriteFileToolCall` 组件顶层，禁止条件分支内新增 Hook。
- **总验收与顺序——采纳**：增加 live 用例机械排除检查；输入与线程批次相邻，其余顺序为产物→账号→视频。

## 二次评审复核记录

- **sendMessage 公共接口——采纳最小兼容方案**：`agents/new/page.tsx` 另有三处 `await sendMessage`，若返回对象会破坏现有 await/catch。保持 `sendMessage(...): Promise<void>`，返回的 Promise 本身就是 accepted；completion 完全由 hook 内部管理，新建 Agent 文件无需修改。fire-and-forget auto-send 显式处理 accepted rejection。
- **同一 run 最终消息——采纳**：loadedRunIds 只控制旧 run 分页；当前 run 在 onFinish 后必须允许重取，并按 run_id 原子替换消息分片，不能因 loaded 跳过最终新增消息。
- **dirty guard 类型门控——采纳**：ViMax 仍走普通 FrameOpsPanel，visualDirty 仅对 `ai_image/ai_video/ai_visual` 生效，补 ViMax 原行为回归。
- **accepted store 的 logout 内存清理——采纳**：PR4 增加通用 `subscribePrivateStateReset(listener)`，PR5 注册 `clearAllVideoOperations()`；认证层不反向依赖 video-gen。
- **live 检查失败语义——采纳**：先捕获 `playwright test --list` 输出并显式检查退出码，再搜索 live 路径；列表生成失败直接阻断。

## Global Constraints

- **禁止手改 registry**：`frontend/src/components/ui/**`、`frontend/src/components/ai-elements/**` 只读；输入清空契约必须在自有 `InputBox`/threads 封装层适配。
- **不改 live 测试工作**：`frontend/tests/e2e/live/**` 与 `frontend/playwright.live.config.ts` 保持原样；新增回归只放 `frontend/tests/unit/` 或 `frontend/tests/e2e/`。
- **避开其他未提交工作**：执行前用独立 worktree 从确认后的 main 开始；不得把当前 `backend/seed/**`、`workspace-home.tsx`、`workspace-nav-menu.tsx`、`frontend/playwright.config.ts` 等未提交改动带入本批。
- **TDD**：每个实现任务先写并运行失败测试，再写最小实现使其通过；不得用纯 import/类型检查冒充行为验证。
- **测试落位**：纯逻辑放 node 档 `.test.ts`；需要 document/renderHook/组件渲染的放 dom 档 `.dom.test.*`；Playwright 只放 `tests/e2e/`。既有 `src/**/*.test.*` 内联测试沿用原位置，新测试默认放 `tests/unit/`。
- **PR 纪律**：每批一个主题分支；执行 `pnpm check` 和相关测试后才可进入 PR 评审。前一批合入后，下一批 rebase 新 main。
- **文档纪律**：每批按实际执行日创建 `docs/changelog/changelog-YYYY-MM-DD-core-product-frontend-reliability-pr<N>.md`，包含「验收记录」；回本计划勾选步骤并维护唯一状态头及 changelog 链接。
- **长命令**：全量测试、构建和任何真实长任务一律后台运行；后台运行期间冻结被测代码和测试输入。
- **回退**：每批只依赖前端代码，无数据库或配置迁移；发现问题按 PR revert，不以新增第二层补丁掩盖根因。

## Review Focus

- **受理而非排队时清空**：onCreated 前失败/停止保留输入，onCreated 后即使 SSE 保持打开也可清空；后续停止/断线不得再次控制 composer → PR1 真实 SDK + 可控 SSE 测试。
- **迟到写操作的所有者**：旧上传、旧线程历史、旧 mutation、旧视频操作响应不得写入新一代状态 → PR1/PR2/PR4/PR5 分别验证 operation/generation/client 代际。
- **非终态不误报失败**：断线、history loading、interrupt、正常 tool-result 间隙都不得显示“运行已结束” → PR2 反例测试。
- **合法等待与合法空内容**：长队列要降频且解释，合法空文件不能被当成尚未到达 → PR5 等待反馈与 PR3 ready 状态测试。
- **非目标行为不变**：remotion_course/ViMax 不进入新 accepted-operation；live/付费用例不得进入默认全量 mock 套件 → PR5 类型门控测试与总验收机械检查。

## 文件范围

| 批次 | 允许路径（allow） | 禁改路径（ban） | 共享基座触达 |
|---|---|---|---|
| PR1 输入与上传 | `frontend/src/components/workspace/input-box.tsx`；两个 chat page；`frontend/src/core/{threads,uploads}/**`；对应 tests | registry、writer/gallery/voice、live 测试、backend | 是，`core/threads/hooks.ts`，串行独占 |
| PR2 线程恢复 | `frontend/src/core/{threads,tasks}/**`；`frontend/src/components/workspace/{chats,messages}/**`；两个 chat page；对应 tests | registry、writer/gallery/voice、live 测试、backend | 是，`core/threads/hooks.ts`，串行独占 |
| PR3 产物引用 | `frontend/src/core/artifacts/**`；`frontend/src/components/workspace/{messages,artifacts}/**`；对应 tests | registry、writer/gallery/voice、backend | 是，`core/artifacts/{hooks,loader}.ts`，串行独占 |
| PR4 私有状态隔离 | `frontend/src/core/{auth,query,console,api,uploads}/**`；`frontend/src/components/query-client-provider.tsx`；对应 tests | writer/gallery/voice 功能代码、backend | 是，QueryClient 代际、fetcher、SDK client，串行独占 |
| PR5 视频任务 | `frontend/src/core/video-gen/**`；`frontend/src/components/workspace/video-gen/**`；对应 tests | remotion/ViMax 专属组件、backend、config | 是，`core/video-gen/hooks.ts`，串行独占 |

---

### Task 1（PR1）：输入受理契约与上传会话

**Files：**
- Modify：`frontend/src/components/workspace/input-box.tsx`
- Modify：`frontend/src/app/workspace/chats/[thread_id]/page.tsx`
- Modify：`frontend/src/app/workspace/agents/[agent_name]/chats/[thread_id]/page.tsx`
- Modify：`frontend/src/core/threads/errors.ts`
- Modify：`frontend/src/core/threads/hooks.ts`
- Modify：`frontend/src/core/uploads/api.ts`
- Test：`frontend/tests/unit/components/workspace/input-box-submission.dom.test.tsx`（新增）
- Test：`frontend/tests/unit/core/threads/stream-submission-lifecycle.dom.test.tsx`（新增）
- Test：`frontend/tests/unit/core/uploads/api.dom.test.ts`（新增）
- Test：`frontend/tests/unit/app/workspace/agents/agent-chat-page.dom.test.tsx`
- Test：`frontend/tests/unit/app/workspace/agents/new-agent-submission.dom.test.tsx`（新增；只验证公共接口兼容，不修改新建 Agent 功能）
- Test：`frontend/tests/e2e/chat-submission.spec.ts`（新增）

**Interfaces：**
- `sendMessage(...)`: 保持公开返回类型 `Promise<void>`，返回值本身就是 accepted Promise；不再把 `thread.submit()` 的返回值当作受理或后台结束，也不向调用方暴露对象。
- `accepted`: 上传完成、SDK `onCreated` 后 resolve；busy、上传/创建前失败、受理前停止则 reject。InputBox 与新建 Agent 的现有 await/catch 都等待这一结果。
- `completion`: 仅由 hook 内部持有，由 SDK onFinish/onError 驱动后台状态、toast 和清理；不进入公共返回类型，不控制 composer，且必须有内部 rejection 处理。
- `uploadFilesWithProgress(threadId, files, onProgress?, options?: { signal?: AbortSignal; idleTimeoutMs?: number })`: 第四参可选；默认 120 秒无网络进展即失败，上传与等待响应共用空闲超时，用户随时可取消。

- [ ] **Step 1：先写提交与上传失败测试。** 覆盖：①有正文和附件时受理前停止，controller 文本、附件和 `draft:{tid}` 均保留；②创建前失败保留；③纯附件会发送；④空白正文且无附件不发送；⑤busy 有提示且保留；⑥模型自动校正分支不提前 resolve；⑦A 上传切到 B，B 不显示进度/不被禁用，A 的 XHR abort；⑧无网络进展 120 秒 reject，持续进展会重置计时。另用新建 Agent 保存流程验证现有 `await sendMessage/catch` 无需修改，受理失败不会显示“已请求保存”；auto-send 的 accepted rejection 有被观察处理。
- [ ] **Step 2：增加真实 SDK + 可控 SSE 时序测试。** 用现有 controllable stream/fake client 验证：调用 `thread.submit()` 本身不会等待流结束；只有 `onCreated` 才 resolve accepted；onCreated 后 SSE 保持打开时 composer 已清空；随后停止/断线不会再次触发生成器清空。受理前停止则 accepted reject，迟到的 onCreated 不得改变已保留的草稿。
- [ ] **Step 3：运行测试确认旧实现失败。** 执行 `cd frontend && pnpm exec vitest run tests/unit/components/workspace/input-box-submission.dom.test.tsx tests/unit/core/threads/stream-submission-lifecycle.dom.test.tsx tests/unit/core/uploads/api.dom.test.ts tests/unit/app/workspace/agents/agent-chat-page.dom.test.tsx tests/unit/app/workspace/agents/new-agent-submission.dom.test.tsx`。记录清空时机、纯附件、agent parity、模型分支、新建 Agent 兼容和上传生命周期的实际失败项。
- [ ] **Step 4：实现内部双生命周期、公开单 Promise。** 以 operation id 创建 deferred accepted；onCreated 只 resolve 当前 operation，创建前异常/onError/onStop 只 reject 当前 operation。后台 completion 挂接 SDK 回调并内部 catch，但不从公共接口返回；`sendMessage` 本身只返回 accepted，busy 时立即 reject 并提示。InputBox 和两个 page 直接把该 Promise 交给 PromptInput，新建 Agent 三处 await/catch 保持原样，auto-send 用显式 catch 承接已统一提示的 rejection。
- [ ] **Step 5：修正模型校正与停止分支。** 模型自动校正后的 `setTimeout` 只负责延迟，外层必须返回/await 内层 `onSubmit(message)` 的 accepted Promise；停止同时 reject 当前 accepted 和抛出 `kind="stopped"` 的提交控制异常。不得修改 `prompt-input.tsx`。
- [ ] **Step 6：实现可取消上传与 operation 所有者。** 每次网络进展重置 idle timer，load/error/abort/外部取消都清理监听与 timer，只 settle 一次。用递增 operation id + AbortController 取代可被 threadId effect 随意复位的单布尔 guard；旧 operation finally 只释放自己，切线程取消旧上传并阻止其后续进度/optimistic 写入新线程。两个 page 均接 `isUploading/uploadProgress`。
- [ ] **Step 7：跑定向与 mock 行为验收。** 重跑 Step 3 全绿；执行 `cd frontend && pnpm exec playwright test tests/e2e/chat-submission.spec.ts`，实际经过“受理前停止”“受理后长流清空”“失败重试”“纯附件”“A 上传切 B”，不能只检查函数被调用。
- [ ] **Step 8：批次门禁与收尾。** 执行 `cd frontend && pnpm check`；确认无 registry/live/其他未提交文件进入 diff。写 PR1 changelog「验收记录」，勾选本任务并更新状态头，按约定提交分支与 PR。

### Task 2（PR2）：长 Agent 会话的幂等历史、真实终态、URL 与断线恢复

**Files：**
- Modify：`frontend/src/core/threads/hooks.ts`
- Create：`frontend/src/core/threads/recovery.ts`
- Modify：`frontend/src/core/threads/errors.ts`
- Modify：`frontend/src/core/tasks/subtask-status-sync.ts`
- Modify：`frontend/src/components/workspace/messages/message-list.tsx`
- Modify：`frontend/src/components/workspace/messages/virtual-message-list.tsx`
- Modify：`frontend/src/components/workspace/chats/use-thread-chat.ts`（仅确需共享 URL 状态时）
- Modify：两个 chat page
- Test：`frontend/tests/unit/core/threads/thread-history-reset.dom.test.tsx`（新增）
- Test：`frontend/tests/unit/core/threads/thread-history-idempotence.dom.test.tsx`（新增）
- Test：`frontend/tests/unit/core/threads/recovery.dom.test.ts`（新增）
- Test：`frontend/tests/unit/core/tasks/subtask-status-sync.test.ts`
- Test：`frontend/tests/unit/components/workspace/messages/message-list-scroll.dom.test.tsx`（新增）
- Test：`frontend/tests/e2e/chat-thread-recovery.spec.ts`（新增）

**Interfaces：**
- `useThreadHistory(threadId)`: 用 generation 丢弃旧线程响应，以 `historyShards: Map<runId, Message[]>` 保存各 run 分片；loadedRunIds 只控制旧 run 的分页加载，onFinish 对当前 run 强制重取并原子替换其分片。按 `(run_id, message.id)` 去重，缺 id 时以该 run 内 seq 兜底，run 间保持原顺序。
- `deriveSubtasks(messages, { runStatus?, hasInterrupt?, isHistoryLoading?, isRecoverableDisconnect? })`: 有 tool result 先按原 parser；无 result 时：`success→completed`，`error/timeout→failed`，`interrupted + hasInterrupt→等待`，`interrupted + !hasInterrupt→取消/中断失败`，其余运行/加载/断线状态不得判 failed。
- `reconnectActiveRun(): Promise<void>`：读取当前线程 `lg:stream:<tid>` 并 `joinStream`；无 runId 时失效 history/runs。只有连接类错误显示重连，业务 4xx/取消不得盲目重连。
- `onSend/onStart/onSendError`：onSend 在附件上传完成、提交 run 前触发，仅用预生成 UUID 更新地址栏，不提前改变 controlled threadId/isNewThread；onStart 确认创建状态；创建前失败恢复对应 `/chats/new`。

- [ ] **Step 1：先写 history 幂等、最终补齐和终态反例。** 决定性用例：首次 `run-1:[m1]`，onFinish 后同 run 返回 `run-1:[m1,m2]`，最终必须恰好 `[m1,m2]`，既不重复 m1，也不因 loaded 丢 m2。再覆盖新增 run-2 接续、旧 run 分页不回退、A 慢历史切 B 后不得拼入 B。无 tool result 时分别测试 success、error/timeout、取消 interrupt、等待用户 interrupt、history loading、网络断线、正常 running 间隙，只有真实 error/timeout/取消可 failed。
- [ ] **Step 2：补滚动、URL 和恢复测试。** 长线程初次水合落底、prepend 不跳；发送后地址栏脱离 `/new`，运行中刷新可 rejoin，创建前失败回退；仅网络错误提供重连，无 runId 时刷新 history/runs。
- [ ] **Step 3：运行测试确认旧实现失败。** 执行 `cd frontend && pnpm exec vitest run tests/unit/core/threads/thread-history-reset.dom.test.tsx tests/unit/core/threads/thread-history-idempotence.dom.test.tsx tests/unit/core/threads/recovery.dom.test.ts tests/unit/core/tasks/subtask-status-sync.test.ts tests/unit/components/workspace/messages/message-list-scroll.dom.test.tsx`。记录重复 history、终态误判、URL/恢复失败；不得把已撤回的“每次切线程串台”当成唯一测试前提。
- [ ] **Step 4：实现 generation + 按 run 分片替换。** threadId 变化清 messages/historyShards/loadedRunIds/hasMore；每次加载捕获 generation + run_id，只有当前代且成功返回才替换该 run 的完整分片。旧 run 分页只选择未 loaded 的 run；onFinish 以 onStart 记录的 currentRunId（缺失时才回退最新 run）强制重取当前分片，即使已 loaded 也原子替换，随后再失效 `["thread", threadId]`。flatten 时按 run 原顺序拼接，不对整个 history 盲目 prepend。
- [ ] **Step 5：实现真实 run 状态分流。** 从当前 run、thread.interrupt、history loading、连接错误构造上下文；断线保持可恢复状态并显示全局重连，不先失败；等待澄清不显示“运行已结束”。success 缺 result 以完成但无返回文本处理，error/timeout/取消显示真实原因。
- [ ] **Step 6：实现初始落底和有限恢复。** 以 threadId key 重置虚拟化 refs，首屏 instant 定位；仅初次水合落底，用户上翻/加载旧页保留锚点。`recovery.ts` 只识别连接类错误，onError 给重连 action，失败保留可重试提示。
- [ ] **Step 7：统一两个 page 的 URL 生命周期。** 保留 SDK 使用预生成 UUID 创建线程的语义，不在 onSend 提前翻 controlled threadId；onSend 移到上传完成、提交 run 前，避免长上传时地址栏指向未创建线程。地址栏先落 UUID，onStart 再确认，创建前失败回退，不重新引入 P0-3 假设。
- [ ] **Step 8：跑定向与长任务 mock 验收。** 重跑 Step 3 全绿；执行 `cd frontend && pnpm exec playwright test tests/e2e/chat-thread-recovery.spec.ts`，覆盖同 run 重取补齐且不重复、新 run 接续、长历史重开、运行中刷新、取消终态、断线重连和 interrupt 等待。
- [ ] **Step 9：批次门禁与收尾。** 执行 `cd frontend && pnpm check` 和 threads/tasks/messages 既有回归；写 PR2 changelog「验收记录」，勾选并更新状态头，按约定提交 PR。

### Task 3（PR3）：结构化 write-file 引用、内容状态与安全 auto-open

**Files：**
- Create：`frontend/src/core/artifacts/write-file-ref.ts`
- Modify：`frontend/src/core/artifacts/loader.ts`
- Modify：`frontend/src/core/artifacts/hooks.ts`
- Modify：`frontend/src/components/workspace/artifacts/artifact-file-detail.tsx`
- Modify：`frontend/src/components/workspace/messages/message-group.tsx`
- Create：`frontend/src/components/workspace/messages/write-file-tool-call.tsx`
- Test：`frontend/tests/unit/core/artifacts/write-file-ref.test.ts`（新增）
- Test：`frontend/tests/unit/core/artifacts/write-file-content-state.dom.test.ts`（新增）
- Test：`frontend/tests/unit/components/workspace/messages/write-file-auto-open.dom.test.tsx`（新增）
- Test：`frontend/tests/unit/components/workspace/artifacts/artifact-file-detail-write-file.dom.test.tsx`（新增）
- Test：`frontend/tests/e2e/chat-artifact-ref.spec.ts`（新增）

**Interfaces：**
- `createWriteFileRef({ path, messageId, toolCallId }): string`：path 作为独立 query 参数编码，不再嵌入 URL pathname。
- `parseWriteFileRef(value): { path; messageId; toolCallId } | null`：支持新格式和合法旧格式；非法 percent、缺定位字段时返回 null，不抛异常。
- `loadArtifactContentFromToolCall(...)`: 返回 `{status:"ready",content:string} | {status:"pending"} | {status:"not_found"} | {status:"invalid"}`。合法空字符串是 ready；合法引用但流式 message/toolCall/content 未齐是 pending；非流式仍找不到对应项是 not_found。

- [ ] **Step 1：写路径矩阵、内容状态和 auto-open 失败测试。** 参数化 `% # ? 中文 空格`，断言 round-trip；`100%.txt` 不抛 URIError。分别覆盖 invalid、流式 pending、非流式 not_found、ready 空文件、ready 非空文件。auto-open 断言同 toolCallId 只触发一次、100ms 内用户手选不被覆盖、卸载无 timer/setState、缺 path 不构造 URL。
- [ ] **Step 2：运行测试确认旧实现失败。** 执行 `cd frontend && pnpm exec vitest run tests/unit/core/artifacts/write-file-ref.test.ts tests/unit/core/artifacts/write-file-content-state.dom.test.ts tests/unit/components/workspace/messages/write-file-auto-open.dom.test.tsx tests/unit/components/workspace/artifacts/artifact-file-detail-write-file.dom.test.tsx`。预期新解析/状态类型不存在，旧 auto-open 和空串行为断言失败。
- [ ] **Step 3：实现并接入安全引用。** 用 `URLSearchParams` 生成 ref，用单一 parser 读取；message-group、loader、详情不得再各自 `decodeURIComponent(url.pathname)`。保留合法旧格式，已截断且无法恢复的旧值按 invalid 处理，不猜原文件名。
- [ ] **Step 4：在 useArtifactContent/详情层呈现四种状态。** ready 才返回正文（允许空串）；pending 显示加载；invalid/not_found 显示可恢复错误与重试/关闭动作，不再统一 `content ?? ""` 当空白文件。非 write-file 的既有查询和编辑语义不变。
- [ ] **Step 5：用独立组件承载无条件 Hook。** `ToolCall` 的 write_file/str_replace 分支只返回 `WriteFileToolCall`；auto-open effect 位于该组件顶层，按 toolCallId 记录已尝试 id 并清理 timer，用户选择变化后不再抢选。不得在条件分支里直接新增 Hook。
- [ ] **Step 6：跑定向与端到端验收。** 重跑 Step 2 全绿；执行 `cd frontend && pnpm exec playwright test tests/e2e/chat-artifact-ref.spec.ts`，让流式 write_file 自动打开特殊文件名，验证 pending→ready、聊天区不崩、正文对应正确 toolCall、合法空文件不误报。
- [ ] **Step 7：批次门禁与收尾。** 执行 `cd frontend && pnpm check` 和 artifacts 既有回归；写 PR3 changelog「验收记录」，勾选并更新状态头，按约定提交 PR。

### Task 4（PR4）：统一 unauthorized 与认证代际私有状态隔离

**Files：**
- Create：`frontend/src/core/query/client.ts`
- Create：`frontend/src/core/auth/private-state.ts`
- Modify：`frontend/src/components/query-client-provider.tsx`
- Modify：`frontend/src/core/auth/AuthProvider.tsx`
- Modify：`frontend/src/core/api/fetcher.ts`
- Modify：`frontend/src/core/api/api-client.ts`
- Modify：`frontend/src/core/uploads/api.ts`
- Modify：`frontend/src/core/console/hooks.ts`
- Test：`frontend/tests/unit/core/auth/private-state.dom.test.ts`（新增）
- Test：`frontend/tests/unit/core/auth/auth-provider-logout.dom.test.tsx`（新增）
- Test：`frontend/tests/unit/core/api/fetcher.dom.test.ts`（新增）
- Test：`frontend/tests/unit/core/api/api-client-unauthorized.dom.test.ts`（新增）
- Test：`frontend/tests/unit/core/uploads/api-unauthorized.dom.test.ts`（新增）
- Test：`frontend/tests/unit/core/console/console-query-scope.dom.test.tsx`

**Interfaces：**
- `getQueryClient()/subscribeQueryClient(listener)/replaceQueryClient()`：认证代际 QueryClient store；Provider 用 `useSyncExternalStore` 订阅当前实例。reset 后旧实例可由其旧 mutation 收尾，但新页面/新账号只使用新实例。
- `resetPrivateClientState(): Promise<void>`：取消旧实例在途查询、替换实例、删除 `draft:*`、`deerflow.thread-model.*`、`writer_draft_*`，清空本标签页 sessionStorage；保留 theme、locale 和非私有偏好，不调用 `localStorage.clear()`。
- `handleUnauthorized(returnPath): Promise<void>`：single-flight 执行 reset 和登录跳转，供共享 fetcher、AuthProvider.refreshUser、上传 XHR、LangGraph SDK 四处调用；logout 复用 reset 但保持回首页。
- `subscribePrivateStateReset(listener): () => void`：每次实际 reset 完成时同步通知并返回退订函数；监听异常不得阻断其他清理。PR5 用它清 accepted-operation 内存，不让认证层反向依赖 video-gen。
- `useConsoleDataScope()`: 返回 `<admin|user>:<真实 user id>`，不再让所有 admin 共用 `admin` 缓存。

- [ ] **Step 1：写四入口 single-flight 与跨代际测试。** 分别触发 fetcher、refreshUser、XHR、SDK client 401，断言同一时刻只执行一次 reset/跳转且都清理。预填 threads、video tasks、course projects、console 和本地草稿；reset 后 theme/locale 保留，其余私有状态为空。让一个旧 QueryClient 的 mutation 在 reset 后完成并调用旧 `setQueryData`，断言新实例无数据；慢查询同样不得回写。
- [ ] **Step 2：运行测试确认旧实现失败。** 执行 `cd frontend && pnpm exec vitest run tests/unit/core/auth/private-state.dom.test.ts tests/unit/core/auth/auth-provider-logout.dom.test.tsx tests/unit/core/api/fetcher.dom.test.ts tests/unit/core/api/api-client-unauthorized.dom.test.ts tests/unit/core/uploads/api-unauthorized.dom.test.ts tests/unit/core/console/console-query-scope.dom.test.tsx`。预期旁路未清理、缓存/本地键残留、admin 作用域和迟到写断言失败。
- [ ] **Step 3：实现认证代际 QueryClient store。** 保持 Provider 组件 API 不变，用订阅切换实例；不依赖 AuthProvider 位于 QueryClientProvider 外层的 hook 关系，不产生 core→components 反向 import。旧实例只用于收尾，禁止旧回调污染新实例。
- [ ] **Step 4：实现 reset、reset 通知与 single-flight unauthorized。** reset 各存储异常不得阻断剩余清理；完成实例与存储替换后调用一次私有状态监听，监听异常隔离。handleUnauthorized 同时只存在一笔；跳转前完成实例替换。logout API 失败也完成本地 reset 并回首页。测试验证多次并发 401 只通知一次，退订后不再通知。
- [ ] **Step 5：覆盖四类入口。** fetcher、refreshUser、XHR load 401、SDK 默认 client/runCreationClient/streamRecoveryClient 全部接同一 handler；SDK 只检查 Response.status，不读取/破坏 SSE body。XHR 用 Promise 收尾后再 reject/跳转，避免多入口重复 reset。
- [ ] **Step 6：收窄 console 作用域并跑全入口回归。** 更新 scope，重跑 Step 2 与 `console-role-transition.dom.test.tsx`；补 A 登出→B 登录的 DOM 验收，证明作品、课程资产、线程、用量无 A 数据，旧 mutation 完成也不会复活。
- [ ] **Step 7：批次门禁与收尾。** 执行 `cd frontend && pnpm check`；写 PR4 changelog「验收记录」，勾选并更新状态头，按约定提交 PR。

### Task 5（PR5）：AI 生图/AI 生视频的操作身份、等待反馈与编辑真实性

**Files：**
- Create：`frontend/src/core/video-gen/accepted-operation.ts`
- Modify：`frontend/src/core/video-gen/hooks.ts`
- Modify：`frontend/src/components/workspace/video-gen/video-gen-detail.tsx`
- Modify：`frontend/src/components/workspace/video-gen/video-gen-page.tsx`
- Modify：`frontend/src/components/workspace/video-gen/video-gen-frame-ops-panel.tsx`
- Test：`frontend/tests/unit/core/video-gen/accepted-operation.dom.test.ts`（新增）
- Test：`frontend/src/core/video-gen/hooks-invalidation.dom.test.tsx`
- Test：`frontend/tests/unit/components/workspace/video-gen/video-gen-frame-ops-panel.dom.test.tsx`（新增）
- Test：`frontend/tests/e2e/video-gen-operation-lifecycle.spec.ts`（新增）

**Interfaces：**
- `markVideoOperationAccepted({ operationId, taskId, taskType, baselineUpdatedAt, acceptedAt })`: 仅允许 `ai_image/ai_video/ai_visual`；每个 mutation 创建新 operationId，baselineUpdatedAt 取受理前该任务缓存值。
- `videoOperationRefetchInterval({ operationId, taskId, status, updatedAt, now, baseDelayMs }): number | false`：只操作当前 operationId；将服务端 updated_at 解析为毫秒时间戳，只有 `updatedAt > baselineUpdatedAt` 的活动态才把该 operation 记为 seenActive，seenActive 后更新的 ready/failed 才清除。缺 updated_at 或时间戳无效时保守等待，不判当前代完成。
- 同标签页恢复：`video-gen.accepted-operation.${taskId}` 保存 operationId/taskType/baselineUpdatedAt/acceptedAt/seenActive；终态、删除任务、logout 清理，跨标签页同步不在本批。
- `visualDirty = promptChanged || sourceChanged`，与既有 `narrationDirty` 分开；守卫由 `supportsVisualDirtyGuard(videoType)` 只对 `ai_image/ai_video/ai_visual` 启用，ViMax 与其他类型保持原按钮行为。

- [ ] **Step 1：写身份、类型、恢复与等待测试。** 模拟 ready→多次旧 ready→pending→composing→ready，断言旧响应不清当前 operation；连续 A/B 两次操作时，A 的 active/ready 不得清掉 B。缺 updated_at 保守等待。覆盖刷新恢复、终态/删除/logout 清理、5 分钟后降频且显示“已受理，等待后台开始处理”。remotion_course/ViMax 不 mark 且旧轮询不变，ai_visual 纳入。
- [ ] **Step 2：写付费前守卫与错误反馈测试。** 白名单任务 prompt/source 未保存时视觉重生禁用，narrationDirty 仍只挡配音；ViMax 在同样未保存编辑下保持原按钮可用/禁用规则。三类视觉乐观编辑失败回滚且 toast。
- [ ] **Step 3：运行测试确认旧实现失败。** 执行 `cd frontend && pnpm exec vitest run tests/unit/core/video-gen/accepted-operation.dom.test.ts src/core/video-gen/hooks-invalidation.dom.test.tsx tests/unit/components/workspace/video-gen/video-gen-frame-ops-panel.dom.test.tsx`。记录旧 ready 停机、跨代清理、类型门控、dirty guard 和 toast 失败项。
- [ ] **Step 4：实现带 operationId 的 accepted store。** recompose/re-render/终态 renderFromDraft 成功受理时读取任务类型和 baselineUpdatedAt，只对白名单任务 mark；请求失败删除当前 operation。所有 interval/clear 都校验 operationId，旧响应不得修改新 operation。
- [ ] **Step 5：接入共享 hook、列表和详情。** `useRecomposeTask/useReRenderTask/useRenderFromDraft` 先 mark 再 invalidate；删除局部 `recomposeGraceUntilRef` 特例。未看到活动态时旧 ready 不停；5 分钟后仅降频到 `max(baseDelayMs, 15000)`。详情和列表在超长等待时显示受理提示，不能只后台轮询无解释。
- [ ] **Step 6：实现恢复、清理与分类型编辑守卫。** sessionStorage 同标签页恢复并校验记录；终态、`useDeleteVideoTask` 清理单项，accepted store 通过 PR4 的 `subscribePrivateStateReset(clearAllVideoOperations)` 同时清内存和持久记录，验证 logout 后无残留。只有白名单任务在未保存 prompt/source 时禁用“重生画面”并提示先保存；ViMax 原行为不变。视觉乐观 mutation 在 rollback 同一 onError 中 toast。
- [ ] **Step 7：跑可控队列端到端验收。** 重跑 Step 3 全绿；执行 `cd frontend && pnpm exec playwright test tests/e2e/video-gen-operation-lifecycle.spec.ts`，覆盖详情 recompose、预览转正式、连续操作、刷新恢复和超长等待提示，等待最终 ready 与产物版本刷新，全程不切窗口焦点。
- [ ] **Step 8：批次门禁与收尾。** 执行 `cd frontend && pnpm check` 和相关 video-gen 既有回归；写 PR5 changelog「验收记录」，勾选并更新状态头，按约定提交 PR。

### Task 6：总验收与交付（不新增 PR）

- [ ] **Step 1：机械排除 live 用例。** 在干净 worktree 执行以下命令，命中任何 `tests/e2e/live/` 都必须停止；不得只凭当前未提交的 `testIgnore` 配置推断安全。
  ```bash
  cd frontend
  if ! playwright_list="$(pnpm exec playwright test --list)"; then
    echo "Playwright test listing failed; refusing to run the full suite" >&2
    exit 1
  fi
  printf '%s\n' "$playwright_list"
  if grep -qE 'tests/e2e/live/' <<<"$playwright_list"; then
    echo "Default Playwright config includes paid/live tests" >&2
    exit 1
  fi
  ```
- [ ] **Step 2：跑完整前端门禁。** 在 Step 1 通过后，依次后台执行 `cd frontend && pnpm test`、`cd frontend && pnpm test:e2e`、`cd frontend && pnpm check`，全部通过；任一失败先区分本批回归与既有问题，不得用跳过测试收尾。
- [ ] **Step 3：跑仓库静态与文档门禁。** 执行根 `make check`、`python3 scripts/check_docs_convention.py`、`git diff --check`；检查每批 changelog 都有「验收记录」且链接回本计划。
- [ ] **Step 4：只在必要时做最小真实 API 验收。** 仅当 mock 无法证明真实响应头、SSE 或队列行为时，选择一个最短、最低消耗的现役 Agent run 或一个 AI 生图/生视频操作；记录 thread id、受理/后台 Promise 时序、URL、取消/断线/恢复和最终产物，不循环重试、不运行全量 live 套件、不部署。非必要则明确记录“未调用付费 API 及原因”。
- [ ] **Step 5：检查最终 diff 与非目标零改动。** 确认无 backend、docker、config、skills、seed、registry、writer/gallery/voice 功能、remotion/ViMax 专属实现、live 测试文件和其他未提交工作进入本批；无测试日志、截图、临时脚本等产物误入库。
- [ ] **Step 6：完成文档与交付。** 勾选全部已完成项，状态头写明 5 个 PR 与 changelog 链接、实际验收范围及未验证边界；向用户交付 PR 列表、关键行为证据、付费 API 使用情况（若有）和“未部署”说明。

## 本批完成定义

- 五批各自通过定向测试和 `pnpm check`，最终完整前端测试与根静态门禁通过。
- accepted 之前停止/失败/busy 保留草稿；accepted 之后长流不再控制 composer；纯附件可发，上传可取消且不跨线程污染；新建 Agent 的现有 await/catch 不变，auto-send 无未观察拒绝。
- 同一 run 重取能补齐最终消息且不重复，新增 run 不丢；断线、history loading、interrupt 不误报失败，取消有明确终态。
- 特殊文件名不会崩聊天，write-file 只 auto-open 一次且不覆盖用户选择；invalid/pending/not_found/ready 不再混成空串。
- 四类 401 入口统一清理；A/B 账号切换不复用私有查询或本地草稿，旧 mutation 只能写废弃 QueryClient。
- AI 生图/AI 生视频不会拿未保存的旧 prompt/source 付费重生；操作响应按 operationId 隔离，非白名单行为不变，超长等待有反馈且不停轮询。
- 默认全量 Playwright 机械确认不含 live 用例；未部署，未修改任何禁区或超出产品范围的功能。
