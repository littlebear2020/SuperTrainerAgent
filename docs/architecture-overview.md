# 架构总览

> 本文件是心智模型与导航，不是细节文档：拓扑行与根 `AGENTS.md` 逐字对齐（AGENTS.md 是权威，本文件是展开）；子系统只写一句话+指针，细节以各模块笔记为准。
> 旧 `backend/docs/ARCHITECTURE.md`（2026-08-03 时点，LangGraph Server 三服务口径）已于 2026-10-08 归档至 `docs/archive/2026-10/backend-docs-legacy/`，勿再引用。

## 1. 现行拓扑

```
浏览器
  → nginx 反代（/api/* → gateway；/ → 页面；/api/langgraph/* → gateway 重写为 /api/*）
  → frontend :3000（Next.js）+ gateway :8001（FastAPI，GET /health）
  → worker 池家族（ffmpeg 池 / remotion 池，独立容器，WORKER_POOL 注入区分）
```

单机多进程、离线可部署；数据库默认 SQLite（`database.backend`，生产可 postgres）。代码分层：`backend/packages/harness/deerflow/`（可发布 agent 框架，import `deerflow.*`）← `backend/app/`（应用层，import `app.*`），依赖单向，反向有 CI 测试守护。

## 2. 两条主链路

**同步请求链**：浏览器 → nginx → gateway 路由（`backend/app/gateway/routers/`）→ 鉴权中间件 → 业务 service → 响应。chat 流式走 LangGraph SDK，同样经 nginx 反代（`/api/langgraph/*` → gateway 重写为 `/api/*`；仅调试场景才绕过 nginx 直连，见 `frontend/AGENTS.md` 环境变量节）。

**异步任务链**：API 建单 → `unified_tasks` 表 → gateway 协程执行 →（重 IO 段）`composing` 交棒 worker 池 → worker CAS 认领执行 → 回写终态。状态机/认领谓词/重试/排查表全部见 `task-pipeline.md`，本文不展开。

## 3. 关键子系统一句话 + 指针

| 子系统 | 一句话 | 细节去哪 |
|---|---|---|
| agent 框架 | lead agent 装配 = 模型 + 中间件链 + 工具 + 提示词模板 | `backend/packages/harness/deerflow/HARNESS.md`；中间件链权威顺序在 `backend/AGENTS.md` |
| 沙箱 | agent 执行代码的隔离容器（aio_sandbox） | `backend/docs/BACKEND_MODULE_NOTES.md` |
| skills | `skills/public` + `skills/custom` 扫描 SKILL.md 挂载 | 同上；格式口径见 HARNESS.md §3.5 |
| memory | agent 全局记忆队列 + 存储 | 同上 |
| channels | IM 接入层（钉钉/飞书/Discord 等） | 同上 |
| config schema | `config.yaml` + `config.example.yaml`，结构性改动递增 `config_version` | `backend/AGENTS.md`「Config 关键口径」节 |
| brand pack | 课程视频品牌包（配色/字体/版式），单一事实源 | `backend/app/gateway/video_gen/brand_pack.py` |
| 视频生成 | 多 video_type 编排骨干（script→storyboard→TTS→subtitle→visual→compose） | `backend/docs/VIDEO_GEN_NOTES.md` |
| 任务管线 | 所有异步能力的统一底座 | `task-pipeline.md` |

前端侧：路由分区/视频工作台/状态管理 → `frontend/docs/FRONTEND_MODULE_NOTES.md`；约定与坑 → `frontend/AGENTS.md`。

## 4. remotion 管线在拓扑中的位置

`video_type="remotion_course"` 是异步任务链上的一个任务类型：gateway 完成解析/分镜/素材后置 `composing`，由 `WORKER_POOL=remotion` 的独立池认领（认领谓词固定二元组），用 `video-templates/course-standard/` 模板渲染。模板改动必须重建 remotion-worker 镜像（TEMPLATE_VERSION 防漂移）。全部业务口径见 `remotion-course-pipeline.md`。

## 5. 规模演进注记（为什么是单机多进程而非微服务）

目标档位是单机服务 10~20 人（未来大几十人）、离线环境、U 盘堡垒机部署：微服务的服务发现/多机编排在这个档位是纯成本；单机多进程 + 任务表队列已提供所需的隔离（重 IO 进 worker 池）与可见性（任务状态全在 `unified_tasks` 可查）。扩容路径是加 worker 副本与调并发槽（compose 副本数 × `MAX_CONCURRENT_TASKS`），不是拆服务。
