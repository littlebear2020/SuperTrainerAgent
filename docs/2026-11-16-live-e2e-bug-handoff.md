# Live E2E 测试 Bug 清单(交核实 Agent 用)

> **TL;DR**:2026-11-16 对火山云测试服务器(main 8c915775)全功能 live E2E + 三轮审查/复核(video-gen 资产页 E×7、writer 等未审模块 F×12 为第三轮新增)后,共 **58 项记录**。三轮复核共**证伪/降级 5 项**(P0-1 串台、P0-2 切线程滚动、F2 邀请码清空、B6 原场景未复现、P1-1 实测标注错位),**坐实高优 7 项**:B1 重开停顶、B2+P0-3(URL/流 clear)、C1 含%崩面板、C2 确认卡丢编辑、D1 停止清草稿、F1 writer 回滚冻结自动保存、F5 声线上传失败零提示;另有 E1 video-gen 轮询停机族(5 入口)、F6 登出缓存泄漏等中高优。所有实测均注明方法,代码推演与实测冲突以实测为准。

## ⚠️ 复核更正(2026-11-16 第三轮,优先读)

1. **P0-1「切线程/新对话消息串台」→ 证伪撤回**。干净复测(只查主内容区、用消息正文独有词避开侧栏标题污染):①已有线程点「新对话」→ 正常显示 agent 引导页,无旧消息;②侧栏 A(course-video)→B(podcast)切换 → B 内容正确、无 A 残留。此前"实测坐实"是 body 全文匹配被侧栏线程标题("…垃圾分类…")污染的误报。useThreadHistory 状态不重置的代码推演与实测不符——核实 agent 若要翻案需给出可复现场景。
2. **P0-2「切线程滚动不重置」→ 降级**。实测 A→B 切换后落底正常(scrollTop=2095=底部)。保留为 B1(重开停顶,实测 scrollTop=0 坐实)的修复线索(ref 残留分析),不再单列 P0。
3. **P1-1 的「黑盒实测坐实」标注 → 更正**。当时实测场景(流式中回车)实际验证的是 **D1(回车=停止+清空草稿)**:代码链 handleSubmit 先判 streaming→onStop→早退 resolved→composer 清空,与探针观察(转圈消失+文本蒸发)吻合。P1-1 本体(agent 页上传窗口 sendInFlightRef 静默 return)保持「代码确定、未实测」。
4. **B7 播客语速 → 弱化为观察**。测试提示词自相矛盾(~160 字撑不满 60s),不能据此断定语速缺陷。
5. **P2-1(error 态输入框)→ 降置信**:被杀线程重开实测输入框可用、无 error 态,未复现。
6. C2/C8/C9 经人工读码复核**维持成立**(确认按钮 dirty 时可点但提交 props 旧值;覆盖后的 `a` 对外链无 `_blank`;运行时拼接 Tailwind 类无 safelist)。
7. **第三轮(扩面+行为复核)再更正**:**F2 邀请码清空 → 证伪降级**——API 实测后端 schema `min_length=6` 直接 422 拒空码,码不可能被清空;真实缺陷降为"不改码保存设置会 422 报错"的 UX 问题。**B6 创建框残留 → 原场景实测未复现**(图片动效/AI视频各连续开关两次,类型均正确),降为低优先观察。**F1 writer 回滚冻结 → 代码三环节亲手核验成立**(后端 create_version bump / 前端丢弃返回的新版本号只取 content / saveConflict 早退冻结自动保存),浏览器级实测因选择器未走完,标注"代码确定、实测待补"。P1-4 localStorage 死键:探针上下文无匹配键(该上下文未在新会话选模型),不构成证实也不证伪,维持代码推演。

## 第三轮扩面发现(E 系列:video-gen/资产页;F 系列:writer/twins/console/settings/auth/gallery)

> 两路专项审查,均要求给出可证伪复现路径与对冲说明;重要项的行为级复核结论见上方更正第 7 条。

### E 系列:视频生成模块与作品资产页

| # | 级 | 问题 | 位置 | 置信度 |
|---|---|---|---|---|
| E1 | P1 | recompose/re-render 60s 宽限守卫只挂在详情页确认弹层一个入口,其余 5 个触发点(音频自动重合成/整片重生成/项目更新重生成/预览转正式/列表与分镜页重试)在信号量竞争下 DB 状态翻转滞后 → 单次 invalidate 拿旧态 → 轮询停死,错过 composing→ready;切窗口焦点才恢复(focus-refetch 是唯一对冲) | 守卫:video-gen-detail.tsx:205,211,997;停机判定:hooks.ts:137-145,111-125;无守卫入口:audio-settings.tsx:178,434、detail.tsx:561-565,836-839、video-gen-page.tsx:272-277、storyboard.tsx:1483-1487;后端翻转滞后:task_framework/service.py:844-883 | 确定(逻辑链+后端已核;触发频率取决于信号量竞争) |
| E2 | P2 | remotion 素材区「重新生成配图/视频」:后端 202 异步,前端不接 onRegenStart(regenPending 恒空)→ storyboard 不轮询,完成后 UI 不刷新;202 后按钮立即可再点,连点=重复计费 | remotion-course-asset-area.tsx:85,109,211 + detail.tsx:252-257 + 后端 video_gen.py:2819-2831 | 确定(代码链;对冲仅 focus-refetch) |
| E3 | P2 | remotion 分镜页硬编码 pollAudioReady=false + 帧音频不接 onGenerateStart → generate-audio(异步 started)后 UI 永不翻 audio_ready;audioVersion 破缓存参数因 task 查询停用而冻结 | remotion-course-storyboard.tsx:741,560-565,737,832 + 后端 video_gen.py:3305-3341 | 确定 |
| E4 | P3 | 分镜编辑「视觉来源/版式切换/一键关闭图文镜」失败静默回弹无 toast(同文件其他 mutation 都有 M8 补偿,这三处漏) | hooks.ts:1208-1212,1242-1246,1271-1275 | 确定(影响低) |
| E5 | P3 | 帧操作面板「重生画面」不拦未保存的 prompt/视觉来源(F6 只修了 narration 维度),按旧值付费生成 | video-gen-frame-ops-panel.tsx:214,310-317 | 疑似(有文案弱对冲) |
| E6 | P4 | 作品页类型过滤只放行 ai_image/ai_visual/ai_video → remotion_course/vimax 任务在「我的作品」无入口;创建对话框的视频类型选择器/话题生成模式在当前唯一挂载点(focused 恒 true)不可达,且仍有指向死 UI 的提示文案 | completed-video-grid.tsx:38-39 + video-gen-create-dialog.tsx:1781-1876,1587-1592 | 疑似(可能是架构收敛,但死文案表明至少有遗忘) |
| E7 | P4 | 资产视图(CourseProjectPage)加载失败无重试按钮(works 网格有) | course-project-page.tsx:115-118 | 确定(影响小) |

E 系列已核查无问题:列表轮询单 query 不放大 QPS、详情页状态映射完整、下载门控、增删帧 409 反馈、重排乐观回滚、SSE 退避清理、字幕校验、无跨任务状态污染。

### F 系列:writer / twins / console / settings / auth / gallery / share / agents / 壳

| # | 级 | 问题 | 位置 | 置信度 |
|---|---|---|---|---|
| F1 | 高 | **writer 版本回滚后必发伪 409,自动保存冻结**:后端 restore 先 create_version(bump);前端 handleRestore 丢弃返回的新 WriterDoc 只取 content_json → TipTap setContent 触发 3s 防抖 → PUT 携带旧 base_version → 409 → saveConflict=true → doSave 早退,此后编辑只落 localStorage | version-panel.tsx:102-116 + writer-page.tsx:1541-1543,816,794,855 + api.ts:663 + 后端 writer_service.py:380-393 | **代码三环节亲手核验成立**;浏览器实测待补(选择器) |
| F2 | ~~高~~→**P3** | ~~邀请码被清空~~ **证伪**:后端 min_length=6 拒空码;真实问题=不改码保存设置会 422 报错(UX) | account-settings-page.tsx:63-101 + 后端 auth.py:654-690(schema 校验为对冲) | **API 实测证伪数据丢失**;422 UX 降 P3 |
| F3 | 中高 | 3s 防抖窗口内提交标题/类型变更 → 已排定的旧 doSave 闭包持旧 version → 409 冻结(与 F1 同后果);再敲一字可自救 | writer-page.tsx:1176-1189,674-685,793-921 | 确定(路径确定,命中看操作顺序) |
| F4 | 中 | 标题改名/改类型 PUT 的 409 被全局 onError 吞掉,标题静默回退无提示 | core/writer/hooks.ts:109-117 + writer-page.tsx:1184,679 | 疑似(时序窗口小) |
| F5 | 高(单点) | 声线克隆上传:服务端预检 fail 以 HTTP 200 返回,前端只处理 ok/pass → **失败零提示**,UI 停回初始态 | upload-panel.tsx:130-140,184-194 + api.ts:43-56 + 后端 voice_twin/service.py:146-174 | 确定(前后端均核) |
| F6 | 中 | 登出不清 react-query 缓存(模块级单例 QueryClient,logout 无 clear/invalidate)→ 换号登录 30s 内可见前一账号的运营台数据与列表;localStorage 草稿/线程设置同样残留 | AuthProvider.tsx:89-105 + query-client-provider.tsx + core/console/hooks.ts:34 | 确定(缓存事实;泄漏窗口=staleTime) |
| F7 | 低 | 401 拦截重定向只带 pathname 丢 query 参数;validateNextParam 拒绝带端口的 next | fetcher.ts:83-86 + core/auth/next-path.ts:31 | 确定(命中面小) |
| F8 | 低 | 邀请码过期时间回显用 UTC 截断,东八区显示差 8 小时 | account-settings-page.tsx:53-55 vs 77-79 | 确定(纯显示) |
| F9 | 低 | 删除文档未清防抖 timer,卸载后补发 PUT 报 404 toast(窗口 <3s) | writer-page.tsx:1156-1159,641,921 | 疑似(时序) |
| F10 | 低 | 损坏的 localStorage 草稿 JSON.parse catch 后把原始 JSON 串当正文载入并可能被自动保存固化 | writer-page.tsx:766-773 | 疑似(边缘) |
| F11 | 低 | gallery 401 专属 toast 分支为死代码(fetcher 已先硬跳转) | use-gallery-page-state.ts:168-175,244-246,292-294 | 确定(影响低) |
| F12 | 低 | AgentGallery 主题值无 SSR mounted 门控,暗色首帧闪 light | agent-gallery.tsx:19 | 确定(极低) |

F 系列已核查无问题:自动保存网络失败路径(toast+重试+localStorage 双写+beforeunload)、分享 token 权限后端校验、share 页 XSS(rehype-sanitize 白名单)、console 图表空态/除零护栏、channels 掩码凭据合并、agents 列表一致性、sidebar 壳。
>
> 来源:live E2E 套件(`frontend/tests/e2e/live/`,15 用例全绿)+ 黑盒探针实测 + 专项代码审查;产物档案在测试机本地 `frontend/test-results/live-artifacts/`。本文档为交核实用快照,核实结论请另行登记。

## 环境

- 被测:<测试服务器地址> (trainer-v3 栈,镜像 v3-20261116,main@8c915775)
- 测试账号:<测试账号>(管理员;凭据见仓库外 <仓库外运维记录>)
- 关键测试线程(服务器上可直接查):
  - 课程视频·完成态:`<脱敏任务ID>`(95 步,交付 mp4)
  - 播客·完成态:`<脱敏任务ID>`
  - PPT·被杀 error 态:`<脱敏任务ID>`
  - 课程视频·被杀态:`<脱敏任务ID>`

---

## B1 🔴 重开历史对话停在最顶部,最终交付内容不可见

- **现象**:打开历史线程,消息容器 `scrollTop=0`(实测:总高 4265px / 视口 680px / 最终交付消息在 y≈2884px)。长对话(课程视频 95 步)重开后看到的是自己的提问+工具调用+Token 统计,AI 最终成品消息在 4 屏之外,体验上像"结果丢了"。
- **复现**:登录 → 侧栏打开「30秒垃圾分类科普视频」线程 → 观察滚动位置。
- **疑似位置**:`frontend/src/components/workspace/messages/virtual-message-list.tsx` / `message-list.tsx` / `message-list-item.tsx`;线程页消息水合完成的 effect(找 scrollTo/scrollIntoView/bottom 锚点的缺失或时机问题)。短对话(≤1 屏)不可见该问题——L2 短线程重开测试是过的。
- **期望**:历史消息加载完成后落到底部(最新消息)。
- **附带**:底部内容被悬浮输入框遮住一角(布局 padding/z-index,与滚动问题独立)。

## B2 🔴 agent 对话运行期间 URL 停在 `/chats/new`,刷新即丢现场

- **现象**:agent 长任务运行中(实测课程视频 39 分钟),页面 URL 一直是 `/workspace/agents/<a>/chats/new`;前端期间轮询 `/api/threads/new/history`——该接口对 "new" 返回**空**(实测 0 条,线程不存在)。运行中刷新 → 落在空白新会话页,只能靠侧栏找回。短对话结束后 URL 会正常落定(实测),疑似仅在流结束回调里才 router.replace。
- **复现**:向 course-video 发起生成 → 运行中观察 URL 与网络请求 → 刷新。
- **疑似位置**:`frontend/src/core/threads/hooks.ts`(onSend/onFinish 里线程 ID 的获取与 URL 替换时机)、agent 聊天页组件(`src/app/workspace/agents/[agent_name]/chats/...`)。
- **期望**:消息发出、服务端建线程拿到 ID 后立即 `router.replace` 到真实 URL;顺带让 mid-run 的 history 轮询拿到真实 ID。

## B3 🟡 present_files 交付文件在消息流里不可点击

- **现象**:agent 交付产物后,文件名只以行内代码文本列在回复里(截图证实),下载入口在右侧产物面板;DOM 中 `a[href*="/api/threads/"]` 存在但不在消息流内。用户不易发现下载点。
- **疑似位置**:`frontend/src/core/messages/utils.ts`(extractPresentFiles 消费方)、`message-group.tsx`、`artifacts/artifact-file-list.tsx`/`artifact-trigger.tsx` 的挂载位置。
- **期望**:消息内渲染文件交付卡片(文件名+大小+下载按钮)。

## B4 🟡 沙箱密钥注入默认关闭,播客 TTS 开箱即坏(preflight 缺检查)

- **现象**:`DEER_FLOW_SKILL_SECRET_AUTOBIND` 默认关;播客技能需 `VOLCENGINE_VOICE_ACCESS_KEY` 进沙箱,拿不到就 fail-closed 拒绝出片(agent 行为正确)。默认部署配置下该能力不可用且无任何启动告警。已在测试机加 AUTOBIND=1 修复(见 ops 记录),**产品层缺一道防呆**。
- **核实点**:部署预检(`v3_preflight_config.py`,服务器 scripts-v3 下;仓库侧对应 backend/scripts 或部署文档)应增加「启用技能 required-secrets ∈ 注入可达集」检查。
- **相关口径**:`docs/personal-server-deployment-guide.md` 的 AUTOBIND 说明段。

## B5 🟡 冷启动 worker 与 gateway 建表竞态

- **现象**:全栈冷启动时 production-worker/remotion-worker 先于 gateway 建表去查 SQLite,刷 `no such table: unified_tasks` 错误日志,数秒后自愈。
- **疑似位置**:worker 启动轮询逻辑(`docker/production-worker/` 的 worker 入口)加启动重试;或 compose(`docker/docker-compose-deploy.yaml` 及服务器 v3 compose)让 worker `depends_on: gateway: condition: service_healthy`。
- **影响**:日志噪音+误导排障;功能无损。

## B6 🟡 首页创建框「类型残留」(已知沿袭,本次未复现)

- **现象**:README 记录——创建框连续开关两次,第二次可能残留上次任务类型。本次测试序列(图片动效→Esc→AI 视频)未复现(第二次标题正确「创建 AI 动态视频」);原场景(同一框连续开关)未验证。
- **疑似位置**:`frontend/src/components/workspace/video-gen/video-gen-create-dialog.tsx`(已有 `presetResidual` 相关代码痕迹,L779 附近),状态复位时机。
- **核实**:按原场景(同框开关两次)补复现用例再修。

## B7 🟢 播客产出时长与预期偏差大

- **现象**:要求"约 60 秒原文朗读"(~160 字),实测产出 22.0s——TTS 语速档偏快或时长估算未反推语速。功能可用,体验与预期不符。
- **疑似位置**:`skills/public/podcast-studio/`(render 时长/语速逻辑;SOUL.md 的分句与拼接约定)。

## B8 🟢 匿名访问工作台先渲染壳再跳登录

- **现象**:未登录直接开 `/workspace`,前端先把工作台骨架渲染出来(慢网 5s+),数据请求 401 后才客户端跳 `/login?next=...`(跳转本身正常,API 全 401 安全无损)。
- **疑似位置**:无服务端中间件守卫;考虑 Next middleware 做 cookie 存在性检查的首跳重定向。

## B9 🔵(待解释,可能安全相关)token_version 被莫名提升,全部会话吊销

- **现象**:2026-11-16 19:37 后,所有已发会话 JWT 突然失效,`/api/v1/auth/me` 返回 `token_invalid: "Token revoked (password changed)"`,但密码**未被修改**(旧密码仍可登录,实测 200)。疑似有某操作/路径错误地提升了 `users.token_version`(报错文案也误导——写死"password changed")。
- **核实点**:`backend/app/gateway/routers/auth.py` 及用户路由中所有 `token_version += 1` 的调用点(改密/登出全部设备/…);对照服务器 gateway 日志 19:37-20:00 窗口的写操作。若确认存在非预期提升路径,升安全级。
- **测试侧影响**:期间打开任何历史对话会白页跳登录,易与 B1/B2 混淆。

---

## 深度代码审查发现(2026-11-16 追加,交核实 Agent 复核)

> 两个贯穿性事实(多条发现依赖):
> **F1** 同段路由切线程不重挂载——侧栏 `Link` 导航是同动态段换参,两个 chats layout 均无 key,page 内所有 useState 跨线程存活(`recent-chat-list.tsx:234`;`workspace/chats/[thread_id]/layout.tsx`、`workspace/agents/[agent_name]/chats/[thread_id]/layout.tsx`)。
> **F2** langgraph-sdk `useStream` 在受控 threadId prop 变化时 `stream.clear()` 并 abort 在飞 SSE(`node_modules/@langchain/langgraph-sdk/dist/react/stream.lgp.js:161-166`)。

### P0(严重,直接影响正确性)

**P0-1 切线程历史消息「串台」**【确定|代码】
`frontend/src/core/threads/hooks.ts:639-688`——`useThreadHistory` 的 `messages/runsRef/indexRef` 切线程不重置:旧线程消息保留,新线程 run 消息前置拼接到旧消息上(`setMessages(prev => [..._messages, ...prev])`,661 行);`mergeMessages`(94-121 行)只按 live id 去重,拦不住。触发:侧栏任意切线程;**从已有线程点「新对话」→ /chats/new 直接显示上一个线程的完整对话**。连带骨架屏条件永假(闪旧会话)、`hasMore` 跨线程沿用旧游标。

**P0-2 切线程滚动/虚拟化状态不重置(B1 的全量变体)**【确定|代码】
`virtual-message-list.tsx:140-145, 227-256`——`positionedInitialVirtualWindowRef/previousCountRef/anchorRef` 等组件级 ref 跨线程存活;Conversation 容器无 threadId key。切线程后 scrollTop 停旧位置/被钳;曾虚拟化定位过的会话,新线程**首帧落底逻辑被跳过**(231-233 行)→ 不止"重开停顶"(B1),**每次切线程都复现**。另:60 组阈值处静态↔虚拟分支互切时滚动跳变(139 行,独立触发面)。

**P0-3 agent 页首条消息流被 clear 中断重放(B2 根因的另一半)**【代码路径确定|后果疑似】
`core/threads/hooks.ts:180-199` + `agents/[agent_name]/chats/[thread_id]/page.tsx:88-100`——agent 页**没传 `onSend`**(chats 页有,67-70 行),threadId prop 直到 `onCreated→onStart` 才翻转,此时 SSE 在飞 → 触发 F2 `stream.clear()` 中断首流,靠 SDK rejoin 从头重放(表现:首条消息闪断重播;join 失败则整轮断)。修 B2 时应同时补 agent 页 `onSend` 与 chats 页对齐。

### P1(高,功能缺陷)

**P1-1 运行/上传中第二条消息被静默吞掉**【确定|代码+黑盒实测坐实】
`agents/.../page.tsx:82-88,163-184`(未接 `isUploading/uploadProgress`,`InputBox` 未禁用)+ `hooks.ts:354-357`(`sendInFlightRef` 静默 return)+ `ai-elements/prompt-input.tsx:800-820`(promise 后清空 composer)→ 第二条消息无痕迹丢失。**黑盒实测:运行中(dots=3、输入框可编辑)发第二条,6s 后消息不在页面上**。

**P1-2 发送失败/纯附件消息,用户输入被无声清空**【确定|代码】
`input-box.tsx:438-462`——`onSubmit` 不 await `sendMessage`,失败也先清空输入(与注释意图相反);`if (!message.text) return`(444-446)纯附件发送被吞且附件被清。

**P1-3 被杀 run 的子任务卡片永久转圈、无失败原因**【确定|代码+黑盒实测佐证】
`subtask-status-sync.ts:38-42` + `subtask-card.tsx:90-97,171-210`——无 tool result 的 task 一律 `in_progress`,UI 不读线程 error/interrupt 态。**黑盒实测:被杀 PPT 线程重开,页面无任何"出错/中断"文案,只见一串 Token 统计**;半截 tool_call 也渲染成"已完成步骤"(`message-group.tsx`)。

**P1-4 新线程模型覆盖写入临时 UUID + localStorage 永久泄漏**【确定|代码】
`core/settings/store.ts:139-147`、`local.ts:15,54-78`——/new 页 threadId 是临时 uuid,per-thread 模型选择落盘到 `deerflow.thread-model.{临时uuid}`,换真实 id 后失效;删除线程只清 `draft:{tid}`(`hooks.ts:827-833`),死键累积。

### P2(中低,健壮性/边角)

| # | 问题 | 位置 | 置信度 |
|---|---|---|---|
| P2-1 | 被杀/出错线程重开,输入框可能永久 error 态(SDK historyError → 提交按钮 X,无解释 UI);本次实测未复现(输入框可用),需核实后端是否把 cancel 记为 task error | stream.lgp.js:193-202,391 + prompt-input.tsx:1099-1101 | 疑似 |
| P2-2 | `sendInFlightRef` 在首发中途被 `useEffect([threadId])` 复位(prop 翻转两次),连点防护仅靠 status 单点;autosend/submitThroughComposer 直调无防护 | hooks.ts:325-329;chats/page.tsx:144 | 确定代码 |
| P2-3 | `["thread",id]`(runs 列表)从不失效,onFinish 只刷 threads/search → 分页游标错位、live 消息被重复拉取(靠去重掩盖) | hooks.ts:301-311,778-791 | 疑似 |
| P2-4 | SSE 重试耗尽(reconnectRef 一次性)后前端停止渲染,服务端 run 仍在跑,无会话内自动恢复,需手动刷新 | stream.lgp.js:373-390 + client.js:155-186 | 疑似 |
| P2-5 | 反馈(点赞/点踩)按钮永远不渲染:MessageListItem 从不传 feedback/runId,死代码 | message-list.tsx:198-209 + message-list-item.tsx:169-175 | 确定 |
| P2-6a | `write_file` 自动开 artifact 的 setTimeout 在渲染体内调度、无清理,卸载后仍 setState | message-group.tsx:392-403 | 确定 |
| P2-6b | `document.title` 离开聊天页不恢复 | thread-title.tsx:20-40 | 确定 |
| P2-6c | 切线程瞬间 Token 徽章显示旧线程用量 | token-usage.ts:63-82 | 确定 |
| P2-6d | subagent 组 `toolCall.id` 缺失时任务卡片静默丢失 | message-list.tsx:263,284-300 | 确定 |

### 与黑盒发现(B 系列)的关系

- B1(重开停顶)= P0-2 的一个切面;B2(URL 停 /new)= P0-3 同片区根因的另一半——**修 B1/B2 时必须连同 P0-2/P0-3 一起修**,否则切线程场景照样复现。
- B1/B2/P0-1/P0-2/P0-3/P1-1 全部落在「线程生命周期 + 消息列表」同一片区,建议核实后**一批规划**(F1/F2 两个贯穿性事实是共同根因)。

## 第二轮深挖发现(2026-11-16 追加:消息渲染/产物区 C 系列 + 输入/侧栏/parity D 系列)

> 本轮 P0-1 已黑盒实测坐实:从已有线程点「新对话」,新会话页残留上一个线程完整对话(实测 TRUE)。「运行中第二条消息被吞」二次实测复现。Markdown 表格/代码渲染实测正常;「导出」为菜单流程非损坏,真实问题见 D3。

### C 系列:消息渲染 / 产物 / 确认卡(`frontend/src/components/workspace/messages/` + `artifacts/`)

| # | 级 | 问题 | 位置 | 置信度 |
|---|---|---|---|---|
| C1 | P0 | write-file 文件名含 `%` → `decodeURIComponent` 抛 URIError → 产物面板崩;**流式 auto-open 即触发,无需点击**;错误边界吞掉整个聊天区(对照 `core/artifacts/utils.ts:11-17` 已有 try/catch,此处遗漏) | `artifacts/artifact-file-detail.tsx:116-122` + `message-group.tsx:392-403` | 确定(node 实测) |
| C2 | P0 | 故事确认卡「确认故事并渲染」**丢弃用户编辑**:onConfirm 闭包用 props `story`(服务端旧值)而非 StoryForm 内 `draft`,注释声称 persist 编辑但实现做不到 | `messages/storyboard-confirm-card.tsx:78-83`(draft 在 235 行) | 确定 |
| C3 | P1 | write-file 路径含 `#`/`?` → `new URL("write-file:...")` 截断:文件名丢失、内容加载静默失败(返回 undefined 空白,无提示) | `message-group.tsx:394-396,411-418` + `core/artifacts/loader.ts:58-60` | 确定(node 实测) |
| C4 | P1 | 确认卡不感知 task 状态:确认受理后按钮恢复可点,再点吃后端 409 toast;帧编辑在 composing 期同样 409;story 模式底部「确认渲染」因 frames=0 永久禁用但仍显示(死按钮) | `storyboard-confirm-card.tsx:94-130`(只读 task.result.story 不读 status;后端 `video_gen.py:2413-2419` 已核对) | 确定 |
| C5 | P1 | 确认卡 storyboard 查询 `retry:false` 且无轮询无重试:挂载瞬间偶发 404/409(自家注释承认)→ 永久卡「蓝图为空」,按钮禁用,无重试入口 | `storyboard-confirm-card.tsx:42,85-91` + `core/video-gen/hooks.ts:267-277` | 确定 |
| C6 | P2 | 消息分组指纹不含 `reasoning_content`:SDK 原地 mutate 思考 token 时指纹不变 → memo 跳过 → 中段组流式思考文本停更 | `core/messages/utils.ts:193-207` + `message-group.tsx:201` | 疑似(指纹缺口确定,SDK mutate 行为待证) |
| C7 | P2 | write_file auto-open:渲染体内裸 setTimeout 无清理(每 chunk 堆积/卸载后仍 setState/100ms 内覆盖用户手动选择);onClick 分支构造 URL 未判空 path | `message-group.tsx:392-403,411-418` | 确定 |
| C8 | P2 | message-list-item 自定义 `a` **后置覆盖** markdown-content 基准 `a`:外链失去 `_blank+noopener`,点击直接站内跳走(流式现场丢失);citation: 特有面在主聊天路径死代码 | `markdown-content.tsx:249-272` + `message-list-item.tsx:251-264` | 确定 |
| C9 | P2 | `` `max-w-[${maxWidth}]` `` 运行时拼接 Tailwind 任意值类,JIT 不生成 → AI 消息大图无宽度约束可撑破气泡 | `message-list-item.tsx:198` | 确定 |
| C10 | P2 | 孤儿 tool result(配对 AI 消息未到)开 processing 组 → 0 步 ChainOfThought 渲染成**空边框盒子**;run 被杀则永留 | `core/messages/utils.ts:106-121` + `message-group.tsx:526-579` | 确定代码/疑似频率 |
| C11 | P3 | 重复 React key(web_search 按 url / present_files 重叠文件无去重);`getFileExtension` 整路径 split(`/mnt/v2.0/README`→"0/README",Dockerfile 不识别);确认卡对 draft 任务每 2s 永久轮询(分镜页已修的 QPS 坑,聊天卡保留) | `message-group.tsx:244,286`;`artifact-file-list.tsx:76-79`;`core/utils/files.tsx:154-156`;`storyboard-confirm-card.tsx:41` | 确定/边缘 |

C 系列已核查无问题:流式半截 Markdown(封顶+防崩壳齐备)、XSS 三入口(rehype-sanitize 链完整)、超长工具结果(12k 截断+滚动)、超大 CSV(worker+限额)、中文/空格产物文件名(encodeArtifactPath 覆盖良好)。

### D 系列:输入框 / 附件 / 侧栏 / 双页 parity(`input-box.tsx`、`core/uploads/`、`recent-chat-list.tsx`、双 page.tsx)

| # | 级 | 问题 | 位置 | 置信度 |
|---|---|---|---|---|
| D1 | P0(体验) | **停止生成清空用户草稿+附件**:流式中点停止/按 Enter → handleSubmit 对 streaming 早退返回 resolved Promise → PromptInput 照常 `clear()`;随后 debounce 把空串落盘(删 `draft:{tid}`)→ 草稿**永久丢失** | `input-box.tsx:440-443,291-298` + `ai-elements/prompt-input.tsx:798-820` | 高(链路逐行) |
| D2 | P2 | 上传 XHR 无 timeout、无取消路径(abort 监听存在但无人调用)→ 弱网挂起时 `isUploading` 永真 → composer 整体禁用卡死,只能刷新 | `core/uploads/api.ts:39-108` + `input-box.tsx:222,483,516,825` | 代码确定/触发中频 |
| D3 | P2 | 导出两条路径(页头 `thread.messages` / 侧栏 `threads.getState`)都只读 checkpoint state,**不走 run-messages 全量 API**(hooks.ts:578-622 自己注释了截断问题)→ 服务端压缩过的历史导出即丢;上传中导出混入乐观占位消息;侧栏导出失败 toast 硬编码英文 | `export-trigger.tsx:28-49` + `recent-chat-list.tsx:185-208` | 机制确定(截断范围取决于后端压缩) |
| D4 | P2 | `isUploading`/进度/乐观消息是页面级单一 state 非线程隔离:线程 A 大文件上传中切到 B → **B 的输入框被禁用**、进度显示在 B、A 的乐观消息写进当前视图 | `core/threads/hooks.ts:316-317,428-436,448-460` | 高 |
| D5 | P3 | agents 页 `readPendingPrompt` 丢弃 autoSend/existingThread 标志(现无触发方,潜在);空白字符消息可发送(space 为 truthy);PromptInput provider 模式绕过 accept/maxFiles/maxFileSize 校验;agent 线程创建时 metadata 写失败被吞 → 线程永久归入普通会话组 | `agents/.../page.tsx:132-145`;`input-box.tsx:444`;`ai-elements/prompt-input.tsx:168-186`;`core/threads/hooks.ts:217-228` | 确定/低频 |

D 系列双页 parity 完整差异(除已知 onSend/isUploading 外):agents 页缺 `useSpecificChatMode`(?mode=skill 预填)与 autoSend 语义;其余差异均为合理设计。已核查无问题:打断后可立即重发(SDK stop=cancel+404 清理)、上传失败乐观回滚、粘贴大图校验、blob URL 回收、侧栏选中态/标题/删除重命名。

### 累计视图(给规划用)

- **同一根因批次(线程生命周期/串台族)**:B1 B2 P0-1 P0-2 P0-3 P1-1 D4 —— F1(同段路由不重挂载)+ F2(SDK clear)是共同根因,建议一批修+一批测
- **确认卡族**:C2 C4 C5(同一组件三个独立缺陷)+ B6 创建框残留(video-gen 对话框族)
- **产物渲染族**:C1 C3 C7 C8 C9 C11(write-file URL 方案整体重构或逐点补)
- **输入安全族**:D1 D2 D5(草稿丢失最伤用户;XHR 超时/取消是工程债)
- **导出**:D3 单独小批


| # | 问题 | 位置/建议 |
|---|---|---|
| A1 | 离线包 MANIFEST 镜像 ID 手写出错(remotion ID 第 26 字节 be↔95,已交叉验证排除风险) | 打包脚本改为 `docker image inspect` 自动生成校验和,禁手写 |
| A2 | `config.example.yaml` config_version(31)落后服务器实配(35) | 服务器侧增量改动后回填示例版本 |
| A3 | 服务器磁盘会被旧镜像/构建缓存/旧版本目录累积吃满(部署时已 85%) | 升级 SOP 固化「留当前+上一版」清理(红线:禁 `docker system prune -a`,按名删) |

## 已验证无需处理(供核实 agent 跳过)

- 匿名 API 数据安全(threads/artifacts 全 401)
- 流式渲染/澄清选项交互/消息持久化/侧栏最近对话/产物面板/i18n/线程标题生成
- LLM(360)/生图(360)/生视频(360 seedance 主力)/TTS(方舟)路由与产线全通
