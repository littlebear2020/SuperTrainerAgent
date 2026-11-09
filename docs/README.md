# SuperTrainerAgent 文档导航

本目录只保留可公开的产品与开发文档。原项目内部审计、运维手册、计划和 changelog 未纳入公开快照。

## 产品与开发

| 文档 | 用途 |
|---|---|
| `architecture-overview.md` | 系统拓扑、请求链和任务链 |
| `personal-server-deployment-guide.md` | 通用服务器部署指南 |
| `remotion-course-pipeline.md` | Remotion 课程视频管线 |
| `task-pipeline.md` | 统一任务框架、worker 池与恢复机制 |
| `360-api-reference.md` | 360 平台 API 参考 |
| `findings-backlog.md` | 新问题登记处 |

后端模块笔记见 `backend/docs/`，前端模块笔记见 `frontend/docs/`，Harness 框架说明见 `backend/packages/harness/deerflow/HARNESS.md`。

## 开发约定

- 根 `AGENTS.md` 是项目约定入口，后端和前端目录另有各自约定。
- 新计划放在 `docs/plans/`，执行记录放在 `docs/changelog/`；需要时按根 `AGENTS.md` 的格式创建。
- 不提交真实主机、账号、访问令牌、内部任务 ID 或其他敏感运维信息。
