# TrainerAI Vendored Patches vs Upstream ppt-master

- Upstream: https://github.com/hugohe3/ppt-master (MIT)
- Base version: **v6.3.0**（2026-09-11 升级选定；此前 v2.10.0 2026-07-21 恢复时选定，首次 vendor 2026-06-19 为 v2.7.0，2026-06-29 丢失）
- Upstream pinned baseline：本地源树 `/Users/littlebear/Documents/ppt-master-main-9.8/skills/ppt-master`
  （zip 下载、无 git 历史）。**下次升级的 diff 基准** = 清单摘要
  `manifest_sha256=363cd587f6fdf0a3401c03907da57319083077694ae5fefddefa8f3756e9120c`
  （12,954 文件。算法钉死：逐文件 sha256 hex → `relpath+hex` 按字节序 LC_ALL=C 排序串接 →
  总 sha256，排除 `__pycache__/`，relpath NFC 归一。2026-09-12 重钉：旧值
  `04158079…` 无任何算法可复现、已作废）。生成/校验：
  `cd backend && uv run python scripts/ppt_master_manifest.py <基准树根> [--check <sha256>]`。
  上游 zip 档案 `~/Downloads/ppt-master-main.zip`：sha256
  `e047146e5d479aca2f9d6b5efb269267fef2f4c8f7933419bfc174d5354868d1`、
  92,569,685 B、zip 条目 13,035（zipfile API 计数）。上游 6.3.0 自带
  fail-closed `attribution_guard.py`（LICENSE SHA256 + frontmatter 三身份字段精确值 +
  gate marker 恰 1 次 + 9 个 gate 脚本 AST 结构）——**改 SKILL.md 时 frontmatter 不可动，
  覆盖段内不得出现 marker 字符串字面量**；vendored 冒烟测试已钉 guard 必须 pass。

## Patches（v6.3.0 映射表，2026-09-11 重打；P14–P19 为重打后追加，2026-10-05 回填登记）

| # | 文件 | 偏离 | 原因 |
|---|------|------|------|
| P1 | SKILL.md | frontmatter 后插 TrainerAI 覆盖段（8 条；frontmatter 不动，gate marker 不重复） | 声明 TTS/生图 override + P11/P12/P13 + C 扩展降级口径 + D3 铁律指向 SOUL.md；命令示例统一 `${SKILL_DIR}` 形态 |
| P2 | scripts/tts_backends/backend_trainerai.py | 新增（TrainerAI 专属，随升级原样移植） | 配音走 Voice Twin（360 预设），禁 edge-tts |
| P3 | scripts/image_backends/backend_trainerai.py | 新增（选择策略 + 文档化；docstring 补 6.3.0 注册表口径） | 生图 provider 选择（volcengine/qwen/zhipu 均为 6.3.0 原生 backend）；**`auto` 降级终点显式 = `manual`，不走上游 `host-native` Path B**（TrainerAI 宿主无宿主生图工具） |
| P4 | scripts/model_tiers.py | 新增（TrainerAI 专属，原样移植） | 4 档模型选择映射 /api/models 元数据；上游无同名脚本 |
| P5 | scripts/runtime_limits.py | 新增（TrainerAI 专属，原样移植） | 子进程输出 BoundedTextBuffer 防 OOM |
| P6 | scripts/mock_fixtures.py | 新增（TrainerAI 专属；2026-09-11 补 title/subtitle XML 转义=旧 K5 修复） | 前端/CI 确定性 fixture |
| P7 | scripts/ppt_visual_review.py | 新增（TrainerAI 专属；2026-09-11 补 sys.path 判重=旧 K3 修复） | 视觉质检包装；6.3.0 `SVGQualityChecker.check_file` 经 `svg_quality.checker` 再导出且签名向后兼容（新增 kwargs 可选），零改动移植 |
| P8 | scripts/notes_to_audio.py | trainerai 六接线点：import / env 前缀(QIHU_/ZYUN_) / `--provider` choices+默认 / 构造分支 / 生成分发分支 / `_provider_manifest_details` 分支 / `--list-voices` 分支（本次新增，旧版缺） | 接线 P2；6.3.0 的 tts_backends 扩到 5 后端（elevenlabs/minimax/qwen/cosyvoice/edge），注册链断言见 `test_ppt_master_tts_trainerai.py` |
| P9 | workflows/stages/generate-audio.md | 文档顶部插 TrainerAI Provider Override 块（新路径；旧路径 workflows/generate-audio.md 已不存在） | 默认 provider=trainerai(146)、edge 作废、预设音色表、trainerai WAV 无逐字时轴如实声明 |
| P10 | scripts/tts_backends/backend_trainerai.py | SSE `data: null` 防御（`(event.get("data") or {})` + isinstance 守卫）——已内嵌于 P2 模块 | 6.3.0 上游 5 个 TTS 后端无 360 SSE 同源代码，防御保留在 vendored backend 内 |
| P11 | SKILL.md（P1 覆盖段第 3 条） | 文档级禁用：`Acquire Via` 禁 `web`、不加载 image-searcher.md/executor-web-image.md、不运行 image_search.py、不产出 image_sources.json | 生产环境图库图片下载不可靠 + 素材自闭环；6.3.0 挂载点 9 处（清单见 Phase 0 结论文档）；`svg_quality` checker 对缺失 manifest 优雅跳过（已实测）；vendored `scripts/image_sources/` 代码保留不删 |
| P12 | SKILL.md（P1 覆盖段第 4 条） | 确认面 chat-only：恒视同 confirm-surface.md §1 chat 分支；禁 `confirm_ui/server.py` 全部调用形态；Default 与 **Beautify**（复用 Default Step 4 编排）同受约束；`split`/`resume-execute` 一并声明禁用 | 沙箱无端口发布通路；per-thread sandbox 跨会话失效；quick-generate/roundtrip 上游本就不触 confirm_ui |
| P13 | SKILL.md（P1 覆盖段第 5 条） | 禁 `svg_editor/server.py` 全部触点（Step 6 Mandatory 自启动/live-preview/visual-review）；预览走 artifacts（`template_preview_pptx.py` + LibreOffice PNG + present_files）；visual-review 阶段与 `visual_review.py` 同步禁用 | 同上；`visual_review.py` 依赖 live-preview server（不可达 exit=2） |
| P14 | scripts/svg_to_pptx/pptx_package/discovery.py | **功能性偏离**（commit `7a612e5eb`，2026-09-23 长任务稳定性批 2 F，changelog-2026-09-23-long-task-stability-batch.md:97,153）：+24 行——notes 启用但 notes/ 缺失时 WARN 到 stderr（修前静默返回 {}）；声明逐页备注但部分页缺 notes 文件时 WARN 缺页清单（前 10 个）。均不 hard fail，硬拦截留给 generate-pptx.md Step 7 质量门 | DEF-05：导出静默缺备注零信号，agent 无从自修复 |
| P15 | workflows/generate-pptx.md | **行为约束偏离**（同 commit `7a612e5eb`，changelog:78,98,152）：Step 1 按 Ultra 档位 gating 调研委托；Step 7 加备注完整性硬门禁 | D1 档位口径 + DEF-05 质量门（与 P14 WARN 配对：脚本软信号 + 工作流硬拦截） |
| P16 | workflows/stages/topic-research.md | **行为约束偏离**（同 commit `7a612e5eb`，changelog:77,151）：Execution Context 整段重写——subagent 可用性 = Ultra 档（仅 Ultra 派 `general-purpose` 隔离 worker，禁 named custom 类型——researcher 等定义已退役）；非 Ultra 档默认跳过外部检索（围绕已知规划或披露缺口，禁止主上下文 search/fetch 循环——1.9M token 事故源） | D1：防非 Ultra 档主上下文检索失控 |
| P17 | scripts/image_backends/backend_common.py | 语义等价（commit `2f95a2bdc`，2026-09-30 SkillScan 批）：`os.environ.get` → `os.getenv` | P1-S3 SkillScan 静态闸门 CRITICAL 规则合规 |
| P18 | scripts/image_backends/backend_bfl.py | 语义等价（同 commit `2f95a2bdc`）：`os.environ.get` ×2 → `os.getenv` | 同 P17 |
| P19 | scripts/image_backends/backend_fal.py | 语义等价（同 commit `2f95a2bdc`）：`os.environ.get` ×2 → `os.getenv` | 同 P17 |
| P20 | SKILL.md（P1 覆盖段第 9 条）+ workflows/generate-pptx.md Step 4 + workflows/governance/failure-recovery.md §3 | Chat 确认持久化：Stage1/Stage2 用户明确确认后立即写 `<project>/confirm_state.json`（stage、确认值摘要、confirmed_at）；恢复时与 `design_spec.md`/`spec_lock.md` 互证，一致则按断点续跑，禁止重新确认或重跑策划 | 2026-10-14 事故：确认证据仅存 chat 上下文，上下文丢失后按恢复规则重跑策划并发生方案漂移 |
| P21 | SKILL.md（P1 覆盖段第 10 条）+ workflows/generate-pptx.md Step 1 | 源图转写落盘与复查收敛：首次读准立即写 `<project>/analysis/source_image_transcriptions.md`，后续以文本为准；单格存疑只复查该格，整图重读 ≤1 次；OCR 与目视冲突以目视为准并记录冲突格 | 2026-10-14 事故：表格截图反复整图 OCR/目读 15+ 轮，源文档全量重读 3–4 次，转写结果不落盘导致 token 失控 |
| P22 | SKILL.md（P1 覆盖段第 11 条）+ workflows/generate-pptx.md Step 6 | Step 6 前与长任务每 5 页执行 `df -h` 磁盘预检，工作区分区可用 <1GB 即停并报告；每写完一个 SVG 校验大小 >0，0 字节即删除、报告并停止 | 2026-10-14 事故：根分区写满后 P01 产生 0 字节 SVG，agent 仅释放 4.7MB 即继续写页，最终导出必败且浪费 token |

**硬钉测试**：`backend/tests/test_ppt_master_skill_vendored.py`——attribution_guard 冒烟
（vendored 补丁后必须 exit 0）+ P1 覆盖段 P11/P12/P13 marker 断言。P12/P13 为文档级
软约束，若 E2E walkthrough 发现 agent 无视覆盖段，升级为代码级 env kill-switch。

## Known differences / deferred minor issues（2026-07-22 登记；2026-09-11 复评）

| # | 位置 | 状态 |
|---|------|------|
| K1 | tts backend_trainerai `resolve_voice_id` | **维持登记**：PRESET_VOICES 之外的合法 360 音色静默落回 146（warning）——有意收窄为旁白预设，fail-open 对 agent 友好 |
| K2 | ~~SSE 空 code~~ | ✅ 已修并入 P10（2026-07-27） |
| K3 | ppt_visual_review `_load_checker` sys.path 累积 | ✅ 2026-09-11 修复（判重后 insert） |
| K4 | runtime_limits `rss_mb` macOS 启发式误判 | **维持登记**：best-effort 监控展示用途 |
| K5 | mock_fixtures title XML 转义 | ✅ 2026-09-11 修复（stdlib escape + 回归测试） |
| K6 | ppt_visual_review `page_index` 无下划线文件名 fallback 撞号 | **维持登记**：仅质检展示排序用途 |

上游升级时按本表逐一重打补丁。
