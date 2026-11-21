# 个人「我的用量」优化实施计划

> **状态**：未执行（实施中）；定稿日 2026-11-19；二次复核 2026-11-21；执行日 2026-11-21；基准 commit `8c78dace`（执行前已从 `b2378833` 完成漂移比较并 re-pin）；任务 7 完成后按实际执行范围更新本行并链接 changelog。
> **TL;DR**：按已确认的 V4 Demo，将普通用户「我的用量」重构为总费用、总 Token、7/14/30 天切换、六项筛选、任务三态与可展开模型明细；后端只读聚合现有记录，不改表、不改采集、不改计价、不新建账务架构；以未知不为 0、媒体未核算不造数、跨分页对账和角色查询隔离为验收红线。
> **For agentic workers:** REQUIRED SUB-SKILL: 使用 `executing-plans`（单执行者、单工作区）逐任务实施；必须先完成任务 0，之后的每项遵循“失败测试 → 确认原因 → 最小实现 → 定向回归 → 提交”，不得把本计划当成允许直接改生产的授权。

**Goal**：让普通用户在同一统计口径下看懂所选周期内六项能力的已核算费用与 LLM Token，并能逐任务查看状态、消耗、模型明细及返回原任务，同时保持管理员现有运营台不变。

**Architecture**：新增独立的个人用量只读聚合包与 `GET /api/usage`，从现有 `runs`、`threads_meta`、`unified_tasks`、`external_api_calls` 汇总，不改运行链和数据库。前端以角色边界组件分流：普通用户挂载新 `PersonalUsagePage`，管理员继续挂载现有 `ConsolePage`；汇总与任务列表由同一响应提供，分页不影响总额。

**Tech Stack**：Python 3.12、FastAPI、SQLAlchemy、Pydantic、pytest；Next.js 16、React 19、TypeScript、Tailwind CSS 4、TanStack Query、Vitest、Playwright；不新增生产依赖。

**Spec**：本文件 §1–§4 是执行唯一事实源。已获用户确认的 V4 Demo 及其临时截图已清理，不再作为执行依赖；视觉与行为契约以 §1.2 页面模块和 §4 验收为准。历史 Demo 的数字、首条默认展开和“查看全部任务”形态不直接等同于生产数据与分页规则。

---

## 一、产品契约与已拍板决策

### 1.1 页面只回答三组问题

1. 所选近 7 / 14 / 30 天，六项能力合计已核算费用和 LLM Token 是多少？
2. 某个任务当前是已完成、进行中还是未完成；在所选周期内已产生多少费用和 Token？
3. 该任务的各模型输入、输出、缓存读取、总 Token 与费用是多少；LLM 与可关联媒体费用如何组成任务已核算费用？

六项能力及 ID 固定为：

| 用户文案 | ID | 任务归组 |
|---|---|---|
| 文字动效 | `course-video` | Agent `thread_id` |
| 课程海报 | `creative-studio` | Agent `thread_id` |
| PPT 课件 | `ppt-master` | Agent `thread_id` |
| 语音播客 | `podcast-studio` | Agent `thread_id` |
| 图片动效 | `ai_image` | `unified_tasks.id` |
| AI 视频 | `ai_video` | `unified_tasks.id` |

旧 `course-ppt` 归入 `ppt-master`；`card-poster`、`case-article`、`design-specialist`、`knowledge-extraction`、`video-clip` 归入 `creative-studio`。其他 Agent、其他视频任务类型和隐藏旧能力均不得进入本页。

### 1.2 评审决策记录（grilling 结果）

| Frontier 问题 | 已确认答案 |
|---|---|
| 页面定位 | 个人用量与费用透明，不是正式账单；使用“已核算费用”“含估算”。 |
| 任务粒度 | 四类 Agent 按 `thread_id` 聚合，两类视频按 `task_id`；不向上合并项目/作品账单。 |
| 时间范围 | 默认 7 天，可选 14/30 天；按用户本地自然日；消耗记录创建时间决定归属。 |
| 跨日运行 | 单次运行不拆分到每天；其消耗归入该运行 `created_at` 所在窗口，不做伪精确分摊。 |
| 费用范围 | 供应商回传优先、价目估算兜底；LLM + 可关联媒体费用的已核算部分。 |
| Token 范围 | 只计 LLM 输入 + 输出，含子代理；缓存读取不重复累加；不混入媒体调用量。 |
| 三态 | Agent 当前状态取该线程全时间最新 run，视频取 `TaskRow.status`；`success/ready` → 已完成，明确非终态 → 进行中，失败、超时、取消、中断及未知态 → 未完成；消耗仍只统计窗口内记录。 |
| 进行中/未完成 | 进行中展示已落库值并注明“截至目前”，无可靠值显示“核算中”；未完成保留已产生消耗。 |
| 模型明细 | 默认折叠，展示模型名、输入/输出/缓存/总 Token、费用；历史缺失显示“暂无模型明细”，不回填。 |
| 未知与零 | 未知用 `null` → `—`，真实免费/零消耗才可显示 0；无价格时 Token 仍正常展示。 |
| 页面模块 | 两张总览卡、时间切换、六项筛选、任务列表、模型展开与原任务链接；无趋势、占比、API 次数和供应商面板。 |
| 对账 | 总览与全部任务同源、同窗口、同六类、同口径；不是只对当前分页求和。 |

剩余 frontier：无。实施所需的接口名、文件落点和测试方式由本计划依据代码事实确定，不再把事实查询转交给用户。

### 1.3 已确认的数据边界，不得静默扩张

- 当前 Agent 运行链没有统一写入 `thread_id` 级媒体核算上下文。只能汇总 `external_api_calls.task_id` 已能关联到 Agent `thread_id` 的行；无可归因行时 `media_cost = null`，不得写成 0，也不得为此新增采集中间件。
- `llm_cost = null` 表示没有可计价 LLM 分量，不代表 LLM 免费；`media_cost = null` 表示没有已核算媒体费用，可能来自未归因、已归因但无价目，或仅有免费/失败等不计费调用。UI 对 `media_cost = null` 统一显示中性的“媒体费用未核算”，不得把已归因未计价的记录误写成“未归因”。两者都未知时任务 `cost = null`，只有一个分量已知时任务成本是该已知分量。
- 单次运行没有模型拆分时，任务总 Token 仍按该运行已落库的输入 + 输出派生，总费用仍使用已有运行聚合计价；`models = []`，不得用主模型名反推完整模型明细，也不得改用供应商单独回传的 `total_tokens` 作为展示真相。
- 修改价目会改变按当前价目重算的历史估算。本批不建设价格快照、不调账、不承诺固定历史账单。
- `docs/findings-backlog.md` 已有 ViMax `amount/usage` 透传与 Doubao 字幕版 TTS 计价字段缺口。本批不顺手修采集链；相关成本只按“已核算部分”披露，验收和 PR 不得宣称全覆盖。
- 无 task 上下文的 `llm_bookkeeping` 行会把 `thread_id` 回落为 domain 字符串，即使仍带 `user_id`，也无法锚定到本页的 Agent thread 或视频 task，故按现契约静默排除。这是已知未覆盖范围，不得把 domain 字符串当成任务 ID 聚合，也不得宣称个人用量覆盖全部 LLM 调用。

### 1.4 2026-11-19 评审修订对照（不改变已拍板产品范围）

| 评审项 | 原表述或缺口 | 定稿修订 |
|---|---|---|
| 执行分支 | 任务 1–6 已提交，任务 7 才要求建分支 | 新增任务 0，在首笔提交前完成干净 worktree、`personal-usage-optimization` 分支和漂移检查。 |
| Agent 状态 | 只有单值状态归一，多 run 取值不明 | 当前状态取全时间最新 run，消耗查询仍限窗口；无 run 回退规则与测试同步补齐。 |
| Token 口径 | “输入 + 输出”与存储 `total_tokens` 可能冲突 | 展示和模型完整性校验统一由输入 + 输出派生，存储 `total_tokens` 只作诊断。 |
| 缓存与媒体未知 | 缓存 Token 非空，媒体 `null` 一律写“未归因” | 缓存 Token 改为可空；媒体 `null` 统一写“未核算”，覆盖未归因与未计价。 |
| 真实 LLM 费用 | 只复用金额 helper，真实/估算来源及无价目币种未闭合 | 按 bucket 判定来源；真实 `amount` 无价目时沿用现有部署单币 CNY 假设，并增加带精度的近似数值测试。 |
| 映射与键盘 | 旧别名可能另建映射，完成定义缺键盘断言落点 | 复用 `resolve_retired_agent`；任务 6/7 增加 Tab、Enter/Space 验收。 |

### 1.5 2026-11-21 二次复核修订对照（代码事实与执行基建校准）

| 复核项 | 复核发现 | 本次定稿修订 |
|---|---|---|
| 基准与主线 | 原 `09ef5c60` 不被任何分支包含；main 已前进且 i18n 三件漂移 | 当前基准 re-pin 为 `b2378833`，保留原基准于此；任务 0 必须先比较当前基准与执行日 main，再按需 re-pin，禁止先擦除比较基准。 |
| E2E 身份与端口 | 基础 Next env 写死 auth-disabled admin；专用前端端口与默认 gateway 撞号 | 专用配置整块替换 Next webServer env，显式普通 SSR 身份断言；专用端口改为 3127/3137。 |
| 权限与覆盖边界 | `runs.user_id` 可空；无 task 上下文的 bookkeeping 以 domain 代替任务 ID | Agent 以 threads_meta 归属为锚并只接受本人/NULL run，跨用户非空值仍排除；domain 回落行显式列入未覆盖。 |
| 时间、Token 与数值 | 排序/展示时间未精确；run 级缺失措辞与 NOT NULL 列不符；金额直接相等脆弱 | 区分 activity_at 与 updated_at；run 直接输入 + 输出，bucket 缺失才判不完整；金额统一带精度的近似断言。 |
| 实现锚点与过期引用 | 路由、夹具字段形状、Demo 清理和默认 limit 可进一步锁定 | 写死 pathOfThread 调用、两类 amount JSON 位置、Demo 已清理、`list_by_user` 默认 100 与 `useIsConsoleAdmin()` 复用。 |

---

## 二、统一接口契约

### 2.1 后端公共类型

新建 `backend/app/gateway/_shared/personal_usage/types.py`，命名固定如下；实现可补充内部辅助类型，不得改变公共语义。

```python
UsageCapability = Literal[
    "course-video", "creative-studio", "ppt-master", "podcast-studio",
    "ai_image", "ai_video",
]
UsageStatus = Literal["completed", "running", "incomplete"]
UsageCostSource = Literal["real", "estimated", "mixed"] | None

@dataclass(frozen=True)
class UsageWindow:
    days: int
    start_utc: datetime
    end_utc: datetime       # exclusive
    start_date: str        # local YYYY-MM-DD
    end_date: str          # local YYYY-MM-DD, today

@dataclass
class UsageModel:
    name: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int | None
    total_tokens: int
    cost: float | None
    cost_source: UsageCostSource

@dataclass
class UsageTask:
    task_key: str          # agent:<thread_id> | video:<task_id>
    source_id: str
    capability: UsageCapability
    title: str
    status: UsageStatus
    cost: float | None
    llm_cost: float | None
    media_cost: float | None
    currency: str | None
    cost_source: UsageCostSource
    total_tokens: int | None
    models: list[UsageModel]
    updated_at: datetime | None

@dataclass
class UsageSummary:
    cost: float | None
    currency: str | None
    cost_source: UsageCostSource
    total_tokens: int | None

@dataclass
class PersonalUsageResult:
    window: UsageWindow
    summary: UsageSummary
    tasks: list[UsageTask]
    total_tasks: int
    has_more: bool
```

纯函数契约：

```python
def make_usage_window(days: int, tz_offset_minutes: int, now: datetime | None = None) -> UsageWindow: ...
def canonical_agent_capability(*candidates: str | None) -> UsageCapability | None: ...
def normalize_usage_status(raw_status: str | None) -> UsageStatus: ...
def merge_cost_source(current: UsageCostSource, incoming: UsageCostSource) -> UsageCostSource: ...
```

窗口规则：本地当天为 `end_date`，本地 `today - (days - 1)` 为 `start_date`；`start_utc` 为本地起始日 00:00 换算 UTC，`end_utc` 为本地明日 00:00 换算 UTC，查询统一使用 `>= start_utc`、`< end_utc`。

`canonical_agent_capability` 按参数顺序返回首个可落入六类白名单的候选；每个候选必须先经现有 `app.gateway._shared.retired_agents.resolve_retired_agent` 归一，不得复制 `RETIRED_AGENT_ALIAS` 或另建旧名表。

`normalize_usage_status` 只负责单值归一：`success/ready` → `completed`；`pending/running/draft/extracting/converting/synthesizing/scripting/generating/rendering/story_ready/composing` → `running`；失败、超时、取消、中断、未知值和 `None` → `incomplete`。多 run 取哪一条由 §2.2 决定，不得在该纯函数中用状态优先级猜测。

Token 展示与完整性校验统一使用 `input_tokens + output_tokens`：任务使用运行级 `total_input_tokens + total_output_tokens`，模型使用 bucket 的 `input_tokens + output_tokens`；`RunRow.total_tokens` 与 bucket 自带的 `total_tokens` 只作诊断，不参与展示或对账。run 的 `total_input_tokens/total_output_tokens/total_tokens` 三列均为 NOT NULL、默认 0，run 级直接求和，不写“缺失按未知”的死分支；只有 bucket JSON 的输入/输出字段缺失或不完整时才使该运行模型明细无效并令任务 `models=[]`。缓存读取字段缺失时 `cache_read_tokens = null`，只有数据明确给出 0 才可返回 0；计价时仍可把缺失缓存按 0 传给 `_token_cost`，但该计价回退不得反向改写展示值。

`merge_cost_source`：`None + real = real`，`None + estimated = estimated`，`real + estimated = mixed`，同类合并不变。

### 2.2 聚合入口

新建 `backend/app/gateway/_shared/personal_usage/service.py`：

```python
async def get_personal_usage(
    session_factory,
    *,
    user_id: str,
    days: int,
    tz_offset_minutes: int,
    capability: UsageCapability | Literal["all"] = "all",
    offset: int = 0,
    limit: int = 20,
) -> PersonalUsageResult: ...
```

约束：

- Agent 使用 `operation_kind = "run"`；视频 LLM 使用 `operation_kind = "llm_bookkeeping"` 且 `thread_id = unified_tasks.id`，不得把两类记录重复相加。
- 复用 `console._build_pricing_map`、`_run_cost`、`_token_cost`、`_pricing_currency` 与 `task_cost.evaluate_media_call` 的行级计价语义；不得复制价格解析或媒体估算算法。
- 使用 SQLAlchemy 直接查询 `TaskRow`，不调用默认 `limit=100` 的 `TaskRepository.list_by_user` 充当全量聚合。
- 权限过滤以任务归属为锚，而不是要求所有历史行都非空记录 `user_id`：Agent 先以 `ThreadMetaRow.user_id = 当前用户` 取得 owned thread IDs，再按 `thread_id` 关联 runs，且 run 仅允许 `RunRow.user_id = 当前用户 OR RunRow.user_id IS NULL`；其他非空用户值必须排除，另一用户拥有的 thread 永远不得进入候选。视频以 `TaskRow.user_id = 当前用户` 为锚，视频 LLM 继续按当前用户过滤；媒体外部调用除 `task_id` 关联外仍须满足 `ExternalApiCallRow.user_id = 当前用户`。SQLite 返回的 naive datetime 按 UTC 处理，与现有 console `_as_utc` 语义一致。
- Agent 在 `thread.created_at` 落入窗口，或窗口内存在其 run/可归因媒体记录时纳入；视频在 `TaskRow.created_at` 落入窗口，或窗口内存在其 LLM/媒体记录时纳入。窗口外且无新消耗的旧任务不出现。
- Agent 状态查询与消耗查询分离：对已纳入线程，当前状态取该用户这些线程的**全时间最新** `operation_kind="run"`，按 `created_at DESC, run_id DESC` 确定；必须批量取得候选线程的最新状态，不得每线程一次查询，不得取窗口内旧 run 或以任何活跃 run 覆盖更新的终态。无 run 时用 `ThreadMetaRow.status` 经 `normalize_usage_status` 回退，`idle/None` 最终为 `incomplete`。视频状态直接归一 `TaskRow.status`。
- 任务级模型明细采用“全有或全无”：仅当该任务所有含 Token 的 LLM 记录都有有效模型 buckets，且每条记录的 buckets 输入/输出合计分别等于运行级输入/输出合计时才返回 `models`；任一记录缺失、不完整或对不上则 `models=[]`，任务总值仍保留，不把部分拆分伪装成完整明细。存储 `total_tokens` 不一致本身不改变上述输入 + 输出口径。
- 汇总在分页前完成；`total_tasks` 是筛选后的全部任务数，`has_more = offset + len(tasks) < total_tasks`。任务与 summary 对已知费用/Token 分量求“已核算小计”，只有没有任何已知分量时才为 `null`；未知项既不当作真实 0，也不得把其他已知小计清零。
- 内部排序键 `activity_at`：Agent 取 `thread.created_at` 与窗口内 run/可归因媒体 `created_at` 的最大值，视频取 `TaskRow.created_at` 与窗口内 LLM/媒体 `created_at` 的最大值；任务按 `activity_at DESC, task_key DESC` 稳定排序。不得用 run/task `updated_at` 充当排序键，避免进度写入或活性 ping 扰动分页。空任务允许有状态但 `cost/total_tokens = null`。
- 对外 `updated_at`：Agent 取 §2.2 所选全时间最新 run 的 `updated_at`，无 run 时回退 `ThreadMetaRow.updated_at`；视频直接取 `TaskRow.updated_at`。它与内部 `activity_at` 职责分离，HTTP 仍按 §2.3 序列化为 UTC ISO 时间。
- 混币时不做数值相加，按现有守卫返回 `cost/currency = null`；真实 0 与未知 `null` 必须分开。
- `cost_source`（沿用现有 `amount_source` 语义）由所有已知分量合并为 `real`、`estimated` 或 `mixed`。LLM 侧逐 bucket 判定：可解析 `amount` 且被 `_run_cost` 采用的分量为 `real`，无有效 `amount` 但经价目得到成本的分量为 `estimated`，同一 run/任务同时出现则为 `mixed`；非法 `amount` 回还价目估算时仍标 `estimated`，不得照搬 `compute_task_costs` 将 LLM 一律标为估算的旧标注。LLM 币种优先沿用 `_pricing_currency(pricing)`；pricing 为空但真实 `amount` 已给出成本时，沿用 `task_cost` 的部署单币假设记 CNY，估算分量仍不得在无价目时造币或造价。
- 金额数值断言统一使用带精度的近似比较：后端 `pytest.approx(expected, abs=1e-9)`，前端 `toBeCloseTo(expected, 8)`；不得用裸 `==`/`toBe` 验证逐行累计金额，也不得用 `toBeCloseTo` 默认精度放宽到失去分辨力。

### 2.3 HTTP API

新建 `backend/app/gateway/_shared` 包之外的 `backend/app/gateway/routers/personal_usage.py`：

```text
GET /api/usage?days=7&capability=all&tz_offset_minutes=480&offset=0&limit=20
```

- `days` 仅允许 `7 | 14 | 30`，默认 7；`capability` 仅允许 `all` 或六类 ID；`offset >= 0`；`1 <= limit <= 100`，默认 20。
- `tz_offset_minutes` 与现有 console 相同，范围 `-840..840`，默认 0；前端始终显式传本地偏移。
- 使用现有 `require_permission("runs", "read")` 和当前有效用户 ID；**不提供 `user_id` 参数**。管理员调用本接口也只看本人，平台视图继续走 `/api/console/*`。
- SQL 后端缺失时沿用现有 console 的 503 语义；参数错误返回 422。
- 响应字段镜像 §2.1：`window`、`summary`、`tasks`、`total_tasks`、`has_more`。HTTP `window` 只序列化 `days/start_date/end_date`，不下发内部 `start_utc/end_utc`；日期使用本地 `YYYY-MM-DD`，`updated_at` 使用 UTC ISO 时间。

### 2.4 前端公共类型

新建 `frontend/src/core/usage/types.ts`，字段与 §2.1 保持 snake_case：

```ts
import type { WorkCapability } from "@/core/workspace/video-assets-route";

export type UsageDays = 7 | 14 | 30;
export type UsageCapability = WorkCapability;
export type UsageStatus = "completed" | "running" | "incomplete";
export type UsageCostSource = "real" | "estimated" | "mixed" | null;
export type UsageCapabilityFilter = UsageCapability | "all";

export interface PersonalUsageResponse {
  window: { days: UsageDays; start_date: string; end_date: string };
  summary: {
    cost: number | null;
    currency: string | null;
    cost_source: UsageCostSource;
    total_tokens: number | null;
  };
  tasks: UsageTask[];
  total_tasks: number;
  has_more: boolean;
}
```

`UsageTask` 使用 §2.1 的 `task_key/source_id/capability/title/status/cost/llm_cost/media_cost/currency/cost_source/total_tokens/models/updated_at`；`UsageModel` 使用 `name/input_tokens/output_tokens/cache_read_tokens/total_tokens/cost/cost_source`，其中 `cache_read_tokens: number | null`，`total_tokens` 必须由输入 + 输出派生。不得用 `0` 替代后端 `null`。

查询 hook 固定为：

```ts
usePersonalUsage({
  days,
  capability,
  offset,
  limit: 20,
  tzOffsetMinutes: localTzOffsetMinutes(),
})
```

query key = `["personal-usage", user?.id ?? "anonymous", days, capability, offset, 20, tzOffsetMinutes]`；30 秒 stale time，仅同用户可复用 placeholder。切周期/能力立即复位 `offset = 0` 和展开状态。

---

## 三、路径边界与明确非目标

| 类型 | 路径 |
|---|---|
| 允许修改 | 本计划；`backend/app/gateway/_shared/personal_usage/**`、`backend/app/gateway/routers/personal_usage.py`、`backend/app/gateway/app.py`（仅 import/include）；`backend/tests/test_personal_usage*.py`；`frontend/src/{core/usage,components/workspace/usage}/**`；`frontend/src/components/workspace/console/console-role-page.tsx`、`frontend/src/app/workspace/console/page.tsx`；三份 i18n 文件；`frontend/src/core/utils/format.ts`、`frontend/src/core/console/format.ts`（仅 re-export）；对应测试与 E2E 配置；执行 changelog；`docs/findings-backlog.md`（仅限范围外发现按格式追加） |
| 禁改路径 | `config*.yaml`、数据库 schema/迁移、`docker/`、`skills/`、`backend/seed/`、provider/middleware/运行采集链、`backend/app/gateway/_shared/task_cost.py`、`backend/app/gateway/routers/console.py`、现有管理员 console 组件、registry 生成物、真实 `.env`/数据库/用户数据 |
| 共享基座触达 | 只读复用 console 计价函数、`retired_agents.resolve_retired_agent`、六项目录、`pathOfThread`、现有视频详情路由；不改其逻辑。`app.py`、i18n、E2E 共享工具按最小增量编辑，先核对其他会话未提交改动 |

明确不做：

- 不新建表、不改 `config_version`、不新增套餐/额度/预算/充值/发票。
- 不做实时计费、跨日单次 run 拆分、价格快照、媒体核算上下文补建、历史回填。
- 不删除或重写管理员运营台，不顺手修 findings backlog 的采集缺口。
- 不把 Demo 数字当生产期望值，不在 UI 默认展开首个任务；Demo 的首条展开只用于展示结构。
- 不修改已随 works-assets 批合入 main 的 `frontend/playwright.config.ts`、`workspace-home.tsx`、`workspace-nav-menu.tsx` 等无关文件；它们仍非本批目标，禁改边界不变。

---

## 四、分任务实施清单

### 任务 0：在首笔提交前完成执行隔离与漂移检查

**Files**：
- Move: `docs/plans/pending/2026-11-19-personal-usage-optimization-plan.md` → `docs/plans/2026-11-19-personal-usage-optimization-plan.md`（仅实际执行时移动；本次定稿仍保留在 pending）

- [x] **Step 1：确认执行目录不混入其他批次**。先运行 `git status --short --branch`；若当前目录位于其他主题分支或存在其他会话未提交改动，不得 stash、clean、覆盖或径直提交，须按仓库 worktree 约定另找干净 main 基线。
- [x] **Step 2：先比较当前基准与执行日 main**。运行 `git diff b2378833..main --stat`，重点核对三份 i18n 的 `courseItems` 六键与 workspace 组件引用，以及本计划允许路径、数据模型、计价 helper、角色边界、视频路由和 E2E 基建；漂移影响 §1–§3 契约时先修订计划再继续。若该基准因后续历史改写不可达，记录事实并直接核对执行日 main 代码，不得用尚未比较的新基准制造空 diff；原 `09ef5c60` 仅作 §1.5 历史记录，不要求 fresh clone 必须可取。
- [x] **Step 3：完成比较后再按需 re-pin**。若执行日 main 已离开 `b2378833`，在 Step 2 完成后将状态行基准更新为实际 main tip，并在执行 changelog 记录新基准；未前进则保持 `b2378833`。禁止先 re-pin 再做历史漂移比较。
- [x] **Step 4：在第一次代码提交前建分支**。从已评估并 re-pin 的干净 main 创建独立 worktree（当前目录不干净时必需）与 `personal-usage-optimization` 分支；任务 1–7 的提交只能落在该分支，任务 7 不得再临时建分支或改回其他主题分支整理提交。
- [x] **Step 5：携带定稿计划、移动并勾选**。记录实际执行日期；若新建 worktree，先检查其中的 pending 文件是否为本“二次复核 2026-11-21”版本，不是则从原工作区复制本定稿到目标 worktree 同一路径，禁止用 main 上可能存在的旧版覆盖，再将本计划移回 `docs/plans/`。验收完成前状态头保持门禁可识别的“未执行（实施中）”，任务 7 再改为已执行并链接 changelog。任务 0 勾选和文档移动随任务 1 首笔提交，不在旧分支留下文档提交。

### 任务 1：固定六类映射、状态、成本来源与自然日窗口

**Files**：
- Create: `backend/app/gateway/_shared/personal_usage/{__init__.py,types.py}`
- Modify: `docs/plans/2026-11-19-personal-usage-optimization-plan.md`（任务 0/1 勾选与执行日期）
- Test: `backend/tests/test_personal_usage.py`

**Interfaces**：产出 §2.1 全部类型与四个纯函数，后续 Agent、视频聚合和路由只消费这些定义。

- [x] **Step 1：先写失败测试**。覆盖六类正映射、全部旧别名、候选优先级、非六类返回 `None`；`success/ready`、§2.1 列出的全部非终态（至少含 `draft/story_ready/composing`）、失败/取消/中断/未知三态；7/14/30 天与 UTC+8 跨日边界；`real/estimated/mixed` 合并；非法 days 由路由层拒绝而不是纯函数静默接受。
- [x] **Step 2：运行并确认失败**。`cd backend && PYTHONPATH=. uv run pytest tests/test_personal_usage.py -q`，预期因模块/函数不存在而 FAIL。
- [x] **Step 3：实现 §2.1 类型与纯函数**。旧别名必须调用 `resolve_retired_agent` 后过六类白名单；状态未知值保守归为 `incomplete`；`cache_read_tokens` 保留可空性，Token 展示不读取存储 `total_tokens`，缓存读取不进入独立 Token 加法。
- [x] **Step 4：运行定向测试并确认通过**。同 Step 2，预期全绿；同时运行 `tests/test_retired_agent_alias.py`，证明未另建漂移别名表。
- [x] **Step 5：提交**。`feat(usage): 定义六类任务用量口径`，提交中包含任务 0 的计划移动与勾选，不包含其他会话文件。

### 任务 2：实现四类 Agent 任务归组与模型/Token/费用聚合

**Files**：
- Create: `backend/app/gateway/_shared/personal_usage/agent.py`
- Modify: `backend/app/gateway/_shared/personal_usage/types.py`（仅确需的内部类型）
- Test: `backend/tests/test_personal_usage_agent.py`

**Interfaces**：实现 `aggregate_agent_usage(session, *, user_id, window, capability) -> list[UsageTask]`，只产出四类 Agent 任务。

测试夹具至少包含：同一 `thread_id` 两次运行、不同新旧状态组合、旧 `course-ppt`、非六类 Agent、另一用户、窗口外 run、窗口内新媒体配合窗口外最新 run、无模型拆分 run、无 run 的线程、本人 thread 下 `RunRow.user_id IS NULL` 的历史 run，以及同一 owned thread 下另一非空用户的污染 run。

- [x] **Step 1：写真实 SQLite 状态、归组与时间失败测试**。两次同线程 run 必须合并为一个 `agent:<thread_id>`；旧失败 + 最新成功 → `completed`，旧成功 + 最新进行中 → `running`，旧进行中 + 最新失败 → `incomplete`；最新 run 在窗口外但窗口内存在可归因媒体时仍取该最新 run 的当前状态，窗口外消耗不得进入；无 run 且 `ThreadMetaRow.status="idle"` → `incomplete`。同时验证标题、旧别名、§2.2 `activity_at` 排序键与对外 `updated_at`；本人 thread 的 NULL `user_id` run 可纳入，另一非空用户的污染 run 必须排除，另一用户拥有的 thread 永不出现。
- [x] **Step 2：写数值、Token 与费用来源失败测试**。所有金额断言使用 §2.2 的 `pytest.approx(..., abs=1e-9)`；LLM 真账只写入 `_run_cost` 消费的 `token_usage_by_model[model].amount`，不得虚构 RunRow 独立 amount 列。`priced-model` 单价输入 10/百万、输出 30/百万：run1 输入 60/输出 40，run2 输入 10/输出 10；任务 `total_tokens = 120`、模型输入 70/输出 50、`llm_cost = 0.0022`、`cost_source="estimated"`。窗口外 999 Token 不得进入；无可关联媒体行时 `media_cost is None`。另断言：①存储 `RunRow.total_tokens=999` 或 bucket `total_tokens=998` 时上述 120 与模型派生值不变，输入/输出对账仍通过；②缓存字段缺失 → `cache_read_tokens is None`，显式 0 → 0；③无 pricing 的真实 bucket `amount=1.5` → `llm_cost=1.5/currency="CNY"/cost_source="real"`；④同一 run 真实 3 + 估算 8 → 成本 11、`cost_source="mixed"`；⑤非法 `amount` 回还价目后标 `estimated`；⑥失败 run 的 20 Token 仍计入；⑦无模型拆分或混合完整度时 `models=[]` 但总值保留；⑧无价格且无真实 `amount` 时成本为 `None` 而非 0。
- [x] **Step 3：先确认测试失败，再实现最小聚合**。Agent 消耗只查询窗口内 `operation_kind="run"`，严格使用 §2.2 owned-thread 锚点与“本人或 NULL”run 过滤；当前状态批量另查全时间最新 run 并遵循无 run 回退；媒体只查询 `external_api_calls.task_id` 可关联到这些 thread 且 `user_id` 属于本人的记录。按 `thread_id` 分组，读取 `ThreadMetaRow.display_name` 和已有 metadata/assistant 候选；Token 按输入 + 输出派生，模型明细遵守 §2.2“全有或全无”；逐 bucket 判定真实/估算来源，复用 `_run_cost` 与 `_token_cost`，不得复制定价算法或旧 `compute_task_costs` 的来源标注。
- [x] **Step 4：运行 `tests/test_personal_usage_agent.py` 与任务 1 回归并确认通过**。确认 Step 1/2 的状态、权限、数值、Token、模型完整性和费用来源断言全部通过。
- [x] **Step 5：提交**。`feat(usage): 聚合 Agent 任务消耗`

### 任务 3：实现两类视频任务、可关联媒体费用与总汇总

**Files**：
- Create: `backend/app/gateway/_shared/personal_usage/{video.py,service.py}`
- Test: `backend/tests/test_personal_usage_video.py`

**Interfaces**：实现 `aggregate_video_usage(...) -> list[UsageTask]` 与 §2.2 `get_personal_usage`，供路由唯一调用。

固定数值夹具：

- `video-1` / `ai_image` / `ready`：窗口内 LLM 输入 120、输出 80，派生总 Token 200，模型同任务 2，故 `llm_cost = 0.0036` 且该分量为 `estimated`；一条 `task_id=video-1` 的媒体行 `metadata_json={"amount": 0.5}`，故 `media_cost=0.5`、`cost=0.5036`、`cost_source=mixed`。
- `video-2` / `ai_video` / `running`：窗口内创建但无消耗行，必须出现且 `cost/total_tokens=None`。
- 与任务 2 的一个 Agent 任务合并后：`summary.total_tokens=320`、`summary.cost=0.5058`、币种 CNY、`cost_source=mixed`。`video-2` 的未知值不得把上述已知小计变成 0 或 `null`。

- [ ] **Step 1：写视频任务、总汇总与分页失败测试**。覆盖上述数值（金额统一按 §2.2 近似比较）、`ai_image/ai_video` 白名单、其他 task_type 与 domain 回落 `thread_id` 排除、媒体 `task_id` 关联、视频无消耗进行中任务、§2.2 `activity_at` 与 `TaskRow.updated_at`；额外构造 21 个任务，断言 `limit=20` 第一页 20 且 `has_more=True`、第二页 1，两页 summary 均为完整 21 任务合计。
- [ ] **Step 2：写未知与对账断言**。无价格且无真实 `amount` 时费用 `None` 而 Token 保留；视频 LLM 无 pricing 但有真实 `amount=1.5` 时同样返回 `llm_cost=1.5/currency="CNY"/cost_source="real"`；无归因媒体时 `media_cost=None`；**有 `task_id` 但 `evaluate_media_call` 返回 `source="unpriced"` 时同样返回 `media_cost=None`**，不得虚构 0 或在 API 额外宣称“未归因”；真实媒体 `metadata_json.amount=0` 可保留 0；混币不求和；summary 是全部任务的已知小计而非当前页合计。
- [ ] **Step 3：运行并确认失败，再实现批量查询与合并**。LLM 与媒体行按窗口过滤、按 task_id 批量取出；Token 按输入 + 输出派生，LLM 来源按 §2.2 逐 bucket 判定；复用 `evaluate_media_call`，不得每任务一次查询（N+1），不得为区分未归因/未计价另建媒体计价算法或扩大 API 字段。
- [ ] **Step 4：运行任务 1–3 定向测试并确认通过**。确认 Step 1/2 的近似金额、视频真实 LLM 来源、未知媒体、跨分页对账与完整 summary 断言全部通过。
- [ ] **Step 5：提交**。`feat(usage): 汇总视频任务与个人总账`

### 任务 4：暴露只读 `/api/usage` 并锁定权限和响应契约

**Files**：
- Create: `backend/app/gateway/routers/personal_usage.py`
- Modify: `backend/app/gateway/app.py`（仅注册 router）
- Test: `backend/tests/test_personal_usage_router.py`

**Interfaces**：产出 §2.3 唯一 HTTP 契约；前端不得再请求旧五个 console 读接口来拼个人页。

- [ ] **Step 1：用 `make_authed_test_app` 写路由失败测试**。验证 200 响应字段、默认 days=7、7/14/30、六类筛选、offset/limit、tz 参数、窗口日期、UTC `updated_at`；响应中的 `cache_read_tokens=null` 不得被序列化为 0，模型/任务 `total_tokens` 已是输入 + 输出派生值，真实 LLM `amount` 无价目时返回 `cost_source="real"/currency="CNY"`。
- [ ] **Step 2：写边界测试**。非法 days/capability/limit/offset 返回 422；无 SQL 返回 503；另一用户的任务永不出现；API 无 `user_id` 参数；管理员调用仍只返回本人个人用量。
- [ ] **Step 3：运行确认失败，再实现 Pydantic 响应模型、路由与注册**。不得新增写接口或鉴权分支。
- [ ] **Step 4：运行 `tests/test_personal_usage_router.py` 及任务 1–4 全部测试，并复跑 `tests/test_console_router.py` 证明管理员 API 未回归**。
- [ ] **Step 5：提交**。`feat(usage): 增加个人用量只读接口`

### 任务 5：建立前端数据层、格式与角色边界

**Files**：
- Create: `frontend/src/core/usage/{types.ts,api.ts,hooks.ts,routes.ts}`
- Create: `frontend/src/components/workspace/console/console-role-page.tsx`
- Create: `frontend/src/components/workspace/usage/personal-usage-page.tsx`（仅最小可挂载容器，任务 6 完成视觉与交互）
- Modify: `frontend/src/app/workspace/console/page.tsx`
- Modify: `frontend/src/core/utils/format.ts`、`frontend/src/core/console/format.ts`（re-export）
- Test: `frontend/tests/unit/core/usage/{api.test.ts,routes.test.ts,hooks.dom.test.tsx}`
- Test: `frontend/tests/unit/components/workspace/console/console-role-page.dom.test.tsx`

**Interfaces**：

- `fetchPersonalUsage(options): Promise<PersonalUsageResponse>`，只调用 `/api/usage`。
- `usageTaskHref(task): string`：Agent 固定调用 `pathOfThread(task.source_id, { agent_name: task.capability })` 生成 canonical URL，四类 capability ID 即真实 agent 名，禁止退回 `/workspace/chats/<id>` 或自造路由；视频返回 `/workspace/video-gen/<encodeURIComponent(source_id)>`。
- `ConsoleRolePage`：通过现有 `useIsConsoleAdmin()` 读取角色（内部以 `system_role` 为准），admin → 原 `ConsolePage`；regular → 新 `PersonalUsagePage`，两个页面不共存；不得另写角色判断。

- [ ] **Step 1：写 API/路由失败测试**。断言 7/14/30、capability、offset、limit、tz 全部进入 query；Agent 旧类型已归一到六类 URL，视频详情路径正确；通用 `null` 与缓存 `cache_read_tokens=null` 格式化 `—`，显式真实 0 正常显示，真实 LLM 费用的 CNY/`real` 字段不在前端被改写成估算或 0。
- [ ] **Step 2：写角色与 query 隔离失败测试**。regular 不挂载旧 ConsolePage，五个 console 查询和定价请求均为 0；admin 不请求 `/api/usage`；admin→user 同帧卸载定价和旧查询，不泄露 admin cache。
- [ ] **Step 3：运行确认失败，再实现数据层与角色边界**。`formatInteger` 移至 `core/utils/format.ts` 并由 console re-export；现有 console 组件逻辑不改。同步实现最小 `PersonalUsagePage` 容器，只调用 `usePersonalUsage` 并提供加载/错误出口，保证本任务提交可独立构建，完整视觉在任务 6 完成。
- [ ] **Step 4：运行 usage 单测、角色测试及 `console-role-transition`、`console-query-scope` 回归并确认通过**。
- [ ] **Step 5：提交**。`feat(usage): 建立个人页数据层与角色边界`

### 任务 6：按 V4 实现两张总览卡、六项筛选、任务与模型明细

**Files**：
- Modify: `frontend/src/components/workspace/usage/personal-usage-page.tsx`
- Create: `frontend/src/components/workspace/usage/{usage-summary.tsx,usage-capability-filter.tsx,usage-task-list.tsx,usage-model-breakdown.tsx}`
- Modify: `frontend/src/core/i18n/locales/{types.ts,zh-CN.ts,en-US.ts}`
- Test: `frontend/tests/unit/components/workspace/usage/{personal-usage-page.dom.test.tsx,usage-task-list.dom.test.tsx,usage-model-breakdown.dom.test.tsx}`

**Interfaces**：消费任务 5 的 response/hook/route。六类筛选直接映射 `WORK_CAPABILITIES`，文案必须取 `t.workspace.home.courseItems[id].title`，不得另建能力名册。

- [ ] **Step 1：写页面行为与键盘失败测试**。两张卡展示总费用/总 Token；仅 7/14/30；六类与“全部”筛选项存在；切换后 offset 复位、展开收起、汇总随响应更新；任务状态严格显示已完成/进行中/未完成。任务行可经 Tab 聚焦并用 Enter/Space 展开收起，独立“打开原任务”按钮可经键盘触发且不得冒泡触发行展开。
- [ ] **Step 2：写模型、未知态与边界失败测试**。模型输入/输出/缓存/总 Token/费用逐列正确，总 Token 使用接口已派生值；缓存 `null` → `—`、显式 0 → 0；`LLM + 媒体 = 任务已核算` 仅在分量已知时成立；无归因和已归因未计价产生的 `media_cost=null` 均显示“媒体费用未核算”，不得显示“未归因”或 0；`models=[]` 显示“暂无模型明细”；无价格费用 `—`，Token 不丢；真实 LLM `amount` 的 `real/CNY` 来源正确；进行中注明截至目前、未完成保留消耗；长模型名、超大 Token、当前页不足 20、筛选无数据和请求失败均有明确断言，请求失败不得伪装成全 0。
- [ ] **Step 3：运行确认失败，再实现组件与 i18n**。默认全部收起；任务行使用可聚焦的语义交互元素并支持 Enter/Space，展开区独立“打开原任务”按钮调用 `usageTaskHref`，不得嵌套按钮或让按钮点击冒泡；实现加载、错误重试、统一空态、20 条/页与上一页/下一页。
- [ ] **Step 4：运行 usage DOM 测试、i18n 类型检查和相关 console 回归并确认通过**。确认 Step 1/2 的键盘、模型、未知态、长模型名、大数、分页、空态与错误断言全部通过。
- [ ] **Step 5：提交**。`feat(usage): 重构个人用量页面`

### 任务 7：真实行为验收、全量回归与交付文档

**Files**：
- Create: `frontend/tests/e2e/personal-usage.e2e.ts`
- Create: `frontend/playwright.personal-usage.config.ts`
- Modify: `frontend/tests/e2e/utils/download-gateway.mjs`（仅增加测试身份响应）
- Create: `docs/changelog/changelog-<实际执行日>-personal-usage-optimization.md`
- Modify: 本计划状态头与勾选；若产生范围外发现，按既有格式追加 `docs/findings-backlog.md`，不得覆盖他人未提交修改

**E2E 配置约定**：专用配置可继承现有 Playwright 的 `testDir/use/projects` 等配置，但 `testMatch = "**/*.e2e.ts"`，因此默认套件不会误跑该文件。**Next webServer 的 `env` 必须整块替换，不得照抄基础 env 或指望 CLI/浅继承覆盖**：显式设置 `SKIP_ENV_VALIDATION="1"`、`DEER_FLOW_AUTH_DISABLED="0"`、`DEER_FLOW_INTERNAL_GATEWAY_BASE_URL` 指向 download-gateway；基础配置的 `DEER_FLOW_AUTH_DISABLED="1"` 不得残留。专用 lane 使用独立端口 3127/3137 和独立构建。`download-gateway.mjs` 为 `/api/v1/auth/me` 返回固定普通测试用户；测试先写 `access_token` cookie，SSR 鉴权走测试 gateway，其余 API 仍由 Playwright route mock，禁止真实数据库和付费供应商。

- [ ] **Step 1：先写 Playwright 行为断言并做反向验证**。普通用户打开 `/workspace/console` 看到新页面，并显式断言 SSR 初始身份为 regular 普通用户而非 auth-disabled admin；7/14/30 切换时总费用、总 Token、任务和模型明细同步变化；筛选后总额等于该能力全部任务合计；展开两个模型可对账，总 Token 等于输入 + 输出，真实 LLM `amount` 按供应商回传与 CNY 展示且不得标为估算，打开原任务 URL 正确。暂时将 `ConsoleRolePage` 强制回退旧页面，确认新页面与零旧请求断言会失败，随后立即恢复，防止“永远为绿”的假 E2E。
- [ ] **Step 2：补充分页、状态、键盘与响应式断言**。21 任务跨页合计不变；进行中显示截至目前；未完成消耗不为 0；未归因与已归因未计价均显示“媒体费用未核算”且不显示 0 或“未归因”；缓存缺失显示 `—`、显式 0 显示 0；Tab 可聚焦任务行，Enter/Space 可展开收起，原任务按钮可键盘触发且不冒泡；390px 与 320px 无页面级横向溢出，模型表只能在自身容器内滚动；无旧 console/定价请求。
- [ ] **Step 3：运行专用 E2E 并修复至通过**。`cd frontend && CI=1 E2E_PORT=3127 E2E_GATEWAY_PORT=3137 pnpm exec playwright test --config playwright.personal-usage.config.ts`。
- [ ] **Step 4：代码冻结后串行运行全量门禁**（均后台，禁止共享可变构建目录）：

```bash
cd backend && env -u RUN_LIVE uv run pytest tests/ -q
cd frontend && pnpm check && pnpm test
cd frontend && CI=1 E2E_PORT=3107 pnpm test:e2e
cd frontend && CI=1 E2E_PORT=3127 E2E_GATEWAY_PORT=3137 pnpm exec playwright test --config playwright.personal-usage.config.ts
```

- [ ] **Step 5：执行一次隔离真实 API 行为验收**。用临时 SQLite 和测试身份播种六类、两用户、两周期、缺价、无模型明细及可关联媒体调用，并加入多 run 状态倒序、存储 `total_tokens` 与输入 + 输出不一致、缓存字段缺失、无 pricing 的真实 LLM `amount`、已归因未计价媒体、本人 thread 下 NULL `user_id` run 与另一非空用户污染 run 六组边界；经真实 `/api/usage` 验证 §2.1–§2.3，不调用 create、不调用任何付费 API、不读取开发或用户数据库。结果写入 changelog 的「验收记录」节。
- [ ] **Step 6：运行最终检查**。根目录 `make check`、`python3 scripts/check_docs_convention.py`、`git diff --check` 全部通过；检查并删除测试临时产物，不删除既有或陌生文件。
- [ ] **Step 7：更新计划勾选与状态头，编写 changelog**。必须包含实现级/行为级验收命令及实际关键输出、已知数据边界、未验证项、是否涉及 config_version（本批应为“不涉及”）。
- [ ] **Step 8：按仓库 PR 流程交付**。跨 backend/frontend 且超过 5 文件，直接使用任务 0 已创建且承载全部提交的 `personal-usage-optimization` 分支 + PR + OCR；全量本地门禁先于 push，无 Critical/Important 且 CI 绿后才可 rebase merge。

---

## 五、Review Focus

1. **存储总 Token 不一致、缓存缺失、无 usage、真实 0 同时存在**：展示与对账只认输入 + 输出，缓存未知必须 `null`，真实 0 不得变成未知；任务 2/3/4/6 均有测试。
2. **真实 LLM `amount`、媒体未归因/未计价或混币**：真实/估算来源必须逐分量合并，无价目真实 LLM 按 CNY 单币假设披露；不得虚构媒体 0、把未计价写成未归因或跨币种求和；任务 2/3/6 覆盖。
3. **多 run 状态、进行中、未完成、窗口边界任务**：当前状态取全时间最新 run，消耗仍限窗口；不得清零已产生消耗、伪造实时值或跨日精确分摊；任务 1–3 与 E2E 覆盖。
4. **角色切换、多用户缓存与 NULL 历史 run**：普通用户不能发旧 console/定价请求，admin→user 不能泄露全量数据；本人 thread 的 NULL run 可纳入，另一非空用户污染 run 必须排除；任务 2/4/5/7 覆盖。
5. **筛选、分页、键盘、长模型名和大数**：总额必须跨全部页对账，分页按 `activity_at` 稳定排序且活性 ping 不扰序，Tab/Enter/Space 可操作，页面不能被长模型名或数字撑破；任务 2–7 覆盖。

---

## 六、完成定义

只有同时满足以下条件才可宣称完成：

- 普通用户页面与 V4 的信息架构一致，且只出现六项真实能力。
- 7/14/30 天、能力筛选、任务费用、Token、模型明细来自同一响应和同一窗口。
- 总览等于筛选后全部任务的已核算合计，不是当前页合计；未知任务不清零其他已知小计。
- 分页按 `activity_at DESC, task_key DESC` 稳定排序，活性 ping 不扰序；对外 `updated_at` 按 §2.2 独立于排序键取值。
- Agent 当前状态取全时间最新 run，视频状态取 `TaskRow.status`；进行中无伪实时，未完成消耗不清零。
- 任务与模型总 Token 均由输入 + 输出派生；存储 `total_tokens` 不参与展示或模型完整性对账。
- `null`、真实 0、缓存缺失、无模型明细、媒体未归因/未计价、无价格、真实 LLM `amount` 和混币均按本计划处理；媒体 `null` 统一显示“媒体费用未核算”。
- 原任务链接、加载/错误/空态、分页、Tab/Enter/Space 键盘操作及 320/390px 响应式通过行为验收。
- 管理员运营台、权限、计价和采集逻辑无回归；NULL 历史 run 按 §2.2 纳入，另一非空用户污染 run 被排除；无新表、无新生产依赖、无 config_version、无真实付费调用。
- 全量 backend/frontend 测试、既有与专用 E2E、根 `make check`、docs convention、`git diff --check` 全绿；changelog 含可审计验收记录。
