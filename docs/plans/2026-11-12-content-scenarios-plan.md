# 内容创作场景优化实施计划

> **状态**：已执行（2026-11-12；Task 1–2 全部完成，12 张中英文卡片、真实技能路由与完整前端门禁通过，等待用户审查）。执行记录见 `docs/changelog/changelog-2026-11-12-content-scenarios.md`；播客 Agent 仍按 `docs/plans/pending/2026-11-12-podcast-studio-plan.md` 独立串行执行，不属于本批。
> **TL;DR**：按真实输入、工作流与交付物优化 Creative Studio、Course Video、PPT Master 三组共 12 张中英文卡片；只改前端 locale 与点名测试，不改后端、技能、首页六卡或 co-writer；以真实技能路由证据和完整前端门禁验收。

> **For agentic workers:** REQUIRED SUB-SKILL: 使用 `executing-plans`，单执行者、单工作区按 Task 1→2 串行，逐项勾选。实现任务遵循「失败测试 → 确认失败 → 最小实现 → 确认通过 → 本地提交」，不得并行修改共享 locale 或测试。

**Goal:** 让三组既有 Agent 的场景卡与真实输入、技能能力和交付物一致，避免通用、重复或超出能力的提示词。

**Architecture:** 保留通用 Agent chat、新会话 guide、案例 ID 和只预填机制，仅修改 Creative Studio、Course Video、PPT Master 的中英文案例内容。技能与 Agent seed 不变；通过硬编码内容契约、既有交互回归和真实工具路由验证保证文案与能力一致。

**Tech Stack:** React、TypeScript、Vitest、Playwright；不新增依赖、API、数据库、Docker、后端或技能修改。

**Spec:** 用户已确认的 Creative Studio、Course Video、PPT Master 场景优化需求，以及 2026-11-12 同意的复审建议：卡片只承诺技能真实能力、漫画默认建议 4 页且允许调整、内容场景与播客拆成独立交付批次。

**基准 commit:** `12536796`。执行前运行 `git status --short --branch` 与 `git diff 12536796 --stat`；用户提供的两个根目录源视频保留且不得提交。若出现新的代码漂移，先核对共享 locale 与测试再执行。

**执行方式:** 只创建本地 commits，不 push、不建 PR、不部署。Task 2 的真实模型/工具行为验证只在用户批准执行本计划后进行，可停在确认门，不生成最终媒体、不调用 TTS。

## Global Constraints

- 卡片点击仍只预填，不自动发送、不创建线程、不绑定技能；Agent ID、guide props、既有路由和提交机制不变。
- 只改 Creative Studio、Course Video、PPT Master 三组共 12 张卡；co-writer 中英文四卡（含 podcast）逐字不变，其他 Agent 不变。
- PPT 仅将案例 ID `brand-template` 改为 `document-to-course`；其他既有案例 ID 保留。
- 每张卡的标题、描述和提示词必须能对照目标技能 SKILL.md 的真实原语；无法对照的措辞一律删除，不为文案好看而放宽能力。
- 漫画默认建议 4 页、允许用户调整；`four-panel` 只表示四格版式，不用它推导页数。**技能本身不规定默认页数**，4 页是本计划的建议值，实际页数以用户确认门为准。画风、角色和分镜确认后再生成页面与 PDF。
- 不新增“自动录屏、截图标注箭头、自动查证数据来源、虚构 UI、编造数据”等技能不具备或违背真实输入边界的承诺。
- 本批不替换首页 co-writer，不新增播客入口，不改 Writer、seed、SOUL、技能、配置或媒体；这些属于 pending 播客计划或非目标范围。

## 评审决策记录

| Frontier 问题 | 决策 |
| --- | --- |
| Q1：卡片是否只换标题 | 否。同步重写中英文描述与提示词，明确真实输入、工作流和交付物，点击机制不变。 |
| Q2：技能能力与文案冲突时听谁的 | 以技能 SKILL.md 为准；删除超能力承诺，不修改技能迎合文案。 |
| Q3：知识漫画页数 | 默认建议 4 页，用户可调整，画风/角色/分镜先确认；不将 `four-panel` 解释为四页。 |
| Q4：PPT 第四卡如何消除重复 | `brand-template` 改为 `document-to-course`，从 PDF/DOCX/Markdown 经 Default 精编新 PPTX，不走 Quick/Beautify。 |
| Q5：co-writer 与其他 Agent 是否同步改动 | 否。co-writer 四卡用独立硬编码快照保护，其他 Agent 与首页六卡不在本批。 |
| Q6：内容场景与播客是否同一执行批次 | 否。本计划可独立交付；播客部分按文档治理拆到 pending，授权和执行前单独评估漂移。 |

规划 frontier 已收敛；执行中若需要修改技能、Agent seed、后端、首页六卡或任何 ban 路径，立即停止回报。

## 三组 Agent 的内容契约

| Agent | 卡片与主路由 | 必须保留的边界 |
| --- | --- | --- |
| Creative Studio | `knowledge-card` → `knowledge-card-auto-generator`：毛玻璃知识卡，SVG + MP4；`course-poster` → `course-poster`：科技风竖版 PNG（默认 1080×1920）；`infographic` → `trainerai-infographic`：单张中文信息图 PNG；`series-card` 改「把知识点画成连续漫画」 → `trainerai-comic`：默认建议 4 页的连续漫画、角色表/分镜确认、页面图片 + 合并 PDF | 漫画统一角色和画风，先确认画风/分镜，用户可调整页数；imagine 是共享后端，不是替代路由。海报提示词必须收集课程标题、分享日期、课程时长。 |
| Course Video | `knowledge-explainer`：主题调研知识科普；`product-tutorial` 改「用我的截图做产品讲解」：用户提供真实截图 + 图文版式；`concept-animation` 改「把我的数据做成动态图表」：用户提供的数据 → 图表 → 动态视频；`script-to-video`：完整讲稿保真转视频 | 全部仍以 TTS + 自定义 Remotion + 静帧门交付一个 MP4；不承诺录屏、截图标注箭头或自动查证数据来源；缺素材必须要求用户提供，不得虚构 UI 或编造数据；固定稿不改写不扩写。 |
| PPT Master | `from-scratch`：Quick 主题快出；`roundtrip`：Edit Native 局部改稿；`beautify`：Beautify 内容与页序 1:1 美化；`document-to-course`：PDF/DOCX/Markdown 资料精编成新 PPTX | Quick 无 Stage 1/2；Native 未编辑页不重建；Beautify 不改页数/页序，拥挤页只在页内改善，图表/表格按提取数据重建而非逐字节还原；文档走 Default，有结构与方案确认，忠于资料。 |

## 卡片能力核实（执行依据）

| 卡 ID | 允许写的交付物 | 技能依据 | 禁止写的表述 |
| --- | --- | --- | --- |
| `knowledge-card` | 毛玻璃知识卡；SVG 静态图 + MP4 动画；三种主题 | `knowledge-card-auto-generator/SKILL.md` frontmatter | 「任意风格」「自定义品牌」 |
| `course-poster` | 科技风竖版课程海报 PNG；默认 1080×1920；收集标题/分享日期/时长 | `course-poster/SKILL.md:35,47-59` | 「横版」「多尺寸一次出」 |
| `infographic` | 单张信息图 PNG；21 布局 × 22 风格 | `trainerai-infographic/SKILL.md:3`；产出固定 `infographic.png` | 「多页信息图」「自动拆多张」 |
| `series-card` | 默认建议 4 页且可调整的连续漫画 + 合并 PDF；角色表与分镜先确认 | `trainerai-comic/SKILL.md:111,128-129`（`merge-to-pdf.ts`、`{slug}.pdf`）。**注意：技能本身不规定默认页数**，页数来自用户确认门，4 页是本计划的建议值（`:195` 的 `four-panel` 是版式、非页数） | 「four-panel 就是四页」「一次出无限页」「技能默认 4 页」 |
| `knowledge-explainer` | 主题调研 → 知识科普视频 | `course-video` 挂 `deep-research` + `auto-video-flow` | 「实时联网讲最新进展」 |
| `product-tutorial` | 用户提供截图 → 图文版式视频 | `auto-video-flow/SKILL.md:105` | 「自动录屏」「自动截取界面」「截图标注箭头」 |
| `concept-animation` | 用户提供的数据 → 图表 → 动态视频 | `chart-visualization/SKILL.md` | 「自动查证数据」「标注引用出处」 |
| `script-to-video` | 固定讲稿 → 视频，不改写不扩写 | `auto-video-flow` 固定稿纪律 | 「帮你润色讲稿」 |
| `from-scratch` | 主题 → 可编辑课件，跳过 Stage 1/2 | `ppt-master/workflows/profiles/quick-generate.md` | 「无质量检查即可交付」 |
| `roundtrip` | 保原设计的局部内容修改 | `ppt-master/workflows/edit-native-pptx.md` | 「重排版式」 |
| `beautify` | 文字与页序 1:1 不变的美化，拥挤页只页内改善 | `beautify-pptx.md:100,133,152,162` | 「重新组织结构」「拆分拥挤页」「图表逐字节保留」 |
| `document-to-course` | PDF/DOCX/MD → 新 PPTX，忠于资料，走 Default | `ppt-master/SKILL.md:98` + `source_to_md` 链 | 「Quick 直接出」「不改资料结构」 |

## Review Focus

1. **提示词看似合理但技能没有对应能力**：Task 1 逐卡禁止表述断言，Task 2 回读 SKILL.md 并观察真实工具路由。
2. **真实输入被提示词省略**：海报标题/分享日期/时长、产品截图、用户数据、固定讲稿和文档来源必须在 Task 1 有中英文断言。
3. **修改三组卡片时误伤 co-writer 或其他 Agent**：独立硬编码快照不得从被测 locale 派生，Task 1/2 均验证旧交互；`agent-new-chat-guide.dom.test.tsx` 只允许改三处文案字面量，结构/交互断言必须原样保留。
4. **案例 ID 只用于 React key，不能证明运行时路由**：播客不在本批；本批 Task 2 必须看实际技能读取/工具调用，不能只看 ID 或模型自述。
5. **PPT 文档卡误入 Quick/Beautify/Create Template**：Task 1 断言 Default 触发语且不提 Quick；Task 2 观察实际读取的是 `workflows/generate-pptx.md`——`brand-template` 删除后 `workflows/create-template.md` 仍在仓库，旧提示词可能仍命中该路由。可停在方案确认门。

## 路径边界（allow / ban / 共享基座）

| 允许路径（allow） | 禁改路径（ban） | 共享基座触达 |
| --- | --- | --- |
| `frontend/src/core/i18n/locales/{types,zh-CN,en-US}.ts` | 其他前端组件、路由、Writer、registry UI、首页六卡和媒体 | **是**：共享 locale，单执行者独占 |
| `frontend/tests/unit/core/agents/new-chat.test.ts`；`frontend/tests/unit/components/workspace/agent-new-chat-guide.dom.test.tsx`；`frontend/tests/e2e/agent-new-chat-guide.spec.ts` | 其他测试只运行不修改；不得删除/弱化旧断言或隐藏旧 DOM | **是**：共享 guide 测试，串行修改 |

> **allow 缺口说明（2026-11-12 复审补记）**：`agent-new-chat-guide.dom.test.tsx` 的默认 `agentName` 是 `course-video`，并硬编码断言了 Course Video 卡的英雄标题（`:141`「把知识讲成一堂视频课」）、首卡标题（`:145`「讲清一个知识点」）与 tips 文案（`:164`）。Task 1 改动这些文案后该测试必然变红，必须同步修改——它是被测文案的守护测试，不是可绕过的护栏。修改口径：只更新随文案变化的字面量，保留全部结构与交互断言（case 数量、disabled 态、草稿确认、locale 切换等）。
| 本计划与 `docs/changelog/changelog-2026-11-12-content-scenarios.md` | pending 播客计划、旧计划、README、AGENTS.md、第二份设计稿 | 同一执行者收尾，真实「验收记录」 |

**一律禁改：** `backend/**`、`skills/**`、`docker/**`、`config*.yaml`、`.env*`、数据库/迁移、锁文件、真实服务器配置、`frontend/src/core/writer/**`、co-writer 与其他未点名前端文件。需要修改时立即停止并报告。

## 文件职责图

| 文件 | 动作 | 单一职责 |
| --- | --- | --- |
| `frontend/src/core/i18n/locales/types.ts` | Modify | 仅 PPT 新案例 ID 所需的类型字面量 |
| `frontend/src/core/i18n/locales/{zh-CN,en-US}.ts` | Modify | 三组 12 卡中英文标题、描述与提示词；co-writer 不变 |
| `frontend/tests/unit/core/agents/new-chat.test.ts` | Modify | 内容契约、禁止表述、必填输入和 co-writer 硬编码快照 |
| `frontend/tests/unit/components/workspace/agent-new-chat-guide.dom.test.tsx` | Modify | 仅更新随文案变化的硬编码字面量（英雄标题/首卡标题/tips），保留全部结构与交互断言 |
| `frontend/tests/e2e/agent-new-chat-guide.spec.ts` | Modify | 只预填、手动提交、双语与非目标 Agent 行为回归 |
| `docs/changelog/changelog-2026-11-12-content-scenarios.md` | Create | 本批真实命令、行为证据和未解决事项 |

---

### Task 1：按真实场景优化三组既有 Agent 的四卡

**Files:**
- Modify: `frontend/src/core/i18n/locales/{types,zh-CN,en-US}.ts`
- Modify: `frontend/tests/unit/core/agents/new-chat.test.ts`
- Modify: `frontend/tests/unit/components/workspace/agent-new-chat-guide.dom.test.tsx`
- Modify: `frontend/tests/e2e/agent-new-chat-guide.spec.ts`

**Interfaces:** 保留 Creative/Course/PPT 的 Agent ID、现有案例 ID（除 PPT `brand-template` 改为 `document-to-course`）、通用 guide props 与只预填机制。

- [x] **Step 1：先写三组场景内容与旧能力保护失败测试**
  按内容契约逐卡断言中英文标题、输入、交付物与禁止表述。覆盖漫画默认建议 4 页/可调整/连续角色/分镜/PDF，海报标题/分享日期/时长，产品卡真实截图且无标注，数据卡用户数据且无自动溯源，Beautify 三项 v1 边界，固定稿不改写，PPT 文档卡走 Default 且不提 Quick。另用独立硬编码对象逐字段快照 co-writer 中英文四卡（含 podcast），预期值不得从被测 locale 派生。
  **同时更新 `agent-new-chat-guide.dom.test.tsx` 的三处硬编码字面量**（`:141` 英雄标题、`:145` 首卡标题、`:164` tips）到新文案，其余断言一字不动——这三处是文案的直接守护，不更新会让 Task 2 的 `pnpm test` 无法转绿。
- [x] **Step 2：分别运行测试确认旧文案失败**
  Run（分别执行并记录，不得用 `&&` 短路灯红阶段）：`cd frontend && pnpm exec vitest run --project node tests/unit/core/agents/new-chat.test.ts`；`cd frontend && pnpm exec vitest run --project dom tests/unit/components/workspace/agent-new-chat-guide.dom.test.tsx`；`cd frontend && CI=1 E2E_PORT=3100 pnpm exec playwright test tests/e2e/agent-new-chat-guide.spec.ts`。
  Expected: 新场景与禁止表述断言因旧文案 FAIL；不得先改实现。co-writer 快照应在改动前后均通过，若失败说明误伤旧能力。DOM 测试的失败应仅来自三处文案字面量，若出现结构/交互断言失败说明改错了范围。
- [x] **Step 3：最小更新中英文四卡**
  同步 zh-CN/en-US 与类型，只改 Creative、Course、PPT 三组共 12 张卡；co-writer 四卡一个字都不改。每张卡回读「卡片能力核实」对应 SKILL.md；`document-to-course` 明确走 Default，不使用 Quick 触发语，且不得命中 `workflows/create-template.md`（该工作流仍在仓库中，`brand-template` 删除后仍可被旧提示词触发）。
- [x] **Step 4：确认内容与既有交互回归通过**
  原样重跑 Step 2 三组命令，并运行 `cd frontend && pnpm check`，Expected: 全绿。三组 12 张卡符合新契约，co-writer 四卡逐字未变，合计 16 张仍只预填；标记、草稿、handoff、非目标 Agent 和提交次数不回归；DOM 测试的结构与交互断言全部保留且通过。
- [x] **Step 5：本地提交**
  Commit: `feat(agent): 按真实场景优化创作案例`。

### Task 2：真实技能路由、全量门禁与交付

**Files:** 行为验证仅使用本地测试线程和输出；文档仅修改本计划并创建点名 changelog。

- [x] **Step 1：验收三组 Agent 的真实路由与能力边界**
  Creative 漫画实际读取 `trainerai-comic`，默认建议 4 页但允许用户调整；Course 产品卡要求真实截图且不承诺标注、数据卡要求用户数据且不承诺溯源、固定稿不改写；PPT 文档卡进入 Default 而非 Quick/Beautify。可停在确认门，避免全量渲染和 TTS；证据必须来自实际技能读取/工具调用，不得只看模型自述。逐卡回读 SKILL.md，承诺找不到原语即退回 Task 1 修正。
- [x] **Step 2：运行前端完整门禁**
  按长命令规范后台运行 `cd frontend && pnpm check && pnpm test`；随后运行 `cd frontend && CI=1 E2E_PORT=3100 pnpm test:e2e && pnpm perf:check`，全部通过，无新增 JavaScript pageerror。
- [x] **Step 3：更新计划与 changelog**
  逐项勾选并更新唯一状态头，记录执行范围和 changelog 链接。changelog 必须含 ≤5 行 TL;DR 与「验收记录」节，逐行写真实命令和关键输出，分别记录 12 卡内容、co-writer 保护、真实技能路由、完整门禁和未解决事项。
- [x] **Step 4：对最终文档重跑根门禁**
  在 Step 3 完成后运行仓库根 `make check && python3 scripts/check_docs_convention.py && git diff --check`，全部通过；不得只在 changelog 创建前运行文档门禁。
- [x] **Step 5：清理并本地提交文档**
  确认无在途任务/测试容器残留，清理本批不需要的测试输出，汇总修改文件、commit、真实证据、遗留项和审查重点。只提交本地文档，不 push、不建 PR、不部署。

## 回滚与停止规则

- 内容或交互回归只能回退对应 locale/测试改动，不得删除 co-writer、隐藏旧 DOM 或放宽断言。
- 真实路由与卡片承诺不一致时返回 Task 1 修文案；不得修改技能、seed、后端或 ban 路径迎合卡片。
- 若无法区分基准失败，保留现场并报告具体命令、文件和失败输出，不伪造通过结果。
- pending 播客计划不是本批交付物；不得为了“顺便完成”而提前执行其中任何步骤。

## 本地完成定义

1. 三组 12 张场景卡每张都能落到技能依据，中英文与旧交互不回归，co-writer 四卡逐字未变。
2. 海报必填输入、真实截图、用户数据、固定稿、漫画页数与 PPT Default/v1 边界均有内容和行为证据。
3. 真实技能读取/工具调用证明高风险路由正确，不能只用模型自述或案例 ID 验收。
4. `pnpm check`、`pnpm test`、`pnpm test:e2e`、`pnpm perf:check`、根 `make check`、文档门禁和 `git diff --check` 全部通过。
5. 计划勾选、执行 changelog 和真实验收记录完成；`agent-new-chat-guide.dom.test.tsx` 仅改文案字面量、结构与交互断言全部保留并通过；只做本地 commits，等待用户审查。
