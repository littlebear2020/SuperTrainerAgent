# 六项核心能力实测缺陷修复

> **TL;DR**：本批串行修复播客 HTML 结构标签进入朗读与 PPT 咖啡样本同类文字重叠；关键决策 = 保留安全抓取与现有转换器，以 SVG ink 估算框和导出后 PDF 行框双层机械验收；影响面 = podcast-studio、ppt-master 与对应测试/文档，不部署、不默认执行付费制作。

## 执行范围

- PR 1（播客）执行中；PR 2（PPT）尚未开始，严格等待 PR 1 合入。
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
- PR 2 的 RED→GREEN、行为验收、两条 PR 的 CI/OCR 与最终门禁将在实际执行后回填；未执行项不记为通过。
