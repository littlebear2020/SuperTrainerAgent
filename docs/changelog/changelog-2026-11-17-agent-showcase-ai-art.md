# Agent Showcase 生图重做 changelog（2026-11-17）

> **TL;DR**：用项目 360 `openai/gpt-image-2` 重新生成 RAG 卡、提示词卡、训练信息图、路线图的画面主体，再精确叠加中文；四张分别采用三幕玻璃卡、桌面单页笔记、等距纸雕流水线、纸雕路线图四种构图，替代原来同一套卡片栅格。素材总量 18,174,623 bytes，测试与 E2E 通过。

## 一、背景

用户反馈上一版手排 4 张“画面结构雷同、配色非常丑”，并确认项目 `.env` 有可用 360 key。根因是此前 360 接口 401，退回 HTML/SVG 后四张共用了同一套骨架。

## 二、改动

- 用 `trainerai-imagine`（`qihu360` + `openai/gpt-image-2`，2048×1152）生成 4 张无文字底图：
  - 三块玻璃模块 / 数据流（深靛紫）。
  - 俯拍暖纸桌面 + 钢笔/荧光笔。
  - 等距纸雕五阶段流水线（暖纸、青绿、锈红、钴蓝）。
  - 纸雕弯曲路径 + 四个里程碑（赤陶/森林绿/赭黄）。
- 用精确排版叠加中文，四种不同构图：
  - RAG：标题 + 三段说明在上，画面在下。
  - 提示词：单页纵向笔记，无卡片。
  - 训练：画面主体 + 底部五段说明带。
  - 路线图：阶段说明沿纸雕路径分布。
- 更新 `showcase.ts` tag/style/description/prompt/agent note；素材 README 总量改为 18,174,623 bytes。

## 验收记录

- `cd frontend && pnpm check` → 0 errors，Prettier 全过
- `pnpm exec vitest run --project node tests/unit/core/agents/showcase.test.ts` → 7 passed
- `pnpm test` → 266 files / 1754 tests passed
- `E2E_PORT=3184 pnpm exec playwright test tests/e2e/agent-new-chat-guide.spec.ts` → `41 passed (26.2s)`；creative-studio 首屏媒体预算 ≤1.5MB 通过（预览压到 1280×720/quality 82）
- `python3 scripts/check_docs_convention.py` → `OK 仓库治理约定全过`
- 素材总数 58，总量 18,174,623 bytes（<20 MB）
- 本地 production build 预览 → 4 张加载，无 console error / 404

## 三、说明

- 360 key 从项目 `.env` 运行时读取，不写入仓库/日志。
- Gemini 通道只出 1408×768，本批用 `openai/gpt-image-2` 统一 2048×1152。
- 两张海报、两页漫画、两张 SVG 结构图未改动。
