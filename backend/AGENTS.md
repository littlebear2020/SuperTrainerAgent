# Backend Conventions

碰 `backend/` 时生效。全局约定（文档/计划/边界/完成定义）见根目录 `AGENTS.md`。

## 文档维护口径

- 行为/约定变更：批次工作按根 AGENTS.md 约定写 changelog；小修小改只写 commit message。
- 本文件只记录**模型读代码推不出来**的约定与坑；模块细节按需查本目录 `docs/`（见末节索引）。

## Commands

```bash
# 项目根
make dev / make start / make stop / make check-env / make install
# backend/ 目录
make dev            # Gateway :8001（reload）
make test           # 后端全量测试（xdist 并行，约 2 分钟；串行兜底 make test-serial）
make lint / make format
cd backend && uv run pytest tests/ -q                      # 全量
cd backend && PYTHONPATH=. uv run pytest tests/test_x.py -v  # 单文件
```

## Harness / App 边界（红线）

- `packages/harness/deerflow/`（import `deerflow.*`）= 可发布 agent 框架；`app/`（import `app.*`）= 不可发布应用层。
- 依赖单向：**app → deerflow 允许，反向禁止**，`tests/test_harness_boundary.py` 在 CI 守护。

## 中间件链（顺序即语义）

装配于 `packages/harness/deerflow/agents/middlewares/tool_error_handling_middleware.py`（`_build_runtime_middlewares`，基座段）+ `packages/harness/deerflow/agents/lead_agent/agent.py`（`_build_middlewares`，lead 段）（路径相对 `backend/`），顺序：
ThreadData → Uploads → WorkspaceInventory → Sandbox → DanglingToolCall → LLMErrorHandling → Guardrail → SandboxAudit → ToolResultSanitization → ToolErrorHandling → SandboxCircuitBreaker（基座段到此）→ DynamicContext → Summarization → TodoList → TokenUsage → TokenAttention → Title → Memory → ViewImage → DeferredToolFilter → SystemMessageCoalescing → SubagentLimit → LoopDetection → SafetyFinishReason → ModelLengthFinishReason → TokenBudget（条件启用）→ TerminalResponse → **ToolCallProtocol** → SlowLLMCall → custom 注入位 → Clarification（必须最后）。
新增/重排中间件前先读这两个装配点；ViewImage 的 wrapper 位置（Summarization 之后）有测试钉死（`tests/test_view_image_middleware.py:18`），链整体无测试全序钉死——漂移靠审计抓。

### LoopDetection 的 run 级生命周期（2026-10-14 加固批，移植上游 #2752/#5344/#6804）

- warn 级警告**瞬态**：`after_model` 只入 `(thread_id, run_id)` 作用域的 pending 队列，下一次 `wrap_model_call` 才把 `HumanMessage(name="loop_warning")` 追加到请求末尾（ToolMessage 之后，配对不破）；警告**不进 checkpoint**，`after_agent` 丢弃未消费警告。上游 `_LoopDecision`/`tool_freq_overrides`/`consume_stop_reason` 未移植——本仓 F4 豁免帽、F6 runtime.context 打点、recursion 联动、窗口化频率照旧。
- 全部检测状态按 `(thread_id, run_id)` 隔离：新用户 run 拿新预算，同 run 的 goal continuation 共享预算；无 `run_id` 时锚定 invocation（本仓 langgraph 无 `Runtime.control`，锚 `stream_writer` 对象身份）。`before_agent` 是拓扑 no-op，不碰 sibling run 的警告。
- warn 文案允许"先保存阶段成果 + 一次 read-before-write 重试"；hard stop 文案与阈值不变。

### ToolCallProtocol（正文伪工具调用守卫）

- 只拦截完整、JSON 可解析、无原生 `tool_calls`/`invalid_tool_calls` 的 `<tool_call>...</tool_call>` 字符串正文；**任何情况下不解析执行正文中的命令**。
- 首次命中：移除伪消息 + `jump_to="model"`，经 `wrap_model_call` 注入 `tool_protocol_recovery` 恢复提示（每 `(thread_id, run_id)` 一次，恢复计数按 run 重置）。
- 第二次同 run 命中：`after_model` 提交移除并打 `stop_reason="tool_protocol_error"`，下一次 `wrap_model_call` 抛 `ToolProtocolError` → worker 通用异常路径落 `status=error`（两段式是因为 after_model 在模型节点提交之后执行，直接抛会把伪消息留进 checkpoint）。
- 注册在 TerminalResponse **之后**是有意的：after_model 反序执行，后注册者先看模型输出——伪消息必须先被守卫拦截，才轮得到 TerminalResponse 判"空终态"；Clarification 仍最后。装配顺序有测试钉死（`tests/test_tool_call_protocol_middleware.py::TestChainAssembly`）。
- `LLMErrorHandlingMiddleware` 对 `ToolProtocolError` **显式放行**（同 `GraphBubbleUp` 形态，2026-10-15 评审二轮修复）——协议终断不是 provider 故障，外层不得吞成 fallback AIMessage 伪装 success；worker 级测试必须带这条真实链组合。
- LoopDetection 与 ToolCallProtocol 的 wrap 均"drain→try→异常回队→raise"（上游 #5433 同型）——模型调用瞬时失败被外层重试时，pending warning/恢复提示不丢失。
- ToolCallProtocol 的 after_model/aafter_model **必须**带 `@hook_config(can_jump_to=["model"])`（TerminalResponse/Todo 同款）：jump_to 是 EphemeralValue，反序链上排其后的常驻钩子节点各耗一步，不声明则跳转值到不了路由边——生产链首次违规会静默空成功（2026-10-15 评审三轮实证，测试 `TestProductionOrderChain` 钉死）。同类警示：**新增会 after_model 跳转的中间件时，穿透/终断类测试必须用生产注册序链**（含 LoopDetection/TerminalResponse 等），两件套最小链测不出拓扑缺陷。
- Summarization 的摘要回落 except 前放行 ToolProtocolError（2026-10-15 评审三轮）——回落重调会吞内层一次性弹消费的终止标志。

## Config 关键口径

- `config.yaml` 在项目根（推荐）；优先级：显式 path > `DEER_FLOW_CONFIG_PATH` > backend/ > 根。
- 改 schema 必须递增 `config.example.yaml` 的 `config_version`（部署端 config 只读挂载，靠版本号触发升级提示）。
- `get_app_config()` 按 mtime 热重载；但 lifespan 启动快照持有者是 **restart-required**：`database.*`、`checkpointer.*`、`run_events.*`、`stream_bridge.*`、`sandbox.use`、`log_level`、`channels.*`、`checkpoint_prune.*`。
- 模型定价在 `model_pricing` 表（控制台免重启改）；`config.yaml` 的 `models[*].pricing` 只是启动兜底，且会在进 provider 前被剔除（多带字段会让 provider API 400）。

## 运行与入口

- 启动矩阵：本地 `make dev` / `make start`；docker dev `./scripts/docker.sh start`；docker prod `./scripts/deploy.sh`。
- nginx 路由：`/api/langgraph/*` → gateway:8001（重写为 `/api/*`）；其余 `/api/*` → gateway:8001；`/` → frontend:3000。
- Gateway `:8001`，`GET /health`；生产设 `GATEWAY_ENABLE_DOCS=false`。

## 后端关键坑（踩过、推不出来）

- **admin 鉴权**：`@require_permission` 表达不了 admin（authz 给每个认证用户全权限列表且无 admin 项）——必须 `Depends(require_admin)`，且用 dependency 而非 body 内 check（宽 `except Exception` 会把 403 吞成 500）。
- **Provider 包裹链无 `__getattr__`**：给 provider 新增能力属性必须同时透传 `ThrottledAIVideoProvider` + `FallbackAIVideoProvider` + `capabilities.py` 静态表三处；测试必须用真实包裹链（BUG-B：裸 mock 让"生产从未生效"的代码 5 个测试全绿）。改防护类代码后做变异验证（撤回修复看测试真红）。
- **视频时长**：音频是时间轴唯一真相（`audio_flow.reconcile_durations_with_audio`）；下发 provider 的时长经 `duration_policy` 向上对齐栅格；提示词里写"9 秒"无效（duration 是独立 API 参数）。
- **H3 与 Hailuo 共用 `QihuVideoProvider` 但请求体不同**：H3 强制扁平 `extra_body.resolution`（仅 768P/2K）+ `extra_body.ratio`，缺即 400——`extra_body` 按模型门控（`_is_h3_model`）。
- **子代理链的 SandboxMiddleware 必须 `release_on_exit=False`**（子代理继承父沙箱，退出释放会拆父容器）；lead 链保持 True。
- **TTS 降级链** doubao→qihu→edge，但**克隆音色永不降级**（静默换音色比任务失败更糟）。
- **沙箱 bash 输出**中间截断（`sandbox.bash_output_max_chars`，默认 20000），完整版落 `<workspace>/.tool-results/`。
- MCP：stdio 工具 session 池化；HTTP/SSE 不池化（cancel scope 限制）。
- `seed` 的 agent 集只增不删：退役壳的运行时目录（`.deer-flow/agents/`）须手动清理。

## TDD（强制）

- 新特性/修复必须带 `backend/tests/test_<feature>.py`。
- 测试节奏：迭代中跑定向（`pytest tests/test_x.py -q`）+ 相关回归；**收尾跑一次全量**（`make test`，xdist 并行约 2 分钟）全绿才算完——与根 AGENTS.md 完成定义同口径。裸跑 pytest 不带 CI=true 也是安全的（live 测试一律 `RUN_LIVE=1` 显式 opt-in）。
- 测试循环 import 问题：在 `tests/conftest.py` 加 `sys.modules` mock（有先例）。

## 代码风格

ruff lint + format；行宽 240；Python 3.12+ 类型标注；双引号。

## 模块详情（路径相对 `backend/`，按需查阅，勿全载）

- video_gen（分镜管线/provider lore/方言层/remotion_course/QC）：`docs/VIDEO_GEN_NOTES.md`
- 其余模块（voice_twin/sandbox/subagent/tools/MCP/skills/memory/channels/client/config schema）：`docs/BACKEND_MODULE_NOTES.md`
- 课程管线全局口径另见根 `AGENTS.md` 的 remotion_course 节与 `../docs/remotion-course-pipeline.md`。
