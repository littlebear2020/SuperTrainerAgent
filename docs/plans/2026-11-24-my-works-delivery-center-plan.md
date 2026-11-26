# 我的作品真实交付文件中心实施计划

> **状态**：已执行（2026-11-25 实现，2026-11-26 [PR #63](https://github.com/littlebear2020/SuperTrainerAgent/pull/63) rebase 合入 main，merge commit `cfbd120b`；Task 0–9、行为验收、OCR/CI、合入与清理全部完成；执行记录见 [changelog-2026-11-25-my-works-delivery-center.md](../changelog/changelog-2026-11-25-my-works-delivery-center.md)；定稿基准 `ab92a527`，执行基线 `64ef5b52`）。
> **TL;DR**：把四类 Agent 的对话历史升级为真实交付文件卡，并统一六类作品的文件信息、搜索与查看器；保留现有任务状态、详情、分镜、重试、删除、URL 深链和可访问性；不改数据库、Agent runtime、技能、媒体管线或部署配置。
> **For agentic workers：** REQUIRED SUB-SKILL：使用 `subagent-driven-development`（推荐）或 `executing-plans` 逐项实施；所有步骤使用 checkbox 跟踪，先 RED 后 GREEN。执行指令一经下达即端到端完成全部勾选项，任务之间不停下等待评审——评审移入 PR（见任务 9）；勾选框之外的小节是不可执行的说明，不构成待办。

**Goal**：让 `/workspace/video-gen` 的六类「我的作品」都以一次真实创作为卡片，直接查看、下载其交付文件，并能回到原对话或任务继续工作。

**Architecture**：保留现有 `useThreads` 完整分页和 `useVideoTasks` 数据源，新增每批最多 50 个线程的 artifact 摘要 API，避免浏览器逐卡 N+1；前端把 Agent 与任务统一归一化为 `WorkSummary`，由共享网格、卡片和查看器消费。`course-video` 合并 Agent 对话与 Remotion 任务，`ai_video` 同时收 `ai_video` 与 `vimax`；其余分类维持单一来源。首版不做跨域服务端大聚合、不引入文件版本快照。

**Tech Stack**：Python 3.12+、FastAPI、Pydantic、LangGraph Checkpointer、pytest；Next.js App Router、React、TypeScript、Tailwind CSS 4、TanStack Query、现有 shadcn/ui、Lucide、Vitest、Playwright；不新增依赖。

**Spec / 需求来源**：用户 2026-11-24 对 Demo 与当前代码研究的评审请求及后续实施计划请求；Demo 源模型 `temp/worktree-archive-20261122/home-plan/temp/my-works-artifact-demo-2026-11-22/src/App.tsx`；现行信息架构与行为保全基线 `docs/plans/2026-11-13-works-assets-ia-cleanup-plan.md`；六能力来源 `docs/plans/2026-11-12-workspace-experience-and-pricing-optimization-plan.md`；Remotion 语义 `docs/remotion-course-pipeline.md`。

## 一、范围与非范围

### 本批交付

1. 新增 Agent 线程 artifact 摘要服务与批量只读 API：文件路径、文件名、媒体类型、大小、修改时间、可读状态和交付状态。
2. 视频任务列表新增安全的 `delivery` 元数据，不向新 UI 暴露宿主绝对路径。
3. 建立六能力统一 `WorkSummary`、来源映射、稳定排序、标题与文件名搜索、每次 6 条的本地展示分页。
4. 用真实交付文件卡替换四类 Agent 的纯对话 Motif 卡；未交付对话仍保留并可继续创作。
5. 用共享查看器承载视频、图片、音频和通用文件详情；同对话多文件可切换，不宣称版本管理。
6. 保留任务所有状态、详情、分镜、重试、删除确认和旧后端兼容降级。
7. 新增文案的完整中英文、暗色、移动端、键盘、错误／空态和既有路由回归。
8. 已取消任务的标签改为「已取消」，不再落入默认的「排队中」；共享状态徽标同步改为中性、不旋转，作品卡与详情页一致。

### 明确不做

- 不新增「全部作品」Tab，不做跨来源服务端排序、全局搜索或服务端游标分页。
- 不做同名文件快照、版本恢复、交付事件审计、收藏、批量下载或分享。
- 不为 Agent 视频生成首帧、不新增转码、Range、缩略图缓存或 CDN；主动打开查看器时才请求媒体内容。
- 不修改 `present_files`、`merge_artifacts`、checkpoint schema、数据库表或迁移。
- 不修改 Agent SOUL、技能、worker、Remotion 模板、Docker、nginx、config schema 或鉴权中间件。
- 不改造 Writer、Gallery、资产中心、创建弹窗、任务详情和分镜编辑器内部行为。
- 不调用真实付费生成 API；测试使用临时文件、隔离存储和 API mock。
- 不保留对话卡的「N 轮对话」轮数：该显示依赖 `values.messages`，而 `/threads/search` 只返回标题，生产上从未显示过。
- 不英文化既有的状态标签与任务类型标签（`getVideoTaskStatusLabel`、`videoTaskTypeLabel` 仍是中文常量，详情页同用）；中英文只覆盖本批新增文案。
- 不改造 artifact GET（整文件读入内存、不支持 Range），已登记 findings backlog；Agent 视频／音频查看器因此沿用整文件下载。
- 不部署：发布顺序与回滚见任务 9 末尾的发布说明，部署须用户另行下达指令。

## 二、评审决策记录

以下是对设计树 frontier 的推荐答案，也是本计划采用的默认值。Q1–Q12 为定稿决策，Q13–Q16 为 2026-11-24 两轮评审优化新增（用户已同意）。任何一项改变，都应在执行前回写对应任务和测试。

| Frontier | 推荐决策 | 影响与理由 |
|---|---|---|
| Q1 卡片粒度 | 一个 Agent 对话或一个视频任务 = 一张创作记录卡；文件归属于卡片 | 不把每个辅助文件误当独立作品；同对话全部文件在查看器可达。 |
| Q2 六类结构 | 保留现有六能力和 URL，不新增「全部」 | 避免本批同时承担跨域聚合与分页。 |
| Q3 任务归属 | `remotion_course/remotion → course-video`；`ai_image/ai_visual → ai_image`；`ai_video/vimax → ai_video` | `remotion` 仅作旧任务只读兼容，不与 `remotion_course` 管线混称；ViMax 卡仍显示「ViMax 故事」。 |
| Q4 Agent 数据获取 | 保留完整线程搜索，新增 `POST /api/works/artifact-summaries`，每批最多 50 个 ID | 保留跨全部历史的本地搜索，消除逐卡请求，不改 thread_meta 查询层。 |
| Q5 搜索与排序 | 搜索作品标题和文件名；统一按 `updated_at ?? created_at` 倒序，空／坏时间最后，ID 稳定决胜 | 文件名搜索基于已加载摘要；任务排序从旧 `created_at` 有意调整为与展示时间一致。 |
| Q6 100 条上限 | 移除前端最近 100 条截断，仍首屏 6、每次 +6 | 接口已返回的任务不再不可达；DOM 数量仍受控，服务端分页留后续。 |
| Q7 多文件语义 | UI 一律称「交付文件」，按文件修改时间排序；同名路径只代表当前内容 | 不伪造 `v1/v2` 关系，不出现「恢复版本」。 |
| Q8 封面与流量 | 任务继续用真实 `thumbnail_url`；Agent 图片可懒加载；Agent 视频／音频／PPT 用带真实文件名的类型封面 | 当前 artifact GET 会读完整二进制，禁止列表预载视频首帧。 |
| Q9 查看体验 | 共用 Dialog 查看器，替换任务卡内联播放器 | 避免网格撑高；媒体只在选中后挂载，关闭即卸载。 |
| Q10 任务操作 | ready=查看，draft=继续编辑，failed=重试；详情与删除保留，重试与删除仍二次确认 | 不为了接近 Demo 三按钮而删掉真实任务能力。 |
| Q11 视觉与可访问性 | 沿用 WorkspaceSidebar、设计 token、暗色、i18n、现有胶囊 Tab、ARIA 与 URL | Demo 只借信息层级，不迁独立侧栏和硬编码浅色皮肤。 |
| Q12 新旧后端兼容 | 旧响应没有 `delivery` 时仍可用现有任务下载 URL 查看／下载，文件名显示「由下载响应确定」 | 避免制造假文件名。只支持「新 backend + 旧 frontend」与回滚：新 frontend 遇旧 backend 时四类 Agent 显示可重试的错误态（摘要接口 404），故 backend 必须先于 frontend 发布。 |
| Q13 已取消任务标签 | `cancelled` 显示「已取消」，中性样式、不旋转；改的是共享 `StatusBadge`，作品卡与详情页同时生效 | 文案落入默认「排队中」（`task-status-label.ts` 的 default 分支），样式落入默认的 indigo + 旋转加载图标（`StatusBadge` 的 else 分支），旧测试把前者钉住了；只改文案会让详情页出现「已取消」配转圈。 |
| Q14 对话轮数 | 不保留「N 轮对话」 | 轮数依赖 `values.messages`，search 不返回，生产上从未显示；钉它的两条旧测试标记为不再适用。 |
| Q15 摘要读取并发 | 逐线程顺序读取，不设并发上限配置 | checkpointer 在全局锁内反序列化整个 checkpoint，并发无收益（定稿阶段本机合成观察：`gather + Semaphore(4)` 与顺序循环同为 20–81 ms／50 线程，不作为性能保证）；少一个环境变量和一条测实现细节的测试。 |
| Q16 刷新状态 | 刷新按钮用本地 pending 包住 `onRetry()`，不聚合各来源的 `isRefetching`；刷新失败时已有作品保留 | `useVideoTasks` 有活跃任务时每 5 秒轮询，聚合 `isRefetching` 会让按钮每 5 秒转一次圈；刷新失败就把能用的列表换成错误态是倒退。 |

## 三、现状事实与行为保全

### 已核实的现状

- 六能力与 canonical URL 已在 `frontend/src/core/workspace/video-assets-route.ts` 和 `video-gen-page.tsx` 落地，含 roving ARIA；必须复用，不重写路由。
- 四类 Agent 当前按 metadata 过滤对话，卡片只显示标题、时间与 Motif（「N 轮对话」的代码路径依赖 `values.messages`，而 `/threads/search` 只投影 `values.title`，生产上从未显示）；该接口不能提供 artifacts。
- `thread.values.artifacts` 是 `present_files` 写入的路径数组；reducer 去重且保首次顺序，不包含文件 stat 或版本快照。
- `/api/video/tasks` 已生成 `TaskDetail.result`，其中通常有 `output_path/file_size`；前端 `VideoTaskBrief` 尚无正式交付契约。
- 现有 `selectCompletedWorks` 只收 `ai_image/ai_visual/ai_video` 并截 100 条，Remotion 与 ViMax 在六类作品墙不可达。
- 通用 artifact GET 对最终二进制分支使用 `read_bytes()`，因此本批禁止卡片 `<video preload="metadata">`。
- 批量读 checkpoint 成本可接受：`AsyncSqliteSaver.aget_tuple` 在全局锁内反序列化整个 checkpoint，2026-11-24 用合成 checkpoint 实测 50 线程一批 15–81 ms（每线程 30–300 条消息，75–750 KB）；成本随 checkpoint 体积线性增长，任务 9 记录实测耗时。这是定稿阶段本机的合成观察（纯文本消息，脚本未入库），不作为性能保证，以任务 9 执行时的实测为准。
- `/api/video/tasks` 列表在事件循环上对每个 ready 任务做一次 `Path.exists()` 判定 `artifact_missing`；本批在同一函数内对存在的文件多一次 `stat`，同量级。
- 任务状态标签 `cancelled` 落入默认「排队中」（`frontend/src/core/video-gen/task-status-label.ts:29`）。

### 旧功能保全表

| 场景 | 必须保持的行为 |
|---|---|
| ready 且产物存在 | 真实任务封面；查看、下载、详情、删除可用；下载仍走 `/api/video/tasks/{id}/download`。 |
| ready 且 `artifact_missing=true` | 保留记录并显示产物已清理；查看与下载不可用，不发媒体请求。 |
| draft 且 progress>=100 | 主行动进入原 storyboard 路由；详情与删除可用。 |
| draft 且 progress<100 | 显示生成分镜，不出现播放／下载／继续编辑。 |
| failed | 真实错误完整可见；`recomposeActionFor` 与确认弹层语义不变。 |
| running/composing/qc_repair/pending | 原状态标签不丢；不开放无效媒体操作；详情与删除不回归。 |
| cancelled | 标签显示「已取消」，不旋转；无媒体操作；详情与删除可用。 |
| 刷新失败且已有作品 | 保留现有作品，工具条显示错误与重试；不清空为空态。 |
| Agent 无交付文件 | 对话不消失，显示未交付并继续原 canonical 对话。 |
| Agent 多文件／部分缺失 | 全部交付记录可见；可用文件可查看下载，缺失文件禁用媒体操作。 |
| Agent `unknown`（checkpoint 缺失或 artifacts 损坏） | 对话卡保留，文件块显示「文件信息不可用」；主行动为继续对话，不出现查看／下载。 |
| 旧 Agent 别名 | `agentNameOfThread` 与 `pathOfThread` 继续处理旧 `project_type` 和 canonical URL。 |
| 六能力与资产视图 | `tab/capability`、无关键 query、浏览器前进后退、键盘 Tab、项目资产行为不变。 |

## 四、接口与文件地图

### 后端契约

新增 `POST /api/works/artifact-summaries`：

- 请求：`{ "thread_ids": string[] }`；原始数组 1–50 项，ID trim 后非空、最长 128 字符；按首次出现去重。
- 鉴权：要求认证和 `threads:read`；逐 ID `check_access(..., require_existing=True)`，任一不可访问则在读取 checkpoint／文件前整体 404。
- 响应：按去重后的请求顺序返回 `ThreadArtifactSummary[]`，不返回消息正文或宿主路径。
- 单线程状态：`delivered | not_delivered | partial | missing | unknown`。
- 单文件字段：`path`（仅 `/mnt/user-data/outputs/**` 虚拟路径）、`name`、`media_kind`、`size`、`modified_at`、`exists`。
- `media_kind`：`video | image | audio | document | other`；文件按「可读优先、修改时间倒序、路径字典序」稳定排列。

`media_kind` 按扩展名（小写）映射，无扩展名或未命中即 `other`。SVG／HTML／XHTML 是后端强制下载的主动内容，一律不内联预览：

| `media_kind` | 扩展名 | 前端处理 |
|---|---|---|
| `image` | `png` `jpg` `jpeg` `webp` `gif` | 懒加载真实图片 |
| `video` | `mp4` `webm` `mov` | 查看器内播放 |
| `audio` | `mp3` `wav` `m4a` `aac` `ogg` | 查看器内播放 |
| `document` | `pptx` `ppt` `pdf` `docx` `doc` `xlsx` `xls` `md` `txt` `csv` `json` | 类型封面 + 下载 |
| `other` | 其余全部，含 `svg` `html` `htm` `xhtml` | 类型封面 + 下载，`previewUrl=null` |

- checkpoint 不存在、artifacts 类型损坏，或任一路径非法（不在 `/mnt/user-data/outputs/` 下，或被 `resolve_thread_virtual_path` 拒绝，含 `..` 穿越）时，整个线程为 `unknown`：不回显该路径、不读取该线程的任何目标文件，服务端日志不带路径原文。不得伪装 `not_delivered`。空 artifact 数组才是 `not_delivered`。
- 路径合法但某个文件 `stat` 抛 `OSError`（含已不存在）只影响该文件：`exists=false`、`size` 与 `modified_at` 为 `null`，同线程其余文件照常。
- 读取按去重后的请求顺序逐线程进行，不并发扇出（Q15）：checkpointer 的全局锁本就串行化读取；单个文件的 `stat` 经 `asyncio.to_thread` 执行，不阻塞事件循环。

`TaskBrief` 增加可选 `delivery: TaskDeliveryMetadata | null`：

- 字段：`file_name`、`file_size`、`file_updated_at`、`media_kind`、`exists`。
- 仅 `video_gen` ready 任务由 `result.output_path` 派生；非 ready 不回传陈旧交付信息。
- `exists`、`artifact_missing` 与 `delivery` 的大小和 mtime 必须取自同一次 `stat`；缺失时名可保留、大小／时间为 null、`exists=false`。
- 不新增 `output_path` 到响应模型；既有 `result` 兼容字段不在本批扩大或删除。

### 前端契约

`frontend/src/core/works/types.ts` 定义：

```ts
type WorkSource =
  | { kind: "agent"; thread: AgentThread }
  | { kind: "video_task"; task: VideoTaskBrief };

type AgentDeliveryState = "delivered" | "not_delivered" | "partial" | "missing" | "unknown";

type WorkFile = {
  id: string;
  name: string | null;
  mediaKind: "video" | "image" | "audio" | "document" | "other";
  size: number | null;
  modifiedAt: string | null;
  exists: boolean;
  previewUrl: string | null;
  downloadUrl: string | null;
};

type WorkSummary = {
  id: string;
  capability: WorkCapability;
  title: string;
  source: WorkSource;
  agentDeliveryState: AgentDeliveryState | null;
  sortAt: string | null;
  files: WorkFile[];
  primaryFile: WorkFile | null;
};
```

- `id` 固定为 `thread:<thread_id>` 或 `task:<task_id>`，防止跨来源碰撞。`WorkFile.id`：Agent 文件为 `thread:<thread_id>:<虚拟路径>`，任务文件为 `task:<task_id>:output`。
- `sortAt` 是来源的 `updated_at ?? created_at` 原始字符串，排序与展示都用它（排序解析失败排最后，展示解析失败显示「更新时间未知」）；不设重复字段。
- `title` 保存 trim 后的原始标题，允许为空字符串；空标题由卡片显示 locale 的「未命名作品」。不把任何语言的兜底文案写进模型——现有 `titleOfThread` 的 `"Untitled"` 与任务卡的「未命名作品」都不带进来。
- Agent URL 由 `urlOfArtifact` 生成；任务 URL 由 `getVideoDownloadUrl` 生成。
- 任务 `primaryFile` 为当前输出；Agent 为排序后的首个可读文件。
- 旧后端缺 `delivery` 的 ready 任务仍生成一个 `name=null` 的当前输出文件，保持查看／下载能力。
- `WorkCapability` 与现有六能力 SoT 复用，不新建第七种能力。

### 文件职责

**Backend**

- Create `backend/app/gateway/works/__init__.py`：Works 应用服务包标记。
- Create `backend/app/gateway/works/schemas.py`：批量请求、文件摘要、线程摘要、交付状态。
- Create `backend/app/gateway/works/service.py`：checkpoint 读取、路径校验、stat、媒体分类、稳定排序和逐线程摘要。
- Create `backend/app/gateway/routers/works.py`：批量 endpoint、认证、逐 ID owner 检查。
- Modify `backend/app/gateway/routers/__init__.py`、`backend/app/gateway/app.py`：注册 router。
- Modify `backend/app/gateway/_shared/task_framework/schemas.py`：`TaskDeliveryMetadata` 与 `TaskBrief.delivery`。
- Modify `backend/app/gateway/routers/video_gen.py`：ready 任务交付投影，保留现有 missing 判定。

**Frontend core**

- Create `frontend/src/core/works/types.ts`：上述共享类型与 API DTO。
- Create `frontend/src/core/works/capability-sources.ts`：六能力来源与穷尽任务类型映射。
- Create `frontend/src/core/works/api.ts`：批量摘要请求。
- Create `frontend/src/core/works/model.ts`：归一化、排序、搜索、批次切分与主文件选择。
- Create `frontend/src/core/works/hooks.ts`：单 Query 顺序分批请求、来源启停、统一加载／错误／刷新。
- Modify `frontend/src/core/threads/hooks.ts`：`useThreads(params, options?: { enabled?: boolean })`，默认行为不变。
- Modify `frontend/src/core/video-gen/hooks.ts`：`useVideoTasks(options?: { enabled?: boolean })`，默认行为与轮询不变。
- Modify `frontend/src/core/video-gen/types.ts`：任务 `delivery` 类型。
- Modify `frontend/src/core/video-gen/task-status-label.ts`：`cancelled` → 「已取消」。

**Frontend UI**

- Create `frontend/src/components/workspace/video-gen/work-media.tsx`：卡片真实封面／类型封面与查看器媒体内容。
- Create `frontend/src/components/workspace/video-gen/work-viewer-dialog.tsx`：文件切换、媒体错误降级、下载与来源行动。
- Create `frontend/src/components/workspace/video-gen/work-card.tsx`：统一卡片和来源特定操作。
- Create `frontend/src/components/workspace/video-gen/work-grid.tsx`：搜索、计数、6 条分页、状态与选中作品。
- Create `frontend/src/components/workspace/video-gen/status-badge.tsx`：共享 `StatusBadge` 与 `getActiveStatusLabel`，补 cancelled 分支。
- Modify `frontend/src/components/workspace/video-gen/video-gen-page.tsx`：统一 WorksView、真实刷新、页头说明，`StatusBadge` 改为 re-export；保留 AssetsView 与 legacy TaskCard 导出。
- Delete `frontend/src/components/workspace/video-gen/agent-conversation-grid.tsx`：所有适用断言迁移后删除。
- Delete `frontend/src/components/workspace/video-gen/completed-video-grid.tsx`：所有状态与操作断言迁移后删除。
- Modify `frontend/src/app/workspace/video-gen/page.tsx`：Suspense 骨架匹配新页头／工具条／网格。
- Modify `frontend/src/core/i18n/locales/{types,zh-CN,en-US}.ts`：新作品、文件、状态、查看器和错误文案。

**Tests**

- Create `backend/tests/test_works_artifact_summaries.py`。
- Create `backend/tests/test_works_router.py`。
- Create `backend/tests/test_video_gen_delivery_metadata.py`。
- Create `frontend/tests/unit/core/works/{api,model}.test.ts`、`hooks.dom.test.tsx`。
- Modify `frontend/tests/unit/core/video-gen/task-status-label.test.ts`：钉住 `cancelled` → 「已取消」。
- Modify `frontend/tests/unit/components/workspace/video-gen/status-badge.dom.test.tsx`：钉住 cancelled 徽标中性、不旋转。
- Create `frontend/tests/unit/components/workspace/video-gen/{work-viewer-dialog,work-grid}.dom.test.tsx`。
- Modify `frontend/tests/unit/components/workspace/video-gen/video-gen-page-url.dom.test.tsx`。
- Modify／迁移旧 `agent-conversation-grid.dom.test.tsx`、`completed-video-grid.dom.test.tsx`、`completed-video-grid.test.ts` 的仍适用断言。
- Modify `frontend/tests/e2e/utils/{mock-api,mock-video-api}.ts`：可播种文件摘要与 delivery。
- Modify `frontend/tests/e2e/work-capabilities.spec.ts`。
- Create `frontend/tests/e2e/works-delivery.spec.ts`。

## 五、路径边界与共享基座

| 允许路径（allow） | 禁改路径（ban） | 共享基座触达 |
|---|---|---|
| 上述 backend works／router／task schema／video_gen 列表投影、frontend works／作品卡／hooks／i18n／任务状态标签 helper、对应测试、plan 与执行 changelog | `components/ui/**`、`components/ai-elements/**`、`skills/**`、`backend/seed/agents/**`、`docker/**`、`video-templates/**`、`config*.yaml`、auth middleware、runtime reducer、DB migration、Writer／Gallery／资产中心内部实现 | 是；`app.py`、router registry、task schema、thread/video hooks、locale、E2E mock 由单执行者串行修改，不做并行派发 |

执行中若发现必须触碰 ban 路径，立即停止并回规划侧，不私自扩大范围。

计划间重叠（2026-11-24 评审补）：与 `docs/plans/2026-11-24-personal-usage-layout-redesign-plan.md` 共享 `frontend/src/core/i18n/locales/{types,zh-CN,en-US}.ts`，路径重叠表非空——两份计划串行执行、不并行派发；对方先合入时，本批先 rebase 再追加文案（对方只追加两个 key）。

## 六、Review Focus

1. **批次混入他人 thread ID**：必须在任何 checkpoint／文件 stat 前 404；由任务 2 `rejects_foreign_thread_before_any_state_read` 钉扎。
2. **摘要尚未返回或请求失败**：不得瞬间显示「未交付」或空列表；由任务 5 和 7 的 loading/error 分离测试钉扎。
3. **同路径覆盖、不同路径多文件、部分缺失**：只能展示当前交付文件事实，不得造版本；由任务 1、4、6、7 联合覆盖。
4. **旧任务响应缺 delivery 且状态多样**：查看／下载／分镜／重试／删除不能因统一卡片消失；由任务 4、7、8 的兼容矩阵钉扎。
5. **媒体网络与自动播放**：列表不得请求 Agent 视频／音频内容；打开才挂载、关闭即卸载；由任务 6 与任务 9 浏览器请求计数验证。
6. **未来新增任务类型**：映射必须编译期穷尽，不得再次静默漏出作品墙；由任务 4 `satisfies Record<VideoTaskType, ...>` 与映射测试钉扎。

---

### Task 0：执行前置——确认计划已入库、工作区与漂移核对

**Files：**
- 无文件改动（只读核对）。

- [x] **Step 1：保护他人改动并确认本计划在执行分支里。** 运行 `git status --short --branch`，以执行时输出为准（定稿时主 clone 有他人未提交改动：`docs/plans/2026-11-17-core-product-frontend-reliability-plan.md` 已修改，多份 2026-11-24 计划未跟踪）；不得 stash、clean、覆盖或提交这些文件。本计划与对应的 backlog 登记行已在 2026-11-24 于 main 上单独提交（未推送，随执行分支的 PR 合入）：用 `git log -1 --oneline -- docs/plans/2026-11-24-my-works-delivery-center-plan.md` 确认该提交在执行分支的历史里。不在，说明分支基于 origin/main 建立——回主 clone 把执行分支 rebase 到本地 main。
- [x] **Step 2：核对漂移后建执行 worktree。** 运行 `git diff ab92a527..main --stat`（只看代码文件；计划与 backlog 的文档提交不算漂移），逐一核对本计划点名的文件是否漂移：`video-gen-page.tsx`、两个旧 grid、`core/threads/hooks.ts`、`core/video-gen/{hooks,types,task-status-label}.ts`、`routers/video_gen.py`、`_shared/task_framework/schemas.py`、三份 locale、E2E mock。与个人用量计划共享三份 i18n：对方先合入则本批先 rebase（见「五、路径边界」）。漂移影响本计划契约时先修订计划再实施。之后按根 AGENTS.md 的 worktree 卫生建分支 `my-works-delivery-center`。

### Task 1：实现 Agent artifact 摘要纯服务

**Files：**
- Create：`backend/app/gateway/works/__init__.py`
- Create：`backend/app/gateway/works/schemas.py`
- Create：`backend/app/gateway/works/service.py`
- Test：`backend/tests/test_works_artifact_summaries.py`

**Interfaces：**
- Produces：`build_thread_artifact_summary(thread_id: str, artifact_paths: object) -> ThreadArtifactSummary`。
- Produces：`load_thread_artifact_summary(thread_id: str, checkpointer) -> ThreadArtifactSummary`。
- Produces：`DeliveryState`、`ArtifactFileSummary`、`ThreadArtifactSummary`，供任务 2 使用。

- [x] **Step 1：先写服务层失败测试。**覆盖：空数组=`not_delivered`；全部存在=`delivered`；部分存在=`partial`；全部缺失=`missing`；checkpoint 不存在／artifacts 非 list 或含非字符串项／任一路径非法（非 outputs 前缀、`..` 穿越、解析器拒绝）=`unknown`，且非法路径不回显、该线程任何目标文件都不读取；重复路径去重；同 mtime 按 path 稳定排序；按「四、后端契约」的映射表分类（每种 `media_kind` 各取一例，含 `svg` 与 `html` 落 `other`、无扩展名落 `other`）；stat 的 size/mtime 正确且 UTC；单文件 `stat` 抛 `OSError` 只让该文件 `exists=false`，同线程其余文件照常。
- [x] **Step 2：运行并确认 RED。**Run：`cd backend && PYTHONPATH=. uv run pytest tests/test_works_artifact_summaries.py -q`。Expected：模块不存在导致 FAIL。
- [x] **Step 3：实现 schema 与同步 stat 投影。**先做 outputs 前缀检查再调用 `resolve_thread_virtual_path`——解析器本身接受整个 `/mnt/user-data/`，不会替你限定 outputs。前缀不符或解析器抛错即判整个线程 `unknown`，日志只记 thread_id 与原因类别。路径合法时单个文件 `stat` 的 `OSError` 只标该文件 `exists=false`，不泄漏异常中的宿主路径。文件 mtime 用 `datetime.fromtimestamp(stat.st_mtime, UTC)`；使用 stat 结果而非 result 缓存猜大小。`media_kind` 按「四、后端契约」的映射表分类。
- [x] **Step 4：实现 checkpoint 摘要读取。**以 `configurable.thread_id/checkpoint_ns` 取最新 tuple；不存在返回 `unknown`。文件系统投影放入 `asyncio.to_thread`；不要在服务内做权限判断或返回消息正文。
- [x] **Step 5：运行并确认 GREEN。**Run：同 Step 2。Expected：全部 PASS；再跑 `uv run ruff check app/gateway/works tests/test_works_artifact_summaries.py`。
- [x] **Step 6：提交。**`git add backend/app/gateway/works backend/tests/test_works_artifact_summaries.py && git commit -m "feat(works): 增加对话交付文件摘要服务"`。

### Task 2：提供批量摘要 API 与逐线程权限守卫

**Files：**
- Create：`backend/app/gateway/routers/works.py`
- Modify：`backend/app/gateway/routers/__init__.py`
- Modify：`backend/app/gateway/app.py`
- Test：`backend/tests/test_works_router.py`

**Interfaces：**
- Consumes：任务 1 的 `load_thread_artifact_summary`。
- Produces：`POST /api/works/artifact-summaries`，供任务 4 前端 API 使用。

- [x] **Step 1：先写路由失败测试。**覆盖未认证 401、无权限 403、空批次／>50／空白 ID 422、重复 ID 去重且响应顺序稳定、合法批次返回文件摘要、任一 foreign ID 返回 404 且 checkpointer 与 stat 零调用、缺失 meta 行按 `require_existing=True` 拒绝、50 条合法请求全部返回。
- [x] **Step 2：运行并确认 RED。**Run：`cd backend && PYTHONPATH=. uv run pytest tests/test_works_router.py -q`。Expected：路由不存在或 404。
- [x] **Step 3：实现 router。**使用认证与 `@require_permission("threads", "read")`；从 `AuthContext` 取用户，对所有 ID 先完成 `check_access` 再开始任何摘要读取。按去重后的输入顺序逐个 `await` 摘要读取（Q15：不并发扇出），结果顺序即输入顺序。
- [x] **Step 4：注册路由并限制响应面。**在 router `__init__` 和 `app.py` 采用现有注册风格；响应只含任务 1 DTO，不嵌入 `messages`、`payload`、宿主绝对路径。
- [x] **Step 5：运行并确认 GREEN 与权限回归。**Run：`cd backend && PYTHONPATH=. uv run pytest tests/test_works_router.py tests/test_works_artifact_summaries.py tests/test_artifacts_router.py tests/test_video_gen_router_authz.py -q`。Expected：PASS。
- [x] **Step 6：提交。**`git add backend/app/gateway/routers/works.py backend/app/gateway/routers/__init__.py backend/app/gateway/app.py backend/tests/test_works_router.py && git commit -m "feat(works): 增加批量交付摘要只读接口"`。

### Task 3：为视频任务补安全交付元数据

**Files：**
- Modify：`backend/app/gateway/_shared/task_framework/schemas.py`
- Modify：`backend/app/gateway/routers/video_gen.py`
- Test：`backend/tests/test_video_gen_delivery_metadata.py`
- Regression：`backend/tests/test_video_gen_router.py`、`backend/tests/test_thumbnail.py`

**Interfaces：**
- Produces：`TaskDeliveryMetadata` 与 `TaskBrief.delivery`，供任务 4 归一化。

- [x] **Step 1：先写失败测试。**构造 ready MP4 的真实临时文件，断言文件名、字节数、mtime、media_kind、exists；ready 但输出丢失返回 exists=false；非 ready 即使 result 有旧 output_path 也 delivery=null；`delivery` 对象不含任何宿主路径（既有 `TaskDetail.result` 里的 `output_path` 不扩大也不删除，断言只针对 `delivery`）；其他域 TaskBrief 默认 delivery=null。
- [x] **Step 2：运行并确认 RED。**Run：`cd backend && PYTHONPATH=. uv run pytest tests/test_video_gen_delivery_metadata.py -q`。Expected：缺少 delivery 字段。
- [x] **Step 3：实现最小投影。**在 `_task_with_thumbnail` 内沿用同一个 output path 真相；ready 且路径字符串存在时生成 delivery，存在文件用 `Path.stat()` 一次取 size/mtime。`exists`、`artifact_missing` 与 delivery 的 size/mtime 必须取自同一次 `stat`（`FileNotFoundError` 即缺失），语义与现有 `Path(out).exists()` 判定一致——列表接口对这个函数没有 try/except，`exists()` 之后再 `stat()` 的竞态会让整个任务列表 500。保持 thumbnail 行为不变，不改 worker 或 result 写库。
- [x] **Step 4：运行并确认 GREEN。**Run：`cd backend && PYTHONPATH=. uv run pytest tests/test_video_gen_delivery_metadata.py tests/test_video_gen_router.py tests/test_thumbnail.py -q`。Expected：PASS。
- [x] **Step 5：提交。**`git add backend/app/gateway/_shared/task_framework/schemas.py backend/app/gateway/routers/video_gen.py backend/tests/test_video_gen_delivery_metadata.py && git commit -m "feat(works): 补充视频任务交付文件元数据"`。

### Task 4：建立前端统一模型、穷尽映射与 API client

**Files：**
- Create：`frontend/src/core/works/types.ts`
- Create：`frontend/src/core/works/capability-sources.ts`
- Create：`frontend/src/core/works/api.ts`
- Create：`frontend/src/core/works/model.ts`
- Modify：`frontend/src/core/video-gen/types.ts`
- Test：`frontend/tests/unit/core/works/api.test.ts`
- Test：`frontend/tests/unit/core/works/model.test.ts`

**Interfaces：**
- Produces：`fetchArtifactSummaries(threadIds: string[]): Promise<ThreadArtifactSummary[]>`。
- Produces：`buildAgentWorks(threads: readonly AgentThread[], summaries: readonly ThreadArtifactSummary[], capability: WorkCapability): WorkSummary[]`。
- Produces：`buildTaskWorks(tasks: readonly VideoTaskBrief[], capability: WorkCapability): WorkSummary[]`。
- Produces：`selectAndFilterWorks(works: readonly WorkSummary[], query: string): WorkSummary[]`——先按 Q5 排序，再按标题与文件名过滤。
- Produces：`chunkThreadIds(threadIds: readonly string[], batchSize = 50): string[][]`。
- Produces：`WORK_CAPABILITY_SOURCES`，其任务映射使用 `satisfies Record<VideoTaskType, WorkCapability>`。

- [x] **Step 1：先写模型失败测试。**覆盖六能力来源、所有 `VideoTaskType` 映射、course-video 双来源合并、跨来源 ID 不碰撞、`updated_at ?? created_at` 稳定倒序、无效日期最后、trim/case-insensitive 标题与文件名搜索、主文件选择、Agent 五态、旧任务无 delivery 的 `name=null` 降级、非 ready／missing 无可下载文件、空／纯空白标题保留为空字符串。
- [x] **Step 2：先写 API 失败测试。**mock 现有 `@/core/api/fetcher`，断言 POST URL、JSON body、FastAPI 错误透传、非 OK 不返回空数组。
- [x] **Step 3：运行并确认 RED。**Run：`cd frontend && pnpm exec vitest run --project node tests/unit/core/works/api.test.ts tests/unit/core/works/model.test.ts`。Expected：模块不存在。
- [x] **Step 4：实现类型、来源表和纯函数。**`chunkThreadIds` 先排序去重再按 50 切批；归一化不修改原 tasks/threads；文件大小展示留给 UI 的 `formatFileSize`，0 字节如实显示 `0 B`，未知显示 `—`。
- [x] **Step 5：实现 API client 和任务 delivery 类型。**复用现有错误读取与 URL helper；不得新建第二套 artifact URL 编码逻辑，不把 task result 的宿主路径写入 WorkSummary。
- [x] **Step 6：运行并确认 GREEN。**Run：同 Step 3，加 `pnpm exec tsc --noEmit --pretty false`。Expected：测试 PASS、类型检查无新错误。
- [x] **Step 7：提交。**`git add frontend/src/core/works frontend/src/core/video-gen/types.ts frontend/tests/unit/core/works && git commit -m "feat(works): 建立统一作品与交付文件模型"`。

### Task 5：实现分批数据 hooks 与来源启停

**Files：**
- Create：`frontend/src/core/works/hooks.ts`
- Modify：`frontend/src/core/threads/hooks.ts`
- Modify：`frontend/src/core/video-gen/hooks.ts`
- Test：`frontend/tests/unit/core/works/hooks.dom.test.tsx`

**Interfaces：**
- Consumes：任务 2 API、任务 4 model。
- Produces：`useWorks(capability: WorkCapability)`，返回 `{ works, isLoading, isError, refetch }`；`isError` 为真时 `works` 仍是最近一次成功的数据（可能为空数组）。
- Produces：`useArtifactSummaries(threadIds: readonly string[])`，内部使用一个 TanStack `useQuery`，在 queryFn 中顺序消费 50 条批次。

- [x] **Step 1：先写 hooks 失败测试。**101 个 ID 顺序产生 50/50/1 三批且最多 1 个批次请求在途；重复 ID 不重复请求；输入顺序变化不改变 query key；0 ID 不请求；摘要 loading 时不派生 not_delivered；任一批失败返回 error、已成功批次不得冒充完整摘要，retry 重新执行该 Query；agent-only 不取 video tasks，task-only 不取 threads，course-video 两者都取。
- [x] **Step 2：运行并确认 RED。**Run：`cd frontend && pnpm exec vitest run --project dom tests/unit/core/works/hooks.dom.test.tsx`。Expected：`useWorks` 不存在或 enabled 参数不存在。
- [x] **Step 3：给现有 hooks 加可选 enabled。**默认 `true`，不改变已有调用方 query key、分页、refetchInterval 和窗口焦点语义；disabled 查询不得计入新 `useWorks` 的 loading/error。
- [x] **Step 4：实现顺序分批与组合。**Query key 含排序去重后的完整 ID 数组；queryFn 用 `for...of` 顺序等待每个 50 条批次，禁止 `Promise.all` 并行打满批次。仅当前能力线程进入摘要请求。`refetch` 同时刷新实际启用来源与摘要；error 不得被归一化为空 works，部分批次结果不得进入模型。
- [x] **Step 5：运行并确认 GREEN 与相关回归。**Run：`cd frontend && pnpm exec vitest run --project dom tests/unit/core/works/hooks.dom.test.tsx tests/unit/components/workspace/video-gen/video-gen-page-url.dom.test.tsx`。Expected：全部 PASS，现有 hooks 默认行为和旧页面测试无回归。
- [x] **Step 6：提交。**`git add frontend/src/core/works/hooks.ts frontend/src/core/threads/hooks.ts frontend/src/core/video-gen/hooks.ts frontend/tests/unit/core/works/hooks.dom.test.tsx && git commit -m "feat(works): 接入分批作品摘要数据层"`。

### Task 6：实现真实媒体与共享作品查看器

**Files：**
- Create：`frontend/src/components/workspace/video-gen/work-media.tsx`
- Create：`frontend/src/components/workspace/video-gen/work-viewer-dialog.tsx`
- Modify：`frontend/src/core/i18n/locales/types.ts`
- Modify：`frontend/src/core/i18n/locales/zh-CN.ts`
- Modify：`frontend/src/core/i18n/locales/en-US.ts`
- Test：`frontend/tests/unit/components/workspace/video-gen/work-viewer-dialog.dom.test.tsx`

**Interfaces：**
- Consumes：`WorkSummary`、`WorkFile`。
- Produces：`WorkMedia`、`WorkViewerDialog`；查看器受控接收 `work`、`open`、`onOpenChange`、`onContinue`、`onTaskNavigate`。

- [x] **Step 1：先写查看器失败测试。**视频／音频使用 controls 且不 autoplay，图片 object-contain，PPTX／other 显示文件详情与下载；文件缺失禁用查看下载；多文件可切换且当前按钮 aria-pressed=true；关闭卸载 media；media error 显示真实降级但保留下载；Agent 继续对话与任务详情路由各调用正确回调；`files` 为空的 work 不渲染任何 media src 与下载入口；无版本／恢复文案。
- [x] **Step 2：运行并确认 RED。**Run：`cd frontend && pnpm exec vitest run --project dom tests/unit/components/workspace/video-gen/work-viewer-dialog.dom.test.tsx`。Expected：组件不存在。
- [x] **Step 3：实现 WorkMedia。**卡片区：任务有 thumbnail 才渲染图片，Agent image 才懒加载真实图，其他用 Lucide 类型图标 + 真实文件名／格式；不得给列表视频设置 src。查看器区：仅 open 的当前文件设置 media src，`preload="metadata"` 仅允许发生在 Dialog 内。
- [x] **Step 4：实现 Dialog 与文件列表。**默认选 primaryFile；切换作品时重置当前文件；当前文件不存在时不猜 URL。PPTX 明确「下载后使用 PowerPoint/WPS 查看编辑」，不引入 iframe 伪造预览。
- [x] **Step 5：补齐中英文与暗色 token。**所有新文案入 locale；字体至少使用现有 xs/sm 档，不复制 Demo 大量 10px 低对比文本；Dialog 保留 Esc、焦点与关闭按钮语义。
- [x] **Step 6：运行并确认 GREEN。**Run：同 Step 2，并跑 `pnpm exec prettier --check src/components/workspace/video-gen/work-*.tsx src/core/i18n/locales/{types,zh-CN,en-US}.ts`。Expected：PASS。
- [x] **Step 7：提交。**`git add frontend/src/components/workspace/video-gen/work-media.tsx frontend/src/components/workspace/video-gen/work-viewer-dialog.tsx frontend/src/core/i18n/locales/types.ts frontend/src/core/i18n/locales/zh-CN.ts frontend/src/core/i18n/locales/en-US.ts frontend/tests/unit/components/workspace/video-gen/work-viewer-dialog.dom.test.tsx && git commit -m "feat(works): 增加统一交付文件查看器"`。

### Task 7：实现统一作品卡、操作矩阵与网格状态

**Files：**
- Create：`frontend/src/components/workspace/video-gen/work-card.tsx`
- Create：`frontend/src/components/workspace/video-gen/work-grid.tsx`
- Create：`frontend/src/components/workspace/video-gen/status-badge.tsx`（共享 `StatusBadge` 与 `getActiveStatusLabel` 从 `video-gen-page.tsx` 原样移入）
- Modify：`frontend/src/components/workspace/video-gen/video-gen-page.tsx`（改为从 `status-badge.tsx` re-export）
- Modify：`frontend/src/core/video-gen/task-status-label.ts`
- Test：`frontend/tests/unit/components/workspace/video-gen/work-grid.dom.test.tsx`
- Test：`frontend/tests/unit/core/video-gen/task-status-label.test.ts`（追加 `cancelled` 用例）
- Test：`frontend/tests/unit/components/workspace/video-gen/status-badge.dom.test.tsx`（追加 cancelled 用例）
- Migrate：旧 Agent／Completed grid 测试中的仍适用断言（映射见「七、旧测试迁移映射」）。

**Interfaces：**
- Consumes：任务 6 查看器、现有 `useRecomposeTask`、`recomposeActionFor`、`RecomposeConfirmDialog`、AlertDialog。
- Produces：`TaskWorkActions`——任务来源卡的底部操作区，每卡一实例，自持该任务的 `useRecomposeTask` 与重试／删除两个确认弹层的开关状态。
- Produces：`WorkGrid({ works, isLoading, isError, onRetry, onContinue, onTaskNavigate, onDelete })`。

- [x] **Step 1：先写卡片操作矩阵失败测试。**逐项覆盖「旧功能保全表」所有任务状态，其中 `cancelled` 标签为「已取消」、徽标中性不旋转；特别断言每个 TaskWorkActions 自己持有 task ID 的 recompose mutation，失败重试与删除均先确认，Agent 不出现任务删除，未交付／missing／unknown Agent 主行动为继续对话，unknown 的文件块显示「文件信息不可用」。同时给 `task-status-label.test.ts` 追加 `cancelled` → 「已取消」用例，给 `status-badge.dom.test.tsx` 追加「cancelled：中性样式、不旋转」用例。
- [x] **Step 2：先写网格失败测试。**首屏 6、每次 +6、超过旧 100 条仍可继续；搜索标题与文件名、清空搜索、计数为创作记录数；切换 capability 通过 key 重置搜索与 visibleCount；loading/error/empty/no-results 分离；展开查看器不撑高卡片；同一行卡片等高、媒体区 16:9、操作区底对齐；卡片内不出现轮数文案；刷新中按钮禁用且不可重复提交，成功后恢复；`isError` 且已有作品时保留作品并在工具条显示错误与重试，不清空成空态。
- [x] **Step 3：运行并确认 RED。**Run：先 `cd frontend && pnpm exec vitest run --project dom tests/unit/components/workspace/video-gen/work-grid.dom.test.tsx tests/unit/components/workspace/video-gen/status-badge.dom.test.tsx`，再 `cd frontend && pnpm exec vitest run --project node tests/unit/core/video-gen/task-status-label.test.ts`（两条分别跑，各自都要红）。Expected：work-grid 组件不存在；`task-status-label` 的 `cancelled` 用例因仍返回「排队中」而 FAIL；`status-badge` 的 `cancelled` 用例因仍旋转而 FAIL。
- [x] **Step 4：实现 WorkCard 与来源操作。**共用 16:9 媒体区、类型／状态、两行标题、文件块、更新时间和底部操作。ready=查看、draft=继续编辑、failed=重试；任务详情与删除可放现有 DropdownMenu，但删除仍 AlertDialog，failed 重试仍 RecomposeConfirmDialog。真实错误不得为等高而截断。先把 `StatusBadge` 与 `getActiveStatusLabel` 原样移到 `status-badge.tsx`、由 `video-gen-page.tsx` re-export（既有导入方不改），再给 `StatusBadge` 补 cancelled 分支：中性样式、不旋转（其余非终态沿用现有旋转样式），详情页随之生效。WorkCard 从 `status-badge.tsx` 导入——任务 8 之后 `video-gen-page.tsx` 会导入 WorkGrid，从它导入徽标会形成循环依赖。
- [x] **Step 5：实现 WorkGrid。**本地 query 搜索全部已归一化 works，visibleCount 只限制 DOM；集成唯一选中作品 Dialog；刷新按钮用本地 pending 状态包住 `await onRetry()`（Q16），pending 期间禁用；`isError` 且 `works` 非空时保留网格，只在工具条显示错误与重试。空分类显示对应能力空态，搜索无结果显示清除搜索，二者不得混用。
- [x] **Step 6：按映射迁移旧测试，不直接删红。**按「七、旧测试迁移映射」逐行落实：每条旧用例要么在新测试里有等价或更强覆盖，要么标注为不再适用并写明原因；全部旧用例有归属后，才删除三个旧测试文件。迁移条数与不再适用条数记入 changelog 验收记录。
- [x] **Step 7：运行并确认 GREEN。**Run：`cd frontend && pnpm exec vitest run --project dom tests/unit/components/workspace/video-gen/work-grid.dom.test.tsx tests/unit/components/workspace/video-gen/work-viewer-dialog.dom.test.tsx tests/unit/components/workspace/video-gen/status-badge.dom.test.tsx && pnpm exec vitest run --project node tests/unit/core/works/model.test.ts tests/unit/core/video-gen/task-status-label.test.ts`。Expected：PASS。
- [x] **Step 8：提交。**只暂存本任务新建／迁移的文件：`work-card.tsx`、`work-grid.tsx`、`task-status-label.ts`、`status-badge.tsx`、`video-gen-page.tsx`（仅徽标移出与 re-export）、`work-grid.dom.test.tsx`、`task-status-label.test.ts`、`status-badge.dom.test.tsx`、旧 grid 测试的删除或迁移文件，以及任务 6 查看器测试的必要同步；使用明确路径列表，不执行目录级 `git add frontend/tests`。提交：`git commit -m "feat(works): 统一六类作品卡片与操作状态"`。

### Task 8：接入六能力页面并保留 URL／资产／旧别名行为

**Files：**
- Modify：`frontend/src/components/workspace/video-gen/video-gen-page.tsx`
- Modify：`frontend/src/app/workspace/video-gen/page.tsx`
- Delete：`frontend/src/components/workspace/video-gen/agent-conversation-grid.tsx`
- Delete：`frontend/src/components/workspace/video-gen/completed-video-grid.tsx`
- Modify：`frontend/src/core/i18n/locales/{types,zh-CN,en-US}.ts`
- Modify：`frontend/tests/unit/components/workspace/video-gen/video-gen-page-url.dom.test.tsx`
- Modify：`frontend/tests/e2e/utils/mock-api.ts`
- Modify：`frontend/tests/e2e/utils/mock-video-api.ts`
- Modify：`frontend/tests/e2e/work-capabilities.spec.ts`
- Create：`frontend/tests/e2e/works-delivery.spec.ts`（核心行为场景，先 RED 后 GREEN）

**Interfaces：**
- Consumes：`useWorks` 与 `WorkGrid`。
- Preserves：`buildVideoAssetsHref`、`pathOfThread`、任务详情／storyboard 路由、`useDeleteVideoTask`。

- [x] **Step 1：先更新页面与 E2E 失败断言。**六能力都渲染统一 `work-card`；course-video 同时出现 Agent 与 remotion_course/remotion，ai_video 同时出现 ai_video/vimax；页头包含「查看、下载交付文件，或回到原对话继续创作」；所有分类可搜索与真实刷新；URL、前进后退、无关键 query、ARIA、资产 CRUD 断言不削弱。同时创建 `works-delivery.spec.ts` 的核心行为场景（三文件 Agent 一卡展示与文件切换、未交付继续对话、partial/missing 操作门控、course-video 混合来源、任务 ready/draft/failed/missing 主行动、旧后端无 delivery 降级、搜索文件名、刷新错误后重试），并在 E2E mock 里补齐摘要与 delivery 的播种支持——RED 要因断言失败而红，不能因夹具缺失而红。
- [x] **Step 2：运行并确认 RED。**先 `cd frontend && pnpm exec vitest run --project dom tests/unit/components/workspace/video-gen/video-gen-page-url.dom.test.tsx`，再 `cd frontend && pnpm exec playwright test tests/e2e/works-delivery.spec.ts`。Expected：页面测试仍渲染旧 AgentConversationGrid／CompletedVideoGrid；新 E2E 因旧 UI 不存在而断言 FAIL。
- [x] **Step 3：接入 WorksView。**按 capability 调 `useWorks`；Agent continue 用 `pathOfThread`，任务 navigate 保留 `/workspace/video-gen/{id}[/storyboard]`，删除复用 mutation。保留现有 capability Tab 实现和 AssetsView，不改 `video-assets-route.ts`。
- [x] **Step 4：移除旧组件。**确认生产与测试均无旧 grid 引用后删除旧组件，不保留隐藏旧 DOM 或双份卡片骗测试。
- [x] **Step 5：更新 Suspense 与 locale。**骨架反映页头、工具条和卡网格；清理只服务旧对话卡的文案前，先 grep `openProject` 等共享键，资产页键不得误删。
- [x] **Step 6：运行并确认 GREEN。**Run：`cd frontend && pnpm exec vitest run --project dom tests/unit/components/workspace/video-gen/video-gen-page-url.dom.test.tsx tests/unit/components/workspace/video-gen/work-grid.dom.test.tsx && pnpm exec playwright test tests/e2e/work-capabilities.spec.ts tests/e2e/works-delivery.spec.ts`。Expected：PASS。
- [x] **Step 7：提交。**只暂存本任务 `Files` 清单中的页面、locale、两个旧 grid 删除路径、明确列出的 mock 与 E2E 文件；禁止目录级 `git add frontend/src frontend/tests`，先 `git diff --name-only` 核对再 `git commit -m "feat(workspace): 将我的作品接入真实交付中心"`。

### Task 9：行为验收、性能复核、全量门禁与发布收口

**Files：**
- Modify：`frontend/tests/e2e/works-delivery.spec.ts`（补齐剩余场景与断言；核心场景已在任务 8 建立）
- Create：`docs/changelog/changelog-<实际执行日>-my-works-delivery-center.md`
- Modify：本计划状态头与执行勾选。

- [x] **Step 1：补齐交付行为 E2E。**核心场景（三文件 Agent 一卡展示与文件切换、未交付继续对话、partial/missing 操作门控、course-video 混合来源、任务 ready/draft/failed/missing 主行动、旧后端无 delivery 降级、搜索文件名、刷新错误后重试）已在任务 8 先 RED 后 GREEN。本步补齐：同名路径不出现版本文案；`unknown` 作品主行动为继续对话且无查看／下载；`cancelled` 任务显示「已取消」且徽标不旋转。
- [x] **Step 2：加入网络与媒体断言。**初次六卡渲染不得请求任何 Agent MP4/MP3 内容；打开一个视频只请求该作品 URL；关闭后 media 元素卸载；图片 lazy；无自动播放；ready missing 的 thumbnail/media/download 请求均为 0。夹具必须含至少一个带 MP4 的 Agent 作品和一个带 PNG 的 Agent 作品，missing 任务带非空 `thumbnail_url`（后端在输出已删、缩略图仍在时就这样下发）——否则这些计数断言恒过。
- [x] **Step 3：加入响应式与可访问性断言。**保留 `reviewWorkCards` 的卡片级几何断言（16:9 媒体区、同行等高、操作区底对齐），把其中文档级的 `documentElement.scrollWidth` 断言换成元素级：每张卡与其动作行自身 `scrollWidth <= clientWidth`，卡片右缘不超出网格——工作区根是 `h-screen overflow-hidden`、页面根自带滚动，文档级断言恒真。视口点：1440×1000 三列、1280×800 三列（`xl` 起点；不带 `sidebar_state` cookie 时侧栏默认展开占 256px，内容宽仅约 1024px，是三列最紧处）、390×844 单列、320px 宽单列；不得写 `sidebar_state=false` 让列变宽来过测。同行卡动作区合理对齐但错误文本不截断；键盘六能力 Tab、Dialog Esc／焦点、下载 accessible name、新增文案的中英文和暗色至少各一条代表路径。截图落 `test.info().outputPath`（Playwright 输出目录，不入库），需要人工看的截图路径写进 PR 描述。
- [x] **Step 4：反向验证新断言会失败。**E2E 在实现之后才写，没有 RED 阶段，全绿后一次性做两处临时改动并重跑对应用例：① 给列表区媒体元素临时挂 `src`（或去掉图片的 `loading="lazy"`）→ 「首屏不得请求 Agent MP4/MP3」与「图片 lazy」断言必须 FAIL；② 临时去掉卡片封面对 `artifactMissing` 的提前返回，让 missing 任务真的渲染缩略图 → 「missing 任务 thumbnail 请求为 0」断言必须 FAIL（只保留 `thumbnail_url` 不是有效变异：正确实现本就先判 `artifactMissing`）。记录实际失败项后恢复，`git diff --stat` 确认与改动前一致，再重跑至绿。
- [x] **Step 5：跑真实后端行为验收。**使用 pytest 的隔离 DB、两个用户、真实临时 outputs 文件与 checkpoint，经 ASGI 路由验证本人摘要、跨用户 404、文件更新后的 size/mtime 变化；不读取开发／用户数据库，不调用付费 API。同时记录 50 线程一批的耗时（定稿阶段本机合成观察为 15–81 ms，仅作量级参考，见「三、已核实的现状」），写入 changelog 验收记录。
- [x] **Step 6：运行定向测试。**Run：`cd backend && PYTHONPATH=. uv run pytest tests/test_works_artifact_summaries.py tests/test_works_router.py tests/test_video_gen_delivery_metadata.py -q`；`cd frontend && pnpm exec playwright test tests/e2e/work-capabilities.spec.ts tests/e2e/works-delivery.spec.ts tests/e2e/video-assets.spec.ts`。Expected：全 PASS，无意外 skip。
- [x] **Step 7：按仓库门禁后台运行全量验证。**依次运行 `cd backend && uv run pytest tests/ -q`、`cd frontend && pnpm check && pnpm test`、`cd frontend && pnpm test:e2e`、`cd frontend && pnpm perf:check`、根 `make check`、`python3 scripts/check_docs_convention.py`。预计 >120s 的命令后台化，冻结被测输入，不并行共享 `.next`、端口或数据库。根 `make check` 已含 ruff format、knip 与 i18n key 检查，不另跑。`/workspace/video-gen` 是 `perf:check` 的受测路由，新 Dialog 会增加该路由 JS：超出 `performance-budgets.json` 预算时，在 PR 说明写归因并随 PR 评审调整预算表，不得无声抬线，也不得为此删减功能。
- [x] **Step 8：处理既有失败边界。**若命中 backlog 已登记的 video-gen `networkidle` 等基线问题，保留原始失败与 main 对照，不顺手修无关模块；本计划自身测试未绿不得交付。
- [x] **Step 9：写 changelog 并回填状态头。**changelog 必须含 TL;DR 与「验收记录」，逐行记录 `命令 → 实际关键输出`，并写入任务 7 的迁移条数与不再适用条数、Step 5 的 50 线程批次耗时；回填本计划原状态头（执行范围 + changelog 链接），不新增第二状态行。保留用户原有改动。随后提交本任务产物，`git add` 只写明确路径（`works-delivery.spec.ts`、changelog、本计划、Step 8 的必要修复）：`git commit -m "test(works): 交付中心行为验收与发布收口"`。
- [x] **Step 10：分支、PR 与评审。**使用 `my-works-delivery-center` 分支；因跨 backend/frontend 且触碰 `video_gen.py`，必须 PR + OCR，不能直推 main。`gh` 调用设 `GH_REPO` 防解析到 upstream；PR 标题 = 批次摘要，正文带 plan／changelog 链接与需要人工看的截图路径。评审与 CI 以完成通知驱动，不轮询。每轮发现先对照推送的文件核实再处置：真实问题修复后重推；误报、低优先、延后项用一条 PR 评论记录处置，不为让评审安静而反复推送（每次 push 都重跑评审，发现也不单调）。轮次上限以用户下达执行指令时给的为准，未给则 3 轮后带回拍板。无 Critical/Important 发现且 CI 绿才可 `gh pr merge --rebase`。
- [x] **Step 11：清理。**PR 合入后删除本次生成的日志、临时截图和不需要交付的测试输出；按根 AGENTS.md 的 worktree 卫生移除执行 worktree 与远程分支。移除前先盘点 worktree 内被忽略的文件（截图、测试输出、证据），需要留档的先改名归档，不静默删除。

**发布说明（不可勾选，不属自动执行范围；部署须用户另行下达指令，操作以仓库外的 ops 记录为准）：**先发 additive backend，再发 frontend（Q12）；仅 gateway 与 frontend 需要发布，无 DB 迁移，worker／remotion-worker／sandbox 不重建。回滚先 frontend 后 backend，通过 revert PR 完成，不删除任何用户 outputs 或 checkpoint。

## 七、完成定义与可审计验收矩阵

| 用户可感知行为 | 实现级证据 | 行为级证据 |
|---|---|---|
| 四类 Agent 显示真实文件而非 Motif 对话卡 | works service/model/grid 测试 | `works-delivery.spec.ts` 三文件与未交付场景 |
| 六类作品无任务类型静默丢失 | 穷尽映射与 TypeScript 检查 | course-video、ai_video 混合来源 E2E |
| 文件可查看、下载并回到来源 | URL 单测、操作矩阵 | 实际点击 viewer/download/continue/task detail |
| 多文件与缺失状态诚实 | delivered/not_delivered/partial/missing/unknown 测试 | partial/missing E2E，无版本恢复文案 |
| 任务既有后期能力不回归 | 全状态与确认弹层测试 | draft、failed、missing 行为 E2E |
| 列表不偷偷加载大媒体 | WorkMedia src 断言 | 浏览器请求计数与关闭卸载 |
| 深链、移动端、暗色、双语不回归 | URL/DOM/i18n 测试 | 1440/1280/390/320（元素级溢出断言）、键盘、刷新、前进后退 |
| 权限不跨用户 | 逐 ID owner 单测 | 双用户真实 ASGI API 验收 |
| 已取消任务不再显示「排队中」 | `task-status-label` 与 `status-badge` 单测 | 操作矩阵与 E2E 的 cancelled 场景 |
| 刷新失败不丢可用列表 | WorkGrid 刷新测试 | 刷新错误后重试的 E2E |
| 非法路径不回显、不被读取 | 服务层非法路径用例 | 不适用（损坏数据无用户可感知正例，以服务层证据为准） |

只有实现级与行为级证据同时通过、全量门禁绿、changelog 验收记录真实完整，才可将状态头改为「已执行」。

### 旧测试迁移映射（任务 7 Step 6 的执行依据）

待迁 21 个旧用例块（展开 `it.each` 后 28 条）：`agent-conversation-grid.dom.test.tsx` 8 个、`completed-video-grid.dom.test.tsx` 10 个、`completed-video-grid.test.ts` 3 个。`video-gen-page-url.dom.test.tsx` 7 条与 `work-capabilities.spec.ts` 4 条原地改造，全部保留。

| 旧用例 | 去向 |
|---|---|
| Agent：requests complete history, filters exactly, dedupes and sorts stably | `core/works/model.test.ts`（过滤、去重、稳定排序）+ `hooks.dom.test.tsx`（完整分页取数） |
| Agent：searches trimmed case-insensitive titles and paginates six at a time | `model.test.ts`（搜索）+ `work-grid.dom.test.tsx`（首屏 6、每次 +6） |
| Agent：resets search and visible count when the agent changes | `work-grid.dom.test.tsx` |
| Agent：renders the %s motif in an aligned card（4 条） | 不再适用：Motif 由真实封面／类型封面取代。卡等高与操作区 `mt-auto` 的结构断言迁入 `work-grid.dom.test.tsx` |
| Agent：counts only real human messages as displayed turns | 不再适用：轮数移除（Q14） |
| Agent：omits turns when values or messages are missing empty or have no human message | 不再适用：同上 |
| Agent：keeps loading, error with retry, empty and no-result states separate | `work-grid.dom.test.tsx` |
| Agent：opens legacy aliases through canonical agent URLs without delete controls | `work-grid.dom.test.tsx`（旧别名继续对话、Agent 无删除） |
| Completed：renders six then loads six more without another request | `work-grid.dom.test.tsx` |
| Completed：shows only honest title type status update and cover | `work-grid.dom.test.tsx` |
| Completed：missing artifact stays visible but cannot view or download | `work-grid.dom.test.tsx` |
| Completed：view expands only the selected ready work and download uses existing endpoint | `work-viewer-dialog.dom.test.tsx`（单一选中）+ `model.test.ts`（下载 URL 不变） |
| Completed：playback error replaces player with honest fallback | `work-viewer-dialog.dom.test.tsx` |
| Completed：keeps loading empty and error states distinct | `work-grid.dom.test.tsx` |
| Completed：follows controlled work type and resets preview and pagination without internal tabs | `work-grid.dom.test.tsx` |
| Completed：preserves %s status and safe actions（5 条） | `work-grid.dom.test.tsx` 操作矩阵；`cancelled` 的期望标签由「排队中」改为「已取消」（Q13） |
| Completed：keeps an incomplete draft free of playback download and storyboard actions | `work-grid.dom.test.tsx` |
| Completed：restores details storyboard retry confirmation and delete confirmation by exact id | `work-grid.dom.test.tsx` |
| selectCompletedWorks：keeps every status for both display types and excludes other pipelines | `model.test.ts`，语义更新：六能力穷尽映射，不再排除 remotion／vimax |
| selectCompletedWorks：sorts created_at descending without mutating input and caps at 100 | `model.test.ts`，语义更新：`updated_at ?? created_at` 倒序、不改输入、无 100 上限 |
| canPreviewOrDownload：gates preview and download on ready and non-missing | `model.test.ts`（可下载文件判定） |

## 八、假设与后续独立候选项

本计划默认用户接受 Q1–Q16，尤其接受 ViMax 归入 `ai_video`、Remotion（`remotion_course` 与旧 `remotion`）归入 `course-video` 能力（显示名「文字动效」），以及首版保留本地完整搜索而非改服务端存储。

以下仅在后续独立计划中处理，不在本批留下半实现分支：

- 「全部作品」跨来源服务端聚合、排序和游标分页。
- 可索引的交付 manifest、真正的同名文件版本快照与恢复。
- Agent 视频 Range、缓存首帧、图片缩略图和更高阶媒体性能（其中 artifact GET 整文件读入、不支持 Range 一项已登记 findings backlog，2026-11-24）。
- 收藏、分享、批量下载等管理能力。
