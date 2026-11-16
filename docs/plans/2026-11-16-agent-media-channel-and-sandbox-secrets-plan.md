# Agent 媒体通道、课程助理退役与沙箱密钥注入

> **状态**：未执行（2026-11-16；Task 1–6 已执行，Task 7 评审修复收尾中，待最终验收与 PR 合入。执行记录：`docs/changelog/changelog-2026-11-16-agent-media-channel-and-sandbox-secrets.md`）。
> **TL;DR**：只允许 `QIHU_API_KEY`、`VOLCENGINE_VOICE_ACCESS_KEY` 按 Agent 技能白名单做沙箱 per-call 注入；PPT 与创意生图默认走 360 Seedream，PPT 旁白改字节 TTS；创意工作收成 7 项视觉技能；课程助理直接退役。全新部署，不做历史兼容，不部署。

> **For agentic workers：** 按 Task 1→6 串行执行并勾选。`skills/`、`backend/seed/agents/` 和沙箱代码是核心路径，推送走分支 + PR。本计划不授权自动 push，不授权部署。

**Goal：** 修复“宿主已配置密钥但沙箱读不到”，统一 PPT/创意工作的生图与配音通道，收敛创意工作入口，并从新版本中移除课程助理。

**Architecture：** 沙箱在 active/request secrets 都为空时，按本次运行生效的公共启用技能白名单收集声明，再与宿主环境及两个允许的密钥名取交集，per-call 注入；子代理显式传递自己的有效技能白名单。PPT 保留 `--provider trainerai` 这一 CLI 名称，只替换其底层 TTS 实现；PPT 生图新增并注册 360 backend，`trainerai-imagine` 复用同一 360 请求合同。

**Spec：** 本计划及 `docs/2026-11-16-sandbox-secret-injection-investigation.md`。前一计划 `docs/plans/2026-11-16-course-video-workflow-convergence-plan.md` 已完成，本计划不重复修改其白名单和读稿步骤。

## 评审决策记录

- **部署边界**：本批是改版，完成后全新部署；不做旧对话续跑、退役别名、memory 迁移或运行时兼容代码。
- **自动注入边界**：默认宿主自动注入只允许 `QIHU_API_KEY`、`VOLCENGINE_VOICE_ACCESS_KEY`，且声明必须来自当前 Agent 白名单中的公共、启用技能。active/request secrets 的既有优先级不变；显式 AUTOBIND 兼容通道只在没有 `available_skills` 时保留。
- **诊断边界**：不在每次 bash 输出追加缺键提示，避免未使用 TTS/生图时也报警；脚本缺键或供应商失败时 fail-closed，保留真实错误。
- **课程助理**：用户已不再使用，直接退役，不指定替代 Agent，不保留历史会话兼容。
- **验收边界**：本地 Mac 完成单测、mock HTTP、工具级集成和定向 Playwright；不默认调用真实付费接口，不部署，不运行另一工作未跟踪的 live 套件。

## 完成后的效果

| 行为 | 对应任务 |
| --- | --- |
| 视频课程专家、播客的沙箱 bash 能读到宿主已有的 `VOLCENGINE_VOICE_ACCESS_KEY`；PPT 能读到该键和 `QIHU_API_KEY`；创意工作能读到 `QIHU_API_KEY`；其他宿主密钥不会因技能声明而自动注入。 | Task 1；Task 3、4 对齐声明后复核 |
| PPT 默认旁白为字节 `seed-tts-2.0`，音色 `zh_male_m191_uranus_bigtts`，输出 MP3；默认配图为 360 `bytedance/doubao-seedream-5.0-pro`。 | Task 3 |
| `trainerai-imagine` 未显式指定 provider/model 且宿主有 `QIHU_API_KEY` 时，文生图和参考图都默认走 360 Seedream，不受其他 provider key 影响。 | Task 4 |
| 创意工作只保留 7 项视觉技能；剪辑、知识萃取关键词不再路由到它；知识卡案例改为 PNG。课程助理不再出现在种子和工作台。 | Task 2、5 |

不变的边界：视频课程专家现有白名单与读稿步骤；后端视频任务默认 provider；`video-generation` 自己的选路；公共技能目录除明确修改的文件外均保留；`appearance.ts`、`constants.ts` 不改。

## 全局约束

- 默认生图模型：`bytedance/doubao-seedream-5.0-pro`；360 base URL：`https://api.360.cn`。
- 360 请求：文生图 `POST /v1/images/generations`，参考图 `POST /v1/images/edits`；body 含 `model`、`prompt`、`extra_body.size`、`extra_body.watermark=false`；参考图放在 `extra_body.image`。不发顶层 `size`，不发 `sequential_image_generation`。尺寸只映射到 `2048x1152`、`2048x2048`、`1152x2048`。
- 字节 TTS：`POST https://openspeech.bytedance.com/api/v3/tts/unidirectional/sse`，头含 `X-Api-Key`、`X-Api-Resource-Id: seed-tts-2.0`；默认 speaker 为 `zh_male_m191_uranus_bigtts`，输出 MP3。失败不回退 edge-tts 或 360 `stream_speech`。
- 自动宿主注入只允许两个密钥名；不加入 `DOUBAO_TTS_DEFAULT_SPEAKER`，不读取 `ZYUN_API_KEY`。
- 不修改 `.env`、根 `config.yaml`、`config.example.yaml`、compose、Docker；不写入 `DEER_FLOW_SKILL_SECRET_AUTOBIND`。
- 不修改 `skills/public/auto-video-flow/**`、`video-generation/**`；除 Task 7 同步密钥纪律外，不修改播客工作流和脚本；不删除本次从白名单移除的技能目录。
- 不修改 `frontend/tests/e2e/live/**`：该目录当前属于另一项未跟踪工作。本批不运行 live 套件，也不覆盖其文件。
- 测试和日志只断言密钥名、存在性与打码结果，不打印真实密钥值。
- 执行前检查 `git status --short`；允许路径以外的未提交文件保留，不覆盖、不纳入本批。

## Review Focus

- 声明了 `DATABASE_URL` 或其他密钥的技能也不能触发默认宿主注入 → Task 1 安全测试。
- 两个 Agent 连续执行不能复用彼此的声明集合 → Task 1 缓存隔离测试。
- PPT 默认命令必须真正进入字节 TTS 和已注册的 360 backend → Task 3 脚本级集成测试。
- `trainerai-imagine --ref` 必须真正到达 `/v1/images/edits`，而非被主程序提前拒绝 → Task 4 mock fetch 测试。
- 课程助理删除后，种子契约与工作台卡片集合必须一致 → Task 5 跨仓测试。

## 允许路径

- `backend/packages/harness/deerflow/sandbox/tools.py`
- `backend/packages/harness/deerflow/subagents/executor.py`
- `backend/tests/test_bash_secret_injection.py`
- `backend/tests/test_secret_context.py`（仅确需调整兼容断言时）
- `backend/tests/test_subagent_executor.py`
- `backend/seed/agents/creative-studio/**`
- `backend/seed/agents/ppt-master/SOUL.md`
- `backend/seed/agents/course-assistant/**`（整目录删除）
- `backend/app/gateway/seed.py`
- `backend/tests/test_seed_agents.py`
- `backend/tests/test_seed_ppt_master.py`
- `backend/tests/test_course_assistant_skill_whitelist.py`（删除）
- `backend/tests/test_ppt_master_tts_trainerai.py`
- `backend/tests/test_ppt_master_image_trainerai.py`
- `backend/tests/test_ppt_master_skill_vendored.py`
- `skills/public/ppt-master/SKILL.md`
- `skills/public/ppt-master/TRAINERAI_PATCHES.md`
- `skills/public/ppt-master/scripts/notes_to_audio.py`
- `skills/public/ppt-master/scripts/tts_backends/backend_trainerai.py`
- `skills/public/ppt-master/scripts/image_gen.py`
- `skills/public/ppt-master/scripts/image_backends/backend_trainerai.py`
- `skills/public/ppt-master/scripts/image_backends/backend_qihu360.py`（新增）
- `skills/public/ppt-master/workflows/stages/generate-audio.md`
- `skills/public/trainerai-imagine/SKILL.md`
- `skills/public/trainerai-imagine/scripts/main.ts`
- `skills/public/trainerai-imagine/scripts/main.test.ts`
- `skills/public/trainerai-imagine/scripts/providers/qihu360.ts`
- `skills/public/course-poster/SKILL.md`（只改指向 `canvas-design` 的描述）
- `frontend/src/components/workspace/workspace-home.tsx`
- `frontend/src/components/workspace/workflow-grid.tsx`
- `frontend/src/core/i18n/locales/zh-CN.ts`
- `frontend/src/core/i18n/locales/en-US.ts`
- `frontend/tests/unit/core/agents/new-chat.test.ts`
- `frontend/tests/unit/workspace/active-agents-sync.test.ts`
- `frontend/tests/unit/workspace/workspace-home.test.ts`
- `.env.example` 中过时的 `ZHIPU_API_KEY` 注释
- `docs/plans/2026-11-16-agent-media-channel-and-sandbox-secrets-plan.md`
- `docs/changelog/changelog-2026-11-16-agent-media-channel-and-sandbox-secrets.md`

Task 7 评审修复追加允许路径：

- `backend/packages/harness/deerflow/community/aio_sandbox/aio_sandbox.py`
- `backend/packages/harness/deerflow/skills/loader.py`
- `backend/tests/test_aio_sandbox_bash_env_output.py`
- `backend/tests/test_skills_loader.py`
- `backend/tests/test_visual_media_ratio_contracts.py`
- `backend/tests/test_seed_podcast_studio.py`
- `frontend/tests/unit/components/workspace/home-course-entry.dom.test.tsx`
- `frontend/tests/unit/components/workspace/workspace-home-i18n.dom.test.tsx`
- `skills/public/podcast-studio/SKILL.md`（只同步密钥纪律）
- `skills/public/trainerai-image-cards/**`、`skills/public/trainerai-comic/**`（只统一既定三档比例及契约）
- `docs/personal-server-deployment-guide.md`（只同步密钥注入口径）

`backend/packages/harness/deerflow/sandbox/tools.py` 本计划独占，不并行修改。

---

### Task 1：按 Agent 白名单注入两个允许的宿主密钥

**接口：** 保持 `_resolve_injected_secrets(runtime) -> dict[str, str] | None` 不变。active secrets → request secrets 的优先级不变；两者为空且 `runtime.config["metadata"]["available_skills"]` 是列表时，走新的受限宿主注入；没有该字段或为 `None` 时，才保留旧 AUTOBIND 行为。

- [x] **Step 1：先写失败测试。** 在 `test_bash_secret_injection.py` 覆盖：白名单技能只得到宿主已存在且属于两个允许名的值；`DATABASE_URL`、`ARK_API_KEY` 即使被声明也不注入；禁用、custom、legacy 技能不参与；空列表注入空；连续解析两个不同技能列表不串缓存；active/request 行为不变。`test_subagent_executor.py` 增加 `astream` 捕获，断言子代理 `run_config["metadata"]["available_skills"]` 等于其最终 `config.skills` 的排序列表，`None` 原样保留。
- [x] **Step 2：确认测试先失败。** 跑 `cd backend && uv run pytest tests/test_bash_secret_injection.py tests/test_subagent_executor.py -q`，失败原因应是新受限路径和子代理 metadata 尚不存在，旧用例不得被改成接受宽泛注入。
- [x] **Step 3：实现最小解析逻辑。** 在 `tools.py` 以 `load_skills(enabled_only=True)` 获取技能，再取 Agent 白名单与 `category == "public"` 的交集。声明名、两个允许名、宿主已有值三者取交集。按规范化技能列表做缓存 key，或复用现有 60 秒 TTL 的单项缓存但把当前技能列表纳入 key；不得让不同列表共用一份声明集合。密钥值仍在每次调用时读取，不缓存值。
- [x] **Step 4：传递子代理白名单。** 在 `executor.py` 构造 `run_config` 时写入上述 metadata；不改变父级与 `task_tool` 已有的白名单交集逻辑，不转发额外宿主环境。
- [x] **Step 5：验证工具接线。** 在现有 `TestBashToolWiring` 增加受限宿主注入用例，证明 `execute_command(env=...)` 只收到允许键，回显值仍打码。跑 `uv run pytest tests/test_bash_secret_injection.py tests/test_secret_context.py tests/test_subagent_executor.py -q`，通过后再进入 Task 2。

### Task 2：创意工作收成视觉生图

白名单顺序固定为：`trainerai-imagine`、`trainerai-diagram`、`trainerai-image-cards`、`trainerai-infographic`、`trainerai-comic`、`trainerai-article-visuals`、`course-poster`。`tool_groups` 保持 `web`、`file:read`、`file:write`、`bash`。

- [x] **Step 1：先改契约测试。** 将 `CREATIVE_STUDIO_SKILLS` 改为上述 7 项，说明从“19 个白名单”改为“7 个视觉技能”；跑 `cd backend && uv run pytest tests/test_seed_agents.py -q`，确认旧 config 使新断言失败。
- [x] **Step 2：收敛种子。** 修改 creative-studio 的 config 与 SOUL：删除研究写作、视频剪辑、`knowledge-card-auto-generator`、`canvas-design` 路线及对应兜底；保留视觉生图家族、课程海报和结构图。同步文件顶部和 SOUL 中不再成立的注释。
- [x] **Step 3：修正课程海报描述。** 只把“使用 canvas-design 技能的设计哲学方法”改为“按本技能写明的 Systemic Pulse 版式”。字体路径、模板、截图命令不动；字体仍从整树挂载的 canvas-design 目录读取，不把该技能加回白名单。
- [x] **Step 4：同步首页。** 从 creative-studio 关键词删除 `视频剪辑`、`剪辑视频`、`视频切片`、`提取精华`、`剪辑`、`知识萃取`、`经验提炼`、`知识提取`、`提炼知识`；保留视觉关键词。卡片描述改为“视觉生图：海报、信息图、图片卡片、漫画与封面”，标签改为“海报设计”“信息图”“图片卡片”。更新相关注释和 `workspace-home.test.ts`，剪辑/知识萃取断言为 `null`，知识卡片/海报仍路由 creative-studio。
- [x] **Step 5：更新新对话案例。** `knowledge-card` id 不变。中文标题“做一套图片卡片”，提示词“请把[主题]做成一套社交图片卡片，面向[受众]。先确认风格、布局和张数，再交付 PNG。”；英文标题 “Create an image-card series”，提示词 “Turn [topic] into a social image-card series for [audience]. Confirm the style, layout, and count first, then deliver PNG files.” 同步中英文 locale 和 `new-chat.test.ts`，其他案例 id 与内容不改。
- [x] **Step 6：跑定向回归。** `cd backend && uv run pytest tests/test_seed_agents.py -q`；`cd frontend && pnpm exec vitest run tests/unit/workspace/workspace-home.test.ts tests/unit/core/agents/new-chat.test.ts`，均通过后进入 Task 3。

### Task 3：PPT 默认字节旁白与 360 配图

**接口：** 保留 `notes_to_audio.py --provider trainerai`；`backend_trainerai.generate(text, output_path, *, api_key, voice_id)` 签名不变，新增字符串常量 `DEFAULT_SPEAKER = "zh_male_m191_uranus_bigtts"`，`output_extension()` 返回 `.mp3`。生图 backend 名为 `qihu360`，模块为 `backend_qihu360.py`，默认模型固定为上述 Seedream id。

- [x] **Step 1：先更新旧契约并写失败测试。** TTS 测试改为断言 openspeech URL、`X-Api-Key`、`X-Api-Resource-Id`、默认 speaker、MP3 和真实 SSE 错误；明确不读 `ZYUN_API_KEY`。生图测试断言只选 qihu360、只读 `QIHU_API_KEY`、body 使用 `extra_body` 且无顶层 `size`/`sequential_image_generation`。在 `test_seed_ppt_master.py`、`test_ppt_master_skill_vendored.py` 增加 SOUL、frontmatter 两个 required-secrets 与完整性门断言。
- [x] **Step 2：确认新测试失败。** 跑 `cd backend && uv run pytest tests/test_ppt_master_tts_trainerai.py tests/test_ppt_master_image_trainerai.py tests/test_seed_ppt_master.py tests/test_ppt_master_skill_vendored.py -q`，保留仍适用的 vendored/注册链旧断言，不整文件替换成只查字符串的弱测试。
- [x] **Step 3：替换 trainerai TTS 实现。** `tts_backends/backend_trainerai.py` 改为字节 seed-tts-2.0 的薄适配，请求字段对齐已验证的 `_doubao_tts.py`，只读 `VOLCENGINE_VOICE_ACCESS_KEY`；删除 360 stream_speech、数字音色和 WAV 合并逻辑。缺键、HTTP/SSE 失败、无音频均抛错，不重写另一套降级链。
- [x] **Step 4：接通默认旁白。** `notes_to_audio.py` 的 trainerai 分支在未传 voice 时使用 `DEFAULT_SPEAKER`，并显式传给 generate；env prefix 改为 `VOLCENGINE_`，删除 `QIHU_`/`ZYUN_`；manifest 记录 `seed-tts-2.0`，trainerai 仍不声称生成字级 SRT。`--list-voices` 只展示该固定音色。同步 `workflows/stages/generate-audio.md` 的 provider、speaker、MP3 与无字幕说明。
- [x] **Step 5：注册 360 生图 backend。** 新增 `backend_qihu360.py`，实现 `image_gen.py` 现有 `generate(...)` 接口、模型/尺寸校验、响应下载与 `SUPPORTS_REFERENCE_IMAGE=True`。比例按最近比例映射三档尺寸；本地参考图转 data URI。`image_gen.py` 注册 qihu360、加入 `QIHU_` env prefix，并更新参考图支持提示。`image_backends/backend_trainerai.py` 的候选只保留 qihu360/QIHU。
- [x] **Step 6：对齐 Agent 与 vendored 记录。** `ppt-master/SKILL.md` 增加两个 required-secrets，覆盖段改为字节 TTS 与 360 Seedream；`backend/seed/agents/ppt-master/SOUL.md` 改为 `IMAGE_BACKEND=qihu360` 和固定 speaker；`TRAINERAI_PATCHES.md` 登记本次 frontmatter 扩展、TTS 替换、360 backend 与文档变化。`.env.example` 只删除 ZHIPU 注释中“由 ppt-master 声明”的过时说法。
- [x] **Step 7：验证真实脚本接线。** 用 mock HTTP 跑默认 `notes_to_audio.py --provider trainerai` 和 `image_gen.py --backend qihu360`，断言请求与产物扩展名，不调用真实接口。重跑 Step 2 四个测试文件，并执行 `python3 skills/public/ppt-master/scripts/attribution_guard.py`，全部通过后进入 Task 4。

### Task 4：trainerai-imagine 默认走 360 Seedream

- [x] **Step 1：先写失败测试。** 更新 `main.test.ts`：模型 id 推断为 qihu360；QIHU+OPENAI+ARK 同时存在时默认仍为 qihu360；显式 `--provider` 不被覆盖；显式 qihu360+ref 不再报错；body 含 `extra_body.size`、`watermark=false`，无顶层 `size` 和 `sequential_image_generation`。
- [x] **Step 2：确认测试失败。** 跑 `cd skills/public/trainerai-imagine && npx tsx --test scripts/main.test.ts`。
- [x] **Step 3：接通 provider 与参考图。** `inferProviderFromModel` 先识别 `bytedance/...seedream...` 为 qihu360，再保留方舟 id 的 seedream 判断。`detectProvider` 在未显式指定 provider 且 QIHU 存在时优先 qihu360，参考图也一样；QIHU 缺席时保留现有其他 provider 逻辑。qihu360 加入 ref-capable 集合。
- [x] **Step 4：实现 360 请求合同。** `DEFAULT_MODEL` 改为上述模型；`buildGenerationsBody` 使用 `extra_body` 并映射三档尺寸。`generateImage` 在无 ref 时走 generations，有 ref 时读取本地图片为 data URI，单图传字符串、多图传数组并走 edits。mock fetch 覆盖两条 URL、单/多 ref、响应下载，不真实出网。
- [x] **Step 5：更新技能声明与默认文档。** required-secrets 只保留 `QIHU_API_KEY`，删除 OPENAI/AZURE/ARK 声明；描述、默认模型、Provider Selection 和环境变量表不再把 Gemini/其他 key 写成默认。其他显式 provider 代码保留，但不参与默认宿主自动注入。
- [x] **Step 6：重跑 Step 2 测试。** 全部通过后，核对 Task 1 的真实 frontmatter 注入表仍只有用户允许的两个密钥名。

### Task 5：课程助理直接退役

- [x] **Step 1：先改退役契约。** `test_seed_version_and_exact_agent_roster` 删除 course-assistant，版本改为 `2026-11-16.2`；删除仅服务课程助理的 `SEC_NULL_EXPLICIT_AGENTS` 测试和 `test_course_assistant_skill_whitelist.py`。前端 `active-agents-sync.test.ts` 删除课程助理兜底断言，保留“卡片集合 === seed 目录集合”。
- [x] **Step 2：删除种子和卡片。** 删除 `backend/seed/agents/course-assistant/`，从 `WORKFLOWS` 删除对应卡片；不新增 retired alias，不改 appearance/constants，不处理历史对话。
- [x] **Step 3：递增种子版本。** `SEED_VERSION = "2026-11-16.2"`，更新现行 roster 与本批注释；历史变更记录保留，但不再把 course-assistant 描述为现役。
- [x] **Step 4：验证。** 跑 `cd backend && uv run pytest tests/test_seed_agents.py -q` 与 `cd frontend && pnpm exec vitest run tests/unit/workspace/active-agents-sync.test.ts tests/unit/workspace/workspace-home.test.ts`。
- [x] **Step 5：仅本地旧运行时按需清理。** 若本机已有 `backend/.deer-flow/agents/course-assistant/`，停止相关服务后把整个目录移动到同级备份目录再验收；不写迁移代码、不删除 memory。全新部署不需要此步骤。

### Task 6：文档与本地验收

- [x] **Step 1：逐行核对“完成后的效果”。** 每行必须能指向已通过的测试或文件差异；没有把计划外文件改动当作完成条件。
- [x] **Step 2：写 changelog。** 新建 `docs/changelog/changelog-2026-11-16-agent-media-channel-and-sandbox-secrets.md`，含不超过 5 行 TL;DR 和“验收记录”，每条写“命令 → 实际输出关键行”，不记录密钥值。
- [x] **Step 3：跑本地完整门禁。** `cd backend && uv run pytest tests/ -q`；`cd frontend && pnpm test`；`pnpm exec playwright test tests/e2e/agent-new-chat-guide.spec.ts tests/e2e/home-course-entry.spec.ts`；回根目录跑 `make check`。长命令按项目规范后台执行，记录实际退出码。
- [x] **Step 4：收尾核对。** `python3 scripts/check_docs_convention.py`、`git diff --check` 通过；勾选本计划已完成步骤。`git status --short` 中不得出现 `.env`、根 `config.yaml`、compose、Docker 或计划外文件改动；creative-studio 的 seed config 是预期变更。无需部署或真实付费调用即可交付，若未做 live 调用，changelog 明确写“供应商请求为 mock 验收”。


### Task 7：独立评审发现的问题修复

- [x] 严格启用状态：restricted 注入启用 `strict_extensions_config`，配置损坏时零注入；正常无配置仍按公共技能默认启用。
- [x] AIO env 通道：显式返回非零退出码，复用 SDK 返回的 bash session ID 保持该通道 cwd，密钥只经 `env=` 传递。
- [x] PPT 媒体：无显式 backend 时默认 qihu360；TTS 要求成功 152、拒绝 151/提前 EOF/坏事件，启用官方语言检测，长文分片后由 ffmpeg 合成并原子发布。
- [x] 360 生图：Python/TS 均把 `output_format=png` 放在 `extra_body`；URL 与 b64 同时存在时 URL 优先，b64-only 保持兼容；非 Seedream、非单图和非 PNG 输出后缀在付费请求前失败。
- [x] 参数与比例：显式模型不被 QIHU 默认截获，显式 size 优先，卡片默认 1:1、漫画/竖版默认 9:16、横版 16:9，比例契约进入 backend pytest。
- [x] 测试与文档：隔离宿主 provider 环境，真实首页卡片纳入 seed 守卫，同步播客/部署文档、PPT CLI 帮助和 vendored 记录。
- [x] 真实字节 TTS 验收：一句 MP3 通过，未输出密钥值；具体探针结果写入 changelog。
- [x] 真实 360 一张 PNG 复验：2048×2048、3,527,027 bytes，URL 优先下载成功。
- [ ] 最终后端/前端门禁、静态检查、独立评审、提交及 PR 合入 main。
