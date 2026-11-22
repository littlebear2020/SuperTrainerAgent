# 服务器实测问题修复与 TTS 单通道收敛

> **TL;DR**：本批按四个主 PR 串行修复实测问题；当前提交覆盖停止生成保留草稿与 Task 0 取证，后续 PR 继续处理播客 URL、TTS 单通道和前端/live 断言；不自动部署、不修改真实配置、不执行未授权付费调用。

## 当前已落地

- P1（PR #40，已合入）：InputBox 在表单捕获阶段拦截 streaming 停止，保留文本、附件与 localStorage 草稿；正常发送仍清空。
- P11（PR #41，已合入）：播客 URL 读取在连接 socket 已分离时使用 `response.fp.raw._sock` 设置剩余超时；EOF 安全结束，开放响应缺 socket 时响亮失败，安全链不变。
- P3（PR 3 本地四提交进行中）：目录/API 只保留 4 个中文字节音色；统一入口在任何网络调用前拒绝 preset/Edge/裸 Edge ShortName；调用方与 ViMax 不再降级换声。
- P3 清理：PPT Master 共用字幕算法迁至 `subtitle_timing.py`，CosyVoice/ElevenLabs/MiniMax import 回归通过；Edge/360 客户端、依赖、配置结构与 sandbox 安装项移除；`config.example.yaml` 升至 33，真实 config 未触碰。
- Task 0：确认实现代码相对计划基准无漂移；P6/P7 证据材料位于 `temp/live-test-remediation-20261122/`，均不满足条件 PR 门禁。
- PR 3 已按最新决策固定为四笔本地提交、全部验证后一次性 push、同一 PR 合入与回滚。

## 后续固定范围

- PR 2：播客 URL 抓取 socket 超时，保留完整 SSRF 安全链。
- PR 3：只保留 4 个中文豆包音色与既有克隆，移除 Edge/360 TTS。
- PR 4：动态默认徽标、用量标题占位、首渲文案与 live 断言。
- 新增独立后续 PR：Sandbox 空闲释放在本地与部署默认均为 7200 秒；不冒充 P6 已查明根因。
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
- PR 3 旧夹具迁移后四个文件回归 → 80 passed；OCR 与旧签名测试修正后的最终后端全量 `uv run pytest tests/ -q` → **10159 passed, 91 skipped**。
- PR 3 sandbox：`docker build --pull=false --network=host --progress=plain -t deerflow-sandbox:tts-single-channel-20261122 -f docker/sandbox/Dockerfile .` → 构建成功，镜像 ID `sha256:6fcb9108898f…`；镜像内 `EDGE_TTS_NOT_INSTALLED; FFMPEG_OK`，复制技能后 `COPIED_PPT_SKILL_DOUBAO_AND_SUBTITLE_OK`。
- PR 3 本地 OCR → 5 findings：3 个旧注释/死参数已清理，1 个非中文默认值前置拒绝已按 RED→GREEN 修复（相关 74 passed）；`backend_edge.py` 仍存在为误报，已用文件系统核实其不存在。
- `python3 scripts/check_docs_convention.py` → OK 仓库治理约定全过。

## 阻塞与未执行

- P6：目标 run 终止记录与 checkpoint 未保留，无法确认免费恢复；外部阻塞、未重跑、未开 PR。
- P7：只有单次异常和后续未复现材料，缺少稳定数值序列；证据不足、未开 PR。
- 真实字节配音：`LIVE_NOT_VERIFIED`；未获付费授权，不执行。
- 部署后 L1/L2：未部署、未执行，不作为主 PR 合并门禁。
