# 个人「我的用量」布局优化实施计划

> **状态**：未执行（实施中；执行日 2026-11-24；2026-11-24 评审修订已并入）；定稿日 2026-11-24；基准 commit `f44073a1`（执行日 re-pin：`ab92a527..f44073a1` 对五个 usage 组件、既有测试、`personalUsage` i18n 与三个钉死全局文件零漂移）；执行后须链接实际执行日的 changelog。
> **TL;DR**：按已确认的静态 Demo 重排普通用户「我的用量」，收拢页宽、筛选、双指标和任务列表；只改前端组件结构、样式与必要列名 i18n，不改 API、数据、计费、状态、分页或管理员后台；以既有行为测试全绿 + 1440/1024/820/819/390/320px 浏览器布局断言验收（溢出断言取任务行/单元格级并经反向验证）。
> **For agentic workers:** REQUIRED SUB-SKILL: Claude 必须使用 `executing-plans`（单执行者、单工作区）逐任务实施；每项遵循“失败测试 → 确认失败原因 → 最小实现 → 定向回归 → 提交”，执行即勾选对应 checkbox，不得把本计划当成直接改生产的授权。

**Goal**：在不增加任何产品功能的前提下，将现有「我的用量」从超宽、分散的卡片堆叠优化为紧凑、对齐、可响应的布局，并保持所有既有数据与交互语义不变。

**Architecture**：保留 `PersonalUsagePage` 的查询、状态和角色边界，只在其现有子组件内重排 JSX 与 Tailwind 类。页头和筛选由页面容器与 `UsageCapabilityFilter` 负责，双指标由 `UsageSummary` 负责，任务面板、展开明细和模型表继续由 `UsageTaskList` / `UsageModelBreakdown` 负责；不新增状态管理层、API 层或 UI registry 组件。

**Tech Stack**：Next.js 16、React 19、TypeScript、Tailwind CSS 4、现有 `Button`、现有 `lucide-react`、Vitest 4（`dom` project）、Playwright；不新增任何依赖。

**Spec**：本文件 §1 是视觉与布局唯一事实源；`<主clone>/temp/2026-11-24-usage-layout-demo/{index.html,preview-desktop.png,preview-mobile.png}` 仅为辅助视觉参照（`<主clone>` = 主 clone 根目录；`temp/` 被 gitignore，worktree 内不存在，见任务 0 Step 5）。既有行为契约以 `docs/plans/2026-11-19-personal-usage-optimization-plan.md` §1.1–§1.3 和现行测试为准，本计划不重开这些决策。

## Global Constraints

- 仅普通用户 `PersonalUsagePage` 改版；管理员 `ConsolePage`、角色分流和 `/api/usage` 完全不变。
- 不新增趋势图、搜索、导出、排行、时间戳、能力标签、任务计数徽标或任何当前页面没有的功能。
- 不照搬 Demo 的静态数据、仅切换选中态的筛选、`1–11 / 11 项`、禁用分页、首条展开或 `deepseek-chat` 占位明细；生产页面继续使用真实 query 数据且默认全部收起。
- 保留 `usePersonalUsage` 参数、`days/capability/offset/expanded` 状态、切筛选复位逻辑、分页大小、加载/错误/空态、placeholder 更新提示和页脚说明。
- 保留现有 `data-testid`、任务三态、金额与 Token 格式化、`null → —`、真实 0、费用来源徽章、原任务 URL、键盘操作和事件隔离；新增 testid 只能用于布局断言，且不得以 `usage-days-` 或 `usage-capability-` 开头（既有测试用前缀选择器按数量断言）。
- 面板、边框与普通文字使用现有语义色 Token；紫色强调与三态状态色仅在 usage 组件局部通过 Tailwind 浅色/深色类实现（主题没有 success/warning token），不改全局主题，不写内联样式，不新增全局 CSS 文件。
- 禁止修改 `frontend/src/components/ui/**`、`frontend/src/components/ai-elements/**`；生产图标使用 `lucide-react`，不得复制 Demo 的内联 SVG。
- 本批不新增功能或产品面，不属 grilling 强制前置范围；视觉由已确认的静态 Demo 定稿，评审残余决策记录于 §1.6。`docs/findings-backlog.md` 的“用量标题国际化”属于数据来源问题，本批明确推迟，不顺手修改。
- 单执行者按任务 0 → 4 串行执行；共享文件较多，不得拆成并行 worktree。

## Review Focus

1. **长标题、大 Token、大金额与缺标题**：标题可截断但完整文本仍可从 `title` 和 aria-label 获取（<820px 两行截断）；数值单元格超出列宽时换行，不截断、不压邻列；任务 3 单测、任务 4 大数值夹具的行/单元格包含性断言覆盖。
2. **320/390px 与 819/820px breakpoint，以及侧栏展开**：任务表在 820px 切换为两行卡片式网格；侧栏展开（内容区比收起态少 208px）时标题列让位，数值列不被裁切；溢出断言取任务行与数值单元格的 `scrollWidth <= clientWidth`，不用文档级（在本工作区恒真）；任务 3 实现、任务 4 覆盖（含侧栏展开 1050/820px）并反向验证。
3. **展开长模型表**：只允许 `model-table-scroll` 自身横向滚动，不能带宽整个文档；任务 3 保留既有断言、任务 4 实浏览器覆盖。
4. **键盘与原任务链接**：Tab、Enter、Space、焦点、独立链接点击和禁止冒泡不得因行布局重做而回归；焦点环用显式 indigo 类（主题 `--ring: transparent`）；任务 3 单测、任务 4 E2E 覆盖。
5. **加载、错误、空数据与管理员边界**：布局调整不得伪造全 0、隐藏重试或让普通页发旧 console/定价请求；任务 1/3 保留既有测试、任务 4 全量回归覆盖。

---

## 一、设计契约与文件边界

### 1.1 范围内 / 范围外

**范围内**：

- 普通用户页的最大内容宽度、页头、统计周期、能力筛选、双指标汇总、任务面板、任务行、展开区、分页和响应式布局。
- 为“任务 / 状态”两个可见列名补充现有三份 i18n；为结构断言补充必要的 `data-testid`。
- 更新现有 Vitest / Playwright，不写像素基线，不引入视觉测试依赖。
- 执行后的计划勾选、状态头和一份 changelog。

**范围外**：

- 后端、数据库、API 类型、查询 hook、计费、采集、权限、路由、管理员运营台。
- `config*.yaml`、`config_version`、部署文件、技能、seed、provider/middleware。
- 真实数据迁移、真实付费 API、Live E2E、全局 theme/token 改版。
- 静态 Demo 的入库或产品化；`temp/` 文件不是本批生产代码来源。

### 1.2 页面结构与尺寸

生产 DOM 逻辑顺序固定为：

```text
PersonalUsagePage
└─ max-width content
   ├─ header：标题/副标题 + 统计周期 segmented control
   ├─ capability toolbar：筛选标签 + 全部/六项能力
   ├─ 加载、错误或 placeholder 状态（沿用现有条件）
   ├─ summary：同一面板内的费用 / 总 Token 两栏
   ├─ task panel：面板标题/分页信息 + 列头 + 任务列表 + 分页按钮
   └─ footer note
```

| 区域 | 桌面（≥1050px） | 中等（820–1049px） | 手机（<820px，另在 <640/<600px 细化） |
|---|---|---|---|
| 内容容器 | `max-width: 1120px`，在可用 main 内水平居中 | 同左 | 宽度 100%，页面 padding 16px |
| 页面 padding | 左右 32px、上下 40px | 24–32px | 16px；`sm` 可恢复 24px |
| 页头 | 标题在左、周期在右，顶部对齐 | 同左 | <820px 改为纵向，周期控件占满可用宽度 |
| 标题 | 28px / 1.25 / 700，副标题 14px、间距 8px | 同左 | 标题可降至 25px，副标题不得截断 |
| 周期控件 | 可见“统计周期”标签；三按钮同一浅底分段容器，每钮至少 66px 宽 | 同左 | 标签与控件纵向排列，三按钮等分整行 |
| 能力工具栏 | 页头下约 30px；标签与全部七项同组，白色/浅色细边框面板 | 同左 | <820px 标签独立一行；<600px 七项单行横向滚动并隐藏滚动条 |
| 汇总面板 | 工具栏下约 20px；同一圆角面板两等分，中间竖分隔线 | 同左 | <640px 改上下两栏，中间横分隔线 |
| 汇总项 | 桌面 padding 24px 28px；数值 36px、提示 12px | 保持可读，数值可用 `clamp` | padding 20px；数值不小于 28px |
| 任务面板 | 汇总下约 24px；一个 `rounded-2xl` 面板，不再为每条任务创建独立卡片 | 同左 | 同左，左右 padding 收至 16px |
| 面板头 | 高约 60px；左“用量任务”，右唯一一份 `usage-page-info` | 同左 | 同左，文字可换行但不得溢出 |
| 任务列头 | 高 39px，五列：任务 / 状态 / 总 Token / 已核算费用 / 展开图标 | 使用下方中等列宽 | <820px 隐藏，仍保留每行全部字段 |
| 任务主行 | `minmax(0,1fr) 92px 142px 126px 28px`，列间 18px，左右 22px，最小高 70px | `minmax(0,1fr) 82px 118px 105px 28px`，列间 12px，最小高 70px | `minmax(0,1fr) auto 28px` 三列两行，grid areas 为 `"title cost chevron"` / `"status tokens chevron"`：标题/费用在第一行，状态/Token 在第二行，箭头是单元素、占第 3 列跨两行并垂直居中 |
| 展开区 | 与所属任务同一面板单元，顶部细分隔线、浅底、20–22px padding | 同左 | padding 16px；费用三项 <640px 改单列 |
| 分页/footer | 底部只保留上一页/下一页按钮，右侧对齐；页脚说明在面板外约 14px | 同左 | 分页按钮可等分整行；说明完整换行 |

标题列在桌面/中等网格不设像素下限（`minmax(0,1fr)`）：侧栏展开时内容区比收起态少 208px，标题让位并截断，状态/Token/费用/箭头列不得被裁切；视口断点保持不变。

### 1.3 视觉与可读性规则

- 背景沿用工作区现有背景，不新增渐变背景；面板使用 `bg-card`、`border-border`、轻量 `shadow-sm`，圆角以 `rounded-2xl` 为主。
- 激活周期使用白底（深色模式用适当语义色）+ 轻阴影；激活能力使用局部浅紫底和紫字。非激活项均为低对比文字，不再使用一排黑色实心按钮。
- usage 局部浅紫可采用 `violet-50/violet-600`，深色模式使用 `dark:bg-indigo-400/10 dark:text-indigo-300`；焦点环显式用 `focus-visible:ring-indigo-500`（按钮经 className 追加，任务行另加 `focus-visible:ring-2`）——两套主题的 `--ring` 都是 `transparent`，`Button` 自带的 `ring-ring/50` 与现有 `ring-ring` 均不可见；不得只依赖颜色表达激活，`aria-pressed` 继续保留。
- 汇总费用使用 `Coins`，总 Token 使用 `Cpu`，图标容器约 28px 且 `aria-hidden=true`；费用来源徽章沿用原文案和取值，只调整视觉，不增加状态含义。
- 任务标题 13–14px / 500–550；状态使用文字 + 5–6px 圆点。主题没有 success/warning token，用 workspace 现有调色板组合：已完成 `text-emerald-600 bg-emerald-500/10 dark:text-emerald-400`，进行中 `text-amber-700 bg-amber-50 dark:text-amber-300 dark:bg-amber-950/30`，未完成 `bg-muted text-muted-foreground`。圆点用伪元素或空 `span`，`aria-hidden=true`、不含文本节点（既有断言对状态文案做精确匹配）。不得只显示圆点，不得改变三态文案。
- Token 与金额使用 `tabular-nums`、右对齐、`min-w-0` 和 `break-all`：超出列宽时换行，不得 `whitespace-nowrap`、截断或压到邻列；汇总数值同样保留 `break-all`；任务行与数值单元格不加 `overflow-hidden`。标题 ≥820px 单行 `truncate`，<820px 两行截断（`max-[819px]:line-clamp-2 max-[819px]:whitespace-normal`，触屏没有 `title` 提示），节点带 `title={title}`，现有 aria-label 继续含完整标题。
- 展开箭头使用 `ChevronDown`，展开时旋转 180°；动效仅 150–200ms，且不得影响测试或 `prefers-reduced-motion`。
- 模型表沿用六列，容器保持现有 `min-w-[640px]` 与 `overflow-x-auto`，不随 Demo 改 680px；不得通过删除列、缩小到不可读或页面级滚动解决窄屏。

### 1.4 文件职责

| 文件 | 本批唯一职责 |
|---|---|
| `frontend/src/components/workspace/usage/personal-usage-page.tsx` | 最大页宽、页头/周期布局、任务面板壳、唯一分页信息和 footer 排列；保留所有状态与查询逻辑 |
| `frontend/src/components/workspace/usage/usage-capability-filter.tsx` | 七项筛选的工具栏、激活样式和手机横向滚动 |
| `frontend/src/components/workspace/usage/usage-summary.tsx` | 双指标合并面板、图标、分隔线与来源徽章样式 |
| `frontend/src/components/workspace/usage/usage-task-list.tsx` | 桌面列头、任务网格行、状态样式、箭头与展开区布局 |
| `frontend/src/components/workspace/usage/usage-model-breakdown.tsx` | 只调整表格间距/边框，保留六列、格式和独立横向滚动 |
| `frontend/src/core/i18n/locales/{types.ts,zh-CN.ts,en-US.ts}` | 增加 `personalUsage.taskColumn` 与 `personalUsage.statusColumn`，其余文案不改 |
| 现有 usage Vitest / Playwright | 固定新结构、响应式与所有旧行为；不新建平行测试套件 |

### 1.5 路径三栏

| 类型 | 路径 / 决策 |
|---|---|
| 允许路径（allow） | 本计划；上表五个 usage 组件；三份 i18n；`frontend/tests/unit/components/workspace/usage/**`；`frontend/tests/e2e/personal-usage.e2e.ts`；执行日 `docs/changelog/changelog-YYYY-MM-DD-personal-usage-layout-redesign.md`；范围外发现时按格式追加 `docs/findings-backlog.md` |
| 禁改路径（ban） | `backend/**`、`frontend/src/core/usage/**`、`frontend/src/app/**`、`frontend/src/components/workspace/console/**`、管理员 console、全局 CSS/theme、`components/ui/**`、`components/ai-elements/**`、`config*.yaml`、`docker/**`、`skills/**`、Playwright 配置、其他 E2E 与无关文档 |
| 共享基座触达 | **需最小触达**：三份 i18n 只追加两个 key；根 `make check` 会检查共享门禁。不得改 i18n 既有条目，不得改共享运行代码；执行前先核对他人未提交修改 |

### 1.6 评审决策记录（2026-11-24 评审）

本批不新增功能或产品面，未走 grilling 强制前置；视觉由已确认的静态 Demo 定稿，以下是 Demo 未覆盖、评审时补拍板的决策。

| Frontier 问题 | 已确认答案 |
|---|---|
| 侧栏展开时任务网格溢出 | 视口断点不变；标题列 `minmax(0,1fr)` 让位，数值列不被裁切。不做容器查询；以一个小用例（侧栏展开，1050/820px）做回归。 |
| 超出列宽的数值 | 保留换行（`break-all`），不 nowrap、不截断；汇总数值同理。 |
| 手机长标题 | <820px 两行截断；桌面单行 `truncate` + `title`。 |
| 溢出断言对象 | 任务行与数值单元格的 `scrollWidth <= clientWidth`；不用 `documentElement`（恒真）。 |
| 本地全量 mock E2E | 保留：PR CI 同跑，属双重保险；专用 personal-usage 配置 CI 不跑，必须本地跑。 |
| 模型表最小宽度 | 保持 640px，不随 Demo 改 680px。 |

### 1.7 2026-11-24 评审修订对照

| 评审项 | 原表述或缺口 | 定稿修订 |
|---|---|---|
| 新 testid 命名 | `usage-capability-filter` / `usage-capability-scroll` 撞既有前缀选择器 `[data-testid^='usage-capability-']`（按 7 个计数） | 改名 `usage-filter-toolbar` / `usage-filter-scroll`，并写入 Global Constraints。 |
| 溢出断言 | 断言 `documentElement.scrollWidth`，在本工作区恒真 | 改为行/单元格级断言，任务 4 Step 4 增反向验证。 |
| 侧栏展开 | 断点按视口；展开时约 1050–1085px、820–930px 两段网格溢出并被裁 | 标题列去掉像素下限。 |
| 大数值 | Review Focus 称已覆盖但无步骤造数；数值由 `break-all` 改 nowrap 会压邻列 | 规格定为换行；E2E 夹具加 `task-05` 大数值任务。 |
| 状态圆点 | 未说明；既有断言对状态文案精确匹配 | 圆点不得含文本节点。 |
| 焦点环 | 要求足够对比度，但主题 `--ring: transparent` | 钉死 `focus-visible:ring-indigo-500`。 |
| 状态色 | 称用现有语义 token，实际没有 success/warning | 写明调色板类与深色变体。 |
| 任务 0 | worktree 是条件分支；缺依赖安装、手动 pre-commit、`temp/` 路径与未跟踪计划副本处理 | 默认走 worktree，补 Step 4–6。 |
| 重复项 | 模型表 680px、`pnpm check` 重复、复位断言重复 | 删除。 |

### 1.8 2026-11-24 二次评审修订对照（外部评审反馈）

| 评审项 | 评审发现 | 处置 |
|---|---|---|
| 大数值夹具 | `task-05` 大数值与写死的汇总 mock 不再对账 | 采纳：`fullSummary` 与四处汇总断言同步；证据截图改取第一个用例场景。 |
| 文档门禁顺序 | changelog 在门禁之后才写；pre-commit 文件列表漏掉未提交与未跟踪文件 | 采纳：文档门禁挪到 Step 8，暂存后跑。 |
| 反向验证 | 改任务行网格不会让列头断言失败；只去 `break-all` 未必溢出 | 采纳：三处明确 mutation，各配预期失败，恢复后查残留。 |
| 侧栏展开实测 | 完成定义宣称了未验证的行为 | 采纳：新增展开态 1050/820px 包含性用例。 |
| 主 clone 计划副本 | 直接删除可能丢失后续评审修改 | 采纳：先备份，pull 后 diff，再清理。 |
| 手机网格箭头 | 两行都写了箭头，有歧义 | 采纳：写明 grid areas。 |
| 漂移核对范围 | 漏了计划钉死的三个全局文件 | 采纳：加入只读核对清单。 |
| 细节 | 中等列最小行高、离线安装兜底、写死的文件数、英文 locale 定位 | 采纳。 |
| 计划间重叠（评审未提） | 与 `my-works-delivery-center` 计划共享三份 i18n | 两份计划串行执行。 |
| “只有 git-lfs 钩子”不准确 | 认为 `pre-push` 不是 git-lfs 的 | 不采纳：该钩子内容为 `git lfs pre-push`。 |

---

## 二、分任务实施清单

### 任务 0：执行隔离、漂移检查、worktree 环境与分支准备

**Files**：
- Modify: `docs/plans/2026-11-24-personal-usage-layout-redesign-plan.md`（执行日期、必要 re-pin、任务勾选）

- [x] **Step 1：检查工作区并保护他人改动**。运行 `git status --short --branch`，以执行时输出为准（定稿时主 clone 已有其他计划的未提交修改，本计划本身也尚未跟踪）；不得 stash、clean、覆盖或提交这些文件。与 `docs/plans/2026-11-24-my-works-delivery-center-plan.md` 共享三份 i18n，路径重叠表非空：两份计划串行执行、不并行派发；对方先合入时，本批先 rebase 再追加两个 key。
- [x] **Step 2：比较定稿基准与执行日 main**。运行 `git diff ab92a527..main --stat`，并核对五个 usage 组件、现有测试和三份 i18n 是否漂移；同时只读核对本计划钉死的三个禁改文件：`frontend/src/styles/globals.css`（`--ring: transparent`、`.scrollbar-none`）、`frontend/src/components/ui/sidebar.tsx`（侧栏 16rem / 3rem）、`frontend/src/app/workspace/workspace-content.tsx`（根 `h-screen overflow-hidden`）。若影响 §1 契约，先修订计划再实施。完成比较后才可将状态头基准 re-pin 为实际 main tip。
- [x] **Step 3：新建隔离 worktree 与执行分支**。主 clone 不干净时不在其中执行：按 `using-git-worktrees` 从干净 main 新建 `../SuperTrainerAgent-personal-usage-layout-redesign` 与分支 `personal-usage-layout-redesign`，并把本定稿计划复制到 worktree 同一路径。本批预计超过 5 个文件且为 plan 驱动，最终必须走分支 + PR + OCR，不适用直推 main。
- [x] **Step 4：补齐 worktree 依赖与手动钩子**。worktree 不共享 node_modules 与 venv：在 worktree 执行 `cd frontend && pnpm install --offline --frozen-lockfile` 与 `cd backend && uv sync --frozen --offline`（根 `make check` 的 ruff 需要）；离线安装因缓存缺失失败时，改用在线的 `pnpm install --frozen-lockfile` / `uv sync --frozen` 并在 changelog 记录原因。本机未安装 pre-commit 钩子（`.git/hooks` 里只有 git-lfs 的四个钩子），每个任务提交前手动运行 `pre-commit run --files <本任务改动文件>`；其 eslint/prettier 会原地改文件，须先于后台测试运行。
- [x] **Step 5：固定主 clone 侧路径**。`temp/` 被 gitignore，worktree 内没有 Demo，且 worktree 的忽略文件会随 `git worktree remove` 一起删除：Demo 取 `<主clone>/temp/2026-11-24-usage-layout-demo/`，任务 4 的截图证据写 `<主clone>/temp/2026-11-24-personal-usage-layout-evidence/`。
- [x] **Step 6：建立执行记录**。状态头改为“未执行（实施中）”并记录实际执行日；确认 `frontend/AGENTS.md` 与根 `AGENTS.md` 无新增适用范围。首笔任务提交携带计划勾选，不在旧分支留下计划提交。PR 合入后、主 clone pull 前，把主 clone 中未跟踪的旧计划副本移到 `<主clone>/temp/` 备份（否则 pull 被同名文件拦截）；pull 后与合入版本 diff，确认备份没有独有修改再清理，有则带回用户。

### 任务 1：重排内容容器、页头、统计周期与能力工具栏

**Files**：
- Modify: `frontend/src/components/workspace/usage/personal-usage-page.tsx`
- Modify: `frontend/src/components/workspace/usage/usage-capability-filter.tsx`
- Test: `frontend/tests/unit/components/workspace/usage/personal-usage-page.dom.test.tsx`

**Interfaces**：
- Consumes: 现有 `WINDOW_OPTIONS`、`days/capability` 状态、`changeDays/changeCapability`、`WORK_CAPABILITIES` 和三份既有文案。
- Produces: `data-testid="usage-layout-content"`、`usage-header`、`usage-window-control`、`usage-filter-toolbar`、`usage-filter-scroll`（不以 `usage-capability-` 开头，见 Global Constraints）；既有 `usage-days-*` 与 `usage-capability-*` 全部保留。

- [x] **Step 1：先写页头与工具栏失败测试**。新增 `groups the window control in the header and all capabilities in one toolbar`：断言 `usage-header` 同时包含标题和 `usage-window-control`；周期仍只有 7/14/30；`usage-filter-toolbar` 包含可见 `filterLabel` 文本、恰好一个 `usage-filter-scroll` 和恰好七个 `button`（在工具栏内按元素计数，不用 testid 前缀）；周期与能力按钮节点含 `focus-visible:ring-indigo-500`。切换后的参数复位与 `aria-pressed` 已由既有复位测试覆盖，不重复断言。
- [x] **Step 2：运行并确认因新结构不存在而失败**。执行 `cd frontend && pnpm exec vitest run --project dom tests/unit/components/workspace/usage/personal-usage-page.dom.test.tsx`；预期新 testid 断言 FAIL，既有数据行为不得被改写来制造假绿。
- [x] **Step 3：按 §1.2/§1.3 实现容器、页头和筛选**。只移动现有 JSX 并调整 Tailwind 类；周期和能力按钮继续使用现有 `Button` 与 `aria-pressed`，激活样式通过条件类完成，焦点环按 §1.3 经 className 追加 `focus-visible:ring-indigo-500`。手机能力滚动用全局已有的 `scrollbar-none` 工具类，不新增 CSS 文件；不得把按钮改成静态选中态。
- [x] **Step 4：运行页面定向回归**。重跑 Step 2，预期全绿；重点确认切筛选复位展开/分页、加载/错误/空态和 placeholder 测试均保持通过。
- [x] **Step 5：提交本任务**。提交主题：`refactor(usage): 重排个人用量页头与筛选`；只暂存本任务文件和计划勾选。

### 任务 2：将费用与 Token 收拢为双栏汇总面板

**Files**：
- Modify: `frontend/src/components/workspace/usage/usage-summary.tsx`
- Test: `frontend/tests/unit/components/workspace/usage/personal-usage-page.dom.test.tsx`

**Interfaces**：
- Consumes: `PersonalUsageResponse["summary"]`、`formatUsageCost`、`formatInteger`、现有 `UsageCostSourceBadge`。
- Produces: 外层 `data-testid="usage-summary"`；既有 `usage-summary-cost`、`usage-summary-tokens`、`usage-summary-cost-source` 保留。

- [x] **Step 1：先写汇总结构失败测试**。新增 `keeps both existing totals inside one responsive summary panel`：`usage-summary` 同时包含且只包含两个既有汇总 testid；两个汇总项仍是并列语义区域，装饰图标 `aria-hidden=true`；费用值、Token 值、提示与来源徽章保持原响应值，`null` 仍为 `—`，真实 0 仍显示 0。
- [x] **Step 2：运行并确认新容器断言失败**。使用任务 1 Step 2 命令，预期仅因 `usage-summary` / 图标结构尚不存在而失败。
- [x] **Step 3：实现单面板双栏汇总**。将原来两张独立卡放入同一 `rounded-2xl` 网格容器；≥640px 两栏竖分隔，<640px 上下横分隔。使用 `Coins`、`Cpu` 和 §1.3 局部紫色，不添加第三项指标、趋势或占比；数值格式化与 `UsageCostSourceBadge(source)` 分支不改，两个数值节点保留 `break-all`、不改为 nowrap。
- [x] **Step 4：运行 usage 页面测试与格式检查**。重跑页面 DOM 测试，并执行 `cd frontend && pnpm exec prettier --check src/components/workspace/usage/usage-summary.tsx tests/unit/components/workspace/usage/personal-usage-page.dom.test.tsx`，预期全部通过。
- [x] **Step 5：提交本任务**。提交主题：`refactor(usage): 合并个人用量汇总面板`。

### 任务 3：收拢任务面板、响应式行与展开明细

**Files**：
- Modify: `frontend/src/components/workspace/usage/personal-usage-page.tsx`
- Modify: `frontend/src/components/workspace/usage/usage-task-list.tsx`
- Modify: `frontend/src/components/workspace/usage/usage-model-breakdown.tsx`
- Modify: `frontend/src/core/i18n/locales/types.ts`
- Modify: `frontend/src/core/i18n/locales/zh-CN.ts`
- Modify: `frontend/src/core/i18n/locales/en-US.ts`
- Test: `frontend/tests/unit/components/workspace/usage/personal-usage-page.dom.test.tsx`
- Test: `frontend/tests/unit/components/workspace/usage/usage-task-list.dom.test.tsx`
- Test: `frontend/tests/unit/components/workspace/usage/usage-model-breakdown.dom.test.tsx`

**Interfaces**：
- Consumes: 现有 `UsageTaskList` props（`tasks/expanded/onToggle`）、`UsageModelBreakdown` props、全部任务字段和 `usageTaskHref`；组件公共 props 不变。
- Produces: `data-testid="usage-task-panel"`、`usage-task-columns` 与逐任务 `task-title-${source_id}`；新增 i18n `taskColumn`（中“任务”/英“Task”）、`statusColumn`（中“状态”/英“Status”）。既有任务、详情、模型、原任务、分页 testid 全部保留，页面中 `usage-page-info` 只出现一次。

- [x] **Step 1：先写面板与列头失败测试**。在页面测试新增 `contains the list and pagination in one task panel`：面板头含“用量任务”和唯一分页信息，`role=list[name=用量任务]` 与分页 nav 同属 `usage-task-panel`；列头依次为“任务 / 状态 / 总 Token / 已核算费用”，英文 locale 使用新增英文 key，不允许硬编码中文；英文渲染下用 testid 或现有英文 `taskListLabel` 定位列表，不新增第三个 key。
- [x] **Step 2：先写任务行可读性与结构失败测试**。在 `usage-task-list.dom.test.tsx` 新增：长标题节点 `task-title-${id}` 含 `truncate`、`line-clamp-2` 与完整 `title` 属性、缺标题仍显示“标题生成中”；费用/Token 节点含 `tabular-nums` 与 `break-all`、不含 `whitespace-nowrap`；状态节点含空圆点节点（`aria-hidden=true`、无文本），其 `textContent` 仍严格等于三态文案；主行含 `focus-visible:ring-indigo-500`；列头为视觉辅助且 `aria-hidden=true`；每个任务仍是 `role=listitem`，主行仍为可聚焦 `role=button`，默认全部收起，箭头装饰不进入无障碍名称。
- [x] **Step 3：运行三份 DOM 测试并确认新断言失败**。执行 `cd frontend && pnpm exec vitest run --project dom tests/unit/components/workspace/usage/personal-usage-page.dom.test.tsx tests/unit/components/workspace/usage/usage-task-list.dom.test.tsx tests/unit/components/workspace/usage/usage-model-breakdown.dom.test.tsx`；预期因任务面板、列头和新标题节点尚不存在而 FAIL。
- [x] **Step 4：实现任务面板与响应式行**。按 §1.2 实现单一任务面板、列头、桌面/中等/手机网格、状态 pill、箭头、展开费用三栏与分页位置；不新增局部 expanded 状态，不改父组件 `toggleTask`。标题列用 `minmax(0,1fr)`；数值单元格 `min-w-0 break-all`，任务行与数值单元格不加 `overflow-hidden`；标题 <820px 两行截断；状态圆点按 §1.3。模型表只调间距/边框，保持 `min-w-[640px]`。
- [x] **Step 5：重跑并复核所有旧行为断言**。重跑 Step 3，必须同时通过新旧断言：Tab/Enter/Space、独立原任务链接、链接点击不冒泡、运行中“截至目前/核算中”、未完成消耗、费用等式成立条件、媒体未核算、无模型明细、缓存 `null/0`、真实费用来源、长模型名和大数自身滚动。不得删除或放宽这些断言。
- [x] **Step 6：运行全部 usage DOM 回归与 i18n 类型检查**。执行 `cd frontend && pnpm exec vitest run --project dom tests/unit/components/workspace/usage && pnpm exec tsc --noEmit`，预期全绿；再执行 `cd frontend && pnpm exec vitest run --project dom tests/unit/components/workspace/console/console-role-page.dom.test.tsx`，确认普通/管理员分流未变。
- [x] **Step 7：提交本任务**。提交主题：`refactor(usage): 收拢任务列表与展开明细`；三份 i18n 只能有两个 key 的最小增量。

### 任务 4：真实浏览器布局验收、全量回归与交付

**Files**：
- Modify: `frontend/tests/e2e/personal-usage.e2e.ts`
- Modify: 本计划状态头与全部勾选
- Create: `docs/changelog/changelog-<实际执行日>-personal-usage-layout-redesign.md`
- Modify（仅范围外发现）: `docs/findings-backlog.md`

**Interfaces**：只消费任务 1–3 的 testid 和既有 mock response；不得修改 Playwright 配置、端口、身份、mock gateway 或生产 API。

- [ ] **Step 1：先补桌面行为级布局断言**。在现有 E2E 正常数据场景中加入 1440×900 与 1024×768 检查：`usage-layout-content` 宽度不超过 1120px 且在 main 可用区域居中；页头与周期同处 header；七个能力按钮在桌面位于同一工具栏（`usage-filter-toolbar`）；两个汇总项左右排列；`usage-task-columns` 可见；任务行高 ≥ 70px，Token/费用右对齐；标题计算样式 `white-space` 为 `nowrap`；包含性断言（见 Step 2）通过。随后点击任务并确认展开区、模型表和原任务链接仍可用。
- [ ] **Step 2：先补 breakpoint、大数值与手机行为级断言**。先抽 helper `expectContained(page)`：对每个任务行、每个 `task-cost-*` 与 `task-tokens-*` 单元格断言 `scrollWidth <= clientWidth`。不拿 `document.documentElement` 当溢出证据：工作区根是 `h-screen overflow-hidden`、页面根是 `overflow-x-hidden`，文档级断言恒真，列被裁掉也通过（既有那条保留不删，另加本断言）。覆盖 820、819、390、320px：820px 列头可见；819px 起列头隐藏且任务标题/费用在第一行、状态/Token 在第二行，标题计算样式 `white-space` 为 `normal`；390/320px 汇总上下排列、能力筛选只在自身容器横向滚动、所有任务字段可见。在第二个用例的 21 任务夹具里给 `task-05` 追加 `total_tokens: 9876543210987`、`cost: 1234567.89`、`llm_cost: 1234567.89`，断言 `task-tokens-task-05` 含 `9,876,543,210,987`、`task-cost-task-05` 含 `¥1,234,567.89`，且各宽度 `expectContained` 通过。夹具保持对账（汇总是写死的 mock，没有求和断言也会失真）：`fullSummary` 同步为 `summary(1234586.89, 9876543211177, "real")`，第一页与第二页的四处汇总断言改为 `¥1,234,586.89` 与 `9,876,543,211,177`。展开长模型表后，继续断言只有 `model-table-scroll` 可横向滚动；不得用隐藏内容换取通过。另加一个小用例 `keeps numeric columns contained with the sidebar expanded`：给 `openPersonalUsage` 加可选参数 `{ sidebar?: "open" }`（把 `sidebar_state` cookie 写成 `true`），复用第二个用例的夹具与 mock（抽成 helper）；在 1050×900 与 820×844 下各跑一次 `expectContained`，这两个视口是侧栏展开时溢出最严重的位置。
- [ ] **Step 3：运行专用 E2E 至全绿**。执行 `cd frontend && CI=1 E2E_PORT=3127 E2E_GATEWAY_PORT=3137 pnpm exec playwright test --config playwright.personal-usage.config.ts`。任务 1–3 已实现，此时没有天然的失败阶段：新断言若失败，只允许按 Step 4 的边界修复后重跑，不得先删除或放宽新断言。
- [ ] **Step 4：反向验证新断言会失败**。全绿后一次性做三处临时改动，重跑 Step 3 命令，逐条核对预期失败：① 列头去掉 <820px 的隐藏类 → 819px「列头隐藏」断言 FAIL；② 任务行网格在所有宽度固定为桌面列 → 819px「标题/费用在第一行、状态/Token 在第二行」断言 FAIL；③ 数值单元格去掉 `break-all` 并加 `whitespace-nowrap` → `expectContained` 对 `task-05` FAIL（只去 `break-all` 不够，长数字可在“ Token”前的空格换行）。记录实际失败项后恢复，用 `git diff --stat` 确认工作区与改动前一致、无残留，再重跑至绿。任何修复只允许落在本计划 allow 的 usage 组件；保留 SSR regular identity、周期/筛选真实数据变化、两模型对账、三态、分页、键盘、无旧 console/定价请求等所有原断言。
- [ ] **Step 5：保存实际效果证据但不提交像素基线**。在同一专用 E2E 环境以 1440×900 截取默认收起和首条展开两图，以 390×844 截取手机图（三个视图均取第一个用例的正常数据场景，第二个用例的汇总栏是大数值夹具），放 `<主clone>/temp/2026-11-24-personal-usage-layout-evidence/`（worktree 内的忽略文件会随 `git worktree remove` 删除）；人工核对与 §1 的桌面/手机结构一致。截图仅作 changelog 验收证据，不加入 `tests/e2e/**/*.png` 基线，不创建新 docs 子目录。
- [ ] **Step 6：代码冻结后串行运行代码门禁**。长命令按根约定后台化，禁止两个会共享 `.next` / `test-results` 的命令并行。先对本任务已改未提交的文件跑手动 pre-commit（钩子未安装，且 eslint/prettier 会原地改文件，必须先于后台测试）；`pnpm check` 已含于 `make check`，不重复。文档门禁放到 Step 8、文档写完之后再跑：

```bash
pre-commit run --files frontend/tests/e2e/personal-usage.e2e.ts
cd frontend && pnpm test
cd frontend && CI=1 E2E_PORT=3127 E2E_GATEWAY_PORT=3137 pnpm exec playwright test --config playwright.personal-usage.config.ts
cd frontend && CI=1 E2E_PORT=3107 pnpm test:e2e
make check
```

预期：所有命令 exit 0；本批未改 backend，不以跳过前端测试或缩减既有 E2E 代替门禁。
- [ ] **Step 7：清理产物并完成文档**。检查 `git status --short` 和测试输出目录，只删除本批运行产生的临时日志/报告，保留陌生文件。更新计划勾选与状态头；changelog 命名对齐本计划，含 ≤5 行 TL;DR、「验收记录」节（每行“命令 → 实际关键输出”）、桌面/手机截图路径、未验证项和“不涉及 config_version / 后端 / 部署”。
- [ ] **Step 8：文档门禁后按仓库 PR 流程交付**。changelog 与计划写完（Step 7）后才跑文档门禁，文件列表须覆盖分支提交、暂存与未跟踪文件：

```bash
git add <本批全部文件，含未跟踪的 changelog 与计划>
pre-commit run --files $(git diff --cached --name-only main)
git diff --cached --check main
python3 scripts/check_docs_convention.py
```

钩子若改写了源码或测试文件，重新暂存并复跑 `make check` 与受影响测试；只改文档则复跑后两条命令。全过后按测试/文档分两笔提交（建议 `test(usage): 验证个人用量响应式布局`、`docs(usage): 记录个人用量布局优化`），推送已承载全部任务的 `personal-usage-layout-redesign`；`gh` 调用设置 `GH_REPO`，创建 PR 后等待 OCR + CI，无 Critical/Important 且 CI 绿才 rebase merge，否则先修复或带回用户拍板。

---

## 三、完成定义

只有同时满足以下条件，Claude 才可宣称完成：

- 页面结构、尺寸、断点、状态样式与 §1 一致；桌面不再超宽分散，手机不出现页面级横向滚动；侧栏展开（1050/820px）或收起时数值列均不被裁切。
- 仅普通用户页面变化；管理员后台、后端、API、数据库、配置和部署 diff 为零。
- 真实查询与所有旧交互完整保留：7/14/30 天、六项筛选、跨页汇总、三态、展开/收起、费用构成、模型表、原任务、分页、加载/错误/空态和 placeholder 更新。
- 无 Demo 假数据、无默认展开、无新增指标/功能、无新增依赖、无 registry 修改、无全局 theme 修改。
- 现有测试断言不得删除或放宽；新增实现级结构断言和桌面/手机行为级断言均通过，且行为级断言经反向验证确认会失败。
- `pnpm check`、全量 Vitest、专用 personal-usage E2E、既有全量 mock E2E、根 `make check`、docs convention、`git diff --cached --check main` 全部通过（后两项在文档写完后跑）。
- 计划勾选与状态头一致，changelog 有可审计验收记录；PR 的 OCR 与 CI 通过后合入。
