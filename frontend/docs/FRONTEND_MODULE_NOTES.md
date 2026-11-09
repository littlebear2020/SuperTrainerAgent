# Frontend Module Notes

> 起步版（2026-10-08），只写最近改动最密集的三个域；按需增补，勿求全。
> 分工：`frontend/AGENTS.md` = 读代码推不出来的约定与坑；本文件 = 模块地图与组件族索引。两者指针互连、内容不重复。
> Next.js 16 与训练数据的版本差异要点见 `frontend/AGENTS.md` 的 nextjs-agent-rules 节——写代码前先读它。

## 1. 路由与页面分区

App Router，route page 多为薄壳，实际 UI 在 `src/components/workspace/*`：

| 分区                    | 入口                                    | 说明                                                                                                                                                                                            |
| ----------------------- | --------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------- |
| `/`                     | `src/app/page.tsx`                      | landing                                                                                                                                                                                         |
| `/(auth)`               | `src/app/(auth)/login                   | setup/page.tsx`                                                                                                                                                                                 | 登录/初始化 |
| `/workspace`            | `src/app/workspace/layout.tsx`          | 壳层 `WorkspaceLayout`：Server Component 内五态分流（authenticated → AuthProvider+内容；needs_setup → /setup；unauthenticated → 登录跳转；gateway_unavailable → 整页不可用态）；`force-dynamic` |
| `/share/writer/[token]` | `src/app/share/writer/[token]/page.tsx` | 公开分享（目前仅 writer）                                                                                                                                                                       |

workspace 子路由：`chats`（+`[thread_id]`）、`agents`（+`new`、`[agent_name]/chats/[thread_id]`）、`console`、`gallery`、`assets`、`video-gen`（+`[taskId]`、`[taskId]/storyboard`）、`writer`（+`[doc_id]`）、`twins/voice`。全集以 `src/app/**/page.tsx` 为准。

注意：全仓**无** `error.tsx`/`not-found.tsx` 约定文件——错误态不走 App Router 约定，见 §2.4。

## 2. 视频生成工作台

组件族在 `src/components/workspace/video-gen/`（约 30 件），数据层在 `src/core/video-gen/`（`api.ts` fetch 层 + `hooks.ts` 查询层 + `types.ts`）。

关键组件：

- `storyboard-route-split.tsx`（`StoryboardRouteSplit`）：按任务类型分流分镜渲染器——`video-gen-storyboard.tsx`（vimax 链路）vs `remotion-course-storyboard.tsx`（课程链路）。
- `audio-preview-bar.tsx` + `frame-audio-button.tsx`（`FrameAudioButton`）：帧试听/音频预览（两链路合并件）。
- `video-gen-detail.tsx` + `video-gen-detail-warning-banner.tsx`：详情页与九路 sidecar 警告横幅。

**轮询约定**（全部 TanStack Query 函数式 `refetchInterval`，导出纯函数便于单测）：

| hook                            | 位置                             | 轮询           | 停转条件                                                                         |
| ------------------------------- | -------------------------------- | -------------- | -------------------------------------------------------------------------------- |
| `useVideoTasks`（列表）         | `core/video-gen/hooks.ts`        | 5s             | 无 running/pending/composing/qc_repair 任务即停；`draft` 显式排除（防 QPS 爆炸） |
| `useVideoTask`（单任务）        | 同上 `videoTaskRefetchInterval`  | 2s             | 终态停，除非 `options.pollWhile()`（recompose 宽限）；分镜页加载完即停           |
| `useStoryboardWithAudioPolling` | 同上 `storyboardRefetchInterval` | 2s/3s/10s 退避 | `audio_ready`/`audio_error`/taskFailed 停；错误退避 10s                          |
| 渐进预览                        | 同上 `useTaskEvents`             | **非轮询**     | SSE（EventSource + 手动退避重连，`done` 关闭）                                   |

停滞提示不是停轮询：`getStallWarning`（非终态超 5 分钟无更新只出 UI 提示）。

**错误态/引导规范**：

- 统一空态 `components/workspace/empty-state.tsx`（`EmptyState`，Chats/Writer/Gallery 共用）；
- 网关整页不可用 `components/gateway-unavailable.tsx`（两变体挂 auth/workspace layout）；
- mutation 错误统一 `onError → toast.error(后端 detail)`；409 冲突带导航 action 的出口 `toastAction409`（`core/video-gen/hooks.ts`）；
- 就地错误 + 重试按钮模式见 `audio-preview-bar.tsx`。

## 3. 状态管理模式

- **Server state = TanStack Query**，按域组织：`src/core/<domain>/hooks.ts`（agents/artifacts/channels/console/i18n/mcp/memory/models/notification/settings/skills/threads/video-gen/voice-twin/writer 等）。约定：query-key 常量集中（如 `VIDEO_KEY`）；变更 `onSuccess` 失效关联 key、`onError` toast。
- **Client state 无三方库**（无 zustand/redux，package.json 已核实）：局部 `useState`/`useRef`；跨组件用 React Context（`core/tasks/context.tsx` 子任务进度）或手写 listener-store + localStorage（`core/settings/store.ts`，对外 `useLocalSettings`/`useThreadSettings`）。
- **LangGraph client 单例**：`getAPIClient()`（`core/api/api-client.ts`，模块级 Map 缓存；含 CSRF 头注入、run 创建 maxRetries:0 防重复 run）；线程流式状态走 SDK `useStream`（`core/threads/hooks.ts`，主接口 `useThreadStream`/`useSubmitThread`/`useThreads`）。

## 待增补（按需，不强求）

auth/admin 控制台域、IM 通道设置域、`workspace-content.tsx` 壳层细节——改动密集到该写时再补。
