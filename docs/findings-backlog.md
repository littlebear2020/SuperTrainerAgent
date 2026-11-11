# Findings Backlog（范围外发现登记处）

> 任务中发现但不顺手改的问题登记在此，一行一条：`- [ ] YYYY-MM-DD 一句话 + 指针`。完成后勾掉并链接处理批次。历史内部记录未纳入公开快照。

- [ ] 2026-11-10 `vimax-video` 的 `skills: []` 被新会话 `SuggestionList` 回退为全站已启用技能列表，与后端“未绑定技能”语义可能不一致；指针：`frontend/src/components/workspace/input-box.tsx:1298`、`backend/seed/agents/vimax-video/config.yaml`。
- [ ] 2026-11-11 旧全量模型探针仍硬编码 Terra 上游 `openai/gpt-5.6-terra`，且缺新增 DeepSeek；本批只使用新 opt-in 探针，不顺手改旧脚本；指针：`backend/scripts/model_connectivity_check.py:46-66`、`backend/tests/test_model_roster_live.py`。
