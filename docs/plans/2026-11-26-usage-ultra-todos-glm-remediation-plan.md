# 用量删除快照、GLM 5.4 接入、To-dos 运行中提醒与 Ultra 场景化委派实施计划

> **状态**：未执行（执行中：用量快照；定稿 2026-11-26；基准 commit `7e73faf5`；评审决策 Q1–Q14 经两轮 grill 由用户确认；同日按外部评审修订）。
> **TL;DR**：诊断报告的四项拆成四个串行 PR：① 删除作品/会话前写用量快照（新表 `usage_tombstones`），「我的用量」继续计入并标「作品已删除」；② 接入 360 `z-ai/glm-5.4`（标准 + Ultra 可选、非默认，¥8 / ¥28 / ¥2 每百万 token）；③ 先只读取证，证据不否定「模型批量补写」就在 `TodoMiddleware` 加运行中提醒；④ Ultra 提示词改为「≥2 条相互独立的工作即并行委派」，run 元数据落执行模式，文案同步并删除「更长执行预算」口径。影响面 = backend 用量/删除路径/中间件/提示词/run 元数据 + 1 个 alembic 迁移 + 前端用量页与文案 + `config.example.yaml`；不部署，付费验证按 Q10 封顶。
> **For agentic workers：** REQUIRED SUB-SKILL：使用 `superpowers:subagent-driven-development`（推荐）或 `superpowers:executing-plans` 按 Task 0→5 串行执行并逐项勾选，先 RED 后 GREEN。执行指令下达后端到端完成，任务之间不停下等评审（评审在各 PR 内完成），只在五处停下：Task 0 漂移需要改计划、本机基线全量测试不绿、某 PR 的 OCR 3 轮后仍有 Critical/Important、To-dos 真实 run 在提醒后把没做完的项标成 completed、Ultra 默认模型改一轮提示词后仍不达标。状态只改顶部原行，依次写 `未执行（执行中：用量快照）` → `未执行（执行中：GLM 5.4）` → `未执行（执行中：To-dos）` → `未执行（执行中：Ultra）`，Task 5 完成后改为 `已执行（…）`，不得另起状态头。勾选框之外的小节是说明，不构成待办。

**Goal：** 删除作品不再抹掉已发生的费用；`z-ai/glm-5.4` 可在标准与 Ultra 档选用且正确计价；多步骤任务的 To-dos 在执行中及时推进；Ultra 在出现相互独立的工作时确实调用子代理，文案与行为一致。

**Architecture：** 用量统计改为「现存任务/会话 ∪ 删除快照」两类对象，账本仍按 subject id 归组，快照只补类型、标题与状态；删除路径先写快照，写失败则拒绝删除。GLM 5.4 只是花名册新条目（不改 schema），沿用 360 OpenAI 兼容工厂。To-dos 与 Ultra 都只改 Agent 行为层：`TodoMiddleware` 在停滞时注入一条不上屏的提醒，lead 提示词的子代理段改为场景化规则，执行模式以服务端写入的 `run_mode` 落在 run 元数据。

**Tech Stack：** Python 3.12、FastAPI、SQLAlchemy + Alembic、LangGraph/LangChain agents、pytest；Next.js + React + TypeScript、Vitest；不新增依赖。

**Spec / 需求来源：** `docs/2026-11-26-usage-ultra-todos-glm-diagnosis.md`（诊断交接）+ 本文「一、评审决策记录」（2026-11-26 两轮 grill，用户确认，并提供 GLM 5.4 单价）。

## 全局约束

- **不部署、不改测试服数据。** 测试服只做只读 SELECT（Q6）；部署按「六、发布清单」，须用户另行下达指令。
- **付费调用上限（Q10，已批准）：** 全部在本机直连或本机隔离栈执行，超限即停该项，在 changelog「未验证清单」标注；mock/桩通过不冒充 live。
  - GLM 5.4：探针 ≤10 次直连调用 + 1 次真实对话。
  - Ultra：评测 ≤16 个 trial（默认模型 4 个，其余 Ultra 模型每款 4 个，按评测时的花名册计；每个 ≤6 次模型调用，子代理用桩）+ 2 次真实端到端 Ultra 对话。
  - To-dos：≤3 次真实对话。
- **密钥：** 只在启动命令内加载本机模型密钥文件（位置见执行者本地记忆，不写入仓库），不打印、不复制、不入库；本机栈设 `LANGFUSE_TRACING=false`。
- **证据：** 固定放主 clone 的 gitignored 目录 `/Users/littlebear/Documents/SuperTrainerAgent/temp/2026-11-26-usage-ultra-todos-glm/`（以下简称「证据目录」），只存聚合数、探针输出与截图；禁止提交或记录账号、密钥、主机、thread/run/任务 ID、对话正文。
- **报告底线（§7.4）：** 不禁止删除作品、不强制委派、不伪造 To-dos 完成态；每个 PR 至少一条行为级断言。
- **配置：** `config.yaml` 是本机 gitignored 文件，不入库；新增模型条目不递增 `config_version`。
- **范围外发现** 登记 `docs/findings-backlog.md`，不顺手改。
- **PR 门禁（每个 PR 同一套）：** 本机未装 git hooks，提交前手动 `pre-commit run --files <本次改动>`；推送前跑根 `make check`、`cd backend && make test`（>120s，后台运行，等完成通知）、涉及前端时 `cd frontend && pnpm check && pnpm test`；`git push -u origin <分支>`（HTTPS 超时改用 SSH 远端）；`GH_REPO=littlebear2020/SuperTrainerAgent gh pr create`，正文链接本计划与 changelog；等 AI 评审与 CI 的后台完成通知（不轮询），读 `gh pr view --comments`；无 Critical/Important 且 CI 绿 → `gh pr merge --rebase`；有发现则修复后重推，每个 PR 的 OCR 最多 3 轮，每轮处置写成 PR 评论。合入后 `git fetch origin` 并从最新 `origin/main` 开下一条分支。
- **本机隔离栈：** 在执行 worktree 内起 gateway：`cd backend && PYTHONPATH=. uv run uvicorn app.gateway.app:app --host 127.0.0.1 --port 8001`，同一条命令内加载密钥文件，并设 `DEER_FLOW_CONFIG_PATH=<主 clone>/config.yaml`、`LANGFUSE_TRACING=false`、`WORKER_HEALTH_URL`/`REMOTION_WORKER_HEALTH_URL` 指向 localhost；数据目录用 worktree 自己的全新 `backend/.deer-flow/`，不碰本机开发库；`/api/v1/auth/initialize` 建一次性管理员（密码由脚本生成，存 `/tmp` 下 600 权限文件，不进对话）。需要界面时再起 `pnpm run dev`（:3000）与 `docker/nginx/nginx.local.conf`（:2026）。结束时对 8001、3000、2026 各执行一次 `lsof -ti tcp:<端口> | xargs kill`。

## 一、评审决策记录（2026-11-26 grill，用户确认）

| # | 决策点 | 定稿 | 依据 / 被否方案 |
| --- | --- | --- | --- |
| Q1 | 删除后用量明细怎么展示 | 保留条目，标「作品已删除」，隐藏「打开原任务」；无标题时显示「已删除的作品」 | 否：只计入总额、不列条目——总额不等于列表合计，`total_tasks` 与分页对不上 |
| Q2 | 用什么保住已删除项的类型 | 删除前写 `usage_tombstones` 快照（新表 + 1 个迁移）；统计对象 = 现存 ∪ 快照；快照写失败即拒绝删除（此时尚未删任何东西） | 账本本身分不出类型：视频任务的 LLM 账本行只有 `domain=video_gen`；UI run 的 `assistant_id` 恒为 `lead_agent`，Agent 名只在请求 context 里，从未落库。否：软删除（作品列表、worker 认领、恢复巡检、线程搜索、`require_existing` 鉴权都要过滤）；写入时打标签（要改 4–5 个账本写入口，含受保护的 `video_gen` 路径，聚合也要重写，历史行同样没有标签） |
| Q3 | 修复上线前已删的作品 | 不回填，只对上线后的删除生效，changelog 写明 | 没有可靠的类型来源；否：以「类型未知」计入「全部」——会混入本不统计的 ViMax、Remotion、普通对话费用 |
| Q4 | Ultra 的子代理语义 | 场景化委派：≥2 条相互独立的工作就并行委派；单线、前后依赖、确认门流程、Agent 禁止委派的工作仍直接执行；文案同步；付费评测验收；run 元数据落 `run_mode` | 现提示词的 "Default to direct execution"（2026-08-13 C-04/T21）与「多子代理协作」文案冲突；否：只改文案（Ultra 存在感仍弱）、强制委派（报告禁止） |
| Q5 | Ultra「更长执行预算」 | 删除过时口径（改模式阶梯计划，不在文案承诺） | 服务端 `max_recursion_limit: 2500` 是三档共同上限，从未实现；否：补实现（改 config 结构、递增 `config_version`、联动部署端） |
| Q6 | 取证方式 | 测试服只读 SELECT `run_events`/`runs`/`threads_meta`，只留脱敏聚合 | 否：本机付费复现（花钱，也不等于用户那次） |
| Q7 | To-dos 修复口径 | 取证确认模型批量补写 → `TodoMiddleware` 运行中提醒（K 默认 6，按证据校准；每个停滞窗口一次；绝不改状态）；证据不支持 → 拆 pending 查 SSE/前端；服务器不可达或样本不足 5 个 run → K = 6 照常实施（评审补充：提醒不改状态，一次本机短对话当不了 20% 的样本）；多个 `in_progress` 只用于真正并行的工作，提示词与 UI 不改 | 现有唯一的强制手段是收尾提醒（要求「调用 `write_todos` 标完成」），正好诱发收尾批量补写；否：自动推进状态（伪造完成）、只加提示词 |
| Q8 | GLM 5.4 放哪些档 | 标准 + Ultra 可选，均非默认，不进闪速；能力位按探针实测；工具调用不过则整项取消 | 2026-11-18 排除 GLM 的原因出在关思考的闪速档，标准与 Ultra 都是「开思考 + high」；360 目录（免费的 `GET /v1/models`）已确认有 `z-ai/glm-5.4` |
| Q9 | GLM 5.4 单价 | 用户提供：输入 ¥8、输出 ¥28、缓存读取 ¥2（每百万 token） | — |
| Q10 | 付费调用授权 | 批准「全局约束」所列上限，执行时照此，超限即停并标未验证 | 否：每次单独询问（打断端到端执行）、本批不做付费验证 |
| Q11 | 计划形态与顺序 | 一份计划、单执行者、四个 PR 串行：用量快照 → GLM 5.4 → To-dos → Ultra；BUG-03 证据不支持则拆 pending；GLM 工具调用不过则取消 PR 2；不含部署 | 涉及金额的先做；GLM 先进 Ultra，后面的评测才能覆盖它；Ultra 评测跑在最终 Agent 行为上 |
| Q12 | Agent 对话里的 Ultra | 照常提供，文案用通用说法，Agent 工作流规定优先，不加「本次未调用子代理」提示 | ppt-master SOUL 明令禁止子代理生成 SVG、course-video 是确认门顺序流程，它们在 Ultra 下基本不委派属设计如此；否：按 Agent 隐藏（需新增每 Agent 元数据）、结束时提示 |
| Q13 | Ultra 评测达标线 | 默认模型 qwen4-max 阻断：多分支场景 3 次中 ≥2 次在同一条回复里并行派出 ≥2 个 `task`；简单场景 0 次 `task`。其他 Ultra 模型只记录，3 次全为 0 登记 backlog、不移出 Ultra。默认模型不达标 → 在上限内改一轮提示词重测，仍不达标停下交用户 | — |
| Q14 | GLM 5.4 价格写在哪 | 两处：config 兜底 `pricing`（首次调用即有价）+ 部署时录入数据库（运行时以库为准）；管理员价目接口是整表替换，必须先取全表再整表提交 | 否：只录库（录入前算不出金额，Claude 三款先例）、只写 config |

**明确不做：** 软删除与账本写入时打标签（Q2）；回填修复前已删除的作品（Q3）；强制委派、按 Agent 隐藏 Ultra、「本次未调用子代理」提示（Q4/Q12）；实现 Ultra 更长执行预算（Q5）；自动推进 To-dos、改 To-dos 提示词或界面（Q7）；GLM 5.4 进闪速或设为任何档默认（Q8）；递增 `config_version`；修 `useDeleteThread` 双 DELETE（只登记 backlog，见 Task 0）；部署。

**backlog 复核（定稿前）：** 与本批相关的只有四条 2026-11-26 条目（由 Task 0 Step 2 标注并入，Task 5 勾选）；其余条目（前端可靠性、ViMax 计量等）与本批路径无交集，不并入。

## 二、串行 PR 路径表

| PR | 允许路径（allow） | 禁改路径（ban） | 共享基座触达 |
| --- | --- | --- | --- |
| **PR 1 / 用量快照（BUG-01）** | `backend/app/gateway/_shared/personal_usage/{tombstones.py（新增）,agent.py,video.py,types.py}`、`backend/app/gateway/routers/personal_usage.py`、`backend/app/gateway/_shared/task_framework/service.py`、`backend/app/gateway/routers/threads.py`、`backend/app/gateway/deps.py`、`backend/packages/harness/deerflow/persistence/migrations/{env.py,versions/q0d1e2f3a4b5_add_usage_tombstones_table.py（新增）}`、`backend/tests/{test_personal_usage_deletion.py,test_usage_tombstones_migration.py}`（新增）、`backend/tests/test_360_new_models_roster.py`（仅第 331 行本机版本断言，见 Task 0 Step 3）、`frontend/src/core/usage/types.ts`、`frontend/src/components/workspace/usage/usage-task-list.tsx`、`frontend/src/core/i18n/locales/{types.ts,zh-CN.ts,en-US.ts}`、`frontend/tests/unit/components/workspace/usage/usage-task-list.dom.test.tsx`、本计划/changelog/backlog | `backend/app/gateway/video_gen/**`、`backend/app/gateway/routers/video_gen.py`、`task_framework/repository.py` 与 worker 认领/恢复、`unified_tasks`/`threads_meta` 表结构、账本写入口（`llm_usage_sink.py`、`agent_media_usage.py`、`throttle.py`）、`frontend/src/core/threads/hooks.ts`、`config*.yaml`、`skills/`、harness agents | **需规划侧独占**：迁移链头（`p9c0d1e2f3a4` → `q0d1e2f3a4b5`）与 `BaseTaskService.delete_task`（所有任务域共用，仅 `ai_image`/`ai_video` 写快照） |
| **PR 2 / GLM 5.4（FEAT-01）** | `config.example.yaml`、本机 `config.yaml`（不入库）、`backend/tests/{test_model_modes_config.py,test_model_roster_live.py}`、本计划/changelog/backlog | `backend/packages/harness/deerflow/{models,config}/**`、`backend/app/gateway/routers/models.py`、`frontend/**`、`config_version` | 否（只加花名册条目，不改 schema） |
| **PR 3 / To-dos（BUG-03）** | `backend/packages/harness/deerflow/agents/middlewares/todo_middleware.py`、`backend/tests/{test_todo_middleware.py,test_todo_progress_graph.py（新增）}`、`frontend/tests/unit/components/workspace/todo-list.dom.test.tsx`（新增）、本计划/changelog/backlog；证据不支持时仅 `docs/plans/pending/2026-11-26-todos-realtime-transport-plan.md` | `lead_agent/agent.py`（To-dos 提示词不改）、`runtime/runs/worker.py`、其他中间件与注册顺序、`frontend/src/**` | **需规划侧独占**：中间件链（只改 `TodoMiddleware` 内部，不改注册顺序） |
| **PR 4 / Ultra（BUG-02）** | `backend/packages/harness/deerflow/agents/lead_agent/prompt.py`（仅 `_build_subagent_section` 与 `apply_prompt_template` 的两句子代理提醒）、`backend/app/gateway/services.py`（仅新增 `_run_mode_metadata`，以及 `start_run` 内把 `build_run_config` 与 context 合并移到 `create_or_reject` 之前并传入 metadata）、`backend/tests/{test_lead_agent_prompt.py,test_gateway_services.py,test_ultra_delegation_live.py（新增）}`、`frontend/src/core/i18n/locales/{zh-CN.ts,en-US.ts}`（仅 `ultraModeDescription`）、`docs/plans/2026-11-18-model-mode-ladder-plan.md`（勘误）、本计划/changelog/backlog | `frontend/src/core/threads/hooks.ts`（提交参数不变）、`subagent_limit_middleware.py`、子代理注册表、`backend/seed/agents/**`（SOUL 不改）、`config*.yaml`、`max_recursion_limit` | **需规划侧独占**：lead 静态提示词（prompt-cache 前缀按模式区分，只动子代理段与两句子代理提醒） |

路径重叠：PR 1 与 PR 4 都改 `zh-CN.ts`/`en-US.ts`（键不同），四个 PR 共用本计划、changelog 与 backlog——单执行者串行、前一个合入后再开下一个，禁止据此并行派发。

## 三、实施任务

### Task 0：基线、文档入库、执行环境与只读取证

- [x] **Step 1：漂移检查。** `git fetch origin main`，再 `git diff --stat 7e73faf5..origin/main -- backend/app/gateway/_shared/personal_usage backend/app/gateway/_shared/task_framework/service.py backend/app/gateway/routers/threads.py backend/app/gateway/routers/personal_usage.py backend/app/gateway/deps.py backend/app/gateway/services.py backend/packages/harness/deerflow/persistence/migrations backend/packages/harness/deerflow/agents/middlewares/todo_middleware.py backend/packages/harness/deerflow/agents/lead_agent/prompt.py frontend/src/components/workspace/usage frontend/src/core/usage frontend/src/core/i18n/locales config.example.yaml backend/tests/test_model_modes_config.py backend/tests/test_model_roster_live.py backend/tests/test_lead_agent_prompt.py backend/tests/test_todo_middleware.py backend/tests/test_gateway_services.py`。输出为空 → 继续；非空 → 逐文件核对本文引用的函数与行为，需要时先改计划。另确认 alembic 头仍是 `p9c0d1e2f3a4`（`versions/` 中没有别的文件以它为 `down_revision`）；已有新头时，Task 1 的 `down_revision` 改指新头。
- [x] **Step 2：文档入库（纯 docs，直推 main）。**
  - `docs/findings-backlog.md` 四条 2026-11-26 条目末尾各追加 `；并入 docs/plans/2026-11-26-usage-ultra-todos-glm-remediation-plan.md`。
  - 新增一条：`- [ ] 2026-11-26 静态走读：useDeleteThread 对同一线程连发两次 DELETE（SDK threads.delete 经 nginx 改写与直连 fetch 落到同一路由），第一次已删 threads_meta，第二次会被 require_existing 判 404，删除可能误报失败；用量快照批不夹带，待运行时确认；指针：frontend/src/core/threads/hooks.ts（useDeleteThread）、backend/app/gateway/authz.py（require_existing）。`
  - `python3 scripts/check_docs_convention.py` 通过；`pre-commit run --files docs/2026-11-26-usage-ultra-todos-glm-diagnosis.md docs/findings-backlog.md docs/plans/2026-11-26-usage-ultra-todos-glm-remediation-plan.md` 通过。
  - 只 `git add` 这三个文件（工作区其他未跟踪文件不属于本批，不得带入），提交 `docs(plans): 用量快照与 Ultra/To-dos/GLM 5.4 实施计划入库`，推 main；记下该提交的短 hash 作为执行基线，写入 changelog。
- [x] **Step 3：执行 worktree 与本机环境。**
  - `git worktree add ../SuperTrainerAgent-exec-usage-ultra -b usage-deletion-snapshot origin/main`（主 clone 恒停 main）；worktree 内 `cd backend && uv sync --frozen --offline`、`cd frontend && pnpm install --frozen-lockfile --offline`，不复用主 clone 的 venv。
  - worktree 根目录建符号链接 `config.yaml` → 主 clone 的 `config.yaml`。本机 config 仍是 v32（v33 = `88c1485a`：示例只改了版本号与 TTS 注释，本机没有 `voice_twin` 段，无键可迁）：先备份到证据目录 `config.yaml.v32.bak`，再只把 `config_version: 32` 改为 `33`（不用 `make config-upgrade`，它会整文件 YAML 重写）。
  - 在 PR 1 分支先提交 `test(models): 本机 config 版本断言对齐 v33`：`backend/tests/test_360_new_models_roster.py:331` 的 `== 32` 改为 `== 33`，只改这一处。该断言要求「本机 == 示例 == 32」，而示例已是 33，本机有 config 时改不改版本号都红（CI 没有 config，跳过）；不改，PR 1 的全量门禁从第一天就是红的。验证：`cd backend && PYTHONPATH=. uv run pytest tests/test_model_modes_config.py tests/test_360_new_models_roster.py tests/test_config_example_parity.py -q` → 全部 passed。
  - 基线：后台跑一次 `cd backend && make test`，等完成通知。全绿才进入 Task 1；有失败先排查本机环境（venv、config 链接），仍红则停下交用户，不在本批修。
  - 按执行日新建 `docs/changelog/changelog-<执行日>-usage-ultra-todos-glm-remediation.md`（状态头之后 ≤5 行 TL;DR、每个 PR 一节、「验收记录」、「未验证清单」），随 PR 1 提交；状态行改为 `未执行（执行中：用量快照）`。
- [x] **Step 4：测试服只读取证（Q6）。**
  - 按仓库外的测试服 ops 记录登录（本文不写主机、路径与账号）；统计脚本写在证据目录 `server_evidence.py`，拷到服务器临时目录执行后删除；SQLite 一律以只读 URI（`file:<db>?mode=ro`）打开，只 SELECT，不重启服务、不改数据。
  - **To-dos 统计（近 14 天）：**
    - 数据：`run_events` 中 `event_type='llm.ai.response'` 且 `event_metadata` 的 `caller` 为 `lead_agent` 的行，按 `run_id`、`seq` 排序。工具调用只在 `content`（`AIMessage.model_dump()` 的 JSON；该事件属 message 类，不截断）的 `tool_calls[].name` / `tool_calls[].args` 里，`event_metadata` 只有 `caller`、`usage`、`latency_ms`、`llm_call_index`。
    - 「一轮」= 含 ≥1 个工具调用的一条回复；含 `write_todos` 的回复是更新点，不计入停滞轮数。样本 = 含 `write_todos` 且总轮数 ≥6 的 run，个数记 `runs_sampled`。
    - 每个样本 run：`todo_updates` = 更新点个数；`max_stale` = 相邻更新点之间、以及最后一个更新点之后到 run 结束的最长连续轮数；`tail_batch` = 最后一次更新里新变成 `completed` 的项 ≥2（与上一次 `write_todos` 的 `args.todos` 按 `content` 比对：这次是 completed、上次不是；第一次更新的「上一次」视为空列表）。
    - 分类：`max_stale ≥ 6` 且（`todo_updates ≤ 2` 或 `tail_batch`）→ 批量补写型；`max_stale < 6` → 及时型；其余记 `other`，不算及时型。`batch_share = 批量补写型 / runs_sampled`。
  - **Ultra 基线：** `runs.model_name` 含 `qwen4-max` 或 `kimi-k3.1-pro`（只在 Ultra 可选）的 run 数，其中出现 ≥1 次 `task` 调用的 run 数，按 `threads_meta.metadata_json.agent_name` 分组（空 = 普通对话）。
  - 只把聚合数写入证据目录 `server-evidence.json`：`runs_sampled`、`batch_like`、`timely`、`other`、`batch_share`、及时型 run 的相邻更新间隔 `gap_p50`/`gap_p75`、`k_selected`、Ultra 各计数。
  - **决定 Task 3 走向：**
    - `runs_sampled ≥ 5` 且 `batch_share ≥ 20%` → Task 3 实施，`K = clamp(gap_p75, 4, 10)`（及时型不足 5 个 run 时 K = 6）；
    - `runs_sampled ≥ 5` 且 `batch_share < 20%` → 证据不支持「模型批量补写」，Task 3 走 Step 8 拆 pending；
    - 服务器不可达或 `runs_sampled < 5` → changelog 记 `SERVER_EVIDENCE_NOT_VERIFIED`，K = 6，Task 3 照常实施（提醒不改状态；一次本机短对话当不了 20% 的样本，不补本机基线）。

### Task 1 / PR 1：删除快照保住历史用量（BUG-01）

分支 `usage-deletion-snapshot`。

**Interfaces（Produces）：**

- `backend/app/gateway/_shared/personal_usage/tombstones.py`（新增）：
  - `class UsageTombstoneRow(Base)`，表 `usage_tombstones`：主键 `(kind String(16), subject_id String(64))`，`kind ∈ {"video", "agent"}`；`user_id String(64)` 非空、带索引；`capability String(32)` 非空；`title String(256)` 可空；`status String(20)` 非空（删除时的 `UsageStatus`）；`subject_created_at DateTime(timezone=True)` 非空；`deleted_at DateTime(timezone=True)` 非空、默认当前 UTC。
  - `USAGE_TASK_TYPES = frozenset({"ai_image", "ai_video"})`。
  - `async def record_task_tombstone(task: TaskRow) -> bool`：`task.task_type` 不在 `USAGE_TASK_TYPES` 或 `get_session_factory()` 为 None → 返回 False；否则 `session.merge` 一行（`kind="video"`、`capability=task.task_type`、`title=task.title or None`、`status` 按下方规则、`subject_created_at=task.created_at`）并提交 → True。数据库异常原样抛出。
  - `async def record_thread_tombstone(thread_id: str) -> bool`：读该线程的 `threads_meta` 行；行不存在或 `thread.user_id` 为空 → False（不触碰已有快照；无主线程本来不进任何人的用量，而快照表 `user_id` 非空，硬写会让删除失败）。再取该线程的 run，过滤与 `aggregate_agent_usage` 相同：`operation_kind="run"`，且 `user_id` 等于 `thread.user_id` 或为空；按 `created_at desc, run_id desc` 排序，第一条为最新 run。`resolve_agent_capability(thread, runs)` 为 None → False；否则 merge 一行（`kind="agent"`、`user_id=thread.user_id`、`title` 取法同聚合：不等于 thread_id 的 `display_name`，否则最新 run 的 `first_human_message`，截断到 256 字符；`status` 取最新 run 的状态，没有 run 时用 `thread.status`，再按下方规则；`subject_created_at=thread.created_at`）→ True。数据库异常原样抛出。
  - 两个写入函数的 `status` = `normalize_usage_status(原状态)`，结果为 `running` 时改记 `incomplete`：快照写入后不再更新（任务删除会取消执行），否则已删除条目会永久显示「进行中」与「截至当前」。
  - `async def load_tombstones(session, *, user_id: str, kind: Literal["video", "agent"], capabilities: Collection[str] | None = None) -> list[UsageTombstoneRow]`（`None` = 不按能力过滤）。
- `types.py`：新增 `def resolve_agent_capability(thread: ThreadMetaRow, runs: Iterable[RunRow]) -> UsageCapability | None`，从 `aggregate_agent_usage` 的内联逻辑原样抽出（候选顺序：`thread.assistant_id`、`metadata.agent_name`、`metadata.assistant_id`、各 run 的 `assistant_id`；结果为 `ai_image`/`ai_video` 时返回 None），与 `canonical_agent_capability` 放在一起，聚合与快照共用；`agent.py` 改为调用它。放在 `types.py` 而不放 `agent.py`：`agent.py` 依赖控制台路由，`task_framework/service.py` 顶层 import 快照模块时会间接拉入它，有循环 import 风险。
- `types.py`：`UsageTask` 末尾新增必填字段 `deleted: bool`（不给默认值，否则子类 `UsageTaskEntry` 的 `activity_at` 会因「无默认值字段排在有默认值字段之后」报错）；`agent.py`、`video.py` 的构造器补参。
- `routers/personal_usage.py`：`UsageTaskResponse.deleted: bool = Field(description="作品或会话已删除：历史费用与 Token 仍计入，原任务不可再打开。")`，`_task_response` 透传。
- 前端：`UsageTask.deleted?: boolean`（旧后端不带此字段时按 false 处理）；i18n `personalUsage.deletedBadge`（「作品已删除」/ "Deleted"）、`personalUsage.deletedTitle`（「已删除的作品」/ "Deleted work"）。

**聚合规则（`aggregate_video_usage` 与 `aggregate_agent_usage` 同口径）：**

- 统计对象 = 现存行 ∪「没有现存行的快照」；同一 id 现存行优先，不重复计。
- 快照对象只有在窗口内有 ≥1 条该用户的 LLM 或媒体账本行时才列出；现存对象的列出规则不变。
- 快照条目取快照的能力、标题、状态，`deleted=True`、`updated_at=deleted_at`；`activity_at` 公式不变，用 `subject_created_at` 代替 `created_at`。能力筛选按快照能力。
- 账本仍按 subject id 归组：删除后才落库的晚到行照常计入。`summary` 与 `total_tasks` 仍在分页前对全量计算。
- `aggregate_agent_usage` 里「没有现存线程就直接返回」改为「现存线程与快照都为空才返回」。

- [x] **Step 1：写快照与迁移的失败测试。** 新建 `backend/tests/test_personal_usage_deletion.py`：`db` fixture 同 `test_personal_usage_video.py::db`；`_VideoTaskService(BaseTaskService)` 仿 `test_task_framework.py::_FakeService`（`domain="video_gen"`、`id_prefix="vg"`、submit 关 auto_start），repo = `TaskRepository(get_session_factory())`。
  - `test_thread_outside_usage_records_no_tombstone`（参数化两例）：普通对话（`assistant_id="lead_agent"`、metadata 无 `agent_name`）；`user_id` 为空的 course-video 线程。两例 `record_thread_tombstone` 都返回 False，表内 0 行。
  - `test_repeated_thread_delete_keeps_first_tombstone`：course-video 线程首次调用 → True；删掉 `threads_meta` 行后再调 → False，快照的 capability 与 title 不变（钉住 `useDeleteThread` 双 DELETE）。
  - `test_tombstone_failure_aborts_task_deletion`：monkeypatch `app.gateway._shared.task_framework.service.record_task_tombstone` 抛 `RuntimeError` → `delete_task` 抛出；`unified_tasks` 行与任务目录都还在。
  - `test_tombstone_failure_aborts_thread_deletion`：monkeypatch `app.gateway.routers.threads.record_thread_tombstone` 抛错 → `delete_thread_data.__wrapped__` 抛出；`threads_meta` 行还在，假 event store 的 `delete_by_thread` 未被调用（fakes 装配仿 `test_thread_delete_event_store.py`）。
  - 新建 `backend/tests/test_usage_tombstones_migration.py`：仿 `test_persistence_bootstrap.py::test_baseline_table_names_constant_matches_baseline` 的临时库装配。`test_usage_tombstones_revision_upgrades_previous_head`：先 `_upgrade(cfg, "p9c0d1e2f3a4")` 再 `_upgrade(cfg, "head")`，断言有表 `usage_tombstones` 与索引 `ix_usage_tombstones_user_id`，且 `_get_head_revision() == "q0d1e2f3a4b5"`。`test_usage_tombstones_revision_downgrades_cleanly`：升到头后 `alembic.command.downgrade(cfg, "p9c0d1e2f3a4")`，表消失（回滚路径，见「六」）。
- [x] **Step 2：确认失败。** `cd backend && PYTHONPATH=. uv run pytest tests/test_personal_usage_deletion.py tests/test_usage_tombstones_migration.py -q` → 因 `tombstones` 模块与 revision 不存在而失败。
- [x] **Step 3：实现快照写入。**
  - 新增 `tombstones.py`；在 `types.py` 新增 `resolve_agent_capability`，`agent.py` 改为调用它（行为不变）。`task_framework/service.py` 与 `routers/threads.py` 在模块顶层 import 两个写入函数（测试按模块属性 monkeypatch）。
  - 新增 revision `backend/packages/harness/deerflow/persistence/migrations/versions/q0d1e2f3a4b5_add_usage_tombstones_table.py`：`down_revision="p9c0d1e2f3a4"`，表已存在即跳过（写法同 `f8b2c4d6e1f3_add_external_api_calls_table.py`），downgrade 删表。
  - `migrations/env.py` 与 `deps.py` 的 app 模型 importlib 清单各加 `app.gateway._shared.personal_usage.tombstones`（后者保证空库走 `create_all` 时建表）。
  - `BaseTaskService.delete_task` 在归属校验通过后、取消与清理之前 `await record_task_tombstone(task)`；`delete_thread_data` 的第一条语句 `await record_thread_tombstone(thread_id)`。两处都不吞异常：快照写失败，本次删除即失败。
- [x] **Step 4：确认通过并回归。** Step 2 的命令全绿；再跑 `PYTHONPATH=. uv run pytest tests/test_task_framework*.py tests/test_thread_delete_event_store.py tests/test_content_tasks_delete.py tests/test_voice_twin_production.py tests/test_persistence_bootstrap.py tests/test_persistence_bootstrap_legacy_columns.py tests/test_harness_boundary.py tests/test_personal_usage_agent.py -q` → 全部 passed。
- [x] **Step 5：提交。** 手动 pre-commit 后 `git commit -m "feat(usage): 删除作品与会话前写入用量快照"`（`tombstones.py`、`types.py`、`agent.py`、revision、`env.py`、`deps.py`、`task_framework/service.py`、`routers/threads.py`、两个新测试文件、changelog）。
- [x] **Step 6：写统计口径的失败测试**（同一新文件）：
  - `test_deleting_ai_image_task_keeps_usage_totals`：已计价模型 + ai_image 任务（标题 `删除前标题`）+ 窗口内 1 条 `llm_bookkeeping` 行 + 1 条可计价媒体行；`before = await get_personal_usage(...)`；`await service.delete_task(task_id, USER)` 返回 True；断言 `before.summary.cost is not None`、`after.summary.cost == pytest.approx(before.summary.cost)`、`after.summary.total_tokens == before.summary.total_tokens`、`after.total_tasks == before.total_tasks == 1`、`after.tasks[0].deleted is True`、`capability == "ai_image"`、`title == "删除前标题"`、`before.tasks[0].status == "running"`（未启动任务为 pending）且 `after.tasks[0].status == "incomplete"`；`unified_tasks` 已无该行，两条账本行仍在。
  - `test_deleted_ai_video_task_stays_in_its_capability_filter`：ai_video 任务（LLM + 媒体各 1 行）删除前后，`capability="all"` 的总费用与 Token 不变；删除后 `capability="ai_video"` 返回 1 条，`"ai_image"` 返回 0 条。
  - `test_deleting_agent_thread_keeps_usage_totals`：course-video 线程（`assistant_id="lead_agent"`、`metadata={"agent_name": "course-video"}`）+ 1 条带 token 的 `run` 行 + 1 条媒体行；经 `delete_thread_data.__wrapped__` 删除（`app.state.thread_store = ThreadMetaRepository(get_session_factory())`，其余 fakes 同 Step 1）；总费用与 Token 不变，条目 `deleted is True`、`capability == "course-video"`。
  - `test_late_ledger_rows_after_deletion_are_counted`：删除后再插一条同 task_id 的可计价媒体行 → 总费用 = 删除前总额 + 该行费用。
  - `test_deleted_task_without_usage_is_not_listed`：窗口内创建、没有任何账本行的 ai_image 任务：删除前列出（现存规则不变），删除后不列出、`total_tasks == 0`。
  - `test_live_row_wins_over_stale_tombstone`：同一 id 既有快照又有现存任务（模拟快照写入后删除失败）→ 只列 1 条，`deleted is False`，费用不翻倍。
  - `test_summary_with_deleted_subjects_reconciles_across_pagination`：3 个对象（1 个已删除）、`limit=1` 逐页取：各页费用之和 == `summary.cost`，`total_tasks == 3`。
- [x] **Step 7：确认失败。** `PYTHONPATH=. uv run pytest tests/test_personal_usage_deletion.py -q` → Step 6 新增用例失败（删除后费用为 None、没有 `deleted` 字段）。
- [x] **Step 8：实现聚合。** 按「聚合规则」改 `video.py`、`agent.py`、`types.py`、`routers/personal_usage.py`。
- [x] **Step 9：确认通过并回归。** `PYTHONPATH=. uv run pytest tests/test_personal_usage_deletion.py tests/test_personal_usage.py tests/test_personal_usage_agent.py tests/test_personal_usage_router.py tests/test_personal_usage_video.py tests/test_core_media_usage_e2e.py -q` → 全部 passed。
- [x] **Step 10：提交。** `git commit -m "fix(usage): 删除作品后用量统计保留历史费用与 Token"`（`video.py`、`agent.py`、`types.py`、`routers/personal_usage.py`、`test_personal_usage_deletion.py`）。
- [ ] **Step 11：写前端失败测试。** `usage-task-list.dom.test.tsx` 新增 `it("marks deleted works and hides the open-original link")`：渲染 `{deleted: true, title: null}` 的任务并展开 → `getByTestId("task-deleted-<id>")` 文本为「作品已删除」，标题为「已删除的作品」，`queryByTestId("open-original-<id>")` 为 null；同列表里未删除的任务仍有 `open-original-<id>`。`cd frontend && pnpm exec vitest run tests/unit/components/workspace/usage/usage-task-list.dom.test.tsx` → 该用例失败。
- [ ] **Step 12：实现前端。** `core/usage/types.ts` 加 `deleted?: boolean`；`usage-task-list.tsx`：`task.deleted` 时标题回落 `c.deletedTitle`（未删除仍回落「标题生成中」），状态胶囊后渲染 `<span data-testid={`task-deleted-${id}`}>{c.deletedBadge}</span>`（样式同 `task-running-note`），展开区不渲染「打开原任务」；i18n 三个文件补两个键。
- [ ] **Step 13：确认通过。** Step 11 的命令通过；`pnpm check` 通过。
- [ ] **Step 14：提交并走 PR。** `git commit -m "feat(usage): 用量明细标记已删除作品"`（`types.ts`、`usage-task-list.tsx`、i18n 三个文件、前端测试）；按「PR 门禁」开 PR（标题 `fix(usage): 删除作品后保留历史用量`），合入后状态行改为 `未执行（执行中：GLM 5.4）`。

### Task 2 / PR 2：接入 360 GLM 5.4（FEAT-01）

分支 `glm-5-4-model`。

**Interfaces（Produces）：** 花名册条目 `glm-5.4`（本机 `config.yaml` 与 `config.example.yaml` 注释模板）；Task 4 的 Ultra 评测按 `modes` 自动纳入它。终态条目（能力位以 Step 3 的探针结论为准）：

```yaml
- name: glm-5.4
  display_name: 360·GLM 5.4
  description: 支持深度思考，首字约 {a}–{b} 秒   # a/b = 流式用例与首字用例两次实测首字耗时的最小/最大值，取整秒
  use: deerflow.models.patched_openai:PatchedChatOpenAI
  model: z-ai/glm-5.4
  modes: [standard, ultra]
  api_key: $QIHU_API_KEY
  base_url: https://api.360.cn/v1
  request_timeout: 180.0
  max_retries: 2
  max_tokens: 32768
  temperature: 0.7
  stream_chunk_timeout: 240
  supports_vision: true
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
  pricing:
    currency: CNY
    input_per_million: 8
    output_per_million: 28
    input_cache_hit_per_million: 2
```

位置：本机 `config.yaml` 放在 `kimi-k3.1-pro` 条目之后（`models[0]`、`models[1]` 不动，花名册测试钉着）；`config.example.yaml` 放在 `# - name: kimi-k3.1-pro` 模板块之后、`# ── 2026-11-11 模型模板` 注释之前，逐行加 `  # ` 前缀。

- [ ] **Step 1：先以不可见状态加本机条目。** 本机 `config.yaml` 按上方加入条目，但 `modes: []`，description 暂写 `支持深度思考`。
- [ ] **Step 2：live 探针（付费，≤10 次调用，分两段）。** `backend/tests/test_model_roster_live.py` 的 `_create_probe_model` 增加可选 `reasoning_effort` 透传。
  - **2a 思考契约（2 次，先单独跑）：** 新增 `test_live_glm_5_4_thinking_contract`：开思考 + `reasoning_effort="high"` 1 次（记录是否报错、有无推理内容、`reasoning` token 数）；关思考 1 次（记录是否报错、推理 token 是否为 0、答案是否非空）。按 Step 3 定下 `supports_thinking` 后再跑 2b。
  - **2b 其余 8 次（按产品路径：glm-5.4 一律以 `thinking_enabled=<条目的 supports_thinking>`、`reasoning_effort="high"` 调用，其他模型不变）：** `TARGET_MODEL_NAMES` 追加 `"glm-5.4"`（文本、流式、工具调用 + 工具结果续答，共 4 次；这几个共享用例原本一律关思考，glm-5.4 若也关思考，关思考参数被拒会被误判成工具调用不通过）；`test_live_glm_5_4_vision_identifies_red_image`（复用 `_red_image_data_url`，1 次）、`test_live_glm_5_4_first_token_latency`（1 次流式，记录首个非空 chunk 的耗时；`test_live_streaming_response` 同样打印首字耗时）、`test_live_glm_5_4_long_context_needle`（1 次：约 5 万字符的填充文本中藏一句口令，要求原样答出口令）、`test_live_glm_5_4_error_shape`（1 次：传 `max_tokens=-1`，断言抛出带状态码的异常而不是空回复，记录异常类型）。
  - 超时形态不另测（与其他 360 模型共用 `request_timeout`/`stream_chunk_timeout`），列入「未验证清单」。在同一条命令内加载密钥后，先跑 `RUN_LIVE=1 PYTHONPATH=. uv run pytest tests/test_model_roster_live.py -k "glm and thinking" -v -s`（2a），定完能力位再跑 `-k "glm and not thinking"`（2b），输出都存证据目录 `glm-probe.txt`。
- [ ] **Step 3：按探针结论定能力位。**
  - 工具调用或工具结果续答失败（2b，按产品路径开思考）→ **取消 PR 2**：删除本机条目、撤回 Step 2 的改动、backlog 登记「GLM 5.4 工具调用不通过」并附探针输出，直接进入 Task 3。
  - 开思考请求报错：错误指向 `reasoning_effort` → `supports_reasoning_effort: false`，思考位不动；其他报错 → `supports_thinking: false`，删除两段 `when_thinking_*`，不加入 `THINKING_MODELS`。
  - 开思考请求成功 → 保留 `supports_thinking: true` 与两段配置：标准、Ultra 都开思考，前端只在非闪速且 `supports_thinking` 为真时开思考（`frontend/src/core/threads/hooks.ts:927`），置 false 会让两档全程不思考。关思考的结果只影响以关思考调用所选模型的内部请求，目前只有建议追问（`backend/app/gateway/routers/suggestions.py:132`，失败时返回空列表）：关思考报错 → backlog 登记「GLM 5.4 关思考被拒，建议追问为空」并写进 changelog 已知限制；关思考后仍有推理 → changelog 记录。删除 `when_thinking_disabled` 绕不开报错：工厂会从 `when_thinking_enabled` 推导出同样的关思考参数（`deerflow/models/factory.py:148-158`）。
  - 视觉失败 → `supports_vision: false`。
  - `test_live_glm_5_4_thinking_contract` 的断言按实测契约写（例如关思考被拒时，该半段改为断言报错并注明原因），不为此复跑付费探针。
  - 然后把 `modes` 改为 `[standard, ultra]`，description 按 2b 两次实测首字耗时填写。
- [ ] **Step 4：写花名册失败测试**（依赖本机 config，CI 没有 config 时按现有口径 skip）。`backend/tests/test_model_modes_config.py`：
  - `EXPECTED_MODES["glm-5.4"] = ["standard", "ultra"]`；`supports_thinking` 保持 true 时 `THINKING_MODELS` 加 `"glm-5.4"`（自动纳入两态 factory 形态测试；该测试只看工厂参数形态，不受关思考实测结果影响）；`test_example_contains_mode_model_templates` 随之要求示例里有 `# - name: glm-5.4`。
  - `test_glm_5_4_roster_contract`：`model == "z-ai/glm-5.4"`；`use` 为 PatchedChatOpenAI；`base_url` 为 360；`max_tokens == 32768`；能力位与 Step 3 结论一致；`"glm-5.4" not in real_config.mode_defaults.values()`；`model_extra["pricing"] == {"currency": "CNY", "input_per_million": 8, "output_per_million": 28, "input_cache_hit_per_million": 2}`。
  - `test_glm_5_4_pricing_resolves_upstream_id`：monkeypatch `app.gateway.routers.console.get_app_config` 返回 `real_config`；`price = _lookup_pricing(_build_yaml_pricing_map(), "z-ai/glm-5.4")` 非 None；`_token_cost(1_000_000, 100_000, price, cache_read_tokens=250_000) == pytest.approx(9.3)`（750k × 8 + 250k × 2 + 100k × 28，单位为每百万 token）。
  - `test_models_api_lists_glm_5_4_in_standard_and_ultra`：仿 `test_models_api_returns_modes_and_defaults_without_credentials` 调 `list_models(config=real_config)`：glm-5.4 的 `modes == ["standard", "ultra"]`，`mode_defaults` 仍等于 `EXPECTED_MODE_DEFAULTS`。
  - `test_example_glm_5_4_template_pins_pricing`（只读 `config.example.yaml`，CI 也跑；上面三条依赖本机 config，CI 会跳过）：取示例中 `# - name: glm-5.4` 到下一个 `# - name:` 之间的文本，断言含 `#     currency: CNY\n`、`#     input_per_million: 8\n`、`#     output_per_million: 28\n`、`#     input_cache_hit_per_million: 2\n`。
  - 运行 `PYTHONPATH=. uv run pytest tests/test_model_modes_config.py -q` → 示例模板相关用例失败。
- [ ] **Step 5：补示例模板并确认通过。** `config.example.yaml` 加注释模板；`PYTHONPATH=. uv run pytest tests/test_model_modes_config.py tests/test_360_new_models_roster.py -q` → 全部 passed。前端按 `modes` 过滤，「标准↔Ultra 切档保留、切到闪速回落默认」与 claude-sonnet-5-5 同形，由现有 `input-box-mode-models.dom.test.tsx` 覆盖，不新增前端测试。
- [ ] **Step 6：真实对话一次（付费，1 次）。** 本机隔离栈（只起 gateway），API 级：`POST /api/threads/{id}/runs/stream`，context = 标准档（`mode: "standard"`、`model_name: "glm-5.4"`、`is_plan_mode: true`、`subagent_enabled: false`、`thinking_enabled` 按能力位、`reasoning_effort: "high"`），输入 `请先用任务列表规划三个步骤，再逐步写出一份三段式周报模板。`。断言：run 状态 success；本机库里该 run 的 `model_name` 含 `glm-5.4`（没有静默回退）；`token_usage_by_model` 有 glm-5.4 桶，`_build_pricing_map` + `_run_cost` 算出的费用非 None，且与按 8/28/2 手算一致；至少一次 `write_todos` 原生工具调用成功；`GET /api/models` 中 glm-5.4 的 `modes` 为 `["standard", "ultra"]`。结果写入证据目录 `glm-run.json`。
- [ ] **Step 7：提交并走 PR。** `git commit -m "feat(models): 接入 360 GLM 5.4（标准/Ultra 可选）"`（`config.example.yaml`、`test_model_modes_config.py`、`test_model_roster_live.py`；本机 `config.yaml` 不入库）。`config.example.yaml` 属核心路径，必须走 PR；PR 正文写明部署侧要按「六」同步服务器 config 与价目。合入后状态行改为 `未执行（执行中：To-dos）`。

### Task 3 / PR 3：To-dos 停滞时提醒模型（BUG-03）

分支 `todos-progress-reminder`。前提：Task 0 Step 4 判定为实施（批量补写型 ≥ 20%，或证据不足）；判定为拆 pending 时直接做 Step 8。

**Interfaces（Produces，`todo_middleware.py`）：**

- `def _tool_rounds_since_last_todo_update(messages: list[Any]) -> int | None`：最后一条含 `write_todos` 的 AIMessage 之后，含 ≥1 个工具调用且不含 `write_todos` 的 AIMessage 条数（一条回复里的并行调用算一轮）；消息里没有 `write_todos` 时返回 None（交给既有的上下文丢失提醒）。
- `def _progress_reminder_after_last_update(messages: list[Any]) -> bool`：最后一次 `write_todos` 之后是否已有 `name="todo_progress_reminder"` 的 HumanMessage。
- `TodoMiddleware._PROGRESS_STALE_TOOL_ROUNDS: int`，取 Task 0 得出的 K（默认 6）。
- `before_model` 新分支：`write_todos` 仍在上下文里 + 有 `status == "in_progress"` 的项 + 轮数 ≥ K + 本窗口尚未提醒 → 返回 `{"messages": [HumanMessage(name="todo_progress_reminder", additional_kwargs={"hide_from_ui": True}, content=<下方文案>)]}`；不返回 `todos` 键，不改任何状态；上下文丢失提醒与收尾提醒的逻辑不变。新分支写在现有 `if _todos_in_messages(messages): return None` 之前（或并进该分支）：那条 return 正是「`write_todos` 仍可见」的情形，写在它后面提醒就是死代码。前端已按 `hide_from_ui` 隐藏此类消息（`frontend/src/core/messages/utils.ts`），不改前端。
- 两类页面：普通聊天页与 Agent 聊天页（`app/workspace/chats/[thread_id]/page.tsx:267-272`、`app/workspace/agents/[agent_name]/chats/[thread_id]/page.tsx:343-349`）都把 `thread.values.todos` 交给同一个 `TodoList`，提醒在后端对两类对话同样生效；因此由后端图级测试 + `TodoList` 测试覆盖两页，不另加页面级测试。
- 提醒文案（逐字，`{rounds}` 为实际轮数）：

```
<system_reminder>
You have made {rounds} rounds of tool calls since your last `write_todos` update.
If the step you were working on is finished, call `write_todos` now: mark it `completed` and mark the next step `in_progress`.
Do not mark a step `completed` unless it is fully done. If the current step is still in progress, keep working without changing the list.
</system_reminder>
```

- [ ] **Step 1：确认走向与 K。** 按 Task 0 Step 4 的判定：实施 → `_PROGRESS_STALE_TOOL_ROUNDS = K`，继续 Step 2；拆 pending → 直接 Step 8。证据不足（`SERVER_EVIDENCE_NOT_VERIFIED`）时 K = 6，照常实施，不补本机基线。
- [ ] **Step 2：写单元失败测试。** `backend/tests/test_todo_middleware.py` 新增 `class TestProgressReminder`（K 用类属性 monkeypatch 为 6）：
  - `test_counts_tool_rounds_since_last_write_todos`：write_todos 之后 2 轮业务工具（其中一轮有两个并行调用）→ 2。
  - `test_rounds_none_without_write_todos`。
  - `test_injects_progress_reminder_after_k_rounds`：todos `[in_progress, pending]` + 6 轮 → `name == "todo_progress_reminder"`、`additional_kwargs["hide_from_ui"] is True`、内容含 `6 rounds` 与 ``Do not mark a step `completed` unless it is fully done``、返回值里没有 `todos` 键。
  - `test_no_progress_reminder_below_k_rounds`：5 轮 → None。
  - `test_progress_reminder_once_per_stale_window`：已提醒后再过 6 轮 → None。
  - `test_progress_reminder_rearms_after_write_todos`：提醒 → write_todos → 6 轮 → 再次提醒。
  - `test_no_progress_reminder_without_in_progress_item`：全 pending、全 completed 各一例 → None。
  - `test_context_loss_reminder_unchanged`：消息里没有 write_todos 时仍注入 `todo_reminder`。
- [ ] **Step 3：写真实图失败测试。** 新建 `backend/tests/test_todo_progress_graph.py::test_todos_advance_mid_run_after_progress_reminder`：真实 `create_agent` + `TodoMiddleware` + `ThreadState` + 业务桩工具 `do_step`；脚本模型仿 `test_subagent_turn_budget.py::_ScriptedModel`：第 1 轮 `write_todos([A in_progress, B pending])`，之后每轮调 `do_step`；看到最后一条消息是 `todo_progress_reminder` 时调 `write_todos([A completed, B in_progress])`；再 2 轮 `do_step` 后 `write_todos([A completed, B completed])`；最后给出无工具调用的回复。用 `stream_mode="values"` 收集快照，断言：出现 `[completed, in_progress]` 快照，且早于最后一条无工具调用 AIMessage 所在的快照；消息中恰有 1 条 `todo_progress_reminder`。
- [ ] **Step 4：确认失败。** `PYTHONPATH=. uv run pytest tests/test_todo_middleware.py tests/test_todo_progress_graph.py -q` → 新用例失败。
- [ ] **Step 5：实现。** 按 Interfaces 改 `todo_middleware.py`（`abefore_model` 仍委托同步实现）。
- [ ] **Step 6：确认通过并回归。** Step 4 的命令全绿；`PYTHONPATH=. uv run pytest tests/test_loop_detection_middleware.py tests/test_terminal_response_middleware.py tests/test_tool_call_protocol_middleware.py tests/test_lead_agent_prompt.py -q` → 全部 passed。前端新增回归钉 `frontend/tests/unit/components/workspace/todo-list.dom.test.tsx::rerenders status changes between snapshots`：`<TodoList todos={[A in_progress, B pending]} collapsed={false} />` 重渲染为 `[A completed, B in_progress]` 后，A 的内容节点带完成态（`QueueItemContent completed` 渲染出的样式），B 的内容节点带 `text-primary/70`。界面本批不改，此用例预期直接通过；`pnpm exec vitest run tests/unit/components/workspace/todo-list.dom.test.tsx` 通过。
- [ ] **Step 7：本机真实验证（付费，≤3 次）。** 本机隔离栈（只起 gateway），标准档默认模型，API 级：`POST /api/threads/{id}/runs/stream`，`stream_mode: ["values"]`，context 同前端标准档（`mode: "standard"`、`is_plan_mode: true`、`subagent_enabled: false`、`thinking_enabled: false`、`reasoning_effort: "high"`）。输入逐字如下，共 11 个必须逐次执行的工具步骤；「整理学习目标」一项跨 8–9 轮，K ≤ 8 时足以触发提醒：`请先用任务列表列出两项：「整理学习目标」（第 1–9 步）和「检查并总结」（第 10–12 步），再严格按顺序执行。每一步单独调用一次工具，等上一步的结果出来再做下一步，不要合并步骤：1) 新建 notes.md，只写一行「学习目标」；2) 读取 notes.md；3) 在末尾追加第 1 条学习目标；4) 读取 notes.md；5) 追加第 2 条学习目标；6) 读取 notes.md；7) 追加第 3 条学习目标；8) 读取 notes.md；9) 给每条目标补一句完成标准；10) 读取 notes.md；11) 列出工作区文件，确认 notes.md 在其中；12) 用一段话总结 notes.md 的内容。` 探针脚本 `todos_live_probe.py` 放证据目录，逐个 values 事件记录接收时间、todos 状态序列与消息序列，写入 `todos-live.json`。先跑 1 次，提醒没触发再跑，最多 3 次。判定：
  - **通过：** 消息里出现 `todo_progress_reminder`，且在它之后、最终回复之前有一次 `write_todos`。
  - **停下交用户：** 提醒之后的 `write_todos` 把对应工具步骤还没执行的项标成 completed（例如第 9 步执行前就把「整理学习目标」标完成）。
  - **3 次都没触发提醒**（模型及时更新，或把步骤并进少于 K 轮；K ≥ 9 时本场景可能到不了 K）：Step 3 的图级测试已绿就照常合入，changelog 写「live 未触发提醒」并列入未验证清单。
  - **只记录、不阻断：** 某个快照里 `in_progress` 超过 1 个（本场景没有并行工作；报告要求非并行时只有一个 `in_progress`，本批不改提示词与 UI），或提醒出现之前就提前标完成（与本批改动无关）→ 登记 backlog。
- [ ] **Step 8：（仅证据不支持批量补写时）拆 pending。** 不改代码；新建 `docs/plans/pending/2026-11-26-todos-realtime-transport-plan.md`，头部写拆分来源（本计划 Task 3）、基准 commit、`server-evidence.json` 的聚合数与判定；正文列出 SSE（nginx 缓冲、`values` 事件发布间隔）与前端（`useStream` 状态合并）的排查项。backlog 对应条目注明「拆 pending」，直接进入 Task 4。
- [ ] **Step 9：提交并走 PR。** `git commit -m "feat(agents): To-dos 停滞时提醒模型及时更新进度"`（`todo_middleware.py`、`test_todo_middleware.py`、`test_todo_progress_graph.py`、`todo-list.dom.test.tsx`）；中间件属核心路径，按「PR 门禁」走 PR；合入后状态行改为 `未执行（执行中：Ultra）`。

### Task 4 / PR 4：Ultra 场景化委派与 run 执行模式（BUG-02）

分支 `ultra-delegation-policy`。

**Interfaces：**

- Consumes：Task 2 合入后 `modes` 含 `ultra` 的模型（含 glm-5.4；PR 2 取消时为 3 个模型），默认模型取 `mode_defaults["ultra"]`。
- Produces：`app.gateway.services._run_mode_metadata(config: dict[str, Any]) -> dict[str, Any]`，返回 `{"run_mode": {"mode": str | None, "subagent_enabled": bool, "is_plan_mode": bool}}`，读运行时视图 `{**config.get("configurable", {}), **config.get("context", {})}`，与 `lead_agent/agent.py::_get_runtime_config` 同口径：`body.config["context"]` 优先，其次 `body.config["configurable"]`，顶层 `body.context` 只补缺。
- `start_run`：把 `config = build_run_config(...)` 与其后的 context 合并整块移到 `create_or_reject` 之前（两者都不依赖 run 记录，中间的线程 upsert 只读 `body.metadata`；顺带让 `build_run_config` 的 ValueError 在建 run 之前抛出），传给 `create_or_reject` 的 `metadata = {**(body.metadata or {}), **_run_mode_metadata(config)}`（服务端值覆盖客户端同名键）。新 dict 不写回 `body.metadata`，`threads_meta` 的 metadata 不变。
- 提示词（`prompt.py`）：`n == 1` 全部保持原文（并行没有收益）；以下只改 `n > 1`，用变量区分两支。
  - `_build_subagent_section` 的标题与开头一段（"Subagents are optional. **Default to direct execution.** …"）换成下方开头文案。
  - DELEGATION CHECK 标题的括注从 ``(required before every `task` call)`` 改为 `(for cases the routing rules above do not settle)`：净收益检查只管规则没覆盖的情况。检查正文、负面信号、硬上限、可用子代理、examples 原样保留。
  - workflow 整段换成下方五步：原第 1–3 步（"Establish the cheapest credible direct-execution path" 起）先找直接执行路径，会把独立工作流也拉回直接执行；原第 4、6 步的文字保留为新第 3、5 步。
  - `apply_prompt_template` 里的 `subagent_reminder`、`subagent_thinking` 换成下方两句。原句「Delegate when the net benefit is clear; otherwise execute directly」「no simpler direct path exists」位于系统提示末尾的 critical reminders，不改就会把新规则压回去。

开头文案：

```
## Subagent Routing: Run Independent Workstreams in Parallel

Subagents are enabled for this run.

**Delegate when the request contains two or more independent workstreams**, for example:
- researching several subjects, sources, or options that do not depend on each other;
- producing several deliverables that do not need each other's results;
- a bounded, context-heavy investigation that would otherwise crowd out important lead-agent context.

Launch independent workstreams as parallel `task` calls in the same response, then verify and synthesize the results.

**Execute directly** when the work is a single thread, a chain where each step needs the previous step's result, a workflow with user confirmation gates, or a small request you can finish with one or two tool calls in total. Agent or skill instructions that forbid delegation always take precedence.

For cases the rules above do not settle, run the delegation check below.
```

workflow（`{n}` 照旧插值）：

```
1. Split the request into workstreams and note which ones need another's output.
2. If two or more workstreams are independent, delegate them. Otherwise apply the delegation check above, including every negative signal, and delegate only if it wins clearly.
3. Give each subagent a bounded, non-overlapping scope, relevant known context and paths, an expected output, and explicit side-effect ownership.
4. Launch the independent workstreams together, up to {n} calls and the remaining run allowance.
5. Verify and synthesize returned results. Resolve contradictions against primary evidence instead of forwarding incompatible conclusions.
```

两句提醒：

```
- **Subagent Mode**: When the request has two or more independent workstreams, delegate them as parallel `task` calls in the same response. Execute directly for single-thread work, dependent chains, confirmation-gated workflows, or when agent or skill instructions forbid delegation. **HARD LIMIT: max {n} `task` calls per response.**
- **Before delegating**: identify the independent workstreams and confirm each subagent's deliverable and completion criteria. Max {n} `task` calls per response.
```

- 文案：zh-CN `ultraModeDescription` = `最强档：相互独立的子任务会并行交给子代理，简单任务直接完成，耗时更长`；en-US = `Strongest mode: independent subtasks run in parallel on subagents, simple tasks are done directly; takes longer`。
- 评测场景（逐字）：多分支 `请分别调研阿里云、腾讯云、华为云三家对象存储（OSS / COS / OBS）的标准存储单价与免费额度，三家各自独立整理成一段说明，最后汇总成一张对比表。`；简单 `把这句话改写得更正式：明天的会改到下午三点。`

- [ ] **Step 1：写提示词失败测试。** `backend/tests/test_lead_agent_prompt.py`：
  - 用 `test_subagent_section_delegates_independent_workstreams`（n=3）替换 `test_subagent_section_defaults_to_direct_execution`。断言包含："two or more independent workstreams"、"parallel `task` calls in the same response"、"Agent or skill instructions that forbid delegation always take precedence"、"DELEGATION CHECK (for cases the routing rules above do not settle)"、"Split the request into workstreams"、"expected benefit is clearly greater than the expected cost"、"MAXIMUM 3 `task` CALLS PER RESPONSE"、"MAXIMUM 12 `task` CALLS PER RUN"。断言不包含："Default to direct execution"、"required before every `task` call"、"Establish the cheapest credible direct-execution path"、"Launch only the smallest useful batch"、"DECOMPOSE, DELEGATE, SYNTHESIZE"、"Complex tasks should be decomposed"。
  - 新增 `test_single_slot_subagent_section_keeps_net_benefit_rule`（n=1）：含 "Default to direct execution"、"required before every `task` call"、"Establish the cheapest credible direct-execution path"，不含 "independent workstreams"。
  - `test_subagent_reminders_aligned_with_direct_execution` 改名 `test_subagent_reminders_follow_routing_rule`：`max_concurrent_subagents=3` 的提示词含 "Subagent Mode**: When the request has two or more independent workstreams, delegate them as parallel `task` calls in the same response." 与 "Before delegating**: identify the independent workstreams"，不含 "Delegate when the net benefit is clear; otherwise execute directly"、"no simpler direct path exists"、"Subagent Mode**: Default to direct execution"、"DECOMPOSITION CHECK"，仍含 "DELEGATION CHECK"；`max_concurrent_subagents=1` 的提示词仍含原两句。
- [ ] **Step 2：写 run 元数据失败测试。** `backend/tests/test_gateway_services.py`：`_capture_start_run_config` 让 `create_or_reject` 的 AsyncMock 把调用 kwargs 记入 `captured["create_kwargs"]`。
  - 新增 `test_start_run_records_server_owned_run_mode`：context `{"mode": "ultra", "subagent_enabled": True, "is_plan_mode": True}`、`metadata={"run_mode": "forged", "keep": 1}` → `captured["create_kwargs"]["metadata"] == {"keep": 1, "run_mode": {"mode": "ultra", "subagent_enabled": True, "is_plan_mode": True}}`。
  - 新增 `test_start_run_without_context_records_null_mode` → `run_mode == {"mode": None, "subagent_enabled": False, "is_plan_mode": False}`。
  - 新增 `test_start_run_run_mode_follows_runtime_precedence`（参数化两例）：① `config={"configurable": {"subagent_enabled": False}}`、`context={"mode": "ultra", "subagent_enabled": True, "is_plan_mode": True}` → `run_mode["subagent_enabled"] is False`；② `config={"context": {"subagent_enabled": True}}`、`context={"mode": "ultra", "subagent_enabled": False, "is_plan_mode": True}` → `True`。两例都断言 `run_mode` 三个键与 `captured["config"]` 运行时视图（`{**configurable, **context}`）里的值一致，且调用后 `body.metadata` 里没有 `run_mode`。
- [ ] **Step 3：确认失败。** `PYTHONPATH=. uv run pytest tests/test_lead_agent_prompt.py tests/test_gateway_services.py -q` → 新用例失败。
- [ ] **Step 4：实现。** 改 `_build_subagent_section`、`apply_prompt_template` 的两句提醒与 `start_run`，新增 `_run_mode_metadata`。
- [ ] **Step 5：确认通过并回归。** Step 3 的命令全绿；`PYTHONPATH=. uv run pytest tests/test_subagent_limit_middleware.py tests/test_lead_agent_skills.py tests/test_lead_agent_model_resolution.py tests/test_agent_run_visibility.py tests/test_retired_agent_alias.py -q` → 全部 passed。
- [ ] **Step 6：文案与勘误。**
  - i18n 两个文件改 `ultraModeDescription`。
  - `docs/plans/2026-11-18-model-mode-ladder-plan.md` 勘误三处：
    - §1.1 Ultra 行的「规划 + 子代理 + 更长执行预算」改为「规划 + 子代理（2026-11-26 勘误：三档同受服务端 `max_recursion_limit: 2500`，不再承诺更长执行预算）」；
    - §1.2 末条的「Ultra 与标准的差异是规划、子代理与执行预算」改为「Ultra 与标准的执行差异只有子代理（两档都开规划；执行预算口径已于 2026-11-26 删除）」；
    - §1.5 的 Ultra 描述换成新文案，并注「2026-11-26 更新」。
  - `cd frontend && pnpm check` 与 `python3 scripts/check_docs_convention.py` 通过。
- [ ] **Step 7：写 live 评测。** 新建 `backend/tests/test_ultra_delegation_live.py`，模块级 skip 与 `pytestmark = pytest.mark.live` 同 `test_model_roster_live.py`。
  - 单个 trial：`create_agent(model=create_chat_model(name, thinking_enabled=<supports_thinking>, reasoning_effort="high", app_config=cfg, attach_tracing=False), tools=<桩工具>, middleware=[_create_todo_list_middleware(True)], system_prompt=apply_prompt_template(subagent_enabled=True, max_concurrent_subagents=3, app_config=cfg), state_schema=ThreadState)`。
  - 桩工具：取 `get_available_tools(include_mcp=False, model_name=name, subagent_enabled=True, app_config=cfg)` 的 name、description、args_schema 重建，`task` 返回「子任务已完成（评测桩）」，其他工具返回「（评测桩）已执行」——子代理不真跑。
  - 调用上限：`raise_error = True` 的回调在第 7 次 `on_chat_model_start` 时抛 `_TrialBudgetExceeded`，结束该 trial，保留最后一个 values 快照。
  - 统计：每条 AIMessage 的 `task` 调用数。
  - 用例：
    - `test_default_ultra_model_delegates_independent_workstreams`（阻断）：多分支 3 次中，某条回复里 `task` 数 ≥2 的 trial 至少 2 个；简单场景 1 次，`task` 总数为 0。
    - `test_other_ultra_models_delegation_rates_recorded`（不阻断）：其余 Ultra 模型各跑 3 次多分支 + 1 次简单，只记录结果。
  - 汇总写到环境变量 `ULTRA_EVAL_OUT` 指定的 JSON 路径。
- [ ] **Step 8：运行评测（付费，≤16 个 trial）。** 在同一条命令内加载密钥后运行 `RUN_LIVE=1 ULTRA_EVAL_OUT=<证据目录>/ultra-eval.json PYTHONPATH=. uv run pytest tests/test_ultra_delegation_live.py -v -s`，先跑默认模型（4 个 trial：3 次多分支 + 1 次简单）。
  - 达标：其余 Ultra 模型各 4 个 trial（按评测时的花名册：四款共 16，PR 2 取消时三款共 12）。
  - 不达标：只改 `n > 1` 分支的委派文案（开头规则、检查标题、workflow、examples）与两句提醒，改一轮，默认模型重跑 4 个 trial；其余模型降为各 1 次多分支 + 1 次简单（合计 8 + 2 ×（模型数 − 1）：四款 14、三款 12）。仍不达标 → 停下，带 `ultra-eval.json` 交用户决定。
  - 记录：各模型触发率写进 changelog；某个模型多分支 3 次全为 0 时登记 backlog。
- [ ] **Step 9：真实端到端 Ultra（付费，2 次）。** 本机隔离栈全套（gateway + 前端 + nginx），Ultra 档、默认模型、多分支场景。断言：
  - `POST /api/threads/{id}/history` 里有 ≥2 个 `task` 调用且各有 ToolMessage 结果，run 状态为 success；
  - 界面出现子任务卡片，截图存证据目录；
  - 本机库里该 run 的 `metadata_json.run_mode == {"mode": "ultra", "subagent_enabled": true, "is_plan_mode": true}`。
- [ ] **Step 10：提交并走 PR。** 分三笔提交：
  - `feat(agents): Ultra 对相互独立的工作并行委派子代理`（`prompt.py`、`test_lead_agent_prompt.py`、`test_ultra_delegation_live.py`）；
  - `feat(runs): run 元数据记录执行模式`（`services.py`、`test_gateway_services.py`）；
  - `docs(modes): Ultra 文案与模式阶梯勘误`（i18n 两个文件、模式阶梯计划）。
  - 按「PR 门禁」开 PR。

### Task 5：收尾

- [ ] **Step 1：最终门禁。** 四个 PR 合入后，在 worktree `git fetch origin && git switch --detach origin/main`，跑根 `make check`、`cd backend && make test`（后台）、`cd frontend && pnpm check && pnpm test`、`python3 scripts/check_docs_convention.py`，全部通过。
- [ ] **Step 2：完成 changelog。**
  - 「验收记录」逐行写「命令 → 实际关键输出」（每个 PR 的定向测试、全量门禁、PR 与合入 hash）。
  - 证据摘要：`server-evidence.json` 聚合数、GLM 探针结论、各 Ultra 模型触发率、To-dos 实测快照序列。
  - 「未验证清单」：部署侧各项、超限或跳过的付费项、`SERVER_EVIDENCE_NOT_VERIFIED`（如有）。
  - 写明「修复前已删除的作品不回填」与「删除快照写失败会让删除失败」。
- [ ] **Step 3：更新 backlog。**
  - 勾掉四条 2026-11-26 条目；BUG-03 只有 PR 3 合入才勾，否则注明「拆 pending」。
  - 补登：Ultra 非默认模型 0/3 的情况、GLM 取消（如有）。
  - 双 DELETE 条目保持未勾。
- [ ] **Step 4：回填状态头。** 状态行改为 `已执行（<日期>；PR #… → #… → #… → #… 串行合入 main <hash>；changelog：<链接>；未部署，发布见「六」）`，以纯 docs 提交推 main。
- [ ] **Step 5：清理。**
  - `git worktree list`；在 worktree 里 `git status --ignored --short`，确认没有未转移的证据后再 `git worktree remove`；删除四条远程分支。
  - 停掉本机栈端口，确认没有在途的后台任务。

## 四、验证清单

**实现级（进 pytest / vitest 拦回归）：**

- [ ] 迁移：`test_usage_tombstones_migration.py` 的升级与回滚两例。
- [ ] 快照写入：普通对话与无主线程不写、双 DELETE 不覆盖、写失败拒绝删除（任务与会话各一）、删除时进行中的条目记为未完成。
- [ ] GLM 5.4：花名册契约、示例模板（含 CI 也跑的价目断言）、yaml 价目解析 `9.3`、`/api/models` 档位。
- [ ] To-dos：`TestProgressReminder` 八例。
- [ ] Ultra：提示词三例（n=3 分区、n=1 分区、两句提醒）、`run_mode` 三例（含运行时优先级）。
- [ ] 前端：`usage-task-list.dom.test.tsx` 已删除条目、`todo-list.dom.test.tsx` 状态重渲染。
- [ ] 全量：根 `make check`、`cd backend && make test`、`cd frontend && pnpm check && pnpm test`、docs convention。

**行为级（用户可感知）：**

- [ ] BUG-01（API 级）：走真实删除路径，删除前后 `get_personal_usage` 的总费用与 Token 不变（AI 生图、AI 视频、Agent 会话各一例）；能力筛选正确；晚到账本照常计入；`unified_tasks`/`threads_meta` 行已删，作品列表的数据源里没有该项——Task 1 Step 6。
- [ ] FEAT-01（live）：探针通过；真实对话的 `model_name` 没有回退、费用按 8/28/2 入账、原生工具调用成功——Task 2 Step 2/6。
- [ ] BUG-03（图级 + live）：真实 Agent 图在提醒之后、终态之前出现中间迁移；本机真实 run 出现 `todo_progress_reminder`，其后、最终回复前有 `write_todos`，且提醒后没有伪报完成（3 次都未触发时标未验证）——Task 3 Step 3/7。
- [ ] BUG-02（live）：默认 Ultra 模型达标；端到端出现子任务卡片；`run_mode` 落库——Task 4 Step 8/9。
- [ ] 部署侧（延后，需指令）：见「六」第 6 条。

## 五、Review Focus

1. **同一线程连发两次 DELETE**（`useDeleteThread`）：第二次不得改写或清空首个快照——Task 1 Step 1 `test_repeated_thread_delete_keeps_first_tombstone`。
2. **删除时仍在途的供应商调用**在删除后才落账：按 subject id 归组照常计入——Task 1 Step 6 `test_late_ledger_rows_after_deletion_are_counted`。
3. **快照已写入但删除失败**（任务仍在）：现存行优先、不翻倍——Task 1 Step 6 `test_live_row_wins_over_stale_tombstone`。
4. **提醒之后模型把没做完的步骤标为 completed**（伪报）：提醒文案逐字禁止；真实 run 中「整理学习目标」一项跨过 K 轮，提醒落在该项未完成之时，对照消息序列核对提醒后的每个 completed 都有已执行的工具步骤，伪报即停——Task 3 Step 7。
5. **新的委派规则误伤顺序/确认门工作流**（course-video、ppt-master 在 Ultra 下开始乱委派）：规则显式排除这类流程，且「Agent 指令优先」一句有断言——Task 4 Step 1；简单场景 0 委派——Task 4 Step 8。

## 六、发布清单与回滚（需用户另行下达部署指令）

1. **前置：** 读仓库外测试服 ops 记录，按其流程备份数据库；核对服务器 `config_version`，低于 33 时先按 v33 迁移。
2. **发布 gateway 与 frontend：** gateway 启动时 alembic 自动 `upgrade head` 到 `q0d1e2f3a4b5`；部署后确认 `usage_tombstones` 表存在。
3. **服务器 config：** `config.yaml` 加 glm-5.4 条目（含 `pricing`），按 ops 记录的 config 变更方式生效（单文件挂载需要重建容器，仅 restart 不生效）。服务器 config 若已有别的 `pricing` 块，币种须同为 CNY：混用会让 yaml 兜底价整表失效。
4. **价格录入数据库：** 在控制台模型价目页加 glm-5.4（8 / 28 / 2）后整表保存；或用 API：管理员 + `X-CSRF-Token`，先 `GET /api/admin/model-pricing` 取全表，加一行后 `PUT` 全表。**禁止只 PUT 单行**（整表替换会清空其他价目）。验证 GET 结果里 glm-5.4 为 `source: db`。
5. **L1 零成本验收：** 跑完删除 `frontend/tests/e2e/live/.auth/state.json`。
6. **部署后行为验收（涉及真实删除与付费，需另行授权）：**
   - 测试账号建一个最便宜的 AI 生图任务后删除，「我的用量」总额不变，且条目显示「作品已删除」；
   - Ultra 多分支场景跑一次，看到子任务卡片；
   - GLM 5.4 在标准档问答一次。
7. **回滚：**
   - **gateway：** 切回旧镜像之前，先在**新**镜像容器里执行 `alembic downgrade p9c0d1e2f3a4`（删除 `usage_tombstones`；本机已由迁移测试验证），否则旧镜像启动时的 `upgrade head` 找不到 `q0d1e2f3a4b5`，迁移会失败；也可以按 ops 记录还原数据库备份。
   - **config：** 删掉 glm-5.4 条目即可，价目行可以保留。
   - **前端：** 可单独回滚（`deleted` 是可选字段）。
   - **PR 3/4：** 纯代码行为，随镜像回滚。

## 七、已知限制

- 修复前已删除的作品不回填（没有可靠的类型来源）；普通对话（没有 `agent_name`）本来就不进「我的用量」，删除时也不写快照。
- 快照写失败会让删除失败（fail closed）：数据库不可用时用户需要重试。快照只记录删除那一刻的标题与状态，之后不再更新；删除时仍在进行的条目记为「未完成」（会话删除不取消在途 run，晚到的账本照常计入，状态不再变）。
- 窗口内没有任何账本行的作品，删除后从列表消失，`total_tasks` 随之减少；有账本行的条目照常保留。
- GLM 5.4 价目以数据库为准，config 兜底价只在数据库没有该行时生效；调价在控制台改。
- Ultra 是否委派由模型决定：评测只对默认模型设阻断线，其他模型的触发率只记录。course-video、ppt-master 等工作流在 Ultra 下仍基本不委派（设计如此，Q12）。
- To-dos 提醒只在停滞 ≥K 轮时每个窗口触发一次，不能保证模型一定及时更新，也不自动推进状态。
- `useDeleteThread` 双 DELETE 未修，已登记 backlog。
