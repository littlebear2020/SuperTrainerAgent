# 我的作品 / 我的资产信息架构与卡片统一实施计划

> **状态**：未执行；任务 1～3 已完成，任务 4 待执行。定稿基线 `4fa78245`；工作区已有 `docs/findings-backlog.md` 修改和两个未跟踪 MP4，均不属于本批，不得覆盖或提交。
> **TL;DR**：只收敛页面信息架构、卡片网格与媒体区；现有配色、字体、胶囊 Tab、圆角、阴影、按钮和状态徽标全部沿用；真实作品封面及创建、分镜、详情、播放、下载、重试能力不删不改；不改后端、不改路由结构。

> **For agentic workers：** 用户复核后使用 `executing-plans` 逐项实施；每项按“失败测试 → 最小实现 → 定向回归”执行并勾选，不得把 demo 当作视觉规范。

**Goal**：消除「我的作品 / 我的资产」重复导航与标题，让六类作品在现有界面风格中共享一致的网格列宽、16:9 媒体区与同行等高布局，同时完整保留真实数据和现有能力。

**Architecture**：继续使用 `/workspace/video-gen` 的 `tab` / `capability` 路由和侧栏双入口；只改五个页面/卡片组件及必要 i18n。对话卡新增纯 CSS/Lucide Motif，成品卡继续消费 `VideoTaskBrief.thumbnail_url`，资产卡继续消费课程项目封面链。资产选择在 `video-gen-page.tsx` 内部条件挂载的 `AssetsView` 中管理，离开资产视图即卸载，保持当前返回列表的语义。不新增共享卡片抽象、不新增依赖。

**Tech Stack**：Next.js App Router、React、TypeScript、Tailwind CSS 4、现有 shadcn/ui Button、Lucide、TanStack Query、Vitest、Playwright。

**Spec / 依据**：用户多轮确认的结构方案；`temp/works-assets-demo-2026-11-13/page-mockup.html` 仅作结构示意。能力事实源为现有组件与测试；视觉事实源为当前生产组件，而非 demo。

## 一、评审决策记录

| 决策点 | 定稿结论 |
|---|---|
| 视图切换 | 侧栏唯一入口；删除页内一级「我的作品 / 我的资产」Tab。 |
| 标题 | 只保留一个 h1，按 `route.tab` 显示；不写解释性副标题。 |
| 路由 | 保留 `tab`、`capability`、canonicalization、无关 query 和 `DEFAULT_WORK_CAPABILITY`；不新增页面，不增加「全部」Tab。 |
| 视觉风格 | 当前代码为准：Indigo、胶囊能力 Tab、`rounded-2xl`、现有阴影、Button variant、StatusBadge 均不改；不采用 demo 蓝色主题、下划线 Tab、播放覆盖层或「⋯」菜单。 |
| 卡片统一 | 统一网格列宽、16:9 媒体区、正文布局与同一行卡片的底部对齐；允许真实错误、操作数量及展开播放器造成合理的行高差异；不统一或伪造业务字段。 |
| 对话封面 | 四种纯 CSS/Lucide Motif，只作能力标识；不生成图片，不冒充产物。 |
| 成品封面 | 真实 `thumbnail_url` 优先；不用 Motif 替代。 |
| 作品操作 | 本批保持现有按钮与确认弹层，不做收纳、不删动作。 |
| 搜索/排序 | 保留对话现有标题搜索；不扩展到成品/资产，不新增排序控件。 |
| 轮数 | 仅真实 `messages` 存在且有用户消息时显示，否则不渲染。 |
| grilling | 按「明确小改」豁免新增拷问；本表记录用户已完成的结构、对话卡、统一网格与条件轮数决策。 |

## 二、Global Constraints

- 不修改 backend、API、provider、worker、config、docker、skills。
- 不修改创建对话框、分镜编辑器、任务详情页及其音频/字幕能力。
- 不修改 `frontend/src/components/ui/`、`frontend/src/components/ai-elements/`。
- 不修改 `frontend/src/core/workspace/nav-routes.ts`、`video-assets-route.ts`。
- 不新增依赖、图片资产、App Router 页面或共享组件文件。
- 不新增时长、帧数、分辨率、页数、花费等列表字段；不编造轮数、封面或更新时间。
- 动态 h1 复用 `t.sidebar.myWorks` / `t.sidebar.myAssets`；「打开项目」使用新增 `t.workspace.works.openProject`，轮数使用 `t.workspace.works.turns(count)`，同步修改 `types.ts`、`zh-CN.ts`、`en-US.ts`；不做整文件 locale 回写，不顺手改造资产页既有硬编码文案。
- 不接管或提交基线中已有的 findings-backlog 修改和两个 MP4。

## 三、文件地图

**页面与卡片：**
- `frontend/src/components/workspace/video-gen/video-gen-page.tsx`：动态 h1、资产页头 CTA、项目选择状态；删除一级 Tab。
- `frontend/src/components/workspace/video-gen/course-project-page.tsx`：受控项目列表；删除重复 h2/CTA；沿用现有项目卡和封面。
- `frontend/src/components/workspace/video-gen/completed-video-grid.tsx`：统一成品卡外壳，保留全部状态与动作。
- `frontend/src/components/workspace/video-gen/agent-conversation-grid.tsx`：统一对话卡外壳，增加四种 Motif 与条件轮数。
- `frontend/src/app/workspace/video-gen/page.tsx`：fallback 改为单页头 + 内容骨架。

**i18n：**
- `frontend/src/core/i18n/locales/types.ts`
- `frontend/src/core/i18n/locales/zh-CN.ts`
- `frontend/src/core/i18n/locales/en-US.ts`

**测试：**
- `frontend/tests/unit/components/workspace/video-gen/video-gen-page-url.dom.test.tsx`
- `frontend/tests/unit/components/workspace/video-gen/course-project-page.dom.test.tsx`
- `frontend/tests/unit/components/workspace/video-gen/completed-video-grid.dom.test.tsx`
- `frontend/tests/unit/components/workspace/video-gen/agent-conversation-grid.dom.test.tsx`
- `frontend/tests/e2e/video-assets.spec.ts`
- `frontend/tests/e2e/work-capabilities.spec.ts`：同步旧面板定位与「继续对话」按钮文案，覆盖六能力导航回归。

**执行收尾：** 本计划、`docs/changelog/changelog-2026-11-13-works-assets-ia-cleanup.md`。

## 四、旧功能保全表

| 数据/状态 | 必须保持的行为 |
|---|---|
| ready 且产物存在 | 真实缩略图；查看仅展开当前真实视频；下载使用原 endpoint；详情与删除确认可用。 |
| draft 且 progress >= 100 | 继续编辑分镜进入原 storyboard 路由；详情、删除可用。 |
| draft 且 progress < 100 | 显示「生成分镜」；不出现有效播放/下载。 |
| failed | 显示真实错误；重试合成仍经确认；详情、删除可用。 |
| running / composing / qc_repair / pending / cancelled | 状态文案不丢；不提供无效查看/下载；详情与删除不退化。 |
| artifact_missing | 显示「产物已清理」；禁止查看和下载。 |
| 对话历史 | 去重、稳定排序、标题搜索、空态/错误态、每次 +6、canonical 打开路由不变。 |
| 课程项目 | 锚图 → 风格预览 → 主色 fallback；新建、打开、返回、编辑详情与既有守卫不变。 |
| 视频创建与后期 | 创建参数、分镜编辑、逐帧重生、音频/字幕、重新合成等页面和逻辑零改动。 |

## 五、Review Focus

1. **真实成品封面被 Motif 替换**：任务 2 断言 `thumbnail_url` 仍进入 `TaskThumbnail`。
2. **草稿/失败/缺产物动作因卡片重排消失**：任务 2 保留并运行现有状态与确认弹层测试。
3. **messages 缺省或为空导致假轮数**：任务 3 覆盖 undefined、空数组、混合消息类型。
4. **资产 CTA 上提后创建/打开/返回断链，或切换视图后残留旧详情**：任务 1 覆盖受控 `selectedId` 全链路，任务 4 用真实 E2E 验证 AssetsView 卸载重置。
5. **删除一级导航后遗留失效 ARIA 或旧测试定位**：任务 1 同步移除外层 tabpanel 关联并更新两个 E2E 文件；任务 4 运行双 spec 回归。

---

### 任务 1：收敛页面信息架构并受控化资产项目

**Files：**
- Modify：`video-gen-page.tsx`、`course-project-page.tsx`、`app/workspace/video-gen/page.tsx`、三个 locale 文件
- Test：`video-gen-page-url.dom.test.tsx`、`course-project-page.dom.test.tsx`、`video-assets.spec.ts`、`work-capabilities.spec.ts`

**Interface：**
```ts
interface CourseProjectPageProps {
  selectedId: string | null;
  onSelect: (projectId: string) => void;
  onBack: () => void;
}
```

- [x] **先写失败测试**：把目标结构断言写为不存在一级 `#video-assets-tab-*`，h1 分别为「我的作品 / 我的资产」，外层不再有指向已删除一级 Tab 的 `role="tabpanel"` / `aria-labelledby`，六能力 Tab、URL 与默认 capability 不变；资产页头「新建项目」创建后调用 `onSelect(project.id)`，项目卡调用同一回调，`onBack` 清空选择；离开资产视图再返回时回到项目列表。
- [x] **实现页面结构**：在同文件增加内部 `AssetsView`，持有 `selectedId` 并复用 `CourseProjectDialog`；只在 `route.tab === "assets"` 时挂载，离开即卸载，不用新增 set-state effect。动态 h1 复用 `t.sidebar.myWorks` / `t.sidebar.myAssets`。删除 `TABS`、`selectTab`、一级 Tab 键盘分支及外层 `id="video-assets-panel-*"`、`role="tabpanel"`、`aria-labelledby`，保留必要布局容器；保留能力 Tab 的 panel ID、roving ARIA 与 query 保留逻辑。项目详情态隐藏列表 CTA。
- [x] **实现受控资产列表**：删除重复 h2/CTA；保留真实封面、标题、风格、更新时间与详情早返回；卡片动作通过 `t.workspace.works.openProject` 显示「打开项目 / Open project」，但进入同一详情。项目卡与作品卡同样使用 16:9 媒体区、纵向 flex 和底部动作对齐，配色、边框、圆角与阴影类名不变。
- [x] **同步 fallback 与测试**：只保留一个页头骨架和内容骨架；将被删除一级 Tab 的测试替换为上述目标行为断言，能力 Tab 键盘测试不得删除。同步更新 `video-assets.spec.ts` 与 `work-capabilities.spec.ts` 中一级 Tab 点击、外层 tabpanel 和 `#video-assets-panel-works` 定位，改用侧栏链接、动态 h1、六能力 panel 或现有卡片 testid。将 `course-project-page.dom.test.tsx` 的「编辑项目」文本断言与 `button("编辑项目")`、以及 `video-assets.spec.ts` 的「编辑项目」文本断言分别改为「打开项目」；不要全局替换测试数据标题「待编辑项目」。
- [x] **定向回归**：运行 `video-gen-page-url.dom.test.tsx` 与 `course-project-page.dom.test.tsx`，确认路由、创建、打开、返回、视图切换重置、加载/错误/空态全部通过；两个 E2E 的完整回归在任务 4 执行。

### 任务 2：统一成品卡外壳，不改变真实媒体与操作

**Files：**
- Modify：`completed-video-grid.tsx`
- Test：`completed-video-grid.dom.test.tsx`（保留 `completed-video-grid.test.ts` 纯逻辑断言）

**Interface：** `CompletedVideoGrid`、`CompletedWorkCard`、`selectCompletedWorks`、`canPreviewOrDownload` 的签名均不变。

- [x] **先补结构与状态断言**：卡片含 `aspect-video` 媒体区；真实 `thumbnail_url` 仍渲染为原 URL；卡片为纵向 flex，正文占剩余空间，动作区底部对齐。参数化覆盖 `running`、`composing`、`qc_repair`、`pending`、`cancelled`，断言状态文案不丢、查看不可用、下载不存在、详情与删除仍在；这些属于旧功能保全回归，不要求在现代码上刻意失败。
- [x] **最小实现**：仅调整容器和布局类——`h-full flex flex-col`、媒体 `shrink-0`、正文 `flex flex-1 flex-col`、动作区 `mt-auto`；沿用 `rounded-2xl border shadow-sm`、现有标题/状态行、按钮 variant 和间距。
- [x] **明确不做**：不新增播放覆盖层，不移动 StatusBadge，不收纳按钮，不添加业务字段，不自动播放；不通过固定卡片总高度或截断错误信息制造视觉等高。
- [x] **定向回归**：运行 completed-video-grid DOM 与纯逻辑测试，逐项核对「旧功能保全表」全部状态，以及加载更多、真实播放/下载和确认弹层。

### 任务 3：统一对话卡并加入四种 Motif 与条件轮数

**Files：**
- Modify：`agent-conversation-grid.tsx`、三个 locale 文件
- Test：`agent-conversation-grid.dom.test.tsx`、`work-capabilities.spec.ts`

**Interface：**
```ts
// 仅用于卡片展示与测试；无数据或没有用户消息时返回 null。
export function userTurnCount(thread: AgentThread): number | null;
```

- [x] **先写失败测试**：四种 agent 分别产生 `data-motif="course-video|creative-studio|ppt-master|podcast-studio"`；两条 human + 一条 ai 显示「2 轮」，运行时 `values` 或 `messages` 为 undefined、`messages` 为空数组或无 human 时不显示轮数；打开路由仍为原 canonical URL。
- [x] **实现 Motif**：按 `agentName` 选择文字行/海报/幻灯片/声波；只用现有 Tailwind 中性色与 Indigo 色板、Lucide 图标和 CSS，`aria-hidden="true"`，不新增图片或依赖。
- [x] **统一卡片结构**：复用任务 2 的布局原则，媒体区 `aspect-video`，正文与「继续对话」底部对齐；保留 titleOfThread、能力 label、真实更新时间和现有搜索/分页。
- [x] **实现轮数与文案**：`userTurnCount` 只统计 SDK 用户消息 `type === "human"`，正数才返回；读取时容忍运行时 `values` / `messages` 缺省。locale 增加 `turns(count)`，现有 `works.open` 改为「继续对话 / Continue」，并同步 `agent-conversation-grid.dom.test.tsx` 的 `button("打开")` 与 `work-capabilities.spec.ts` 两处「打开」按钮定位为「继续对话」。
- [x] **定向回归**：运行 AgentConversationGrid DOM 测试，确认去重、排序、搜索、空态、加载更多、路由和条件轮数全部通过；更新后的 `work-capabilities.spec.ts` 在任务 4 与其他 E2E 一并运行。

### 任务 4：行为回归、视觉复核与交付

**Files：**
- Modify：`frontend/tests/e2e/video-assets.spec.ts`、`frontend/tests/e2e/work-capabilities.spec.ts`
- Create：`docs/changelog/changelog-2026-11-13-works-assets-ia-cleanup.md`

- [ ] **更新行为级 E2E**：通过侧栏链接切换作品/资产；断言页内无一级 Tab、h1 与 URL 正确、六能力切换及详情路由的侧栏 active 不回归。保留真实播放、下载、加载更多、缺产物阻断断言，并运行 `work-capabilities.spec.ts` 覆盖六能力、历史路由和更新后的「继续对话」入口。
- [ ] **验证资产详情卸载重置**：在真实 E2E 中打开项目详情，经侧栏依次切换「我的作品 → 我的资产」，断言项目列表与「新建项目」可见，旧项目详情和「返回项目列表」不存在；不以 mock 组件的卸载记录替代该行为断言。
- [ ] **视觉复核**：桌面 1440px 与移动 390px 逐类抽查六能力及资产页；三列/单列无横向溢出，媒体区实际保持 16:9，同一行卡片等高且动作区底部对齐。长标题、真实错误、不同操作数量与展开播放器允许产生合理行高差异，不要求所有卡片总高度固定一致；确认胶囊 Tab、Indigo、圆角、阴影、按钮和状态徽标未变成 demo 样式。
- [ ] **运行定向测试**：

```bash
cd frontend && pnpm exec vitest run --project dom tests/unit/components/workspace/video-gen/video-gen-page-url.dom.test.tsx tests/unit/components/workspace/video-gen/course-project-page.dom.test.tsx tests/unit/components/workspace/video-gen/completed-video-grid.dom.test.tsx tests/unit/components/workspace/video-gen/agent-conversation-grid.dom.test.tsx
cd frontend && pnpm exec vitest run --project node tests/unit/components/workspace/video-gen/completed-video-grid.test.ts tests/unit/components/workspace/video-gen/course-project-cover.test.ts tests/unit/core/workspace/video-assets-route.test.ts
cd frontend && pnpm exec playwright test tests/e2e/video-assets.spec.ts tests/e2e/work-capabilities.spec.ts
```

- [ ] **运行交付门禁**：`cd frontend && pnpm test`、`cd frontend && pnpm check`、根 `make check`、`python3 scripts/check_docs_convention.py`。长命令按仓库约定后台执行，记录真实关键输出。
- [ ] **写 changelog**：包含 TL;DR 与「验收记录」；每条为“命令 → 实际输出关键行”，不得伪造。更新本计划状态头并勾完已执行项。
- [ ] **PR 收口**：按根 AGENTS.md 建分支 `works-assets-ia-cleanup`、逐主题提交、push、PR + OCR；不直推 main。无 Critical/Important 且 CI 绿才可 rebase merge，否则修复或回报。

## 六、P1 / 明确不做

- 页级统一搜索、排序菜单、动作「⋯」收纳、hover 动画、播放覆盖层。
- 最近消息摘要、真实视频自动预览、后端分页、thread 删除。
- 共享卡片组件抽象、设计 token 改造、像素基线。
- 创建、分镜、详情、后端生成管线的任何行为调整。

范围外问题只登记 `docs/findings-backlog.md`，不顺手修改。
