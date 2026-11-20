# 沙箱镜像预装 tiktoken（ppt-master 精确 token 计数）

> **状态**：免费验证已完成；**未做从基座的完整镜像重建**（验证用 overlay 构建 + `docker build --check`）、**未部署**、**未跑付费 P01 端到端复测**（冻结中）。
> **TL;DR**：沙箱镜像缺 tiktoken，`execution-context` 在沙箱内 `token_status=unavailable` 硬 raise，执行包全链路失效；本批把 tiktoken 0.12.0 + regex 2026.4.4（cp310 manylinux，amd64/aarch64）和 o200k_base BPE 缓存 vendoring 进 `docker/sandbox/cache/`，构建期 `RUN --network=none` 离线装入并设 `TIKTOKEN_CACHE_DIR`；关键决策 = 不赌任何镜像源/外网（清华源实测 403、BPE 托管在 Azure blob）；影响面 = 沙箱镜像需重建才生效（K16），`local-deploy.sh` 的后端索引默认改 pypi.org，不动技能树与 config schema。

## 1. 问题与根因

- `skills/public/ppt-master/scripts/project_management/execution_context.py`（#27/#34 栈）强制 `token_status=exact`：沙箱缺 tiktoken 时 `[ERROR] token_status=unavailable: exact o200k_base counting is required`，exit 1，执行包产不出来。
- 基座 `all-in-one-sandbox:1.11.0`（Python 3.10.12、Ubuntu 22.04、glibc 2.35）无 tiktoken/regex；`uv.lock` 因 `requires-python>=3.12` 不含 cp310 wheel，无法复用其哈希。
- 外部依赖三处都不可靠：① 清华源对包索引整站 403（实测 numpy/requests/duckdb/tiktoken 与根索引，curl/pip/uv 三种 UA，开发机与容器内一致）；② 公司网络对 pypi.org 静默丢包（Dockerfile 2026-10-23 事故注释）；③ BPE 数据托管在 Azure blob，运行期首次 `get_encoding` 要联网下载。

## 2. 改动

- `docker/sandbox/cache/python-wheels/`（新增）：4 个 wheel + 带 `--hash=sha256` 的 `requirements.txt`（版本、来源、升级流程写在文件头注释里）。
- `docker/sandbox/cache/tiktoken/fb374d41…`（新增）：o200k_base BPE，文件名 = `sha1(下载 URL)`（tiktoken 缓存键），sha256 与 tiktoken 内置 `expected_hash` 一致。
- `docker/sandbox/Dockerfile`：两条 `COPY` + `ENV TIKTOKEN_CACHE_DIR=/opt/tiktoken-cache` + 一条 `RUN --network=none`——`pip --no-index --no-deps --require-hashes` 离线安装、删除临时 wheel、构建期断言 `o200k_base` 编码 `hello world == [24912, 2375]`。插在 ppt-master 依赖块之后，在 `--upgrade pip` 之后（`--break-system-packages` 需要 pip ≥ 23.0.1）。
- `.gitattributes`：`docker/sandbox/cache/tiktoken/* binary`——该文件是 19.9 万行纯文本，`* text=auto eol=lf` 会让 git 做换行归一化（改字节即破坏 sha256），区内文本文件还会整份进 AI 评审。
- `scripts/local-deploy.sh`：新增 `PYPI_INDEX_URL`（默认 `https://pypi.org/simple`，可覆盖），用于远程 `deploy.sh` 的 `UV_INDEX_URL`，替换写死的清华源。
- `skills/public/ppt-master/TRAINERAI_PATCHES.md`：登记 P32（避开 #27 栈已占用的 P30/P31）。
- 测试：`test_sandbox_tiktoken_vendoring.py`（19）、`test_local_deploy_package_index.py`（3）。
- 未改动：技能树代码与工作流、`config*.yaml` / `config_version`、provider 代码、`backend/Dockerfile`、`docker-compose*.yaml`。

## 3. 决策与需要评审关注的点

- **没有把 `PYPI_INDEX_URL` 传给沙箱构建**（偏离了“构建段显式走 pypi.org”的字面要求，有意为之）：沙箱 Dockerfile 的 `PIP_INDEX` 会被烘焙成运行期 pip 源（`/etc/pip.conf` + `ENV PIP_INDEX_URL`），改成 pypi.org 会把 2026-10-23 的“运行期 pip 挂死十余分钟”事故烘焙回镜像。tiktoken 已 vendoring，构建期不依赖任何索引，故沙箱构建不传索引；由 `test_local_deploy_does_not_bake_a_build_index_into_the_sandbox_image` 钉住。
- **`PYPI_INDEX_URL` 默认 pypi.org 对公司服务器可能不通**：这是按任务要求设的默认值，我无法验证公司网络；受限网络用 `PYPI_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/` 覆盖（该镜像在本机与容器内实测 200）。只影响后端 `uv sync`，不影响沙箱构建。
- **`RUN --network=none`**：构建本身即证明离线安装与缓存命中不依赖网络。本文件已用 RUN heredoc（FONTCONFIG），同属 BuildKit 语法，未新增 builder 要求；`docker build --check` 对整份 Dockerfile 无警告。
- **`TIKTOKEN_CACHE_DIR` 目录 root 所有、任务用户（uid 1000）只读**：o200k_base 命中缓存不写文件，已验证；但 `tiktoken.get_encoding('cl100k_base')` 等其它编码在沙箱里会 `PermissionError`（无写权限）而不是联网下载。全仓技能树只用 `o200k_base`（grep 6 处，均为同一常量），故不放宽权限；若将来有其它编码需求，需另行预烘焙。
- **版本/哈希升级要三处同步**：`requirements.txt`、wheel 文件、测试里的预期（测试中的 sha256 是独立于 requirements 的第二份真值，防一处笔误让校验自己通过）。

## 4. 镜像状态与部署注意（待部署对齐）

- 线上沙箱镜像仍是 `cee995a7` 时代构建；同标签的本地镜像（`deerflow-sandbox:local-cee995a7`）已验证无 tiktoken/regex。
- 本地重建采用当前 main 的 Dockerfile，相对 `cee995a7` 多两处：Rosetta warmup 补丁（`a6d8d07c`，Apple Silicon 专用，把 `_warmup_pools()` 的急切预热置空；amd64 线上同样会带上，行为是会话按需创建）与模板指纹排序（`7253b375`，`LC_ALL=C sort -z` 使 `TEMPLATE_VERSION` 与沙箱内比对口径一致）。**部署前线上与本地镜像内容不同，部署时需一并对齐。**
- 部署需重建沙箱镜像（K16）才生效；`deploy_on_server.sh pack --no-sandbox` 会 `--exclude=docker/sandbox/cache`，连同新缓存目录一起排除——用它打的包上不能重建沙箱镜像（原有行为，服务器已有缓存时才适用；已登记 backlog）。
- 体积：仓库内新增约 7.5 MB（4 个 wheel 共 3,854,436 B + BPE 缓存 3,613,922 B）。镜像增量略大于此——`COPY` 层保留 wheel，后续 `RUN` 里的 `rm -rf` 不回收该层，另加已安装包体积——**未实测**。

## 5. 未验证 / 边界

- **从基座的完整重建未执行**：这一步被权限分类器拦下，没有绕过。替代验证 = overlay 构建（对真实 Dockerfile 新增区块逐字提取，叠在 arm64 `local-cee995a7` 与 amd64 `v3-20261104` 上）+ `docker build --check`（无警告）。完整重建（含 apt/npm/pip 全链路、约 15 GB）待你放行后补跑。
- 未部署、未合并 #27/#34、未跑付费 P01 端到端复测；本批只证明“沙箱内精确计数可用”，不证明 qwen 行为。
- 冒烟所用技能树 = #34 分支 `2d2c22bb`（只读挂载，工作树始终干净）；main 本身还没有 `execution_context.py`，合并顺序为 #27 → #34 → 本 PR 无依赖，本 PR 可独立合入。

## 验收记录

- `cd backend && PYTHONPATH=. uv run pytest tests/test_sandbox_tiktoken_vendoring.py -q`（RED：Dockerfile/local-deploy.sh 未改）→ `7 failed, 13 passed`；失败原因均为“缺 COPY/ENV/RUN --require-hashes / 缺默认索引”，无手误。
- 同一命令（`.gitattributes` 用例 RED）→ `1 failed`（缺 `docker/sandbox/cache/tiktoken/* binary`）；`test_local_deploy_does_not_bake_…` RED → `1 failed, 2 passed`（脚本里还带着 `--build-arg PIP_INDEX`）。
- `PYTHONPATH=. uv run pytest tests/test_sandbox_tiktoken_vendoring.py tests/test_local_deploy_package_index.py tests/test_sandbox_offline_readiness.py -q` → `32 passed`（19 + 3 + 10）。
- 变异检查（scratch 副本，`/tmp/taskB-scripts/mutate.py`，不入库）→ `MUTATIONS=27 SURVIVORS=none`：损坏 wheel 字节、两架构 wheel 互换并重算哈希（仅 ELF 检查能抓）、删/多 wheel、requirements 去 `==`/去 hash/错 hash/版本漂移、BPE 改名/改字节/多文件、Dockerfile 去 `--require-hashes`/`--network=none`/`--no-index`/`--no-deps`/`--break-system-packages`、加 `--index-url`、去 ENV/ENV 指错路径、去 COPY（×2）、去构建期断言、安装块挪到 pip 自升级之前、local-deploy 退回清华/去默认值/把索引烘焙进沙箱构建、去 `.gitattributes` 条目。
- `docker build --check -f docker/sandbox/Dockerfile .` → `Check complete, no warnings found.`
- arm64 overlay 构建（`FROM deerflow-sandbox:local-cee995a7` + Dockerfile 新增区块）→ `Successfully installed regex-2026.4.4 tiktoken-0.12.0`、`tiktoken 0.12.0 o200k_base OK (offline)`。
- **BEFORE**（旧镜像 `local-cee995a7`，uid 1000，`--network none`，挂载 #34 技能树）`execution-context <proj> P01` → `[ERROR] token_status=unavailable: exact o200k_base counting is required`，rc=1。
- **AFTER**（overlay，同条件，入口 bypass）→ P01 `tokens=12320 sha256=d3f1597b… read_ranges=1-8,9-15`、P03 `tokens=14391 sha256=408158c9… read_ranges=1-8,9-16`，均 rc=0；与宿主（py3.12 + tiktoken 0.12.0）输出逐位一致。
- **AFTER（真实 AIO 服务）**（overlay 以 provider 同款 flags 起容器——`--cap-drop=ALL` + 5 个 `--cap-add`、`no-new-privileges`、`seccomp=unconfined`、3g 内存、2g shm，端口 28080；经 `agent_sandbox` SDK）→ `shell.exec_command` 与 `bash.exec`（带 env）两条通道均见 `TIKTOKEN_CACHE_DIR=/opt/tiktoken-cache`、`exact o200k, cache hit, no download: [24912, 2375]`（探针把 `tiktoken.load.read_file` 换成一碰即抛，证明未联网）；P01/P03 同上数值，`api_probe rc=0`。（18080 被 `docker-superagent-1` 占用，改 28080，未触碰该容器。）
- **AFTER（amd64 生产架构，Rosetta/QEMU 仿真）**（`FROM littlebear2016/deerflow-sandbox:v3-20261104` + 同一区块）→ 构建 `o200k_base OK (offline)`；运行 `x86_64`，原生扩展 `_tiktoken.cpython-310-x86_64-linux-gnu.so`，P01/P03 tokens 与 sha256 同上。
- `pip3 check` 摘要前后一致（arm64 `local-cee995a7` 对 overlay：`b720b5861363`；amd64 `v3-20261104` 对 overlay：`b720b5861363`，各 1 行既有输出）——新增包未引入依赖冲突；`regex` 的唯一依赖方是 `tiktoken`。
- `cd backend && PYTHONPATH=. uv run pytest tests/test_aio_sandbox*.py tests/test_docker_sandbox_mode_detection.py tests/test_local_sandbox*.py tests/test_sandbox_offline_readiness.py tests/test_sandbox_tiktoken_vendoring.py -q`（逐文件列出）→ `205 passed, 1 skipped`。
- `cd backend && make test` → `10058 passed, 89 skipped, 438 warnings in 94.82s`，EXIT=0。
- 根 `make check`（后台运行，日志 `/tmp/taskB-make-check.log`，不入库；**启动时本 changelog 与 findings-backlog 的新增行尚未写完**，二者在其结束之后才定稿，其余全部改动文件的 mtime 均早于启动）→ EXIT=0；`All checks passed!`、`1319 files already formatted`、eslint `0 errors`（121 条 warning 为既有，本批未改 frontend）、`All matched files use Prettier code style!`、`i18n key 存活检测通过：630 个叶子全活`。代码侧结论因此仍对应当前代码；**其中 docs convention 的 `OK` 只对应当时的草稿，不覆盖这两个文档的终稿**，终稿结果见下面两行复跑。
- **终稿复跑 ①** `python3 scripts/check_docs_convention.py`（changelog 与 findings-backlog 定稿后；本行写入前后各跑一次，结果一致）→ `OK 仓库治理约定全过`，rc=0，无 `WARNING`。本地裸跑未设 `CONVENTION_BASE_SHA` / `CONVENTION_HEAD_SHA`，校验项 14（二进制禁区门禁）只在 CI 传入 diff range 时生效，本行不覆盖；`[binary-zone-ok]` 写在首笔提交 message 里。
- **终稿复跑 ②** `git add` 暂存全部改动后 `git diff --cached --check`（同样写入前后各一次）→ 无输出，rc=0。不用裸 `git diff --check`：它不扫未跟踪文件，changelog、两个新测试、`requirements.txt` 全会被漏过（scratch 仓实测：带行尾空格的未跟踪文件裸跑 rc=0 假通过，暂存后 `--cached` 报 `trailing whitespace`、rc=2）；wheel 与 BPE 缓存为二进制，无文本 hunk，不在该检查范围。
- `uv run ruff check` + `ruff format --check`（两个新测试文件）→ `All checks passed!`、`2 files already formatted`。
- 以 uid 1000、联网状态调用 `tiktoken.get_encoding('cl100k_base')` → `PermissionError: /opt/tiktoken-cache/…`（见 §3，已知边界）。
- `git diff --numstat` → 4 个 wheel 与 BPE 缓存均为 `-	-`（git 识别为二进制），`requirements.txt` 为 `17	0`。

## 6. PR 与后续

- 分支 `ppt-master-tiktoken-sandbox`，基于 main `32214615`，独立于 #27/#34 栈；因触及 `docker/`、`skills/`、`docker/sandbox/cache/**`（不可直推核心路径 + 二进制禁区），走分支 + PR + OCR；含二进制禁区改动的提交带 `[binary-zone-ok]`。
- CI/OCR 结果与合并状态以 PR 的 checks 与评论为准，不回填本文件（回填会再触发一轮评审）。
- 后续（均需用户授权）：从基座完整重建并复跑冒烟；部署对齐线上 `cee995a7`；付费 P01 端到端复测，同时验收首读修复（#34）、沙箱精确计数（本批）与 #27 合并门槛。
