# Agent Showcase 案例素材重制 changelog（2026-11-17）

> **TL;DR**：按用户反馈重制 creative-studio 的 8 个案例素材，并重做 PPT agent 三套 deck 的版式、字体层级与 21 张逐页预览；素材总量 15.62 MB，路径/数量/字节数测试仍通过。关键决策 = 用精确排版 + SVG 替代批量生图，配色与字号可控；影响面仅 `frontend/public/agent-showcase`、`showcase.ts`、DOM 测试路径与素材 README，不动 backend/接口/页面骨架。

## 一、背景

用户在预览中反馈两类问题：

1. 课程海报 agent 的案例“颜色单一、画面不精致，海报风格不够多、很多雷同”，并圈出 poster-llm-light、infographic-roadmap、diagram-agent-stack 与 diagram-rag。
2. PPT 制作 agent 的三个案例“细节粗糙，尤其第一个配色丑、画面颜色不一致、字体字号不好”，三套都需要精细优化。

## 二、改动

### Creative studio（10 个案例）

- 保留两页漫画（comic-agent / comic-gradient），其余 8 件全部重制：
  - poster-agent-dark：深空摄影底图 + 宋体标题 + 细线框/角落刻度，9:16。
  - poster-llm-light：瑞士网格、暖纸、钴蓝/朱红/芥黄几何块与能力柱状图。
  - card-prompt：暖纸卡片 + 黄色荧光笔 + 钴蓝/珊瑚/薄荷/琥珀四色索引。
  - card-rag：深色霓虹玻璃三卡 + 终端式证据链。
  - infographic-training：五阶段编辑数据图，五色分段 + 产出/输入标签。
  - infographic-roadmap：赤陶/森林绿/赭黄四阶段路线图，菱形 gate 与停条件带。
  - diagram-agent-stack：1600×1000 纯 SVG，四层玻璃卡片 + 右侧调用链。
  - diagram-rag-architecture：1600×1000 纯 SVG，离线索引/在线检索/评估三区。
- 更新 `showcase.ts` 的风格标签、双语描述、prompt 与 agent note；两页漫画描述不变。
- 统一英文标签用 Arial/Courier New、中文用系统黑体回退，避免生图文字不可控。

### PPT agent（3 套 × 7 页）

- quick-prompt-v3：暖纸 + 钴蓝/朱红，统一页眉/页码；把原黑色/饱和蓝整页收敛为单页深色模板卡；字号提升到 34–46pt 标题、10–13pt 正文。
- standard-llm-app-v3：深色电影感，琥珀/青强调；重排课程地图、三层架构、RAG 五步、Agent 循环、评估矩阵；加大页码与页脚层级。
- template-360-ai-v4：保留 360 品牌封面、页眉 logo、绿色视觉语言；重排 4 模块目录、能力边界、落地三步、风险四卡；新增卡片元信息与动作行，减少空卡。
- 三套 deck 的逐页预览由 PPTX 经 LibreOffice 导出，1920×1080 WebP；PPTX 仍是原生可编辑文本框/形状。
- 文件名版本递增：`quick-prompt-v3.pptx`、`standard-llm-app-v3.pptx`、`template-360-ai-v4.pptx`；`showcase.ts`、DOM 测试与 README 同步。

### 素材口径

- 总数仍为 58 个媒体文件；总量 15,615,743 bytes（15.62 MB），低于 20,000,000 bytes 预算。
- PPTX 分别为 236,985 / 437,726 / 467,022 bytes；`showcase.ts` 的 `pptxBytes` 与文件 stat 一致。

## 验收记录

- `cd frontend && pnpm check` → `121 problems (0 errors, 121 warnings)`，`All matched files use Prettier code style!`
- `pnpm exec vitest run --project node tests/unit/core/agents/showcase.test.ts` → `7 passed`
- `pnpm exec vitest run --project dom tests/unit/components/workspace/agent-showcase.dom.test.tsx` → `23 passed`
- `pnpm test` → `Test Files 266 passed (266)`，`Tests 1754 passed (1754)`
- `E2E_PORT=3180 pnpm exec playwright test tests/e2e/agent-new-chat-guide.spec.ts` → `41 passed (43.2s)`
- `pnpm check:dead` → 退出码 0（仅 knip 既有 CSS 配置提示）
- `node scripts/check_i18n_keys.mjs` → `630 个叶子全活`
- `python3 scripts/check_docs_convention.py` → `OK 仓库治理约定全过`
- 本地预览（production build + mock gateway，1440 宽）→ creative-studio 10 个案例、PPT 三套 deck 弹窗与 21 张缩略图均加载；`ERRORS []`（无 pageerror、console error、4xx/5xx）

## 四、注意事项

- 360 生图接口本轮返回 401（`code 1002 鉴权不通过`），未新增生图；poster-agent-dark 的球体底图来自既有素材裁切，漫画沿用原文件。后续若要新增生图案例，先更新该接口凭据。
- 未提交一次性 HTML/SVG/PPTX 生成脚本；素材来源、字体与压缩口径已写入 `frontend/public/agent-showcase/README.md`。
- 360 模板素材仍按用户 2026-11-17 的授权随项目分发。
