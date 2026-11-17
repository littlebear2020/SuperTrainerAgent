# Agent Showcase 第二轮预览反馈精修 changelog（2026-11-17）

> **TL;DR**：修复第二轮预览反馈的四点：人物列改为固定 180/160/140px（四个角色画布宽度不同导致文字起点抖动 33px）、删除 hero 的 Agent 标签、四个 Agent 统一为同一套 indigo 强调色（标签/hover/播放/下载）、creative-studio 更名「课程海报」；同时修掉分类筛选的计数不一致（头部 10 / 右侧 2）并支持再次点击已选分类回到「全部」。影响面仅 showcase 前端组件、i18n 名称与测试。

> **状态**：已执行（follow-up PR #18；前序 #15/#16/#17）。

## 一、问题与根因

| 反馈 | 根因 |
|---|---|
| 四个 Agent 人物“歪了” | Hero 图片用 `w-auto`，透明画布宽度不同（128/130/137/161px），标题起点随图片宽度漂移 445–478px（33px 抖动），切换 Agent 时构图不一致 |
| Hero 上方文字标签要删 | `ShowcaseHero` 的 eyebrow（短线 + Agent 名称）重复了顶栏 badge |
| 四个 Agent 配色不统一 | CSS Module 按 `data-agent` 给 ppt/podcast/creative 分别设了 amber/violet/rose accent，卡片 hover/播放/下载也各自取色 |
| 分类按钮逻辑奇怪 | ① 头部案例数固定 10、右侧筛选数变 2；② 筛选到 2 张时 CSS 多栏把两张都堆进第 1 列（右侧两列全空，像只剩 1 张）；③ 再次点击已选分类不会回到全部 |
| 「视觉设计」要改名 | `newChatGuides["creative-studio"].displayName` 与 `showcase.label` 仍是「视觉设计」 |

## 二、改动

1. **固定人物列**：图片列固定 `w-[140px] sm:w-[160px] xl:w-[180px]`，图片 `w-full h-[160/178/188px] object-contain object-center`；四个 Agent 的图片盒、标题起点完全一致（1440 下均 180px 列、标题 x 相同）。
2. **删除 hero 标签**：去掉短线 + Agent 名称 eyebrow，Hero 只保留两行主标题；同时删除已无消费方的 `ShowcaseAgent.label` 字段与四条 Agent 定义（避免死数据）。
3. **统一强调色**：删除 CSS Module 的按 Agent accent（含暗色覆盖），全部使用 indigo（浅色 `#3f4fd1`/`#eef1ff`，暗色 `#a5b4fc`/12% indigo）；卡片 hover、focus ring、PPT 下载、播客播放/波形、筛选 focus 全部统一为 indigo。
4. **更名**：creative-studio 的 `displayName` 中文 `视觉设计 → 课程海报`、英文 `Visual design → Course poster`；`showcase.label` 同步为 `课程海报 / COURSE POSTER`。
5. **筛选逻辑**：
   - 头部计数改为跟随可见列表（筛选后 `2 个可核验案例`）；
   - 右侧计数在筛选时显示 `2 / 10 件`，未筛选显示 `10 件`；
   - 筛选态改用 grid（`sm:grid-cols-2 xl:grid-cols-3`，`items-start`），避免多栏把 2 张卡都堆进第 1 列；「全部」仍保持瀑布流自然比例；
   - 再次点击当前分类回到「全部」（「全部」本身保持）；
   - 筛选按钮补 `cursor-pointer`，focus ring 统一 indigo。
6. **测试**：
   - 单测：hero 不再包含 Agent 名称；筛选后头部/右侧计数一致；再次点击分类恢复 10 条；
   - E2E 新增 3 条：四 Agent 人物几何一致 + 标签移除、统一 tag 配色 + 课程海报命名、筛选计数同步 + 筛选卡分列 + 切回全部；
   - 保留原有的 1440/1024/390 首屏、placeholder、alpha、字号等门禁。

## 三、实测（1440×900，mock 预览）

- 四 Agent 人物盒：x=282、宽=180、高=188；标题 x 全部一致（修复前 445–478 漂移）。
- tag 计算色：`color: rgb(63, 79, 209)`、`background: rgb(238, 241, 255)`，四 Agent 相同。
- 筛选：初始 10 件 / 头部 10；点「课程海报」→ 2 件 / 头部 2 / 右侧 `2 / 10 件`，两张卡分列 x=602/878（修复前都在 x=602）；再点「课程海报」→ 10 件 / 全部。
- 顶栏 badge：creative-studio 显示「课程海报」。

## 验收记录

- `cd frontend && pnpm check` → 0 error；prettier 通过
- `cd frontend && pnpm test` → 266 files / 1753 tests passed
- `cd frontend && pnpm test:e2e -- tests/e2e/agent-new-chat-guide.spec.ts` → `41 passed`
- `python3 scripts/check_docs_convention.py` → OK

## 四、AI review 修复

- 删除死字段 `ShowcaseAgent.label`（含四条 Agent 定义），并把 E2E 的“无标签”断言改为 hero 内无 `<p>`；
- 筛选态 grid 复用 `showcaseCardClass("video")`，并仅当筛选结果全部为 visual 时启用（防止未来 deck/video 混排被套三列窄栅格）；
- `headerCaseCount` 简化为 `visibleCases.length`（无 visual case 时两者恒等）。

## 五、说明

- 本批不涉及后端/数据/媒体；`badge`、`note` 等字段保留（当前无渲染引用），避免数据迁移；`label` 因已无消费方按 review 建议删除。
- 若后续希望保留各 Agent 的差异化强调色，需同时改回 CSS accent 变量与 E2E 配色断言。
