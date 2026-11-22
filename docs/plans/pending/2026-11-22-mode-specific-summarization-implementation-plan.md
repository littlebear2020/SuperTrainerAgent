# 按模式分化 Summarization 配置能力实施计划

> **状态**：未执行；定稿日 2026-11-22；基准 commit `0e84b1485f029d33b8ba57023003f6d4e39055c8`（当时 `origin/main`）。从 `8c78dace` 到该基准的 12 个提交全部属于个人用量功能，未触及 summarization、harness 或 `config.example.yaml`。执行前若基准前进，先重查漂移。
> **TL;DR**：交付 `summarization.modes.{flash,standard,ultra}` 的可配置能力，复用已透传的 runtime `mode`，不改前端、模型花名册或 `/models`。出厂模板使用 `modes: {}`，因此本批合入后所有现有运行仍走全局 32k / 10 messages / 30k；24k/32k/48k 等只作测试夹具，绝不随 `config-upgrade` 自动启用。结构版本 32→33，严格 RED→GREEN、全量测试、分支 + PR；实际阈值启用另过基准与用户验收门。
> **For agentic workers**：REQUIRED SUB-SKILL：默认使用 `executing-plans` 单执行者串行完成 Task 0→5，每一步先读勾选状态；代码任务必须先写失败测试并保留 RED 证据，再最小实现。必须从最新 `origin/main` 建独立 worktree/分支，不直推 main，不部署，不动价格、密钥或付费任务。

**Goal**：让 lead-agent 能按显式 `flash/standard/ultra` 选择摘要 `trigger/keep/trim_tokens_to_summarize`，同时保证空配置与旧客户端行为逐字不变，为后续受控标定提供安全开关。

**Architecture**：在现有 `SummarizationConfig` 下增加可选 mode policy 字典，以纯函数返回带有效三字段的配置副本；lead-agent 装配只新增一个 `mode` 参数，其余摘要模型、reuse、skill rescue、checkpoint 与中间件顺序全部保持不变。配置模板只增加空字典与注释，不引入候选阈值。

**Tech Stack**：Python 3.12、Pydantic 2、LangChain 1.2.15、现有 DeerFlow harness、PyYAML、pytest；不新增依赖，不改前端。

**Spec**：`docs/plans/pending/2026-11-21-mode-specific-summarization-plan.md`，重点阅读 §2–§8。研究中的候选数值不是本批上线值。

## Global Constraints

- 本批交付**配置能力，不启用三档不同默认值**；`config.example.yaml` 的活跃值必须是 `summarization.modes: {}`。
- runtime `mode` 是唯一分档依据；禁止按 `model_name` 反推，Claude 与 DeepSeek 的跨档花名册保持不变。
- mode 缺失、未知或该 mode 未配置时，完整沿用现有全局策略；不得默认套 standard override。
- **本批不做旧模式别名**：前端 `normalizeMode` 虽会把 `thinking/pro` 迁成 standard，后端 resolver 仍把 `thinking`、`pro`、`FLASH` 视为未知并回全局。直连 API 是否补别名属于后续启用门的独立产品决策，不得由 schema 测试悄悄改变。
- `DeerFlowClient._get_runnable_config` 与 agent 缓存键本批不增加 mode；该入口继续走全局策略，禁止顺手修改 `client.py`。
- 只覆盖 `trigger`、`keep`、`trim_tokens_to_summarize`；`enabled/model_name/resummarize_*/preserve_recent_skill_*/skill_ledger_*` 必须继承，档内重复声明这些字段应校验失败。
- 不改 `ModelConfig.modes`、`mode_defaults`、前端提交、`/models`、数据库、摘要 prompt、摘要模型选择、中间件顺序或 tool-output 行为。
- 不 backport 上游 2.2.0 durable context，不升级 LangChain/LangGraph/harness，不运行 `RUN_LIVE=1`。
- 结构性配置变更只改 `config.example.yaml` 并递增到 33；本地配置用 `make config-upgrade`，不得手改 `config.yaml`。
- 回滚配置为 `summarization.modes: {}`；代码回滚即恢复单全局策略。

## Review Focus

1. **旧客户端没有 mode**：应返回原全局配置对象，不复制出意外默认值；由 Task 1 `test_missing_or_unknown_mode_returns_base_unchanged` 与 Task 2 装配测试共同钉死。
2. **同一模型跨档**：同一 `claude-sonnet-5-5` 在 standard/Ultra 应服从显式 mode，而非模型推断；由 Task 2 `test_explicit_mode_wins_for_shared_model` 钉死。
3. **空 modes 的全局 null trim**：继承时不能把 `null` 变成代码默认 30k 或覆盖值；由 Task 1 `test_empty_modes_preserves_global_null_trim` 与 Task 2 kwargs 测试钉死。
4. **原有 pair/reuse/rescue 行为**：现有 `test_summarization_middleware.py` 证明默认参数下的 AI/Tool、latest user、skill rescue、frozen cutoff 与失败 pass-through 零回归；本批不为三档另铺一套中间件矩阵，由 Task 2 运行该文件回归钉死。
5. **升级脚本不得注入实验阈值**：v32 本地配置升级后只能新增空 `modes`，不得改原 trigger/keep/trim、价格或密钥；由 Task 3 迁移断言钉死。

---

## 文件地图

| 文件 | 责任 |
|---|---|
| `backend/packages/harness/deerflow/config/summarization_config.py` | 新 policy 类型、`modes` 字段与纯解析函数 |
| `backend/packages/harness/deerflow/agents/lead_agent/agent.py` | 从既有 runtime config 取 mode，传入摘要工厂 |
| `backend/tests/test_summarization_mode_policy.py` | 新增 schema、继承、非法输入与三档解析测试 |
| `backend/tests/test_lead_agent_model_resolution.py` | 新增 runtime mode 转发、共享模型与工厂 kwargs 测试 |
| `backend/tests/test_summarization_middleware.py` | 复用既有行为回归；只有确有必要才增加有效 policy 的构造断言 |
| `backend/tests/test_model_modes_config.py` | 将示例/本地配置版本断言 32 更新为 33 |
| `backend/tests/test_360_new_models_roster.py` | 同步第 306–330 行两处版本断言与版本来历注释，避免全量套件仍钉 32 |
| `config.example.yaml` | `config_version: 33` 与 `summarization.modes: {}` |
| `docs/plans/pending/2026-11-22-mode-specific-summarization-implementation-plan.md` | 执行时勾选并移回 `docs/plans/` |
| `docs/changelog/changelog-YYYY-MM-DD-mode-specific-summarization.md` | 按实际执行日记录验收证据与“未启用候选阈值”边界 |

---

### Task 0：最新基线、独立 worktree 与本地配置入口

**Files**：只创建 worktree/分支和被 gitignore 的配置符号链接；本任务不改受控文件。

- [ ] **Step 1：检查远端与研究漂移**

Run:

```bash
git fetch origin main
git diff --stat 0e84b1485f029d33b8ba57023003f6d4e39055c8..origin/main
git diff --name-only 0e84b1485f029d33b8ba57023003f6d4e39055c8..origin/main -- backend/packages/harness config.example.yaml frontend/src/core/threads/hooks.ts backend/app/gateway/services.py
```

Expected：若第二组输出非空，暂停执行并复核本计划接口；不得无视漂移照抄。确认 worktree 总数符合仓库卫生要求，无在途同名分支。

- [ ] **Step 2：从最新 main 建独立 worktree**

Run:

```bash
git worktree add ../TrainerAI2.0-agent-mode-specific-summarization -b mode-specific-summarization origin/main
git -C ../TrainerAI2.0-agent-mode-specific-summarization status --short --branch
```

Expected：工作区干净，HEAD 等于执行时 `origin/main`；主 clone 保持 main。

- [ ] **Step 3：把 worktree 的本地配置入口链接到既有主仓配置**

在主 clone 已有 `config.yaml` 时执行；若不存在，不得让脚本静默生成一份新配置，应停止并由用户决定配置入口。

```bash
test -f "$PWD/config.yaml"
ln -s "$PWD/config.yaml" ../TrainerAI2.0-agent-mode-specific-summarization/config.yaml
```

Expected：后续 `make config-upgrade` 使用真实既有配置；全程不读取、打印或复制其中的密钥。

- [ ] **Step 4：切换并锁定后续工作目录**

```bash
cd ../TrainerAI2.0-agent-mode-specific-summarization
pwd
git status --short --branch
```

Expected：当前路径为刚创建的 worktree，分支为 `mode-specific-summarization`。从 Task 1 起，本文所有命令的工具 cwd 都设为 `../TrainerAI2.0-agent-mode-specific-summarization`（相对主 clone；后续 shell 已切目录时使用该绝对路径）；主 clone 只读，不得在其中提交 Task 1/2/3 的改动。

### Task 1：Policy schema 与纯解析函数（RED→GREEN）

**Files**：
- Modify: `backend/packages/harness/deerflow/config/summarization_config.py`
- Create: `backend/tests/test_summarization_mode_policy.py`

**Interfaces**：

```python
class SummarizationModePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    trigger: ContextSize | list[ContextSize]
    keep: ContextSize
    trim_tokens_to_summarize: int = Field(strict=True, ge=1)

class SummarizationConfig(BaseModel):
    # existing fields unchanged
    modes: dict[ModeName, SummarizationModePolicy] = Field(default_factory=dict)

def resolve_summarization_config(
    config: SummarizationConfig,
    mode: str | None,
) -> SummarizationConfig: ...
```

`resolve...` 在无有效 override 时返回原对象；有 override 时用 `model_copy(update=...)` 只替换三字段，不修改输入对象，不重新选择摘要模型。

`SummarizationModePolicy` 对档内 `trigger/keep` 增加 **before validator**，在现有全局 `ContextSize` 转换前检查原始 mapping：`tokens/messages` 必须是非布尔正整数，`fraction` 必须是非布尔有限数且位于 `(0,1]`（整数 `1` 合法）；拒绝 `True`（当前 `ContextSize` 会把它转成 `1`）、`1.0` tokens/messages、0、负数、NaN/Inf，空 trigger 列表也拒绝。只改 policy 校验，不给全局 `ContextSize` 新增 validator。

validator 的形状分支必须明确：单个 `ContextSize` 实例直接返回；非空且全为 `ContextSize` 的列表直接返回；原始 dict 或 dict 列表逐项执行上述严格检查；空列表先于 `all(...)` 判断拒绝，混合对象/dict 或其余类型交给 Pydantic 报形状错误。新增 `test_policy_accepts_prebuilt_context_size_objects` 覆盖合法对象及对象列表，非法数值测试使用原始 dict，避免已转换对象绕过 before 阶段后产生假证据。

- [ ] **Step 1：先写 schema 与继承失败测试**

在 `test_summarization_mode_policy.py` 写以下测试，fixture 明确使用全局 `trigger=32000`、`keep=messages:10`、`trim=30000`，override 使用研究候选值仅作测试数据：flash=24000/tokens:4000/16000，standard=32000/tokens:6000/30000，Ultra=48000/tokens:8000/48000。

```python
def test_empty_modes_preserves_global_null_trim() -> None:
    base = SummarizationConfig(trim_tokens_to_summarize=None)
    assert resolve_summarization_config(base, "flash") is base
    assert base.modes == {}


def test_missing_or_unknown_mode_returns_base_unchanged() -> None:
    # base 带 flash override；mode=None、"thinking"、"FLASH" 仍返回 base。


def test_resolves_each_mode_without_mutating_base() -> None:
    # 三档分别断言有效 trigger/keep/trim；base 仍为 32000/messages:10/30000。


def test_override_preserves_unrelated_global_knobs() -> None:
    # model_name、reuse、skill rescue、ledger 与 enabled 全部等于 base。
```

另写以下 schema 测试，不再增加实现 transcript：

- `test_unknown_mode_key_rejected`：`modes={"thinking": ...}` 产生 `ValidationError`。
- `test_boolean_trim_rejected` / `test_non_positive_trim_rejected`：`True/0/-1` 被拒绝。
- `test_policy_requires_complete_entry`：只给 trigger、缺 keep、缺 trim 均失败。
- `test_unconfigured_mode_returns_base`：只有 flash 时 standard 返回原对象。
- `test_null_modes_rejected_but_missing_modes_defaults_empty`：`modes: null` 失败，省略该键得到 `{}`。
- `test_policy_forbids_extra_or_global_only_fields`：未知拼写、档内 enabled/model_name 均失败。
- `test_policy_context_sizes_are_strict`：trigger 与 keep 分别覆盖非正数、布尔、tokens/messages 浮点、fraction 越界及 NaN/Inf、空 trigger 列表；合法标量、单元素列表和 fraction=1 均通过。
- `test_explicit_alias_policy_is_not_implicitly_added`：`thinking/pro/FLASH` 均回全局，不把前端 normalizeMode 语义复制到后端。

再新增生产装载往返 `test_app_config_file_loads_mode_policy_into_singleton`：照 `backend/tests/test_app_config_reload.py` 的骨架，用 `tmp_path` 写最小 YAML，必须包含 `sandbox.use: deerflow.sandbox.local:LocalSandboxProvider` 和仅含 flash override 的 summarization 段；另写 `{"mcpServers": {}, "skills": {}}`，用 `monkeypatch.setenv("DEER_FLOW_EXTENSIONS_CONFIG_PATH", ...)` 指向该临时 JSON。调用真实 `AppConfig.from_file`，然后断言 `get_summarization_config()` 含该策略、`resolve_summarization_config(..., "flash")` 返回有效值，且输入 `AppConfig.summarization` 与 singleton 的 `model_dump()` 在 resolve 前后不变。不得 monkeypatch `_apply_singleton_configs` 或 loader。

`AppConfig.from_file` 会刷新 `_apply_singleton_configs` 的全部状态，所以 `finally` 只恢复 summarization 不合格。调用前记录、结束后逐项恢复以下 11 组；没有 setter 的使用对应 loader，并传之前对象的 `model_dump()`：

| 状态 | 调用前 getter | finally 恢复入口 |
|---|---|---|
| title | `get_title_config` | `set_title_config(previous)` |
| summarization | `get_summarization_config` | `set_summarization_config(previous)` |
| memory | `get_memory_config` | `set_memory_config(previous)` |
| agents_api | `get_agents_api_config` | `set_agents_api_config(previous)` |
| subagents | `get_subagents_app_config` | `load_subagents_config_from_dict(previous.model_dump())` |
| tool_search | `get_tool_search_config` | `load_tool_search_config_from_dict(previous.model_dump())` |
| guardrails | `get_guardrails_config` | `load_guardrails_config_from_dict(previous.model_dump())` |
| loop_detection | `get_loop_detection_config` | `set_loop_detection_config(previous)` |
| checkpointer（包括 None） | `get_checkpointer_config` | `set_checkpointer_config(previous)` |
| stream_bridge（包括 None） | `get_stream_bridge_config` | `set_stream_bridge_config(previous)` |
| ACP agents | `get_acp_agents` | `load_acp_config_from_dict({name: cfg.model_dump() for ...})` |

恢复后断言所有 getter 的当前值与调用前快照相等，尤其 checkpointer/stream bridge 的 None 不得被默认值替换；环境变量由 monkeypatch 自动恢复。

- [ ] **Step 2：运行新测试，确认 RED**

Run: `cd backend && PYTHONPATH=. uv run pytest tests/test_summarization_mode_policy.py -q`

Expected：因 `SummarizationModePolicy` / `resolve_summarization_config` 不存在而 collection/import FAIL；不得先实现再补写“失败测试”。

- [ ] **Step 3：实现最小 schema 与纯函数**

在 `summarization_config.py` 导入 `ModeName` 与 `ConfigDict`，按上述签名增加类型、字段、档内 before validators 与 resolver。resolver 仅接受 `flash/standard/ultra` 且该 key 已配置时复制三字段；其余情况返回 `config`。不添加 mode 到 provider kwargs，不改全局 singleton loader 或全局 `ContextSize`。

- [ ] **Step 4：运行新测试，确认 GREEN**

Run: `cd backend && PYTHONPATH=. uv run pytest tests/test_summarization_mode_policy.py -q`

Expected：全部 PASS；特别确认 null trim 与未知 mode 没有走 model_copy 默认值。

- [ ] **Step 5：提交独立 schema 变更**

```bash
git add backend/packages/harness/deerflow/config/summarization_config.py backend/tests/test_summarization_mode_policy.py
git commit -m "feat(summarization): 增加三档策略配置与纯解析"
```

### Task 2：Runtime mode 装配与行为回归（RED→GREEN）

**Files**：
- Modify: `backend/packages/harness/deerflow/agents/lead_agent/agent.py:60-126,254-299`
- Modify: `backend/tests/test_lead_agent_model_resolution.py`
- Test: `backend/tests/test_summarization_mode_policy.py`
- Test: `backend/tests/test_summarization_middleware.py`

**Interfaces**：

```python
def _create_summarization_middleware(
    *,
    app_config: AppConfig | None = None,
    mode: str | None = None,
) -> DeerFlowSummarizationMiddleware | None: ...
```

内部第一行有效配置改为 `resolve_summarization_config(get_summarization_config(), mode)`。`_build_middlewares` 在创建摘要前取得 `cfg = _get_runtime_config(config)`，传 `mode=cfg.get("mode")`；后续 plan/subagent 继续复用同一个 cfg，不重复读取。

- [ ] **Step 1：先写工厂与 runtime 转发失败测试**

在 `test_lead_agent_model_resolution.py` 复用现有 fake model / app_config helper，新增：

```python
def test_summarization_factory_uses_effective_mode_policy(monkeypatch) -> None:
    # 按 test_create_summarization_middleware_uses_configured_model_alias 的做法，
    # monkeypatch get_summarization_config 返回带 modes 的 config，并捕获 middleware kwargs。
    # flash override 的 trigger 写成单元素列表，断言 kwargs trigger=[("tokens", 24000)]、
    # keep=("tokens", 4000)、trim=16000；另一用例用标量 trigger，断言得到单个 tuple。


def test_build_middlewares_forwards_explicit_mode(monkeypatch) -> None:
    # configurable 与 context 两层分别驱动；断言工厂收到 flash/standard/ultra。
    # context.mode 覆盖 configurable.mode，沿用 _get_runtime_config 既有优先级。


def test_explicit_mode_wins_for_shared_model(monkeypatch) -> None:
    # 同一 model_name="claude-sonnet-5-5" 分别传 standard/ultra，捕获到不同有效 policy；
    # 不得出现按 model_config.modes 自动选择的分支。


def test_missing_mode_keeps_global_summarization_kwargs(monkeypatch) -> None:
    # 缺 mode 时仍为 32000/messages:10/30000；全局 trim=None 时 kwargs 不传 trim。
```

- [ ] **Step 2：运行定向测试，确认 RED**

Run: `cd backend && PYTHONPATH=. uv run pytest tests/test_lead_agent_model_resolution.py tests/test_summarization_mode_policy.py -q`

Expected：新增断言失败，或 `_create_summarization_middleware` 收到意外 `mode` 参数；既有测试应保持通过，失败必须来自新增能力缺口。

- [ ] **Step 3：实现最小装配修改**

只修改上述签名、有效 config 解析和 `_build_middlewares` 传参。不要调整摘要中间件在链中的位置，不要把 mode 写入 `ModelRequest`、provider model kwargs 或 state。

- [ ] **Step 4：运行装配与完整摘要行为回归**

Run:

```bash
cd backend
PYTHONPATH=. uv run pytest tests/test_lead_agent_model_resolution.py tests/test_summarization_mode_policy.py tests/test_summarization_middleware.py -q
```

Expected：全部 PASS；原有 fresh/reuse/pass-through、AI/Tool pair、latest user、skill rescue/ledger、trim 与 checkpoint 测试零回归。

- [ ] **Step 5：提交运行时接线**

```bash
git add backend/packages/harness/deerflow/agents/lead_agent/agent.py backend/tests/test_lead_agent_model_resolution.py backend/tests/test_summarization_mode_policy.py
git commit -m "feat(summarization): 按运行时模式装配摘要策略"
```

### Task 3：空模板、config_version 33 与正式迁移（RED→GREEN）

**Files**：
- Modify: `config.example.yaml:15,1144-1220`
- Modify: `backend/tests/test_model_modes_config.py:103-117`
- Modify: `backend/tests/test_360_new_models_roster.py:306-330`
- Test: `backend/tests/test_config_example_parity.py`
- Local ignored: 既有 `config.yaml`，只允许脚本修改

- [ ] **Step 1：先把版本与空模板测试改为目标态**

将 `test_example_config_version_is_32` / `test_user_config_version_is_32` 分别更名并改为断言 33；同时修改 `test_360_new_models_roster.py` 的 `test_example_config_version_bumped` 与 `test_user_config_version_bumped` 两处 32 断言为 33，并在版本注释顶部追加 `33=按模式摘要策略空模板（2026-11-22，modes: {}，不启用候选阈值）`，保留 32 的模型模式阶梯来历。

在 `test_model_modes_config.py` 新增 `test_example_summarization_modes_default_empty`：

```python
example = yaml.safe_load((REPO_ROOT / "config.example.yaml").read_text())
assert example["config_version"] == 33
assert example["summarization"]["modes"] == {}
assert example["summarization"]["trigger"] == [{"type": "tokens", "value": 32000}]
assert example["summarization"]["keep"] == {"type": "messages", "value": 10}
assert example["summarization"]["trim_tokens_to_summarize"] == 30000
```

- [ ] **Step 2：运行示例版本测试，确认 RED**

Run: `cd backend && PYTHONPATH=. uv run pytest tests/test_model_modes_config.py::test_example_config_version_is_33 tests/test_model_modes_config.py::test_example_summarization_modes_default_empty tests/test_360_new_models_roster.py::test_example_config_version_bumped -q`

Expected：示例仍为 32 且没有 `modes`，相关断言 FAIL；第二个文件的版本测试必须出现在 RED/GREEN 证据中。

- [ ] **Step 3：实现最小模板变更**

`config.example.yaml` 只做：

```yaml
config_version: 33
```

并在 `summarization` 现有全局三字段之后增加：

```yaml
  # Optional per-mode overrides. Empty preserves the global policy above for every mode.
  # Each configured entry requires trigger, keep and trim_tokens_to_summarize.
  # Candidate values stay disabled until the benchmark/activation gate is approved.
  modes: {}
```

不得把研究候选值写进活跃 YAML；无需新增 version-specific replacement migration。

- [ ] **Step 4：预检缺失键并运行仓库正式升级命令**

先用与 `scripts/config-upgrade.sh` 相同的递归字典规则，只读计算 example 相对既有本地配置的所有缺失键路径。除 `summarization.modes` 外还有任何缺失路径即停止，重新评估本计划与本地配置漂移；不得把“只新增 modes”写成未经验证的预期。

确认实际打开的是 worktree 根符号链接：当前 shell 的 `DEER_FLOW_CONFIG_PATH` 优先级最高，backend/config.yaml 次之，因此执行：

```bash
test -L config.yaml
test -f config.yaml
test ! -e backend/config.yaml
env -u DEER_FLOW_CONFIG_PATH make config-upgrade
```

Expected：升级到 33，added paths 与预检一致。脚本会递归合并全部缺失键并用 `yaml.dump(..., sort_keys=False)` 重写整个文件；注释/格式会丢失，不能用字节 diff 判断成败。若输出“已经是最新版本”，只有后续同时确认 version=33 且 `modes == {}` 才算成功；version 33 但 modes 缺失/非空仍停止。

- [ ] **Step 5：验证迁移保留原值与敏感面**

用 Python 只输出布尔结果，对解析后的 YAML 比较 `config.yaml.bak` 与升级后配置：原 summarization 全局 trigger/keep/trim/reuse/skill 值不变且新增 `modes == {}`；models 的 `api_key/pricing`、顶层 `model_pricing`、`video_gen` 不变。不得打印任何 key 或价格，也不做文件字节 diff。

符号链接场景下脚本打开的是 worktree `config.yaml`，备份 `config.yaml.bak` 落在 worktree，而写入跟随符号链接到主仓真实配置。该备份是完整配置副本；上述布尔检查通过后立即执行 `rm -f config.yaml.bak && test ! -e config.yaml.bak`，除指定字段投影外不得打开、复制或输出它。

然后运行：

```bash
cd backend
PYTHONPATH=. uv run pytest tests/test_model_modes_config.py tests/test_360_new_models_roster.py tests/test_config_example_parity.py -q
```

Expected：全部 PASS，示例与本地版本均为 33。

- [ ] **Step 6：提交配置模板与版本测试，不提交本地配置**

```bash
git add config.example.yaml backend/tests/test_model_modes_config.py backend/tests/test_360_new_models_roster.py
git commit -m "feat(config): 增加摘要模式空模板并升级配置版本"
```

### Task 4：全量门禁、行为验收与文档

**Files**：
- Modify: 本计划 checkbox 与状态头；执行时移至 `docs/plans/`
- Create: `docs/changelog/changelog-<实际执行日>-mode-specific-summarization.md`
- 不创建临时基准脚手架，不运行付费测试

- [ ] **Step 1：运行后端全量测试**

Run: `cd backend && env -u RUN_LIVE make test`

用工具的后台任务方式启动（`run_in_background: true`），由完成通知续接，不用前台等待或跨调用轮询。该目标自带 `CI=true PYTHONPATH=. uv run pytest tests/ -q -n auto --dist loadfile`；显式移除 shell 中可能已有的 `RUN_LIVE=1`，避免付费测试被脏环境卷入。

Expected：全量 PASS；不得用 deselect 或 skip 新失败冒充全绿。若确为既有本地环境分层问题，按仓内既有口径单独复跑并在 changelog 写清命令与关键输出。

- [ ] **Step 2：运行仓级静态与文档门禁**

Run:

```bash
make check
python3 scripts/check_docs_convention.py
git diff --check
```

Expected：三者 exit 0；本批无前端源码改动，不新增 frontend 测试文件。

- [ ] **Step 3：做零付费行为验收**

通过已新增的工厂测试与本地配置只读投影确认：

- 空 `modes` + 缺 mode：有效配置仍严格等于全局 32k / 10 messages / 30k。
- 仅在测试构造的临时 `SummarizationConfig` 中启用三档，三档 kwargs 分别等于 fixture；不写回真实配置。
- `make config-upgrade` 后真实 `config.yaml` 的 `modes` 仍为空。
- 全程 `RUN_LIVE` 未设置，未发起真实模型/摘要调用。

- [ ] **Step 4：更新文档与勾选**

计划头部记录已执行范围、测试与 PR 链接并移回 `docs/plans/`；如相对 Spec 链接因移动失效，同步改为 `../pending/2026-11-21-mode-specific-summarization-plan.md`。changelog 必须含 ≤5 行 TL;DR、验收记录节（命令 → 实际关键输出）及醒目边界：**“本批只交付配置能力，未启用三档不同默认阈值，未做付费质量/成本验收。”**

- [ ] **Step 5：提交文档**

```bash
git add docs/plans docs/changelog
git commit -m "docs(summarization): 记录模式策略配置能力验收"
```

### Task 5：审查、推送与 PR

- [ ] **Step 1：审查完整 diff 与路径边界**

Run: `git diff origin/main...HEAD --stat && git diff origin/main...HEAD`

Expected：只出现本计划允许的后端 schema/装配/测试、根配置模板和文档；无 `config.yaml`、密钥、价格、前端、部署或 harness 升级 diff。

- [ ] **Step 2：推送并创建 PR**

```bash
git push -u origin mode-specific-summarization
GH_REPO=littlebear2020/SuperTrainerAgent gh pr create --title "feat(summarization): 增加按模式摘要策略配置能力" --body-file <临时PR正文文件>
```

不要用 `gh pr create --fill` 代替正文。正文必须显式列出：本计划、研究 Spec、按实际日期生成的 changelog、`modes: {}` 回滚方式、config_version 32→33、默认无行为变化及未做付费标定；临时正文文件在 PR 创建后删除，不作为额外仓内文档提交。

- [ ] **Step 3：等待 CI/OCR 并处理结论**

Expected：backend/frontend/e2e/lint/quick-checks 等 PR 必需检查全绿；Critical/Important 评审发现先修复并重跑相关门禁。不得因“功能未默认启用”豁免测试或评审。

- [ ] **Step 4：按仓库 PR 流合入并清理**

只有用户授权的执行流才自动 rebase merge；合入后确认远端 main、删除本 worktree 和已合并分支。不得部署。若 GitHub rebase merge 重写 SHA，以 tree/diff 核对内容，不声称原分支 SHA 原样成为 main。

---

## 后续启用门：不属于本批默认配置变更

以下项目不能由执行者自行勾选为“已验证”：

- [ ] 免费 replay 证明各候选的 trigger、cutoff、source coverage、pair、reuse 与 pass-through 行为；测试夹具候选不等于生产值。
- [ ] 单独决定是否给直连 API 增加 `thinking/pro → standard` 别名及大小写归一；在决定前，后端继续把这些值当未知并回全局，不能由实施者默认对齐前端。
- [ ] 用户另行批准 live 样本、价目快照和预算后，才能运行研究 Spec §7 的配对质量/成本实验。
- [ ] 每模式独立得到质量非劣与成本/时延结论；pilot 不冒充确认集。
- [ ] 用户审阅基准报告并选定启用值后，另开配置激活计划/PR；递增当时 config_version，继续使用 `make config-upgrade`。
- [ ] 若先升级上游 2.2.0 或 tool-output 机制，作废旧候选结论并重新标定。

本批完成不等于“三档已经在生产使用不同阈值”；它只提供可审查、可回滚、默认无副作用的开关。

## 执行假设

- 默认采用原生 `executing-plans` 串行执行，因为 Task 1→3 共享同一配置接口和装配点，不适合并行争抢。
- 本批基线为当前 LangChain 1.2.15 与本仓自定义 summarization，不等待 2.2.0 升级，也不与其混在同一 PR。
- 用户尚未批准付费基准，因此计划不把 live 验证或阈值激活列为可自动执行步骤。
