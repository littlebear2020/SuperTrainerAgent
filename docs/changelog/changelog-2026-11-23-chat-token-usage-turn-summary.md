# 对话任务 Token 明细按轮汇总

> **状态**：本地实现与前端全量验证完成；PR CI/OCR 待记录；未部署，测试服验收待另行批准。
> **TL;DR**：将同一可见用户轮次内分散在 processing、产物和普通回复后的逐条 Token 行合并为一行；按消息对象引用去重并保留旧行求和口径，缺 usage 显示次数，流式期间不计算；仅改前端消息展示、测试与文档，不改后端、顶部徽章、`accumulateUsage` 或部署。

## 改动

- 在 `core/messages/usage.ts` 新增 `summarizeTurnUsage(groups)`：以可见 human 组划轮，支持列表首部隐式首轮；每轮按 `Set<Message>` 的对象引用去重，使用原有 `getUsageMetadata` 累加输入、输出和总 Token；同 id 的不同对象仍分别计入。
- 汇总 Map 以轮内最后一个可渲染组的原始索引为锚点。`isEmptyClarificationGroup` 精确定义为 `assistant:clarification` 且首条消息不存在或不满足既有 `hasContent`；渲染分支与汇总锚点共用该谓词。
- 独立评审发现上传中的乐观 task 占位 AI 在旧 UI 提前返回、没有 Token 行；汇总现精确排除 `assistant` 组内 `additional_kwargs.element === "task"` 的占位，避免上传期间新增虚假“未返回用量”，其他组口径不变。
- `MessageList` 只在 `tokenUsageEnabled && !thread.isLoading` 时汇总，否则复用同一个冻结的 `EMPTY_TURN_USAGE: ReadonlyMap`；通过既有 `VirtualMessageList.renderAfterGroup` 在每轮只渲染一行，终稿组使用顶部让位，使顺序为回复、工具栏、Token 行。
- 删除 processing、present-files、subagent、storyboard、clarification 和普通回复内的旧逐条出口。展示复用既有文案：全缺仅显示 `unavailableShort`，部分缺在已返回数值之和后显示 `unavailableShort ×N`。
- 新增 node、DOM 与 Playwright 覆盖：17 行离线夹具守恒、引用去重、同 id 异对象、缺失/零值、多轮、空 clarification、流式零计算、工具栏位置、96 组虚拟窗口、错误/停止、多个产物组及真实 run-history prepend 后的索引与分轮数值。

## 数值与口径核对

- `SERVER_BASELINE_NOT_VERIFIED`：本次没有获批的测试服验收或新的有效会话，不读取真实任务数据；使用脱敏消息形状的手工离线夹具继续。
- 17 行夹具：旧逐行之和、按对象引用汇总、现有 `accumulateUsage` 均为输入 `1220`、输出 `2440`、总计 `3660`。
- 独立同 id 异对象夹具：新汇总为 `30/60/90`，现有 `accumulateUsage` 因按 id 去重为 `10/20/30`。这是离线构造对该已知口径冲突的验证，不代表已统计真实数据出现频率；按要求未改顶部徽章或 `accumulateUsage`，已登记 backlog。
- 汇总行只含主线程可见 AI 消息，不含子代理与 middleware 调用，因此不要求等于顶部总会话徽章；后续“继续”按新 visible human 另起一轮。

## 已知限制与范围外发现

- **prepend 像素级滚动锚定未通过，不能宣称已验证。** 新 Playwright 场景实测旧 run prepend 后锚点 y 从 `149` 变为 `-41`；三个新组按估计高度补偿 `528px`，实际高度合计 `337.5px`，差值 `190.5px`。根因位于本批禁改且未修改的 `VirtualMessageList` 估计高度补偿路径。保留并通过了 prepend 后原始组索引平移、每轮一行及 `140/12` 数值不串轮的断言；像素锚定问题已登记 backlog，未通过删改生产代码或宣称 `NOT_VERIFIED` 来掩盖这次已知失败。
- 未做测试服部署、真实会话验收或付费制作；这些需用户另行批准。

## 验收记录

- `git diff --stat 52c316763dc15e60649f1dc6eb0faf52b50013d4..origin/main` → 无输出，计划基准与最新 main 无漂移。
- 基线 `vitest`（消息 node + DOM）→ `38 passed` + `5 passed`，合计 `43/43`。
- RED：`pnpm exec vitest run --project node tests/unit/core/messages/usage.test.ts tests/unit/core/messages/utils.test.ts` → `19 failed / 29 passed`，缺少 `summarizeTurnUsage`、`isEmptyClarificationGroup` 和冻结空 Map。
- RED：DOM 定向命令 → `8 failed / 2 passed`；旧 processing + present-files + 终稿夹具实际 `3` 行而非 `1` 行。
- RED：`pnpm exec playwright test tests/e2e/chat.spec.ts -g token --reporter=line --workers=1` → `8 failed`，旧实现分别出现 `2` 或 `4` 行；held-open 流式期间的 `0` 行断言先行通过。
- GREEN：node 定向命令 → `2 files / 48 tests passed`。
- GREEN：DOM 定向命令（含既有空组虚拟列表回归）→ `3 files / 10 tests passed`。
- GREEN：标准 Playwright token 命令，无构建绕过 → `8 passed`。
- 变异①撤掉 `mt-9` 让位 → 工具栏位置断言如预期失败：`Expected >= 498 / Received 470`；随后恢复。
- 变异②移除 `!thread.isLoading` → 加载中门控用例如预期失败：`1 failed / 4 passed`；随后恢复。
- 独立全分支评审 → 1 项 Important：上传 task 占位被误计缺 usage；新增回归 RED `Expected missingCount 0 / Received 1`，最小修复后 node 定向 `49/49 passed`；无 Critical。
- `pnpm test` → 评审修复后最终重跑 `284 test files / 1846 tests passed`。
- `pnpm test:e2e` → 评审修复后最终重跑 `128 passed, 3 skipped`。
- `pnpm check` → 评审修复后串行重跑，ESLint `0 errors`（既有 `120 warnings`）、TypeScript 与 Prettier 通过。首次与 E2E 并行时因共享 `test-results/` 被清理而出现 ESLint `ENOENT`，随后均串行重跑通过，不作为代码失败。
- `make check` → backend/frontend 静态检查、`knip`、i18n `660` 个叶子存活检测与 docs convention 全部通过；既有 frontend `120 warnings`，无 error。
- `python3 scripts/check_docs_convention.py` → `OK 仓库治理约定全过`。
- PR CI/OCR → 待实际完成后补录，不预先声称通过。
