# Project Conventions

## 项目速览

- **产品**：SuperTrainerAgent——AI 课程内容智能生产平台（课程视频、PPT 课件、声音分身、研究报告、视觉设计等），基于 TrainerAI v2 与 DeerFlow 2.0 多 agent 框架。能力实况以代码为准：预置专家 agent 见 `backend/seed/agents/`、技能见 `skills/`；`README.md` 可作概览，名册类以代码为准（见「文档治理」README 防腐约定）。
- **拓扑**：浏览器 → nginx 反代（`/api/*` → gateway，`/` → 页面）→ `frontend/` Next.js :3000 + `backend/` FastAPI gateway :8001 + 独立 worker 池（视频合成等重 IO）。
- **目录地图**：`backend/`（Python 3.12+；`app/` = 应用层，`packages/harness/deerflow/` = 可发布 agent 框架）、`frontend/`（Next.js + React + TS + Tailwind）、`docker/`（镜像与 compose）、`skills/`（agent 技能）、`video-templates/`（Remotion 模板）、`docs/`（一切文档，治理规则见「文档治理」）。
- **分层约定**：本文件只放全局约定与红线；碰 `backend/`、`frontend/` 时先读各自目录的 `AGENTS.md`。

## 文档治理

- **plan 命名**：`YYYY-MM-DD-<主题>-plan.md`——日期 = 定稿日，主题用英文 kebab-case；分批/拆分时主题后接批次后缀（如 `...-batch-b-plan.md`、`...-phase0-2-plan.md`）。位置：现行计划放 `docs/plans/`，未执行拆分件放 `docs/plans/pending/`，已关闭历史档放 `docs/plans/archive/`（2026-09-30 已一次性并入原根目录散放计划与 superpowers/plans 两代旧档）。
- **changelog 命名**：`changelog-YYYY-MM-DD-<主题>.md`，一律放 `docs/changelog/`——日期 = 执行日，主题与对应 plan 对齐；一批次一份，批次后缀与 plan 一致（如 `changelog-...-batch-a.md` 对应 `...-batch-b-plan.md` 的批次序列）。小修小改（单文件、测试绿、无部署注意项与移交遗留）可不写 changelog，以 commit message 为准。
- **未执行计划物理隔离（2026-09-30 约定）**：计划定稿时，不当批执行的部分（如 Batch B / Phase 2）即拆分为 `docs/plans/pending/` 下的独立计划文档（命名同 plan 规则，头部标注拆分来源与原计划进度）；已执行计划在头部维护一行执行状态（已执行范围 + changelog 链接 + 拆出部分去向；状态头缺失由 check 脚本拦截）。pending 计划由用户定期查看、自行决定何时执行——执行时将文件移回 `docs/plans/` 并在头部标注执行日期与 changelog 链接。pending 计划头部须记录「基准 commit」（定稿时的 HEAD 短 hash），执行前先 `git diff <基准> --stat` 评估漂移面；定稿超 30 天的 pending 由 check 脚本打印 warning（非阻断）。
- **TL;DR 约定（2026-10-08）**：plan 与 changelog 在状态头之后必须有 ≤5 行 TL;DR（做了什么/关键决策/影响面）；审计/评审类大文档（>40K）同样要求。存量不回填，只约束新增（check 脚本按文件名日期 ≥ 2026-10-08 阻断）。写法示例：
  `> **TL;DR**：本批做了 X（一句话）；关键决策 = Y（为什么）；影响面 = Z（哪些目录/是否动部署）。`
- **changelog 验收记录节（2026-10-12）**：changelog 必须含「验收记录」节，每行 = `命令 → 实际输出关键行`（关键行即可，不全量粘贴）——「测试绿了」与「需求满足了」之间要有可审计链路（2026-10-12 FDE 事故教训）。check 脚本对新增 changelog（文件名日期 ≥ 2026-10-12）缺该节先 warning，约 2 周后升阻断。防假证据边界：机检只管存在与形态，真实性由 CI run 引用、AI 评审抽查、用户抽检兜底。
- **plan 勾选制（2026-10-12）**：plan 的改动清单每项用 markdown checkbox（`- [ ]`），执行即勾——勾选是明细、状态头是摘要，冲突时以勾选状态回推状态头；跨会话恢复进度的第一动作 = 读勾选状态，不重读全文推断。
- **并行派发 plan 路径三栏（2026-10-13 MA-P0）**：用于并行派发的 plan 必须含「允许路径（allow）/ 禁改路径（ban）/ 共享基座触达（否/需规划侧独占）」三栏；规划侧派发前产出两份 plan 的路径重叠表，非空 → 改 plan 或改串行，**禁止派发**。单任务 plan 自愿采用三栏。并行全流程见「多 Agent 并行研发」节。
- **findings backlog（2026-10-12）**：任务范围外发现（不顺手改的问题）登记进 `docs/findings-backlog.md`（格式：`- [ ] YYYY-MM-DD 一句话 + 指针`），不再只在回复/changelog 里说一次即蒸发；plan 定稿前例行查一遍 backlog，相关项并入或显式推迟。
- **grill 前置（2026-10-12）**：功能类 plan（新功能/新产品面）定稿前必须用 grilling 技能过一轮决策树拷问（四入口共治，未装的入口先安装），产出的 frontier 问题与推荐答案落进 plan 的「评审决策记录」节。豁免：事故修复、明确小改、纯文档/配置批。
- **docs/ 按月归档（2026-09-30 约定）**：`docs/` 根目录只放常青文档（部署/运维指南等无日期文档）与当月 dated 产物；**`docs/` 根目录下不得新建子目录**（白名单 = `archive/`、`changelog/`、`plans/` + 当月 dated 目录，2026-10-17 补充明文——临时素材/资产请放 `temp/`，`demo-assets` 类自建目录会被门禁拦截）；每月初把上月 dated 文件（报告/调研/评审/设计稿/证据等）移入 `docs/archive/YYYY-MM/`，归档后跑 `scripts/check_docs_convention.py` 确认违例归零。plan 与 changelog 各有专目录（`plans/`、`changelog/`），不进月度归档。首批已于 2026-09-30 归档 2026-04~08（含 design/、notes/、superpowers/、pr-evidence/ 四个功能目录解散并入）。`docs/plans/archive/` 内 2026-08 前旧约定命名（133 件）存量豁免、不做批量改名（批量改名断引用，治理成本大于收益）；`check_docs_convention.py` 白名单同步该口径（2026-10-05 E4 入约）；新增归档计划一律使用现行命名约定（豁免只覆盖 2026-08 前存量，不产生新豁免）。
- **机械校验**：以上约定由 `scripts/check_docs_convention.py` 兜底（含测试落位检查——backend pytest 套件只放 `backend/tests/`（豁免：`backend/scripts/` live 探针、vendored vimax；`docker/production-worker/` 另有独立套件不在此门），frontend 按 runner 落位：vitest 收 `tests/unit`/`src` 的 `.test.*`，playwright 收 `tests/e2e`，放错位置静默不跑、CI 假绿；含 backend/docs 形态检查——只放常青笔记与 API 参考，日期前缀一次性产物判违例，白名单见脚本）——新建/移动文档后跑一遍，违例即改，不靠自觉；且已接入 CI（`.github/workflows/quick-checks.yml`，全 PR 必过）**并挂入 pre-commit（docs/ 文件变更时本地 commit 即拦截，2026-10-17 起——原先只有 make check/CI 事后反馈，出过 agent 未跑门禁顺手新建违例目录的事故）**：归档类与命名类违例同门阻断，**每月第一个碰 docs 的 PR 需顺带完成上月归档**（有意设计，非 CI 抽风；CI 跑 UTC，月初边界有数小时判定窗口）。另含约定文件形态检查：约定一律写 `AGENTS.md`；各层 `CLAUDE.md`（含仓库根）必须存在且只能是 `@./AGENTS.md` 指针，防双轨漂移；另含 plan/changelog 新增件 TL;DR 检查（阻断，只约束文件名日期 ≥ 2026-10-08 的新增件）、pending 超 30 天提醒（非阻断 warning）、findings-backlog 行格式检查（阻断）、changelog 验收记录节检查（≥ 2026-10-12 新增件，先非阻断 warning、约 2 周后升阻断）。月度归档可一条命令执行：`python3 scripts/archive_docs.py`（默认 dry-run，`--apply` 才移动；归档后自验 + 常青复核提醒）。
- **README 防腐约定（2026-10-05）**：README 采用指针式写法——Agent/技能/模型/通道等易变名册的单一事实源是代码（`backend/seed/agents/`、`skills/`、`config.yaml`、`extensions_config.json`），README 只描述机制与概览。改这些名册时无需同步 README；也不要把易变清单、批次注记写回 README。

## 完成定义（交付前自查）

- CI 静态门禁的本地对应物 = 根 `make check`（backend ruff + frontend eslint/tsc/prettier/knip/i18n + docs convention，2026-10-08 CI 优化批 A1）。
- backend 改动：`cd backend && uv run pytest tests/ -q` 全绿（至少定向测试 + 相关回归）；pre-commit 的 ruff 必须过。
- backend 测试分层（markers 权威声明见 `backend/pyproject.toml`，此处为速览）：`slow`（真实等待 >1s）/ `e2e`（跨多层真实管线）/ `integration`（跨进程边界，预留）/ `live`（打真实付费 API，需 `RUN_LIVE=1` 显式 opt-in，默认不跑）。
- 测试落位（放错位置静默不跑、CI 假绿）：backend pytest 只放 `backend/tests/`（豁免：`backend/scripts/` live 探针、vendored vimax、`docker/production-worker/` 独立套件）；frontend vitest 收 `tests/unit`/`src` 的 `.test.*`、playwright 收 `tests/e2e`。
- 测试口径：迭代中跑定向 + 相关回归，提交前全量 `uv run pytest tests/ -q` 绿为门禁。
- **plan 验证清单分层（2026-10-12）**：验证清单分两层——**实现级**（签名/常量/路由断言，进 pytest/vitest 拦回归）+ **行为级**（用户可感知行为的端到端断言）。**仅行为变更批次**必须含 ≥1 条行为级断言（纯重构/文档/配置批豁免，豁免须在 plan 写明）；行为级断言优先廉价探针（ffprobe 抽查、API 级断言），重型渲染类允许以「生效验证（部署侧）」条目延后（先例：`docs/plans/2026-10-12-auto-video-flow-voice-delivery-hardening-plan.md` §4）。
- frontend 改动：`cd frontend && pnpm check` 过。
- 新建/移动文档：`python3 scripts/check_docs_convention.py` 过，且按「文档治理」约定落位。
- 批次工作收尾：按约定写 changelog（小修小改除外）；已执行计划在头部维护执行状态。
- 发现任务范围外的问题：在回复里说明，不顺手改。

## Git 约定

- commit message 沿用现有历史风格：`type(scope): 中文摘要`——type 常用 `feat/fix/docs/test/chore/refactor`，scope 取模块名（如 `uploads`、`skills/sandbox`），可省；一笔 commit 一个主题，摘要写明"做了什么、为什么"（参照 `git log --oneline`）。
- **PR 评审流（2026-10-12 入约，agent 自动执行，用户无需指定）**：AI Code Review（ai-review.yml）仅 PR 触发——直推 main 永远吃不到评审层。判定规则：
  - **直推 main**：小修（单主题、≤5 文件、非跨 backend/frontend 层）与纯 docs 批次（评审对这类 diff 无增益，docs 路径本就 paths-ignore）。
  - **分支 + PR**：成批工作——plan 驱动批次、>5 文件、跨层改动、或动核心链路（provider 包裹链/中间件链/config schema/沙箱）。
  - agent 侧全自动流程：建分支（`<主题>-kebab-case`）→ 常规提交粒度逐笔 commit → push → `gh pr create`（标题 = 批次摘要，正文带 plan/changelog 链接；gh 调用须设 `GH_REPO` 防解析到 upstream）→ 等 AI 评审 + PR 侧 CI（后台完成通知驱动，不轮询）→ 读评审结论（`gh pr view --comments` / reviews API）：无 Critical/Important 发现且 CI 绿 → `gh pr merge --rebase`（保逐笔主题提交、保线性历史）自动合并并回报用户；有发现 → 修复或带回拍板，修复后重推分支复跑。
  - PR 流程只叠加评审层，**不豁免本地门禁**（推送前全量测试绿的完成定义不变）；用户明确要求直推时以用户为准。
- **不可直推核心路径清单**（2026-10-13 MA-P0 C3 入约）：命中以下路径的改动**必须**走分支 + PR + OCR，「≤5 文件小修直推」豁免不适用——文件数少 ≠ 风险小（P0-3）。清单 SoT = `scripts/check_main_push_paths.py` 的 `CORE_PATH_PATTERNS`（镜像一致性由 check 脚本校验项 19 兜底；本机 pre-push 拦截 + push 后 CI 报警的形态见本节「合入门补偿控制」条）：
  - `backend/app/**/auth*`、`backend/app/**/*_auth*`（鉴权/凭据，含 admin_auth 类后缀形态）
  - `backend/app/**/middleware*`、`backend/packages/harness/deerflow/**/middleware*`（中间件链）
  - `backend/app/**/provider*`（provider 包裹链）
  - `backend/app/**/video_gen*`（视频生成管线）
  - `config*.yaml`（config schema，限根目录）
  - `docker/`、`skills/`、`backend/seed/agents/`（镜像 / 技能与加载 / 预置 agent）
  - `scripts/check_docs_convention.py`、`.github/workflows/`、`AGENTS.md`（门禁与约定本身）
- **合入门补偿控制**（2026-10-13 MA-P0 C10；免费私有仓挂不了 branch protection——2026-10-13 实测 API 403，Pro 国内支付不可行、无限期搁置）：①本机 pre-push 拦截——推 main 且命中核心路径清单即拒（`scripts/check_main_push_paths.py`，已在 pre-commit pre-push 注册；`--no-verify` 可绕过、需自律）；②push 后 CI 报警兜底——`.github/workflows/main-push-guard.yml` 报告模式 + docs-convention/secret-scan 本就跑在 push main 上，红/警即回滚；③纪律——合入检查单与并行流 review-before-push（见「多 Agent 并行研发」节）。

## 多 Agent 并行研发（MA-P0，2026-10-13 入约）

- **五步流**：规划侧集中定稿两份独立 plan（各含 allow/ban 三栏，路径重叠表为空才派发）→ 两执行 Agent 各自独立 worktree 按各自 plan 实现 → 规划侧集中审两路 diff（共享基座层加重）→ 审过后才授权 push/PR（走「Git 约定」PR 评审流）→ **串行合入** main（先合一路，另一路 rebase 新 main 后再合；禁止双 PR 同时抢 main）。
- **模式共存**：单任务与并行双 worktree 共存——日常小改/单 plan 仍走单执行者流；并行是可选加速，仅当两路路径可互斥时开启。
- **会话指引**：两 worktree = 两独立会话即可（同工具或跨工具均可，不强制混用）；禁止同一会话跨两个 worktree 写。
- **worktree 卫生**：同时 ≤2–3 个；`git worktree add ../TrainerAI2.0-<agent|role>-<topic> -b <topic>`；合入后 `git worktree remove` + 删远程分支；主 clone 恒停在 main，不在主 clone 上开执行会话。
- **review-before-push**：执行侧默认不 push、不开 PR，只提交本地 commits；收尾打包说明（分支名、diff 统计、测试命令与结果、是否碰 config_version）交规划侧；规划侧 `git diff main...<branch>`（共享基座 path 加重审）通过后才授权 push/PR。
- **派发前提**：plan 状态机干净（唯一状态头，check 脚本校验项 18 兜底）+ allow/ban 三栏齐 + 路径重叠表为空，缺一禁止派发；执行中途发现必碰 ban-list → 停工回规划侧重写互斥，勿私自扩权；姐妹 PR 合入后在本 worktree rebase main，解冲突仍守 ban-list，冲突落入禁改文件 → 停并交规划侧。
- **硬性提醒**：worktree 只隔离工作目录与 index，**不**消除共享文件上的 merge/rebase 冲突；产品侧 DeerFlow「多 agent」≠ 本仓研发多 Agent 并行（勿拿 `backend/seed/agents/` 能力叙事当 SDLC 并行 SOP）；OpenSpec 非前置、不强制。

## 长命令执行规范（2026-10-10 入约）

- **后台化阈值**：预计 >120s 的命令（全量测试、构建、渲染、镜像、部署、迁移）一律以 `run_in_background: true` 启动；>600s 无前台选项，必须后台；<120s 且结果立即需要的照常前台。
- **后台启动后按场景续接**：(a) 有可并行工作 → 继续做，完成通知到达时处理结果；(b) 无可并行工作（如交付前最后一道验证）→ 结束本轮、由完成通知唤醒，结束本轮时显式声明「等待任务 X（id）完成」；(c) 纯定时等待用后台**裸** `sleep N`（后不接任何命令）作定时器，配合完成通知做事件驱动等待；(d) 禁止用任何前台 sleep 序列填充等待。（2026-10-10 Trae 冒烟实测：完成通知唤醒可靠、延迟约 10s；该环境工具集无 TaskOutput 阻塞读取原语。）
- **禁止跨工具调用轮询**（`sleep N; tail/grep/cat …` 形态）。判据：删掉该命令，任务照常完成、只是更晚看到结果 → 是轮询。单条命令内的有界循环、功能性 sleep（限速退避、等远端排空）不受此限。前台行首 `sleep` 由 PreToolUse 钩子机械拦截（后台裸 sleep 定时器为唯一放行形态）——hook 脚本已入库 `scripts/hooks/guard_sleep_poll.py`（2026-10-13 MA-P0 C4，P0-1 消解），Claude Code 经 `.claude/settings.json`（本机文件，不入库）以 `$CLAUDE_PROJECT_DIR` 指仓内路径。**覆盖矩阵**：Claude Code 会话 = 机械拦截（fresh clone 后照此配置即生效）；Kimi Code / Cursor / 其他工具会话不读 `.claude/hooks/` = 同规则靠纪律自律。
- **就绪探测**用单条有界命令且必须带失败语义（探测超时须非零退出，不得静默放行）：
  `for i in $(seq 1 60); do curl -sf -o /dev/null <URL> && { echo READY; exit 0; }; sleep 2; done; echo "NOT READY" >&2; exit 1`
  探针族：HTTP=`curl -sf` / TCP=`nc -z` / 容器=`docker inspect` 健康态 / DB=`pg_isready` / 文件=`[ -f path ]`。
- **后台任务的输入冻结**：后台任务运行期间不得修改其输入（被测代码、构建上下文、数据库）；并行启动的任务不得共享可变状态（端口、数据库、构建目录）。
- **孤儿兜底**：收尾声明完成前确认无在途后台任务；会话恢复时先巡检孤儿任务。

## 边界与禁区（勿手改，各附原因）

- `.env`、密钥、证书等机密文件——勿读取、勿复制、勿外发（含贴入回复/日志/测试夹具）；需要配置形态时参考 `.env.example`。
- `frontend/src/components/ui/`、`frontend/src/components/ai-elements/`——registry 生成物（Shadcn/MagicUI/AI SDK），手改会在下次生成/同步时被覆盖，且 ESLint 豁免无校验兜底；定制走 theme token / className / 上层封装组件。
- `skills/public/ppt-master/`——vendored 上游树，补丁必须在 `TRAINERAI_PATCHES.md` 登记；塞未登记文件会污染补丁语义（2026-09-12 缺陷批教训）。
- `backend/packages/vimax/`——vendored 第三方 ViMax，pre-commit lint 豁免区；改动走 vendored patch 口径。
- `docker/sandbox/cache/`——离线 chrome/字体包，公司服务器离线镜像构建（K16）的唯一来源，勿删勿换。CI 有机械门禁（check 脚本校验项 14），刻意更新须 commit message 带 `[binary-zone-ok]`。
- `video-templates/course-standard/.baseline-m0/`——像素回归基线；只有刻意改版式时才随 diff-gallery 流程更新。CI 有机械门禁（check 脚本校验项 14），刻意更新须 commit message 带 `[binary-zone-ok]`。
- `config.yaml` 结构性改动须同步 `config.example.yaml` 并递增 `config_version`（部署端 config 只读挂载，靠版本号触发升级提示）。

AI 评审规则镜像见 `.opencodereview/rule.json`，改禁区时同步。

## 检索指引（先找对目录再 grep）

全局入口：`docs/README.md`（区域地图 + 常青文档索引 + 找什么去哪 + 领域词汇表）。

- **检索 docs/ 默认排除冷区**（`docs/archive/` 与 `docs/plans/archive/` 是历史冻结区，结论可能已被后续批次勘误，勿作现行依据）：grep 加 `--glob '!docs/archive/**' --glob '!docs/plans/archive/**'`；查"某批为什么这么做"才进冷区。
- 现行长期口径 → docs/ 根目录常青文档（索引见 `docs/README.md`）+ 本文件；产品能力概览 → `README.md`（名册类以代码为准，见「文档治理」README 防腐约定）。

## 服务器与部署

- 通用部署方式见 `docs/personal-server-deployment-guide.md` 与 `docker/` 中的配置样例。
- 本仓库公开开发，真实主机、账号、密钥、内部任务 ID、用户数据和运行数据库不得提交。内部运维手册与部署记录应保存在仓库外。

## Remotion 课程视频管线（remotion_course）

- `video_type="remotion_course"` 走后端任务管线，**全链路独立**（解析/分镜/素材/版式不套用 ai_image/ai_video 口径）；合成走独立 remotion-worker 池（`WORKER_POOL=remotion`）。
- 模板 = `video-templates/course-standard/`；**改模板必须重建 remotion-worker 镜像**（TEMPLATE_VERSION hash 防漂移；公司服务器走部署手册 K16）。
- 素材来源按路径前缀判定：`visuals/embedded/`、`visuals/frame_` = 用户素材（只替换，拒绝视觉重生）；`visuals/img_`、`shot_` = AI 产物（可重新生成）。
- 旧 `video_type="remotion"` 是 chat-handoff 链路（agent 现写代码），与 `remotion_course` 语义不同，门控不得合并。
- 品牌包单一事实源 = `brand_pack.py`；props 构建在 gateway，worker 只消费。
- 本地 E2E 起池方式、worker 认领谓词、解析双档/docx 内嵌图/reparse/QC/前端形态/测试清单等全部详细口径 → `docs/remotion-course-pipeline.md`。
