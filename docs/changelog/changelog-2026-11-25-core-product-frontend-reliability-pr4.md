# 核心产品前端可靠性 PR4：结构化 write-file 引用、内容五态与安全 auto-open

> **TL;DR**：聊天里 write_file / str_replace 工具步骤的引用改成把 path 作为 query 参数编码（`% # ?` 空格 一类文件名不再定位失败）；正文按五态加载（ready / pending / not_found / invalid / failed），非 append 的写入显示这次调用的内联快照，append / str_replace 读文件当前内容，工具失败显示错误；auto-open 只在流式参数完整后尝试一次、不抢用户的选择、卸载不留 timer。只改 `core/artifacts`、`messages/message-group.tsx`、`artifacts/artifact-file-detail.tsx`、中英词条与对应测试，后端、registry、认证零改动；Vitest + mock Playwright 验收，零付费调用，未部署。计划：`docs/plans/2026-11-17-core-product-frontend-reliability-plan.md`（Task 4）。

## 变更摘要

- **纵切 A · 安全引用**：新增 `core/artifacts/write-file-ref.ts`——`createWriteFileRef` 用 `URLSearchParams` 生成 `write-file:?path=…&message_id=…&tool_call_id=…`，三个定位字段任一缺失即返回 null；`parseWriteFileRef` 是唯一解析入口，支持新格式与合法的旧格式（path 在 pathname、只解码一次），非法 percent、缺定位字段、旧路径已截断时返回 null，不抛异常、不猜文件名。`message-group.tsx` 负责生成，loader 与详情页负责解析，详情页不再 `decodeURIComponent(url.pathname)`；loader 校验引用的 path 与定位到的工具调用一致。
- **纵切 B · 五态加载与分工具正文来源**：`loadArtifactContentFromToolCall` 返回 `WriteFileContentState`。判定按目标优先：先以 toolCallId 为主键定位工具调用与它的 result——成功的非 append write_file 直接 ready（`args.content` 内联快照，空串也是 ready）；`append: true` 与 str_replace 成功后 GET 当前文件（明确不是历史调用快照），GET 非 OK / 网络异常捕获为 `failed: { message }`；工具 Error（`status === "error"` 或结果文本以 `Error:` 开头）是 failed。只有目标缺失时才看加载状态：流式参数未完整、SDK history loading、自定义 run-history loading、`hasMoreHistory=true` → pending；全部 settled 后仍缺失才是 not_found；引用非法或 path 与定位到的调用不一致是 invalid。`useArtifactContent` 的 write-file 查询 key 带着归属信号（目标阶段 + 三个加载标志），run 还没 onFinish 时 result 一到就能 pending → ready / failed。tool args 的原始路径走 raw 编码通道（`encodeArtifactPath(…, { raw })`，逐段编码字面 `%`，仍丢弃 `.` / `..` 段）；artifact list 的「已编码路径幂等」语义不变。详情页 ready 才显示正文，其余四态在正文区给出说明；目标缺失时按能力提供「加载完整历史 / 重试」，都真实调用 PR3 暴露的 `refetchHistory`，无该能力时只显示关闭。
- **纵切 C · auto-open 竞态**：write_file / str_replace 分支只渲染新组件 `WriteFileToolCall`，auto-open 的 effect 位于组件顶层（hook 不能写在条件分支里）。完整参数的信号是 `tool_call_chunks` 原始串的严格 `JSON.parse`——SDK 交付的 `tool_calls[].args` 是宽松的部分 JSON 解析结果，半截 path（如 `/mnt/data/100%`）也在里面；不完整时不显示 path、不构造引用。每个 toolCallId 只试一次（计时器真正触发时才记账），用户在 100ms 等待内改了选择就不抢，卸载清计时器；effect 的依赖只放原始值，避免计时器被反复清掉重排。
- **纵切 D · 行为验收**：`tests/e2e/chat-artifact-ref.spec.ts` 三条——特殊文件名分 chunk 流式到达（半截不选、完整后 pending → ready、聊天区不崩、不读文件）；append / str_replace 读当前文件、工具失败显示错误；空文件是 ready 且为空。e2e mock 新增产物正文路由（按原始路径登记内容）。

## 漂移与执行边界

- 分支 `chat-artifact-ref` 最初基于 PR3 评审前的头 `b6e27711`（纵切 A、B 在 PR3 评审期间完成并提交），PR #60 与 #61 合入后用 `git rebase --onto` 只把纵切 A、B 两笔重放到新 main，无冲突。
- 计划 allow 之外、ban 之内没有触达。计划 Files 未列出但改动了 `core/i18n/locales/{types,zh-CN,en-US}.ts`：四态说明是用户可见文案（`writeFileRef.*` 六个词条，中英），与 PR3 的 `recovery.*` 同一口径。
- 未新增依赖、未改 `components/ui/**`、`components/ai-elements/**`、认证、`core/api/**`、QueryClient；未部署、未调用付费 API、未跑 live 套件。`config_version` 不涉及。

## 执行修正（计划文本与实现的差异）

1. **契约复核**：计划的「流式中仅从 tool_call_chunks 拼接原始 args，以 JSON.parse 成功作为完整参数信号」在当前依赖上成立，但路径与计划措辞略有不同——SDK 1.6.0 的 `MessageTupleManager` 把每个流式片段强制转成 `AIMessageChunk` 再 `concat`，交付给 UI 的消息字典同时带 `tool_calls`、`tool_call_chunks`、`invalid_tool_calls`；`@langchain/core` 1.1.20 的 `collapseToolCallChunks` 用 `parsePartialJson`（宽松），所以半截 path 以完整对象的形态出现在 `tool_calls[].args` 里，原始串在 `tool_call_chunks[].args` 里。用真实的 `AIMessageChunk.concat` 探针实测（半截 → `path: "/mnt/data/100%"`；完整 → 全串），单元与 e2e 夹具都按这个形态构造。
2. **纵切 B 遗留的一处缺陷在纵切 C 修掉**：`useArtifactContent` 普通文件的查询写成 `enabled: enabled && !isWriteFile`，`enabled` 缺省时表达式是 `undefined`，而 TanStack Query 把 `undefined` 当启用——write-file 引用会被当成文件路径去 GET。应用里唯一的调用方总是传布尔值所以没有暴露，是纵切 C 的 hook 用例（`enabled` 缺省 + 断言不发 GET）抓到的；改成 `enabled !== false && !isWriteFile`，普通文件的 `undefined / true / false` 语义不变。
3. **e2e 夹具的落位**：计划把 `mock-api.ts` 的夹具能力列在纵切 B 的提交里；实际纵切 B 的用例不需要（loader 单测自己 stub fetch），产物正文路由随纵切 D 的端到端用例一起加。
4. **流式帧序**：端到端的 SSE 帧必须按真实服务端的顺序发——metadata 之后先发一条带用户消息的 `values`，再发 `messages` 片段；并且要等流真正开始消费后再发后续帧（流刚建立时同一批写入的 `values` 没有生效，用户消息会被后面的 AI 片段挤掉）。定位方法是沿 React fiber 树读页面手里的消息列表，以及在 node 里直接驱动 SDK 的 `MessageTupleManager`（帧的形状本身没问题）。桥接到保持连接的 SSE 服务的 glob 末尾顺手放开，防提交 URL 带查询串时匹配不上（PR3 的 join 桥接踩过）。
5. **断言位置**：`.md` 的 write-file 在详情里走 Markdown 预览而不是代码编辑器，正文断言用文本；空文件用例改用 `.txt`（走 CodeMirror），才能直接断言「编辑器已挂载且为空」；同一组里靠前的工具步骤默认折叠在「N more steps」后面，没展开时不在 DOM 里。
6. **一个漏网的 lint error**：纵切 B 的提交里 `writeFileTargetMayArrive` 用 `||` 串联可选布尔标志，ESLint（`prefer-nullish-coalescing`）报 error。提交前最后一次改动该文件后没有重跑 pre-commit（hook 本身有效，用探针文件复核过：同样的写法会被拦住），PR4 首次全量门禁的 `make -C frontend check` 才抓到。单独一笔修正，行为不变。

## 验收记录

- 纵切 A RED：`cd frontend && pnpm exec vitest run tests/unit/core/artifacts/write-file-ref.test.ts` → `Error: Cannot find package '@/core/artifacts/write-file-ref'`、`Tests no tests`；详情页用例 `artifact-file-detail-write-file.dom.test.tsx` → `Tests 2 failed (2)`（`expected '复制到剪贴板关闭正文内容' to contain '100% final#1.txt'`、`expected '正文内容' to be ''`）。GREEN → `write-file-ref.test.ts` `Tests 12 passed (12)`。
- 纵切 B RED：`pnpm exec vitest run tests/unit/core/artifacts/write-file-content-state.dom.test.ts` → `Tests 13 failed`（非 append ready 内联快照、目标缺失与加载态无关、append / str_replace 读当前文件、GET 失败映射 failed、超限被拒、pending / not_found / invalid、raw 路径等；`AssertionError: expected '正文' to deeply equal { status: 'ready', c…`）。GREEN → `write-file-content-state.dom.test.ts` + `artifact-file-detail-write-file.dom.test.tsx` `Test Files 2 passed (2)`、`Tests 19 passed (19)`。
- 纵切 C RED：`pnpm exec vitest run tests/unit/components/workspace/messages/write-file-auto-open.dom.test.tsx` → 组件不存在时 `Failed to resolve import "@/components/workspace/messages/write-file-tool-call"`；把旧的渲染期计时器原样搬进新组件后 `Tests 6 failed | 2 passed (8)`——半截 path 被选中 1 次、同 toolCallId 被选中 2 次、用户手选后被抢 2 次、卸载后仍触发 1 次。实现后 hook 用例先暴露 `enabled` 缺省的缺陷（`expected "vi.fn()" to not be called at all, but actually been called 1 times`），修复后 `Tests 10 passed (10)`（6 条组件、2 条 `MessageGroup` 用真实 `AIMessageChunk` 夹具验证 `tool_call_chunks` 接线、2 条 hook 过渡）。变异：去掉 `convertToSteps` 里的 `rawArgs` 传递 → 半截用例 `select` 被调用 1 次。
- 纵切 D：`pnpm exec playwright test tests/e2e/chat-artifact-ref.spec.ts` 首次 `3 failed`（`.cm-content` 选不到预览、折叠步骤未展开、流式帧序不对），修正后 `3 passed`，`--repeat-each=6 --workers=2` → `18 passed`。
- 计划 Step 16 的三条 vitest 复跑：`write-file-ref.test.ts` → `Tests 12 passed (12)`；`write-file-content-state.dom.test.ts` + `artifact-file-detail-write-file.dom.test.tsx` → `Tests 19 passed (19)`；`write-file-auto-open.dom.test.tsx` → `Tests 10 passed (10)`。
- 首次批次门禁：`make -C frontend check` → exit 2（ESLint `121 problems (1 error, 120 warnings)`，`core/artifacts/loader.ts 271:55 prefer-nullish-coalescing`），同一次运行里全量 vitest `Test Files 304 passed (304)`、`Tests 2097 passed (2097)`，全量 e2e `181 passed, 3 skipped (3.2m)`，`pnpm perf:check` exit 0。修正后全仓 `pnpm exec eslint . --ext .ts,.tsx` → `120 problems (0 errors, 120 warnings)`，artifacts 回归 `12 files / 101 passed`。
- 批次门禁（修正后重跑）：`make -C frontend check` → exit 0（ESLint `120 problems (0 errors, 120 warnings)` 存量、`tsc --noEmit` / `prettier --check` / knip 随 `&&` 链通过、i18n `679 个叶子全活`）；全量 `cd frontend && pnpm exec vitest run` → `Test Files 304 passed (304)`、`Tests 2097 passed (2097)`；`pnpm exec playwright test` 全量 → `181 passed, 3 skipped (2.9m)`；`pnpm perf:check` → exit 0（`/workspace/chats/[thread_id]` JS 3580383 B、CSS 255804 B；比 PR3 合入时多约 5.6 KB，在预算内）。
- 每个代码提交前 `pre-commit run --files <改动文件>` → eslint / prettier / gitleaks 全 Passed。

## 未验证项

- 未接真实后端或模型：流式片段由本地 SSE 服务按 SDK 源码对齐的形态发出（`messages-tuple` 事件、`AIMessageChunk` 字典），真实 LangGraph 服务端的帧形态只按 SDK 的消费代码对齐；产物正文由 mock 路由按路径返回。
- 分工具语义依赖工具名 `write_file` / `str_replace` 与 `append` 参数名；后端改工具名时引用会落成 invalid（不猜文件）。
- append / str_replace 显示的是文件**当前**内容，不是那次调用之后的历史版本——这是有意的取舍（不为还原历史版本写文件操作回放器），计划 Interfaces 已写明。
- 像素 / 交互断言只在 Chromium（Playwright）里验证。
- 未部署，未跑 `tests/e2e/live/**`。

## 配置与回滚

- `config_version`：**不涉及**；无 schema、依赖或部署变更。
- 回滚 = revert 本分支的提交：write-file 引用回到 path 嵌入 pathname 的旧格式（特殊文件名定位失败），正文只有「有 / 无」两态（空文件与失败分不清），auto-open 在渲染期创建计时器、半截 path 也会被拿去构造引用。
