# Worktree 生命周期简化实施计划

> **状态**：已执行（2026-11-22；main 与 GitHub 已同步，仅保留主工作区）；[changelog](../changelog/changelog-2026-11-22-worktree-lifecycle-simplification.md)。
> **TL;DR**：合入 harness 纯文档研究与未提交的有价值文档，原始 demo/截图/交接材料完整归档；关闭不合入的 PR #39，移除 4 个附加 worktree 和已闭环分支；不改产品代码、部署或 CI。

**Goal：**消除“哪个 worktree 最新、合入后是否还需保留”的歧义，同时确保文档、demo 和交接证据不因清理丢失。

## 范围

- 做：清点独立提交、保存原始材料、合入纯文档、脱敏收编报告、删除已闭环 worktree。
- 不做：不合入 PR #39 的发布/CI/安全边界改动，不修改部署脚本、产品代码或测试工作流。

## 已执行清单

- [x] 清点 5 个 worktree；确认 harness 有 2 笔纯文档提交，works-assets 为 patch-equivalent，ppt-soul 无独立提交。
- [x] 将 `home-plan` 的 `temp/`、`.screenshots/`、原始报告、计划及修改前版本完整保存到 `temp/worktree-archive-20261122/`，包括 video-gen、my-works demo 和 hero preview。
- [x] 将本地 main 从 `8c78dace` 快进到 `0e84b148`，快进合入 harness 的 `ef966562`、`0811bcf0`。
- [x] findings 冲突保留双方全部条目；收编研究、实施计划、设计交接和测试文档，入库副本仅脱敏主机、账号和任务 UUID。
- [x] 将重新出现的 Cursor 复核版修复计划单独归档，并更新 main 对应 pending 计划，未覆盖新版本。
- [x] 推送 `f52623c2` 与 `41d102a3`；关闭 PR #39 但不合入，保留远程分支供设计/计划追溯。
- [x] 移除 home-plan、harness-p0、ppt-soul、agent-flow 4 个 worktree；删除 works-assets 本地/远程分支及 harness、ppt-soul 本地分支。
- [x] 验证 `git worktree list` 仅剩 `/Users/littlebear/Documents/SuperTrainerAgent`，main 与 origin/main 一致且工作区干净。

## 后续唯一规则

新任务从最新 `origin/main` 创建独立 worktree；一个目录只承担一个任务；PR 合入后立即移除 worktree 和分支。旧目录不得直接复用于新任务。

## 验证

- `python3 scripts/check_docs_convention.py` 通过。
- demo、截图、原始设计与计划文件存在性探针通过。
- 纯文档批次，不运行产品全量测试，不触发部署。
