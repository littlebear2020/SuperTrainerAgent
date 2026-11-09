---
name: corporate-video
description: |
  将企业全员通知、制度宣贯、安全规范等文字稿转化为专业宣传视频。使用 Remotion + 豆包 TTS 实现 AI 协作视频制作全流程。触发词："制作通知视频"、"生成宣传视频"、"全员通知视频"、"制度宣贯视频"、"安全规范视频"、"给视频加字幕"。适用于：安全通知、制度发布、合规培训、内部宣传等场景。
required-secrets:
  - name: VOLCENGINE_VOICE_ACCESS_KEY
  - name: DOUBAO_TTS_DEFAULT_SPEAKER

---

# 企业通知宣传视频制作

文字稿 → 场景拆分 → TTS配音（含字幕时间戳） → Remotion代码 → 渲染输出

## 前置要求

- **Node.js** v22+, **Python 3.10+**, **FFmpeg**
- **TTS 红线**：沙箱内只用豆包 TTS（`scripts/_doubao_tts.py`），edge-tts 必失败，任何技能不得回退。
- **TTS env**：需 `VOLCENGINE_VOICE_ACCESS_KEY` + `DOUBAO_TTS_DEFAULT_SPEAKER` 环境变量
- **Remotion 项目**: 使用预装模板（见 auto-video-flow 技能的"方案一"）
- 渲染前: sandbox 已预装 Chrome headless shell，无需 `npx remotion browser ensure`

## 工作流程

### 第一步：场景拆分

将文字稿拆分为场景（每场景 20-50 秒），标注：编号、名称、讲解稿、画面设计。
- 不删减原文要点，只改表达方式
- 每场景聚焦一个主题
- 讲解稿口语化，适合播报风格

### 第二步：TTS 配音 + 字幕时间戳

1. 将讲解稿写入文本文件（每场景用 `=== SCENE N ===` 分隔），存为 `scripts/<video-name>_texts.txt`
   - **不要在 Python 脚本中硬编码中文文本**，避免引号转义地狱
2. 复制 `scripts/generate_tts.py` 到项目 `scripts/` 目录
3. 编辑配置项：`VIDEO_NAME`、`FPS`、`NAMES`、`BUFFER_FRAMES`
4. 运行: `python3 -u scripts/generate_tts.py`
5. 输出：
   - 音频: `public/audio/<video-name>/XX-name.mp3`
   - 时间数据 JSON: `scripts/tts_<video-name>.json`
   - 字幕 JSON: `src/subtitles.json`（含每句 startFrame/endFrame）
   - 终端打印: SCENES 配置 + 总帧数（直接复制到 TSX）
6. **拆分长字幕**: TTS 按句号拆分，一句话可能 60+ 字导致多行。运行 `python3 scripts/split_subtitles.py src/subtitles.json` 按逗号拆为短子句（默认 ≤35 字/条）

### 第三步：Remotion 视频代码

1. 复制 `references/VideoTemplate.tsx` 到 `src/` 作为起点
2. 将 TTS 生成的字幕 JSON 复制到 `src/subtitles.json`（**必须用 JSON 文件，不要内联到 TSX**）
3. 在 TSX 中 `import subtitlesData from './subtitles.json'` 加载字幕
4. 根据场景设计修改组件，参考 [references/design-guide.md](references/design-guide.md)
5. 在 `src/Root.tsx` 注册 Composition

### 第四步：渲染

```bash
npx remotion render <CompositionId> --output /mnt/user-data/workspace/video-XXX/output/video.mp4 --codec h264 --concurrency 1
```

- `--gl angle`: macOS 必加，否则渲染崩溃
- `--concurrency 1`: 减少内存占用，防止 OOM
- 预览效果: `--frames 0-300` 只渲染前 10 秒

### 第五步：检查与迭代

- 字幕时间是否与语音同步
- 画面布局（元素居中、充分利用空间）
- 字体大小（正文 ≥ 22px，标题 ≥ 48px）
- 场景过渡节奏

## 语音选择

音色**显式确定**（2026-10-12 起）：`scripts/generate_tts.py` 已带 `SPEAKER` 常量（默认男声 `zh_male_m191_uranus_bigtts`·云舟2.0）与 `speaker` 形参——每次任务按需求确认音色后改常量或传参，**禁止依赖 `DOUBAO_TTS_DEFAULT_SPEAKER` env 默认兜底**（env 默认是女声，裸调 = 需求音色物理上不可达，2026-10-12 事故形态）。

在册授权豆包音色（单一事实源 = 后端 `voice_catalog.py` 的 `CURATED_VOICES`；查不到的 id 勿用，在册外 id 会报 "resource ID is mismatched"）：

- 男声：`zh_male_m191_uranus_bigtts`（云舟2.0·稳重大气）、`zh_male_liufei_uranus_bigtts`（刘飞2.0·富有表现力）
- 女声：`zh_female_vv_uranus_bigtts`（env 默认）

生成后自证：脚本逐场景打印 `(speaker=...)` 行——交付时贴该行作音色证据，不接受口头声明；需临时换音色时在 `_doubao_tts.synthesize(text, speaker=...)` 显式传 speaker id，可选值同上在册清单（或 `/api/voice-twins/preset-voices` 的 doubao 前缀条目）。

## 资源文件

| 文件 | 用途 |
|------|------|
| `scripts/generate_tts.py` | TTS + 字幕时间戳生成脚本 |
| `scripts/split_subtitles.py` | 长字幕拆分脚本（按逗号拆短句） |
| `references/VideoTemplate.tsx` | Remotion 组件模板（含字幕叠加） |
| `references/design-guide.md` | 画面设计规范 |

## 关键经验（必读）

完整踩坑记录见 [references/gotchas.md](references/gotchas.md)，以下是核心要点：

1. **中文文本放外部文件** — Python 脚本中硬编码中文引号会触发转义地狱，用 `=== SCENE N ===` 分隔的 txt 文件
2. **字幕数据用 JSON import** — TSX 中内联含中文引号的字符串会导致 esbuild 编译失败
3. **长字幕必须拆分** — TTS 按句号拆分的一句话可能 60-140 字，用 `split_subtitles.py` 按逗号拆为 ≤35 字的短子句
4. **字幕单行显示** — `whiteSpace: 'nowrap'` + `textOverflow: 'ellipsis'`，字号 24px
5. **二维码/图片对齐** — 用固定容器 + flex 居中 + maxWidth/maxHeight，卡片用 `alignItems: 'stretch'` 等高
6. **TTS 只用豆包** — `scripts/_doubao_tts.py` 的 `synthesize()`；失败抛 RuntimeError 如实报错，**不要回退 edge-tts**（沙箱内必失败）
7. **组件引用必须大写** — `<sceneComponents[i] />` 会报错，需赋值给大写变量
8. **macOS 渲染必加 `--gl angle`** — 否则 Chrome 渲染进程崩溃
9. **长视频用 `--concurrency 1`** — 防止内存不足被 OOM killer 终止
