# PPT 美化摘要修复 Batch A：上下文完整性

> **状态**：部分执行（PR A 实现、首轮远程评审及评审修复后的本地完整门禁已完成；新 7 页输入已冻结，但 Git 推送仍超时且当前进程缺 `QIHU_API_KEY`，真实冒烟未开始，未合并）。
> **TL;DR**：修复无效摘要源、旧占位 checkpoint 复用与最新用户消息丢失；增加字符级尾部重试、精确 sentinel 校验、fresh/reuse 直接用户保留及结构化日志；未做真实 qwen 复测、46 页回放、默认模型完整交付或部署。

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
- 代码 HEAD：`6e2f660b4385a21efb8f98647e8a47960671d670`（含 OCR 第一轮修复；最终 PR HEAD 待推送后记录）。
- qwen thread / 起止时间 / 压缩统计 / P01 结果：尚未产生（未运行）。
- 最终 PPTX：Batch A 不交付；Batch B 尚未开始。
- 新输入位于 git-ignored `temp/`；未覆盖或删除既有 Doubao 交付，未做 46 页回放或部署。
