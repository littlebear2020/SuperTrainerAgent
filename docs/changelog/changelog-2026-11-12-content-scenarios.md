# 内容创作场景优化

> **状态**：已执行（2026-11-12；三组 12 张中英文卡片、真实路由与完整前端门禁完成；只做本地 commits，不 push、不建 PR、不部署）。
> **TL;DR**：Creative/Course/PPT 三组 12 卡改为真实输入、技能工作流与交付物；PPT 第四卡仅由 `brand-template` 改为 Default `document-to-course`；co-writer 四卡和首页六卡未改，真实技能读取与完整前端门禁通过。

## 变更摘要

- Creative Studio：毛玻璃知识卡（SVG + MP4）、科技风竖版课程海报（默认 1080×1920）、单张中文信息图、默认建议 4 页且可调整的连续漫画（角色表/分镜确认后生成页面与 PDF）。
- Course Video：主题调研科普、用户真实截图讲解、用户数据动态图表、完整讲稿逐字保真转视频；不包含录屏、截图标注或自动溯源承诺。
- PPT Master：保留 Quick、Edit Native、Beautify 三卡边界；`brand-template` 改为 `document-to-course`，PDF/DOCX/Markdown 仅走 Default，经结构与方案确认后生成新 PPTX。
- 只修改 `frontend/src/core/i18n/locales/{zh-CN,en-US}.ts`、`new-chat.test.ts` 和 DOM 测试三处随文案变化的字面量。`types.ts` 与 E2E spec 无需变更；首页六卡、Writer、后端、技能、配置和 co-writer 四卡均未改。
- 实现提交：`2d14418a feat(agent): 按真实场景优化创作案例`。

## 真实技能路由证据

- Creative `content-scenarios-creative-studio-5a68c2cee8`：真实 `read_file` 返回 `trainerai-comic` 与自动选择表，`ask_clarification` 提供默认 4 页、可调页数及画风/角色/分镜确认项；未生成图片、PDF 或调用 TTS。
- Course `content-scenarios-course-video-401082dd82`：真实读取 `auto-video-flow`，明确要求上传真实截图并区分固定稿/自由创作确认门；未创建文件、调用 TTS 或渲染。
- Course `content-scenarios-course-video-065477784e`：真实读取 `chart-visualization`，明确要求用户数据且不补造数值；未创建文件、调用 TTS 或渲染。
- Course `content-scenarios-course-video-f35089707d`：真实读取 `auto-video-flow`，逐字复述两句固定稿并明确不改写、不扩写，只机械推导分镜；未创建文件、调用 TTS 或渲染。
- PPT 真实 Gateway thread `b16324e6-4664-44f7-a0d2-c97d90d46a63`：真实上传 Markdown，工具步骤依次读取 `ppt-master/SKILL.md`、运行 `attribution_guard.py`、读取 `workflows/routing.md` 与 `workflows/generate-pptx.md`，随后停在 Default Stage 1 `ask_clarification`；未读取 Quick/Beautify/create-template，未生成 PPTX。

### 原卡片提示词补证（独立审查后）

- Creative `content-scenarios-creative-studio-adbea24b60`：探针保存 card ID、locale 与原始提示词（仅填主题），实际读取 `trainerai-comic/SKILL.md`、自动选择表与完整工作流；确认门行为由上述 `5a68c2cee8` 证据交叉验证。
- Course `content-scenarios-course-video-90fd72fe1c`：原截图卡仅填产品/时长，Agent 检查空素材目录后调用 `ask_clarification` 索取真实截图，未虚构 UI、未调用 TTS/渲染。
- Course `content-scenarios-course-video-e809dd8642`：原数据卡仅填真实 CSV 文件名，实际上传并读取数据及 `auto-video-flow`，在讲稿/分镜设定确认前停止，未调用图表生成、TTS 或渲染。
- Course `content-scenarios-course-video-5690f202e8`：原固定稿卡仅填完整讲稿，Agent 在任何写入/TTS 前进入确认；上述 `f35089707d` 证据同时验证实际技能读取和逐字不改写。
- PPT 真实 Gateway thread `23355d7e-43c5-4ce2-80d6-d2bc85126261`：人类消息与 locale 原文逐字一致、未追加路由或停止暗示；实际工具步骤读取 `ppt-master/SKILL.md`、`routing.md`、`generate-pptx.md` 并运行完整性校验，Agent 自行到达 Default Stage 1 确认门。未读取 Quick/Beautify/create-template，未确认、未生成 PPTX。

## 验收记录

- 基线 `pnpm exec vitest run --project node tests/unit/core/agents/new-chat.test.ts` → `9 passed`；DOM 基线 → `10 passed`；E2E 基线 → `17 passed`。
- RED：`new-chat.test.ts` → `28 failed / 7 passed`，失败均来自旧场景文案与新 `document-to-course`；co-writer 中英文硬编码快照均通过。
- RED：DOM → `1 failed / 9 passed`，首个失败为英雄标题字面量，其余结构/交互用例通过；E2E → `17 passed`。
- GREEN：`new-chat.test.ts` → `35 passed`；DOM → `10 passed`；`CI=1 E2E_PORT=3100 pnpm exec playwright test tests/e2e/agent-new-chat-guide.spec.ts` → `17 passed`。
- 独立审查输入护栏变异验证：只删除产品卡 prompt 的“真实截图”、保留 description 后，新 prompt 专用断言 `1 failed / 34 passed`；恢复后 `35 passed`。修复提交 `d82df492 test(agent): 加固场景卡输入断言`。
- `cd frontend && pnpm check` → 首次与 E2E 并发时因 Playwright 清理共享 `test-results` 触发 ESLint ENOENT；串行复跑后 ESLint `0 errors`（既有 123 warnings）、TypeScript 与 Prettier 通过。
- `cd frontend && pnpm check && pnpm test` → 初批及审查修复后均为静态门禁通过、`249 test files / 1630 tests passed`。
- `cd frontend && CI=1 E2E_PORT=3100 pnpm test:e2e && pnpm perf:check` → `77 passed`；生产构建成功，五路由资产测量与预算检查退出 0，无新增 JavaScript pageerror。
- `make check && python3 scripts/check_docs_convention.py && git diff --check` → backend ruff 全过、frontend 静态/knip/i18n 全过（599 叶子）、docs convention `OK 仓库治理约定全过`，`git diff --check` 无输出；最终补证文档完成后再次复跑。
- `git diff -- frontend/tests/unit/components/workspace/agent-new-chat-guide.dom.test.tsx` → 仅英雄标题、首卡标题、tips 三处字面量变化；结构、草稿、disabled、handoff、locale 与提交断言未改。

## 验证限制与未解决事项

- 嵌入式 Creative 探针在确认门已出现后触发客户端递归上限；实际技能读取和确认门证据已保存。PPT 嵌入式探针同样在后续读取前终止，因此改用真实 Gateway 页面完成 Default 验收；未把这些探针异常记作产品路由成功。
- 本批按计划在确认门停止，没有生成最终漫画、课程视频或 PPTX，也没有调用 TTS；媒体质量不属于本批验收范围。
- Deferred minor：PPT 文档卡禁止 Quick 的反例正则暂未覆盖小写 `quick/quickly/fast`；当前文案不含这些触发语，Default 与输入已有 prompt 专用正向断言，后续可加强反例。
- 本地测试线程、上传资料与测试账号仅用于验收，收尾清理本批不再需要的资源；两个根目录源视频未修改、未提交。
