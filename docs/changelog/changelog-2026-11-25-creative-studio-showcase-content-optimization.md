# 创意工作室 10 件视觉案例内容优化

> **状态**：已执行（2026-11-25；实现与离线验收全绿；未部署、未运行付费生成）。
> **TL;DR**：按已审 Demo 原子更新 10 件视觉案例，替换 7 件素材并保留 3 件；关键决策 = 文案统一为“内容类型 + 使用场景”、VisualCase 以 formatLabel 展示真实交付格式，提示词逐字按计划附录落地；影响面 = frontend showcase、素材与测试，不改四分类、布局、交互、backend、skills、config、docker 或部署。

计划：[`docs/plans/2026-11-25-creative-studio-showcase-content-optimization-plan.md`](../plans/2026-11-25-creative-studio-showcase-content-optimization-plan.md)

## 改动

- 10 件案例按附录 A 顺序落地：课程海报 2、知识图解 4、知识漫画 2、结构图 2；4 个换题 ID 改为 `visual-poster-manager`、`visual-comic-customer`、`visual-diagram-talent`、`visual-diagram-addie`。
- 7 件替换素材来自已审 Demo：两张海报、客服漫画、梯度下降漫画、训练图解、人才模型与 ADDIE；RAG、Prompt、企业路线图 3 件保留案例的 preview/original 二进制不变。
- 全部 title/tag/description、note、recipeNote、fields/spec 按附录精确更新；海报提示词固定自包含 HTML+CSS+SVG 与 Chrome 命令，客服漫画和梯度下降提示词使用各自指定参数，不调用固定模板。
- VisualCase 以 `formatLabel` 取代内部风格名；VisualCard/VisualDialog 仅把格式信息改为 `宽 × 高 · formatLabel`，Video/Deck/Podcast 不变。
- 两张 SVG 仅含 `viewBox="0 0 1600 1000"`，无固定根宽高、外链图片或 `@import`；旧 10 个无引用素材已删除。
- 新增十件原图 naturalWidth/naturalHeight、SVG viewBox/download、四视口瀑布流/筛选 grid/弹窗/横向溢出验收；既有 CTA、附件、草稿、只填不发、焦点恢复与首屏预算断言保留。

## 执行决策

- 本机 `cwebp 1.6.0` 不接受计划的 `-out` 拼写，实际使用其等价参数 `-o`；所有替换 preview 仍由对应 PNG 以 `-q 88` 生成，未从 JPG 二次压缩。
- 经用户明确授权，`agent-new-chat-guide.dom.test.tsx` 仅更新三条与新附录冲突的旧断言：两处选中值改为 `【请填写，如社群招募】` / `【请填写，如企业内训】`，一处位置断言改为对应新占位符；未改光标行为。
- 截图测试增加图片加载与 decode 等待，避免证据拍到黑块；产品代码、布局与验收门槛均未因此改变。

## 资产与行为验收

- 资产引用闭包 **58** 个，视觉 preview **10** 个；引用文件总量 **17,493,062 bytes**（≤20,000,000）。
- 冷缓存创意工作室首屏媒体 **1,244,766 bytes**（≤1,500,000），不请求 `/originals/`，无 pageerror、404 或 console error。
- 三件保留案例的 6 个 preview/original 文件 SHA-256 校验全部不变。
- 1440 / 1280 / 1024 / 390 四视口的首屏、瀑布流、筛选 grid 与弹窗均通过自动断言和截图目检；缩略图可辨识、弹窗中文正常、无横向溢出。16 张原始截图与 4 张联系表位于 `temp/2026-11-25-creative-studio-showcase-content-optimization/`，未入库。
- 仅验证提示词文本和既有填充链路；未运行真实生成，不承诺点击 CTA 会生成与示例同版作品。

## 验收记录

- `git diff e8803fe5832ba3da3715698c07ccfe9c370f0b43 --stat` → 基准至最新 main 为 43 个文件的聊天/任务恢复等改动；本计划 showcase allow 路径零漂移。
- RED：`pnpm exec vitest run --project node tests/unit/core/agents/showcase.test.ts` → `2 failed | 11 passed`（旧 ID、缺 SVG）；DOM → `5 failed | 30 passed`（旧文案、格式、比例和 ID）。
- GREEN：同一 node/DOM 定向命令 → node `13 passed (13)`、DOM `35 passed (35)`；`tsc --noEmit` 退出 0。
- `shasum -a 256 -c .superpowers/sdd/2026-11-25-creative-studio-showcase-content-optimization-plan/retained-assets.sha256` → RAG、Prompt、路线图 6 个文件全部 `OK`。
- 直接加载 `showcase.ts` 的资产闭包统计 → `references: 58`、`totalBytes: 17493062`、`previewBytes: 1147226`、`creativeHeroBytes: 97540`。
- `pnpm exec playwright test tests/e2e/agent-new-chat-guide.spec.ts tests/e2e/agent-showcase-style-workflow.spec.ts` → `60 passed (36.4s)`；`[creative-studio initial media] 1244766 bytes`。
- 四视口 Playwright 用例 + `temp` 联系表目检 → 1440/1280 三列、1024 两列、390 单列；筛选 grid、中文弹窗与横向溢出检查全过。
- `pnpm test` → `Test Files 300 passed (300)`、`Tests 2056 passed (2056)`。
- `pnpm check` → 退出 0；ESLint `0 errors`（120 条既有 warnings）、TypeScript 通过、Prettier `All matched files use Prettier code style!`。
- `python3 scripts/check_docs_convention.py` → `OK 仓库治理约定全过`。
