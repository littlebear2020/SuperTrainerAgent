# Agent Showcase 真实产物类型重制计划

> **状态**：已执行（2026-11-17；变更日志：`docs/changelog/changelog-2026-11-17-agent-showcase-authentic-art.md`）。
> **TL;DR**：按“三张纸雕同风格、画面与提示词类型不一致、不要奶油底”的反馈，停用纸雕/米黄路线，把 7 件素材按真实产物类型重做：深蓝瑞士海报、深色 Prompt 手稿、冷白数据管线、深绿时间线、石墨结构图、冷白蓝图、靛蓝水墨漫画；整套改用深蓝/石墨/靛蓝/青绿/珊瑚。

## 一、背景

上一版 4 张生图虽然结构不同，但 prompt 卡/训练图/路线图都用了暖纸 + 3D 纸雕，观感仍是同一画风；且画面更像装饰插画，与 prompt 里要求的“知识卡 / 信息图 / 路线图”不完全一致。用户同时要求停用淡黄/奶油底色，并圈出海报 B、两张结构图与两页漫画。

## 二、方案

按“真实产物类型”重排，不再用同一套卡片骨架或纸雕：

| 素材 | 类型 | 做法 |
|---|---|---|
| poster-llm-light | 课程海报 | 深蓝底瑞士网格，荧光绿/朱红数据块，白字 |
| card-prompt | 知识卡 | 深色 Prompt 手稿 + 四段批注，完整占位符文本 |
| infographic-training | 信息图 | 冷白数据管线：五节点横线 + INPUT/OUTPUT，无纸雕 |
| infographic-roadmap | 信息图 | 深绿时间线：S 形路径 + 四个阶段 + 停条件 |
| diagram-agent-stack | 结构图 | 石墨黑底四层结构，蓝/青/琥珀/黄绿 |
| diagram-rag-architecture | 结构图 | 冷白蓝图，靛蓝/珊瑚/青绿三区 |
| comic-gradient | 漫画 | 原水墨四格重着色为靛蓝水墨，去掉米黄纸底 |

两张海报中的深空海报、RAG 3D 卡、日系漫画保持不动。

## 三、改动清单

- [x] 重做 6 件 HTML/SVG 版式，替换对应 original/preview。
- [x] 靛蓝水墨重着色 comic-gradient，保留原四格构图与文案。
- [x] 更新 `showcase.ts` 的 tag/style/description/prompt/agent note。
- [x] 更新素材 README 的来源与总量（17,140,182 bytes）。
- [x] 定向 vitest、全量 vitest、目标 E2E、`pnpm check`、docs 门禁。
- [x] 本地 production build 预览确认无奶油底、四种信息图版式可读。

## 四、验收

- `pnpm check` → 0 errors。
- 定向 vitest → 7 passed；`pnpm test` → 266 files / 1754 tests passed。
- `E2E_PORT=3187 pnpm exec playwright test tests/e2e/agent-new-chat-guide.spec.ts` → `41 passed (47.1s)`。
- `python3 scripts/check_docs_convention.py` → 全过。
- 素材总量 17,140,182 bytes（<20MB）；首屏媒体预算 ≤1.5MB。
- 浏览器预览无 console error / 404。

## 五、说明

- 本轮信息图/知识卡/海报/结构图全部由精确排版/SVG 产出，符合项目“精确排版 + SVG”的真实能力；不以生图插画冒充信息图。
- 奶油/米黄底色在本轮全部移除；漫画改为靛蓝水墨。
- 一次性画布脚本不进入仓库。
