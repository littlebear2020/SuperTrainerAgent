# Agent Showcase 预览反馈精修 changelog（2026-11-17）

> **TL;DR**：按用户本地预览反馈收尾：Hero 人物按 `/workspace` 首页比例缩小（188/178/160px）、composer 改为 demo 的 `max-width: 860px` 居中、移除 guide 底部「开始之前」tips 与案例区「产物已核验」badge/案例 note；关键决策 = 用户预览反馈覆盖原计划 §3 的 Hero 230–250px 与输入框 100% 规格，Hero/案例区仍共享 1180 容器、composer 独立按 demo 尺寸居中；影响面 = 仅 showcase/guide 前端组件与对应测试、文档，不动数据/媒体/后端。

> **状态**：已执行（follow-up PR #17；主计划 `docs/plans/2026-11-17-agent-showcase-visual-fidelity-plan.md`）。

## 一、触发

用户 2026-11-17 本地预览反馈三项：

1. 截图中出现「推理深度」下拉，疑问 360 通道模型是否支持。
2. Hero 人物偏大，希望参考 `/workspace` 首页比例缩小；composer 太“长条”，希望参考 demo 设计尺寸。
3. 去掉 guide 底部「开始之前」tips、案例区「产物已核验」badge 与案例 note。

## 二、排查结论与改动

### 1. 推理深度（非产品代码问题，仅修预览 mock）

- 本地预览用的是 mock API；mock 模型条目里被写入了 `supports_reasoning_effort: true`，于是 `InputBox` 按真实逻辑渲染了「推理深度」菜单。
- 真实 `config.yaml` 中 `supports_reasoning_effort` 出现 **0 次**（`config.example.yaml` 仅在注释里示例 `false`），360 通道模型不会显示该菜单。
- 处理：仅从本地预览 mock 移除该字段并重启预览；仓库代码无改动。若未来某模型 config 显式声明 `supports_reasoning_effort: true`，UI 仍会按设计显示。

### 2. Hero 人物缩小（参考首页比例）

- 首页角色：`h-60`（240px）+ `scale-110` ≈ 264px，标题 `sm:text-5xl`（48px），角色/标题 ≈ 5.5。
- Showcase 标题 33px（桌面）/25px（移动），原人物 242/170px，比例约 7.3/6.8，明显偏大。
- 改为：`h-[188px]`（≥1280）、`sm:h-[178px]`、`h-[160px]`（移动），比例约 5.7/6.4，同时给首屏让出约 54px。

### 3. Composer 按 demo 尺寸

- demo `.composer { max-width: 860px; margin: 0 auto; }`（hero 1000 / 案例区 1280 三档）。
- `agent-guide-input` 由 `w-full`（1180 容器满宽）改为 `mx-auto w-full max-w-[860px]`；圆角 28/24px、高度与 glass 逻辑不变。
- Hero 与 Showcase 仍共享 1180 容器（左右边缘 delta=0）；composer 按 demo 在 shell 内居中（实测中心偏差 0）。

### 4. 去掉 tips / badge / note

- 删除 `AgentNewChatGuide` 的 `data-testid="agent-guide-tips"` 整块（「开始之前」+ tips），并同步删除随之失效的 `agents.newChatGuide.tipsTitle` i18n key（types/zh/en）；guide 的数据字段 `tips` 保留但不再渲染。
- 删除 `AgentShowcase` 案例头部的「产物已核验 / 原生可编辑 / MP3 可试听」badge。
- 删除案例区底部 `agent.note` 段落；`CheckCircle2` 随之移除。
- 数据层的 `badge` / `note` 字段保留（无渲染引用），避免无意义的数据迁移。

## 三、测试调整

- 单测：`agent-new-chat-guide.dom.test.tsx` 不再断言 tips，改为断言 `agent-guide-tips` 不存在；保留 cases→composer 文档顺序。
- E2E（`agent-new-chat-guide.spec.ts`）：
  - 对齐断言改为 Hero/Showcase 两根节点共享 1180 shell；composer 单独断言 840–880px 且中心与 shell 中心偏差 ≤4px；
  - 原 1440 输入宽 ≥900 的断言改为 840–880（demo 860）；
  - 新增「不渲染 tips / badge / 案例 note」回归测试；
  - 三视口首屏 `top` / `h3 bottom` 门禁不变。

## 四、实测（dev server，mock API）

| 视口 | Hero 人物高 | Composer 宽 | Composer 中心偏差 | 首卡 top | 首卡 h3 bottom |
|---|---:|---:|---:|---:|---:|
| 1440×900 | 188 | 860 | 0 | 478 | 700.6 |
| 1024×768 | 178 | 860 | 0 | 460 | 716.1 |
| 390×844 | 160 | 357 | 0 | 546.5 | 823.7 |

三视口均无 tips / badge / note；无横向滚动。

## 验收记录

- `cd frontend && pnpm check` → `✖ ... (0 errors, ... warnings)`；`Checking formatting... All matched files use Prettier code style!`
- `cd frontend && pnpm check:dead` + `node scripts/check_i18n_keys.mjs` → knip 无死导出；`i18n key 存活检测通过：631 个叶子全活`
- `cd frontend && pnpm test` → `Test Files 266 passed (266)` / `Tests 1753 passed (1753)`
- `cd frontend && pnpm test:e2e -- tests/e2e/agent-new-chat-guide.spec.ts` → `38 passed`
- `python3 scripts/check_docs_convention.py` → `OK 仓库治理约定全过（...）`
- 本地预览复核 → `tips=false badge=false`，composer `860px` 居中，无 pageerror/4xx

## 五、取舍与后续

- 本批覆盖主计划 §3 的 Hero 230–250px 与 composer 100% 规格；主计划状态头已追加本批说明。
- 若后续希望改成「Hero 与 composer 同宽」或恢复 1180 满宽 composer，需要同时调整 E2E 对齐断言与首屏基线，不要只改 CSS。
