# Agent showcase 视觉贴合优化

> **TL;DR**：按只读 Demo 修正四个 Agent 新会话页的媒体布局与视觉层级；关键决策 = 保留真实 InputBox、媒体交互、草稿与工作流，仅重做 showcase 作用域内布局、人物比例和配色；影响面 = frontend showcase 组件、定向测试与文档，不改 backend、skills、路由、stream、registry、全局 InputBox 或 lockfile。

## 实施内容

- 课程视频在宽屏恢复三卡同排，保留真实播放、提示词回填与输入区行为。
- PPT 恢复横向多页案例卡：每套显示前四页、剩余页数、特性、查看与直接下载；点击任一预览页直接进入对应页，Dialog 翻页、边界和切换重置不变。
- 视觉设计恢复自然比例瀑布流，海报按 `9:16` 完整呈现，不再强制裁成 `4:5`；保留筛选、大图与 JPG/SVG 下载。
- 播客恢复 32 根音波、当前节目播放态和来源摘要；音波只属于实际播放卡片，支持减少动态效果偏好，单音频互斥与过期事件防护不变。
- 人物统一使用 `object-contain` 和既有浅色圆角底板，不再裁切下半身；移动端改为人物在上、标题在下。
- 放大左栏与主标题层级，减少重复标题、半透明卡片与无效玻璃叠加；按 Demo 局部恢复 PPT 琥珀色、播客紫色、视觉玫红色，不改全局 `agentAppearance`。
- showcase 使用项目 `--background`、`--card`、`--border` 等 token 形成更干净的阅读表面，暗色继续使用独立配色。
- 1024px 平板档压缩人物高度、区间距和顶端留白；首屏测试等待实际 CSS transition 结束后测量，阈值不放宽。

## 评审修正

- 将装饰色与文字色分离，四个 Agent 的浅色标签均达到至少 4.5:1 对比度；PPT 下载按钮默认与悬停态经 Canvas 规范化颜色验证均达标。
- 剩余页角标按实际预览数计算，无剩余页时不渲染，并设为 `pointer-events-none`，不再遮挡第 4 页点击区域。
- 视觉预览在加载前按素材原始宽高比占位，保留完整画面并避免懒加载布局跳动。
- 四个 PPT 预览索引、同 deck 关闭重开、音波切换与减少动态效果均有行为断言。
- AI 关于同 deck 不重挂载的 medium 经真实组件复现排除；关闭时 `activeDeck=null` 会卸载 Dialog，新增回归后保留原实现。

## 验收记录

- `pnpm exec vitest run tests/unit/core/agents/showcase.test.ts tests/unit/components/workspace/agent-new-chat-guide.dom.test.tsx tests/unit/components/workspace/agent-showcase.dom.test.tsx` → `Test Files 3 passed (3)`，`Tests 39 passed (39)`。
- `E2E_PORT=3177 E2E_GATEWAY_PORT=3187 pnpm exec playwright test tests/e2e/agent-new-chat-guide.spec.ts` → `33 passed (23.5s)`；覆盖四类媒体、草稿/显式提交、co-writer、暗色、英文、三视口与新增视觉行为断言。
- 新增浏览器断言确认：1440px 三个视频同排；海报实际宽高比约 `0.5625`；移动人物完整且位于标题上方；只有正在播放的卡片音波运行动画。
- `make -C frontend check` → ESLint 0 errors（121 个既有 warnings）、TypeScript 通过、Prettier 通过、knip 通过、`i18n key 存活检测通过：632 个叶子全活。`
- `E2E_PORT=3177 E2E_GATEWAY_PORT=3187 node --experimental-strip-types /Users/littlebear/Documents/SuperTrainerAgent-home-plan/.screenshots/agent-showcase/refinement/capture-verification.mjs` → `status: passed`、`errors: []`；真实 MP4 播放/关闭卸载、PPT 第 3 页直达、PPTX/JPG/SVG/MP3 下载、三视口、英文、暗色、草稿取消与显式提交全部通过。
- 真实下载核验 → PPTX 200,540 bytes、JPG 211,283 bytes、SVG 7,553 bytes、MP3 703,050 bytes；creative-studio 冷缓存初始媒体 **1,452,968 bytes ≤ 1,500,000 bytes**，console error = 0。
- `python3 scripts/check_docs_convention.py` → `OK 仓库治理约定全过`。

## 证据

- 优化后截图、行为结果和复验脚本保存在源工作区 `.screenshots/agent-showcase/refinement/`，旧版本截图保留在上一级目录供对照；完整说明见该目录 `verification-summary.md` 与 `verification-results.json`。
- 四页首屏：`course-video-desktop-1440x900.png`、`ppt-master-desktop-1440x900.png`、`creative-studio-desktop-1440x900.png`、`podcast-studio-playing.png`。
- 响应式、英文与暗色：`course-video-1024x768.png`、`course-video-390x844.png`、`ppt-master-english-1440x900.png`、`course-video-dark-1440x900.png`。
