# 聊天首轮回复与首页细节修复记录

> **状态**：已实施并完成本地验收；远端 PR、OCR 与合入状态以 GitHub 为准。
> **TL;DR**：修复首轮回复被隐藏 Gallery 分享请求带离聊天页的问题；三条本版隐藏入口旧场景改为明确跳过；助教继续下移并更新两处能力词条。仅前端与测试，无付费 API 调用、无部署。

## 变更摘要

- 普通聊天与 Agent 聊天的首轮回复可正常展示、保留会话 URL、恢复 ready，且不会请求动态追问。
- 根因是隐藏 Gallery 的“分享到灵感库”仍在回复后挂载并请求 `/api/gallery/categories`；401 经共享请求封装转为登录跳转。当前版本不再渲染该分享入口，也不再预取 Gallery 设置或分类。
- Gallery 长提示词自动发送、Writer 新建文档成功/失败三条深链旧用例按当前产品范围明确跳过；深链页面代码保留，其他 Gallery 安全预填用例继续执行。
- 首页助教在桌面端进一步下移，底边接近“视频课”标题；图片动效第三词改为“纸艺拼贴”，AI 视频第一词改为“精密工程”，中英文同步。

## 验收记录

- 修复前 `chat-diagnostic.log` 记录 SSE 200 与回复 URL 更新后，`/api/gallery/categories` 返回 401，随后进入 `/login?next=...` 并回到 `/workspace`；`chat-regression-red.log` 复现回复不可见。
- `CI=1 E2E_PORT=3104 pnpm exec playwright test tests/e2e/chat.spec.ts tests/e2e/agent-chat.spec.ts tests/e2e/gallery-auto-send.spec.ts tests/e2e/writer-create-doc.spec.ts --retries=0 --workers=1 --trace=off` → **11 passed / 3 skipped**；日志 `chat-and-hidden-green.log`。
- `CI=1 E2E_PORT=3106 pnpm exec playwright test --retries=0 --workers=1 --trace=off` → **89 passed / 3 skipped (1.9m)**；日志 `full-e2e-chat-fix-final.log`。
- `pnpm exec vitest run tests/unit/components/workspace/workspace-home-i18n.dom.test.tsx` → **2/2 passed**；随后 `pnpm test` → **263 files / 1702 tests passed**；`pnpm check` → **0 errors / 121 个既有 warnings**，Prettier 通过；日志 `frontend-unit-check-final.log`。
- 最终 `{ make check && cd frontend && pnpm test && cd .. && git diff --check; }` → 退出码 0；knip、i18n 605 叶子、docs convention、diff check 全过，单元测试再次 **263 files / 1702 tests passed**；日志 `root-check-chat-home-final.log`。
- OCR 可维护性建议后，进一步删除不可达分享 prop、弹窗及分享专用 API helper；聊天定向 E2E **8/8 passed**（`pr6-ocr-chat-green.log`），同一根门禁与 **1702/1702** 再次通过（`pr6-ocr-root-check.log`）。
- 首页词条 RED→GREEN：`home-tags-red.log` 记录旧词导致失败，`home-tags-green.log` 记录 **4/4 passed**。
- 桌面截图 `home-polish-final.png` 已读回：助教底边与视频课标题接近，不遮挡卡片，两处新词可见。

以上日志与截图均位于 `temp/workspace-home-final-design/`。

## 边界与人工验收

- 未调用生图、LLM、TTS 或其他付费 API；未修改后端、价格、真实配置或 Docker，未访问或部署测试/公司服务器。
- 本地预览：`http://localhost:3001/workspace`。人工只需复核助教与视频课标题的相对位置、两处词条，以及普通/Agent 首轮回复不再跳回首页。
