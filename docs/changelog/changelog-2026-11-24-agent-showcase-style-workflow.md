# Agent 新会话案例改为“用我的内容复用此创作方式”的通用模板

> **状态**：离线验收完成（单测、全量 e2e、视口前后对比、`pnpm check`、`perf:check`、文档门禁全过）；最小 live 冒烟未跑——本机没有模型凭据，原因与补跑办法见「未执行项」。未部署、未产生付费调用。
> **TL;DR**：课程海报、文字动效、PPT 课件、语音播客的 20 篇案例提示词从“重做原案例”改成“用我的内容复用此创作方式”的通用模板；卡片与预览弹窗共用一个只填不发的 CTA，弹窗底部多一行“保留 / 填写”说明；课程海报把知识卡片与信息图合并为「知识图解」。关键决策 = 执行中两处按视口实测修正了计划指定的 CTA 文案长度（视频卡 5 字、PPT 卡 4 字），否则 1280 下会出现新的截断或换行。影响面 = 仅 `frontend/` 的 showcase 数据、卡片、弹窗、i18n 与测试；不动 backend、技能、部署。

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

## 未执行项

- **最小 live 冒烟（5 条 run）未跑。** 计划要求本机 `make dev` 跑 5 条真实 run；实测本机没有可用的模型凭据：gateway 启动即报 `Environment variable QIHU_API_KEY not found for config value $QIHU_API_KEY`，主 clone 没有 `.env`，shell 环境与 shell 配置里也没有这些变量。没有去系统里翻找密钥，也没有改用测试服（计划边界是本机、不部署）。因此“模板能否让真实 agent 停在确认门”这一条**行为级验收未验证**；模板与运行时的一致性目前只有代码核对（技能 SOUL / SKILL.md、上传白名单、marker 用例）支撑。
- 补跑办法（二选一，均需用户提供条件）：① 在本机提供模型凭据（如按 `.env.example` 建根目录 `.env`），用计划的 5 条输入按原边界跑；② 把本分支部署到测试服后，在测试服上按原边界跑。冒烟输入（自有短讲稿、脚本生成的 3 页 `.pptx`）与驱动脚本已留存于主 clone `temp/2026-11-24-agent-showcase-evidence/smoke-kit/`（不入库），驱动脚本会轮询线程历史，一旦出现 TTS / 渲染类命令立即取消 run。
- 未做部署、未产生任何付费调用。

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
- live 冒烟 → `RuntimeError: Failed to load configuration during gateway startup: Environment variable QIHU_API_KEY not found`，5 条 run 均未执行。
- `python3 scripts/check_docs_convention.py` → `OK 仓库治理约定全过`。
