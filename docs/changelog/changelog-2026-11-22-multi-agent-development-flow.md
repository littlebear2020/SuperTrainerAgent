# 单人多 Agent 研发流程优化

> **状态**：本地实施与验收完成；未部署、未运行付费 live；PR CI/OCR 终态以交付页面为准。
> **TL;DR**：新增 Agent 会话基线与不可变 Git 发布包；其他 Agent 在途工作改为提示，未验收 `[release-blocker]` 阻断正式发布；补齐研发基座 CI 路径和 PR/live 三项版本证据。影响面 = 研发约定、CI、打包与部署前检查，不改产品运行代码、不改 config_version。

## 1. 完成内容

- `scripts/agent_context.py`：输出 HEAD/base、ahead/behind、脏路径、worktrees、计划 blob 与 SHA-256；识别 patch-equivalent 残留分支，提供 clean/HEAD/计划哈希硬检查。
- `scripts/release_archive.py`：从明确 Git 提交流式生成 tar.gz，普通本地修改和未跟踪文件无进入通道；包内 `release-manifest.json` 记录 source/base SHA、排除路径与本地配置选择；失败原子保留旧包。
- `deploy_on_server.sh` / `deploy_to_test_server.sh`：保留 `pack/upload/deploy` 和产物名，默认 `origin/main`；测试可显式打包未合入 ref；上传和部署显示源码 SHA。
- `--no-sandbox` 统一只排除 `chrome-linux`，保留 fonts、Python wheels 与 BPE；常规升级不追加本地秘密配置，首次部署或明确需要时才使用显式配置开关。
- `pre_deploy_check.py`：fetch/ref 失败和未勾选 `[release-blocker]` 阻断；开放 PR、其他 worktree 和本地 main 漂移改为提示，适应单人多 Agent 并行。
- PR 模板和 live runbook 固定 `tested_sha` / `deployed_sha` / `spec_sha256`，禁止用本地 HEAD 冒充实际部署版本。
- backend 单测与 quick-checks 覆盖 `skills/`、`docker/`、根 config、三件流程脚本和两件包装脚本。

## 2. 决策与兼容边界

- 保持单负责人，不增加人工 reviewer；作者整分支自审后仍由 PR OCR 提供独立评审。
- 其他 Agent 的在途工作不再阻断不可变发布，因为发布内容不再来自当前工作区；主动绕过发布命令仍需靠负责人保管发布凭证约束。
- 已携带计划提交的执行分支不强制 `HEAD == origin/main`，但必须 behind=0、干净并记录计划哈希，避免基线检查误阻断正常执行。
- 本地配置只有显式选项才追加：`config.yaml` 与 `.env` 必需，`frontend/.env` / `extensions_config.json` 可选；仓库内符号链接允许，越界链接拒绝。
- 本批不部署、不读取真实配置、不运行付费测试、不清理其他 Agent 的 worktree 或未提交文件。

## 3. 现存正式发布阻断

以下两项没有被本批修复，已登记在 `docs/findings-backlog.md`：

1. PPT flat 首读最终 SOUL 修复尚缺目标行为复测；须完成同冻结输入 P01 验证或回退。
2. 沙箱 tiktoken 批尚缺从基座完整重建与线上镜像对齐；须完成完整重建、双架构冒烟和部署清单核对。

因此当前 `make pre-deploy` 返回 1 是预期保护，不得通过删除条目或绕过检查发布。

## 验收记录

- Task 1 RED：`test_agent_context.py` → `6 failed`，均为工具不存在；GREEN → `6 passed`。
- Task 2 RED：`test_release_archive.py` → `5 failed`，含不可变打包和包装脚本缺失；GREEN → `5 passed`。
- Task 3 RED：`test_pre_deploy_check.py` → `4 failed`，缺 release-blocker 与 `--ref` 语义；GREEN → `4 passed`。
- CI/协作静态断言 RED → `2 failed`（CI 路径、PR 模板缺失）；最终三件测试文件 `17 passed`。
- 整分支自审补充“携带计划提交分支”回归 → 单测先 `1 failed`，修正指引后单测 `1 passed`、相关套件 `17 passed`。
- 本仓真实打包：`python3 scripts/release_archive.py --ref origin/main --output /tmp/agent-flow-release.tar.gz --exclude docker/sandbox/cache/chrome-linux` → `REAL_ARCHIVE_OK 0e84b1485f029d33b8ba57023003f6d4e39055c8 17023`；未提交脚本不在包内，wheels/BPE/fonts 均在，chrome-linux 不在。
- 真实部署前探针：`python3 scripts/pre_deploy_check.py --ref origin/main` → `PRE_DEPLOY_PROBE_OK rc=1 blockers=2`；两条例外均阻断，其他 worktree 均为提示。
- `bash -n deploy_on_server.sh deploy_to_test_server.sh` → exit 0；两件 workflow 经 PyYAML 解析 → `WORKFLOW_YAML_OK 2`。
- 最终 `cd backend && UV_PROJECT_ENVIRONMENT=/Users/littlebear/Documents/SuperTrainerAgent-home-plan/backend/.venv make test` → `10290 passed, 95 skipped, 434 warnings in 78.34s`，exit 0。
- 最终根 `UV_PROJECT_ENVIRONMENT=/Users/littlebear/Documents/SuperTrainerAgent-home-plan/backend/.venv make check` → exit 0；ESLint `0 errors`（121 条存量 warning）、Prettier 通过、knip 完成、i18n `659 个叶子全活`、docs convention `OK`。
- `git diff --check` 与 `python3 scripts/check_docs_convention.py` → exit 0；`OK 仓库治理约定全过`。

## 5. 未验证范围

- 未向真实服务器 upload/deploy，未验证服务器配置恢复或实际运行配置是否一致；本批只提供不可变包与版本证据。
- 未使用真实本地秘密配置测试显式追加路径；该路径使用临时占位文件与仓库内符号链接测试。
- 未跑付费 live、完整沙箱镜像重建或产品功能 E2E；本批不因此关闭上述两个 release-blocker。
- PyPI 网络初期不可用时复用既有后端虚拟环境，未新增、升级或降级依赖。
