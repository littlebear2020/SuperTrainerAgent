---
name: auto-video-flow
description: |
  AI 协作视频制作全流程：从内容稿到视频产出。使用当用户要求制作视频、生成科普视频、创建讲解视频时触发。流程包括：(1) AI 撰写视频内容稿和分镜表，(2) TTS 生成配音，(3) 生成 Remotion 代码，(4) 静帧预览自检，(5) 渲染输出视频。触发词："制作视频"、"生成视频"、"remotion视频"、"科普视频"、"讲解视频"。
required-secrets:
  - name: VOLCENGINE_VOICE_ACCESS_KEY

---

# Remotion 视频制作全流程

内容稿 + 分镜表 → TTS 配音 → 生成代码 → 静帧预览门 → 渲染输出。

## 前置要求

- **Node.js** v22+, **Python 3**, **FFmpeg**。
- **TTS 红线**：沙箱内只用豆包 TTS（`scripts/_doubao_tts.py`），edge-tts 必失败，任何技能不得回退。（沙箱出网隔离拦微软 TTS 服务器；synthesize 抛 RuntimeError 时如实报错停止。）
- **大输出落盘**：可能产生大量输出的命令（ffprobe、格式解析、OCR、转码日志等）必须把完整输出重定向到文件，再用 read_file 分段/摘要读取；禁止把全量输出直接打到 stdout（撑爆上下文）。
- **长命令后台化**：预计运行超过 3 分钟的命令（渲染、转码、批量 TTS）不得前台阻塞执行——用 nohup 后台启动 + 轮询查进度（标准形态见第五步「后台执行 + 轮询」）。bash 命令**输出静默超过 300 秒会被沙箱静默击杀**（`no_change_timeout` 语义，非挂钟上限），前台长渲染/前台静默等待 300 秒必踩线。
- **沙箱离线纪律**（2026-10-23 公司服务器事故）：沙箱内**禁止任何运行期安装**——`pip install` / `npm install` / `apt-get` 一律不得执行。生产服务器外网不可达，`pip install` **不会报错而是静默挂起**（曾让 agent 以为“下载慢”而 sleep 空等十余分钟）；需要的包只能在镜像构建时烤入，`import` 失败且确属镜像缺包时如实告知用户“需重建沙箱镜像”并停止，不要自行安装。图像处理（放大核验、去标识/inpaint、裁切）直接用**系统 `python3`**（`/usr/bin/python3`，预装 OpenCV `cv2`、Pillow、numpy；命令行另有 ffmpeg / ImageMagick / tesseract）。**不要创建或激活 venv**——工作区里若有旧会话遗留的 `.venv`，它隔离了系统包（`import cv2` 必失败但镜像其实有），`import` 报错先 `deactivate`/直接用 `/usr/bin/python3` 重试，而不是去装包。

## 创建 Remotion 项目

每个视频需要一个独立的 Remotion 项目。**优先使用预装模板（无需网络），备用方案从 skill 模板安装。**

### 方案一：预装模板（推荐，服务器部署用）

sandbox 镜像已预装 Remotion 项目模板到 `/opt/remotion-template/`（含 node_modules 和 Chrome headless shell），可直接复制：

```bash
cp -r /opt/remotion-template /mnt/user-data/workspace/<video-name>
cd /mnt/user-data/workspace/<video-name> && rm -rf node_modules/.cache
```

> ⚠️ 复制目标用**新建**的工程目录，分镜表/讲解稿等上游产物放在工程目录之外。**不要先 `rm -rf <目录>/*.md` 之类“清场”再复制**——2026-10-23 事故中 `rm *.md` 顺手删掉了同目录的 `storyboard.md`，整段分镜被迫重做。目录已存在时先 `ls` 确认内容再决定。

检查模板是否存在：
```bash
ls /opt/remotion-template/package.json
```

复制前比对模板版本（防镜像烤入的过期模板静默生效）：镜像构建时会把模板源 hash 写入 `/opt/remotion-template/TEMPLATE_VERSION`，与 skill 模板目录现算的同口径 hash 比对，不一致说明镜像模板过期——如实告知用户须重建 sandbox 镜像（部署手册 K16），不要默默用旧模板渲染：

```bash
cat /opt/remotion-template/TEMPLATE_VERSION
cd /mnt/skills/public/auto-video-flow/templates/remotion-base && find . -type f -not -path "./node_modules/*" -not -name ".DS_Store" -not -name "TEMPLATE_VERSION" | sort | xargs md5sum | md5sum | awk '{print $1}'
# 两行输出不一致 → 镜像模板过期
```

### 方案二：从 skill 模板安装（仅本地开发用，sandbox 环境请用方案一）

如果 `/opt/remotion-template/` 不存在（仅本地开发环境），从 skill 目录复制并安装依赖：

```bash
# ⚠️ 仅本地开发！sandbox 镜像已预装，不需要 npm install
cp -r /mnt/skills/public/auto-video-flow/templates/remotion-base /mnt/user-data/workspace/<video-name>
cd /mnt/user-data/workspace/<video-name> && npm install && npx remotion browser ensure
```

> 方案二需要网络下载 node_modules（~200MB）和 Chrome headless shell（~150MB），sandbox 环境中不可用。

> **模板家族**：`templates/` 下除 `remotion-base`（通用口播）外还有 `remotion-news`（新闻联播风，带标题条/底部信息条）与 `remotion-quote`（引语金句风），三者 props 契约相同、均走全局帧字幕 + 根部挂载约定。news 模板的 `render_date` 参数（底部信息条日期）由你组织 props JSON 时填当天日期（`date +%Y-%m-%d`）——刻意不用 `new Date()`（墙钟日期让同一 props 的两次渲染产出不同帧）；不填则页脚日期留空。

## 工作流程

### 第零步：断点续作盘点

开工前先盘点工作区，避免重做已完成的步骤：

1. 查看系统注入的 `<workspace_files>` 块（及其中列出的 manifest 文件），确认本项目是否已有 outline/内容稿/分镜表（storyboard.md）/音频/静帧（stills/）/片段等产物
2. 有则断点续作：复用已有产物，从缺失的步骤继续；**绝不重新生成已完成的步骤**
3. 确认没有任何既有产物，才走下方第一步起的完整流程

### 第一步：内容稿 + 分镜表

**确认门①**（写稿前向用户确认）：主题、受众、时长、风格、语音性别、字幕需求、**是否配 BGM**（要配则请用户提供音频文件，放置与启用口径见第三步「BGM 配置」）。

**确认阶段最小侦察**：用户确认方案前只做最小侦察（读文档结构/时长/关键参数即可）；OCR、抽帧、全量解析、试合成等重操作一律等确认通过后再执行。

按时长拆场景（每场景 **15-30s**——30-50s 长场景是画面单调的温床）：
- 1min → 3-4 场景，3min → 6-10 场景，5min → 10-16 场景

撰写讲解稿原则：口语化、每句 15-25 字、每场景 3-8 句、有停顿感。

```markdown
## 场景1：开场引入（20秒）
"大家好！今天我们来了解一个开源项目——OpenMAIC。"
"MAIC 的全称是 Multi-Agent Interactive Classroom，多智能体互动课堂。"
"想知道它是怎么做到的吗？让我们一起来探索。"
```

**产出分镜表** `storyboard.md`（落工作区，纳入第零步断点续作盘点清单）——先写旁白内容稿，再逐场景填表，每场景一行：

| 场景 | 时长 | 旁白句（编号） | 屏显内容 | 视觉事件（句N→元素） | 版式 |
|------|------|----------------|----------|----------------------|------|
| 1 开场 | ~18s | 1-4 | 标题「OpenMAIC」+ 副题关键词 | 句1→大标题入场；句2→全称逐词高亮 | 标题页 |
| 2 核心概念 | ~25s | 5-10 | 三个关键词卡片（各 ≤12 字） | 句5→卡片1；句7→卡片2；句9→卡片3 | 要点逐出 |
| 3 收尾 | ~15s | 11-13 | 一句 CTA + 域名 | 句12→CTA 高亮入场 | 结尾 CTA |

分镜表三列硬约束：**视觉事件列非空**（每场景 ≥2 个句索引挂载点）、屏显列 ≤60 字（见下「屏显与旁白分离」）、**相邻行版式不同**。

**「屏显与旁白分离」**（强纪律）：屏显只放关键词/数字/结构，**每场景屏显总量 ≤60 字**；旁白全文只进音频与字幕，不上屏。例外：用户点名要「金句视频」时，引语类内容可整句上屏。

**版式菜单**（自定义组件路径，8 种以内防选择瘫痪——DataDrivenVideo 路径的版式能力以模板 props 契约为准，两路径各自演进）：标题页 / 要点逐出 / 大数字 / 对比双栏 / 流程步骤 / 图文（文档截图或用户素材，素材放 `public/images/<video>/`，用 `staticFile()` 引用）/ 引语 / 结尾 CTA。**相邻场景不得同版式**。

**确认门②**（按创作模式条件化）：
- **固定脚本模式**（用户上传了完整口播稿/逐字稿——分镜表只是用户文本的机械推导）：分镜表**落盘 + 向用户展示，但不阻塞**——展示后直接进第二步 TTS；用户事后异议时按场景改表重跑该场景即可（单场景改动成本低，不必前置卡门）。
- **AI 自由创作模式**（内容稿由你撰写）：分镜表交用户过目，**修改在表上迭代**（改 markdown 比重写组件便宜两个数量级），**用户确认后才进第二步 TTS——此门必须等，不得跳过**（创作内容未经确认即进 TTS，用户就失去了最便宜的修改窗口）。

### 第二步：TTS 音频与字幕时间轴

> ⚠️ **TTS 红线**：沙箱内只用豆包 TTS（`scripts/_doubao_tts.py`），edge-tts 必失败，任何技能不得回退。

1. 为每个场景/帧准备旁白文本（来自第一步的内容稿）。
2. 直接使用 `scripts/generate_audio_template.py`（复制改写为 `generate_[video]_audio.py`）逐句合成——它会自动加载同目录的 `_doubao_tts.py`（需 `VOLCENGINE_VOICE_ACCESS_KEY`；正式路径显式传 speaker，只有 `speaker=None` 时才要求 `DOUBAO_TTS_DEFAULT_SPEAKER` env 兜底）：
   ```bash
   cp /mnt/skills/public/auto-video-flow/scripts/generate_audio_template.py /mnt/user-data/workspace/scripts/generate_[video]_audio.py
   cp /mnt/skills/public/auto-video-flow/scripts/_doubao_tts.py /mnt/user-data/workspace/scripts/_doubao_tts.py
   ```
   **复制脚本时必须连同 `_doubao_tts.py` 一起复制**——generate 脚本运行时按同目录相对路径动态加载它，漏拷即报模块缺失。

   **配音音色（硬规则）**：
   - 在册授权豆包音色（单一事实源 = 后端 `voice_catalog.py` 的 `CURATED_VOICES`）：男声 `zh_male_m191_uranus_bigtts`（云舟2.0·稳重大气，课程视频主流）、`zh_male_liufei_uranus_bigtts`（刘飞2.0·富有表现力）；女声 `zh_female_vv_uranus_bigtts`（`DOUBAO_TTS_DEFAULT_SPEAKER` env 默认）。**每次任务按需求确认音色**——用户要男声就选男声 id；禁止无脑沿用默认、禁止凭记忆编造 id（在册外的 id 会报 "resource ID is mismatched"）。
   - 模板已带 `SPEAKER` 常量与 `speaker` 形参（`generate_tts(text, out, speaker=...)` / `generate_tts_timestamped` 同）：**必须显式确定音色**（改模板 `SPEAKER` 常量或逐句传参），**禁止依赖 env 默认兜底**——env 默认是女声，裸调合成 = 成片女声 + 无法自证（2026-10-12 FDE 事故形态：模板裸调曾使男声需求物理上不可能达成）。
   - **音色自证（不接受口头声明）**：音频生成后必须出示证据——生成日志每句的 `(speaker=...)` 打印行 + `ffprobe` 抽查各段时长正常。日志没有 speaker 行 = 无权宣称音色正确。

3. 字幕时间戳三级策略（首选词级，逐级降级）：
   - **首选：豆包 TTS 自带字级时间戳**。请求参数 `req_params.audio_params.enable_subtitle: true`（仅 TTS2.0/ICL2.0 生效），响应新增 event 364（TTSSubtitle，每子句一条），`sentence.words[]` 含 `word/startTime/endTime/confidence`——**基于原文打轴、时间为整 session 相对秒**。2026-09-08 已用生产凭证实测通过（探针 `temp/doubao_ts_probe/probe2.py`：两句 26+16 词全量返回，末词 8.515s 对齐 ffprobe 实测 9.288s 片长）。注意：`additions.cache_config` 命中缓存的响应不带字幕；ssml/latex 会关闭字幕。
   - **降级：whisper.cpp 本地对齐**（官方 `@remotion/captions` + remotion-captions skill 的 `transcribe-captions.md`，离线兜底，需把模型烤进沙箱镜像）。
   - **最后兜底：ffprobe 句级时长**——逐句 ffprobe 实测时长累加出句级时间轴，精度只到句。`generate_audio_template.py` 已升级为**词级首选**：直接产出全局毫秒 `captions` 字段（换算契约同上），词级不可用（合成失败/无字幕事件/数据畸形）时自动降级、只出句级 `segments`——**优先直接用模板脚本，勿再手写词级换算**。
4. 产出统一转为 `@remotion/captions` 的 Caption JSON（`{text, startMs, endMs, timestampMs, confidence}`）。

   ⚠️ 词级换算（豆包路径必须做）：`synthesize_with_timestamps` 返回的
   `startTime/endTime` 是【每次调用 session 相对秒】——逐句/逐场景调用各自从 0 计。
   写入 `props.captions` 前必须换算为【全局毫秒】：
   `startMs = round((该句全局起始秒 + word.startTime) * 1000)`，`endMs` 同理；
   该句全局起始秒 = 场景起点（Σ 前序场景 durationInFrames / fps）+ 句内偏移。
   直接拼接各次调用的原始 words → 第 2 句起 karaoke 字幕静默错位
   （漏乘 1000 则第 1 句就错）。消费端见模板 `src/KaraokeCaptions.tsx`（全局毫秒契约）。

5. **字幕时间轴为全局时间轴，字幕组件必须挂在 Composition 根部**（呼应模板 DataDrivenComposition.tsx 的修复；嵌进 `<Sequence>` 第 2 场景起必消失）。`tts_<video>.json` 里两套段别混淆：`segments_global`（全局帧，供 `DataDrivenVideo` 的 `props.subtitleSegments`）与 `segments`（场景内局部帧，仅供 VideoTemplate.tsx 自定义组件路径，其字幕随场景挂 `<Sequence>` 内、局部帧匹配）。注意形状映射：`segments_global` 是数组 `[sceneIndex, startFrame, endFrame, text]`，填入 `props.subtitleSegments` 时必须逐条转成对象 `{"sceneIndex": …, "startFrame": …, "endFrame": …, "text": …}`——照抄数组字段全为 undefined，字幕静默不出现。

> **衔接**：本步产出 `tts_<video>.json`——复制为项目 `src/tts_timing.json`（第三步代码直接 import，禁止抄写到第二处），然后进第三步代码生成。

### 第三步：代码生成

**使用 write_file / str_replace 工具直接生成 Remotion 代码**。不要调用外部 `claude` CLI（嵌套调用会卡住或超时）。

#### 步骤

1. **读取模板**：读取此 skill 的 `references/VideoTemplate.tsx` 了解组件结构
2. **确定时长与字幕数据源**：第二步 TTS 产出的 `tts_<video>.json`（已复制为 `src/tts_timing.json`）是唯一时长事实源——代码中 import 使用，**禁止把帧时长/字幕时间戳手工抄写到第二处**（旧流程"从 TTS 输出复制 SCENES 帧数"已废除，抄写必然漂移）。
3. **创建视频组件** `src/[VideoName]Video.tsx`（用 `write_file`）：
   - **分镜表驱动**：每个场景组件对应分镜表一行——版式/屏显内容/视觉事件列逐条落地为代码；屏显只用分镜表屏显列的关键词，不搬旁白全文
   - **DataDrivenVideo 直用 = 草稿/原型专用**：把 scenes/subtitleSegments 组织成 props JSON，用模板预置的 `DataDrivenVideo` 合成（`src/DataDrivenComposition.tsx`，Root.tsx 已注册，渲染时 `--props` 传入，无需写组件代码）——适合快速验证内容/时长/字幕链路，但纯 narration props 的画面是**纯色背景 + 整段旁白全文居中的文字墙**。**成片一律走自定义视觉路径**（下一条）——模板固定版式路线（visual 槽位五版式）已于 2026-10-09 实现并经用户验收后否决（产品决策：平台核心画面能力 = agent 逐场景创作，模板化画面质量不达交付线；实现留在 git 历史 1f76f1e0e/7693b1dac 可溯）。（BGM props 口径见下方「BGM 配置」）
   - 自定义视觉（**成片默认路径**）：为每个场景创建 React 组件（参考模板中的 IntroScene / MainScene / ConclusionScene），场景用 `<Sequence from={起点} durationInFrames={时长}>` 包裹——场景组件内 `useCurrentFrame()` 是**局部帧**（从场景起点重计），入场动画才能逐场景生效
   - **「逐句入场」默认要求**：每个视觉元素挂 `at=<句索引>`（Reveal 组件，样板见 VideoTemplate.tsx），随旁白逐句入场——整场景一次性全显是文字墙根因，禁止。**句索引数据源（钉死）**：句索引 = `src/tts_timing.json` 的 **`segments`（场景内局部帧）数组**中该场景的句序号；**禁用 `segments_global`**——那是全局帧，仅供根部挂载字幕层 `props.subtitleSegments` 使用（第二步两套段别口径不变）。场景组件挂在 `<Sequence>` 内，`useCurrentFrame()` 即局部帧、与 `segments` 直接匹配，**禁止全局帧换算**（gotchas 旧示例的全局换算仅适用根部挂载层，勿照搬）
   - 复制模板中的 Subtitle 组件和音频 `<Sequence>` 排布
4. **注册到 Root.tsx**（用 `str_replace`）：
   - import 视频组件
   - 添加 Composition 并挂 `calculateMetadata`：从 `tts_timing.json` 求和各场景实测音频帧数得出 `durationInFrames`（官方规则见 remotion-official 的 `calculate-metadata.md`）。**时长音频驱动，不要再把 TTS 输出的 total 手工抄进 durationInFrames**
5. **验证编译**：
   ```bash
   # --conditions=style 必带：解析 src/index.css 的 @import "tailwindcss"（缺则该命令必失败）
   cd <项目路径> && npx esbuild src/Root.tsx --bundle --platform=browser --jsx=automatic --conditions=style --outfile=/dev/null
   ```
6. **修复错误**（如有）：用 str_replace 修复编译错误，常见问题见下方"关键规则"
7. **过静帧预览门**：编译通过后先进第四步「静帧预览门」抽帧自检；静帧全过才进第五步渲染输出（渲染命令在第五步；>8 分钟长片按第五步「长片渲染纪律」分段渲染）

#### BGM 配置（确认门①勾选了 BGM 时）

- BGM 文件放项目 `public/bgm.mp3`——**首选由用户上传提供**（沙箱无网不现下载；不要引用任何本机音乐下载能力/技能）。确认门①要配 BGM 但用户未给文件 → 先请用户上传，不要跳过
- **用户不上传时的内置曲**（模板已内置，项目初始化即随模板带出）：`cp public/bgm/bgm-calm.mp3 public/bgm.mp3` 启用沉稳曲，或 `cp public/bgm/bgm-upbeat.mp3 public/bgm.mp3` 启用轻快曲——两首均**免署名许可**（Mixkit Stock Music Free License，Educational Purposes 与 web 视频平台在允许列），出处与许可快照见模板 `public/bgm/ATTRIBUTION.md`；**勿在成片中混入任何署名要求不明的第三方音频**
- **DataDrivenVideo 路径（`bgmEnabled`/`bgmVolume` props 口径的唯一出处）**：组织 props 时显式 `bgmEnabled: true`、`bgmVolume` 建议 0.12-0.18；模板在 `public/bgm.mp3` 缺失时**静默降级为无 BGM**（`DataDrivenComposition.tsx` 的 getStaticFiles 守卫，既有行为）——渲染后发现没 BGM 先查文件是否在位（内置曲启用方式 = 上面的 `cp` 到位后照走本路径）
- **自定义组件路径**：写法见 `references/VideoTemplate.tsx` 根部硬编码 `<Audio>` 样板——该样板在文件缺失时渲染期抛错，未备文件删除该行。两条路径口径不混用

#### 关键规则

**正确性规则**：

- **中文字体**：`src/index.css` 已内置字体回退栈（Noto Sans SC / PingFang SC / Microsoft YaHei），不要删除；自定义组件继承 `:root` 字体即可，否则中文显示为方块。详见 `references/gotchas.md`
- **中文引号**：JSX 属性中的 `""''` 必须替换为 `“”‘’` 弯引号或 HTML 实体 `&ldquo;`/`&rdquo;`
- **Unicode 转义**：JSX 文本节点中 `\uXXXX` 不会被解析，必须用 `{"\uXXXX"}` 表达式或直接写中文
- **数据一致性**：时长与字幕时间戳必须来自同一份 `src/tts_timing.json`（import，非抄写）
- **字幕挂载（DataDrivenVideo 路径）**：字幕组件挂在 Composition 根部，按全局帧渲染（嵌进 `<Sequence>` 会从 0 重计帧号，第 2 场景起字幕必消失——模板 DataDrivenComposition.tsx 已按此实现）。自定义组件路径走局部帧口径：字幕随场景挂 `<Sequence>` 内（SceneSubtitle），见第二步「两套段别」说明与 VideoTemplate.tsx 文件头——两条路径各自的纪律不变，勿混用
- **布局**：内容垂直居中（justifyContent: "center"），底部 100px 预留字幕空间（与 gotchas.md 满屏布局表口径一致）
- **水平优先**：水平布局优于垂直布局

**美学规则**（与第一步分镜表同源，第四步静帧预览门逐条检查）：

- **动效密度**：任意连续 90 帧（3s）画面至少一次视觉事件（元素入场/高亮/计数/转场）；单场景静止持续不得超 150 帧（5s）；每场景 ≥2 个句索引挂载点；相邻场景版式不得相同
- **屏显与旁白分离**：屏显只放关键词/数字/结构，每场景屏显总量 ≤60 字（第一步分镜表已约束，编码同口径复核）
- **性能护栏**：浮动粒子 >5 个禁用；优先 transform/opacity 类低开销动效（gotchas「动态背景的性能权衡」表——动态背景用移动网格/渐变光球等 ✅ 级效果）

#### 大型视频（10分钟+ / 15场景+）

分批生成代码，每次创建 3-5 个场景组件，避免一次性生成过长的文件。可用模板组件快速组装（FadeIn、Card、CodeBlock、SectionTitle、DocImage）。

### 第四步：静帧预览门

全片渲染前先抽静帧自检——把「看到画面」从渲染成品后挪到渲染前，视觉修改在秒级静帧上验证，不再每次修改赌一次全片渲染。**静帧全过才进第五步渲染输出**。

#### 抽帧清单（每场景 3 帧）

1. **基础布局帧**：场景起点 +30 帧。场景起点帧 = `src/tts_timing.json` 前序场景帧数累加（与第五步长片分段边界同一推导口径，一行 python/jq 可得）
2. **视觉事件帧**：分镜表视觉事件列标注的某句边界帧 +15 帧——验 Reveal 真的生效。不要抽「场景中点」：中点可能恰落在两个视觉事件之间，抽到最空的帧，漏检率最高
3. **动画完成态末帧**：以最后一个视觉事件/动画结束帧为基准再加 15 帧余量，且不超过 `场景起点 + duration - 15`——只抽场景起点或事件中间帧会把未完成布局误判为成片（2026-10-13 实测：抽帧结果与静帧不一致）

#### 抽帧命令

⚠️ **`--frame=N` 是全局帧**（相对整个 Composition 起点）——不是场景内局部帧；用局部帧抽帧会抽错场景/抽到黑帧。

```bash
cd <项目路径> && mkdir -p stills
# 自定义组件路径：id = Root.tsx 注册的 Composition id
npx remotion still MyVideo stills/scene01-a.png --frame=45
# DataDrivenVideo 路径：id 固定 DataDrivenVideo，必须带 props
npx remotion still DataDrivenVideo stills/scene01-a.png --frame=45 --props=./props.json
```

#### view_image 逐张自检（六条检查项）

stills 在 `/mnt/user-data/workspace/` 下、view_image 白名单内，逐张核对：

- [ ] 无溢出（内容不触画面边缘）
- [ ] 底部 100px 字幕区无内容侵入（字幕文本本身除外）
- [ ] 与相邻场景版式不重复
- [ ] 屏显内容与分镜表一致（关键词，非整段旁白）
- [ ] 字号符合 gotchas 尺寸表
- [ ] 无方块字（中文豆腐块）
- [ ] 关键卡片/容器边界量化断言全过（容器外框坐标外逐行扫描前景色，行计数 = 0）

**未过 → 改代码 → 只重抽失败场景**（不重抽全部）；**全过 → 才进第五步渲染输出**。

#### 降级路径（view_image 不可用 / 模型无视觉时）

1. stills 复制到 `/mnt/user-data/outputs/stills-review/`（独立子目录，**审阅用可删**，不混交付物），请用户过目确认
2. 跑 Pillow 像素启发式自检（沙箱镜像含 Pillow；不新增文件，内联执行；初版阈值，按误报/漏报微调）：

```bash
python3 - stills/scene01-a.png stills/scene02-a.png stills/scene03-a.png <<'PYEOF'
from PIL import Image
import statistics, sys

# 检测：字幕区侵入（底部 100px 横带、排除字幕条自身合法区后非背景 >2%）/
# 边缘溢出（四边 10px 边带 >1%）/ 版式雷同（相邻两张帧平均像素差 <2）。
# 字幕区排除（2026-09-30 复审实测）：字幕文本占底带 2-10%（英文短字幕 1.9%、
# 中文 22 字 6.3%、35 字两行 9.9%）——单一阈值分不开「合法字幕」与「内容侵入」，
# 故按 Subtitle 组件几何（bottom:50 / 居中 / maxWidth 1400 / fontSize 36）排除
# 中央条 x∈[200,1720] × y∈[900,1040]（两行余量），只查底带其余区域。
# 非背景判定距离 60（动态背景辉光/网格差值低于此不算，±12 档全图误报）；
# 顶边带跳过前 8px（顶部进度条合法占位）。末两张应为相邻场景的同点位帧。
DIST = 60

def med(pts): return tuple(int(statistics.median(p[i] for p in pts)) for i in range(3))
def load(p): return Image.open(p).convert("RGB")

def corner_bg(im):
    w, h = im.size; px = im.load(); s = 20; ms = []
    for x0, y0 in ((0,0),(w-s,0),(0,h-s),(w-s,h-s)):
        ms.append(med([px[x,y] for y in range(y0,y0+s) for x in range(x0,x0+s)]))
    return med(ms)

def ratio_nonbg(im, bg, box, step=4, mask=None):
    px = im.load(); tot = hit = 0
    for y in range(box[1], box[3], step):
        for x in range(box[0], box[2], step):
            if mask and mask(x, y): continue
            c = px[x, y]; tot += 1
            if any(abs(c[i]-bg[i]) > DIST for i in range(3)): hit += 1
    return hit / tot if tot else 0

def subtitle_strip(x, y):
    return 200 <= x < 1720 and 900 <= y < 1040  # 字幕条合法区，见顶部注释

paths = sys.argv[1:]
ims = [load(p) for p in paths]
for p, im in zip(paths, ims):
    w, h = im.size; bg = corner_bg(im)
    sub = ratio_nonbg(im, bg, (0, h-100, w, h), mask=subtitle_strip)
    edges = {
        "top(8-18px)": ratio_nonbg(im, bg, (0, 8, w, 18)),
        "bottom": ratio_nonbg(im, bg, (0, h-10, w, h)),
        "left": ratio_nonbg(im, bg, (0, 0, 10, h)),
        "right": ratio_nonbg(im, bg, (w-10, 0, w, h)),
    }
    warns = []
    if sub > 0.02: warns.append(f"字幕区非背景 {sub:.1%} > 2% —— 内容侵入字幕区？")
    for k, v in edges.items():
        if v > 0.01: warns.append(f"边缘 {k} 非背景 {v:.1%} > 1% —— 内容溢出触边？")
    print(p, "OK" if not warns else "WARN: " + "; ".join(warns))

for a, b in zip(ims, ims[1:]):
    if a.size != b.size: continue
    pa, pb = a.load(), b.load(); w, h = a.size; acc = tot = 0
    for y in range(0, h, 8):
        for x in range(0, w, 8):
            ca, cb = pa[x, y], pb[x, y]
            acc += sum(abs(ca[i]-cb[i]) for i in range(3)); tot += 3
    d = acc / tot
    print(f"layout-diff {d:.1f}" + ("  WARN: <2 相邻帧几乎相同——版式雷同？" if d < 2 else ""))
PYEOF
```

3. 对关键卡片/容器执行边界量化断言（坐标来自组件几何/浏览器测量；四周各取 8px 外带，逐行扫描白/近白前景；前景判据 = **RGB 全通道 ≥252**，防 `(247,251,250)` 类浅底误报）：

```bash
python3 - stills/scene01-c.png 200 300 900 600 <<'PYEOF'
from PIL import Image
import sys

path, x1, y1, x2, y2 = sys.argv[1], *map(int, sys.argv[2:6])
im = Image.open(path).convert("RGB")
width, height = im.size
margin = 8

def foreground(color):
    return all(value >= 252 for value in color)

rows = []
# 上下外带：横向扫描容器宽度，捕捉文字/内容越过底边或顶边（2026-10-13 事故方向）。
for y in (*range(max(0, y1 - margin), y1), *range(y2, min(height, y2 + margin))):
    if any(foreground(im.getpixel((x, y))) for x in range(x1, x2)):
        rows.append(y)
# 左右外带：纵向扫描容器高度，捕捉横向溢出。
for y in range(y1, y2):
    outside_x = (*range(max(0, x1 - margin), x1), *range(x2, min(width, x2 + margin)))
    if any(foreground(im.getpixel((x, y))) for x in outside_x):
        rows.append(y)
rows.sort()
print(f"outside-foreground-rows={len(rows)}")
if rows:
    raise SystemExit(f"FAIL: rows={rows[:10]}")
PYEOF
```

#### 与长片渲染纪律的关系

本门在渲染输出**之前**；>8 分钟长片过门后仍按第五步「长片渲染纪律」分段渲染。

#### 资源纪律（F5 OOM 背景同源）

- `still` 无 `--concurrency` 参数（实测 4.0.522）——**串行抽帧**：一次一张，不并行起多 Chrome
- **静帧门执行期间不清 `node_modules/.cache`**：bundle 缓存让第 2 张起提速一个量级（方案一复制模板时的清缓存只在项目初始化发生一次）
- **不与全片/分段渲染并发执行**
- 首次 still 含全量 bundle 开销（可能 30s+），后续 still 走 bundle 缓存（秒级-十几秒）

### 第五步：渲染输出

确保 `src/index.ts` 入口存在。

#### 渲染前资源自检（开工前必做一次）

```bash
# 读容器实际内存档位（勿写死 3072MB——档位可被运维调整，读实际值）
cat /sys/fs/cgroup/memory/memory.usage_in_bytes /sys/fs/cgroup/memory/memory.limit_in_bytes 2>/dev/null
# 工作区磁盘余量
df -h /mnt/user-data 2>/dev/null
# 工程目录 core dump 与体积堆积（core 是 2026-10-13 打包阶段 OOM 根因：3×~500MB 把 bundle 内存推过 3G cgroup）
find . -maxdepth 2 -type f -name 'core*' -size +50M -delete
du -sh .
```

内存用量 >70% 或磁盘 <10G：先清理再开工（`rm -rf node_modules/.cache`、删除已合并完的 part 中间文件）。本机 macOS 无 cgroup 路径，命令带 `2>/dev/null` 容错——读不到不阻塞，跳过即可。

#### 短片（≤8 分钟）

单命令整片渲染（沙箱内 `--concurrency=1`；本机 macOS 资源充足可用 2）。渲染走「后台执行 + 轮询」形态：

```bash
# 沙箱内一律 --concurrency=1（内存受限）；本机 macOS 资源充足可用 2
cd <项目路径> && mkdir -p /mnt/user-data/outputs && npx remotion render [CompositionId] /mnt/user-data/outputs/[VideoName].mp4 --concurrency=1
```

#### 长片渲染纪律（成片 > 8 分钟必须执行）

超过 8 分钟（8min × 30fps = **14,400 帧**）的成片**禁止单命令整片渲染**——沙箱内存/共享内存档位撑不住，几乎必然 SIGKILL / `Target closed`。必须分段：

1. **定段**：每段 **~5,000-6,000 帧**（沙箱内存档 ≥6G 且 shm ≥2G 落地后可放宽到 ~10,000 帧）。**场景边界优先**：从 `src/tts_timing.json` 累加各场景实测音频帧数推导场景起点帧（场景起点帧 = 前序场景帧数之和，一行 python/jq 可得），优先在场景边界切段，避免接缝落在句子中间；拿不到场景边界时按等长分段兜底。
2. **逐段渲染 + 段校验**：每段完成即确认 part 文件存在且 `ffprobe` 实测段时长与预期一致（防静默截断），失败只重跑失败段：
   ```bash
   npx remotion render [CompositionId] out/parts/part-1.mp4 --frames=0-5400 --concurrency=1
   ffprobe -v error -show_entries format=duration -of csv=p=0 out/parts/part-1.mp4
   ```
3. **合成 + 总时长校验**：全段完成后 ffmpeg concat（**list 用 `$PWD` 绝对路径**，与 gotchas.md 口径一致），总时长与 `tts_timing.json` 合计误差 **<1s** 才移入 outputs：
   ```bash
   printf "file '%s/out/parts/part-1.mp4'\nfile '%s/out/parts/part-2.mp4'\n" "$PWD" "$PWD" > /tmp/concat.txt
   ffmpeg -y -f concat -safe 0 -i /tmp/concat.txt -c copy /mnt/user-data/outputs/[VideoName].mp4
   ```
4. **接缝抽检**：对段边界各抽 1-2 帧确认无黑帧/跳变（场景边界切分时通常无需修复）。

#### 后台执行 + 轮询（>3 分钟命令的标准形态）

渲染一律后台跑、前台只轮询——bash **输出静默超过 300 秒会被沙箱击杀**（`no_change_timeout`），前台长渲染必死：

```bash
# 后台启动：退出码落盘，日志重定向
cd <项目路径> && nohup sh -c 'npx remotion render [CompositionId] out/parts/part-1.mp4 --frames=0-5400 --concurrency=1 > render_part-1.log 2>&1; echo $? > render_part-1.exit' > /dev/null 2>&1 &
echo $! > render.pid

# 轮询：120-240 秒一档（早期/短段 120s，稳态长段最多 240s——硬上限 240s 必须低于 300s 静默击杀线）
sleep 180; tr '\r' '\n' < render_part-1.log | tail -5; echo "exit=$(cat render_part-1.exit 2>/dev/null || echo running)"
```

完成判定 = `.exit` 文件为 `0` **且**产物校验通过（ffprobe 时长）。`.exit` 非零 → 按「渲染失败重试配方」处置。

⚠️ **轮询命令须带微小变化**（反循环自保）：交替 `tail -3`/`tail -5`、交替查看日志与 `.exit` 文件、或附 `echo 第N次` 序号——同一命令在 20 次调用窗口内重复 5 次会触发 harness 反循环硬停，固定模板的轮询命令恰好会撞上。

#### 交付前检查

- [ ] 音频完整性（每场景正常播放）
- [ ] 字幕同步（与语音基本一致；重点抽查第 2 场景起字幕仍在）
- [ ] 画面遮挡（字幕不遮挡重要内容）
- [ ] 布局美观（紧凑不分散）
- [ ] 关键容器边界断言全过

#### 交付前验证（宣称完成的证据链）

向用户宣称"成片已交付"之前，必须先出示以下证据——**没有证据链的完成宣称一律无效**（2026-10-12 事故会话 1 曾伪造"output.mp4 已生成"，本节即为其反制）：

1. `ls -l /mnt/user-data/outputs/<VideoName>.mp4`——成片真实存在且大小合理（MB 级，非 0 字节桩）
2. `ffprobe -v error -show_entries format=duration -of csv=p=0 <成片>`——实测时长与 `src/tts_timing.json` 的 total 帧数换算（÷30fps）同一量级（±10% 内）；对不上 = 渲染截断或 concat 丢段
3. 渲染日志末尾的 `Encoded N/N` 满帧证据（N=N；分段渲染时逐段核对段数与 concat 清单一致）
4. 音色证据（呼应第二步）：生成日志每句 `(speaker=...)` 行
5. 视觉/布局结论证据：必须附像素坐标、边界外行计数、图像 diff 值之一；禁止「看起来正常/好像被裁」这类无量化判据的结论。工具只返回读取成功而不返回图像内容时，不得声称自己已经看到（2026-10-13 幻觉实例反制）

**文件写入规则**：落盘文件（组件代码/配置/清单等）一律用 `write_file` 工具——沙箱官方写文件通道，自动建目录、不经 bash 审计分类器；**禁用 `cat > file <<'EOF'` 式 heredoc 写文件**（2026-10-12 10:23 超长 heredoc 撞沙箱审计高危模式被 block 的实证）。`python3 - <<'PYEOF'` 这类**不落盘的内联执行**不受影响（本文件既有示例即此形态）。

### 第六步：视频优化（迭代）

当用户要求优化已生成的视频时，遵循以下流程：

#### 原则：减少工具调用次数

每次 read_file → str_replace → bash 验证 循环消耗大量上下文。优化时应**批量操作**：

1. **先规划，后编辑**：列出所有需要修改的场景和具体改动，一次性执行
2. **合并同类修改**：相同类型的修改（如字号调整、卡片宽度）用 `sed` 批量替换，不要逐场景 str_replace
   ```bash
   # 批量替换字号（比逐场景 str_replace 高效得多）
   # 沙箱 Linux 用 sed -i 's/.../.../g'；macOS 本机才需要 sed -i ''（空备份后缀）
   cd <项目路径> && sed -i 's/fontSize: 15/fontSize: 18/g' src/[VideoName]Video.tsx
   ```
3. **最后统一验证**：所有修改完成后，运行一次 esbuild 验证 + 一次 render

#### 优化流程

1. **理解需求**：明确用户要优化什么（布局、字幕、配色、动画等）
2. **读取当前代码**：一次性读取完整视频组件（不要反复读取）
3. **制定修改计划**：列出所有需要修改的位置
4. **批量执行修改**：
   - 纯数值替换：优先用 `sed` 命令批量处理——**批量数值修改后必须全场景 still 复检溢出**（第四步静帧预览门同一套命令；全局替换易在个别场景造成溢出）
   - 复杂逻辑修改：用 `str_replace`，但每次处理多个修改点
5. **验证（静帧回路）**：esbuild 验证 → 修复错误 → **受影响场景 still 抽帧 + view_image 核对**（第四步命令与六条检查项；sed 批量修改则全场景复检）→ 静帧全过 → 一次全片渲染交付
6. **报告结果**：告知用户修改了什么，输出视频路径

#### 循环警告后的交付纪律（2026-10-14 加固批）

收到 `loop_warning`（重复调用/频率告警）**不等于可以口头收尾**：

1. **先保存阶段成果**：警告文案明确允许保存——把已完成的工程文件（组件代码、props、tts_timing.json、已渲染分段）用 write_file 落盘；被 read-before-write 拦截时先读目标文件再重试一次。
2. **证据链照常走完**：文件存在、ffprobe 时长、静帧抽检（第四步六条检查项）一项不能少——**不得以"总结已完成的工作"代替交付**；确无可用保存路径或重试失败时，在最终答复中如实说明哪些文件未保存。
3. **停止点名工具，不是停止全部工具**：频率警告只禁用被点名的循环工具，保存/写文件类工具照常可用。
4. **恢复续作复用已有工程**：中断后续作（换模型继续、新会话、或同会话再试一次）从现有工程断点继续，**不重做已有素材**；先跑「第零步：断点续作盘点」。

#### 常见优化项

| 优化类型 | 方法 | 工具 |
|----------|------|------|
| 字号偏小 | `sed` 批量替换 | bash |
| 卡片/布局偏小 | 按场景 str_replace 调整宽度 | str_replace |
| 字幕遮挡内容 | 调整 paddingBottom 或内容区域 maxH | str_replace |
| 配色调整 | 修改 COLORS 常量 | str_replace |
| 动画太快/太慢 | 调整 interpolate 参数或 spring config | str_replace |
| 场景内容调整 | 修改具体场景组件的 JSX | str_replace |
| 画面单调 | 回第一步分镜表补视觉事件/换版式（**不是**改字号配色——样式微调救不了缺视觉事件的场景） | 分镜表迭代 + str_replace |

## Remotion 最佳实践

编写 Remotion 代码时，查阅官方规则（vendor 在仓库 `skills/public/remotion-official/`，沙箱内路径 `/mnt/skills/public/remotion-official/`，版本与 Remotion 同步）：

- `remotion-markup/` — 写码规则全集（动画、转场、时序、字幕、音频、字体、图表等主题）
- `remotion-captions/` — 词级字幕（whisper.cpp 对齐、Caption JSON、逐词高亮）
- `remotion-create/` / `remotion-render/` — 项目创建与渲染参数
- `remotion-docs/` — Remotion 文档速查

重点篇章：`calculate-metadata.md`（时长音频驱动）、`transitions.md`（TransitionSeries 真转场）、`multi-scene-video.md`（多场景编排）、`silence-detection.md`（静音检测）、`effects.md`（视觉特效）。

## 关键资源

| 文件 | 用途 |
|------|------|
| `scripts/generate_audio_template.py` | TTS 音频生成脚本模板（doubao-only；**复制时必须连同 `_doubao_tts.py`**） |
| `scripts/_doubao_tts.py` | 字节豆包 TTS 合成（`synthesize(text, speaker, speed_ratio)`；默认 speaker 读 `DOUBAO_TTS_DEFAULT_SPEAKER` env；需 `VOLCENGINE_VOICE_ACCESS_KEY`）。沙箱内 TTS 只用它。 |
| `references/VideoTemplate.tsx` | Remotion 视频组件模板 |
| `references/gotchas.md` | 踩坑经验与故障排除（渲染 SIGKILL / `Target closed` / 分段渲染 concat 口径） |
| `skills/public/remotion-official/` | Remotion 官方规则（vendor 副本，沙箱内 `/mnt/skills/public/remotion-official/`） |

长片任务另见本文件内部小节：第五步「长片渲染纪律」「后台执行 + 轮询」「渲染前资源自检」与「故障排除 → 渲染失败重试配方」。

## 故障排除

遇到问题时查阅 [references/gotchas.md](references/gotchas.md)，包含：
- 字幕同步问题与修复
- Chrome headless shell 缺失
- 渲染 SIGKILL（内存不足）
- `Target closed`（Chrome 渲染进程死亡）
- 布局原则与字幕遮挡修复

### 渲染失败重试配方

⚠️ **禁止原样重跑**：同一命令重复调用达 5 次即触发 harness 反循环硬停（失败重试豁免帽只有 2 次/命令）——每次重试**至少改一个参数**，按降级序执行（降并发 → 段长减半 → 清理后重试）：

| 症状 | 处置 |
|------|------|
| 打包阶段（非渲染阶段）137/SIGKILL | 先查 core 文件与大文件堆积（第五步 `find ... core*` + `du -sh .`），确认工程体积正常后再按既有降级序处置 |
| `Exec failed, signal SIGKILL` / 退出码 137 | cgroup 内存击杀：改 `--concurrency=1`；仍失败 → 段长减半重切分 |
| `Target closed` | Chrome 渲染进程死亡：确认已走「长片渲染纪律」分段；`pkill -f chrome-headless` 清残留后清缓存重试 |
| `Cannot allocate memory` | 内存耗尽：`rm -rf node_modules/.cache` + 降并发 + 缩短段长 |
| 命令静默消失（无报错、无输出） | 前台踩了 300s 静默超时：转「后台执行 + 轮询」形态重跑同一任务 |

清理动作：`rm -rf node_modules/.cache`、`pkill -f chrome-headless`；重试前用第五步「渲染前资源自检」命令读 cgroup 内存实际值确认档位（勿凭记忆写死）。
