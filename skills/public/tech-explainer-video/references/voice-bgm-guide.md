# 音色配置指南

> **TTS 红线**：沙箱内只用豆包 TTS（`scripts/_doubao_tts.py`），edge-tts 必失败，任何技能不得回退。本指南 edge-tts 时代的音色表已废弃删除（沙箱出网隔离拦微软 TTS 服务器，那些 Voice ID 在沙箱内永远不可用）。

## 豆包音色选择

默认音色由环境变量 `DOUBAO_TTS_DEFAULT_SPEAKER` 决定（部署时配置，无需逐句指定）。

需要为特定内容更换音色时，显式传 `speaker` 参数：

```python
import sys
sys.path.insert(0, '/mnt/skills/public/tech-explainer-video/scripts')
from _doubao_tts import synthesize
audio = synthesize('旁白文本', speaker='<doubao-speaker-id>')
```

可选 speaker id 以后端预置音色目录为准（语音孪生模块 `/api/voice-twins/preset-voices`，doubao 前缀条目）；不要凭记忆编造 speaker id——查不到可用值就用 env 默认。

### 按科技领域匹配（风格建议）

| 领域 | 风格 | 建议 |
|------|------|------|
| 人工智能/大数据 | 理性、专业、有科技感 | 选"专业播音"类男声 speaker |
| 量子技术/前沿物理 | 深沉、引人思考 | 选"新闻播报"类男声 speaker |
| 生物科技/医学 | 亲和、可信 | 选"温暖友好"类女声 speaker |
| 新能源/环保 | 积极、阳光 | 选"年轻活力"类男声 speaker |
| 通用科技 | 清晰、易懂 | env 默认即可 |

## 语速建议

科普视频推荐语速：**3字/秒**（略慢于正常语速，便于理解）。

豆包默认语速（`speed_ratio=1.0`）接近此值，通常不需要调整。如需微调，传 `speed_ratio`（如 `0.9` 略慢、`1.1` 略快）：

```python
audio = synthesize('旁白文本', speed_ratio=0.9)
```

## BGM（预留接口）

当前版本不添加 BGM，后续扩展时可通过以下方式：
- 用户上传 BGM 文件到 `/mnt/user-data/uploads/`
- `concat_segments.py` 支持 `--bgm` 参数叠加背景音乐
