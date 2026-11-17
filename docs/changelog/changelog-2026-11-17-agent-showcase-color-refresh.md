# Agent Showcase 案例配色重制 changelog（2026-11-17）

> **TL;DR**：按“黑底很差、米色背景重复、看不出区别”的反馈，重做 6 件 creative-studio 素材的底色：薄荷笔记卡、靛紫玻璃卡、冰蓝信息图、赤陶路线图、深青结构图、淡紫蓝图；海报与漫画不动，PPT 三套不动；总量 15.68 MB，定向测试与 E2E 通过。

## 一、背景

上一版把 8 件素材统一成“米色纸 + 近黑”两条底色带，案例墙出现两个问题：

- RAG 知识卡的近黑底在浅色页面里过重，用户直说“黑色背景效果很差”。
- prompt 卡、训练信息图、路线图都是米色底，并排时“背景颜色重复、看不出区别”。

本轮只调整底色与卡片层级，保留已经成立的海报/漫画和版面结构。

## 二、改动

| 素材 | 改法 |
|---|---|
| card-prompt | 米色 → 薄荷纸 `#dcefd9`；四张卡改纯白、深绿描边，四色索引不变 |
| card-rag | 近黑 → 靛紫渐变（`#171049 → #4a2470`）；玻璃卡提亮，终端改深靛紫并恢复 TRACE 条 |
| infographic-training | 米色 → 冰蓝渐变（`#dcecfb → #cfe2f7`）；白卡 + 五色阶段，深蓝底栏 |
| infographic-roadmap | 米色 → 赤陶底 `#b85234`；卡片改奶油色，保留赤陶/森林绿/赭黄阶段色 |
| diagram-agent-stack | 近黑蓝 → 深青 `#04262b`；四层填充提亮（紫/青/琥珀/黄绿） |
| diagram-rag-architecture | 米色 → 淡紫蓝图 `#e7e2f8`；白盒、靛蓝连线、紫灰评估带 |

同时更新 `showcase.ts` 的 tag/style/description/prompt/agent note 与素材 README。

## 验收记录

- `cd frontend && pnpm check` → `121 problems (0 errors, 121 warnings)`，Prettier 全过
- `pnpm exec vitest run --project node tests/unit/core/agents/showcase.test.ts` → `7 passed`
- `pnpm exec vitest run --project dom tests/unit/components/workspace/agent-showcase.dom.test.tsx` → `23 passed`
- `pnpm test` → 266 files / 1754 tests passed
- `pnpm exec playwright test tests/e2e/agent-new-chat-guide.spec.ts` → `41 passed (53.5s)`
- `python3 scripts/check_docs_convention.py` → `OK 仓库治理约定全过`
- 素材总量 `15,677,880 bytes`（15.68 MB，<20 MB 预算）；58 个文件不变
- 本地 production build 预览（1440 宽）→ 10 件案例加载，无 console error / 404

## 三、说明

- 两张海报、两页漫画、三套 PPT 均未改动。
- 360 生图接口仍返回 401，本轮没有新增生图；全部为精确排版/SVG 重着色。
- 一次性画布脚本不进入仓库；素材来源、底色与压缩口径见 `frontend/public/agent-showcase/README.md`。
