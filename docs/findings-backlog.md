# Findings Backlog（范围外发现登记处）

> 任务中发现但不顺手改的问题登记在此，一行一条：`- [ ] YYYY-MM-DD 一句话 + 指针`。完成后勾掉并链接处理批次。历史内部记录未纳入公开快照。

- [ ] 2026-11-10 `vimax-video` 的 `skills: []` 被新会话 `SuggestionList` 回退为全站已启用技能列表，与后端“未绑定技能”语义可能不一致；指针：`frontend/src/components/workspace/input-box.tsx:1298`、`backend/seed/agents/vimax-video/config.yaml`。
- [ ] 2026-11-11 旧全量模型探针仍硬编码 Terra 上游 `openai/gpt-5.6-terra`，且缺新增 DeepSeek；本批只使用新 opt-in 探针，不顺手改旧脚本；指针：`backend/scripts/model_connectivity_check.py:46-66`、`backend/tests/test_model_roster_live.py`。
- [ ] 2026-11-16 宿主已配置密钥但沙箱默认未注入，需研究 run/Agent/技能级授权与诊断闭环；指针：`docs/2026-11-16-sandbox-secret-injection-investigation.md`。
- [ ] 2026-11-17 核对 qwen/Doubao `usage_metadata`、流式累计与近似 token 的统计口径，本批不修 provider/token 统计；指针：`backend/packages/harness/deerflow/agents/middlewares/token_attention_middleware.py:107-143`、`backend/packages/harness/deerflow/runtime/journal.py:379-388`。
- [ ] 2026-11-19 执行包写盘改用 `NamedTemporaryFile` 后文件权限由 0644 变为 0600（同 umask 022 受控对比），未在跨 uid 的 Docker/AIO 挂载环境验证；默认 LocalSandboxProvider 同 uid 判断低风险，需要时按 umask 恢复 0o644 并补测试；指针：`skills/public/ppt-master/scripts/project_management/execution_context.py:386-402`、`docs/changelog/changelog-2026-11-19-ppt-beautify-executor-context-pack.md` §6。
- [ ] 2026-11-19 `Native-ready` 多键 map（`a=no; b=yes`）只按首个条目判定，该页不加载 native_table 规则，与 HEAD 正则行为一致但与文档语法 `<key>=yes|no; ...` 不符；指针：`skills/public/ppt-master/scripts/project_management/execution_context.py:211-222`、`skills/public/ppt-master/references/plan-core.md:142`。
