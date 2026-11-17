# Agent Showcase 真实产物类型重制 changelog（2026-11-17）

> **TL;DR**：停用纸雕/米黄路线，按真实产物类型重做 7 件：深蓝瑞士海报、深色 Prompt 手稿、冷白数据管线、深绿时间线、石墨结构图、冷白蓝图、靛蓝水墨漫画；整套改为冷色系，素材总量 17,140,182 bytes，测试与 E2E 通过。

## 一、背景

用户指出：

- prompt 卡、训练图、路线图三张都是暖纸 + 3D 纸雕，画风完全一样；
- 画面更像装饰插画，与提示词要求的“知识卡 / 信息图 / 路线图”类型不一致，脱离真实能力；
- 不要再用淡黄/奶油配色；海报 B、两张结构图、两页漫画也被圈出。

## 二、改动

- `poster-llm-light`：深蓝底瑞士网格，荧光绿/朱红数据块，白字；不再用暖白纸。
- `card-prompt`：深色终端底 + 完整 Prompt 手稿 + 四段批注，真实“提示词卡”形态。
- `infographic-training`：冷白数据管线，五节点横线、INPUT/OUTPUT 与连续箭头，取消纸雕。
- `infographic-roadmap`：深绿时间线，S 形路径串起四阶段与停条件，取消四列卡片。
- `diagram-agent-stack`：石墨黑底四层结构，蓝/青/琥珀/黄绿区分。
- `diagram-rag-architecture`：冷白蓝图，靛蓝/珊瑚/青绿三区。
- `comic-gradient`：原水墨四格重着色为靛蓝水墨，去掉米黄纸底。
- 更新 `showcase.ts` tag/style/description/prompt/agent note 与素材 README 总量。

## 验收记录

- `cd frontend && pnpm check` → 0 errors，Prettier 全过
- `pnpm exec vitest run --project node tests/unit/core/agents/showcase.test.ts` → 7 passed
- `pnpm test` → 266 files / 1754 tests passed
- `E2E_PORT=3187 pnpm exec playwright test tests/e2e/agent-new-chat-guide.spec.ts` → `41 passed (47.1s)`
- `python3 scripts/check_docs_convention.py` → `OK 仓库治理约定全过`
- 素材总数 58，总量 17,140,182 bytes（<20 MB）；首屏媒体预算 ≤1.5MB
- 本地 production build 预览 → 无 console error / 404，无奶油底色

## 三、说明

- 信息图/知识卡/海报/结构图全部为精确排版/SVG，符合项目真实能力；没有再拿生图插画冒充信息图。
- 两张海报中的深空海报、RAG 3D 卡、日系漫画未改。
- 一次性画布脚本不进入仓库。
