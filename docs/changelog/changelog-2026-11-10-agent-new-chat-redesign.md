# Agent 新会话引导页重构执行记录

> **状态**：任务 1–8 已完成；独立审查发现已处理；仅本地提交，未 push、未部署。
> **TL;DR**：四个 Agent 新会话改为任务型双语引导，16 张案例只预填不自动发送；修复 handoff、草稿、阶段标记、错误恢复和深色可读性，不改后端或 Agent 技能；全部前端与仓库门禁通过。

## 变更摘要

- `course-video`、`ppt-master`、`creative-studio`、`co-writer` 的 `chats/new` 使用页面级正常文档流，展示专属 Hero、各 4 张案例、现有 InputBox 与 Tips；已有会话和非目标 Agent 保留原布局。
- 16 张案例均由中英文 locale 提供真实 prompt；图标继续复用 `agentAppearance()`，顶栏使用用户可读短名，不再展示 raw Skill chips。
- 案例点击复用 PromptInputController：空草稿直接预填，非空草稿提供替换/追加/取消，聚焦但不自动提交；PPT 保留快速生成与改造现有文件两条旧工作流语义。
- handoff 使用显式 `handoffDoneKey === handoffKey`，未完成时案例不可用；待确认选择绑定原 key，参数变化后失效；InputBox 不因参数或临时 UUID 变化重挂/清稿。
- 目标新会话通过 `showNewThreadSuggestions=false` 关闭旧建议区；非目标 `vimax-video` 的全站 Skill 回退有真实 `/api/skills` fixture 回归。
- Co-writer 的 `[[STAGE:OUTLINE/DRAFT]]` 在 AI 消息与 Markdown/JSON 导出清理入口剥离；流式分片的不完整前缀会暂存，正文中间相同文字保留。
- Agent 加载/失败有诚实状态与手动 Retry；目标新会话错误后不因 focus、reconnect 或 remount 自动恢复，初始 retry 策略和非目标查询不变。
- 新会话草稿不持久化；已有线程切到新会话会保存旧草稿并清空当前输入，临时 UUID 变化不会清除 handoff。
- Hero、案例、状态与 Tips 补齐语义前景色和深色表面样式；深色对比度有真实浏览器断言。

## TDD 与行为验收

- 内容契约：先因 `new-chat` 模块缺失失败，实现后 9/9；覆盖四 ID、16 案例双语对齐、内部禁用名、Co-writer 标记和 PPT 旧语义。
- 引导组件：先因组件缺失失败，实现后展示 4/4；预填状态机先出现 5 条行为失败，实现后 9/9；最终审查新增 key 失效用例后共 10/10。
- 阶段清理：消息与导出先出现 4 条泄漏失败，实现后消息/导出/Writer 48/48；流式分片用例加入后 53/53。
- 建议区门控：两个关闭方向先失败，默认专属/全站回退保持通过；相邻 InputBox 回归通过。
- 页面与 E2E：页面 DOM 和四路由先失败；实现后页面/组件 19/19、行为 E2E 16/16。审查修复后新增草稿、确认框、分片、自动恢复与深色用例均完成 RED→GREEN。
- 所有自动化均 mock 后端；案例点击阶段线程创建和 run 数均为 0，只有用户显式 Enter 提交才各发生一次。

## 独立审查

新鲜上下文审查覆盖 `dc1eb3d2..fbb1cddf` 的 7 笔实现提交，发现 0 Critical、5 Important、2 Minor。五项 Important 均已通过失败测试→修复→完整套件验证，修复提交为 `24d6907f`；未进行第二次形式化复审。

Deferred minors：
- 人工消息若以 stage 字样开头，当前共享导出清理也会剥离它；需在另一次 scoped 修复中区分 human/AI 导出语义。
- 目标 Agent 已有线程的顶栏也使用本地化短名而非后端 `agent.name`；无功能影响，暂不在本批扩大变更。

## 验收记录

- `cd frontend && pnpm check` → 退出码 0；ESLint `0 errors, 123 warnings`（存量警告），TypeScript 与 Prettier 通过。
- `cd frontend && pnpm test` → `Test Files 249 passed (249)`，`Tests 1600 passed (1600)`。
- `cd frontend && pnpm check:dead` → 退出码 0，无未使用导出。
- `cd frontend && pnpm exec playwright test tests/e2e/agent-new-chat-guide.spec.ts tests/e2e/agent-chat.spec.ts tests/e2e/thread-history.spec.ts --workers=1` → `23 passed`，无 pageerror。
- `cd frontend && pnpm test:e2e` → `72 passed`，退出码 0；含全部四 Agent/16 卡、handoff、导出前清理、错误重试、390px 非重叠与深色对比度。
- `cd frontend && pnpm perf:check` → 退出码 0；仅确认五个既有路由无共享包体积回归，未修改性能预算，不声称直接测量目标 Agent 路由。
- 仓库根 `make check` → 退出码 0；backend ruff、frontend check/knip/i18n 与 docs convention 全过，`599 个叶子全活`。
- `python3 scripts/check_docs_convention.py` → `OK 仓库治理约定全过`，退出码 0。
- `git diff --check`、`git status --short --branch` → 无空白错误；实现提交均只含 allow 文件，无测试日志入库，无活跃后台任务。

## 部署与未执行项

- 未 push、未建 PR、未部署；等待用户审查。
- 未运行真实付费模型或生成链路。四个 Agent 的真实 SOUL 工作流需按计划在用户另行授权后人工冒烟；本次不把该未执行项宣称为通过。
- 无 backend、seed、SOUL、`SEED_VERSION`、API、数据库、Docker、Skills、video-templates、registry 组件或锁文件改动。
