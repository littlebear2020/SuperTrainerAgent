# 故障排除

## 编译错误

### "Expected '>' but found '['" 或 "Expected '}' but found ..."
**原因**: 在 TSX 中内联含中文引号的字符串（`""` 或 `""` 被解析为 JS 字符串结束符）
**解决**: 
1. 字幕数据放在 `src/subtitles.json`，用 `import subtitlesData from './subtitles.json'` 加载
2. **永远不要在 TSX 中硬编码含中文引号的字符串**

### "<sceneComponents[i] />" 报错
**原因**: JSX 中小写变量名被当作 HTML 标签
**解决**: 赋值给大写变量
```tsx
// ❌ <sceneComponents[i] />
// ✅
const SceneComp = sceneComponents[i];
<SceneComp />
```

## 渲染错误

### "Visited ... but got no response"
**原因**: Chrome headless 渲染崩溃
**解决**: 添加 `--gl angle`
```bash
npx remotion render MyComp --output out.mp4 --codec h264 --gl angle
```

### 渲染进程被 SIGTERM 杀掉（OOM）
**原因**: 内存不足
**解决**: 
- `--concurrency 1` 减少内存占用
- 关闭其他应用释放内存

## TTS 问题

> **TTS 红线**：沙箱内只用豆包 TTS（`scripts/_doubao_tts.py`），edge-tts 必失败，任何技能不得回退。edge-tts 时代的排障条目（command not found / 超时）已随该引擎一起废弃。

### 豆包 TTS 报 RuntimeError（env 缺失 / HTTP 错误 / 会话失败）
**原因**: `VOLCENGINE_VOICE_ACCESS_KEY` 或 `DOUBAO_TTS_DEFAULT_SPEAKER` 未进沙箱环境，或火山侧临时故障
**解决**: env 缺失→补齐沙箱环境白名单后重试；HTTP 错误/会话失败→如实报错停止，**不要回退 edge-tts**（必失败且掩盖真实错误）。网络抖动由 `_doubao_tts.py` 内置 1 次指数退避重试自动吸收。

### 中文文本导致 Python SyntaxError
**原因**: 中文引号 `""` 在 Python 字符串中导致转义混乱
**解决**: 讲解稿放在外部 `.txt` 文件中，用 `=== SCENE N ===` 分隔，脚本读取文件

### 豆包 TTS 长文本耗时
**解决**: 按句拆分生成，ffmpeg 拼接（脚本已内置此逻辑）

## 字幕问题

### 字幕文本太长，显示为多行堆叠
**原因**: TTS 按句号拆分，一句可能 60-140 字，必然换行
**解决**: TTS 生成后，用 `scripts/split_subtitles.py` 将长字幕按逗号进一步拆分为短子句，时间按字符数比例分配
```bash
python3 scripts/split_subtitles.py src/subtitles.json
# 72 句 → 324 句，每条 ≤ 35 字
```
**字幕样式建议**: `fontSize: 24`, `whiteSpace: 'nowrap'`, `textOverflow: 'ellipsis'` 确保单行显示

### 字幕时间与语音不匹配
**解决**: 重新运行 generate_tts.py，它会基于实际音频时长计算帧号

## 图片对齐问题

### 多张二维码/图片大小不一致导致卡片高度不齐
**原因**: 不同图片原始尺寸不同，Img 组件直接设 width/height 会拉伸
**解决**: 
```tsx
// ✅ 用固定容器 + flex 居中 + maxWidth/maxHeight
<div style={{ width: 180, height: 180, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
  <Img src={staticFile(img)} style={{ maxWidth: 180, maxHeight: 180 }} />
</div>
// 卡片用 alignItems: 'stretch' 保证等高
```

## 性能

- 25239 帧（14分钟）约需 25-30 分钟渲染（M 系列芯片，--concurrency 1）
- 先用 `--frames 0-300` 渲染前 10 秒验证效果
- 确认无误后全量渲染
