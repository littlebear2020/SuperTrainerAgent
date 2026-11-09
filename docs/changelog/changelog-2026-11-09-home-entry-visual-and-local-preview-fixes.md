# 首页视觉与本地预览问题修复

> **状态**：代码已执行并提交本地分支；部署待进行。
> **TL;DR**：按 Demo 精修首页内容层并补齐 PPT/文章作品图；修复播放中断误报与近期项目跳转；本地预览启用视频服务并恢复 MCP 配置。无 backend/API/schema 改动。

## 变更摘要

- 首页保留原背景，新增入口图标、卡片箭头、媒体标签、目标标签、精细边框与 hover 层级；修正标题/徽标拥挤。
- 播放被暂停打断产生的 `AbortError` 不再误记为媒体失败；对应作品进入 `playing` 后会清除旧错误，真实媒体错误仍显示降级提示。
- 新增本地设计并浏览器渲染的 `ppt-work.webp` 与 `article-work.webp`，均为 960×600 WebP。外部生图服务返回 401 后未留下半成品，改用本地 SVG 设计与浏览器渲染，中文经图像检查正常。
- 近期项目“查看全部”改到既有 `/workspace/chats` 对话搜索列表。
- 本地预览的 503 根因是 `video_gen` 未启用；仅修改 gitignored 本地配置后服务成功初始化。真实错误仍保留错误态，不伪装成空数据。
- 本地缺少 gitignored `extensions_config.json` 导致 MCP 列表为空；从现有示例恢复后，配置加载器实际识别 metaso、so_360_search_mcp、so_spider、zhitumcp 四项。未修改后端或提交任何密钥。

## 验收记录

- 播放与导航定向 DOM 测试 → `10 passed`；视觉/图片/导航相关测试 → `14 passed`。
- `cd frontend && pnpm check` → 退出码 0；ESLint 0 errors，TypeScript 通过，Prettier 全过。
- `cd frontend && pnpm test` → `Test Files 245 passed (245)`，`Tests 1558 passed (1558)`。
- 隔离 Playwright 实例执行 `pnpm test:e2e` → `55 passed`，无复用本地 3000 端口造成的登录态污染。
- `cd frontend && pnpm perf:check` → 退出码 0，既有五路由预算未改。
- 仓库根 `make check` → 退出码 0；backend ruff、frontend 静态/knip/i18n 与文档检查通过。
- `python3 scripts/check_docs_convention.py` → `OK 仓库治理约定全过`。
- 图片 ffprobe → 两张均为 `webp`、960×600；15,268 B 与 12,822 B。
- 最终浏览器截图检查 → 视频卡显示真实画面，无错误 overlay；图文卡三张作品图均正常；点击“查看全部”实际到达 `/workspace/chats`。
- 本地 Gateway 日志 → `Video gen service initialized`；MCP 配置加载器 → 4 个真实配置项。

## 部署与边界

- 尚未部署，仅保留本地提交。
- 无 backend、数据库、Docker、Skills、video-templates、锁文件或真实服务器配置改动。
- 本地 `config.yaml`、`extensions_config.json` 与前端环境文件均为 gitignored，不进入 commit。
