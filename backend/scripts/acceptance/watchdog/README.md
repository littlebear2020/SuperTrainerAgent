# 看门狗活性重构--部署验收套件（2026-08-24 计划 W8/W9）

计划：`docs/plans/2026-08-24-watchdog-liveness-impl-plan.md`（v3 定稿）。
对应门禁：计划第四节「部署前门禁」WD1~WD4、WD6~WD8。

## 文件

| 文件 | 门禁项 | 说明 |
|------|--------|------|
| `run_mock_gates.py` | WD2①② / WD3 / WD4 / WD8 | **本机可跑**的故障注入演练（时间尺度同比缩小：P1 120s→2s、扫描 60s→1s、cancel 界 30s→1s）。生产部署前必须全 PASS。 |
| `calibrate.py` | WD7 / W8 | 测试服跑 ≥3 个 ≥50 帧真实任务（360 通道），采集阶段时长分布对照 W4 预算公式，存档 `backend/test-output/watchdog-calibration-YYYYMMDD/`。p99 > 预算时按公式上调标量（`video_gen.stall_budget_scalars`），**禁止直接拍数**。 |
| `wd1_reap_scan.py` | WD1 | 任务跑完后扫 gateway 日志（或 DB 兜底），断言目标任务零收尸且全部 ready。 |

WD6（单测零回归）由 pytest 直接覆盖：

```bash
cd backend && uv run python -m pytest tests/test_task_framework_liveness.py \
  tests/test_task_framework_reap_semantics.py tests/test_reaper_composing_backstop.py \
  tests/test_video_gen_watchdog.py tests/test_sibling_modules_reaper_regression.py \
  tests/test_task_framework*.py tests/test_video_gen_stall*.py tests/test_voice_twin*.py
```

## 执行顺序（部署前）

1. **单测门禁（WD6）**：上面的 pytest 全绿。
2. **mock 演练（WD2/WD3/WD4/WD8）**：
   ```bash
   cd backend && uv run python scripts/acceptance/watchdog/run_mock_gates.py
   ```
3. **测试服部署 gateway 镜像**（仅 gateway 重建 + restart，不动 nginx / worker）。
4. **校准（WD7）**：
   ```bash
   VG_ACCEPT_BASE=http://<测试服>:8001 uv run python scripts/acceptance/watchdog/calibrate.py
   ```
   产物存 `test-output/watchdog-calibration-YYYYMMDD/calibration.json`；p99 超预算按公式调 `stall_budget_scalars` 后复跑。
5. **误杀扫描（WD1）**：
   ```bash
   # gateway 容器日志先落到宿主机文件（docker logs gateway > gw.log）
   uv run python scripts/acceptance/watchdog/wd1_reap_scan.py --task-ids vg_a,vg_b,vg_c --log-file gw.log
   ```

## 真死时效的测试服实操（WD2 真实口径补充）

mock 演练已验证机制与时效公式（(P1 120s + 扫描 60s) × 1.1 ≈ 200s）。测试服
实测对照：

- **gateway 侧**：提交一个长任务，渲染中途 `docker restart` gateway 容器--
  观察 restart 后 `Gateway restart recovery` 日志（启动恢复路径，非 reaper）；
  若需验证 reaper 主谓词（协程死但进程活），用 `calibrate.py` 采样序列里的
  `max_observed_silence_s` 作健康对照即可（真实协程死亡无法从外部注入，
  mock 演练 wd2a 已覆盖该路径）。
- **composing 侧**：任务进入 composing（progress 95）后 `docker compose stop
  worker`，观察 worker 清道夫或 gateway W3b 兜底在「租约剩余时长 + P3」内
  收尸（租约口径，不承诺 200s）；gateway 日志出现
  `Reaped composing task ... claim released for re-pickup`。

## 部署后观察（非门禁）

- WD5：voice_twin/PPT 3 天无误杀（语义由 W7 单测保证不变）。
- WD9：reaper 收尸日志按 `predicate` label 归因（death/budget/backstop/
  composing_backstop）抽查 3 天，应只剩真死。

## 回滚（计划第五节）

```yaml
video_gen:
  liveness_threshold_seconds: 0      # 主谓词静默（P9）
  stall_progress_freeze_seconds: 1200  # 旧 20 分钟窗口
  composing_backstop_enabled: false   # W3b 兜底关（P10）
```
扫描周期回 300：`app.py` 的 `vg_svc.start_stall_reaper(interval=60)` 改回不传参
（或直接回滚镜像）。四处复位即回旧行为，代码可留镜像。
