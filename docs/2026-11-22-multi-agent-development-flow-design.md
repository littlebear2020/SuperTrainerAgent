# 单人多 Agent 研发流程优化设计

> **TL;DR**：保留单负责人、分支 + PR + OCR 的现有模式，增加机器可读的会话基线、不可变发布包和 `release-blocker` 闭环；其他 Agent 的在途工作不再阻塞从已审提交发布，不引入人工审批或重型项目管理系统。

## 1. 背景与目标

当前主要风险不是缺少规范，而是 Agent 在旧工作区读取旧计划、行为未验证仍合入、部署直接打包当前目录，导致“测试对象、验收依据、合入代码、部署内容”可能不是同一个版本。

目标：

- 每个 Agent 开始任务和交接时都能生成一致的基线证据：代码 SHA、基准 SHA、计划文件哈希、工作区状态。
- live 验收同时记录本地期望版本与实际部署版本，避免用旧计划判断新代码。
- 发布包只来自明确 Git 提交，不包含普通未提交或未跟踪文件，并内置发布清单。
- 多个 Agent 的在途 worktree/PR 只作提示，不妨碍从已审 `origin/main` 提交发布。
- 最终行为未验证的合入例外必须登记为 `release-blocker`，正式部署前检查阻断。
- `skills/`、`docker/`、根 config 和研发流程脚本变更触发后端回归，减少 CI 路径盲区。

非目标：

- 不增加人工 reviewer、分支保护费用或外部项目管理平台。
- 不自动部署、不触发付费测试、不修改服务器配置。
- 不在本批清理其他 Agent 的 worktree、未提交文件或功能缺陷。
- 不追求每次 PR 都跑完整镜像重建或全部 live E2E。

## 2. 方案比较与选择

### 方案 A：只补充文档约定

成本最低，但仍依赖每个 Agent 自觉，无法解决旧基线、脏目录打包和例外失控，不采用。

### 方案 B：本地标准库护栏 + 现有 CI（采用）

新增两个无第三方依赖的 Python 工具，改造现有部署前检查和脚本，并用 pytest 在临时 Git 仓库做行为测试。优点是单负责人维护成本低、所有 Agent 可共用、CI 与本地同构；局限是无法阻止主动绕过命令的人，因此发布凭证和正式部署仍须由唯一负责人保管。

### 方案 C：GitHub Projects、受控合并机器人和完整 CD

强制力更强，但配置、权限和运行成本明显更高，不符合当前单人阶段的 YAGNI 原则，暂不采用。

## 3. 组件设计

### 3.1 会话基线工具 `scripts/agent_context.py`

输出人可读摘要或 JSON，字段至少包括：

- `head_sha`、`branch`、`base_ref`、`base_sha`、`ahead`、`behind`；
- `dirty` 与变更路径；
- `worktrees`；
- 可选 `spec_path`、`spec_blob_sha`、`spec_sha256`；
- `patch_equivalent_to_base`，识别提交 SHA 不同但补丁已进入基准的残留分支。

默认只报告，不限制正常开发中的脏工作区。`--require-clean` 用于开始任务或正式验收；`--require-head` 要求 HEAD 与基准完全一致；`--expect-spec-sha256` 用于复核报告是否仍对应同一份计划。Git 命令失败、计划缺失或哈希不匹配必须非零退出，不静默降级。

典型入口：

```bash
python3 scripts/agent_context.py --base origin/main --spec docs/plans/<plan>.md --require-clean --require-head --json
```

live 报告还需单独记录部署包中的 `release-manifest.json` SHA；本地 HEAD 不能冒充服务器实际版本。

### 3.2 不可变发布工具 `scripts/release_archive.py`

输入为 `--ref` 和输出路径：

- 解析完整提交 SHA；默认要求该提交是 `origin/main` 的祖先，测试服务器显式 `--allow-unmerged` 才可打包未合入提交。
- 通过 `GIT_LFS_SKIP_SMUDGE=1 git archive` 流式读取已提交文件，普通本地修改和未跟踪文件没有进入通道；LFS 路径由 `git check-attr -z --source=<source_sha>` 与对应 `git ls-tree -z` 的 NUL 分隔记录确定，仅允许用工作区中 size/OID 完全匹配的真实对象替换指针，缺失或修改即失败。
- 生成根目录 `release-manifest.json`，至少记录源码 SHA、完整 ref、构建所用 Git 基准、排除路径和是否包含本地配置。
- 只有显式 `--include-local-config` 才追加本地配置：`config.yaml` 与 `.env` 必须存在，`frontend/.env` 与 `extensions_config.json` 存在时才追加；必需文件缺失或路径越界直接失败。
- `--exclude` 只接受仓库内归一化路径；输出使用临时文件后原子替换，失败不留下半包。
- `--no-sandbox` 统一只排除 `docker/sandbox/cache/chrome-linux`，保留字体、Python wheels 与 BPE 缓存。

生产和测试包装脚本保留原命令入口及产物名，默认 `--ref origin/main`；测试可用环境变量显式指定其他 ref。pack 开始时使旧 SHA 侧车失效，成功后原子写入“源码 SHA + tar.gz SHA-256 + 配置标记”三行侧车；upload 与服务器 deploy 只读取侧车并重算包摘要，不二次解析移动 ref，也不重复解压 manifest。上传和服务器解压后均显示发布 SHA，部署记录以该 SHA 为准。

### 3.3 部署前检查 `scripts/pre_deploy_check.py`

阻断项调整为：

- 发布 ref 无法解析或不允许发布；
- `docs/findings-backlog.md` 存在未勾选的 `[release-blocker]`。

以下从阻断改为提示：其他 worktree、开放 PR、本地 main 与远端不同步。原因是发布包已绑定不可变 ref，其他 Agent 的在途工作不会混入；本地 main 是否干净也不再代表发布包内容。

保留 changelog 部署动作和 `config_version` 提示，但不再把它们当作配置已经生效的证明。正式发布脚本在打包前执行该检查；测试服务器允许显式未合入 ref，用于验证修复。

### 3.4 协作和 CI

- PR 模板增加 `tested_sha`、`spec_sha256`、行为验收、部署动作和未验证例外字段。
- live runbook 要求报告同时记录本地期望、计划哈希和实际部署 SHA；敏感地址、账号和内部任务 ID 仍保存在仓库外。
- backend 单元测试工作流覆盖 `skills/`、`docker/`、根 config、发布/会话脚本和包装脚本；quick-checks 对相同研发基座变化安装后端检查环境。
- `AGENTS.md` 将“活跃任务数限制”与“已合入 worktree 清理”分开，规定会话开始、交接、例外合入和发布的机械命令。

## 4. 错误处理与兼容

- 网络不可用时允许基于已有本地 `origin/main` 开发；发布前必须重新解析并核对远端，核对失败则不得正式发布。侧车与包同信道只防旧包和意外损坏，不宣称防主动篡改。
- 老服务器首次部署若没有配置备份，必须使用显式本地配置选项；常规升级默认不带本地秘密配置。manifest 标记显式包含本地配置时，deploy 跳过旧配置恢复，防止新配置被备份覆盖；本地与服务器发布包/侧车均为 0600，显式包含配置的包由 `EXIT trap` 在成功与失败路径均删除；本地与服务器均需前置检查 `python3`。
- 现有 `pack/upload/deploy` 命令和 tar 产物名不变，降低迁移成本。
- 未验证例外允许合入和测试部署，但正式部署被阻断，直到对应 backlog 项带验证证据勾选关闭。

## 5. 验证策略

实现级：

- 临时 Git 仓库验证基线 JSON、脏树/HEAD 强制检查、计划哈希和 patch-equivalent 检测。
- 构造含已提交文件、本地修改、未跟踪文件和虚拟配置的仓库，验证包内容、manifest、排除路径、未合入 ref 拒绝和原子失败。
- 验证 pre-deploy 对其他 worktree/PR 仅提示、对 release-blocker 阻断。
- 静态断言 CI 路径、PR 模板和包装脚本入口。

行为级：

- 在临时仓库制造“已提交版本 A + 工作区版本 B + 未跟踪秘密占位文件”，生成的包只能包含 A，不得包含 B 或占位文件。
- 对一个已合入 patch-equivalent 分支和一个分叉分支运行会话工具，前者提示可清理，后者不得误判。
- 在样例 backlog 中切换 release-blocker 勾选状态，部署前检查分别返回 1 和 0。

收尾运行相关 pytest、后端全量、根 `make check`、文档治理检查及真实打包探针；不运行部署和付费测试。

## 6. 假设

- 仓库只有一位最终负责人，多 Agent 只负责隔离开发和提出交付证据。
- 其他 Agent 的在途工作可以存在，但不得共享本批工作区或修改本批输入。
- 现有 `origin/main` 在开发开始时为 `0e84b148`；推送前如远端已前进，先 rebase 并重跑相关验证。
- 本批是研发流程与发布工程改造，不承诺解决审计中发现的功能缺陷本身。
