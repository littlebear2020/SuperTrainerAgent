# 首页六卡媒体计量与计价接入

> **TL;DR**：本批让首页六张卡片（course-video / creative-studio / ppt-master / podcast-studio / AI 生图 / AI 生视频）新任务的生图、配音、视频调用以正确的用户、任务、次数与计量维度进入现有 `external_api_calls` 媒体费用核算；关键决策 = 先修直接计价（completion token 回退、字幕版 TTS 补 `model/char_count`），再建默认休眠的线程账本 sink（`DEER_FLOW_MEDIA_USAGE_LEDGER=0`），最后在五份共享 `_doubao_tts.py`、PPT 两个 backend 与 `qihu360.ts` 发射事件——不改 schema、不回填历史账、不扫 bash 回显；影响面 = `backend/app/gateway/{_shared,video_gen/providers,voice_twin,routers}`、`backend/packages/harness/deerflow/runtime/`、`skills/` 五份镜像与 vendored PPT 补丁 P35，部署需在开关置 1 后重建 gateway。

## 执行范围

- 计划：[`docs/plans/2026-11-23-six-core-media-usage-metering-plan.md`](../plans/2026-11-23-six-core-media-usage-metering-plan.md)，三个串行 PR（直接计价 → 账本基座 → 技能接入），Task 0–5。
- 展示值是**当前可计价记录的估算下界，不是供应商正式账单**；`unpriced_media_calls` 只表示已知未计价行数，未埋点通道不在该计数内。
- 证据保存在主 clone 的 gitignored `temp/2026-11-23-six-core-media-usage/`，不提交账号、密钥、真实主机、任务 ID 或用户数据。

## 平台媒体总额可能跳升

首次纳入四类 Agent 沙箱内直接请求供应商的真实花费（此前 gateway 完全看不到），上线并打开开关后平台媒体总额与个人用量**可能上升**——这是补齐真实花费，不是价格变动；管理员价目未改。

## 改动内容

### PR 1 / 直接媒体计价先行（`fix(usage): 补齐直接媒体计价字段`）

- `backend/app/gateway/_shared/media_pricing_resolver.py`：`_estimate_call_cost()` 的 per_kilo_token 依次读取 `completion_tokens → total_tokens`，取第一个可用值，不相加、不用 duration 猜 token；非数字坏值按缺失处理。修前只认 `total_tokens`，仅回传 completion 的成功视频行整体漏成 unpriced。
- `backend/app/gateway/video_gen/providers/throttle.py`：`_build_extra_from_ref()` 在 `total_tokens` 之外透传 `completion_tokens`（稀疏写入，未给则不写键），image/video 两条成功路径共用。
- `backend/app/gateway/voice_twin/doubao_tts_client.py`：字幕版 `synthesize_sse_with_timestamps()` 的 `record_external_api_call` 补齐与普通版一致的 `extra={"model": "seed-tts-2.0", "char_count": len(text)}`。修前只记 status（线上行 `metadata_json=None`），千字价目永远估不出。失败行同样带字段，估算器只估 `ok/success`。
- 不改单价、不改 schema、不改 `config_version`；`DEFAULT_MEDIA_PRICING` 仍只是展示兜底，估算只读 `media_pricing` DB 行。
- 测试：`test_doubao_tts_client.py::TestUsageRecording`（假 httpx 流驱动真实合成入口）、`test_throttle_amount_passthrough.py`（completion/total 透传与字符串归一）、`test_media_pricing.py`（completion 优先 / total 回退 / 皆缺 unpriced / amount 优先 / 不相加）、`test_personal_usage_video.py::test_direct_media_pricing_units`（9 例参数化：seedream 与 seedream-360 per-image、gpt-image-2 两种 token 字段、seedance completion-only 与 total 回退、字幕 TTS 千字、无 token unpriced、失败 TTS 行不计）与 `test_direct_task_llm_plus_media_sum_and_source` / `test_direct_task_with_real_amount_row_is_mixed_source`（LLM+媒体求和；`cost_source` 的 `estimated` / `mixed` 口径与汇总一致）。
- findings：字幕 TTS 项勾掉并链接本批；ViMax 项保持未勾选（共享计价器 completion 优先已完成，ViMax 直记桥 amount/usage 透传仍不做）。

### PR 2 / 休眠账本与 sink 基座（`feat(usage): 建立线程媒体计量账本`）

- `backend/packages/harness/deerflow/runtime/media_usage.py`（新增，纯 harness，不 import `app.*`）：账本宿主路径 `sandbox_work_dir/.usage/media-usage.jsonl`；`media_call_id = "media_" + sha256(thread:event)[:40]`（46 字符，跨 run 幂等）；`parse_ledger_line` 按契约校验（tts `char_count 1..100000`、image `model` 须含 `seedream` 且 ≤128、未知字段忽略、tts 事件里的 `model` 丢弃）；`read_ledger_events` 以 `O_NOFOLLOW|O_NONBLOCK` 打开、只接受普通文件、只读尾部 1 MiB（窗口起点落在行中间时丢首个半行、末尾无换行的半行留给下次）、同批重复 `event_id` 只算一次、坏行计 `errors`；`map_event_to_call` 固定 provider/method/status（tts → `doubao_tts` / `synthesize_sse[_with_timestamps]` / `seed-tts-2.0`，image → `seedream-360` / `generate_image` / 事件原样 `model`），metadata 带 `metering_source=sandbox_reported`。
- `backend/app/gateway/_shared/agent_media_usage.py`（新增）：`drain_agent_media_usage(thread_id, user_id, agent_name)`——Agent 名经 `canonical_agent_capability` 限定在 course-video / creative-studio / ppt-master / podcast-studio，否则直接返回且**不碰文件系统**；读取在 `asyncio.to_thread`；分批（≤500）查已存在 `call_id`；按文件顺序写新行、单次 ≤1000 条（未写的留在窗口内下次续写）；每条独立 session 提交、失败先 rollback：`IntegrityError` 记 duplicate、其他异常记 error 并继续；`truncated` 且窗口最早一条有效事件未入库时 WARNING `ledger window overflow`；`error>0` 必 WARNING；日志不带行内容。
- `backend/packages/harness/deerflow/runtime/runs/worker.py`：`RunContext.media_usage_sink`（默认 `None`）；顶部 `getattr(ctx, "media_usage_sink", None)` 解包 + run 开始时捕获 `get_effective_user_id()`；`_resolve_agent_name` 按 `context.agent_name → configurable.agent_name → assistant_id` 取名（与 D3 块同序）；`publish_end` 与 cleanup 任务之后用 `asyncio.wait_for(..., MEDIA_USAGE_DRAIN_TIMEOUT=10)` 调用 sink，异常/超时只 WARNING、不改变 run 状态。
- `backend/app/gateway/deps.py`：`get_run_context` 仅在 `DEER_FLOW_MEDIA_USAGE_LEDGER` 去空白后等于 `1` 时注入 sink；默认/其他值保持 `None`（休眠：不读线程目录、不写 `sandbox_reported` 行）。
- `unpriced_media_calls`（部分计价响应）：`_aggregate_media_rows` 额外返回已知 `source=unpriced` 行数；`UsageTask` 新增**必填** `unpriced_media_calls: int`（不给 dataclass 基类加默认字段），`agent.py` / `video.py` 两处构造器同步；`GET /api/usage` 响应新增该字段，`Field(description=...)` 写明「不含未埋点通道；`media_cost` 是已知下界」。API 不新增调用数。前端按 TS 类型读取、忽略未知字段，本 PR 不改前端。
- 不改 `llm_usage_sink.py` / `runtime/journal.py` / `sandbox/tools.py`、不改 schema / `config_version`、不新增内部 API 或沙箱凭据。开关关闭时 `/api/usage` 响应仍会多出 `unpriced_media_calls`。
- 已知限制（沿用计划 §四，本批不解决）：run 结束后才完成的后台调用要等同一线程下一次 run 结束才入库；晚到/关闭期间积累的事件 `created_at` 是入库时间；两次 drain 之间新增超过 1 MiB（约 7000 条）时窗口外事件丢失，只能靠 `ledger window overflow` WARNING 发现；生产仅支持本地 Aio 容器；agent 删除账本则对应事件丢失。
- 测试：`test_agent_media_usage.py`（契约 / 读取 / 映射 / `call_id`，28 例含 symlink、FIFO、目录、超长行、窗口边界）、`test_media_usage_worker.py`（worker 钩子 / 开关 / drain 幂等 / 上限续写 / overflow / 错误分类 / 竞态 IntegrityError）、`test_personal_usage_{agent,video,router}.py` 新增 `unpriced_media_calls` 用例（含 HTTP 契约与 Field 语义）。

## 验收记录

- `git diff --stat 52c316763dc15e60649f1dc6eb0faf52b50013d4..origin/main` → `14 files changed, 2030 insertions(+), 121 deletions(-)`，全部为 chat-token-usage 的 frontend/docs 改动，不触及 sink / worker / provider / 五份 `_doubao_tts.py` / 补丁序号，计划无需修订。
- `find skills -name '_doubao_tts.py' -exec md5 -r {} \;` → 5 份，md5 全部 `ebc2cc025ba7238d1d5a27c997b54e53`；`grep -nE '^\| *P3[0-9]' skills/public/ppt-master/TRAINERAI_PATCHES.md` → 最新 P34，下一编号 P35。
- `git check-ignore -v temp/2026-11-23-six-core-media-usage/` → `.gitignore:110:temp/`；`git check-ignore -v .superpowers/sdd/` → `.gitignore:112:.superpowers/`。
- 干净 worktree 测试环境：主 clone venv 含 `_editable_impl_deerflow_harness.pth`，复用会使 `deerflow` 解析到主 clone（`deerflow -> /Users/littlebear/Documents/SuperTrainerAgent/backend/packages/harness/deerflow/__init__.py`，`app -> .worktrees/media-usage/backend/app`）；改在 worktree `backend/` 执行 `uv sync --frozen --offline`（uv 本地缓存，无网络）→ `sync exit=0`，之后两者均解析到 `.worktrees/media-usage/backend/`。
- PR 1 基线：`PYTHONPATH=. uv run --no-sync pytest tests/test_media_pricing.py tests/test_throttle_amount_passthrough.py tests/test_doubao_tts_client.py -q` → `26 passed, 1 warning in 5.26s`。
- Task 0 Step 3（测试服只读核对）：`docker ps` → 六服务 healthy，gateway/frontend `v3-20261123`（main `a2f6059d`），无在跑沙箱容器；`awk '/^sandbox:/' config.yaml` → `use: deerflow.community.aio_sandbox:AioSandboxProvider`（本地 Aio 容器）、`allow_host_bash: false`；`docker inspect trainer-v3-gateway` 挂载 → `v3-deer-flow -> /app/backend/.deer-flow (rw)`，`printenv` → `DEER_FLOW_HOME=/app/backend/.deer-flow`、`DEER_FLOW_HOST_BASE_DIR` 已设、`QIHU_IMAGE_MODEL` 与 `DEER_FLOW_MEDIA_USAGE_LEDGER` 均未设置；`id` → gateway `uid=0(root)`；`config.yaml` → `ai_image_provider: seedream-360`。
- Task 0 Step 3（DB，容器内 `sqlite3 ... ?mode=ro`）：`alembic_version` → `['p9c0d1e2f3a4']`；`select count(*) from media_pricing` → **`0`**（表为空；种子迁移 `l4e8f0a9b2c6` 是 upsert，全新库迁移后本应有行，唯一删除路径是管理员 PUT 全量替换）。估算器只读 DB 行 → 测试服当前**所有媒体行都是 unpriced**，与计划「复用现有价目」的假设冲突；按「不改管理员价格」未动，留待用户决定后再做 Task 5 费用验收。既有 `doubao_tts/synthesize_sse_with_timestamps/success` 行 `metadata_json` → `None`（印证计划缺口）；`qihu/generate_image/ok` 行 `metadata_json` → `{"height":…, "model": "seedream-360", "total_tokens": 9216, "width":…}`；`seedance/generate_video/ok` 行 → `{"duration": …, "model": "seedance", "total_tokens": …}`。
- PR 1 RED：`pytest tests/test_doubao_tts_client.py -q` → `3 failed, 10 passed`（`KeyError: 'extra'`）；`pytest tests/test_media_pricing.py tests/test_throttle_amount_passthrough.py -q` → `6 failed, 23 passed`（completion 用例 `comparison failed`）。
- PR 1 GREEN：`pytest tests/test_personal_usage_video.py tests/test_media_pricing.py tests/test_throttle_amount_passthrough.py tests/test_doubao_tts_client.py -q` → `59 passed, 1 warning in 1.35s`。
- 变异验证（撤回三处产品修复后重跑同四文件）→ `12 failed, 47 passed`（completion-only / 字幕 TTS extra 用例全红）；恢复后 `59 passed`。
- `pre-commit run --files <10 个改动文件>` → ruff lint Passed / ruff format 首轮 Failed（hook 原地格式化 `test_doubao_tts_client.py`，重跑 Passed）/ gitleaks Passed / docs convention Passed；`make -C backend lint` → `All checks passed!` + `1329 files already formatted`。
- `cd backend && CI=true PYTHONPATH=. uv run --no-sync pytest tests/ -q -n auto --dist loadfile`（= `make test`，干净 worktree 独立 venv）→ `10231 passed, 91 skipped, 435 warnings in 125.19s`，exit=0。
- 根 `make check` → backend ruff `All checks passed!` / `1329 files already formatted`；frontend knip 仅 configuration hint；`i18n key 存活检测通过：660 个叶子全活`；`OK 仓库治理约定全过`，exit=0。
- PR 1 评审轮次 1（OCR run 45216169382-1，PR #50）：1 项 `bug · medium`——`completion_tokens=0` 时 `float(0)` 成功即按 0 计价、不回退 `total_tokens`。处置：**采纳**。RED `pytest tests/test_media_pricing.py -q` → `4 failed, 22 passed`（zero / zero-str / negative / zero-without-total）；GREEN：0 与负数按缺失处理、回退 `total_tokens`，两者皆非正数则 unpriced → 四个定向文件 `63 passed`。
- PR 1 评审轮次 2（OCR run 45217015113-1）：1 项 `maintainability · low`（模型名字面量建议复用 `RESOURCE_ID`）→ 延后（计价标签与 API 资源 ID 语义不同；普通版为既有代码）。无 Critical / Important。
- PR 2 RED：`pytest tests/test_agent_media_usage.py -q` → `1 error during collection`（模块不存在）；`pytest tests/test_media_usage_worker.py -q` → `ImportError: cannot import name 'agent_media_usage'`；`pytest tests/test_personal_usage_{agent,video,router}.py -q` → `5 failed, 53 passed`（`unpriced_media_calls` 缺失）。
- PR 2 GREEN：`pytest tests/test_personal_usage.py tests/test_personal_usage_agent.py tests/test_personal_usage_video.py tests/test_personal_usage_router.py tests/test_agent_media_usage.py tests/test_media_usage_worker.py -q` → `160 passed`；worker / harness 边界 / RunContext 相关回归（`test_run_worker_*.py`、`test_harness_boundary.py`）→ `100 passed`；`grep -n 'from app\|import app' packages/harness/deerflow/runtime/media_usage.py` → 无输出（harness 边界干净）。
- PR 2 变异验证（三处同时变异：去掉 `seedream` 标记校验 / `IntegrityError` 误记 error / sink 挪到 `publish_end` 之前）→ `3 failed, 49 passed`（恰为目标用例 `model-no-seedream`、`test_worker_calls_sink_after_publish_end_with_ui_shaped_identity`、`test_drain_integrity_error_on_race_counts_duplicate`）；恢复 → `52 passed`。
- PR 2 `pre-commit run --files <backend 改动>` → 首轮 ruff lint/format Failed（F821 漏导入 `Path` + hook 原地 `--fix`/format 三文件，均为良性：`.encode("utf-8")`→`.encode()`、换行、四引号 docstring）→ 修复后全部 Passed；`make -C backend lint` → `All checks passed!` / `1333 files already formatted`。
- PR 2 `cd backend && CI=true PYTHONPATH=. uv run --no-sync pytest tests/ -q -n auto --dist loadfile`（= `make test`）→ `10292 passed, 91 skipped, 430 warnings in 66.98s`，exit=0；根 `make check` → `OK 仓库治理约定全过`，exit=0。
