# ppt-master flat Executor 首读修复（Step 6 → role-switch → 持久 SOUL）

> **状态**：两次付费复测均产出 P01 且摘要健康，但首读项均未通过；最终 SOUL 持久化修复的静态与全量门禁通过，未做第三次付费，行为尚未验证。按用户“修完直接合并”决策处置。
> **TL;DR**：Step 6-only 与 `SKILL.md` role-switch 例外都在长上下文中来得太晚：模型先读核心，之后才加载对应规则。最终修复把同一 flat 首读阻塞提升到始终加载的 `SOUL.md` D3，并保留 `SKILL.md` 入口规则；Quick 与非 flat 不变，`SEED_VERSION=2026-11-21.2`。

## 1. 第一次三合一复测

- HEAD `dd916e8f` 同时包含 #27、#34、#35；输入 SHA=`16f7f0dbf06a559b8889333a0eae9a80349d39d92132efae7a369fe50ea9ff24`；thread=`02977564-0703-428a-9843-73521a737337`。
- 全局 call 100 无界读取 `executor-base.md`，call 101 才读取 Step 6，call 104 才生成执行包。
- P01=`01_封面.svg`（2057 bytes、XML 可解析）；执行包 `token_status=exact`、`tokens.total=11992`；1616 个 checkpoint、13 个有效摘要、零占位，最终确认保留且无重问。

## 2. role-switch 入口修复

- `SKILL.md` 在通用 role-reference 读取之前加入 flat Default/Beautify Executor 例外：Step 6 未保留时先加载 Step 6，再生成并读取当前页执行包；Quick 与非 flat 不变。
- 新增静态回归、P31 登记，`SEED_VERSION=2026-11-21.1`；最终树 full backend `10174 passed, 89 skipped`，根 `make check` 通过。

## 3. 第二次付费复测

- HEAD=`37264287`，thread=`0ec9ebf0-73ce-4dd0-89d5-e1683d3043d6`，同一冻结输入、fresh home/thread、qwen3.8-flash。
- 结果 `PASS+EXERCISED` 仅表示 P01 已产出：P01=`01_封面.svg`（2666 bytes、XML 可解析），执行包 `token_status=exact`、`tokens.total=11252`、SHA=`f984b503…`；1820 个 checkpoint、16 个有效摘要、零占位，最终确认保留。
- **首读仍失败**：全局 call 138/147 无界读取两个核心，call 159 才首次生成执行包。call 137 宣布进入 Step 6，直到 call 156–158 才重新读取 Step 6；说明 `SKILL.md` 例外本身被压缩后无法约束 role-switch。

## 4. 持久 SOUL 修复与边界

- `backend/seed/agents/ppt-master/SOUL.md` D3 新增第 7 条：flat 规格对过门后，若 Step 6 不在有效上下文，第一动作是读取 Step 6，随后生成并读取当前页执行包；执行包读完前禁止完整核心、完整规格/锁和全 roster 批次，通用 role-switch 不得先读核心。
- `SEED_VERSION=2026-11-21.2`；fresh home 六个 agent 邮票均为 `.2`，运行时 SOUL 含新规则。
- 第二次复测后未再获得付费授权，因此 SOUL 修复只有静态/reseed/全量门禁证据，不宣称行为已验证；不部署、不做 46 页回放。

## 验收记录

- role-switch 新回归（修复前）→ `1 failed`，缺 flat Executor exception；修复后 → `1 passed`。
- P31 与 seed 首轮测试（修改前）→ `2 failed`；相关两文件（修改后）→ `94 passed`。
- 首轮相邻回归 → `374 passed`；首轮最终 rebase 树 full backend → `10174 passed, 89 skipped`。
- SOUL 持久化回归（修改前）→ `3 failed`：缺 D3 规则、P31 缺 SOUL、版本仍为 `.1`。
- SOUL 修改后相关两文件 → `95 passed`。
- fresh home reseed → `SEED_VERSION 2026-11-21.2 seeded 6`，`STAMPS_OK True count 6`，`SOUL_RULE_PRESENT True`。
- SOUL 最终树 `cd backend && make test` → `10175 passed, 89 skipped, 431 warnings in 148.26s`。
- SOUL 最终树 `make lint` + 根 `make check` + `git diff --check` → exit 0；Ruff `All checks passed!`、前端 `0 errors`、docs convention `OK`。
