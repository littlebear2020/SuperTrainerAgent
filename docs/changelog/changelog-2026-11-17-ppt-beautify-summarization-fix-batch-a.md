# PPT 美化摘要修复 Batch A：上下文完整性

> **状态**：部分执行（PR A 实现 + 评审修复 + 本地完整门禁 + 推送完成，新 HEAD `c17df653` 的 quick-checks/backend-unit-tests/AI+OCR 全绿、OCR 增量 0 发现；qwen P01 冒烟已运行：框架修复在真实重压下证明正确，但 authoring read-heavy 致 P01 未产出（FAIL+EXERCISED）；经用户决策 PR A 保持未合并、PR B 未开始，authoring 另开 §6 单变量计划；不授权部署或 46 页回放）。
> **TL;DR**：修复无效摘要源、旧占位 checkpoint 复用与最新用户消息丢失（字符级尾部重试、精确 sentinel 校验、fresh/reuse 直接用户保留、结构化日志）；qwen 冒烟证实零占位 checkpoint/确认保留/fallback 行使/无重问，但 P01 因 executor-base/shared-standards-core 重读未产出（独立 authoring 问题）；未做 46 页回放、默认模型完整交付或部署。

## 变更记录

- 测试 helper 增加可注入 `trim_tokens_to_summarize` 与 `token_counter`，默认行为不变。
- 新增单行工具尾部、System-only 来源、空/占位模型响应及 `ToolProtocolError` 的 sync/async 公共链测试。
- 正常裁剪保持上游结果；仅在空或仅 System 时按原 30K 预算进行无 `start_on` 的字符级尾部重试。
- `_create_summary` / `_acreate_summary` 共用来源校验、prompt 构建与空/精确占位响应校验；失败由既有外层透传，不 stash 新 checkpoint。
- 空/两个精确历史 sentinel checkpoint 立即失去复用资格；fresh 成功才覆盖，失败保持透传且下次仍不得复用旧值。
- 统一 partition 末尾直接保留最新真实用户消息，按对象身份去重并维持原始相对顺序；隐藏、动态 reminder、summary/skill ledger 等框架 Human 不作为用户指令选取。
- fresh/reuse/checkpoint 拒绝记录 `mode`、消息数、cutoff、近似 token 与 thread/agent；字符级重试和最终透传记 WARNING，结构化记录不含正文、工具结果或摘要全文；保留既有异常 traceback 诊断。
- 更新 `backend/docs/summarization.md`，明确有效源、透传不推进 cutoff 与日志口径；token 统计口径疑点仅登记 backlog，不在本批修改。

## 验收记录

- `cd backend && PYTHONPATH=. uv run pytest tests/test_summarization_middleware.py -q`（未修改基线）→ `74 passed, 2 warnings in 2.51s`。
- `cd backend && PYTHONPATH=. uv run pytest tests/test_summarization_middleware.py -q`（新增测试、旧实现）→ `14 failed, 76 passed, 2 warnings in 1.19s`；单行尾部 sync/async 均为摘要模型 `Called 0 times`，System-only 被错误调用摘要模型，空/两个历史占位响应均错误进入重建窗口。
- 同一 RED 运行中的 sync/async `ToolProtocolError` 穿透与 stash 清理保护用例通过，确认该既有语义未退化。
- `cd backend && PYTHONPATH=. uv run pytest tests/test_summarization_middleware.py tests/test_tool_call_protocol_middleware.py -q`（最小修复后）→ `125 passed, 2 warnings in 3.49s`。
- `cd backend && uv run ruff format tests/test_summarization_middleware.py && PYTHONPATH=. uv run pytest tests/test_summarization_middleware.py tests/test_tool_call_protocol_middleware.py -q` → `1 file reformatted`；最终 `125 passed, 2 warnings in 1.59s`。
- `cd backend && uv run ruff check packages/harness/deerflow/agents/middlewares/summarization_middleware.py tests/test_summarization_middleware.py` → `All checks passed!`。
- `cd backend && PYTHONPATH=. uv run pytest tests/test_summarization_middleware.py -q`（Task 2 新增测试、Task 1 实现）→ `12 failed, 96 passed, 2 warnings in 1.19s`；两个历史 sentinel 在低增长下仍被复用，fresh/reuse 窗口的原始最终确认对象计数均为 `0`。
- `cd backend && PYTHONPATH=. uv run pytest tests/test_summarization_middleware.py tests/test_dynamic_context_middleware.py tests/test_view_image_middleware.py tests/test_tool_call_protocol_middleware.py -q`（Task 2 最小修复后）→ `215 passed, 2 warnings in 8.13s`；hook、技能 ledger/rescue、五字段 checkpoint 与 AI/Tool 配对回归均通过。
- `cd backend && uv run ruff check packages/harness/deerflow/agents/middlewares/summarization_middleware.py tests/test_summarization_middleware.py && uv run ruff format --check ...` → `All checks passed!`；`2 files already formatted`。
- `cd backend && PYTHONPATH=. uv run pytest tests/test_summarization_middleware.py -q -k 'summary_logs_fresh_and_reuse or old_sentinel_rejection_is_logged or character_fallback_is_logged or summary_source_passthrough_logs or asummary_fresh_log'`（新增日志测试、Task 2 实现）→ `5 failed, 108 deselected`；目标日志均不存在。
- `cd backend && PYTHONPATH=. uv run pytest tests/test_summarization_middleware.py -q`（结构化日志实现后）→ `113 passed, 2 warnings in 0.72s`；合成用户、工具、摘要秘密标记均未进入日志。
- `cd backend && PYTHONPATH=. uv run pytest tests/test_summarization_middleware.py tests/test_dynamic_context_middleware.py tests/test_view_image_middleware.py tests/test_tool_call_protocol_middleware.py -q`（PR A 最终定向/相邻回归）→ `220 passed, 2 warnings in 2.39s`。
- `cd backend && make test` → `10075 passed, 89 skipped, 437 warnings in 328.00s`。
- `cd backend && make lint` → `All checks passed!`；`1317 files already formatted`。
- 根 `make check`（首次）→ 后端 lint 与前端检查通过，计划状态头“执行中”触发 docs convention 唯一违例；改为约定的 `未执行（执行中…）` 后，根 `make check` 复验退出 `0`，最终输出 `OK 仓库治理约定全过`。
- `python3 scripts/check_docs_convention.py` → `OK 仓库治理约定全过`。
- `git diff --check` → 无输出，退出 `0`。
- OCR 修复后 `cd backend && PYTHONPATH=. uv run pytest tests/test_summarization_middleware.py tests/test_dynamic_context_middleware.py tests/test_view_image_middleware.py tests/test_tool_call_protocol_middleware.py -q` → `224 passed, 2 warnings in 2.53s`。
- OCR 修复后 `cd backend && make test` → `10079 passed, 89 skipped, 431 warnings in 148.12s`。
- OCR 修复后 `cd backend && make lint` → `All checks passed!`；`1317 files already formatted`。
- OCR 修复后根 `make check` → 退出 `0`；docs convention 输出 `OK 仓库治理约定全过`。

## PR 与评审记录

- PR A：`#27`，初始代码/文档 HEAD `fecaf8ae0784968f0f0094f87ab08c6480a4066e`。
- 初始远程检查：`backend-unit-tests`、`quick-checks`、AI Code Review 均为 `pass`。
- OCR medium（traceback / 异常时窗口日志）：新增 4 个回归在修复前 `4 failed, 113 deselected`；将 fresh/reuse 决策日志移到 handler 前并恢复 sync/async `logger.exception` 后，`117 passed, 2 warnings`。
- OCR medium（唯一用户被保留后来源为空）：不接受恢复 sentinel 的建议，因为计划硬条件要求无有效原始来源时透传且不推进 checkpoint。合成探针实际输出：`handler_received_original=True`、`summary_model_calls=0`、`new_checkpoint=None`、`latest_user_present_in_passthrough=True`；已在原 OCR 线程回复。
- 两项 OCR 均已在原线程回复处理结果；评审修复代码 HEAD 为 `6e2f660b4385a21efb8f98647e8a47960671d670`。
- `git push`（评审修复与阻塞记录）→ 300 秒后 `fatal: unable to access ...: SSL connection timeout`；远程 PR HEAD 保持 `fecaf8ae0784968f0f0094f87ab08c6480a4066e`，按停止纪律未重试。
- 已在 PR #27 顶层评论记录输入与网络阻塞；PR 未合并，PR B 未开始。
- 修订输入后 `git ls-remote` → `Empty reply from server`；随后一次实际 `git push` → `Recv failure: Operation timed out`。GitHub API 可同时正常响应，但不为绕过该故障改用其他推送通道。
- 真实运行预检（仅加载配置，不调用模型）→ `ValueError: Environment variable QIHU_API_KEY not found for config value $QIHU_API_KEY`；当前 shell 的 `QIHU_API_KEY=UNSET`。未读取/复制 `.env`，未更换模型。

## 真实复测记录

原 7 页副本与旧 qwen 日志在执行时缺失。用户于 2026-11-17 明确授权修订：允许从现存 46 页原稿一次性制作并冻结新 7 页输入，以新 hash 完成两轮 qwen 验收；不再要求恢复原输入。

- 输入准备命令：`python-pptx` 从 46 页源选取 `[1, 7, 10, 20, 24, 36, 46]` 并保存到 `temp/ppt-beautify-7p/店长招聘与汰换-7页代表页.pptx` → 源 `slides=46, sha256=e2cc3fc95e6696253b9e73f41aeb7a0736408d4c34cb30929d16abb62b875754`；新输入 `slides=7, bytes=7351912, sha256=16f7f0dbf06a559b8889333a0eae9a80349d39d92132efae7a369fe50ea9ff24`，`p03_native_table=True`。
- 代码 HEAD：`c17df653c85977695946905f9a97722fa8edff73`（PR A 最终 HEAD，含 OCR 修复 `6e2f660b`；已推送，远程三项检查全绿，OCR 增量 `0 finding(s) across 1 selected item(s)`）。
- 驱动方式：进程内 `DeerFlowClient`（与网关共用同一 `create_agent`/`_build_middlewares` 工厂，含 `_create_summarization_middleware`），显式 `SqliteSaver` 落盘 `temp/qwen-summary-pr1-home/data/checkpoints.db`；`DEER_FLOW_CONFIG_PATH` 原样引用项目 config（`token_budget.enabled=false`、`max_recursion_limit=2500`、`qwen/qwen3.8-flash`、summarization trigger 32000/trim 30000/keep 10）；fresh home/thread/project/sandbox，seed `SEED_VERSION=2026-11-16.4`。半交互式同一 thread 多轮 = 一次冒烟。
- qwen thread：`abf69dab-4db6-4133-80bb-315db8a5cc8f`。起止（UTC）：turn1 美化请求约 03:01–03:11（596s，33 调用）→ Stage 1 澄清；turn2 Stage 1 确认约 03:13–03:22（537.6s，58 捕获=33 回放+25 新）→ Stage 2 三选一（推荐 B）；turn3 最终确认 B 真实 authoring 约 03:36–04:29（3012s，100 新调用）。
- 压缩统计（累计实时日志 + checkpoint 审计）：`character_tail_fallback=16`、`fresh=17`、`reuse=125`、`pass_through=2`；1798 checkpoint、17 个有效摘要 checkpoint、**bad_count=0（零占位/空）**；最新摘要有效（`## SESSION INTENT Re-layout/beautify…`），cutoff_index=284/message_count=294/approx_tokens=360392 随 fresh 正常推进；`exercised=true`。
- 确认保留：最新真实用户消息（排除 `<uploaded_files>`/`<workspace_files>` 框架包装）= 「确认方案 B…」，`latest_real_user_is_B=True`；17 次压缩/100 次调用**全程无重问 Stage 1/2**（行为级证明）。
- P01 结果：**未产出**。turn3 硬停 `TOOL_LIMIT_100_NO_P01`（100 次真实调用=49 read_file/45 bash/6 write_file，6 次 write 全为 design_spec/spec_lock/confirm_state/recommendations，无 SVG）。重读目标（全态 65 次 read_file）：`executor-base.md`×8、`spec_lock.md`×6、`generate-pptx.md`×5、`design_spec.md`×5、源 md×4、`shared-standards-core.md`×4、native-data-interface×3、style/mode refs 各×2。
- 判定：**FAIL+EXERCISED**。框架修复（PR A）证明正确；失败属 ppt-master read-heavy authoring（计划 §6「摘要健康…P01 仍因 executor-base/shared-standards-core 重读而失败」分支），独立于框架 bug，本批不改 prompt/5000 skill cap。
- 处置（用户决策）：PR A 保持未合并（handoff「P01 通过才合并」门槛）、PR B 未开始、未增加付费复测；authoring 另开 §6 单变量计划。证据保留 git-ignored `temp/qwen-smoke/`（turn-01~04/audit/result/checkpoints.json）与 `temp/qwen-summary-pr1-home/`；本批沙箱容器已停，无孤儿进程。
- 驱动 note：turn3 首次尝试因 `client.stream` values 模式 resume 回放历史（client.py:648-676）被监控误判 REASK（零模型调用）；修复为按 turn 前消息 ID 过滤回放后重发 B 真实运行（非付费复测）。usage `output_tokens` 异常偏低为已知 qwen token 口径问题（findings-backlog），非行为信号；权威调用数以 checkpoint 消息为准。
- 最终 PPTX：Batch A 不交付；Batch B 尚未开始。新输入位于 git-ignored `temp/`；未覆盖或删除既有 Doubao 交付，未做 46 页回放或部署。
