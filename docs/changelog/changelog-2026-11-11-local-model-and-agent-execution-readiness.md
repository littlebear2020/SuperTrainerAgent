# 本地模型、Agent 执行与首页/作品验收

> **状态**：已执行，但未达到整批完成标准；任务 1–8 已完成，任务 9 的原始后端全量 Step 2 保持未勾选。当前代理环境下该命令有一项失败，不以无代理环境覆盖后的通过结果替代原始结果；其余门禁、真实验收与服务恢复记录如下，等待用户审查。
> **TL;DR**：Terra 切换到 `openai/gpt-6.1-terra` 并新增 DeepSeek；本地 Aio 自建镜像恢复隔离 bash、PPT、Remotion 与知识卡执行；首页三卡和指定样例已更新，作品列表恢复全状态与两类入口；真实业务产物已验收，下载修复仅兼容“全 fake-IP DNS + 回环代理”。

## 变更摘要

- Terra 保留旧内部名 `gpt-5.6-terra` 作为兼容别名，上游改为 `openai/gpt-6.1-terra`，显示名为 `360·GPT 6.1 Terra`；实测 `supports_vision=true`、`supports_thinking=false`。360 通道拒绝顶层 `thinking`，用户已明确接受该能力边界。
- 新增 `deepseek-v4.1-flash` → `deepseek/deepseek-v4.1-flash`，使用 `PatchedChatDeepSeek` 与 `api_base`。默认模型仍为 `qwen3.8-flash`，脚本、字幕、ViMax、生图和生视频模型/provider 配置未改；`config_version=31`，30 保留给既有 W2 约定。
- 本地切换到 `AioSandboxProvider`，镜像 `deerflow-sandbox:local-cee995a7`，`allow_host_bash=false`、`environment={}`。example 的活跃默认仍为 Local，Aio 是显式选项。
- 经用户单独授权，`docker/sandbox/Dockerfile` 只增加基座 `warmup_task = None` 构建补丁，解决 Rosetta 下 eager pool 预热阻塞；此前试验的 uvloop/OTel/MCP 改动均已撤回，未在容器内安装依赖。
- 首页三个视频 badge 中文统一为 `AI视频`、英文为 `AI Video`；第一卡改为 `文字动效 / Text animation`，Agent 短名继续为 `课程视频 / Course video`。既有媒体 URL、路由和创建框类型未变。
- 作品列表将 `ai_image + ai_visual` 归入 AI 生图、`ai_video` 归入 AI 生视频；保留全部状态、全局最近 100 条和每类 6→12 本地分页；详情、分镜、失败重试确认、删除确认复用既有能力，只有 `ready && !artifact_missing` 可预览/下载。
- 经用户单独授权的最小下载修复：`backend/app/gateway/_shared/safe_download.py` 与 `docker/production-worker/pipelines/net_guard.py` 仅在全部 DNS 记录属于 `198.18.0.0/15` 且对应 scheme 存在回环 HTTP(S) 代理时放行；Gateway 显式经代理访问原域名并保留 Host/SNI。无代理、IP 字面量、混合 DNS、其他私网仍拒绝；最终自审又补上 Worker 对 `NO_PROXY` 绕过的拒绝，防止直连 fake-IP。

## 首页媒体

- `文字动效示例视频.mp4` → `frontend/public/course-samples/remotion.mp4`：源第 30 秒起连续 10 秒；960×540、24fps、H.264、yuv420p/tv、无音轨、faststart；1391759 bytes；SHA256 `d441db1564e7064f785d71e8f5571e0250076a85c269d07d1180077f58d79ed6`。
- `图片动效示例视频.mp4` → `frontend/public/course-samples/image-motion.mp4`：源第 15 秒起连续 10 秒；相同公开规格；1211349 bytes；SHA256 `ac28c41bef40b6f0636f401bdf535345d1659d797b933db700ac1549e226b265`。
- 两个根目录源视频只读且未提交；hash 分别保持 `6f42dec66dbf95c0a59381acb9d6b9db6318e129677ee7f5f63481fe0db8dd68`、`5ffa7ceaa930e44864e588647905ffc85b4a3ce01588e0a3649639d715e9bb67`。第三卡 `ai-video.mp4` 未改。

## 四组独立验收结论

### A. 模型组：通过

- 两个目标模型的普通文本、流式、工具调用和工具结果续轮真实探针合计 `6 passed`。
- Terra 256×256 纯红图真实识别通过；thinking 上游返回 `Unknown parameter: thinking`，保持关闭并经用户接受，不宣称无功能损失。
- 两个模型各自完成真实 Aio bash marker；页面模型列表和旧对话可用，默认/视频配置未改。

### B. 沙箱与真实能力组：真实能力通过，交付门禁见下文

- 自建镜像 ID `sha256:6beffeba3b93c0de85b03bb8f633ad01cc38268a24ac565f3d973e724a87932a`，`linux/amd64`；基座 `1.11.0` digest `sha256:6328d7fd2f0ff0b4c147c3d05b3df1ce331f4a482eb6e550ecd64ed1fcf906e7`。
- `/v1/sandbox`、`/v1/shell/exec`、`/v1/bash/exec` 分别验证；PPT 依赖、模板 hash、真实静帧、文件写读、只读技能挂载、回环端口、3g memory/2g shm 均通过。
- 既有 Agent live `test_agent_uses_bash_tool` 与 `test_write_then_read` 实际 `2 passed`；新模型也分别完成 bash 闭环。
- PPT 原生改稿：2 页，第一页 slide XML 逐字节不变，第二页标题改为“本地验收第二页”；receipt `passthrough=1 rebuilt=1`，readback/delivery 通过；PPTX 29445 bytes，页面下载 hash 与产物一致。
- Course Video：真实 `npx remotion render FireStartVideo`；输出 1920×1080、30fps、H.264+AAC、9.898667 秒、1647242 bytes，页面可下载。
- Creative Studio：知识卡包含精确中文“摩擦生热/摩擦做功/温度升高/达到燃点”，SVG 7762 bytes、MP4 2197682 bytes（1920×1080、5.056 秒），预览/下载通过。海报 PNG 1080×1920、1740914 bytes，预览/下载通过。
- 海报 provider 过程不透明处已保留：Seedream 三次失败，Agent 随后使用 Gemini 的真实产物；用户明确接受该结果。本批未重复付费重试，也不把该过程写成 Seedream 成功。
- Co-writer：大纲确认后全文完成；Markdown 3854 字符、JSON 4 messages，页面/导出无 `[[STAGE:`、system-reminder、workspace_files 或 uploaded_files 内部标记。
- 首页独立生图 `vg_c7d88c4e0fe6`：running/75% 已在作品列表可见、详情可开且不可预览/下载；ready 后真实雪山图 1152×2048、260546 bytes，无占位警告；最终 MP4 1152×2048、30fps、H.264+AAC、4.300 秒、2002268 bytes。页面播放与下载通过，下载 SHA256 `abde0dc8973000a56ba59a5ed0e52cf0b65459bf09655f2d61d5ac0a5f385255` 与产物一致。
- 首页独立生视频 `vg_53f71d70b37c`：running/75% 已可见且门控正确；既有 Seedance 2.0（360）真实任务 195 秒完成，源 MP4 720×1280、24fps、4.041667 秒、1669606 bytes；最终 MP4 1152×2048、30fps、H.264+AAC、1.632 秒、803662 bytes。页面播放与下载通过，下载 SHA256 `41ee4f2f1a90af0350dbed5fc1f426d25d2245d616212a3fb9049008866214d4` 与产物一致。

### C. 首页组：通过

- 真实页面与双语/浏览器测试确认三卡 badge、第一卡标题、既有路由和 Agent 短名。
- 两个替换后的视频均实际进入 `playing` 且 `currentTime` 增长；仅当前面板播放、页面隐藏/reduced motion 停止、失败降级和移动端旧行为由既有 E2E 覆盖并通过。

### D. 作品组：通过

- 新 `ai_image` 与 `ai_video` 均在 ready 前真实出现，ready 后同卡预览/下载恢复；历史 draft/failed 可见且原详情只读可开，未自动重试或删除用户任务。
- 本数据库没有历史 `ai_visual`，因此真实数据归类为“不适用”；其分类、重试和操作由 unit/mock E2E 覆盖，未伪造历史任务。
- 没有既有 missing 任务；只对本批新视频产物做临时改名，真实页面显示“产物已清理”、预览禁用且无下载，随后立即恢复并核对 hash；刷新后预览/下载恢复。
- 外层“我的作品/我的资产”双 tabs 保持；本批测试项目完成真实新建、读取、更新和删除，既有项目未改。

## 旧功能与边界

- 旧 PPT 对话深链直接刷新后历史消息、上传记录和工具步骤仍可读；临时文本上传显示真实文件名后从 composer 移除，未提交模型调用。
- MCP 设置页仍显示既有 `metaso`、`so_360_search_mcp` / `so_spider`、`zhitumcp` 条目；未修改开关或 `extensions_config.json`。
- 所有真实业务任务已终态；没有根据后台 killed 通知推断业务结果，没有删除既有用户任务或数据库。
- 内置浏览器没有 `pageerror` 监听接口，真实页面的 uncaught JavaScript error 自动覆盖标记为“人工/未自动覆盖”；mock E2E 的 pageerror 断言通过，但不冒充真实页面监听。

## 验收记录

每行保留实际命令与关键输出；`NOT RUN` / 失败不写成通过。

- `cd backend && env -u RUN_LIVE PYTHONPATH=. uv run pytest <Task 1 定向文件> -q` → 原环境先出现 `test_get_thread_mounts_includes_acp_workspace` 的 DooD 路径差异；`DEER_FLOW_HOST_BASE_DIR=` 后 `196 passed`。
- Task 2 名册/工厂/配置回归 + ruff → `215 passed`，`All checks passed`，`2 files already formatted`。
- `cd backend && RUN_LIVE=1 PYTHONPATH=. uv run pytest tests/test_model_roster_live.py -v -s -rs` → 修正上游必填 wire 参数后基础探针 `6 passed`；Terra vision 通过，thinking 真实 400 并保持 false。
- Task 3 离线回归 + ruff → `216 passed`，ruff/format 通过。
- `docker build --platform linux/amd64 -t deerflow-sandbox:local-cee995a7 -f docker/sandbox/Dockerfile .` → 最终镜像 `sha256:6beffeba3b93…`；四目录非空、双路由与真实 PPT/Remotion/知识卡预检通过。
- Task 4 Aio 定向与相邻回归 → `157 passed`，ruff/format 通过。
- `cd backend && RUN_LIVE=1 PYTHONPATH=. uv run pytest tests/test_client_live.py::TestLiveToolUse::test_agent_uses_bash_tool tests/test_client_live.py::TestLiveMultiToolChain::test_write_then_read -v -s -rs` → `2 passed`，无 Local-disabled skip。
- Task 5 真实工具加载回归 → `7 passed`。
- Task 6 前端 node/dom/E2E → node `21 passed`、dom `15 passed`、Playwright `15 passed`。
- Task 7 首页 node/dom/E2E（含作品真媒体） → node `3 passed`、dom `10 passed`、Playwright `18 passed`。
- `cd backend && env -u RUN_LIVE PYTHONPATH=. uv run pytest tests/test_safe_download.py -q` → `17 passed`；ruff/format 通过。
- `cd docker/production-worker && ../../backend/.venv/bin/python -m pytest tests/test_outbound_guard.py tests/test_download_asset_hardened.py -q` → 初次 `26 passed`；自审补 NO_PROXY 防护后最终 `27 passed`，真实 CDN 守卫仍为 `REAL_FAKE_IP_GUARD_OK`。
- Worker 独立全套 `../../backend/.venv/bin/python -m pytest tests/ -q` → 两次均未完成，不记为通过：首次 900s 超时，第二次在同一位置持续无 CPU 进展后停止。定位到既有 `test_pool_claim_isolation.py` 认领用例未释放共享渲染信号量，后续 `test_worker_ownership_lost.py` 阻塞；后者单独 `1 passed`，与 lease 文件组合 `21 passed`，而 pool + ownership 最小组合 30s 超时。该旧测试不在授权修改范围内，未修改。
- **原始后端门禁**：`cd backend && env -u RUN_LIVE uv run pytest tests/ -q` → `2 failed, 9936 passed, 31 skipped`（785.59s）。`test_removed_keys_absent_from_repo_config` 因本批精简本地配置时丢失既有安全注释而失败，已只恢复注释并定向 `1 passed`；`test_probe_failure_logs_actionable_message` 因本地代理让不存在域名返回 HTTP 502、而非连接异常，原始定向复现失败。
- 环境判别：`NO_PROXY=* no_proxy=* env -u RUN_LIVE uv run pytest tests/test_worker_probe_url.py::test_probe_failure_logs_actionable_message -q` → `1 passed`；不修改业务代码或旧测试。
- 无代理后端全量回归：`NO_PROXY=* no_proxy=* env -u RUN_LIVE uv run pytest tests/ -q` → `9938 passed, 31 skipped`（537.11s）；不能替代上方原始命令结果。
- `cd frontend && pnpm check` → 退出 0；ESLint `0 errors`（既有 123 warnings），TypeScript 与 Prettier 通过。
- `cd frontend && pnpm test` → `249 test files / 1604 tests passed`（29.99s）。
- `cd frontend && CI=1 E2E_PORT=3100 pnpm test:e2e`（真实 Gateway/Worker 仍在）→ `72 passed, 4 failed, 1 flaky`；失败均发生登录/首页跳转。停止真实 Gateway/Worker 后，同命令定向复核 5 个相关用例 → `5 passed`；确认是未 mock 请求碰到真实服务的环境污染，未改测试。
- 隔离真实 Gateway/Worker 后完整重跑 `CI=1 E2E_PORT=3100 pnpm test:e2e` → `77 passed`（1.6m），无失败或 flaky。
- `cd frontend && pnpm perf:check` → 退出 0；生产构建成功，五路由资产测量完成；`/workspace/video-gen` JS `3559652 B`、CSS `244142 B`。
- 仓库根 `make check` → 退出 0；backend ruff `1309 files already formatted`，Frontend 检查、knip、i18n（599 叶子）与 docs convention 全过。
- `python3 scripts/check_docs_convention.py` → `OK 仓库治理约定全过`。
- `git diff --check` → 无输出、退出 0；最终编辑后仍需复跑。
- 恢复本地真实服务后：`2026 /`、`3000 /`、`8001/health`、`8040/health` 与 `2026/workspace` 均 HTTP 200；数据库无 active task/run；无本批测试容器残留，只保留无关的既有 open-notebook 容器；两个根目录源视频 hash 未变。恢复后的浏览器由用户手动接管，未继续自动控制。

## 本地提交与审查边界

- `2e200da4 chore(config): 更新 Terra 并新增 DeepSeek 模型`
- `f2f400cc chore(config): 回填 Terra 实测能力标记`
- `a6d8d07c chore(config): 启用隔离 Aio 沙箱恢复执行能力`
- `b3403210 fix(video-gen): 恢复作品分类与未完成任务`
- `0920cae4 feat(workspace): 统一视频入口命名并更新示例`
- `83ceda3a fix(video-gen): 兼容本地代理的素材下载`
- `7a242c08 fix(video-gen): 拒绝绕过代理的 fake-IP 下载`
- 文档提交主题：`docs: 记录本地模型、Agent 与首页作品验收`；只创建本地 commits，不 push、不建 PR、不部署。
- 最终为同一执行者自审（用户要求单执行者，未派生子代理）；这种审查弱于 fresh reviewer。未发现新的模型/首页/作品 Critical 或 Important 问题；发现的 Worker NO_PROXY 边界已按 RED→GREEN 修复并提交。

## 未解决事项

- 原始后端全量命令未全绿：上述 Worker 探测用例受本机回环代理影响。必须保留原始失败记录；无代理回归只证明代码在非代理环境下的结果。
- Worker 独立全套受既有测试共享信号量污染而挂起，具体文件和复现见验收记录；本批未获授权修改该旧测试，不能把超时写成通过。
- 真实页面 `pageerror` 未自动监听，需用户人工验收；不能声称“无 pageerror”。
- 旧 `model_connectivity_check.py` 的 Terra 硬编码漂移已登记 `docs/findings-backlog.md`，本批未修改脚本。
- 本地 Docker/Rosetta 结果不代表公司服务器 provisioner/Docker 已验证；本批不部署。
