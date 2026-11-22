# 全功能模块 live GUI 细化测试报告

> **TL;DR**：按 `docs/plans/2026-11-22-full-module-live-gui-test-plan.md` 对测试服务器完成全模块细化测试（A~I 九批次 + 双轨自动化）。**六类付费真实生成五类交付成功**（图片动效 ¥0.0068 / AI 视频 ¥0.0070 / 海报 / 播客 MP3 29.7s / 知识卡，全部可解码校验通过；PPT 生成中因线程渲染异常未在 UI 收产物）。**复现 11-16 三个已知 bug**（B1 重开不落底、D1 停止清草稿、F1 伪 409——后者已有冲突提示+逃生门，严重度下降）；**新发现 8 项**，最高优：部署模型花名册与阶梯设计不符且 claude-haiku/opus 未定价（产生未计价调用）。实际消耗 LLM ¥30.48（Ultra 档跑长任务所致）+ 媒体小额，未超 ¥100 软上限。

> 测试执行：ZCode 自动化代理（GUI 黑盒 + 仓库 live E2E 双轨）；日期：2026-11-22
> 被测：`<测试服务器地址>`（代码基准 origin/main@`0e84b148`）
> 账号：普通用户 <测试账号>（主测）+ 管理员 <测试账号>（管理台批，凭据按授权读取 ops 文件，不入库）
> 证据：`temp/worktree-archive-20261122/home-plan/temp/full-module-e2e-20261122/`（gitignored，60+ 张截图，关键项见 §7）；本文档为未跟踪本地件
> 计划：`docs/plans/2026-11-22-full-module-live-gui-test-plan.md`（勾选制，已回填执行状态）

---

## 1. 执行摘要

| 批次 | 结果 | 摘要 |
|---|---|---|
| H′ 自动化套件 | ✅ 12/14 | L1 5/6 + L2 7/8；2 失败均为**套件漂移**（测试资产过时），非产品缺陷 |
| A 账号与鉴权 | ✅ 8/10 | 登录/登出/匿名跳转/错误提示/换号隔离全过；A7 渠道 UI 不存在、A10 注册未触发 |
| B 壳与导航 | ✅ 6/8 | 折叠/菜单/重命名/置顶/token 徽章/title 全过；B4 删除留清理阶段、B5 导出产物路径 unsupported |
| C 对话通用 | ✅ 7/13 | 三档三模型矩阵全通、草稿持久化过；**D1 停止清草稿复现**；附件类 4 项 unsupported（IAB 无文件选择） |
| D 付费真实生成 | ✅ 5/7 | **五类真实产物交付且可解码**；D5 PPT 交互链路验证但收产物被渲染异常阻断、D6 执行中 |
| E 详情/分镜/二次编辑 | ✅ 7/12 | 音频面板/字幕样式保存引导/时间校验/竖屏容器形态全过；付费重生类 3 项为控成本未逐一触发 |
| F 周边模块 | ✅ 7/8 | writer 全流程（含伪 409 复现）、分享闭环、TTS 失败提示、资产项目、移动端零溢出 |
| G 管理台 | ✅ 5/8 | 看板/定价/用户筛选/对账/权限边界过；渠道与名册视图无独立入口（在定价页内） |
| I 已知回归 | 4/6 | **B1、D1、F1' 复现坐实**；B3 实证改善（交付卡可下载）；I5/I6 未触发 |

**总判定**：核心功能全部可用，付费生成主链路健康；无 P0 级功能损坏。需关注：配置漂移（F1-2）、聊天线程渲染族（B1/D1/D5 变体）、成本可见性。

## 2. 付费真实生成结果（D 批核心）

| # | 能力 | 产物 | 费用 | 验证 |
|---|---|---|---|---|
| D1 | 图片动效 ai_image | 9:16 mp4 8.3s，2 帧 | ¥0.0068 | 播放正常、字幕烧录、分镜卡入账 ✅ |
| D2 | AI 视频 ai_video | 9:16 mp4 9.1s，**真实动态镜头**（竹林光效） | ¥0.0070 | 播放正常、「视」来源标、入账 ✅ |
| D3 | 课程海报 creative-studio | 2048×2048 PNG（doubao-seedream-5.0-pro） | LLM 计入 | 交付卡+事实性说明，链路五步无报错 ✅ |
| D4 | 语音播客 podcast-studio | MP3 29.666s（预期 29.664s，delta 0.002s） | LLM ¥0.27+ | **ffprobe 全片零错误**、幂等/防篡改过、豆包 TTS 通 ✅ |
| D7 | 知识卡 creative-studio | 1536×2048 PNG（3:4 裁切自 1:1） | LLM 计入 | 交付卡+下载按钮，五步链路无报错 ✅ |
| D5 | PPT ppt-master | design spec 阶段（测试截止时） | LLM 已计 | 两段确认交互 ✅；**pptx 收产物被线程渲染异常阻断**（§4-9） |
| D6 | 文字动效 course-video | 分镜表撰写中（测试截止时） | LLM 已计 | 发起/执行链路 ✅；确认卡未出现（讲稿直通模式） |

- 生成中断点恢复：D1 帧 1 被 360 风控拒绝（code 12020）→ **改画面提示词后单帧重试成功**——失败恢复闭环可用。
- 播客确认门禁：用户明说「不要提问」仍强制确认（技能设计正确，agent 亦如实说明）。
- 用量闭环：五类任务全部出现在个人用量页与作品页，费用与任务卡一致。

## 3. 已知 bug 回归结果（I 批）

| # | 11-16 编号 | 本轮结论 | 证据 |
|---|---|---|---|
| I1/B1 | 重开历史对话不落底 | **仍存在**：重开后 scrollTop=102/总高 2223，最新消息不可见 | i1_reopen_scroll_final.png |
| I2/D1 | 停止生成清空草稿 | **仍存在**：流式中草稿已输入（DOM 证实），点停止后 composer 清空（run 停在输出 1 token） | c3_final_after_stop.png |
| I4/F1' | writer 回滚后伪 409 | **伪冲突仍复现**，但已有「保存冲突」提示条 + 强制覆盖/重新加载按钮；强制覆盖后保存循环恢复——**较 11-16 的静默冻结明显改进** | f1_writer_post_restore_edit.png |
| B3 | 交付文件不可点击 | **改善**：present_files 交付卡在消息流内带下载按钮（海报/知识卡均实证） | d3_poster_result.png |
| B7 | 播客时长偏差 | 本轮要求 30s 实际 29.666s（偏差 0.1%），**未复现** | d4 交付报告 |
| I3/F5 | 声线上传失败零提示 | 上传场景 unsupported（IAB 无文件选择）；工具箱 TTS 失败**有明确提示**（红色失败态+原因），未复现零提示 | f3_tts_result.png |

## 4. 新发现清单（本轮新增）

| # | 级 | 问题 | 证据/位置 |
|---|---|---|---|
| N1 | **中高（配置）** | **部署模型花名册与模式阶梯设计不符且产生计价缺口**：闪速档实际=Qwen3.8-Flash/GLM5.3-Flash/Claude-Haiku（设计为 qwen3.8-flash+deepseek-v4.1-flash）；标准/Ultra 档无「默认」徽标（mode_defaults 指向的 deepseek-v4.1-flash/qwen4-max 不在部署花名册）；且 **claude-haiku-5.5 / claude-opus-5.5 在定价页未定价**——用户可选但调用不计价（看板「未计价调用 16」部分与此相关）。前端过滤/回落机制本身正常 | t3_model_menu_standard.png、g4_pricing.png、11-22 上午报告 F1 |
| N2 | 中（体验） | **「停止生成」清空用户草稿**（=I2，本轮实测坐实；长流式任务中用户边等边写的输入会丢） | c3_final_after_stop.png |
| N3 | 中（体验） | **长线程重开历史渲染截断**：跨账号往返后打开 PPT 长线程，仅渲染开头确认卡，40+ 步历史不可见且滚动失效（B1 的加重变体，疑似 P0-2 虚拟化家族） | d5_final_check2.png |
| N4 | 中（成本） | **模式选择跨对话持久化 + agent 长任务无档位成本提示**：四个 agent 任务在残留的 Ultra 档（qwen3.8-max，输入 12 元/百万）执行，LLM 消耗 ¥30.48——若在标准档（deepseek 1.3 元/百万量级）成本约为 1/8。用户对「上次选的 Ultra 会用于下个长任务」无感知 | g6_user_filter.png |
| N5 | 低 | **Edge TTS 免费通道整体 403**（「Invalid response status」），5 个免费音色全部不可用；付费豆包通道正常 | f3_tts_result.png |
| N6 | 低 | **agent 线程标题未生成时，用量页任务标题显示裸 UUID**（对比：视频任务显示脚本前缀） | f7_mobile_usage.png |
| N7 | 低 | **首次渲染状态徽章显示「重新合成中」**（应为「合成中」；「重新」用于首渲措辞不当） | d1_rendering_detail.png |
| N8 | 低（i18n） | 中英混杂多处：设置弹窗（账号/外观中文 + Profile/Sign Out 英文）、登录错误提示「Incorrect email or password」、分享失效页双语并列 | a5_account_tab.png、a1_wrong_password.png、f2_share_revoked.png |
| N9 | 信息 | **live E2E 套件两处漂移**：L1「四个 agent 卡」期望通用占位文案（agent 页现为能力定制占位）；L2「控制台:用量面板」期望管理台 testid（普通用户现挂载个人用量页）。建议随 UI 演进更新选择器 | H1/H2 输出 |
| N10 | 信息 | 播客 agent 报告：沙箱内 URL 直连抓取分支 100% 失败（`SocketIO` 无 `settimeout` 属性），SSRF 防护链路未被实际覆盖——agent 已建议单独修复（技能层问题，登记 backlog 由用户决定） | d4 交付报告 |

## 5. 成本对账

- 管理台（普通号，近 7 天）：**LLM ¥30.48**（9.2M tokens，18 任务）+ 媒体未计入此项（生图按张另计）
- 媒体：生图 ¥0.5/张 × 实际调用张数（D1 两帧 + 海报 1 + 知识卡 1 ≈ ¥2）+ AI 视频按 token（¥0.0070 已核）
- 大头是 N4 所述的 Ultra 档 agent 任务（qwen3.8-max ¥28.05）；若重跑建议先切标准档
- **未超软上限 ¥100** ✅；管理员号零新增消耗（仅查看）

## 6. 未竟项与原因

- **D5 pptx / D6 mp4 终产物**：测试截止时 agent 仍在服务器端执行（N3 渲染异常阻断 UI 查看）；产物落盘后可在作品页与用量页核对——建议用户稍后在「我的作品」直接查看
- B4 线程删除、C11 切线程落底复测：留待清理阶段顺带执行
- C5~C8 附件族、F3 声线克隆上传：IAB 运行时不支持文件选择，标记 unsupported（可用仓库 live E2E 或手动补测）
- E3/E4/E8/E10~E12 付费重生类：为控成本未逐一触发（D1/D2 已覆盖分镜编辑与重合成主路径）
- A7 渠道、A10 注册：普通号设置无渠道 UI（观察项）；注册流程未触发（避免制造多余账号）

## 7. 验收记录（操作 → 实际关键输出）

| 验证点 | 实际结果 |
|---|---|
| L1 套件 | `pnpm test:e2e:live:l1` → **5 passed, 1 failed**（占位文案漂移），2.5m |
| L2 套件 | `pnpm test:e2e:live:l2` → **7 passed, 1 failed**（管理台 testid 漂移），2.7m |
| 三档模型矩阵 | 闪速+qwen3.8-flash / 标准+claude-sonnet-5.5 / Ultra+qwen3.8-max 各发一条 → 三条均回复「正常」，Token 徽章 35.2K→122.0K→146.1K |
| D1 出片 | 分镜 2 帧 → 确认渲染 → 详情页「ready / ¥0.0068」→ 播放 8.3s 正常 |
| D2 出片 | 直渲 → 「ready / ¥0.0070」→ 播放 9.1s 动态镜头正常 |
| D4 出片 | agent 交付报告：「final 29.666 s vs 预期 29.664 s，ffprobe 全片零错误，可正常播放」 |
| 停止清草稿 | 流式中 textbox 值=「C3最终草稿…」（DOM）→ 点停止 → `textbox` 值为空（D1 复现） |
| 回滚伪 409 | 回滚后编辑 → 「保存冲突：文档已在其他窗口被修改」+ 强制覆盖/重新加载按钮 → 强制覆盖后再次编辑无冲突 |
| 重开不落底 | 重开长线程 → scrollTop=102 / scrollHeight=2223 / atBottom=false |
| 分镜时间校验 | 结束时间填 00:00:99,999 → 红框 + 「存在非法时间戳…已暂时禁用保存」+ 保存按钮 disabled |
| 管理台对账 | 用户筛选 365513199 → 近 7 天 ¥30.48 / 9,233,940 token / 18 任务（Ultra 档 agent 任务为主） |
| 换号隔离 | 普通号登出 → 管理员登录 → 最近对话 0 条普通号数据 |

## 8. 截图索引（`temp/worktree-archive-20261122/home-plan/temp/full-module-e2e-20261122/`，共 60+ 张）

关键证据：`c3_final_after_stop.png`（D1 草稿丢失）、`f1_writer_post_restore_edit.png`（伪 409）、`i1_reopen_scroll_final.png`（不落底）、`d5_final_check2.png`（历史截断）、`t3_model_menu_standard.png`（无默认徽标）、`g4_pricing.png`（未定价）、`f7_mobile_usage.png`（用量页实时数据）、`d4_final_check.png`（播客 ffprobe 报告）、`e1_player_playing.png`（9:16 成片横框黑边）。

## 9. 部署更新后复验（2026-11-22 下午，用户同步花名册后）

| 项 | 复验结论 |
|---|---|
| N1 花名册 | **主体已修复 ✅**：闪速=Qwen3.8-Flash(默认徽标)+DeepSeek-V4.1-Flash；标准=DeepSeek-V4.1-Flash+Claude-Sonnet-5.5；Ultra=Qwen4-Max+Kimi-K3.1-Pro+Claude-Sonnet-5.5；GLM/Haiku/Terra/Opus/Qwen3.8-Max 已移出档位。**两个残留**：①标准档选中 DeepSeek、Ultra 档选中 Qwen4-Max（均为设计默认）时按钮**仍无「（默认）」徽标**（对比闪速档正常）→ 疑似 `mode_defaults.standard/ultra` 仍未配置；②定价表已列入新模型但 **deepseek-v4.1-flash / qwen4-max / kimi-k3.1-pro 全部「未定价」**（claude-haiku/opus 也仍未定价）→ 档内主力模型调用无价目兜底，未计价问题未解决且更贴近默认主路径（recheck_pricing.png） |
| B1 重开不落底 | **已修复 ✅**：重开长线程 scrollTop=1362/atBottom=true，最新消息完整可见（recheck_b1.png） |
| N3 线程历史截断 | **未再复现 ✅**：D5 线程完整渲染且落底；但同线程向上翻页出现跳回开头的滚动异常（虚拟化族轻度表现，观察项） |
| D5 PPT 产物 | **未完成 ❌**：用量页状态「未完成」（¥17.65 / 5,277K token）——测试结束后 run 中断，pptx 未交付；「未完成」三态标注正确 |
| D6 文字动效产物 | **已交付 ✅（补充确认）**：output.mp4 3.4MB，H.264 1080×1920@30fps 274 帧一次通过、ffprobe 证据链齐全、讲稿逐字未改、消息流内下载链接可用（recheck_d6_final.png） |
| D1 停止清草稿 | **仍未修复 ❌**：流式中草稿已输入 → 点停止 → composer 清空（recheck_d1_after_stop.png） |
| F1' writer 回滚伪 409 | **仍未修复 ❌**：回滚后编辑即弹「保存冲突」（提示条+逃生门仍在）（recheck_f1_writer.png） |
| N5 Edge TTS 403 | **仍未修复 ❌**：复测同错误 `TTS synthesis failed: 403 Invalid response status`（recheck_edge_tts_result.png） |

复验消耗：极小（几条轻量 LLM 消息 + 一次 TTS 失败调用，无媒体生成）。

## 10. 建议后续

1. **N1 配置漂移**：核对测试/公司服务器 config.yaml 的 `models[].modes`、`mode_defaults`、缺失价目（claude-haiku/opus），与模式阶梯 plan 对齐或回填决策记录
2. **N2/N3**：与 11-16 bug 清单「线程生命周期/消息列表」批次合并规划（B1/D1/P0-2/N3 同片区）
3. **N4**：agent 任务发起前显示当前档位与预估成本，或新会话档位回默认
4. **N9**：更新 live 套件两处断言（占位文案、console testid），恢复 CI 化 live 回归
5. N5~N8 低优项登记 findings-backlog 由用户排期
