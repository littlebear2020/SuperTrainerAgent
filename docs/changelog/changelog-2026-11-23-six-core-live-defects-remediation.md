# 六项核心能力实测缺陷修复

> **TL;DR**：本批串行修复播客 HTML 结构标签进入朗读与 PPT 咖啡样本同类文字重叠；关键决策 = 保留安全抓取与现有转换器，以 SVG ink 估算框和导出后 PDF 行框双层机械验收；影响面 = podcast-studio、ppt-master 与对应测试/文档，不部署、不默认执行付费制作。

## 执行范围

- PR 1（播客）已通过 [#47](https://github.com/littlebear2020/SuperTrainerAgent/pull/47) rebase 合入，merge commit `9a010c827c9ef8e07819dae34d0e1c00453d448d`；PR 2（PPT）现从该新 main 串行开始。
- 原始实测证据已复制到主 clone 的 gitignored `temp/2026-11-23-six-core-live-test/`；只使用脱敏重建夹具作为测试输入，不提交真实任务数据。

## 验收记录

- `git diff --stat a2f6059dddbe6362c312d833519bd20f5322b9b9..origin/main` → 无输出，执行基线与计划基准一致，两组 allow 区域 name-only diff 均为空。
- `git check-ignore -v temp/2026-11-23-six-core-live-test` → 命中 `.gitignore:110:temp/`；`git status --short -- temp/2026-11-23-six-core-live-test` 无输出。
- 播客基线：隔离环境首次 `uv run` 在测试前因 PyPI TLS handshake EOF 无法构建 hatchling；改用既有主 clone backend venv 的 `UV_PROJECT_ENVIRONMENT=... uv run --no-sync` → `test_podcast_studio_ingest.py` 47 passed，未把依赖解析失败记为产品测试失败。
- PR 1 RED：`uv run pytest tests/test_podcast_studio_ingest.py -k 'document_structure or after_body or literal_html or frameset' -q` → 4 failed / 47 deselected；完整文档首行实际为 `html Example Domain`，标题重复，frameset/空 body 均含结构文本。
- PR 1 GREEN：同一命令 → 4 passed / 47 deselected；完整 `test_podcast_studio_ingest.py` → 51 passed，SSRF、逐跳重定向、Host/SNI/TLS、消毒、hash 与 approval/integrity 回归通过。
- PR 1 无 TTS 行为探针：真实 `intake()` + `prepare_verbatim()` 处理脱敏重建 HTML → `PODCAST_PROBE_OK`，`turn_count=1`、`verbatim_matches_source=true`、`tts_called=false`；脱敏 `sources.json` 与探针结果保存在 gitignored temp。
- PR 1 全量：串行 `uv run pytest tests/ -q` 约 5 分钟仅到 10%，主动中止且不计为通过；仓库标准 `cd backend && make test`（同一完整套件，`CI=true ... -n auto --dist loadfile`）→ **10186 passed, 91 skipped**。
- PR 1 根 `make check` → backend ruff/format、frontend eslint/tsc/prettier/knip/i18n 与 docs convention 全过；既有前端 120 条 warnings、0 errors。工作区首次运行缺 `node_modules`，按锁文件 `pnpm install --frozen-lockfile --prefer-offline` 后复跑通过。
- PR #47 `gh pr checks 47 --watch` → backend-unit-tests pass (3m15s)、quick-checks pass、AI Code Review pass；OCR 摘要 1 selected item / **0 findings**；已 rebase 合入为 `9a010c827c9ef8e07819dae34d0e1c00453d448d`。
- PR 2 SVG RED：`uv run pytest tests/test_ppt_master_svg_quality.py -q` → 1 failed / 8 passed，旧实现没有 sibling overlap 报告；GREEN → 9 passed；与 `test_ppt_master_skill_vendored.py` 相关回归 → 16 passed。
- PR 2 SVG 基线：真实 `_check_text_bounds()` 扫描 `style_previews/*.svg` + `native_projection/*.svg` → 26 份、1 个候选命中；`15_signing_target.svg` 的「战兴业务」/「223」为 31.0×13.7px，属原生图表 fallback，故最终定为 **warning**；正常相邻行最小非负间隙 0.4px。完整清单与坐标在 gitignored `ppt-svg-baseline.json`。
- PR 2 PDF RED：`uv run pytest tests/test_ppt_master_pdf_prescreen.py -q` → 7 failed（新脚本缺失）；实现后 7 passed。真实 Poppler XHTML 包装回归先 RED（no pages），修复后完整预筛 8 passed；同 block、`7`/`42` 目检、`123` 与短标签硬失败、转换/解析失败均有断言。
- PR 2 文档 RED→GREEN：P34 与 Default/Beautify 工作流断言先 2 failed；补齐 SKILL、generate-pptx、TRAINERAI_PATCHES 后，SVG/PDF/vendored 相关回归 → **25 passed**，attribution guard 通过；未改 Quick/Edit Native、SOUL、SEED_VERSION、frontmatter 或 `pptx_delivery_check.py`。
- 原始咖啡证据：`pdftotext -bbox-layout 咖啡冲泡入门.pdf ...` → 第 2 页「再分段注水」/「偏差信号」**same_block=true**，相交 53.77×6.726pt，`block_delivery`。
- 脱敏真实生成链：重建两页 SVG → `svg_quality_checker.py --stage final` → `svg_to_pptx.py` → `soffice` → `pdftotext -bbox-layout`。重叠版 PPTX 预筛 `blocking_count=2`；首个修复版又发现「再分段」/「注水」内部行重叠 2.52pt，继续下移源 tspan 后重新导出，最终 `passed=true`、`blocking_count=0`、`manual_review_count=0`。
- 实际 CLI：`python3 scripts/pptx_text_overlap_prescreen.py <修复后 coffee.pptx> > validation/text-overlap-cli.json` → exit 0，`CLI_PRESCREEN_OK status=passed blocking=0`。两版 PDF、bbox-layout、PPTX、JSON 报告均在 gitignored temp；未运行付费 Agent、未以 PNG/自述替代机械结果。
- `docs/findings-backlog.md` 已登记 display_name、用量标题 i18n、audio-only、`clone:` 空后缀、Quick/Edit Native 未覆盖及补丁表格式；P7 维持未复现，failure-recovery 既有预算项继续推迟。
- 实际 CLI 双向结果：重叠版 `pptx_text_overlap_prescreen.py` → exit 1，`CLI_PRESCREEN_BLOCKED status=failed blocking=2`；修复版 → exit 0，`CLI_PRESCREEN_OK status=passed blocking=0`。
- PR 2 新脚本与 backend 测试定向 `ruff check` → All checks passed；`ruff format --check` → 4 files already formatted。直接以 backend 配置扫描 vendored `checker.py` 会报 400 个存量风格问题（该树本属豁免区），本批未清理或扩大范围。
- PR 2 全量 `cd backend && make test` → **10204 passed, 91 skipped**；根 `make check` → backend ruff/format、frontend eslint/tsc/prettier/knip/i18n 与 docs convention 全过（既有前端 120 warnings / 0 errors）。
- PR 2 的 CI/OCR 与合入结果将在实际执行后回填；未执行项不记为通过。
