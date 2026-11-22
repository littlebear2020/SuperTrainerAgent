# changelog 2026-11-22 全功能模块 live GUI 细化测试

> **TL;DR**：执行 `docs/plans/2026-11-22-full-module-live-gui-test-plan.md`（同日定稿同日执行完毕）：A~I 九批次全模块 GUI 黑盒 + L1/L2 live 套件双轨；五类付费真实生成交付成功且可解码，复现 11-16 三个已知 bug（B1/D1/F1'），新增 10 项发现（最高优=部署模型花名册漂移+claude-haiku/opus 未定价）；消耗 LLM ¥30.48 + 媒体小额，未超预算。影响面 = 纯测试产出（无生产代码改动）。

## 执行内容

- 测试计划：`docs/plans/2026-11-22-full-module-live-gui-test-plan.md`（勾选已回填，状态头已更新为已执行）
- 测试报告：`docs/2026-11-22-full-module-test-report.md`（含覆盖矩阵、发现清单 N1~N10、验收记录、截图索引）
- 截图证据：`temp/worktree-archive-20261122/home-plan/temp/full-module-e2e-20261122/`（gitignored）
- 上一轮（上午）增量冒烟报告：`docs/2026-11-22-live-gui-test-report.md`

## 关键结论

1. 六类付费生成：D1 图片动效/D2 AI 视频/D3 海报/D4 播客/D7 知识卡全部真实交付（mp4 可播放、MP3 ffprobe 零错误、PNG 交付卡下载可用）；D5 PPT 两段确认交互正常、终产物因线程渲染异常未在 UI 收取（服务器后台继续）。
2. 已知 bug 回归：B1（重开不落底）、D1（停止清草稿）复现坐实；F1'（writer 回滚伪 409）仍复现但已有冲突提示+强制覆盖逃生门；B3（交付文件不可点击）实证改善；B7（播客时长）未复现。
3. 新发现最高优：部署模型花名册与模式阶梯设计不符（标准/Ultra 无默认徽标）且 claude-haiku/opus 未定价 → 未计价调用；建议核对服务器 config.yaml。
4. 成本发现：模式选择跨对话持久化 + agent 长任务无档位成本提示 → Ultra 档（qwen3.8-max）跑四个 agent 任务产生 ¥30.48 LLM 消耗。

## 验收记录

- `pnpm test:e2e:live:l1` → 5 passed, 1 failed（占位文案套件漂移）
- `pnpm test:e2e:live:l2` → 7 passed, 1 failed（console testid 角色分流漂移）
- D1 → 详情页 `ready / ¥0.0068`，播放 8.3s 正常
- D2 → `ready / ¥0.0070`，播放 9.1s 动态镜头正常
- D4 → agent 交付「final 29.666 s，ffprobe 全片零错误，可正常播放」
- 停止清草稿 → 流式中草稿 DOM 在 → 点停止 → composer 空（D1 复现）
- 回滚伪 409 → 「保存冲突」提示条出现 → 强制覆盖后保存循环恢复
- 重开长线程 → scrollTop=102 / scrollHeight=2223 / atBottom=false（B1 复现）
- 管理台用户筛选 → 普通号近 7 天 ¥30.48 / 9,233,940 token / 18 任务
- `python3 scripts/check_docs_convention.py` → OK 全过

## 移交与遗留

- D5/D6 终产物待用户稍后在「我的作品」核对（服务器后台执行）
- N1 配置漂移、N2/N3 线程渲染族、N4 成本可见性建议排期；N5~N8 低优登记 backlog 由用户决定
- 附件上传族（C5~C8、F3 克隆上传）因 IAB 无文件选择未测，可用 live E2E 或人工补测
