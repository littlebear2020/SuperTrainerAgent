# 播客 Agent 与音频创作

> **状态**：已执行（2026-11-12；代码、四份真实 MP3、人工试听及本地门禁完成）。实时安全 URL 未验收，按用户决定由用户后续自行验收；不宣称四模式全部验收通过。
> **TL;DR**：新增 `podcast-studio` Agent、四模式共享技能及首页播客入口，保留 co-writer/Writer；四份真实 MP3 的编码、分片、顺序、时长及人工试听通过。只做本地 commits，不 push、不建 PR、不部署；实时 URL 验收由用户后验。

## 变更摘要

- 新增 `skills/public/podcast-studio/`：统一 PDF/DOCX/MD/HTML 输入、非信任 HTML 清理、URL 公网地址与重定向检查、来源/稿件 hash、长文限制、按句分片、确认门和渲染 manifest。100,000 字符上限及 300 字 turn 内分片均 fail-closed。
- 新增直接豆包 TTS helper，固定云舟男声与 Vivi 女声两个 stock，不使用克隆、preset、Edge 或静默降级。分片按 turn 顺序合成、按 manifest 顺序拼接，未启用并发分片；双人轮间停顿 300ms，最终 `libmp3lame` 重编码，失败不补静音。
- 新增 `podcast-studio` seed Agent，`SEED_VERSION` 升至 `2026-11-12`。真实 reseed 产生六个旧 Agent 备份，第二次 seed 为 0；执行前无既有 memory 可迁移，未删除旧目录、旧线程或 co-writer。
- 新会话 guide 为 5 个 Agent、20 个案例；首页仍为六卡，仅以播客卡替换末位 co-writer。co-writer 的 guide 四卡、Writer 与历史路由保留；新增 960×540 本地 WebP 封面。
- 同步旧 `WORKFLOWS` 的 `podcast-studio` 映射及既有数量注释，以满足现役 Agent 集合同步守卫。该组件在当前 `src` 无引用，不改变实际首页六卡；旧同步测试原样保留。
- 修复默认时间戳对 Python 3.11+ `datetime.UTC` 的依赖，改用 Python 3.10 可用的 `timezone.utc`；真实 Aio Python 3.10 默认 render 已验证。

## 密钥与运行时证据

- 用户批准的实际注入名集合始终精确为 `ARK_API_KEY`、`DOUBAO_TTS_DEFAULT_SPEAKER`、`QIHU_API_KEY`、`VOLCENGINE_VOICE_ACCESS_KEY`，未输出或写入任何密钥值。
- 真实 bash 验证：开启 `DEER_FLOW_SKILL_SECRET_AUTOBIND=1` → `0055a63a-cb89-447d-b198-f565239bb938` 四个名字均 PRESENT；关闭 → `5a741155-ad7b-438f-aa2a-5ef6156ff392` 均 MISSING；恢复 → `e4bc2783-e752-418e-bb6e-5b4b022dcb84` 均 PRESENT。
- Task 2 frontmatter 新增后及 Task 7 前均复核集合无漂移。frontmatter 仅声明 `VOLCENGINE_VOICE_ACCESS_KEY`，实际音色显式传入，不依赖默认 speaker 环境变量。
- 宿主探针返回 `HOST_KEY_MISSING`，未读取 `.env`；真实沙箱诊断 thread `b4cdee91-c551-42cf-834d-1d8251447630` 分别合成云舟/Vivi 成功，均为可解码 MP3/24kHz。

## 四份真实 MP3 与人工试听

四份产物均绑定 `assistant_id=podcast-studio` 的真实 thread，下载 HTTP 200；`ffprobe codec=mp3/sample_rate=24000`，FFmpeg 全解码通过，分片 hash、顺序与时长对账通过。每份 manifest 位于对应 thread 的 `user-data/outputs/render_manifest.json`，测试音频及来源正文不提交仓库。

| Profile | Thread | 解码时长 | 大小 | 分片 / 时长差 |
|---|---|---:|---:|---:|
| `read_original` | `c5a0249e-7dbd-4754-ae55-8ab45ca7639e` | 18.434s | 74,157 B | 1 / 0.002s |
| `news_single` | `dba40316-482b-47fb-bef1-6c0b66bec4e7` | 527.906s | 2,112,045 B | 14 / 0.014s |
| `interview_two` | `d6105124-b7cd-4856-ae52-4a2a6c0bb0e4` | 148.730s | 595,341 B | 11 / 0.002s |
| `debate_two` | `05e5d45c-0d82-4866-8f7e-f12ccd900d9f` | 400.274s | 1,601,517 B | 8 / 0.014s |

- 原文模式的 script/source 一致，伪造命令未执行；新闻使用真实 web + deep-research 和已读取正文，访谈为 11 轮男女交替，辩论为 8 个正反 turn。三种改写均在真实 `ask_clarification` 前保持零正式分片/TTS。
- 用户已于 2026-11-12 明确确认四份音频均已试听、质量无问题，因此 Task 7 Step 6 勾选。
- **边界**：辩论产物使用同词条 HTML 快照，不满足实时 URL 来源验收；四份音频的技术检查与试听通过不等于四模式实时来源验收全部通过。

## 验收记录

- `cd backend && PYTHONPATH=. uv run pytest tests/test_bash_secret_injection.py tests/test_secret_context.py -q` → `25 passed`。
- `cd backend && PYTHONPATH=. uv run pytest tests/test_podcast_studio_ingest.py tests/test_skill_whitelist_resolution.py tests/test_skills_bundled.py tests/test_bash_secret_injection.py -q` → `75 passed`。
- `cd backend && PYTHONPATH=. uv run pytest tests/test_podcast_studio_audio.py tests/test_skill_script_copies.py tests/test_audio_concat_no_drift.py tests/test_audio_decode_duration.py -q` → `29 passed, 2 skipped`。
- `cd backend && PYTHONPATH=. uv run pytest tests/test_seed_podcast_studio.py tests/test_seed_agents.py tests/test_skill_whitelist_resolution.py tests/test_skills_bundled.py tests/test_agent_memory_scope.py -q` → `67 passed`。
- Task 5 前端定向 node → `53 passed`；DOM → `14 passed`；真实 E2E → `30 passed`，无新增 pageerror，移动端无横向溢出。
- Task 6 backend seed/skill/ingest/audio/secret/sandbox 定向回归 → `224 passed`；frontend 首页/guide/Writer 相关回归 → `97 passed`。真实 Aio 中 PDF/DOCX/MD/HTML intake/prepare/validate 与离线确认门通过，第二次 seed 为 0。
- Python 3.10 时间戳 RED → 真实 Aio 默认 render `ImportError: cannot import name 'UTC'`；GREEN → `python=3.10.12 default_timestamp_render=PASS status=success`。宿主 `pytest tests/test_podcast_studio_audio.py tests/test_podcast_studio_ingest.py -q` → `47 passed`，Ruff 与格式检查通过。
- `cd frontend && pnpm exec vitest run --project node tests/unit/workspace/active-agents-sync.test.ts` → 修复前 `1 failed`，缺 `podcast-studio`；最小同步后 `2 passed`，未修改测试。
- `cd backend && env -u RUN_LIVE uv run pytest tests/ -q` → `9990 passed, 31 skipped, 421 warnings in 468.29s`，退出 0。
- `cd frontend && pnpm check && pnpm test` → 静态检查通过；`250 test files / 1640 tests passed`。
- `cd frontend && CI=1 E2E_PORT=3100 pnpm test:e2e && pnpm perf:check` → `80 passed (1.7m)`；Next.js 生产构建、TypeScript、五路由资产测量及预算检查退出 0。
- `make check && python3 scripts/check_docs_convention.py && git diff --check` → backend ruff 通过；frontend ESLint `0 errors`（123 条既有 warnings）、TypeScript、Prettier、knip、599 个 i18n 叶子检查通过；docs convention `OK 仓库治理约定全过`，`git diff --check` 无输出，退出 0。

## 未解决事项与用户决定

- 多个真实 URL 尝试分别被 fake-IP、HTTP 404、人机验证或动态模板/下线提示阻断，未取得可用于 Step 5 的合格实时正文。没有换 provider、补静音、伪造成功或把验证页/模板页当作文章来源。
- 用户于 2026-11-12 明确决定不再由本批执行实时 URL 验收，后续自行验收，并要求完成代码与交付收尾。Task 7 Step 5 因此保持未勾选，记录为“未验证、用户后验”，不是通过项。
- 实时 URL 安全实现仍保持 fail-closed；本批交付的是代码、离线契约、真实音色与四份技术合格且已试听的 MP3，不宣称实时网页来源能力已通过端到端验收。
- 全文只含本地提交，不 push、不建 PR、不部署，不修改 Docker、`.env*`、部署文档、数据库/迁移或锁文件。
