# Agent 新会话案例展示集成

> **状态**：2026-11-17 已完成实现与本地/真实行为验收；PR 评审与 CI 合入流程进行中。
> **TL;DR**：为课程视频、PPT、播客、视觉设计四个 Agent 的新会话页落地人物 hero、scoped composer 与 20 个真实案例；关键决策 = 复用真实 InputBox、草稿/提交与工作流，案例数据统一驱动四类媒体和 58 个静态素材；影响面 = frontend 指定展示/测试/文档路径，不改 backend、skills、路由、stream、registry 或 lockfile。

## 实施内容

- 新增统一 `showcase.ts` 数据与资产清单：课程视频 3、PPT 3、播客 4、视觉 10；UI、hero、步骤和案例元数据双语，示例 prompt 保持中文。
- 保留播客四种 `[模式: …]`、PPT Quick/Default/Edit Native、视觉两种模式与课程 `auto-video-flow`/真实素材/确认边界。
- 四个 showcase Agent 使用人物 + 小标签 + 两行大标题替换旧 header；co-writer 保留原 header、4 个简单卡和旧顺序。
- 真实 `InputBox` 仅增加 scoped `max-w-5xl`、28/24px 圆角，未覆盖玻璃背景、边框、阴影或全局样式，弹层保持可溢出。
- 视频支持真实播放、关闭暂停/卸载与焦点返回；PPT 支持 7 页缩略图、翻页边界、切换重置和 PPTX 下载；视觉支持五类筛选、大图、JPG/SVG 下载；播客支持单音频互斥、进度、过期 Promise/过期媒体事件防护、播放失败/ended/error/卸载清理、文稿和来源安全链接；外部报道统一使用 HTTPS 原文。
- 草稿取消/追加/替换、显式提交、URL/sessionStorage handoff、旧别名、retry、loading/error、已有会话和 co-writer 英文 prompt 均保留相关回归。

## 素材与压缩口径

- 来源选定清单：54 个案例媒体 + 4 个 hero，共 **58 个文件、15,318,890 bytes**。
- 人物源图 alpha 全部为 255。T0 采用统一的**浅色圆角底板**：离线把边缘连通的近白背景替换为浅靛渐变并裁出透明圆角，不做运行时抠图；亮/暗色样图见 `.screenshots/agent-showcase/t0-hero-light-dark.png`。
- 最终实际媒体总量：**58 个文件、15,284,192 bytes（15.28 MB / 14.58 MiB）**，较来源减少 34,698 bytes；`README.md` 不计入媒体清单。
- 视频保留 H.264/AAC MP4 与 WebP poster；视觉使用 WebP 预览，JPG 原图/SVG 按需读取；PPTX 为 200,540 / 308,973 / 465,113 bytes；MP3 保留 64kbps。
- 第三套 PPT 基于用户提供的 360 模板做原生改造，用户已授权随项目分发；来源、生成日期、授权与压缩口径见 `frontend/public/agent-showcase/README.md`。

## 验收记录

- `make -C frontend check` → ESLint 0 errors（121 个既有 warnings）、TypeScript 通过、Prettier 通过、knip 通过、`i18n key 存活检测通过：632 个叶子全活。`
- `pnpm exec vitest run tests/unit/core/agents/showcase.test.ts tests/unit/components/workspace/agent-new-chat-guide.dom.test.tsx tests/unit/components/workspace/agent-showcase.dom.test.tsx` → `Test Files 3 passed (3)`，`Tests 30 passed (30)`。
- `E2E_PORT=3177 E2E_GATEWAY_PORT=3187 pnpm exec playwright test tests/e2e/agent-new-chat-guide.spec.ts` → `27 passed (19.3s)`。
- `E2E_PORT=3177 E2E_GATEWAY_PORT=3187 pnpm exec playwright test tests/e2e/agent-new-chat-guide.spec.ts --grep "course-video showcase"` → `1 passed`；模型弹层、技能列表按既有 `showNewThreadSuggestions=false` 不渲染、真实附件选择与预览均通过，composer 不裁切。
- `pnpm build` → 生产构建成功，用于最终行为验收。
- `E2E_PORT=3177 E2E_GATEWAY_PORT=3187 node --experimental-strip-types /Users/littlebear/Documents/SuperTrainerAgent-home-plan/.screenshots/agent-showcase/capture-verification.mjs` → `status: passed`、`errors: []`；真实 MP4 播放/关闭卸载、PPTX/JPG/SVG/MP3 下载、三视口、英文、暗色、草稿取消与显式提交全部通过。
- 真实下载核验 → PPTX 200,540 bytes、JPG 211,283 bytes、SVG 7,553 bytes、MP3 703,050 bytes。
- creative-studio 冷缓存初始媒体 → **1,452,968 bytes ≤ 1,500,000 bytes**，console error = 0，未请求视频/音频/原图/PPTX。
- `python3 scripts/check_docs_convention.py` → `OK 仓库治理约定全过（docs 命名/位置/月度归档/根目录白名单/CLAUDE 指针/测试落位/plan 状态头/backend/docs 形态/二进制禁区门禁/禁区规则镜像一致性）`。

## 首屏与证据

- 1440×900：hero bottom 270、输入框 bottom 410、第一张卡 top 506（阈值 ≤820）。
- 1024×768：hero bottom 270、输入框 bottom 410、第一张卡 top 506；第一张卡标题 bottom 已断言 ≤768。
- 390×844：hero bottom 222、输入框 bottom 358、第一张卡 top 450（阈值 ≤764）。
- 三者均无横向溢出；桌面 composer ≥900px 且内外圆角 28px，移动 24px。
- 首屏截图：`.screenshots/agent-showcase/course-video-desktop-1440x900.png`、`course-video-1024x768.png`、`course-video-390x844.png`。
- 英文：`.screenshots/agent-showcase/ppt-master-english-1440x900.png`；暗色：`course-video-dark-1440x900.png`、`course-video-dark-video-dialog.png`。
- 四类媒体、草稿、显式提交和 co-writer 截图及完整结果见源工作区 `.screenshots/agent-showcase/verification-summary.md` 与 `verification-results.json`。
