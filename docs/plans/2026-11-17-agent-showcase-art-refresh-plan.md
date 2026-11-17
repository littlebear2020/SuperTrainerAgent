# Agent Showcase 案例素材重制计划

> **状态**：已执行（2026-11-17；变更日志：`docs/changelog/changelog-2026-11-17-agent-showcase-art-refresh.md`）。
> **TL;DR**：重制 creative-studio 的 8 个案例素材（2 海报、2 知识卡、2 信息图、2 结构图，保留 2 页漫画），并重做 PPT agent 三套 deck 的版式、字体层级与逐页预览；只动 showcase 素材、数据与说明，不改 Agent runtime、接口或页面骨架。

## 一、背景与范围

用户 2026-11-17 对已上线的「课程海报」与「PPT 制作」案例给出反馈：

- 课程海报的示例“太丑，颜色单一、画面不精致，海报风格不够多、很多雷同”，并圈出两张 poster、roadmap 信息图与两张结构图。
- PPT 制作的三个案例“细节粗糙，尤其第一个配色丑、画面颜色不一致、字体字号不好”，三套都需要精细优化。

本批范围：

- 重制 creative-studio 的 8 个案例文件：poster-agent-dark、poster-llm-light、card-rag、card-prompt、infographic-training、infographic-roadmap、diagram-agent-stack、diagram-rag-architecture。两页漫画保留，避免无谓改动。
- 重做 PPT 三套 deck：quick（7 页）、standard（7 页）、template（7 页）；输出原生可编辑 PPTX 与 1920×1080 逐页预览。
- 更新 `frontend/src/core/agents/showcase.ts`、素材 README、DOM 测试中的 PPTX 路径。
- 不改 backend、`skills/`、Agent runtime、页面路由与布局组件。

## 二、视觉决策

- 用精确排版替代“同一套生图模型批量出图”：海报、知识卡、信息图采用 HTML/CSS 画布导出，结构图改为纯 SVG；两页漫画沿用既有生图产物。原因是本轮要解决的正是文字可读性、字号层级与配色一致性，精确排版更可控。
- 八件素材建立各自风格，而不是同一套深蓝/米色：深空摄影感海报、瑞士编辑风海报、暖纸笔记卡、霓虹玻璃卡、浅色编辑数据图、赤陶暖调路线图、暗色层叠结构图、浅色技术蓝图。
- PPT 三套各自统一：quick 用暖纸 + 钴蓝/朱红 + 单一深色模板卡；standard 用深色电影感 + 琥珀/青；template 保留 360 绿色品牌封面与页眉，重排为更克制的通识课版式。
- PPT 字体统一为 Arial / 苹方回退 / Courier New 标签；中文走系统黑体回退，避免预览端中文落到宋体。

## 三、改动清单

- [x] 重制 8 个 creative-studio 案例素材并替换 preview/original。
- [x] 保留 comic-agent / comic-gradient 两页漫画，仅复用原文件。
- [x] 重做三套 PPTX（quick-prompt-v3、standard-llm-app-v3、template-360-ai-v4）与 21 张逐页预览。
- [x] 更新 `showcase.ts` 的路径、字节数与视觉文案（风格标签、描述、prompt、agent note）。
- [x] 更新 `frontend/public/agent-showcase/README.md` 的来源、数量与大小口径。
- [x] 更新 DOM 测试中的 PPTX 路径；资产数量/预算/字节数测试保持通过。
- [x] 本地 `pnpm check`、定向 vitest、全量 vitest、预览截图验收。

## 四、验收

- `cd frontend && pnpm check` → 0 errors。
- `pnpm exec vitest run --project node tests/unit/core/agents/showcase.test.ts` → 7 passed。
- `pnpm exec vitest run --project dom tests/unit/components/workspace/agent-showcase.dom.test.tsx` → 23 passed。
- `pnpm test` 全量 → 266 files / 1754 tests passed。
- `pnpm check:dead`、`node scripts/check_i18n_keys.mjs`、`python3 scripts/check_docs_convention.py` → 全部通过。
- 素材总量 15,615,743 bytes（≤20,000,000）；PPTX 实际字节数与 `pptxBytes` 一致。
- 本地预览：creative-studio 10 张案例与 PPT 三套预览在 1440 宽下检查；无 404、无控制台错误。

## 五、遗留与说明

- 360 生图接口本轮返回 401（鉴权不通过），因此没有新增生图；海报底图复用既有素材裁切，漫画保持原样。后续如需新增生图案例，先修复该接口凭据。
- 素材生成用的 HTML/SVG/PPTX 构建脚本为一次性本地工具，未进入仓库；资产来源与字体口径记录在 README，路径/数量/大小由测试兜底。
