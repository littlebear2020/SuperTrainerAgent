# 六项核心能力实测缺陷修复实施计划

> **状态**：未执行（执行中：PPT）；定稿日 2026-11-23；执行日 2026-11-23；基准 commit `a2f6059dddbe6362c312d833519bd20f5322b9b9`；PR 1 已合入为 `9a010c827c9ef8e07819dae34d0e1c00453d448d`；changelog：[六项核心能力实测缺陷修复](../changelog/changelog-2026-11-23-six-core-live-defects-remediation.md)。
> **TL;DR**：只按 PR 1（播客 HTML 结构标签不得进入朗读正文）→ PR 2（PPT 咖啡样本第 2 页文字重叠）串行修复，禁止并行派发。播客保留安全抓取与现有转换器，PPT 以 ink 估算框检查和导出后 `soffice`/`pdftotext -bbox-layout` 机械预筛双层验收；不新增依赖、不默认跑付费制作。
> **For agentic workers：** REQUIRED SUB-SKILL：用户批准本计划后，使用 `executing-plans` 单执行者按 Task 0→4 串行执行并逐项勾选；每个实现任务先写失败测试、保存真实 RED，再写最小实现。两个 PR 严格按 PR 1→PR 2 合入，禁止并行派发；状态只修改文件顶部原状态行，依次使用 `未执行（执行中：播客）`、`未执行（执行中：PPT）`，两条 PR 都合入后才改为 `已执行`，不得另起状态头。

**Goal：** 仅面向首页六张卡片的新任务（course-video、creative-studio、ppt-master、podcast-studio、AI 生图、AI 生视频），消除已核实的播客结构词朗读和 PPT 真实文字行重叠；不处理历史对话或历史任务。新任务沿用现有 4 个字节直连音色，克隆不在本批范围内。

**Architecture：** 保留现有 Next.js/FastAPI/Agent/技能架构。播客在消毒后的 HTML 上移除文档结构节点并展开容器，把剩余节点交给现有转换器，不改安全抓取链；PPT 在共享 SVG 检查器中比较同模块独立 `<text>` 的 ink 估算框，再在导出后用现有 LibreOffice/Poppler 工具比较 PDF 的任意文字行框（含同一 block），以机械结果为主、目检为辅，不引入新的版面引擎、OCR 服务或视觉供应商。

**Tech Stack：** Python 3.12、BeautifulSoup 4、现有 `simple_html_to_markdown_traversal`、`SVGQualityChecker`/`text_measure`、pytest、现有 `svg_to_pptx.py`、LibreOffice `soffice`、Poppler `pdftotext -bbox-layout`、GitHub CI/OCR；不新增运行时依赖，不改前端 registry 组件。

**Spec / 证据来源：** 本文件「一、缺陷证据与验收基线」为实施契约。原始证据在执行前从 `.worktrees/live-test-remediation-pr1/temp/2026-11-23-six-core-live-test/` 复制到执行 worktree 之外的 gitignored `temp/`；复制件和脱敏派生物均不得进入 git。账号、密钥、签名 URL、运行数据库、`thread_id`、`run_id` 和任务 ID 必须脱敏，不读取或提交 `.env`、密钥和真实任务数据。

## 一、缺陷证据与验收基线

### D1：公开网页播客把结构标签 `html` 读入正文（根因已核实）

- `convert_html_bytes()` 当前把整棵 BeautifulSoup 交给 `simple_html_to_markdown_traversal()`。Example Domain 形态的现状返回以 `html Example Domain` 开头，随后才是 `# Example Domain`；Doctype 和 `<head><title>` 因而不是可见正文却被收入朗读文本。
- 不采用 `content_root = soup.body or soup`：完整文档上它虽能去掉结构词，却会丢失 `</body>` 之后的段落；没有 body 的 frameset 又退回整棵树，结构词仍在。
- 验收目标：结构容器不产生正文或标题，可见 h1、段落、链接、列表和代码示例保持；正文真的含有单词 `html` 时不能误删。`<title>` 不进入朗读正文，本批也不单独写入 sources 元数据。

### D2：PPT 咖啡样本第 2 页存在真实文字行重叠（渲染证据已核实）

- 第 2 页「再分段注水」与「偏差信号」的文字行框纵向重叠约 7pt，Poppler 将两行归入同一个 block；只比较跨 block 的行对会得到 0 重叠，验收会失真。
- 正常卡片的相邻行是独立文本框，两个文本框的声明矩形不相交；因此不能只比较 x/y 声明框，也不能把 ZIP、原生形状或 Agent 自述当作视觉通过。
- 验收目标：SVG 检查器能在导出前发现同模块独立 `<text>` 的 ink 重叠；导出后机械预筛必须命中咖啡样本这一组同 block 行对。**装饰性大数字只按文本内容判定**：整行文本去除首尾空白后必须完整匹配 `[0-9]{1,2}`（1–2 个数字字符），不得按短文本、行宽或字号放宽；其余文字重叠（包括「再分段注水」「偏差信号」等短标签）均阻止交付。符合该数字规则的命中只标记「该页必须目检」，不能单独使整份交付硬失败。
- `view_image` 跨轮只保留最近 2 张图，同一轮最多 8 张；超过后早期页面可能只剩文本占位。任何目检都不能替代机械行框结果，需要看图时每轮不超过 8 页，并在当轮把每页结论写成文本。

### 只观察、不在本批修实现

- P7 长线程本次真实页面加载完成后可落底、滚动和往返，未复现白屏/异常跳顶；没有跨账号对照及滚动数值序列，维持未复现，不修。
- `display_name=null`、用量标题国际化、ppt-master audio-only 规则、`clone:` 空后缀仍是未处理候选，不并入本批实现；未覆盖的 PPT 导出路径与补丁表格式在收尾登记，保持未勾选。

## 二、范围、决策与路径边界

### 本批执行

1. **PR 1 / 播客**：HTML 结构标签不得进入朗读正文，补齐真实 `convert_html_bytes()` 的 RED→GREEN 和播客 SSRF 回归。
2. **PR 2 / PPT**：修复咖啡样本同类文字重叠的共享检查器，并增加导出后的机械预筛；Default/Beautify 工作流新增同一道门，Quick/Edit Native 的未覆盖路径明确登记，不冒充已覆盖。
3. 两个 PR 由同一执行者严格串行，前一条合入后再开始下一条；不为其他静态候选或 P7 开实现 PR。

### 明确不做

- 不处理历史对话、历史任务或历史音色任务；新任务的音色列表和默认音色维持现有 4 个字节直连音色，不做克隆、声音分身恢复或任何音色入口改造。
- 不改 `fetch_url()`、SSRF、重定向、Host、SNI、TLS，不改 vendored `web_to_md.py`，不写 `replace("html", "")`。
- 不改 `quick-generate.md`、`edit-native-pptx.md` 的导出流程；这两处未覆盖本批新增的导出后预筛，收尾写入 findings backlog 并保持未勾选。共享 SVG 检查器本身对所有调用它的路径生效，不能据此宣称这两条路径已有导出后预筛。
- 不加长 `failure-recovery.md`；其 prompt 预算超限的 2026-11-20 findings backlog 项本批显式推迟。不改 `backend/seed/agents/ppt-master/SOUL.md`，因此不递增 `SEED_VERSION`；frontmatter 不动。
- 不整理 `TRAINERAI_PATCHES.md` 现有表格（空行拆表、P32 在 P31 前、缺 P30 行均保留）；新增 P34 只追加一行，表格缺陷另行登记。
- 不新增依赖，不改前端 registry，不重写模板引擎或字体测量，不读取/提交 `.env`、密钥、认证 state、运行数据库或真实任务数据，不自动部署、不默认运行付费制作。

### 串行 PR 路径表

| PR | 允许路径（allow） | 禁改路径（ban） | 共享基座触达 |
| --- | --- | --- | --- |
| **PR 1 / 播客正文** | `skills/public/podcast-studio/scripts/podcast_studio.py`、`backend/tests/test_podcast_studio_ingest.py`、必要的 `skills/public/podcast-studio/SKILL.md`、本计划（执行时移至 `docs/plans/`）、对应 `docs/changelog/` | `fetch_url()`、SSRF/重定向/Host/SNI/TLS 校验、vendored `web_to_md.py`、`skills/public/ppt-master/**`、其他 backend service/route、前端、真实配置和任务数据 | **否**；继续调用现有转换器，不改共享抓取或安全基座 |
| **PR 2 / PPT 检查与预筛** | `skills/public/ppt-master/scripts/svg_quality/checker.py`、`skills/public/ppt-master/scripts/pptx_text_overlap_prescreen.py`（新增）、`backend/tests/test_ppt_master_svg_quality.py`（新增）、`backend/tests/test_ppt_master_pdf_prescreen.py`（新增）、`backend/tests/test_ppt_master_skill_vendored.py`、`skills/public/ppt-master/workflows/generate-pptx.md`、`skills/public/ppt-master/SKILL.md`、`skills/public/ppt-master/TRAINERAI_PATCHES.md`、本计划、对应 `docs/changelog/`、`docs/findings-backlog.md` | `skills/public/ppt-master/workflows/profiles/quick-generate.md`、`skills/public/ppt-master/workflows/edit-native-pptx.md`、`workflows/governance/failure-recovery.md`、`backend/seed/agents/ppt-master/SOUL.md`、`backend/app/gateway/seed.py`、上游大版本升级、live-preview server、无关模板/图片/音频链、`config*.yaml`、前端和真实用户 PPT | **需规划侧独占**：共享 SVG 检查器影响多个调用路径；两 PR 全程串行，不与其他批次并行修改，不触及 SOUL/seed/runtime |

代码路径重叠表：PR 1 与 PR 2 的产品实现路径互不相交；共享计划、changelog、findings 和证据只由同一执行者按 PR 串行更新。禁止把共享文档重叠解释成可并行派发的理由；gitignored `temp/` 证据不属于可提交路径。

## 三、实施任务

### Task 0：批准、证据保全与漂移检查

**Files：** 仅执行 worktree 之外的 gitignored `temp/` 取证目录，以及执行时按计划更新的计划/changelog；本任务不改产品文件。

- [x] **Step 1：先复制并保全证据。** 在清理任何旧 worktree 之前，将 `.worktrees/live-test-remediation-pr1/temp/2026-11-23-six-core-live-test/` 复制到主 clone 的 gitignored `temp/2026-11-23-six-core-live-test/`（执行 worktree 之外）。用 `git check-ignore` 和 `git status` 证明复制件不会进入 git；只保留脱敏后的可复核派生物，脱敏账号、密钥、签名 URL、运行数据库、`thread_id`、`run_id` 和任务 ID，不把原证据贴入日志或提交。
- [x] **Step 2：确认批准范围与最新基线。** 用户批准后运行 `git fetch origin main`、`git diff --stat a2f6059dddbe6362c312d833519bd20f5322b9b9..origin/main`，分别检查两组 allow 区域的 name-only diff；目标接口或测试漂移时先更新计划映射，不按旧行号覆盖。
- [x] **Step 3：建立单一隔离 worktree/分支并更新原状态行。** 从最新 main 建执行 worktree，获批后将本计划移回 `docs/plans/`、建立批次 changelog 骨架；开始 PR 1 时只把顶部原状态行改为 `未执行（执行中：播客）`，不得新增状态头，不得在主 clone 上实施或开并行实现会话。
- [x] **Step 4：保存最小反例与正常对照。** 从证据重建脱敏的完整 HTML、`</body>` 后段落、frameset/空 body、含普通 `html` 正文和代码示例，以及咖啡第 2 页同模块独立 `<text>`、正常卡片和装饰性大数字夹具；记录来源是原文件还是重建文件，不提交无关用户内容。执行记录：HTML 与 SVG 均为按计划契约重建的脱敏夹具，位于主 clone gitignored `temp/2026-11-23-six-core-live-defects-derived/`；frameset/空 body 由 Task 1 测试内联构造。
- [x] **Step 5：运行文档检查。** `python3 scripts/check_docs_convention.py` 通过后才进入 PR 1。

### Task 1 / PR 1：播客 HTML 结构标签不再进入朗读

**Files：** `skills/public/podcast-studio/scripts/podcast_studio.py`、`backend/tests/test_podcast_studio_ingest.py`，必要时更新 `skills/public/podcast-studio/SKILL.md`。

**Interfaces：** `convert_html_bytes(raw: bytes, requested_url: str, final_url: str, headers: dict) -> str` 签名、返回 Markdown 字符串及现有 sanitization 不变；`intake()` 的 source schema、hash 和 `prepare_verbatim()` 原文一致性不变。

- [x] **Step 1：写完整文档的真实失败测试。** 在 `test_convert_html_bytes_excludes_document_structure` 中用 `<!doctype html><html><head><title>Example Domain</title>…</head><body><h1>Example Domain</h1><p>This domain is…</p>…</body></html>` 调用真实 `convert_html_bytes()`，断言输出不以 `html `、`# html` 或独立 `html` 行开头，h1 与段落各出现一次，`<title>` 文本不重复出现。
- [x] **Step 2：写保真、越界节点和空文本测试。** `test_convert_html_bytes_preserves_after_body_and_literal_html` 必须真实调用转换器，断言 `</body>` 后的段落保留、正文「html 是标记语言」保留、`<pre><code>&lt;html&gt;</code></pre>` 保留；`test_convert_html_bytes_frameset_or_empty_body_fails_text_check` 覆盖 frameset 与空 body，断言得到空文本，并直接调用 `_check_text()` 断言响亮抛出 `PodcastError`。
- [x] **Step 3：运行并确认真实 RED。** `cd backend && uv run pytest tests/test_podcast_studio_ingest.py -k 'document_structure or after_body or literal_html or frameset' -q`。完整文档用例必须因当前实现实际输出 `html Example Domain` 开头且标题重复而 RED；不得手造输出、 mock 转换器或修改断言制造假失败。实际旧实现为 4 failed / 47 deselected，首条失败值确为 `html Example Domain`。
- [x] **Step 4：实现最小 DOM 处理。** 在 `convert_html_bytes()` 内按以下约束修改：
  - 保留 `_sanitize_html()` 和现有转换器调用。
  - 解析消毒后的 HTML。
  - 移除 Doctype、Comment。
  - 删除 `<head>`，因此 `<title>` 不进入朗读正文，也不在本批单独写入 sources 元数据。
  - unwrap `<html>` 和 `<body>`，让 body 内以及 `</body>` 之后的剩余节点仍交给 `simple_html_to_markdown_traversal()`；不使用 `soup.body or soup`。无正文的 `<frameset>` 作为结构节点移除，确保 frameset 对照得到空文本并由 `_check_text()` 响亮失败。
  - 不改 vendored `web_to_md.py`，不写 `replace("html", "")`，不改其他转换逻辑。
- [x] **Step 5：运行定向与完整安全回归。** 上述测试转绿后运行 `cd backend && uv run pytest tests/test_podcast_studio_ingest.py -q`，覆盖非公网 IP、混合 DNS、逐跳重定向、HTTPS 降级、Host、SNI、TLS、大小/超时、HTML 消毒、script/source hash 与 approval/integrity；空文本仍由 `_check_text()` 响亮失败，不能变成空音频。实际定向 4 passed，完整回归 51 passed。
- [x] **Step 6：做无 TTS 行为探针。** 用真实 `convert_html_bytes()`/`intake()` 对受控 Example Domain 夹具（网络可用时可取公开 `https://example.com`）保存脱敏 `sources.json` 文本，确认无结构词、越界段落保留且原文朗读 turn 一致；不调用 TTS、不产生音频费用。实际使用本地脱敏重建夹具，输出 `PODCAST_PROBE_OK`，1 个原文 turn 与 source text 完全一致，`tts_called=false`。
- [x] **Step 7：完成 PR 1 门禁并串行交接。** `cd backend && uv run pytest tests/ -q` 全绿，根 `make check` 与文档检查通过；提交 `fix(podcast): 排除网页结构标签进入朗读`，经 CI/OCR 无 Critical/Important 后 rebase 合入。合入后只把原状态行改为 `未执行（执行中：PPT）`，再开始 PR 2。实际以仓库标准 `make test` 运行同一完整后端套件（10186 passed / 91 skipped）；PR [#47](https://github.com/littlebear2020/SuperTrainerAgent/pull/47) 的 backend-unit-tests、quick-checks、AI Code Review 全绿，OCR 0 findings，merge commit `9a010c827c9ef8e07819dae34d0e1c00453d448d`。

### Task 2 / PR 2：让 SVG 检查器识别同模块文字碰撞

**Files：** `skills/public/ppt-master/scripts/svg_quality/checker.py`、`backend/tests/test_ppt_master_svg_quality.py`（新增）；基线结果按要求回填本计划与 changelog。

**Interfaces：** 在 `SVGQualityChecker` 增加同模块独立 `<text>` 的 sibling 检查，复用现有 `_estimated_text_bounds(..., include_headroom=False)`、`_bounds_overlap_dimensions()` 和现有容差常量（含 `_BOUNDS_OVERFLOW_TOLERANCE`）；同一 `<text>` 内的 tspans 不互比，不重写字体测量或其他检查阶段。最终 error/warning 级别由模板基线决定，不预先假定 1px 容差安全。

- [x] **Step 1：写咖啡派生的真实失败测试。** 新 backend 测试按现有 `importlib.util.spec_from_file_location`/`sys.path` 方式加载技能 `checker.py`，构造同一卡片模块内两个独立 `<text>`：多行「再分段注水」的 ink 下缘越过「偏差信号」上缘，声明矩形保持不相交。调用真实检查器，当前实现必须因没有 sibling overlap 报告而 RED。实际旧实现 1 failed / 8 passed，咖啡 ink 纵向相交 9.4px，声明框不相交。
- [x] **Step 2：加入误报对照并确认收集。** 覆盖同一 `<text>` 内 tspans 不互比、边缘相接或现有容差内不报错、隐藏/非视觉/既有 off-canvas morph 排除、正常卡片相邻行不误报；`skills/public/ppt-master/scripts/tests/` 不在 CI、Makefile、pre-commit 中执行，新增检查只放 backend 测试。运行 `cd backend && uv run pytest tests/test_ppt_master_svg_quality.py --collect-only -q`，确认新测试由 backend pytest 收集，不把技能目录里的 `python3 -m pytest` 当作主命令。实际收集 9 tests。
- [x] **Step 3：实现候选碰撞检查。** 在现有文本几何检查之后，对同一模块的独立 `<text>` 两两比较 `include_headroom=False` 的 ink 估算框，复用现有容差和重叠尺寸 helper；保留既有可见性、非视觉和 off-canvas morph 排除，不以声明框替代 ink，也不改 `_estimated_text_bounds()` 的测量算法。先保留候选报告以便基线统计，不提前锁定最终级别。
- [x] **Step 4：先跑现有模板 SVG 基线再定级。** 用执行基线中仓库已有的模板 SVG（另以现有正常卡片夹具作对照，不用新造的“正常”样本替代模板）运行候选检查，记录模板清单、正常相邻行的小间隙、所有新增命中及若定为 error 的新增 error 数。基线出现新增 error 时，本批把检查定为 **warning**，并把误报样本、坐标和理由写回本计划；只有零新增 error 才定为 **error**。将最终级别、基线命令和结果写入 changelog，不能预先宣称 1px 容差安全。实际扫描 `scripts/confirm_ui/static/style_previews/*.svg` 与 `scripts/tests/fixtures/native_projection/*.svg` 共 26 份，若定为 error 会新增 1 个 error：`15_signing_target.svg` 的图例「战兴业务」(x=682,y=274) 与原生图表 fallback 数值「223」(x=743,y=287) 命中 31.0×13.7px；该组属于 `data-pptx-replace-with="chart"` 的 native projection fallback，不作为本批硬失败，故最终级别为 **warning**。现有模板正常相邻行最小非负纵向间隙为 0.4px；基线明细保存在 gitignored `temp/2026-11-23-six-core-live-defects-derived/ppt-svg-baseline.json`。
- [x] **Step 5：运行检查器定向与相关回归。** `cd backend && uv run pytest tests/test_ppt_master_svg_quality.py tests/test_ppt_master_skill_vendored.py -q` 全绿；咖啡夹具命中、修复布局后的同夹具无同类命中，普通中文/英文/数字混排、同段 tspans、合法换行和既有 module overlap 行为不回归。保留 RED→GREEN 与基线证据。实际 16 passed。
- [ ] **Step 6：保存检查器实现提交。** 在同一 PR 2 内提交 `fix(ppt-master): 检出同模块文字碰撞`，不夹带 SOUL、seed 或无关模板变化，便于审阅实际算法 diff。

### Task 3 / PR 2：新增导出后的机械文字行预筛与交付约束

**Files：** `skills/public/ppt-master/scripts/pptx_text_overlap_prescreen.py`（新增）、`backend/tests/test_ppt_master_pdf_prescreen.py`（新增）、`backend/tests/test_ppt_master_skill_vendored.py`、`skills/public/ppt-master/workflows/generate-pptx.md`、`skills/public/ppt-master/SKILL.md`、`skills/public/ppt-master/TRAINERAI_PATCHES.md`。

**Interfaces：** 新 CLI 形如 `python3 ${SKILL_DIR}/scripts/pptx_text_overlap_prescreen.py <printed_output.pptx> > <validation/text-overlap.json>`；脚本只调用已有 `soffice` 和 `pdftotext -bbox-layout`，以标准库解析行框并输出页码、block/line 标识、相交尺寸和处置分类，不新增 Python 包或系统依赖。

- [ ] **Step 1：写机械预筛失败测试并确认收集。** 新 backend 测试按现有方式加载技能脚本，使用最小 `bbox-layout` 夹具断言任意两条文字行框都比较，包括同一 block 内的行对；咖啡样本「再分段注水」/「偏差信号」必须命中并阻止交付，跨 block-only 算法不得作为合格实现。另以整行为 `7`、`42` 的夹具断言只产生「该页必须目检」标记，不是整份交付硬失败；以 `123`、「再分段注水」和「偏差信号」夹具断言不能落入装饰性数字例外。运行 `cd backend && uv run pytest tests/test_ppt_master_svg_quality.py tests/test_ppt_master_pdf_prescreen.py --collect-only -q`，两份新测试都必须被收集。
- [ ] **Step 2：实现无新增依赖的预筛。** 调用现有 `soffice` 将 PPTX 转 PDF，再调用 `pdftotext -bbox-layout` 解析每条 `<line>` 的实际框；对所有行对计算相交，包括同 block，记录可定位证据。仅当命中行的完整文本（去除首尾空白后）匹配 `[0-9]{1,2}` 时才归为装饰性大数字并标记该页目检；其余任何文字行重叠都阻止交付，不能用短文本或字号规则放行「再分段注水」/「偏差信号」。转换/解析失败必须响亮失败。
- [ ] **Step 3：走离线真实行为验收。** 用脱敏夹具 SVG → 现有 `svg_to_pptx.py` → LibreOffice → `pdftotext -bbox-layout`，保存原始 PDF、行框报告和修复前后结果；原咖啡第 2 页那一对必须命中，修正源 SVG 后重新导出并证明不再命中。不得为验收再跑一轮付费 Agent，不得以 Agent 自述或 PNG 代替行框结果。本机或沙箱缺少 `soffice` 或 `pdftotext` 时，记录实际缺失命令并保持本步及行为验收未勾选，不能勾成完成。
- [ ] **Step 4：把新增门写入 Default/Beautify 工作流。** `generate-pptx.md` 覆盖 Default 和 Beautify：在现有 Step 7.3 导出/postflight 之后只新增 `pptx_text_overlap_prescreen.py` 预筛，失败回到源 SVG 修复并重新导出；**不得新增 `pptx_delivery_check.py` 调用**，该脚本只检查压缩包、字体和关系等交付结构，不检查文字重叠，Step 7.3 已有导出 postflight。Quick/Edit Native 是独立导出路径，本批不改这两处（Edit Native 既有 `pptx_delivery_check.py` 调用保持不变）；收尾必须把未覆盖路径写入 `docs/findings-backlog.md` 并保持未勾选，不能宣称全导出路径已有该门。
- [ ] **Step 5：只追加 P34 覆盖约束与补丁行。** 在 `SKILL.md` TrainerAI 覆盖段只增加一条 **P34**：Default/Beautify 导出后必须用 `soffice` + `pdftotext -bbox-layout` 比较任意文字行框（含同 block）；只有整行文本完整匹配 `[0-9]{1,2}` 时才属于装饰性大数字并只要求该页目检、记录结论，其余文字重叠（含短标签）均阻止交付；Agent 自述/看图不能替代机械结果。扩展 `test_overlay_pins_document_patches` 钉住 P34 及上述关键 token，同时保留当前 P11–P13、P20–P22 断言；frontmatter 不动，不改 SOUL、不递增 SEED_VERSION，不加长 `failure-recovery.md`。`TRAINERAI_PATCHES.md` 只追加一行 P34，不整理既有表格。
- [ ] **Step 6：限制目检并完成 PR 2 门禁。** 若机械命中或交付策略要求目检，每轮最多 8 页，当轮为每页写下文字结论，跨轮不依赖已被丢弃的旧图；禁止只用 Agent 看 PNG 作为交付门。运行 `cd backend && uv run pytest tests/test_ppt_master_svg_quality.py tests/test_ppt_master_pdf_prescreen.py tests/test_ppt_master_skill_vendored.py -q`、backend 全量、根 `make check`、`python3 scripts/check_docs_convention.py` 和 vendored attribution guard，全部通过后提交 `fix(ppt-master): 增加文字行机械预筛`，经 CI/OCR 无 Critical/Important 后 rebase 合入。

### Task 4：串行收尾、证据与未处理项

- [ ] **Step 1：只在两条 PR 都合入后关闭计划。** PR 1 合入后已使用 `未执行（执行中：PPT）`；PR 2 合入且门禁完成后，只修改顶部原状态行为 `已执行`，回填执行日期和 changelog 链接，不新增第二行状态头。
- [ ] **Step 2：登记可审计证据。** 两个 PR 共用对应批次 changelog，逐行写「命令 → 实际输出关键行」，包含播客真实 RED→GREEN、SSRF 回归、PPT 检查器基线/最终 warning 或 error 决策、同 block 咖啡命中、离线导出链、CI/OCR 链接；若工具缺失或行为未验收，明确写 `NOT_VERIFIED`，不以 mock 或自述冒充完成。
- [ ] **Step 3：把仍未处理项写入 findings backlog 并保持未勾选。** 以实际执行日期分别登记 `display_name=null`、用量标题国际化、ppt-master audio-only 规则、`clone:` 空后缀、未覆盖的 PPT 导出路径（至少 `quick-generate.md` 与 `edit-native-pptx.md`）、`TRAINERAI_PATCHES.md` 补丁表格式（空行、P32/P31 顺序、缺 P30）；链接本计划作为指针。P7 维持未复现，不修，不新增实现任务。
- [ ] **Step 4：运行最终本地门禁并清理。** 在最新 main 复核受影响测试、`cd backend && uv run pytest tests/ -q`、根 `make check` 和 `python3 scripts/check_docs_convention.py`；检查 `git status`，删除本轮生成的临时日志，确保 gitignored `temp/` 证据、脱敏材料和真实任务数据未进 git。部署、付费制作和配置变更均不是默认动作。

## 四、全局 Review Focus

1. **正文真含单词 `html` 但不能误删**：普通段落和 `<pre><code>` 必须由真实 `convert_html_bytes()` 保留；同时保持完整播客 SSRF 回归，不能借修结构词放宽抓取安全。
2. **`</body>` 之后的正文不能丢**：独立段落和 frameset/空 body 对照必须证明容器展开不是 `soup.body or soup`，空文本由 `_check_text()` 响亮失败。
3. **声明框不相交但同一 block 内的文字行可以重叠**：检查器用 `include_headroom=False` 的 ink 估算框，导出预筛比较任意 `pdftotext` 行框，咖啡那一对必须命中，不能只比跨 block 或声明矩形。
4. **Agent 自述和看图都不能代替 `pdftotext` 行框结果**：PNG 目检只在机械预筛之后作补充；只有整行完整匹配 `[0-9]{1,2}` 的数字命中才能触发该页目检并留下文本结论，其余文字重叠（包括短标签）必须阻止交付。

## 五、完成定义

- 只有 PR 1、PR 2 两个 PR，按顺序独立合入；每个 PR 都有针对原始失败形态的真实 RED→GREEN、相关回归、受影响层全量、CI/OCR 和可审计 changelog，顶部状态最终仅为 `已执行`。
- 播客完整文档不再以结构 `html` 开头，h1/段落各一次、title 不重复，`</body>` 后正文保留，普通 `html` 与代码示例保留，frameset/空 body 响亮失败，SSRF 全套安全测试不减弱。
- PPT 检查器使用 ink 估算框并保留排除语义；现有模板基线及最终 warning/error 决策有据可查，若有新增 error 必为 warning 且误报样本已写回计划和 changelog，不能预设容差安全。
- 离线夹具真实走完 SVG→PPTX→LibreOffice→`pdftotext -bbox-layout`；咖啡同 block 行对被预筛命中并在源 SVG 修复后消失，只有整行完整匹配 `[0-9]{1,2}` 的数字命中只标页面目检，其余文字重叠（包括「再分段注水」「偏差信号」）均阻止交付。缺少 `soffice` 或 `pdftotext` 时行为验收保持未完成，不能宣称交付门通过。
- Default/Beautify 的新增导出后预筛明确写入工作流；Quick/Edit Native 未覆盖路径已登记且未勾选，不宣称所有导出路径都有该门。P34 只追加一条，frontmatter、SOUL、SEED_VERSION 和 `failure-recovery.md` 保持不动。
- 未新增依赖、未改前端 registry、未读取或提交机密/真实任务数据、未自动部署或默认付费制作；`display_name=null`、用量标题国际化、audio-only、`clone:` 空后缀、补丁表格式和 P7 未复现均如实保留为未处理项。
