# 项目 ASR 规范

本项目的语音识别（ASR）统一使用 **volcengine/asr-turbo** 模型，通过 360 API 调用。

**不要使用 whisper、dashscope 或其他 ASR 工具。** 当遇到语音转文字需求时，始终使用本文档描述的方案。

## 用法

```bash
python3 /mnt/skills/public/video-dl/scripts/transcribe.py <video_path> -o <output.srt> --language zh
```

**参数说明：**

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `<video_path>` | 视频文件路径（位置参数，必填） | — |
| `-o, --output` | 输出 SRT 文件路径 | 与视频同目录同名，后缀改为 `.srt` |
| `-l, --language` | 语言代码 | `zh` |
| `-m, --model` | ASR 模型 | `volcengine/asr-turbo` |

**示例：**

```bash
# 中文视频
python3 /mnt/skills/public/video-dl/scripts/transcribe.py /mnt/user-data/workspace/video-20260524-abc1/raw/input.mp4 -o /mnt/user-data/workspace/video-20260524-abc1/raw/input.srt --language zh

# 英文视频
python3 /mnt/skills/public/video-dl/scripts/transcribe.py /mnt/user-data/workspace/video-20260524-abc1/raw/input.mp4 --language en
```

## 工作原理

1. 用 ffmpeg 提取音频（PCM WAV, 16kHz, 单声道）
2. 音频 base64 编码后随请求直连上传——**无外部文件托管，用户音频不出域**（2026-09-09 起移除 uguu/tmpfiles/litterbox 境外托管回退，自闭环拍板项 6）
3. 调用 360 API（`https://api.360.cn/v1/audios/generations`），模型 `volcengine/asr-turbo`
4. 解析返回的 utterances，生成标准 SRT 字幕文件

脚本使用 Python `requests` 库完成所有 HTTP 请求（不依赖 curl），以避免沙箱 nginx 代理拦截。

## 环境变量

| 变量 | 说明 | 必填 |
|------|------|------|
| `ASR_360_API_KEY` | 360 API 密钥 | 是（可由 `QIHU_API_KEY` 回退） |
| `ASR_360_BASE_URL` | API 地址 | 否（默认 `https://api.360.cn/v1/audios/generations`） |

## 支持的语言

- `zh` — 中文（默认）
- `en` — 英文
- 中英混合自动识别

## 网络要求

沙箱必须能访问：
- `api.360.cn` — ASR API（唯一外部依赖，AI 模型调用）

## 故障排查

| 问题 | 原因 | 解决 |
|------|------|------|
| `No ASR API key configured` | 未设置 `ASR_360_API_KEY` 或 `QIHU_API_KEY` | 在 config.yaml 的 sandbox env 中配置 |
| `ASR base64 直连失败` | 沙箱到 api.360.cn 网络不通 / 音频过大 | 检查沙箱网络出口与 api.360.cn 可达性；确认 `requests` 库已安装；超长视频先切片再转录 |
| `ASR API error` | API 密钥无效或配额耗尽 | 检查密钥有效性 |
| `ASR returned no utterances` | 音频为空或无法识别 | 检查视频是否有音轨 |
