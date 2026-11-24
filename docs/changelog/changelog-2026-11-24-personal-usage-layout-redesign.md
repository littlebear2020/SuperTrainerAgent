# 个人「我的用量」布局优化

> **TL;DR**：普通用户「我的用量」按已确认的静态 Demo 重排：1120px 居中内容、页头分段周期控件、单工具栏七项筛选、合并双指标面板、单一任务面板（列头 + 响应式网格行 + 面板内分页）；只改五个 usage 组件、两个 i18n key 与现有测试，后端/API/管理员后台/全局主题零改动；以任务行/数值单元格级溢出断言（含侧栏展开 1050/820px）经三处反向验证验收。执行基准 `ab92a527` 比较后 re-pin 至 `f44073a1`；不涉及 `config_version`、后端与部署。

## 变更摘要

- 页头与筛选：内容收进 `max-width: 1120px` 居中容器；标题在左、「统计周期」分段控件在右（<820px 纵向、三按钮等分整行）；七项能力筛选收进一个带边框工具栏（<820px 标签独立一行，<600px 单行横向滚动并隐藏滚动条）。激活态由黑色实心按钮改为白底/浅紫底，`aria-pressed` 与切筛选复位逻辑不变。
- 汇总：两张独立卡合并为同一面板两等分（≥640px 竖分隔，手机上下横分隔），带 `Coins` / `Cpu` 装饰图标；数值、`null → —`、真实 0 与来源徽章取值不变，数值保留 `break-all`。
- 任务面板：不再逐条建卡；列头与每行共用同一套列定义（桌面 `minmax(0,1fr) 92/142/126/28`，中等 `82/118/105/28`），标题列不设像素下限，侧栏展开时让位而不裁切数值列；<820px 列头隐藏，同一组单元格经 grid areas 重排为两行（标题/费用、状态/Token）。状态改为文字 + 空圆点 pill，三态文案不变；标题 ≥820px 单行截断带 `title`、<820px 两行截断；Token/金额 `tabular-nums` 右对齐 `break-all`；箭头改 `ChevronDown` 旋转；分页信息只在面板头出现一次，分页按钮在面板内右侧对齐（手机等分整行）。
- 焦点环：两套主题的 `--ring` 都是 `transparent`，周期/筛选/分页/重试按钮与任务行显式追加 `focus-visible:ring-indigo-500`。
- i18n 只追加 `personalUsage.taskColumn` / `statusColumn` 两个 key；模型表只调间距/边框，保持 `min-w-[640px]` 与独立横向滚动。
- 范围外发现登记 findings backlog（本批不夹带）：工作区嵌套 `<main>` 地标（`SidebarInset` 与页面根都是 `<main>`）；两套主题 `--ring: transparent` 使 `Button` 自带焦点环不可见。

## 漂移与执行边界

- 任务 0：主 clone 的其他计划未提交/未跟踪文件一律未动；`git diff ab92a527..f44073a1 --stat` 共 21 个文件，usage 组件、既有测试、`core/usage`、三份 i18n 的 `personalUsage` 块与三个钉死全局文件（`globals.css`、`ui/sidebar.tsx`、`workspace-content.tsx`）零漂移，基准 re-pin 至 `f44073a1`。
- 全部实现位于独立 worktree（原生 worktree 工具建于 `.claude/worktrees/personal-usage-layout-redesign`，目录名与计划文本的 `../SuperTrainerAgent-…` 不同，隔离语义一致）的 `personal-usage-layout-redesign` 分支；依赖离线安装成功（`pnpm install --offline --frozen-lockfile` 6s、`uv sync --frozen --offline`）。
- 与 `my-works-delivery-center` 计划共享三份 i18n：对方尚未执行，本批先合入，对方执行时先 rebase。
- 未新增依赖、未改 `components/ui/**`、`components/ai-elements/**` 与全局主题；未部署、未调用付费 API、未跑 live E2E。

## 执行修正（计划文本与实现的差异）

- 断点类名：§1.3 写的 `max-[819px]:` 在 Tailwind 4 中编译为 `@media not all and (min-width:819px)`，恰好 819px 视口不生效（首次专用 E2E 在 819px 失败：标题仍 `nowrap`、卡片 areas 未应用）；实现改为 `max-[820px]:`，即 `min-[820px]:` 的精确补集。
- 手机数值列：§1.2 写的 `auto` 改为 `fit-content(45%)`——`auto` 列先取最大内容宽，大数值夹具会把标题列压到约 49px（320px），违反 §1.3「不得压到邻列」；`fit-content(45%)` 对普通数值行为不变，只给长数值封顶。
- 英文断言：`useI18n` 在挂载时按 `locale` cookie 重新播种，`initialLocale` 无法切换已挂载的 provider，英文断言在 `locale=en-US` cookie 下重挂载（仓库既有测试的做法）。
- 执行记录：`executing-plans` 的 task-start/task-done 脚本只识别英文 `Task N` 标题，本计划用「任务 N」，ledger 行手写，内容等价。
- 任务 0 Step 6 的勾选覆盖合入前部分；合入后的备份 → pull → diff → 清理在同一执行会话的合入之后完成，结果见执行报告。

## 验收记录

- 任务 1 RED：`cd frontend && pnpm exec vitest run --project dom tests/unit/components/workspace/usage/personal-usage-page.dom.test.tsx` → `1 failed | 4 passed`，失败项 `groups the window control in the header and all capabilities in one toolbar`（`usage-header` 不存在）；GREEN → `5 passed (5)`。
- 任务 2 RED：同命令 → `1 failed | 5 passed`，失败项 `keeps both existing totals inside one responsive summary panel`（`usage-summary` 不存在）；GREEN → `6 passed (6)`；`vitest run --project dom tests/unit/components/workspace/usage` → `3 files / 13 passed`；`prettier --check` 两文件 → `All matched files use Prettier code style!`。
- 任务 3 RED：三份 usage DOM 测试 → `2 failed | 13 passed`，失败项 `contains the list and pagination in one task panel`（`usage-task-panel` 不存在）与 `keeps titles, numbers and status pills readable in the responsive row`（`task-title-long` 不存在）；GREEN → `3 files / 15 passed`；`pnpm exec tsc --noEmit` → exit 0；`console/console-role-page.dom.test.tsx` → `2 passed`。
- 专用 E2E 第一次：`1 failed | 2 passed`——819px 标题 `white-space` 期望 `normal` 实得 `nowrap`（见「执行修正」）；修正后重跑 `3 passed (9.1s)`。
- 反向验证（三处临时改动各跑一次专用 E2E，随后恢复；`git diff | shasum -a 256` 恢复前后同为 `4d31a233…992a6`）：① 列头去掉 <820px 隐藏 → `expect(usage-task-columns).toBeHidden()` Expected hidden / Received visible；② 任务行在所有宽度固定为桌面列并去掉卡片 areas → `expectCardLayout`「状态在标题下一行」`toBeGreaterThan` Expected > 580.25 / Received 570.25；③ 数值单元格去 `break-all` 加 `whitespace-nowrap` → `expectContained` offenders `["task-tokens-task-05: 150 > 142"]`（两个大数值用例均失败，小数值用例仍过）。
- 恢复后终跑：专用 E2E + 临时截图用例 → `4 passed (9.8s)`（截图用例不入库，跑完即删）。截图（1440×900 收起/展开、390×844 收起/展开）存主 clone `temp/2026-11-24-personal-usage-layout-evidence/`，对照 §1.2 的桌面/手机结构核对一致。
- OCR 第 1 轮修复后（几何与包含性断言改为 `expect.poll` / `toPass` 5s 重试读取，夹具去掉嵌套三元、汇总由同一组数值推导并断言对账，缺失节点报出 testid）：专用 E2E → `3 passed (9.5s)`；反向验证 ②③ 在新断言上复跑仍各自失败（`Expected > 580.25 / Received 570.25`；`task-tokens-task-05: 150 > 142`）。
- OCR 第 2 轮（4 条 low，无 inline）：受理 3 条——展开箭头补 `task-chevron-${id}` testid（生产侧仅此一个属性），侧栏用例前置读取改为 `toPass`，筛选/模型表滚动检查合并为 `expectScrollsInOwnContainer`（断言不变）；「按钮数 7 改由能力目录推导」再次不改（产品常量，计划与单测都钉死 7）。usage DOM → `3 files / 15 passed`；专用 E2E → `3 passed (9.3s)`。
- `pre-commit run --files <各任务改动文件>` → 每个任务提交前 eslint / prettier / gitleaks / docs convention 全 Passed。
- `cd frontend && CI=1 pnpm test` → `Test Files 284 passed (284)`、`Tests 1872 passed (1872)`，23.76s。
- 专用 E2E 门禁：`cd frontend && CI=1 E2E_PORT=3127 E2E_GATEWAY_PORT=3137 pnpm exec playwright test --config playwright.personal-usage.config.ts` → `3 passed (7.8s)`。
- 全量 mock E2E：`cd frontend && CI=1 E2E_PORT=3107 pnpm test:e2e` → `Running 145 tests`、`142 passed, 3 skipped (2.5m)`。
- 根目录 `make check` → exit 0：backend `ruff check` `All checks passed!`、`ruff format --check` `1335 files already formatted`；frontend `pnpm check` ESLint `0 errors, 120 warnings`（存量 warning）、`tsc --noEmit` 通过、prettier `All matched files use Prettier code style!`；knip 无死导出；i18n `665 个叶子全活`；docs convention `OK 仓库治理约定全过`。
- 文档门禁（文档写完后）：暂存全部文件后 `pre-commit run --files <暂存文件>` → 全 Passed；`git diff --cached --check main` → exit 0；`python3 scripts/check_docs_convention.py` → `OK 仓库治理约定全过`。

## 未验证项

- 未运行 `RUN_LIVE=1` 付费测试、live E2E 或真实部署；专用与全量 E2E 均为 Playwright API mock。
- 侧栏展开态只覆盖 1050×900 与 820×844 两个视口；中等列宽只在 1024×768 实测；深色模式只有类名与单测覆盖，未截图核对。
- 焦点环、hover 与 `prefers-reduced-motion` 只断言了类名，未做视觉回归；无像素基线（计划明确不写）。

## PR 与远端验收

- PR：[#57 个人「我的用量」布局优化](https://github.com/littlebear2020/SuperTrainerAgent/pull/57)（分支 `personal-usage-layout-redesign`）。OCR 三轮：第 1 轮 8 条（2 条「`max-[820px]` 在 820px 仍命中」的 bug 经构建产物核实为误报，test·medium 与 4 条 low 已修复，1 条产品常量不改），第 2 轮 4 条 low（3 条修复、1 条维持不改），第 3 轮 4 条 low（2 条无可操作项/不改，2 条受理并登记 findings backlog，不再推送）；全程无 Critical/Important 未处置。逐轮处置见 PR 评论。每个代码提交的 PR 侧 CI（e2e-tests、frontend-unit-tests、lint-frontend、perf-budgets、quick-checks、review）全绿；合入方式 `gh pr merge --rebase`。

## 配置与回滚

- `config_version`：**不涉及**；无 schema、依赖或部署变更。
- 回滚 = revert 本批 `refactor(usage)` / `test(usage)` 提交，页面恢复旧的卡片堆叠布局，数据与 API 不受影响。
