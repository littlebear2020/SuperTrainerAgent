# Trainer AI 品牌图标更新

> **状态**：本地实施与验证完成，PR 收口进行中。
> **TL;DR**：采用几何 T + 紫色菱形作为 Trainer AI 新品牌标识，提供标准与反白变体；同步侧栏、落地页、登录/注册/初始化页、背景遮罩及 favicon，精简落地页并放大背景闪耀效果；不改后端、课程或视频管线。

## 完成内容

- 新增标准与反白 SVG 品牌标识，替换侧栏、落地页头部和 favicon；原 Deer 遮罩同步替换。
- 落地页仅保留居中的 Trainer AI 标题与进入按钮，移除副标题和三个功能卡片，放大背景 T 闪耀效果。
- 登录、注册、管理员初始化及侧栏根据主题切换标准/反白图标；欢迎文案移除鹿 emoji。
- 补充侧栏品牌路径及展开/收起深色模式测试。

## 视觉与行为复核

- 已核对深色落地页的反白图标及放大后的背景 T。
- 已核对白色登录、注册和管理员初始化页面的标准图标；初始化页背景 T 同步放大。
- 侧栏展开/收起均按主题切换图标；其他课程任务和预览服务未中断。

## 验收记录

- `cd frontend && pnpm exec vitest run --project dom tests/unit/components/workspace/workspace-header-active.dom.test.tsx` → `Test Files 1 passed (1)`，`Tests 4 passed (4)`，覆盖默认品牌路径及展开/收起深色模式。
- `cd frontend && pnpm test` → `Test Files 263 passed (263)`，`Tests 1716 passed (1716)`。
- `make check` → 退出 0；`0 errors`，Prettier 通过，i18n `604 个叶子全活`，文档约定检查通过。
- `E2E_PORT=3127 E2E_GATEWAY_PORT=3137 pnpm exec playwright test`（工作目录 `frontend`）→ `97 passed (1.8m)`，`3 skipped`，完整 E2E 无失败。
- `python3 scripts/check_docs_convention.py` → `OK 仓库治理约定全过`。
