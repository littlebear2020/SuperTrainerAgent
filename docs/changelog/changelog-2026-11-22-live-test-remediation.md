# 服务器实测问题修复与 TTS 单通道收敛

> **TL;DR**：四个主 PR、Doubao hotfix 与 Sandbox #45 已合入；P6/P7 按证据收口且未开实现 PR；最终复核发现的退役音色重生绕过正在独立 hotfix 验收；不自动部署、不修改真实配置、不执行未授权付费调用。

## 当前已落地

- P1（PR #40，已合入）：InputBox 在表单捕获阶段拦截 streaming 停止，保留文本、附件与 localStorage 草稿；正常发送仍清空。
- P11（PR #41，已合入）：播客 URL 读取在连接 socket 已分离时使用 `response.fp.raw._sock` 设置剩余超时；EOF 安全结束，开放响应缺 socket 时响亮失败，安全链不变。
- P3（PR #42，已合入）：目录/API 只保留 4 个中文字节音色；统一入口在任何网络调用前拒绝 preset/Edge/裸 Edge ShortName 与白名单外 Doubao；调用方不降级换声，前端旧值保留但停用。
- Doubao 直连 hotfix（PR #43，已合入）：`VoiceCloneService` 目录音色直连 DoubaoTTSClient，不再误查克隆仓库；voice-twin 目录 API 与共享白名单一致。
- P3 清理：PPT Master 共用字幕算法迁至 `subtitle_timing.py`，CosyVoice/ElevenLabs/MiniMax import 回归通过；Edge/360 客户端、依赖、配置结构与 sandbox 安装项移除；`config.example.yaml` 升至 33，真实 config 未触碰。
- Task 0：确认实现代码相对计划基准无漂移；P6/P7 证据材料位于 `temp/live-test-remediation-20261122/`，均不满足条件 PR 门禁。
- PR 3 已按四笔提交完成评审与合入。
- P5/P9/P10/P13（PR #44，已合入）：动态默认徽标、缺标题统一占位、通用合成文案和 composer/live 断言已按 TDD 完成；英文默认徽标 i18n 回归修复后，GitHub 全量 CI 与 OCR 通过。
- Sandbox（PR #45，已合入）：本地与默认未覆盖的空闲释放统一为 7200 秒，显式配置与 `0=禁用` 保留；部署说明与现行模块文档已同步。
- 最终复核 hotfix（本地实现）：批量/单帧重生在后台任务、CAS、视觉与 TTS 前同步拒绝退役或目录外音色；缺省 `voice_id` 的历史默认行为不变。

## 后续固定范围

- 最终复核 TTS 重生 hotfix：完成全量与远端门禁后独立合入；不冒充 P6 已查明根因。
- Writer、P4、P8、P12、P15 不加回；P6/P7 未取证不开 PR。

## 验收记录

- `git diff 0e84b148..origin/main --stat` → 14 个文档文件，无实现代码漂移。
- 基线 `cd frontend && pnpm test` → 279 files / 1806 tests passed。
- 基线 `cd backend && uv run pytest tests/ -q` → 10273 passed, 95 skipped。
- P1 RED：`pnpm exec vitest run --project dom tests/unit/components/workspace/input-box-stop-draft.dom.test.tsx` → 两条 streaming 用例因停止后文本变空失败，正常发送对照通过。
- P1 GREEN：同一命令 → 3 passed；移除旧 streaming 早退后相关 InputBox 回归 → 8 files / 33 tests passed。
- `cd frontend && pnpm check && pnpm test` → check 通过；280 files / 1809 tests passed。
- PR #40 GitHub checks → E2E、frontend-unit、lint、quick-checks、perf-budgets 全绿；AI Code Review 0 findings；已 rebase 合入。
- PR #41 → 本地 OCR 0 findings；GitHub OCR 1 条 low（`_sock` 私有属性说明，保留既定行为）；CI 全绿并已合入。
- P11 RED→GREEN：`uv run pytest tests/test_podcast_studio_ingest.py -q` → 新增 5 条在旧实现因 SocketIO 无 `settimeout` 失败；修复后 47 passed。
- P11 安全/真实响应：本机 Python 3.12.13 与沙箱 Python 3.10.12 的 close/chunked/redirect/EOF 探针均 `SOCKET_PROBE_OK`；公开 sandbox intake → `requested_url=https://1.1.1.1`、`final_url=https://one.one.one.one/`、`mime=text/html`、`PUBLIC_INTAKE_OK`。
- `uv run ruff check tests/test_podcast_studio_ingest.py ../skills/public/podcast-studio/scripts/podcast_studio.py` → All checks passed；format check → 2 files already formatted。
- PR 2 全量 `cd backend && uv run pytest tests/ -q` → 10278 passed, 95 skipped。
- PR 2 根 `make check` → backend ruff、frontend eslint/tsc/prettier/knip/i18n 与 docs convention 全过。
- PR 2 本地 `OCR_NO_UPDATE=1 ocr review --audience agent ...` → 1 file reviewed，0 findings。
- PR 3 提交①：目录/API/统一入口相关回归 → 168 passed, 3 skipped；缓存旁路 26 条通过。
- PR 3 提交②：去降级与 video_gen 组合回归 → 259 passed。
- PR 3 提交③：PPT/voice_twin/video_gen 相关回归 → 355 passed；`uv sync` 移除 `edge-tts` 与仅其需要的 `tabulate`，`EDGE_TTS_NOT_INSTALLED`；后端 ruff 全过。
- PR 3 旧夹具迁移后四个文件回归 → 80 passed；高危白名单与空 ID 修复后的最终后端全量 `uv run pytest tests/ -q` → **10172 passed, 91 skipped**。
- PR 3 sandbox：`docker build --pull=false --network=host --progress=plain -t deerflow-sandbox:tts-single-channel-20261122 -f docker/sandbox/Dockerfile .` → 构建成功，镜像 ID `sha256:6fcb9108898f…`；镜像内 `EDGE_TTS_NOT_INSTALLED; FFMPEG_OK`，复制技能后 `COPIED_PPT_SKILL_DOUBAO_AND_SUBTITLE_OK`。
- PR 3 本地 OCR → 5 findings：3 个旧注释/死参数已清理，1 个非中文默认值前置拒绝已按 RED→GREEN 修复（相关 74 passed）；`backend_edge.py` 仍存在为误报，已用文件系统核实其不存在。
- PR 3 提交④ RED → 纯逻辑 9 failed、DOM 17 failed，均为旧条目仍显示/旧值未保留或未阻断；GREEN → 纯逻辑 11 passed、DOM 29 passed，相关共享组件回归 31 passed，`pnpm typecheck` 通过。
- PR 3 提交④ 最终全量 `pnpm test` → 281 files / 1813 tests passed；相关 Playwright → 4 passed + 1 passed；语言/音色用例旧实现 7 个语言选项 RED，新实现仅中文与 4 个字节音色 GREEN。
- PR 3 四提交整体本地 OCR → 43 files / 9 findings；白名单零交集越权回落、角色池白名单和非中文配置兜底均已 RED→GREEN 修复，白名单顺序兼容保留；修复 diff 复核 → **0 findings**。其余 low 维护建议未扩大范围。
- PR #42 首轮 GitHub CI 全绿；GitHub OCR 8 findings，其中角色池 import 固化为 medium，已改为调用时惰性计算并补 RED→GREEN；该修复本地 OCR → 0 findings。
- PR #42 第二轮 GitHub CI 全绿；OCR 后续发现已收敛为空默认渲染前拒绝、池空保留默认回退但明确告警、单口播不误判、无效 speaker 告警及前端命名/默认 ID 复用；最终本地 OCR → 0 findings。
- PR #42 最终轮高危项：共享入口现拒绝目录/白名单外 Doubao 与空 ID，正向测试夹具使用目录内真实 ID；PPT 失效 CLI 参数文档已同步，修复 diff 本地 OCR → 0 findings。
- Doubao 直连 hotfix RED → 目录音色误查克隆 repo、目录 API 白名单不一致；GREEN → 相关 260 passed，全量 10174 passed / 91 skipped，根 `make check` 与本地 OCR 0 findings；PR #43 CI/OCR 全绿并已合入。
- PR 4 P5 RED → 动态徽标缺失且手写后缀残留；GREEN → InputBox 7 条通过，当前档唯一徽标、非默认不误标、仅剥离末尾后缀。
- PR 4 P9 RED → Agent/视频缺标题回退 ID、前端无占位；GREEN → 后端相关 92 passed、前端 usage 8 passed，首条用户消息回退保留。
- PR 4 P10 RED → 通用 composing 为“重新合成中”；GREEN → active-status 3 + StatusBadge 4 passed，主动 pending 文案未改。
- PR 4 P13 RED → 无稳定 composer testid；GREEN → InputBox 7 条与 typecheck 通过；live `--list` → 20 tests / 8 files，仅列出不执行。
- PR 4 rebase 后最终全量：backend `uv run pytest tests/ -q` → **10176 passed, 91 skipped**；frontend `pnpm test` → **281 files / 1818 tests passed**；相关 mock E2E → **8 passed**；根 `make check` 与 docs convention 通过。
- PR 4 本地 OCR → 11 files / 4 findings；默认徽标仍严格按 `mode_defaults`，不把配置缺失后的临时回退误标为默认；终态缺标题占位遵循 D12；重复徽标样式 low 建议 deferred，未扩大范围。
- PR #44 首轮 GitHub E2E → 119 passed / 1 flaky / 2 skipped / 1 failed；英文 PPT 导览因新徽标硬编码中文“默认”稳定失败，本地同用例 RED 复现。徽标接入 `inputBox.defaultModelBadge` 后同用例 GREEN，InputBox 7 passed、typecheck/i18n 通过；前端全量仍为 281 files / 1818 tests passed，根 `make check` 通过。
- PR #44 第二轮 `gh pr checks 44 --watch` → backend/frontend/E2E/perf/lint/quick-checks/AI-review 全部 pass；GitHub OCR 4 条 low，无 Critical/Important；已 rebase 合入，merge commit `6441bf55b487a35fdcdd92f70f60311bb2cf1da3`。
- `python3 scripts/check_docs_convention.py` → OK 仓库治理约定全过。

- Sandbox 7200 TDD：`uv run pytest tests/test_sandbox_idle_timeout.py -q` 在旧实现为 2 failed / 2 passed（缺省 600、一小时空闲被回收）；改为 7200 后，新测试及相关 Sandbox 回归 53 passed / 1 skipped，显式 30/0 均保留。
- Sandbox 7200 本地 OCR → 3 files / 0 findings；根 `make check` 与 docs convention 通过；未读取或修改真实 config，未部署。
- Sandbox 最终后端全量 `uv run pytest tests/ -q` → **10180 passed, 91 skipped**；P2/P4/P12/Writer 分享页/P14/P15 已分别登记 `docs/findings-backlog.md`，无对应产品代码 diff。
- 整体复核后的文档同步：`config.example.yaml` 仅把空闲释放注释从 600 改为 7200（无结构变化，不递增 `config_version`）；部署指南明确已有显式覆盖仍优先；backend 模块说明移除已删除的 Edge/360 文件与降级链。
- PR #45 最终 `gh pr checks 45 --watch` → backend-unit-tests、quick-checks、AI Code Review 全 pass，OCR 4 files / 0 findings；已合入，merge commit `4210d612a483475f9c19739c65d0d32db0cdff3b`。
- 重生门禁 hotfix：退役音色 + CAS 的批量/单帧用例在旧实现均 RED（DID NOT RAISE）；前置统一校验后 2 passed，相关重生与单通道回归 229 passed，本地 OCR 0 findings、根 `make check` 通过；后端全量 **10182 passed / 91 skipped**；未执行真实付费重生。

## 阻塞与未执行

- P6：目标 run 终止记录与 checkpoint 未保留，无法确认免费恢复；外部阻塞、未重跑、未开 PR。
- P7：只有单次异常和后续未复现材料，缺少稳定数值序列；证据不足、未开 PR。
- 真实字节配音：`LIVE_NOT_VERIFIED`；未获付费授权，不执行。
- 部署后 L1/L2：未部署、未执行，不作为主 PR 合并门禁。
