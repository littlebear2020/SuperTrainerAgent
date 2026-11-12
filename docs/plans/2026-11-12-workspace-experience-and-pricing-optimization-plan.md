# 工作区体验与文字动效修复优化实施计划

> **状态**：已执行（任务 0–6；任务 7–8 未执行。任务 0 提交 `1e5face7`，任务 1 提交 `0ee573a8` 并完成一次真实显式 speaker TTS，测试补强提交 `72d61eb5`；任务 2 本地 linux/amd64 sandbox 全部验收通过；任务 3 提交 `f63f0fe7`，普通/Agent/案例 Playwright 12/12；任务 4 提交 `32d38103`，video-assets Playwright 11/11；任务 5 六能力首页、三张 poster、按需媒体与中英移动截图验收完成，首页/landing Playwright 12/12；任务 6 六类筛选、Agent 完整历史、URL/键盘导航与 offset 50 验收完成，作品 Playwright 13/13，证据见 `temp/workspace-experience-2026-11-12/`）。
> **TL;DR**：夜间 goal 串行交付 R1–R6/R8，并完成本地测试、sandbox 镜像/容器验证、最小 TTS、PR/OCR；R7 定价和播客实时 URL 不阻塞 goal；不访问测试/公司服务器、不部署、不构建发布镜像、不重建 remotion-worker，次日由用户人工验收并自行决定公司部署。

> **For agentic workers:** REQUIRED SUB-SKILL: 使用 `executing-plans`（单执行者、单工作区）逐项实施；不得调用 AskUserQuestion，不得等待人工回复。每个实现任务遵循“失败测试 → 确认功能原因失败 → 最小实现 → 定向回归 → 提交”；全部长命令后台串行，冻结被测输入。

**Goal**：在 Mac 本地完成 R1–R6/R8 的实现、自动化与本地真实链路验收、文档和 PR/OCR，形成用户次日可直接人工验收的代码与证据；R7 由用户后续自行配置，不在夜间 goal 内改价或验收价格。

**Goal 完成/退出状态**：
- `PASS`：R1–R6/R8 全部实现；本地全量门禁、隔离 API 行为验收、linux/amd64 sandbox 镜像与容器级指纹/静帧通过；一次最小显式 speaker TTS 通过；当前 PR checks/OCR 通过并按约定合入；文档与证据齐全。
- `PARTIAL`：实现与可离线验收已完成，但凭证、供应商、GitHub/OCR/CI 等外部条件导致 live 或远端门未通过。执行者不询问用户、不伪造成功，完成其余任务后以 `PARTIAL_LIVE_NOT_VERIFIED` 或 `PARTIAL_REMOTE_PENDING` 报告具体缺口。
- `BLOCKED_PRECONDITION`：启动时另一 session 仍在写入，或未提交文件与本批 allow 路径重叠。不得等待、stash、reset、覆盖或替陌生改动提交，未改业务文件即退出并报告。

**Architecture**：保留 Next.js 工作区、通用 Agent chat、threads/video API 和课程项目 CRUD，通过 URL 驱动视图、共享能力目录与角色派生状态重构前端。文字动效继续区分 course-video 沙箱链和 `remotion_course` worker 链；只修 TTS 前置/声明与 sandbox 指纹，不合并管线、不改 worker。定价继续使用现有“真实 amount 优先、管理后台 DB 覆盖 config.yaml”的机制，本批不新增价格来源。

**Tech Stack**：Next.js 16、React 19、TypeScript、Tailwind CSS 4、TanStack Query、Vitest、Playwright；Python 3.12、FastAPI、pytest；Mac 本地 Docker、AIO Sandbox、Remotion、FFmpeg/ffprobe。不新增前端或 Python 依赖。

**Spec / 需求来源**：用户提供的截图 1–10 与八项需求；部署包 `supertrainer-deploy-12536796.tar.gz` 的 MANIFEST；现行首页/内容场景/播客计划；两轮 Cursor 复核建议及用户最新“夜间 goal 一次执行、Mac 本地完成、权限/费用预授权、服务器不部署”的边界。

**历史部署对照基线**：`12536796fa77ca96b990ec388375096ca49f1cc8`（仅用于代码差异与模板 hash 对照，不要求连接该部署环境；`remotion-base=0cef2d5c…`、`course-standard=93ad6d32…`）。

**定稿时代码基线**：`c4865cfd67a9516a8a8838bea9857d6c2e3abd93`（当前 HEAD 与 main 一致；PR #2 已合并）。goal 启动时重新记录 HEAD，并运行 `git diff c4865cfd --stat` 与 `git diff 12536796..HEAD --stat`；共享文件如有漂移，先更新文件/测试映射，不得用旧基线整文件覆盖当前代码。

**执行方式**：单执行者、单工作区、任务 0→8 串行，**全部实现与验收均在 Mac 本地完成**。允许本地代码/文档修改与提交、启动/停止本批服务、构建本地 sandbox、清理本批登记的容器、一次最小付费 TTS，以及按仓库约定 push/新建 PR/OCR/CI/合入。禁止访问测试或公司服务器、推送镜像到 registry、构建发布用 frontend/gateway、重建 remotion-worker、修改价格或读取/输出密钥值。

**本地预授权（来自用户最新说明）**：本地 Docker 构建与运行、本批容器清理、本地进程级 AUTOBIND 双向验证与恢复原状态、一次最短 TTS 及对应本地渲染、GitHub 分支/PR/checks/OCR/rebase merge 均已同意，无需 goal 中途再次询问。授权不等于凭证存在或供应商可用，失败按上述状态诚实退出。

## 一、需求映射与根因结论

| 需求 | 核实结论 | 夜间 goal 交付 |
|---|---|---|
| R1 关闭三个自动追问 | `InputBox` 在 streaming 结束后自动 POST suggestions，`n=3`；关闭 chips 只隐藏 UI。 | 移除前端动态追问请求、状态、UI 和布局联动；保留新会话案例/静态建议及后端接口兼容旧标签页。 |
| R2 文字动效失败 | TTS 只认 `VOLCENGINE_VOICE_ACCESS_KEY`；正式路径显式 speaker，但前置仍无条件要求默认 speaker。模板指纹受 collation 影响。 | 修前置并删除 auto 默认 speaker 声明；sandbox/SKILL 两处使用 NUL+C 排序；本地构建 sandbox 并做容器级验证，不碰服务器和 worker。 |
| R3/R4 导航与设置 | 普通用户五个 console 读接口已强制本人 scope；视频 tabs 不写 URL，底部硬编码，Settings 固定八项。 | 作品/资产可深链；我的空间、真实用户底部；普通设置仅账号/外观，管理员八项。 |
| R5 六类作品 | 映射保持：course-video、ai_image、ai_video、creative-studio、ppt-master、podcast-studio；分类依据为 agent metadata 与旧别名。 | 两类视频保持全状态卡；四类 Agent 新增可搜索、完整分页、可打开的对话历史；六类与外层 tab 可深链。 |
| R6 我的资产与项目详情 | 项目卡/新建弹窗可复用；调色与总保存耦合，关联任务数仍用于锚图提示。 | 只卸载品牌包、色彩微调、关联视频 UI；保留 palette、品牌默认值和 projectTasks。 |
| R7 模型与素材定价 | 用户已决定自行通过 config.yaml/管理后台配置；本地旧配置与目标环境不能混用。 | **OUT_OF_SCOPE**：不新增默认层、播种、迁移、素材行、计价改造，不选择 doubao_tts 价格，不要求夜间拍板。 |
| R8 首页 | 两个分类条互斥隐藏三卡；三个视频同时播放；近期项目仍渲染；静态图比例不一。 | 紧凑 Hero + 六能力网格；移除近期项目；静态图 16:9 容器 `object-contain`，视频 poster 960×540，单卡预览。 |

### 已排除的错误修复方向

- 不把 `ARK_API_KEY` 当 OpenSpeech key，不把密钥写入常驻 `sandbox.environment`，不默认打开全量环境继承。
- 不把 `optional: true` 当作 AUTOBIND 排除语义；required/optional 都进入声明并集。
- 不修改/重建 remotion-worker；不把服务器旧 warm sandbox 清理搬进 Mac goal。
- 不把 `/workspace/console` 全部管理员化，不把“我的资产”指向 `/workspace/assets`。
- 不改变 R5 映射，不恢复 remotion_course/vimax 创建入口，保留视频全状态卡。
- 不用 CSS 隐藏近期项目、品牌包或设置项来伪造完成。
- 不填三条 Claude TBD，不新增素材行，不把未知价写 0，不读取另一个本地数据库作为夜间输入。

## 二、评审决策记录

| Frontier 问题 | 夜间 goal 决策 |
|---|---|
| 追问 | 前端移除动态链，Network 零 suggestions 请求；后端 API 暂留兼容旧 JS。 |
| 默认 speaker | 从 auto required-secrets 删除，不采用 optional；其他技能声明不变，不能据此断言全局不再注入。 |
| helper | 不合并、不 fork；两份当前 SHA-256 相同，保留 env fallback/裸自检兼容，live 必须显式 speaker。 |
| 模板指纹 | 只改 sandbox Dockerfile 和 auto SKILL：`-print0 | LC_ALL=C sort -z | xargs -0 md5sum | md5sum | awk '{print $1}'`；文件集、MD5、symlink 语义不变。 |
| 本地镜像与真实调用 | 保留 Mac 本地 linux/amd64 sandbox 构建、容器 hash/compositions/静帧和一次最短 TTS；不接受“全部移交部署批”，否则无法验证 Dockerfile 与供应商链。 |
| 本地旧容器 | 不清理陌生容器；仅清理由本批登记创建的实例。若不能隔离共享 warm 环境，则只做独立容器验证并报告 runtime warm 未验证，不宣称旧容器已替换。 |
| 我的作品/资产 URL | 裸 `/workspace/video-gen` 默认 works+course-video，`?tab=assets` 为项目资产，`capability` 表达六类，不新增 App Router 页面。 |
| 用户名称 | 显示邮箱 local-part，完整邮箱放 title，首字符作头像；显示管理员/普通用户，移除 Pro Plan，不新增用户字段。 |
| 设置权限 | 只做 UI 收敛；隐藏 section 不挂载，非法 defaultSection 回退账号，后端个人资源权限不变。 |
| 四类 Agent 历史 | 使用 useThreads 不传 limit 的完整分页，按标题本地搜索，canonical agent 分类，首屏 6、每次 +6；首版不做 thread 删除。 |
| 创建弹窗 | 生产仅 WorkspaceHome 为 ai_image/ai_video 条件挂载并传 initialTaskType，focused 不显示通用类型选择器；不新增 unfocused 挂载。 |
| 首页静态媒体 | 三个视频生成 960×540 poster；PPT/知识卡/播客复用现有图，16:9 容器 `object-contain` + 中性背景，不硬裁。知识卡是方形。 |
| R7 | 整体 OUT_OF_SCOPE；doubao_tts 0.12/0.3 由用户后续决定，不阻塞夜间 goal。 |
| Terra | 保持内部 `gpt-5.6-terra` + 上游 `openai/gpt-6.1-terra`；另一个旧 config 的 5.6 上游不覆盖目标。 |
| 播客前序 | 沿用“实时安全 URL 由用户后续自行验收”的既有决定，不重新询问、不代勾前序计划。夜间只验播客入口/历史卡/canonical 路由，不宣称四模式实时验收完成。 |
| 夜间人工检查 | 执行 agent 用 Playwright 截图和 ReadMediaFile 检查布局/留白；用户次日审美与完整交互验收不预先标通过。 |
| PR #2 high | 创建框用“关闭即卸载 + 交替 E2E”证据关闭；阶段标记区分流式临时隐藏与最终/导出清理，避免误删正文。 |

## 三、路径边界（allow / ban / 共享基座）

| 批次 | 允许路径（allow） | 禁改路径（ban） | 共享基座触达 |
|---|---|---|---|
| A：R2 本地修复与验证 | `skills/public/auto-video-flow/{SKILL.md,scripts/generate_audio_template.py}`、`docker/sandbox/Dockerfile`、`.env.example`、部署常青文档、点名后端测试、`temp/workspace-experience-2026-11-12/**` | `docker/production-worker/**`、其他技能/helper、模板 TSX/样式、pixel baseline、真实 `.env/config.yaml`、服务器与发布脚本 | **需规划侧独占**；核心路径 PR/OCR，不与前端批并行 |
| B：R1/R3/R4/R5/R6/R8 前端 | 点名的 `frontend/src/{components/workspace,app/workspace,core}/**`、对应 unit/E2E、`frontend/public/course-samples/**`、隔离测试 DB/本地服务 | registry UI/AI Elements、Writer 业务、后端生产逻辑、配置 schema、锁文件、真实用户数据 | **是，单执行者独占** locale、catalog、video-gen tabs、threads utils、共享 mocks |
| C：本地集成收口 | 本计划、findings backlog、执行 changelog、对应测试与 `temp/` 证据 | 定价后端/DB 迁移/素材行、部署脚本、compose、registry 镜像、真实主机/密钥/用户数据库、两个根目录源视频 | 串行合入、冻结被测代码、最终文档后复跑根门禁 |

路径重叠表：A/B/C 不并行修改共享文件；本计划全部串行。goal 不做并行 worktree，不访问服务器。

## Global Constraints

- 不读取、复制或外发真实 `.env`、密钥、用户数据库和内部运行数据；诊断只输出变量名与 `PRESENT/MISSING`。
- 不改 config_version，不替用户编辑真实 config.yaml 的价格，不在 Mac 与服务器之间复制配置。
- 不修改 registry UI、`skills/public/ppt-master/**`、worker、模板 pixel baseline 或两个根目录未跟踪 MP4。
- 所有新 UI 文案进入 `types.ts`、`zh-CN.ts`、`en-US.ts`；不整文件回写 locale。
- 默认自动化不调用付费 API；唯一 live 项是任务 1 的一次最短显式 speaker TTS，使用隔离输出与显式 opt-in。
- 不新增/修改模型价、素材价、计价 schema、迁移或用量采集；独立缺口只登记 findings backlog。
- 所有证据写 `temp/workspace-experience-2026-11-12/`：原始错误摘要、命令关键输出、hash/image ID、ffprobe、静帧/截图、skip 清单；不得提交临时产物、测试 DB 或密钥。

## Review Focus

1. **凭证/供应商不可用**：只检查进程环境 presence；缺失或失败继续离线工作并标 `LIVE_NOT_VERIFIED`，不询问、不换引擎。
2. **误把 optional 或单技能删除当全局安全边界**：审核实际“启用技能声明并集 ∩ Gateway env”，只记录名称，结束恢复 AUTOBIND 原状态。
3. **指纹只改一处或 worker 被误改**：任务 2 静态证明 sandbox Dockerfile 与 auto SKILL 两处命令完全一致，并验证本地新 image ID；worker diff 必须为空。
4. **角色/隐藏 section 绕过 UI**：fresh mount 两种角色，证明隐藏组件与管理员请求未发生。
5. **第 50 条后的历史与旧别名**：本地隔离 API 数据必须覆盖 offset 50 后匹配和 project_type。
6. **隐藏项目区块清空旧配置**：非默认品牌、非中性 palette 和关联任务做真实 API 保存回归。
7. **静态图片被硬裁**：computed object-fit=contain + 截图检查；次日人工审美不提前声明。
8. **skip 冒充验收**：必要本地用例零意外 skip；live/remote 缺条件必须显式列入未验证清单。

---

## 四、分批实施清单

### 任务 0：一次性前置检查、冻结基线与证据目录

- [x] 记录 HEAD、status、worktrees、PR #2 已合并状态、`git diff 12536796..HEAD --stat`；保留两个根目录源视频且不得提交。定稿时已知非业务改动仅为 `M docs/findings-backlog.md`、本计划未跟踪文件和两个 MP4；启动时重新扫描，若另一 session 仍写入或出现与 allow 重叠的脏文件，立即 `BLOCKED_PRECONDITION`，不等待、不接管。
- [x] 核对播客前序状态，沿用“实时 URL 用户后验”的既有决定；不重新询问、不代勾前序计划，只把播客实时创作列为本 goal 非验收项。
- [x] 创建 `temp/workspace-experience-2026-11-12/`，保存 R2 原始错误摘要和本地两种 locale hash；登记本批服务端口、容器和输出，不记录服务器镜像信息。
- [x] 处置已合并 PR #2 遗留、且至今未修复的 high：创建框用现有 E2E 证据回复；为最终 AI/人类导出中的非完整标记写窄修复与测试，区分流式临时隐藏和最终清理，纳入本批新 PR。不得借本批改 Writer 业务。
- [x] 在当前已冻结功能分支确认计划与代码匹配；预期红灯必须来自缺失能力，不是坏 mock。

### 批次 A：文字动效本地修复与验证（R2）

### 任务 1：修 TTS 前置、缩小声明并做本地显式 speaker 验收

**Files**：
- Modify: `skills/public/auto-video-flow/scripts/generate_audio_template.py`
- Modify: `skills/public/auto-video-flow/SKILL.md`
- Modify: `.env.example`、`docs/personal-server-deployment-guide.md`
- Test: `backend/tests/test_skill_audio_template_speaker.py`（既有）、`test_auto_video_flow_skill.py`、`test_bash_secret_injection.py`
- Create: `backend/scripts/probe_auto_video_flow_explicit_speaker.py`（live opt-in，仅最短一句）

- [x] 普通与词级两个公开入口分别写失败测试：只有 key+显式 speaker 到 mocked transport；缺 key 网络前失败且不误报 speaker；speaker=None 且默认缺失时才报默认 speaker；两项都缺只列实际缺失项。
- [x] 覆盖默认与显式值不同、词级降级仍传同一显式 speaker、前置失败零 transport；更新旧 mock 的 speaker 参数，避免绕过 `_doubao_available()`。
- [x] 最小修复共享前置检查，保持 doubao-only；不改 `_doubao_tts.py`，保留 env fallback 和裸自检兼容，不合并 helper。
- [x] 从 auto required-secrets 删除 `DOUBAO_TTS_DEFAULT_SPEAKER` 并同步 SKILL；不采用 optional，不全局删除示例变量，其他技能保持。
- [x] 增加真实 frontmatter 断言：auto 只声明 `VOLCENGINE_VOICE_ACCESS_KEY`；AUTOBIND 测试明确 required/optional 均计入并集，单技能删除不等于全局删除。
- [x] 在示例/部署指南记录 AUTOBIND 默认关闭及“启用技能声明并集 ∩ Gateway env”风险，保持 `sandbox.environment: {}` 不写真实值；文档只说明机制，不记录服务器操作已执行。
- [x] 分别审计宿主 shell 与本批 Gateway 容器的变量名，只输出 PRESENT/MISSING；宿主 shell MISSING 不等于容器运行时 MISSING。按用户预授权做进程级 AUTOBIND ON/OFF/恢复双向 presence 验证，不改 `.env`、不读值、不留下开关变化。无法安全审计时只记 live 未验证。
- [x] 若宿主 shell MISSING，**禁止 `source .env`、grep/cut/解析 `.env` 或 `uv run --env-file`**：复用或用现有 `docker/docker-compose-dev.yaml` 启动本批 Gateway，由 compose 的 `env_file` 在容器内形成运行环境。仅当容器内 access key=PRESENT 时，用 `env -i` 只注入该密钥名及非敏感运行参数，执行**一次**最短普通 `generate_tts(..., speaker=<已在册值>)`；默认 speaker 不传入且必须为 MISSING，容器内输出后 ffprobe>0。401/403/resource/network/容器启动失败记 `LIVE_NOT_VERIFIED`，不重试扣费、不换引擎、不询问用户。

  ```bash
  # 容器不存在时先由现有开发 compose 启动；不得在宿主 shell source/读取 .env。
  docker compose -f docker/docker-compose-dev.yaml up -d --no-deps gateway
  docker compose -f docker/docker-compose-dev.yaml exec -T gateway sh -lc '
    if [ -z "${VOLCENGINE_VOICE_ACCESS_KEY:-}" ]; then
      echo "VOLCENGINE_VOICE_ACCESS_KEY MISSING"; exit 2
    fi
    env -i \
      PATH="/app/backend/.venv/bin:/usr/local/bin:/usr/bin:/bin" \
      PYTHONPATH="/app/backend" RUN_LIVE=1 TMPDIR=/tmp \
      VOLCENGINE_VOICE_ACCESS_KEY="$VOLCENGINE_VOICE_ACCESS_KEY" \
      /app/backend/.venv/bin/python \
      /app/backend/scripts/probe_auto_video_flow_explicit_speaker.py
  '
  ```

  该命令只把 `VOLCENGINE_VOICE_ACCESS_KEY` 一个密钥名传给探针；日志仅写本批 temp 证据目录，不得打印环境全集或密钥值，容器内临时音频按证据需要复制或删除。

**定向验证**：`cd backend && PYTHONPATH=. uv run pytest tests/test_skill_audio_template_speaker.py tests/test_bash_secret_injection.py tests/test_aio_sandbox.py tests/test_auto_video_flow_skill.py tests/test_skill_script_copies.py tests/test_podcast_studio_audio.py -q`。

### 任务 2：固定指纹并完成 Mac 本地 sandbox 镜像/容器验证

**Files**：
- Modify: `skills/public/auto-video-flow/SKILL.md`
- Modify: `docker/sandbox/Dockerfile`
- Create: `backend/tests/test_remotion_template_version.py`
- Do not modify: `docker/production-worker/**`、worker 运行代码与模板内容

- [x] 新建静态契约测试：两处必须包含 `-print0`、`LC_ALL=C sort -z`、`xargs -0 md5sum`、最终 `md5sum | awk '{print $1}'`；禁止裸 `| sort | xargs md5sum`。
- [x] 新建临时目录行为测试：C/C.UTF-8/en_US 下结果一致；改一字节变化；node_modules/.DS_Store/TEMPLATE_VERSION 不影响；空格/换行文件名不拆分；目录外 symlink 不计入。缺 locale 明确 skip 原因，本地 Linux 容器验收不得跳过。
- [x] 固化 Ubuntu 参考夹具：remotion-base C=`18cc3712bf06ffc6041cfa94f4704722`、en_US=`0cef2d5c42209807e71b1618735a3468`；course-standard C=`93ad6d326092fc0fd57c3bc6b50f0072`、en_US=`540a05a2154ba8bc873d7587fb0e0219`。macOS en_US 值不同，不作为跨平台常量。
- [x] 将两处命令统一为 `find … -print0 | LC_ALL=C sort -z | xargs -0 md5sum | md5sum | awk '{print $1}'`；不改排除规则、MD5 或 `find -type f` 的 symlink 语义。
- [x] SKILL 分别计算 image_hash/source_hash，并用 `test "$image_hash" = "$source_hash"` 失败非零、输出 expected/actual；只比较 pristine 源与 `/opt/remotion-template`。
- [x] 跑 `tests/test_remotion_template_version.py tests/test_auto_video_flow_skill.py tests/test_sandbox_offline_readiness.py tests/test_aio_sandbox.py tests/test_remotion_template_deploy.py`；断言 worker 文件无 diff。
- [x] 后台构建**仅 Mac 本地** linux/amd64 sandbox，tag 为 `deerflow-sandbox:workspace-experience-<shortsha>`，不推 registry；记录本地 image ID 到 temp。
- [x] 用该镜像独立容器验证 `/opt/remotion-template` 戳=18cc…、C/C.UTF-8/en_US 重算一致、compositions 非空、模板现有示例静帧 PNG 非空且可解码。负向测试必须在本批 temp 中复制模板副本并挂载进容器，只修改副本的一个字节，运行同一指纹/相等性校验并断言非零失败；随后重算宿主原模板指纹仍为 18cc…，证明 `/opt/remotion-template` 与 skill 源均未被修改。只清理本批登记的容器。
- [x] 若任务 1 live TTS 成功，复用其音频和固定逐字稿在独立新容器中渲染一个最简 MP4，ffprobe 有时长与音频流；若 live 不可用则保留静帧证据并标 MP4 `LIVE_NOT_VERIFIED`，不伪造结果。

**本地回滚**：用 `git revert` 回滚本批提交（含 SKILL、generate 脚本、Dockerfile、测试），不是只 revert 两个文件；删除本批本地镜像/容器由用户预授权且必须按登记 ID 精确执行。worker 不在回滚面；不得直接对 worker `/opt/course-standard` 重算并要求等于戳，因为构建后还会改 lock registry。

### 批次 B：前端体验与信息架构（R1/R3/R4/R5/R6/R8）

### 任务 3：移除对话结束后的动态追问（R1）

**Files**：`input-box.tsx`、普通/Agent 两个聊天页、三份 locale/type、相关 unit/E2E 与 `tests/e2e/utils/mock-api.ts`；新增 `input-box-no-followups.dom.test.tsx`。

- [x] 先写 streaming→ready 后 suggestions 请求为 0 的失败 DOM 测试，并在普通/Agent E2E 注册 Network 计数路由。
- [x] 删除动态追问 effect、fetch、chips/确认弹窗、关联 state/ref、onFollowupsVisibilityChange 和额外 80px 布局联动；清理五个专用 i18n key 与失效测试。
- [x] 保留新会话 SuggestionList、agent 自有 prompts、技能/静态建议、案例卡和后端 suggestions API/历史记账；不改 registry suggestion。
- [x] 验证普通与 Agent 回复结束、首轮新会话转已有线程后均零 POST；案例仍只预填、不自动发送。

### 任务 4：建立 URL 导航、真实用户底部与角色设置（R3/R4）

**Files**：新增 `core/workspace/video-assets-route.ts`；修改 nav-routes、workspace-nav-chat-list/menu、command-palette、AuthProvider、console hooks/page、settings-dialog、video-gen-page、三份 locale/type；对应 unit/E2E 与 `backend/tests/test_console_router.py`。

- [x] 纯函数覆盖 tab=works|assets、六类 capability、非法值回退；裸 `/workspace/video-gen`=works+course-video，`?tab=assets`=项目资产，assets 不携带 capability。
- [x] 侧栏拆“我的作品/我的资产”，组名改“我的空间”；普通 console=我的用量，管理员=运营后台；⌘K 复用同一导航 SoT。
- [x] active 同时考虑 pathname 与规范化 tab：绝不双高亮，任务详情仅我的作品高亮，`/workspace/assets` 模板中心不变。
- [x] AuthProvider 派生 isAdmin；底部显示邮箱 local-part、首字符头像、角色与独立齿轮，完整邮箱放 title，移除 Pro Plan/设置和更多，不嵌套按钮。
- [x] Settings 普通仅 account/appearance，管理员八项；effectiveActiveSection 同时约束菜单/内容，普通 defaultSection=about 回退 account，隐藏组件不挂载、不发请求。
- [x] console 角色 admin→user 时定价 tab 回 analysis；保持普通用户无用户选择器/owner 列/定价请求，不新增生产后端管理员限制。
- [x] 后端仅扩测试：五个普通读接口伪造他人 user_id 均 403，本人正常；管理员工具/定价仍由 require_admin 保护。

### 任务 5：完成首页六能力网格、按需媒体与完整静态图（R8）

**Files**：
- Modify: `workspace-home.tsx`、`home-course-catalog.tsx`、`home-course-entry.tsx`、三份 locale/type、`project-list.tsx`（仅死导出处理）
- Modify: `frontend/tests/e2e/utils/locale.ts`（cookie baseURL 跟随 `E2E_PORT`，替换 `localhost:3000` 硬编码）
- Create: `frontend/public/course-samples/{text-motion-poster,image-motion-poster,ai-video-poster}.webp`
- Reuse: `ppt-work.webp`、`knowledge-card.webp`、`podcast-work.webp` 与既有三段 MP4
- Test: 首页 catalog/DOM/i18n、home-course-entry/landing E2E、视觉截图

- [x] 先更新失败测试：六卡同显、无分类按钮、无近期项目、初始 video/play=0；保留四 Agent URL 与两个 AI 弹窗交替卸载测试。
- [x] 保持创建边界：生产仅首页为 ai_image/ai_video 条件挂载并传 initialTaskType；focused 不渲染通用类型选择器。不新增 unfocused 挂载或 remotion_course/vimax 入口。
- [x] 移除首页 useThreads/ProjectList/查看全部，保留 `/workspace/chats` 与侧栏最近对话；不要全局断言线程搜索请求为零。
- [x] 六 ID 顺序固定为 course-video、ai_image、ai_video、creative-studio、ppt-master、podcast-studio；动作与真实路由不变，删除 entry 分类、技术 target/badge。
- [x] Hero 中文“让好内容，从一个想法开始”，副标题“选择一种能力，把文字、图片和资料变成视频、海报、PPT 或播客。”；英文使用 “Great content starts with one idea.” 与 “Choose a tool to turn your text, images, and source material into videos, posters, slides, or podcasts.”
- [x] 六卡说明依次为：①把文稿与知识点变成节奏清晰、易理解的动态讲解视频；②根据脚本生成画面，让静态内容自然流动；③从一个创意生成更自由、更丰富的动态镜头；④把主题与要点做成清晰、醒目的视觉海报；⑤从主题或资料生成结构清晰、可编辑的演示文稿；⑥把文档、公开链接或主题调研做成可下载的 MP3 播客。英文自然等义，不暴露内部 Agent ID。
- [x] 容器 max-w-5xl，桌面三列、平板两列、手机单列；媒体容器统一 16:9，auto-rows-fr + 弹性卡体等高，不截断英文，手机允许纵向滚动。
- [x] 仅生成并读回三张 960×540 视频 poster；三个静态卡复用现有图，统一 16:9 容器 `object-contain` + 中性背景，完整显示，不用 object-cover 硬裁，不新增静态重制图。
- [x] 初始仅 poster；hover 200ms/focus 仅挂载当前视频，离开/页面隐藏即停；触屏与 reduced motion 不挂载视频并关闭装饰动画。播放失败保留 poster 并提示“预览暂不可用，仍可开始创作”，图片失败退中性色块，点击始终可用。
- [x] Playwright 验证六入口、两行等高、390px/320px 无横向溢出、键盘/focus、媒体失败、中英文和静态图 computed object-fit=contain；修正 locale helper 使 cookie baseURL 跟随 E2E_PORT，执行 agent 用 ReadMediaFile 检查桌面/移动截图与两侧留白。

### 任务 6：为我的作品增加六类筛选与 Agent 对话历史（R5）

**Files**：新增 `work-capabilities.ts`、`agent-conversation-grid.tsx`；修改 `core/threads/utils.ts`、video-gen-page、completed-video-grid、三份 locale/type、E2E mock；对应 unit/DOM/E2E。

- [x] 建立六能力单一目录，映射严格保持 course-video、ai_image、ai_video、creative-studio、ppt-master、podcast-studio；标签取 i18n，不复制路由映射。
- [x] 新增 agentNameOfThread：依次读取 context.agent_name、metadata.agent_name、旧 metadata.project_type，再经 resolveAgentName；pathOfThread 复用。缺 metadata、lead_agent、co-writer、未知名不猜分类。
- [x] 测试 course-ppt→ppt-master 与五个 creative 旧别名；旧线程统一打开 `/workspace/agents/<canonical>/chats/<thread_id>`。
- [x] 四类 Agent 使用 useThreads 不传 limit 的完整分页（请求仍 50/页），按 thread_id 去重、updated_at ?? created_at 与 ID 稳定排序；offset 50 后目标也必须出现。
- [x] 卡片只显示真实 titleOfThread、能力类型、更新时间与“打开”；每类提供标题 includes 搜索（trim+大小写归一），首屏 6、每次 +6，不虚构封面、ready、下载或成品统计。
- [x] loading、error+retry、真空、搜索无结果分离；切能力清空搜索并复位 visibleCount。首版不提供 thread 删除。
- [x] 六类二级 tab 与外层 tab 从 URL 派生，点击 push、非法值 replace；刷新/前进后退恢复，方向键/Home/End 与 ARIA 完整。
- [x] CompletedVideoGrid 的 workType 改由父层 URL capability 驱动，移除内部重复 tab/local workType；切能力复位预览与 visibleCount。
- [x] 视频两类保留原卡和全局最近 100：ai_visual 归图片动效；draft/running/failed/ready 全保留，仅 ready 且非 missing 可预览/下载，原详情/重试/删除不变。
- [x] 仅在当前面板需要时挂载 threads/video 查询；E2E 六类逐一实际点击，覆盖搜索、分页、打开、错误重试、移动无溢出和播客 404 的诚实不可用态。

### 任务 7：精简我的资产项目详情且保全存量配置（R6）

**Files**：`course-project-detail.tsx`；新增 `course-project-simplified-detail.dom.test.tsx`，扩 create-payload、mock-video-api、video-assets E2E 与 remote 手测文档。

- [ ] 先写失败测试：新建和编辑详情均不出现“品牌包/色彩微调/关联视频”，基本设置、角色、风格锚图、道具与保存仍存在。
- [ ] 仅卸载 BrandPackSection、调色 Collapsible 和关联视频 JSX，清理闲置 import/state；不用 display:none 保留挂载。
- [ ] 保留 palette state、服务端回灌、PUT color_palette、dirty guard；保留 tasksQ/projectTasks/relatedTaskCount。
- [ ] 播种非默认品牌与 palette，修改标题保存后断言品牌不覆写、palette 逐字段不变；不再发品牌 LOGO/主题预览请求。
- [ ] 新建 POST 仍只含 title/可选风格，不强写 dark-blue；remotion_course+courseProjectId+follow_project payload 与默认品牌/中性后期不变。
- [ ] 任务详情和 remotion_course 分镜深链仍正确；只更新 e2e-remote 的旧品牌 UI 断言，夜间不执行 remote，改由本地 hermetic E2E 证明。
- [ ] 运行 knip；若 BrandPackSection 失去全部消费者，只清理无消费者的前端导出/死代码，不删后端品牌字段、资产中心写入能力或项目数据。

### 批次 C：本地集成收口

### 任务 8：本地全量门禁、隔离 API 行为验收、PR/OCR 与证据交付

- [ ] 定向测试全部转绿后冻结代码，后台串行运行 `cd backend && env -u RUN_LIVE uv run pytest tests/ -q`，再运行 `cd frontend && pnpm check && pnpm test`。
- [ ] 后台运行 `CI=1 E2E_PORT=3100 pnpm test:e2e && pnpm perf:check`；不得共享 `.next`/E2E 端口或修改被测输入，必要用例零意外 skip。
- [ ] 使用**隔离的本地测试 DB 与测试身份**，经真实后端 API 播种/读取：六类历史、offset 50 后记录、旧别名、本人/他人权限、非默认 palette 保存；不得触碰开发或用户 DB，不用纯 mock 冒充此层。
- [ ] 汇总 R1–R6/R8 行为证据、R2 本地镜像/live 证据及 R7/播客实时 URL 的 OUT_OF_SCOPE 说明；更新本计划勾选/状态头和执行 changelog，TL;DR 与「验收记录」写真实命令关键输出和本地证据路径。
- [ ] 最终文档修改后，从根目录运行 `make check && python3 scripts/check_docs_convention.py && git diff --check`；性能预算变化必须归因评审，不得无声抬线。
- [ ] 按预授权 push 当前功能分支，并用 `GH_REPO=littlebear2020/SuperTrainerAgent gh pr create --base main` **新建 PR**（标题为本批摘要，正文链接本计划与执行 changelog；PR #2 已合并，不得更新或复用）。等待新 PR 当前 HEAD 的 checks/OCR；Critical/Important/high 清零或以代码/复现证据正式驳回。CI/OCR 不可用记 `REMOTE_PENDING`，不直推 main。
- [ ] checks 全绿且评审处置完成后按仓库约定 rebase merge；若仅远端未决，保留本地 commits 并报告 `PARTIAL_REMOTE_PENDING`，不重复推送、不伪造合并。
- [ ] 清理本批不需要的测试输出与登记容器，恢复 AUTOBIND/本地服务到约定状态；最终报告逐项 PASS/FAIL/SKIPPED/BLOCKED、commit/PR/check/OCR ID、付费调用是否发生及原因、次日人工清单。

## 五、最终验收追踪表

| 需求 | 夜间 goal 必须看到的证据 |
|---|---|
| R1 | 普通/Agent 回复结束后 suggestions POST=0；新会话案例仍可预填。 |
| R2 | auto 只声明 access key；显式 speaker 单测；指纹跨 locale 同值且一字节变化；本地新 image ID、容器静帧；一次 TTS/ffprobe 或明确 LIVE_NOT_VERIFIED；worker diff=0。 |
| R3 | 我的空间、作品/资产深链、普通我的用量/管理员运营后台、邮箱派生用户名与角色。 |
| R4 | 普通仅账号/外观且隐藏组件零请求；管理员八项；伪造他人 user_id=403。 |
| R5 | 六类真实本地 API fixture；offset 50 后历史、旧别名、标题搜索、canonical URL；视频全状态不回归，映射不变。 |
| R6 | 新建/编辑均无三块 UI；非默认品牌/palette 经真实 API 保存前后一致；关联任务计数和任务深链正常。 |
| R7 | **OUT_OF_SCOPE**：无新默认层/迁移/素材行，无 doubao_tts 价格选择，不阻塞 goal。 |
| R8 | 首页无近期项目，六卡同显、初始零播放、单卡预览；静态图 contain 完整显示，移动端/英文/失败降级和截图检查通过。 |

## 六、假设与明确非目标

1. `12536796` 只作历史代码对照，不要求连接其运行环境；夜间 goal 不含任何服务器探针或部署。
2. 目标 Terra 为内部 `gpt-5.6-terra` + 上游 `openai/gpt-6.1-terra`；历史 live 记录不冒充本次本地新鲜探针。
3. 用户接受邮箱 local-part 作为当前用户名称；真实昵称/头像上传另批设计。
4. 六能力默认文字动效；不做服务端 cursor、全文搜索、thread 删除或恢复 remotion_course/vimax 创建入口。
5. R7 价格完全由用户后续配置；三条 Claude TBD 保持，doubao_tts 0.12/0.3 由用户后续决定，不是夜间阻塞。
6. 不实施定价后端、计价用量、空库播种、迁移、默认价或素材行改造；findings backlog 的独立缺口留后续。
7. 不顺手处理 vimax-video 空技能建议、旧模型探针名册和 useDeleteThread 双 DELETE，不代勾播客前序计划。
8. 任务 0–2 已按勾选完成代码修改、本地验收与一次付费 TTS；未修改价格、未部署，任务 3–8 未执行；整体 goal 的最终状态仍以任务 8 的真实证据为准。

## 七、次日人工验收与后续公司部署（不在夜间 goal 范围）

以下事项由用户次日在 Mac 手动验收；公司部署由用户验收后另行决定和执行，执行 agent 夜间不得代做：

1. 用普通账号与管理员各走一遍 R3/R4，确认角色文案、本人用量、设置项和底部用户区。
2. 在普通与 Agent 对话结束后确认无三个自动追问；检查新会话案例仍符合预期。
3. 逐一打开六类作品并搜索历史，检查真实数据、卡片观感、打开路由和既有视频操作。
4. 新建/编辑项目，确认三块 UI 隐藏且既有品牌/palette 未被清空。
5. 在桌面与手机检查首页文字、比例、PPT/知识卡留白、媒体预览和完整点击体验。
6. 以固定逐字稿完成一次 course-video 最短作品，检查 TTS 音色、模板版本提示、MP4 画面与声音；夜间 live 未验证项必须优先补做。
7. 用户决定 doubao_tts 价格口径、补做播客实时安全 URL 验收，并复核所有 `PARTIAL/SKIPPED/BLOCKED` 项。
8. 用户自行部署时仅携带必要镜像变化：本批**不需要重建 remotion-worker，不包含价格迁移，不应由 goal 推送任何镜像或访问公司/测试服务器**。

## 附录 A：前两轮评审已锁定的技术结论

- 定价后端改造已删除；普通估算价与真实 amount 的语义不得混淆，三条 Claude 的 TBD 不被强行填写。
- Terra 内部旧别名与 6.1 上游不可混用；另一个旧本地配置不是目标事实源。
- auto 正式路径显式 speaker；optional 不会缩小 AUTOBIND；两份 helper 当前并未分叉。
- worker 的 TEMPLATE_VERSION 只写不读，聊天链修复不涉及 worker 重建。
- 指纹采用 NUL+C；Ubuntu 两组参考值已核实，macOS en_US 不当作同值常量。
- PPT 和知识卡在 16:9 object-cover 下均裁上下；本计划改 object-contain。创建弹窗首页实际挂载必传 initialTaskType，prop 本身仍可选。
