# DeerFlow Harness 二次开发指南

> 本文档适用于 DeerFlow 2.0+ 版本
> 创建日期：2026-05-05
> 维护者：项目开发团队
> 复活注记（2026-10-08）：本文曾于 2026-08 归档至 docs/archive 冷区，经 4 关键节量化核验（agent 装配/skill 加载/memory 机制一致；中间件清单滞后——见 §3.2.1 注意块）判定机制层仍有效，移回本包随代码自描述。

---

## 目录

1. [项目概述](#1-项目概述)
2. [Harness 架构](#2-harness-架构)
3. [核心模块详解](#3-核心模块详解)
4. [二次开发指南](#4-二次开发指南)
5. [最佳实践](#5-最佳实践)
6. [扩展方法](#6-扩展方法)
7. [故障排查](#7-故障排查)

---

## 1. 项目概述

### 1.1 什么是 Harness

Harness 是 DeerFlow 的**核心编排引擎**，负责：
- Agent 的创建与生命周期管理
- 中间件管道（Middleware Pipeline）的构建
- 工具系统的注册与发现
- 沙箱执行环境的隔离
- 子 agent 的委派与调度
- 记忆系统的管理
- 技能（Skills）的加载与注入

**设计原则**：
- ✅ **可组合性**：通过中间件管道灵活组装功能
- ✅ **可扩展性**：支持 MCP、Skills、自定义工具
- ✅ **安全性**：沙箱隔离执行，防止恶意代码
- ✅ **可追溯性**：完整的运行日志和 token 使用追踪
- ✅ **无感知性**：Harness 不依赖上层应用，可独立使用

### 1.2 目录结构

```
deer-flow/
├── backend/
│   ├── packages/
│   │   └── harness/                    # 🦌 Harness 核心包
│   │       └── deerflow/
│   │           ├── agents/             # Agent 工厂与中间件
│   │           │   ├── lead_agent/     # Lead Agent 入口
│   │           │   ├── middlewares/    # 中间件（数量以代码为准）
│   │           │   └── memory/         # 记忆系统
│   │           ├── config/             # 配置系统
│   │           ├── models/             # 模型工厂
│   │           ├── tools/              # 工具系统
│   │           ├── sandbox/            # 沙箱环境
│   │           ├── skills/             # 技能系统
│   │           ├── mcp/                # MCP 协议集成
│   │           ├── persistence/         # 持久化层
│   │           └── client.py           # 嵌入式客户端
│   └── app/                           # 应用层（Gateway API, Channels）
├── skills/
│   ├── public/                        # 公开 Skills（通用能力；名册以代码为准）
│   └── custom/                        # 自定义 Skills（业务场景；名册以代码为准）
└── config.yaml                        # 核心配置文件
```

> 注（2026-10-08 复核）：上树为核心结构示意，不逐一枚举——`agents/` 下实际还有 guardrails/、reflection/、runtime/、subagents/、tracing/、uploads/ 等模块；技能与目录名册一律以代码为准。

### 1.3 架构分层

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           Client (Browser)                               │
└─────────────────────────────────┬────────────────────────────────────────┘
                                  │
                                  ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                         Nginx (Port 2026)                               │
│                         反向代理入口点                                    │
│  ┌────────────────────────────────────────────────────────────────────┐  │
│  │  /api/langgraph/*  →  LangGraph Server (2024)                     │  │
│  │  /api/*            →  Gateway API (8001)                          │  │
│  │  /*                →  Frontend (3000)                              │  │
│  └────────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────┬────────────────────────────────────────┘
                                  │
          ┌───────────────────────┼───────────────────────┐
          │                       │                       │
          ▼                       ▼                       ▼
┌─────────────────────┐ ┌─────────────────────┐ ┌─────────────────────┐
│   LangGraph Server  │ │    Gateway API      │ │     Frontend        │
│   (Harness 运行)    │ │    (App 层)         │ │    (Next.js)       │
└─────────────────────┘ └─────────────────────┘ └─────────────────────┘
```

---

## 2. Harness 架构

### 2.1 Agent 构建流程

```
make_lead_agent(config)
         │
         ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                            Middleware Chain                              │
│  ┌────────────────────────────────────────────────────────────────────┐  │
│  │ 1. ThreadDataMiddleware    - 初始化 workspace/uploads/outputs       │  │
│  │ 2. UploadsMiddleware       - 处理上传文件                          │  │
│  │ 3. SandboxMiddleware      - 获取沙箱环境                          │  │
│  │ 4. SummarizationMiddleware - 上下文压缩                           │  │
│  │ 5. TitleMiddleware        - 自动生成标题                          │  │
│  │ 6. TodoListMiddleware     - 任务跟踪                              │  │
│  │ 7. ViewImageMiddleware    - 视觉模型支持                          │  │
│  │ 8. ClarificationMiddleware - 处理澄清请求                         │  │
│  └────────────────────────────────────────────────────────────────────┘  │
└────────────────────────────────────┬────────────────────────────────────┘
                                     │
                                     ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                              Agent Core                                  │
│  ┌──────────────────┐  ┌──────────────────┐  ┌──────────────────────┐   │
│  │      Model       │  │      Tools       │  │    System Prompt     │   │
│  │  (from factory)  │  │  (configured +   │  │  (with skills)       │   │
│  │                  │  │   MCP + builtin) │  │                      │   │
│  └──────────────────┘  └──────────────────┘  └──────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────┘
```

### 2.2 工具系统架构

```
┌─────────────────────────────────────────────────────────────────────────┐
│                            Tool Sources                                  │
└─────────────────────────────────────────────────────────────────────────┘

┌─────────────────────┐  ┌─────────────────────┐  ┌─────────────────────┐
│   Built-in Tools    │  │  Configured Tools   │  │     MCP Tools       │
│  (packages/harness/deerflow/tools/)       │  │  (config.yaml)      │  │  (extensions.json)  │
├─────────────────────┤  ├─────────────────────┤  ├─────────────────────┤
│ - present_files     │  │ - web_search        │  │ - github            │
│ - ask_clarification │  │ - web_fetch         │  │ - filesystem        │
│ - view_image        │  │ - bash              │  │ - metaso           │
│ - task (subagent)   │  │ - read_file         │  │ - ...               │
│                     │  │ - write_file        │  │                     │
└─────────────────────┘  └─────────────────────┘  └─────────────────────┘
           │                       │                       │
           └───────────────────────┴───────────────────────┘
                                   │
                                   ▼
                      ┌─────────────────────────┐
                      │   get_available_tools() │
                      │   (tools/tools.py)       │
                      └─────────────────────────┘
```

### 2.3 沙箱系统架构

```
┌─────────────────────────┐
│   SandboxProvider       │ (抽象基类)
│  - acquire()            │
│  - get()                │
│  - release()            │
└───────────┬─────────────┘
            │
    ┌───────┴───────┐
    ▼               ▼
LocalSandbox    AioSandbox
(开发环境)       (Docker 生产环境)
```

**虚拟路径映射**：
| 虚拟路径 | 物理路径 |
|---------|---------|
| `/mnt/user-data/workspace` | `.deer-flow/threads/{thread_id}/user-data/workspace` |
| `/mnt/user-data/uploads` | `.deer-flow/threads/{thread_id}/user-data/uploads` |
| `/mnt/user-data/outputs` | `.deer-flow/threads/{thread_id}/user-data/outputs` |
| `/mnt/skills` | `skills/` |

---

## 3. 核心模块详解

### 3.1 Agent 工厂 (`agents/`)

#### 3.1.1 Lead Agent 入口

**文件**：`agents/lead_agent/agent.py`

**函数**：`make_lead_agent(config, app_config)`

**职责**：
- 创建和管理 agent 的生命周期
- 解析运行时配置（模型选择、thinking 模式、子代理开关等）
- 构建中间件链（Middleware Chain）
- 加载工具集
- 应用系统提示词模板

#### 3.1.2 Agent 工厂

**文件**：`agents/factory.py`

**函数**：`create_deerflow_agent(model, tools, features, plan_mode, checkpointer)`

**职责**：
- 提供 SDK 级别的 Agent 创建接口
- 支持声明式特性配置（RuntimeFeatures）
- 支持 @Next/@Prev 中间件定位装饰器

#### 3.1.3 线程状态

**文件**：`agents/thread_state.py`

```python
class ThreadState(AgentState):
    messages: list[BaseMessage]           # 对话历史
    sandbox: SandboxState                 # 沙箱环境信息
    artifacts: list[str]                  # 生成的文件路径
    thread_data: ThreadDataState          # {workspace, uploads, outputs} 路径
    title: str | None                     # 自动生成的对话标题
    todos: list[dict]                     # 任务跟踪
    viewed_images: dict                   # 视觉模型的图像数据
```

### 3.2 中间件系统 (`agents/middlewares/`)

DeerFlow 使用 LangChain 的中间件机制，每个中间件在模型调用前后执行特定逻辑。

#### 3.2.1 中间件列表

> **运行时链的权威顺序不在本文**——以两个装配点为准：`agents/middlewares/tool_error_handling_middleware.py` 的 `_build_runtime_middlewares`（基座段）+ `agents/lead_agent/agent.py` 的 `_build_middlewares`（lead 段），汇总清单见仓库 `backend/AGENTS.md`「中间件链」节（随代码同步维护，本文不复制，防双源漂移）。
>
> 机制要点：中间件在模型调用前后执行特定逻辑；装配分两阶段（基座段 → lead 段），Clarification 恒为末位；自定义中间件用 §3.2.2 的 `@Next/@Prev` 定位注入。

#### 3.2.2 自定义中间件

使用 `@Next` 和 `@Prev` 装饰器实现灵活的中间件定位：

```python
from deerflow.agents.features import Next, Prev

@Next(ClarificationMiddleware)
class CustomMiddleware(AgentMiddleware):
    pass

@Prev(ToolErrorHandlingMiddleware)
class AnotherMiddleware(AgentMiddleware):
    pass
```

### 3.3 配置系统 (`config/`)

#### 3.3.1 核心配置

**文件**：`config/app_config.py`

```python
class AppConfig(BaseModel):
    models: list[ModelConfig]           # 模型配置
    sandbox: SandboxConfig              # 沙箱配置
    tools: list[ToolConfig]            # 工具配置
    skills: SkillsConfig               # 技能配置
    memory: MemoryConfig               # 记忆配置
    summarization: SummarizationConfig # 摘要配置
    extensions: ExtensionsConfig       # MCP 服务器
    database: DatabaseConfig           # 数据库后端
```

#### 3.3.2 环境变量使用

**推荐模式**：
```yaml
models:
  - name: deepseek-v3
    api_key: $DEEPSEEK_API_KEY  # ✅ 推荐
    # api_key: sk-cp-xxx...    # ❌ 不推荐（硬编码）
```

### 3.4 工具系统 (`tools/`)

#### 3.4.1 工具来源

1. **内置工具**（始终可用）
   - `present_file` - 文件展示
   - `ask_clarification` - 澄清请求
   - `view_image` - 图像查看
   - `task` - 子代理委派

2. **配置工具**（通过 `config.yaml`）
   - 网页搜索、Web 抓取
   - Bash、文件系统操作

3. **MCP 工具**（通过 `extensions_config.json`）
   - 秘塔AI、GitHub 等 MCP 服务器

#### 3.4.2 添加自定义工具

```python
# 1. 创建工具类
from langchain.tools import BaseTool

class MyCustomTool(BaseTool):
    name = "my_custom_tool"
    description = "自定义工具描述"

    def _run(self, query: str) -> str:
        return f"执行结果: {query}"
```

```yaml
# 2. 在 config.yaml 中注册
tools:
  - name: my_custom_tool
    group: custom
    use: my_package.tools:MyCustomTool
```

### 3.5 技能系统 (`skills/`)

#### 3.5.1 技能目录结构

```
skills/
├── public/                        # 公开技能（提交到仓库）
│   ├── deep-research/
│   │   ├── SKILL.md
│   │   ├── scripts/
│   │   └── references/
│   └── auto-video-flow/
│       └── SKILL.md
└── custom/                        # 自定义技能（gitignored）
    └── my-skill/
        └── SKILL.md
```

#### 3.5.2 SKILL.md 格式

```yaml
---
name: my-skill
description: 技能描述，用于触发匹配。当用户要求...时触发。
---

# 技能名称

## 工作流程

### Step 1: 准备
...

### Step 2: 执行
...

## 输出
...
```

#### 3.5.3 技能依赖

技能之间可以通过读取其他技能来构建依赖：

```markdown
### Step 1: 调研
读取 skill: deep-research，对研究主题进行系统调研。
```

### 3.6 模型工厂 (`models/`)

**文件**：`models/factory.py`

**函数**：`create_chat_model(name, thinking_enabled, **kwargs)`

**支持的模型类型**：
- OpenAI (`langchain_openai:ChatOpenAI`)
- Anthropic (`langchain_anthropic:ChatAnthropic`)
- DeepSeek (`langchain_deepseek:ChatDeepSeek`)
- vLLM 自定义 provider

**Thinking 模式配置**：
```yaml
models:
  - name: deepseek-v3
    supports_thinking: true
    when_thinking_enabled:
      extra_body:
        thinking:
          type: "extended"
          budget: 10000
```

### 3.7 持久化层 (`persistence/`)

**文件**：`persistence/engine.py`

**支持的数据库后端**：
- `memory` - 内存（开发）
- `sqlite` - SQLite with WAL 模式（推荐开发）
- `postgres` - PostgreSQL（生产）

---

## 4. 二次开发指南

### 4.1 开发原则

#### ✅ 推荐做法

1. **配置驱动扩展**
   - 通过 `config.yaml` 和 `extensions_config.json` 扩展功能
   - 避免直接修改核心代码

2. **Skills 优先**
   - 业务逻辑优先通过 Skills 实现
   - Skills 是 Harness 的一等公民

3. **中间件组合**
   - 使用 @Next/@Prev 装饰器定位新中间件
   - 避免修改已有中间件的执行顺序

4. **工具解耦**
   - 自定义工具应该独立于核心代码
   - 通过 `config.yaml` 注册即可生效

#### ❌ 避免做法

1. **不要直接修改核心代码**
   - 修改 `agents/factory.py`、`agents/lead_agent/` 等核心文件
   - 避免引入版本升级冲突

2. **不要硬编码配置**
   - API Keys 必须使用环境变量
   - 敏感配置必须可配置化

3. **不要绕过沙箱**
   - 工具执行必须通过沙箱隔离
   - 避免直接访问主机文件系统

### 4.2 添加新的 MCP 服务器

#### Step 1: 编辑 `extensions_config.json`

```json
{
  "mcpServers": {
    "my-mcp": {
      "enabled": true,
      "type": "stdio",
      "command": "npx",
      "args": ["-y", "@mcp/server-my"],
      "env": {
        "API_KEY": "$MY_MCP_API_KEY"
      }
    }
  }
}
```

#### Step 2: 重启服务

```bash
make docker-restart  # Docker 模式
# 或
make dev             # 本地模式
```

#### Step 3: 验证

```bash
curl http://localhost:2026/api/mcp/servers
```

### 4.3 添加新的工具

#### Step 1: 实现工具类

```python
# backend/packages/harness/deerflow/tools/my_tool.py
from langchain.tools import BaseTool

class MySearchTool(BaseTool):
    name = "my_search"
    description = "执行自定义搜索，返回 JSON 结果"

    def _run(self, query: str) -> str:
        # 实现搜索逻辑
        return '{"results": []}'
```

#### Step 2: 注册工具

```yaml
# config.yaml
tools:
  - name: my_search
    group: search
    use: deerflow.tools.my_tool:MySearchTool
```

### 4.4 添加新的 Skill

#### Step 1: 创建目录结构

```
skills/custom/my-skill/
├── SKILL.md
├── scripts/
│   └── main.py
└── references/
    └── guide.md
```

#### Step 2: 编写 SKILL.md

```yaml
---
name: my-skill
description: 我的自定义技能。当用户要求...时触发。
---

# 我的自定义技能

## 工作流程

1. 准备环境
2. 执行主逻辑
3. 输出结果
```

#### Step 3: 启用技能

默认情况下，`skills/custom/` 目录下的技能会被自动扫描并可用。

### 4.5 自定义中间件

#### 示例：添加日志中间件

```python
# backend/packages/harness/deerflow/agents/middlewares/audit_middleware.py
from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langgraph.runtime import Runtime

class AuditMiddleware(AgentMiddleware[AgentState]):
    """审计中间件 - 记录所有模型调用"""

    @override
    def before_model(self, state: AgentState, runtime: Runtime) -> dict | None:
        messages = state.get("messages", [])
        if messages:
            last_msg = messages[-1]
            logger.info(f"Audit: {getattr(last_msg, 'type', 'unknown')} - {len(messages)} messages")

        # 可以修改输入（返回 dict 即可）
        return None

    @override
    def after_model(self, state: AgentState, runtime: Runtime) -> dict | None:
        messages = state.get("messages", [])
        if messages:
            last_msg = messages[-1]
            logger.info(f"Audit completed: {getattr(last_msg, 'type', 'unknown')}")
        return None
```

#### 注入中间件

在 `make_lead_agent` 中通过 `custom_middlewares` 参数注入：

```python
middlewares = _build_middlewares(
    config,
    model_name,
    custom_middlewares=[AuditMiddleware()]
)
```

### 4.6 添加新的模型 Provider

#### Step 1: 实现 Provider

```python
# backend/packages/harness/deerflow/models/my_provider.py
from langchain.chat_models import BaseChatModel

class MyChatModel(BaseChatModel):
    @property
    def _llm_type(self) -> str:
        return "my-chat-model"

    def _generate(self, messages, stop, **kwargs):
        # 实现生成逻辑
        pass
```

#### Step 2: 注册 Provider

```yaml
# config.yaml
models:
  - name: my-model
    display_name: My Model
    use: deerflow.models.my_provider:MyChatModel
    api_key: $MY_MODEL_API_KEY
    base_url: https://api.my-model.com/v1
    supports_thinking: true
```

---

## 5. 最佳实践

### 5.1 配置管理

#### 生产环境配置

```yaml
# config.yaml
log_level: info

database:
  backend: postgres
  url: $DATABASE_URL

sandbox:
  provider: aio_sandbox
  mode: production

memory:
  enabled: true
  # 现行字段（2026-10-08 复核）：storage_class（存储提供者类路径，默认
  # deerflow.agents.memory.storage.FileMemoryStorage）/ storage_path；
  # 旧示例的 backend: postgres 字段已废弃。
  storage_class: deerflow.agents.memory.storage.FileMemoryStorage

token_usage:
  enabled: true
```

#### 环境变量管理

```bash
# .env
OPENAI_API_KEY=sk-xxx
METASO_API_KEY=mk-xxx
DATABASE_URL=postgresql://user:pass@localhost:5432/deerflow
```

### 5.2 安全实践

#### ✅ API Key 管理

```yaml
# ✅ 正确：使用环境变量
api_key: $OPENAI_API_KEY

# ❌ 错误：硬编码
api_key: sk-REDACTED...
```

#### ✅ MCP 安全

```json
{
  "mcpServers": {
    "trusted-server": {
      "enabled": true,
      "type": "http",
      "url": "https://internal.example.com/mcp"
    }
  }
}
```

#### ✅ 沙箱隔离

- 工具执行必须在沙箱内
- 不直接访问主机文件系统
- 使用虚拟路径 `/mnt/user-data/*`

### 5.3 性能优化

#### ✅ 工具按需加载

```yaml
# config.yaml
tool_search:
  enabled: true
  max_tools: 100
```

#### ✅ 上下文压缩

```yaml
# config.yaml
summarization:
  enabled: true
  trigger:
    message_count: 20
  keep:
    first_n: 2
    last_n: 5
```

#### ✅ 模型缓存

```yaml
# config.yaml
checkpointer:
  type: postgres
  connection_pool_size: 10
```

### 5.4 技能开发

#### ✅ Skill 结构

```
skill-name/
├── SKILL.md                 # 必需：技能定义
├── scripts/                 # 可选：可执行脚本
├── references/              # 可选：参考文档
└── templates/               # 可选：模板文件
```

#### ✅ Skill 设计原则

1. **触发明确**：description 要清晰描述触发场景
2. **工作流完整**：从输入到输出要有完整路径
3. **依赖显式**：明确说明依赖的其他 skills
4. **输出有据**：说明输出格式和存储位置

#### ✅ 技能依赖示例

```markdown
## 工作流程

### Step 1: 调研
读取 skill: deep-research，对主题进行深度调研。

### Step 2: 可视化
读取 skill: chart-visualization，生成配套图表。

### Step 3: 输出
生成最终报告到 /mnt/user-data/workspace/output/
```

### 5.5 项目 ASR 规范

本项目语音识别（ASR）统一使用 **volcengine/asr-turbo** 模型，通过 360 API 调用。

- **脚本**：`skills/public/video-dl/scripts/transcribe.py`
- **API**：`https://api.360.cn/v1/audios/generations`
- **环境变量**：`ASR_360_API_KEY`（回退到 `QIHU_API_KEY`）
- **规范文档**：`skills/public/video-dl/references/asr-guide.md`

开发需要语音转文字的技能时，引用 `asr-guide.md` 并使用 `transcribe.py`。**不要引入 whisper、dashscope 等其他 ASR 工具。**

### 5.6 调试技巧

#### ✅ 日志级别调整

```yaml
# config.yaml
log_level: debug
```

#### ✅ 工具验证

```bash
# 测试工具配置
make doctor

# 查看可用工具
curl http://localhost:2026/api/models
```

#### ✅ 沙箱调试

```bash
# 查看沙箱日志
make docker-logs | grep sandbox

# 测试沙箱执行
curl -X POST http://localhost:2024/sandbox/execute \
  -H "Content-Type: application/json" \
  -d '{"command": "ls /mnt/user-data"}'
```

---

## 6. 扩展方法

### 6.1 扩展工具集

#### 方式一：内置工具（代码实现）

```python
# packages/harness/deerflow/tools/builtins/my_tool.py
def get_my_tool() -> BaseTool:
    return Tool(
        name="my_tool",
        func=my_function,
        description="工具描述"
    )
```

#### 方式二：MCP 工具（配置实现）

```json
// extensions_config.json
{
  "mcpServers": {
    "my-mcp": {
      "enabled": true,
      "type": "http",
      "url": "https://api.example.com/mcp"
    }
  }
}
```

### 6.2 扩展技能集

#### 公开技能（贡献到仓库）

```
skills/public/
└── my-awesome-skill/
    └── SKILL.md
```

#### 自定义技能（本地使用）

```
skills/custom/
└── my-business-skill/
    └── SKILL.md
```

### 6.3 扩展中间件

#### 继承 AgentMiddleware

```python
class MyMiddleware(AgentMiddleware[AgentState]):
    def __init__(self, option: str = "default"):
        super().__init__()
        self.option = option

    @override
    def before_model(self, state: AgentState, runtime: Runtime) -> dict | None:
        # 前置逻辑
        return None

    @override
    def after_model(self, state: AgentState, runtime: Runtime) -> dict | None:
        # 后置逻辑
        return None
```

### 6.4 扩展模型

#### 支持新的模型 Provider

1. 实现 `BaseChatModel` 接口
2. 在 `models/` 目录下创建 `my_provider.py`
3. 在 `config.yaml` 中注册

---

## 7. 故障排查

### 7.1 常见问题

#### Q1: 工具不生效

**症状**：新添加的工具无法被 Agent 调用

**排查步骤**：
1. 检查 `config.yaml` 配置是否正确
2. 运行 `make doctor` 验证配置
3. 查看日志：`make docker-logs | grep my_tool`
4. 确认工具名称在 `get_available_tools()` 返回列表中

#### Q2: MCP 服务器连接失败

**症状**：`extensions_config.json` 配置的 MCP 服务器无法连接

**排查步骤**：
1. 检查 API Key 是否正确配置在 `.env`
2. 测试 MCP 服务器：
   ```bash
   curl -X POST https://your-mcp-server/api/mcp \
     -H "Authorization: Bearer $YOUR_API_KEY" \
     -d '{"jsonrpc":"2.0","method":"tools/list","params":{},"id":1}'
   ```
3. 查看 MCP 日志：`make docker-logs | grep mcp`

#### Q3: 技能不触发

**症状**：编写的新技能没有被 Agent 使用

**排查步骤**：
1. 检查 SKILL.md 格式是否正确
2. 确认 description 包含触发关键词
3. 检查技能是否在正确的目录（public/custom）
4. 重启服务使配置生效

#### Q4: 沙箱执行失败

**症状**：工具执行时报错

**排查步骤**：
1. 检查 Docker 是否运行：`docker info`
2. 查看沙箱日志：`make docker-logs | grep sandbox`
3. 测试沙箱连接：
   ```bash
   docker exec deer-flow-langgraph ls /mnt/user-data
   ```
4. 检查沙箱配置：
   ```yaml
   sandbox:
     provider: local  # 开发模式
     # provider: aio_sandbox  # 生产模式
   ```

#### Q5: 记忆系统不工作

**症状**：对话历史没有被记忆

**排查步骤**：
1. 检查记忆配置：
   ```yaml
   memory:
     enabled: true
   ```
2. 检查数据库连接
3. 查看记忆日志：`make docker-logs | grep memory`

### 7.2 配置验证

```bash
# 完整检查
make doctor

# 测试配置
cd backend
uv run python -c "from deerflow.config import get_app_config; print(get_app_config().model_dump_json(indent=2))"
```

### 7.3 日志分析

```bash
# 查看所有日志
make docker-logs

# 过滤特定模块
make docker-logs | grep -E "(agent|middleware|tool)"

# 实时日志
make docker-logs -f
```

---

## 附录

### A. 文件路径映射

| 虚拟路径 | 说明 |
|---------|------|
| `/mnt/user-data/workspace` | 工作区（上传文件处理） |
| `/mnt/user-data/uploads` | 用户上传目录 |
| `/mnt/user-data/outputs` | 输出文件目录 |
| `/mnt/skills` | 技能目录 |
| `/mnt/user-data/artifacts` | 生成的工件 |

### B. API 端点

| 端点 | 说明 |
|------|------|
| `GET /api/models` | 列出可用模型 |
| `GET /api/skills` | 列出可用技能 |
| `POST /api/threads` | 创建新线程 |
| `GET /api/mcp/servers` | 列出 MCP 服务器 |
| `GET /health` | 健康检查 |

### C. 配置热更新

DeerFlow 支持部分配置热更新：
- `extensions_config.json` 修改后自动生效
- `config.yaml` 修改需要重启服务

### D. 版本升级

```bash
# 查看当前版本
git log --oneline -1

# 更新到最新版本
git fetch origin
git merge origin/main

# 解决冲突后
make docker-restart
```

---

## 维护记录

| 日期 | 修改人 | 修改内容 |
|------|--------|---------|
| 2026-05-05 | 开发团队 | 初始创建 |

---

**文档版本**: 1.0.0
**最后更新**: 2026-05-05