---
name: tech-explainer-video
description: |
  将前沿科技文章转化为科普短视频。全流程覆盖：文档解析→脚本规划→台词撰写→分镜设计→AI生图→AI图生视频→TTS配音→合成输出。
  触发词："科技科普视频"、"前沿科技视频"、"科技文章转视频"、"科普短视频"、"technology explainer video"。
  适用场景：(1) AI/量子/生物/航天等前沿科技文章转科普视频 (2) 技术报告可视化讲解 (3) 科技趋势解读视频。
required-secrets:
  - name: VOLCENGINE_VOICE_ACCESS_KEY
  - name: DOUBAO_TTS_DEFAULT_SPEAKER
  - name: QIHU_API_KEY

---

# 前沿科技科普视频生成

将科技文章转化为通俗、精美的科普短视频。

## 前置要求

- **trainerai-imagine** 技能（生图，360 API / Ark seedream）
- **video-generation** 技能（图生视频，海螺/MiniMax Hailuo，360 API）
- **TTS 红线**：沙箱内只用豆包 TTS（`scripts/_doubao_tts.py`），edge-tts 必失败，任何技能不得回退。
- **TTS env**：需 `VOLCENGINE_VOICE_ACCESS_KEY` + `DOUBAO_TTS_DEFAULT_SPEAKER` 环境变量
- **FFmpeg**（音视频合成，sandbox 已预装）
- **QIHU_API_KEY** 环境变量已设置

## 关键原则

1. **必须严格执行用户确认门控**：每个阶段完成后必须暂停，向用户展示结果并等待确认后才能进入下一阶段
2. **必须达到分镜数量和字数硬性要求**：总镜数22-28个，总字数900-1300字
3. **内容生成（阶段1-4）和素材生成（阶段5-6）严格分离**：用户确认分镜后才开始生成素材

## 工作流程

### 阶段一：内容解析

接收用户输入（PDF/文档/URL/文本），分析并输出结构化内容：

```
【科技领域】：AI / 量子 / 生物 / 能源 / 航天 / 芯片 / 材料 / 通用
【内容难度】：入门级 / 进阶级 / 专业级
【核心概念】：
  1. 概念1：原理解释
  2. 概念2：原理解释
  ...
【关键数据】：提取的关键数据列表
【原文摘要】：200字以内核心内容摘要
【原文关键句】：5-8个最重要原句
```

**规则**：
- 保留完整结构和逻辑，提取所有技术术语
- 识别文章类型（技术突破/行业分析/产品介绍/原理解析）
- 明确内容边界，标注原文未涉及的内容

> **⚠️ 确认门控 #1**：向用户展示内容分析结果，等待用户确认后进入阶段二。如果用户认为分析有误，修正后重新确认。

### 阶段二：脚本规划

制定科普策略和四段式结构：

**科普策略**：
- 文章类型 → 决定叙事侧重（创新/趋势/功能/原理）
- 叙事风格 → 震撼/温情/理性/趣味
- 钩子策略 → 震撼数据/问题悬念/场景代入/历史回溯
- 知识模块 → 从8个模块中选2-3个（原理揭秘/发展历程/技术对比/应用场景/突破意义/行业影响/未来展望/挑战争议）

**四段式结构**（目标5-7分钟，总字数900-1300字）：

| 章节 | 时长 | 镜数 | 字数 | 目标 |
|------|------|------|------|------|
| 钩子引入 | 30-45s | **3-4镜** | 100-150字 | 抓住注意力 |
| 核心展开 | 120-150s | **10-12镜** | 450-600字 | 系统讲解概念 |
| 深入剖析 | 60-90s | **5-7镜** | 200-300字 | 揭示深层价值 |
| 总结升华 | 30-45s | **3-4镜** | 150-250字 | 强化记忆 |

**类比指定**：从 `references/analogy-library.md` 选取类比，贯穿全文。

**视觉风格**：从 `references/style-guide.md` 匹配领域风格。

**音色**：从 `references/voice-bgm-guide.md` 选择 TTS 音色。

> **⚠️ 确认门控 #2**：向用户展示完整脚本规划（策略、四段式大纲、类比方案、视觉风格、音色选择），等待用户确认后进入阶段三。

### 阶段三：台词撰写

按四段式结构逐章撰写台词。

**硬性规则**：
- **每句 30-40 字**（不足30字需扩写，超过40字需拆分）
- **总字数 900-1300 字**（不足900字必须扩写，超过1300字必须精简）
- **口语化**：像朋友聊天，不像上课
- **类比一致**：使用阶段二指定的类比，禁止中途更换
- **有来源标注**：每句台词标注来源（原文改写/原文引用/合理推演）

**句式技巧**：
- 短句扩写：添加"想象一下"、"要知道"、"这意味着"等引导语
- 大数据类比化："1750亿，相当于每个中国人能分到12个"
- 对比数据具象化："提升1000倍，就是1小时变3.6秒"
- 术语首次出现：术语 + 括号简释（15字以内）

**禁忌**：
- 禁止"众所周知..."、"简单来说..."
- 禁止长难句、被动句、双重否定
- 禁止大段复制原文书面表达

**输出格式**：
```
【台词文本-钩子引入】
（纯台词，无标注）
想象一下：只有3个人的小团队，短短5个月，写出了100万行代码
...

【台词标注-钩子引入】
台词1：情绪[好奇] | 来源[原文改写-第2段] | 字数[35]
...
```

**自检（必须执行）**：
- 统计总字数，确认在900-1300范围内
- 确认每句台词在30-40字
- 预估配音时长 = 总字数 ÷ 3字/秒
- 目标：900-1300字 → 300-433秒配音

> **⚠️ 确认门控 #3**：向用户展示完整台词文本和字数统计，等待用户确认后进入阶段四。

### 阶段四：分镜设计

为每句台词设计画面。**必须生成22-28个分镜**，这是硬性要求。

**每个分镜包含**：
1. **台词**（纯文本，30-40字）
2. **情绪**（好奇/惊讶/专注/理解/震撼/思考/振奋/回味）
3. **生图提示词**（英文，用于 trainerai-imagine）
4. **图生视频提示词**（英文，用于 video-generation）
5. **可视化类型**（数据动画/概念动画/流程图/信息图/对比图/时间轴）
6. **镜头类型**（推/拉/移/环绕/固定）
7. **配音时长**（字数 ÷ 3）
8. **过渡停顿**（0.5-1秒）

**提示词生成规则**（参照 `references/style-guide.md`）：

生图格式：`{风格词}; {主体描述}; {镜头描述}; {环境描述}; 8k, high quality, 16:9`

生视频格式：`{镜头运动} + {主体运动} + {效果细节}`

**视频提示词注意事项**：
- 避免使用 "extreme zoom"、"heavy push-in" 等剧烈镜头运动
- 推荐使用 "subtle"、"gentle"、"slow" 修饰镜头运动，避免画面快速缩放
- 画面主体应保持在画面中央区域，避免内容被裁切

**差异化检查**：
- 相邻分镜必须有不同主体元素
- 禁止连续3镜相同镜头运动
- 每章节至少2种可视化类型

**镜数硬性校验**：
```
总镜数 = 钩子引入(3-4) + 核心展开(10-12) + 深入剖析(5-7) + 总结升华(3-4)
必须满足：22 ≤ 总镜数 ≤ 28
不满足则必须调整，调整方向：
- 镜数不足 → 在核心展开章节增加分镜（拆分长台词或添加过渡镜）
- 镜数超标 → 合并核心展开中的短台词
```

**输出**：保存为 JSON 文件 `/mnt/user-data/workspace/{video-name}/storyboard.json`，格式参照 `templates/storyboard-schema.json`。

> **⚠️ 确认门控 #4**：向用户展示分镜表格摘要（镜号、章节、台词、镜头类型、可视化类型），确认总镜数和预估时长。等待用户确认后才能开始素材生成。

### 阶段五：素材批量生成

**在阶段四获得用户确认后，才能开始本阶段。本阶段无需逐镜确认，按顺序批量执行。**

按分镜顺序，逐镜生成素材。**执行顺序很重要：先 TTS → 再生图 → 再生视频 → 最后合成。** 这样可以根据音频时长动态调整视频时长。

**目录结构**：
```
/mnt/user-data/workspace/{video-name}/
├── storyboard.json
├── prompts/       # 提示词文件
├── images/        # 生图结果
├── videos/        # 图生视频结果
├── audio/         # TTS 配音结果
└── merged/        # 合成后的片段
```

**步骤**（对每个分镜循环执行）：

**5.1 TTS 配音（先做，确定音频时长）**
```bash
mkdir -p /mnt/user-data/workspace/{video-name}/{prompts,images,videos,audio,merged}

# 只用豆包 seed-tts-2.0（edge-tts 必失败，任何技能不得回退）
python3 /mnt/skills/public/tech-explainer-video/scripts/_doubao_tts.py  # 自检（可选）

# 生成单句配音（失败抛 RuntimeError——如实报错停止，不要回退 edge-tts）：
python3 -c "
import sys
sys.path.insert(0, '/mnt/skills/public/tech-explainer-video/scripts')
from _doubao_tts import synthesize
audio = synthesize('<narration>')
open('/mnt/user-data/workspace/{video-name}/audio/shot-{N}.mp3','wb').write(audio)
print('doubao TTS ok')
"

# 获取音频时长，用于决定视频生成时长
AUDIO_DUR=$(ffprobe -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 /mnt/user-data/workspace/{video-name}/audio/shot-{N}.mp3)
echo "Shot {N} audio duration: ${AUDIO_DUR}s"
```

**TTS 红线**：沙箱内只用豆包 TTS（`scripts/_doubao_tts.py`），edge-tts 必失败，任何技能不得回退。
- 需 env：`DOUBAO_TTS_DEFAULT_SPEAKER` + `VOLCENGINE_VOICE_ACCESS_KEY`（豆包音色由前者决定，无需手动指定）
- synthesize 失败抛 RuntimeError——如实报错停止，edge-tts 时代的"兜底/自动回退"写法已全部废弃

**5.2 生成图片**
```bash
echo "<image_prompt>" > /mnt/user-data/workspace/{video-name}/prompts/shot-{N}.txt

tsx /mnt/skills/public/trainerai-imagine/scripts/main.ts \
  --promptfiles /mnt/user-data/workspace/{video-name}/prompts/shot-{N}.txt \
  --image /mnt/user-data/workspace/{video-name}/images/shot-{N}.jpg \
  --ar 16:9
```

**5.3 图生视频（时长根据音频动态调整）**
```bash
echo "<video_prompt>" > /mnt/user-data/workspace/{video-name}/prompts/shot-{N}-video.txt

# 视频时长 = min(音频时长, 10)，API 上限约10秒
# 如果音频 > 视频时长，merge_segment.py 会自动冻结最后一帧补足时长
VIDEO_DUR=$(python3 -c "print(min(int(float('${AUDIO_DUR}')), 10))")

python /mnt/skills/public/video-generation/scripts/generate.py \
  --prompt-file /mnt/user-data/workspace/{video-name}/prompts/shot-{N}-video.txt \
  --reference-images /mnt/user-data/workspace/{video-name}/images/shot-{N}.jpg \
  --output-file /mnt/user-data/workspace/{video-name}/videos/shot-{N}.mp4 \
  --duration ${VIDEO_DUR}
```

**5.4 合成单段（自动冻结末帧补足音频时长）**
```bash
python /mnt/skills/public/tech-explainer-video/scripts/merge_segment.py \
  --video /mnt/user-data/workspace/{video-name}/videos/shot-{N}.mp4 \
  --audio /mnt/user-data/workspace/{video-name}/audio/shot-{N}.mp3 \
  --subtitle "<narration>" \
  --output /mnt/user-data/workspace/{video-name}/merged/shot-{N}.mp4 \
  --pause 0.5
```

**重要：音画时长匹配机制**
- TTS 配音时长由台词字数决定（约3字/秒），通常 10-13 秒
- AI 视频生成有上限（约10秒），可能短于配音
- `merge_segment.py` 会自动冻结视频最后一帧，补足到与配音等长
- 这样确保**每句旁白都能完整播完，画面保持静止直到播完**

**每完成一个分镜，打印进度**：`✓ Shot {N}/{total} 完成 ({chapter}) — {narration前20字}...`

**失败处理**：
- 如果某镜生成失败，跳过并记录，最后统一重试
- 最多重试3次，仍失败则用上一镜视频片段代替

### 阶段六：合成输出

拼接所有片段为最终视频：

```bash
python /mnt/skills/public/tech-explainer-video/scripts/concat_segments.py \
  --segments \
    /mnt/user-data/workspace/{video-name}/merged/shot-01.mp4 \
    /mnt/user-data/workspace/{video-name}/merged/shot-02.mp4 \
    ... \
  --output /mnt/user-data/outputs/{video-name}-final.mp4
```

完成后用 `present_files` 展示给用户，并报告：
- 总时长（分:秒）
- 总镜数
- 文件大小

## 时长速查

| 总字数 | 配音时长 | +停顿(22镜) | 总时长 |
|--------|---------|-------------|--------|
| 900字 | 300s | +15s | ~5.3分钟 |
| 1100字 | 367s | +18s | ~6.4分钟 |
| 1300字 | 433s | +22s | ~7.6分钟 |

## 确认门控总结

| 门控 | 位置 | 展示内容 | 必须等待确认 |
|------|------|---------|-------------|
| #1 | 阶段一→二 | 内容分析结果 | 是 |
| #2 | 阶段二→三 | 脚本规划（策略+大纲+类比+风格+音色） | 是 |
| #3 | 阶段三→四 | 完整台词+字数统计 | 是 |
| #4 | 阶段四→五 | 分镜表格摘要+总镜数+预估时长 | 是 |

**禁止跳过任何确认门控。每次确认必须暂停并等待用户明确回复。**

## 资源文件

| 文件 | 用途 |
|------|------|
| `references/analogy-library.md` | 7大领域类比库 |
| `references/style-guide.md` | 科技风格配置 + 提示词模板 |
| `references/voice-bgm-guide.md` | 音色匹配表 |
| `scripts/_doubao_tts.py` | 豆包 seed-tts-2.0 TTS（首选，env 驱动，stdlib only） |
| `scripts/merge_segment.py` | 单段合成（自动冻结末帧补足音频、中文字体字幕、半透明背景） |
| `scripts/concat_segments.py` | 全片拼接（预留BGM接口） |
| `templates/storyboard-schema.json` | 分镜 JSON Schema |

## 注意事项

1. **内容准确性**：所有台词必须基于原文，禁止杜撰科学事实
2. **提示词语言**：生图和生视频提示词用英文，台词用中文
3. **中文字幕**：`merge_segment.py` 自动查找中文字体（NotoSansCJK），并添加半透明背景
4. **音画同步**：视频短于配音时，自动冻结末帧补足，确保旁白完整播完
5. **镜头运动**：视频提示词中避免剧烈推拉，使用 "subtle"/"gentle" 修饰
6. **失败处理**：单镜失败不影响全局，跳过并记录，最后统一重试
7. **不要读取 Python 脚本内容**，直接按参数调用即可
8. **输出路径**：所有写入操作在 `/mnt/user-data/workspace/` 下执行
9. **禁止一次性跳到最终输出**：必须严格按阶段1→2→3→4→5→6顺序执行
