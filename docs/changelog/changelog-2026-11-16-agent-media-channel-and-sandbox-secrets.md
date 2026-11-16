# Agent 媒体通道、课程助理退役与沙箱密钥注入

> **TL;DR**：沙箱默认注入只开放 `QIHU_API_KEY`、`VOLCENGINE_VOICE_ACCESS_KEY` 并按 Agent 白名单隔离；PPT 默认字节 TTS/360 Seedream，创意工作收成 7 项视觉技能，课程助理退役。独立评审后补齐 AIO、TTS 完成态、默认生图、PNG/URL 响应和测试门禁；真实 TTS 与 360 生图冒烟均通过。

## 变更摘要

- **沙箱密钥注入**：active/request secrets 为空时，按当前 Agent 白名单内公共启用技能声明、两个允许键和宿主已有值取交集，per-call 注入；声明缓存按技能列表隔离，子代理传递最终白名单。扩展配置读取/解析失败时零注入，正常无配置仍按公共技能默认启用；旧 AUTOBIND 只保留给无 `available_skills` 的兼容路径。
- **AIO 执行语义**：env 调用显式返回非零退出码，复用 SDK 返回的 bash session ID 保持该通道 cwd；密钥只通过 `env=` 传递，不拼入命令。
- **创意工作**：白名单从 19 项收敛为 7 项视觉技能；SOUL 删除研究写作、视频剪辑及玻璃态卡片路线；首页关键词、卡片文案和新对话 PNG 案例同步。图片卡片默认 1:1，漫画/竖版默认 9:16、横版 16:9，避免声明比例与 360 三档输出不一致。
- **PPT 旁白**：`trainerai` provider 改走字节 `seed-tts-2.0`，默认 `zh_male_m191_uranus_bigtts`，输出 MP3。只有成功 152 才接受音频；151、提前 EOF、坏 JSON/非对象事件均失败关闭。使用官方 `enable_language_detector`，长文分片后经 ffmpeg 合成并原子发布，任一分片或合成失败不替换旧产物。
- **PPT 生图**：新增并注册 `backend_qihu360.py`；无 `IMAGE_BACKEND` 且无 `--backend` 时，有 QIHU 即默认 qihu360。generations/edits 使用 `extra_body.size/watermark/output_format=png/image`，不发顶层 `size` 或 `sequential_image_generation`；响应同时含 URL 与 b64 时 URL 优先，b64-only 保持兼容。
- **trainerai-imagine**：未显式 provider/model 时 QIHU 默认 360 Seedream；显式 GPT Image 不再被截获。非 Seedream、非单图、非 PNG 输出后缀在请求前失败；显式 size 优先；PNG magic 校验、参考图 MIME、URL 优先和测试环境隔离均已覆盖。
- **课程助理**：种子、专属测试和工作台卡片删除，`SEED_VERSION=2026-11-16.2`；不新增退役别名，不改 `appearance.ts`/`constants.ts`，不处理历史对话。真实首页 `HOME_COURSE_ITEMS` 的 Agent 目标也纳入 seed 守卫。
- **文档**：同步 PPT SOUL/SKILL、`TRAINERAI_PATCHES.md`、generate-audio、播客密钥纪律、`.env.example` 和部署指南。
- **范围记录订正**：执行时漏列的 3 个机械测试依赖（`test_seed_podcast_studio.py` 与两个前端 DOM 测试）已补入计划 Task 7 允许路径；不再使用“当时已获用户批准”的不准确表述。

## 评审修复记录

- 修复默认生图只支持显式 `--backend`、TTS 部分流误判成功、JPEG/PNG 后缀与字节不一致、显式模型误路由、AIO 非零退出丢失及 env 通道 cwd 不续接。
- 修复启用配置失败时技能默认启用造成的密钥 fail-open；密钥解析改用严格启用状态。
- 修复非 Seedream 模型仍发请求、`n!=1` 被静默忽略、显式 size 被比例覆盖、BMP/TIFF MIME 误标。
- 修复外部 `QIHU_API_KEY`/`QIHU_MODEL`/`QIHU_BASE_URL` 对测试的污染；比例契约从独立 TS 文件迁入 backend pytest，确保全量与 CI 能收集。
- 真实 360 冒烟额外发现响应同时含非图片 b64 与有效 URL 时优先 b64 的故障；Python/TS 均已改为 URL 优先，URL 下载失败时回退有效 b64，两个候选都失败才报错。

## 验收记录

- 沙箱/AIO/loader/播客扩展回归：`cd backend && uv run pytest tests/test_bash_secret_injection.py tests/test_aio_sandbox_bash_env_output.py tests/test_skills_loader.py tests/test_secret_context.py tests/test_subagent_executor.py ... -q` → `159 passed, 3 skipped`；覆盖配置损坏零注入、非零退出、连续 env 调用 cwd、两键限制与缓存隔离。
- PPT 相关回归：`cd backend && uv run pytest tests/test_ppt_master_*.py tests/test_seed_ppt_master.py -q` → `81 passed, 1 warning`；含默认无 backend CLI、TTS 151/EOF/坏事件、长文真实 ffmpeg 合成、双字段 URL 优先及 b64-only。
- PPT 完整性：`python3 skills/public/ppt-master/scripts/attribution_guard.py` → `exit=0`。
- 视觉比例与种子：`cd backend && uv run pytest tests/test_visual_media_ratio_contracts.py tests/test_seed_agents.py -q` → `24 passed, 1 warning`；三条比例契约由 pytest 收集，旧比例变异测试曾分别失败。
- trainerai-imagine：`cd skills/public/trainerai-imagine && npx --no-install tsx --test scripts/main.test.ts` → `49/49 passed`；外部 `QIHU_API_KEY=offline-test-key` 重跑同样 `49/49 passed`。
- 全技能相关回归：`npx --no-install tsx --test ./*.test.ts ./providers/*.test.ts` → `80/80 passed`。
- TypeScript 严格检查：`tsc --noEmit --strict ... scripts/*.ts scripts/providers/*.ts` → `exit=0`，无类型错误。
- 前端相关回归：`pnpm exec vitest run tests/unit/workspace/active-agents-sync.test.ts tests/unit/workspace/workspace-home.test.ts tests/unit/components/workspace/home-course-entry.dom.test.tsx tests/unit/components/workspace/workspace-home-i18n.dom.test.tsx tests/unit/core/agents/new-chat.test.ts` → `5 files / 61 passed`。
- 前端全量：`cd frontend && pnpm test` → `Test Files 263 passed`、`Tests 1717 passed`、`exit=0`。
- 定向 Playwright：`cd frontend && pnpm exec playwright test tests/e2e/agent-new-chat-guide.spec.ts tests/e2e/home-course-entry.spec.ts` → `33 passed`、`exit=0`。
- 真实字节 TTS：一句“你好，这是一次简短的语音验收。” → MP3 `21,357 bytes`、`24,000 Hz`、单声道、`2.664s`；未输出密钥值。
- 真实 360 Seedream：一张 1:1 极简图标 → PNG `3,527,027 bytes`、`2048x2048`；首次暴露双字段 b64 优先级问题，修复后复验通过；未输出签名 URL 或密钥值。
- 后端全量：`cd backend && uv run pytest tests/ -q` → `10035 passed, 89 skipped`、`exit=0`。
- 静态门禁：根 `make check` → `exit=0`；backend ruff、frontend ESLint/TypeScript/Prettier/knip/i18n、docs convention 全过。
- PR 结果：待评审与 CI 完成后记录。

## PR #11 评审修复

- 第一轮 CI（backend/frontend/E2E/lint/quick/perf/review）全部通过；AI 评审意见继续处理。
- AIO 仅在明确会话失效时清空旧 session ID 并无 session 重试一次，通用错误与沙箱死亡不重复执行。
- PPT 仅忽略空 data/`[DONE]` 控制帧，坏事件仍失败关闭；base64 分片允许空白但保持严格字符校验。
- 360 实际请求复用 `buildGenerationsBody`；尺寸归一有明确 warning，非 Seedream、非单图和未知 MIME 提前失败，本地参考图限制 10 MB。
- 恢复全局网络重试为 3 次；确定性参数错误保持单次尝试，最终失败报告使用真实尝试次数。

## 后续事项与限制

- AIO 的 session ID、cwd 和退出码使用符合 SDK 形状的离线客户端验证，未启动真实 AIO 容器；部署侧仍应复核 env 不残留、跨命令 cwd 与非零退出。
- 真实供应商验收覆盖字节单句 TTS 和 360 generations；edits、多语言音色及长文供应商调用使用 mock/本地 ffmpeg 验证，未逐项 live。
- `appearance.ts`/`constants.ts` 的历史图标与默认名按本批范围保留，不是现役卡片或 seed 入口。
- 本批按全新部署口径不写退役别名、不迁移历史会话；未修改 `.env`、根 `config.yaml`、compose、Docker，未触碰另一工作未跟踪的 live 测试。
