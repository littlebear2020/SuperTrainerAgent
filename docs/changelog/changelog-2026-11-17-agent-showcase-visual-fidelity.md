# Agent Showcase 视觉保真修复 changelog（2026-11-17）

> **TL;DR**：把 4 个 Agent 新会话页从“头像卡 + 三段宽度 + 空胶囊输入框”拉回 demo 的“透明人物 + 连续左对齐 + 案例墙”构图；关键决策 = 只改视觉层与可选 placeholder prop，不重写数据/媒体/路由，也不替换项目侧栏、顶栏和真实 `InputBox`；影响面 = 仅 `frontend/src/components/workspace/agent-showcase/**`、`agent-new-chat-guide.tsx`、`input-box.tsx`、agent chat page、`core/agents/showcase.ts`、hero 资产与对应测试，不动 backend/skills/其他前端批次。

> **状态**：已执行（P0 PR #15 + P1 PR `<P1_PR>`；计划 `docs/plans/2026-11-17-agent-showcase-visual-fidelity-plan.md`）。

## 一、执行范围

按 `docs/plans/2026-11-17-agent-showcase-visual-fidelity-plan.md` 分两个 PR 执行：

| 阶段 | 内容 | PR |
|---|---|---|
| P0 | Hero 透明去卡片、统一 1180 容器、Agent 专属 placeholder、去全宽背景补丁；测试同步 | #15 |
| P1 | 卡片层级/字号/单一卡片背景来源、左栏 280px、首屏节奏 | `<P1_PR>` |

从 `origin/main`（`41b724c2`）新建干净 worktree `SuperTrainerAgent-showcase-fidelity`；与 PPT-master 批次（backend/skills/docs）路径重叠为空，独立 worktree 并行，PR 串行合入。

## 二、改动

### P0

- **Hero 透明化**：四张 `frontend/public/agent-showcase/hero/*.webp` 离线去背（背景为竖向浅色渐变，逐行建模 + 边缘连通洪泛 + 羽化 + 去背景色污染），输出带 alpha 的 WebP（VP8X + ALPH）；四角与上/左/右 12px 边缘 alpha=0，暗色合成无白边/矩形。Hero 组件移除 `rounded-*`/`border-*`/`bg-indigo-50`/`shadow-*`，改左对齐；桌面 242px、移动 170px 人物；标题 33px/25px；Agent 标签 11–12px + 24–28px 短线；径向柔光移入 CSS Module（`.heroGlow`）。
- **统一容器**：`agent-new-chat-guide.tsx` 的 showcase 分支唯一 `max-w-[1180px]` shell；Hero/输入框/Showcase 内部仅 `w-full`，删除 `max-w-4xl`/`max-w-5xl`/`max-w-7xl` 混用；`AgentShowcase` 增加 `data-testid="agent-showcase"`。
- **专属 placeholder**：`ShowcaseAgent.inputPlaceholder`（中英四套）；`InputBox` 新增可选 `placeholder?: string`，默认仍是 `t.inputBox.placeholder`；仅 showcase 新会话由 page 按 locale 传 **string**，已有会话/co-writer 不传。
- **去全宽背景**：删除 `.showcaseGuide` 的 `background` / `box-shadow: 0 0 0 100vmax` / `clip-path`，让 `WorkspaceContent` 的 `FloatingBlobsBackground` + `--blob-root-bg` 透出；与 `/workspace` 首页同背景，无米色块/接缝。
- **测试**：E2E 覆盖三视口边缘差 ≤8px、首卡 top/h3 bottom 基线、四 Agent 专属 placeholder、390 长 placeholder 换行、hero 四角/边缘 alpha；单测覆盖 placeholder 默认/覆盖、page 级传递条件、showcase 数据完整性。

### P1

- **卡片层级**：视频标题 18px / 正文 13px；PPT 标题 19px / 预览列 296px / 2×2 缩略图；视觉标题 15px；播客标题 16px；标签 11px；元信息 10.5px；媒体/卡片圆角 10–14px；边框更轻，hover 才强化边框/阴影。
- **单一卡片背景**：删除 CSS Module 的 `.showcase-card { background: var(--card) }` 与暗色覆盖（含 `backdrop-filter: none`），卡片改用 `bg-card/70 dark:bg-white/5`，避免 module 优先级压住 utility。
- **左栏与节奏**：≥1280 左栏 280px、1024 240px；标题 27px、正文 13px、步骤间距 20px；压缩 Showcase 顶部/标题后间距，首屏三视口全部落在基线 +30px 内且首卡 h3 可见。

## 三、视觉证据（不入库）

参考 demo 与 before/after 截图保留在 worktree 外：

- demo：`/Users/littlebear/Documents/SuperTrainerAgent-home-plan/temp/agent-listenhub-demo-v2/`（含 `.work/shots/hero-viewport-*.png`）
- before：`.screenshots/agent-showcase/refinement/`（origin/main 现状）与本批 `visual-fidelity/before/`
- after：`.screenshots/agent-showcase/visual-fidelity/p0-after/`、`p1-after/`
- 参数化 capture：`.screenshots/agent-showcase/visual-fidelity/capture-visual-fidelity.mjs`（`FRONTEND_DIR`/`BASE_URL`/`OUT_DIR`），不依赖已删除的验收 worktree
- 覆盖：4 Agent × 1440/1024/390 × 亮/暗；另附 `/workspace` 首页同背景对照

## 四、首屏三视口实测（course-video，production build）

| 视口 | 首卡 top（阈值） | 首卡 h3 bottom（阈值） | 输入框宽 |
|---|---:|---:|---:|
| 1440×900 | 532（≤581） | 754.6（≤900） | 1132（≥900） |
| 1024×768 | 492（≤545） | 748.1（≤768） | 927 |
| 390×844 | 556.5（≤618） | 833.7（≤844） | 357 |

三视口 Hero/输入框/`agent-showcase` 根节点左右边缘 delta=0；无横向滚动。

## 五、并行与合入

- 与 PPT-master 批次并行：showcase 分支只碰 frontend；PPT 分支只碰 backend/skills/docs；路径重叠为空，独立 worktree。
- PR 串行合入：P0（#15）合入后 P1 rebase 新 main 再合，禁止双 PR 同时抢 main。
- 透明资产主方案一次成功，未触发“统一无边框柔光”兜底。

## 六、验收记录

- `cd frontend && pnpm check` → `✖ 121 problems (0 errors, 121 warnings)`；`Checking formatting... All matched files use Prettier code style!`
- `cd frontend && pnpm test` → `Test Files 266 passed (266)` / `Tests 1753 passed (1753)`
- `cd frontend && pnpm test:e2e -- tests/e2e/agent-new-chat-guide.spec.ts` → `37 passed`
- `python3 scripts/check_docs_convention.py` → `OK 仓库治理约定全过（...）`
- `FRONTEND_DIR=... BASE_URL=http://localhost:3177 OUT_DIR=... node --experimental-strip-types capture-visual-fidelity.mjs` → `errors: []`，生成 24 张 4 Agent × 1440/1024/390 × 亮暗截图
- Hero alpha（E2E canvas 断言）→ 四角 `[0,0,0,0]`，上/左/右 12px 边缘最大 alpha `0`，不透明近白像素占比 `< 0.15`
- 首屏实测 → 见第四节表格

## 七、遗留

- P1 可选 `showAttachmentLabel`（上传按钮文字）本批未做；计划标注为可选，未影响 placeholder 契约。
- 视觉构图结论需人工设计评审；自动化只锁可计算的门（对齐/宽度/字号/首屏/alpha），不以“测试全绿”替代视觉验收。
