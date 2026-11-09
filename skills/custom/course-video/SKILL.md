---
name: course-video
description: 视频课程制作全流程。从选题调研、脚本撰写、AI配音到画面生成和渲染。当用户要求制作视频课程、教学视频、培训视频时触发。
---

# 视频课程制作

## 工作流程

### Step 1: 选题调研
读取 skill: deep-research，对课程主题进行深度调研，收集权威资料。

### Step 2: 课程大纲
基于调研结果，设计课程大纲：
- 确定章节数量和每章时长
- 目标：每章 3-8 分钟，总时长 15-60 分钟
- 输出到 `/mnt/user-data/workspace/video-XXX/metadata/outline.json`

### Step 3: 脚本撰写
为每个章节撰写详细脚本：
- 包含解说词（配音文本）和画面描述
- 解说词风格：专业但口语化，适合朗读
- 输出到 `/mnt/user-data/workspace/video-XXX/metadata/scripts/`

### Step 4: 配音生成

> ⚠️ **TTS 红线**：沙箱内只用豆包 TTS（`scripts/_doubao_tts.py`），edge-tts 必失败，任何技能不得回退。（edge-tts 时代的回退/兜底写法已全部废弃——沙箱出网隔离拦微软 TTS 服务器，回退必然失败且掩盖真实错误。）

需 env：`VOLCENGINE_VOICE_ACCESS_KEY` + `DOUBAO_TTS_DEFAULT_SPEAKER`（豆包音色由后者决定，无需手动指定）。

```bash
python3 -c "
import sys
sys.path.insert(0, '/mnt/skills/custom/course-video/scripts')
from _doubao_tts import synthesize
audio = synthesize('脚本内容')  # 失败抛 RuntimeError——如实报错停止，不要回退 edge-tts
open('/mnt/user-data/workspace/video-XXX/output/audio/chapter_01.mp3','wb').write(audio)
print('doubao TTS ok')
"
```

### Step 5: 画面生成与组装

> 职责切分：course-video 管课程结构/讲稿/章节编排；Remotion 动态画面的代码生成与渲染**委托 auto-video-flow skill**（模板、TTS 脚本、字幕与渲染规则见 `/mnt/skills/public/auto-video-flow/`）。

根据脚本中的画面描述：
- 使用 trainerai-imagine skill 生成配图
- 使用 video-generation skill 生成视频片段（如需要）
- 需要 Remotion 编程生成动态画面时，按 auto-video-flow skill 的全流程执行

### Step 6: 合成渲染
使用 ffmpeg 将配音和画面合成为最终视频：

> **前置检查**: 执行合成前，先用 `ffmpeg -version` 确认 ffmpeg 可用，并用 `ffprobe` 检查视频素材时长是否匹配音频时长。若缺少视频素材，可跳过合成步骤并告知用户需要补充素材。

```bash
ffmpeg -i video.mp4 -i audio.mp3 -c:v copy -c:a aac -shortest -y output.mp4
```

## 完成后
告知用户：章节数量、总时长、输出路径，并提供预览。
