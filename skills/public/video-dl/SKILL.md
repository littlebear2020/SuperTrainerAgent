---
name: video-dl
description: 下载视频并生成字幕。仅支持 Bilibili。当用户提供视频 URL 并要求下载、转录或准备视频素材时触发。
required-secrets:
  - name: ASR_360_API_KEY
    optional: true
  - name: QIHU_API_KEY

---

# Video Download & Transcribe

下载在线视频（仅 Bilibili：bilibili.com / b23.tv）并生成字幕文件。这是 video-clip 的前置步骤。

> 2026-09-09 起不再支持 YouTube 等境外平台（公司网络不可达，自闭环拍板项 1）；用户给境外链接时直接说明不支持。

## 何时使用

- 用户提供视频 URL，要求下载
- 用户需要为视频生成字幕
- 用户要求剪辑视频但尚未提供本地文件

## 工作目录约定

为每个视频创建独立的工作目录：

```
/mnt/user-data/workspace/video-YYYYMMDD-{short-id}/
├── raw/
│   ├── input.mp4      ← 下载的视频
│   └── input.srt      ← 字幕文件（下载或生成）
└── metadata/
    └── download.json   ← 下载元信息
```

## Step 1: 下载视频

运行下载脚本（在 sandbox 中）：

```bash
python3 /mnt/skills/public/video-dl/scripts/download.py "VIDEO_URL" -o /mnt/user-data/workspace/video-YYYYMMDD-xxx/raw/
```

**选项：**
- `-b chrome` — 使用浏览器 cookies（适用于需要登录的视频）
- 平台仅支持 Bilibili（bilibili.com / b23.tv），其他 URL 脚本直接报错

**结果：** 脚本输出 JSON：`{"success": true, "video": "...", "subtitle": "...", "platform": "..."}`

- 如果 `subtitle` 为 `null`，视频没有内嵌字幕，需要进入 Step 2 转录
- 如果 `subtitle` 有值，跳过 Step 2

## Step 2: 语音转录（仅当没有字幕时）

```bash
python3 /mnt/skills/public/video-dl/scripts/transcribe.py /mnt/user-data/workspace/video-YYYYMMDD-xxx/raw/input.mp4 -o /mnt/user-data/workspace/video-YYYYMMDD-xxx/raw/input.srt --language zh
```

**转录方式：**
- 使用 `volcengine/asr-turbo` 模型（通过 360 API），云端识别，准确率高，支持中英文混合。
- 需要沙箱能访问 `api.360.cn`（网络连通）。
- 需要设置环境变量 `ASR_360_API_KEY`（默认为 `QIHU_API_KEY`）。

详细的 ASR 用法和故障排查，参见 `references/asr-guide.md`。

## 完成后

告知用户：
1. 视频已下载到工作目录
2. 字幕是否已获取/生成
3. 如果用户要剪辑，建议使用 `video-clip` skill
