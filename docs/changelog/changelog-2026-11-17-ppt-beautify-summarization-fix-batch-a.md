# PPT 美化摘要修复 Batch A：上下文完整性

> **状态**：执行中（Task 1/2 与结构化日志已完成定向验证；完整门禁、PR 评审与真实冒烟未完成）。
> **TL;DR**：修复无效摘要源、旧占位 checkpoint 复用与最新用户消息丢失；增加字符级尾部重试、精确 sentinel 校验、fresh/reuse 直接用户保留及无正文日志；尚未做 46 页回放、默认模型完整交付或部署。

## 变更记录

- 测试 helper 增加可注入 `trim_tokens_to_summarize` 与 `token_counter`，默认行为不变。
- 新增单行工具尾部、System-only 来源、空/占位模型响应及 `ToolProtocolError` 的 sync/async 公共链测试。
- 正常裁剪保持上游结果；仅在空或仅 System 时按原 30K 预算进行无 `start_on` 的字符级尾部重试。
- `_create_summary` / `_acreate_summary` 共用来源校验、prompt 构建与空/精确占位响应校验；失败由既有外层透传，不 stash 新 checkpoint。
- 空/两个精确历史 sentinel checkpoint 立即失去复用资格；fresh 成功才覆盖，失败保持透传且下次仍不得复用旧值。
- 统一 partition 末尾直接保留最新真实用户消息，按对象身份去重并维持原始相对顺序；隐藏、动态 reminder、summary/skill ledger 等框架 Human 不作为用户指令选取。
- fresh/reuse/checkpoint 拒绝记录 `mode`、消息数、cutoff、近似 token 与 thread/agent；字符级重试和最终透传记 WARNING，不记录正文、工具结果、摘要全文或异常 traceback。
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

## 真实复测记录

尚未执行。输入 hash、代码 HEAD、thread、压缩统计与 P01 结果将在 Task 4 按事实回填；本批不交付最终 PPTX，最终交付路径由 Batch B 记录。
