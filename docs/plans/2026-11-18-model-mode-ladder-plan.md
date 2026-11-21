# 模型模式阶梯（闪速 / 标准 / Ultra）落地计划

> **状态**：已执行（2026-11-21：Task 0–6 本地实现与验收完成；[PR #37](https://github.com/littlebear2020/SuperTrainerAgent/pull/37) 已创建，CI 待绿；[changelog](../changelog/changelog-2026-11-21-model-mode-ladder.md)）。
> **TL;DR**：把用户可见模式收敛为「闪速 / 标准 / Ultra」，删除「思考」档与推理深度入口；按模式限定可选模型并显式配置默认（闪速=qwen3.8-flash，标准=claude-sonnet-5-5，Ultra=qwen4-max）。思考强度由模式内部固定（闪速关、标准/Ultra 最大），Claude 保留但无思考输出；DeepSeek 保留为可选、不作默认（2026-11-20 决策）。影响面 = config schema/模型清单、factory 防泄漏、`/models` API、前端模式与模型选择；不改后端 agent 管线、部署形态与数据库。
> **基准**：评审时 `origin/main = 35338212`；当前 `config_version: 31`，本批升到 32。模型能力依据为 2026-11-18 全量探针与压测，闪速 minimal 参数已补测。

> **For agentic workers：** 按 Task 0→6 串行执行并勾选；每步先写失败测试再实现。本批跨 backend config schema + gateway API + frontend，必须从最新 `origin/main` 建独立 worktree、分支 + PR，不直推、不并行修改共享基座。**前置次序**：DSML 守卫计划已由用户决定不执行（2026-11-20），本计划接受守卫空窗，DeepSeek 仅保留为可选、不得设为任何档位默认；`core-product-frontend-reliability` 与 `agent-showcase-visual-fidelity` 排在本计划之后并 rebase。不得与 reliability 并行改 `input-box.tsx` / `core/threads/hooks.ts`。

**Goal：** 用户只看到三个模式，每个模式只列该档允许的模型并自动落到合法模型；思考强度不再作为用户选项，由模式内部映射为 `thinking_enabled + reasoning_effort`。

**Architecture：** `ModelConfig.modes` 声明模型属于哪些模式，顶层 `mode_defaults` 声明每档默认模型；factory 显式排除 `modes`，避免配置字段进入 provider。`/api/models` 下发两者，前端统一做模式归一、模型过滤、默认/回退和提交参数派生。后端 agent/factory 管线不改语义，只消费既有参数。

## 一、最终设计

### 1.1 模式 × 模型

| 模式 | 可选模型 | 默认 | 内部行为 |
|---|---|---|---|
| **闪速** | `qwen3.8-flash`、`deepseek-v4.1-flash` | `qwen3.8-flash` | 关思考（`thinking: disabled` + `reasoning_effort: minimal`）、不规划、不调子代理 |
| **标准** | `deepseek-v4.1-flash`、`claude-sonnet-5-5` | `claude-sonnet-5-5`（2026-11-20 修订） | 规划开、不调子代理；Claude 无思考、响应快；DeepSeek 手动选择时开思考（`thinking: enabled` + `reasoning_effort: high`） |
| **Ultra** | `qwen4-max`、`kimi-k3.1-pro`、`claude-sonnet-5-5` | `qwen4-max` | 最大思考（`thinking: enabled` + `reasoning_effort: high`）、规划 + 子代理 + 更长执行预算；Kimi 标注长文档/慢，Claude 无思考 |

### 1.2 模式 → 请求参数

| mode | `thinking_enabled` | `reasoning_effort` | `is_plan_mode` | `subagent_enabled` |
|---|---|---|---|---|
| `flash` | false | `minimal` | false | false |
| `standard` | true（模型支持时） | `high` | true | false |
| `ultra` | true（模型支持时） | `high` | true | true |

- `reasoning_effort` 只有模型声明 `supports_reasoning_effort: true` 才转发；Claude 声明 false，参数被 factory 丢弃，不报错。
- 前端按当前 `model_name` 查 `supports_thinking`，Claude 在标准/Ultra 发 `thinking_enabled: false`；后端现有 warning 保留作防线，不降低级别。
- `reasoning_effort` 完全由模式派生，**不再允许 localStorage 或旧 thread context 的显式值压过模式矩阵**。
- 「标准 / Ultra 最大思考」= `thinking enabled + effort high`；Ultra 与标准的差异是规划、子代理与执行预算，不是更高思考强度。

### 1.3 模型能力配置

| 模型 | 上游 ID | modes | supports_thinking | supports_reasoning_effort | supports_vision | 备注 |
|---|---|---|---|---|---|---|
| qwen3.8-flash | `qwen/qwen3.8-flash` | `[flash]` | true | true | true | 保持 `models[0]`，供 title/summarization/memory 等内部轻量调用 |
| deepseek-v4.1-flash | `deepseek/deepseek-v4.1-flash@360-latency` | `[flash, standard]` | true（现 false，修正） | true | true（现 false，修正） | `PatchedChatDeepSeek` + `api_base`；内部 `name` 不变 |
| claude-sonnet-5-5 | `anthropic/claude-sonnet-5-5` | `[standard, ultra]` | false | false | true | 不支持强制 `tool_choice`；项目未使用，保持 auto |
| qwen4-max | `qwen/qwen4-max` | `[ultra]` | true | true | true | 新增；`PatchedChatOpenAI` + `base_url` |
| kimi-k3.1-pro | `moonshotai/kimi-k3.1-pro` | `[ultra]` | true | true | true | 新增；`stream_chunk_timeout: 300`，`request_timeout: 300` |
| 除上表外的全部在册模型（含 qwen3.8-max / glm / terra / opus / haiku） | 不变 | `[]` | 维持现状 | 维持现状 | 维持现状 | 不参与模式筛选；qwen3.8-max 继续供 `video_gen.vimax_llm_model` 等内部引用 |

四个 thinking 模型统一使用 **`when_thinking_*` 成对配置，不使用 `thinking:` 快捷字段**：

```yaml
  supports_thinking: true
  supports_reasoning_effort: true
  when_thinking_enabled:
    extra_body:
      thinking:
        type: enabled
  when_thinking_disabled:
    extra_body:
      thinking:
        type: disabled
    reasoning_effort: minimal
```

**形态约束（外部评审 C2 复核修订）：** `thinking:` 快捷字段会进入 `effective_wte["thinking"]`，对这批 OpenAI 网关模型不是正确形态；上述写法在 enable 时进入 `extra_body.thinking`，disable 时显式给出 `disabled + minimal`。仅写 `when_thinking_disabled` 会走「用户配置优先」分支，**不会自动补 minimal**，因此 `reasoning_effort: minimal` 必须保留在 YAML 中。

**2026-11-18 真实探针：** qwen3.8-flash 与 `deepseek@360-latency` 在 `disabled + minimal` 下各连续 3 次均为 `reasoning_tokens=0`、答案正确、工具调用正常；`enabled + high` 均有推理产出。`@360-latency` 不在 `GET /v1/models` 目录，但网关可直连，基础对话/工具/thinking/vision 全过，短问答 0.8–1.9s。

### 1.4 模式切换与持久化语义

- 内部模式值改为 `flash / standard / ultra`；旧 `thinking` / `pro` 读取时归一为 `standard`，未知值归一为 `undefined` 后走默认 `standard`。
- **档内手动选择保留**：切档时当前模型若仍属于新档，继续沿用；若不属于，切到 `mode_defaults[mode]`。
- 档内模型被删或配置不再允许时，按「显式默认 → 该档第一个可用模型 → 全列表第一个模型」回退。
- 不额外实现「返回原档恢复上次手动选择」；单一 `model_name` 持久化即最终语义。per-thread 模型覆盖同样经过合法性纠偏。
- `modelDelistedToast` 只在模型不在**全量**在册列表时提示；仅因跨档被过滤不提示。
- 加载旧设置时清空旧 `reasoning_effort`；之后不再把它作为独立用户设置持久化。

### 1.5 用户可见文案

| 模式 | 名称 | 描述 |
|---|---|---|
| `flash` | 闪速 | 最快响应，适合改写、抽取、标题等轻量任务 |
| `standard` | 标准 | 默认档：规划开、响应快，可手动切换深度思考模型 |
| `ultra` | Ultra | 最强档：多子代理协作，适合复杂多步骤任务，耗时更长 |

模型描述与模式解耦：
- `deepseek-v4.1-flash`：支持深度思考，速度均衡（不再作为任何档位默认，仅在手动选择时使用）。
- `claude-sonnet-5-5`：响应快，不输出思考过程。
- `qwen4-max`：最强思考，首字约 5–7 秒。
- `kimi-k3.1-pro`：长文档专用，首字约 9–28 秒。

## 二、评审决策记录

| # | 问题 | 结论 | 依据 |
|---|---|---|---|
| Q1 | 是否保留用户可见的「思考强度」开关？ | **不保留**。闪速=关，标准/Ultra=high。 | 只有 4 个模型真支持；low≈medium 无感知差异；模型 × 强度解释矩阵过大 |
| Q2 | 是否保留「思考」模式？ | **删除**，语义并入标准与 Ultra。 | 原「思考」与 Pro 描述重叠 |
| Q3 | 闪速是否默认开最大思考？ | **否**。 | 首字约 2–3 倍、token 约 5–6 倍 |
| Q4 | 闪速默认模型是否换成 deepseek？ | **否**，维持 qwen3.8-flash。 | 快速档基准 42/43 vs 41/43，延迟持平，qwen 已有生产验证 |
| Q5 | 标准默认用 qwen4-max 还是 deepseek/Claude？ | **claude-sonnet-5-5**（2026-11-20 修订；原定 deepseek-v4.1-flash）。 | 用户决定不执行 DSML 守卫计划，避免把 DeepSeek 设为默认放大缺陷暴露面；Claude 响应快、无思考，标准档靠规划补质量。原依据保留：deepseek 1.3–2.8s、p90 2.7s、40/41；qwen4-max 首字 4.7–6.7s |
| Q6 | Ultra 默认用 qwen4-max 吗？ | **是**。 | 最大强度全维度 40/41、工具 6/6 |
| Q7 | Kimi 是否进 Ultra？ | **是**，标注长文档/慢。 | 最大思考首字 8.6–27.7s；工具 6/6 |
| Q8 | Claude 无思考，是否进标准与 Ultra？ | **是**，定位最快、无思考，靠规划/子代理补执行深度。 | p50 1.8s、首字 1.5s；项目未使用强制 tool_choice |
| Q9 | GLM / Gemini / gpt-6.x 是否入模式矩阵？ | **否**。 | GLM 低档不可控且慢；Gemini 无思考输出；gpt-6.x 工具约束互斥 |
| Q10 | 模式值与旧数据如何迁移？ | 新值 `flash/standard/ultra`；旧 `thinking/pro → standard`，旧 effort 清空。 | 防止已删除的推理深度入口留下无法修正的 low/medium 残留 |
| Q11 | DeepSeek 用标准 ID 还是 `@360-latency`？ | **用 `@360-latency`**，内部 `name` 不变。 | 专属计费变体；直连实测可用，目录缺失不等于不可用 |
| Q12 | `minimal` 未上过线，能否直接作为闪速档？ | **可以**，两模型真实探针均验证。 | `disabled + minimal` 各 3 次 reasoning=0，工具正常；`enabled + high` 对照正常 |
| Q13 | 与其他在途计划的次序？ | **本计划 → reliability / showcase visual-fidelity**；DSML 计划不执行（2026-11-20 用户决策）。 | 本计划不得把 DeepSeek 设为任何档位默认，仅保留为可选；reliability 需在本批模式语义上重写模型校正分支与测试 |

## 三、外部评审修订对照（保留原方案 → 定稿差异）

| 项 | 复核结论 | 本计划的修订 |
|---|---|---|
| C1 `modes` 上 wire | **成立**：factory exclude 集未含新字段，空列表也会被 dump。 | Task 1 增加 factory exclude `modes`；allow 补 factory.py；新增全模型实例化 kwargs 断言 |
| C2 `thinking:` 块 | **成立**：OpenAI 网关形态错误，闪速关闭会静默失效。 | 全部改为 `when_thinking_*`；显式补 `reasoning_effort: minimal`；补 enable/disable 两态 factory 测试与真实探针 |
| C3 前端漏文件 | **成立**：`mode-hover-guide.tsx`、`welcome.tsx`、chat page 会受类型/i18n 影响。 | 全部纳入 Task 4 与 allow |
| `useModels` / hooks 数据源 | **成立**：只改 types 拿不到 defaults 与模型能力。 | `useModels` 透出 `modeDefaults`；`useThreadStream` 内查 `useModels` 派生 thinking |
| mount / submit guard | **成立**：原回退恒为 `models[0]`，会产生脏组合。 | 两条路径统一 `defaultModelForMode` |
| 旧 effort 覆盖新模式 | **成立**。 | 加载清空、提交一律按模式派生 |
| 碎测试盘点 | **成立且需扩面**。 | Task 5 逐文件列出 backend/frontend 处置；提交参数断言移到 threads hooks 测试 |
| `modes` 值校验 | **成立**。 | 使用 `Literal` 类型同时约束 `modes` 与 `mode_defaults` key，不只校验三个默认模型 |
| `is_plan_mode` / 回退分支 | **成立**。 | 行为清单补 plan/subagent 全矩阵、三级回退、旧后端回退、迁移与 sticky 用例 |
| 执行探针形态 | **成立**：裸 curl 401、裸 python 无 venv/环境变量。 | 改为 uv + env 加载的 config 探针；API 用 pytest 直接调用 router，不写无鉴权 curl |
| 评审对 disable 的解释 | **部分不准确**：显式 `when_thinking_disabled` 不会自动补 minimal。 | 定稿在 YAML 显式写 minimal，不照抄评审的「自动补齐」表述 |
| 在途计划数量 | **评审计数过时**：当前未跟踪计划为 9 份，不是 7 份。 | Task 0 只写「多份在途计划先入库」，不写死数量 |

## 四、改动清单

### Task 0：执行准备与串行约束

- [x] 规划侧先把本计划及仍需引用的在途计划以**单独纯 docs 批次**入库，作为跨会话次序载体；不与功能 diff 夹带。`agent-showcase-integration` 已在 `origin/main` 有实现文件，其状态头过时问题另行处理，不纳入本批。
- [x] 确认 DSML 守卫计划保持「不执行」（2026-11-20 用户决策），并核对本批未把 DeepSeek 设为任何档位默认；`git fetch origin`，从**最新 `origin/main`** 建 worktree/分支 `model-mode-ladder`，记录实际短 hash 与 `config_version: 31`。评审基准 `35338212` 仅用于漂移核对，不替代执行时最新基线。
- [x] 不带入当前工作区的无关修改与未跟踪素材。worktree 的 gitignored `config.yaml` 用**符号链接**指向主工作区文件；`.env` 只在当前 shell 以 `set -a; source <主工作区>/.env; set +a` 加载，不复制、不打印、不提交（主工作区无 `.env`，本地测试改用非敏感占位环境）。CI 无真实花名册时按现有 skip 口径运行。

### Task 1：后端 schema、factory 防泄漏与 API

- [x] `model_config.py`
  - [x] 新增 `ModeName = Literal["flash", "standard", "ultra"]`。
  - [x] 新增 `modes: list[ModeName] = Field(default_factory=list)`，非法值由 Pydantic 直接拒绝。
- [x] `app_config.py`
  - [x] 新增 `mode_defaults: dict[ModeName, str] = Field(default_factory=dict)`。
  - [x] 加 `@model_validator(mode="after")`：默认模型必须存在且其 `modes` 包含对应 mode；否则 `ValueError`。
- [x] `models/factory.py`
  - [x] `model_dump(exclude={...})` 增加 `modes`；除这一处外不改工厂逻辑。
- [x] `app/gateway/routers/models.py`
  - [x] `ModelResponse.modes: list[str] = []`；`ModelsListResponse.mode_defaults: dict[str,str] = {}`。
  - [x] 列表与详情端点同步填充；不暴露 api_key 等敏感字段。
- [x] `config.example.yaml` / `config.yaml`：`config_version: 31 → 32`，补 `modes` / `mode_defaults` 模板。

### Task 2：模型清单与思考参数

- [x] `qwen3.8-flash`：`modes: [flash]`，`supports_reasoning_effort: true`，补 §1.3 的 `when_thinking_*`。
- [x] `deepseek-v4.1-flash`：`model: deepseek/deepseek-v4.1-flash@360-latency`（内部 name 不变），`modes: [flash, standard]`，`supports_thinking/vision/reasoning_effort: true`，补成对配置。
- [x] `claude-sonnet-5-5`：`modes: [standard, ultra]`，thinking/effort 保持 false，不写 `when_thinking_*`。
- [x] 新增 `qwen4-max`：`modes: [ultra]`，`PatchedChatOpenAI + base_url`，`max_tokens: 32768`、`temperature: 0.7`、三个能力旗标 true、成对配置、`stream_chunk_timeout: 240`。
- [x] 新增 `kimi-k3.1-pro`：`modes: [ultra]`，同上，`stream_chunk_timeout: 300`、`request_timeout: 300`。
- [x] 除 §1.3 五个模型外的全部在册模型显式 `modes: []`；不改 qwen3.8-max 的内部引用。
- [x] `mode_defaults: {flash: qwen3.8-flash, standard: claude-sonnet-5-5, ultra: qwen4-max}`；`models[0]` 保持 qwen3.8-flash。
- [x] 执行修订：factory 的显式 `when_thinking_disabled` 分支弹出重复的运行时 `reasoning_effort`，确保配置中的 `minimal` 优先且真实 lead-agent 调用不发生重复 kwargs。

### Task 3：前端数据层、类型与迁移

- [x] `core/models/types.ts`：`Model.modes?: string[]`；`ModelsResponse.mode_defaults?: Record<string,string>`。
- [x] `core/models/hooks.ts`：`useModels` 透出 `modeDefaults`（缺省 `{}`）。
- [x] 新建 `core/models/modes.ts`
  - [x] `InputMode = "flash" | "standard" | "ultra"`。
  - [x] `normalizeMode(raw)`：`thinking/pro → standard`；`flash/standard/ultra` 原样；未知 `undefined`。
  - [x] `modelsForMode`：全部模型都缺 `modes` 字段时回退全量（旧后端）；否则仅 `modes.includes(mode)`，`modes: []` 不显示。
  - [x] `defaultModelForMode`：显式默认 → 该档首个 → 全列表首个。
- [x] `core/settings/local.ts`：使用统一 `InputMode`；`getLocalSettings()` 归一 mode 并清空旧 `reasoning_effort`；per-thread model override 保持，后续由合法性纠偏处理。
- [x] `core/threads/hooks.ts`：在 `useThreadStream` 内调用 `useModels`，按 `context.model_name` 查能力；归一 mode 后统一派生 §1.2 四个参数，忽略旧显式 effort。

### Task 4：前端模式菜单、模型选择与类型接线

- [x] `input-mode.ts`：复用统一 `InputMode`；`resolveMode` 默认 `standard`；`reasoningEffortForMode = flash→minimal，standard/ultra→high`。
- [x] `input-box.tsx`
  - [x] 从 `useModels()` 同时读取 `models` 与 `modeDefaults`，传入过滤/默认函数。
  - [x] 模式菜单三项；删除「思考」和「推理深度」全部 UI 与相关 handler/死变量。
  - [x] `models` 按当前模式过滤；`selectedModel` 只从合法集合解析。
  - [x] mount 自动校正 effect 与 `handleSubmit` guard 都改用 `defaultModelForMode`，不再恒取 `models[0]`。
  - [x] `handleModeSelect` 按 §1.4 保留合法手动选择、非法时切默认；不再持久化独立 effort。
  - [x] `handleModelSelect` 保持当前模式；delisted 检测仍基于全量模型，不把跨档过滤当撤册。
- [x] `mode-hover-guide.tsx`：本地 `AgentMode` 与 i18n key 改为三模式，删除 thinking/pro 分支。
- [x] `welcome.tsx`：prop 改用统一 `InputMode`。
- [x] `app/workspace/chats/[thread_id]/page.tsx`：随统一 context 类型通过 tsc，不改变提交/上传逻辑。
- [x] i18n 三文件：删除 `reasoningMode*` / `reasoningEffort*` 死键；`proMode*` 改 `standardMode*`；Ultra 描述强调子代理与耗时。

### Task 5：测试盘点与行为验证

- [x] 新增 `backend/tests/test_model_modes_config.py`
  - [x] schema：`modes` 非法值、`mode_defaults` 非法 key/不存在模型/不属于该 mode 全拒绝。
  - [x] factory：对全部在册模型实例化，`modes` 不在构造 kwargs；四个 thinking 模型 enable 时为 `extra_body.thinking=enabled`，disable 时为 `disabled + reasoning_effort=minimal`，无错误的顶层 `thinking` 快捷形态；Claude 不携带 thinking/effort。
  - [x] API：列表/详情返回 `modes`，列表返回 `mode_defaults`。
  - [x] 花名册与 ID：五个模型的 modes、deepseek `@360-latency`、Claude 无 thinking 块；两文件 `config_version=32`。
- [x] **全量复核并更新** `backend/tests/test_360_new_models_roster.py`，至少覆盖：
  - [x] `TARGET_MODEL_IDS` 与示例文本中的 deepseek ID（`:211/:242/:289`）。
  - [x] `view_image` 断言反转为 deepseek 可用（`:231-237`）。
  - [x] deepseek 三个能力旗标与 `when_thinking_*`（`:240-250`）。
  - [x] API 映射不要对 deepseek 继续断言 `supports_thinking/effort=false`（`:253-267`）。
  - [x] factory 的 effort pop 断言拆成 Terra 仍 pop、DeepSeek 保留/按模式派生（`:270-275`）。
  - [x] `config_version` 两处 pin 改 32（`:296-319`）。
- [x] `backend/tests/test_model_roster_live.py`：只在另获 `RUN_LIVE=1` 授权时更新并运行；不进入默认门禁（本批未获授权，未改未跑）。
- [x] 前端 `input-mode.test.ts`：三模式默认值、`normalizeMode`、effort 映射。
- [x] 新增 `core/models/modes.test.ts`：过滤、旧后端全量回退、三级默认回退、空列表、未知 mode。
- [x] 新增 settings 迁移测试：旧 `pro/thinking` 归一、旧 effort 清空、per-thread model override 保留。
- [x] 处置既有 InputBox 测试：
  - [x] `input-box-thinking-unavailable.dom.test.tsx`：原 3 个 thinking 用例删除/重写为「无思考模型仍可选标准，菜单无思考档」。
  - [x] `input-box-mode-decouple.dom.test.tsx`：`pro/medium` 改 `standard` 且不再持久化独立 effort；保留模型加载失败 toast 用例；delisted 用例改按新默认/模式语义。
  - [x] `input-box-new-thread-suggestions.dom.test.tsx`、`input-box-no-followups.dom.test.tsx`、`input-box-polish.dom.test.tsx` 纳入定向回归，按新 mock 数据补 `modes` 或验证旧后端回退，不静默删除。
- [x] 新增 `input-box-mode-models.dom.test.tsx`
  - [x] 默认 standard + claude；切 flash/Ultra 自动纠偏；合法共享模型保留；非法才切默认。
  - [x] 模型列表只显示本档模型；跨档过滤不触发 delisted toast。
- [x] 新增 `core/threads/thread-submit-mode-context.dom.test.tsx`（仿 `stream-isloading-at-submit` 捕获 `runs.stream` 入参）
  - [x] flash：`thinking_enabled=false`、`is_plan_mode=false`、`subagent_enabled=false`、`reasoning_effort=minimal`。
  - [x] standard + deepseek（手动切换）：`thinking_enabled=true`、`is_plan_mode=true`、`subagent_enabled=false`、`reasoning_effort=high`。
  - [x] standard + Claude（默认）：`thinking_enabled=false`、其余同上。
  - [x] Ultra：`subagent_enabled=true`；旧 `pro/thinking` 与旧 effort 不再覆盖矩阵。

### Task 6：文档、门禁与 PR

- [x] 按**实际执行日**创建 `docs/changelog/changelog-YYYY-MM-DD-model-mode-ladder.md`，包含 ≤5 行 TL;DR 和「验收记录」（命令 → 实际关键输出），不预写死日期。
- [x] 回本计划勾选并维护唯一状态头与 changelog 链接。
- [x] 运行并记录：`python3 scripts/check_docs_convention.py`、根 `make check`、`cd backend && uv run pytest tests/ -q`、`cd frontend && pnpm check && pnpm test`。
- [x] 分支 + PR；不自动合并、不部署（[PR #37](https://github.com/littlebear2020/SuperTrainerAgent/pull/37)）。reliability 后续批次在本 PR 合入后 rebase，并更新其 PR1 Step 5 与测试假设。

## 五、验证清单（实现级 / 行为级）

**实现级：**

- [x] 在加载 env 的 backend shell 执行（worktree 的 config 已按 Task 0 就位）：

```bash
PYTHONPATH=. uv run python -c 'from deerflow.config.app_config import AppConfig; c=AppConfig.from_file(); print(c.mode_defaults); print({m.name: m.modes for m in c.models})'
```

  预期：三个默认齐全，五个目标模型 modes 与 §1.3 一致，其余为 `[]`。
- [x] `cd backend && PYTHONPATH=. uv run pytest tests/test_model_modes_config.py tests/test_360_new_models_roster.py -q` 全绿；`modes` 不上 wire 有独立实例化断言。
- [x] API 不写无鉴权裸 curl；由 pytest 直接调用 `list_models(config=...)` 验证响应，避免 middleware 401 与假验收。

**行为级（mock，可重复）：**

- [x] 首次进入默认「标准 + claude」；切「闪速」只剩 qwen/deepseek 且默认 qwen；切「Ultra」只剩 qwen4-max/Kimi/Claude 且默认 qwen4-max。
- [x] flash/standard/ultra 提交参数全矩阵符合 §1.2，包含 `is_plan_mode` 与 `subagent_enabled`；Claude 不发 thinking。
- [x] 旧 `pro/thinking` 设置迁移为标准，旧 low/medium 不残留；合法档内选择保留，非法选择自动回退。
- [x] 「后端强制关闭警告」不靠 mock 断言；代码上 Claude 在前端已发 `thinking_enabled=false`，该警告只保留给绕过前端的直接 API 调用。

**真实网关证据（已定稿前完成，不冒充部署验收）：**

- [x] qwen/deepseek@360-latency 的 `disabled + minimal` 各 3 次 reasoning=0、工具正常；`enabled + high` 对照正常。部署后的真实 thread 验收需另行授权，不在本计划自动执行。

## 六、路径边界

| 类别 | 路径 |
|---|---|
| **允许路径（allow）** | `config.yaml`、`config.example.yaml`、`backend/packages/harness/deerflow/config/{model_config,app_config}.py`、`backend/packages/harness/deerflow/models/factory.py`（exclude `modes` + 显式禁用态 `reasoning_effort` 去重）、`backend/app/gateway/routers/models.py`、`backend/tests/{test_model_modes_config.py,test_360_new_models_roster.py,test_model_roster_live.py}`、`frontend/src/core/models/**`、`frontend/src/core/settings/local.ts`、`frontend/src/core/threads/hooks.ts`、`frontend/src/components/workspace/{input-mode.ts,input-box.tsx,mode-hover-guide.tsx,welcome.tsx}`、`frontend/src/app/workspace/chats/[thread_id]/page.tsx`、`frontend/src/core/i18n/locales/**`、`frontend/tests/unit/**`、`docs/changelog/**`、`docs/plans/**` |
| **禁改路径（ban）** | `backend/app/**/provider*`、`backend/app/**/auth*`、`backend/app/**/middleware*`、`backend/app/gateway/video_gen/**`、`backend/seed/agents/**`、`backend/packages/harness/deerflow/agents/**`、`docker/`、`skills/`、`video-templates/`、`frontend/src/components/ui/**`、`frontend/src/components/ai-elements/**`、`extensions_config.json`、`.github/workflows/**`、其他在途计划的实现文件 |
| **共享基座触达** | 需规划侧独占（单任务串行）；config schema 仅 `modes` / `mode_defaults`，factory 仅 exclude `modes` 与显式禁用态 effort 去重；`input-box.tsx` 与 `core/threads/hooks.ts` 在 reliability 前后不得并行改 |

## 七、风险与回滚

| 风险 | 处理 |
|---|---|
| 部署端 config 只读挂载 | `config_version 31→32` 触发升级提示；部署侧另行合并，本批不自动部署 |
| `@360-latency` 上游下线或路由失败 | 它不在目录，**部署前必须做直连 probe**；失败时只把 `model` 回退为标准 `deepseek/deepseek-v4.1-flash` 并重新验收，内部 name/modes 不变。不做未验证的自动静默降级 |
| qwen4-max / Kimi 首字慢 | 仅 Ultra 可选并标注；标准默认 Claude（响应快、无思考）规避 |
| Claude 无思考被误期待 | 描述明确「不输出思考过程」；plan/subagent 参数有测试，不把 thinking 当质量唯一来源 |
| 配置非法或默认模型被删 | schema fail fast；前端三级回退；不允许模型从所有档静默消失 |
| 旧后端无 `modes` / `mode_defaults` | 全模型缺字段时回退全量列表与顺序默认，不阻断旧部署 |
| DSML 守卫缺失（用户 2026-11-20 决定不执行） | DeepSeek 仅保留为可选、不作任何档位默认（标准默认 Claude）；用户手动选择 DeepSeek 时仍存在正文伪工具调用与子代理假成功缺陷，部署/验收不宣称已修复 |
| 回滚 | **前端、后端与 config 同批回滚**，不支持只回退前端。新前端写入的 `standard` 在旧前端无法显示模式标签；回滚说明须让用户重选模式或清理 `deerflow.local-settings`，后端请求本身仍按旧布尔参数兼容 |
| 工作区污染/配置泄漏 | worktree 只带本批文件；config 用符号链接，env 仅 shell 加载；不复制/打印/提交密钥 |

## 八、非目标

- 不删除 GLM / Terra / Claude Opus / Haiku / qwen3.8-max 等在册模型，只设置 `modes: []`。
- 不改 Agent 固定模型、课程/PPT/播客管线、provider/middleware/auth、Docker、技能、视频模板。
- 不重新设计输入框视觉，不做自动模型路由或同档失败降级。
- 不顺手更新 `backend/scripts/model_connectivity_check.py` 的全量旧名册；findings backlog 2026-11-11 条目**本批显式推迟**，由独立探针批次承接。
- 不处理 `agent-showcase-integration` 状态头过时、其他在途计划实现或部署。
