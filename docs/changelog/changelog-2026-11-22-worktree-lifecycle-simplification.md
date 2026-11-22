# Worktree 生命周期简化与文档保全

> **状态**：已执行；main 已推送，仅保留主工作区。
> **TL;DR**：合入 harness 2 笔纯文档提交和本地有价值的研究/设计/交接材料；原始 demo、截图与报告完整保存在 gitignored 归档；移除 4 个附加 worktree，关闭未合入 PR #39，不改部署和 CI。

## 合入内容

- `ef966562`：批准模式摘要研究并登记 trim 缺口。
- `0811bcf0`：完成 harness 升级 P0 摸底。
- `f52623c2`：收编研究计划、实施计划、video-gen 设计交接、实测报告和 changelog；保留 findings 双方条目。
- `41d102a3`：收编清理期间新出现的 Cursor 复核版修复计划，未用旧版本覆盖。

入库报告只脱敏真实主机、账号和任务 UUID；原始内容未改写，保存在 `temp/worktree-archive-20261122/`。

## 原始材料归档

- `home-plan/temp/`：video-gen-flow-demo、my-works-artifact-demo、workspace-home-final-design、全模块测试截图等。
- `home-plan/.screenshots/`：agent-showcase 截图、验证脚本和 `t0-hero-preview.html`。
- `home-plan/docs/`：未跟踪报告、交接文档、计划及修改前版本。
- `main/docs/plans/pending/`：被批准版取代的旧研究稿。
- `harness-p0/temp/`、`agent-flow/sdd/`：研究与执行交接记录。

## Worktree 处置

- 删除 `home-plan` worktree 和 works-assets 本地/远程分支；其补丁已等价进入 main。
- 合入后删除 `harness-p0` worktree 和本地分支。
- 删除无独立提交的 `ppt-soul-validation-20261122` worktree 和本地分支。
- 关闭 PR #39（未合入），保留远程分支供设计/计划追溯；删除 agent-flow worktree。

## 验收记录

- `git worktree list` → 仅 `/Users/littlebear/Documents/SuperTrainerAgent [main]`。
- `git status --short --branch` → `## main...origin/main`，无未提交受控文件。
- `git rev-parse HEAD` / `git rev-parse origin/main` → 均为 `41d102a3103546e516c85b32e015ba7ab3408134`。
- GitHub PR #39 → `state=CLOSED`、`mergedAt=null`。
- demo 与原始证据存在性探针 → `ARCHIVE_AND_DOCS_OK`。
- `python3 scripts/check_docs_convention.py` → `OK 仓库治理约定全过`。
- 本批只合入文档并清理工作区，未运行产品全量测试，未部署、未修改 CI。
