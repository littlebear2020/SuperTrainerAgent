# Findings Backlog（范围外发现登记处）

> 任务中发现但不顺手改的问题登记在此，一行一条：`- [ ] YYYY-MM-DD 一句话 + 指针`。完成后勾掉并链接处理批次。历史内部记录未纳入公开快照。

- [ ] 2026-11-10 `vimax-video` 的 `skills: []` 被新会话 `SuggestionList` 回退为全站已启用技能列表，与后端“未绑定技能”语义可能不一致；指针：`frontend/src/components/workspace/input-box.tsx:1298`、`backend/seed/agents/vimax-video/config.yaml`。
