# Agent Showcase 案例配色重制计划

> **状态**：已执行（2026-11-17；变更日志：`docs/changelog/changelog-2026-11-17-agent-showcase-color-refresh.md`）。
> **TL;DR**：按用户“黑底很差、米色背景重复、看不出区别”的反馈，只重做 creative-studio 的 6 件素材底色（薄荷、靛紫、冰蓝、赤陶、深青、淡紫），保留两张海报与两页漫画；PPT 三套与页面骨架不动。

## 一、问题与决策

用户 2026-11-17 第二轮反馈（截图圈出 4 件）：

- RAG 知识卡的黑色背景“效果很差”。
- 训练信息图与路线图/笔记卡“背景颜色重复”，放在一起“看不出区别”。
- 需要“其他更好的色彩”。

问题不是构图，而是**底色带宽太窄**：8 件重制素材里 6 件是米色/奶油纸，2 件是近黑，导致案例墙整体发灰、发闷。本轮只换底色体系，不改内容结构：

| 素材 | 原底色 | 新底色 | 目的 |
|---|---|---|---|
| card-prompt | 米色纸 | 薄荷纸 + 白卡 | 与赤陶路线图彻底区分 |
| card-rag | 近黑 | 靛紫渐变 + 玻璃卡 | 去掉黑底，保留深色玻璃质感 |
| infographic-training | 米色 | 冰蓝渐变 + 白卡 | 冷色数据图，与暖色路线图分开 |
| infographic-roadmap | 米色 | 赤陶底 + 奶油卡 | 形成一块明确的暖色块 |
| diagram-agent-stack | 近黑蓝 | 深青（petrol）+ 四色层 | 深色但不发黑，保留技术结构感 |
| diagram-rag-architecture | 米色 | 淡紫蓝图 + 白盒 | 浅色技术图，与冰蓝信息图区分 |

两张海报（深空、瑞士网格）和两页漫画（日系、水墨）保持不动，避免把已经成立的方向一起推翻。

## 二、改动清单

- [x] 重绘 6 件素材的底色/卡片/文字层级，保持 2048×1152（结构图 1600×1000 SVG）。
- [x] 更新 `showcase.ts` 的风格标签、描述、prompt 与 agent note（薄荷/靛紫/冰蓝/赤陶/深青/淡紫）。
- [x] 更新素材 README 的底色说明与总量（15,677,880 bytes）。
- [x] 定向 vitest、全量 vitest、目标 E2E、`pnpm check`、docs 门禁。
- [x] 本地 production build 预览截图确认案例墙不再“黑+米”重复。

## 三、验收

- `cd frontend && pnpm check` → 0 errors。
- `pnpm exec vitest run --project node tests/unit/core/agents/showcase.test.ts` → 7 passed。
- `pnpm exec vitest run --project dom tests/unit/components/workspace/agent-showcase.dom.test.tsx` → 23 passed。
- `pnpm test` → 266 files / 1754 tests passed。
- `pnpm exec playwright test tests/e2e/agent-new-chat-guide.spec.ts` → 41 passed。
- `python3 scripts/check_docs_convention.py` → 全过；素材总量 ≤20,000,000 bytes。
- 本地预览：creative-studio 案例墙 10 件加载，无 console error / 404。

## 四、说明

- 只重做底色，不重做两张海报和两页漫画；PPT 三套 deck 不在本批范围。
- 360 生图接口仍为 401，未新增生图；本轮全部用精确排版/SVG 完成。
- 一次性画布脚本不进入仓库；素材来源与总量记录在 README。
