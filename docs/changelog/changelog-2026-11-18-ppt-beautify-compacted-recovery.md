# PPT 美化 compacted recovery 收窄修复记录

> **状态**：部分执行／行为验收未通过（静态门禁与 PR #28 远程 CI/AI+OCR 全绿、OCR 0 发现；两次授权 qwen 冒烟均为 FAIL+EXERCISED，P01 未产出；PR 仅评审，不合并）。
> **TL;DR**：将 compacted recovery 拆为查盘 + 当前页有界恢复，登记 P30 并 bump `SEED_VERSION=2026-11-18.1`；随后补上其对首次 Step 6 读批次的显式优先级。两次真实 qwen 运行都保持摘要健康且无重问，但都在 100 次调用后无 P01；prompt-only 收窄未成立，未改 summarization、trigger/cap 或 PR B。

## 1. 改动范围

- `generate-pptx.md`：compacted 独立恢复行；先盘点 `svg_output/`，再读 `spec_lock.md`、当前页 §IX 与当前页必要模块章节，使用 `start_line`/`end_line`；另补首次 Step 6 读批次中的 compacted 优先级。
- `failure-recovery.md`：同步治理矩阵；fresh/resumed/restarted/external 的完整恢复保留。
- `TRAINERAI_PATCHES.md`：登记 P30 与两次事故证据。
- `SEED_VERSION`：`2026-11-16.4` → `2026-11-18.1`；两个 fresh home 均实际播种 6 个 agent 并核对运行时邮票。
- 测试：表格行级恢复契约、非 compacted 全量恢复保留、首次 Step 6 批次优先级、精确 seed 版本。
- 未修改：summarization 中间件及测试、压缩 trigger/trim/skill rescue cap、模型配置、SOUL、核心 reference 内容、前端、docker、PR B。

## 2. TDD 与免费验证

- 首轮 RED：独立 compacted generate/failure 行不存在、fresh 行未拆分、seed 版本旧，`4 failed, 35 passed`。
- 首轮 GREEN：恢复行与 P30/seed 落地后 `39 passed`。
- 首次行为 FAIL 后补优先级 RED：`test_compacted_recovery_preempts_initial_step6_batch` 因缺少优先级段落失败；补齐后 `40 passed`。
- 最终相邻回归：`305 passed`；全量后端：`10083 passed, 89 skipped`；Ruff、格式、根 `make check`、docs convention、`git diff --check` 全通过。
- 驱动为零调用 preflight 增加独立 home/evidence、实际 seed 邮票与工作树技能来源校验；真实证据均保留在 `temp/`，未入库。

## 3. 两次真实 qwen 冒烟

共同条件：SHA-256=`16f7f0dbf06a559b8889333a0eae9a80349d39d92132efae7a369fe50ea9ff24` 的 7 页输入、P03 原生表格、qwen3.8-flash、token budget disabled、recursion limit 2400、正常 Stage 1/2、fresh home/thread；最终确认后 100 次调用无 P01 / 新占位 / 重问 / 20 分钟无产物硬停。

| 项 | v1 | v2（首次 Step 6 优先级修正） |
|---|---|---|
| HEAD | `6616aa94` | `0c942a8f` |
| Thread | `172306f2-e4c7-420d-bfe4-da839f460448` | `56d43057-aa36-48ff-93a7-f0fbdc029efe` |
| Stage 2 选择 | 模型明确推荐方向 0 暖源复刻 | 模型未推荐；按首个列出项选方向 1 瑞士栅格，默认生产参数 |
| 最终轮 | 2505.9s，100 次观察调用 | 2410.6s，100 次观察调用 |
| 结果 | `FAIL+EXERCISED`，无 P01 | `FAIL+EXERCISED`，无 P01 |
| 摘要审计 | 1424 checkpoints；13 个有效摘要；`bad_count=0` | 1424 checkpoints；14 个有效摘要；`bad_count=0` |
| 累计 mode | fallback=12 / fresh=13 / reuse=98 / pass_through=1 | fallback=15 / fresh=14 / reuse=97 / pass_through=2 |
| 确认/重问 | `final_confirmed=true`，`clarification=null` | `final_confirmed=true`，`clarification=null` |

驱动的流式参数记录为空对象，因此路径与行号结论不采用 `turn-03.json` 的空 args，而采用持久化 checkpoint 中 99 个已落盘工具调用的真实参数；其名称顺序与驱动前 99 次观察一致，第 100 次观察发生在硬停边界。

## 4. 行为根因与未通过边界

### v1：compacted 行未覆盖首次 Step 6 批次

- fresh #7（08:24:25）后，call 70 全读 `executor-base.md`，call 74 全读 `shared-standards-core.md`；此前轮次没有这些文件的读取，属于首次 Step 6 批次，不是重复恢复。
- call 79–91 继续全 roster 的 mode/preset/table/native-data/image/visualization 模块。
- fresh #10 后，call 97–99 才查盘、有界读取 `design_spec.md` §IX（140–346）并 grep 当前所需规则。
- 结论：compacted 行能被部分执行，但没有显式压过更早的首次强制批次。

### v2：显式优先级仍未约束 qwen 的实际读取

- fresh #5（10:03:35）后，call 56/60 仍全读 `executor-base.md` 与 `shared-standards-core.md`，随后继续全 roster 模块。
- fresh #7 后，模型查盘并重读 Step 6，但 call 80/81 仍全读 semantic/preset；call 82–84 用 `bash sed` 读完 executor-base，call 89 再用 `bash sed` 读取 shared-core 大段。
- 后段虽有有界 §IX 与文本校准，100 次预算仍在首个 SVG 前耗尽。
- 结论：在 32000-token 窗口下，仅调整恢复提示及优先级不能可靠阻止 qwen 恢复全量/全 roster 读取，且 `read_file` 行范围约束可被 `bash sed/cat` 绕过。P01 目标未满足，不能宣称此修复有效。

下一步需要另行评审确定性恢复产物/执行机制或首次 Step 6 读取架构，而不是继续叠加相似 prompt。任何第三次实现与付费运行都需新的决策和显式授权；本批不启动 PR B、不合并 PR A 或本 authoring PR。

## 验收记录

- `cd backend && PYTHONPATH=. uv run pytest tests/test_ppt_master_skill_vendored.py tests/test_seed_agents.py tests/test_seed_ppt_master.py -q`（RED）→ `4 failed, 35 passed`；缺独立 compacted/fresh 行且 seed 仍为 `.4`。
- 同一命令（首轮 GREEN）→ `39 passed`。
- `...::test_compacted_recovery_preempts_initial_step6_batch -q`（优先级 RED）→ `1 failed`，缺 `Compacted recovery precedence`。
- 同一三文件命令（优先级 GREEN）→ `40 passed`。
- `PYTHONPATH=. uv run pytest tests/test_ppt_master*.py tests/test_seed_*.py tests/test_sandbox_read_file_range.py tests/test_skillscan_native.py -q` → `305 passed`。
- `cd backend && make test` → `10083 passed, 89 skipped in 57.34s`。
- `cd backend && make lint` → `All checks passed!`；`1317 files already formatted`。
- 根 `make check` → exit 0；前端 `0 errors`，docs convention `OK`。
- v1 零调用 preflight → hash match / 7 slides / P03 table / seeded=6 / runtime=`2026-11-18.1` / skills worktree match / HEAD=`6616aa94`。
- v1 最终轮 + audit → `HARD_STOP=TOOL_LIMIT_100_NO_P01`；`FAIL+EXERCISED`；`bad_count=0`；13 个有效摘要；无 P01。
- v2 零调用 preflight → 同上且 compacted + precedence marker 均存在，HEAD=`0c942a8f`。
- v2 最终轮 + audit → `HARD_STOP=TOOL_LIMIT_100_NO_P01`；`FAIL+EXERCISED`；`bad_count=0`；14 个有效摘要；无 P01。
- `gh pr checks 28 --watch` → quick-checks `pass 23s`、backend-unit-tests `pass 4m5s`、review `pass 24s`。
- `gh pr view 28 --comments` → `OpenCodeReview: Review complete: 0 finding(s) across 1 selected item(s)`；reviews/inline comments 均为空。
- `git diff --exit-code 468a7761 -- backend/.../summarization_middleware.py backend/tests/test_summarization_middleware.py` → exit 0，summarization 零改动。

## 6. 评审与合并边界

- 本地实现提交：`6616aa94`、`0c942a8f`。
- stacked PR：[#28](https://github.com/littlebear2020/SuperTrainerAgent/pull/28)，base=`ppt-beautify-summarization-context-integrity`（PR A #27）。远程 quick-checks（run `44289144376`）、backend-unit-tests（`44289144387`）、review（`44289144587`）全绿；OCR `0 finding(s)`、`files_reviewed=1`，无 inline comments / Critical / Important。
- PR A 保持未合并，PR B 未开始；不部署，不宣称完整 7 页或 46 页通过。
