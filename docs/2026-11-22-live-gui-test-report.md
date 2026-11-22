# 近一周更新全功能 GUI 测试报告（live 服务器黑盒）

> **TL;DR**：2026-11-22 对测试服务器（trainer-v3）做了近一周（11-15~11-22）代码更新的全功能 GUI 黑盒测试，逐点击+截图验证。两大新功能全部通过：个人用量页（角色分流/总览卡/六项筛选/三态/模型明细/分页/更新提示，含真实数据闭环 ¥0.0185+17,962 Token 全对账吻合）、模型模式阶梯（三档菜单/无思考档残留/按档过滤/合法模型回落）。**唯一实质发现 F1（中）**：部署 config 的模型花名册未按模式阶梯批同步——标准/Ultra 档无「默认」徽标、花名册与 plan 矩阵不一致。付费生成链路（真实出片/PPT 生成）按成本考虑未触发，详见 §5。

> 测试执行：ZCode 自动化代理（浏览器 GUI 黑盒，逐点截图 + DOM 双重验证）；测试日期：2026-11-22
> 代码基准：`origin/main@0e84b148`（2026-11-22，个人用量交付记录）
> 截图证据目录：`temp/worktree-archive-20261122/home-plan/temp/e2e-gui-20261122/`（gitignored，防账号信息入库；本文档为未跟踪本地件，与 `2026-11-16-live-e2e-bug-handoff.md` 同口径）

---

## 1. 环境与账号

- 被测：`<测试服务器地址>`（trainer-v3 栈；`/health` 返回 `{"status":"healthy","service":"deer-flow-gateway"}`）
- 测试账号：<测试账号>（**普通用户**角色——正好覆盖个人用量页的普通用户分流）；密码见会话记录，不写入本文档
- 浏览器：ZCode 内置浏览器，视口 1440×900
- 环境准备动作：仅「GUI 登录」一项；无任何数据预置/注入。测试中产生的唯一写入 = T4 的一条轻量聊天消息（闪速档，LLM 消耗 ¥0.0185，随后被用作 T2 有数据形态的验证素材）

## 2. 测试范围：近一周变更 → 测试映射

| 变更（origin/main 合入日） | GUI 可测性 | 本轮覆盖 |
|---|---|---|
| **个人用量页**（feat/fix usage ×9，11-21~22） | ✅ 核心可测 | T2 全覆盖（含真实数据闭环） |
| **模型模式阶梯**（闪速/标准/Ultra，11-21） | ✅ 核心可测 | T3 全覆盖（含发现 F1） |
| agent-showcase 视觉重制（11-17，9 commits） | ✅ 可测 | T5（案例卡/视频弹窗/播放帧） |
| 作品资产 IA（收口 11-13，边缘窗口） | ✅ 可测 | T6 冒烟 |
| 聊天链路回归（B2/URL、Token 统计） | ✅ 可测 | T4 |
| ppt-master 当前页执行包（11-19~21，skill 层） | ❌ 需真实 PPT 生成任务 | 未触发（付费+长任务），见 §5 |
| summarization 压缩修复（11-17，后端内部） | ❌ 压缩行为 GUI 不可直接观察 | 聊天冒烟已过；见 §5 |
| 沙箱 tiktoken vendoring（11-20，构建期） | ❌ 基建层 | 见 §5 |
| 媒体通道收尾修复（TTS 重试/gpt-image 回退/360 参考图上限，11-16） | ❌ 需真实生成任务 | 见 §5 |
| live E2E 套件入库（11-19） | — 测试基建 | 不属用户功能，不入 GUI 范围 |
| 沙箱空闲回收 2h（config） | ❌ 运维配置 | 见 §5 |

## 3. 测试结果明细

> 每项均按「操作 → DOM 验证 + 截图目检」双通道执行；全部通过项无页面可见错误、无空白区、无失败占位。运行时不支持 console 日志采集，以页面可见错误表现为证据（全程未出现）。

### T1 登录与工作台壳 ✅

- 步骤：打开 `/login` → 填账号密码 → 点「登录」
- 实际：落在 `/workspace`；侧栏四入口（首页/我的作品 Beta/我的资产/我的用量）+ 底部账号身份「普通用户」
- 首页「内容工作室」渲染完整：视频课三卡 + 图文音频课三卡，配图为新重制素材，无破图
- 证据：`t1_login_before.png`、`t1_login_after.png`、`t1_workspace_loaded.png`

### T2 个人用量页（核心新功能）✅

空态与结构（`t2_usage_initial.png`）：
- 普通用户访问 `/workspace/console` 挂载 `PersonalUsagePage`（角色分流生效，管理员 ConsolePage 未出现）
- 标题+副标题「六项能力的已核算费用与 LLM Token」；统计周期 = 近 7 天(默认按下)/14/30；筛选 = 全部+六项（文字动效/图片动效/AI视频/课程海报/PPT课件/语音播客，与 plan §1.1 文案一字不差）
- 两张总览卡口径注记：「含估算；未知不记为 0」「输入 + 输出，缓存读取不重复累加」；空态「所选条件下暂无用量记录」；底注「…不是正式账单」

交互（`t2_usage_30d_transient.png`、`t2_usage_filter_ppt.png`）：
- 7/14/30 切换按下态正确迁移；切换瞬间 `usage-updating` 筛选期提示可见（11-22 fix `8484cf7e` 生效，两次捕获均命中）
- 六项筛选切换正常，复位正常

有数据形态（T4 产生真实数据后回验，`t2_usage_with_data.png`、`t2_usage_model_detail.png`）：
- 已核算费用卡 **¥0.0185 + 「估算」徽标**（cost_source=estimated 正确上浮到总览）；总 Token **17,962**
- 任务行「视频制作流程与材料需求 / 已完成 / ¥0.0185 / 17,962 Token」——三态之一（已完成）实测；进行中/未完成两态无数据场景（由仓内单测覆盖）
- 展开模型明细：LLM 已核算 ¥0.0185 / **「媒体费用未核算」**（media_cost=null 的中性措辞红线 ✅）/ 模型表 `alibaba/qwen/qwen3.8-flash | 输入 17,692 | 输出 270 | 缓存读取 — | 总 17,962 | ¥0.0185 估算`
  - 总 Token = 输入+输出（17,692+270=17,962）✅；缓存读取缺失显示「—」而非 0 ✅（未知≠0 红线）
  - 费用与聊天页 Token 徽章（输入 16.9K/输出 257 + 标题生成等内部调用）量级吻合
- 「打开原任务」链接 → 正确跳回线程页，消息完整（`t2_open_original_task.png`）
- 文字动效筛选后任务仍在（capability 归组正确，`t2_usage_filter_course_video.png`）
- 近 30 天窗口与近 7 天数字一致（消耗均发生在今日，跨窗口对账正确，`t2_usage_30d_with_data.png`）
- 分页「1–1 / 1 项」，上一页/下一页均正确禁用

### T3 模型模式阶梯（核心新功能）✅ 含发现 F1

- 模式菜单恰好三项：**闪速 / 标准 / Ultra**，描述文案完整（「默认档：规划开…」「最强档：多子代理协作…」）；当前档带 ✓（`t3_mode_menu_open.png`）
- **旧「思考」档与「推理深度」入口零残留** ✅（切换循环多轮确认）
- 按档过滤生效：闪速档只列闪速模型（`t3_model_menu_flash.png`），标准/Ultra 各自独立清单（`t3_model_menu_standard.png`、`t3_model_menu_ultra.png`）
- 切档时合法模型保留、非法模型自动回落到合法集 ✅（Ultra→闪速回落到 qwen3.8-flash 默认）
- 闪速档默认徽标正常：「360·Qwen 3.8 Flash（默认）」✅
- **F1（见 §4）**：标准/Ultra 档清单无任何「默认」徽标
- 附：模式 hover 引导（mode-hover-guide）正常显示（`t4_send_before.png` 左下角浮层）

### T4 聊天链路（回归）✅

- 闪速档发送轻量消息（明确要求不触发生成管线，agent 正确只回确认文案，未启动视频管线、未产生媒体费用）
- **发送后 URL 立即落到真实 thread id**（`/workspace/agents/course-video/chats/904c6d81-…`）——11-16 bug 单 B2/P0-3 区域回归通过
- 流式回复正常渲染；完成后线程标题自动生成「视频制作流程与材料需求」；Tokens 徽章 18.0K、回复下方「输入: 16.9K 输出: 257 总计: 17.1K」（token_usage 中间件链路正常）
- 证据：`t4_send_before.png`、`t4_send_streaming.png`、`t4_reply_done.png`

### T5 agent-showcase 视觉（11-17 批）✅

- agent 新对话引导页三张案例卡渲染正常：真实产物配图、无黑底/奶油底回归、无布局重叠（`t3_agent_chat_initial.png`）
- 案例视频弹窗正常打开；首帧（0:00）与播放中帧（0:05）画面清晰、文字锐利、无重影/串帧伪影（`t5_showcase_video_playing.png`、`t5_showcase_video_mid.png`；注：转场单帧重影修复 f1aa4bd7 无法用静态截图穷证，抽样两帧未见异常）
- 附带确认：`/` 落地页为极简 hero（Header+Hero，无 showcase 卡）——showcase 挂载点在 agent 引导页，与代码一致；`/workspace/agents/new` 为智能体创建表单，渲染正常（`t5_agents_new.png`）

### T6 我的作品 / 我的资产（IA 收口冒烟）✅

- 「我的作品」六类 tab 与用量页六项口径一致；T4 产生的线程以卡片出现在「文字动效」tab（标题/时间/继续对话按钮齐全，`t6_video_gen_list.png`）
- 「我的资产」= `/workspace/video-gen?tab=assets`，空态「暂无课程项目，点击『新建项目』开始」+ 新建按钮（`t6_assets_tab.png`）

### 角色入口边界（观察项）

普通用户侧栏仅 首页/我的作品/我的资产/我的用量 四入口；writer、voice twins、gallery、console 管理台均无入口——与角色边界设计一致，未做绕过 URL 的直访测试。

## 4. 发现清单

### F1【中·部署配置】标准/Ultra 档无「默认」徽标；部署花名册与模式阶梯 plan 矩阵不一致

- **现象**：闪速档模型清单首项带「（默认）」（qwen3.8-flash）；标准档清单（Qwen 3.8 Max / GPT 5.6 Terra / Claude Haiku 5.5 / Claude Sonnet 5.5）与 Ultra 档清单（Qwen 3.8 Max / GPT 5.6 Terra / Claude Sonnet 5.5 / Claude Opus 5.5）**均无默认徽标**；标准档初始即选中 Claude Sonnet 5.5（非 plan 设计默认）
- **预期**（`docs/plans/2026-11-18-model-mode-ladder-plan.md` §1.1）：闪速=[qwen3.8-flash, deepseek-v4.1-flash] 默认 qwen3.8-flash；标准=[deepseek-v4.1-flash, claude-sonnet-5-5] 默认 deepseek-v4.1-flash；Ultra=[qwen4-max, kimi-k3.1-pro, claude-sonnet-5-5] 默认 qwen4-max
- **根因指向**：plan 明确「新增 qwen4-max / kimi-k3.1-pro」并升 config_version 31→32，但部署侧花名册中这些模型不存在，mode_defaults 指向的默认模型（deepseek-v4.1-flash / qwen4-max）不在档内 → 徽标无处可挂。前端按 `/api/models` 过滤与回落机制本身工作正常（回落选中的都是合法模型），缺陷在**服务器 config.yaml 未同步模式阶梯批的花名册/mode_defaults**（该批也无 changelog 存档；11-16 运维发现 A2 已记录服务器 config_version=35 与示例 31 是两条谱系）
- **影响**：用户在标准/Ultra 档看不到官方默认模型引导；档内模型面与设计不符（Terra/Opus/GLM/Haiku 入档，qwen4-max/kimi/deepseek 缺席）；功能可用但与设计意图偏离
- **建议**：核对测试服务器（及公司服务器）config.yaml 的 `models[].modes` 与 `mode_defaults` 是否按模式阶梯批更新；若服务器实配有理由保留现花名册，回填一份决策记录（plan 或 changelog）防漂移
- **证据**：`t3_model_menu_flash.png`（有默认徽标，对照）、`t3_model_menu_standard.png`、`t3_model_menu_ultra.png`（无徽标）

### 观察项（不构成缺陷）

- `/` 落地页为极简 hero——若预期是含 showcase 卡的营销页需另确认（代码 `app/page.tsx` 即 Header+Hero，与实现一致）
- 用量页「进行中/未完成」两态、`usage-retry` 错误重试条：本次无对应数据/故障场景，未实测（仓内有单测覆盖）
- 11-16 bug 单中 B1（重开停顶）仅对长对话明显，本轮线程为短对话，未复验该场景

## 5. 未覆盖范围与原因

以下近一周变更**未做 GUI 实测**，均为「需触发真实付费生成/长任务」或「GUI 不可直接观察」类：

1. **ppt-master 当前页执行包**（11-19~21 五连 fix）：需真实跑一次 PPT 生成任务验证首读顺序/执行包行为；付费+分钟级长任务，且该批已带自身冒烟与 OCR 验收记录
2. **summarization 压缩修复**（11-17）：上下文压缩是后端内部行为，GUI 只能间接观察；需长对话逼近阈值才触发
3. **媒体通道收尾修复**（TTS 重试重建请求、gpt-image 回退 Azure、360 参考图总量上限，11-16）：均需真实生成任务触发异常/超限路径
4. **沙箱 tiktoken vendoring**（11-20）：构建期/基建层，本次聊天未走到沙箱技能执行路径，未专项验证
5. **视频生成两类任务（图片动效/AI 视频）真实出片**：付费生成，本轮仅冒烟列表/入口形态（该链路 11-16 已做过全量 live E2E，且 11-19 交接文档已登记其已知问题清单）

## 6. 验收记录

| 验证点 | 实际结果关键行 |
|---|---|
| 登录 | 点击「登录」→ URL 变为 `/workspace`，侧栏出现「我的用量」入口 |
| 用量页挂载 | 普通用户开 `/workspace/console` → DOM 出现 `heading "我的用量"` + `group "统计周期"`(3 键) + `group "按能力筛选"`(7 键) |
| 筛选期旧数据提示 | 点击「近 30 天」→ `[data-testid="usage-updating"]` 3s 内可见（两次交互均命中） |
| 用量闭环对账 | 展开明细 → `row "alibaba/qwen/qwen3.8-flash 17,692 270 — 17,962 ¥0.0185 估算"`；17,692+270=17,962 与任务行、总览卡一致 |
| 未知≠0 红线 | `definition: 媒体费用未核算`；缓存读取列显示 `—` |
| 原任务回跳 | 点「打开原任务」→ URL `/workspace/agents/course-video/chats/<脱敏任务ID>`，消息完整 |
| 三档模式 | `menu "标准"`/`menu "闪速"`/`menu "Ultra"` 各含恰好 3 个 menuitem；全部 snapshot 中无「思考」「推理深度」字样 |
| URL 落真实 id | 发送后 5s 内 URL 由 `/chats/new` 变为 `/chats/904c6d81-…` |
| Token 统计 | 回复完成 → `button "Tokens 18.0K"` + 「输入: 16.9K 输出: 257 总计: 17.1K」 |
| 窗口对账 | 近 30 天与近 7 天 summary 均为 `¥0.0185 / 17,962` |

## 7. 截图索引（`temp/worktree-archive-20261122/home-plan/temp/e2e-gui-20261122/`）

| 文件 | 内容 |
|---|---|
| t1_login_before / t1_login_after / t1_workspace_loaded | 登录页 → 登录后 → 首页内容工作室 |
| t2_usage_initial | 用量页空态（总览卡/六项筛选/口径注记） |
| t2_usage_30d_transient / t2_usage_30d_settled | 30 天切换瞬态（updating 提示）与稳态 |
| t2_usage_filter_ppt / t2_usage_filter_course_video | 筛选交互 |
| t2_usage_with_data / t2_usage_model_detail / t2_open_original_task / t2_usage_30d_with_data | 有数据形态/模型明细/原任务回跳/30 天对账 |
| t3_agent_chat_initial | agent 引导页（showcase 案例卡） |
| t3_mode_menu_open | 三档模式菜单 |
| t3_model_menu_flash / t3_model_menu_standard / t3_model_menu_ultra | 三档模型清单（F1 证据） |
| t4_send_before / t4_send_streaming / t4_reply_done | 发送前(hover-guide)/流式中/完成+Token 统计 |
| t5_landing_top / t5_agents_new / t5_showcase_video_playing / t5_showcase_video_mid | 落地页/智能体创建/视频弹窗/播放中帧 |
| t6_video_gen_list / t6_assets_tab | 我的作品/我的资产 |
