# 模型模式阶梯（闪速 / 标准 / Ultra）

> **TL;DR**：用户模式收敛为闪速、标准、Ultra，并按档过滤模型与派生思考/规划/子代理参数；默认模型为 qwen3.8-flash、claude-sonnet-5-5、qwen4-max，DeepSeek 仅可选；`config_version` 升至 32，不执行 DSML、不部署、不改价格或密钥。

## 变更摘要

- 后端新增 `modes` 与 `mode_defaults` 配置、合法性校验及 `/models` 下发；factory 阻止 `modes` 泄漏到 provider，并避免显式禁用态的 `reasoning_effort` 重复构造参数。
- 花名册新增 qwen4-max 与 kimi-k3.1-pro；DeepSeek 使用 `@360-latency`，四个思考模型统一 `when_thinking_*` 与禁用态 `minimal`。
- 前端统一三档模式、旧 `pro/thinking` 迁移、旧后端模型列表回退、档内手动选择保留与三级默认回退；删除思考档和独立推理深度 UI。
- 提交参数由模式与模型能力派生：闪速关闭思考/规划/子代理，标准与 Ultra 使用 high effort，Ultra 开启子代理；Claude 不开启 thinking。

## 配置与边界

- `config.example.yaml` 与本地符号链接的 `config.yaml` 已迁移到 `config_version: 32`；本地迁移断言既有 `api_key`、`pricing`、`video_gen` 不变。
- 标准档默认 Claude；DeepSeek 在闪速/标准中仅可手动选择，不是任何档位默认。
- 未运行 `RUN_LIVE=1` 付费测试，未部署，未修改 DSML、后端 agent 管线、价格或密钥。

## 验收记录

- `QIHU_API_KEY=test-key ARK_API_KEY=test-key CI=true uv run pytest tests/test_model_modes_config.py tests/test_360_new_models_roster.py -q` → `36 passed`。
- Task 5 前端定向 Vitest（模式、迁移、提交矩阵及 InputBox 回归）→ `12 files passed / 63 tests passed`。
- `pnpm typecheck` → exit 0。
- config 探针 → `flash=qwen3.8-flash, standard=claude-sonnet-5-5, ultra=qwen4-max`；其余在册模型 `modes=[]`。
- `python3 scripts/check_docs_convention.py` → `OK 仓库治理约定全过`。
- `make check` → exit 0；Ruff、ESLint/TypeScript/Prettier、knip、i18n 与 docs convention 全过（ESLint 存量 121 warnings、0 errors）。
- `CI=true pnpm check && CI=true pnpm test` → exit 0；`272 files passed / 1782 tests passed`。
- `QIHU_API_KEY=test-key ARK_API_KEY=test-key CI=true uv run pytest tests/ -q --deselect tests/test_video_voices.py::TestDoubaoVoicesInEndpoint::test_no_lang_includes_doubao_curated_voices --deselect tests/test_video_voices.py::TestDoubaoVoicesInEndpoint::test_lang_zh_includes_doubao_curated_voices` → `10245 passed, 31 skipped, 2 deselected`。
- 上述两条语音用例单独以 `VOLCENGINE_VOICE_ACCESS_KEY=test-key` 运行 → `2 passed`；合计后端 10247 条全绿。分段是因为全局豆包占位 key 会改变其他测试的执行环境，两条用例未省略。

## 回滚与后续

- 前端、后端与 config 需同批回滚；旧前端无法显示新 `standard` 设置，回滚后需重选模式或清理 `deerflow.local-settings`。
- 部署前仍需按风险清单做 `@360-latency` 直连 probe；本批不把定稿前真实探针冒充部署验收。
- reliability 后续批次应在本 PR 合入后 rebase，并更新模型校正分支与测试假设。
