# DeerFlow Harness 升级工程 P0：基线、目标与本地补丁迁移摸底

> **状态**：未执行（P0 只读研究已完成；升级实施与付费验证均未执行，待评审）；研究日 2026-11-22；本仓基准 `origin/main=0e84b1485f029d33b8ba57023003f6d4e39055c8`；上游基线 `8a3a309d1ce8bac8418251be29d4e4297a20c5eb`；2.3.0 动向观察点 `main=7b84fd09983e2a539f5d6be392d253b3e8b1889a`。
> **TL;DR**：本仓导入链可精确追溯到上游 2026-09-28 的 `8a3a309d`（当时 `2.2.0-dev`、config 50），不是某个 release tag；当前 harness 相对该基线已有 692 个差异路径，直接覆盖升级风险极高。建议“分阶段升级 + #27 行为矩阵先行 + 仅对上游仍失败的残余缺口上游化”，而不是整包覆盖或无限期摘樱桃。
> **For agentic workers**：本文仅为只读研究，不是升级、改代码/配置、部署或运行付费任务的授权。所有待实测项均明确标记，不得以本文推测替代后续验证。

**Goal**：固定本仓 harness 的上游起点与漂移规模，评估 2.1.0、2.2.0 及 2.3.0-unreleased 的影响，形成可评审的本地补丁迁移清单、路线选择、风险与工作量估算。

**范围**：只读检查 git/GitHub 历史、上游 release/changelog/源码与本仓代码；不改生产代码、配置、harness、数据库或部署，不运行模型任务，不读取真实用户数据。

---

## 一、基线定位：精确 commit 与漂移量

### 1.1 导入链与精确上游锚点

| 层级 | 锚点 | 已核实事实 |
|---|---|---|
| 当前本仓 main | `0e84b1485f029d33b8ba57023003f6d4e39055c8` | P0 研究基准；代码以该 commit 为准 |
| 本仓无历史导入 | `2f4848b840bd14b60511bb92d2b10f59b4f1a0db` | 2026-11-09 `feat: 导入 TrainerAI v2 开源基线`；提交明确来源为 TrainerAI2.0 `release/v2@3ad1afe67d1f1374b46b7ad7c5f9b581e2fb8c0b` |
| TrainerAI2.0 来源快照 | `3ad1afe67d1f1374b46b7ad7c5f9b581e2fb8c0b` | 其 `backend/packages/harness/deerflow` tree hash 为 `386ecde1ace5bb635daef4cc602fcc4eb467bcc9`，与本仓导入提交的同路径 tree **完全相同** |
| 上游精确基线 | **`8a3a309d1ce8bac8418251be29d4e4297a20c5eb`** | 2026-09-28；`backend/pyproject.toml` 为 `2.2.0-dev`，`config.example.yaml` 为 `config_version: 50` |

精确 SHA 不是按日期猜出的：

1. TrainerAI 历史计划记录了 2026-09-29 使用的上游只读快照，但当时快照目录没有 git 元数据，只确定 `2.2.0-dev / config 50`。
2. 上游当日候选 `ed09efdb7f3d13e4b700a98f8ba04f390ac48f5c` 与该快照的 harness 仅有 10 个差异文件。
3. GitHub compare 证明 `8a3a309d..ed09efdb` 正好有 11 个 commit，且 harness 变更文件集合正好就是这 10 个文件。
4. 逐一读取 `8a3a309d` 的 10 个文件并与快照做 SHA-256 比较，**10/10 完全一致**；因此快照内容与 `8a3a309d` 的 harness 精确对齐。
5. 上游 `v2.0.0=7e7f0410`、`v2.1.0=345f08be`、`v2.2.0=58fc5bd5` 的 harness tree 均不与 TrainerAI 快照相同，故不能把任一 tag 冒充精确导入基线。

### 1.2 漂移量化（目录级原始 diff）

比较根均为 `backend/packages/harness`，包含 Python、文档与 `pyproject.toml`；`git diff --no-index --numstat` 的原始结果如下：

| 比较面 | 差异文件 | 新增行 | 删除行 | 二进制 | 解释 |
|---|---:|---:|---:|---:|---|
| 上游 `8a3a309d` → 本仓导入 `2f4848b8` | **692** | **19,440** | **126,788** | 0 | 导入前 TrainerAI 已长期分叉；包含大量有意裁掉的上游功能 |
| 上游 `8a3a309d` → 当前 main | **692** | **19,686** | **126,788** | 0 | 当前累计原始差异；不是“本地新增代码净值”，也不是迁移工作量 |
| 本仓导入 `2f4848b8` → 当前 main | **8** | **306** | **60** | 0 | 导入后新增修改面，文件清单完全可枚举，见 §4.1 |

路径形态进一步分解：

- 上游基线有 **681** 个文件；导入快照与当前 main 均有 **291** 个文件。
- 上游 → 导入/当前均为 **451 删除、61 新增、180 修改**（合计 692）。
- 删除的 126,788 行大多反映 TrainerAI 主动删减的上游功能，**不能**解释成需要重写同等行数；但它意味着“直接拿上游目录覆盖本仓”会产生大规模功能消失。
- 导入后 8 个文件没有新增/删除路径，全部为修改；当前文件集合与导入快照一致。

结论：本仓不是“上游某 tag + 少量近期补丁”的简单形态，而是**历史裁剪 fork + 8 个近期 harness 修改文件**。后续必须维护路径级迁移 manifest，不能只处理最近 8 个文件后就宣称升级完成。

---

## 二、升级目标 2.1.0：指定 breaking changes 与本仓影响

一手来源：[v2.1.0 release（discussion #5832）](https://github.com/bytedance/deer-flow/discussions/5832)。该 release 自 2.0.0 起含 772 个合并 PR；PR 数仅说明变更规模，不直接换算本仓工作量。

| 上游变化 | 本仓已核实现状 | 影响与升级动作 | 待测 |
|---|---|---|---|
| **Trace ID 强制**：每个 Gateway 响应带 `X-Trace-Id`；run metadata 中的调用方 `deerflow_trace_id` 被覆盖，应使用请求头固定关联 ID（上游 PR #5119） | 本仓已有 `trace_context.py` 与 secret redaction，但没有发现当前 Gateway 发出 `X-Trace-Id` 的实现；`secret_context.py` 已假定该 ID 由服务端签发 | 高。升级会触达 Gateway 中间件/鉴权链、run metadata、日志与前端关联方式；不能把本仓 redaction 当成已具备完整 trace 功能 | 响应头、日志、持久化 run 三者一致性；CSRF/Auth/CORS 中间件顺序；重复 ID 与调用方伪造场景 |
| **`/mnt/skills` 保留为 managed enabled-only projection**；移除 `DEER_FLOW_HOST_SKILLS_PATH` / `SKILLS_HOST_PATH` 的旧用法，E2B 同路径 operator mount 跳过（PR #4178） | 本仓已经使用 `/mnt/skills`，但 compose、provisioner 与 AIO provider 仍显式依赖上述 host-path env；另有 custom/legacy/user 双层挂载 | 高。不能简单删 env；需要把 DooD 宿主路径解析、用户技能投影、legacy 只读兼容分别映射到上游 projection，避免禁用技能重新出现或宿主路径错挂 | public/custom/legacy/users 四层可见性；禁用技能 fail-closed；DooD 路径一致；容器内写入不能污染 canonical source |
| **Memory 配置重构**：`manager_class`、可插拔 backend、`backend_config`；`/memory/config` 改形状；`storage_path` 改为根目录，旧文件式值带 warning 丢弃（PR #4122 等） | 本仓 `MemoryConfig` 仍是 `storage_path/storage_class/debounce_seconds/model_name/...` 平铺，默认 `FileMemoryStorage` 且有自定义 per-user 隔离；`config.example.yaml` 仍写 `storage_path: memory.json` | 高。上游有旧字段迁移，但会丢弃文件式 `storage_path`；本仓“绝对路径退出用户隔离”的自定义语义不能静默丢失。需先决定迁移到 backend_config 还是保留显式兼容层 | 旧 memory.json 的实际落点、per-user 隔离、配置 API 形状、自定义 class 构造参数、启动 warning 与回滚；前端当前未发现直接消费 `storage_path` |
| **Checkpoint delta 频率**：`database.checkpoint_delta_snapshot_frequency` 移到 `database.checkpoint_delta.snapshot_frequency`，默认 1000→10（PR #4516） | 当前本仓没有 DeltaChannel 配置面；现有 `checkpoint_prune` 是另一套保留策略，不能混为一谈 | 中。若升级启用 delta，必须显式选择频率并做 replay 验证；本仓不存在需要保持的旧 delta cadence，不应为不存在的历史配置虚构迁移 | DeltaChannel replay、并发写入、长线程恢复、存储增长；与本仓 checkpoint prune 的交互 |
| **Docker 默认 loopback**：发布入口默认绑 `127.0.0.1`，需要显式 `BIND_HOST` 才暴露（PR #4618） | 本仓 `scripts/serve.sh` 与 compose 中仍有 `0.0.0.0`；部署模式本来就需要对外提供 nginx 入口 | 中高。默认收紧是安全改进，但生产/局域网部署必须显式配置；不能把容器内部 Gateway bind 与 nginx 对外发布混为一谈 | 本地/测试/公司服务器三种入口；非标准端口、代理头与 `BIND_HOST` 合法性；升级后是否会意外只监听回环 |

另外两项需要在 P1 清单中保留：

- `SKILL.md` 目录成为 runtime package 边界，嵌套 `SKILL.md` 不再独立注册（PR #4098）：本仓技能树需做嵌套清单与名称冲突检查，结果**待测**。
- E2B `sandbox.replicas` 容量限制开始生效（PR #4391）：当前主路径不是 E2B，但未来启用该 provider 时待测 wait/fail 行为，不能按 AIO 语义推断。

---

## 三、升级目标 2.2.0：breaking changes 与本仓影响

一手来源：[DeerFlow 2.2.0（discussion #7896）](https://github.com/bytedance/deer-flow/discussions/7896)。2.1.0→2.2.0 含 1068 个合并 PR；以下按本仓实际影响归组，不把 release 中与本仓无关的新功能写成既有回归。

| 变化组 | 本仓影响评估 | 必须验证/动作 |
|---|---|---|
| **本地 make dev/start 默认 loopback**（PR #6587/#6635） | 与 2.1 Docker 项叠加；本仓 `serve.sh` 仍固定 `0.0.0.0` | 明确区分开发默认与部署显式开放；非法 `BIND_HOST` 在服务停止前拒绝 |
| **凭据静态加密**：新增 env-only `DEER_FLOW_CREDENTIALS_KEY`；IM/MCP OAuth 凭据迁移到加密存储，多实例缺 key 拒绝启动（PR #6611/#7207/#6988） | 高。本仓已有 channels、MCP、secret redaction 与自有配置链，不能沿用旧明文回滚假设 | key 的注入/轮换/备份、首次明文导入、降级时重新配置、跨实例读取；全程不得在日志/测试中泄露真实凭据 |
| **多进程门禁更严**：`GATEWAY_WORKERS>1`、`deployment.multi_instance`、`WEB_CONCURRENCY` 要 Redis stream bridge；非 GitHub IM 要 leader lease | 高。本仓 `deps.py` 已读取 worker 信号，需与上游门禁合并而非保留两套互相矛盾的启动判断 | memory bridge 多进程必须失败；Redis 不可用 readiness；leader lease 丢失与恢复；worker drain |
| **Scheduler 契约变化**：结构化错误 envelope、每用户并发上限、DST 校验 | 当前本仓未发现上游 `/api/scheduled-tasks*` 专用路由与前端对应消费面；若引入应视为新功能面，不冒充既有回归 | 若启用 scheduler，再单列 API/前端/IM 验收；当前标为**待接入/待测** |
| **Thread 删除级联**：删除 thread 同时清 runs/run events/feedback（PR #5535） | 本仓已有自定义 owner/权限/数据删除逻辑，不能用上游路由覆盖 | 失败不得留下“元数据删了但历史仍可读”；重删、跨用户、checkpoint/store 局部失败、用户数据备份 |
| **历史 append-only / tool-output**：旧 write 内容 elision 默认关闭；`allowed-tools` 只作兼容 metadata | 会影响本仓技能与工具授权语义，且与 prompt cache 相关 | 旧请求前缀是否变化、allowed-tools 不再形成虚假安全边界、工具实际授权必须走真实 policy |
| **Middleware 工具授权**：`write_todos` 与 extension middleware tools 纳入 Layer-1 | 本仓自定义 tool groups 与中间件装配较多，升级后显式 policy 可能少绑定工具 | 逐工具 assembly/runtime 双向核对，尤其 plan/subagent/sandbox；空 allowlist 必须 fail-closed |
| **Fail-closed 默认值**：私有/loopback fetch、CGNAT、IM allowlist、外部 system/developer 消息、USER.md 用户隔离 | 与本仓自定义 provider、渠道、消息过滤链存在交叉 | 不能为通过旧测试而放宽上游安全默认；本地/内网确实需要时显式 allow_private_addresses |
| **Uploads/path 收紧**：Windows 保留名、重名不覆盖、Markdown companion、skipped_files 语义 | 本仓已有 no-follow、原子写、路径 containment 等自定义修复，需要按行为合并而非选一边覆盖 | symlink/硬链接、重名、转换失败、Windows 路径、部分成功响应 |
| **Slash menu 缩减**：只保留内置 `/goal`、`/compact`，技能改用 `@` 引用 | 与本仓技能激活/secret 注入和现有 composer 行为可能冲突 | 这是产品行为决策，需单独评审；若保留旧 slash 面，必须记录为有意本地差异 |
| **API 形状收紧**：4xx、wait error envelope、空 event filter、resume identity、SQLite file URI、bool-as-int、Responses API、CLI 非零退出 | 与本仓 FastAPI 错误 envelope、auth、provider wrapper 和脚本存在广泛接触面 | 运行契约测试，不靠前端静默修正；迁移已有错误处理与测试夹具 |

### 3.1 与 summarization 直接相关的非破坏但高风险变化

上游 2.2.0 不只是改阈值：摘要进入 `ThreadState.summary_text`，原始消息通道会 compaction，并由 durable context 投影 summary/goal/delegation/artifact 等信息；tool output 默认支持外置与有界 preview。本仓当前 #27 架构是“只替换单次 inference window，完整 state/checkpoint 保留”，两者**不是同构实现**。

因此：

- 不得直接 cherry-pick 本地 `summarization_middleware.py` 覆盖上游文件。
- 不得把本地 `summarization_checkpoint` 五字段或 skill rescue 内部状态当成升级后的稳定 API。
- 模式摘要研究已排在本升级之后；其 trigger/keep/trim 数值必须在新机制上重新标定。

---

## 四、2.3.0-unreleased 动向

一手来源固定为 [上游 main CHANGELOG](https://github.com/bytedance/deer-flow/blob/main/CHANGELOG.md)，观察 commit `7b84fd09983e2a539f5d6be392d253b3e8b1889a`（2026-11-22）。Unreleased 会继续漂移，后续实施必须重新 pin，不得把本快照当稳定 release。

| 动向 | 与本仓关系 | 升级策略 |
|---|---|---|
| **摘要 prompt 加强**（PR #8114）：保留活跃用户要求、字面批准、动作条件与显式修订；显式 `summary_prompt` 优先 | 与 #27 的“最新确认/占位”目标同向，但 prompt 改善不能替代机制测试 | 若本仓使用自定义 prompt，要检查是否遮蔽上游新增指导；把批准/修订保留纳入 #27 行为矩阵 |
| **DeltaChannel 依赖底线提高**（#8139）：`langgraph-checkpoint` 4.3.0、SQLite saver 3.1.2、Postgres saver 3.1.3 | 本仓 `langgraph>=1.1.9,<1.2`、saver 依赖与离线镜像交付均有约束 | 升级 lockfile 前做依赖兼容与离线构建；并发 replay/祖先分页不能只靠安装成功判定 |
| **AIO 带 per-call env 命令可跨 subagent 并发**，同一 subagent 内仍有序 | 与本仓 `aio_sandbox.py` 的 bash session 复用、锁、cwd 续接和失效重试直接相交 | 合并时不能同时保留两个互斥执行模型；需以并发、有序、env 隔离、cwd 与“只重试明确失效会话”为一套验收矩阵 |
| HITL Ask/Auto/Full、project memory、Queue/Steer 等持续扩张 | 与本仓 mode/tool policy/项目记忆可能有未来价值，但不是本批必须项 | 默认不追全部新功能；仅评估与安全/摘要/AIO 直接相关项 |

---

## 五、本地补丁迁移清单

### 5.1 导入后 8 个修改文件（完整枚举）

下表的行数是 `2f4848b8..当前 main` 的原始 numstat；迁移仍须结合导入前 fork 差异，不得只按行数判断复杂度。

| 文件 | + / - | 本地职责与迁移动作 |
|---|---:|---|
| `agents/middlewares/summarization_middleware.py` | 132 / 38 | #27 有效摘要源、占位淘汰、最新用户保留与日志；按 §5.2 做行为映射，禁止文件级覆盖 |
| `community/aio_sandbox/aio_sandbox.py` | 58 / 12 | bash.exec 按调用 env、session cwd 续接、明确失效才重试；与 2.3 AIO 并发改动共同评审 |
| `sandbox/tools.py` | 79 / 6 | required-secrets/宿主密钥按技能与用户白名单注入；优先复用上游 carrier，保留本地授权与 redaction 回归 |
| `skills/loader.py` | 12 / 2 | `strict_extensions_config` 与 fail-closed 配置读取；先核对上游是否已有同义开关，再决定保留薄参数或删除重复实现 |
| `subagents/executor.py` | 5 / 0 | 将子代理有效技能白名单写入 `metadata.available_skills`，约束密钥注入；需映射到上游 run context/metadata 白名单，不能新增旁路秘密载具 |
| `config/app_config.py` | 13 / 2 | 本仓模式配置与模型默认值接入；与上游模型管理/配置迁移合并，检查启动快照和热重载边界 |
| `config/model_config.py` | 5 / 0 | `modes` 等本仓模型字段；核对上游 ModelConfig 是否已有同义字段及 extra 字段泄漏防线 |
| `models/factory.py` | 2 / 0 | 本仓模式派生参数/provider 构造兼容；不得让本地 pricing/mode 字段进入 provider 请求体 |

### 5.2 #27 在上游升级中的专门合并策略

#27 实际状态以 [本仓 PR #27](https://github.com/littlebear2020/SuperTrainerAgent/pull/27) 为准；历史计划中的“未合并”只是旧状态快照。其 harness 核心修改即上表 `132 / 38`。

| #27 行为 | 上游 2.2/main 已核实情况 | 合并决定 |
|---|---|---|
| trim 空/退化时字符级尾部 fallback | 上游 2.2 改为按时间顺序的有界 chunks，覆盖所选 source，而非只取尾部 | 升级后优先退役本地 fallback；先用相同单行/多行/超长消息 replay 验证 chunk 覆盖与最大 64 chunk 失败路径 |
| 最新真实用户消息必须保留且仅一次 | 上游 2.2 已按最新真实用户 ID 保留，且 release 时间早于本仓 #27 | 视为已有上游等价覆盖；不得再上游提交重复补丁。需用 fresh/reuse、框架 Human、隐藏回复等本仓夹具复验 |
| 空摘要/失败不得推进 checkpoint | 上游 `_nonempty_summary` 返回 None，失败路径不生成 compaction result | 映射到新 `compact_state/acompact_state` 公共路径验证；不得直接比较旧私有 checkpoint 字段 |
| 两个精确历史占位句不得持久化/复用 | 上游代码仍能看到 `No previous conversation history.` 字符串，但 `_prepare_compaction` 会拦截空选区；是否有其他可达路径仍需测试 | **待测**。若上游公共链仍产出占位，则形成最小上游 PR；若不可达，记录为已覆盖而不制造 PR |
| 本地 inference-only window + 五字段 checkpoint | 上游已切换 ThreadState/durable-context 机制 | 不做文件级 cherry-pick；将行为测试迁移到新架构 |

**“先上游化再升级”的适用条件**：

1. 在固定上游 main 上运行同一组合成行为测试，证明公共路径确实失败。
2. 只上游化该残余行为，不带本仓 checkpoint、私有字段、seed、配置或技能假设。
3. 上游接受后升级到包含它的 commit；若暂未接受，则进入显式本地 patch manifest，并限制为薄适配层。
4. 若测试在上游通过，记录“已覆盖”，直接升级，**不为流程形式制造上游 PR**。

### 5.3 导入前 fork 漂移也必须入 manifest

原始差异最大的区域包括 runtime worker/manager、AIO provider、subagent executor、sandbox tools、lead agent、memory、extensions 与 summarization；此外有 451 个上游文件被裁掉。P1 必须机器生成完整路径 manifest，至少分四桶：

- `local-added`：本仓新增且有生产消费者；
- `local-modified`：需要行为映射/上游合并；
- `upstream-removed-intentionally`：本仓有意删减，升级时继续保持删除；
- `upstream-removed-incidentally`：原因不明，需要评审是否恢复。

本次没有为 692 个路径逐一伪造语义归属；这正是 P1 的明确工作，而不是 P0 可以省略的步骤。

---

## 六、三条路线对比、建议与工作量

以下均为**人日粗估**，假设 1 名熟悉本仓的工程师 + AI 辅助，包含代码合并、离线测试与评审，不包含等待上游审核、真实付费基准、生产灰度及不可预见的数据修复。

| 方案 | 做法 | 主要风险/代价 | 粗估工作量 |
|---|---|---|---:|
| **全量升级** | 直接以 2.2 或当前 main 为目标，一次性处理全部本地差异 | 一次性冲突面最大；692 差异路径与大规模功能裁剪使回归定位困难；容易“能启动但能力消失” | **12–20 人日**，且返工方差最大 |
| **分阶段升级** | 以 2.1、2.2、2.3 候选为可审查中间基线；每阶段冻结行为与测试；最终再决定是否部署最新 main | 总工序更多，但每次冲突、迁移与回滚边界清晰；中间 tag 可只作合并基线而不部署 | **16–26 人日**，风险最低 |
| **定向摘樱桃** | 仅移植 trace/security/checkpoint/AIO/summary 中的明确主题修复 | 短期最快、每包可独立验收；但 base 漂移继续扩大，未来总成本与重复冲突增加 | **3–8 人日/主题包**，不是完整升级 |

**推荐：分阶段升级为主，定向摘樱桃只用于有明确事故/安全压力的少数修复。**

推荐次序：

1. **P0（本文）**：固定基线与路线，已完成只读摸底。
2. **P1（1–2 人日）**：生成 692 路径 manifest；建立当前行为/测试清单；先跑 #27 与 AIO 的上游对照矩阵。
3. **P2（3–5 人日）**：以 2.1 为中间基线处理 trace、skills projection、memory、checkpoint 与 loopback；配置迁移与回滚必须同批设计。
4. **P3（4–7 人日）**：进入 2.2，处理 credentials、multi-instance、安全与 API 行为；迁移 durable summarization，不覆盖 #27。
5. **P4（3–5 人日）**：评估并选择性进入 2.3 候选；重新 pin main，处理 checkpoint 依赖与 AIO 并发；模式摘要研究在稳定基线上重新标定。
6. **P5（5–7 人日）**：全量回归、离线镜像、临时数据迁移演练、评审与分环境灰度；真实付费任务只在单独授权后运行。

该拆分与 16–26 人日估算是大区间计划，不是承诺工期；P1 若发现更多生产消费者，应先调整估算，而不是压缩验证。

---

## 七、风险清单与明确待测项

### 7.1 高风险

- **大规模功能裁剪**：451 个上游文件被删除的意图未逐条确认，升级不能自动恢复或再次删除。
- **秘密与凭据**：skills 密钥、MCP/IM 凭据、redaction、DooD host path 与上游加密迁移交织；任何真实值都不得进入测试、日志或 PR。
- **状态兼容**：checkpoint delta、summary_text、durable context、本仓 checkpoint prune 与旧 thread 数据需要迁移/回滚演练。
- **#27 回归**：上游已有大部分等价行为，但占位句可达性、reuse/fresh、AI/Tool 配对与最新用户仍须用公共路径验证。
- **AIO 执行模型**：2.3 并发改动与本地 session/cwd/重试补丁可能互相破坏，需合并后实测。

### 7.2 中风险

- 默认 loopback 导致部署入口不可达；多实例/Redis/leader lease 导致启动失败。
- Memory 文件式路径迁移导致旧事实不可见或用户隔离改变。
- 中间件工具授权、空 allowlist、技能 package 边界导致工具/技能意外消失。
- 本仓自定义 FastAPI/前端错误契约被上游形状改变。

### 7.3 明确待测（当前均未运行）

- 上游公共 compact 路径的 #27 全行为矩阵；占位句是否仍可达。
- 本仓现有技能树的嵌套 `SKILL.md`、public/custom/legacy/users 投影与禁用技能不可见性。
- SQLite/Postgres checkpoint replay、delta 并发、prune 与迁移回滚。
- 真实 AIO 并发、cwd、session 失效重试、env 隔离与命令不重复执行。
- 真实部署的 BIND_HOST、Redis、leader lease、credentials key 轮换与离线镜像。
- 真实模型质量、token/费用、p95 延迟与长任务性能；**本 P0 没有运行任何付费任务，也没有这些结论**。

---

## 八、证据索引与复核命令

### 8.1 上游一手资料

- [v2.1.0 release](https://github.com/bytedance/deer-flow/discussions/5832)
- [v2.2.0 release](https://github.com/bytedance/deer-flow/discussions/7896)
- [上游 main CHANGELOG（2.3.0-unreleased）](https://github.com/bytedance/deer-flow/blob/main/CHANGELOG.md)
- [v2.2.0 summarization_middleware.py](https://github.com/bytedance/deer-flow/blob/v2.2.0/backend/packages/harness/deerflow/agents/middlewares/summarization_middleware.py)
- [v2.1.0 memory_config.py](https://github.com/bytedance/deer-flow/blob/v2.1.0/backend/packages/harness/deerflow/config/memory_config.py)
- [2.2.0 durable_context_middleware.py](https://github.com/bytedance/deer-flow/blob/v2.2.0/backend/packages/harness/deerflow/agents/middlewares/durable_context_middleware.py)
- 上游基线 commit：[8a3a309d](https://github.com/bytedance/deer-flow/commit/8a3a309d1ce8bac8418251be29d4e4297a20c5eb)
- 2.3.0 观察 commit：[7b84fd09](https://github.com/bytedance/deer-flow/commit/7b84fd09983e2a539f5d6be392d253b3e8b1889a)

### 8.2 本仓证据

- 导入提交：`2f4848b840bd14b60511bb92d2b10f59b4f1a0db`
- TrainerAI 来源：`3ad1afe67d1f1374b46b7ad7c5f9b581e2fb8c0b`
- 当前模式摘要研究：`docs/plans/2026-11-21-mode-specific-summarization-plan.md`（已批准、未执行，排在本升级后）
- #27 历史计划：`docs/plans/2026-11-17-ppt-beautify-summarization-fix-plan.md`；当前合入状态以 PR #27 为准
- 当前代码：`backend/packages/harness/deerflow/`、`backend/app/gateway/`、`docker/`、`scripts/serve.sh`

### 8.3 可复核命令

```bash
# 本仓导入后 8 文件与精确行数
git diff --numstat 2f4848b8..0e84b148 -- backend/packages/harness

# 基线包解开后，目录级原始漂移（路径按本地解开位置调整）
git diff --no-index --numstat -- <upstream-8a3a309d>/backend/packages/harness <import-2f4848b8>
git diff --no-index --numstat -- <upstream-8a3a309d>/backend/packages/harness backend/packages/harness

# 文档门禁
python3 scripts/check_docs_convention.py
```

本 P0 的临时下载包、API 探针和 diff 脚本均为研究临时产物，不作为正式测试或长期依赖。
