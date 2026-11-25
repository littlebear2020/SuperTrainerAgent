# 核心产品前端可靠性 PR3：长 Agent 会话的幂等历史、真实终态与断线恢复

> **TL;DR**：聊天历史改成真实分页（首页最新、`before_seq` 向前）并按 run 分片，旧 run 的子任务终态按所属 run 判定（被停止的 run 记为「已取消」而不是失败）；停止后按取消结果有界同步，取消失败可重试，断线后给单飞的手动重连；切线程整体重置滚动、prepend 按实测高度校正锚点。只改 `core/threads`、`core/tasks`、`messages/**`、两个 chat page 与对应测试，后端、registry、认证零改动；Vitest + mock Playwright 验收，零付费调用，未部署。计划：`docs/plans/2026-11-17-core-product-frontend-reliability-plan.md`（Task 3）。

## 变更摘要

- **纵切 A · before_seq 分页与 requestEpoch 分片**：`fetchAllRunMessages` 按真实 API 取数——首页不带游标、返回最新 200 条，`has_more` 时以本页第一条的 seq 作 `before_seq` 向前取旧页；取消 25 页 / 5000 条的静默截断，防死循环只靠「游标必须严格变小」。`useThreadRuns` 显式传 `limit=100`。`useThreadHistory` 重写：runs 列表 newest-first，首次只加载最新的 run，loadMore 向前一个 run；每个 run 一个分片（消息 + 加载时的 status / updated_at），按旧→新、run 内按 seq 拼接并按 `(run_id, message.id)` 去重；generation 丢弃切线程前的迟到响应，requestEpoch 只让同一 run 最新一次请求原子替换分片，loadedRunIds 只在完整成功后写入。
- **纵切 B · 按 run 终态判定与恢复 owner**：`deriveSubtasks` 增加 run 归属——没有 tool result 的 `task` 调用按所属 run 判定：success → completed，error / timeout → failed（不虚构错误详情），interrupted → 新增的 `cancelled`，澄清（success + 未回答的 `ask_clarification`）与 pending / running 保持 in_progress；history loading / 断线期间不下发终态 patch。停止后按 PR2 的 `CancelOutcome` 同步：`cancelled` → 失效 runs 列表并重取该 run 的分片；`failed` → 提示并保留可重试目标（「重试停止」即再次调用同一 cancel）；显式取消的等待加 30 秒上限。连接类错误收尾且 run 可能仍在服务端运行时给「重新连接」（按 `(threadId, runId)` 单飞，SDK 流在途时不重复 join，join resolve 不等于恢复成功）；stale run 404 清 reconnect key、失效 runs / history。恢复入口由 `ThreadContext.recovery` 交给 `MessageList` 尾部的 `RecoveryBanner`。
- **纵切 C · 滚动重置、history 契约与补齐式重载**：`Conversation` 以 `threadId` 为 key、`initial="instant"`（不改 registry）；`ThreadContextType` 新增可选 `isHistoryLoading / hasMoreHistory / refetchHistory`（PR4 据此区分 pending 与 not_found）；`refetchHistory` 是用户显式触发、带新鲜度判定的补齐式重载——只重取未成功加载、非终态、加载后已变化的 run，在途请求先等它结束，任一失败保留旧分片并 reject 以便重试。
- **滚动锚定**：`VirtualMessageList` 在 prepend 后先按估计高度对齐，新行实测高度陆续到位时按同一锚点逐帧校正直到稳定；静态分支（<60 组）按真实 DOM 节点锚定（浏览器原生滚动锚定在滚动位置为 0 时不下调）；prepend 跨过虚拟化阈值时静态锚点交给虚拟分支继续校正。
- **PR2 评审遗留在此落地**（PR #59 第 3 / 4 轮处置）：移除受理看门狗轮询（SDK 的 `enqueue` 先置 `isLoading=true` 再发请求，React 总会先渲染到在途；轮询在生产不可达却留有误判面）；`sendMessage` 的拒绝契约 JSDoc；auto-send 回填读最新草稿（不覆盖等待期间写的草稿）；mock 的 run stream 线程 id 先读请求体；显式取消的等待上限。
- **e2e 夹具**：`mock-api` 提供 newest-first 的 runs 列表与按真实端点分页的 run-messages 路由。

## 漂移与执行边界

- 分支 `chat-thread-recovery` 基于 PR2 合入后的 main `e8803fe5`；主题分支上另有 4 笔是 PR2 评审轮次的遗留（见「变更摘要」末条），都在 PR3 的 allow 内。
- 计划 allow 之外、ban 之内没有触达，但有两处计划未列出的文件被改动：`frontend/eslint-suppressions.json`（`hooks.ts` 重写清偿了 3 条 react-hooks 存量 suppression，残留条目会让 `eslint .` exit 2，`frontend/AGENTS.md` 要求 `--prune-suppressions` 收尾）与 `core/i18n/locales/{types,zh-CN,en-US}.ts`（新增 `subtasks.cancelled` 与 `recovery.*` 词条：计划 Interfaces 要求 interrupted → 「取消/中断」且恢复入口是用户可见文案）。
- 纵切 B 拆成两笔提交（按 run 判定 / 取消同步与重连），纵切 C 拆成两笔（状态重置与契约 / 像素锚定）。
- 未新增依赖、未改 `components/ui/**`、`components/ai-elements/**`、认证、`core/api/**`、QueryClient；未部署、未调用付费 API、未跑 live 套件。`config_version` 不涉及。
- runs 列表后端固定上限 100：超过 100 个 run 的线程，更早的 run 与补齐式重载都覆盖不到，这是明确的未覆盖边界，mock 不伪造分页能力。

## 执行修正（计划文本与实现的差异）

1. **看门狗移除而非放宽**：PR2 第 3 轮评审的 high 指出 1.5 秒兜底有误判面、误判后迟到的 onCreated 不认领。它只覆盖「React 从未渲染过 `isLoading=true`」的情形，而 SDK 的 `enqueue` 先置 `isLoading=true` 再发网络请求，React 在其后的微任务里就提交这一帧——生产环境里流不可能先于这一帧结束，只有测试的 act 批处理会让整条流在微任务内跑完。流收尾 effect 的兜底保留；对应用例改成真实顺序（流过一个宏任务再结束），变异验证 `expected 'timeout' to be 'failed'`。
2. **`Subtask.status` 新增 `cancelled`**：计划写「interrupted → 取消 / 中断」但 `Subtask.status` 只有 `in_progress | completed | failed`；既有 live 路径把用户取消的任务记为 `failed`（`Task cancelled by user.` 前缀）。按计划意图新增状态与中英词条，卡片用 `BanIcon`。
3. **`ThreadContext.recovery`**：计划的 Interfaces 只列了三个 history 字段；恢复入口需要一条从两个 chat page 到 `MessageList` 的通道，沿同一「可选消费者」契约加了 `recovery`。
4. **mount 自动 rejoin 的 run 归属**：计划的 onFinish 归属只提了用 `meta.run_id` 定位分片；但刷新后接续的流没有经过 onCreated，hook 不知道「当前 run」——停止、重连、终态同步都无从归属。实现里 hook 读 SDK 的同一个 reconnect key（`lg:stream:{threadId}`）作为当前 run 的兜底，key 在流正常收尾时由 SDK 清掉。
5. **onFinish 强制重取先登记再刷新**：e2e 实测 onFinish 后新 run 的历史端点被请求两次（runs 列表刷新触发「加载最新 run」的 effect，与强制重取各发一次）。`refetchRun` 先登记、再刷新，effect 看到登记就不重复请求。
6. **静态分支也锚定**：原注释写「静态态交还浏览器原生滚动锚定」；实测 Chrome 的原生锚定在滚动位置为 0 时不下调（用户在顶部点「加载更多」会被顶到新插入的旧消息上），而计划的 ≤4px 断言恰好在滚动位置 0 处量（锚点 y=149）。
7. **「加载更多」哨兵自动触发**：`LoadMoreHistoryIndicator` 在进入视口 120px 内就自动加载，短线程一开始就会把旧 run 接续进来；e2e 用扣住旧 run 响应的方式在 prepend 之前量锚点。

## 验收记录

- 纵切 A RED：`pnpm exec vitest run tests/unit/core/threads/thread-history-pagination.test.ts tests/unit/core/threads/thread-history-reset.dom.test.tsx tests/unit/core/threads/thread-history-idempotence.dom.test.tsx` → 分页 `Tests 4 failed | 2 passed (6)`（`expected [ Array(451) ] to deeply equal [1..451]`、`expected […(5000)] to have a length of 5001 but got 5000`、`expected [Function] to throw error matching /cursor did not advance/ but got 'Cannot read properties of undefined'`、`promise resolved "[]" instead of rejecting`），分片 `5 failed | 3 passed (8)`（`expected ['m3','m4'] to deeply equal ['m1','m3','m4']` 等）。GREEN → threads 套件 `17 files / 117 passed`，`tsc --noEmit` exit 0；e2e 的 chat.spec 历史 run 顺序改成 newest-first 后 8 个 chat 相关 spec `71 passed / 1 skipped / 1 failed`（失败项是既有的 prepend 抖动，本纵切 D 处理）。
- 纵切 B RED：`pnpm exec vitest run tests/unit/core/tasks/subtask-status-sync.test.ts` → `5 failed | 9 passed (14)`（`expected 'in_progress' to be 'completed'`、`expected 'in_progress' to be 'cancelled'`、`expected [{ id:'tc1' }] to deeply equal []`、`deriveSubtasksByRun is not a function`）；卡片用例 `expected '整理资料p' to contain '子任务失败'`。GREEN → tasks `6 files / 64 passed`；卡片变异（去掉 cancelled 行）`expected '整理资料p' to contain '子任务已取消'`。
- 恢复 owner RED：`pnpm exec vitest run tests/unit/core/threads/recovery.dom.test.ts` → `Failed to resolve import "@/core/threads/recovery"`；实现后经过夹具修正（假计时器只在推进等待上限时打开、替身不通知 React 需显式重渲染）剩余真实失败为 `expected ['run-1','run-1'] to deeply equal ['run-1']`（在途加载上叠了一次请求）。GREEN → `9 passed`，连同 mount-rejoin 用例 `10 passed`。变异：不保留重试目标 → `2 failed`；去掉单飞 → `1 failed`；跳过有界刷新 → `2 failed`；去掉 onFinish 重取 → `expected "bound bound fetch" to be called 2 times, but got 1 times`；去掉 reconnect key 兜底 → `expected null to be 'run-live'`。
- 纵切 C RED：`message-list-scroll.dom.test.tsx` → `expected undefined to be 'instant'`；`thread-history-refetch.dom.test.tsx` → `historyApi(...).refetchHistory is not a function` ×4；两个 page 的契约用例 `expected undefined to match object { isRecoverableDisconnect, … }`。GREEN → `3 passed` / `4 passed` / 回归集 `44 files / 270 passed`。
- 像素锚定（Playwright，96 组线程、翻到顶部后 prepend）：旧实现锚点行 top `136 → -204`（4×176 估计 − 364 实测 = 340px）、`96 → -244`、70 行 prepend 后锚点行不在渲染窗口；新实现 `136 → 136`、`96 → 96`、`96 → 96`。
- 纵切 D：`pnpm exec playwright test tests/e2e/chat-thread-recovery.spec.ts` 首次 `10 failed / 2 passed`（join 路由的 glob 没放开 SDK 的查询串、短线程的旧 run 自动接续、虚拟列表在底部时首行不在 DOM），逐项修正后 `10 passed / 2 failed` → `12 passed / 1 failed`；`--repeat-each=3 --workers=1` 第一次 `38 passed / 1 failed`（真实分页用例在开场定位动画未结束时翻顶，被拉回），加「到底且稳定」等待后 → `39 passed (35.0s)`。
- 批次门禁：`make -C frontend check` → exit 0（ESLint `0 errors, 120 warnings` 存量、`tsc --noEmit` 通过、prettier `All matched files use Prettier code style!`、knip 无死导出、i18n `671 个叶子全活`）；全量 `cd frontend && pnpm test` → `Test Files 299 passed (299)`、`Tests 2026 passed (2026)`；`pnpm test:e2e` 全量 → `168 passed, 3 skipped (1.8m)`（含 chat.spec 的 `token summaries … after real run-history prepend`，此前约 1/3 概率超时）；`pnpm perf:check` → exit 0（`/workspace/chats/[id]` JS 3571236 B、CSS 255804 B，在 `performance-budgets.json` 之内）。
- 每个代码提交前 `pre-commit run --files <改动文件>` → eslint / prettier / gitleaks 全 Passed；`pnpm exec eslint . --ext .ts,.tsx --prune-suppressions` → exit 0，`0 errors, 120 warnings`（存量）。

## 未验证项

- 未接真实后端或付费模型：分页、终态同步、取消与重连全部由 Playwright mock 与保持连接的本地 SSE 服务证明；真实 join 流的 `metadata` / `values` 事件形态只按 SDK 源码对齐。
- runs 列表上限 100：超过 100 个 run 的线程，更早的 run 不可见，`refetchHistory` 也只处理前 100 个。
- 像素断言只在 Chromium（Playwright）里验证；其他浏览器的原生滚动锚定与 `use-stick-to-bottom` 的配合未测。
- 断线后 run 在服务端走到终态的补取依赖 runs 列表的 `status / updated_at` 变化；服务端不更新 `updated_at` 时不会触发。
- 用户在首次水合的约 300ms 平滑落底动画期间上翻，动画可能把视口拉回底部（`resize="smooth"` 是 registry 的 `Conversation` 默认值，本批不改 registry）。
- 未部署，未跑 `tests/e2e/live/**`。

## 配置与回滚

- `config_version`：**不涉及**；无 schema、依赖或部署变更。
- 回滚 = revert 本分支的提交：历史回到「首页当最早页 + after_seq」的错误分页与 25 页截断，子任务在 run 结束后按当前 run 一刀切，停止 / 断线没有恢复入口，切线程不重置滚动。
