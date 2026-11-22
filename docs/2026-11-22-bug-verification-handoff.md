# Bug 核实清单（交核实 Agent 逐条核实用）

> **TL;DR**：2026-11-22 全模块 GUI 测试（A~I 九批次 + 下午部署同步后复验一轮）产出 15 项待核实问题：5 项复测坐实仍未修复（停止清草稿/writer 伪409/Edge TTS 403/档内模型未定价/默认徽标缺失）、6 项单次观察待证、2 项测试资产漂移、2 项产品决策项。每条含复现路径与判定标准，核实结论请逐条标注「存在 / 已修复 / 无法复现」+ 证据。
>
> 环境：`<测试服务器地址>`（2026-11-22 下午已同步模型花名册）；测试账号：普通用户 <测试账号> / 管理员 <测试账号>（凭据见仓库外 ops 记录）
> 代码基准：origin/main@`0e84b148`（部署同步后的 config 以服务器实配为准）
> 证据截图：`temp/worktree-archive-20261122/home-plan/temp/full-module-e2e-20261122/`（recheck_* 为下午复验轮，其余为上午全量轮）
> 完整测试上下文：`docs/2026-11-22-full-module-test-report.md`（§3 回归结论 / §4 发现 / §9 复验）

---

## 一、复测坐实仍未修复（优先核实）

### P1【P0 体验】停止生成会清空用户已输入的草稿

- **现象**：AI 流式回复期间，用户在输入框打字（草稿），点击停止按钮 → 草稿被静默清空且不会恢复（防抖还会把空串落盘覆盖 localStorage 草稿）。长任务用户「边等边写」的输入直接丢失。
- **复现**：任意 agent 对话 → 发送一条会产生长回复的消息 → 回复流式期间在输入框输入任意文本 → 点输入框右侧停止按钮（流式中 Submit 位变为停止）→ 观察输入框。
- **判定**：停止后草稿仍在 = 已修复；草稿消失 = 存在。
- **已核证据**：流式中 DOM textbox 值含草稿文本 → 停止后 snapshot 无草稿，两次独立复现（上午 `c3_final_after_stop.png`、复验 `recheck_d1_after_stop.png`）。
- **疑似位置**（沿 11-16 bug 单 D1，当时定位）：`frontend/src/components/workspace/input-box.tsx:440-443,291-298`（handleSubmit 对 streaming 早退返回 resolved Promise）+ `frontend/src/components/ai-elements/prompt-input.tsx:798-820`（早退后仍执行 clear()）。

### P2【中】writer 版本回滚后继续编辑必发伪「保存冲突」

- **现象**：文档回滚到任意历史版本后继续编辑，自动保存立即弹「保存冲突：文档已在其他窗口被修改」（单窗口操作，并无他人修改）。
- **当前缓解**：已有冲突提示条 + 「强制覆盖/重新加载」按钮（较 11-16 的静默冻结改进）；强制覆盖后保存循环恢复。伪冲突根因未修。
- **复现**：`/workspace/writer`（无侧栏入口，直接输 URL）→ 打开任意有多版本的文档 → 版本历史 → 选任意旧版本 → 回滚到此版本 → 关闭面板 → 正文/标题继续输入 → 等 3s 自动保存触发 → 观察顶部。
- **判定**：回滚后编辑无冲突弹条 = 已修复；出现「保存冲突」= 存在。
- **已核证据**：`recheck_f1_writer.png`（复验轮）+ `f1_writer_post_restore_edit.png`（全量轮），两次复现。
- **疑似位置**（沿 11-16 bug 单 F1）：后端 `writer_service.py:380-393`（restore 先 create_version bump）+ 前端 `version-panel.tsx:102-116` / `writer-page.tsx`（丢弃返回的新版本号只取 content → setContent 触发防抖 → PUT 携带旧 base_version → 409）。

### P3【中】Edge TTS 免费音色通道整体 403

- **现象**：课件配音工具箱选择任一 Edge TTS（免费）音色生成音频，100% 失败：`TTS synthesis failed (1/1 chunks): 403, message='Invalid response status'`。5 个免费音色（云健/云希/云扬/晓晓/晓依）全部不可用。付费豆包音色正常（播客 D4 已验证）。
- **复现**：`/workspace/twins/voice` → 课件配音工具箱 → 文稿转音频 → 选「云健（男）」→ 输入任意文本 → 生成音频 → 看制作历史。
- **判定**：生成成功有播放 = 已修复；同 403 = 存在。
- **已核证据**：两次失败记录（12:24 与 14:09），`recheck_edge_tts_result.png`。
- **核实要点**：服务器到 Edge TTS 上游的网络连通性/风控（服务器侧出网被拒？上游封数据中心 IP？），非前端问题。失败提示本身展示正常（有红色失败态+原因）。

### P4【中高·计费】档内主力模型未定价，调用无价目兜底

- **现象**：管理台定价表中以下模型标「未定价」（单价 0）：**deepseek-v4.1-flash（标准档默认）、qwen4-max（Ultra 档默认）、kimi-k3.1-pro（Ultra 档）、claude-haiku-5-5、claude-opus-5-5**。花名册同步后这些模型进入用户可选档位，用户选中调用时若无供应商真实回传金额则无法计价（运营台有「未计价调用」计数，当前 16+ 条）。
- **复现**：管理员登录 → 运营后台 → 定价管理 → 查看模型定价表 Source 列。
- **判定**：上述模型出现输入/输出单价（Source 非未定价）= 已修复；仍 0/未定价 = 存在。
- **已核证据**：`recheck_pricing.png`（下午复验，同步后仍在）。
- **核实要点**：`config.yaml`（服务器实配）models[].pricing 是否漏配；360 网关是否回传真实 amount（若回传则影响减弱为「估算缺失」）。

### P5【低中·UI】标准/Ultra 档无「默认」徽标

- **现象**：模型选择列表中，闪速档首项显示「360·Qwen 3.8 Flash（默认）」，但标准档选中 DeepSeek V4.1 Flash、Ultra 档选中 Qwen 4 Max（均为模式阶梯设计中的默认模型）时无任何「默认」标注——用户无法得知各档默认模型。
- **复现**：agent 对话 composer → 切标准档 → 打开模型列表 → 观察是否有「（默认）」徽标；切到 DeepSeek 后看模型按钮文案。
- **判定**：DeepSeek/Qwen4-Max 显示「（默认）」= 已修复；无 = 存在。
- **已核证据**：`recheck_standard_deepseek_default.png`（选中 DeepSeek 无徽标）vs `recheck_flash_models.png`（闪速有徽标，对照）。
- **核实要点**：服务器 `config.yaml` 的 `mode_defaults.standard/ultra` 是否配置（花名册同步时可能漏了 mode_defaults 段）；前端徽标渲染逻辑 `frontend/src/components/workspace/input-box.tsx`（mode 相关分支）。

## 二、单次观察，待核实

### P6【中】PPT 生成任务 run 中断未交付

- **现象**：ppt-master 线程 `<脱敏任务ID>`（水的三态迷你PPT大纲）消耗 5,277,166 token（¥17.65）后 run 中断，pptx 未交付；用量页状态正确标注「未完成」。用户确认过 Stage 1（选 1）与 Stage 2（选 2 方案 B），agent 声明确认后不再打断。
- **核实要点**：服务器 gateway/worker 日志查该 thread_id 的 run 结束原因（超时？被杀？沙箱错误？）；interrupt/resume 机制是否可让用户在 UI 里续跑。
- **判定**：找到中断根因 / UI 有续跑入口 = 核实完成。

### P7【低中】超长线程滚动异常家族

- **现象 a（已修复确认）**：重开历史长对话不落底——**部署更新后已修复**（重开直接落底），可回归确认后关闭。
- **现象 b（仍观察）**：5,000K+ token 的超长线程（PPT 线程）内向上翻页（PageDown/dom 滚动）会跳回对话开头附近，滚动位置与内容不对应；上午还出现过一次跨账号往返后历史整体截断（仅渲染开头确认卡，40+ 步不可见）——复验时未再复现。疑似虚拟化列表（`virtual-message-list.tsx` 11-16 P0-2 家族）在超长内容下的边界表现。
- **复现**：打开上述 PPT 线程 → 先到底 → 向上翻页多次 → 观察是否跳顶/白屏。
- **判定**：稳定复现跳顶/截断 = 存在；无法复现 = 记录偶发观察。

### P8【产品核实】模式选择跨对话持久化 + agent 长任务无成本提示

- **现象**：composer 档位选择被记住并应用到后续新对话（含 agent 长任务）。测试中残留的 Ultra 档（qwen3.8-max，输入 12 元/百万）跑了四个 agent 任务，LLM 花费 ¥30+；若在标准档约为 1/8。用户对「上次选的档位会用于下个长任务」无任何成本感知。
- **核实要点**：这不是实现 bug（`handleModelSelect 保持当前模式`、持久化是既定语义），请核实**产品语义是否符合设计意图**：①模式持久化范围（会话级/浏览器级/账号级）与 plan `docs/plans/2026-11-18-model-mode-ladder-plan.md` §1.4 的约定是否一致；②是否需要「长任务发起前显示当前档位/预估成本」或「新会话回落标准档」。
- **判定**：给出设计意图结论即可。

### P9【低】agent 线程标题未生成时，用量页任务标题显示裸 UUID

- **现象**：podcast 线程标题未生成期间（侧栏显示 Untitled），个人用量页任务行标题显示 `<脱敏任务ID>`（裸 thread UUID）；视频类任务则显示脚本前 30 字。
- **复现**：新发起一个 agent 任务 → 未等标题生成时打开 `/workspace/console`（个人用量）→ 看任务行标题。
- **判定**：标题显示为线程标题或友好占位（如「（生成标题中）」）= 已修复；裸 UUID = 存在。
- **疑似位置**：`backend/app/gateway/_shared/personal_usage/service.py`（title 回退字段）+ `frontend/src/components/workspace/usage/personal-usage-page.tsx`。
- **证据**：`f7_mobile_usage.png`（第二张任务卡为 UUID）。

### P10【低】首次渲染状态徽章显示「重新合成中」

- **现象**：视频任务首次正式合成（此前从未合成过）时，详情页状态徽章显示「重新合成中」——「重新」用于首次合成措辞不当，应为「合成中」。
- **复现**：新建图片动效/AI 视频任务 → 渲染 → 详情页看状态徽章。
- **判定**：首渲显示「合成中」= 已修复；仍「重新合成中」= 存在。
- **疑似位置**：`frontend/src/components/workspace/video-gen/` 详情页状态文案映射。
- **证据**：`d1_rendering_detail.png`。

### P11【中·技能/沙箱】沙箱内 URL 直连抓取 100% 失败（SSRF 防护链路未覆盖）

- **现象**：播客 agent 冒烟自报：podcast-studio 技能自带 fetcher 在沙箱内 `_set_socket_timeout()` 走到 `response.fp.raw.settimeout()` 分支即抛 `AttributeError: 'SocketIO' object has no attribute 'settimeout'`（`connection.sock` 为 None 时触发），对 example.com 等 100% 复现——任何 http/https 来源都无法直连 intake。agent 当时绕行（用平台 web reader 逐字存本地 Markdown），导致 `sources.json` 中 `kind=markdown`、`requested_url/final_url` 为 None——**URL 安全链路（SSRF 防护、IP 钉住、DNS rebinding 钳住、HTML 消毒）本次未被实际执行**。
- **核实要点**：这是播客 agent 自报的技能层问题，请核实的不是「播客失败」（播客最终交付成功），而是：①沙箱 Python 3.10 直连公网 URL 是否 100% 失败；②若修复直连分支，SSRF 防护（IP 钉住/DNS rebinding/HTML 消毒）是否会被真实覆盖；③该分支在真实用户直接投喂 URL 时是否 100% 失败。
- **判定**：沙箱内 `urlopen('https://example.com')` 复现 AttributeError = 存在。
- **位置**：`skills/public/podcast-studio/`（fetcher/intake 脚本；agent 报告建议修 `raw._sock / raw_io` 的真实 socket 或跳过该 fallback 分支）。
- **证据**：`d4_final_check.png`（agent 交付报告「两点冒烟发现」第 1 点全文）。

## 三、文案与测试资产

### P12【低·i18n】界面中英文混杂三处

1. 设置弹窗账号栏：`Profile` / `Change Password` / `Update Password` / `Sign Out` 为英文，其余中文（`a5_account_tab.png`）
2. 登录失败提示：`Incorrect email or password` 英文（`a1_wrong_password.png`）
3. 分享撤销后的失效页：中文标题「文档不存在或已过期」+ 英文副标 `Shared document not found or expired` 并列（`f2_share_revoked.png`）
- **判定**：三处统一中文 = 已修复。

### P13【测试资产】live E2E 套件两处断言已过时

1. `frontend/tests/e2e/live/l1-smoke.spec.ts:92`：agent 新会话页等待通用占位「今天我能为你做些什么？」——现 agent 页 hero composer 为能力定制占位（如「粘贴讲稿，或输入课程主题…」）→ L1 5/6
2. `frontend/tests/e2e/live/l2-features.spec.ts:108-113`：控制台断言找 `llm-usage-panel`/`console-cost-chart`（管理员视角）——普通用户现挂载个人用量页（`usage-page-info` 等 testid）→ L2 7/8
- **判定**：按现行 UI 更新断言后 L1+L2 全绿 = 已修复。注意 l2 控制台用例若用管理员身份跑则原断言仍有效——请核实套件身份约定后改。

## 四、产品决策项（非缺陷，需拍板后转任务）

### P14 writer 模块无任何入口

- **现象**：`/workspace/writer`（写作 Beta，完整编辑器+版本+分享+导出）功能正常，但普通用户界面（侧栏/首页）**没有任何入口**，只能直接输 URL。代码核实：全仓无外部链接指向 writer（仅模块内导航+分享页）。11-16 轮即如此。
- **待拍板**：a) 计划下线 → 清理入口承诺与 L2 用例，伪 409（P2）修复降级；b) 继续保留 → 加回入口，P2 转正常修复。
- **附带**：L2 套件「writer:新建文档、编辑、刷新后内容持久」用例当前通过（功能本身健康）。

### P15 普通用户无「我的渠道/凭据」设置入口

- **现象**：设置弹窗仅「账号/外观」两栏，无渠道配置区（后端有 `user_channels` 路由）。11-16 轮记录过「channels 掩码凭据合并已核查无问题」，但当时入口形态待确认。
- **待拍板**：普通用户渠道自配是否为规划内功能；是 → 入口缺失待补；否 → 后端路由按内部能力保留即可。

## 五、已修复/已改善确认（无需重复核实，回归参考）

| 项 | 结论 | 证据 |
|---|---|---|
| B1 重开历史对话不落底 | **已修复**：重开直接落底（atBottom=true） | recheck_b1.png |
| N1 模型花名册 | **已同步**：三档清单与模式阶梯设计矩阵一致（闪速=qwen3.8-flash+deepseek-v4.1-flash；标准=deepseek+claude-sonnet；Ultra=qwen4-max+kimi+claude-sonnet），旧杂牌模型移出 | recheck_flash/standard/ultra_models.png |
| N3 线程历史整体截断 | 复验未复现（仅 P7b 的翻页跳顶残留观察） | recheck_d5_thread.png |
| B3 交付文件不可点击 | **已改善**：present_files 交付卡在消息流内带下载按钮（海报/知识卡/视频均实证） | d3_poster_result.png、recheck_d6_final.png |
| B7 播客时长偏差 | 未复现：要求 30s 实际 29.666s（偏差 0.1%） | d4 交付报告 |

## 六、已验证无问题（核实 agent 可跳过的面）

登录/登出/匿名跳转（next 参数正确）/换号隔离零残留；用量页全契约（角色分流/两卡口径/7-14-30/六项筛选/三态/模型明细输入+输出口径/缓存缺失显「—」/「媒体费用未核算」措辞/分页/打开原任务/移动端零溢出）；三档模式菜单三模型矩阵连通性；writer 建档/自动保存/刷新持久/分享生成与撤销闭环；视频任务详情/分镜编辑/画面提示词编辑与失败重试/字幕样式保存引导/字幕时间校验/9:16 成片播放；管理台看板/用户筛选/素材单价；六类付费生成五类交付（mp4 可播放、MP3 ffprobe 零错误、PNG 下载可用）。
