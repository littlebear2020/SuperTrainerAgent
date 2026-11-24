# Agent 新会话案例改为“用我的内容复用此创作方式”的通用模板

> **状态**：离线验收完成（单测、全量 e2e、视口前后对比、`pnpm check`、`perf:check`、文档门禁全过）；最小 live 冒烟已于同日补跑——5 条 run 中 4 条按预期停在确认门 / 出 1 张图，Edit Native 一条因模型供应商读超时未验证，见「最小 live 冒烟补跑」。共 5 条 run、1 张图、0 次 TTS / 渲染；未部署。
> **TL;DR**：课程海报、文字动效、PPT 课件、语音播客的 20 篇案例提示词从“重做原案例”改成“用我的内容复用此创作方式”的通用模板；卡片与预览弹窗共用一个只填不发的 CTA，弹窗底部多一行“保留 / 填写”说明；课程海报把知识卡片与信息图合并为「知识图解」。关键决策 = 执行中两处按视口实测修正了计划指定的 CTA 文案长度（视频卡 5 字、PPT 卡 4 字），否则 1280 下会出现新的截断或换行；补跑的 live 冒烟 4/5 通过，并据此把课程海报弹窗说明里“先确认方案再出图”改成如实的“内容齐全时直接出图”。影响面 = 仅 `frontend/` 的 showcase 数据、卡片、弹窗、i18n 与测试；不动 backend、技能、部署。

计划：[`docs/plans/2026-11-24-agent-showcase-style-workflow-plan.md`](../plans/2026-11-24-agent-showcase-style-workflow-plan.md)

## 改动

- 数据层 `core/agents/showcase.ts`：`ShowcaseCaseBase` 新增可选 `actionLabel`（CTA 文案）与 `recipeNote`（弹窗底部“保留 / 填写”说明）；20 篇 `prompt` 按计划附录 A 改写为通用模板（视觉 10 篇、视频 3 篇、PPT 3 篇、播客 4 篇），不含本案例标题、样片秒数 / 页数、来源名与链接、案例媒体路径。
- 卡片：`PromptButton` 用 `actionLabel` 作 CTA 与可访问名（`${actionLabel}: ${标题}`）；结构图徽标改「SVG 可编辑」；PPT 卡风格行加「视觉：」前缀；播客卡 tag 追加「案例实录」、来源行加「案例制作时来源：」前缀。
- 弹窗：四个预览弹窗底部追加一行说明与同一 CTA（testid 统一为 `showcase-use-style`）；点击先关预览再交给输入框，关闭时不把焦点还给卡片；视频弹窗任何关闭路径都先暂停视频。
- 输入框：填入后选中刚插入片段里的第一个填写占位符（`【请…】` / `【可选填写…】`），没有占位符则光标置末；追加时只在新片段里找，不动草稿里已有的占位符。
- 课程海报：筛选把 `card` 与 `infographic` 合并为「知识图解」（键 `card`，数据层 `category` 不动），筛选按钮 6 → 5，4 个案例的 tag 改「知识图解 · <风格>」，`note` 改为 4 类口径。
- i18n：`zh-CN.ts`、`en-US.ts`、`types.ts` 新增 `svgEditable`、`stylePrefix`、`caseRecording`、`caseSourcePrefix`，删除 `filterInfographic`，`filterCard` 改为「知识图解 / Knowledge graphics」。
- 测试：DOM 钉住四类网格容器 class 作为布局基线；新增模板不含案例事实、CTA 文案预算、弹窗 CTA 只填不发且不动附件、占位符选中等断言；新增 `tests/e2e/agent-showcase-style-workflow.spec.ts`（四 Agent 参数化主链路、两条 CTA 经草稿确认等价、390 弹窗、1440 布局基线、英文文案）；筛选合并与可访问名前缀带来的既有断言按计划更新。

## 执行决策（对计划的修正）

计划「评审决策记录」与附录 A 是范围与实现的依据，以下四处是执行中按实测做的修正，均已回写到计划勾选项：

1. **占位符的认定**：模板里 `【…】` 既用于填写占位符也用于小节标题（`【我的内容】`、`【必需材料】`等）。计划写“选中第一个 `【…】` 占位符”，按字面实现会把小节标题选中、用户一打字就覆盖标题。按计划第三节对占位符的定义，只选中 `【请…】` / `【可选填写…】` 形态；小节标题跳过。
2. **视频卡 CTA 改为 5 字**（按风格创作 / 调研后创作）：改动前实测 1440 下卡片宽 260px、元信息列 108px，「云舟2.0（男声）」恰需 108px。计划原定的 6 字「按此风格创作」会让按钮宽约 12px，音色行在 1440 即开始截断，违反 B3“截断不多于现状”。英文不变（`Use this style` / `Research first`）。
3. **PPT 卡 CTA 改为 4 字**（按此出稿 / 按此规划 / 按此改造；英文 `Quick draft` / `Plan and build` / `Edit my PPT`）：1280 下 PPT 卡宽 655px，按钮行在 CTA 为 106px 时单行；计划原定的 8–10 字文案（142 / 169px）会让三张卡的按钮行换到第二行、每张高 44px，1024 下「按此方式改造我的 PPT」同样换行。硬约束是“不新增行”，CTA 文案本身允许改，所以缩到不宽于原按钮；完整措辞在弹窗说明行里。
4. **固定讲稿模板没有文字占位符**（附录 A.2 原文如此，用户输入是上传的讲稿），这类模板走“光标置末”分支；DOM 测试据此条件化断言。

单元测试把视频卡与 PPT 卡的 CTA 预算统一钉为中文 ≤5 字、英文 ≤15 字符（不宽于原「用此提示词 / Use this prompt」）。

## 视口与布局核对（浏览器实测 1440 / 1280 / 1024 / 390）

同一采集脚本在改动前（`ab92a527`）与改动后各跑一遍，比较四个 Agent × 四个视口共 16 个格子的网格容器 class、每张卡的横向位置与宽度、标签高度、视频卡元信息列截断量：

- 16 个格子的网格容器 class、卡片横向位置与宽度、标签高度全部不变；视频 xl 一行三卡、PPT 纵向堆叠、播客 2×2、课程海报默认瀑布流与筛选 grid 均不变。
- 视频卡元信息列在 1440 与 1280 的 `clientWidth / scrollWidth` 逐项相同（1440：`108/108, 108/114, 93/93…`；1280：`56/79, 56/114…`），音色行截断不多于现状。
- 唯一的高度差异：390 下 PPT 卡从 521px 变为 477px——按钮变短后原本换行的按钮行回到单行。属于良性变化，不是新增行。
- 英文 locale 在 1280 下补测：PPT、视频、播客的 CTA 均不宽于原 `Use this prompt`，按钮行换行状态与改动前一致。
- 截图与指标落在主 clone 的 `temp/2026-11-24-agent-showcase-evidence/{before,after}/`（已 gitignore，不入库），对比输出见同目录 `layout-compare.log`。

## 最小 live 冒烟补跑（2026-11-24，PR #55 合入后）

首次交付时这一项未跑：主 clone 没有 `.env`，shell 里也没有 `config.yaml` 引用的 `$QIHU_API_KEY` 等变量，gateway 启动即报 `Environment variable QIHU_API_KEY not found`。用户随后指明凭据在仓库外的备份快照 `~/Documents/备份快照/trainer ai/.env`（29 个变量，覆盖 `config.yaml` 引用的全部键）。补跑环境与计划的边界一致：

- 后端取合入后的 main（隔离 worktree，全新 `backend/.deer-flow/`，不碰本地开发库），`config.yaml` 用主 clone 的，凭据在同一启动命令里 `source` 进进程，不打印、不入库；前端 `pnpm run dev`（:3000）+ nginx（:2026），与 `make dev` 的服务组合相同。关闭 LangFuse 上报，worker 探测地址指向本机空端口。
- 驱动走真实界面：点卡片上的 CTA → 覆盖填写占位符 → 经 composer 附文件 → 回车发送；每 4 秒轮询线程历史，出现真正执行 TTS / 渲染的命令或超过 1 张图就取消 run。输入为自有短讲稿与脚本生成的 3 页 `.pptx`。
- 用量：5 条 run、1 张图、0 次 TTS / 渲染，每条只跑 1 次。

| # | Agent · 模板 | thread | 结果 |
|---|---|---|---|
| 1 | course-video · 固定讲稿 | `7d3eb849-fa4d-48d5-864f-554709aee310` | **通过。** 读了上传讲稿与技能文档、做了最小侦察（字数统计），按模板给出方案（云舟2.0 男声、词级字幕、不加 BGM、16:9、深色科技网格与概率条），`ask_clarification`「请确认以上制作方案」→ 停在确认门①。 |
| 2 | ppt-master · Default | `4dae2099-ebd1-4ec3-8216-abb4c43c0c0f` | **通过。** 走默认生成流程，用填入的主题建项目并写需求简报，`ask_clarification`「【Stage 1 · 沟通契约 + 模板选择】…受众：刚入职的新员工…页数：你指定 8 页…自由设计…深色杂志风 / 琥珀与青」→ 停在 Stage 1，未生成 PPTX。 |
| 3 | ppt-master · Edit Native | `5ed3e6bf-54ca-4781-8888-75c4f2bdc086` | **未验证。** 该 run 的第一个模型请求就触发供应商读超时（gateway 日志：`LLM call failed after 1 attempt(s) (model=anthropic/claude-sonnet-5-5): ReadTimeout`），线程只有 3 条消息，模板没有被 agent 处理。已用掉这一条唯一的一次 run，未重发。 |
| 4 | podcast-studio · interview_two | `8e492c27-84de-4c76-977f-71c58ffb05d4` | **通过。** 围绕填入的主题联网调研（17 次 search / fetch，来源为 GitLab handbook、Atlassian、ADR、Diátaxis 等新来源），跑完素材 intake，写出 34 轮访谈稿，`ask_clarification`「请确认以上完整口播稿(34 轮)、来源…音色(男声主持=云舟2.0,女声嘉宾=Vivi2.0)。确认后…生成 MP3」→ 停在稿件确认，0 次 TTS。 |
| 5 | creative-studio · 课程海报 | `b3c63619-9027-4ac2-b878-972af3fa808a` | **通过。** 恰好 1 次生图（agent 自述「用 trainerai-imagine 生图，模型是 360 API 的 Seedream 5.0 Pro，没有用 course-poster 模板」），产物 `python-course-poster.png` 1152×2048（9:16），画面文字全部来自填入内容，主视觉为「Python 入门」原创，无案例内容。 |

**由冒烟发现并修正的一处不实文案**：计划 4.1 写“发送后 agent 会先确认方案再出图”，并据此在 10 条课程海报的 `recipeNote` 结尾写了「发送后 agent 先确认方案再出图。」。实测第 5 条在内容齐全时，agent 把「需求确认」作为自查步骤报告后直接进入生图，没有等待确认（SOUL 的流程是“关键节点用 `ask_clarification` 确认”，是否需要由 agent 判断）。这句会让用户以为出图前还有一次把关，已改为「内容齐全时 agent 会直接出图；缺内容会先向你索取。」，DOM 测试先红后绿。模板正文本身不需要改：它写的是“如果我的内容仍为空，请先向我索取”，与实测一致。

**仍未验证**：Edit Native 模板能否让真实 agent 停在逐页编辑计划确认。补跑只需 1 条 run（同一输入与驱动，见 `smoke-kit/`），超出已授权的“5 条、每条 1 次”边界，需用户点头。

结果明细（每条 run 的完整消息与工具调用）与成图留在主 clone `temp/2026-11-24-agent-showcase-evidence/smoke-results/`（不入库）。

## 范围外发现

- 全量 e2e 首轮 `chat.spec.ts` 的 token 汇总 prepend 用例失败 1 次（与本批无关的页面），单独重跑 3/3 通过，第二轮全量通过；已登记 `docs/findings-backlog.md`。

## 验收记录

- `git diff ab92a527 --stat` → 代码零漂移（仅主 clone 未提交的另一份计划文档）；前端可靠性计划 PR1 未合入。
- 20 篇模板渲染对照附录 A（`node` 直接加载 `showcase.ts` 打印）→ 首行、字段组、风格文案、音色逐字一致；输出留存于证据目录 `prompts-rendered.txt`。
- RED（切片 A）`pnpm exec vitest run` 三个 showcase 测试文件 → `12 failed | 44 passed`，失败原因均为功能缺失（`actionLabel` 未定义、`showcase-use-style` 不存在、筛选按钮 6 个）。
- GREEN `pnpm exec vitest run tests/unit/core/agents/showcase.test.ts tests/unit/components/workspace/agent-showcase.dom.test.tsx tests/unit/components/workspace/agent-new-chat-guide.dom.test.tsx` → `3 passed (3)` / `65 passed (65)`。
- `pnpm test` → `Test Files 284 passed (284)` / `Tests 1868 passed (1868)`（基线 1846，新增 22）。
- `pnpm check` → `check_exit=0`（eslint 0 errors、tsc、prettier 均过）。
- `pnpm exec playwright test`（全量，真实配置含构建）→ 首轮 `141 passed / 1 failed / 24 skipped`，失败项为 `chat.spec.ts` token prepend 用例；`--repeat-each 3` 单独重跑 → `3 passed`；第二轮全量 → `142 passed / 27 skipped`（skipped 为 `live/` 目录按设计跳过）。
- `pnpm exec playwright test tests/e2e/agent-new-chat-guide.spec.ts tests/e2e/agent-showcase-style-workflow.spec.ts`（CTA 文案定稿后）→ `55 passed`。
- `pnpm perf:check` → `exit=0`，五路由 JS/CSS 在预算内（`/workspace/chats` JS 3264306 B 等）。
- 视口前后对比脚本 → `LAYOUT BASELINE`：16 格网格 class、卡片 x/w、标签高度不变；视频元信息截断逐像素相同；差异仅 390 下 PPT 卡高度 521 → 477。
- live 冒烟（首次）→ `RuntimeError: Failed to load configuration during gateway startup: Environment variable QIHU_API_KEY not found`，5 条 run 均未执行。
- live 冒烟（补跑）→ 5 条 run：#1 `ask_clarification`「请确认以上制作方案」（0 次 TTS / 渲染）；#2 `ask_clarification`「【Stage 1 · 沟通契约 + 模板选择】…」；#3 `LLM call failed after 1 attempt(s) (model=anthropic/claude-sonnet-5-5): ReadTimeout`，未验证；#4 `ask_clarification`「请确认以上完整口播稿(34 轮)…」（0 次 TTS）；#5 生图命令 1 次、`present_files` 1 个文件、`sips` → `1152 × 2048`。
- 文案修正 RED → DOM `shows the recipe note above the visual dialog CTA`：`expected … to contain '内容齐全时 agent 会直接出图'`；GREEN → 三个 showcase 测试文件 `3 passed (3)` / `65 passed (65)`，`tsc --noEmit` 退出 0。
- `python3 scripts/check_docs_convention.py` → `OK 仓库治理约定全过`。
