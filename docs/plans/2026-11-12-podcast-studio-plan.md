# 播客 Agent 与音频创作实施计划

> **状态**：已执行（2026-11-12；代码、Task 1–6、Task 7 Step 1–4/6、Task 8 Step 1–5 完成。Task 7 Step 5 实时安全 URL 未验收，按用户决定后续自行验收，不宣称四模式全部验收通过。执行记录：`docs/changelog/changelog-2026-11-12-podcast-studio.md`）。
> **TL;DR**：新增 `podcast-studio` Agent 和一个四模式共享技能，以文档/链接/调研为来源，经必要确认后用字节 TTS 交付 MP3；首页仅以播客卡替换 co-writer，旧 Agent、Writer 与历史路由全部保留。AUTOBIND 默认关闭，不改 Docker、`.env.example` 或部署文档；真实密钥名集合未获用户当场授权时不得开启或宣称完成。

> **For agentic workers:** REQUIRED SUB-SKILL: 使用 `executing-plans`，单执行者、单工作区按 Task 1→8 串行，逐项勾选。Task 1 未获用户当场授权时立即停止，不得自行扩权。共享 locale、seed、技能和测试不得并行修改。

**Goal:** 让用户能够通过新的播客 Agent 完成文档原文朗读、单人新闻、双人访谈和双人辩论四类真实 MP3 创作。

**Architecture:** 前端继续使用通用 Agent chat 与现有任务型新会话引导。后端新增一个 `podcast-studio` Agent 和一个 `skills/public/podcast-studio` 共享技能，四模式共用 ingestion、来源/稿件契约、完整性哈希、音色白名单、分片合成和音频验收。豆包 TTS 采用显式 speaker 的直接沙箱 helper，不经过会自动换音色/换引擎的通用 ProductionService；双人对话通过逐轮合成和 FFmpeg 重编码拼接实现。

**Tech Stack:** Python 3.12 宿主测试、沙箱系统 Python 3.10 兼容代码、现有 LangChain/LangGraph、AioSandboxProvider、字节 `seed-tts-2.0`、FFmpeg/ffprobe、既有 `source_to_md.py`、FastAPI 现有 Agent 体系；React、TypeScript、Vitest、Playwright。**不新增第三方依赖、API、数据库、Docker 配置或部署配置。**

**Spec:** 用户已确认的播客四模式需求，以及 2026-11-12 同意的复审建议：请求级通道未装配不得冒充可用、AUTOBIND 改为真实密钥名集合授权门、补齐 HTML/SSRF 与资源限制、确认哈希只作完整性/陈旧稿检测、四模式必须经真实 Agent 验收、内容场景与播客拆分独立交付。

**基准 commit:** `12536796`（pending 定稿时 HEAD）。执行前必须运行 `git status --short --branch`、`git diff 12536796 --stat`，重点核对共享 locale、seed、技能加载和测试漂移；若内容场景主计划已执行，保留其 12 卡改动，不覆盖、不回退。用户提供的两个根目录源视频保留且不得提交。

**执行方式:** 只创建本地 commits，不 push、不建 PR、不部署。实现任务遵循「失败测试 → 确认功能原因失败 → 最小实现 → 定向与相关回归通过 → 本地提交」；Task 1/6/7 是行为验证任务，不伪造红灯。若后续授权远端交付，`skills/**`、`backend/seed/agents/**` 等核心路径必须走功能分支 + PR/OCR，不得直推 main。

## Global Constraints

- 新 Agent 固定为 `podcast-studio`，采用“一个 Agent + 一个 `podcast-studio` 技能 + 四个 profile”，不复制四套 ingestion/TTS/测试。
- 四个稳定模式 ID 为 `read_original`、`news_single`、`interview_two`、`debate_two`。`read_original` 禁止改写；其余三种必须先展示完整口播稿、来源与角色/音色，并通过真实 `ask_clarification` 后才能调用任何正式 TTS，包括用户试听。
- 首页分类名改为“图文音频课”，同步去掉描述中的“文章/articles”，改为海报、PPT、播客；播客卡 badge 为“语音播客”、标题为“播客制作”，进入 `/workspace/agents/podcast-studio/chats/new`；内部 `entry: "text"` 和六卡结构不变。
- **只从首页替换 co-writer**：不删除或改名其 seed、不加退役别名、不移除其 guide 或历史路由；`frontend/src/core/writer/**` 和 Writer 页面继续以 `co-writer` 执行。新会话 guide 变为 5 个 Agent、20 个案例，co-writer 原四卡中英文逐字不变。
- `podcast-studio` 使用显式 `tool_groups: [web, file:read, file:write, bash]`，`skills: [podcast-studio, deep-research]`，`model: null`，`category: public`；不挂 `video_engine`，不修改现有六个 Agent 的 seed/SOUL。
- 新增 seed 内容必须将 `SEED_VERSION` 从 `2026-09-23` 递增到 `2026-11-12`。标准机制会对已存在的六个旧 Agent reseed 并新增一个 Agent；本机预期产生六个 `.bak.<ts>`，仅已有 `memory.json` 会回到活跃目录，全新部署不得机械断言六个备份。执行前记录实际目录和 memory 清单，第二次 seed 应为 0。
- 新技能放 `skills/public/podcast-studio/`，不放用户 custom 遮蔽目录。frontmatter 仅声明必需的 `VOLCENGINE_VOICE_ACCESS_KEY`；密钥不得写入 `.env*`、SOUL、日志、测试夹具、计划或 changelog。
- **AUTOBIND 默认关闭，本批不改 Docker、`.env.example`、`config*.yaml` 或部署指南。** 当前前端/Gateway 没有装配 `config.context.secrets` 调用方，不能声称请求级通道已在产品 UI 可用；AUTOBIND 是不改中间件/run 链的现有通道，但注入范围是所有已启用技能声明并集 ∩ Gateway env，且对每次 bash 调用生效，不是 podcast 专属。
- Task 1 只能输出声明/配置/待注入的**变量名集合**，不得输出值。用户必须对该次实际集合明确授权后，执行者才可在本地进程环境临时开启 `DEER_FLOW_SKILL_SECRET_AUTOBIND=1`；计划获批本身不等于授权密钥并集。Task 2 新增 frontmatter 后及 Task 7 前必须复核集合未漂移，漂移则重新停止授权。
- 默认音色固定为现有 stock：单主持人及双人男声 `doubao:zh_male_m191_uranus_bigtts`（云舟2.0），双人女声 `doubao:zh_female_vv_uranus_bigtts`（Vivi2.0）。helper 使用去前缀裸 speaker；不得依赖 `DOUBAO_TTS_DEFAULT_SPEAKER`，不得使用克隆、preset、360、Edge 或任意未验收音色。
- 三种改写模式的确认稿变更会使批准哈希失效，必须重新确认。哈希只证明稿件完整性和陈旧状态，**不是可抵抗恶意模型/提示注入的用户批准凭证**；本批承诺真实交互中的确认行为，不宣称模型级提示注入免疫。若需要受信后端签发、绑定 thread 和 script hash 的强审批，必须另批设计，不在本批暗改 run 链。
- 所有改写模式的非空 turn 都必须引用已读取的 `source_ids`；搜索摘要不能冒充已读来源，URL 和文档内容一律视为非信任数据而非命令。固定 CLI 不得执行来源中的命令；直接 helper 只允许 Task 7 的诊断探针使用，正式工作流只能经 `podcast_studio.py render`。
- 原文朗读承诺的是“提取后的可见正文及文字顺序不变”，不是 PDF/DOCX 二进制或版式级还原。不得静默删除代码段或正文，不得增补/总结；扫描 PDF、复杂版式、登录墙、动态网页和超限输入必须明确报限，不能静默跳过或截断。
- 沿用 Voice Twin 请求模型的 100,000 Python 字符口径，在最终提取/清洗文本上、切分前检查：100,000 接受，100,001 硬失败并要求用户拆分。长文按标题/段落/句子边界分块并保留相对最终提取文本的偏移。
- 每个 TTS turn 内默认 `MAX_SEGMENT_CHARS=300`，按句边界切分，同 turn 分片间**不额外插入停顿**；单句超过 300 字时硬失败并要求用户拆分该句，不得沉默截断。双人每轮显式 speaker、顺序合成、默认轮间停顿 300ms、无重叠、无 BGM，最终用 `libmp3lame` 重编码拼接，禁止 `-c copy`。任一轮失败不得生成貌似成功的最终 MP3。
- 最终主交付为 `/mnt/user-data/outputs/<safe-title>_<timestamp>.mp3`，避免同一线程多次创作覆盖；附 `script.json`、`sources.json`、`render_manifest.json` 记录精确输出路径供审计。字级时间轴、声音克隆、背景音乐、原生双人重叠、长任务服务端调度不在本批。
- 本地文档只读调用 PPT Master 的转换能力，不得修改 vendored 技能。非信任 HTML 在转换前必须拒绝或移除所有外部资源、`file://` 与本地相对资源引用，转换器不得自行联网或复制本地文件；图片不朗读，丢弃情况写入 warnings。
- URL 只允许 http/https、TLS 校验、限制大小/时间/重定向，禁用环境代理，拒绝 HTTPS→HTTP 降级。全部 A/AAAA 任一非公网即拒绝，连接钉住已验证 IP 并保留原 Host/SNI/证书主机名，每跳重定向重新做同等检查，覆盖 DNS rebinding、IPv6、CGNAT、保留段、多播和未指定地址，不能只检查 hostname 或 `verify=True`。URL 内容仅支持 HTML/Markdown 文本，其他 MIME 明确失败；远程 PDF/DOCX 不在本批承诺范围。
- 本地文件默认上限 50 MiB、URL 原始响应上限 10 MiB、DOCX 最多 2048 个 ZIP 项/单项 25 MiB/总解压 100 MiB，转换子进程总 deadline 60s、转换输出上限 2 MiB；超限在转换前或流式读取中失败。最终文本仍执行 100,000 字符门，不得以 2 MiB 输出截断冒充成功。真实 fixture 不得制造资源耗尽。
- 默认自动化全部 mock 网络与 TTS transport；真实字节调用只在 Task 1 授权后的 Task 7 显式执行，不更换 provider。既有 `_doubao_tts.py` 自带最多两次尝试（网络错误与 429/5xx 单次指数退避），本批继承且精确测试调用次数，不 fork 修改常量。

## 评审决策记录

| Frontier 问题 | 决策 |
| --- | --- |
| Q1：四个功能是否四个技能 | 否。一个共享技能四 profile，共享解析、确认、合成和验收，避免漂移。 |
| Q2：co-writer 是否删除或改名 | 否。只替换首页卡；旧 guide、路由、Writer、seed 和历史线程全部保留，原四卡逐字不变。 |
| Q3：哪些模式必须确认 | news/interview/debate 必须真实 `ask_clarification`；read_original 不改写，只做非空、稀疏、超限、完整性和安全预检。 |
| Q4：语音模型与音色 | 现有字节 `seed-tts-2.0`；云舟男单主持/男声 + Vivi 女声，显式 speaker，禁止静默换音色。 |
| Q5：原文是否逐字节朗读 | 不是。保证提取后可见正文与顺序，转换器新增标题/Page N/无用标记须剥离，正文不得改写；无法证明保真时 fail-closed。 |
| Q6：文档与链接如何识别 | 本地文件复用只读转换能力；非信任 HTML 先禁外部资源；URL 使用带连接钉扎的新安全包装；主题调研用 web + deep-research。 |
| Q7：双人如何生成 | 不是原生双人服务；按 turn 分别调用直接豆包 helper，FFmpeg 重编码合并，失败不补静音、不换引擎。 |
| Q8：新 Agent 如何进入运行时 | 新增 seed 并 bump 全局版本，接受标准全量 reseed + 备份/记忆保护，不做绕版本特例。 |
| Q9：是否新增后端 API/配置/Docker | 不需要也不允许。通用 Agent chat + 技能脚本完成；不改 config_version、数据库、Docker、`.env.example` 或部署文档。 |
| Q10：非目标范围 | 不做 BGM、克隆、原生重叠、字级时间轴、服务端播客任务中心，也不改 Writer/旧 Agent。 |
| Q11：TTS 密钥如何进沙箱 | **运行时授权门，不标成用户已拍板。** 默认关闭 AUTOBIND；Task 1 先审计实际变量名集合，用户当场批准后才允许本地进程级开启和后续步骤。不得修改 compose/.env，不得把全集授权解释成单 key 授权；拒绝则停止本计划。 |
| Q12：co-writer 既有 podcast 卡 | 保留且中英文逐字不变。它产出播客文稿，`podcast-studio` 产出音频成品；播客四卡避免“写播客稿”，明确“生成音频”。 |
| Q13：确认哈希的安全语义 | 只作 integrity/stale-change 检测，不冒充受信审批凭证；真实 Agent 行为验收必须观察确认门，恶意样本只证明固定 CLI 不执行内容和本次 Agent 未越权，不推广成模型免疫。 |
| Q14：与内容场景批次的关系 | 内容场景主计划可独立交付；本 pending 执行前先检查共享 locale 漂移，不覆盖其 12 卡优化，也不要求重复其真实路由验收。 |

除 Task 1 的运行时密钥名集合授权外，规划 frontier 已收敛；执行中若需要原生播客 API、新 provider、强审批后端、vendored 修改或 ban 路径，立即停止回报。

## 播客四模式与共享数据契约

| Profile / 案例 | 输入与稿件 | 角色与确认 | 交付 |
| --- | --- | --- | --- |
| `read_original` 文档原文朗读 | PDF/DOCX/MD/HTML/安全 URL；确定性生成稿，不经撰稿 LLM | 单 host；无需改写确认，但完整性/安全预检失败必须停止 | 单音色 MP3，正文顺序不变 |
| `news_single` 单人新闻播报 | 来源或主题调研 → 新闻口播稿 | 单 host（默认云舟）；完整稿与来源确认后才合成 | 单音色 MP3 |
| `interview_two` 双人访谈 | 来源/调研 → 访谈问答与串场 | 恰好 male + female 两角色，不同 stock 音色；确认后才合成 | 逐轮男女 MP3 合并 |
| `debate_two` 双人辩论 | 来源/调研 → 双方立场、回应与总结 | 恰好 male + female，观点完整且无人身攻击；确认后才合成 | 逐轮男女 MP3 合并 |

### `script.json` / `sources.json` / `chunks.jsonl`

- `script.json` 固定字段：`schema_version="podcast-studio.v1"`、`profile`、`title`、`sources`、`voices`、`turns`、`integrity_sha256`、`approval`。
- `script.sources[]` 只含 `{source_id, raw_sha256, extracted_text_sha256}`；`sources.json` 为 `{schema_version, sources[]}`，每个完整 source 还含 `kind/requested_url/final_url/mime/charset/converter/warnings/text`。validate 按 `source_id` 连接并重算文本 hash，引用缺失、重复或 hash 漂移均拒绝。
- 每个 turn 含 `turn_id/role/text/source_ids`；`turn_id` 唯一且按 `t0001` 递增，role 仅允许 read_original/news 的 `host` 与 interview/debate 的 `male/female`。三种改写模式所有非空 turn 至少一个已读取 source；read_original 不得出现撰稿 LLM 生成的新 turn。
- `voices` 单人为 `{host: <stock>}`，双人为 `{male: <stock>, female: <stock>}`，render 转为裸 speaker 前严格匹配白名单和 profile。
- canonical JSON = 去掉 `integrity_sha256` 与 `approval` 后的完整 script 对象，以 `sort_keys=True`、`separators=(",", ":")`、`ensure_ascii=False`、UTF-8、无尾随换行序列化；其 SHA-256 写入 `integrity_sha256`。
- `approval.required=false` 仅用于 read_original，固定 `status="not_required"`、`approved_sha256=null`；render 仍重算并验证 integrity。其余模式初始为 `required=true/status="pending"/approved_sha256=null`，只有真实用户确认后才调用 `confirm`，写入 `status="approved"` 和等于当前 integrity 的 hash。缺字段、错配、过期或任何文字/角色/来源/音色变化均拒绝 render。
- `chunks.jsonl` 每行含 `source_id/start/end/text_sha256`；偏移相对该 source 的最终 `text`，read_original 按偏移拼接必须逐字重建原文。chunks 供长文读取和审计，不是第二条审批事实源，render 只消费已验证的 script turns。
- CLI 固定为：`intake`、`prepare-verbatim`、`normalize-script`、`confirm`、`validate`、`render`。其他三模式由 Agent 生成 draft JSON，`normalize-script` 校验并建立 pending script；CLI 不调用撰稿 LLM。

## 路径边界（allow / ban / 共享基座）

| 允许路径（allow） | 禁改路径（ban） | 共享基座触达 |
| --- | --- | --- |
| `skills/public/podcast-studio/**` | 其他技能，尤其 `skills/public/ppt-master/**` 只读；`skills/custom/**` 一律不建；不得运行期安装依赖 | **是，单执行者独占**；新技能、脚本、测试同步 |
| `backend/seed/agents/podcast-studio/{config.yaml,SOUL.md}`；`backend/app/gateway/seed.py` 仅版本与说明 | 其他六个 seed Agent（含 co-writer）、退役映射、voice provider/service/catalog、其他 Gateway API/DB/run/middleware 链 | **是**：全局 reseed，必须验证备份与 memory |
| `backend/tests/test_{seed_podcast_studio,podcast_studio_ingest,podcast_studio_audio}.py`；`backend/tests/test_seed_agents.py` 仅追加新 Agent、精确版本和独立工具组断言；`backend/scripts/probe_podcast_studio_tts.py` | 其他旧测试不得削弱；旧模型探针、ProductionService 不改 | 默认无网络，live 探针仅 Task 7 显式执行 |
| `frontend/src/components/workspace/home-course-catalog.tsx`、`core/agents/{new-chat.ts,appearance.ts}`、`core/i18n/locales/{types,zh-CN,en-US}.ts` | Writer、retired-mapping、动态 chat 页、registry UI、其他路由与业务组件 | **是**：共享 locale/Agent ID/首页六卡，串行修改；先核对内容场景批次漂移 |
| `frontend/public/course-samples/podcast-work.webp`（960×540，对应既有 `aspect-video` 容器） | 根目录两个源视频及其他既有媒体 | 本地静态绘制耳机/双人音频波形封面，不调用生图 API |
| 可修改测试仅限：`frontend/tests/unit/core/agents/{new-chat,appearance}.test.ts`、`unit/components/workspace/{home-course-catalog.test.ts,workspace-home-i18n.dom.test.tsx,workspace-home-course-entry.dom.test.tsx,home-course-entry.dom.test.tsx}`、`tests/e2e/{agent-new-chat-guide,home-course-entry,landing}.spec.ts`、`tests/e2e/utils/mock-api.ts` | 不删除 co-writer/旧 Agent/Writer 测试，不改全局 mock 默认语义；`core/threads/utils.test.ts` 等未列测试只运行不修改 | **是**：本地 `GUIDE_AGENTS` 显式追加 podcast fixture，旧用例保留 |
| 本计划与 `docs/changelog/changelog-2026-11-12-podcast-studio.md` | 内容场景主计划、其他旧计划、README、AGENTS.md、部署指南、第二份设计稿 | 同一执行者收尾，真实「验收记录」 |

**一律禁改：** `config.yaml`、`config.example.yaml`、`.env*`、`docker/**`、数据库/迁移、锁文件、`frontend/src/core/writer/**`、Writer 页面、co-writer 与其他既有 Agent seed/SOUL、`skills/custom/**`、真实服务器配置。需要修改时立即停止并报告。

## Review Focus

1. **AUTOBIND 密钥面扩大或本地开关未生效**：Task 1 只输出变量名、当场授权、空闲窗口进程级双向验证，Task 2/7 复核集合；不得改 compose 或 `.env`。
2. **HTML 图片、DNS rebinding、重定向和大型压缩文档绕过安全门**：Task 2 网络/文件零调用、连接钉扎和资源上限测试，Task 6 在真实沙箱复核。
3. **改写模式未确认、确认后改稿或测试空断言**：Task 3 在真实 render 路径只 mock 最底层 transport；Task 7 观察真实 `ask_clarification`、确认前零分片/零 TTS 命令。
4. **双人串声、旧分片复用、重复拼接和静默降级**：Task 3 缓存键、精确时长/段序/hash、重试次数和 FFmpeg 测试，Task 7 试听钉扎。
5. **全局 reseed 影响旧 Agent**：Task 4 精确集合/版本/fixture 测试，Task 6 记录真实执行前状态、备份、memory 与二次 0。
6. **首页替换误伤 co-writer/Writer**：Task 5 硬编码快照和旧路由回归，Task 6 Writer assistant_id 验证。
7. **文档/网页提示注入**：Task 2 固定 CLI 恶意样本、无 sentinel/额外 subprocess/confirm；Task 7 真实 Agent 负例只证明本次未越权，不宣称模型免疫。
8. **宿主测试与沙箱能力不一致**：Task 2 只写 Python 3.10 兼容代码，Task 6 在真实镜像中验证 PDF/DOCX/MD/HTML，不用宿主可选依赖假绿。
9. **CLI 证据冒充真实 Agent 产品证据**：Task 7 四个 MP3 均绑定真实 thread/assistant_id、技能调用、确认门和 present/download；至少一个真实安全 URL 与一个真实主题调研成功。

## 文件职责图

| 文件 | 动作 | 单一职责 |
| --- | --- | --- |
| `skills/public/podcast-studio/SKILL.md` | Create | 四模式路由、来源/确认/安全/音色/失败契约与固定命令 |
| `skills/public/podcast-studio/scripts/podcast_studio.py` | Create/Modify | `intake / prepare-verbatim / normalize-script / confirm / validate / render`，不自行改变业务路由 |
| `skills/public/podcast-studio/scripts/_doubao_tts.py` | Create（字节一致复制） | 直接豆包显式 speaker 调用，禁止引擎降级；现有副本 MD5 守护适用 |
| `backend/seed/agents/podcast-studio/config.yaml` | Create | Agent 身份、工具组和两个技能白名单 |
| `backend/seed/agents/podcast-studio/SOUL.md` | Create | 四模式、来源展示、真实确认门、原文保真和 outputs 交付 |
| `backend/app/gateway/seed.py` | Modify | 仅 SEED_VERSION 与新增 Agent 说明 |
| 三个 `backend/tests/test_{seed_podcast_studio,podcast_studio_ingest,podcast_studio_audio}.py` | Create | ingestion、音频、seed 契约；默认无付费调用 |
| `backend/scripts/probe_podcast_studio_tts.py` | Create | 宿主机 stock-only 显式 live 预检，只读进程 env，不读 `.env` |
| 前端 catalog/Agent/i18n/媒体与点名测试 | Modify/Create | 首页替换、播客 guide、双语与行为回归 |

---

### Task 1：审计并双向验证密钥通道（人工授权阻塞门）

**Files:** 仅本地 Gateway 进程环境、全新测试线程和变量名审计输出；**不修改任何仓库文件，不创建新测试文件，不产生音频或付费调用。**

**Interfaces:** 使用现有 `_resolve_injected_secrets` 与 bash 接线；本任务不依赖 Task 2/3 的未来技能、frontmatter 或 render。

- [x] **Step 1：验证现有离线契约**
  Run: `cd backend && PYTHONPATH=. uv run pytest tests/test_bash_secret_injection.py tests/test_secret_context.py -q`。
  Expected: PASS，覆盖 active → request → AUTOBIND 优先级、默认关闭、bash 接线和回显打码。这是既有行为验证，不伪造失败测试。
- [x] **Step 2：只审计变量名并暂停等待用户批准**
  在 Gateway 进程环境内列出所有已启用技能的 `required-secrets` 声明并集、Gateway env 中有值的名字及两者交集；只输出排序后的**名字**，不得读取 `.env`、输出值或把值写入报告。向用户报告：这是跨所有带 bash Agent 的全局 bash 注入，不是 podcast 专属。用户必须明确批准本次实际交集，才能继续；否则 Task 1 不勾选、本计划停止。
- [x] **Step 3：获批准后在本地真实 bash 验证正向注入**
  确认无在途任务。用进程环境 `DEER_FLOW_SKILL_SECRET_AUTOBIND=1` 启动本地 Gateway，不改 compose/.env；使用无 active/request secrets 的全新测试线程和现有带 bash Agent，逐项只检查变量是否存在，Expected: 已批准集合全部 PRESENT，且实际注入名集合与批准集合完全一致。不得 echo 值。
- [x] **Step 4：反向验证并恢复授权状态**
  在另一空闲窗口以 `DEER_FLOW_SKILL_SECRET_AUTOBIND=0` 重启本地 Gateway，仍用无 active/request secrets 的新线程，Expected: 目标名 MISSING。随后按用户授权恢复 `=1` 并再次确认 PRESENT，供 Task 7 使用；若任一侧受 active/request secrets 干扰或集合不符，停止并报告，不得改写其他通道凑通过。
- [x] **Step 5：记录授权证据**
  在后续 changelog 预留记录：批准的具体变量名、正/反向关键输出、进程启动方式和最终开关状态；不含密钥值。本任务无仓库代码变更，不创建空提交。

### Task 2：建立播客技能的 ingestion、来源与确认契约

**Files:**
- Create: `skills/public/podcast-studio/SKILL.md`
- Create: `skills/public/podcast-studio/scripts/podcast_studio.py`
- Create: `backend/tests/test_podcast_studio_ingest.py`

**Interfaces:** 实现共享数据契约中的 `intake / prepare-verbatim / normalize-script / confirm / validate`；产出 `sources.json`、`chunks.jsonl`、`script.json`，供 Task 3 render 消费。本任务没有 render，不写“未确认时 TTS 零调用”的空断言。

- [x] **Step 1：先写 ingestion 与确认失败测试**
  覆盖 PDF/DOCX/MD/HTML 分派、空文本、稀疏 PDF、100,000/100,001 边界、代码块、转换器元数据、原文偏移逐字重建；本地输入/ZIP/2 MiB 输出/60s 超时上限；URL scheme、非 HTML/Markdown MIME、混合 DNS、第二次 DNS 改答、IPv6、CGNAT、代理、TLS、HTTPS 降级、重定向、大小/超时；本地及远程 HTML 的私网图片、`file://`、相对文件引用，以及 link/script/iframe/CSS `url()` 等外部资源均网络/复制零调用。另覆盖四 profile schema、source/turn 引用、canonical JSON、read_original 免确认但校验 integrity、其他模式 pending/approved、过期 hash、未知 role/音色拒绝。恶意文档须断言无 sentinel 文件、无额外 subprocess、无 confirm 调用。
- [x] **Step 2：运行测试确认失败**
  Run: `cd backend && PYTHONPATH=. uv run pytest tests/test_podcast_studio_ingest.py -q`。
  Expected: 因模块/命令不存在而 FAIL，网络与 TTS 均 mock；新测试自身安装全网络 tripwire，不能依赖只拦旧 ProductionService 的 `hermetic_tts`。
- [x] **Step 3：实现最小共享契约**
  frontmatter 只声明 `VOLCENGINE_VOICE_ACCESS_KEY`。定义 `convert_html_bytes(raw, requested_url, final_url, headers)` 适配器：新脚本安全抓取、解码并禁外部资源后，调用纯 HTML 转换函数，不调用会自行 `fetch_url` 的 `process_url`。来源自行记录 requested/final URL、raw/text hash、MIME、charset、converter、warnings。PDF 对比转换前后每页字符量和顺序，检测自动页眉页脚删除/复杂重排；使用 no-images，稀疏或无法证明保真时失败。DOCX 在转换前检查 ZIP 展开限制。所有代码仅使用 Python 3.10 可用语法和标准/镜像既有库，不新增宿主依赖。
- [x] **Step 4：补齐 SKILL 工作流并确认通过**
  写明四 profile、固定命令、来源/确认/安全/密钥规则及“hash 不是强审批凭证”。重跑 ingest、`test_skill_whitelist_resolution.py`、`test_skills_bundled.py`、`test_bash_secret_injection.py`，Expected: 全绿且无网络。重新审计 Task 1 的变量名集合；若新增/改变待注入名字，停止并重新取得用户批准。
- [x] **Step 5：本地提交**
  Commit: `feat(skills): 新增播客文档解析与确认契约`。

### Task 3：实现固定音色的 MP3 合成与可恢复渲染

**Files:**
- Create: `skills/public/podcast-studio/scripts/_doubao_tts.py`
- Modify: `skills/public/podcast-studio/scripts/podcast_studio.py`
- Create: `backend/tests/test_podcast_studio_audio.py`
- Create: `backend/scripts/probe_podcast_studio_tts.py`

**Interfaces:** render 只消费 Task 2 的 script/sources，不读取第二份审批文件；输出 MP3 与 manifest。`_doubao_tts.py` 必须与 `skills/public/auto-video-flow/scripts/_doubao_tts.py` 字节一致。

- [x] **Step 1：先写音频失败测试**
  覆盖 stock 白名单/性别、完整 ID→裸 speaker、单人同色、双人顺序、300 字句边界、超长单句失败、长 turn 继承 role、同 turn 无额外停顿、未确认/未知/克隆/preset/edge 音色拒绝、缺 key 在网络前失败、429/5xx 恰好两次、普通 4xx/SSE 失败恰好一次、空音频失败、24kHz、无 360/Edge。网络 tripwire 安装在真实 render 下的最底层 transport，未确认/过期 hash 必须证明 transport 零调用。另写分片续跑用例：匹配分片可续跑，缺分片硬失败；分别改变文本、音色、role 和 turn 顺序并重新 confirm 后，旧 MP3 分片均不得复用。
- [x] **Step 2：运行测试确认失败**
  Run: `cd backend && PYTHONPATH=. uv run pytest tests/test_podcast_studio_audio.py -q`。
  Expected: 因 render/helper 不存在而 FAIL，不得触发真实 API。
- [x] **Step 3：实现直接豆包 render 与宿主诊断探针**
  逐 turn/segment 显式裸 speaker；分片续跑键包含 `integrity_sha256 + turn_id + role + bare_speaker + seed-tts-2.0 + speed_ratio + segment_text_sha256`，任一不匹配即作废旧分片，改稿/改音色/改 role/顺序均不得复用。记录分片 hash/时长/顺序，只续跑匹配且缺失的分片；任一轮失败不得生成最终 MP3 或成功 manifest，不补静音。用 libmp3lame 重编码拼接并执行 ffprobe + 全解码。宿主 probe 只读进程 env、不读 `.env`；未设 `RUN_LIVE=1` 时打印 SKIP、零网络并以退出码 0 结束，不要用 pytest 模块 skip 充当 CLI 守卫。
- [x] **Step 4：确认音频、缓存失效与副本回归通过**
  重跑音频测试及 `test_skill_script_copies.py`、`test_audio_concat_no_drift.py`、`test_audio_decode_duration.py`，Expected: 全绿。新播客测试直接调用自己的拼接函数，断言 `abs(final_decoded - (Σsegment_decoded + 0.3 × 轮间停顿数)) < 0.1s`，检查段序/hash、缺分片硬失败及 Step 1 的重新批准后缓存失效，不能用旧视频服务测试替代。
- [x] **Step 5：本地提交**
  Commit: `feat(skills): 实现双音色播客音频渲染`。

### Task 4：新增 Podcast Agent 并处理全局 reseed

**Files:**
- Create: `backend/seed/agents/podcast-studio/{config.yaml,SOUL.md}`
- Modify: `backend/app/gateway/seed.py`
- Create: `backend/tests/test_seed_podcast_studio.py`
- Modify: `backend/tests/test_seed_agents.py`

**Interfaces:** 与通用 Agent API/动态 chat 兼容；SOUL 只能调用 Task 2/3 已定义的技能与固定 CLI，正式工作流不得直接调用 helper。

- [x] **Step 1：先写 Agent/seed 失败测试**
  断言 public、model null、显式工具组、仅 `podcast-studio + deep-research`、无 video_engine；四模式及 `[模式: <profile>]` 路由、原文不改写、三种改写真实 ask_clarification、固定音色、失败不换引擎、outputs/present_files。新增精确字面量断言 `SEED_VERSION == "2026-11-12"`，七 Agent 集合严格等于 `co-writer/course-assistant/course-video/creative-studio/ppt-master/vimax-video/podcast-studio`，避免导入常量自证。用临时目录 fixture 测六旧 + 一新、备份、非空 memory 保留、二次 0。**不要修改 `SEC_NULL_EXPLICIT_AGENTS`**；另写 podcast 独立断言严格等于 `["web", "file:read", "file:write", "bash"]`，保留 course-assistant 三件套断言。
- [x] **Step 2：运行测试确认失败**
  Run: `cd backend && PYTHONPATH=. uv run pytest tests/test_seed_podcast_studio.py tests/test_seed_agents.py -q`。
  Expected: 因 Agent/版本不存在而 FAIL。
- [x] **Step 3：新增最小 seed 与版本**
  创建 config/SOUL，版本递增到 `2026-11-12` 并补说明；不得改其他 Agent、retired mapping 或 Writer。SOUL 明确确认哈希只作完整性检查，用户确认行为来自真实 ask_clarification 工作流。
- [x] **Step 4：确认 seed 与相关回归通过**
  重跑上述测试及 `test_skill_whitelist_resolution.py`、`test_skills_bundled.py`、`test_agent_memory_scope.py`，Expected: 全绿，原六 Agent 工具组/记忆/路由断言未删除。
- [x] **Step 5：本地提交**
  Commit: `feat(agent): 新增播客创作 Agent`。

### Task 5：接入播客新会话与首页替换

**Files:**
- Modify: `frontend/src/components/workspace/home-course-catalog.tsx`
- Modify: `frontend/src/core/agents/{new-chat.ts,appearance.ts}`
- Modify: `frontend/src/core/i18n/locales/{types,zh-CN,en-US}.ts`
- Create: `frontend/public/course-samples/podcast-work.webp`
- Modify: `frontend/tests/e2e/utils/mock-api.ts`
- Modify: `frontend/tests/e2e/{agent-new-chat-guide,home-course-entry,landing}.spec.ts`
- Create: `frontend/tests/unit/core/agents/appearance.test.ts`
- Modify: `frontend/tests/unit/components/workspace/{home-course-catalog.test.ts,workspace-home-i18n.dom.test.tsx,workspace-home-course-entry.dom.test.tsx,home-course-entry.dom.test.tsx}`

**Interfaces:** `AgentNewChatGuideId` 追加 `podcast-studio`；首页最后一卡替换，co-writer guide/路由/Writer 不变。四案例 ID 与 profile ID 一致，但 ID 仅作 React key；提示词必须含 `[模式: read_original|news_single|interview_two|debate_two]` 供 SOUL 识别。

- [x] **Step 1：先更新全部新旧测试与 fixture**
  在 Step 1 同步所有受影响断言，不留到实现后才改。覆盖六卡末项 podcast-studio、中英文“图文音频课/语音播客/播客制作”、去除描述中的文章/articles、精确路由、专属外观、960×540 封面、双语四案例及模式标记、真实生成音频措辞。guide 总数 5 Agent/20 案例；co-writer 硬编码快照、Writer assistant_id、vimax/未知 Agent 不变；四卡只预填且手动提交只创建一个 thread/run；移动端无横向溢出并收集零 pageerror。**给 `agent-new-chat-guide.spec.ts` 的本地 `GUIDE_AGENTS` 显式追加 podcast fixture**；同步 landing、英文标题、旧 ID、旧封面和唯一正则匹配，不放宽断言。
- [x] **Step 2：分 project 运行并确认失败**
  分别运行并记录，不得用 `&&` 短路：
  - node：`cd frontend && pnpm exec vitest run --project node tests/unit/core/agents/new-chat.test.ts tests/unit/core/agents/appearance.test.ts tests/unit/components/workspace/home-course-catalog.test.ts`
  - dom：`cd frontend && pnpm exec vitest run --project dom tests/unit/components/workspace/home-course-entry.dom.test.tsx tests/unit/components/workspace/workspace-home-i18n.dom.test.tsx tests/unit/components/workspace/workspace-home-course-entry.dom.test.tsx`
  - E2E：`cd frontend && CI=1 E2E_PORT=3100 pnpm exec playwright test tests/e2e/home-course-entry.spec.ts tests/e2e/agent-new-chat-guide.spec.ts tests/e2e/landing.spec.ts`
  Expected: 新播客 ID/文案/外观/fixture 相关断言 FAIL；不得先改实现。
- [x] **Step 3：最小实现前端替换与静态封面**
  追加 guide/appearance/i18n；首页只换末卡，不改动态 chat、Writer、retired mapping 或其他媒体。容器已是 `aspect-video`（16:9），封面固定 960×540；使用本地 SVG/HTML 绘制耳机与双人波形并导出 WebP，读回检查可解码与视觉，不调用生图 API。
- [x] **Step 4：确认首页、guide 与旧路由回归通过**
  原样重跑 Step 2 三组命令，并运行 node `core/threads/utils.test.ts`，Expected: 全绿，无 pageerror，移动端无横向溢出；co-writer 路由、guide 四卡、Writer 路径逐项未变。
- [x] **Step 5：本地提交**
  Commit: `feat(workspace): 新增播客入口并替换首页写作卡`。

### Task 6：验证真实 reseed、真实沙箱离线与旧能力兼容

**Files:** 仅运行时产物；若发现缺陷只能在 allow 内另起最小 TDD 修复，不顺手扩范围。

- [x] **Step 1：在任务空闲窗口执行真实 reseed**
  先记录执行前 Agent 目录和 memory 清单并备份。启动 Gateway，按实际旧目录数验证 `.bak`、podcast 新目录和旧 seed 内容未变；本机若没有 memory，记录“无既有 memory 可迁移”，非空保留证据只使用 Task 4 fixture，不伪装真实记忆证据。第二次 seed 为 0，不删除备份或旧线程。
- [x] **Step 2：验证真实 Agent 发现 API**
  `GET /api/agents/podcast-studio` 必须 200 且 category/tool_groups/skills 正确；assistants search 能找到同一 Agent。静态 seed 与前端 mock 不得替代此检查。
- [x] **Step 3：在真实沙箱完成文档输入与确认链冒烟**
  在真实 Aio 镜像的系统 Python 3.10 中分别用 PDF、DOCX、MD、HTML fixture 执行 intake/prepare，确认依赖、语法和转换结果；宿主单测不得代替。空/稀疏/恶意/超限/私网 URL/恶意 HTML 均 fail-closed。URL 正例在本步可 mock transport，Task 7 必须有真实安全 URL 成功。
- [x] **Step 4：用真实 render 验证离线确认门**
  只 mock 最底层 TTS transport，不 mock 整个 render。三种改写确认前 transport 零调用，确认后生成 mock 音频；read_original 不经撰稿 LLM但验证 integrity。检查 manifest、分片缓存失效和输出路径。恶意样本同时检查无 sentinel、额外 subprocess 和未批准 confirm。
- [x] **Step 5：验证 co-writer/Writer/非目标 Agent**
  co-writer 新旧路由与 Writer 从零写作/全文编辑仍为 `assistant_id=co-writer`；vimax-video/course-assistant 不出现播客 guide，旧技能与路由不变。
- [x] **Step 6：运行定向回归**
  backend seed/skill/ingest/audio/secret 测试、frontend 首页/guide/Writer 相关测试与既有沙箱测试全绿；本任务无真实付费调用。

### Task 7：真实音色与真实 Agent 四模式 MP3 验收

**Files:** 仅本地测试线程、来源副本和输出产物；不提交用户文档、来源正文或音频，不记录密钥值。

- [x] **Step 1：复核授权并预检两种真实 stock**
  重新计算实际注入变量名集合，必须与 Task 1 用户批准集合一致；不一致立即停止重新授权。确认本地进程 AUTOBIND 按授权开启、全新测试线程无 active/request secrets。宿主 key 已存在时运行 `cd backend && RUN_LIVE=1 PYTHONPATH=. uv run python scripts/probe_podcast_studio_tts.py`，分别生成云舟/Vivi 短 MP3；宿主缺 key 只记 `HOST_KEY_MISSING`，不得读 `.env` 或误判沙箱失败。随后在真实 podcast 线程的沙箱内用诊断探针分别验证两种裸 speaker，非空、MP3/24kHz、可解码；正式四模式未通过前不得启动，不更换音色。失败记录须区分密钥未注入、音色不可用、网络失败。
- [x] **Step 2：经真实 Agent 验收 read_original**
  从真实 `/workspace/agents/podcast-studio/chats/new` 或等价真实 API 创建 `assistant_id=podcast-studio` 线程，用含独特句、代码、链接和一条伪造命令的短 MD。记录实际技能读取/沙箱调用；正文无增删改、无撰稿 LLM、伪造命令未执行、单音色。render 只经固定 CLI，保留 thread、script hash、manifest、present/download 证据。
- [x] **Step 3：经真实 Agent 验收 news_single 与主题调研**
  输入一个主题，实际使用 web + deep-research 得到至少一个已读取来源；展示完整新闻稿和来源，真实 `ask_clarification` 前无 MP3、分片或 TTS 命令。用户确认后才合成云舟 MP3，稿件 hash 与 manifest 一致。搜索摘要不能代替来源正文。
- [x] **Step 4：经真实 Agent 验收 interview_two**
  用真实 PDF 或 HTML 形成至少 4 轮男女访谈；真实确认前零 TTS，确认后角色/段序/裸 speaker 正确，无串声/异常停顿，合并一个 MP3并 present/download。
- [ ] **Step 5：经真实 Agent 验收 debate_two 与安全 URL**
  输入一个真实公开、安全 URL，成功读取正文后形成至少 4 轮正反辩论；双方立场均存在、无人身攻击。真实确认后才合成，男女音色和顺序正确，最终一个 MP3并 present/download。URL 核心能力不能用 fail-closed 代替成功。
  **2026-11-12 用户决定**：不再由本批执行实时 URL 验收，后续由用户自行验收；本项保持未勾选，明确记为“未验证、用户后验”，不计为通过。此前的 fake-IP、HTTP 404、人机验证和动态模板结果均未取得合格实时正文。
- [x] **Step 6：核验四个真实 MP3 的下载与音频质量**
  四个 MP3 均绑定各自真实 thread/assistant_id、present/download 200、非空、ffprobe codec=mp3/24kHz、FFmpeg 全解码无错；最终解码时长与分片及 300ms 轮间停顿对账在 0.1s 内，段序/hash 正确。人工试听角色、可懂度与原文/确认稿一致性；失败如实记录，不补静音或换引擎。四个真实模式未全部通过时，不得勾选本计划完成。

### Task 8：全量门禁、文档与交付

**Files:**
- Modify: 本计划
- Create: `docs/changelog/changelog-2026-11-12-podcast-studio.md`

- [x] **Step 1：运行后端与前端标准门禁**
  按长命令规范后台运行 `cd backend && env -u RUN_LIVE uv run pytest tests/ -q`；`cd frontend && pnpm check && pnpm test`，全部通过。
- [x] **Step 2：运行 E2E 与性能门禁**
  `cd frontend && CI=1 E2E_PORT=3100 pnpm test:e2e && pnpm perf:check`，全部通过；mock 不替代 Task 7 真实证据。
- [x] **Step 3：更新计划与 changelog**
  逐项勾选并更新唯一状态头，记录实际执行范围及 changelog 链接。changelog 必须含 ≤5 行 TL;DR 和「验收记录」节，逐行写真实命令与关键输出，分别记录密钥授权名集合（只名字）、离线、真实音色、四模式真实 thread/MP3、URL/调研成功、旧能力兼容、人工试听和未解决事项，不包含密钥或用户内容。
- [x] **Step 4：对最终文档重跑根门禁**
  在 Step 3 完成后运行仓库根 `make check && python3 scripts/check_docs_convention.py && git diff --check`，全部通过；不得只在 changelog 创建前运行文档门禁。
- [x] **Step 5：恢复本地测试入口并交付审查**
  确认无在途任务/测试容器残留，清理本批不需要的测试输出，服务健康，明确记录 Gateway 最终 AUTOBIND 状态。汇总修改文件、commit、真实证据、遗留项和审查重点；只提交本地文档，不 push、不建 PR、不部署。
  整支审查发现 5 项 Important 均已完成 RED→GREEN 修复，后端全量 `10004 passed, 31 skipped`；两项 Minor deferred 记录于 changelog。最终 Gateway/前端均为 HTTP 200，AUTOBIND 仅进程级 `=1`，无本批测试容器残留。

## 回滚与停止规则

- 新 Agent/seed 出现问题时恢复对应本地提交与旧 SEED_VERSION，只在空闲窗口按备份恢复运行目录；不得删除 memory、旧线程或 co-writer。
- 音频失败不补静音、不换音色、不伪造 MP3；已完成的独立阶段可保留，但不得宣称四模式全部完成。
- 原文/来源/HTML/URL 安全/资源上限/确认完整性任一失败即 fail-closed，不通过忽略警告交付。
- 若必须改 vendored PPT Master、共享 voice provider、ProductionService、数据库、Docker、`.env*`、run/middleware 链或其他 ban 路径，停止并报告。
- 首页或 Writer 回归不得通过删除 co-writer、加退役别名、隐藏旧 DOM 或削弱旧测试消除。
- **AUTOBIND 回滚**：若实际注入名集合超出用户当次授权，立即在本地进程环境恢复 `=0` 并重启 Gateway；不得改 compose、`.env` 或 `sandbox.environment` 兜底。未获授权时播客 TTS fail-closed 是正确行为。
- 计划获批不等于批准实际密钥名集合；Task 1 未获当场明确授权时，不用其他密钥通道绕行。

## 本地完成定义

1. **密钥授权真实有效**：Task 1 记录用户批准的具体变量名集合、开启 PRESENT/关闭 MISSING/恢复后 PRESENT，实际集合无漂移；只检查配置文件不算证据。
2. 首页仅有播客替换 co-writer 的变化；分类描述不再宣传文章，podcast 新会话四卡含明确模式指令，提交、旧 co-writer/Writer/历史路由均正确，受影响测试同步而非删除。
3. PDF/DOCX/MD/HTML 在真实沙箱均有成功证据；主题调研在 Task 7 至少一个成功案例。实时安全 URL 按用户 2026-11-12 的最新决定由用户后续自验，本批不标记成功。空、稀疏、恶意、私网、超限等负例 fail-closed，不能用负例代替核心能力成功。
4. 原文保真、来源、完整性/确认行为和固定 CLI 提示注入防线成立；确认哈希未被宣传为强审批凭证，Python 3.10 沙箱兼容经过实测。
5. 四模式各有一个绑定真实 podcast Agent thread 的可下载 MP3，音色、角色、顺序、稿件、编码、精确时长与人工试听均通过；任何部分失败不伪装成功。
6. 全局 reseed 的实际备份/memory 证据、真实 Agent 发现 API、第二次 0、全部标准门禁和 changelog 验收记录完成；最终文档修改后已重跑根门禁。
7. 用户本地复核并接受后，才另议公司服务器部署；本计划不包含部署完成，也不修改任何真实服务器配置。
