# 首页六卡媒体计量与计价接入实施计划

> **状态**：已执行（2026-11-24；Task 0–5 全部勾选；PR 1 #50 → PR 2 #51 → PR 3 #52 串行合入 main `bbb20855`，合入后独立终审（无 Critical）的修复 PR #53 与延后项加固 PR #54 合入 `436de16c`；测试服 gateway `v3-20261124c` 已部署、`DEER_FLOW_MEDIA_USAGE_LEDGER=1`、六卡真实调用与回滚演练通过、修复后复测通过；changelog：`docs/changelog/changelog-2026-11-23-six-core-media-usage-metering.md`；未决：供应商账单未对账（`SUPPLIER_RECONCILIATION_NOT_VERIFIED`），见 findings-backlog）；定稿日 2026-11-23；基准 commit `52c316763dc15e60649f1dc6eb0faf52b50013d4`。
> **TL;DR**：只覆盖首页六张卡片的新任务，按“直接计价修复 → 休眠账本基座 → 技能发射接入”三个串行 PR 实施。Agent 媒体事件追加到线程工作区 `.usage/media-usage.jsonl`（生产仅支持本地 Aio 容器沙箱），worker 在 run 结束后读取并按当前线程/用户落库；不扫 bash 回显、不依赖 RunJournal；复用现有表和价目，不改 schema、不回填历史账。防护只做保证功能可用的最小底线，不新增 CI 工作流；Writer/ViMax/语音克隆等非首页能力不要求统计。
> **For agentic workers：** REQUIRED SUB-SKILL：用户批准后使用 `executing-plans` 单执行者按 Task 0→5 串行执行并逐项勾选；实现先写真实失败测试，再写最小实现。三个 PR 严格按 PR 1→2→3 合入，禁止并行派发。状态只修改顶部原行，依次使用 `未执行（执行中：直接计价）`、`未执行（执行中：账本基座）`、`未执行（执行中：技能接入）`，三条 PR 都合入后才改为 `已执行`，不得另起状态头。

**Goal：** 让 course-video、creative-studio、ppt-master、podcast-studio、AI 生图、AI 生视频六类首页新任务的生图、配音、视频调用，以正确的用户、任务、次数和计量维度进入现有媒体费用核算。展示值是**当前可计价记录的估算下界，不是供应商正式账单**；无法可靠计价的调用继续明确为未核算，不伪造成 0 或完整覆盖率。Agent 侧事件随 run 结束入库；run 结束后才完成的后台调用要等同一线程下一次 run 结束才入库，没有后续 run 则不入库（已知限制，不宣称 nohup 完整覆盖）。

**Architecture：** 保留供应商、技能流程、`external_api_calls` 和 `GET /api/usage`。四类 Agent 的脚本只把最小计量事件追加到线程账本；run 结束后（`publish_end` 之后）worker 调用可选 sink，sink 读取账本尾部窗口，绑定当前 `thread_id/user_id/agent_name` 后顺序写入现有表，不改 `llm_usage_sink.py`。两张直接媒体卡继续使用 throttle，只补 usage 字段与估算回退。沙箱不访问数据库、不获得 internal token、不新增内部 API；采集不经过 stdout/stderr、`sandbox/tools.py` 或 `RunJournal`。

**Tech Stack：** Python 3.12（backend）、Python 3.10（沙箱脚本兼容上限）、TypeScript/Node `node:test`、LangGraph worker、SQLAlchemy、现有 `external_api_calls`/`media_pricing`、pytest；不新增生产依赖、数据库列、前端组件或 `config_version`。

**Spec / 证据来源：** 本文件是第一实施契约。现状依据为六卡技能脚本、`backend/app/gateway/_shared/`、`backend/packages/harness/deerflow/{runtime,subagents}/` 与 2026-11-23 用量页截图；截图只证明 `media_cost=null`，不得据此推断历史任务的具体缺失字段，历史任务不回填。

## 一、范围、现状与决策

### 1.1 六卡计费面

| 首页卡片 | 当前实际媒体路径 | 本批目标 |
| --- | --- | --- |
| course-video | `auto-video-flow` → 复制脚本及同目录 `_doubao_tts.py`，逐句调用、无单句字数上限；技能规定批量 TTS 用 nohup 后台 + 同 run 轮询 | 在共享 helper 成功返回时追加普通/字幕 TTS 事件，字数准确；缓存不重复计量 |
| creative-studio | 各视觉技能 → `trainerai-imagine` → 360 Seedream（`--model`/`QIHU_IMAGE_MODEL` 可选非默认 Seedream） | 每次成功生成/编辑一张图写一条事件并记录真实模型，批量并发不丢不重 |
| ppt-master | `image_backends/backend_qihu360.py`（只支持默认模型）、`tts_backends/backend_trainerai.py` | 每张 360 生图、每个 TTS 分片分别计量；其他 vendored provider 不纳入当前生产通道 |
| podcast-studio | `podcast_studio.py` → 同目录 `_doubao_tts.py` | 共享 helper 记录每个真实合成分段；已有分段缓存不重复计量 |
| AI 生图 | `ai_image` → gateway throttle → `external_api_calls` | 同时验证 per-image（seedream 系）与 per-kilo-token（gpt-image-2）两种价目单位 |
| AI 生视频 | `ai_video` → gateway throttle → `external_api_calls` | usage 传播 completion/total；估算 completion 优先，缺 token 时不以 duration 硬猜 |

AI 生图/生视频任务的旁白经 `orchestration.tts.synthesize` 默认走字幕版 gateway TTS（`DOUBAO_WORD_SRT` 缺省开启），该路径目前只记 status、没有 `model/char_count`；PR 1 补齐后这部分旁白才可按千字计价，所以字幕 TTS 的修复对这两张卡有直接意义。

本地 Remotion、FFmpeg、SVG/图表、文件转换、下载和普通 IO 不纳入供应商媒体费。Writer、ViMax、语音克隆、voice-twin PPT 恢复、其他 Agent/provider 和非首页历史任务不在验收范围；共享代码的自然复用不得写成这些能力已完整覆盖。

### 1.2 已确认缺口与评审修正

- 四类 Agent 在沙箱内直接请求供应商，gateway 内现有 `record_external_api_call()` 看不到这些调用；该函数还吞异常、无法区分重复与写失败，沙箱路径改用独立入口。
- stdout 采集会漏掉技能规定的 nohup 日志重定向、管道/超时、子代理和长输出，并挤占模型上下文，因此改为**账本文件**。
- 字幕版 gateway TTS 缺 `model/char_count`；throttle 和估算器只认 `total_tokens`，仅回传 `completion_tokens` 的成功视频会漏价。
- `media_{run_id}_{event_id}` 会超过 `call_id String(64)`（SQLite 不校验长度会掩盖该问题）；改为 thread 绑定的定长摘要。
- UI 发起的 run 的 `assistant_id` 恒为 `lead_agent`，真实 Agent 名在 `context.agent_name`；sink 若只看 `assistant_id` 会拒掉全部事件。
- 普通 LocalSandbox 只改写命令文本里的虚拟路径（`sandbox/tools.py` 的 `replace_virtual_paths_in_command`），脚本内部打开的 `/mnt/user-data/...` 不会被改写；部署手册的默认 LocalSandbox 也不提供 bash。账本方案只在本地 Aio 容器（工作区 bind-mount）成立。
- 媒体计价支持模型级价目（`resolve_media_price`：模型级优先、provider 级兜底）；360 生图若把非默认 Seedream 一律写成默认模型，会绕开模型级价目并让按模型对账失真。
- 个人用量只对已计价行求和，部分计价不能仅靠 `media_cost=null` 表达；补 `unpriced_media_calls` 表示**已知未计价行数**，未埋点通道不在该计数内。

### 1.3 评审决策记录（含被否方案）

| 决策点 | 定稿答案 |
| --- | --- |
| 采集通道 | **采用账本文件**。否掉 stdout/stderr：nohup、管道、超时、子代理、长输出和机读 JSON stdout 均不可靠；否掉迁移供应商调用或新增内部计量 API：改动面大，且引入沙箱凭据与跨层授权问题。 |
| 埋点层级 | TTS 下沉到全部 **5 份** `_doubao_tts.py`（md5 一致，现有守护已覆盖），避免 agent 复制/改名模板绕过；PPT 的两个 backend 共用新增的 `scripts/media_usage_ledger.py`；图片在 `qihu360.ts`。其他 Agent 虽共享文件，但 sink 只接受核心 Agent。 |
| 身份与名单 | Agent 名按 `context.agent_name → configurable.agent_name → assistant_id` 取（与 `worker.py` 现有取法一致），经 `canonical_agent_capability` 后限定在四个 Agent；不维护第二份六卡名单，不按脚本名证明来源。 |
| 读取与去重 | run 结束后读账本**尾部 1 MiB**，先分批查已存在 `call_id`，再按文件顺序从旧到新提交新行，单次最多 1000 条，未写的仍在窗口内则下次续写。不做游标、不做后台扫描：单行约 120–150 B，窗口约容纳 7000 条，实际单 run 只有数百条；两次 drain 之间新增超过窗口时窗口外事件会丢，靠 `ledger window overflow` WARNING 发现并列为已知限制，不宣称绝对不丢。`call_id = "media_" + sha256(f"{thread_id}:{event_id}")[:40]`（46 字符），跨 run 幂等且不超 64。 |
| 事件契约 | 只含 `event_id/kind/with_timestamps/char_count/model/width/height/elapsed_ms`。image 的 `model` 必填，取发射端本次实际请求的模型（须含 `seedream`、≤128 字符），原样记入 metadata，计价走现有 `resolve_media_price`，不改写为默认模型；TTS 的 model 由服务端固定为 `seed-tts-2.0`（五份 helper 与 PPT backend 均为常量）。provider/method 由服务端映射，未知字段忽略，禁止 amount。上界只防坏数据，不代表供应商单次上限：单行 ≤4096 B、TTS `char_count 1..100000`（课程视频逐句调用、无单句上限，不能用播客 300/PPT 1000 推上界）。 |
| 防护边界 | 只守三条底线：读取不阻塞 gateway（`O_NOFOLLOW\|O_NONBLOCK` 且仅普通文件、窗口限长）；发射器永不抛错，且账本对跨 uid 的 gateway 可读（`mode` 参数受 umask 影响，创建后显式 chmod 目录 0o755、文件 0o644，同 `paths.py` 的 `ensure_thread_dirs` 先例）；错误日志不带行内容。**不做**签名/HMAC、对抗性 fuzz、新增 CI 工作流；TS 发射由 `main.test.ts`（本地）与 Task 5 真实验收覆盖，不称作 CI 守护。 |
| 计量时机 | 供应商明确成功（TTS 返回音频；360 返回含图的 200）即记录；本地下载/写盘失败不抹去消耗，HTTP/业务失败不估算。 |
| token 与金额 | per-kilo-token 依次 completion→total，不相加、不用 duration 猜 token；沙箱只做估算，拒绝 amount。 |
| 范围与历史 | 生产只支持本地 Aio 容器（沙箱 `/mnt/user-data/workspace` 即宿主 `sandbox_work_dir`）；普通 LocalSandbox 只在测试里经 `DEER_FLOW_MEDIA_LEDGER_DIR` 使用，provisioner 远端不在本批，Task 0 核对到这两种形态即在 PR 1 后暂停。只对部署后的新调用生效，不回填；上线后平台媒体总额可能因补齐真实花费而上升，changelog 必须明示。 |
| 验证成本 | 实现期离线为主；用户批准部署和必要消费后，再做六卡及 nohup 的最小真实验收与供应商账单对账。 |

## 二、串行 PR 路径表

| PR | 允许路径（allow） | 禁改路径（ban） | 共享基座触达 |
| --- | --- | --- | --- |
| **PR 1 / 直接媒体计价先行** | `backend/app/gateway/video_gen/providers/throttle.py`、`backend/app/gateway/_shared/media_pricing_resolver.py`、`backend/app/gateway/voice_twin/doubao_tts_client.py`、`backend/tests/test_{throttle_amount_passthrough,media_pricing,doubao_tts_client,personal_usage_video}.py`、计划/changelog/findings | skills/harness/frontend、Writer/ViMax/语音克隆实现、价目迁移、schema、`config*.yaml`、其他视频 provider | **需规划侧独占** throttle 与估算器；只验证直接六卡路径 |
| **PR 2 / 休眠账本与 sink 基座** | `backend/packages/harness/deerflow/runtime/media_usage.py`（新增）、`backend/packages/harness/deerflow/runtime/runs/worker.py`、`backend/app/gateway/deps.py`、`backend/app/gateway/_shared/agent_media_usage.py`（新增）、`backend/app/gateway/_shared/personal_usage/{types.py,agent.py,video.py}`、`backend/app/gateway/routers/personal_usage.py`、`backend/tests/test_{agent_media_usage,media_usage_worker}.py`（新增）、相关现有测试（如 `test_personal_usage*.py`）、计划/changelog | 技能发射文件、`sandbox/tools.py`、`runtime/journal.py`、`_shared/llm_usage_sink.py`、frontend、`config*.yaml`、数据库迁移、通用 internal auth、直接 provider | **需规划侧独占** worker finally 与 sink；默认 `DEER_FLOW_MEDIA_USAGE_LEDGER=0` 休眠，不依赖 event_store/RunJournal |
| **PR 3 / 六卡技能发射接入** | 五份 `skills/{public,custom}/**/scripts/_doubao_tts.py`（以 Glob 实测清单为准）、`skills/public/ppt-master/scripts/{media_usage_ledger.py（新增）,image_backends/backend_qihu360.py,tts_backends/backend_trainerai.py}`、`skills/public/ppt-master/TRAINERAI_PATCHES.md`、`skills/public/trainerai-imagine/scripts/{providers/qihu360.ts,main.test.ts}`、`backend/tests/test_{sandbox_media_usage_scripts,core_media_usage_e2e}.py`（新增）、相关现有测试、计划/changelog/findings | `podcast_studio.py`、`generate_audio_template.py`、`trainerai-imagine/scripts/main.ts`、ppt-master `SKILL.md`/SOUL/SEED_VERSION、其他 provider、Writer/ViMax/语音克隆实现、frontend、`config*.yaml` | **需规划侧独占** 五镜像与 vendored PPT 补丁；P35 为当前下一编号，若执行时漂移则顺延并更新计划 |

代码路径重叠表：三个 PR 的产品实现路径互不相交；共享计划、changelog、findings 和证据由同一执行者串行更新，禁止据此并行派发。

## 三、实施任务

### Task 0：漂移检查、服务器基线与执行准备

- [x] **Step 1：检查最新基线。** 用户批准后运行 `git fetch origin main`、`git diff --stat 52c316763dc15e60649f1dc6eb0faf52b50013d4..origin/main`，复核 sink、worker、六卡 provider、五份 `_doubao_tts.py` 和补丁序号；当前 P34 已占用，下一为 P35，漂移时先更新计划。
- [x] **Step 2：建立干净执行 worktree。** 从最新 main 建单一 worktree/分支，将本计划移回 `docs/plans/` 并建立批次 changelog；开始 PR 1 时只把原状态行改为 `未执行（执行中：直接计价）`。测试使用干净 worktree，不继承主 clone 的本地 `config.yaml` 造成的假失败。
- [x] **Step 3：只读核对部署与价目。** 核对数据库类型（SQLite/Postgres）、`sandbox.use` 与实际沙箱形态（本地 Aio 容器 / provisioner 远端 / 普通 LocalSandbox）及 gateway 能否读到线程 workspace、迁移、`seedream-360/image`、`gpt-image-2/image`、`seedance/video`、`doubao_tts/tts` 价目（含 seedream-360 有无模型级行）与实际 `ai_image_provider`，以及 `QIHU_IMAGE_MODEL` 是否覆盖默认模型；不得修改管理员价格。沙箱不是本地 Aio 容器时，PR 1 照常执行，PR 2/3 暂停交用户决定。无法访问服务器时记 `SERVER_BASELINE_NOT_VERIFIED`，按部署手册的 Aio 形态继续、由 Task 5 Step 1 实测兜底，不伪造结果。
- [x] **Step 4：把证据放到执行 worktree 之外。** 固定使用主 clone 的 gitignored 绝对路径 `/Users/littlebear/Documents/SuperTrainerAgent/temp/2026-11-23-six-core-media-usage/`，避免 `git worktree remove` 删除证据；不得提交账号、密钥、签名 URL、运行数据库、真实任务正文或任何 run/thread/任务 ID。
- [x] **Step 5：锁定统一门禁。** 每个 PR 跑定向回归和根 `make check`；backend 全量统一用 `cd backend && make test`，长命令后台执行并在干净 worktree 运行，不再以串行 `uv run pytest tests/ -q` 代替。每个 PR 的 OCR 最多 3 轮 push/评审，逐轮在 PR 评论记录发现与处置；3 轮后仍有 Critical/Important 则停下交用户。

### Task 1 / PR 1：先补齐直接媒体计价

- [x] **Step 1：写字幕 TTS 失败测试。** `test_doubao_tts_client.py` 对 `synthesize_sse_with_timestamps` 的成功/失败记录断言 `model=seed-tts-2.0`、`char_count=len(text)`；普通版不变，失败不估算。
- [x] **Step 2：写 usage 与估算失败测试。** `test_throttle_amount_passthrough.py` 断言 completion/total 都进入 metadata；`test_media_pricing.py` 验证 completion 优先、仅 total 回退、两者皆缺 unpriced、amount 优先，且 completion+total 不相加。
- [x] **Step 3：最小实现。** 字幕版补与普通版一致的 extra；throttle 传播两个 token 字段；`_estimate_call_cost()` 依次读取 completion→total。不得把 duration 当 token 或修改单价。
- [x] **Step 4：参数化验证直接任务。** `test_personal_usage_video.py` 覆盖：seedream/seedream-360 的 per-image、gpt-image-2 的 per-kilo-token；AI 视频只有 completion 时计费、无 token 为 `media_cost=null`；带 `char_count` 的字幕 TTS 行（任务旁白）按千字计入；LLM + 媒体总额和来源。运行上述四个定向文件全绿。
- [x] **Step 5：按 Task 0 统一门禁合入。** 提交 `fix(usage): 补齐直接媒体计价字段`，CI/OCR 无 Critical/Important 后 rebase 合入；随后只把原状态行改为 `未执行（执行中：账本基座）`。findings 中字幕 TTS 项验证后勾掉；ViMax 项保持未勾选并注明“共享计价器 completion 优先已完成，ViMax amount/usage 透传仍不做”。

### Task 2 / PR 2：建立休眠账本、幂等 sink 与部分计价 API

**Interfaces：**

- 账本：沙箱内 `/mnt/user-data/workspace/.usage/media-usage.jsonl`；发射器可用环境变量 `DEER_FLOW_MEDIA_LEDGER_DIR` 覆盖目录（仅测试用；普通 LocalSandbox 不改写脚本内部路径，不在支持范围）。宿主路径 = `get_paths().sandbox_work_dir(thread_id, user_id=user_id) / ".usage" / "media-usage.jsonl"`，`user_id` 取 run 开始时的 `get_effective_user_id()`，与沙箱挂载同一用户目录。WorkspaceInventory 已跳过点目录，不新增可见文件或上下文噪声。
- 事件（每行一个 JSON）：`event_id: str`、`kind ∈ {tts,image}`、`with_timestamps: bool`（仅 tts）、`char_count: int`（tts 必填）、`model: str`（image 必填，须含 `seedream`、≤128 字符）、`width/height: int`（image 可选）、`elapsed_ms: int`（可选）；未知字段忽略。
- 服务端映射：tts → provider `doubao_tts`、model `seed-tts-2.0`、method `synthesize_sse`（`with_timestamps=false`）或 `synthesize_sse_with_timestamps`；image → provider `seedream-360`、model 取事件 `model`（默认 `bytedance/doubao-seedream-5.0-pro` 命中现有别名；其他 Seedream 有模型级行用模型级价，否则按 provider 级兜底）、method `generate_image`；status 固定 `success`。
- 读取底线与上界：单行 ≤4096 B、TTS `char_count ∈ 1..100000`；只读文件尾部 1 MiB（窗口起点落在行中间时丢弃首个半行，末尾无换行的半行留给下次）；用 `O_NOFOLLOW|O_NONBLOCK` 打开并只接受普通文件；按文件顺序从旧到新，单次最多写入 1000 条新事件，未写的仍在窗口内则下次续写；文件大于窗口且窗口内最早一条有效事件尚未入库时 WARNING `ledger window overflow`（窗口外可能有事件丢失）；超限/坏行/不可读计入 error 并 WARNING，日志不含行内容；同批内相同 `event_id` 只算一次。
- 接口：`deerflow.runtime.media_usage.ledger_path(thread_id: str, user_id: str) -> Path`；`read_ledger_events(path: Path) -> LedgerRead`（`events: list[MediaUsageEvent]`、`errors: int`、`truncated: bool`（文件大于窗口）；`MediaUsageEvent` 为含上述事件字段的 frozen dataclass）；`RunContext.media_usage_sink: Callable[..., Awaitable[Any]] | None = None`，调用形态 `await sink(thread_id=..., user_id=..., agent_name=...)`，worker 忽略返回值；`app.gateway._shared.agent_media_usage.drain_agent_media_usage(*, thread_id: str, user_id: str, agent_name: str | None) -> MediaDrainStats`（`written/duplicate/error: int`）。

- [x] **Step 1：写契约与读取失败测试。** 新增 `test_agent_media_usage.py`：合法 TTS/image 事件及服务端映射，image 的非默认 Seedream `model` 原样记录；必填缺失/越界/未知 kind/不含 `seedream` 的 model 计 error；课程长句 `char_count=5000` 合法、`100001` 计 error；未知字段（含 provider/method/amount，以及 tts 事件里的 model）被忽略且不进 metadata；尾部半行不处理；文件大于窗口时 `truncated=True`；symlink、FIFO、超长行不阻塞且计 error（一个用例覆盖）；`call_id` 同 thread+event 跨 run 相同、跨 thread 不同，且 `len(call_id) == 46`（不能只靠 SQLite 证明）。
- [x] **Step 2：写 worker 与落库失败测试。** 新增 `test_media_usage_worker.py`：开关关、sink 为 None、非核心 Agent 时不碰文件系统；Agent 名取法覆盖三种形态——UI 形态（`assistant_id="lead_agent"` + `context.agent_name="course-video"`）被接受、`assistant_id` 直传核心 Agent 被接受、其他 Agent 被拒；drain 在 `publish_end` 之后执行，超时或异常不改变 run 结果；`event_store=None`、`track_token_usage=False`、`SimpleNamespace` 缺新字段（`getattr(..., None)`）仍工作；晚到事件在下一次 run 结束入库，重复读取为 duplicate；单次上限与窗口在测试里调小：超上限时先写旧事件、下次 drain 续写其余，窗口外有未入库事件时打 `ledger window overflow` WARNING；written/duplicate/error 分开计数，`error>0` 打 WARNING。
- [x] **Step 3：实现账本读取与 worker 钩子。** `runtime/media_usage.py` 只负责路径、受限读取、解析与上界，不 import `app.*`。worker 顶部用 `getattr(ctx, "media_usage_sink", None)` 解包并保留原 finally 的 `ctx` 解绑防护；在 `await bridge.publish_end(run_id)` 之后用 `asyncio.wait_for(..., 10)` 调用 sink，异常或超时只记 WARNING，不改变 run 状态。
- [x] **Step 4：实现落库 sink。** `agent_media_usage.py`：先对有效 Agent 名调用 `canonical_agent_capability`，限定在 course-video/creative-studio/ppt-master/podcast-studio，否则直接返回；读取账本后分批（≤500）查已存在 `call_id`，按文件顺序对新事件直接构造 `ExternalApiCallRow`（`task_id=thread_id`，metadata 含映射后的 model、`char_count`/`width`/`height`、`metering_source=sandbox_reported`），每条用独立 session 提交（同 `record_external_api_call`），失败先 rollback 再继续下一条：`IntegrityError` 记 duplicate，其他异常记 error；`truncated` 且窗口最早一条有效事件不在已存在集合中时打 overflow WARNING；结束输出一行汇总。`deps.get_run_context` 仅在 `DEER_FLOW_MEDIA_USAGE_LEDGER=1` 时注入 sink。不改 `llm_usage_sink.py`。
- [x] **Step 5：补部分计价响应。** `_aggregate_media_rows` 额外返回已知 `source=unpriced` 行数，`UsageTask`/HTTP 响应增加必填 `unpriced_media_calls: int`，更新 `agent.py`、`video.py` 两处构造器和现有测试（不要给 dataclass 基类加默认字段，以免 `UsageTaskEntry` 继承顺序出错）。语义写进 `routers/personal_usage.py` 中该字段的 `Field(description=...)`：该字段不含未埋点通道，`media_cost` 是已知下界；API 不新增调用数，调用数只在 DB 与供应商账单侧核对。
- [x] **Step 6：按统一门禁交付休眠基座。** 默认 `DEER_FLOW_MEDIA_USAGE_LEDGER=0` 时不新增采集和落库；`/api/usage` 响应仍会多出 `unpriced_media_calls` 字段（前端按 TS 类型读取，忽略未知字段）；测试显式开启验证。提交 `feat(usage): 建立线程媒体计量账本`，经 CI/OCR 后 rebase 合入，再把原状态行改为 `未执行（执行中：技能接入）`。

### Task 3 / PR 3：在共享 TTS 实现中发射账本事件

**发射规则：** 仅在供应商明确成功后发射；整个发射包在 try/except（Node 为 try/catch）内，永不抛错、不写 stdout/stderr；目录取 `DEER_FLOW_MEDIA_LEDGER_DIR`，缺省 `/mnt/user-data/workspace/.usage`；每行 ≤4096 B，`event_id` 为 32 位随机 hex。`mode` 参数受 umask 影响，每次写入都显式设权限（失败忽略），保证跨 uid 的 gateway 可读：

- Python（五份 `_doubao_tts.py`、PPT 共用 helper）：`os.makedirs(dir, exist_ok=True)` 后 `os.chmod(dir, 0o755)`；`os.open(path, O_APPEND|O_CREAT|O_WRONLY, 0o644)` 后 `os.fchmod(fd, 0o644)`，单次 `os.write` 一行；只用 Python 3.10 可用的 stdlib API（不用 `datetime.UTC` 等 3.11+ 接口）。
- Node（`qihu360.ts`）：同步 `mkdirSync({recursive: true})` + `chmodSync` + `openSync(path, "a", 0o644)` + `fchmodSync` + 单次 `writeSync` + `closeSync`，不留未 await 的 Promise，避免未处理的 rejection 让生图进程退出。

- [x] **Step 1：写五镜像发射失败测试。** `test_sandbox_media_usage_scripts.py` 以环境变量把账本指向 `tmp_path`，用假 `urlopen` 驱动 `_doubao_tts.synthesize()` / `synthesize_with_timestamps()`：成功各写一行（`kind=tts`、`with_timestamps` 正确、`char_count=len(text)`）；重试只写最终成功一次；失败不写；供应商成功但调用方写盘失败仍有账本行；账本目录不可写/不存在时仍返回音频；umask 077 下 `.usage` 目录为 `0o755`、账本文件为 `0o644`。五份副本沿用现有 md5 一致守护。
- [x] **Step 2：在五份 `_doubao_tts.py` 同步实现。** 在 `synthesize()`/`synthesize_with_timestamps()` 成功返回点按发射规则写一行；不改模板、不依赖脚本名；corporate-video/tech-explainer-video 因 md5 守护同步修改，非核心 Agent 由 sink 排除；顺带把头部 MIRROR 注释的 “currently 4 copies” 改为 5 份并补上 podcast-studio。
- [x] **Step 3：接入 PPT TTS 分片。** 新增 `ppt-master/scripts/media_usage_ledger.py`（遵守发射规则的共用 helper，两个 backend 共用）；`backend_trainerai.py` 在每个真实成功分片写一条事件，多分片不合并、重试不重复；`TRAINERAI_PATCHES.md` 追加 P35 行登记新 helper 与两处补丁，不改 frontmatter、SOUL、SEED_VERSION 或其他 provider。
- [x] **Step 4：验证后台与并发。** 子进程调用 `synthesize()`（stdout/stderr 重定向到日志文件，模拟 nohup），断言账本有行且日志无任何计量标记；子进程里用 `sitecustomize` 替换 `urllib.request.urlopen`（`_ENDPOINT` 是写死常量，无环境覆盖）；两个进程并发各写 N 行，行数齐全且逐行可解析。运行 `test_sandbox_media_usage_scripts.py`、`test_skill_script_copies.py`、`test_ppt_master_tts_trainerai.py` 与 podcast 相关回归。

### Task 4 / PR 3：接入生图并完成离线贯通测试

- [x] **Step 1：接入 creative-studio 与 PPT 生图。** `trainerai-imagine/providers/qihu360.ts` 在 360 响应含 `data[0].url|b64_json` 之后、CDN 下载之前写一条 image 事件（`event_id/kind/model/width/height/elapsed_ms`，`model` 为 `generateImage()` 本次实际下发的模型）；`image_backends/backend_qihu360.py` 在同一位置经 `media_usage_ledger.py` 写入（该 backend 只支持默认模型）。200 但无图、HTTP 错误不写；CDN 下载失败仍保留已写事件；其他 provider 不伪造 360 事件。
- [x] **Step 2：写 TS 本地行为测试。** `main.test.ts` 增加 qihu360 用例：成功写一行且字段集仅含契约字段、`kind === "image"`；`--model` 指定非默认 Seedream 时事件 `model` 与之相同；200 无图不写；下载失败仍保留；账本目录不可写时生图照常返回；umask 077 下文件为 `0o644`。按现有本地方式运行，不新增 CI 步骤。
- [x] **Step 3：运行 PR 3 回归。** `test_sandbox_media_usage_scripts.py`、`test_ppt_master_tts_trainerai.py`、`test_ppt_master_image_trainerai.py`、`test_skill_script_copies.py`、PPT vendored 回归；在 `skills/public/trainerai-imagine` 运行 `npx --no-install tsx --test scripts/main.test.ts scripts/build-batch.test.ts`。P35 补丁表保持既有格式缺陷，不顺手整理。
- [x] **Step 4：新增显式离线贯通测试。** 新建 `backend/tests/test_core_media_usage_e2e.py` 并标 `@pytest.mark.e2e`：podcast/course-video 经 `_doubao_tts.py`、PPT 经 `backend_trainerai.py`/`backend_qihu360.py`（假供应商）写账本，再经 `drain_agent_media_usage` → SQLite → `GET /api/usage`；直接两卡经 provider 夹具到同一接口。creative-studio 只用 TS 同形账本行做契约贯通，不单独证明其端到端（真实 TS 由 `main.test.ts` 与 Task 5 验证）。断言归属、DB 调用数、费用公式、`unpriced_media_calls`、`cost_source`；非核心 Agent 的账本不入库；重复读取不重复计费。
- [x] **Step 5：按统一门禁合入 PR 3。** 提交 `feat(usage): 接入核心技能媒体计量`，CI/OCR 无 Critical/Important 后 rebase 合入。开启开关仅在部署阶段执行，三条 PR 合入本身不宣称测试服已验收。

### Task 5：真实验收、回滚与收尾

- [x] **Step 1：部署后做六卡最小真实验收。** 用户批准部署和必要消费后，在测试服设置 `DEER_FLOW_MEDIA_USAGE_LEDGER=1` 并重启 gateway，分别触发 course-video 一段 TTS、creative-studio 一张图、PPT 一张图加一段旁白、podcast 一个短分段、AI 生图一张、AI 生视频一个最短镜头。逐项对照供应商记录、`external_api_calls`、`unpriced_media_calls` 与页面，验证任务归属、次数和当前价目；同时确认 gateway 能读到沙箱容器写出的账本（跨 uid 权限），UI 实际路径（`lead_agent` + `context.agent_name`）被接受，creative-studio 事件的 `model` 与实际下发模型一致。
- [x] **Step 2：补 nohup 批量与供应商账单覆盖率。** 按技能正式指引（nohup 后台 + 同 run 轮询）跑一次 course-video 批量 TTS，确认随该 run 结束入库；再做一次「run 先结束、后台后完成」，确认在同一线程下一次 run 结束入库。nohup 覆盖只按这一口径表述，没有后续 run 的后台事件不入库。尽可能用供应商账单导出核对成功媒体调用数和金额，计算已计价覆盖率；拿不到账单时标 `SUPPLIER_RECONCILIATION_NOT_VERIFIED`，不能用页面自证完整。
- [x] **Step 3：执行回滚演练。** 将开关置 0 并重启 gateway，确认停止新账本入库且制作流程不受影响，已有行保留；重新置 1 后确认关闭期间积累的事件被补入（`created_at` 为入库时间）。清理仅在用户另行批准后，先备份再按 `metering_source=sandbox_reported` 定向处理，不自动删除历史数据。
- [x] **Step 4：运行最终门禁并记录跳升原因。** 在最新 main 的干净 worktree 后台执行 `cd backend && make test`，再跑根 `make check`、Node 补充测试、E2E 和 docs convention；检查 `git status`，删除临时日志，证据仅保留在主 clone 的 gitignored `temp/`。changelog 明示“首次纳入沙箱真实花费，平台媒体总额可能跳升”，逐项记录 RED→GREEN、CI/OCR、服务器查询和实际供应商消费。
- [x] **Step 5：完成文档。** findings 保留 ViMax/其他非首页项未勾选，未覆盖 provider 或无 token 的情况登记而非清零；changelog「验收记录」逐行写“命令 → 实际关键输出”。三条 PR 合入且验收完成后，只把顶部原状态行改为 `已执行`；未授权部署/消费或缺少账单时相关项保持未勾选，不宣称完整验收。

## 四、回滚与已知限制

- 开关 `DEER_FLOW_MEDIA_USAGE_LEDGER` 是 gateway 环境变量，翻转需重启 gateway；默认 0，只控制 sink 注入，不改 `config_version`。PR 3 的发射文件只追加本地账本，开关关闭时照写但无人读取。
- 置 0 即停止新增计量，不影响制作，不删已有行；重新置 1 靠 `call_id` 幂等续读，**关闭期间积累的事件会在重新开启后补入（受 1 MiB 窗口限制），`created_at` 为入库时间**，changelog 注明，建议 PR 3 部署与开关开启放在同一发布窗口。
- 数据清退不是默认回滚动作；确需清理时先备份，仅针对 `metering_source=sandbox_reported`，不得按 run_id 或整任务粗删。
- 已知限制（本批不解决）：
  - run 结束后才完成的后台调用，要等同一线程下一次 run 结束才入库；没有后续 run 则一直留在账本里不入库。
  - 晚到或开关关闭期间积累的事件，`created_at` 是入库时间，跨天入库会计入入库当天的用量。
  - 两次 drain 之间新增超过 1 MiB（约 7000 条事件）时窗口外的事件丢失，只能靠 `ledger window overflow` WARNING 发现；账本只追加、不轮转，读取成本恒定，只多占线程磁盘。
  - 生产仅支持本地 Aio 容器；未埋点通道/provider 不出现在统计中；agent 删除账本则对应事件丢失。
- 账本读取、解析、数据库错误只影响计量，不阻断制作；任何 error 必须 WARNING，不与 duplicate 合并。

## 五、全局 Review Focus

1. **只靠账本采集**：nohup、子代理、管道、长输出不经 stdout/RunJournal，由 Task 3 Step 4 与 Task 4 Step 4 钉死；nohup 的入库口径是同 run 完成或同线程下一次 run 结束，由 Task 5 Step 2 实测。
2. **UI 身份**：run 的 `assistant_id` 恒为 `lead_agent`，Agent 名必须按 context→configurable→assistant_id 取，由 Task 2 Step 2 钉死。
3. **跨 run 幂等与长度**：`call_id` 由 thread+event 派生、46 字符，重复读取为 duplicate，由 Task 2 Step 1 钉死。
4. **写失败可见且不拖慢用户**：written/duplicate/error 分开，error 必 WARNING；drain 在 `publish_end` 之后且有时限，由 Task 2 Step 2–4 钉死。
5. **发射器永不影响制作**：发射失败不改变 TTS/生图结果，umask 077 下账本仍跨 uid 可读，仅用 Python 3.10 API，Node 端无未处理 rejection，由 Task 3 Step 1 与 Task 4 Step 2 钉死、Task 5 Step 1 在真实沙箱兜底。
6. **部分计价不是完整账单**：`unpriced_media_calls` 只表示已知未计价行；AI 生图两种价目单位与带旁白的 AI 任务都必须验收，由 Task 1 Step 4 与 Task 2 Step 5 钉死。
7. **真实模型**：image 事件记录实际下发的 Seedream 模型，不改写为默认，计价走现有模型级/provider 级解析，由 Task 2 Step 1 与 Task 4 Step 2 钉死。

## 六、完成定义

- 三个 PR 依次合入：直接计价先独立生效，账本基座默认休眠，技能发射最后接入；Writer/ViMax/语音克隆等非首页能力不做覆盖率承诺。
- 四类 Agent 的成功生图/TTS 通过账本落到当前用户的 `external_api_calls.task_id`（生产限本地 Aio 容器）；nohup、子代理、复制/改名模板和长输出不再依赖 bash 回显，缓存和重复读取不重复计费；同 run 轮询完成的 nohup 随该 run 入库，run 结束后才完成的后台调用在同一线程下一次 run 结束入库，没有后续 run 则不入库（已知限制）。
- `call_id` 跨 run 幂等且长度安全（46），written/duplicate/error 可见；开关可回滚且不影响制作，平台总额跳升有 changelog 说明。
- course-video/podcast 按真实字数计价（长句不被上界误拒），creative-studio 按实际 360 生图张数与真实模型计价，PPT 每张图和每个 TTS 分片分别计价；AI 生图两种价目单位均覆盖，AI 视频只有 completion 也可计价，字幕版旁白 TTS 具备 `model/char_count`。
- 个人用量展示是已知估算下界，`unpriced_media_calls` 语义准确，未知费用不写成 0；DB 调用数与供应商记录可对照，API 不虚构调用数。
- 文档、changelog、findings 与脱敏证据反映真实验收；缺 token、缺价目、缺供应商账单或未授权部署/消费时，不宣称完整核算，历史数据不回填。
