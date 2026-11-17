# Agent Showcase Hero / Composer 设计优化 changelog（2026-11-17）

> **TL;DR**：按第三/四轮预览反馈重做顶部构图与卡片质感：Hero 收窄到与 composer 相同的 860px 中心列（原来 hero 1132 / composer 860 导致人物、标题、输入框三条轴线错位），人物改为 240/200/190px 并在亮色下做底部柔化、加顶部局部 ambient wash；composer 加高到 140px（移动 132）并改为更清晰的玻璃卡片（边框/阴影/内高）。同时去掉顶部 48px 阴影带、人物底部虚化，把四类案例卡统一成与 composer 一致的玻璃卡质感，并修复「用此提示词」按钮换行与课程视频第 2 步文案逻辑；处理 #18 review 的两条建议（筛选不变量强制、accent 对齐 indigo-500）。

> **状态**：已执行（follow-up PR #19；前置 #15/#16/#17/#18）。

## 一、问题诊断

| 反馈 | 实际原因 |
|---|---|
| 人物“依然是歪的” | Hero 仍是 1132 宽（整块左对齐），composer 860 居中：人物 x≈281、标题 x≈497、输入框 x≈417，三条轴线互不重合；视觉上人物像被甩在左边 |
| 画面没有设计感 | 人物是硬裁切（膝盖处直角）、顶部只有透明人物+文字；composer 是半透明玻璃条，边界几乎融进背景；整个顶部没有视觉锚点 |
| 输入框上下偏窄 | 项目 InputBox 在 guide 里是 116px 高；demo composer 124，且 demo 是白底卡片+阴影，视觉上更“厚” |

## 二、改动

### Hero（与 composer 同宽）

- `ShowcaseHero` 外层改为 `mx-auto max-w-[860px]`，与 `agent-guide-input` 的 860 列完全同宽同轴；showcase 仍是 1180 宽。
- 人物列：`175/205/230px` 宽，人物高 `190/200/240px`（移动/平板/桌面），`object-contain object-center`；顶部与标题在 860 列内左对齐。
- 标题：26/31/34px（移动/平板/桌面），行高 1.34。
- 人物底部在亮色下用 96.5%→100% 的 mask 柔化硬裁切；暗色关闭 mask（避免半透明腿部在深色上显“鬼影”）。
- 新增 guide 局部 `.guideAmbient`：顶部 460px 的白色（亮）/indigo（暗）径向 wash，无硬边、不改变项目 `FloatingBlobsBackground`。
- 移除上一轮加的 `heroShadow`（在裁切处形成暗色涂抹，反而更“脏”）。

### Composer（demo 尺寸 + 更厚）

- `guide-composer.module.css`：桌面 `min-height: 140px`、移动 `132px`；textarea 内高 88/78px。
- 表面从“透明玻璃条”改为更清晰的玻璃卡片：浅色 `rgb(255 255 255 / .82→.60)` 渐变 + `slate-200` 1px 边框 + 双层阴影；暗色 `rgb(20 26 45 / .72→.58)` + 白色 12% 边框。
- 圆角保持 28/24px；`overflow` 仍不裁切（下拉/附件不受影响）。

### 节奏

- guide 的 `sm:gap-5` 收为 `sm:gap-4`（hero→composer、composer→showcase 均 16px），配合 hero 高度得到首屏：
  - 1440：首卡 top 554（≤581）、h3 bottom 777（≤900）
  - 1024：首卡 top 506（≤545）、h3 bottom 762（≤768）
  - 390：首卡 top 562（≤618）、h3 bottom 839（≤844）

### #18 review 修复

- 筛选行/筛选 grid 的不变量改为代码强制：`isVisualOnly = cases.length > 0 && every(kind === "visual")`，混合 kind 的 agent 不再渲染筛选行，也不会落入多栏单列堆积路径。
- `--showcase-accent` 对齐 Tailwind indigo-500（`#6366f1`），`--showcase-accent-text` 保留更深的 `#3f4fd1` 作对比度安全色。

## 三、追加：第四轮预览反馈

1. **页面最上方阴影带**：不是 box-shadow，而是 `.guideAmbient` 只从 guide section 顶部开始，scroll container 的 `pt-12`（48px）区域仍是页面原背景，形成一条明暗分界。将 `.guideAmbient` 改为 `inset: -48px 0 auto; height: 508px`，让 wash 覆盖顶部 48px；采样 y=0/24/48 均与下方同色。
2. **人物底部虚化**：删除 hero 图片的 `mask-image`；恢复硬裁切（亮/暗一致），不再有半透明腿部。
3. **案例卡质感对齐 composer**：`.showcase-card` 统一为玻璃卡：浅色白色渐变 + `slate-200/85` 边框 + 双层投影 + `backdrop-filter: blur(16px)`；暗色深色渐变 + 白 10% 边框；hover 加强边框/阴影；播客播放中用 `data-playing` 加 indigo 高亮。同步移除四类卡上的 `bg-card/70`/`border-border/*`/`hover:shadow-*` 等分散 utility，避免多来源覆盖。
4. E2E 的卡片表面断言从 `background-color` 改为 `background-image` 含 gradient（卡片已是渐变表面）。

## 三、测试调整

- E2E 对齐断言：从“Hero/Showcase 左右边缘一致”改为“Hero 与 composer 同宽同轴（≤1px）+ 两者中心与 showcase 中心一致（≤4px）”。
- 人物几何断言：1440 下人物列宽 230、高 240，四 Agent 一致。
- 首屏、placeholder、alpha、字号、筛选计数/分列等既有门禁保留。
- 单测无需改（无文案/结构断言变化）。

## 五、追加：第五轮预览反馈

1. **「用此提示词」按钮串行**：`PromptButton` 补 `shrink-0` + `whitespace-nowrap`，不再被左侧元信息挤压换行；PPT「打开案例 / 下载 PPTX」与播客「查看文稿」按钮同步加 `whitespace-nowrap`。实测 1440/1024/390 按钮均 36px 高、单行。
2. **课程视频第 2 步文案逻辑**：原文案 “agent 给出口播稿和逐场景设计；上传讲稿按原文使用，不改写、不扩写。” 把“agent 出稿”和“上传讲稿”并置，逻辑冲突。改为分支表述：
   - zh：若你提供讲稿，按原文使用、不改写不扩写；若只给主题，agent 先写出口播稿与逐场景设计，交你核对。
   - en：Supplied scripts stay verbatim; topic-only briefs get a drafted narration and scene plan for your review.
3. **回归**：`showcase.test.ts` 新增第 2 步双分支断言；E2E 新增按钮 `white-space: nowrap` 与高度 ≤40 断言。

## 六、追加：第六轮预览反馈（人物站在对话框上沿）

- 参考其他产品截图，人物底部需要**刚好落在对话框上沿**，而不是悬在上方。
- `agent-guide-input` 在 `sm` 以上使用 `-mt-[18px]`（抵消 16px shell gap + 2px 覆盖）并加 `relative z-20`，让 composer 盖住人物裁切边；移动端保持原有间距（人物在标题上方）。
- 顺带修复遗留的 `lg:gap-4 xl:gap-5`：此前 1440 的 shell gap 实际是 20px 而非 16px，导致负 margin 后仍有 2px 间隙；统一为 `gap-4` 后 1440/1060 均为 2px 覆盖。
- E2E 增加 `input.y <= hero.bottom`（≥640）断言，锁定“人物站在对话框上沿”。

## 七、追加：第七轮预览反馈（整体上移 + 下方留白）

- 参考 ListenHub：composer 与 how-it-works 之间需要更明确的呼吸区。
- guide 顶部 padding 收紧：桌面 `sm:pt-6 → sm:pt-2`（-16px），移动 `py-4 → pt-2`（-8px），Hero+composer 整体上移。
- composer→showcase 间距：桌面 16 → **32px**（`sm:mt-4`），移动 16 → **24px**（`mt-2`）；showcase 绝对位置保持不变（上移量与下方留白相互抵消），不牺牲首屏。
- 实测：1440 首卡 top 528 / h3 bottom 751；1024 488 / 744；390 562 / 839，均保持原阈值内。
- E2E 增加 1440 下 `showcase.y - composer.bottom ≥ 24` 断言。

## 验收记录

- `cd frontend && pnpm check` → 0 error；prettier 通过
- `cd frontend && pnpm test` → 266 files / 1754 tests passed
- `cd frontend && pnpm test:e2e -- tests/e2e/agent-new-chat-guide.spec.ts` → **41 passed**
- `cd frontend && pnpm check:dead` → 通过；`node scripts/check_i18n_keys.mjs` → 631 叶子全活
- `python3 scripts/check_docs_convention.py` → OK

## 四、取舍

- 本批覆盖了主计划 §3 的 Hero 230–250px 与“输入框保持项目高度”规格（用户预览反馈优先）；主计划状态头已追加说明。
- Hero 宽度取 860（与 composer 同宽）而非 demo 的 1000：demo 的 hero 1000 / composer 860 会让两者左轴差 70px，在本项目侧栏壳里更容易被读成“歪”；统一 860 更稳。若后续想恢复 1000，只改 `max-w`，同时更新 E2E 对齐断言。
