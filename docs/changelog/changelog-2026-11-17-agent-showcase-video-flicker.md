# Agent Showcase 课程视频闪帧修复 changelog（2026-11-17）

> **TL;DR**：修复课程视频 v1 的 8 帧、v3 的 1 帧单帧 2×2 重影（t≈0.10–13.90s / 36.60s），用后一帧替换后重编码，时长与音轨不变；v2 未检出。素材总量 16,962,605 bytes，showcase 资产测试与目标 E2E 通过。

## 一、背景

用户反馈课程视频 agent 的第一个示例视频播放时“有的画面会闪一下”，截图对应《大模型为什么会产生幻觉？》32 秒视频。

## 二、诊断

逐帧亮度和单帧异常检测发现：坏帧把同一页 PPT 以 2×2 四宫格重复渲染一帧，下一帧立即恢复正常，因此观感是闪一下。命中：

- v1：n=3、8、15、125、128、139、149、417（t≈0.10/0.27/0.50/4.17/4.27/4.63/4.97/13.90s）。
- v3：n=1098（t≈36.60s）。
- v2：无。

## 三、修复

- 用后一帧替换坏帧，帧数/时间轴不变；重新编码 H.264 30fps yuvj420p，音频 copy。
- 重编码后 v1 968 帧 / 32.320000s，v3 1171 帧 / 39.082667s，与数据里的时长一致。

## 验收记录

- `ffprobe v1.mp4` → `nb_frames=968`、`duration=32.320000`、`1280x720`、`30/1`
- `ffprobe v3.mp4` → `nb_frames=1171`、`duration=39.082667`
- 单帧异常检测 → v1 `anomalies 8 → 0`；v3 `1 → 0`；v2 `0`
- 抽帧 → 0.1–0.5s、4.1–5.1s、13.9s 附近无 2×2 重影
- `cd frontend && pnpm check` → 0 errors
- `pnpm exec vitest run --project node tests/unit/core/agents/showcase.test.ts` → 7 passed
- `pnpm test` → 266 files / 1754 tests passed
- `E2E_PORT=3182 pnpm exec playwright test tests/e2e/agent-new-chat-guide.spec.ts` → `41 passed (52.9s)`
- 浏览器播放 v1（mock gateway，1440 宽）→ `duration=32.32`、`readyState=4`、`videoError=null`，4.6s 处无 2×2 重影、无 console error
- 素材总量 `16,962,605 bytes`（16.96 MB，<20 MB）

## 四、说明

- 仅替换坏帧，不改视频内容/海报/文案；v2 未动。
- 重编码 CRF 17，v1/v3 合计增加约 1.28MB；一次性修复脚本不入库。
