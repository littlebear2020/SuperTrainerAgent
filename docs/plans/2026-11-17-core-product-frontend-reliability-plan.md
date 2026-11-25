# 核心产品前端可靠性优化 Implementation Plan

> **状态**：已执行（PR1–PR4 均已 rebase 合入 main，总验收已于 2026-11-25 在合入后的 main `20430f40` 上完成；2026-11-17 定稿；2026-11-24 完成范围裁剪与契约复核，保留 4 个纯前端串行 PR，执行顺序调整为视频编辑安全 → 输入上传 → 线程恢复 → 产物引用并按新顺序重编号；同日第二轮契约修订：agents/new 受控输入、回调归属与可等待取消、分片新鲜度、澄清状态模型、纵切提交闭包、PR1 守卫范围收窄；视频操作代际已拆至 `docs/plans/pending/2026-11-24-video-operation-generation-plan.md`，延后项已登记 `docs/findings-backlog.md`；不授权部署）。**执行进度（2026-11-25）**：PR1 视频编辑安全已合入（PR [#58](https://github.com/littlebear2020/SuperTrainerAgent/pull/58)），验收记录见 [changelog-2026-11-25-core-product-frontend-reliability-pr1.md](../changelog/changelog-2026-11-25-core-product-frontend-reliability-pr1.md)；PR2 输入受理已合入（PR [#59](https://github.com/littlebear2020/SuperTrainerAgent/pull/59)），验收记录见 [changelog-2026-11-25-core-product-frontend-reliability-pr2.md](../changelog/changelog-2026-11-25-core-product-frontend-reliability-pr2.md)。PR3 长会话恢复已合入（PR [#60](https://github.com/littlebear2020/SuperTrainerAgent/pull/60)），验收记录见 [changelog-2026-11-25-core-product-frontend-reliability-pr3.md](../changelog/changelog-2026-11-25-core-product-frontend-reliability-pr3.md)。PR4 write-file 引用已合入（PR [#62](https://github.com/littlebear2020/SuperTrainerAgent/pull/62)），验收记录见 [changelog-2026-11-25-core-product-frontend-reliability-pr4.md](../changelog/changelog-2026-11-25-core-product-frontend-reliability-pr4.md)。**总验收（2026-11-25，合入后的 main `20430f40`）**：`playwright test --list` → `Total: 184 tests in 26 files`、不含 `tests/e2e/live/`；`cd frontend && pnpm test` → `Test Files 304 passed (304)`、`Tests 2116 passed (2116)`；`pnpm test:e2e` → `181 passed, 3 skipped (3.3m)`；`make -C frontend check` → exit 0（ESLint 0 errors）；`pnpm perf:check` → exit 0；根 `make check` → exit 0（ruff `All checks passed!`、ruff format 通过、前端 ESLint 0 errors）；`python3 scripts/check_docs_convention.py` → `OK 仓库治理约定全过`；逐 PR `git diff --check` 全过、`--name-only` 对照 allow/ban 无越权（`core/api/` 只有 PR2 的取消包装与中止归一化；产品代码未新增 accepted-operation 或 Web Storage 键 / 写入，PR3 读取 / 清除的是 SDK 自己的 `lg:stream:{{threadId}}` 重连键）；未调用付费 API，原因：未获授权；未部署。首次 Step 2 在机器满载（10 核、5 分钟平均负载 30）时跑出 1 条既有用例超时与 19 条 e2e `page.goto` 超时，负载回落后原样重跑全绿，两次结果都保留在此；该贴近超时上限的既有用例已登记 backlog。未验证边界见各 PR changelog 的「未验证项」；视频跨代轮询另立 `docs/plans/pending/2026-11-24-video-operation-generation-plan.md`。同日用户以 /goal 指令授权本批建分支、push、开 PR 与评审通过后合并；仍不授权部署、付费 API 调用与 live 套件。
> **TL;DR**：分 4 个串行 PR，按视频编辑安全→输入上传→线程恢复→产物引用执行（视频批路径零重叠、体量最小，先止住帧面板的未保存旧参数付费重生）。聊天补齐真实分页、取消恢复和滚动锚定；write-file 补齐流式参数完整性与分工具内容语义；AI 生图/生视频只修帧面板「重生画面」的未保存付费重生、取消轮询和失败反馈。换账号残留与视频跨代 accepted-operation 不在本批：延后项已登记 backlog，后者已拆至 pending 计划。流式停止保留草稿已落地，不得回退。

> **For agentic workers：** 推荐 `superpowers:executing-plans` 在当前会话按文档顺序执行（Task 0 → Task 1–4 即 PR1–PR4 → Task 5）；若用户另行选择，才使用 `superpowers:subagent-driven-development`。先完成 Task 0 的基线预检，再用 `superpowers:using-git-worktrees` 从包含本计划修订的提交建立隔离工作区，按 checkbox 逐项勾选，不得并行派发这 4 个 PR。步骤内的 Commit 步是本地提交，按 Git 约定逐笔落；本计划不授权自动 push、自动合并或部署，push 与开 PR 另行确认。

**Goal：** 消除现役核心产品前端中会导致草稿丢失、聊天/产物不可用、旧参数付费生成、取消后持续轮询以及长 Agent 会话结果无法恢复的问题。

**Architecture：** 不重写页面，围绕四个共享边界做小步修复：受理与后台运行分离的提交事务、真实分页与按 run 分片的线程恢复、参数完整且按工具语义务实的 write-file 引用、带类型门控的视频编辑安全。视频跨代轮询需要服务端操作代际，已拆至 pending 计划 `docs/plans/pending/2026-11-24-video-operation-generation-plan.md`，不在纯前端批次伪造归属。每个 PR 独立完成 red→green→行为验收，后一批基于合入后的 main。

**Tech Stack：** Next.js 16、React 19、TypeScript、TanStack Query、LangGraph SDK、Vitest（node + happy-dom）、Playwright（mock API）；不新增依赖。

**Spec：** `docs/2026-11-16-live-e2e-bug-handoff.md`（按月归档后位于 `docs/archive/2026-11/`）提供缺陷线索；`docs/changelog/changelog-2026-11-23-chat-token-usage-turn-summary.md` 提供 prepend 像素锚定失败证据。本计划的「现行决策」与「明确不做」两节是最终范围与实现决策的单一事实源，原缺陷清单及外部评审中已证伪或超出当前产品边界的项目不得回流。

## 现行决策

- **产品范围**：仅包含 course-video、ppt-master、creative-studio、podcast-studio 四类 Agent 的共用聊天体验，以及 AI 生图/AI 生视频两条 video-gen 管线。
- **严格前端**：不修改 Gateway、worker、Harness、Docker、compose、config schema、技能或预置 Agent。视频任务查询没有操作代际，纯前端无法同时证明“旧 ready 不误完成”和“快速终态不永轮询”；该部分由 `docs/plans/pending/2026-11-24-video-operation-generation-plan.md` 承接，本批不得用本地 operationId、时间戳或无限轮询冒充闭环。
- **4 个串行 PR 与执行顺序**：PR1 视频编辑安全；PR2 输入与上传；PR3 长任务线程恢复；PR4 产物引用。2026-11-24 把视频批从末位提前（原编号 PR4）：它与前三批路径零重叠、体量最小，守的是唯一直接付费的风险。PR2 与 PR3 共享提交 Promise、onSend/onStart/onStop、threads hooks 和两个 page，必须相邻；PR3 向 PR4 提供统一 history loading/refetch 契约；每批在前一批合入后 rebase。
- **执行方式**：推荐当前会话顺序执行，不为每批重复建立共享上下文；不拆给多个子代理并行执行。
- **验收边界**：Vitest + mock Playwright 为默认；只有真实 SSE/队列/浏览器行为无法由 mock 替代时才允许最小付费 API 验证，不循环烧 token、不跑全量付费套件、不部署。
- **兼容现有数据**：保留历史线程、作品、课程资产、草稿和合法已有产物引用；不清库、不做数据迁移，也不为换账号去清查询缓存或本地草稿。
- **受理模型**：`sendMessage` 保持 `Promise<void>`，返回值即 accepted——上传完成且 SDK `onCreated` 后 resolve；busy、创建前失败、受理前停止则 reject。completion 只在 hook 内部管理。立论依据：SDK `submit()` 内的 `stream.start()` 只把任务接入队列即返回（已对照 `node_modules` 核实），所以问题是过早 resolve，不是长任务期间不能清空。三个调用点逐一承接 rejection，普通聊天不再用 `void`。
- **受理逻辑落位**：双生命周期与 submission 归属放纯逻辑模块 `core/threads/submission.ts`，hook 只做 SDK 接线（2026-11-24 评审：`hooks.ts` 已 947 行且 PR2、PR3 连续修改；纯逻辑可用 node 档测试）。
- **回调归属**（2026-11-24 第二轮）：SDK 队列严格串行，同一时刻只有一个流在跑，所以 busy 的判定并入 `thread.isLoading`——上一条流的 SDK 队列未收尾时的提交按 busy 拒绝并保留输入。这样任一时刻最多只有一个 submission 可能收到回调：无 meta 的回调（onCreated、创建前 onError、onStop）归属当前 submission，onCreated 之后按 run_id 归属带 meta 的 onFinish/onError。`handleStreamStart`、错误 toast、optimistic 清理、URL 与线程 metadata 更新这些共享副作用，只在回调归属当前 submission 时执行。
- **可等待的取消**（2026-11-24 第二轮）：SDK 在 `thread.stop()` 内部发出的 cancel 是浮动 Promise，真实失败时 `await thread.stop()` 接不到。改为 cancel 包装永不 reject、返回结果对象，hook 显式 await 它并把结果写进 submission 的 completion；PR2 产出、PR3 消费。`frontend/src/core/api/api-client.ts` 的 cancel 包装因此是 PR2 对 `core/api/**` 禁改的唯一例外。执行期补充（2026-11-25，e2e 实测）：SDK 自带的 AsyncCaller 只按 message 前缀识别中止，Chromium 的中止消息会被当成普通失败退避重试约 15 秒，受理前停止后流迟迟不收尾；同一 compat 层通过 `callerOptions.fetch` 把中止 rejection 归一成 `AbortError` 前缀，属于同一文件例外的延伸，不涉及认证。
- **agents/new 的输入受控**（2026-11-24 第二轮）：`agents/new` 现在直接用非 Provider 的 `PromptInput`，registry 在 onSubmit 前就 `form.reset()`，rejection 保不住正文。改为经 `PromptInputProvider`（registry 现有组件，不改 registry）；对应测试按生产组件树渲染，不在测试里额外包 Provider。
- **历史与终态**：真实 API 首页返回最新页，旧消息用 before_seq 向前翻页；按 run 分片并配 requestEpoch；非流式不等同失败，终态按所属 run 判定。后端状态为 `pending/running/success/error/timeout/interrupted`，没有独立 `cancelled`。本产品的澄清是 `success` + 未回答的 `ask_clarification`（写入后 `goto=END`，不是 interrupted），`interrupted` 一律按取消/中断；SDK 的 `thread.interrupt` 可能由非空 `next` 合成，不作判据。onFinish 用 SDK 传入的 `meta.run_id` 定位分片，不依赖只由 onStart 设置的 runId。
- **refetchHistory 采用带新鲜度判定的补齐式重载**（2026-11-24 两轮评审）：先失效并重取 runs 列表，只跳过「终态且 status/updated_at 与加载时一致」的旧 run；未加载、非终态、加载后已变化的 run 都重取，串行进行。「曾经加载成功」不等于内容仍新鲜，否则断线后继续产生的消息会被判成 not_found；全量重取在最大线程上又过重。
- **write-file 引用**：内容分 invalid/pending/not_found/ready/failed 五态，判定按目标优先——先定位目标 tool call 与 result，成功的非 append write_file 直接 ready（空文件也是 ready），append/str_replace 成功走 GET；只有目标或 result 缺失时才按加载状态判 pending/not_found。完整参数前不构造 ref、不 auto-open；来自 tool args 的原始路径走不预解码的 raw 编码通道。
- **视频编辑安全**：visualDirty 只对 `ai_image/ai_video/ai_visual` 生效，ViMax 保持原按钮行为；legacy recompose grace 保留。守卫范围限于详情页帧操作面板的「重生画面」（事故清单 E5）；同页「项目已更新，重新生成画面」入口不在本批，已登记 backlog。
- **live 用例机械排除**：总验收先捕获 `playwright test --list` 并显式检查退出码，再搜 live 路径。
- **已落地不得回退**：2026-11-22 `3070833f` 已保证流式中停止或回车保留草稿与附件（`input-box-stop-draft.dom.test.tsx`）。2026-11-21 模式阶梯已改 `sendMessage` 的 mode / thinking / plan / subagent / reasoning 矩阵。本批在这两处之上修改，不得整段回写旧实现。
- **findings backlog 复核**：2026-11-23 prepend 像素锚定项并入 PR3，不再推迟；沙箱密钥授权与诊断继续由 `docs/plans/2026-11-16-agent-media-channel-and-sandbox-secrets-plan.md` 承接。本批全部延后项已于 2026-11-24 定稿时登记 `docs/findings-backlog.md`（F5、F6、D3、P2-5、P2-6b、P2-6c、C8、C9、E6、E7 与详情页重新生成入口），不夹进功能 PR；F5 是否转 hotfix 由用户另行决定。其余 backlog 项属于 ViMax、voice、模型探针或非本批范围，全部显式推迟。

## 明确不做（已决策，不回流）

- **换账号或登录过期后仍看见上一账号的作品、线程、用量或草稿（F6）**：2026-11-24 整段移出。401 单飞清理、认证代际 QueryClient、登出清 `draft:` / `deerflow.thread-model.` / `writer_draft_` / sessionStorage、console 按 user id 分缓存均不实施；本批不新增需要账号清理的持久状态，上传 401 维持现有跳转。已登记 backlog。
- **视频 accepted-operation 与跨代轮询（E1）**：本地 operationId、accepted store、baselineUpdatedAt、sessionStorage 恢复、降频设计、跨标签页同步和 logout 订阅全部转入 pending 计划。三次契约复核确认本地 operationId 不在任务查询响应中，纯前端不能可靠归属 A/B 操作。
- **详情页「项目已更新，重新生成画面」**：走 `useReRenderTask`，不读帧面板的未保存状态；本批的付费守卫只覆盖帧面板「重生画面」。已登记 backlog。
- **确认卡**：现役 course-video 仅绑定 `web/file:read/file:write/bash`，没有 `video_engine`；后端 `plan_storyboard` 当前只服务 ViMax。不修、不删共用确认卡，也不纳入 C2/C4/C5/C11。
- **已证伪项**：B3（消息内已有下载卡）、B6（创建框关闭即卸载）、B8（已有 SSR 鉴权）、P0-3（SDK 并未按原文所述必然 clear 首流）不安排修复。
- **低优先项**：D3 导出、反馈按钮（P2-5）、document.title（P2-6b）、Token 瞬时旧值（P2-6c）、C8/C9、E6 死 UI、E7 资产重试按钮不夹带。已登记 backlog。

## Global Constraints

- **禁止手改 registry**：`frontend/src/components/ui/**`、`frontend/src/components/ai-elements/**` 只读；输入清空契约必须在自有 `InputBox`/threads 封装层适配。
- **不改 live 测试工作**：`frontend/tests/e2e/live/**` 与 `frontend/playwright.live.config.ts` 保持原样；新增回归只放 `frontend/tests/unit/` 或 `frontend/tests/e2e/`。
- **从包含本计划的提交开工**：当前 4-PR 修订未进入 main 时不得直接建执行 worktree。先经用户确认提交本计划，再以该提交为 BASE_SHA；预检不得出现旧 5-PR、账号隔离或认证代际 QueryClient 任务。
- **TDD**：每个实现任务先写并运行失败测试，再写最小实现使其通过；不得用纯 import/类型检查冒充行为验证，也不得给 mock 响应伪造生产 API 不存在的字段。
- **测试落位**：纯逻辑放 node 档 `.test.ts`；需要 document/renderHook/组件渲染的放 dom 档 `.dom.test.*`；Playwright 只放 `tests/e2e/`。既有 `src/**/*.test.*` 内联测试沿用原位置，新测试默认放 `tests/unit/`；滚动像素断言只能放 Playwright。
- **PR 纪律**：每批一个主题分支（PR1 `video-gen-edit-safety`、PR2 `chat-submission-acceptance`、PR3 `chat-thread-recovery`、PR4 `chat-artifact-ref`）。每个纵切走完红→绿后立即按该 Commit 步本地提交：「确认全绿」步同时跑 `cd frontend && pnpm typecheck`；提交前对改动文件跑 `pnpm exec prettier --write` 与 `pnpm exec eslint`；提交后用 `git status --porcelain` 确认没有本纵切的文件被落下。端到端用例与 mock 夹具单独一笔 `test(e2e)` 提交。执行 `make -C frontend check` 和相关测试后才可进入 PR 评审。前一批合入后，下一批 rebase 新 main。
- **文档纪律**：每批按实际执行日创建 `docs/changelog/changelog-YYYY-MM-DD-core-product-frontend-reliability-pr<N>.md`，包含「验收记录」；回本计划勾选步骤并维护唯一状态头及 changelog 链接。
- **长命令**：全量测试、构建和任何真实长任务一律后台运行；后台运行期间冻结被测代码和测试输入。
- **回退**：每批只依赖前端代码，无数据库或配置迁移；发现问题按 PR revert，不以新增第二层补丁掩盖根因。

## Review Focus

- **受理而非排队时清空**：onCreated 前失败/停止保留输入，onCreated 后即使 SSE 保持打开也可清空；后续停止/断线不得再次控制 composer，所有 rejection 必须被观察且每次失败只提示一次 → PR2 真实 SDK + 可控 SSE + 全调用方测试。
- **迟到写操作的所有者**：旧上传、旧 submit 回调、旧线程历史和同 run 慢旧请求不得写入当前操作 → PR2/PR3 分别验证 upload operation、回调归属与取消结果、thread generation 和 requestEpoch。视频跨代响应因缺服务端契约明确不在本批。
- **非终态不误报失败**：断线、history loading、interrupt、正常 tool-result 间隙都不得显示“运行已结束”；历史 run 的终态不得被当前 run 重写 → PR3 按 run 反例测试。
- **合法等待、合法空内容与真实写入失败**：流式半截 path 不得 auto-open，合法空文件是 ready，append/str_replace 不伪装内联全文，工具 Error 不得当成功 → PR4 完整参数与五态测试。
- **非目标行为不变**：remotion_course/ViMax 保留 legacy recompose grace 和原编辑按钮规则；live/付费用例不得进入默认全量 mock 套件 → PR1 类型门控与轮询矩阵、总验收机械检查。认证、QueryClient、运营台缓存和登出清理保持现状。

## 文件范围

| 批次 | 允许路径（allow） | 禁改路径（ban） | 共享基座触达 |
|---|---|---|---|
| PR1 视频编辑安全 | `frontend/src/core/video-gen/**`；`frontend/src/components/workspace/video-gen/video-gen-frame-ops-panel.tsx`；对应 tests 与 `tests/e2e/utils/mock-video-api.ts` | remotion/ViMax 专属组件、backend、config、accepted-operation/sessionStorage、`core/auth/**`、`core/api/**`、`core/query/**`、`core/console/**`、`query-client-provider.tsx` | 是，`core/video-gen/hooks.ts`，串行独占 |
| PR2 输入与上传 | `frontend/src/components/workspace/input-box.tsx`；两个 chat page；`frontend/src/app/workspace/agents/new/page.tsx`；`frontend/src/core/{threads,uploads}/**`；`frontend/src/core/api/api-client.ts`（仅 cancel 包装与中止错误归一化，见「可等待的取消」）；对应 tests、4 个既有 e2e spec 的 thread id/URL 断言与 `tests/e2e/utils/mock-api.ts` | registry、writer/gallery/voice、live 测试、backend、`core/auth/**`、`core/api/**` 其余文件、`core/query/**`、`core/console/**`、`query-client-provider.tsx` | 是，`core/threads/hooks.ts` 与 `core/api/api-client.ts` 的 cancel 包装，串行独占 |
| PR3 线程恢复 | `frontend/src/core/{threads,tasks}/**`；`frontend/src/components/workspace/{chats,messages}/**`；两个 chat page；对应 tests、受 mock 分片能力影响的既有 e2e spec 与 `tests/e2e/utils/mock-api.ts` | registry、writer/gallery/voice、live 测试、backend、`core/auth/**`、`core/api/**`、`core/query/**`、`core/console/**`、`query-client-provider.tsx` | 是，`core/threads/hooks.ts`，串行独占 |
| PR4 产物引用 | `frontend/src/core/artifacts/**`；`frontend/src/components/workspace/{messages,artifacts}/**`；对应 tests 与 `tests/e2e/utils/mock-api.ts` | registry、writer/gallery/voice、backend、`core/threads/**`、`core/auth/**`、`core/api/**`、`core/query/**`、`core/console/**`、`query-client-provider.tsx` | 是，`core/artifacts/{hooks,loader}.ts`；只消费 PR3 history 契约 |
| 共享文档 | 本计划；各 PR 对应的 `docs/changelog/changelog-YYYY-MM-DD-core-product-frontend-reliability-pr<N>.md`。`docs/plans/pending/2026-11-24-video-operation-generation-plan.md` 与 `docs/findings-backlog.md` 的延后项登记只允许出现在 Task 0 基线提交 | 其他 docs 新建/移动；功能 PR 不得修改 findings backlog | 否 |

---

### Task 0：执行基线与范围预检（不新增 PR）

- [x] **Step 1：冻结计划。** 本计划的基线提交须同时包含本计划、拆出的 pending 计划和 `docs/findings-backlog.md` 的延后项登记（F5、F6、D3、P2-5、P2-6b、P2-6c、C8、C9、E6、E7 与详情页重新生成入口），任一缺失先补齐再提交。以该提交为 `BASE_SHA` 并从它创建 worktree；定稿后计划再有修订时，重新提交并更新 BASE_SHA。确认其中只有 4 个纯前端 PR，执行顺序为 PR1→PR4，没有账号隔离任务，也没有 accepted-operation 实施步骤。F5 是否转 hotfix 由用户另行决定。
- [x] **Step 2：核对真实 API 与测试夹具。** 开工前确认 run-messages 首页为最新页、支持 before_seq；`GET /threads/{id}/runs` 路由不声明 `limit`，固定按 `list_by_thread` 默认的 100 条返回（SDK 发出的 `?limit=10` 被忽略）；视频 mutation/list/detail 均无操作代际。若这些契约已变化，先回本计划修订对应决策，不得沿用旧假设。预检 `git status --short` 和 allow/ban，后续所有 diff 均以 BASE_SHA 审查。

### Task 1（PR1）：AI 生图/AI 生视频的编辑真实性、取消轮询与失败反馈

**Files：**
- Modify：`frontend/src/core/video-gen/hooks.ts`
- Modify：`frontend/src/components/workspace/video-gen/video-gen-frame-ops-panel.tsx`
- Modify：`frontend/tests/e2e/utils/mock-video-api.ts`（补 regenerate 请求记录及 visual-source/layout/bulk 可控失败）
- Test：`frontend/tests/unit/core/video-gen/video-task-refetch-interval.test.ts`
- Test：`frontend/src/core/video-gen/hooks-invalidation.dom.test.tsx`
- Test：`frontend/tests/unit/components/workspace/video-gen/video-gen-frame-ops-panel.dom.test.tsx`（新增）
- Test：`frontend/tests/e2e/video-gen-edit-safety.spec.ts`（新增）

**Interfaces：**
- `visualDirty = promptChanged || sourceChanged`，与既有 `narrationDirty` 分开；`supportsVisualDirtyGuard(videoType)` 只对 `ai_image/ai_video/ai_visual` 启用。visualDirty 只挡帧操作面板的“重生画面”，narrationDirty 只挡配音；ViMax 与其他类型保持原按钮行为。
- `videoTaskRefetchInterval(status, pollWhile?)`: `cancelled` 无条件返回 false；`ready/failed` 继续保留现有 legacy `pollWhile` 宽限语义，活动态和其他非目标分支不变。不得删除 `recomposeGraceUntilRef`，不得让 remotion_course/ViMax 失去现有保护。
- `useUpdateFrameVisualSource/useUpdateFrameLayout/useBulkDisableGraphic`：在各自同一 onError 中先恢复 ctx.prev，再 toast 一次真实错误；不在调用组件重复提示。
- **明确不创建：** `accepted-operation.ts`、本地 operationId 轮询归属、sessionStorage 恢复记录、跨标签页同步和 logout 订阅。E1 旧 ready 停机族与超长队列恢复由 `docs/plans/pending/2026-11-24-video-operation-generation-plan.md` 承接。

**纵切 A：cancelled 终态停轮询**

- [x] **Step 1：写取消终态与 legacy 轮询矩阵（红）。** 在 `video-task-refetch-interval.test.ts` 断言：普通 cancelled 任务立即停轮询，即使 pollWhile 为 true 也不继续；ready/failed 在无 grace 时停止、有 grace 时维持 2 秒；running/pending/composing/qc_repair 等既有行为不变。remotion_course/ViMax 的 legacy recompose grace 保持，ai_image/ai_video/ai_visual 也不在本批提前删除 grace；断言无 accepted-operation/sessionStorage 写入。
- [x] **Step 2：运行确认旧实现失败。** 执行 `cd frontend && pnpm exec vitest run tests/unit/core/video-gen/video-task-refetch-interval.test.ts`，记录 cancelled 仍继续轮询的失败项。
- [x] **Step 3：实现 cancelled 停轮询（绿）。** `videoTaskRefetchInterval` 对 `cancelled` 无条件返回 false，直接复用现有终态语义；其余状态与 legacy `pollWhile` 宽限保持不变，不删除 `recomposeGraceUntilRef`。
- [x] **Step 4：重跑 Step 2 确认全绿（含 typecheck）。**
- [x] **Step 5：Commit。** 暂存 `video-task-refetch-interval.test.ts` 与 `frontend/src/core/video-gen/hooks.ts`，提交 `fix(video-gen): cancelled 终态停止轮询`。

**纵切 B：付费前守卫与乐观回滚提示**

- [x] **Step 6：写付费前守卫与错误反馈测试（红）。** 在 `hooks-invalidation.dom.test.tsx` 与新增的 `video-gen-frame-ops-panel.dom.test.tsx` 覆盖：三类白名单任务 prompt/source 未保存时帧面板视觉重生禁用且不发付费 mutation；narrationDirty 只挡配音，visualDirty 不误挡配音；ViMax 在同样未保存编辑下保持原按钮规则；`useUpdateFrameVisualSource/useUpdateFrameLayout/useBulkDisableGraphic` 失败分别回滚且 toast 恰好一次。
- [x] **Step 7：运行确认旧实现失败。** 执行 `cd frontend && pnpm exec vitest run src/core/video-gen/hooks-invalidation.dom.test.tsx tests/unit/components/workspace/video-gen/video-gen-frame-ops-panel.dom.test.tsx`，记录类型门控、dirty guard 和三处 toast 的实际失败项。
- [x] **Step 8：实现分类型编辑守卫与回滚提示（绿）。** 只在白名单任务上以 visualDirty 禁用帧面板“重生画面”并提示先保存；`supportsVisualDirtyGuard(videoType)` 做类型门控，narrationDirty 保持独立，ViMax 原行为不变，音色过滤保持 2026-11-23 收敛后的目录。在三个 mutation 各自的 onError 中先恢复 `ctx.prev`，再 toast 一次真实错误；不改其他 mutation，不顺手清理 dead TaskCard 或重写列表。
- [x] **Step 9：重跑 Step 7 确认全绿（含 typecheck）。**
- [x] **Step 10：Commit。** 暂存本纵切的测试、`hooks.ts` 与 `video-gen-frame-ops-panel.tsx`，提交 `fix(video-gen): 未保存编辑禁止付费重生，乐观编辑失败回滚并提示`。

**纵切 C：行为验收与收尾**

- [x] **Step 11：无付费行为验收。** 先给 `mock-video-api.ts` 补 regenerate 请求记录与 visual-source/layout/bulk 可控失败并做夹具自证，再写 `tests/e2e/video-gen-edit-safety.spec.ts` 并执行 `cd frontend && pnpm exec playwright test tests/e2e/video-gen-edit-safety.spec.ts`：覆盖 ai_image/ai_video/ai_visual 在帧面板未保存 prompt/source 时点“重生画面”零付费请求、visual/narration dirty 独立、ViMax 非目标行为、三处乐观失败回滚与提示、cancelled 后请求停止。
- [x] **Step 12：Commit。** 暂存 `mock-video-api.ts` 与 `video-gen-edit-safety.spec.ts`，提交 `test(e2e): video-gen 编辑安全用例与 mock 夹具`。
- [x] **Step 13：批次门禁与收尾。** 执行 `make -C frontend check` 和相关 video-gen 既有回归；确认无 remotion/ViMax 专属实现或其他未提交文件进入 diff。写 PR1 changelog「验收记录」，明确记录 E1 跨代轮询仍未修复、去向为 pending 计划；勾选本任务并更新状态头，`python3 scripts/check_docs_convention.py` 过后提交 `docs(frontend-reliability): PR1 验收记录`。push 与开 PR 另行确认。

### Task 2（PR2）：输入受理契约与上传会话

**Files：**
- Create：`frontend/src/core/threads/submission.ts`
- Modify：`frontend/src/components/workspace/input-box.tsx`
- Modify：`frontend/src/app/workspace/chats/[thread_id]/page.tsx`
- Modify：`frontend/src/app/workspace/agents/[agent_name]/chats/[thread_id]/page.tsx`
- Modify：`frontend/src/app/workspace/agents/new/page.tsx`
- Modify：`frontend/src/core/threads/errors.ts`
- Modify：`frontend/src/core/threads/hooks.ts`
- Modify：`frontend/src/core/uploads/api.ts`
- Modify：`frontend/src/core/uploads/prompt-input-files.ts`
- Modify：`frontend/src/core/api/api-client.ts`（仅 cancel 包装）
- Modify：`frontend/tests/e2e/utils/mock-api.ts`
- Modify（既有测试同步）：`frontend/tests/unit/core/threads/thread-submit-mode-context.dom.test.tsx`、`frontend/tests/unit/core/api/api-client-cancel.dom.test.ts`
- Modify（既有 e2e，仅 thread id / URL 断言）：`frontend/tests/e2e/chat.spec.ts`、`agent-chat.spec.ts`、`agent-new-chat-guide.spec.ts`、`thread-history.spec.ts`
- Test：`frontend/tests/unit/core/threads/submission.test.ts`（新增，node 档）
- Test：`frontend/tests/unit/core/threads/stream-submission-lifecycle.dom.test.tsx`（新增）
- Test：`frontend/tests/unit/components/workspace/input-box-submission.dom.test.tsx`（新增）
- Test：`frontend/tests/unit/core/uploads/api.dom.test.ts`（新增）
- Test：`frontend/tests/unit/app/workspace/agents/agent-chat-page.dom.test.tsx`
- Test：`frontend/tests/unit/app/workspace/agents/new-agent-submission.dom.test.tsx`（新增；覆盖 bootstrap、普通聊天、保存三个调用点）
- Test：`frontend/tests/unit/app/workspace/chats/chat-page-autosend.dom.test.tsx`（新增；普通会话 auto-send 的 rejection）
- Test：`frontend/tests/e2e/chat-submission.spec.ts`（新增）

**Interfaces：**
- `sendMessage(...)`: 保持公开返回类型 `Promise<void>`，返回值本身就是 accepted Promise；不再把 `thread.submit()` 的返回值当作受理或后台结束，也不向调用方暴露对象。
- `accepted`: 上传完成、SDK `onCreated` 后 resolve；busy、上传/创建前失败、受理前停止则 reject。每个 submit 调用登记独立 submission identity；受理后建立 runId→submission 映射。
- `busy`: 上一次发送在途（`sendInFlightRef`）或 `thread.isLoading` 为真即视为 busy，accepted 以 `kind: "busy"` reject、保留输入、不提示。SDK 队列严格串行，`thread.isLoading` 为真意味着上一条流（含停止后的收尾）还没结束。
- `submission.ts`（纯逻辑，node 档可测）：每次实际 submit 产生一个带递增 id 的 submission，持有 `accepted`、`completion` 两个 Promise 与只生效一次的 `accept()` / `reject(reason)` / `complete()`；`completion` 内部已 catch，永不产生 unhandled rejection；rejection reason 以 `kind: "busy" | "stopped" | "failed"` 区分。hook 只按下面的归属规则把 SDK 回调接到对应 submission，不另写第二套状态机。
- **回调归属**：任一时刻最多只有一个 submission 可能收到 SDK 回调。无 meta 的回调（onCreated、创建前的 onError、onStop）一律归属当前 submission；onCreated 之后按 run_id→submission 归属带 meta 的 onFinish/onError。`handleStreamStart`、错误 toast、optimistic 清理、URL 与线程 metadata 更新只在回调归属当前 submission 时执行；不属于任何在途 submission 的回调（如 mount rejoin 的历史 run）走现有路径。
- `completion`: 仅由 hook 内部持有，由 SDK onFinish/onError/onStop 与取消结果驱动后台状态和一次性清理；不进入公共返回类型，不控制 composer，且必须有内部 rejection 处理。accepted 后停止保持 accepted 已 resolve，但仍须收尾 completion。
- `onSend/onStart/onSendError`：保留现有 `onSend(requestedThreadId)` 与 `onStart(threadId, runId)` 形态。onSend 在附件转换与上传完成、提交 run 前触发，仅用预生成 UUID 更新地址栏，不提前改变 controlled threadId/isNewThread；onStart/onCreated 确认创建状态。新增 `onSendError(requestedThreadId, { wasNewThread })`，只在本次 onSend 已触发、onCreated 前的失败或停止时调用；URL 回退仅在 wasNewThread=true 且地址栏仍是本次 requestedThreadId 时执行，已有线程保持原 URL。两个 page 在本 PR 完成接线。`undefined → 当前 submission 的预生成 UUID` 是身份认领，不得取消本次上传。
- `onStop`：由 hook 内部按 submission 收尾，不向外抛控制异常。停止路径：`thread.stop()` 中断本地流；已受理的 submission 再 `await cancelRun`，结果写入 completion——受理成功记 `stopped`，失败记 `cancelFailed` 并 toast 一次真实错误（重试入口由 PR3 提供）；受理前停止尚无 runId，不发 cancel。迟到回调绑定具体 submit 调用，不得只读可变 current operation。
- `cancelRun(threadId, runId): Promise<CancelOutcome>`：即 `core/api/api-client.ts` 的 cancel 包装，是本 PR 对 `core/api/**` 的核心改动（另有同文件的中止错误归一化，见「现行决策」）。`CancelOutcome = {status:"cancelled"} | {status:"not_cancellable"} | {status:"failed"; error}`，永不 reject。SDK 经 `thread.stop()` 发出的同一调用忽略返回值，因此不再产生 unhandled rejection。`not_cancellable` 维持现有 409 兼容语义（清 reconnect key、视为已停止）。同一 runId 的并发取消可合并为一次请求，服务端对重复取消幂等。
- `uploadFilesWithProgress(threadId, files, onProgress?, options?: { signal?: AbortSignal; idleTimeoutMs?: number })`: 第四参可选；默认 120 秒无网络进展即失败，上传与等待响应共用空闲超时。附件 URL fallback 转换也接收 signal；本批承诺切线程/卸载可取消，不新增单独取消按钮。
- **agents/new 的输入受控**：`agents/new/page.tsx` 的 `PromptInput` 外包一层 `PromptInputProvider`（registry 现有组件，不改 registry），使正文只在 accepted 后清空。
- **Produces for PR3：** 冻结的 `onSend/onStart/onSendError/onStop` 生命周期（含两个 page 的 URL 回退接线）与 `CancelOutcome`；PR3 只消费取消结果做终态同步与重试入口，并补断线恢复，不重新定义 accepted/composer 契约。

**纵切 A：受理契约与 submission 归属**

- [x] **Step 1：写受理契约与全调用方测试（红）。**
  - `submission.test.ts`（node 档）：同一 submission 的 accepted 只 settle 一次；迟到的 accept/reject/complete 不生效；completion 不产生 unhandled rejection。
  - `stream-submission-lifecycle.dom.test.tsx`（真实 SDK + 可控 fake client，可控流写法可复用 `stream-isloading-at-submit.dom.test.tsx`）：`thread.submit()` 本身不等待流结束，只有对应 submission 的 onCreated 才 resolve accepted；onCreated 后 SSE 保持打开时 composer 已清空，随后停止/断线不再控制 composer。A 受理前停止→SDK 空闲后同线程 B 重试：只出现一次 onCreated 且归属 B，A 不触发 onStart、toast 或 optimistic 清理；SDK 未收尾时提交按 busy 拒绝。accepted 后停止必须收尾 completion，迟到 finish/error 不重复 settle。
  - `input-box-submission.dom.test.tsx`：①受理前停止/创建前失败保留正文、附件和 `draft:{tid}`；②纯附件会发送，空白正文且无附件不发送；③busy 保留输入；④模型自动校正分支不提前 resolve。
  - 新建 Agent 按生产组件树（含 `PromptInputProvider`）用真实 PromptInput（`new-agent-submission.dom.test.tsx`、`agent-chat-page.dom.test.tsx`）验证 bootstrap、普通聊天、保存三处 rejection 均被观察，普通聊天失败不清正文，保存失败不显示“已请求保存”，busy/上传/创建错误每次 toast 恰好一次。
  - 普通会话 page 的 auto-send（`chat-page-autosend.dom.test.tsx`）：rejection 被观察，无 unhandled rejection。
  - `thread-submit-mode-context.dom.test.tsx` 改用真实 `useStream` + 可控 fake client：现有 mock 的 submit 只 resolve、不触发 onCreated，新的 sendMessage 会等到超时。
- [x] **Step 2：运行确认旧实现失败。** 执行 `cd frontend && pnpm exec vitest run tests/unit/core/threads/submission.test.ts tests/unit/core/threads/stream-submission-lifecycle.dom.test.tsx tests/unit/components/workspace/input-box-submission.dom.test.tsx tests/unit/app/workspace/agents/agent-chat-page.dom.test.tsx tests/unit/app/workspace/agents/new-agent-submission.dom.test.tsx tests/unit/app/workspace/chats/chat-page-autosend.dom.test.tsx tests/unit/core/threads/thread-submit-mode-context.dom.test.tsx`。记录清空时机、三调用方与 auto-send 的 rejection、模型分支、回调归属和 busy 判定的实际失败项。
- [x] **Step 3：实现双生命周期、归属与受控输入（绿）。** 在 `submission.ts` 实现 Interfaces 约定的受理/完成双生命周期；hook 只做接线：按归属规则把 onCreated/onError/onFinish 接到对应 submission，共享副作用按归属门控；创建前异常/onError/onStop 只 settle 对应 submission，completion 挂接 SDK 回调并内部 catch；busy 并入 `thread.isLoading`。InputBox、两个 page 和新建 Agent 普通聊天直接把 Promise 交给 PromptInput，`agents/new` 的输入包进 `PromptInputProvider`；bootstrap/auto-send 显式 catch，保存 catch 只恢复状态，不重复 toast。模型自动校正后的 `setTimeout` 只负责延迟，外层返回/await 内层 accepted Promise；保留 `normalizeMode` 的 mode / thinking / plan / subagent / reasoning 矩阵与现有 `onSubmitCapture`；受理前停止以 `kind="stopped"` reject accepted。hook 统一负责 busy、上传和创建错误提示，所有调用方只观察 rejection 或恢复本地状态。不得修改 `prompt-input.tsx`。
- [x] **Step 4：重跑 Step 2 及既有 `input-box-stop-draft.dom.test.tsx` 确认全绿（含 typecheck）。**
- [x] **Step 5：Commit。** 暂存 `submission.ts`、`hooks.ts`、`input-box.tsx`、三个 page、`errors.ts`、`thread-submit-mode-context.dom.test.tsx` 与本纵切测试，提交 `fix(chat): 提交以 onCreated 为受理点，受理前失败或停止保留输入`。

**纵切 B：上传会话与 URL 时机**

- [x] **Step 6：写上传生命周期与 URL 时机测试（红）。** `api.dom.test.ts`（新增）：无进展 120 秒 reject、持续进展重置计时、预 aborted signal 不发 XHR。`input-box-submission.dom.test.tsx` 追加：⑤A 上传切 B 时 abort A 且不污染 B；⑥附件 URL fallback 转换中取消不进入上传/submit；onSend 在上传完成、提交 run 前触发。`agent-chat-page.dom.test.tsx` 追加 onSendError 的 URL 回退：新线程回 `/chats/new`，已有线程不离开原 URL，地址栏已不是本次 requestedThreadId 时不回退。
- [x] **Step 7：运行确认旧实现失败。** 执行 `cd frontend && pnpm exec vitest run tests/unit/core/uploads/api.dom.test.ts tests/unit/components/workspace/input-box-submission.dom.test.tsx tests/unit/app/workspace/agents/agent-chat-page.dom.test.tsx`，记录上传生命周期、onSend 时机和 URL 回退的失败项。
- [x] **Step 8：实现上传所有者、取消与 URL 时机（绿）。** 每次网络进展重置 idle timer，load/error/abort/外部取消都清理监听与 timer，只 settle 一次；附件转换后、上传后、submit 前重新检查 signal/operation。用递增 upload operation + AbortController 管理 A→B 和卸载，旧 finally 只释放自己；onSend 按 Interfaces 后移，自身预生成 UUID 回写不得 abort 自己。两个 page 均接 `isUploading/uploadProgress`（普通会话 page 已接，核对即可）与 `onSendError`，上传 401 维持现有跳转。同步改造 mock-api 的 thread create、SSE Content-Location 等预生成 UUID 回显，不得继续固定 MOCK_THREAD_ID；runs/history 分片能力留给 PR3。随之更新 4 个既有 e2e spec 里依赖固定 `MOCK_THREAD_ID` 的 thread id / URL 断言，以 create 请求回显的 thread id 为准。
- [x] **Step 9：重跑 Step 7 与 Step 2，并执行 `cd frontend && pnpm exec playwright test tests/e2e/chat.spec.ts tests/e2e/agent-chat.spec.ts tests/e2e/agent-new-chat-guide.spec.ts tests/e2e/thread-history.spec.ts`，确认全绿（含 typecheck）。**
- [x] **Step 10：Commit。** 暂存 `core/uploads/**`、`hooks.ts`、`input-box.tsx`、两个 chat page、`mock-api.ts`、4 个既有 e2e spec 与本纵切测试，提交 `fix(uploads): 上传空闲超时与跨线程取消，onSend 后移至上传完成`。

**纵切 C：可等待的取消**

- [x] **Step 11：写取消结果测试（红）。** `api-client-cancel.dom.test.ts` 扩展：cancel 返回 500/网络错误时包装返回 `{status:"failed"}` 且不产生 unhandled rejection；`not_cancellable` 409 维持现有语义；经真实 SDK 的 `thread.stop()` 路径触发时同样无 unhandled rejection。`stream-submission-lifecycle.dom.test.tsx` 追加：受理后停止且 cancel 受理 → completion 以 `stopped` 收尾；cancel 失败 → 以 `cancelFailed` 收尾、toast 恰好一次、不显示 interrupted；受理前停止不发 cancel。
- [x] **Step 12：运行确认旧实现失败。** 执行 `cd frontend && pnpm exec vitest run tests/unit/core/api/api-client-cancel.dom.test.ts tests/unit/core/threads/stream-submission-lifecycle.dom.test.tsx`，记录真实取消失败仍抛出、completion 无法按取消结果收尾的失败项。
- [x] **Step 13：实现 cancel 包装与停止路径（绿）。** `core/api/api-client.ts` 的 cancel 包装改为永不 reject 并返回 `CancelOutcome`；hook 的停止路径按 Interfaces 先 `thread.stop()` 再 `await cancelRun`，结果写入 submission 的 completion。不改 `core/api/**` 的其他文件，不涉及认证。
- [x] **Step 14：重跑 Step 12 确认全绿（含 typecheck）。**
- [x] **Step 15：Commit。** 暂存 `core/api/api-client.ts`、`submission.ts`、`hooks.ts` 与本纵切测试，提交 `fix(chat): 取消请求可等待，取消失败不再成为未处理拒绝`。

**纵切 D：行为验收与收尾**

- [x] **Step 16：mock 行为验收。** 写 `tests/e2e/chat-submission.spec.ts` 并执行 `cd frontend && pnpm exec playwright test tests/e2e/chat-submission.spec.ts`，实际经过“受理前停止”“受理后长流清空”“失败重试”“纯附件”“A 上传切 B”“新会话首条附件消息”“创建前失败回退 /chats/new”，并断言无未处理 rejection、无重复 toast，不能只检查函数被调用。
- [x] **Step 17：Commit。** 暂存 `chat-submission.spec.ts`，提交 `test(e2e): 提交受理与上传会话用例`。
- [x] **Step 18：批次门禁与收尾。** 执行 `make -C frontend check`；确认无 registry/live/其他未提交文件进入 diff。写 PR2 changelog「验收记录」，勾选本任务并更新状态头，`python3 scripts/check_docs_convention.py` 过后提交 `docs(frontend-reliability): PR2 验收记录`。push 与开 PR 另行确认。

### Task 3（PR3）：长 Agent 会话的幂等历史、真实终态、URL 与断线恢复

**Files：**
- Modify：`frontend/src/core/threads/hooks.ts`
- Modify：`frontend/src/core/threads/types.ts`
- Create：`frontend/src/core/threads/recovery.ts`
- Modify：`frontend/src/core/threads/errors.ts`
- Modify：`frontend/src/core/tasks/subtask-status-sync.ts`
- Modify：`frontend/src/components/workspace/messages/context.ts`
- Modify：`frontend/src/components/workspace/messages/message-list.tsx`
- Modify：`frontend/src/components/workspace/messages/virtual-message-list.tsx`
- Modify：`frontend/src/components/workspace/chats/use-thread-chat.ts`（仅确需共享 URL/history 状态时）
- Modify：两个 chat page
- Modify：`frontend/tests/e2e/utils/mock-api.ts`
- Modify（受 mock 分片能力影响的既有 e2e spec，随实际影响面定）
- Test：`frontend/tests/unit/core/threads/thread-history-pagination.test.ts`
- Test：`frontend/tests/unit/core/threads/thread-history-reset.dom.test.tsx`（新增）
- Test：`frontend/tests/unit/core/threads/thread-history-idempotence.dom.test.tsx`（新增）
- Test：`frontend/tests/unit/core/threads/recovery.dom.test.ts`（新增）
- Test：`frontend/tests/unit/core/tasks/subtask-status-sync.test.ts`
- Test：`frontend/tests/unit/components/workspace/messages/message-list-scroll.dom.test.tsx`（新增；只测状态重置，不测像素）
- Test：`frontend/tests/e2e/chat-thread-recovery.spec.ts`（新增）

**Interfaces：**
- `fetchAllRunMessages(threadId, runId, fetchImpl?)`: 首页无游标取最新 200 条；has_more 时以本页第一条 seq 设置 `before_seq` 向前取旧页，最终过滤 middleware caller 并按 seq 升序返回。取消 25 页/5000 条静默截断，仅以游标必须严格前进、不得重复作为防死循环保护；禁止沿用“首页是最早页 + after_seq”的错误 mock。
- `useThreadRuns(threadId)`: 显式调用 `runs.list(threadId, { limit: 100 })`。后端路由当前忽略该参数、固定上限 100；显式传值是防御——路由日后支持 `limit` 时不致回退到 SDK 默认的 10 条。单测和共享 mock 均断言 limit=100，超过 100 仍是明确未覆盖边界。
- `useThreadHistory(threadId)`: 用 generation 丢弃旧线程响应，以 `historyShards: Map<runId, RunMessage[]>` 保留 run/seq，每个分片同时记录加载时该 run 的 `status` 与 `updated_at`；`requestEpochByRun` 只让该 run 最新请求原子替换分片。loadedRunIds 只在完整成功后写入，inFlightRunIds 单独去重；onFinish 对 meta 指向的 run 强制重取。按 `(run_id, message.id)` 去重，缺 id 用该 run 内 seq，run 按旧→新、run 内按 seq 拼接。
- `deriveSubtasks(messages, { runId?, runStatus?, isHistoryLoading?, isRecoverableDisconnect? })`: 每次只处理一个 run 的消息和该 run 的状态；有 tool result 先按原 parser，无 result 时 `success→completed`、`error/timeout→failed`、`interrupted→取消/中断`，其余运行/加载/断线状态不得判 failed。澄清不是 interrupted：`success` + 未回答的 `ask_clarification`（以 `isClarificationToolMessage` 判定）的 run 里，已有 tool result 的子任务保持原状态。history loading/断线不得重写旧 run 终态。
- **onFinish 归属**：`onFinish(state, meta)` 用 SDK 传入的 `meta.run_id` / `meta.thread_id` 定位要强制重取的分片并校验 thread generation；mount 自动 rejoin 不经过 onCreated，不能依赖只由 onStart 设置的 runId。
- **Produces for PR4：** `ThreadContextType` 在现有 `thread` 旁额外暴露可选 `isHistoryLoading: boolean`、`hasMoreHistory: boolean` 与 `refetchHistory(): Promise<void>`；主聊天两个 page 必须提供，其他消费者可缺省。hasMoreHistory=true 表示仍有未加载旧 run，不能因 isHistoryLoading=false 就判 not_found。refetchHistory 是用户显式触发、带新鲜度判定的补齐式重载：先失效并重取 runs 列表，再按旧→新串行处理最多 100 个 run——未成功加载、非终态、或 `status`/`updated_at` 与加载时不同的 run 重取，终态且未变的旧 run 跳过；每个请求受该 run 的 epoch 约束并等待既有在途请求 settled，全部 settled 后才结束；任一失败保留旧分片、不把该 run 标记为新鲜，并 reject 以便重试。loadMoreHistory 只前进一个 run，两者不等同。
- `reconnectActiveRun(): Promise<void>`：仅供用户在连接类错误后手动重试，按 `(threadId, runId)` 单飞；mount 自动恢复继续由 SDK `reconnectOnMount` 独占，isLoading 时不重复 join。join Promise 只表示排队，连接结果仍由 onFinish/onError 驱动；无 runId 或 stale run 404 清 key 后失效 history/runs，业务 4xx/取消不盲目重连。
- **Consumes from PR2：** onSend/onStart/onSendError/onStop 已冻结（含 page 侧 URL 回退）；PR3 只消费 `CancelOutcome` 做停止后的同步，并补断线恢复。`cancelled` → 有界刷新 runs 与当前分片（running→interrupted）；`failed` → 保留可重试错误，恢复入口的重试即再次调用同一 cancel，不伪造 interrupted；`not_cancellable` → 维持现有兼容语义。

**纵切 A：before_seq 分页与 requestEpoch 分片**

- [x] **Step 1：写真实分页、history 幂等和同 run 竞态测试（红）。** 用 451 条消息模拟首页 `252..451`、before_seq 页 `52..251`、`1..51`，最终必须恰好 `1..451`；再用 5001 条廉价 mock 证明超过原 25 页仍不丢首条，游标不前进/重复时显式失败而非死循环。覆盖首次 `run-1:[m1]`、onFinish 重取 `[m1,m2]` 后原子替换，新增 run-2 接续，A 慢历史切 B 不拼入；同 run A/B 请求按 B→A 完成时仍保留 B 的最终分片，旧 run 失败后可重试且 loadedRunIds 不提前写入。runs API newest-first 时 flatten 仍按旧→新，并断言 runs.list 请求 limit=100。
- [x] **Step 2：运行确认旧实现失败。** 执行 `cd frontend && pnpm exec vitest run tests/unit/core/threads/thread-history-pagination.test.ts tests/unit/core/threads/thread-history-reset.dom.test.tsx tests/unit/core/threads/thread-history-idempotence.dom.test.tsx`，记录真实首页方向、同 run 覆盖和切线程重置的失败；不得把已撤回的“每次切线程串台”当成唯一测试前提。
- [x] **Step 3：实现 before_seq + generation/requestEpoch 分片（绿）。** useThreadRuns 显式传 limit=100。threadId 变化清 messages/historyShards/loadedRunIds/inFlightRunIds/requestEpochByRun/hasMore；每次加载捕获 generation + runId + epoch，只有当前代、当前 run 最新 epoch 且完整成功才替换分片。分页失败保留旧分片并允许重试，游标必须严格前进。复用 PR2 的预生成 UUID 回显，仅为 mock-api 新增 runs/history 分片能力。
- [x] **Step 4：重跑 Step 2 确认全绿（含 typecheck）。**
- [x] **Step 5：Commit。** 暂存本纵切测试、`hooks.ts`、`types.ts`、受影响的既有 e2e spec 与 `mock-api.ts`，提交 `fix(chat): run 历史改用 before_seq 向前翻页，按 run 分片并防慢旧请求覆盖`。

**纵切 B：按 run 终态、取消同步与恢复 owner**

- [x] **Step 6：写按 run 终态、取消与恢复反例（红）。** 覆盖 run-1 error 无 result + run-2 running、run-1 interrupted + run-2 success，旧任务不得被当前 run 改判；当前 run 分别覆盖 success、error/timeout、interrupted（用户停止）、success + 未回答的 `ask_clarification`（澄清）、history loading、断线和正常 running。onFinish 只刷新 `meta.run_id` 对应的分片：mount 自动 rejoin、没有 onCreated 时同样成立。停止后分别模拟：cancel 受理 → running→interrupted，断言有界 refetch runs 和当前分片；cancel 500/网络失败 → 无 unhandled rejection、不显示 interrupted、出现可重试错误，重试即再次调用同一 cancel；`not_cancellable` → 维持现有语义。mount 自动 rejoin 未结束时手动重连不得二次 join，join resolve 不得直接显示恢复成功。
- [x] **Step 7：运行确认旧实现失败。** 执行 `cd frontend && pnpm exec vitest run tests/unit/core/threads/recovery.dom.test.ts tests/unit/core/tasks/subtask-status-sync.test.ts`，记录按 run 终态和取消恢复的失败项。
- [x] **Step 8：实现按所属 run 的终态、取消同步与恢复 owner（绿）。** MessageList 按 shard/run 调用 derive，保留 runId 和 runsById，不给整个扁平列表套当前 runStatus；agents/new 的可选默认值保持原行为。hook 的 onFinish 用 meta 定位分片并校验 generation，重取后失效 `["thread", threadId]`。错误详情优先使用当前 `thread.error`，runs API 没有 error 字段时使用状态级通用文案，不虚构详细原因。停止后按 PR2 的 `CancelOutcome` 同步：`cancelled` 才做有界刷新，`failed` 保留可重试错误并在恢复入口提供重试，不伪造终态。mount rejoin 归 SDK，手动 reconnect 按 Interfaces 单飞。
- [x] **Step 9：重跑 Step 7 确认全绿（含 typecheck）。**
- [x] **Step 10：Commit。** 暂存本纵切测试、`recovery.ts`、`subtask-status-sync.ts`、`hooks.ts`、`errors.ts` 与 `message-list.tsx`，提交 `fix(chat): 子任务终态按所属 run 判定，取消后有界同步，手动重连单飞`。

**纵切 C：滚动重置、history 契约与补齐式重载**

- [x] **Step 11：写 URL、滚动与补齐式重载测试（红）。** 发送后地址栏脱离 `/new`，运行中刷新仅 rejoin 一次，创建前失败回退（PR2 已实现，此处做回归）。Playwright 用至少 60 个不同高度消息组验证：长线程初次水合最后一组可见且底部误差 ≤2px；prepend 前后首个原可见组 `getBoundingClientRect().top` 差值 ≤4px（既有实现实测偏移 190.5px，证据见 Spec 所列 changelog）；线程 A 上翻后切 B，B 仍初次落底；静态/虚拟阈值两侧和加载旧页均不跳底。补齐式重载：99 个终态且加载后未变的旧 run 不重取；1 个加载时为 running、之后变 success 的 run 必须重取并补齐新增消息；刷新失败的 run 不被标记为新鲜，可再次重试。
- [x] **Step 12：运行确认旧实现失败。** 执行 `cd frontend && pnpm exec vitest run tests/unit/components/workspace/messages/message-list-scroll.dom.test.tsx`，并先跑一次 `chat-thread-recovery.spec.ts` 记录失败项；像素位移只能在真实 Playwright 验收，happy-dom 不得冒充布局证据。
- [x] **Step 13：实现滚动重置、history 契约与补齐式重载（绿）。** 给整个 Conversation 加 `key={threadId}`，同时重置 StickToBottom 与虚拟化 refs，传 `initial="instant"` 而不修改 registry；仅初次水合落底，prepend 补偿未测高行估计高度与实测高度差并保留锚点。复用 PR2 已冻结的 onSend/onStart/onSendError，不在 onSend 提前翻 controlled threadId。通过 ThreadContextType 提供 isHistoryLoading/hasMoreHistory/refetchHistory，按 Interfaces 实现带新鲜度判定的补齐式重载并等待 settled；保持 thread.isThreadLoading 与自定义 history loading 的可区分组合，不重新引入 P0-3 假设。
- [x] **Step 14：重跑 Step 12 确认全绿（含 typecheck）。**
- [x] **Step 15：Commit。** 暂存本纵切测试、`core/threads/hooks.ts`、`types.ts`、`messages/**`、`context.ts`、确需改动的 `use-thread-chat.ts` 与两个 chat page，提交 `fix(chat): 切线程整体重置滚动并首次水合落底，暴露带新鲜度判定的 history 加载契约`。

**纵切 D：行为验收与收尾**

- [x] **Step 16：长任务 mock 验收。** 重跑 Step 2、Step 7、Step 12 的 vitest 命令全绿；执行 `cd frontend && pnpm exec playwright test tests/e2e/chat-thread-recovery.spec.ts --repeat-each=3 --workers=1`，覆盖同 run 重取、新 run 接续、真实分页、长历史重开、运行中刷新、取消终态、断线重连、interrupt 等待和 Step 11 像素断言。记录 runs list 仅 100 条的未覆盖边界，不用 mock 伪造分页能力。
- [x] **Step 17：Commit。** 暂存 `chat-thread-recovery.spec.ts` 与 `mock-api.ts` 的本纵切改动，提交 `test(e2e): 长会话恢复与滚动锚定用例`。
- [x] **Step 18：批次门禁与收尾。** 执行 `make -C frontend check` 和 threads/tasks/messages 既有回归；写 PR3 changelog「验收记录」，勾选并更新状态头，`python3 scripts/check_docs_convention.py` 过后提交 `docs(frontend-reliability): PR3 验收记录`。push 与开 PR 另行确认。

### Task 4（PR4）：结构化 write-file 引用、内容状态与安全 auto-open

**Files：**
- Create：`frontend/src/core/artifacts/write-file-ref.ts`
- Modify：`frontend/src/core/artifacts/loader.ts`
- Modify：`frontend/src/core/artifacts/hooks.ts`
- Modify：`frontend/src/core/artifacts/utils.ts`（仅新增 raw 编码通道）
- Modify：`frontend/src/components/workspace/artifacts/artifact-file-detail.tsx`
- Modify：`frontend/src/components/workspace/messages/message-group.tsx`
- Create：`frontend/src/components/workspace/messages/write-file-tool-call.tsx`
- Modify：`frontend/tests/e2e/utils/mock-api.ts`
- Test：`frontend/tests/unit/core/artifacts/write-file-ref.test.ts`（新增）
- Test：`frontend/tests/unit/core/artifacts/write-file-content-state.dom.test.ts`（新增）
- Test：`frontend/tests/unit/components/workspace/messages/write-file-auto-open.dom.test.tsx`（新增）
- Test：`frontend/tests/unit/components/workspace/artifacts/artifact-file-detail-write-file.dom.test.tsx`（新增）
- Test：`frontend/tests/e2e/chat-artifact-ref.spec.ts`（新增）

**Interfaces：**
- `createWriteFileRef({ path, messageId, toolCallId }): string | null`：path 作为独立 query 参数编码，不再嵌入 URL pathname；三个定位字段任一缺失即返回 null，不生成含 `undefined` 的引用。
- `parseWriteFileRef(value): { path; messageId; toolCallId } | null`：支持新格式和固定 fixture 的合法旧格式；显式校验非法 percent，缺定位字段或旧路径已截断时返回 null，不抛异常、不猜文件名。新 query 不做二次 decode，旧 pathname 只解码一次。
- `type WriteFileContentState = {status:"ready",content:string} | {status:"pending"} | {status:"not_found"} | {status:"invalid"} | {status:"failed",message:string}`。判定按目标优先：先定位目标 tool call 与它的 result——成功的非 append write_file 直接 ready（args.content 内联快照，空串也是 ready），append/str_replace 成功则 GET 当前文件，工具 Error 是 failed。只有目标 call 或 result 缺失时才看加载状态：流式参数未完整、SDK history loading、自定义 run-history loading 或 hasMoreHistory=true 时是 pending；全部历史加载 settled 且 hasMoreHistory=false 后仍缺失才是 not_found。引用非法或 path 与定位到的 tool call 不一致是 invalid。
- `loadArtifactContentFromToolCall(...): Promise<WriteFileContentState>`（终态签名，纵切 B 原子切换；纵切 A 的过渡签名仍是 `string | undefined`）：以 toolCallId 为主键扫描所有同 messageId 消息并校验 path/工具类型。非 append write_file 成功后才以 args.content 作为内联快照；append=True 与 str_replace 成功后复用 `loadArtifactContent` GET 当前文件，明确不是历史调用快照。`resultMessage.status === "error"` 或结果文本以 `Error:` 开头均为 failed；GET 非 OK/网络异常必须捕获为 failed:{message}，不得逸出 Promise。不得为还原历史版本另写完整文件操作回放器。
- **raw 路径编码**：`loadArtifactContent` 对来自 tool args 的原始路径走 raw 通道——`urlOfArtifact` 增加不预解码的模式，逐段编码字面 `%`，仍丢弃 `.`/`..` 段。artifact list 既有的「已编码路径幂等」语义不变。
- **Consumes from PR3：** ThreadContext 内的 `thread.isLoading`、`thread.isThreadLoading`，以及可选 `isHistoryLoading/hasMoreHistory/refetchHistory`。目标缺失且 hasMoreHistory=true 时显示“加载完整历史”动作并调用 refetchHistory；全部加载后 not_found 的重试也必须真实调用 refetchHistory，无该能力时只显示关闭。invalid/failed 分别显示引用错误和工具/GET 失败，不混成空文件。
- `WriteFileToolCall`：流式中仅从 tool_call_chunks 拼接原始 args，并以 JSON.parse 成功作为完整参数信号；完整 path/messageId/toolCallId 就绪前不得构造 ref、选择或 auto-open。历史完整 tool_calls 不受 chunk 限制。
- **状态可过渡**：`useArtifactContent` 的 query key 或 enabled 纳入「目标参数完整、result 已到位」信号，run 尚未 onFinish 时也能 pending→ready/failed。

**纵切 A：安全引用**

- [x] **Step 1：写路径与旧格式兼容矩阵（红）。** 参数化 `% # ? 中文 空格 +`，断言新格式 round-trip；固定合法旧 URL、旧 `100%.txt`、旧 `#/?` 截断、新 `%2G` 等 fixture，断言不抛 URIError 且不可恢复值返回 null。缺 path/messageId/toolCallId 任一项均不生成 URL；loader 对重复 messageId 以 toolCallId 找到正确消息，path 不匹配返回 undefined。
- [x] **Step 2：运行确认旧实现失败。** 执行 `cd frontend && pnpm exec vitest run tests/unit/core/artifacts/write-file-ref.test.ts`，预期新解析尚不存在。
- [x] **Step 3：实现并接入安全引用（绿）。** 纵切 A 的过渡签名：`loadArtifactContentFromToolCall` 仍返回 `string | undefined`，引用非法、缺定位字段或 path 不匹配时返回 `undefined`，不触发正文渲染；`core/artifacts/hooks.ts` 本纵切不动。用 URLSearchParams 生成 ref，用单一 parser 读取；message-group 负责生成，loader 与详情页负责解析，详情页不得再 `decodeURIComponent(url.pathname)`；loader 校验 ref.path 与定位到的 toolCall.args.path 一致。保留合法旧格式，已截断且无法恢复的旧值按无效处理。
- [x] **Step 4：重跑 Step 2 确认全绿（含 typecheck）。**
- [x] **Step 5：Commit。** 暂存 `write-file-ref.ts`、`loader.ts`、`message-group.tsx`、`artifact-file-detail.tsx` 与本纵切测试，提交 `fix(artifacts): write-file 引用改用 query 参数编码，非法与截断旧格式返回无效`。

**纵切 B：五态加载与分工具正文来源**

- [x] **Step 6：写五态与分工具内容测试（红）。** 按目标优先的判定顺序覆盖：目标 call 与成功 result 齐全时，即使另有旧 run 未加载或新 run 正在流式也直接 ready/failed；目标缺失时分别覆盖流式/SDK history/run-history pending、目标 tool call 位于尚未加载旧 run 时 pending、全部 settled 且 hasMoreHistory=false 后 not_found；ready 空/非空文件；工具 Error failed；引用非法与 path 不匹配 invalid。覆盖非 append write_file、初始 write + append、str_replace、同路径连续 toolCall；用真实 `status="success"`/缺省 + `Error: ...` 文本模拟超限和 str_replace 失败，append/str_replace 的 GET 非 OK/网络异常也映射 failed，均不得显示 args.content 为正常 ready 或让 Promise 逸出异常。加载完整历史和 not_found 重试必须调用并等待 refetchHistory，无该能力时不显示这些动作。raw 路径：断言 `%20/%2F/%23` 字面文件名的真实请求 URL 不被预解码。
- [x] **Step 7：运行确认旧实现失败。** 执行 `cd frontend && pnpm exec vitest run tests/unit/core/artifacts/write-file-content-state.dom.test.ts tests/unit/components/workspace/artifacts/artifact-file-detail-write-file.dom.test.tsx`，预期状态类型尚不存在，旧空串、全局 pending、append/str_replace、工具失败和 raw 路径断言失败。
- [x] **Step 8：实现五态加载与分工具正文来源（绿）。** 在 loader、`core/artifacts/hooks.ts` 与详情层原子切换到 `Promise<WriteFileContentState>`；query key 或 enabled 纳入「目标参数完整、result 已到位」信号。ready 才返回正文且允许空串，pending 显示加载，failed 显示工具错误，not_found 按能力显示真实重试或关闭。按 Interfaces 区分非 append 内联快照和 append/str_replace 当前文件 GET；tool args 的原始路径走 raw 编码通道，artifact list 语义不变。非 write-file 的既有查询和编辑语义不变。
- [x] **Step 9：重跑 Step 7 确认全绿（含 typecheck）。**
- [x] **Step 10：Commit。** 暂存 `loader.ts`、`utils.ts`、`core/artifacts/hooks.ts`、`artifact-file-detail.tsx`、`mock-api.ts` 中为本纵切新增的夹具能力与本纵切测试，提交 `fix(artifacts): write-file 内容五态加载，append/str_replace 读取当前文件`。

**纵切 C：auto-open 竞态**

- [x] **Step 11：写完整参数与 auto-open 竞态测试（红）。** 用真实 AIMessageChunk/可控 SSE 在 path 中间及 `%/#/?` 前切 chunk，半截 path 时 select 为零，完整参数后只选择一次。覆盖同 toolCallId 不重复、100ms 内用户手选不被覆盖、无关 Provider 重渲染不吞掉唯一尝试、卸载无 timer/setState；缺任一定位字段不构造 URL；run 尚未 onFinish 时参数补全、result 到位能 pending→ready/failed。
- [x] **Step 12：运行确认旧实现失败。** 执行 `cd frontend && pnpm exec vitest run tests/unit/components/workspace/messages/write-file-auto-open.dom.test.tsx`，记录旧 auto-open 行为的失败项。
- [x] **Step 13：用独立组件承载无条件 Hook（绿）。** ToolCall 的 write_file/str_replace 分支只返回 WriteFileToolCall；auto-open effect 位于组件顶层，完整参数前保守等待，timer 实际触发时记录 attempted toolCallId 并清理 timer，用户选择变化后不再抢选。不得在条件分支里直接新增 Hook。
- [x] **Step 14：重跑 Step 12 确认全绿（含 typecheck）。**
- [x] **Step 15：Commit。** 暂存 `write-file-tool-call.tsx`、`message-group.tsx`、`core/artifacts/hooks.ts` 的本纵切改动与本纵切测试，提交 `fix(messages): write_file 工具调用拆独立组件，完整参数前不 auto-open`。

**纵切 D：行为验收与收尾**

- [x] **Step 16：端到端验收。** 重跑 Step 2、Step 7、Step 12 的 vitest 命令全绿；执行 `cd frontend && pnpm exec playwright test tests/e2e/chat-artifact-ref.spec.ts`，让特殊文件名 path 分 chunk 流式到达，验证半截不选、完整后 pending→ready、聊天区不崩、正文对应正确 toolCall、合法空文件不误报；另覆盖 append、str_replace 和工具 Error。
- [x] **Step 17：Commit。** 暂存 `chat-artifact-ref.spec.ts` 与 `mock-api.ts` 的剩余改动，提交 `test(e2e): write-file 引用端到端用例`。
- [x] **Step 18：批次门禁与收尾。** 执行 `make -C frontend check` 和 artifacts 既有回归；写 PR4 changelog「验收记录」，勾选并更新状态头，`python3 scripts/check_docs_convention.py` 过后提交 `docs(frontend-reliability): PR4 验收记录`。push 与开 PR 另行确认。

### 后续独立计划：视频操作代际与长队列恢复（不在本批执行）

- 已拆至 `docs/plans/pending/2026-11-24-video-operation-generation-plan.md`，设计约束、待定设计问题与必须覆盖的反例见该计划。本批任何 PR 不得实现其中内容，changelog 不得宣称修复 E1。

### Task 5：总验收与交付（不新增 PR）

- [x] **Step 1：机械排除 live 用例。** 在干净 worktree 执行以下命令，命中任何 `tests/e2e/live/` 都必须停止；以默认 Playwright 配置的 `--list` 为准。
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
- [x] **Step 2：跑完整前端门禁。** 在 Step 1 通过后，依次后台串行执行 `cd frontend && pnpm test`、`cd frontend && pnpm test:e2e`、`make -C frontend check`、`cd frontend && pnpm perf:check`，全部通过；不得并行共享 `.next/test-results`。任一失败先区分本批回归与既有问题，不得用新增 skip/fixme、删弱断言或无声提高资产预算收尾。
- [x] **Step 3：跑仓库静态与逐 PR diff 门禁。** 执行根 `make check` 和 `python3 scripts/check_docs_convention.py`。每个 PR 分别以其 merge-base/head 执行 `git diff --check <pr-base>..<pr-head>` 与 `git diff --name-only <pr-base>..<pr-head>`，对照该 PR 的 allow/ban；共享文档 allow 只覆盖本计划和本 PR changelog，Task 0 的 findings backlog 不混入功能 PR。检查每批 changelog 都有「验收记录」且链接回本计划。
- [x] **Step 4：只在必要时做最小真实 API 验收。** 仅当 mock 无法证明真实响应头或 SSE 行为时，选择一个最短、最低消耗的现役 Agent run；记录 thread id、受理/后台 Promise 时序、URL、取消/断线/恢复和最终消息，不循环重试、不运行全量 live 套件、不部署。PR1 的帧面板未保存付费守卫必须用“零 mutation 请求”证明，不为验收主动付费生成。非必要则明确记录“未调用付费 API 及原因”。
- [x] **Step 5：完成执行文档并冻结交付树。** 勾选全部已完成项，状态头写明 4 个 PR 与 changelog 链接、实际验收范围及未验证边界；补齐各 changelog 的真实验收记录、付费 API 使用情况和“视频跨代轮询另立计划/未部署”说明。此后不再改被测代码或文档，进入最终检查。
- [x] **Step 6：检查最终累计 diff 并交付。** 执行 `python3 scripts/check_docs_convention.py`，再执行：
  ```bash
  git diff --check "$BASE_SHA"..HEAD
  git diff --name-only "$BASE_SHA"..HEAD
  git status --porcelain
  ```
  累计 name-only 按四批 allow 的并集 + 共享文档审查，不用累计 diff 反推单 PR 越权。确认无 backend、docker、config、skills、seed、registry、writer/gallery/voice 功能、remotion/ViMax 专属实现、live 测试、accepted-operation/sessionStorage、认证/查询/运营台禁区及测试产物；`core/api/` 的改动只有 PR2 的 cancel 包装与同文件的中止错误归一化。最终检查通过后不得再改文件；向用户交付 PR 列表、关键行为证据、付费 API 使用情况（若有）、后续计划边界和“未部署”说明。

## 本批完成定义

- 四批各自通过定向测试和 `make -C frontend check`，最终完整前端测试、`pnpm perf:check` 与根静态门禁通过。
- accepted 之前停止/失败/busy 保留草稿；accepted 之后长流不再控制 composer；流式中停止仍保留草稿；纯附件可发，上传可取消且不跨线程污染；新建 Agent 三个调用点与普通会话 auto-send 均观察 rejection，每次错误提示恰好一次。SDK 未收尾时的提交按 busy 保留输入，回调只归属当前 submission；取消结果可等待，取消失败不成为未处理拒绝。
- 真实 before_seq 分页不丢单 run 最早消息；同一 run 重取补齐最终消息且慢旧请求不能回退，新增 run 不丢；终态按所属 run 判定，断线、history loading、interrupt 不误报失败，澄清（success + ask_clarification）不被判成取消；补齐式重载会重取加载后已变化的 run；取消有明确终态，取消失败不伪造 interrupted。
- 长线程初次水合落底误差 ≤2px，prepend 锚点位移 ≤4px，并通过三次重复真实浏览器验收；runs list 超过 100 个的恢复边界明确未覆盖。
- 特殊文件名和分 chunk path 不会崩聊天或提前 auto-open；完整参数后只 auto-open 一次且不覆盖用户选择；目标齐全时不受无关加载状态阻塞；invalid/pending/not_found/ready/failed 不再混成空串，append/str_replace 不伪装内联全文，字面 `%` 文件名能读到正确文件。
- AI 生图/AI 生视频 的帧面板「重生画面」不会拿未保存的旧 prompt/source 付费重生（详情页「项目已更新，重新生成画面」入口不在本批）；visual/narration dirty 独立，三类视觉乐观编辑失败回滚且提示，cancelled 不停留在轮询，非白名单和 legacy grace 行为不变。
- 默认全量 Playwright 机械确认不含 live 用例；未部署，未修改认证、查询缓存、运营台或任何禁区，未新增 accepted-operation/sessionStorage。
- **明确不在本批完成：** 视频 E1 旧 ready 停机、A/B 操作代际归属、超长队列恢复和产物代际刷新；这些必须待 `docs/plans/pending/2026-11-24-video-operation-generation-plan.md` 验收，不得在本批 changelog 中宣称修复。
