# Live E2E 运行手册

> 适用对象：`frontend/tests/e2e/live/` 下的真实部署验收套件（2026-11-19 入库）。
> 与 `frontend/tests/e2e/*.spec.ts`（mock 套件，`pnpm test:e2e`，跑在 CI）是两套互不干扰的东西。

## 为什么有这一套

仓库现有的 22 个 e2e 套件、106 个测试**全部用 `page.route()` 拦截网络**，断言的是「界面在给定数据下渲染正确」。它们测不到：

- 360 网关的 seedance / seedream 模型是否还活着
- 火山方舟 TTS 凭据是否仍有效
- Aio 沙箱能否拉起 Remotion 并渲染出可解码的 mp4
- `present_files` 交付链路是否真把文件递到了前端
- CSRF Double Submit 在真实会话下是否通得过

live 套件不拦任何网络（`grep -c 'page.route('` 全为 0），并对产物做实证校验：`ffprobe` 解码 mp4、校验 pptx 的 zip 魔数与最小体积。

## 三档运行策略

**不要每次全跑。** 按改动性质选择档位。

### L1 冒烟 —— 每次部署后（零成本，秒级）

```bash
LIVE_BASE_URL=https://<被测地址> \
LIVE_EMAIL=<账号> \
LIVE_TEST_PASSWORD=<密码> \
pnpm test:e2e:live:l1
```

零 AI API 消耗。覆盖：`/healthz` 与落地页、匿名访问重定向、UI 登录、首页双分区六卡齐且无横向溢出、四个 agent 卡跳转、两个直接任务卡打开正确类型创建框。

**发现什么**：部署挂了、登录坏了、卡片少了、路由错了。

### L2 轻功能 —— 改动涉及 agent 链路时（一次几分钱，分钟级）

```bash
pnpm test:e2e:live:l2
```

仅 1 条真实 LLM 调用（"请只回复两个字：收到"），其余是 CRUD 与页面可达性。覆盖：course-assistant 真实 LLM 回复、线程重开消息持久、作品资产页、writer 文档 CRUD 与刷新后持久、声音分身页、控制台用量面板、案例库、输入框占位文案。

**发现什么**：真实 API 连通性、凭据有效性、持久化写入。

### L3 重链路 —— 版本发布前手工跑（1.5–2 小时，几十元量级）

```bash
pnpm test:e2e:live:l3
```

六条全付费、`workers: 1` 串行。**不要放进 CI，不要每天跑。**

| 测试                | 真实链路                                           | 实测耗时                          |
| ------------------- | -------------------------------------------------- | --------------------------------- |
| `l3-course-video`   | 360 LLM 撰稿 → auto-video-flow 沙箱 Remotion → mp4 | **约 39 分钟**（900 帧，4C/7.5G） |
| `l3-ai-video`       | 360 seedance 视频 → ffmpeg 池合成                  | 数分钟                            |
| `l3-image-motion`   | 360 seedream 生图 → ffmpeg 动效                    | 数分钟                            |
| `l3-knowledge-card` | 360 LLM 规划 → Aio 沙箱 → 图片                     | 数分钟                            |
| `l3-podcast`        | 360 LLM → 火山方舟豆包 TTS → 沙箱 FFmpeg → MP3     | 数分钟                            |
| `l3-ppt`            | 360 LLM 大纲 → 沙箱 pptx                           | 数分钟                            |

`l3-course-video` 的 `test.setTimeout(60 * 60_000)` 是实测结论：36 分钟预算会以 3 分钟之差误报失败，故放大到 55 分钟等待。

**L3 会占用约 3 个 Aio 容器**（每个约 3 GiB 内存 + 2 GiB shm）。跑之前确认沙箱池有余量，否则排队会让超时预算失效。

## 环境变量

三个都必填，**不提供默认值**——缺失即抛错退出，避免"忘记设置"变成"打到了错误的服务器"。

| 变量                 | 说明                                                                        |
| -------------------- | --------------------------------------------------------------------------- |
| `LIVE_BASE_URL`      | 被测部署根地址，含协议与端口。**禁止写进仓库**（AGENTS.md「服务器与部署」） |
| `LIVE_EMAIL`         | 真实账号邮箱                                                                |
| `LIVE_TEST_PASSWORD` | 真实账号密码                                                                |

`global-setup.ts` 用这三项走 `POST /api/v1/auth/login/local` 换登录态，落盘到 `tests/e2e/live/.auth/state.json`。

**该文件含真实 `access_token` 与 `csrf_token`，已在 `.gitignore` 排除（`/frontend/tests/e2e/live/.auth/`）。切勿 `git add -f`。**

## 与 mock 套件的隔离

三道防线，改动时不要破坏：

1. `playwright.config.ts` 有 `testIgnore: /\/live\//` —— 常规 `pnpm test:e2e` 不会误跑 live
2. `playwright.live.config.ts` 独立 `testDir`、不起 `webServer`（服务已在线）
3. `.gitignore` 排除 `.auth/` 与 `test-results/live*/`

## 产物

| 路径                           | 内容                                   |
| ------------------------------ | -------------------------------------- |
| `test-results/live/`           | Playwright 原始产物（trace、失败截图） |
| `test-results/live-html/`      | HTML 报告                              |
| `test-results/live-artifacts/` | 下载下来的 mp4 / pptx / mp3 实体       |

失败时先看 HTML 报告里的 trace，再看 `live-artifacts/` 里产物是否真的落地了。

## 常见失败与判读

| 现象                                   | 含义                                                  |
| -------------------------------------- | ----------------------------------------------------- |
| `缺少 LIVE_BASE_URL`                   | 环境变量没传，不是代码问题                            |
| `API 登录失败 401/403`                 | 账号密码错，或部署侧认证配置变了                      |
| `waitForPresentFiles` 超时但线程有回复 | agent 生成了但没走 `present_files` 交付——这是真 bug   |
| `ffprobe` 报非零                       | 产物落了但编码坏了，通常是 ffmpeg 池或沙箱异常        |
| `threadIdFromUrl` 抛错                 | URL 停在 `/chats/new`，线程未创建成功（不是测试写错） |

依赖 `ffprobe`（外部可执行），缺失时会在用例深处才失败，先 `ffprobe -version` 自检。
