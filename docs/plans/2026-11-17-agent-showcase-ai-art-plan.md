# Agent Showcase 生图重做计划（4 张卡与信息图）

> **状态**：已执行（2026-11-17；变更日志：`docs/changelog/changelog-2026-11-17-agent-showcase-ai-art.md`）。
> **TL;DR**：用户指出手排的 4 张卡/信息图“结构雷同、配色丑”，并确认项目 360 key 可用；本轮改用项目 `trainerai-imagine`（360 `openai/gpt-image-2`）生成 4 张 2048×1152 画面主体，再用精确排版叠加中文，分别做成“三幕玻璃卡 / 桌面单页笔记 / 等距纸雕流水线 / 纸雕路线图”。

## 一、背景

上一轮为绕开 401，手写 HTML/SVG 排了 8 张图，导致结构都是“顶栏 + 大标题 + 卡片栅格 + 底栏”，用户反馈“画面结构雷同、配色非常丑”，并确认项目 `.env` 里有可用 360 key。

## 二、方案

- 用 `skills/public/trainerai-imagine/scripts/main.ts` 的 `qihu360` + `openai/gpt-image-2`，从项目 `.env` 读取 `QIHU_API_KEY`（不写进仓库/日志）。
- 每张只生成**无文字画面主体**；中文由 HTML 精确叠加，避免模型错字。
- 四张各自不同构图：
  - RAG 卡：三块玻璃模块横排，标题与三段说明在上方，画面在下。
  - 提示词卡：俯拍桌面 flat lay，四要素写成单页纵向笔记。
  - 训练信息图：等距纸雕五阶段流水线，底部一条说明带。
  - 路线图：纸雕弯曲路径 + 四个里程碑，阶段说明沿路径分布。
- 不重做两张海报、两页漫画、两张 SVG 结构图；它们本来构图不同。

## 三、改动清单

- [x] 生成 4 张 2048×1152 生图底图（无文字），逐张检查画面。
- [x] 4 个 HTML 叠加层：精确中文、编号、页脚；修掉文字压图/低对比。
- [x] 替换 `visual/originals` 与 `visual/previews` 对应文件。
- [x] 更新 `showcase.ts` 的 tag/style/description/prompt/agent note。
- [x] 更新素材 README 的来源与总量（18,174,623 bytes）。
- [x] 定向 vitest、全量 vitest、目标 E2E、`pnpm check`、docs 门禁。
- [x] 本地 production build 预览确认 4 张在案例墙与弹窗中可读。

## 四、验收

- `pnpm check` → 0 errors。
- `pnpm exec vitest run --project node tests/unit/core/agents/showcase.test.ts` → 7 passed。
- `pnpm test` → 266 files / 1754 tests passed。
- `E2E_PORT=3184 pnpm exec playwright test tests/e2e/agent-new-chat-guide.spec.ts` → 41 passed（26.2s）。
- creative-studio 首屏媒体预算 ≤1.5MB 通过（4 张预览压到 1280×720/quality 82）。
- `python3 scripts/check_docs_convention.py` → 全过。
- 素材总量 18,174,623 bytes（<20MB）；浏览器无 console error / 404。

## 五、说明

- 360 key 从项目 `.env` 运行时读取，不进入代码、README、日志或提交。
- `openai/gpt-image-2` 输出 2048×1152；Gemini 通道只出 1408×768，所以本批统一用 gpt-image-2。
- 生图提示词与叠加页面为一次性本地工具，不进入仓库。
