# Backend 模块详情（从 backend/CLAUDE.md 迁出，2026-09-30）

> 按需查阅的参考文档，非常驻上下文。video_gen 模块单独在 `VIDEO_GEN_NOTES.md`。

## Voice Clone & Production Module (`app/gateway/voice_twin/`)

Voice cloning via Volcengine Mega TTS API (V1/V3) with a production task pipeline for audio/video generation.

**Architecture**:

```
voice_twin/
├── config.py              # VoiceCloneConfig (appid, token, access_key, storage_dir, edge_tts, reaper/timeout knobs)
├── models.py              # VoiceTwinRow SQLAlchemy model
├── ensure_schema.py       # voice_twins 表启动自愈（对齐 VoiceTwinRow 补缺失列；配套 tests/test_voice_twin_ensure_schema.py）
├── schemas.py             # Pydantic schemas (SynthesizeRequest, MODELS_INFO)
├── service.py             # VoiceCloneService (prescreen, training, polling, synthesize, training reaper)
├── volcengine_client.py   # Volcengine TTS HTTP client (upload, status, SSE synthesize; VolcengineTTSHTTPError)
├── qihu_tts_client.py     # 360 TTS SSE client (QihuTTSHTTPError)
├── doubao_tts_client.py   # ByteDance seed-tts-2.0 SSE client (DoubaoTTSHTTPError)
├── edge_tts_client.py     # edge-tts client (tenacity 3x transport retry + wait_for timeout, audio-only)
├── _retry.py              # Shared TTS retry: transport + structured 429/5xx, exponential backoff (clone + production paths)
├── preset_voices.py       # voice_id syntax helpers (preset:/doubao:/edge:) + catalog display-name lookup
├── errors.py              # Friendly error messages
├── repository.py          # VoiceTwinRepository (async CRUD + list_by_status)
├── ppt_extract.py         # PPT content extraction for ppt-to-video
├── production_service.py  # ProductionService (script-to-audio, ppt-to-video) + degradation chain doubao→qihu→edge
└── production_schemas.py  # Production task schemas
```

**Shared voice catalog (2026-08-09 A1)**: `app/gateway/_shared/voice_catalog.py` is the single source of truth for the 35 curated voices (edge/qihu/doubao, `CatalogVoice` with prefixed ids + scenario/tone tags). `video_gen/orchestration/voices.py` re-exports it as legacy dicts (bare edge ids, capitalized gender) so `/api/video/voices` is byte-identical. `GET /api/voice-twins/preset-voices` returns the full catalog filtered by configured credentials (qihu needs QIHU_API_KEY, doubao needs access_key, edge needs `edge_tts.enabled`).

**Endpoints**: `POST /api/voice-twins/prescreen` / `upload`；`GET /api/voice-twins` / `/{id}/status` / `/{id}/demo`；`POST /{id}/synthesize`；`GET /models` / `/preset-voices`；production 族：`POST /api/voice-twins/production/script-to-audio` / `doc-extract-text` / `ppt-to-video`，`GET/DELETE /api/voice-twins/production[/{id}[/download|/stream]]`。

**Document-to-Audio Flow**: 前端传 `.docx/.txt/.md` → `/doc-extract-text` 预览 → `/script-to-audio` 提交；直连 `/doc-to-audio` 已删（2026-08-09 批次C：零调用方）；`strip_markdown()` 先清洗再 TTS；`_extract_text_from_upload()` 统一校验 + markitdown 转换。

**Concurrent TTS Synthesis**: `asyncio.Semaphore(3)`（`tts_concurrency` 可调）+ Lock 保护进度；`_do_tts_synthesis()` 路由 cloned/360/doubao/edge（`edge:` 前缀），共享 `_retry.call_with_tts_retry`（transport + 429/5xx，3 次指数退避）；降级链 doubao→qihu→edge，**克隆音色永不降级**（静默换音色比失败更糟）；失败 chunk 清理残件并汇报计数；`split_text(max_chars=5000)` 尊重段落/句子边界（英文缩写/小数先掩码）。

**Training stall reaper (2026-08-09 D1)**: `start_training_reaper()` 从 lifespan 启动，按 `training_reaper_interval_minutes` 扫 `training`/`uploading` 超时行，真实远端轮询一次后收敛 ready/failed——不再依赖前端开着轮询。`poll_status` 拆异常：httpx transport → 继续轮询；`KeyError`/`TypeError`/`ValueError` → fail fast。

**Tests**: `tests/test_voice_twin_production.py`

## Sandbox System (`packages/harness/deerflow/sandbox/`)

**Interface**: Abstract `Sandbox` with `execute_command`, `read_file`, `write_file`, `list_dir`；**Provider Pattern**: `SandboxProvider` with `acquire`/`get`/`release`；实现：`LocalSandboxProvider`（本地单例）、`AioSandboxProvider`（Docker 隔离，`community/`）。

**Virtual Path System**: agent 见 `/mnt/user-data/{workspace,uploads,outputs}`、`/mnt/skills`；物理落 `backend/.deer-flow/users/{user_id}/threads/{thread_id}/user-data/...`、`deer-flow/skills/`；翻译走 `replace_virtual_path()`/`replace_virtual_paths_in_command()`；`is_local_sandbox()` 看 `sandbox_id == "local"` 或 `local:{thread_id}` 前缀（per-thread 沙箱）。

**Sandbox Tools**（`sandbox/tools.py`）:
- `bash` — 输出中间截断于 `sandbox.bash_output_max_chars`（20000），超帽全文落 `<workspace>/.tool-results/bash-*.log` 并在截断标记里带路径（2026-09-22 P3）。F3（2026-09-29）：截断前两分支都跑 `sandbox/failure_guidance.append_failure_guidance`——输出尾窗命中资源失败签名（SIGKILL / Target closed / Cannot allocate memory / 行首 Killed / OOM / chrome-headless+crash）时追加中文处置块（`<resource_failure_guidance>` tag，已在 `untrusted_content.py` 登记），告诉模型不要原样重跑（concurrency=1 / --frames 分段 / 清缓存）。
- `ls` — tree 形式，最深 2 层。
- `read_file` — `start_line`/`end_line` 独立可选；非法区间显式报错而非静默给全文。
- `write_file` — 写/追加，自动建目录。
- `str_replace` — 子串替换（单处/全部）；同路径串行化按 `(sandbox.id, path)` 作用域，隔离沙箱互不抢锁。

## Subagent System (`packages/harness/deerflow/subagents/`)

- **内置**: `general-purpose`（除 task 外全工具）、`bash`（命令专家）。单调度线程池 `_scheduler_pool`(3)（`_execution_pool` 已在上游容量模型改造中移除）。`MAX_CONCURRENT_SUBAGENTS = 3`/响应 + `DEFAULT_MAX_TOTAL_SUBAGENTS = 12`/会话（SubagentLimitMiddleware 在 after_model 截断），15 分钟超时。
- **流程**: `task()` → `SubagentExecutor` → 后台线程 → 5s 轮询 → SSE → 结果。**thread_id fail-fast**（P2-1）：runtime context/config 都无 thread_id 时拒绝委托并返回说明文案（`NO_THREAD_ID_DELEGATION_MESSAGE`），生产 run worker 无条件注入。
- **Sandbox Release**（P3-1）：子代理链装配 `release_on_exit=False`——子代理经 task_tool 透传继承父 run 沙箱，退出释放会拆掉父容器（连带温池记账）；父自己的 after_agent 做最终释放。lead 链保持默认 True。
- **委托文案**（P2-3）：主 `<subagent_system>` 保留 DELEGATION CHECK 净收益框架与 2–4/响应 + 12/run 硬帽，但 reminder/thinking 层不再默认劝阻：reminder = "Delegate when the net benefit is clear; otherwise execute directly."，thinking = 单条中性清单——C-04/T21 三重劝阻已退役。
- **轮次预算**（P2-2）：`resolve_recursion_limit` 为 `after_model`→model jump 计有界余量（每个 jump 中间件 `max_retries × 2` 超步），默认链 max_turns=100 → recursion_limit=413；未建模的 jump 形态才 WARNING，覆盖的只 DEBUG。
- **Thinking**（P1-2）：三态 `thinking_enabled`——agent config 显式 > 继承父 run（task_tool 读 run metadata）> 关；`supports_thinking: false` 降级为关 + warn；`reasoning_effort` 仅在 thinking 开且模型声明 `supports_reasoning_effort` 时转发。config.yaml 对 `bash` 显式 `thinking_enabled: false` 压成本。
- **技能注入**（P1-1）：子代理收到 lead 的紧凑技能**索引**（单条 `<skill_system>` SystemMessage，32 技能约 11k 字符）而非每个 SKILL.md 全文（约 285k）；技能经共享沙箱 `read_file` 按需加载。`config.skills` 白名单控制索引范围；`skills: []` 仍短路为零注入；过滤后工具集无 `read_file` 则跳过索引并 warning。
- **事件**: `task_started/running/completed/failed/timed_out` 全部经 `RunJournal.record_subagent_lifecycle` 落 run_events（P3-2；`subagent.task.*`，category `subagent`，取 `runtime.context["__run_journal"]`，缺席静默跳过；行携带 stop_reason/truncated 诚实信号）。

## Tool System (`packages/harness/deerflow/tools/`)

`get_available_tools(groups, include_mcp, model_name, subagent_enabled)` 装配：config 定义工具（`resolve_variable()`）→ MCP 工具（懒加载 + mtime 失效）→ 内置（`present_files` 仅 outputs、`ask_clarification`、`view_image` 仅视觉模型）→ `task`（启用子代理时）。

**Community tools**（`community/`）：tavily（搜索 5 条 + 抓取 4KB）、jina_ai（readability 抓取）、firecrawl（抓取）、image_search（DuckDuckGo）。

**ACP agent tools**：`invoke_acp_agent` 调外部 ACP agent；launcher 必须是真 ACP adapter（裸 `codex` CLI 不是——用 `npx -y @zed-industries/codex-acp` 之类 wrapper）；缺 executable 返回可操作报错而非 `[Errno 2]`；每个 ACP agent 用 per-thread workspace（`{base_dir}/users/{user_id}/threads/{thread_id}/acp-workspace/`），经 `/mnt/acp-workspace/` 只读暴露给 lead（docker 模式 volume 只读挂载，local 模式 tools.py 翻译）。

## MCP System (`packages/harness/deerflow/mcp/`)

- `langchain-mcp-adapters` `MultiServerMCPClient`；**per-server loading**：一个 server 坏了不连坐。
- 懒初始化 + mtime 失效缓存；transport：stdio/SSE/HTTP。
- **Session 池化（仅 stdio）**：`_make_session_pool_tool` 让同线程连续调用复用 session（Playwright 这类有状态 server 保浏览器态）；HTTP/SSE 不池化（anyio cancel scope 必须同任务退出，池化会 RuntimeError）。Interceptor 不受影响。
- OAuth（HTTP/SSE）：`client_credentials`/`refresh_token` 自动刷新注入 Authorization。
- Gateway API 写 extensions_config.json；LangGraph 按 mtime 感知。

## Skills System (`packages/harness/deerflow/skills/`)

- 位置 `skills/{public,custom}/`；`SKILL.md`（frontmatter：name/description/license + 可选 metadata/compatibility/version/author/created_by）。`allowed-tools` **拒绝**——从未生效过（types.py 无此字段、parser 从不读），收了等于宣传不存在的隔离；安装时白名单见 `skills/validation.py`。
- `load_skills()` 递归扫 `skills/{public,custom}`，enabled 状态读 extensions_config.json；启用的技能列进 agent 系统提示（容器路径）；`POST /api/skills/install` 解 .skill ZIP 到 custom/。
- **Imported skills**：`skills/public/open-design/`（od- 前缀）已于 2026-09-09 整体退役删除（69 个 od 技能归零，同步脚本一并删除防复活）；复活路径 = 上游 pinned commit re-sync。

## Model Factory (`packages/harness/deerflow/models/factory.py`)

- `create_chat_model(name, thinking_enabled)` 反射实例化；`thinking_enabled` + per-model `when_thinking_enabled` 覆盖；vLLM 风格 `chat_template_kwargs.enable_thinking` 与旧 `thinking` 别名归一；`supports_vision` 旗标；`$` 前缀值走环境变量；缺 provider 模块时给可操作安装提示。
- **vLLM Provider**（`models/vllm_provider.py`）：`VllmChatModel` 继承 `langchain_openai:ChatOpenAI`，保留 vLLM 非标 `reasoning` 字段（full/delta/tool-call 轮次）。

## IM Channels System (`app/channels/`)

桥接 Feishu/Slack/Telegram/DingTalk 到 agent（经 LangGraph Server 兼容 API）。

**Cross-Path Start Dedup**（2026-09-29 附项）：lark SDK 持有模块级 event loop，同进程同渠道双实例互踩（历史双 "Feishu channel started" → `This event loop is already running`：config.yaml 的 ChannelService + DB 的 UserChannelRegistry 双启动）。现按 `channel_identity`（首个凭证值，如 feishu `app_id`）交叉判同：同身份双启 → 跳过后者 + warning（启动回放跳过）；不同/不可读身份 → 明确拒绝 + 运维指引（`create_or_update` 抛错、`_start_channel` ERROR 拒绝）。全局配置与 DB 实例两条路径都保留（用户拍板），未来飞书用法走用户配置。

**架构**：channel 经 `langgraph-sdk` HTTP client 与 Gateway 通信（同前端）；内部 SDK client 注入进程级 internal auth + 配套 CSRF cookie/header 对，使 Gateway 接受 channel worker 的写请求。

**组件**：`message_bus.py`（异步 pub/sub）/ `store.py`（`channel:chat_id[:topic_id]` → thread_id JSON 持久化）/ `manager.py`（分发器）/ `base.py`（Channel 抽象）/ `service.py`（生命周期）/ 各平台实现（feishu 原地 patch 同一张运行卡片；dingtalk 可选 AI Card 流式）。

**消息流**：平台 → Channel → MessageBus → Manager 查/建 thread → Feishu 用 `runs.stream()` 增量更新卡片、Slack/Telegram 用 `runs.wait()`；命令（/new /status /models /memory /help）本地处理或查 Gateway。

**配置**（`config.yaml` → `channels`）：`langgraph_url`（默认 `http://localhost:8001/api`）、`gateway_url`（默认 `http://localhost:8001`）；Docker Compose 里 channels 在 gateway 容器内，用 `http://gateway:8001[/api]` 或 `DEER_FLOW_CHANNELS_*` env。各渠道凭证字段见 config.example.yaml。

## Memory System (`packages/harness/deerflow/agents/memory/`)

- `updater.py` LLM 抽取更新 + 空白归一化去重 + 原子写；`queue.py` 防抖队列（per-thread 去重，enqueue 时捕获 user_id 跨 Timer 边界；lifespan shutdown 有界 daemon 线程 flush，否则每次重启丢一个防抖窗的记忆）；`scope.py` 持久性闸门 `is_durable_fact`（拒线程级产物——路径/产出文件名/"任务完成"叙述，这些抽取器打分反而 0.9+ 会挤掉真偏好；`category == "correction"` 豁免；拒绝率 >60% 告警指向抽取 prompt）；`prompt.py` 模板；`storage.py` 按 `(user_id, agent_name)` 键控缓存。
- **Per-User Isolation**：`{base_dir}/users/{user_id}/memory.json` + per-agent `agents/{agent_name}/memory.json`；`get_effective_user_id()` 解析，no-auth 默认 `"default"`；绝对 `storage_path` 退出隔离。迁移：`PYTHONPATH=. python scripts/migrate_user_isolation.py [--dry-run]`。
- **流程**：MemoryMiddleware 过滤（用户输入 + 最终 AI 回复）→ 防抖入队 → 后台线程 LLM 抽取 → 原子落盘 → 下次交互注入 facts + context 到 `<memory>`——facts 无数量帽，按 confidence 排序、在 `max_injection_tokens`（默认 2000）token 预算内装填（`format_memory_for_injection`）。
- **配置**：`enabled`/`injection_enabled`/`storage_path`/`debounce_seconds`(30)/`model_name`/`max_facts`(100)/`fact_confidence_threshold`(0.7)/`max_injection_tokens`(2000)。
- 回归覆盖：`tests/test_memory_updater.py`。

## Reflection System (`packages/harness/deerflow/reflection/`)

`resolve_variable(path)` / `resolve_class(path, base_class)`——`module.path:variable_name` 反射。

## Config Schema 补充（`config.yaml` 关键段）

- `models[]`：`use` 类路径、`supports_thinking`/`supports_vision`、provider 字段；vLLM 推理模型用 `deerflow.models.vllm_provider:VllmChatModel`。
- `tools[]` / `tool_groups[]`；`skills.path`/`skills.container_path`。
- `checkpoint_prune`（2026-09-02 A1）：`enabled`/`keep_per_thread`（null=可重生窗口，**调低它是行为变更**）/`daily_hour`/`batch_rows`；lifespan 后台任务（仅 sqlite），跳过在途 run 线程，分批事务删除，失败仅 warn；DELETE 不缩文件——回收靠单独的 `VACUUM INTO`；CLI：`uv run python -m deerflow.runtime.checkpointer.prune --db ... [--apply]`。
- `title` / `summarization`（含反循环复用闸 `resummarize_min_new_messages`(20)/`resummarize_min_new_tokens`(30000)，双 0=每次重摘要；`trim_tokens_to_summarize` 30000；`summary_prompt` null=DeerFlow 默认带 WORKING STATE 节）。
- `max_recursion_limit`：服务端钳制客户端 run recursion_limit（默认 2500；前端发 2500；客户端不发时服务端默认 100）。GraphRecursionError 在 run worker 转可续作错误（"回复「继续」"）。
- `slow_llm_warn_seconds`（90；`llm_slow` 事件 + run 行计数）。
- `empty_response_*`（2026-09-23 批 1）：`empty_response_max_retries`(3)/`empty_response_cooldown_base_seconds`(2.0)/`empty_response_cooldown_max_seconds`(8.0)/`empty_response_trim_on_retry`(true)。
- `sandbox_failure_circuit_breaker_threshold`（5）：连续 `[SANDBOX_DEAD]` 命中熔断（`SandboxCircuitBreakerError` 为 GraphBubbleUp 子类）。
- `disk_min_free_bytes_block`/`disk_warn_free_ratio`：磁盘准入闸（`DiskAdmissionBlocked`，GraphBubbleUp 子类——满载落 run error 而非无限 bash 重试）；docker/aio 模式 run 启动与沙箱创建时强制，local 模式仅 WARN；生产 5GiB/测试机 2GiB。
- `token_budget_warn_tokens`：按任务类型（default/ppt-master/course-video → 12M）warn-only；经 `GET /api/models` 暴露给前端；sed 施加脚本 `scripts/apply_config_increments_2026-09-23.sh`（--target prod|test，--dry-run，幂等，GNU/BSD 兼容）。
- `subagents.enabled` 总开关；`subagents.agents.<name>.thinking_enabled` 三态（缺省=继承父 run，非纯布尔）。
- `extensions_config.json`：`mcpServers`（enabled/type/command/args/env/url/headers/oauth/description）+ `skills`（name → enabled）。两者都可运行期经 Gateway API / DeerFlowClient 改。

## Embedded Client (`packages/harness/deerflow/client.py`)

`DeerFlowClient` 免 HTTP 直用全部能力，返回类型与 Gateway API schema 对齐（HTTP/embedded 消费代码一致）。共享 config 与数据目录，无 FastAPI 依赖。

- `chat(message, thread_id)` 同步聚合；`stream()` 订 `stream_mode=["values","messages","custom"]` 产 StreamEvent（values=全量快照不重复合成 AI 文本；messages-tuple=增量按 id 拼接；custom=StreamWriter 转发；end=携带按 message id 去重的累计 usage）。
- agent 懒创建（`create_agent()` + `_build_middlewares()`，同 `make_lead_agent`）；`checkpointer` 参数支持跨轮持久化；`reset_agent()` 强制重建（记忆/技能变更后）。
- 设计全貌见 `docs/STREAMING.md`（为何 Gateway 与 Client 双路径、stream_mode 语义、per-id 去重不变量、回归策略）。
- Gateway 等价方法：models/MCP/skills/memory/uploads/artifacts 一一对应；差异：upload 收本地 Path（拒目录）且文档转换复用单 worker；artifact 返 `(bytes, mime_type)`；thread 目录清理只有 Gateway 路由有；`update_mcp_config()`/`update_skill()` 自动失效缓存 agent。
- **Tests**：`tests/test_client.py`（百余例，含 `TestGatewayConformance`——client 输出过 Gateway Pydantic 模型校验防漂移）、`tests/test_client_live.py`（真实 LLM 付费调用，显式 opt-in：`RUN_LIVE=1` + config.yaml，默认全跑不触发）。

## Key Features 补充

- **File Upload**：`POST /api/threads/{id}/uploads`；1MB chunk 流式落盘再同步沙箱；单文件 2GiB、单线程 10GiB、自动转换上限 50MiB（config 可调）；PDF/PPT/Excel/Word 经 markitdown；`_make_file_sandbox_readable/writable` 管沙箱权限；按 user 隔离的线程目录存储。设计详见 `../docs/plans/archive/` 的 large-file-upload 计划（2026-09-30 已归档）。
- **Plan Mode**：`config.configurable.is_plan_mode` 开 TodoList middleware；详见 `docs/plan_mode_usage.md`。
- **Context Summarization**：见 `docs/summarization.md`。
- **Vision**：`supports_vision: true` 时 ViewImageMiddleware 只在**推理窗口**注入图像（不落 state/checkpoint）：K=2 最近视图 base64 + 旧视图文本占位；序列化层（`serialize_channel_values_for_api`）对 `hide_from_ui` 消息剥 base64 兜底。
