# 服务器实测问题修复与 TTS 单通道收敛实施计划

> **状态**：未执行（2026-11-22 定稿；同日按 Cursor 复核修订：P12 整项退出，合并固定为四个主 PR，P6/P7 未取证不开 PR；不授权自动部署、付费调用或修改服务器配置）。
> **TL;DR**：四个主 PR 分别修复停止清草稿、播客 URL 抓取、字节 TTS 单通道，以及默认徽标、用量标题、合成文案和 live 断言；P12、Writer、P4、P8、P15 退出。P6/P7 证据不足则记录阻塞且不开 PR；不自动部署，不修改服务器配置。

> **For agentic workers：** 使用 `executing-plans`，单执行者按 Task 0→7 串行执行并逐项勾选；每项先写失败测试、确认功能原因失败、最小实现、定向回归，再提交。主批只允许本计划定义的四个 PR，P6/P7 各自取证后才能另开独立 PR；不在当前分叉工作区直接实施。

**Goal：** 围绕首页六卡及其共享链路，修复已确认的问题，并将在线 TTS 收敛为目录现有的 4 个中文豆包音色和既有声音克隆，彻底移除 Edge 与 360 TTS。

**Architecture：** 保留现有 Next.js/FastAPI/Agent 架构。停止操作在 InputBox 业务包装层捕获，不能进入 PromptInput 的成功清空逻辑；播客继续使用原安全抓取器；TTS 以共享目录和统一 `synthesize()` 为主收口点，旧 Edge/360 输入在网络调用前响亮失败，绝不静默换声。

**Tech Stack：** Next.js、React、TypeScript、TanStack Query、Vitest、Playwright；FastAPI、Pydantic、SQLAlchemy、pytest；既有 Doubao/OpenSpeech SSE、FFmpeg；不引入新框架、新 TTS 供应商或新语言音色。

**Spec / 证据来源：**
- `docs/2026-11-22-bug-verification-handoff.md` 与 `docs/2026-11-22-full-module-test-report.md`（原始实测上下文）。
- 用户产品决策：Writer 与六卡无关则不处理；完全移除 Edge/360 TTS；模型价格后续由管理端手工维护；普通用户渠道不开放。
- 用户确认的 Cursor 复核结论：P12 退出、四个主 PR、L1/L2 仅属部署验收、P6/P7 未取证不开 PR。
- 本地核实：Edge TTS 本机同样 WebSocket 403；播客受控 HTTP 服务复现 `socket.SocketIO` 无 `settimeout`，运行时最小补丁验证可完成读取；既有 Edge/播客测试 73 条通过但未覆盖真实故障形态。

**基准 commit：** `origin/main@0e84b148`。执行前先 `git fetch origin`，从最新 `origin/main` 建分支并运行 `git diff 0e84b148 --stat`；相关文件漂移时先更新计划映射，不按旧行号覆盖。

## 一、评审决策记录

| # | 已定结论 |
|---|---|
| D1 | 产品核心是首页六卡；P2、P14、独立 Writer 业务及 Writer 分享页均不处理，代码和测试不动。 |
| D2 | **P12 整项退出**：账户设置和登录失败文案不改，Writer 分享页英文也不改。 |
| D3 | P4 仅由运营后续在管理端手工维护；不做自动计价改造或上线门禁。P8 成本提醒和 P15 普通用户渠道均不实施。 |
| D4 | 标准档默认仍是 2026-11-20 修订的 **Claude Sonnet**；闪速默认 Qwen Flash，Ultra 默认 Qwen4 Max。DeepSeek 仅手动可选，原核实清单“DeepSeek/Qwen4 Max 出现默认徽标才算修复”的判定作废。 |
| D5 | 在线配音只保留字节直连与既有声音克隆；新建入口只保留共享目录现有的 **4 个 `zh-CN` 豆包音色**，不再用 Edge 为英、日、韩、法、西、德补位。 |
| D6 | 主批固定为 PR 1=P1、PR 2=P11、PR 3=Task 3+4、PR 4=P5/P9/P10/P13；禁止收成一个 PR。P6/P7 未取证不创建 PR。 |
| D7 | PR 3 是同批运行、依赖、配置和镜像切换，必须同一个 PR 以便整体回滚；只拆三笔主题提交，不掺其他问题。 |
| D8 | 服务器 L1/L2 不是 GitHub CI，也不是主 PR 合并门禁；合并看对应单元测试、mock E2E 和 CI，L1/L2 只放部署验收。 |
| D9 | PR 4 不改 `config.example.yaml` 或真实 config；前端展示时剥离 `display_name` 末尾手写“默认”，再按 `mode_defaults` 画唯一徽标。 |
| D10 | PPT Master 的其他后端仍复用 `backend_edge` 字幕函数；先迁移共用函数到中性模块，再删除 Edge 合成、分发和依赖，不能整文件直接删除。 |
| D11 | 基准 `config.example.yaml` 的 `config_version` 为 **32**；只有实际删除 `qihu_tts`/`edge_tts` 配置结构时才加 1。服务器配置与示例不是同一版本谱系，部署按字段合并，不按版本号大小覆盖。 |
| D12 | P9 同时修 Agent 和视频用量：缺标题返回 `null`，保留 Agent 首条消息回退，前端统一显示“标题生成中”。 |
| D13 | P6 原 run 不能免费恢复且无新付费授权时，以“外部阻塞、未重跑、未开 PR”完成本任务；P7 同样允许以“证据不足、未开 PR”收口。 |

## 二、范围与 Review Focus

### 本批执行

- **P1**：停止生成保留草稿、附件和 localStorage。
- **P3**：移除 Edge/360 TTS，只留 4 个中文豆包音色与既有克隆。
- **P5/P9/P10/P13**：动态默认徽标、用量标题占位、首渲文案、live 断言。
- **P11**：修复播客真实 socket 超时处理，完整保留 SSRF 安全链。
- **P6/P7**：先取证；具备根因和可实施修复后才分别开条件 PR。

### 明确不做

- P2、P4 自动改造、P8、P12、P14、P15，以及 Writer 分享页英文。
- 不修改 360 的 LLM、生图、AI 视频能力，不删除共享 `QIHU_API_KEY`；只移除 **360 TTS**。
- 不手改 `frontend/src/components/ui/**`、`frontend/src/components/ai-elements/**`，不读取或提交真实 `.env/config.yaml`、服务器数据库、密钥和用户数据。
- 未获付费授权不清空 4 个中文入口、不新开语言、不重跑 PPT 长任务；真实字节配音记 `LIVE_NOT_VERIFIED` 即可。

### Review Focus

1. **只 preventDefault 未阻断传播**：P1 必须在捕获阶段同时阻止默认行为和继续传播，否则事件仍会进入 PromptInput 清空逻辑。
2. **先删 streaming 早退再补拦截**：可能把停止操作变成发送草稿；必须先证明捕获拦截，再收敛旧分支。
3. **TTS 只删前端入口**：目录、统一入口、实际调用方都要拒绝旧音色，且拒绝发生在任何网络/付费调用前。
4. **旧任务静默换声**：历史音频和用量保留，退役音色重配/重渲只提示改选，不自动替换。
5. **PPT Master 误删共用字幕函数**：迁移后必须验证 CosyVoice、ElevenLabs、MiniMax 的 import 与既有字幕测试。
6. **为修播客绕过安全机制**：不得关闭非公网 IP 拒绝、重定向逐跳复查、Host/SNI 钉住或 HTML 消毒。
7. **服务器配置漂移反向污染代码**：P5 若服务器把 DeepSeek 配成标准默认，只记配置漂移；L1/L2 因部署漂移失败也只记录，不回头改已合入 PR。

## 三、路径边界与合并单位

| PR | 允许路径（allow） | 禁改路径（ban） | 合并条件 |
|---|---|---|---|
| **PR 1 / P1** | `frontend/src/components/workspace/input-box.tsx`；对应新增/既有 InputBox 测试 | registry UI/AI Elements、其他产品文件、P5 | 定向测试 + 相关 InputBox 回归 + 对应 CI 一轮通过；独立合并。 |
| **PR 2 / P11** | `skills/public/podcast-studio/**`、`backend/tests/test_podcast_studio_ingest.py`、必要的对应部署文档 | 其他技能、关闭任何安全校验 | 安全测试随 PR 提交；`skills/` 核心路径必须 OCR；CI 无 Critical/Important。 |
| **PR 3 / Task 3+4** | 点名的 backend voice_twin/video_gen/shared voice catalog、相关 routers/config/tests、音色相关 frontend、`skills/**` 的 Edge/360 TTS 引用、`docker/sandbox/Dockerfile`、`backend/pyproject.toml`、`backend/uv.lock`、`.env.example`、`config.example.yaml`、相关 AGENTS/常青 docs | 360 非 TTS 能力、Writer、P1/P5/P6/P7/P13、Remotion 模板、无关 provider 包裹链、真实配置与密钥 | **唯一大 PR，同一 PR 整体回滚**；三笔主题提交；预计 1–2 轮 OCR，范围不得随评审扩大。 |
| **PR 4 / P5/P9/P10/P13** | `input-box.tsx`、`core/models/**`、personal_usage 的 agent/video/types 与 usage 前端、video status label、live tests/runbook、对应测试 | **PR 1 合入前不得开始**；P12、Writer、`settings/account-settings-page.tsx`、`app/(auth)/login/page.tsx`、`config.example.yaml`、真实 config | 对应单元测试、mock E2E、CI 通过；不含服务器 L1/L2 门禁。 |
| **P6/P7 条件 PR** | 证据确认后分别点名最小实现与测试文件 | 未取证改实现、整体重写虚拟列表、无关中间件/provider 改动 | 各自根因/复现证据先行；证据不足不开 PR，只更新本计划阻塞状态。 |

PR 3 三笔提交固定为：①目录和统一入口拒绝非字节音色；②调用方移除降级并清理技能/依赖/配置/镜像；③前端音色目录和旧值停用态。三笔可在本地逐笔验收，但只能作为一个 PR 评审和合入。

## 四、实施任务

### Task 0：冻结基线与 P6/P7 取证

- [ ] 确认最新 `origin/main`、工作区状态和漂移面；从最新 main 建分支，不带入其他未提交文件。
- [ ] P6 收集目标线程脱敏 run 记录：`run_id/status/error/stop_reason/on_disconnect`、最后事件/工具调用、checkpoint/goal 状态和 gateway/沙箱日志；不得只凭“未完成”推断根因。
- [ ] 核对实际前端 `streamResumable=true` 时 SDK 默认 `on_disconnect=continue`，不把后端默认 `cancel` 误当实际请求。
- [ ] P6 写明终止组件、直接原因、现场是否可免费恢复、最小修复面；若不能免费恢复且无新付费授权，记录“外部阻塞、未重跑、未开 PR”并停止该任务。
- [ ] P7 先建立真实浏览器复现证据：至少 60 个不同高度消息组、大量 Token 行、历史 prepend，记录首可见 key、scrollTop、scrollHeight 与已加载 run 数；未稳定复现则记录“证据不足、未开 PR”。
- [ ] 复核 `/api/models` 的默认配置；服务器若将 DeepSeek 配成标准默认，只登记配置漂移，PR 4 仍按 Claude Sonnet 编写测试。

### Task 1 / PR 1：停止生成保留草稿（P1）

**Files：** 仅 `frontend/src/components/workspace/input-box.tsx` 与对应测试；禁止修改 `prompt-input.tsx`。

- [ ] 写失败测试：streaming 时输入草稿并添加 mock 附件，分别通过停止按钮和 Enter 触发；两者均 `onStop` 一次、`onSubmit` 零次，文本与附件不变，超过 400ms 后 localStorage 草稿仍在。
- [ ] 保留非 streaming 正常发送仍清空文本/附件的对照；运行并确认旧实现因停止后清空而失败。
- [ ] 在 InputBox 外层使用 `onSubmitCapture` 拦截 streaming 时的表单 submit：调用 `onStop`，执行 `preventDefault` 并阻止事件继续传播，确保按钮和 Enter 都不能进入 PromptInput 的 `clear()`。
- [ ] 只有捕获测试证明两条路径均被拦截后，才移除旧 `handleSubmit` 中会形成 resolved Promise 的停止分支；禁止抛异常冒充停止，禁止无拦截直接删除早退。
- [ ] 跑新测试、相关 InputBox 回归和 `pnpm check`；行为验收为“长回复期间边等边写，停止后文字与附件仍在”。
- [ ] 提交 `fix(chat): 停止生成时保留用户草稿`，只开 PR 1，一轮 CI 通过后合入。

### Task 2 / PR 2：播客 URL 抓取（P11）

**Files：** `skills/public/podcast-studio/scripts/podcast_studio.py`、`backend/tests/test_podcast_studio_ingest.py`、必要的 SKILL 行为说明。

- [ ] 新增真实响应形态失败测试：`response.fp.raw` 是 **`socket.SocketIO`**（不是 `http.client.SocketIO`），其实际底层 socket 位于实例属性 `raw._sock`；旧实现必须因 `raw.settimeout` 失败。
- [ ] 最小修复 `_set_socket_timeout`：活动连接使用连接 socket；响应体仍开启时使用 `raw._sock`；EOF 且 `fp is None` 时安全结束；响应体仍开启却找不到活动 socket 时响亮失败。
- [ ] 保留并运行非公网 IP、混合 DNS、重定向逐跳复查、HTTPS 降级拒绝、Host/SNI/TLS、大小/超时和 HTML 消毒测试，一项不得删除。
- [ ] 新增 `Connection: close`、分块响应、EOF 和重定向后 EOF 回归；在本机 Python 3.12 与沙箱 Python 3.10 验证相同语义。
- [ ] 用本地受控 HTTP 服务跑完整 `fetch_url()`，再在实际沙箱执行一次公开 URL intake；断言 `requested_url/final_url/mime` 正确且安全链真实经过。
- [ ] 安全测试随 PR 2 提交，完成核心路径 OCR 和 CI 后合入。

### Task 3 / PR 3-A：TTS 目录、统一入口和调用方只走字节（P3 运行批）

**产品结果：** 基准目录约 35 条，字节目录只有以下 4 条且均为 `zh-CN`；新建入口只保留它们，不新增其他语言：

- `doubao:zh_female_vv_uranus_bigtts`（Vivi2.0）
- `doubao:zh_female_yingyujiaoxue_uranus_bigtts`（Tina2.0）
- `doubao:zh_male_m191_uranus_bigtts`（云舟2.0）
- `doubao:zh_male_liufei_uranus_bigtts`（刘飞2.0）

**Files：** shared voice catalog、voice_twin service/production/config/schema、video_gen orchestration 与实际消费方、音色相关前端和测试；只改完成单通道所需文件。

- [ ] 第一笔提交先写目录/入口失败测试：两个音色 API 和新建 UI 只剩上述 4 条；`preset:*`、`edge:*` 和裸 Edge ShortName 在任何网络调用前得到可读错误。
- [ ] 在 shared catalog 与统一 `synthesize()` 实施拒绝，保留 `doubao:*`、既有克隆、字节重试、字级时间戳、情绪、section_id、缓存和 SRT 行为。
- [ ] 第二笔提交移除 doubao→qihu→edge、qihu→edge 和仅为旧降级存在的策略；字节 429/5xx/传输错误重试耗尽后失败，不换声、不伪装降级成功。
- [ ] 克隆上传、训练、试听、合成保持现状，克隆失败仍不降级。
- [ ] 默认音色只从上述目录选择；英、日、韩、法、西、德不再保留 Edge 默认或新建入口。未获付费授权只记 `LIVE_NOT_VERIFIED`，不因此清空中文入口或新开语言。
- [ ] 历史音频和用量不迁移；旧任务含退役音色时，试听、重配、重生或重渲在提交前提示“原音色已停用，请改选字节直连音色”。
- [ ] 第三笔提交清理前端 provider 类型、分组、筛选和残留值禁用态；旧任务仍可见原值与停用原因，不能自动变成另一音色。
- [ ] 运行相关 backend/frontend 测试和 mock E2E，证明 360 LLM/生图/视频无行为 diff。

### Task 4 / PR 3-B：技能、依赖、配置和镜像清理（与 Task 3 同一 PR）

**Files：** Edge/Qihu 客户端及专属测试、`backend/pyproject.toml`、`backend/uv.lock`、`.env.example`、`config.example.yaml`、相关 skills、`docker/sandbox/Dockerfile`、`backend/AGENTS.md` 和常青模块文档。

- [ ] 全仓分类检索 `edge_tts`、`edge-tts`、`edge:`、`preset:`、Qihu TTS 和旧 TTS 开关；历史报告/changelog 不改，现行运行代码与常青文档不得继续承诺旧通道。
- [ ] PPT Master 先把 `DEFAULT_SUBTITLE_MAX_CHARS`、`format_word_timed_srt` 及其专用辅助函数迁到中性 `tts_backends/subtitle_timing.py`，更新 CosyVoice、ElevenLabs、MiniMax imports 并跑字幕/import 回归。
- [ ] 上述回归通过后，才删除 `notes_to_audio.py` 的 Edge import、`backend_edge.generate` 分发、CLI 列表/默认引用和 Edge 合成 backend/依赖；禁止直接整文件删除导致其他后端坏 import。
- [ ] 所有 vendored 改动登记 `TRAINERAI_PATCHES.md`；保留其他后端现有非 Edge 能力和字节 `backend_trainerai`，不顺手重构。
- [ ] 删除 backend/sandbox 的 Edge 依赖、import、自检与旧重试帮助器；删除 360 TTS 配置/价格解析，保留 Qihu 其他媒体配置与价格。
- [ ] 基准 `config_version=32`。仅当真正删除 `qihu_tts`/`edge_tts` 配置结构时同步示例并升到 33；若只删注释/死配置引用则不升。
- [ ] 部署配置按字段合并，不比较服务器与示例的版本号大小，不自动覆盖真实 config。
- [ ] 更新 `backend/AGENTS.md` 为“字节直连，失败响亮；克隆不降级”，同步其他现行注释；历史文档不改。
- [ ] 重建本地 sandbox 镜像，证明 Edge 包已移除且字节脚本/FFmpeg 可用；Task 3+4 最终只开 PR 3，三笔提交整体 OCR、CI 和合入。

### Task 5 / PR 4：P5、P9、P10、P13（PR 1 合入后开始）

**Files：** InputBox/core models、personal_usage agent/video/types 与 usage 前端、video status label、live tests/runbook及对应测试。禁止修改 P12 文件和任何 config。

- [ ] P5 单测注入 `mode_defaults={flash: qwen3.8-flash, standard: claude-sonnet-5-5, ultra: qwen4-max}`；选中按钮和列表按当前档画唯一默认徽标，DeepSeek 不标默认。
- [ ] 展示前仅剥离 `display_name` **末尾**的全角/半角“（默认）/(默认)”后缀，再添加动态徽标，避免两枚“默认”；不改 `config.example.yaml` 或真实 config，不把名称中间包含 default 的模型误删字。
- [ ] P9 先覆盖 `display_name=None`，并将 `display_name == thread_id` 的占位形态也按缺标题处理；随后把 `agent.py` 标题表达式的末级 `thread.thread_id` 回退改为 `None`，保留 `latest_run.first_human_message` 回退。`personal_usage/video.py` 的 `task.title or task.id` 同步改为缺标题 `None`。
- [ ] `personal_usage/types.py` 和前端 `UsageTask.title` 从必填 `str` 改为可空；UsageTaskList 在可见标题和 aria-label 中统一显示“标题生成中”，正常 Agent/视频标题不变。
- [ ] P10 更新旧文案断言：通用 `composing` 徽章显示“合成中”；主动 recompose 的 mutation pending 按钮仍可显示“重新合成中”。
- [ ] P13 为 composer 增加稳定 testid，替代通用占位文案定位；L2 控制台按普通用户断言 `personal-usage-page` 等现行契约，runbook 明确身份；不改既有 Writer CRUD 用例。
- [ ] 跑相关单元测试、mock E2E 和 CI后合入 PR 4；服务器 L1/L2 留到 Task 7，不作为本 PR 合并条件。

### Task 6：P6/P7 条件实现与允许阻塞收口

- [ ] P6 只有根因明确且存在可实施修复时才开独立 PR；先写最小失败测试，再修终止/恢复链，不只改“未完成”文案。
- [ ] 原 run 可免费恢复时，验收打开原任务并获得合法 ZIP/PPTX；不能免费恢复且无新付费授权时，以“外部阻塞、未重跑、未开 PR”完成，不判实现失败。
- [ ] P7 只有稳定复现后才开独立 PR；加入重开落底、切线程和跨账号隔离对照，最小修复锚定/测量逻辑，不整体重写。
- [ ] P7 修复后真实浏览器重复滚动至少 20 轮，不跳顶、不白屏、不截断，最新与最早历史均可达；happy-dom 不冒充真实布局证据。
- [ ] P7 若仍无法稳定复现，保留诊断用例和观察记录，以“证据不足、未开 PR”完成，不宣称修复。

### Task 7：门禁、登记、部署验收

- [ ] 每个实际执行 PR 更新本计划勾选和唯一状态头；按实际执行日创建对应 changelog，含 TL;DR 与「验收记录」，记录真实命令和关键输出。
- [ ] 按受影响层运行 backend 全量 pytest、frontend `pnpm check && pnpm test`、相关 mock E2E、根 `make check` 和 docs convention；PR 3 另跑 sandbox 镜像与技能复制检查。
- [ ] 四个主 PR 严格按边界独立评审和合入：PR 1→PR 2→PR 3→PR 4；PR 4 必须等 PR 1 合入。P6/P7 条件 PR 不挤入主批，也不与共享文件并行修改。
- [ ] 字节直连真实验收仅在另获付费授权后执行一个最短文本和一个目录内 speaker；无授权记 `LIVE_NOT_VERIFIED`，不清空 4 个入口、不切换其他通道。
- [ ] 执行收尾时把 **P2、P4、P12（账户/登录）、Writer 分享页英文、P14、P15** 分别登记到 `docs/findings-backlog.md`，每项格式为 `- [ ] 2026-11-22 一句话 + 本计划路径`；历史报告和 changelog 不回改。
- [ ] 部署动作不在本计划自动执行。部署后运行 L1/L2 和对应行为探针；它们不是 GitHub CI 或主 PR 合并门禁。
- [ ] L1/L2 若因服务器配置漂移失败，只记录漂移与部署证据，不回头修改已经合入的主 PR；确属新产品缺陷时另开后续任务。
- [ ] PR 3 运行、依赖、配置和镜像必须一起部署/回滚；历史用量和已生成音频不删除。

## 五、完成定义

- 四个主 PR 按固定边界独立评审和合入，未收成一个 PR，也未把 P6/P7 或 P12 混入。
- 新建配音只剩目录现有的 **4 个中文豆包音色**；Edge/360 TTS 无可调用路径，字节失败不静默换声。
- P1/P11 有真实行为证据；P5/P9/P10/P13 有自动化证据，L1/L2 仅作为部署验收记录。
- P6/P7 允许以“证据不足或外部阻塞、未开 PR”收口；只有实际恢复/修复时才要求对应 PPTX 或滚动结果。
- P2、P4 自动改造、P8、P12、P14、P15 和 Writer 分享页均无产品代码/测试 diff；PR 4 无 config diff。
- 所有已创建 PR 的单元测试、mock E2E、CI/OCR 与 changelog 证据一致；未执行的 live/付费项明确标注，不以 mock 通过冒充服务器验收。
