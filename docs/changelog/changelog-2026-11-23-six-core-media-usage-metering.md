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
