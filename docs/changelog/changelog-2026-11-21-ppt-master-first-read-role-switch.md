# ppt-master flat Executor role-switch 首读修复

> **状态**：静态修复与定向验证已完成；第二次付费 P01 行为复测待执行，不宣称首读行为已关闭。
> **TL;DR**：三合一复测发现 `executor-base.md` 仍在执行包之前全量读取；根因是 `SKILL.md` 的通用 role-switch 在 Step 6 加载前触发，Step 6-only 禁令来得太晚。修复 = 在 `SKILL.md` 全局入口加入 flat Default/Beautify Executor 例外，先加载 Step 6（若未保留），再生成并读取当前页执行包；Quick 与非 flat 不变，`SEED_VERSION=2026-11-21.1`。

## 1. 行为失败与根因

- 验证 HEAD `dd916e8f` 同时包含 #27、#34、#35，冻结输入 SHA=`16f7f0dbf06a559b8889333a0eae9a80349d39d92132efae7a369fe50ea9ff24`，thread=`02977564-0703-428a-9843-73521a737337`。
- 全局 call 100 无界读取 `references/executor-base.md`；call 101 才读取 `generate-pptx.md` Step 6；call 104 才首次运行 `execution-context`。因此 #34 的 Step 6 内禁令虽然静态存在，却在模型进入该步骤前无法约束通用 role-switch。
- 同次运行的其他门槛通过：P01 为 `01_封面.svg`（2057 bytes、XML 可解析），执行包 `token_status=exact`、`tokens.total=11992`；1616 个 checkpoint 中 13 个有效摘要、零占位，最终确认保留且无 Stage 1/2 重问。

## 2. 修复

- `skills/public/ppt-master/SKILL.md`：在通用 role-reference 读取之前加入 **Flat Default / Beautify Executor exception (P31)**。flat 路线不得在 role switch 全量读取 `executor-base.md` / `shared-standards-core.md`；Step 6 未保留时先加载 Step 6，再生成并读取当前页执行包。Quick Generate 与非 flat 仍走原规则。
- `TRAINERAI_PATCHES.md`：P31 登记 `SKILL.md`、真实失败调用序列与源头修复；行为仍标为尚未验证。
- `SEED_VERSION` → `2026-11-21.1`，确保部署后 reseed 运行时技能。
- 回归测试钉住：例外位于通用 role-switch 之前，且包含 flat 判定、核心读取禁令、Step 6 加载顺序、执行包替代与 Quick/non-flat 边界。

## 3. 验证边界

- 静态断言只证明源头文案与顺序存在，第二次付费 P01 复测仍是行为合并门。
- 不修改执行包生成代码、summarization、System Prompt、SOUL、Quick 或非 flat 路线；不部署、不做 46 页回放。

## 验收记录

- 新 role-switch 回归（修复前）→ `1 failed`，缺 `Flat Default / Beautify Executor exception (P31)`。
- 同一回归（修复后）→ `1 passed`。
- P31 登记与 seed 版本测试（修改前）→ `2 failed`：P31 缺 `SKILL.md`，版本仍为 `2026-11-20.1`。
- `PYTHONPATH=. uv run pytest tests/test_ppt_master_execution_context.py tests/test_seed_agents.py -q` → `94 passed`。
- `PYTHONPATH=. uv run pytest tests/test_ppt_master*.py tests/test_seed_*.py tests/test_sandbox_read_file_range.py tests/test_skillscan_native.py -q` → `374 passed`。
- fresh home reseed → `SEED_VERSION 2026-11-21.1 seeded 6`，六个运行时邮票均为 `2026-11-21.1`。
- 第二次付费 P01 行为复测 → 待执行。
