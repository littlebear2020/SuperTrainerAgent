---
name: video-clip
description: AI 自动视频剪辑。分析字幕内容，提取精华片段，评分筛选，生成标题，按主题聚类，最终用 ffmpeg 切割输出。当用户提供视频文件（或使用 video-dl 下载后）并要求剪辑、切片、提取精华片段时触发。
---

# Video Clip — AI 自动视频剪辑

从视频中自动提取精华片段。需要视频文件（mp4）和字幕文件（srt）。

如果用户只提供了 URL，先用 `video-dl` skill 下载。

## 输入要求

- 视频文件：`/mnt/user-data/workspace/video-XXX/raw/input.mp4`
- 字幕文件：`/mnt/user-data/workspace/video-XXX/raw/input.srt`

**没有字幕文件？** 如果用户提供的视频没有 SRT 字幕，先用 ASR 生成：

```bash
python3 /mnt/skills/public/video-dl/scripts/transcribe.py /mnt/user-data/workspace/video-XXX/raw/input.mp4 -o /mnt/user-data/workspace/video-XXX/raw/input.srt --language zh
```

详细的 ASR 用法和故障排查，参见 `/mnt/skills/public/video-dl/references/asr-guide.md`。

## 工作目录

所有输出写入视频的工作目录：

```
/mnt/user-data/workspace/video-XXX/
├── raw/           ← 输入文件（video + srt）
├── metadata/      ← 中间分析结果 JSON
│   ├── step1_outline.json
│   ├── step2_timeline.json
│   ├── step3_scored.json
│   ├── step3_high_score.json
│   ├── step4_titles.json
│   └── step5_collections.json
└── output/
    ├── clips/         ← 单独切片
    └── collections/   ← 合集视频
```

## 视频分类

根据视频内容选择分类，影响 prompt 选择。分类对应的 prompt 在 `/mnt/skills/public/video-clip/references/prompts/` 下按目录名组织。

| 分类 | 目录名 | 适用场景 |
|------|--------|---------|
| 通用 | default | 不确定时使用 |
| 知识科普 | knowledge | 教育、技术分享 |
| 演讲 | speech | 演讲、脱口秀、访谈 |
| 商业财经 | business | 商业分析、投资 |
| 观点评论 | opinion | 评论、思辨 |
| 经验分享 | experience | 生活经验、技能 |
| 内容解说 | content_review | 影视解说、游戏解说 |
| 娱乐 | entertainment | 综艺、表演 |

---

## Step 1: 大纲提取

### 1.1 分块

对长视频（>30分钟），先将字幕分块：

```bash
python3 /mnt/skills/public/video-clip/scripts/srt_tools.py chunk /mnt/user-data/workspace/video-XXX/raw/input.srt --chunk-minutes 30 -o /mnt/user-data/workspace/video-XXX/metadata/srt_chunks.json
```

对短视频（<30分钟），直接解析：

```bash
python3 /mnt/skills/public/video-clip/scripts/srt_tools.py parse /mnt/user-data/workspace/video-XXX/raw/input.srt -o /mnt/user-data/workspace/video-XXX/metadata/srt_parsed.json
```

### 1.2 LLM 分析

读取 prompt 模板：`/mnt/skills/public/video-clip/references/prompts/{category}/outline.txt`（或 `default/outline.txt`）

**对每个分块**，将分块的纯文本内容作为输入，按照 prompt 中的指令分析。

- 提取结构化大纲：每个话题包含标题和子话题
- 目标：2-5 个话题/30分钟片段，每个话题 3-12 分钟（理想 6-8 分钟）
- 输出格式要求：编号列表，**加粗话题名**，`-` 列出子话题

### 1.3 输出

将结果写入 `metadata/step1_outline.json`：

```json
[
  {"title": "话题名", "subtopics": ["子话题1", "子话题2"], "chunk_index": 0},
  ...
]
```

---

## Step 2: 时间线提取

### 2.1 准备数据

对每个分块，用 srt_tools 提取对应时间段的字幕：

```bash
python3 /mnt/skills/public/video-clip/scripts/srt_tools.py segment /mnt/user-data/workspace/video-XXX/raw/input.srt --start "HH:MM:SS,mmm" --end "HH:MM:SS,mmm"
```

### 2.2 LLM 分析

读取 prompt 模板：`/mnt/skills/public/video-clip/references/prompts/{category}/timeline.txt`

将大纲 + 对应的 SRT 文本作为输入，按 prompt 指令定位每个话题的精确起止时间。

**约束：**
- 最短 90 秒，目标 3-6 分钟，最长 8 分钟
- 相邻短片段合并
- 时间格式：`HH:MM:SS,mmm`

### 2.3 输出

写入 `metadata/step2_timeline.json`：

```json
[
  {"id": "1", "outline": "话题名", "content": ["子话题1", "子话题2"], "start_time": "00:05:30,000", "end_time": "00:11:45,000", "chunk_index": 0},
  ...
]
```

按 start_time 排序，分配连续 ID（1, 2, 3...）。

---

## Step 3: 内容评分

读取 prompt 模板：`/mnt/skills/public/video-clip/references/prompts/{category}/scoring.txt`

将 step2 的全部片段作为输入，按 prompt 指令对每个片段评分。

**评分维度：** 信息价值、情感共鸣、传播潜力、结构完整性
**评分范围：** 0.0 - 1.0

### 输出

写入两个文件：

- `metadata/step3_scored.json` — 全部片段含分数
- `metadata/step3_high_score.json` — 仅高分片段（>= 0.7）

```json
[
  {"id": "1", "outline": "...", "final_score": 0.85, "recommend_reason": "信息密度高，实用性强", ...},
  ...
]
```

---

## Step 4: 标题生成

读取 prompt 模板：`/mnt/skills/public/video-clip/references/prompts/{category}/title.txt`

将高分片段（id + 标题 + 内容 + 推荐理由）作为输入，为每个片段生成一个吸引人但诚实的标题。

### 输出

写入 `metadata/step4_titles.json`：

```json
[
  {"id": "1", "outline": "...", "generated_title": "生成的标题", "final_score": 0.85, ...},
  ...
]
```

---

## Step 5: 主题聚类

读取 prompt 模板：`/mnt/skills/public/video-clip/references/prompts/{category}/clustering.txt`

将所有带标题的片段作为输入，按主题聚类成合集（每合集 2-5 个片段）。

### 输出

写入 `metadata/step5_collections.json`：

```json
[
  {"id": 1, "collection_title": "合集标题", "collection_summary": "合集摘要", "clip_ids": ["1", "3", "5"]},
  ...
]
```

---

## Step 6: 视频切割

### 6.1 切割单独片段

```bash
python3 /mnt/skills/public/video-clip/scripts/video_cutter.py batch-cut \
  -v /mnt/user-data/workspace/video-XXX/raw/input.mp4 \
  -j /mnt/user-data/workspace/video-XXX/metadata/step4_titles.json \
  -o /mnt/user-data/workspace/video-XXX/output/clips/
```

### 6.2 生成合集（如果需要）

先根据 step5 的 clip_ids 查找对应的切片文件路径，构建合集 JSON：

```json
[
  {"collection_title": "合集标题", "clip_paths": ["/path/to/clip1.mp4", "/path/to/clip3.mp4"]}
]
```

写入临时文件后执行：

```bash
python3 /mnt/skills/public/video-clip/scripts/video_cutter.py batch-concat \
  -j /mnt/user-data/workspace/video-XXX/metadata/collections_paths.json \
  -o /mnt/user-data/workspace/video-XXX/output/collections/
```

---

## 结果汇报

完成后告知用户：

1. 提取了多少个精华片段
2. 生成了多少个合集
3. 输出文件路径
4. 每个片段的标题和评分摘要

## 恢复执行

如果某个步骤失败，可以手动跳到指定步骤：
- 中间 JSON 文件保存在 `metadata/` 下
- 重新执行时从失败的步骤开始，前面步骤的 JSON 已存在可直接使用
- Step 6（切割）可以单独执行，只要 `step4_titles.json` 和 `step5_collections.json` 存在
