# PPT 美化长课件快速交付优化（2026-11-16 批次）

> **状态**：已执行（2026-11-17 完成；本地演示交付完成，经人工纠正后通过；默认模型自主行为与 46 页长链待验证；未 commit / 未 push / 未部署）。
> **TL;DR**：收敛“最终确认后重问、逐页重读 spec_lock、长 deck 无法分段续写”三条规则并 reseed 到本地运行时；用原 46 页稿的 7 页副本跑真实 ppt-master Beautify 对话，经人工纠正后，在 P05 early gate 停住、回复「继续」后从 P06 续写并导出 7 页可编辑 PPTX，附首页前后对比和逐页文字/原生形状核验。本批仅验证 P05 一次暂停，默认模型自主行为与 46 页长链另行验收。
> **影响面**：`skills/public/ppt-master/` 规则文档、`backend/seed/agents/ppt-master/SOUL.md`、`SEED_VERSION` 与既有版本断言；不改后端运行逻辑/前端/部署。

## 改动

- `skills/public/ppt-master/SKILL.md`（P7/P12/P20）、`workflows/governance/failure-recovery.md` §3、`workflows/profiles/beautify-pptx.md` §5、`workflows/generate-pptx.md` Step 6：
  最终确认本身即禁止重问 Stage 1/2，缺规格/锁直接按恢复表补写；`spec_lock.md` 有效上下文复用、每 5 页及恢复时重读；长 deck 可在 P05 early gate 后暂停，「继续」后从 P06 或首个缺失页顺序续写，末页后直接 final gate 与导出。
- `backend/seed/agents/ppt-master/SOUL.md`：D3.3/D3.4/D3.5 与长内容说明同步。
- `skills/public/ppt-master/TRAINERAI_PATCHES.md`：登记 P29。
- `backend/app/gateway/seed.py`：`SEED_VERSION` → `2026-11-16.3`，评审小修补丁后 → `2026-11-16.4`；`backend/tests/test_seed_agents.py` 仅同步既有版本断言。

## 评审小修（2026-11-17）

- 统一“用户明确改方案”的例外：P20、SOUL D3.5、failure-recovery §3、Beautify §5 均补充“只有用户新的明确修改才能重开对应阶段；普通恢复/缺文件不算修改”；`SEED_VERSION` 由 `2026-11-16.3` 升至 `2026-11-16.4` 并重新 reseed。
- 补齐人工干预记录：首轮在 P05 前提前写入 `p06.svg`，已停止该轮并删除该页；`msg-06` 要求删除 P02 新增编号、P03 多余「？」、P04 新增「警惕 · 假 STAR」并恢复 P01 标签冒号；`msg-07` 补齐 P04「16.1万，」逗号、P05 五个步骤前缀和两个小标题冒号。最终 7 页为“Doubao + 人工纠正后通过”，不是默认模型一次自主交付。
- 口径收敛：本批只定义并验证 P05 一次暂停；`P10/P15/...` 多轮分批评审未实现、未验证，46 页长链仍需单独验收。
- 证据限定：文字核验是非空白字符多重集 + 页数/页首行/页序；页内字符串顺序未做严格级证明，另做了 7 页导出渲染的逐页目检。
- 视觉修版：交付副本仅加宽 P01 副标题文本框（`—— 选对店长，带好门店` 不再把“店”单独换行），不改文字、页序或其余版式；运行时对话原始导出保留作证据。

## 验收记录

- `uv run python -c 'from app.gateway.seed import seed_agents; print(seed_agents())'` → `6`；运行时 `backend/.deer-flow/agents/ppt-master/.seed_version` → `2026-11-16.4`；运行时 SOUL 与 seed SOUL `cmp` 一致且含“只有用户新的明确修改”例外。
- `python3 skills/public/ppt-master/scripts/attribution_guard.py` → exit `0`。
- 真实对话 `pptbeautify7p-20261116-1`：最终确认后未重问 Stage 1/2；P01–P05 只跑一次 early gate 并报告 `P01–P05 / 7` 后停止；回复「继续」后新写入页为 `p06.svg`（00:42）→ `p07.svg`（00:46），P01–P05 文件 mtime 未变，恢复批次无 `ppt_to_md` / `project_manager init` / `import-sources` / `beautify_identity` 调用；P07 后直接 final gate + 导出。
- final gate：`7/7 passed, 0 errors, 0 warnings`；`svg_to_pptx.py --native-charts-and-tables` POSTFLIGHT `status=passed quality_gate=passed slides=7 warning_categories=0`。
- `ppt_to_md.py` 源 7 页副本 vs 导出稿 → 两侧均 7 页；P01–P07 页首行逐页一致（`temp/ppt-beautify-7p/verify/ppt-to-md-after-review.txt`）；逐页非空白字符多重集 7/7 一致（`temp/ppt-beautify-7p/verify/text-check-after-review.txt`）。
- python-pptx（递归 group）→ 7 页；每页原生文本形状数 `7/32/7/17/27/30/10`；P03 原生表格 1 个；对话原始导出 248,562 B，交付副本 247,807 B。
- 7 页导出渲染目检（交付目录 `render-final/grid.png`）→ 7 页阅读顺序与源稿版式一致；页内字符串顺序未做严格级证明。
- 导出：[`店长招聘与汰换-美化版-7页.pptx`](</Users/littlebear/Documents/ppt-beautify-7p-delivery/店长招聘与汰换-美化版-7页.pptx>)（交付副本 247,807 B，sha256 `489f4c1e…b9c8`）；首页对比：[`cover-before-after.png`](</Users/littlebear/Documents/ppt-beautify-7p-delivery/cover-before-after.png>)。运行时对话原始导出为 248,562 B / `ef9fb7e3…6625`，仅用于对照，不作为最终交付。

## 默认模型 qwen3.8-flash 干净复测（2026-11-17，未通过）

- 独立 runtime/thread 中按默认模型重跑 7 页：07:42 启动，08:30 最终确认；08:31/08:32 写出 `design_spec.md` / `spec_lock.md`；08:35–08:41 反复读 `executor-base.md` / `shared-standards-core.md` / `design_spec.md`，`svg_output/` 仍为空，未写 P01、未导出；依计划“持续空读而无新产物”边界在 08:41 停止。
- 本轮未删页、未人工补字、未人工改版，因此不构成交付；默认模型仍未通过“干净 7 页自主交付”。此前 7 页 PPTX 仍为 ARK Doubao + 人工纠正结果，不能宣称 qwen 已修复。证据见 `temp/ppt-beautify-7p/qwen-retest-result.md` 与 `qwen-turn-05.log`。

## 偏差与边界

- 真实对话所需 360 默认模型 `qwen3.8-flash` 当日限额已满（401 `单日限额已满`，全部 360 模型同键受限），运行时改用 ARK `doubao-seed-2-1-pro-260915`；仅通过 `temp/` 临时配置注入，仓库模型/seed 配置未改。
- 首轮执行在 P05 前误写了 `p06.svg`；已停止该轮、删除提前页，并让同一 thread 只对 P01–P05 重跑 early gate 并停住，最终验收以修正后的 `P05 → 继续 → P06` 序列为准。
- 导出后又做了两轮人工文字修复：`msg-06` 删除 P02 新增编号、P03 多余「？」、P04 新增「警惕 · 假 STAR」并恢复 P01 标签冒号；`msg-07` 补齐 P04 逗号、P05 五个步骤前缀和两个小标题冒号。7 页最终稿是人工纠正后的结果，不能证明默认模型一次自主交付。
- 本批只定义并验证 P05 一次暂停；`P10/P15/...` 多轮分批评审未实现、未验证，46 页长链仍需单独人工验收。
- 交付副本仅人工加宽 P01 副标题文本框以修掉“店”单独换行；运行时对话原始导出未改，保留作过程证据。
- 本批不做 46 页回放、哈希审计、全页面视觉回归、全量 backend / frontend check、Playwright；不 commit、不 push、不建 PR、不部署。
