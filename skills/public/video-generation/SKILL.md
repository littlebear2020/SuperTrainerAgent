---
name: video-generation
description: 当用户需要生成、创建或想象视频时使用。首选火山方舟豆包 Seedance 2.0（ARK_API_KEY）生成视频，360 Hailuo 为失败回退渠道；支持文生视频和图生视频。
required-secrets:
  - name: ARK_API_KEY
  - name: QIHU_API_KEY
    optional: true

---

# Video Generation Skill

## Overview

This skill generates high-quality videos using structured prompts and Python scripts. Preferred channel: **Doubao Seedance 2.0** (`doubao-seedance-2-0-260128`) on Volcengine Ark (`ARK_API_KEY`); on failure it falls back to **MiniMax Hailuo 2.3** via the 360 API (`api.360.cn`, `QIHU_API_KEY`). It supports text-to-video and image-to-video with async task polling.

## Core Capabilities

- Create structured JSON prompts for AIGC video generation
- Support reference image as guidance or the first/last frame of the video
- Generate videos through automated Python script execution
- Text-to-video (T2V) and Image-to-video (I2V) modes
- Async task polling with automatic retry

## Workflow

### Step 1: Understand Requirements

When a user requests video generation, identify:

- Subject/content: What should be in the video
- Style preferences: Art style, mood, color palette
- Technical specs: Duration, aspect ratio, composition, lighting
- Reference image: Any image to guide generation
- You don't need to check the folder under `/mnt/user-data`

### Step 2: Create Structured Prompt

Generate a structured JSON file in `/mnt/user-data/workspace/` with naming pattern: `{descriptive-name}.json`

### Step 3: Create Reference Image (Optional when trainerai-imagine skill is available)

Generate reference image for the video generation.

- If only 1 image is provided, use it as the guided frame of the video

### Step 4: Execute Generation

```bash
python /mnt/skills/public/video-generation/scripts/generate.py \
  --prompt-file /mnt/user-data/workspace/prompt-file.json \
  --output-file /mnt/user-data/outputs/generated-video.mp4 \
  --duration 8
```

With reference image (image-to-video mode):
```bash
python /mnt/skills/public/video-generation/scripts/generate.py \
  --prompt-file /mnt/user-data/workspace/prompt-file.json \
  --reference-images /path/to/ref1.jpg \
  --output-file /mnt/user-data/outputs/generated-video.mp4 \
  --duration 8
```

Parameters:
- `--prompt-file`: Absolute path to text prompt file (required)
- `--reference-images`: Absolute paths to reference images (optional, enables image-to-video mode)
- `--output-file`: Absolute path to output video file (required)
- `--duration`: Video duration in seconds (optional, default: 8)

[!NOTE]
Do NOT read the python file, instead just call it with the parameters.

## Video Generation Example

User request: "Generate a short video clip depicting the opening scene from "The Chronicles of Narnia: The Lion, the Witch and the Wardrobe"

Step 1: Search for the opening scene of "The Chronicles of Narnia: The Lion, the Witch and the Wardrobe" online

Step 2: Create a JSON prompt file with the following content:

```json
{
  "title": "The Chronicles of Narnia - Train Station Farewell",
  "background": {
    "description": "World War II evacuation scene at a crowded London train station. Steam and smoke fill the air as children are being sent to the countryside to escape the Blitz.",
    "era": "1940s wartime Britain",
    "location": "London railway station platform"
  },
  "characters": ["Mrs. Pevensie", "Lucy Pevensie"],
  "camera": {
    "type": "Close-up two-shot",
    "movement": "Static with subtle handheld movement",
    "angle": "Profile view, intimate framing",
    "focus": "Both faces in focus, background soft bokeh"
  },
  "dialogue": [
    {
      "character": "Mrs. Pevensie",
      "text": "You must be brave for me, darling. I'll come for you... I promise."
    },
    {
      "character": "Lucy Pevensie",
      "text": "I will be, mother. I promise."
    }
  ],
  "audio": [
    {
      "type": "Train whistle blows (signaling departure)",
      "volume": 1
    },
    {
      "type": "Strings swell emotionally, then fade",
      "volume": 0.5
    },
    {
      "type": "Ambient sound of the train station",
      "volume": 0.5
    }
  ]
}
```

Step 3: Use the trainerai-imagine skill to generate the reference image

Load the trainerai-imagine skill and generate a single reference image `narnia-farewell-scene-01.jpg` according to the skill.

Step 4: Use the generate.py script to generate the video
```bash
python /mnt/skills/public/video-generation/scripts/generate.py \
  --prompt-file /mnt/user-data/workspace/narnia-farewell-scene.json \
  --reference-images /mnt/user-data/outputs/narnia-farewell-scene-01.jpg \
  --output-file /mnt/user-data/outputs/narnia-farewell-scene-01.mp4 \
  --duration 8
```
> Do NOT read the python file, just call it with the parameters.

## Output Handling

After generation:

- Videos are typically saved in `/mnt/user-data/outputs/`
- Use `present_files` tool to share generated files with the user. The `filepaths` parameter **must be a list** (array) of absolute paths:
  ```
  present_files(filepaths=["/mnt/user-data/outputs/generated-video.mp4"])
  ```
  When presenting both a reference image and the video together:
  ```
  present_files(filepaths=["/mnt/user-data/outputs/ref-image.jpg", "/mnt/user-data/outputs/generated-video.mp4"])
  ```
- Provide brief description of the generation result
- Offer to iterate if adjustments needed

## Notes

- Always use English for prompts regardless of user's language
- JSON format ensures structured, parsable prompts
- Reference image enhance generation quality significantly
- Iterative refinement is normal for optimal results
- Video generation takes ~5 minutes on average; the script polls automatically
