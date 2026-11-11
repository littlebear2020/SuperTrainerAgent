# 本地模型、Agent 执行与首页/视频作品优化实施计划

> **状态**：已执行（任务 1–8 及任务 9 的文档、隔离 E2E/性能/根门禁和服务恢复；任务 9 Step 2 保持未勾选，尚未整批完成：原始后端全量有一项本地代理环境失败）。经用户另行授权，范围增加 Rosetta 预热 Dockerfile 补丁与 fake-IP 下载最小修复；等待用户审查，不 push、不部署。
> **TL;DR**：Terra 已切换至 `openai/gpt-6.1-terra`、新增 `deepseek/deepseek-v4.1-flash`；自建 Aio 恢复隔离执行，真实 PPT/课程视频/知识卡/海报/写作/独立生图生视频均有产物；首页三卡和作品全状态列表已更新，四组证据与门禁失败分别记录，不用一组通过掩盖另一组失败。

> **For agentic workers:** REQUIRED SUB-SKILL: 执行时使用 `executing-plans`，任务 1→9 单执行者串行，逐项勾选。本计划不适合并行修改共享配置；不得用 subagent 同时写 `config.yaml` 或 `config.example.yaml`。

**Goal:** 让用户能在本地通过新模型和既有 Agent 入口完整执行对话创作任务，在首页看到一致的视频入口命名和新示例，在“我的作品”按 AI 生图/AI 生视频查看制作中、失败和已完成任务，同时保留旧对话模型选择、默认模型、视频管线、历史路由、项目与文件能力，再由用户验收并自行决定是否部署。

**Architecture:** 模型层只调整花名册配置，复用 `PatchedChatOpenAI` / `PatchedChatDeepSeek` 和现有 `/api/models`；Terra 保留旧内部 `name` 作为兼容别名，避免旧线程失效。执行层只切换现有 `AioSandboxProvider`，由本地 Docker 容器提供 bash、Python、Node、Chrome、ffmpeg 等能力，不开放宿主 bash，不改 Agent 图、工具装配或业务流程。首页层仅调整三卡 i18n 文案与既有媒体文件内容，媒体 URL 和点击路由不变。作品层继续消费现有 `/api/video/tasks`，在外层“我的作品/我的资产”内增加类型列表，并复用原任务详情、分镜、重试和删除能力。先分别验证模型、沙箱、首页和作品列表，再做联合真实业务验收。

**Tech Stack:** Python 3.12、现有 LangChain/LangGraph、FastAPI、AioSandboxProvider、本地 Docker、pytest、FFmpeg；React、TypeScript、Vitest、Playwright；不新增依赖，不改后端业务源码。

**Spec:** 用户 2026-11-11 提出的模型替换、对话任务修复、完整本地验收、不丢旧功能，以及首页三卡统一“AI视频”标识、“Remotion动效”更名“文字动效”、使用根目录两段指定示例和“我的作品中按 AI 生图/AI 生视频展示已完成和制作中任务”的补充要求；兼容性基线为 `docs/plans/2026-11-09-home-entry-and-video-assets-redesign-plan.md` 与 `docs/plans/2026-11-10-agent-new-chat-redesign-plan.md`。本文件是唯一执行决策源，不新增第二份设计文档。

**基准 commit:** `cee995a7`（2026-11-10 创建框透明背景修复；当前 HEAD）。执行前运行 `git status --short --branch` 与 `git diff cee995a7 --stat`；若出现本计划之外的代码改动，先区分旧批次或用户改动，不覆盖、不重置、不混入提交。

**执行方式:** 建议 `executing-plans` 单执行者串行；只有用户审核计划并确认当前任务已结束后才启动。本执行阶段只产生本地 commits，完成后由规划侧审 `git diff cee995a7..HEAD` 与非敏感本地配置摘要；不 push、不建 PR、不部署、不操作真实服务器。若用户之后另行授权交付远端，必须使用功能分支 + PR/OCR，命中 `config*.yaml` 等核心路径不得直推 main；合入前另审 `main...HEAD` 全量范围。

## Global Constraints

- 当前已证实的根因是 `LocalSandboxProvider + allow_host_bash: false` 在 `backend/packages/harness/deerflow/tools/tools.py:63-64` 过滤 bash；换模型不能修复执行权限。
- Terra 内部 `name: gpt-5.6-terra` 暂时保留，仅把上游 `model` 改为 `openai/gpt-6.1-terra`、显示名改为 `360·GPT 6.1 Terra`；不得新增重复的 `gpt-6.1-terra` 内部条目。
- 新增 `name: deepseek-v4.1-flash`、`model: deepseek/deepseek-v4.1-flash`、`display_name: 360·DeepSeek V4.1 Flash`，使用 `PatchedChatDeepSeek` 与其 `api_base` 参数形态。
- 两个新上游条目的能力 flags 以保守值起步，未实测前不得复制旧模型 thinking 块、reasoning workaround 或 pricing。Terra 的 `supports_vision/supports_thinking=false` 只是探针前临时状态，不是最终降级决定；Task 3 必须实测并回填能力及必要 wire profile。`supports_reasoning_effort` 原本就是 false，不虚构回归或额外开启。
- `models[0]` 继续是 `qwen3.8-flash`；`video_gen.script_model`、`subtitle_llm_model`、`vimax_llm_model`、生图和生视频 provider 均不变。
- 本地与示例 `config_version` 从 29 同步递增到 **31**；30 保留给现有测试注释中的 W2 预留。`config.example.yaml` 同步模型模板和 Aio 可选配置；活跃默认继续 Local + host bash 关闭，不因本机修复强制所有新部署安装 Docker。若执行时版本已被其他批次占用，停止并重新确定版本，不覆盖他人变更。
- 只使用现有 AioSandboxProvider；`allow_host_bash` 保持 `false`，`sandbox.environment` 保持 `{}`，不把密钥写入容器环境、日志、测试、计划或 changelog。
- 当前 arm64 Mac 没有 Apple `container` CLI，Docker CLI 存在但 daemon 未运行；执行前必须由用户启动 Docker Desktop、启用可用的 Rosetta x86_64 模拟并确认就绪。本计划不安装另一套容器运行时，不调用不带平台的 `make build-sandbox`。
- 目标镜像来自仓库现有 `docker/sandbox/Dockerfile`（基座 `all-in-one-sandbox:1.11.0`）及根构建上下文，必须使用中性本地 tag 和 `linux/amd64`，不得拉取公共 `latest` 或个人 Docker Hub 镜像代替。若现有 Dockerfile 本身缺组件、模板 hash 不符或真实探针失败，立即停止并另立镜像批次；禁止运行期安装。
- 当前真实任务未终态前不得修改配置、停止服务、重启 Gateway 或运行全量测试。不得自动取消、重试或修改用户既有任务。
- 真实模型、PPT、图像、视频和 TTS 验收属于付费操作，仅在用户批准本计划后的对应步骤执行；每例失败不自动重试，不切换 API 形态或业务管线消错。
- 不恢复此前已确认隐藏的旧导航，不改首页整体背景、布局或外层“我的作品/我的资产”信息架构；首页仅统一三张视频卡的标识、第一张卡标题和两段示例，作品页只修复类型列表和任务操作。“不丢旧功能”指原路由、API、数据、项目、上传下载、MCP 与生成能力仍可用。
- 首页 `course-video`、`ai_image`、`ai_video` 三卡的 badge 中文统一为 `AI视频`，英文统一为 `AI Video`；`course-video` 标题中文改为 `文字动效`、英文改为 `Text animation`，mediaAlt 同步去除 Remotion 技术名。其他标题、说明、目标 ID、路由和创建框类型不变。
- 根目录 `文字动效示例视频.mp4` 只用于替换 `frontend/public/course-samples/remotion.mp4` 的内容，`图片动效示例视频.mp4` 只用于替换 `image-motion.mp4` 的内容；现有媒体 URL 不变，第三张 `ai-video.mp4` 不改，不修改或提交根目录源视频。
- 已实测两个源文件分别为约 478.912 秒/1920×1080 与 141.4 秒/2048×1152，均含 AAC 音轨，不能直接入库。各选一个能代表对应能力的连续片段，转码为 8–12 秒、960px 宽、H.264、yuv420p、无音轨、faststart、约 1–2MiB；保留原有仅当前面板播放、页面隐藏/reduced motion 停止和失败降级行为。
- 作品空白根因已证实：`selectCompletedWorks` 同时按 `ready` 和类型过滤；本地只读聚合存在 `draft`、`failed` 的 AI 生图任务而没有 ready 任务，因此全部消失。新需求明确取代旧计划“仅 ready 作品”的契约，不保留隐藏旧 DOM。
- 我的作品内部仅提供 `AI 生图`、`AI 生视频` 两个类型列表，默认 AI 生图；`ai_image + ai_visual` 归 AI 生图，`ai_video` 归 AI 生视频。不再按 status 过滤，制作中、草稿、质检修复、失败、取消和完成均保留，其他 `task_type` 仍排除。
- 继续只消费现有接口的全局最近 100 条 video_gen 任务：先筛选上述三个后端类型、稳定排序、截 100，再按当前类型展示；不声称每个类型各有后端 100 条，不为 `ai_visual` 另开第三类或另拉接口，不新增服务端分页。每类首屏 6 个、本地每次增加 6 个。
- 所有状态均可进入原详情；`draft && progress >= 100` 可继续编辑分镜，failed 复用原二次确认重试，删除保留二次确认。只有 `ready && artifact_missing !== true` 可预览/下载；非 ready、missing 不得因此失去详情、重试或删除等既有能力。
- 我的资产继续是独立课程项目数据与封面/CRUD，不把项目卡片当视频作品，不把视频任务自动并入项目资产。
- 每个实现任务遵循“失败测试 → 确认功能原因失败 → 最小实现 → 定向与相邻回归通过 → 本地提交”；纯环境和真实验收任务不伪造红灯或空提交。
- 不删除或弱化旧测试，不通过 skip、隐藏 DOM、占位文件或人工构造成功响应制造通过。CI 缺少 gitignored 根配置导致的 skip 不算本地名册验收。
- 超过 120 秒的命令后台执行并等待完成通知；全量测试、E2E、性能构建串行，不冻结期间修改被测输入，不与真实生成任务争用端口、数据库或构建目录。

## 评审决策记录

| Frontier 问题 | 本计划决策 |
| --- | --- |
| Q1：是否直接改内部 Terra 名称 | 否。保留 `gpt-5.6-terra` 作为兼容别名，避免旧线程模型引用失效；用户可见名称和上游 ID 更新为 6.1。 |
| Q2：新模型是否继承旧能力 flags | 不直接继承。初始 false 只是探针前状态；Terra 必须经真实 vision/thinking 探针按结果回填，不能接受未声明的静默降级。 |
| Q3：是否同时切换默认/视频模型 | 否。默认、脚本、字幕、ViMax、生图和生视频配置保持不变，隔离变更面。 |
| Q4：如何恢复 shell | 使用现有 AioSandboxProvider + 本地 Docker；不开放宿主 bash，不重写沙箱或 Agent 业务代码。 |
| Q5：镜像缺依赖怎么办 | 先构建仓库现有自建镜像；仍缺组件则停止并报告 image ID、基座版本及可用 digest，另立镜像批次。禁止运行期安装或临时替换渲染链。 |
| Q6：什么时候可以重启 | 当前任务全部结束、用户批准维护窗口后；`sandbox.use` 是启动快照，不能靠热重载冒充生效。 |
| Q7：测试绿是否等于完整能力 | 否。离线测试、mock E2E、真实模型工具调用、真实文件/视频产物分别记录，skip 与 NOT RUN 不写成通过。 |
| Q8：何时部署 | 本计划不部署。用户完成本地验收后，另行核对公司服务器的容器运行方式、镜像 digest、资源和挂载。 |
| Q9：既有两份计划之外的发现 | `vimax-video` 空技能回退及已登记的 stage 导出问题继续留在 backlog，不在本批顺手修改。 |
| Q10：作品是否继续只显示 ready | 否。用户新需求取代旧契约；AI 生图/AI 生视频两个列表保留全部状态，只有预览/下载继续受 ready 与产物存在性门控。 |
| Q11：是否恢复原任务操作 | 是。恢复原详情、草稿分镜、失败重试确认和删除确认，不把旧行式 TaskCard 硬塞进新网格，不改变对应后端接口。 |
| Q12：分类后的类型与 100 条如何解释 | 两个展示类型对应三个后端 task_type：`ai_image + ai_visual` 归 AI 生图，`ai_video` 归 AI 生视频；仍只取接口全局最近 100 条，每类本地 6→12，不新增分页。 |
| Q13：我的资产中的项目卡片是否误归类 | 否。项目来自独立 `GET /api/video/projects` 与 CourseProjectStore；本批保持项目资产边界，不用它替代视频任务。 |
| Q14：首页与 Agent 页如何命名 | 三个首页 badge 为“AI视频”，第一张标题为“文字动效”；`agents.newChatGuides.course-video.displayName` 继续保持“课程视频 / Course video”，因为它是完整课程创作 Agent，不与第三卡 AI 视频管线混称。 |
| Q15：新示例如何映射 | 根目录文字动效源 → 既有 `remotion.mp4`；图片动效源 → 既有 `image-motion.mp4`；URL 不变，`ai-video.mp4` 与第三卡不动。 |
| Q16：长视频能否原样替换 | 不能。两个源均超长且含音轨；执行时选择代表片段并转码，继续满足原 Q6 的 8–12 秒、960px、无音轨、H.264 和播放约束，源文件不动。 |
| Q17：Terra 视觉/思考能力如何定稿 | Task 3 用 256×256 红图与生产形状顶层 thinking 参数实测，只有真实视觉回答/推理产出才开启并回填 wire profile；不支持则停止交用户选择，不默默降级。 |
| Q18：config_version 取多少 | 取 **31**，30 按现有测试注释保留给 W2；不是使用无预留依据的 30，也不在本批重复递增。 |
| Q19：example 是否默认启用 Aio | 否。活跃 Local 默认不变，Aio 作为完整 Agent 能力的显式 opt-in；部署指南同步纠正“本地沙箱默认即可”的能力表述。 |
| Q20：镜像与提交出口 | 使用仓库 Dockerfile 构建中性本地 `linux/amd64` 镜像，不调用无平台的 make 目标；先本地提交并审本批 diff，push/PR 必须另行授权且命中核心配置不得直推 main。 |

frontier 已按用户已确认的模型/执行建议、首页卡片/媒体与作品列表要求及本轮评审证据收敛；若实测推翻上游模型、镜像、素材或任务数据假设，停止回报，不在执行中自行改变上述决策。

## 路径边界（allow / ban / 共享基座）

| 允许路径（allow） | 禁改路径（ban） | 共享基座触达 |
| --- | --- | --- |
| 根 `config.yaml`（本地、gitignored）与 `config.example.yaml`；仅限本计划模型和 sandbox 节、版本与说明 | 其他配置节、`.env*`、`extensions_config*.json`、真实服务器配置 | **是，单执行者独占**；模型与沙箱串行修改同一文件 |
| `backend/tests/test_360_new_models_roster.py`、`backend/tests/test_local_bash_tool_loading.py` | 既有断言只能因本计划明确的新契约扩充；不得删除默认模型、视频配置、Aio 既有断言 | **是**：只增加本批 pin 与版本号更新 |
| 新建 `backend/tests/test_model_roster_live.py` | `backend/tests/test_client_live.py`、`backend/scripts/model_connectivity_check.py` 不修改；后者不原样全量运行 | 新 live 文件整文件 opt-in，不影响默认测试 |
| `frontend/src/components/workspace/video-gen/completed-video-grid.tsx`、`video-gen-page.tsx` | 其他 `frontend/src/**`、registry 组件、旧 TaskCard 无关分支均不改 | **是，单执行者串行**：只恢复作品分类、状态与既有任务操作 |
| `frontend/tests/unit/components/workspace/video-gen/completed-video-grid.test.ts`、`completed-video-grid.dom.test.tsx`、`video-gen-page-source-guard.test.ts`；`frontend/tests/e2e/video-assets.spec.ts`、`utils/mock-video-api.ts` | 不删除既有覆盖；其他前端测试和 mock 默认响应不变 | mock 只增加本批需要的清空/重试/删除/失败控制能力 |
| `frontend/src/core/i18n/locales/{zh-CN,en-US}.ts` 仅首页三卡 copy；`frontend/public/course-samples/{remotion,image-motion}.mp4` 仅替换内容 | 其他 locale 区域、`home-course-catalog.tsx`、`home-course-entry.tsx`、`ai-video.mp4`、根目录源视频均不改 | **是**：媒体 URL 不变，源文件只读；只允许 FFmpeg 生成目标媒体 |
| `frontend/tests/unit/components/workspace/{home-course-entry.dom.test.tsx,workspace-home-i18n.dom.test.tsx,home-course-catalog.test.ts}`；`frontend/tests/e2e/home-course-entry.spec.ts` | 不删除播放/reduced motion/路由/移动端旧覆盖 | 首页文案、媒体映射和真媒体行为由同一执行者验收 |
| 本计划、`docs/changelog/changelog-2026-11-11-local-model-and-agent-execution-readiness.md`、`docs/findings-backlog.md`（仅旧模型探针漂移登记） | 不新增设计稿/审计报告，不改 README、AGENTS.md 或旧模型探针脚本 | 同一执行者收尾，changelog 必须含真实「验收记录」 |
| `docs/personal-server-deployment-guide.md` 的通用 Aio 配置与升级说明（仅文档） | 不记录真实主机、账号、密钥、内部任务 ID、用户数据或实际部署操作 | 只写可公开的部署前提，不执行部署 |
| 本地测试线程、容器和临时产物；仅清理本批记录的资源 | 不删除用户线程、项目、数据库、已有容器或任务产物 | 所有真实任务串行，产物不提交 |

**一律禁改：** `backend/app/**`、`backend/packages/**`、`backend/seed/**`、其他后端业务/框架源码、未明确 allow 的 `frontend/src/**`、`docker/**`、`skills/**`、`video-templates/**`、数据库 schema/迁移、依赖 manifest 与锁文件、部署脚本、原始 Demo。根 `.dockerignore` 虽不等于 `docker/**`，但也不在本批 allow 内，需要修改时必须先由规划侧显式扩权。若必须修改上述任一路径，立即停止并报告，不扩大 allow。前端修复不额外递增 `config_version`。

## Review Focus

1. **新上游模型能聊天但不能调用工具**：Task 2 静态断言不够，Task 3 必须实测两个模型的 tool call 与工具结果续轮，失败不得入册宣称可用。
2. **旧线程仍保存 `gpt-5.6-terra`**：Task 2 钉住兼容别名与 `/api/models` 映射，Task 8 验证旧会话仍可打开和继续，不静默改默认模型。
3. **镜像存在但缺 PPT/Remotion 组件或模板漂移**：Task 4 在切换配置前做真实 roundtrip/静帧预检；禁止安装依赖，Task 5 再验证 provider 实际路径。
4. **任务运行中切沙箱或重启导致数据/容器损坏**：Task 1 是硬门槛；Task 5 只使用专用前缀和测试线程，回滚只清理本批精确资源。
5. **非 ready 卡片恢复后失去状态门控或串任务操作**：Task 6 必须验证 draft/running/qc_repair/failed 可见、精确 ID 的详情/分镜/重试/删除，以及非 ready/missing 无法播放下载。
6. **长视频直接替换导致体积、音轨或映射错误**：Task 7 固定源到目标的映射、代表片段、ffprobe 规格和实际播放，保持 URL/路由/reduced motion 不变。
7. **mock 测试与配置解析制造假绿**：Task 8 必须核验实际 PPTX/MP4/SVG、现有 draft/failed 任务可见、下载和真实工具日志；Task 9 分开记录自动化、真实服务和未执行项。

## 文件职责图

| 文件 | 动作 | 单一职责 |
| --- | --- | --- |
| `config.yaml` | Modify（本地、不提交） | 实际模型路由、Aio provider、实测镜像引用和本地专用容器前缀；其他字段不变 |
| `config.example.yaml` | Modify | 可公开的模型模板、Local 活跃默认与 Aio 可选模板、版本 31 与迁移说明 |
| `backend/tests/test_360_new_models_roster.py` | Modify | 新模型字段、兼容别名、默认/视频不变、API 映射、example 与版本契约 |
| `backend/tests/test_model_roster_live.py` | Create | 两个目标模型的文本、流式、工具调用及 Terra vision/thinking 真实探针；模块级付费门控 |
| `backend/tests/test_local_bash_tool_loading.py` | Modify | 本地真实配置使用 Aio、保留 `allow_host_bash:false` 且有效工具含 bash |
| `frontend/src/components/workspace/video-gen/completed-video-grid.tsx` | Modify | 两类作品选择、类型 tabs、本地分页、状态卡片、预览/下载门控与既有任务操作 |
| `frontend/src/components/workspace/video-gen/video-gen-page.tsx` | Modify | 外层双视图保持不变，仅向作品网格接原路由与删除 mutation |
| `frontend/tests/unit/components/workspace/video-gen/completed-video-grid.{test.ts,dom.test.tsx}` | Modify | 全状态/类型/100 条纯函数与分类切换、精确 ID 操作、播放器卸载、状态门控 |
| `frontend/tests/e2e/video-assets.spec.ts`、`utils/mock-video-api.ts` | Modify | 真实浏览器 DOM 行为、路由、请求次数、重试/删除、移动和无 pageerror；不改后端 |
| `frontend/src/core/i18n/locales/{zh-CN,en-US}.ts` | Modify | 仅首页三卡 badge、第一张标题及对应 mediaAlt；不改路由或其他文案 |
| `frontend/public/course-samples/{remotion,image-motion}.mp4` | Replace bytes | 分别使用用户提供的文字/图片动效源转码；URL、第三卡和根目录源文件不变 |
| 首页相关 unit/E2E 文件 | Modify | 三卡标识、双语命名、媒体映射、真媒体规格、原路由和播放行为 |
| `docs/personal-server-deployment-guide.md` | Modify（文档） | 说明完整 Agent 能力的隔离沙箱前提与配置升级；不执行部署 |
| 计划与 changelog | Modify / Create | 勾选、真实命令输出、产物验收、遗留项与回滚证据 |

---

### Task 1：冻结运行窗口并建立基线

**Files:** 无修改；只读检查。

- [x] **Step 1：确认分支、历史和漂移面**

运行 `git status --short --branch`、`git rev-parse --short HEAD`、`git diff cee995a7 --stat`、`git diff --check`。保留已有 25 个本地提交，不重新克隆、不 reset/rebase，不覆盖用户改动。

- [x] **Step 2：确认任务与 Docker 门槛**

由用户确认当前生成任务已结束；用现有只读 API/页面核对没有运行中任务，数据库只读聚合以运行 Gateway 的实际 `DEER_FLOW_HOME/DB_PATH` 为准，不在 `backend/data/`、`backend/.deer-flow/data/` 与根 `.deer-flow/data/` 之间猜测；本次截图结论来自当前 Gateway 使用的 `backend/.deer-flow/data/deerflow.db`。不得只凭页面看似空闲就停止服务。用户启动 Docker Desktop 并确认 Rosetta 后，`docker info --format '{{.OperatingSystem}} {{.Architecture}}'` 必须成功；当前 daemon 未运行，未解决前不得进入配置切换。

- [x] **Step 3：记录非付费基线**

```bash
cd backend && env -u RUN_LIVE PYTHONPATH=. uv run pytest tests/test_360_new_models_roster.py tests/test_model_factory.py tests/test_config_example_parity.py tests/test_local_bash_tool_loading.py tests/test_aio_sandbox.py tests/test_aio_sandbox_bash_env_output.py tests/test_aio_sandbox_local_backend.py tests/test_aio_sandbox_provider.py tests/test_sandbox_mem_limit.py tests/test_sandbox_shm_size.py tests/test_sandbox_orphan_reconciliation.py -q
```

Expected: 全部通过或仅有明确的本地配置缺失 skip；任何已存在失败先报告，不记作后续任务的预期红灯。

### Task 2：替换 Terra、新增 DeepSeek 并钉住兼容契约

**Files:**
- Modify: `config.yaml`、`config.example.yaml`
- Modify: `backend/tests/test_360_new_models_roster.py`
- Create: `backend/tests/test_model_roster_live.py`

**Interfaces:** 复用 `create_chat_model(name, thinking_enabled=False, attach_tracing=False, **kwargs)` 与 `list_models(config=...)`；不改生产函数签名。

- [x] **Step 1：先写失败断言与带硬门控的 opt-in live 探针**

在现有 roster 测试中新增独立用例，保留 Claude、默认模型与 video_gen 旧断言：
- Terra 别名恰好一条，`name == "gpt-5.6-terra"`、`model == "openai/gpt-6.1-terra"`、显示名为 `360·GPT 6.1 Terra`，不存在重复内部名 `gpt-6.1-terra`。
- DeepSeek 的内部/上游 ID、显示名与 provider 正确，使用 `api_base`。两个目标的三项能力 flags 在 Task 2 均为 false；Terra 后两项由 Task 3 的真实探针重新裁定并更新测试，不把临时 false 当最终契约。
- 用 async 测试 `await list_models(config=real_config)` 验证 `name/model/display_name/thinking/reasoning` 映射；该 API 不返回 vision，不得用它冒充 vision 验证。stubbed factory 验证上游 ID 原样透传且不携带未声明 reasoning effort。
- example 含兼容别名说明、新上游 ID 和 DeepSeek 注释模板；两个版本断言按本批契约从 29 改为 **31**，并在注释中写明 30 为 W2 预留、本批使用 31。

新建 `test_model_roster_live.py`，整文件 `pytestmark = pytest.mark.live`，并照 `test_client_live.py:20-32` 增加模块级 guard：未设 `RUN_LIVE=1` 或缺少根配置时 `pytest.skip(..., allow_module_level=True)`。marker 本身不会阻止付费调用，二者缺一不可。参数化仅覆盖两个内部名称，使用真实 factory，不 patch provider；用真实配置的 `model_copy` 仅将探针条目限制为 `max_tokens=64`、`max_retries=0`，再调用 `create_chat_model(..., thinking_enabled=False, attach_tracing=False)`，不得通过重复关键字覆盖工厂参数。测试普通文本、流式非空、一个无副作用虚拟工具的调用，以及带对应 `ToolMessage` 的第二轮非空回答；不实际执行工具。

- [x] **Step 2：确认离线测试因新需求失败**

Run: `cd backend && env -u RUN_LIVE PYTHONPATH=. uv run pytest tests/test_360_new_models_roster.py -q`

Expected: FAIL 仅因 Terra 旧上游/flags、缺 DeepSeek 或版本仍为 29；另运行新 live 文件时不带 `RUN_LIVE`，Expected: 由模块级 guard 产生 SKIPPED，不用付费请求制造红灯。

- [x] **Step 3：最小修改模型配置**

仅按 Global Constraints 更新 Terra 和 DeepSeek；连接字段复用本地同通道非密钥配置，`.env` 不变。两个新条目初始不加 thinking 块、旧 GPT workaround 或 pricing。默认位、Claude/Qwen/GLM 其他条目与 video_gen 不改。同步 example、名册说明和 `config_version: 31`。

- [x] **Step 4：确认离线契约与相邻回归通过**

```bash
cd backend && env -u RUN_LIVE PYTHONPATH=. uv run pytest tests/test_360_new_models_roster.py tests/test_model_factory.py tests/test_config_example_parity.py tests/test_client.py -q && uv run ruff check tests/test_360_new_models_roster.py tests/test_model_roster_live.py && uv run ruff format --check tests/test_360_new_models_roster.py tests/test_model_roster_live.py
```

Expected: 全绿；真实配置用例本地不得 skip。

- [x] **Step 5：提交本地模型批次**

只 stage 本任务 tracked 文件；根 `config.yaml` 不入 Git。Commit: `chore(config): 更新 Terra 并新增 DeepSeek 模型`。

### Task 3：真实验证新模型并回填 Terra 能力契约

**Files:**
- Modify: `backend/tests/test_model_roster_live.py`
- Modify when probes change capabilities: `config.yaml`、`config.example.yaml`、`backend/tests/test_360_new_models_roster.py`

- [x] **Step 1：运行最小真实模型探针**

Run: `cd backend && RUN_LIVE=1 PYTHONPATH=. uv run pytest tests/test_model_roster_live.py -v -s -rs`

Expected: 两个模型所有基础用例实际执行并通过，无 skip；文本和流式非空，工具名称/参数正确，工具结果续轮成功。模型不存在、鉴权失败、工具 400、要求 Responses API 或持续超时均停止，不自动重试、不修改 provider。

- [x] **Step 2：增加并执行 Terra vision/thinking 能力探针**

vision 使用既有探针同规格的 256×256 纯红图，禁止 1×1 像素图，断言模型回答主色为红色而非仅 HTTP 200。thinking 通过真实 factory 的临时 `model_copy` 复刻线上真实请求体的顶层 `thinking` 形状；依据 `backend/scripts/model_connectivity_check.py:121-135`，openai SDK 会把 `extra_body` 内容 merge 到 body 顶层，不得写成嵌套的 `extra_body.thinking`。断言存在非空 reasoning/thinking 字段或 `reasoning_tokens > 0`，不得像旧脚本一样只验证顶层键被接受。

两类探针分别设置合理 token 预算和 `max_retries=0`，不复用普通文本的 64 token 上限，不记录 reasoning 正文。若 API/上游不支持，保持 false 并停止交用户选择“保留旧 Terra”或“明确接受降级”，不得同时宣称无功能损失。

- [x] **Step 3：按实测回填 flags、wire profile 与离线契约**

仅对 Terra 实测通过的项开启 `supports_vision/supports_thinking`；对 360/OpenAI 兼容通道，config 侧按实测写入 `when_thinking_enabled.extra_body.thinking`，由 SDK 在 wire 层合并为请求体顶层 `thinking`。`when_thinking_enabled.thinking` 是 Anthropic/vLLM 等特定 provider 的另一种构造器形态，不能搬作 Terra 通用写法；`factory.py:132-166` 已区分这些分支。两层不可混淆，也不能只把 flag 改 true 而不提供生产参数。DeepSeek 未实测的能力继续 false，`supports_reasoning_effort` 不因旧 Terra 历史值被误写为回归。

更新 roster 断言并运行 Task 2 的离线/ruff 命令。按 `[t.name for t in get_available_tools(...)]` 断言：vision=true 时含字符串 `view_image`，vision=false 时不含；比较工具 `.name`，不是变量名 `view_image_tool`。若 thinking=true，页面选择思考档不得静默回落。任何 flags/profile 变化独立提交 `chore(config): 回填 Terra 实测能力标记`，无变化不空提交。

- [x] **Step 4：核对模型 API 与真实页面**

读取本地 `/api/models` 的非敏感字段，确认 Terra 显示 6.1、上游 ID 正确、DeepSeek 可见、默认仍为 Qwen；不得要求该 API 提供当前 schema 没有的 vision 字段。用本地登录页面分别选择两个模型做短对话，并按已开启能力验证真实工具/模式。

- [x] **Step 5：记录真实结果**

只记录模型名、用例结果、能力布尔结论、token 数和耗时，不记录密钥、完整提示词、推理正文或用户隐私。此验证任务无代码变更时不空提交。

### Task 4：构建自建 AIO 镜像并切换隔离沙箱配置

**Files:**
- Modify: `config.yaml`、`config.example.yaml`
- Modify: `backend/tests/test_local_bash_tool_loading.py`
- Build only, no edits: `docker/sandbox/Dockerfile` 及其既有构建输入

**Interfaces:** 复用 `deerflow.community.aio_sandbox:AioSandboxProvider`、`get_available_tools(...)`；不修改 provider、工具实现、Dockerfile 或技能。

- [x] **Step 1：写真实配置的失败契约**

新增用例显式 `AppConfig.from_file(str(REPO_ROOT / "config.yaml"))`，并参照 `test_360_new_models_roster.py::real_config` 只在该用例/fixture 中处理根配置缺失 skip，不能把现有 SimpleNamespace 单元测试一并跳过。断言 sandbox 为 Aio、`allow_host_bash is False`、`environment == {}`，并按真实 Agent 工具组调用 `get_available_tools(..., app_config=...)` 断言 bash 与文件工具存在。

example 断言改为：活跃默认继续 Local + host bash 关闭；注释中的 Aio 模板包含自建中性镜像、空 environment 和构建前提，不得要求活跃 Aio 或个人 registry。

- [x] **Step 2：确认功能原因红灯**

Run: `cd backend && env -u RUN_LIVE PYTHONPATH=. uv run pytest tests/test_local_bash_tool_loading.py -q`

Expected: 新真实配置用例因当前 Local provider / bash 缺失而 FAIL；既有 Local/Aio/alias 纯单元测试仍执行并通过，example 断言不依赖本地根配置。

- [x] **Step 3：以仓库根构建 linux/amd64 自建镜像**

确认 Docker Desktop 的 Rosetta x86_64 模拟可用后，在仓库根后台执行。构建不额外使用 `--pull`，基座缺失时由 Docker 正常拉取，避免每次强制访问 registry 增加失败点；构建后记录实际基座 ID/digest。

```bash
docker build --platform linux/amd64 -t deerflow-sandbox:local-cee995a7 -f docker/sandbox/Dockerfile .
docker image inspect deerflow-sandbox:local-cee995a7 --format '{{.Id}} {{.Os}}/{{.Architecture}}'
docker image inspect enterprise-public-cn-beijing.cr.volces.com/vefaas-public/all-in-one-sandbox:1.11.0 --format '{{.Id}} {{json .RepoDigests}}'
docker run --rm --entrypoint sh deerflow-sandbox:local-cee995a7 -c 'uname -m; for path in /opt/chrome-headless-shell /opt/remotion-template /opt/knowledge-card /usr/share/fonts/truetype/ppt-fallback; do test -n "$(ls -A "$path" 2>/dev/null)" || { echo "EMPTY_OR_MISSING: $path"; exit 1; }; done; echo PATHS_OK'
```

不得调用无 `--platform` 的 `make build-sandbox`，不得使用个人 Docker Hub 名称或公共 `latest` 替代。记录本地 image ID、`linux/amd64`、基座 `1.11.0` 及可获得的基座 RepoDigest；本地构建不保证有自身 RepoDigest，不设假门槛。上述冒烟必须显式 `--entrypoint sh`，且 `uname -m` 为 `x86_64`；该镜像有常驻服务 ENTRYPOINT，直接 `docker run <image> <cmd>` 会把命令作为服务参数而挂住，不得据此误判镜像失败。provider 无 `sandbox.platform` 配置可弥补错误架构。

根 `.dockerignore` 的父目录排除与子目录回补是构建上下文风险，不能仅凭规则片段断言成功或失败，必须以实际 COPY 和上述四个非空目录为证据。出现 `COPY ... not found`、目录缺失或空目录立即停止；若需修改 `.dockerignore` 或任何 `docker/**`，先回规划侧扩 allow，不得顺手修复。

- [x] **Step 4：先就绪、再执行镜像与双路由硬预检**

先确认 `sta-aio-precheck` 名称未被任何既有容器占用，再按实际 provider 的资源与浏览器运行参数启动一次性服务容器；不得使用 Docker 默认 64M shm：

```bash
docker run -d --rm --name sta-aio-precheck \
  -p 127.0.0.1:<free-port>:8080 \
  --shm-size 2g --memory 3g --memory-swap 3g \
  --security-opt seccomp=unconfined --pids-limit 512 --ulimit core=0 \
  -v "$PWD/skills:/mnt/skills:ro" \
  deerflow-sandbox:local-cee995a7
```

`--shm-size 2g` / `--memory 3g` 对齐 `local_backend.py:920-955`，`seccomp=unconfined` 对齐其浏览器运行要求；错误资源档可能让 Chrome/Remotion 静帧假阴性，不得据此判定镜像缺组件。挂载必须落在 `/mnt/skills`：`skills_config.py:18-20` 与当前 `config.yaml:178-179` 均使用该路径，技能脚本按 `/mnt/skills/<category>/<skill>` 取文件，挂错位置会让 attribution guard 和模板 hash 比对误报缺组件。只读挂载与 `aio_sandbox_provider.py:383-400` 的生产口径一致；DooD 的 `DEER_FLOW_HOST_SKILLS_PATH` 不适用于本机直跑，本批不新增该 env。

取得实际映射端口后，先以有失败语义的有界轮询确认 `http://127.0.0.1:<port>/v1/sandbox` 返回 200（`backend.py:16-42`）；就绪后再执行功能探针。`/v1/sandbox` 就绪结果与 `/v1/shell/exec`、`/v1/bash/exec` 结果分别记录，不用功能路由失败反推容器未就绪。预检结束只 `docker rm -f sta-aio-precheck` 这个本批具名容器，禁止批量清理。

使用上述自建镜像和只读挂载的 `skills/` 验证：
- Python、Node、npx、ffmpeg/ffprobe、LibreOffice、中文字体、x86_64 Chrome headless 均可用；两条 exec 路由均真实成功。
- PPT 所需 `pptx/cairosvg/PIL/lxml` 等导入成功，`attribution_guard.py` 通过；临时两页 PPTX 完成 roundtrip 导入、质量检查、导出、delivery 和 readback，页数一致且 `rebuilt=0`。
- 镜像内 `/opt/remotion-template` 与 `/opt/knowledge-card` 及各自 node_modules 完整，模板 hash 与只读技能源一致，真实静帧非空；不得用挂载目录冒充镜像预装能力。
- 全程禁止安装依赖。缺组件/hash 不符/路由或探针失败即停止，Course Video 无安装兜底；若必须修改 `docker/**` 或 `skills/**`，另立镜像批次，本批对应验收保持未完成。

- [x] **Step 5：最小修改 sandbox 配置与公开可选模板**

本地 `sandbox.use` 改为 Aio，写入 `deerflow-sandbox:local-cee995a7` 和 `container_prefix: sta-aio-local-acceptance`；保持 `allow_host_bash:false`、输出限制和 `environment:{}`，不加 `provisioner_url` 或虚构 platform 字段。预检默认 replicas=3 对应的 8080–8082 空闲端口，实际端口以容器 inspect 为准；2026/3000/8001/8040/8041 不在该段，不擅自改端口。

example 活跃 Local 默认不变，只在 Aio 注释模板使用中性 `deerflow-sandbox:local`、空 environment 和构建说明，不写本地 tag/image ID/前缀。将过时超时注释改为当前实现：300 秒同时参与硬墙钟上限，主路径另有静默宽限；amd64 模拟可能更慢，长渲染沿用技能的后台执行、退出码和日志轮询，不盲目提高超时。版本沿用本批 **31**，不再递增。

- [x] **Step 6：确认定向与沙箱回归通过**

```bash
cd backend && env -u RUN_LIVE PYTHONPATH=. uv run pytest tests/test_local_bash_tool_loading.py tests/test_aio_sandbox.py tests/test_aio_sandbox_bash_env_output.py tests/test_aio_sandbox_local_backend.py tests/test_aio_sandbox_provider.py tests/test_sandbox_mem_limit.py tests/test_sandbox_shm_size.py tests/test_sandbox_orphan_reconciliation.py tests/test_360_new_models_roster.py tests/test_config_example_parity.py -q
```

Expected: 全绿；本地真实配置用例未 skip。

- [x] **Step 7：提交本地沙箱配置批次**

Commit: `chore(config): 启用隔离 Aio 沙箱恢复执行能力`。只提交 tracked 配置/测试，不提交镜像、容器信息或本地产物。

### Task 5：重启本地环境并验证真实 Agent 执行链

**Files:** 无计划内代码修改；仅本批测试运行时资源。

- [x] **Step 1：在确认空闲的维护窗口重启**

再次确认无运行中任务。用 `make stop` 和既有本地启动方式重启；必须使用 `PATH="$PWD/backend/.venv/bin:$PATH" make dev`。现有独立 Remotion worker 若仍在使用则按原参数重启，但 `make dev` 不负责启动它，course-video 对话 Agent 也不依赖它；不得 kill 不明进程或操作远端服务。

- [x] **Step 2：确认健康、配置与工具面**

`http://localhost:2026`、`http://localhost:8001/health`、`http://localhost:8040/health` 必须返回 200；8041 仅在保留现有独立 worker 或另行验收 remotion_course 管线时检查，不作为 course-video 对话成片判据。`make doctor` 无模型或沙箱阻断项，通过真实进程配置断言 Aio 已生效，不能只读文件推断运行态。

- [x] **Step 3：无模型调用地验证 provider**

用唯一测试 thread acquire 真实 Aio sandbox，验证普通 shell 返回 Linux、请求级 env 的 bash exec、文件写入后读回、技能只读挂载和 PPT/Remotion 命令实际可用。从实际 inspect/acquire 结果取得映射端口，检查 `http://127.0.0.1:<port>/v1/sandbox`，核对端口只绑回环地址、资源限制符合配置。finally 中只 destroy 本探针精确 sandbox；禁止批量清理或 `docker system prune`。

- [x] **Step 4：验证真实 Agent 的 bash 与文件闭环**

```bash
cd backend && RUN_LIVE=1 PYTHONPATH=. uv run pytest tests/test_client_live.py::TestLiveToolUse::test_agent_uses_bash_tool tests/test_client_live.py::TestLiveMultiToolChain::test_write_then_read -v -s -rs
```

Expected: 实际 `2 passed`，事件含 bash 工具调用/结果及写入后读取闭环；Aio 下 `is_host_bash_allowed()` 为 true，若出现 Local-disabled skip 即说明运行态前置未成立，不算通过。再分别用两个新模型各做一次 `echo` 级别 bash 任务，证明“新模型 + Aio”联合可用；失败不自动重试。

- [x] **Step 5：核对旧运行时能力未丢**

确认既有 MCP 配置条目仍在、工具页不为空，原上传/下载与已有对话可读取。不得为通过此步修改 `extensions_config.json`；若原本就未配置，应如实区分“未配置”与“本批丢失”。

### Task 6：恢复作品类型列表、全部状态与既有任务操作

**Files:**
- Modify: `frontend/src/components/workspace/video-gen/completed-video-grid.tsx`
- Modify: `frontend/src/components/workspace/video-gen/video-gen-page.tsx`
- Modify: `frontend/tests/unit/components/workspace/video-gen/completed-video-grid.test.ts`
- Modify: `frontend/tests/unit/components/workspace/video-gen/completed-video-grid.dom.test.tsx`
- Modify: `frontend/tests/unit/components/workspace/video-gen/video-gen-page-source-guard.test.ts`
- Modify: `frontend/tests/e2e/video-assets.spec.ts`
- Modify: `frontend/tests/e2e/utils/mock-video-api.ts`

**Interfaces:** 保留 `CompletedVideoGrid`、`selectCompletedWorks`、6/100 常量和 `canPreviewOrDownload` 导出，更新不再符合语义的注释。网格新增 `onDelete: (id: string) => void` 与 `onNavigate: (id: string, path?: "storyboard") => void`；页面接现有 delete mutation 和 `/workspace/video-gen/:id[/storyboard]`，不新增生产 API。每卡使用同一文件内顶层 `CompletedWorkCard` 和共享 `videoTaskTypeLabel`，不新建组件文件或第三份标签映射。

- [x] **Step 1：先更新失败测试与夹具**

纯函数测试把“仅 ready”改为“两个展示类型、三个后端 task_type 的全部状态”：`ai_image + ai_visual` 归 AI 生图、`ai_video` 归 AI 生视频；覆盖 `ready/draft/pending/running/composing/qc_repair/failed/cancelled`，排除 remotion/vimax 等其他类型。保留稳定排序、输入不变异、全局最近 100 和 `canPreviewOrDownload` 的 ready/missing 门控。

DOM/E2E 增加：默认 AI 生图、切到 AI 生视频、另一类型和其他管线不出现；7+7 条数据且 AI 生图组至少一条 `ai_visual`，验证每类 6→7、切类后播放器真正卸载且分页重置；详情、draft=100 分镜、failed `ai_visual` 重试确认/取消/409 导航、删除确认/取消均断言精确 ID 和调用次数；qc_repair 可见但不可播放/下载/重试；列表 500 后手动重试成功、390px 无横向溢出、无 pageerror。将 `video-assets.spec.ts` 的真实媒体时长断言从精确 `.toBe(10)` 改为 `>=8 && <=12`，保留 `videoWidth=960`，使 Task 7 替换 `image-motion.mp4` 后仍验证同一公开规格而不是旧文件偶然时长。

将既有 `defaults to works and filters ready AI types` 更名为 `defaults to AI image list and shows all statuses`，同时重写旧断言而非只改标题：默认 AI 生图 tab 下 `ready-image/failed-ai/draft-ai` 可见，`ready-video/running-ai` 不可见；切到 AI 生视频 tab 后二者均可见；`ready-course`（remotion_course）与 `ready-vimax`（vimax）在两个 tab 均为 `toHaveCount(0)`。不能只删除旧的“非 ready 全不可见”循环而漏掉 `ready-video` 的分类断言。

为 `MockVideoAPI` 增加 `clearTasks()` 及必要的重试/删除/列表失败控制，只在 `video-assets.spec.ts` 显式清空默认任务；不改其他 spec 依赖的默认创建任务。DOM 直接渲染补齐每卡 recompose mock 和必需回调，避免 harness 错误冒充功能红灯。

- [x] **Step 2：确认三组测试因旧行为失败**

先停止真实 frontend，避免 Next dev 与 E2E 共用 `.next`；mock E2E 统一使用独立端口和 `CI=1`，禁止复用真实服务。

```bash
cd frontend && pnpm exec vitest run --project node tests/unit/components/workspace/video-gen/completed-video-grid.test.ts tests/unit/components/workspace/video-gen/video-gen-page-source-guard.test.ts
cd frontend && pnpm exec vitest run --project dom tests/unit/components/workspace/video-gen/completed-video-grid.dom.test.tsx
cd frontend && CI=1 E2E_PORT=3100 pnpm exec playwright test tests/e2e/video-assets.spec.ts
```

Expected: 分别因非 ready/ai_visual 被过滤、缺类型 tabs/操作回调或旧 E2E 排除非 ready 而 FAIL；不先写实现，不删除旧媒体/missing/分页断言。

- [x] **Step 3：最小实现全局工作集与两个类型列表**

选择器只收 `ai_image/ai_visual/ai_video`、既有稳定排序和全局截 100，不再过滤 status；当前类型从该工作集派生，`ai_visual` 与 `ai_image` 同组，不为每类各取 100 或请求更早历史。外层“我的作品/我的资产”不变；作品内部本地类型 tabs 仅 `AI 生图` / `AI 生视频`，默认 AI 生图，切类只挂载当前列表，重置 6 条和预览，不用隐藏 DOM。

保留当前网格、同卡片尺寸与封面。卡片副标题和 tabs 使用 `videoTaskTypeLabel`，删除本地 `workTypeLabel`，不改变首页入口标题。空态改为当前类型语义；loading/error/empty 互斥，error 保留原 refetch 重试。

- [x] **Step 4：恢复状态对应的既有操作**

所有卡可进详情；draft=100 显示继续编辑分镜；failed 仅在 `recomposeActionFor` 允许时显示重试，并用现有确认 dialog，409 继续进入该任务分镜；删除保留二次确认。在 `completed-video-grid.tsx` 内定义顶层 `CompletedWorkCard`，每卡调用自己的 `useRecomposeTask(task.id, ...)`，禁止在 `.map()` 调 hook、提升成共享 taskId、新建额外文件或反向 import 页面 TaskCard 形成循环依赖。

`ready && !artifact_missing` 才允许内联播放和下载；其他状态/missing 仍可见且可进详情，不误禁用重试或删除。失败可显示真实 error_message；运行/质检状态复用现有 label 和轮询，不新增时长、分辨率、项目统计或服务端字段。

- [x] **Step 5：确认定向与相邻回归通过**

```bash
cd frontend && pnpm exec vitest run --project node tests/unit/components/workspace/video-gen/completed-video-grid.test.ts tests/unit/components/workspace/video-gen/video-gen-page-source-guard.test.ts tests/unit/components/workspace/video-gen/task-card-retry.test.ts tests/unit/core/video-gen/resume-actions.test.ts
cd frontend && pnpm exec vitest run --project dom tests/unit/components/workspace/video-gen/completed-video-grid.dom.test.tsx tests/unit/components/workspace/video-gen/task-card-retry-confirm.dom.test.tsx tests/unit/components/workspace/video-gen/video-gen-page-thumbnail.dom.test.tsx
cd frontend && CI=1 E2E_PORT=3100 pnpm exec playwright test tests/e2e/video-assets.spec.ts tests/e2e/video-gen-error-states.spec.ts tests/e2e/video-gen-storyboard-edit.spec.ts
```

Expected: 全部 PASS；现有 ready 真媒体、ai_visual 重试、missing 零下载、6/100、资产 CRUD 和旧创建/分镜/重试契约不回归。

- [x] **Step 6：提交本地前端修复**

Commit: `fix(video-gen): 恢复作品分类与未完成任务`。只 stage 本任务 allow 文件，不改配置版本或性能预算。

### Task 7：统一首页视频卡命名并替换指定示例

**Files:**
- Modify: `frontend/src/core/i18n/locales/zh-CN.ts`
- Modify: `frontend/src/core/i18n/locales/en-US.ts`
- Modify: `frontend/tests/unit/components/workspace/home-course-entry.dom.test.tsx`
- Modify: `frontend/tests/unit/components/workspace/workspace-home-i18n.dom.test.tsx`
- Modify: `frontend/tests/unit/components/workspace/home-course-catalog.test.ts`
- Modify: `frontend/tests/e2e/home-course-entry.spec.ts`
- Replace bytes: `frontend/public/course-samples/remotion.mp4`
- Replace bytes: `frontend/public/course-samples/image-motion.mp4`
- Read only: 根目录 `文字动效示例视频.mp4`、`图片动效示例视频.mp4`

**Interfaces:** 继续消费 `HOME_COURSE_ITEMS` 的既有 `id/kind/agentName/initialTaskType/media.src`；不修改该 catalog，不新增路由、任务类型、组件 props 或媒体 URL。

- [x] **Step 1：先写三卡文案与媒体映射失败测试**

双语测试精确断言：三个中文 badge 均为 `AI视频`、英文均为 `AI Video`；第一张中文标题为 `文字动效`、英文为 `Text animation`，mediaAlt 不再含 Remotion。E2E 逐卡断言 badge/title 与三个既有 video src，验证第三卡文案不变，并断言进入 course-video 后 Agent 短名继续是既有“课程视频”而非泛化的 AI视频。

catalog 测试固定前三项 URL 仍依次为 `/course-samples/remotion.mp4`、`image-motion.mp4`、`ai-video.mp4`，原六入口路由及 `agents.newChatGuides.course-video.displayName` 测试不动。媒体内容替换不伪造测试红灯，以源映射、转换后 hash、ffprobe 和实际画面验收。

- [x] **Step 2：确认文案测试因旧命名失败**

真实 frontend 已在 Task 6 的 E2E 维护窗口停止，继续隔离 `.next` 与端口。

```bash
cd frontend && pnpm exec vitest run --project dom tests/unit/components/workspace/home-course-entry.dom.test.tsx tests/unit/components/workspace/workspace-home-i18n.dom.test.tsx
cd frontend && pnpm exec vitest run --project node tests/unit/components/workspace/home-course-catalog.test.ts
cd frontend && CI=1 E2E_PORT=3100 pnpm exec playwright test tests/e2e/home-course-entry.spec.ts
```

Expected: 因仍显示 `Remotion动效`、badge 不统一而 FAIL；不得先改实现，不删六入口、播放、reduced motion、失败降级和移动端断言。

- [x] **Step 3：最小更新三卡 i18n 文案**

只修改 `workspace.home.courseItems` 下三张视频卡的 badge、第一张 title 和对应 mediaAlt；保留其他标题、说明、图文课三张卡、组件和 catalog。中文 badge 使用用户指定无空格形式 `AI视频`，英文使用 `AI Video`。`agents.newChatGuides.course-video.displayName` 继续保持“课程视频 / Course video”，不改 Agent 身份、seed 或既有线程顶栏。

- [x] **Step 4：从指定源生成优化媒体并检查真实画面**

先在 `temp/` 对两个源分别抽取少量候选时间点，选择能代表对应能力的连续 10 秒片段，不机械取开头或整段。按既有参数 `-vf scale=960:-2:flags=lanczos,fps=24 -an -c:v libx264 -preset slow -b:v 1200k -maxrate 1500k -bufsize 3000k -pix_fmt yuv420p -movflags +faststart` 转码，覆盖上述两个目标文件。源文件只读，临时抽帧不入库。

转码后立即读取两个输出画面核对内容与标题，不允许映射互换。用 ffprobe 断言每个文件仅一个 H.264 视频流、无音频流、宽 960、时长 8–12 秒、yuv420p，大小约 1–2MiB；记录所选起点、时长和 hash。第三卡及其媒体不做任何改动。

- [x] **Step 5：确认首页定向与真实媒体播放回归通过**

E2E 对两个替换后的首页视频分别等待 `loadedmetadata`，断言 `videoWidth=960`、时长 8–12 秒，并至少观察一次真实 `playing` 和 `currentTime` 增长；不能只 spy play/pause 或检查 src 就宣称媒体通过。

```bash
cd frontend && pnpm exec vitest run --project node tests/unit/components/workspace/home-course-catalog.test.ts
cd frontend && pnpm exec vitest run --project dom tests/unit/components/workspace/home-course-entry.dom.test.tsx tests/unit/components/workspace/workspace-home-i18n.dom.test.tsx
cd frontend && CI=1 E2E_PORT=3100 pnpm exec playwright test tests/e2e/home-course-entry.spec.ts tests/e2e/video-assets.spec.ts
```

Expected: 全部 PASS；三卡新文案可见，两个新视频真实可解码/播放，路由和 Agent 短名不变，只有当前面板播放，reduced motion、隐藏面板、播放失败和移动端旧行为全部保持；Task 6 已更新的作品媒体 8–12 秒区间断言在替换后仍通过，本任务只复跑该文件、不重复修改它。

- [x] **Step 6：提交本地首页文案与媒体批次**

Commit: `feat(workspace): 统一视频入口命名并更新示例`。只 stage 本任务 allow 文件，不提交根目录源视频、临时抽帧或第三卡媒体。

### Task 8：完成真实业务验收与旧功能回归

**Files:** 仅本批新测试线程和临时产物；不改业务源码。

所有场景串行，付费失败不自动重试。每例同时核验真实工具日志、最终产物和用户可感知行为，不能只以“Agent 回复完成”为通过。

- [x] **Step 1：PPT Master 原生改稿**

从首页进入 PPT 制作，上传一次性两页 PPTX 的副本，只改第二页标题并保持第一页及原设计。按既有确认门继续；必须实际运行 roundtrip 导入、质量检查和导出。验证 receipt 与确认范围一致、未编辑页不被重建、readback 含新标题、delivery 无结构错误、PPTX 可下载且文字可编辑。

- [x] **Step 2：Course Video 对话成片**

从首页“文字动效”进入 `course-video`，使用固定超短脚本、无 BGM、已有音色和字幕。必须实际复制既有模板、通过静帧门并执行 `npx remotion render`；最终 MP4 经 present/download 可获取，ffprobe 有有效视频流和合理时长，不得用备用 ffmpeg 拼接或占位视频冒充。

- [x] **Step 3：Creative Studio 视觉产物**

分别完成一张含精确中文的知识卡（SVG + MP4，使用现有 `/opt/knowledge-card`）和一张真实海报生图。验证文字、非空文件、预览/下载与真实素材记录；不得在镜像缺组件时运行安装命令。

- [x] **Step 4：Co-writer 完整写作**

用 `[类型: script] [从零创作]` 完成大纲确认与全文；页面和 Markdown/JSON 导出无内部阶段标记，正文未误删。Co-writer 不新增 bash 或文件工具，不迁移技能。

- [x] **Step 5：独立首页生图/生视频管线**

分别经首页既有创建框执行一个最低范围 `ai_image` 和一个最低支持时长/成本档 `ai_video` 任务。对至少一个新任务，在尚未 ready 时进入对应作品 tab，确认卡片真实可见、可进原详情且不能预览/下载；继续等待原 worker 到 ready 后，再确认同卡可预览和下载。核验原 payload、分镜路由、真实 worker/provider 记录和 MP4 ffprobe，不把 Aio chat 渲染或最终 ready 截图当作独立管线与在途可见性的证据。

- [x] **Step 6：旧功能与数据边界**

检查首页三卡 badge 均为“AI视频”、第一张显示“文字动效”，进入 course-video 后短名继续为“课程视频”；文字/图片动效新样例可播放且 reduced motion 停止，点击仍进入原 Agent/创建框。继续检查旧对话、历史路由深链、项目新建/读取/更新/删除本批测试项目、上传下载、视频资产双 tabs、missing 操作门控和 MCP 工具列表。确认本地既有 draft/failed AI 生图任务出现在“我的作品 → AI 生图”，可打开原详情；若存在历史 `ai_visual` 也只读确认归入 AI 生图，不人为制造或修改历史任务。不得为验收自动重试或删除这些用户任务。隐藏导航保持现设计，不恢复旧 DOM；其他既有用户数据只读，不删除或重置。

- [x] **Step 7：检查浏览器与证据完整性**

真实页面操作期间不得出现 uncaught JavaScript page error；优先用真实本地 Playwright 监听 `pageerror`，登录凭据只能经运行时环境提供且不得入库。无法自动覆盖的项目明确标为人工验收，不伪造“无错误”。将首页命名/媒体、产物类型、页数/流信息、工具调用、两类作品状态、下载结果和任何遗留问题写入 changelog，不提交用户文件。

### Task 9：全量门禁、文档和本地交付

**Files:**
- Modify: 本计划、`docs/personal-server-deployment-guide.md`
- Create: `docs/changelog/changelog-2026-11-11-local-model-and-agent-execution-readiness.md`

- [x] **Step 1：确认真实任务全部结束**

只在所有本批任务终态后进入门禁；停止真实 frontend 和测试 web server，避免 Next dev 共用 `.next`、Playwright 复用真实服务造成 mock 假绿。不得遗留渲染或真实生成后台任务。

- [ ] **Step 2：运行后端与前端标准门禁**

```bash
cd backend && env -u RUN_LIVE uv run pytest tests/ -q
cd frontend && pnpm check && pnpm test
```

Expected: 全部通过；live 用例默认 skip 只表示未重复付费，不抹去 Task 3/5/8 的真实证据。

- [x] **Step 3：运行 E2E、性能与根门禁**

```bash
cd frontend && CI=1 E2E_PORT=3100 pnpm test:e2e && pnpm perf:check
cd .. && make check && python3 scripts/check_docs_convention.py && git diff --check
```

Expected: 全部通过；mock E2E 仅作 UI 回归，不替代真实验收。出现与基准无法区分的失败立即停止报告，不改旧测试消红。

- [x] **Step 4：更新通用部署说明与执行记录**

说明 Local 默认不提供 bash，完整 Agent 能力需要显式 Aio + 按仓库 Dockerfile 构建的自建镜像和依赖预检，明确替换部署指南现有“数据库/沙箱默认即可”的表述；根配置版本为 **31**。补充 amd64/Rosetta、双路由和资源前提，不把本地 Docker 验证写成公司服务器 provisioner/Docker 已验证。将旧 `model_connectivity_check.py` 的 Terra 硬编码漂移登记 `docs/findings-backlog.md`，不顺手扩改脚本。

更新计划状态与逐项勾选，changelog「验收记录」逐行填写真实命令和关键输出，分别列离线、真实模型、真实业务、首页命名/媒体、作品列表行为、人工验收及未解决事项。

- [x] **Step 5：恢复用户测试入口并做最终检查**

门禁结束后按 Task 5 方式重新启动本地真实环境，验证 2026、8001、8040 和页面；8041 仅在保留独立 worker 时检查。确认无本批在途任务、无测试容器残留、工作区仅有预期文件。保留用户既有任务和产物。

- [x] **Step 6：只提交本地文档并交付审查**

Commit: `docs: 记录本地模型、Agent 与首页作品验收`。规划侧审 `git diff cee995a7..HEAD`、非敏感配置摘要和真实证据；不 push、不建 PR、不部署。若用户后续授权远端交付，必须走功能分支 + PR/OCR，不得直推 main。

## 回滚与停止规则

- 模型失败：保留失败证据，不自动重试；Terra 可恢复原上游 ID/显示名/flags，DeepSeek 新条目撤下。tracked 文件用对应本地提交回滚，gitignored `config.yaml` 必须单独按事前后备份恢复。
- Aio 失败：恢复原 LocalSandboxProvider 且 `allow_host_bash:false`，在相同空闲窗口重启；只停止本批记录的精确容器，不删除用户线程或镜像。
- 作品列表失败：只回滚 Task 6 的本地提交，不回滚用户任务或数据库；不得靠隐藏非 ready/ai_visual 卡片恢复表面稳定。
- 首页文案/媒体失败：只回滚 Task 7 的本地提交以恢复原 copy 和媒体 bytes；根目录两个源视频保留不动，不删除用户素材。
- 若其他批次已使用 config 31 或更新配置，不机械降级版本；停止并由规划侧确定合并/回滚方式。
- Docker、模型、PPT、视频、首页媒体、作品列表任一硬门槛失败时，本计划不得标记全部完成。各组可独立报告完成状态，不得用一组通过掩盖另一组阻塞。
- 不通过开放宿主 bash、运行期安装、改业务管线、删旧测试、伪造产物或隐藏错误来消除失败。

## 本地完成定义（四组独立判定）

**A. 模型组**
1. 新模型在列表中正确显示，普通对话、流式、工具调用和 Aio bash 联合实测通过。
2. Terra 的 vision/thinking 已按真实探针回填；不支持项已由用户明确决定是否接受，不能默认为无降级。
3. 默认模型、视频模型和旧线程模型选择不中断。

**B. 沙箱与真实能力组**
1. 自建 linux/amd64 镜像通过双路由、PPT/Remotion/知识卡依赖预检，Aio bash 与文件闭环实测通过。
2. PPT、Course Video、Creative Studio、Co-writer、首页 AI 生图/生视频均有真实行为与产物证据，新任务在 ready 前已真实可见。

**C. 首页组**
1. 三卡 badge 均为“AI视频”，第一张为“文字动效”，Agent 短名继续为“课程视频”。
2. 两个指定源已转成合规样例并正确映射，原路由、真实播放与 reduced motion 不回归。

**D. 作品组**
1. AI 生图列表同时覆盖 `ai_image + ai_visual`，AI 生视频列表覆盖 `ai_video`，全部状态可见；原详情/分镜/重试/删除可用。
2. 非 ready 与 missing 不可预览/下载，现有 draft/failed 任务真实可见，外层双 tabs 与资产项目边界不变。

**共同交付门槛**
1. 旧路由、项目 CRUD、上传下载、MCP 无回归，无伪造成功或意外 skip；所有标准自动化门禁通过。
2. 计划逐项勾选，changelog 含真实验收记录；任一组未完成必须单独标明，不能宣称四组全部交付。
3. 用户再次本地测试并明确接受后，才另议公司服务器部署；本计划本身不包含部署完成。
