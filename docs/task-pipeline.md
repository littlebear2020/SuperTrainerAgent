# 任务管线（统一任务框架）

> 本文写**机制**（状态机/认领/重试/排查），不抄任务类型名册——类型注册表以代码为准。
> 任务表结构以 `backend/app/gateway/_shared/task_framework/models.py` 为准，本文不写 schema。
> 课程视频管线的业务口径另见 `remotion-course-pipeline.md`；本文是它与所有异步能力共享的底座。

## 1. 一句话心智模型

所有异步任务（视频生成、声音分身、内容任务等）共用一张 `unified_tasks` 表：gateway 建单并执行到「可交棒」点，重 IO 段置 `composing` 交棒给 worker 池；worker 按池谓词 CAS 认领、租约保活、完成回写。gateway 与 worker 各有看门狗回收僵死任务。

## 2. 任务状态机

状态常量全集：`backend/app/gateway/_shared/task_framework/schemas.py`（`TERMINAL_STATUSES`、`GATEWAY_RECOVERABLE`）。

- **终态**：`ready` / `failed` / `cancelled`
- **gateway 非终态**：`pending` / `running` + 管线子状态 `draft` / `extracting` / `converting` / `synthesizing` / `scripting` / `generating` / `rendering` / `story_ready`
- **worker 专属**：`composing`（交棒后等待认领/合成中）
- **域专属字面量**：`qc_repair`（video_gen QC 异常态，worker 写入、gateway watcher 推进——不在框架常量里，见 `docker/production-worker/worker.py` 与 `backend/app/gateway/video_gen/service.py` 的 `_process_qc_repair_tasks`）

迁移主路径（gateway 侧，`backend/app/gateway/_shared/task_framework/service.py`）：

```
submit → pending →（auto_start 起 _wrap_pipeline 协程）→ running
  running → ready                        成功（纯 gateway 类型）
  running → composing                    交棒 worker 池（重 IO 段）
  running → draft / story_ready          等人操作的分叉点（不烧重试预算）
  running → pending（retry_count+1）      可重试异常 / 瞬态取消（max_retries=2）
  running → failed                       重试耗尽 / 不可重试异常
worker 侧（docker/production-worker/worker.py）：
  composing →（CAS 认领）→ 执行 → ready / failed / qc_repair
```

任何状态还有两条兜底：`cancel_task` → `cancelled`；`recover_stale_tasks`（启动时）与 `reap_stalled_tasks`（周期看门狗）把僵死行重排 `pending` 或判 `failed`。

## 3. worker 池与认领谓词

池由 compose 注入 `WORKER_POOL` 环境变量区分（`docker/docker-compose*.yaml`），实现统一在 `docker/production-worker/worker.py`：

- **`ffmpeg` 池**（默认）：认领集 = registry 派生白名单（`pipelines/registry.py` 的 `PIPELINES` 键集，扣除 remotion 类型）。
- **`remotion` 池**：认领集 = 固定二元组 `REMOTION_TASK_TYPES`；渲染槽与 still 槽分离（`_render_slot_task_types` / `REMOTION_STILL_SLOTS`）。

认领 = peek + CAS UPDATE 两段（`db_claim_task`）：先 `SELECT … WHERE status='composing' AND claimed_by IS NULL AND task_type IN (…)` 再条件 UPDATE；池谓词函数 `_pool_task_type_predicate` 在两处共用同一份防漂移。重 IO 隔离的理由一句话：渲染/合成是分钟级 CPU/内存大户，与 gateway 同进程会拖垮在线请求。

租约与保活：`LEASE_DURATION` 基础值 + `_lease_for_payload` 按任务规模动态放大（有 cap）；执行中由进度回调顺带 `renew_lease`（SQL 带 `claimed_by` 自证，0 行即自杀防双跑）；优雅退出调 `release_my_leases`。worker 有 `/health`（宣告 `worker_pool` 与 capabilities）与 `/metrics`。

## 4. 一次任务的完整生命周期走查

以 video_gen 为例（其他域同款，共享 `BaseTaskService`/`TaskRepository`）：

1. **创建**：`POST /api/video/create`（`routers/video_gen.py` `create_video`）→ `BaseTaskService.submit`（`task_framework/service.py`）落库 `pending`；remotion_course 建单前探测 remotion 池在线（`ensure_remotion_pool_available`，`video_gen/worker_probe.py`，60s 缓存）。
2. **gateway 执行**：`submit` 内 `auto_start` 起 `_wrap_pipeline` 协程 → `_run_gated_pipeline`（先过全局并发闸 + 每用户闸，`service.py` 的两个 Semaphore）。
3. **交棒判定**：`_should_handoff_to_worker`（`video_gen/service.py`）决定本类型是否交棒；交棒则置 `composing` 等 worker。
4. **worker 认领执行**：peek + CAS → `process_task` → 帧级进度回调（写 progress + 续租）→ 成功置 `ready`。
5. **完成/失败回写**：worker 裸 SQL 回写 status/result/error；失败落 `task_dir/_failure_context.txt`。
6. **QC 分叉**（仅 remotion_course 等）：worker 三扫描发现异常 → `qc_repair` → gateway watcher 修复后 CAS 回 `running` → 重新交棒。

并发语义：gateway 侧默认 `max_concurrent_tasks=20` / 每用户 6（`video_gen/config.py`）；worker 侧 `MAX_CONCURRENT_TASKS`（部署档 3 副本×3 槽）+ remotion still 槽独立。

## 5. 重试、恢复与锁

- **gateway 重试**：`max_retries = 2`；预算超限与 `ValueError/TypeError` 不重试；`draft/story_ready` 不烧预算。
- **重启恢复**：lifespan 挂 `recover_stale_tasks`（`app.py`）——未超预算重排 `pending`，超则 `failed`。
- **看门狗**：`reap_stalled_tasks` 周期跑（budget 超时 / progress 冻结等五谓词；`composing` 行另有租约兜底 `_reap_composing_backstop`，`task_framework/service.py:678`）。worker 侧 `MAX_LEASE_RETRIES=3`，租约过期先重排、耗尽才 `failed`。
- **任务框架无 checkpoint 列**：恢复靠 payload + task_dir 磁盘产物幂等重跑 + `dirty_frames` 增量重渲（`repository.py`）。注意 `backend/app/gateway/checkpoint_lineage.py` 是 **LangGraph 会话**的 checkpoint 机制，与本框架无关，勿混。
- **合成锁**：`_synth_lock_guard`（`video_gen/service.py`）——task_dir keyed asyncio.Lock，串行化同任务目录的音频/字幕写操作，同协程可重入；同款约定见 `repository.py` 的 `_dirty_lock_for`。

## 6. 日志文件地图

| 进程 | 本地裸跑（`scripts/serve.sh`） | docker dev | docker 部署 |
|---|---|---|---|
| gateway | `logs/gateway.log` | 卷挂载同落 `logs/` | stdout → `docker compose logs -f gateway` |
| frontend | `logs/frontend.log` | 同上 | stdout |
| ffmpeg worker | `logs/worker.log` | 同上 | `docker logs -f deerflow-deploy-production-worker-1` |
| remotion worker | `logs/remotion-worker.log`（起池方式见 remotion-course-pipeline.md） | — | stdout |
| nginx | `logs/nginx.log`（+access/error 分离） | — | stdout |

补充可观测面：worker 失败落 `task_dir/_failure_context.txt`；gateway `/metrics` + worker `:8040/metrics` 双 Prometheus 端点。

**运行时数据地图**（`backend/.deer-flow/`，调试「产物去哪了」的第一站；名目以实际目录为准）：

- `deerflow.db` / `tasks.db`——SQLite 库（应用数据 / unified_tasks 任务表，生产可 postgres）；
- `video-gen/`、`voice-twins/`、`gallery/`、`writer-images/`、`content/`——各域任务产物与素材（task_dir 所在）；
- `agents/`——seed agent 的运行时目录（SEED_VERSION 部署目标，退役壳须手动清理）；
- `memory.json`——agent 全局记忆；`uploads`/上传与各域子目录随业务产生。

## 7. 常见卡点排查表

| 症状 | 先看哪 |
|---|---|
| 任务卡 `pending` | gateway 并发闸/每用户闸是否满（`video_gen/config.py`）；看门狗日志 |
| 任务卡 `composing` | worker 池是否在线（`/health`）；认领谓词是否覆盖该 task_type（`worker.py` `_pool_task_type_predicate`）；租约兜底日志 |
| 任务进 `qc_repair` | gateway watcher `_process_qc_repair_tasks` 日志；`task_dir/_failure_context.txt` |
| 任务反复 `pending→running` 循环 | `retry_count` 列；`max_retries=2` 耗尽后应转 `failed` |
| 任务假 in-flight ~30 分钟 | 租约过期等回收——worker 被 kill 的正常窗口；勿手改 DB |
| 磁盘与 DB 不一致（假就绪/死链） | `backend/scripts/reconcile_video_tasks.py`（默认 dry-run） |
| 跨域任务查询 | `GET /api/tasks` / `GET /api/tasks/{id}`（`routers/tasks.py`）；video 域另有 `/api/video/tasks*` 与 SSE `/tasks/{id}/events` |

## 8. 相关文档

- remotion_course 业务口径（解析双档/素材前缀/QC/模板版本）→ `remotion-course-pipeline.md`
- 整体拓扑与本管线的位置 → `architecture-overview.md`
- worker 部署档与 drain 纪律 → `production-server-ops-manual.md`
