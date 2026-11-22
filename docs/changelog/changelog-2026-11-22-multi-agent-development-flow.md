# 单人多 Agent 研发流程优化

> **状态**：实现与本地验收完成；[PR #39](https://github.com/littlebear2020/SuperTrainerAgent/pull/39) 最终 source 属性修复 CI/OCR 复审中；未部署、未运行付费 live。
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
- 发布性能优化后最终 `cd backend && UV_PROJECT_ENVIRONMENT=/Users/littlebear/Documents/SuperTrainerAgent-home-plan/backend/.venv make test` → `10313 passed, 95 skipped, 434 warnings in 76.64s`，exit 0。
- 最终根 `UV_PROJECT_ENVIRONMENT=/Users/littlebear/Documents/SuperTrainerAgent-home-plan/backend/.venv make check` → exit 0；ESLint `0 errors`（121 条存量 warning）、Prettier 通过、knip 完成、i18n `659 个叶子全活`、docs convention `OK`。
- `git diff --check` 与 `python3 scripts/check_docs_convention.py` → exit 0；`OK 仓库治理约定全过`。

## PR #39 首轮 OCR 处置

- 未跟踪计划的 blob 读取：改用 `git ls-tree`，保留内容 SHA-256、blob 置空；新增未跟踪计划测试。
- 单独传 `--expect-spec-sha256`：改为缺少 `--spec` 即失败，新增参数组合测试。
- 已跟踪旧 `release-manifest.json`：复制源成员时排除，保证新 manifest 唯一；新增重复成员测试。
- 两件包装脚本的 pack/upload/deploy：全部先赋值检查 `release_sha`，不得在 echo 命令替换中吞错；deploy 在停服和解压前验证 manifest。
- 首轮 4 条 medium + 1 条 low 均已对应修复；回归结果 `test_agent_context.py` 8 passed、三件流程测试文件合计 21 passed。复审结果以 PR 最新 OCR 为准。

## PR #39 第二轮 OCR 处置

- 正式/测试 pack 增加显式失败保护；生产 pre-deploy 阻断时立即停止，release_archive 失败时保留旧包且不打印成功。
- 不采纳“先删除旧包”的建议：保留原子替换语义，改为 upload 强制比较包内 `source_sha` 与目标 ref 的提交 SHA，旧包和 main 前进后的过期包都不能上传。
- `git archive` stderr 改为临时文件消费，避免大诊断输出填满管道造成死锁；源 manifest 唯一性保持第一轮回归。
- 两件 pack 都显式提示默认包不含 `config.yaml/.env`，首次部署必须选择本地配置开关。
- backlog 缺失改为失败关闭，缩进 `[release-blocker]` 同样识别；根 config 路径单独限制，避免 shell glob 跨目录。
- GitHub Actions 不采用 YAML anchor；两件 workflow 加互守注释，并以 PyYAML 断言 push/pull_request paths 完全一致及 quick-checks 交叉引用。backend 文档触发 lint 是有意的较宽集合。
- 测试 pack 不调用正式 pre-deploy 是保留未合入 ref 与付费修复验证通道；upload/deploy 的函数 `return 1` 配合 `set -e` 和显式 SHA 检查终止流程。
- 第二轮相关回归三件测试文件合计 `26 passed`，`bash -n` 与 `git diff --check` 通过。

## PR #39 第三轮 OCR 处置

- pack 开始时先移除 SHA 侧车，成功后原子写入本次 manifest SHA；upload 只读取该侧车，不再二次解析移动 ref。失败保留旧 tar 时侧车已失效，旧包不能误传。第四轮又把侧车扩展为源码 SHA + 包摘要。
- 两件 upload 都先 `cd "$LOCAL_DIR"`，避免从其他目录调用时读取错误包；deploy 打印 SHA 并要求与 upload 输出人工核对。
- manifest `includes_local_config=true` 时，生产和测试 deploy 均跳过旧配置恢复；显式打包的新配置不会再被服务器备份覆盖。
- `process.stdout` 改为显式空值检查并在等待子进程前关闭；根 config 的目录判断去重。
- 第三轮相关回归三件测试文件合计 `28 passed`，两件 Bash 脚本语法检查和 `git diff --check` 通过。

## PR #39 第四轮 OCR 处置

- 实测当前 Git LFS 会在 `git archive` 时尝试 smudge/下载，行为依赖本机对象与网络。发布工具改为 `GIT_LFS_SKIP_SMUDGE=1` 强制离线指针导出，再通过 `git check-attr` 找到 LFS 路径，只用本地 size/OID 完全匹配的对象替换；不联网、不接受未物化或被修改的对象。
- 普通文件保持原始流式复制；另修掉非对齐文件尾部读取问题，并增加未跟踪计划、重复 manifest 之外的大对象与尾部回归。
- 侧车先升级为“源码 SHA + tar.gz SHA-256”，最终再补配置标记第三行。pack 先使侧车失效、成功后原子写入；upload 重算摘要并随包 scp；服务器 deploy 在停服/解压前再次重算和比对，传输损坏或替换不再依赖人工发现。
- manifest 字段读取统一为 `release_manifest_value`，修复帮助文案和侧车错误提示；显式配置恢复语义保持第三轮结论。
- 完整真实 Chrome 包验证：`REAL_LFS_ARCHIVE_OK 188087520 5e35986566a4f2c4369db965df03543d6d25d0fc37d6ac2aca83ebccaabc2545`，tar 成员大小与 LFS size 一致、成员 SHA-256 与指针 OID 一致。最终复审又将 LFS 属性来源固定为 `source_sha` 对应树，新增“工作区删除属性不影响历史 ref”回归；属性与文件清单均改 NUL 分隔，中文/空格路径不再漏配，scp 逐项显式失败。
- 第四轮及 source/特殊路径补充回归三件测试文件合计 `35 passed`，两件 Bash 脚本语法检查和 `git diff --check` 通过。

## PR #39 最终评审与 deferred minors

- 最终 CI run `44994542102`（backend）、`44994542191`（lint-frontend）、`44994542174`（quick-checks）、`44994542138`（review）全部 `pass`。
- 最终 OCR 无 Critical/Important/medium，仅 3 条 low deferred：① `release_archive.py` finally 后的不可达 return；②③ 两件 deploy 的侧车格式预检不如 upload 明确。后两项在格式错误时仍会因摘要/源码不一致阻断，不影响发布安全语义，故不为低优先打磨新增行为改动。
- 四轮已修发现均保留对应回归；最终三件流程测试文件 `32 passed`，后端全量 `10305 passed, 95 skipped`。

## 缓存与敏感配置边界（PR #39 复审）

- 两件 pack 均明确提示“缓存以 Git 提交内版本为准”，本地未提交、忽略目录或重新下载的更新不会静默入包；需要升级缓存必须先走受控提交。
- 本地发布包和侧车均 `chmod 600`；scp 成功后再收紧服务器权限。显式 `includes_local_config=true` 的包在 deploy 成功后删除 tar 与侧车，失败时仅以 0600 保留用于排障。
- 根 config workflow pattern 改为 `/config*.yaml`，与 quick-checks 根目录语义一致；生产 pack 参数转发统一为单个可选 `--no-sandbox`。
- `ls-tree -z` high 评论经原始输出证伪：实际形态为 `<mode> blob <sha>\t<path>\0`，metadata/path 之间仍保留 TAB，现有解析与已跟踪计划测试正确，未按误报修改。
- 本轮相关回归三件测试文件合计 `38 passed`，两件 Bash 脚本语法检查与 `git diff --check` 通过。

## 发布性能与信任边界（PR #39 复审）

- pack 一次读取 manifest 的源码 SHA 与配置标记，侧车最终为三行“源码 SHA + tar.gz SHA-256 + 配置标记”。upload/deploy 不再读取 manifest，只校验侧车和重新计算一次包摘要，避免大缓存包重复解压。
- deploy 同步补齐源码/摘要格式和配置标记预检；LFS 在读取前检查成员大小，argparse 排除列表默认改为 `None`，git archive 中途失败保留 stderr 根因。
- 同信道侧车只防旧包、误用与意外损坏，不宣称防主动篡改；正式发布必须联网 fetch，离线只允许明确授权的测试打包，不以陈旧 ref 冒充正式版本。
- 重复出现的 `ls-tree -z` high 仍由原始输出与已跟踪计划测试证伪，未修改正确实现。
- 本轮相关回归三件测试文件合计 `40 passed`，两件 Bash 脚本语法检查与 `git diff --check` 通过。

## 安全失败路径与运行依赖（PR #39 复审）

- 两件脚本均在入口前置检查 `python3`，头部声明本地与服务器依赖，避免到部署步骤才报命令不存在。
- 显式配置包改为 deploy 入口根据侧车设置 `EXIT trap`：成功、失败、`set -e` 退出均清理 tar 与侧车；缺侧车时错误提示也要求手动清理潜在敏感包，不再只覆盖成功路径。
- manifest 新增 `contains_sensitive_material`，`release_archive --include-local-config` 输出限制存放和部署后删除警告。
- git archive 只有在进程返回码非零时才把 stderr 归因为 git 失败，gzip/磁盘/替换等原始异常不再被掩盖；多余 pack 参数显式拒绝。
- 本轮相关回归三件测试文件合计 `41 passed`，两件 Bash 脚本语法检查与 `git diff --check` 通过。

## 未验证范围

- 未向真实服务器 upload/deploy，未验证服务器配置恢复或实际运行配置是否一致；本批只提供不可变包与版本证据。
- 未使用真实本地秘密配置测试显式追加路径；该路径使用临时占位文件与仓库内符号链接测试。
- 未跑付费 live、完整沙箱镜像重建或产品功能 E2E；本批不因此关闭上述两个 release-blocker。
- PyPI 网络初期不可用时复用既有后端虚拟环境，未新增、升级或降级依赖。
