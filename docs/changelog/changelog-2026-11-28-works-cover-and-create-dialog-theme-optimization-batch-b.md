# 任务型视频缩略图可靠性（Batch B：worker 抽帧加固与离线回填）

> **状态**：已执行（2026-11-28；PR #70 合入 main `03d83be4`；未部署，发布须用户另行下达指令）。计划：`docs/plans/2026-11-27-works-cover-and-create-dialog-theme-optimization-plan.md`（Batch B = Task 5–7）；执行基线 commit `dd24a2e9`（计划入库）。
> **TL;DR**：worker/离线工具批，不改前端与 gateway 生产代码。① 共享抽帧 helper `_ffmpeg_extract_thumbnail` 加固：先写临时文件、成功才 `os.replace`；代表时点拿不到帧回退 `-ss 0`；最终失败删除旧图与临时文件；新增仅关键字参数 `seek_ratio`（默认 10% = 既有行为）。② Remotion 改调该 helper（`seek_ratio=0.25, timeout=60` 保持现有取帧位置与超时）。③ 新增默认 dry-run 的历史回填脚本 `backend/scripts/backfill_video_thumbnails.py`（只写 `result.thumbnail_path`）。关键决策：抽帧失败不阻断 ready、旧图不冒充新成片；回填复用 gateway 现有同步 `_extract_thumbnail` 不改它。影响面 = `docker/production-worker/pipelines/`（两个 worker 镜像都 COPY 整个 `pipelines/`）、`backend/scripts/`、测试；命中核心路径 `docker/`，走分支 + PR + OCR；不部署、不调用付费 API。

## Task 5：加固共享抽帧 helper，Remotion 改用它

- `docker/production-worker/pipelines/_ffmpeg_helpers.py::_ffmpeg_extract_thumbnail(video_path, output_path, timeout=30, *, seek_ratio=0.1)`：位置参数与返回值不变（三条 AI 管线与多处测试按位置参数调用/桩掉它，调用点不改）；流程 = 取时长（失败视为未知）→ 代表时点 `max(1s, duration×ratio)` 抽到同目录临时文件 `thumbnail.jpg.tmp.jpg` → 校验返回码且文件非空 → 失败则 `-ss 0` 重试 → 成功 `os.replace` 到目标并返回 True；单次尝试的 `TimeoutExpired`/`OSError` 就地记日志并继续下一次尝试（代表时点超时不放弃 `-ss 0` 回退）；最终删除旧图时的 `OSError` 只记 warning、不外抛（调用方裸调用，契约是失败返回 False）；最终失败删除临时文件与目标上已有的旧图，打 warning（含路径），返回 False。helper 仍只依赖标准库（`test_thumbnail.py` 按文件路径加载它）。
- `compose_remotion.py::_extract_thumbnail` 改为 `from pipelines._ffmpeg_helpers import _ffmpeg_extract_thumbnail` 后调用（同 `from pipelines import net_guard` 写法），传 `timeout=60, seek_ratio=0.25`；目标仍是 `task_dir/thumbnail.jpg`，任何失败不向外抛。`worker.py` 的完成写回与三条 AI 管线的调用点不改。
- 边界：时长 <1s 的视频在旧实现里 seek 到 1s 拿不到帧且不出图，现在回退 t=0 成功。

## Task 6：历史任务离线回填工具（默认 dry-run）

- `backend/scripts/backfill_video_thumbnails.py`，按 `reconcile_video_tasks.py` 先例写（`--db-path` 自动探测、dry-run 只读连接 `mode=ro`、`--apply` 才写）。参数：`--task-type`（`choices` 限定五种：remotion_course / remotion / ai_image / ai_visual / ai_video，可多次，多值也在 SQL 层 `IN` 过滤，`LIMIT` 作用于目标类型）、`--task-id`、`--limit`（候选上限：最多处理多少个待补图任务，已带图/成片缺失的行不占额度，分批 `--apply --limit N` 重跑可推进；≤0 被拒；达到上限且后面还有任务时打印 `[limit]` 提示）。
- 范围：只处理上述类型中 `status='ready'`、`result.output_path` 存在、且 `result.thumbnail_path` 缺失或其文件不存在的任务；不重新渲染、不调用 provider、不删除任务或 outputs。
- 抽帧：直接复用 gateway 现有同步 `_extract_thumbnail(output_path, Path(output_path).parent)`，缩略图落在成片同目录的 `thumbnail.jpg`（worker 任务即 `task_dir/thumbnail.jpg`）。dry-run 连文件系统都不写：抽到一次性临时目录、在目录销毁前判定存在性再丢弃。
- 写回：每个任务一条 `UPDATE unified_tasks SET result = json_set(COALESCE(result,'{}'), '$.thumbnail_path', ?) WHERE id = ? AND status = 'ready'`，只动这一个键；写库失败或 UPDATE 命中 0 行（状态已被并发改掉，按 skipped 计）时回收本次新建的缩略图（不留「文件在、引用无」的孤儿，重跑仍能识别为候选）；单任务失败不中断批次；输出 `scanned/generated/skipped/failed` 计数；范围内有抽帧失败时退出码 1。
- 用法写在 `--help`：在 gateway 容器内运行（`docker exec <gateway> sh -c 'cd /app/backend && PYTHONPATH=. uv run --no-sync python scripts/backfill_video_thumbnails.py'`），写入的路径前缀与 gateway 一致、容器里有 ffmpeg。真实环境的 dry-run/apply 须用户另行授权，见计划 §九。

## 已知限制

- 回填复用的 gateway `_extract_thumbnail` 未改（计划 Q14）：它仍 seek ≥1s 且无 t=0 回退，成片时长 <1s 的历史任务回填会失败并计入 `failed`，由前端类型封面兜底；正常课程视频不会触发。
- 新任务的缩略图生成只对 worker（remotion-worker / production-worker 两个镜像）生效，须重建镜像；历史任务要有真实缩略图须在真实环境跑回填。
- 不处理缩略图 URL 的浏览器缓存失效（Q16，已登记 backlog 候选）。

## 验收记录

每行 = 命令 → 实际输出关键行。

- `cd backend && PYTHONPATH=. uv run pytest tests/test_thumbnail.py -q`（Task 5 RED，实现前）→ `3 failed, 6 passed`：`test_helper_failure_removes_stale_thumbnail_and_leaves_no_temp`（旧图残留）、`test_helper_falls_back_to_first_frame_for_sub_second_video`（<1s 无帧）、`test_helper_seek_ratio_is_keyword_only_and_keeps_positional_signature`（无 `seek_ratio`）；GREEN → `9 passed`
- `cd docker/production-worker && ../../backend/.venv/bin/python -m pytest tests/test_remotion_thumbnail.py -q`（Task 5 RED）→ `2 failed`（`AttributeError: module 'pipelines.compose_remotion' has no attribute '_ffmpeg_extract_thumbnail'`）；GREEN → `2 passed`
- 签名兼容回归：`pytest tests/test_thumbnail.py tests/test_worker_ai_visual_payload.py tests/test_ai_video_duration_alignment.py tests/test_worker_local_visual_misalign.py tests/test_worker_incremental_manifest.py -q` → `42 passed`；worker 全量 `pytest tests/ -q` → `359 passed in 37.87s`
- `cd backend && PYTHONPATH=. uv run pytest tests/test_backfill_video_thumbnails.py -q`（Task 6 RED，脚本不存在）→ `6 failed`（`FileNotFoundError: …/scripts/backfill_video_thumbnails.py`）；GREEN → `6 passed`（dry-run 零写入、只处理五种类型且 `--task-type ppt` 被拒、只写 `thumbnail_path` 一个键、幂等、单任务失败不中断、计数、真实 MP4 生成有效 JPEG）
- Task 7 Step 1 行为验收（API 级，临时库 + 真实小 MP4）：回填路径 `test_backfill_is_visible_through_thumbnail_api` → 回填前 `_task_with_thumbnail(...).thumbnail_url is None`，`--apply` 后为 `/api/video/tasks/vg_needs/thumbnail`，`get_video_thumbnail` 端点返回 `FileResponse` 且文件头 `ff d8 ff`；旧图路径 `test_stale_thumbnail_is_not_served_after_failed_extraction` → 加固后的 helper 对损坏成片返回 False、旧 `thumbnail.jpg` 被删、`_task_with_thumbnail` 返回 `thumbnail_url=None`。变异检查：把 `_ffmpeg_helpers.py` 临时换回 `dd24a2e9` 版本再跑该用例 → `AssertionError: assert not True`（旧图仍在），换回后 `1 passed`
- 两文件合跑 `pytest tests/test_thumbnail.py tests/test_backfill_video_thumbnails.py -q` → `17 passed`
- PR #70 评审第 1 轮（2 条 medium + 2 条 low，无 Critical）修复先红后绿：`pytest tests/test_thumbnail.py tests/test_backfill_video_thumbnails.py -q` 新增 4 条用例 → `3 failed, 18 passed`（代表时点超时后未回退、写库失败后缩略图仍在、`[limit]` 提示缺失；多 `--task-type` 的 LIMIT 用例在旧实现下 `scanned=0`），实现后 → `21 passed`；桩掉 helper 的回归 + 两文件 → `54 passed`；worker 全量 → `359 passed`
- PR #70 评审第 2 轮（1 条 medium 内联 + 4 条 low，无 Critical）修复先红后绿：`--limit` 改为候选上限（分批可推进；新增用例 `test_limit_counts_candidates_and_batch_advances`、`test_limit_rejects_non_positive_values`，多类型 LIMIT 用例改为按候选口径断言）、helper 删旧图的 `OSError` 不外抛（`test_helper_failure_to_remove_stale_thumbnail_does_not_raise`）、写库命中 0 行按 skipped 回收（`test_write_back_affecting_zero_rows_is_skipped_and_reclaimed`）、回收目标与 `existed_before` 统一为成片同目录 `thumbnail.jpg`；实现前 → 4 failed，实现后两文件 → `25 passed`；桩掉 helper 的回归 + 两文件 → `58 passed`；worker 全量 → `359 passed`
- Task 7 Step 2 门禁：`pre-commit run --files <本批改动>` → ruff lint/format、gitleaks 全 Passed；`cd backend && make test`（全量）→ `10456 passed, 98 skipped, 467 warnings in 99.82s`（基线 10444 + 本批新增 12），`EXIT=0`
- worker 独立套件全量 `cd docker/production-worker && ../../backend/.venv/bin/python -m pytest tests/ -q` → `359 passed in 74.62s`，`EXIT=0`（跑完在目录下留下 1 字节未跟踪文件 `-`，既有现象，已登记 backlog 并删除）
- 根 `make check` → `MAKE_CHECK_EXIT=0`：`ruff check .` `All checks passed!`、eslint 0 errors（120 warnings 既有）、`i18n key 存活检测通过：711 个叶子全活`、`OK 仓库治理约定全过`
- `python3 scripts/check_docs_convention.py` → `OK 仓库治理约定全过`
- Task 7 Step 3：`docs/findings-backlog.md` 登记 5 行（Batch A 的浅色 muted-foreground 4.55:1、chat-thread-recovery 并发偶发超时；Batch B 的缩略图 URL 缓存候选 Q16、gateway `_extract_thumbnail` 未加固、worker 套件留下 `-` 文件）

## 发布注意项（需用户另行下达部署指令）

- 只需重建 production-worker 与 remotion-worker 两个镜像（两者都 COPY 整个 `pipelines/`）；不需要 gateway 部署；模板未改，TEMPLATE_VERSION 不变。
- 回填脚本随下一次 gateway 镜像进入容器（`backend/Dockerfile` 复制整个 `backend/`）；在那之前要跑就先 `docker cp` 进 gateway 容器。在容器内运行，先在用户授权下跑 dry-run 把脱敏计数交用户；`--apply` 须用户明确指令，执行前备份数据库并记录回滚方式。
- 发布后核对：由部署方按既有流程确认 remotion_course、ai_image/ai_visual、ai_video 新任务能生成真实缩略图。不新增其他任务类型的验收。
- 回滚 = 回退镜像或 revert PR B，不删除已生成的 outputs/thumbnails。
