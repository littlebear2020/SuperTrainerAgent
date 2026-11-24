# Agent 新会话案例与创作配方优化实施计划

> **状态**：已执行（2026-11-24 定稿并当日执行完成；范围 = 第七节全部项；最小 live 冒烟当日补跑，5 条 run 中 4 条通过，Edit Native 一条因模型供应商读超时未验证，第八节「真实 agent」据此保持未勾选；执行中对计划的 CTA 文案长度、占位符认定与“先确认再出图”的表述做了修正，见各勾选项注记；无拆出部分）。changelog：`docs/changelog/changelog-2026-11-24-agent-showcase-style-workflow.md`。
> **基准 commit**：`ab92a527`（执行前先运行 `git diff ab92a527 --stat` 评估漂移）。
> **TL;DR**：在不改变现有页面与案例布局的前提下，把课程海报、文字动效、PPT 课件、语音播客的案例提示词改成“用我的内容复用此创作方式”的通用模板：点击只填模板、不自动发送；课程海报把知识卡片与信息图合并为「知识图解」，预览弹窗底部增加同一 CTA。关键决策 = 首版不做自动参考图（附件到 agent 只是文件路径，10 个案例里 6 个能用且可能把旧文案带进新图），相关状态机整块删除。影响面 = 仅 `frontend/` 的 showcase 数据、卡片、弹窗、i18n 与测试；不动 backend、技能、部署。

**Spec / 需求来源**：用户截图、整体产品评审与补充约束；临时交互 Demo `temp/2026-11-24-creative-studio-showcase-demo/index.html`、`video-demo.html`、`ppt-demo.html`、`podcast-demo.html`。Demo 只证明交互方向，**不是布局、断点或视觉结构的迁移来源**；其模板文案与运行时有冲突，不能照抄（见第五节）。Demo 位于已 gitignore 的 `temp/`，执行时可能已不存在，**缺失时以本计划及附录 A 为准**。

**执行方式**：单执行者、独立分支，正式改动仅限 frontend。按 Agent 垂直切片，依次完成课程海报 → 文字动效 → PPT 课件 → 语音播客，每片自带测试与提交。**与 `docs/plans/2026-11-17-core-product-frontend-reliability-plan.md` 串行，本计划先做**。执行前将本文件移至 `docs/plans/` 并维护勾选和状态头。全程一次执行到底，不设中途人工过目点。

## 评审决策记录

2026-11-24 评审修订（Claude Code 入口 grill + 代码核查）。本节是范围与实现决策的单一事实源：

- **自动参考图首版不做**：参考图到 agent 只是 `<uploaded_files>` 里的一个文件路径——提交只发 text（`frontend/src/core/threads/hooks.ts`），`uploads_middleware.py` 的使用提示全是读文档 / grep / glob，`backend/seed/agents/creative-studio/SOUL.md` 对用户附件未置一词。10 个案例里，结构图 2 件（trainerai-diagram 手写 SVG）无法消费参考图，海报 2 件只有走 trainerai-imagine 才有 `--ref`；其余 6 件的 `--ref` 走图片编辑接口、没有“只取风格”的强度控制，而预览图带着原案例文案，容易把旧文案带进新图。连带不做：`onUsePrompt` 的 `reference` 参数、`VisualDialog` 的「仅填入提示词」、request id / 失败回滚、按文件名识别自动附件。重启条件：技能侧有可靠的“仅风格参考”入口，或备有无字风格样图。
- **模板骨架进计划（附录 A），20 篇由骨架加修正确定**：骨架沿用 Demo 的参数化结构。20 篇模板的 diff 写进 PR 描述，由用户在 PR 上过目；执行过程一次做到合入，不中途停下等过目（2026-11-24 用户决定）。
- **Demo 模板不能照抄，见第五节修正表**：PPT 与视频的首行 `[模式：…]` 没有运行时含义且会让既有 marker 用例变红；海报要走 trainerai-imagine；“冷白蓝图”不能承诺冷白底；Edit Native 的“360 成品”说明不进提示词。
- **最小 live 冒烟（用户 2026-11-24 评审时同意，边界如下，超出需重新确认）**：本机 `make dev`，不部署；共 5 条 run、至多 1 张图、0 次 TTS / 渲染，每条最多跑 1 次（明细见第七节）。模板从没见过真实 agent，这是唯一能验证路由与确认门的手段。
- **与前端可靠性计划串行，本计划先做**：PR1「输入与上传」的路径是 `input-box.tsx`、两个 chat page、`core/{threads,uploads}/**`；本计划的 Guide 宿主是 `frontend/src/app/workspace/agents/[agent_name]/chats/[thread_id]/page.tsx`。文件级不重叠，语义上共用提交上传链；PR1 合入后 rebase 时重跑 `agent-new-chat-guide` 两个测试文件。可靠性计划侧需补一行互记（待办，不在本次修订范围）。同日的 `docs/plans/2026-11-24-personal-usage-layout-redesign-plan.md` 与本计划共用三个 i18n locale 文件（不同键块），不构成阻塞，后合入者 rebase。
- **分类合并键**：数据层 `category` 不动；筛选层把 `card` 与 `infographic` 合并为键 `card`，testid 仍为 `showcase-filter-card`。
- **断言分两类**：布局基线（禁改）与预期内更新（筛选计数、CTA 可访问名前缀），清单见第七节。
- **视频卡 CTA 文案长度**：不超过 6 个汉字、英文不超过 16 个字符。xl 下视频卡内宽约 228px，底行是可截断的元信息加不换行按钮，文案一长音色行就被挤掉。
- **视口**：用浏览器实测 1440 / 1280 / 1024 / 390。页面宽度封顶 1180px，1440 与 2000 等效；1280 是 xl 起点，1024 是 lg 起点（双栏开始）。
- **做减法**：不做“CTA 前后媒体请求对比”，并入“附件数不变 + 模板不含案例媒体路径”两条断言；布局基线只钉网格容器 class，首行三卡 / 首屏不溢出 / 字阶沿用既有 e2e；不新增 `modeLabel`。
- **卡片标注取最小解读**：原“工作流 / 形式与视觉、声音元信息分开展示”落为——PPT 卡风格行加「视觉：」前缀，播客卡 tag 追加「案例实录」、来源行加「案例制作时来源：」前缀。不新增行、不改顺序和 class；视频卡已分别展示讲稿方式、音色与风格，不动。
- **findings backlog**：2026-11-24 查过，无相关项。

## 一、最高优先级：现有布局硬约束

实施时不得通过调整 grid、columns、卡片宽度和断点来“复刻 Demo”。以下结构是验收基线，只能复用，不能重排。

| 区域 | 必须保留的真实结构与数量 |
|---|---|
| 四类页面共享 | 原工作区导航、顶部 Agent badge、新对话/Tokens、人物 Hero、居中玻璃 `InputBox`、左侧 HOW IT WORKS、右侧案例区、原 Dialog 机制均不变。 |
| 课程海报 `creative-studio` | 默认直接展示全部 10 件；`all` 保持 `columns-1 sm:columns-2 xl:columns-3` 瀑布流；分类筛选保留现有普通 grid（`grid gap-4 sm:grid-cols-2 xl:grid-cols-3`），不得挤成单列。 |
| 文字动效 `course-video` | 默认全部 3 件；保留 `grid gap-4 sm:grid-cols-2 xl:grid-cols-3`，桌面 xl 必须**一行三个案例卡片**，不得变成一行两个。 |
| PPT 课件 `ppt-master` | 默认全部 3 件；保留 `flex flex-col gap-4` 的纵向宽卡片；每张卡仍为左侧 2×2 缩略图、右侧内容，保留 `md:grid-cols-[250px_minmax(0,1fr)]` / `xl:grid-cols-[296px_minmax(0,1fr)]` 两档内网格。 |
| 语音播客 `podcast-studio` | 默认全部 4 件；保留 `grid gap-4 sm:grid-cols-2` 的 2×2 卡片、共享单 `<audio>`、互斥播放和原有卡片内容区。 |
| 卡片与弹窗 | 只允许改 CTA、徽标与标签前缀的文案，以及在原 `DialogContent` 底部追加一行说明与按钮；不新增行、不改顺序、不改宽度与媒体比例。原预览能力全部保留：视频播放、PPT 翻页与缩略图、播客文稿与下载、视觉原图 / SVG 下载及焦点恢复。 |

**布局唯一事实源是正式组件**：`agent-showcase.tsx`、`showcase-cards.tsx`、`showcase-audio-player.tsx`、`showcase-dialogs.tsx`。任何与 Demo 不一致之处，以这些文件为准。

## 二、目标与非目标

### 目标

- 将四类 Agent 的案例 `prompt` 从“重做原案例”改为“用我的内容复用此创作方式”，20 篇模板由附录 A 确定。
- 案例卡仍展示真实标题、时长、页数、来源和风格；只有点击后带出的模板被泛化。
- 卡片与原预览弹窗使用同一选择入口和草稿保护；所有 CTA 只填充，不自动发送、不创建 thread/run。
- 课程海报合并知识卡片 / 信息图为「知识图解」，默认 10 件。
- 文字动效区分固定讲稿 / 主题调研；PPT 区分 Quick / Default / Edit Native；播客区分四种节目形式。
- 模板措辞与运行时一致：路由、画幅、确认门、必留标记经代码核对，并用最小 live 冒烟验证。
- 用户手动上传的讲稿、文档、PPTX、图片和其他附件始终保留（本批不程序化增删任何附件）。

### 非目标

- 不改变任何已确认的页面布局、卡片断点、排序、Hero、composer 或导航。
- 不做自动参考图及其唯一性 / 竞态 / 失败回滚（重启条件见「评审决策记录」）；不新增 `modeLabel`。
- 不给文字动效、PPT、播客增加精选、查看全部或分类筛选开关。
- 不自动附视频 MP4、视频海报、PPT 完成品、幻灯片图片、参考 MP3 或旧来源链接。
- 不新增品牌中性模板、Remotion 主题 ID、声音克隆或音频韵律提取能力。
- 不把真实生成作为产品功能（冒烟只用于验收，见第七节）；不改 backend、技能、Agent runtime、上传接口、模型、数据库或配置版本。
- 不修改 `frontend/src/components/ui/`、`frontend/src/components/ai-elements/` registry 文件，不迁移 Demo DOM/CSS/JS。

## 三、统一交互与数据决策

| 决策点 | 已定方案 |
|---|---|
| 案例事实 vs 新任务 | 标题、时长、页数、来源属于案例事实；新模板不继承原主题、原目录、样片秒数、固定 7 页、旧观点或旧来源。 |
| 案例数据 | 继续以 `frontend/src/core/agents/showcase.ts` 为单一事实源；现有 `prompt` 改为通用模板；`ShowcaseCaseBase` 新增可选 `actionLabel`（CTA 文案）与 `recipeNote`（弹窗底部“保留 / 填写”说明），均为 `LocalizedText`。 |
| 卡片与弹窗 CTA | 调用同一 `onUsePrompt(prompt)`，签名不变；弹窗内先关闭预览，再进入草稿判断，避免双 Dialog 叠加。 |
| 非空草稿 | 继续取消 / 追加 / 替换；确认前不改变文本，任何路径都不碰附件。 |
| 附件 | 本批不新增任何程序化附件：CTA 不 fetch、不上传、不改附件列表。用户想要参考图，可用弹窗已有的「下载原图 / SVG」自行附上。 |
| 填入后的光标 | 选中刚填入片段里的第一个 `【…】` 占位符（Demo 行为），没有占位符则光标置末；除此之外不改 composer。 |
| 占位符 | 沿用 Demo 写法：`【请填写】`、`【请填写，可留空】`、`【可选填写】`。不做前端校验，未替换就发送时由 agent 追问。 |
| CTA 文案 | 卡片与弹窗用同一 `actionLabel`；`aria-label` 为 `${actionLabel}: ${标题}`。视频卡不超过 6 个汉字、英文不超过 16 个字符。 |
| 首行语法 | 只有播客（`[模式: read_original]` 等，技能路由依赖）和视觉（`[模式: 视觉创作]` / `[模式: 结构图]`，与现有 marker 用例一致）保留首行；PPT 与视频不使用 `[模式：…]` 首行。 |
| 响应式与 i18n | 沿用现有 Tailwind 断点和 token，新增文案进入 `zh-CN.ts`、`en-US.ts`、`locales/types.ts`；提示词在两个语言下都保持中文（现状）。 |

## 四、各 Agent 案例配方

### 4.1 课程海报：风格与版式

- 一级筛选为「全部 / 课程海报 / 知识图解 / 知识漫画 / 结构图」；`card + infographic` 合并为知识图解，固定 4 件（合并键见「评审决策记录」）。
- 默认全部 10 件，不新增 `featured`、`showAllCases` 或查看全部按钮；`all` 显示 `10 件`，分类显示 `n / 10 件`。知识图解 4 件在 xl 下按 3 列排成 3+1 左对齐。
- 10 个视觉模板保留首行、风格、版式、画幅与输出格式；主题、知识点、角色、情节、节点与文案均留空（附录 A.1）。
- 路由与画幅必须写进模板：海报两篇点名 trainerai-imagine（9:16）；知识图解与漫画写死 16:9（SOUL 默认卡片 1:1、漫画 9:16）；“冷白蓝图”一篇只保留版式与分区配色，底色沿用技能默认暗色主题。
- CTA 为「按此风格创作」；结构图标注「SVG 可编辑」。
- 发送后 agent 会先确认方案再出图，模板不写“跳过确认”。（执行修正：冒烟实测这句不成立——内容齐全时 agent 把「需求确认」作为自查步骤后直接出图，只有缺内容才先索取。模板正文与此一致，不需改；弹窗说明已从“先确认方案再出图”改为“内容齐全时 agent 会直接出图；缺内容会先向你索取”。）

### 4.2 文字动效：制作方式与文本配方

| 案例 | 保留 | 用户输入 | CTA | 自动资产 |
|---|---|---|---|---|
| `video-hallucination` | 固定讲稿；深色科技、网格与概率条；云舟、字幕、无 BGM 作为可改默认值 | 自己的完整口播稿；可改视觉、音色、语速、字幕 | 按此风格创作 | 无 |
| `video-feynman` | 固定讲稿；暖白纸感、杂志排版、手绘线条；Vivi 默认值 | 自己的完整口播稿；可改默认值 | 按此风格创作 | 无 |
| `video-agent-research` | 主题调研；深绿纪实、来源卡片、证据链；刘飞默认值 | 新主题、受众、目标时长；来源偏好可选 | 按此调研创作 | 无 |

共同约束：

- 固定讲稿必须保真、不改写、不扩写、不主动联网；主题调研必须显式要求联网调研并先展示实际来源、完整口播稿和分镜，确认后再 TTS。只给主题时 agent 默认自行写稿、不搜索，所以“联网调研”不能省。
- 三篇都保留 `/skill auto-video-flow`（course-video 绑定的技能）与确认语句；不使用 `[模式：…]` 首行。
- 32/29/39 秒只用于展示；固定讲稿时长按稿长和语速估算，调研模式由用户给目标值，最终以实际 TTS 为准。
- 固定讲稿模式下分镜展示后不阻塞、直接进 TTS（`skills/public/auto-video-flow/SKILL.md` 确认门②）；模板与弹窗说明都不承诺“分镜会停下等确认”。
- InfoQ、经济参考报、KubeCon 仅标为“本案例来源”；不附 MP4、海报或旧分镜，不暗示存在视频风格反解能力。

### 4.3 PPT 课件：工作流与原生文件边界

| 案例 | 保留 | 用户输入 | CTA | 自动资产 |
|---|---|---|---|---|
| `ppt-quick` | Quick 跳过 Stage 1/2；轻量科技、暖纸钴蓝；原生可编辑 | 主题或资料；受众、目标、页数、风格可填或由 Agent 判断 | 按此方式快速出稿 | 无 |
| `ppt-standard` | Default 两阶段确认；深色杂志、琥珀与青 | 主题或资料、受众基础、学习目标、场景；页数可推荐 | 按此方式规划制作 | 无 |
| `ppt-template` | Edit Native 页面计划、源对象保留、原生改造 | 自己有权编辑的 PPTX + 新内容资料；保留 / 删除 / 改造范围 | 按此方式改造我的 PPT | 无 |

共同约束：

- 不使用 `[模式: …]` 首行：ppt-master 的 mode 指沟通模式（`references/modes/_index.md`），没有首行语法。Quick / Default / Edit Native 的触发语句沿用现有提示词（附录 A.3）。
- 不继承固定 7 页和原知识目录；Quick 不等于缩减质量，Default 保留两次确认，Edit Native 在编辑前确认“输出页 → 源页 → 操作”的页面计划。
- 普通 Quick / Default 不附完成品 PPTX 或 WebP，避免误入旧课件改造路线。
- `template-360-ai-v4.pptx` 是含品牌与原内容的案例成品，不作为公共模板自动上传；“案例成品不会自动作为你的模板”写进弹窗 `recipeNote`，不进提示词。
- Edit Native 要求 `.pptx`（上传白名单不含 `.ppt`）。
- 若未来增加“使用示例模板试做”，必须另制品牌中性、内容清空、授权明确且通过 roundtrip 的原生 PPTX，不在本批范围。

### 4.4 语音播客：节目形式与固定音色

| 案例 | 保留 | 用户输入 | CTA |
|---|---|---|---|
| `podcast-context` | `read_original`；不增写、不改写、不总结；云舟单音色 | 自己有权使用的文档或安全公开链接 | 套用原文朗读 |
| `podcast-agent2026` | `news_single`；实际来源、单人完整稿、确认后合成 | 新主题或资料；受众与关注角度可选 | 套用单人播报 |
| `podcast-embodied` | `interview_two`；男主持 + 女嘉宾，多轮问答 | 新主题或资料；嘉宾背景、重点问题可选 | 套用双人访谈 |
| `podcast-open-source` | `debate_two`；陈述、回应、总结 | 新辩题或资料；双方立场与评判维度可选 | 套用双人辩论 |

共同约束：

- 首行逐字保留（技能路由依赖）。
- 音色按模式固定（`podcast_studio.py` 校验）：单人 = 云舟2.0，双人 = 云舟2.0 + Vivi2.0。模板不提供自由选择，不暗示声音克隆或韵律复刻。
- 四个模板均不附 MP3、不自动附旧 URL；旧数字、旧对白、旧观点、旧机构来源不进入新任务。
- 原文朗读不得声称已上传《上下文工程》；未提供文档 / 链接时先索取。
- 卡片与弹窗标为“案例实录”“案例制作时来源”；展示用 transcript 与新任务输入分离。

## 五、模板通用规则与对 Demo 的修正

通用规则（20 篇都适用）：

1. 中文；不含本案例标题、固定秒数 / 页数、旧来源名与链接、旧知识点与示例文案、案例媒体路径（`/agent-showcase/…`）。
2. 必留标记——既有用例 “preserves the agent workflow markers and confirmation boundaries” 必须继续绿：视频含 `auto-video-flow` 与 `确认`，固定讲稿两篇含 `严格依据我上传`，调研一篇含 `来源`；PPT 的 Quick 含 `Quick` 与 `Stage 1/2`，Default 含 `Default` 并匹配“先…确认…结构…方案”，Edit Native 含 `Edit Native` 并匹配“保留原…设计”“未编辑…不重建 / 未修改的对象保持原样”；播客与视觉的首行逐字相等。
3. 需要用户上传时写明文件格式；页数、时长一律留空给用户填或由 agent 推荐。
4. 风格、音色等默认值带“（可修改）”标注。

Demo 模板到本计划的修正：

| Demo 写法 | 问题 | 本计划 |
|---|---|---|
| PPT 首行 `[模式: Quick]` / `[模式: Default]` / `[模式: Edit Native]` | ppt-master 的 mode 是沟通模式，没有首行语法；Demo 正文缺 `Stage 1/2`，marker 用例会红 | 去掉首行，用现有提示词的触发语句 |
| 视频首行 `[模式：固定讲稿]` / `[模式：主题调研]`，且没有 `/skill auto-video-flow` | course-video 按是否上传完整讲稿自动判定；缺标记 marker 用例会红 | 去掉首行，补回技能标记 |
| 海报只写“按参考风格制作” | SOUL 把“课程海报固定版式”路由到 course-poster 固定模板，案例风格无法复现 | 点名 trainerai-imagine |
| “冷白蓝图”写“冷白蓝图底” | SOUL 与 trainerai-diagram 都强制暗色主题 | 底色沿用技能默认，只保留版式与分区配色 |
| Edit Native 提示词含“本案例的 360 成品…” | 案例品牌进了新任务 | 移到弹窗 `recipeNote` |

## 六、路径边界（allow / ban / 共享基座）

| 类别 | 路径 |
|---|---|
| 允许（allow） | `frontend/src/core/agents/showcase.ts`；`frontend/src/components/workspace/agent-showcase/**`；`frontend/src/components/workspace/agent-new-chat-guide.tsx`（仅 `focusComposer` 的选中占位符逻辑）；`frontend/src/core/i18n/locales/{zh-CN,en-US,types}.ts`；`frontend/tests/unit/core/agents/showcase.test.ts`；`frontend/tests/unit/components/workspace/agent-*.dom.test.tsx`；`frontend/tests/e2e/agent-new-chat-guide.spec.ts`；新增 `frontend/tests/e2e/agent-showcase-style-workflow.spec.ts`；本计划与对应 changelog |
| 禁改（ban） | registry：`frontend/src/components/ui/**`、`frontend/src/components/ai-elements/**`；`backend/**`、`skills/**`、`config*.yaml`、`docker/**`；`temp/` 下的 Demo 不入库 |
| 共享基座（前端可靠性计划 PR1 的路径，本计划不碰） | `frontend/src/components/workspace/input-box.tsx`；`frontend/src/app/workspace/agents/**` 下的 page；`frontend/src/core/{threads,uploads}/**`；`frontend/tests/e2e/utils/mock-api.ts` |

## 七、实施清单

### 0. 执行前

- [x] `git diff ab92a527 --stat` 评估漂移；确认前端可靠性计划 PR1 是否已合入——已合入则先读其 changelog，完成后重跑 `agent-new-chat-guide` 相关测试。（2026-11-24 执行：HEAD = origin/main = `ab92a527`，代码零漂移；PR1 未合入，无 changelog。）
- [x] 将本文件移至 `docs/plans/`，维护勾选与状态头；确认 `temp/` 下的 Demo 是否还在，不在则以附录 A 为准。（Demo 仍在主 clone 的 `temp/`。）

### 1. 切片 A：共享底座 + 课程海报

- [x] A1 布局基线：在 `agent-showcase.dom.test.tsx` 钉住网格容器 class——`columns-1 gap-4 sm:columns-2 xl:columns-3`、`grid gap-4 sm:grid-cols-2 xl:grid-cols-3`（视频与筛选后的视觉共用）、`flex flex-col gap-4`、`grid gap-4 sm:grid-cols-2`，以及 DeckCard 的两档内网格。视频首行三卡、首屏不溢出、字阶沿用既有 e2e（`wide video showcase…`、`showcase viewport…`、`showcase cards keep the P1 type scale`），不重写。此后任何任务不得修改这些断言。
- [x] A2 数据模型：`ShowcaseCaseBase` 新增可选 `actionLabel`、`recipeNote`；`PromptButton` 优先用 `actionLabel`，缺省回落到现有 `usePrompt`；`aria-label` 改为 `${actionLabel}: ${标题}`。
- [x] A3 分类合并：数据层 `category` 不动；`agent-showcase.tsx` 的筛选把 `card` 与 `infographic` 合并为键 `card`（匹配两者），testid 仍为 `showcase-filter-card`；`filterCard` 改为「知识图解 / Knowledge graphics」，删除 `filterInfographic` 键（三个 locale 文件）；4 个 card / infographic 案例的 `tag` 改「知识图解 · <风格>」；`note` 改为 4 类口径。
- [x] A4 预期内的断言更新（不属于布局基线）：DOM `filters visual cases…`（筛选按钮 6 → 5，`card` 下 2 → 4 件，“4 / 10 件”）；e2e `creative-studio showcase filters real designs…`（`card` 下 2 → 4 件）、`visual filters keep counts in sync…`（筛选列表改为 poster / card / comic / diagram，件数 2 / 4 / 2 / 2，`card` 在 1440 下占 3 列、其余 2 列）、`English showcase keeps long hero…`（可访问名前缀由 `Use this prompt:` 改为新的 `actionLabel.en`）。（English 一条针对 ppt-master，已随切片 C 更新，并补了弹窗说明行与 CTA 的英文断言。）
- [x] A5 课程海报 10 篇模板：按附录 A.1 写入 `prompt`；`actionLabel`「按此风格创作 / Create in this style」；`recipeNote` 按第四节表格写“保留 / 填写”。
- [x] A6 弹窗 CTA：`VisualDialog` 底部加一行 `recipeNote` 说明与 CTA（testid `showcase-use-style`）；点击先 `onClose` 再 `onUsePrompt(item.prompt)`；`VisualCard` 的 SVG 徽标改「SVG 可编辑」。
- [x] A7 焦点：`AgentShowcase.closeDialog` 增加“不回位卡片”的选项，CTA 发起的关闭使用之；`focusComposer` 改为选中刚填入片段的第一个 `【…】` 占位符。（执行修正：占位符只认 `【请…】` / `【可选填写…】` 形态，`【我的内容】` 这类小节标题不选中。）
- [x] A8 测试：`showcase.test.ts` 新增“模板不含本案例标题、带数字的秒 / 页、来源标签与 URL、`/agent-showcase/` 路径”断言，既有 marker 用例保持绿；DOM 新增“弹窗 CTA 先关预览再填入、附件数不变、不触发 submit、焦点落在输入框或确认弹窗”；`agent-new-chat-guide.dom.test.tsx` 新增“填入后选中首个占位符”。定向测试绿后提交。

### 2. 切片 B：文字动效

- [x] B1 三篇模板（附录 A.2）；`actionLabel`：按此风格创作 / 按此风格创作 / 按此调研创作（英文 Use this style / Use this style / Research first）。（执行修正：视频卡 CTA 改为 5 字「按风格创作 / 按风格创作 / 调研后创作」。改动前实测 1440 下卡片宽 260px、元信息列 108px，「云舟2.0（男声）」恰需 108px；6 字 CTA 会让按钮宽约 12px，音色行在 1440 即开始截断，违反 B3。英文不变。）
- [x] B2 `VideoDialog` 底部 CTA；关闭必须暂停视频。
- [x] B3 测试：marker 用例保持绿；模板不含 32 / 29 / 39 秒与 InfoQ / 经济参考报 / KubeCon；DOM 断言弹窗 CTA。提交后用浏览器在 1280 与 1440 视口实测音色行截断不多于现状。（实测：1440 与 1280 下元信息列 `clientWidth / scrollWidth` 与改动前逐项相同。）

### 3. 切片 C：PPT 课件

- [x] C1 三篇模板（附录 A.3）；`actionLabel`：按此方式快速出稿 / 按此方式规划制作 / 按此方式改造我的 PPT（英文 Quick-draft this way / Plan and build this way / Edit my PPT this way）；`recipeNote` 写明“案例成品不会自动作为你的模板”。（执行修正：CTA 改为 4 字「按此出稿 / 按此规划 / 按此改造」，英文 Quick draft / Plan and build / Edit my PPT。实测 1280 下 PPT 卡宽 655px，按钮行在 CTA 为 106px 时单行，原定文案为 142 / 169px，会让三张卡的按钮行换到第二行、每张高 44px；1024 下 169px 的那张同样换行。硬约束是不新增行。）
- [x] C2 `DeckDialog` 底部 CTA；DeckCard 风格行加「视觉：」前缀。
- [x] C3 测试：marker 用例保持绿；模板不含“360”“7 页”与原主题；DOM 断言弹窗 CTA。提交。

### 4. 切片 D：语音播客

- [x] D1 四篇模板（附录 A.4，首行逐字保留）；`actionLabel`：套用原文朗读 / 套用单人播报 / 套用双人访谈 / 套用双人辩论（英文 Read as written / Solo briefing / Two-host interview / Two-side debate）。
- [x] D2 `PodcastDialog` 底部 CTA；播客卡 tag 追加「案例实录」，来源行加「案例制作时来源：」前缀。
- [x] D3 测试：首行四模式断言保持绿；模板不含 MP3 路径、旧 URL、旧数字与旧对白；共享 audio、互斥播放、文稿、下载行为不变。提交。

### 5. 收尾

- [x] 文案与可访问性：新增文案进入 `zh-CN.ts`、`en-US.ts`、`types.ts`，英文界面无新增中文 UI（提示词保持中文）；保留筛选 `aria-pressed`、Dialog Escape 与焦点、音频控制名称、附件删除名称及暗色 token；新增弹窗底部操作在窄屏自然换行。（英文四 Agent 的 guide 文本与弹窗说明行、CTA 均无 CJK，由新 e2e 断言。）
- [x] 新增 `tests/e2e/agent-showcase-style-workflow.spec.ts`：四个 Agent 参数化一条主链路——打开预览 → 点 CTA → 预览关闭、输入框等于模板、附件数不变、thread / run POST 为 0。
- [x] 视口检查（浏览器实测 1440 / 1280 / 1024 / 390，截图留证）：视频 xl 一行三卡、PPT 纵向宽卡、播客 2×2、课程海报默认瀑布流与筛选 grid 均不变。（同一脚本在改动前后各采集一遍：16 个格子的网格 class、卡片横向位置与宽度、标签高度全部不变，视频卡音色行截断逐像素相同；唯一差异是 390 下 PPT 卡按钮行回到单行、卡片矮 44px。据此把视频卡 CTA 定为 5 字、PPT 卡 CTA 定为 4 字，见 B1、C1 的执行修正。）
- [x] 在 `frontend/` 运行 `pnpm test`、`pnpm check`；在仓库根运行 `python3 scripts/check_docs_convention.py`。（另跑全量 e2e 与 `perf:check`，结果见 changelog 验收记录。）
- [x] **最小 live 冒烟**（边界见「评审决策记录」；本机 `make dev`，不部署）：course-video 1 条（固定讲稿模板，附一份自有短讲稿，停在确认门①）；ppt-master 2 条（Default 停在 Stage 1 确认；Edit Native 附一份有权编辑的 `.pptx`，停在页面计划确认）；podcast-studio 1 条（interview_two，停在稿件确认）；creative-studio 1 条（海报类模板，放行到出 1 张图）。每条记录 thread id、停在哪个确认门、产物是否符合模板；不符合则改模板并重跑门禁。未跑或未通过的，在 changelog 写明。（首次未跑：当时找不到模型凭据，gateway 启动即因 `QIHU_API_KEY` 缺失退出。2026-11-24 合入后用户指明凭据位置，已按原边界补跑：5 条 run、1 张图、0 次 TTS / 渲染。#1 固定讲稿停在确认门①，#2 Default 停在 Stage 1，#4 双人访谈停在稿件确认，#5 课程海报恰好出 1 张 9:16 图且走 trainerai-imagine；#3 Edit Native 因模型供应商读超时未被 agent 处理，仍未验证。实测还发现内容齐全时课程海报 agent 直接出图、不等确认，弹窗说明已改为如实表述。明细见 changelog「最小 live 冒烟补跑」。）
- [x] 把 20 篇模板（`showcase.ts` 的 prompt diff）写进 PR 描述，供用户在 PR 上过目；执行过程不为此停下。
- [x] 新增 `docs/changelog/changelog-<执行日>-agent-showcase-style-workflow.md`，含 TL;DR 与「验收记录」（冒烟结果写入）；在本计划头部回填执行范围与链接。
- [x] 按项目约定走独立分支、PR 与 OCR，不直接推送 main；不提交任何 `temp/` 下的 Demo 文件。AI 评审最多处理 3 轮：误报或低优先级的发现用 PR 评论记录处置（`gh pr comment --body-file`），3 轮后仍有真实的 Critical/Important 才停下问用户。（处置记录见 PR 评论。）

## 八、行为验收清单

- [x] **布局不变**【实现级】：文字动效 xl 一行三卡；PPT 三张纵向宽卡且内部左右结构不变；播客桌面 2×2；课程海报默认 10 件瀑布流，筛选才使用普通 grid。
- [x] **模板正确**【实现级】：四类案例均不含原主题和旧内容；视频不继承样片秒数，PPT 不继承 7 页，播客不继承旧来源 / 观点，课程海报不继承示例文案；PPT 与视频没有 `[模式：…]` 首行；marker 用例全绿。
- [x] **只填不发**【行为级】：卡片和弹窗 CTA 结果一致，只填模板，不自动发送、不创建 thread / run；附件数不变。
- [x] **草稿保护**【行为级】：非空草稿的取消 / 追加 / 替换行为不变，取消不改文本。
- [x] **原能力保留**【行为级】：视频播放与暂停、PPT 翻页与下载、播客共享 audio / 文稿 / 来源、视觉原图 / SVG 下载及暗色 / 焦点行为均正常。
- [ ] **真实 agent**【行为级】：冒烟 5 条按预期停在确认门（课程海报放行到 1 张图），没有“沿用案例内容”的情况。（补跑后 4 条已验证：固定讲稿、Default、双人访谈各停在确认门，课程海报出 1 张图且无案例内容；Edit Native 一条因模型供应商读超时未验证，补跑需再 1 条 run，待用户同意。）

## 九、审查重点与回滚

- **布局优先**：任何 `showcaseCardClass`、DeckCard 内部 grid、播客 grid 或断点变化都必须视为越界，而不是“视觉优化”。
- **模板内容**：首行语法、必留标记、路由（海报走 trainerai-imagine）、画幅写死、不复刻案例内容。
- **共享回调兼容**：`onUsePrompt` 签名不变，co-writer 与非案例创作路径不受影响。
- **品牌与事实污染**：360 成品、旧调研来源、旧数字、旧台词、原讲稿文件名不进入模板。
- **回滚**：仅回滚该 frontend 分支；不涉及数据库、配置版本、素材迁移或部署变更。

## 附录 A：模板骨架（20 篇由此确定）

`{…}` 为执行时填入的变量；其余文字照抄。`recipeNote` 按第四节表格的“保留 / 用户输入”两列写。

### A.1 课程海报（10 篇）

```text
[模式: 视觉创作]
请按参考风格制作一张{类型}。

【我的内容】
{字段组}

【风格与版式】
{风格与版式}

仅参考视觉风格，不复制案例标题、知识点和文案。如果我的内容仍为空，请先向我索取，不要使用案例内容补齐。
```

首行：结构图两篇为 `[模式: 结构图]`。该首行是 agent 自己的输出标记、没有解析器，保留只为与现有提示词和 marker 用例一致。

字段组（四选一）：

| 组 | 字段 |
|---|---|
| 海报 | 课程主题：【请填写】 / 主标题：【请填写】 / 副标题与关键信息：【请填写】 |
| 知识 | 主题：【请填写】 / 核心知识点：【请填写】 / 必须准确呈现的文字：【请填写】 |
| 漫画 | 主题：【请填写】 / 角色与四格情节：【请填写】 / 必须保留的对白：【请填写】 |
| 结构图 | 主题：【请填写】 / 节点与层级：【请填写】 / 连接关系：【请填写】 |

每个字段占一行。类型、字段组与风格：

| 案例 | 类型 | 字段组 | 风格与版式 |
|---|---|---|---|
| `visual-poster-agent` | 课程海报 | 海报 | 深空科技风，深蓝黑背景、青蓝光线、中文大标题与细线框，9:16 竖版；用 trainerai-imagine 生图，不走 course-poster 固定模板；主视觉根据课程主题重新设计 |
| `visual-poster-llm` | 课程海报 | 海报 | 深蓝底、瑞士网格、荧光绿与朱红几何色块，9:16 竖版；用 trainerai-imagine 生图，不走 course-poster 固定模板；主视觉和文案根据课程内容重新设计 |
| `visual-card-rag` | 知识图解 | 知识 | 深靛紫底、半透明 3D 玻璃、青绿高光、三幕横向构图与 16:9 画幅；信息结构按用户内容重新组织 |
| `visual-card-prompt` | 知识图解 | 知识 | 深色终端手稿、正文与四段批注、蓝 / 珊瑚 / 绿 / 紫标注，16:9 画幅；手稿内容全部由用户替换 |
| `visual-comic-agent` | 知识漫画 | 漫画 | 16:9 四格日系漫画、清晰阅读顺序和角色一致性；角色、情节与对白均根据用户主题重新创作 |
| `visual-comic-gradient` | 知识漫画 | 漫画 | 16:9 四格靛蓝水墨、克制留白和连续叙事；角色与比喻根据用户主题重新设计 |
| `visual-infographic-training` | 知识图解 | 知识 | 冷白数据管线、横向节点、连续箭头和输入 / 输出标注，16:9 画幅；阶段数量与内容由用户决定 |
| `visual-infographic-roadmap` | 知识图解 | 知识 | 深绿时间线、S 形路径、阶段节点与停止条件，16:9 画幅；阶段与说明由用户重新填写 |
| `visual-diagram-stack` | 结构图 | 结构图 | 石墨黑底、分层组织、蓝 / 青 / 琥珀 / 黄绿配色，输出可编辑 SVG；层级、节点与关系由用户内容决定 |
| `visual-diagram-rag` | 结构图 | 结构图 | 靛蓝 / 珊瑚 / 青绿分组和清晰连接关系，底色沿用技能默认暗色主题，输出可编辑 SVG；节点和拓扑由用户内容决定 |

### A.2 文字动效（3 篇）

`{视觉}`、`{音色}` 取各案例现有 `styleLabel.zh` 与 `voice.zh`。

固定讲稿（`video-hallucination`、`video-feynman`）：

```text
请严格依据我上传的完整口播稿制作 16:9 视频课，讲稿按原文使用，不改写、不扩写，也不要联网补充。

【必需材料】
完整口播稿：请上传 docx、pdf、md 或 txt 文件；未提供完整讲稿前请勿开始制作。

【视觉与配音】
视觉：{视觉}（可修改）
默认音色：{音色}（可修改）
字幕：保留
背景音乐：不添加
画面：不生成真人角色或剧情镜头，不虚构真实素材。

请先提供完整制作方案，待我确认后再开始执行。整个制作过程请使用 /skill auto-video-flow。
```

主题调研（`video-agent-research`）：

```text
请围绕我的主题做一次深度联网调研，先展示调研来源、完整口播稿和分镜，待我确认后再制作 16:9 视频课。

【需填写】
主题：【请填写】
受众：【请填写】
目标时长：【请填写】

【可选】
来源偏好：【可选填写】

【视觉与配音】
视觉：{视觉}（可修改）
默认音色：{音色}（可修改）
字幕：保留
背景音乐：不添加
要求：事实必须能对应来源，不补写来源中没有的数据，不虚构真实素材，不生成真人角色或剧情镜头。

整个制作过程请使用 /skill auto-video-flow。
```

### A.3 PPT 课件（3 篇）

Quick（`ppt-quick`）：

```text
走 Quick 流程，跳过 Stage 1/2 策略与方案确认，直接出稿并完成质量检查，交付原生可编辑 PPTX。

【我的内容】
主题：【请填写】
资料：【请填写要点，或注明你手动添加的资料文件】
受众：【请填写，如零基础学员】
目标：【请填写学完能做到什么】
页数：【请填写建议页数】

视觉：参考轻量科技、暖纸与钴蓝配色，按我的内容重新设计。

要求：逐页可编辑，不使用整页截图代替原生页面；不要沿用案例主题或文案。
```

Default（`ppt-standard`）：

```text
按 Default 标准流程制作一份原生可编辑 PPTX。先与我确认课程结构和设计方案，再逐页生成。

【我的内容】
主题：【请填写】
资料：【请填写要点，或注明你手动添加的资料文件】
受众：【请填写，如零基础学员】
目标：【请填写学完能做到什么】
页数：【请填写建议页数】

视觉：参考深色杂志风、琥珀与青配色，按我的内容重新设计。

要求：图表与流程页使用原生可编辑对象，不使用整页截图代替；不要沿用案例主题或文案。
```

Edit Native（`ppt-template`）：

```text
请走 Edit Native 原生编辑，改造我上传的现有 PPT：先与我确认逐页编辑计划（输出页 → 源页 → 操作），再执行原生编辑。

【必需材料】
源 PPTX：请上传你有权编辑的 .pptx
新内容资料：【请填写要点，或注明你手动添加的资料文件】

【我的内容】
新主题：【请填写】
受众：【请填写】
目标：【请填写】
页数：按新内容决定，不限定页数
保留范围：【请填写需保留的封面、页眉页脚、版式或对象】
改写范围：【请填写需改写或新增的页面与内容】

要求：仅处理我有权编辑的文件；按“保留范围”保留原设计；未编辑对象不重建，未修改的对象保持原样；新增或改写页面沿用源 PPT 的视觉语言，交付可继续改字、换图、调整版式的 PPTX，不用整页截图替代。
```

### A.4 语音播客（4 篇，沿用 Demo 文案）

```text
[模式: read_original]
请将我提供的文档或安全公开链接中的原文朗读为播客，不增写、不改写、不总结。

【原文来源】
文档或安全公开链接：【请上传文档或粘贴安全公开链接】

【固定音色】
云舟2.0（男声）

仅朗读我有权使用的原文；如未提供文档或链接，请先向我索取。输出 MP3。
```

```text
[模式: news_single]
请围绕我提供的主题或资料制作一期单人播报，先展示完整口播稿、来源和固定音色，确认后再合成 MP3。

【我的内容】
主题：【请填写】
资料或安全公开链接：【请填写，可留空】

【固定音色】
云舟2.0（男声）

请根据我的内容重新撰写，仅使用我提供或经核验的公开资料，不借用案例文稿。
```

```text
[模式: interview_two]
请基于我提供的主题或资料制作一期双人访谈，围绕主题展开多轮问答，先确认完整口播稿、来源和固定音色，再合成 MP3。

【我的内容】
主题：【请填写】
资料或安全公开链接：【请填写，可留空】

【固定音色】
云舟2.0（男声，主持）
Vivi2.0（女声，嘉宾）

请根据我的内容重新撰写，不借用案例文稿。
```

```text
[模式: debate_two]
请围绕我提供的辩题或资料制作一期双人辩论，双方分别陈述立场、回应和总结，先确认完整口播稿、来源和固定音色，再合成 MP3。

【我的内容】
辩题：【请填写】
双方立场：【请填写，可留空】
资料或安全公开链接：【请填写，可留空】

【固定音色】
云舟2.0（男声，一方）
Vivi2.0（女声，另一方）

请根据我的内容重新撰写，不借用案例文稿。
```
