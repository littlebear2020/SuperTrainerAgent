# 工作区体验与文字动效修复优化执行记录

> **状态**：任务 0–8 全部已执行；[PR #3](https://github.com/littlebear2020/SuperTrainerAgent/pull/3) 第三轮 7 checks 全部 SUCCESS、OCR high 清零，已于 **2026-11-12T23:09:49Z** rebase merge，mergeCommit=`ec8320dff48c401f9de06ee65b7914006463468f`。文档收口分支 `workspace-experience-docs-close` 自身 PR 随后由协调者创建。
> **TL;DR**：完成 R1–R6/R8 与集中评审修复；隔离真实 API、backend/frontend 全量、E2E/性能及 R2 容器/live 证据齐备；实现 PR #3 已合入；R7 与播客实时 URL 保持 OUT_OF_SCOPE；未访问服务器、未改价格/config；仅任务 2 构建本地 sandbox，未构建发布 frontend/gateway、未重建 remotion-worker、未推镜像，任务 8 未新增 Docker 构建；剩余仅文档分支自身 PR 的 checks/merge。

## 变更摘要

- R1：对话结束后的动态追问已移除，静态新会话建议、Agent prompts、技能/案例预填保留；普通/Agent/案例 Playwright 12/12。
- R3/R4：我的空间与作品/资产 URL 导航、真实用户底部、角色设置和 console 角色门控完成；video-assets Playwright 11/11，后端 console router 69/69。集中 Important 修复进一步隔离所有 console 查询 scope，admin→user 不再复用旧全量数据，定价 tab 仍回 analysis。
- R5：首页六能力与我的作品六类筛选统一目录；四类 Agent 完整历史、offset 50 后目标、旧别名、标题搜索、canonical URL、键盘/ARIA、错误重试齐备；作品 Playwright 13/13。
- R6：项目详情卸载品牌包、色彩微调与关联视频 UI，保留角色/锚图/道具、palette 回灌与保存、关联计数和 remotion_course 深链；hermetic Playwright 15/15。标题保存合并原始 palette，品牌与扩展键不被覆写。
- R8：首页六卡等高网格、三张 960×540 poster、静态图 contain、单卡按需预览、媒体失败降级齐备；首页/landing Playwright 12/12。集中 Important 修复补齐 coarse/reduced 下全部装饰动画卸载及 `play()` 拒绝处理，真实 hasTouch/computed animation 与播放拒绝验收通过。
- R2：auto TTS 只声明 access key，显式 speaker 前置与 NUL+C 模板指纹完成；探针隔离 helper 已限制为单尝试，全局 `_doubao_tts.py` 未改。

## 隔离真实 API 验收

`backend/tests/test_workspace_experience_isolation.py` 在 spawn 子进程和临时目录中使用临时 SQLite、真实 checkpointer、`DEER_FLOW_HOME`、`video_gen.storage_dir`、临时最小配置及测试 JWT，装配真实 AuthMiddleware、CSRFMiddleware 与 threads/console/video 路由。

- 经 API 创建 51 条本人 course-video threads，第一页 50 条，offset=50 第二页仅最旧 `offset-target`；ThreadResponse 无 context，标题由 API state 写入。
- course-ppt、五个 creative 旧别名及 podcast metadata 均由真实 API 返回，供 canonical 分类；本人 thread 200，他人 thread 404。
- 五个 console 接口本人 user_id 均 200，伪造他人 user_id 均 403，默认 runs 只含本人记录。
- 非默认 `warm` 品牌、LOGO、intro/outro 与含 `grade`、`old`、`old_extension` 的 palette 经与前端 title-save 等价 PUT 后逐字段不变，项目 JSON 仅存在隔离目录。
- `ai_image`、`ai_visual`、`ai_video`、`remotion_course` 均由真实 API 读取；remotion_course 的 `course_project_id` 关联正确。
- **视频任务与 console runs 仅为隔离 DB fixture + 真实 API 读取，未调用 create 或任何付费管线；threads/projects 则经真实 API 播种。**

结构化证据：`temp/workspace-experience-2026-11-12/task8-isolated-api-summary.json`。

## R2 容器与 live 证据

- linux/amd64 镜像 `sha256:d3b38fb096cceda7b7985b41183562eaa5942f894bbb1f80a9509724e5f0ea80`；C/C.UTF-8/en_US.UTF-8 指纹均为 `18cc3712bf06ffc6041cfa94f4704722`， compositions 为 MyComp + DataDrivenVideo，静帧 1920×1080 已读回。
- 一字节负向变异产生不同 hash 且 `rc=1`。**负向脚本 expected/actual 标签与正式语义相反，但 hash 不等 + rc=1 仍是有效变异证据**；历史 temp 日志未修改。
- 历史公开 TTS probe 为一次成功公开探针调用、无人工重试：`PROBE_OK duration=1.632 bytes=13101`；MP3/24000Hz 与 ffprobe/完整 decode 通过。**该记录不用于声称历史 HTTP 总次数已证明。**
- 最小本地 Remotion MP4 为 H.264 1920×1080 + AAC 48000Hz、2.048s，完整 decode 通过；未增加付费视频调用，未重建 remotion-worker，worker/course-standard 源码 diff 为空。

## 验收记录

- `env -u RUN_LIVE uv run pytest tests/test_workspace_experience_isolation.py -q` → **1 passed**；真实 API 摘要如上，ruff check/format 通过。终审补测 dotenv 禁用为 RED→GREEN，最终同命令再次 **1 passed**，非空日志 `temp/workspace-experience-2026-11-12/task8-dotenv-disabled-red.txt`、`task8-dotenv-disabled-green.txt`。
- `cd backend && env -u RUN_LIVE uv run pytest tests/ -q` → **10036 passed, 31 skipped, 421 warnings in 560.75s**，退出码 0；非空日志 `temp/workspace-experience-2026-11-12/pr3-backend-full.txt`。
- `cd frontend && pnpm check && pnpm test` → 静态门禁退出码 0（121 个既有 warnings、0 errors），**263 files / 1698 tests passed**；非空日志 `temp/workspace-experience-2026-11-12/pr3-second-review-frontend-check-and-unit.txt`。
- `CI=1 E2E_PORT=3100 pnpm test:e2e && pnpm perf:check` → **90 passed (2.1m)**，五路由性能输出齐全且均在原预算内，`performance-budgets.json` 未修改；非空日志 `temp/workspace-experience-2026-11-12/pr3-second-review-e2e-perf.txt`。
- 最终性能实测：`/` JS 745503 B/CSS 241569 B；chats JS 3258898 B/CSS 241509 B；thread JS 3564581 B/CSS 241509 B；video-gen JS 3555858 B/CSS 241509 B；writer JS 3760420 B/CSS 241509 B，均低于既有预算。
- 侧栏旧断言修复：`CI=1 E2E_PORT=3100 pnpm exec playwright test tests/e2e/sidebar.spec.ts --workers=1` → **1 passed**；随后完整 E2E 重新执行为上述 90/90，而非沿用失败批次。
- StrictMode 定向证据：`pr3-strict-mode-red.txt` 记录修复前最终 `paused=true`；`pr3-strict-mode-green.txt` 记录修复后 **5 files / 20 tests passed**。直接 `CourseMedia` 测试证明 play=2/pause=1 且最终 `paused=false`，普通 rerender 不额外 play，blur 暂停、focus 恢复；两份日志均位于 `temp/workspace-experience-2026-11-12/`。
- 第三轮前窄修复定向：RED **5 failed / 24 passed** → GREEN **4 files / 29 tests passed**，覆盖 Suspense fallback、快速 queue/focus 交错、失败重试不循环、foo/utm/非法值与无循环；日志 `pr3-second-review-red.txt`、`pr3-second-review-green.txt`。
- `{ make check && python3 scripts/check_docs_convention.py && git diff --check && echo ROOT_CHECK_OK; }` → 退出码 0；backend ruff 通过，frontend 0 errors/121 个既有 warnings，knip/i18n 601 叶子/Prettier/docs convention/diff check 全过；非空日志共 214 行且末行 `ROOT_CHECK_OK`：`temp/workspace-experience-2026-11-12/pr3-second-review-root-check.txt`。
- 文档合入收口根门禁使用同一命令链；非空日志末行 `ROOT_CHECK_OK`：`temp/workspace-experience-2026-11-12/pr3-merged-docs-root-check.txt`。
- `GH_REPO=littlebear2020/SuperTrainerAgent gh pr checks 3` → **7/7 SUCCESS**，对应第三轮最终原始 HEAD `a9b57565`。
- `GH_REPO=littlebear2020/SuperTrainerAgent gh pr merge 3 --rebase` → **MERGED**，mergeCommit=`ec8320dff48c401f9de06ee65b7914006463468f`。

## 远端 PR 与评审状态

- [PR #3](https://github.com/littlebear2020/SuperTrainerAgent/pull/3) 已创建、push 并于 **2026-11-12T23:09:49Z** rebase merge；最终原始 HEAD 为 `a9b57565`，mergeCommit=`ec8320dff48c401f9de06ee65b7914006463468f`。
- 首轮 backend `43311243812` 因 5 个 pdftotext 缺失失败，其余 6 项 pass；AI review `43311243667` pass 并提出 27 项。
- 已修复：high `4556653345` → `92b0df21` + `1f30a186`；poster / SSR / null user → `92b0df21`；Poppler → `d413d9f9`。StrictMode scoped Important 由 `1f30a186` 修复，并有上述直接组件回归证据。
- 第二轮 7 checks 全部 SUCCESS；OCR `43320325183` 提出的 high/medium 已由 `a9b57565` 窄修复或记录计划内驳回口径。
- 第三轮 backend `43325381385`、frontend `43325381362`、lint `43325381320`、quick `43325381322`、E2E+perf `43325381377`、OCR `43325381475` 全部 **SUCCESS**，mergeState=`CLEAN`。
- 第三轮 OCR 无 high；一个 MQL 性能 medium 和 low 建议已记录为 **non-blocking 后续优化**。16+2 条线程回复及 summary disposition 均已发。

## Skip 与未执行项

- 此前 `env -u RUN_LIVE uv run pytest tests/ -q -rs` 验收记录为 **10035 passed, 31 skipped, 421 warnings in 530.35s**，退出码 0；逐项原因日志 `temp/workspace-experience-2026-11-12/task8-backend-full-with-skips.txt`。最新主记录为上述 **10036 passed / 31 skipped / 421 warnings in 560.75s**。
- 31 个既有条件 skip 清单：`test_client_live.py`（1）、`test_model_roster_live.py`（1）、`test_audio_decode_duration.py` 无 gapless 特征（2）、`test_client_e2e.py` live LLM opt-in（11）、`test_create_deerflow_agent_live.py` live LLM opt-in（3）、`test_e2e_vimax_render.py` 付费渲染 opt-in（1）、`test_orchestration_tts.py` needs network（1）、`test_user_scoped_skill_storage.py` 上游移植边界（11）。无新增意外 skip；remote 套件不在此命令范围内。
- `frontend/tests/e2e-remote/` 未运行；实现 PR #3 的任务 8 已全部勾选并合入。仅文档收口分支 `workspace-experience-docs-close` 自身 PR 的 checks/merge 待协调者处理。
- R7 定价、播客实时安全 URL 与次日人工验收为 OUT_OF_SCOPE；无新默认价、迁移、素材行或 doubao_tts 价格选择。
- 未访问测试/公司服务器、用户 DB/密钥，未修改价格/config，未启动子代理。终审发现初版存在自动 dotenv 向上发现仓库真实 `.env` 的风险；最终验收在 spawn 子进程任何 `app.*`/`deerflow.*` 导入前显式设置 `PYTHON_DOTENV_DISABLED=1` 并重跑通过，结构化摘要记录 `dotenv_disabled=true`，未输出任何环境变量值。任务 1 一次授权最短 TTS probe、无人工重试，任务 8 未新增付费调用；仅任务 2 构建本地 sandbox，未构建发布 frontend/gateway、未重建 remotion-worker、未推镜像，任务 8 未新增 Docker 构建。

## Commits 与证据

- 实现：`1e5face7`、`0ee573a8`、`ce029260`、`d928cba2`、`72d61eb5`、`f63f0fe7`、`32d38103`、`24642b96`、`00a0b335`、`5099fcbb`。
- Important 修复：`38db9d7c`、`69c4b1fc`。
- 隔离验收：`ee07c209`；全量侧栏断言修正：`3d977862`。
- 远端评审修复：`d413d9f9`（Poppler）、`92b0df21`（首页媒体与 SSR）、`1f30a186`（StrictMode 播放 cleanup）、`a9b57565`（Suspense、预览闭包与 URL 规范化）。
- 文档证据同步：`67f9c5a4`；PR #3 rebase merge tip：`ec8320dff48c401f9de06ee65b7914006463468f`（原始实现 SHA 保留如上，不逐个改写 rebase SHA）。
- 证据根：`temp/workspace-experience-2026-11-12/`；计划：`.superpowers/sdd/2026-11-12-workspace-experience-and-pricing-optimization-plan/progress.md`。
- 首页截图：`task5-desktop-zh.png`、`task5-mobile-zh.png`、`task5-desktop-en.png`，仅证明功能/布局状态，不替代次日审美验收。

## 次日人工与协调者事项

1. 按普通账号与管理员复核角色、设置、console scope 与本人用量；检查六类作品真实数据、搜索、打开与视频操作。
2. 检查首页中英/移动布局、静态图留白、coarse/reduced 装饰动画、播放失败与点击创建；本批截图不冒充人工审美结论。
3. 以固定逐字稿完成最短 course-video，复核 TTS 音色、模板提示与 MP4 声画；补做播客实时安全 URL 和 R7 定价口径。
4. 实现 PR #3 已合入；文档收口分支自身 PR 随后由协调者创建，创建后仅剩该文档 PR 的 checks/merge，不新增实现任务复选框。
