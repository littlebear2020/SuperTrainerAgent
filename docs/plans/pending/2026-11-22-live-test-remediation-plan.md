# 服务器实测问题修复与 TTS 单通道收敛实施计划

> **状态**：未执行（2026-11-22 根据逐项核实结果及用户最新产品决策定稿；不授权自动部署、付费调用或修改服务器配置）。
> **TL;DR**：优先修复停止清草稿、播客 URL 抓取和长任务证据链；TTS 全面移除 Edge 与 360 通道，只保留字节直连及既有声音克隆；Writer、P4 自动计价改造和普通用户渠道入口均退出本批。影响面 = frontend、backend TTS/video-gen、skills、docker/sandbox、config 示例与 live E2E。

> **For agentic workers：** 使用 `executing-plans`，单执行者按 Task 0→7 串行执行并逐项勾选；每项先写失败测试、确认功能原因失败、最小实现、定向回归，再提交。跨层及核心路径一律分支 + PR + OCR，不在当前分叉且带未提交文件的工作区直接实施。

**Goal：** 在不扩展首页六卡之外产品面的前提下，修复已确认的服务器实测问题，并将所有在线 TTS 路径收敛为字节直连，消除 Edge 403 与 360 TTS 降级复杂度。

**Architecture：** 保留现有 Next.js/FastAPI/Agent 架构。聊天停止在业务包装层与成功提交解耦；播客继续走现有安全抓取器；TTS 以共享音色目录和统一 `synthesize()` 为主收口点，运行时只接受 `doubao:*`、既有 `clone:*` 和明确支持的字节 speaker，旧 Edge/360 音色响亮失败，绝不静默换声。

**Tech Stack：** Next.js、React、TypeScript、TanStack Query、Vitest、Playwright；FastAPI、Pydantic、SQLAlchemy、pytest；既有 Doubao/OpenSpeech SSE、FFmpeg；不引入新框架或新 TTS 供应商。

**Spec / 证据来源：**
- `docs/2026-11-22-bug-verification-handoff.md`（原始问题清单）。
- `docs/2026-11-22-full-module-test-report.md`（服务器实测与截图索引）。
- 用户 2026-11-22 决策：Writer 与六卡无关则不处理；完全移除 Edge TTS 与 360 TTS；模型价格后续由管理端手工维护；普通用户渠道入口不开放。
- 本地核实：Edge TTS 本机同样 WebSocket 403；播客受控 HTTP 服务稳定复现 `SocketIO.settimeout` AttributeError，运行时最小补丁验证可完成读取；既有 Edge/播客测试 73 条通过但未覆盖真实故障形态。

**基准 commit：** `origin/main@0e84b148`。执行前先 `git fetch origin`，从最新 `origin/main` 建分支，并运行 `git diff 0e84b148 --stat`；如相关文件漂移，先更新文件/测试映射，不按旧行号覆盖。

## 一、范围与产品决策

### 本批执行

| 项 | 最终处置 |
|---|---|
| P1 停止清草稿 | 修复，停止时保留草稿、附件和 localStorage。 |
| P3 Edge TTS 403 | 不再诊断或升级 Edge；彻底移除 Edge 与 360 TTS，在线语音只走字节直连。 |
| P5 默认徽标 | 按 `mode_defaults` 动态显示，标准默认以最新设计 Claude Sonnet 为准，DeepSeek 不标默认。 |
| P6 PPT run 中断 | 先取得 run 记录和日志确认根因，再针对根因修复并验收真实 PPTX。 |
| P7 长线程滚动 | 保留重开落底修复；用真实浏览器回归稳定复现跳顶/截断后再最小修复。 |
| P9 用量页裸 UUID | 标题缺失返回明确空值，前端显示本地化“标题生成中”。 |
| P10 首渲文案 | 通用 `composing` 显示“合成中”；主动重新合成按钮可保留“重新合成中”。 |
| P11 播客 URL 抓取 | 修复真实 socket 超时处理，保留并重验完整 SSRF 安全链。 |
| P12 中英文混杂 | 本批只处理账户设置与登录失败；Writer 分享失效页不在范围内。 |
| P13 live E2E 漂移 | 使用稳定 testid 和普通用户个人用量页断言，恢复 L1/L2 绿灯。 |

### 明确不做

- **P2 Writer 回滚伪 409、P14 Writer 入口**：独立 Writer 不属于当前首页六卡核心能力，不修、不删、不加入口，也不改其测试。
- **P4 模型未定价**：不在本批改配置、计价代码或加自动门禁；后续由运营在管理端手工维护，未知价格不得填 0。
- **P15 普通用户渠道入口**：维持现有角色限制，不做任何优化。
- **P8 模式持久化**：保留浏览器级持久化与线程模型覆盖；Ultra 成本提醒不在本批实现，若后续需要另立产品小批。
- 不修改 360 的 LLM、生图、AI 视频能力，不删除其共享 `QIHU_API_KEY`；移除范围只限 **360 TTS**。
- 不手改 `frontend/src/components/ui/**`、`frontend/src/components/ai-elements/**`，不读取或提交真实 `.env/config.yaml`、服务器数据库、密钥和用户数据。

## 二、Review Focus

1. **停止路径仍经过通用成功清空逻辑**：Task 1 必须同时验证按钮与 Enter，且等待防抖后草稿仍存在。
2. **TTS 只删前端入口、后端仍可被旧任务触发**：Task 3 必须在目录、统一合成入口和实际调用方同时拒绝 Edge/360，且拒绝发生在任何付费/网络调用前。
3. **删除降级后静默换声或旧任务误播**：已生成音频和历史用量记录保留；旧任务重配/重渲遇已移除音色时显示明确错误，不自动替换为字节音色。
4. **单通道后的多语言与默认可达性**：只有经过字节直连真实探针且在目录中有可用音色的语言才暴露；不得用 Edge 的遗留默认值填补空缺。
5. **播客修复绕过安全机制**：不得为通过测试而关闭非公网 IP 拒绝、重定向逐跳复查、Host/SNI 钉住或 HTML 消毒。
6. **P6/P7 未取证先改代码**：没有 run 终止证据或稳定滚动复现时，只允许补诊断/回归，不写猜测性修复。

## 三、路径边界（串行执行）

| 批次 | 允许路径（allow） | 禁改路径（ban） | 共享基座触达 |
|---|---|---|---|
| P1/P5 与低优 UI | 点名的 `frontend/src/**`、对应 `frontend/tests/**`、`config.example.yaml` 的默认徽标说明、本计划与执行 changelog | registry UI/AI Elements、Writer 业务与分享页、真实配置 | **需独占** `input-box.tsx`、locale、usage 组件；P5 必须在 P1 合入后再改 |
| P11 | `skills/public/podcast-studio/**`、`backend/tests/test_podcast_studio_ingest.py`、对应部署文档 | 其他技能、关闭安全校验、真实服务器数据 | 核心技能路径，独立 PR + OCR |
| TTS 单通道 | `backend/app/gateway/{voice_twin,video_gen}/**`、`backend/app/gateway/_shared/{voice_catalog,media_constants,media_pricing_resolver}.py`、相关 routers/config/tests、`frontend/src/{core,components}/**` 中音色相关文件、`skills/**` 的 Edge/360 TTS 引用、`docker/sandbox/Dockerfile`、`backend/pyproject.toml`、`backend/uv.lock`、`.env.example`、`config.example.yaml`、相关 AGENTS/docs | 360 非 TTS 能力、Remotion 模板、provider 包裹链无关改动、Writer、真实密钥与服务器配置 | **规划侧独占**；Task 3/4 串行，不与 P1/P5 并行改前端 |
| P6/P7 | 根因确认后点名 run/worker、threads、messages 文件及对应测试；诊断阶段只读日志和新增测试 | 中间件/provider 无关重排、虚拟列表整体重写、付费 L3 | 视根因决定，未取证不得扩大 allow |

所有批次只修改完成该任务所需文件；发现必须进入 ban 路径时停止并回规划侧重写边界。

## 四、实施任务

### Task 0：冻结基线并补 P6 运行证据

**Files：** 诊断阶段不改产品文件；证据按实际执行批次放 `temp/`，不提交敏感数据。

- [ ] 确认最新 `origin/main`、工作区状态和漂移面；从最新 main 建独立分支，不带入当前未跟踪报告或其他分支修改。
- [ ] 对 P6 目标线程读取脱敏 run 记录：`run_id/status/error/stop_reason/on_disconnect`、最后事件、最后工具调用、checkpoint/goal 状态和 gateway/沙箱日志；不得只凭“未完成”推断根因。
- [ ] 核对实际前端提交使用 `streamResumable=true` 时 SDK 的 `on_disconnect=continue`，排除把后端默认 `cancel` 误当实际请求的误判。
- [ ] 形成一段根因结论：终止组件、直接异常/停止原因、用户可恢复状态、需要修改的最小文件；证据不足则 P6 保持阻塞，不进入实现。
- [ ] 复核 P5 的 `/api/models` 实际 `mode_defaults`：闪速 Qwen Flash、标准 Claude Sonnet、Ultra Qwen4 Max；DeepSeek 仅手动可选。

### Task 1：修复停止生成清草稿（P1）

**Files：**
- Modify: `frontend/src/components/workspace/input-box.tsx`
- Test: 新增 `frontend/tests/unit/components/workspace/input-box-stop-preserves-draft.dom.test.tsx`
- Do not modify: `frontend/src/components/ai-elements/prompt-input.tsx`

**Interfaces：** 保留 `InputBox.onStop`、`onSubmit` 和 PromptInput 的既有公开契约；停止是独立控制流，成功发送才允许通用组件清空。

- [ ] 写失败测试：streaming 时输入草稿并添加 mock 附件，点击停止后 `onStop` 一次、`onSubmit` 零次，文本框与附件不变；等待超过 400ms 后 localStorage 草稿仍在。
- [ ] 增加 Enter 触发的同语义用例，以及非 streaming 正常发送仍清空文本/附件的对照；运行并确认旧实现因停止后清空而失败。
- [ ] 在 InputBox 业务包装层截获 streaming submit/停止事件，阻止其进入 PromptInput 成功清空路径；移除原 `handleSubmit` 中会形成 resolved Promise 的停止分支，不抛异常冒充停止。
- [ ] 重跑新测试及既有 InputBox 套件，随后跑 `pnpm check`；手工行为验收为“长回复期间边等边写，停止后文字仍在”。
- [ ] 单独提交 `fix(chat): 停止生成时保留用户草稿`。

### Task 2：修复播客真实 URL 抓取与安全回归（P11）

**Files：**
- Modify: `skills/public/podcast-studio/scripts/podcast_studio.py`
- Test: `backend/tests/test_podcast_studio_ingest.py`
- Modify if needed: `skills/public/podcast-studio/SKILL.md`（仅同步实际行为）

**Interfaces：** `fetch_url(url) -> FetchedURL` 和 `intake()` 的来源元数据契约不变；不得改走平台 web reader 或绕过 URL 安全链。

- [ ] 新增真实形态失败测试：`connection.sock=None`、`response.fp.raw` 为 SocketIO 形态，旧实现必须因 `raw.settimeout` 失败；补齐读取到 EOF 后 `fp=None` 的场景。
- [ ] 最小修复 `_set_socket_timeout`：连接存活时使用连接 socket，响应期使用 SocketIO 对应的真实 socket，EOF/响应已关闭时安全结束；找不到可设置超时的活动 socket 时响亮失败。
- [ ] 保留并运行现有非公网 IP、混合 DNS、重定向逐跳复查、HTTPS 降级拒绝、Host/SNI/TLS、大小/超时、HTML 消毒测试；新增 `Connection: close`、分块响应和重定向后 EOF 回归。
- [ ] 用本地受控 HTTP 服务跑完整 `fetch_url()`，再在实际沙箱 Python 3.10 中执行一次公开 URL intake；断言音频前处理所需的 `requested_url/final_url/mime` 正确且安全链真实经过。
- [ ] 核心路径走独立 PR + OCR；记录修复前后真实错误与成功输出。

### Task 3：TTS 运行链路和音色目录只保留字节直连（P3-A）

**Files / 主要责任：**
- `backend/app/gateway/_shared/voice_catalog.py`：目录只保留验证过的 `doubao:*` 条目。
- `backend/app/gateway/voice_twin/{service,production_service,config,preset_voices,production_schemas}.py`：移除 Qihu/Edge 客户端、配置和降级。
- `backend/app/gateway/video_gen/orchestration/{tts,voices}.py` 及实际消费方：统一入口只走字节直连/既有克隆，旧音色在付费前失败。
- `backend/app/gateway/video_gen/{config,service,course_models,subtitle_engine}.py`、`engines/vimax/**`：只修改完成单通道所需引用。
- `frontend/src/core/{voice-twin,video-gen}/**`、相关 voice selector/create/project 组件和 locale：移除 Edge/360 选项、分组和过时提示。
- Tests: 相关 `backend/tests/test_*voice*`、`test_*tts*`、`test_orchestration_tts.py`、video-gen/ViMax 测试及前端音色测试。

**Interfaces：** `doubao:<speaker>` 继续使用现有 Doubao/OpenSpeech SSE；既有声音克隆和已落盘音频不变。`preset:*`、`edge:*` 及旧的裸 Edge ShortName 均为退役输入。

- [ ] 先写失败测试：两个音色目录 API 都只返回字节条目；前端不再出现免费 Edge/360 分组；`preset:*`、`edge:*` 和裸 Edge ID 在网络调用前得到可读错误。
- [ ] 写“字节失败不换声”测试：429/5xx/传输错误只沿用现有字节重试，重试耗尽后任务失败；不得调用 Qihu/Edge，也不得写“已降级到其他音色”的成功 sidecar。
- [ ] 写克隆回归：克隆上传、训练、试听和合成仍走既有字节/Volcengine 路径，克隆失败仍不降级。
- [ ] 删除 doubao→qihu→edge、qihu→edge 及对应策略分支；简化 `TTSPolicy` 等仅为旧降级存在的接口，保留字节重试、字级时间戳、情绪、section_id、缓存和 SRT 行为。
- [ ] 目录和默认值不再引用 Edge/360；新任务默认使用已验证的字节 speaker。无可用字节音色的语言从新建入口收起，不保留会必失败的语言/音色组合。
- [ ] 旧任务处理：历史音频继续可播放，历史用量不迁移；含退役音色的试听、重配、重生或重渲在提交前提示“原音色已停用，请改选字节直连音色”，不得静默替换。
- [ ] 前端同步清理 provider 类型、分组、筛选和残留值禁用逻辑；旧任务仍可看见原值及停用原因，不能自动变成另一音色。
- [ ] 运行全部相关后端/前端定向测试和 mock E2E；证明 360 LLM/生图/视频配置与请求完全未变。
- [ ] 单独提交 `refactor(tts): 在线配音收敛为字节直连`。

### Task 4：清理 TTS 技能、依赖、配置和沙箱镜像（P3-B）

**Files / 主要责任：**
- Delete or clean: `backend/app/gateway/voice_twin/{edge_tts_client,qihu_tts_client}.py` 及只服务旧通道的测试。
- Modify: `backend/pyproject.toml`、`backend/uv.lock`（用 `uv lock` 生成）、`.env.example`、`config.example.yaml`。
- Modify: `skills/public/ppt-master/**` 的 Edge 引用，尤其 `notes_to_audio.py`、`tts_backends/backend_edge.py`、requirements 和音频文档；所有 vendored 变更登记 `TRAINERAI_PATCHES.md`。
- Modify: 其他技能中仍承诺 Edge/360 TTS 的说明或执行代码；保留现有字节 `_doubao_tts.py` 实现，不合并独立技能进程的帮助器。
- Modify: `docker/sandbox/Dockerfile`、`backend/AGENTS.md`、相关常青模块文档与测试。

- [ ] 全仓检索 `edge_tts`、`edge-tts`、`edge:`、`preset:`、Qihu TTS 和旧 TTS 环境开关，按“运行代码 / 历史证据 / 文档”分类；历史报告和 changelog 不改，现行运行与常青文档不得继续承诺旧通道。
- [ ] 删除 backend 与 sandbox 的 Edge 依赖、import、自检和无用重试帮助器；删除 360 TTS 配置与价格解析，但保留 Qihu 其他媒体能力的配置和价格。
- [ ] PPT Master 继续默认且只走既有 `trainerai` 字节后端；移除 Edge backend/import/dispatch/依赖，所有 vendored 补丁在 `TRAINERAI_PATCHES.md` 登记，不能借上游死代码之名保留可调用 Edge 路径。
- [ ] 若删除 `qihu_tts`/`edge_tts` 配置结构，同步 `config.example.yaml` 并将当前 `config_version` 递增 1；服务器实际 config 由部署侧另行合并，不在本计划自动修改。
- [ ] 更新 `backend/AGENTS.md` 的 TTS 降级链约定为“字节直连，失败响亮；克隆不降级”，并同步其他仍描述旧行为的现行文档/注释。
- [ ] 重建本地 sandbox 镜像并运行现有离线 readiness/技能复制/字节配音测试；证明镜像不再含 Edge 包，且字节技能脚本与 FFmpeg 可用。
- [ ] 单独提交 `chore(tts): 移除 Edge 与 360 语音通道`。

### Task 5：默认徽标、用量标题、状态文案、i18n 与 live 测试（P5/P9/P10/P12/P13）

**Files：** 以基准中以下模块现行文件为准：
- `frontend/src/components/workspace/input-box.tsx`、`core/models/**`、`input-box-mode-models.dom.test.tsx`。
- `backend/app/gateway/_shared/personal_usage/{agent,types}.py`、`frontend/src/{core/usage,components/workspace/usage}/**` 及对应测试。
- `frontend/src/core/video-gen/task-status-label.ts`、相关状态测试。
- `settings/account-settings-page.tsx`、`app/(auth)/login/page.tsx`、三份 i18n 文件；**排除 Writer 分享页**。
- `frontend/tests/e2e/live/{helpers,l1-smoke,l2-features}.ts`、`frontend/docs/LIVE_E2E_RUNBOOK.md`。

- [ ] P5 先写三档徽标测试，再从 `modeDefaults[currentMode]` 渲染选中按钮和列表徽标；同步清理示例及部署侧 `display_name` 中手写“默认”的文案，避免重复徽标。预期标准 Claude、闪速 Qwen Flash、Ultra Qwen4 Max，DeepSeek 无默认徽标；部署侧只改展示文案，不自动修改真实 config。
- [ ] P9 先覆盖 `display_name=None` 及 UUID 占位形态；后端标题缺失返回 `null`，前端 `UsageTaskList` 在标题和 aria-label 中统一显示“标题生成中”，已有标题与首条消息回退不变。
- [ ] P10 更新旧文案断言：所有通用 `composing` 徽章显示“合成中”；主动 recompose 的 mutation pending 按钮仍可显示“重新合成中”。
- [ ] P12 将账户页全部可见文案入 i18n；登录根据 `invalid_credentials` 错误码显示本地化消息，保留其他结构化错误处理。Writer 分享页不改。
- [ ] P13 为 composer 增加稳定 testid，替代通用占位文案定位；L2 控制台按普通用户断言 `personal-usage-page` 等现行契约，runbook 明确普通用户身份；不改既有 Writer CRUD 用例。
- [ ] 运行相关 unit/mock E2E，再在服务器运行零成本 L1 和仅既有一次轻量 LLM 的 L2；预期两处旧漂移消失且无新增失败。
- [ ] 按功能拆成可审查提交，再以一个 PR 收口；P5 与 P1 必须串行。

### Task 6：P6 根因修复与 P7 长线程真实滚动回归

**Files：** 由 Task 0 根因和首批复现结果点名；未确认前不得指定大改中间件或虚拟列表。

- [ ] P6 按已核实根因写最小失败测试并修复；若 run 现场可恢复，提供与真实状态一致的继续/重试入口，不能只把“未完成”改成其他文案。
- [ ] P6 行为验收必须打开原任务、获得真实可下载 PPTX，并验证文件为合法 ZIP/PPTX；同时记录恢复是否产生新费用，未经授权不重跑付费长任务。
- [ ] P7 新建真实 Playwright 夹具：至少 60 个不同高度消息组、大量 Token 行和历史 prepend，反复 PageUp/PageDown，记录首可见 key、scrollTop、scrollHeight 与已加载 run 数。
- [ ] 加入重开落底、切线程和跨账号隔离对照；只有稳定复现跳顶/截断后才修改锚定或测量逻辑，不做整体重写。
- [ ] 修复后重复滚动至少 20 轮，不跳顶、不白屏、不截断，最新消息和最早历史均可达；happy-dom 测试不得冒充真实布局证据。

### Task 7：全量门禁、文档、PR 与部署验收

- [ ] 每批维护本计划勾选和唯一状态头；按实际执行日新增 `docs/changelog/changelog-YYYY-MM-DD-live-test-remediation.md`，含 TL;DR 与「验收记录」，逐行记录真实命令和关键输出。
- [ ] 后台运行 `cd backend && uv run pytest tests/ -q`、`cd frontend && pnpm check && pnpm test`、根 `make check`；另跑相关 mock E2E、docs convention、sandbox 镜像检查，冻结被测输入。
- [ ] 字节直连真实验收仅在执行方另获付费授权后进行：一个最短文本、一个目录内字节 speaker，检查 MP3 可解码、实际 speaker 正确、无 Edge/360 外部调用；失败记 `LIVE_NOT_VERIFIED`，不换通道。
- [ ] 所有成批/跨层改动走分支 + PR；`skills/`、`docker/`、`backend/app/**/video_gen*`、config schema 等核心路径必须 OCR，无 Critical/Important 且 CI 绿后才合入。
- [ ] 部署时保持 gateway、frontend、技能和 sandbox 镜像版本一致；模型价格由运营后续在管理端手工维护，不作为本批上线门禁，也不自动修改。
- [ ] 部署后执行 P1/P11/P5/P9/P10/P12/P13 行为验收及 TTS 字节直连冒烟；P6/P7 按各自真实证据验收。回滚按 PR/批次执行，TTS 运行与镜像清理必须一起回滚，历史用量和已生成音频不回滚删除。

## 五、完成定义

- 首页六卡及其共享链路中，不再存在可调用的 Edge/360 TTS 路径；字节直连失败不会静默换声。
- P1/P11 有真实行为证据，P5/P9/P10/P12/P13 有自动化与部署侧证据。
- P6 有日志支撑的根因和真实 PPTX 结果，或明确记录外部阻塞；P7 有稳定复现与真实滚动回归，未复现时不宣称修复。
- Writer、P4 自动改造、P8 成本提醒、P15 均无产品代码 diff。
- 全量门禁、PR CI/OCR、changelog 验收记录和部署侧结果一致；任何未执行的 live/付费项明确标注，不以 mock 通过冒充服务器验收。
