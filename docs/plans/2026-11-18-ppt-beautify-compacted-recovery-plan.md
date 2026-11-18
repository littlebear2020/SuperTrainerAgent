# PPT 美化 compacted recovery 收窄修复计划

> **状态**：未执行（部分执行：静态门禁全绿；v1 `6616aa94` 与 v2 `0c942a8f` 两次授权 qwen 冒烟均 FAIL+EXERCISED、100 次调用无 P01，摘要零占位且无重问；prompt-only 收窄未成立，后续架构决策待定；PR A 保持未合并，PR B 不启动）。
> **TL;DR**：只收窄 ppt-master 在上下文压缩后的恢复读取，先查盘已有 SVG，再有界读取 `spec_lock.md`、当前页 §IX 与当前页所需模块章节；fresh/resumed/restarted/external 全量恢复、PR A summarization、压缩 trigger/cap 和模型配置均不变。影响面为 vendored 技能文档、补丁登记、SEED_VERSION 与测试；真实验收只到 P01。

## 1. 已确认根因与单一假设

PR A 真实冒烟已证明摘要健康、最终确认保留且没有重问，但 qwen 在最终确认后的 100 次工具调用中没有写出 P01。`generate-pptx.md` 把 compacted 与 fresh/resumed/restarted/external 合并为全量恢复，`failure-recovery.md` 重复同一要求；完整 `executor-base.md`（51KB）与 `shared-standards-core.md`（31KB）的反复读入又触发 32000-token 压缩，形成恢复—压缩循环。

**单一假设**：只要 compacted 分支不再要求全量重读，并明确先复用磁盘产物、仅恢复当前页所需上下文，qwen 就能在同一 PR A 框架代码和同一冻结输入上写出 P01。本批不同时调整 trigger、trim、skill rescue cap、模型或确认文本。

`read_file` 已确认支持独立或成对的 `start_line` / `end_line`（含截断后续读），因此可以通过章节标题定位后有界读取，不需要拆分或改写两个核心参考文件。

## 2. 评审决策记录

| 问题 | 决策 |
|---|---|
| 是否调整压缩 trigger/cap | 否。本批只改 compacted recovery，保持单变量。 |
| 是否保留完整 Design Spec 恢复 | compacted 且两份规划产物仍存在时不读完整 Design Spec，只读当前页 §IX 块；缺失或外部变化仍走原有完整恢复门。当前页正文与 `Relationships` 是 authoring 必需输入，不能以省 token 为由省略。 |
| 是否保留全部 triggered references | fresh/resumed/restarted/external 保留；compacted 只读当前页解析出的 mode/visual-style 与触发模块的必要章节，禁止完整核心文件和全 roster 模块集重读。 |
| 已完成 SVG | 先盘点 `svg_output/` 并检查非空，按 §IX 顺序定位首个缺失页；不得重写已完成非空页面，0 字节仍按 P22 停止。 |
| 计划拷问 | 这是已复现事故的明确小修，适用仓库「事故修复/明确小改」豁免，不另开功能决策树。 |
| Git 形态 | 从 PR A HEAD 建 `ppt-beautify-compacted-recovery`，新 PR 以 PR A 分支为 base，保证 PR diff 只含 authoring 修复；不修改 summarization 文件，不创建 PR B。 |

## 3. 文件范围

| 批次 | 允许路径（allow） | 禁改路径（ban） | 共享基座触达 |
|---|---|---|---|
| compacted recovery | `skills/public/ppt-master/workflows/generate-pptx.md`、`workflows/governance/failure-recovery.md`、`TRAINERAI_PATCHES.md`；`backend/app/gateway/seed.py`；`backend/tests/test_ppt_master_skill_vendored.py`、`test_seed_agents.py`；本计划与对应 changelog；`temp/` 下忽略的驱动和证据 | summarization 中间件及其测试、压缩配置、SOUL、核心 reference 内容、前端、docker、PR B 文件 | 以 PR A 分支为只读祖先；不与其他中间件或技能批并行 |

真实 PPTX、SQLite/checkpoint、日志、生成的 SVG/PPTX 与 `temp/` 证据均禁止入库。

## 4. 实施任务

### Task 1：先固定 compacted 恢复契约（RED）

- [x] 在 `test_ppt_master_skill_vendored.py` 增加表格行级回归：compacted 必须先盘点 `svg_output/`、定位首个缺失页、读取 `spec_lock.md` 与当前页 §IX/必要模块章节，并显式使用有界读取；完整 Design Spec、完整核心文件和全 roster 触发集不得作为该分支的恢复要求。
- [x] 对 `failure-recovery.md` 增加等价断言，防止治理矩阵继续要求 compacted 全量恢复；同时断言 fresh/resumed/restarted/external 的完整恢复仍保留。
- [x] 先把 `SEED_VERSION` 期望改为 `2026-11-18.1`，运行定向测试并记录因旧合并行、旧治理行和旧版本号产生的预期 RED；不得通过放宽断言转绿。

### Task 2：最小技能补丁、登记与版本 bump（GREEN）

- [x] 在 `generate-pptx.md` 将 compacted 拆成独立行：先查盘已有非空 SVG，再读 `spec_lock.md`、当前页 §IX 与当前页所需模块章节；用 `start_line`/`end_line` 有界读取，明确禁止全量恢复循环。其他上下文类型不变。
- [x] 在 `failure-recovery.md` 同步独立 compacted 行和相同边界，不改变缺失规划产物、外部变化与 0 字节 SVG 的既有门禁。
- [x] 在 `TRAINERAI_PATCHES.md` 登记下一个补丁号 P30，记录文件、行为偏离和本次真实事故证据；不改两个核心 reference 的组织或内容。
- [x] 将 `SEED_VERSION` bump 为 `2026-11-18.1`，更新版本注释和精确版本测试；运行 Task 1 同一定向命令确认 GREEN。

### Task 3：fresh home reseed 与未付费门禁

- [x] 复用 `temp/qwen-smoke/driver.py`，为本次独立 evidence/home 增加环境变量覆盖，避免覆盖 PR A 证据；preflight 从实际 `SEED_VERSION` 校验运行时邮票，不再硬编码 `.4`。
- [x] 以 fresh `DEER_FLOW_HOME` 执行 preflight，确认实际 reseed、运行时邮票 `2026-11-18.1`、冻结输入 SHA-256=`16f7f0dbf06a559b8889333a0eae9a80349d39d92132efae7a369fe50ea9ff24`、7 页与 P03 原生表格、qwen3.8-flash、token budget disabled、recursion limit 2400；此步不得发起模型调用。
- [x] 跑 vendored/seed 定向与相邻回归、`git diff --check`、docs convention；依次后台运行 `cd backend && make test`、`make lint` 和根 `make check`，全部通过才进入真实验证。

### Task 4：用户授权后的同输入 qwen P01 冒烟

- [x] 启动任何付费调用前，向用户报告免费门禁结果并取得明确授权；未授权则停在此处。
- [x] 用 fresh home/thread/project/sandbox、同一冻结输入和 qwen3.8-flash 完成正常 Stage 1/2 确认；最终确认采用模型推荐项，不提供内容、布局或工具顺序纠正。
- [x] 最终确认后监控 P01、100 次真实工具调用、20 分钟无新产物、新占位/空 checkpoint 和 Stage 1/2 重问；P01 非空即停，不要求完整 PPTX。**首次结果：FAIL+EXERCISED，100 次调用无 P01，零占位、无重问。**
- [x] 审计 checkpoint 与真实工具参数。**实际：13 个有效摘要、`bad_count=0`、确认保留；首次 Step 6 批读在压缩后仍执行完整核心与全 roster 模块，后段才按 compacted 行查盘并有界读取 §IX。**

### Task 4A：补 compacted 对首次 Step 6 批读的优先级（首次 FAIL 证据开启）

- [x] 先增加失败回归：压缩发生在首次 Step 6 读批次之前或之中时，compacted 行必须优先，禁止重启或补完全量核心/全 roster 批次。
- [x] 在 `generate-pptx.md` 初始读批次前增加显式优先级，并同步 `failure-recovery.md` 与 P30 登记；不改 trigger/cap、不把一般条件模块改成按页触发，沿用同一未发布 `SEED_VERSION=2026-11-18.1`。
- [x] 复跑定向/相邻回归和完整免费门禁。任何第二次付费复测必须再次取得用户明确授权，并使用新的 fresh home/thread；首次失败证据不得覆盖。

### Task 4C：第二次授权复测与停止边界

- [x] v2 使用独立 fresh home/thread、同一输入与 qwen，正常 Stage 1/2 后执行最终监控；模型未给推荐方向，按首个列出项选方向 1，默认生产参数。
- [x] 完成 checkpoint 与真实工具参数审计：**FAIL+EXERCISED，100 次调用无 P01；14 个有效摘要、零占位、无重问。首次全量核心读仍存在，后续压缩还用 `bash sed/cat` 继续读取核心大段，显式优先级未形成行为保证。**
- [ ] P01 成功标准仍未满足。停止相似 prompt 叠加；确定性恢复产物/执行机制或首次 Step 6 读取架构须另行评审，第三次实现与付费运行需新的决策和显式授权。

### Task 5：记录、stacked PR 与 OCR

- [x] 新建 `docs/changelog/changelog-2026-11-18-ppt-beautify-compacted-recovery.md`，含 TL;DR、RED→GREEN、reseed 与真实行为结果、`## 验收记录`；回填本计划勾选和唯一状态头，不宣称完整 7 页或 46 页通过。
- [ ] 提交单主题 commits，确认 summarization diff 为零、真实证据未入库；push `ppt-beautify-compacted-recovery`，以 PR A 分支为 base 开 PR，正文链接计划/changelog 并说明 stacked 关系。
- [ ] 等待 AI/OCR 与 CI，处理所有 Critical/Important；PR A 保持未合并、PR B 不启动。authoring PR 的合入与 PR A 后续推进在 P01 结果和评审齐备后按用户门槛决定，不部署。

## 5. 完成定义与边界

- 规则回归先 RED 后 GREEN，两个工作流文件对 compacted 的口径一致，其他恢复类型不回退。
- P30 登记、`SEED_VERSION=2026-11-18.1`、fresh home reseed 和运行时邮票一致。
- 免费全量门禁绿；付费运行经用户单独授权。
- 成功只表示同一 qwen + 同一 7 页输入在最终确认后写出非空 P01，且摘要健康、无重问、无完整核心文件重读循环；不表示完整 PPTX、7 页或 46 页交付通过。
- 不改 PR A summarization 修复代码，不启动 PR B，不部署。
