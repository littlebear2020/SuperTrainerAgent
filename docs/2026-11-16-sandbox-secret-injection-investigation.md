# 沙箱密钥已配置但未注入：问题复盘与后续研究交接

> **TL;DR**：`.env` 中的 TTS 配置实际已成功加载到宿主，但请求级 secrets 未提供、`DEER_FLOW_SKILL_SECRET_AUTOBIND` 默认关闭，因此沙箱 bash 没收到密钥。这是安全收紧后的产品接线与诊断缺口，不是“用户没配置”；进程级开启 AUTOBIND 后，真实豆包 TTS 已恢复。

## 1. 问题现象

用户已在 `.env` 配置字节 TTS 相关变量，但 course-video 的 Agent 在沙箱中检测到 `VOLCENGINE_VOICE_ACCESS_KEY` 缺失，无法合成配音。

最初的“项目没有配置密钥”判断不准确。准确表述应为：**宿主已配置，但当前运行链没有将密钥授权并注入沙箱。**

## 2. 已验证的证据链

诊断只检查变量存在性，没有读取、复制或输出密钥值。

| 检查层 | 实际结果 | 结论 |
| --- | --- | --- |
| 配置加载前 | 目标变量不在当前 shell | 仅说明尚未加载配置 |
| `get_app_config()` 后 | `VOLCENGINE_VOICE_ACCESS_KEY=True`、`DOUBAO_TTS_DEFAULT_SPEAKER=True` | `.env` 中的两个变量均被成功加载 |
| `sandbox.environment` | 空 | 没有旧的容器级统一下发 |
| 技能声明 | 目标 key 在已启用技能的 required-secrets 声明集合中 | 技能声明本身正常 |
| AUTOBIND | 未开启 | 自动注入通道未启用 |
| 默认注入结果 | `key_count=0`，无 TTS key | 沙箱 bash 收不到 key |
| 仅在诊断进程设置 `DEER_FLOW_SKILL_SECRET_AUTOBIND=1` | TTS key 和默认音色均进入待注入集合 | 开关与故障存在直接因果关系 |
| 本地真实 Agent 验收 | 后续成功生成男声云舟 2.0 音频与字级时间轴 | 密钥有效，原故障不是 provider 帐号或网络问题 |

进程级 AUTOBIND 探针观察到 4 个可注入变量名：`ARK_API_KEY`、`DOUBAO_TTS_DEFAULT_SPEAKER`、`QIHU_API_KEY`、`VOLCENGINE_VOICE_ACCESS_KEY`。这里只记录名称；AUTOBIND 的范围不是单 key，也不是 course-video 专属。

## 3. 代码机制与信任边界

- `.env` 由 `backend/packages/harness/deerflow/config/app_config.py:34` 的 `load_dotenv()` 加载到宿主进程。
- 技能通过 frontmatter 声明所需变量，例如 `skills/public/auto-video-flow/SKILL.md:5` 只声明 `VOLCENGINE_VOICE_ACCESS_KEY`。
- `backend/packages/harness/deerflow/sandbox/tools.py:683-773` 实现 bash 的 secrets 装配：
  - active secrets；
  - request secrets；
  - 最后才是 AUTOBIND。
- `_autobind_skill_secrets()` 在 `backend/packages/harness/deerflow/sandbox/tools.py:730` 默认关闭；开启后取“所有已启用技能声明的并集 ∩ 宿主已有环境变量”，在每次 bash 调用时注入。
- 这不是简单恢复旧 `sandbox.environment`：旧方式让容器内任意 `env` 都能读取常驻密钥，新方式意图收窄为声明键、per-call 注入并对输出打码。
- 因此，安全收紧本身合理；缺口在于配置、授权、运行和诊断之间没有形成用户可理解的闭环。

## 4. 责任与问题归类

### 产品设计/接线缺口

- 用户配置页面或 `.env` 只表达“密钥存在”，没有清楚表达“是否允许进入沙箱”。
- 当前运行没有提供请求级 secrets 时，界面与错误信息仍容易表现为“没有配置”。
- AUTOBIND 是全局、粗粒度开关，缺少按用户、Agent、技能或 run 的细粒度授权入口。
- 启动检查与 doctor 没有把“宿主存在但沙箱未注入”作为可诊断状态。

### Coding agent 本次诊断失误

- 不应把沙箱内 `MISSING` 直接推断成用户未配置。
- 应先分层检查宿主加载、技能声明、请求上下文、AUTOBIND 和实际 per-call 注入。
- course-video Agent 在沙箱内报告缺失本身属实，但缺少宿主侧信息，无法给出完整根因。

## 5. 影响范围

在“无 active/request secrets 且 AUTOBIND 关闭”的条件下，所有依赖**沙箱 bash + required-secrets** 的 Agent/技能都可能复现，包括课程视频、播客、AI 生图/视频及课程助理对这些技能的调用。

以下路径不必然受影响：

- 后端宿主直接调用 provider 的服务，密钥不经过沙箱 bash；
- 不需要密钥的文档解析、Remotion 编码、本地渲染；
- 已经通过 active/request secrets 正确授权的运行。

反向风险同样重要：开启全局 AUTOBIND 后，受影响的不只是当前 Agent。应评估所有带 bash 的 Agent 在每次调用中可获得的声明键集合，不能把“用户配置了 key”等同于“允许所有沙箱任务读取 key”。

## 6. 后续优化候选

| 方案 | 收益 | 代价/风险 |
| --- | --- | --- |
| A. 本地开发显式开启 AUTOBIND | 改动最小，已验证可恢复 TTS | 范围全局且较粗；不应未经验收就作为生产默认 |
| B. 改善错误与 doctor 检查 | 能直接区分“未配置/未授权/未注入”，减少误判 | 不解决授权 UX，只改善可诊断性 |
| C. 按 Agent/技能配置注入白名单 | 比全局 AUTOBIND 收窄，便于静态管理 | 仍需定义用户授权、配置 schema 与运行时优先级 |
| D. run 级用户授权 + 当前激活技能绑定 | 最符合最小权限和按需授权 | 需要完整前端/Gateway/run context/审计接线和安全评审 |
| E. 把 TTS 移到宿主服务 | 避免沙箱直接接触 provider key | 改变模块边界，增加服务与任务链复杂度，不是当前问题的必要修复 |

**推荐组合：**短期做 B，并保留 A 作为本地显式开发选项；中长期优先研究 D，以 run、用户和实际激活技能为边界。不要把 C 的静态白名单误当成用户当场授权，也不要把 D 的 hash/确认记录误当成可抵抗恶意模型的审批凭证。

## 7. 新会话待研究问题

1. active/request secrets 当前有哪些真实生产者与消费者？前端、Gateway 和嵌入式 client 是否都能装配，还是只有框架接口存在？
2. 产品希望“一次授权”作用于单 run、单线程、单 Agent 还是长期偏好？撤销和过期如何处理？
3. 注入集合应按声明并集、实际激活技能，还是用户逐项选择的变量名计算？
4. 如何在不暴露值的前提下展示“宿主已配置、授权范围、注入结果、失败原因”？
5. AUTOBIND 开启时的全局 bash 面是否符合本地与生产各自威胁模型？现有 env_policy 警告为何允许声明键继续注入，审计是否足够？
6. 是否增加 hermetic 测试证明：未授权时零注入、授权后仅允许集合、输出打码、不同用户/线程/Agent 不串密钥？
7. 本地开发与生产是否采用不同默认，如何避免部署文档和实际运行口径漂移？

## 8. 建议的最小验证矩阵

- 宿主未配置 → 明确提示未配置，不进入付费调用。
- 宿主已配置但未授权 → 明确提示未授权，沙箱中变量缺席。
- run 级授权目标 key → 只允许该 key，其他声明键仍缺席。
- 同一线程切换 Agent → 不继承超出新 Agent/技能范围的授权。
- 不同用户/线程 → 不共享 secrets 或注入缓存。
- bash 输出包含注入值 → 日志和工具结果均打码。
- provider 失败 → 保留真实错误，不自动换 provider 或伪造成功。

## 9. 当前本地状态

本批只使用进程级 `DEER_FLOW_SKILL_SECRET_AUTOBIND=1` 做本地验收，没有修改 `.env`、持久化全局开关或 Docker 配置。后续新会话应先读代码验证现状，再决定授权模型，不要把本次临时 workaround 当成已经落地的产品方案。

相关执行背景：`docs/plans/2026-11-16-course-video-workflow-convergence-plan.md`、`docs/changelog/changelog-2026-11-16-course-video-workflow-convergence.md`。
