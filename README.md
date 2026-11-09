# SuperTrainerAgent

[![Python](https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&logoColor=white)](./backend/pyproject.toml)
[![Node.js](https://img.shields.io/badge/Node.js-22%2B-339933?logo=node.js&logoColor=white)](./frontend/package.json)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)

**SuperTrainerAgent** 是一个 AI 课程内容智能生产平台，基于 TrainerAI v2 与 [DeerFlow 2.0](https://github.com/bytedance/deer-flow) 多 Agent 框架，覆盖课程视频、PPT 课件、声音分身、研究报告和视觉设计等内容生产场景。

本仓库是独立产品基线，不包含 TrainerAI 当前 v3 改造，也不包含原仓库的 Git 历史。导入基线为 `release/v2@3ad1afe67d1f1374b46b7ad7c5f9b581e2fb8c0b`。

## 核心能力

- **课程视频**：Remotion 数据驱动渲染、课程模板、品牌包、分镜编辑和静帧预览。
- **PPT 课件**：生成可编辑 PPTX、改造现有 PPT、品牌模板复用、PPT 配音转视频。
- **声音能力**：音色克隆、TTS 合成、文稿转语音和提词器。
- **深度研究与写作**：多 Agent 调研、结构化报告和在线文档编辑。
- **视觉设计**：海报、信息图、知识卡片、漫画、结构图和文章插图。
- **视频与图像处理**：视频下载、转录、剪辑、帧提取、AI 绘图和数据图表。
- **多通道交互**：Web 工作台及多种 IM 通道。

预置 Agent、Skills、模型和通道的实时名册以代码为准：`backend/seed/agents/`、`skills/`、`config.example.yaml` 和 `extensions_config.json`。

## 技术架构

```text
浏览器（Next.js）
  → Nginx
  → FastAPI Gateway
      → Lead Agent（LangGraph / LangChain）
      → Skills / MCP / Sandbox
      → 任务与持久化
  → Worker 池（FFmpeg / Remotion）
```

主要目录：

```text
SuperTrainerAgent/
├── backend/                 # Python 后端、Agent Harness 与测试
├── frontend/                # Next.js 前端与测试
├── skills/                  # Agent Skills
├── video-templates/         # Remotion 模板
├── docker/                  # Docker、worker、沙箱与 Nginx 配置
├── scripts/                 # 开发和部署脚本
├── docs/                    # 可公开的产品与开发文档
├── config.example.yaml      # 配置样例
├── extensions_config.json   # MCP 与 Skills 配置
└── Makefile                 # 开发命令入口
```

## 环境要求

- Python 3.12+
- Node.js 22+、pnpm 10+
- [uv](https://docs.astral.sh/uv/)
- Git LFS
- Docker（沙箱与视频处理）

## 快速开始

### 1. 克隆与配置

```bash
git clone https://github.com/littlebear2020/SuperTrainerAgent.git
cd SuperTrainerAgent
git lfs pull
cp .env.example .env
```

在 `.env` 中配置实际使用的模型服务和认证密钥。`.env` 不得提交到仓库。

### 2. 安装与启动

```bash
make install
make check-env
make dev
```

默认访问 `http://localhost:2026`。

### 3. Docker

```bash
make docker-init
make docker-start
```

通用服务器部署见 `docs/personal-server-deployment-guide.md`，课程视频与任务管线见 `docs/remotion-course-pipeline.md` 和 `docs/task-pipeline.md`。

## 验证

```bash
# 后端
cd backend
uv sync --group dev
uv run pytest tests/ -q

# 前端
cd ../frontend
pnpm install --frozen-lockfile
pnpm check
pnpm test

# 仓库门禁
cd ..
make check
```

真实付费 API 测试需要显式设置 `RUN_LIVE=1`，默认不会执行。

## 安全与公开仓库约定

- 不提交 `.env`、真实 API Key、访问令牌、私钥或生产配置。
- 不提交真实服务器地址、SSH 账号、内部任务 ID、用户数据或运行数据库。
- 测试账号和资源必须通过环境变量显式提供，不使用可用的公共默认凭证。
- `docker/sandbox/cache/` 包含离线构建资源，其中 Chrome 可执行文件使用 Git LFS，clone 后必须执行 `git lfs pull`。

## 致谢

本项目基于 TrainerAI v2 和 [DeerFlow](https://github.com/bytedance/deer-flow) 构建，并使用 LangChain、LangGraph、Next.js、FastAPI、Remotion 等开源项目。

## License

SuperTrainerAgent 代码及 TrainerAI 自有贡献以 [MIT License](./LICENSE) 发布，保留 Bytedance、DeerFlow 和 TrainerAI 的原版权署名。第三方组件与素材保留其各自许可证；内置 BGM 的作者、来源和许可证见 `backend/app/gateway/video_gen/resources/bgm/NOTICE.md`。
