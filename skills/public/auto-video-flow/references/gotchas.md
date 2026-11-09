# 踩坑经验与故障排除

## 场景缓冲时间（BUFFER）

**推荐值：30帧（1秒）**，不要用 90帧（3秒）。

- 3秒缓冲会导致大量空白停顿，视频拖沓
- 1秒缓冲足够覆盖音频尾部自然衰减
- 场景时长公式：`round(audio_duration_seconds * 30 + 30)`

## 中文显示乱码/豆腐块（Tofu）

**问题**：视频中的中文字符显示为方块 □□□。

**根因**：Remotion 默认使用 Arial 字体，不支持中文字符集。

**解决**：

1. 创建 `src/index.css`，设置中文字体回退栈：
   ```css
   body {
     font-family: 'Noto Sans SC', 'WenQuanYi Micro Hei', 'PingFang SC', 'Microsoft YaHei', sans-serif;
   }
   ```
2. 在 `src/index.ts` 中导入：`import './index.css'`
3. 所有组件中的 `fontFamily` 统一使用常量：
   ```tsx
   const FONT = "'Noto Sans SC', 'WenQuanYi Micro Hei', 'PingFang SC', 'Microsoft YaHei', sans-serif";
   ```

**注意**：服务器部署时需确保字体已安装或自托管（如 Plus Jakarta Sans 方案），Docker 镜像中不会自动包含中文字体。

## Unicode 转义序列在 JSX 中不解析

**问题**：JSX 文本节点中写 `\u591A\u8D26\u6237` 显示为字面字符串而非中文。

**根因**：JavaScript 只在 `{}` 表达式中解析 `\uXXXX`，JSX 文本节点中不解析。

```tsx
// ❌ 显示字面字符串 "\u4E2D\u6587"
<div>\u4E2D\u6587</div>

// ✅ 表达式中解析
<div>{"\u4E2D\u6587"}</div>

// ✅ 直接用中文（推荐）
<div>中文</div>
```

**规则**：所有中文直接写入，不要用 Unicode 转义。如果必须用转义，包在 `{}` 表达式中。

## 字幕同步问题（最常见）

**问题**：字幕与语音不同步，有时早有时晚。

**根因**：均分时间戳是错误的，语音速度不均匀。

**正确做法**：使用 TTS 脚本自动生成基于实际音频时长的时间戳。TTS 脚本逐句生成音频，用 ffprobe 取每句实际时长，累加得到精确时间戳。

```typescript
// ❌ 错误：均分
const framePerSegment = sceneDuration / subtitleCount;

// ✅ 正确：TTS 脚本自动输出精确时间戳
const SUBS = subsData.subtitles; // 从 JSON 加载
```

## 字幕样式

根据背景色选择：
- **深色背景**（推荐）：白色文字 `#ffffff` + 深色阴影 `0 0 8px rgba(0,0,0,0.9)` + `bottom: 50px`
- **浅色背景**：深色文字 `#1e293b` + 白色阴影 + 透明背景

避免：无阴影（不可读）、黑色背景白色文字（太重）。

## 画面布局（⭐ 重要）

### 核心原则：内容占屏 70-80%

默认的卡片和字体偏小，**初始设计就要按满屏布局**，不要事后放大。

### 1920×1080 画面尺寸参数表

**布局容器（Wrapper）：**
- padding: `"20px 60px 100px"`（可用内容区约 1800×860px）
- 内容区 maxWidth: 1780px

**字号体系：**
| 元素 | fontSize |
|------|----------|
| 主标题 h1 | 68-72px |
| 场景标题 h2 | 56px |
| 副标题 | 24-26px |
| 卡片标题 | 26-30px |
| 正文/描述 | 18-22px |
| 代码块 | 18px, lineHeight 1.8 |
| 字幕 | 36px |
| Badge/标签 | 22px |
| 小字/辅助 | 18px |

**卡片尺寸：**
| 布局 | 宽度 | gap |
|------|------|-----|
| 3列 | 520px | 36px |
| 2列 | 580px | 60px |
| 5步流程 | 300px | 12px |
| 6步流程 | 260px | 6px |
| 流程中间节点 | 340-440px | 按需 |
| 卡片内 padding | `32px 40px` | borderRadius: 16 |

**图片/文档截图：**
- maxWidth: 1400px
- maxH: 260-380px（场景内容多时缩小防溢出）

**其他组件：**
- 箭头 fontSize: 44px
- 标签 letterSpacing: 4, marginBottom: 16

### 改版前验算（防溢出）

**横向**：每行元素宽度×数量 + gap×(N-1) < 1800px
**纵向**：标题(~110px) + 卡片行(~420px) + 图片(~350px) + 间距 < 860px

逐场景验算，发现溢出提前调整图片 maxH 或拆分内容。

### 其他布局原则

- **内容垂直居中**（`justifyContent: "center"`），不要顶部对齐
- 底部 100px 预留给字幕
- 内容紧凑，水平布局优于垂直布局
- 避免四角分散的绝对定位

## 中文引号导致 esbuild 编译失败

**问题**：JSX 属性值中的中文引号 `""` 被 esbuild 解析为 JSX 字符串结束符。

```tsx
// ❌ 编译失败
<SectionTitle text="路由引擎：消息的"配电箱"" />

// ✅ 修复：用 HTML 实体
<SectionTitle text="路由引擎：消息的&ldquo;配电箱&rdquo;" />

// ✅ 或用 Unicode 转义
<SectionTitle text={"路由引擎：消息的\u201C配电箱\u201D"} />
```

**规则**：所有 JSX 属性值中的中文引号 `""''` 必须替换。

## 大型视频不要用 Claude Code 生成代码

**场景**：超过 15 个场景或 10 分钟的视频。

**原因**：Claude Code 会反复试错（中文引号、esbuild 限制等），浪费大量 token 和时间。

**正确做法**：手写 Remotion 代码，使用模板组件（FadeIn、Card、CodeBlock、SectionTitle）快速组装。只在最后一步让 Claude Code 做渲染调试。

## 文件扩展名错误

**问题**：`Artifact not found` 错误，用户无法下载渲染产物。

**根因**：`present_files` 调用时传入 `.mp` 而非 `.mp4` 等不完整的扩展名。

**解决**：始终使用完整扩展名（`.mp4`、`.png`、`.mp3` 等），不要截断。

## 代码块去掉 ANSI 颜色码

从文档/终端提取的代码片段可能包含 ANSI 转义序列（如 `\x1b[33m`、`\033[1m`），在代码块中会显示为乱码。

```tsx
// ❌ 代码块中出现乱码
const code = `interface Msg {\n  id: string;\x1b[33m  name: string;\x1b[0m\n}`;

// ✅ 提取代码时去掉颜色码
// 方法1：sed
sed -i '' 's/\x1b\[[0-9;]*m//g' code-snippets.txt
// 方法2：代码中清洗
const clean = raw.replace(/\x1b\[[0-9;]*m/g, '').replace(/\033\[[0-9;]*m/g, '');
```

**规则**：嵌入视频的代码片段必须是纯文本，提前清洗所有 ANSI 颜色码。

## 文档图片嵌入

当视频基于文档/教程内容时，源文档的截图应嵌入到对应场景：

```tsx
const DocImage = ({ src, delay = 0, maxH = 380 }) => {
  const f = useCurrentFrame();
  const o = interpolate(f, [delay, delay + 20], [0, 1], { ... });
  return (
    <div style={{ opacity: o, display: "flex", justifyContent: "center", marginTop: 16 }}>
      <img src={staticFile(src)} style={{ maxHeight: maxH, maxWidth: 1000, objectFit: "contain", borderRadius: 8 }} />
    </div>
  );
};
```

图片放在 `public/images/<video>/` 下，用 `staticFile()` 引用。

图片入场同样走逐句挂载：用 Reveal 包裹、挂 `at=<句索引>`（句索引口径见 SKILL.md 第三步「逐句入场」——`tts_timing.json` 的 `segments` 场景内局部帧数组，禁用 `segments_global`）。

## 自定义 TTS 脚本音频必须落 public/audio/（staticFile 只读 public/）

**问题**：自定义 TTS 脚本生成的音频在渲染期 404 / `<Audio>` 引用静默失败（EP01 实证：音频写到 `public/` 之外的项目目录）。

**根因**：Remotion 的 `staticFile()` 与静态资源解析**只读项目 `public/` 目录**——之外的路径渲染进程一律不可见。

**解决**：TTS 产物一律放 `public/audio/<video-name>/`（`generate_audio_template.py` 的 `AUDIO_DIR` 默认即此），组件内 `staticFile("audio/<video-name>/01-intro.mp3")` 引用。改写脚本输出路径时必须保持 `public/` 前缀。

## Chrome Headless Shell 缺失

```
ENOENT: no such file or directory, chrome-headless-shell
```

解决：
```bash
cd <项目路径> && npx remotion browser ensure
```

## 渲染 SIGKILL（内存不足 OOM）

```
Exec failed, signal SIGKILL
```

**原因**：H.264 编码器在最后阶段帧重排需要大量内存。

**排查**：`vm_stat` + `ps aux -m | head -20` 查看内存占用（macOS 本机口径）。常见大户：豆包、飞书、Chrome、微信。**沙箱内**改用 cgroup 路径自检：`cat /sys/fs/cgroup/memory/memory.usage_in_bytes /sys/fs/cgroup/memory/memory.limit_in_bytes 2>/dev/null`（见 SKILL.md 第五步「渲染前资源自检」）。

**解决方案（按优先级）**：
1. **分段渲染**（最有效）：分成两段渲染，再用 ffmpeg concat 合并：
   ```bash
   # 分两段（按场景帧数分）；沙箱内 --concurrency=1，本机 macOS 资源充足可用 2
   npx remotion render MyVideo out/parts/part1.mp4 --frames=0-11221 --concurrency=1
   npx remotion render MyVideo out/parts/part2.mp4 --frames=11222-22442 --concurrency=1

   # 合并（注意 concat list 必须用绝对路径）
   printf "file '%s/out/parts/part1.mp4'\nfile '%s/out/parts/part2.mp4'\n" "$PWD" "$PWD" > /tmp/list.txt
   ffmpeg -y -f concat -safe 0 -i /tmp/list.txt -c copy out/final.mp4
   ```
   长片（>8 分钟 / >14,400 帧）的完整分段纪律（场景边界优先、段长 ~5000-6000 帧、逐段校验、总时长校验）见 SKILL.md 第五步「长片渲染纪律」。
2. **降低并发**：`--concurrency=1`（最慢但最安全；沙箱内默认就该是 1）
3. **关闭占内存的应用**：豆包（~2GB）、飞书（~2.7GB）（macOS 本机口径；沙箱内无桌面应用，做 cgroup 自检即可）
4. **减少动态元素**：浮动粒子（每帧计算 N 个 DOM 位置）是最耗内存的

**注意**：SIGTERM 通常是超时，SIGKILL 是 OOM。两者处理方式不同。

## 渲染进程 `Target closed`（Chrome 死亡）

```
Error: Target closed
```

**原因**：Chrome headless 渲染进程死亡——沙箱内最常见于共享内存/内存不足（`/dev/shm` 档位太小时 Chrome 多进程管线崩溃）；偶发于 Chrome 崩溃残留进程。

**处置**：
1. 确认已走 SKILL.md 第五步「长片渲染纪律」分段渲染 + `--concurrency=1`（整片单命令在沙箱内基本必撞）；
2. `pkill -f chrome-headless` 清残留进程，`rm -rf node_modules/.cache` 后重试；
3. 每次重试至少改一个参数（禁止原样重跑——5 次重复触发 harness 反循环硬停），完整降级序见 SKILL.md「渲染失败重试配方」。

**注意**：`Target closed` 是浏览器进程死亡信号，与 SIGKILL（cgroup 击杀）在沙箱内常为同根（资源不足），但处置入口不同——前者先查 Chrome 残留，后者直接降并发/缩段。

## 纯样式批量修改用 sed

全局字号/宽度调整涉及大量数值替换，sed 比 edit 工具高效：

```bash
# 批量替换字号
sed -i '' 's/fontSize: 15, lineHeight: 1.7/fontSize: 18, lineHeight: 1.8/g' src/Video.tsx
# 批量替换卡片宽度
sed -i '' 's/w={380}/w={520}/g' src/Video.tsx
# 精确替换（用上下文避免误改）
sed -i '' '665s/w={280}/w={420}/' src/Video.tsx
```

**注意**：
- 用足够长的上下文匹配，避免误改其他场景的同值
- 优先用 sed 全局替换，个别场景用行号精确替换
- 改完后跑 `npx esbuild src/Video.tsx --bundle --platform=browser --jsx=automatic --conditions=style --outfile=/dev/null` 验证编译（`--conditions=style` 必带——解析 `src/index.css` 的 `@import "tailwindcss"`，缺则必失败）
- **批量数值修改后必须全场景 still 复检溢出**（命令与检查项见 SKILL.md 第四步「静帧预览门」）——全局替换易在个别场景造成溢出，等下一次全片渲染才发现就晚了

## Claude Code 卡住

不要使用 `claude` CLI 嵌套调用（会超时或卡住）。SKILL.md 已更新为直接使用 write_file / str_replace 工具生成代码。

如果当前模型生成代码质量差或卡住：拆分任务（每次生成 3-5 个场景组件）、或手动编写代码。

## 入口文件缺失

确保 `src/index.ts` 存在：
```typescript
import { registerRoot } from "remotion";
import { RemotionRoot } from "./Root";
registerRoot(RemotionRoot);
```

## npx create-video 交互式问题

`npx create-video@latest` 无法通过 stdin 自动化。解决方案：手动搭建或使用模板项目。

## 大型视频制作策略（10分钟+）

1. **先提取素材**：文档截图 → `public/images/`，文字内容分段
2. **写讲解稿**：口语化，每场景 3-6 句
3. **TTS 生成配音**：用脚本逐句生成，自动计算精确时间戳
4. **手写 Remotion 代码**：用模板组件快速组装，不要用 Claude Code
5. **清缓存后渲染**：`rm -rf node_modules/.cache && npx remotion render ...`
6. **验证输出**：检查字幕同步、图片显示、布局

## 已知问题

- 某些模型生成 Remotion 代码质量不稳定：拆分为每次 3-5 个场景组件可提高成功率
- 大型文件（15场景+）建议分批生成，避免超出模型上下文或 token 限制

## 逐句驱动动画（Sentence-Reveal 模式）

**目标**：讲解稿的每句话驱动对应的视觉元素出现，而非整场景一次显示。

> **已提升为主流程默认要求（2026-09-29）**：本模式即 SKILL.md 第三步代码生成的「逐句入场」——每个视觉元素挂 `at=<句索引>`；局部帧版样板组件（Reveal/Keyword）见 `references/VideoTemplate.tsx`。本节留存实现细节与坑。
>
> **帧口径适用范围（重要）**：下方 useSI 示例里的「全局帧 → 场景本地帧」换算（`f - acc`）**仅适用**于元素/字幕挂在 Composition 根部、或沿用旧全局帧模板的场景。若场景组件挂在 `<Sequence>` 内（VideoTemplate.tsx 现行口径），`useCurrentFrame()` 返回的**已是场景局部帧**，直接与 `tts_timing.json` 的 `segments`（局部帧）匹配，**禁止再做 `f - acc` 换算**——减完是负数/错位帧，动画全乱。

**实现模式**：

```tsx
// 1. 获取当前场景的句子索引（必须用场景本地帧！）
const useSI = (sceneIdx: number) => {
  const f = useCurrentFrame();
  // 关键：将全局帧转换为场景本地帧
  let acc = 0;
  for (let i = 0; i < sceneIdx; i++) acc += SCENES[i].duration;
  const localF = f - acc;
  const sceneSubs = SUBS.filter((s) => s[0] === sceneIdx);
  for (let i = 0; i < sceneSubs.length; i++) {
    if (localF >= sceneSubs[i][1] && localF < sceneSubs[i][2]) return i;
  }
  return sceneSubs.length; // 所有句子播完
};

// 2. 元素在指定句子时出现
const Reveal = ({ at, si, children }: { at: number; si: number; children: React.ReactNode }) => {
  const cur = useSI(si);
  const f = useCurrentFrame(); const { fps } = useVideoConfig();
  if (cur < at) return <div style={{ opacity: 0 }}>{children}</div>;
  const s = spring({ frame: f, fps, config: { damping: 100, stiffness: 120 } });
  return <div style={{ opacity: s, transform: "translateY(" + interpolate(s, [0,1], [25,0]) + "px)" }}>{children}</div>;
};

// 3. 场景中使用
const MyScene = () => {
  const si = 3;
  return (
    <div>
      {/* [0] 说"三个平台" → 出现平台卡片 */}
      <Reveal at={0} si={si}><PlatformCards /></Reveal>
      {/* [1] 说"法务要求" → 出现警告 */}
      <Reveal at={1} si={si}><Warning /></Reveal>
      {/* [2] 说"维护噩梦" → 出现错误 */}
      <Reveal at={2} si={si}><ErrorCard /></Reveal>
    </div>
  );
};
```

**⚠️ 踩坑：全局帧 vs 场景本地帧**

```typescript
// ❌ 致命错误：useCurrentFrame() 返回全局帧，字幕时间戳是场景本地帧
const f = useCurrentFrame();
if (f >= sceneSubs[i][1]) ... // 只有场景0能工作！

// ✅ 正确：先转为场景本地帧
let acc = 0;
for (let i = 0; i < sceneIdx; i++) acc += SCENES[i].duration;
const localF = f - acc;
if (localF >= sceneSubs[i][1]) ... // 所有场景都能工作
```

**逐句对齐原则**：写每个场景前，先列出该场景的所有句子（从字幕 JSON），然后为每个视觉元素指定 `at` 对应的句子索引。

## 动态背景的性能权衡

| 效果 | 内存影响 | 帧计算 | 建议 |
|------|---------|--------|------|
| 移动网格（transform translate） | 低 | 每帧 1 次 | ✅ 保留 |
| 渐变光球（radial-gradient + sin） | 低 | 每帧 1 次 | ✅ 保留 |
| GlowCard 脉冲（boxShadow + sin） | 低 | 每帧 1 次/卡 | ✅ 保留 |
| 浮动粒子（N 个绝对定位 div） | **高** | 每帧 N 次位置计算 | ❌ 砍掉或降到 3-5 个 |
| 代码打字效果（substring） | 低 | 每帧 1 次 | ✅ 保留 |

**规则**：N 个粒子 = N 个 DOM 节点每帧重算位置，15 个粒子在 MacBook Air 上会导致 OOM。

## ⭐ 场景间隔必须为 1 秒（30帧@30fps）

**问题**：TTS 生成的音频文件实际时长比 SCENES.duration 少 2-3 秒，导致场景之间出现 2-3 秒的空白等待，体验很差。

**根因**：SCENES.duration 应该基于**每个句子音频的实际时长**累加，而非手动估算或用字幕时间戳。

**正确做法**：
1. TTS 脚本逐句生成音频时，用 ffprobe 取每句实际时长，累加得到精确的字幕时间戳（本地帧）
2. `duration = 最后一句的 end 帧 + 30`（1秒间隔）
3. **不要 cap 字幕的 end 时间**，不要在句子之间加额外间隔
4. Root.tsx 的 TOTAL_FRAMES 也要同步更新

**验证**：`音频时长 ≈ (duration - 30) / 30`，每个场景间隔约 1 秒。

## ⭐⭐ 文档图片必须严格按原文档内容匹配（EP02 血泪教训）

**问题**：图片放错场景、出现时机错误（讲完后才闪现、或机械展示3秒）。

### 图片分配原则

1. **必须对照原文档**：找到图片在 Word/PDF 中的原始位置，看它前后对应什么文字
2. **图片文件与文档顺序映射**：按文件大小/尺寸比对，确定 image1~N 与文档中 IMAGE #1~#N 的对应关系
3. **按讲解内容匹配场景**：图片应该出现在讲解到相关内容时，不是按场景编号顺序

### 图片出现时机

- **伴随语音出现**：图片在第 0 句或第 1 句就出现，与语音同步展示
- **讲完就切走**：场景 duration = 最后一句 end + 30帧，不加额外图片展示时间
- **❌ 不要在最后一句说完后才显示图片**（听众已进入下一话题）
- **❌ 不要机械展示固定时长**（如3秒），应该跟着语音走

### 正确做法示例

```tsx
// ✅ 图片在讲到相关内容时同步出现
const S10 = () => {
  const si = 10; // CRDT 两大流派
  return (
    <W si={si}>
      <T si={si}>CRDT 两大技术流派</T>
      {/* [0] 说"两大流派"时，对比表同步出现 */}
      <Reveal at={0} si={si}><DocImg src="images/ep02/image4.png" active={true} maxH={300} /></Reveal>
      {/* 之后的句子继续讲解图片内容 */}
    </W>
  );
};
```

（`Reveal` 的 `at=<句索引>` 句索引口径见 SKILL.md 第三步「逐句入场」——`segments` 场景内局部帧数组。）

### 字幕时间戳生成

**必须从每个句子音频的实际时长生成**，句子之间不加间隔：

```python
for j, text in enumerate(texts):
    fname = f"{AUDIO_DIR}/{si+1:02d}-{j+1:02d}.mp3"
    r = subprocess.run(['ffprobe', '-v', 'quiet', '-show_entries', 'format=duration', '-of', 'csv=p=0', fname], ...)
    dur_frames = math.ceil(float(r.stdout.strip()) * 30)
    subs.append([si, scene_start_frame, scene_start_frame + dur_frames, text])
    scene_start_frame += dur_frames  # 不加间隔！间隔只在场景之间
```

## 字幕时间戳：全局帧 vs 本地帧

**陷阱**：TTS 脚本可能输出全局帧或本地帧时间戳，必须统一为**本地帧**（useSI 按本地帧比较）。

**检查方法**：Scene 1 的第一条字幕 start 如果 > 0，说明是全局帧，需要减去场景起始偏移量。

**转换代码**：
```python
offsets = []
acc = 0
for dur in SCENE_DURATIONS:
    offsets.append(acc)
    acc += dur
for sub in subtitles:
    sub[1] -= offsets[sub[0]]  # start 转为本地帧
    sub[2] -= offsets[sub[0]]  # end 转为本地帧
```

## Core dump 堆积撑爆打包内存

**问题**：渲染/打包阶段反复退出码 137，cgroup 内存随工程目录体积异常上涨；工程内出现多个 `core*` 文件。

**原因**：Chrome 崩溃生成 core dump，单个文件可达约 500MB。2026-10-13 实测多份 core 堆积与 bundle 内存超过 3G、清理后降至约 640MB 同步变化；具体计入机制未逐一验证，处置上应视为打包阶段内存/体积风险。

**处置**：先执行 `find . -maxdepth 2 -type f -name 'core*' -size +50M -delete && du -sh .` 清理并复核工程体积；根治依赖 gateway 侧 Docker 启动参数 `--ulimit core=0`（随 gateway 部署生效，旧 warm 容器不追溯）。

## `pkill -f` 自匹配杀掉自身 shell

**问题**：执行 `pkill -f remotion` 后，当前执行命令的 shell 也被杀掉，后续排查中断。

**原因**：`-f` 匹配完整命令行，模式串 `remotion` 也出现在这条 `pkill` 命令自身的 shell 命令行里。

**处置**：先用 `pgrep -f '<pattern>'` 列 PID，核对后按 PID `kill`；或收窄模式（如 `pkill -f chrome-headless`），避免匹配当前 shell。

## 浅色背景下的像素阈值陷阱

**问题**：像素检查用「任一通道 >245 即前景」的判据，浅色背景仍被大量判为前景，阈值试错多轮。

**原因**：实测背景色可为 `(247,251,250)`，本身高于 245；全图/整带扫描又把合法背景与浅色前景混在一起。

**处置**：前景判定阈值取 ≥252，并只扫描目标容器边界外侧的少量行/列；关键结论必须输出边界外前景行计数（0 才通过）。
