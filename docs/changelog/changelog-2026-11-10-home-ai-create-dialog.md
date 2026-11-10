# 首页 AI 专用创建框优化执行记录

> **状态**：已完成并本地提交验收；未 push、未部署。
> **TL;DR**：首页“图片动效 / AI 视频”创建框按入口固定任务类型，移除重复类型选择与话题生成，统一为固定脚本和分镜编辑；通用创建框与既有提交 payload 保持兼容，全部前端门禁通过。

## 变更摘要

- `CreateTaskDialog` 仅在显式传入 `initialTaskType` 时进入首页专用展示：`ai_image` 显示“创建图片动效”，`ai_video` 显示“创建 AI 动态视频”。
- 专用表单不显示视频类型、生成方式或话题输入，默认固定 `mode: "fixed"`；主按钮改为“创建并编辑分镜”，脚本与标题话术按用户任务调整。
- 专用弹窗延续首页白底、淡紫边框、柔和渐变与圆角风格；课程项目、语言、音色、BGM、高级设置和既有提交链路保留。
- 未传 `initialTaskType` 的通用弹窗继续提供类型切换与话题生成；关闭后条件卸载，交替打开无标题、脚本或高级设置残留。
- 只更新过时 E2E 选择器与固定类型断言，不恢复或隐藏旧 DOM；422/503、分镜跳转、语言、音色与 BGM 行为断言保持有效。
- 关联设计：`docs/plans/2026-11-09-home-entry-and-video-assets-redesign-plan.md`；本次为用户确认后的创建框跟进优化，不改后端、API、数据库、Docker、Skills、video-templates 或锁文件。

## 失败分析与处理

- 原整轮结果为 44 通过、11 失败。其中 7 条是用例仍要求专用表单存在已按需求移除的“视频类型”，并沿用旧脚本文案或下拉序号；更新定位后相关套件 14/14 通过。
- 首页导航/播放、landing CTA、writer autosave 共 4 条在原高负载整轮中失败；不改代码、单线程复现后 8/8 通过。最终仍按标准并发配置跑完整 E2E，55/55 通过，未以缩小套件代替门禁。

## 验收记录

- `cd frontend && pnpm exec vitest run --project dom …video-gen-create-dialog-initial-type.dom.test.tsx` → 专用类型、通用兼容与 payload 4/4 通过。
- `cd frontend && pnpm exec playwright test …home-course-entry.spec.ts …landing.spec.ts …writer-editor-flow.spec.ts --workers=1` → `8 passed`，原非创建框失败未稳定复现。
- `cd frontend && pnpm exec playwright test …video-gen-create-dialog.spec.ts …video-gen-create-flow.spec.ts …video-gen-error-states.spec.ts …video-gen.spec.ts --workers=1` → `14 passed`。
- `cd frontend && pnpm check` → 退出码 0；ESLint `0 errors, 123 warnings`（存量警告），TypeScript 与 Prettier 通过。
- `cd frontend && pnpm test` → `Test Files 245 passed (245)`，`Tests 1559 passed (1559)`。
- `cd frontend && pnpm test:e2e` → `55 passed`，退出码 0。
- `cd frontend && pnpm perf:check` → 退出码 0；五个既有路由均通过预算，未修改预算表。
- 仓库根 `make check` → 退出码 0；backend ruff、frontend check/knip/i18n 与 docs convention 全过，`588 个叶子全活`。
- `python3 scripts/check_docs_convention.py` → `OK 仓库治理约定全过`，退出码 0。

## 部署与遗留

- 仅本地提交，不 push、不建 PR、不部署，等待用户审查。
- 无真实付费生成或服务器配置修改；E2E 全部使用 mock API。
- 下一步为已批准的 `docs/plans/2026-11-10-agent-new-chat-redesign-plan.md` 任务 1–8，两批次不混提交。
