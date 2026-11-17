# PPT 美化长课件快速交付优化实施计划

> **状态**：已执行（2026-11-17 完成；本地演示交付完成，经人工纠正后通过；默认模型 qwen 干净复测未通过（08:41 空读停止、无 P01）、46 页长链待验证；未 commit；changelog：`docs/changelog/changelog-2026-11-16-ppt-beautify-long-deck-quick-delivery.md`）。
> **TL;DR**：只修确认恢复、锁重读和同 thread 顺序续写规则；reseed 后用原稿截取 7 页，经人工纠正后真实跑出可编辑 PPTX 和首页对比图。本批仅验证 P05 一次暂停；默认模型 qwen 干净复测未通过，46 页长链另行验收；不新增静态测试、不做 46 页回放、不做哈希审计；本地效果交付不提交、不跑全量测试。

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal：** 用最小规则修改消除“最终确认后重问、反复读取、没有交付”，并让用户直接看到原课件代表页面的美化效果。

**Architecture：** 复用现有 `confirm_state.json` 和 failure-recovery，不增加组件。完整 Design Spec 与 lock 仍先通过原质量门；Step 6 允许同一 thread 在 P05 early gate 后暂停，用户“继续”后从 P06 顺序续写。SOUL 通过递增 `SEED_VERSION` + 本地 reseed 生效。

**Tech Stack：** ppt-master 工作流文档、seed/reseed、attribution guard、现有 `python-pptx` / `ppt_to_md.py` / LibreOffice、真实 ppt-master 对话。

**Spec：** `docs/2026-11-16-ppt-beautify-long-deck-research-brief.md`（只读，保持“待核实”存档）。本批是本地快速效果验证，不宣称证明 P20 是唯一根因，也不宣称 46 页长链已验证。

## 评审决策记录

- **实现保持小修**：不新增恢复解析器、状态文件、中间件、生成脚本、自动续跑或摘要器改造。
- **只保留行为验收**：不新增静态契约测试，不做多轮 red/green；现有 `test_seed_agents.py` 仅同步 `SEED_VERSION` 断言，避免给后续测试留下旧值。
- **7 页刚好够用**：6 页会跳过 early gate，因此从原稿副本保留封面 + 6 个代表页，覆盖 P05 停止和一次“继续”，同时展示真实原稿效果。
- **不做第二次验证**：取消 46 页事故回放、哈希审计、全页面视觉回归和多份重复交付检查。
- **本地交付优先**：本批不 commit、不 push、不部署，因此不跑全量 backend；若用户后续要求提交，再按核心路径分支 + PR 和仓库门禁补齐。

## 全局约束

- 最终确认 `stage: final` + `status: confirmed` 即禁止重问 Stage 1/2；缺规格/锁是恢复入口，只有用户新的明确修改才重开对应决策。
- 上下文有效时不逐页重读 `spec_lock.md`；每 5 页及上下文失效/续写恢复时重读一次。
- 完整规格和锁通过原 Gate 1/2 后才写 SVG；同 thread 顺序分批属于连续模式，仍禁止并行分组、子代理和脚本批量生成 SVG。
- 不恢复 `confirm_ui`、跨聊天 `resume-execute` 或 `generation_mode: split`，不换模型，不修改后端运行逻辑、前端、配置或部署。
- 原 PPTX 只在副本上删页，不修改原文件；测试副本和预览放用户工作区或 `temp/`，不新增仓库测试夹具，不读取密钥或无关私人内容。
- reseed 前确认没有进行中的 run，并检查其他 agent 的运行时本地定制；全局 reseed 可能覆盖这些定制，存在冲突即停止报告。
- 真实对话若再次重问或持续重读而没有新规格/SVG，保存必要 run 证据后停止；不靠长时间重试烧 token，不顺手扩大成后端改造。

## Review Focus

- 规则是否真正进入运行时，而非只修改 seed 源文件 → Task 1 reseed 预检。
- 最终确认后是否不再重问，P05 后是否能停住并从 P06 续写 → Task 2 真实对话。
- 续写是否避免重跑源解析/初始化和重做前五页 → Task 2 工具顺序检查。
- 最终是否有 7 页、文字不变、原生可编辑的 PPTX 和可见对比，而非只有 run=success → Task 2 交付检查。

## 文件范围

**允许修改：**
- `skills/public/ppt-master/SKILL.md`（P7、P12、P20）
- `skills/public/ppt-master/workflows/governance/failure-recovery.md`（仅 §3 互证口径）
- `skills/public/ppt-master/workflows/generate-pptx.md`（仅 Step 6 续写 cadence）
- `skills/public/ppt-master/workflows/profiles/beautify-pptx.md`（仅最终确认后的短路规则）
- `skills/public/ppt-master/TRAINERAI_PATCHES.md`（登记本批偏离）
- `backend/seed/agents/ppt-master/SOUL.md`（D3.3/D3.4/D3.5 与长内容说明）
- `backend/app/gateway/seed.py`（仅 `SEED_VERSION` 与原因注释）
- `backend/tests/test_seed_agents.py`（仅同步既有版本断言，不新增测试）
- 本计划与 `docs/changelog/changelog-2026-11-16-ppt-beautify-long-deck-quick-delivery.md`

**禁止修改：** 其他后端运行逻辑、其他测试、摘要/工作区中间件、其他技能、前端、根 `config.yaml`、部署文件、原事故源 PPTX 与既有解析产物。若必须越界，停止并回到计划。

---

### Task 1：修改规则并确保本地运行时生效

**Produces：** 最终确认后直接补规格；P7 与 SOUL 不再要求逐页重读；同 thread P05 后顺序续写有统一规则；运行时不再使用旧 SOUL。

- [x] **Step 1：收敛确认恢复规则。** 修改 P20 与 failure-recovery：最终确认本身即禁止重问 Stage 1/2，互证只审计已存在的下游产物；缺完整 Design Spec 时按现有恢复表补写，缺 lock 时从规格重建，孤立 lock 不得反推规格。Beautify §5 增加短路：已有有效最终确认且用户无新修改时，不再推荐或等待，直接补规格/锁。
- [x] **Step 2：收敛重读与续写规则。** P7 和 SOUL D3.3 统一为“有效上下文复用，每 5 页及恢复时重读”。SOUL D3.4 改为允许同 thread 顺序分批，仍禁并行分组、子代理和脚本批量生成。Generate Step 6 允许 P05 early gate 后报告 `P01–P05 / total` 并暂停；用户“继续”后从 P06 或首个缺失页开始，不重跑源解析/初始化/身份抽取，末页后直接 final gate 和导出。保留原有 early/final checker、磁盘检查和 consolidated repair。同步 P12、D3.5、长内容说明并登记 `TRAINERAI_PATCHES.md`。
- [x] **Step 3：递增版本并完成本地 reseed。** 将 `SEED_VERSION` 改成新的唯一值，只改常量与原因注释，同步 `test_seed_agents.py` 既有版本断言。无运行冲突时执行 `cd backend && uv run python -c 'from app.gateway.seed import seed_agents; print(seed_agents())'`，或等价触发启动 seed hook；随后用 `get_paths().agents_dir` 确认运行时 `ppt-master/.seed_version` 为新值、SOUL 含新规则。不要手改运行时 SOUL。
- [x] **Step 4：只做必要完整性检查。** 运行 `python3 skills/public/ppt-master/scripts/attribution_guard.py`，必须退出 0。检查 diff 未越界、未修改 frontmatter、未新增测试或运行逻辑；本阶段不跑 pytest。

### Task 2：用原稿 7 页真实交付并收尾

**Produces：** 一份来自原稿代表页面的 7 页可编辑美化 PPTX、首页前后对比图，以及简短的实际验收记录。

- [x] **Step 1：制作 7 页副本。** 用现有 `beautify_inventory.json` 选择封面 + 6 个代表页，至少包含 1 个表格页、1 个流程/方法页和 1 个高密度页，保持原相对顺序。复制源 PPTX 到用户工作区或 `temp/`，用现有 `python-pptx` 只在副本上删除未选页面；不新增依赖、不重跑解析、不修改原文件。
- [x] **Step 2：走真实 Beautify 对话。** 要求文字与 7 页顺序不变、只美化并交付可编辑 PPTX；正常确认 Stage 1/2。最终确认后不得重问；完整规格和锁通过后才出现 P01。P05 early gate 及必要修复完成后报告 `P01–P05 / 7` 并停止，然后回复“继续”。
- [x] **Step 3：确认一次续写并导出。** 从工具顺序确认下一次页面写入是 P06，未重跑 `ppt_to_md`、项目初始化或身份抽取，也未重新生成 P01–P05；不做单独哈希审计。P07 后直接进入现有 final gate 和 Step 7，不再额外等待“继续”。final gate 的合法修复不属于续写重做。
- [x] **Step 4：检查并展示结果。** 确认导出 PPTX 存在且非 0 字节；用 `ppt_to_md.py` 比较源副本与导出文件，确认 7 页、页序和全部文字一致；用一次现有 `python-pptx` 命令确认每页有原生文本形状。用 LibreOffice/`pdftoppm` 渲染源首页与美化首页，向用户交付 PPTX 和对比图路径，不再运行其他交付检查套件。
- [x] **Step 5：简短收尾。** 勾选本计划并更新状态头；在 changelog「验收记录」写明 guard、真实对话结果、导出路径、页数/文字/原生文本检查和对比图路径。运行 `python3 scripts/check_docs_convention.py` 与 `git diff --check`。不提交、不跑全量 backend、不做 46 页回放；未验证的 46 页长链如实保留为后续事项。

## 本批完成定义

- 新规则已写入 seed 和技能，并通过 reseed 进入本地运行时。
- attribution guard 通过，改动未越出允许路径。
- 真实 7 页对话最终确认后未重问，P05 后停住，“继续”后从 P06 续写并导出。
- 用户实际获得 7 页、文字不变、原生可编辑的美化 PPTX 和首页前后对比图。
- 不宣称原 46 页已完整验证，不以全量测试、46 页回放或提交作为本地效果交付条件。

## 明确不做

- 不新增静态契约测试、恢复解析器、状态文件、中间件、生成脚本或自动续跑。
- 不做 46 页事故回放、哈希审计、全页面视觉回归、Playwright、frontend check 或全量 backend。
- 不调模型、token budget、recursion、摘要器或部署配置。
- 不 commit、不 push、不建 PR、不部署；后续提交另按仓库门禁处理。
