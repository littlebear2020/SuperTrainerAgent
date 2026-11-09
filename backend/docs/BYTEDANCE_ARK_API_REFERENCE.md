# 字节火山方舟 Ark API 参考文档

> 基于 Volcengine 官方文档整理（Doubao Seedance 2.0 系列 + Doubao Seedream 4.0–5.0），用于 TrainerAI 项目字节模型接入与 ViMax 引擎对接参考。
> 编写日期：2026-07-12 · 来源：`docs.volcengine.com/docs/82379/1824121`（Seedream）、`/2291680`（Seedance 2.0 系列教程）、`/2298881`（视频生成通用教程）

---

## 0. 概览

### 0.1 模型 ID 速查

**图像生成（Seedream）** — 端点 `POST /images/generations`

| 模型 | Model ID | 分辨率档位 | 备注 |
|---|---|---|---|
| Seedream 5.0 Pro | `doubao-seedream-5-0-pro-260628` | 1K, 2K | 高精度；支持交互编辑(坐标/标记)；**不支持组图/联网搜索/流式**；参考图≤10 |
| Seedream 5.0 Lite | `doubao-seedream-5-0-260128`（也接受 `doubao-seedream-5-0-lite-260128`） | 2K, 3K, 4K | 支持组图/联网搜索/流式；参考图≤14 |
| Seedream 4.5 | `doubao-seedream-4-5-251128` | 2K, 4K | 仅 jpeg；参考图≤14 |
| Seedream 4.0 | `doubao-seedream-4-0-250828` | 1K, 2K, 4K | 支持提示词优化 fast 模式；参考图≤14 |

**视频生成（Seedance）** — 端点 `POST /contents/generations/tasks`（异步 + 轮询）

| 模型 | Model ID | 时长 | 备注 |
|---|---|---|---|
| Seedance 2.0 | `doubao-seedance-2-0-260128` | 4–15s | 最高品质；支持 4k(10bit H.265)/1080p/720p/480p；支持多模态参考/编辑/延长/联网搜索 |
| Seedance 2.0 Fast | `doubao-seedance-2-0-fast-260128` | 4–15s | 兼顾成本速度；仅 480p/720p |
| Seedance 2.0 Mini | `doubao-seedance-2-0-mini-260615` | 4–15s | 最低成本；仅 480p/720p |
| Seedance 1.5 pro | `doubao-seedance-1-5-pro-251215` | 4–12s | 支持 draft 样片模式、离线推理 flex |
| Seedance 1.0 pro | `doubao-seedance-1-0-pro-250528` | 2–12s | — |
| Seedance 1.0 pro fast | `doubao-seedance-1-0-pro-fast-251015` | 2–12s | — |

> TrainerAI BYTEDANCE 计划锁定三个：Chat `doubao-seed-2-1-pro-260628`、生图 `doubao-seedream-5-0-pro-260628`、视频 `doubao-seedance-2-0-260128`。

### 0.2 鉴权与端点

- **base_url**：`https://ark.cn-beijing.volces.com/api/v3`
- **鉴权**：`Authorization: Bearer $ARK_API_KEY`（API Key 在控制台 `ark/region:ark+cn-beijing/apikey` 创建）
- **开通前置**：账户余额 ≥ 200 元，或已购 Seedance 2.0 资源包
- **产物保存**：视频/图片 URL 仅保留 **24 小时**，需及时下载或转存 TOS（推荐 TOS 数据订阅自动转存）
- **任务记录**：保留 7 天，查询区间 [T-7天, T)

---

## 1. Seedream 图像生成 API

### 1.1 端点

`POST https://ark.cn-beijing.volces.com/api/v3/images/generations`

### 1.2 请求体（全字段）

```jsonc
{
  "model": "doubao-seedream-5-0-pro-260628",
  "prompt": "<文本提示词，≤300 汉字 / 600 英文单词>",
  "image": "<url 或 b64 或 [url列表]>",          // 参考图，可选；见 1.3
  "size": "2K",                                   // 见 1.4（像素值 或 档位）
  "output_format": "png",                        // png | jpeg（5.0 Pro/Lite 可选；4.5/4.0 固定 jpeg）
  "response_format": "url",                      // url | b64_json
  "watermark": false,                            // 右下角"AI生成"水印
  "sequential_image_generation": "disabled",     // "auto"=组图输出 | "disabled"=单图
  "sequential_image_generation_options": {"max_images": 4},  // 仅 auto 时
  "stream": false,                               // 流式（5.0 Pro 不支持）
  "tools": [{"type": "web_search"}],              // 联网搜索（5.0 Pro 不支持；仅 Lite）
  "optimize_prompt_options": {"mode": "standard"} // standard | fast（仅 4.0 支持 fast）
}
```

返回：`{"data":[{"url":"<TOS 签名 URL>","size":"1024x1024"}], "usage":{...}}`

### 1.3 参考图（`image` 字段）—— 两种形态

- **单图**：`"image": "<url 或 data:image/xxx;base64,xxx>"`（图文生图/单图编辑）
- **多图**：`"image": ["url1", "url2", ...]`（多图融合，Pro≤10 张 / Lite·4.5·4.0≤14 张）

> 图片输入方式：URL / Base64（`data:image/<格式>;base64,<b64>`，格式小写）/ 素材 ID（`asset://...`，见 §3）。
> 图片限制：格式 jpeg/png/webp/bmp/tiff/gif/heic/heif；宽高比 [1/16, 16]；宽高 > 14px；单张 < 30MB；总像素 ≤ 6000×6000；请求体 ≤ 64MB（大文件勿用 base64）。

### 1.4 输出尺寸（`size`）

两种方式，不可混用：
- **像素值**：`"2048x2048"`（宽x高）。Pro 范围 [1280×720, 约 2048×2048×1.1]；Lite/4.5 [2560×1440, 4096×4096]；4.0 [1280×720, 4096×4096]。
- **档位**：`"1K"|"2K"|"3K"|"4K"`，模型按宽高比自动映射像素（详见官方表）。

### 1.5 各模型能力差异（速查）

| 能力 | 5.0 Pro | 5.0 Lite | 4.5 | 4.0 |
|---|---|---|---|---|
| 文生图 | ✅ | ✅ | ✅ | ✅ |
| 单/多图生图 | ✅ | ✅ | ✅ | ✅ |
| 文生组图/图生组图 | ❌ | ✅ | ✅ | ✅ |
| 流式输出 | ❌ | ✅ | ✅ | ✅ |
| 联网搜索 | ❌ | ✅ | ❌ | ❌ |
| 交互编辑(坐标/标记) | ✅ Pro 独有 | ❌ | ❌ | ❌ |
| 提示词优化 fast 模式 | ❌(仅 standard) | ❌(仅 standard) | ❌(仅 standard) | ✅ |
| 参考图上限 | 10 | 14 | 14 | 14 |

### 1.6 交互编辑（仅 5.0 Pro）

在参考图上叠加坐标点/涂鸦/草图 + 文本，精确指定编辑位置。请求体与单图生图相同（`image` 单图 + `prompt` 描述标记对应关系）。

---

## 2. Seedance 视频生成 API

### 2.1 端点与异步机制

- 提交：`POST https://ark.cn-beijing.volces.com/api/v3/contents/generations/tasks` → 返回 `{"id":"cgt-..."}`
- 轮询：`GET https://ark.cn-beijing.volces.com/api/v3/contents/generations/tasks/{id}` → `status: queued|running|succeeded|failed|expired`
- 成功后取 `content.video_url`（mp4）
- 可选 Webhook：请求体加 `callback_url`，状态变化时 POST 通知
- 任务列表：`GET /contents/generations/tasks?page_size=N&filter.status=succeeded`
- 删除/取消：`DELETE /contents/generations/tasks/{id}`

### 2.2 content 数组与 role（核心）

`content` 是一个数组，元素类型由 `type` 决定，**用 `role` 标记用途**：

| type | 字段 | role 取值 | 说明 |
|---|---|---|---|
| `text` | `text` | — | 提示词（必填） |
| `image_url` | `image_url:{url}` | `first_frame` | 首帧图生视频（1 张） |
| `image_url` | `image_url:{url}` | `last_frame` | 尾帧（与 first_frame 共用，2 张首尾帧） |
| `image_url` | `image_url:{url}` | `reference_image` | 多模态参考图（1–9 张，继承角色/风格/构图） |
| `video_url` | `video_url:{url}` | `reference_video` | 参考视频（0–3 个，运镜/动作/风格） |
| `audio_url` | `audio_url:{url}` | `reference_audio` | 参考音频（0–3 个，音色/旋律/对话） |
| `draft_task` | `draft_task:{id}` | — | 基于 draft 视频生成正式视频（仅 1.5 pro） |

> 模态可任意组合，但**不支持"文本+音频"或"纯音频"**。图片 0–9 张 / 视频 0–3 个 / 音频 0–3 个。
> 图片输入：URL / Base64 / `asset://` 素材 ID。图片宽高比 [0.4, 2.5]，宽高 [300, 6000]px，单张 < 30MB。
> 视频输入：mp4/mov，480p–4k，时长 [2,15]s，总时长 ≤15s，单个 < 200MB，帧率 [24,60]。
> **图片裁剪**：当上传图片宽高比与目标 ratio 不一致时，方舟居中裁剪。建议 ratio 与图片宽高比接近；或设 `ratio:"adaptive"`。

### 2.3 参数（新方式 — request body，强校验，官方推荐）

```jsonc
{
  "model": "doubao-seedance-2-0-260128",
  "content": [...],
  "resolution": "720p",          // 480p|720p|1080p|4k（4k 仅 2.0；1080p 不支持 Fast/Mini）
  "ratio": "16:9",               // 21:9|16:9|4:3|1:1|3:4|9:16|adaptive
  "duration": 5,                 // 整数秒，2.0 系列 [4,15]，可设 -1 智能选时长
  "seed": 11,                    // 复现
  "camera_fixed": false,         // 镜头是否固定
  "watermark": true,             // 右下角"AI生成"水印
  "generate_audio": true,        // 生成有声视频（2.0/1.5 pro 支持）
  "return_last_frame": true,     // 返回尾帧图（用于续接下一片段）
  "service_tier": "default",     // default(在线) | flex(离线推理，50% 价格，2.0 不支持)
  "execution_expires_after": 172800,  // 任务最长执行秒数
  "draft": true,                 // 样片模式（仅 1.5 pro，仅 480p，低成本预览）
  "tools": [{"type": "web_search"}],   // 联网搜索（仅纯文本输入；2.0 支持）
  "callback_url": "https://..."   // Webhook 回调
}
```

轮询成功返回示例：
```json
{"id":"cgt-...","model":"doubao-seedance-2-0-260128","status":"succeeded",
 "content":{"video_url":"https://ark-content-generation-cn-beijing.tos-cn-beijing.volces.com/..."},
 "usage":{"completion_tokens":108900,"total_tokens":108900},
 "resolution":"720p","ratio":"16:9","duration":5,"framespersecond":24,
 "service_tier":"default","execution_expires_after":172800}
```

### 2.4 参数（旧方式 — prompt 后缀，弱校验，不推荐）

在 `content[0].text` 末尾追加：
```
<prompt> --rs 720p --rt 16:9 --dur 5 --fps 16 --wm false --seed -1 --cf false
```
（`--rs`=resolution, `--rt`=ratio, `--dur`=duration, `--fps`, `--wm`=watermark, `--seed`, `--cf`=camera_fixed）
> 参数填错会被忽略或报错。**新项目优先用 §2.3 request body 方式**。

### 2.5 各模型能力差异（速查）

| 能力 | 2.0 | 2.0 Fast | 2.0 Mini | 1.5 pro | 1.0 pro | 1.0 pro fast |
|---|---|---|---|---|---|---|
| 文生视频 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| 图生视频-首帧 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| 图生视频-首尾帧 | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ |
| 多模态参考(图/视频/音频) | ✅ | ✅ | ✅ | ❌图 | ❌图 | ❌图 |
| 编辑视频 | ✅ | ✅ | ✅ | — | — | — |
| 延长视频 | ✅ | ✅ | ✅ | — | — | — |
| 有声视频 generate_audio | ✅ | ✅ | ✅ | ✅ | — | — |
| 联网搜索 | ✅ | ✅ | ✅ | ❌ | ❌ | ❌ |
| 样片 draft | ❌ | ❌ | ❌ | ✅ | ❌ | ❌ |
| 返回尾帧 | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| 离线推理 flex | ❌ | ❌ | ❌ | ✅ | ✅ | ✅ |
| 4k | ✅(10bit) | ❌ | ❌ | ❌ | ❌ | ❌ |
| 1080p | ✅ | ❌ | ❌ | ✅ | ✅ | ✅ |
| 时长范围 | 4–15 | 4–15 | 4–15 | 4–12 | 2–12 | 2–12 |
| 帧率 | 24 | 24 | 24 | 24 | 24 | 24 |

### 2.6 限流（在线推理 default）

| | 2.0（非4k） | 2.0（4k） | Fast/Mini | 1.5/1.0 |
|---|---|---|---|---|
| 最大 RPM（企业/个人） | 600/180 | 15/15 | 600/180 | 600 |
| 最大并发（企业/个人） | 10/3 | 1/1 | 10/3 | 10 |

> 离线推理 flex：按 TPD 限流（1.5/1.0：5000 亿 token/天）。

---

## 3. 关键限制：人脸审查与便利创作（⚠️ 最重要）

### 3.1 Seedance 2.0 不支持直接上传含真人人脸的参考图/视频

> **官方原文**：「Seedance 2.0 系列模型不支持直接上传含有真人人脸的参考图/视频。」

这意味着：**直接上传真实人物照片做 seedance i2v/r2v 输入，会被字节安全审核拦截**（不是 360 网关独有，字节原生就拦）。

### 3.2 信任模型产物机制（关键豁免）

平台信任**本账号下近 30 天内**以下模型生成的含人脸产物，可作 Seedance 2.0 输入素材二次创作，**不触发输入审核拦截**：

| 信任产物 | 生效时间 | 有效期 |
|---|---|---|
| Seedance 2.0 系列生成的含人脸视频 | 2026-03-11 起 | 30 天 |
| Seedance 2.0 含人脸视频对应的尾帧图片 | 2026-04-16 起 | 30 天 |
| **Seedream 5.0 Lite 文生图得到的含人脸图片** | 2026-04-16 起 | 30 天 |

> ⚠️ 限制：仅方舟平台产物（不跨平台）、仅同账号、仅原始产物（剪辑/超期失效）、压缩转发易失效（建议转存 TOS）。信任仅对**输入审核**生效，**输出仍可能命中安全审核失败**。

> ✅ **2026-07-12 补充实测（TrainerAI 项目方确认）**：官方信任产物表只列 Seedream **5.0 Lite** 文生图含人脸产物，**未显式列 5.0 Pro**。经项目方实测确认：**Seedream 5.0 Pro 生成的角色图同样不会被 seedance 拦截**，作 seedance i2v/r2v 输入安全。→ ViMax 角色首帧可直接用 5.0 Pro（一致性优于 Lite），无需退 Lite；后续官方若调整信任范围需回归。此前 `BYTEDANCE_MODELS_INTEGRATION_PLAN.md` 阶段 4.2 风险表「5.0 Pro 须实测否则用 Lite」已据此定调为「Pro 可用」。

> **对 TrainerAI 测试结论的修正**：之前测试「Ark 直连 seedance i2v 对人物图 succeeded」是因为用的是 **Seedream 生成的 AI 人物图**（属于信任产物）。若直接上传**真实人物照片**，Ark 直连也会被拦。memory `video-gen-seedance-ark-i2v-no-human-block` 需以此为准修正。

### 3.3 预置虚拟人像（写实风格肖像）

写实风格视频可用平台预置虚拟人像库，在 `content.image_url.url` 传入 `asset://<asset ID>` 即可。提示词中仍需用"**图片N**"指代（不能用 Asset ID 指代）。

### 3.4 已授权真人素材

真人认证 + 本人授权后，素材上传方舟获 `asset://<asset ID>`，同 3.3 方式使用。

---

## 4. 提示词技巧

### 4.1 基础公式（按任务类型）

- **多模态参考**（提取元素生成新视频）：`参考<图片N>的<主体>，生成...` / `参考<视频N>的<动作/运镜/风格/音效>，生成...`
- **编辑视频**（局部修改，未提及默认不变）：`严格编辑<视频N>，将<原特征>修改为<新特征>`（注意：编辑/延长任务用"视频N"指代，勿用"参考视频N"，以免被判为参考任务）
- **延长视频**：`向前/向后延长<视频N>，生成...` 或轨道补全 `<视频1> + <过渡描述> + 接 <视频2>`
  - 延长：2.0 最多 3 段视频输入，总时长 ≤ 15s，自动截取衔接部分

### 4.2 进阶公式

```
精准主体 + 动作细节 + 场景环境 + 光影色调 + 镜头运镜 + 视觉风格 + 画质 + 约束条件
```
模型内部"空间层（有什么）+ 时间层（怎么变化）"解耦 → 提示词应是"工程型指令"而非"文案型形容"。

### 4.3 主体定义（防 ID 漂移）

- `将<图片N>中的[2-3 个稳定静态特征]定义为<主体N>`，后续持续用同一标签指代
- 多主体：分别定义 + 标签区分，如"将视频1的高个子男人定义为警察，另一个矮个子男人定义为小偷"
- 多素材同主体：`将图片1的[...]、图片2的[...]定义为<主体N>`
- 简单场景可省略定义，每次用 `<主体N>@<图片N>` 绑定

### 4.4 分镜时序

`镜头1 / 镜头2 / 镜头3` 按事件顺序组织，每镜头：运镜切换 → 主体动作表情 → 位置空间变化 → 音频。**避免精确时间（如 0-3 秒），模型支持不稳定**。

### 4.5 动作描述

- 肢体细化 + 程度量化（缓慢抬手、快速转头、用力蹬地）
- **优先低缓连续小动作**，规避狂奔/大跳/剧烈翻滚
- 写明动作过渡衔接
- 情绪外化为身体细节（"悲伤"→低头、肩膀颤抖、眼眶泛红），勿用抽象情绪词

### 4.6 运镜

直接用标准术语：中景/特写/全景/缓慢推镜/平稳横移/固定镜头。**一个镜头只指定 1 种运镜**，不要同时推拉摇移。

### 4.7 画质 / 风格 / 约束词

- 画质：高清、细节丰富、电影质感、色彩自然、光影柔和
- 风格：赛博朋克冷蓝紫、复古胶片、日系清新（动漫风格务必加风格约束，否则易漂移成写实）
- 约束词：`保持无字幕` / `避免生成任何文字或字幕` / `不要生成Logo` / `不要生成水印`

### 4.8 特殊字符规范

| 信息类型 | 符号 | 示例 |
|---|---|---|
| 音乐 | `（）` | （背景中播放着快节奏的摇滚乐） |
| 音效 | `< >` | <远处传来狗叫声> |
| 台词 | `{ }` | {你好，世界}（小语种需标语种：用日语说道{こんにちは}） |
| 字幕 | `【 】` | 【第一章：启程】 |

---

## 5. 常见问题与解决方案

| 问题 | 根因 | 解决 |
|---|---|---|
| **人物 ID 漂移**（换脸/撞明星被拦） | 参考图混用、人脸占比过小 | 额外准备**头部人脸特写图（大头照，仅面部）**；prompt"主体1面部特征参考图片1(大头照)，妆造参考图片2(全身照)"；重要素材前置；**不用多视图**（多视图致双胞胎） |
| 视频含字幕 | — | prompt 加"保持无字幕"；先去文字再生成；优先横屏 |
| 视频含 Logo/水印 | — | prompt 加"不要生成水印/不要生成Logo" |
| 风格漂移（写实漂成真人/动漫漂成写实） | 参考图风格与目标不符且未强调 | prompt 加风格约束词（"2D日漫风格"/"3D国风漫画"）；先把参考图转目标风格 |
| 延长视频衔接处跳变 | — | 剪映补：前段末尾删 6 帧 + 后段开头删 1 帧；续写以转场切镜结尾 |
| **双胞胎**（多人物+三视图） | 主体定义不清 + 多视图混淆 | 清晰定义每人+对应图；加全局约束"禁止外形完全一致的人物/分身/双胞胎"；单人独立照片，不用三视图 |
| 延长画质劣化（多次续写叠加，人脸斑驳） | — | 先把原始视频转**白模视频**（"将视频转为白色3D模型..."）再续写；优先高清图；控制续写次数 |
| 特效不符预期 | 文字描述特效不稳 | 用**参考视频**定义特效形态 |
| **参考人物 > 4 人** | 超过 4 人输出不稳（少人/多人/重复） | 分组生成图（每组≤4人），再用分组图生视频 |
| 视频结尾噪音 | 旁白片尾截断杂音 | 重新生成 或 剪映音量包络线淡出 |
| 中文发音不准 | 多音字/生僻字/形近字 | 换发音一致的常用同音字（"螭龙山"→"吃龙山"） |
| 音色参考不准 | 参考音频音色偏差 | prompt 补细致音色特征描述；台词风格与参考音频语气相近 |
| 首尾帧画面跳变（拉伸压缩） | 输入图与输出 ratio 不一致 | 裁剪输入图到目标像素；或设 `ratio:"adaptive"` |

---

## 6. ViMax 接入对照（TrainerAI 视角）

ViMax-main 原始 generator（`/Users/littlebear/Documents/ViMax-main/tools/`——ViMax-main 为历史外部快照，现已 vendored 至 `backend/packages/vimax/`）走 yunwu 中转，**API 形态与 Ark 直连一致**，可直接平移到 Ark 直连端点：

### 6.1 图像生成对照

| 项 | ViMax-main `image_generator_doubao_seedream_yunwu_api.py` | Ark 直连 |
|---|---|---|
| 端点 | `https://yunwu.ai/v1/images/generations` | `https://ark.cn-beijing.volces.com/api/v3/images/generations` |
| model | `doubao-seedream-4-0-250828`（旧） | `doubao-seedream-5-0-pro-260628`（TrainerAI 计划） |
| 参考图字段 | `payload["image"] = [b64_list]`（image_path_to_b64） | `image: [url 或 b64 list]`（官方两种形态都支持） |
| 其他字段 | `prompt`, `sequential_image_generation:"disabled"`, `response_format:"url"`, `size:"1024x1024"` | 完全一致 + `output_format`/`watermark` |
| 鉴权 | `Bearer yunwu_key` | `Bearer $ARK_API_KEY` |

> **结论**：seedream 的 `image` 字段（string 单图 / list 多图）是 Ark 原生格式，**不是 360 网关 NanoBanana 的 `/images/edits` 概念**。ViMax 一致性靠的是 `image` 多参考图 + ReferenceImageSelector 文本元素映射，不是"参考图编辑"端点。

### 6.2 视频生成对照

| 项 | ViMax-main `video_generator_doubao_seedance_yunwu_api.py` | Ark 直连 |
|---|---|---|
| 端点 | `https://yunwu.ai/volc/v1/contents/generations/tasks` | `https://ark.cn-beijing.volces.com/api/v3/contents/generations/tasks` |
| 模型选择 | 按参考图数：0→`t2v`、1→`ff2v=i2v`、2→`flf2v=i2v`（1.0 lite 模型） | Seedance 2.0 单模型统一处理 t2v/首帧/首尾帧，靠 `role` 区分 |
| content image | `{"type":"image_url","image_url":{"url":b64},"role":"first_frame"}` / `"last_frame"` | 完全一致（`first_frame`/`last_frame` 是 Ark 官方原生 role） |
| 参数 | prompt 后缀 `--rs --rt --dur --fps --wm --seed --cf`（旧方式） | 推荐 request body `resolution`/`ratio`/`duration`/`watermark`/`seed`/`camera_fixed`（新方式，强校验） |
| 轮询 | `GET .../tasks/{id}` → `status:succeeded` → `content.video_url` | 完全一致 |

> **结论**：ViMax 的 `role: first_frame/last_frame` 就是 Ark 官方原生格式（**不是 360 的 `extra_body.role=reference`**）。TrainerAI 现有 `seedance.py`（360 网关）用 `extra_body.role="reference"` 是 360 的变体；Ark 直连应改用 content 内 `role: first_frame/last_frame`。

### 6.3 参考图数量（ViMax 两阶段，不矛盾）

- **图像生成阶段**：`ReferenceImageSelector`（LLM）从角色肖像(正/侧/背) + 前序帧 + 转场帧里选 **≤8 张** → 喂 seedream `image:[list]` 生成该镜头首/尾帧。Ark 官方：Seedream Pro ≤10 张、首尾帧 seedance ≤2 张。
- **视频生成阶段**：只消费该镜头自己生成的 **1–2 张首/尾帧** → 喂 seedance `first_frame`/`last_frame`。**不喂 selector 选的那 8 张**。
- 两阶段输入集不同，无矛盾。

### 6.4 ViMax 一致性机制（整体）

```
script → CharacterExtractor → CharacterPortraitsGenerator(生成 front/side/back 肖像, 复用全片)
       → StoryboardArtist(分镜+decompose ff/lf/motion)
       → CameraImageGenerator(机位树)
       → per camera: ReferenceImageSelector 选≤8 参考图 + text_prompt 元素映射
                   → image_gen.generate_single_image(refs=≤8) → first/last_frame.png
       → per shot: video_gen.generate_single_video(refs=1-2 own frames) → video.mp4
       → concatenate
```
一致性靠：①角色肖像一次生成全片复用 ②ReferenceImageSelector 选参考 + 文本指代元素 ③MLLM/VLM 并行选最佳首帧。

---

## 7. SDK

官方 SDK：`pip install 'volcengine-python-sdk[ark]'`，`from volcenginesdkarkruntime import Ark`。

```python
from volcenginesdkarkruntime import Ark
client = Ark(base_url="https://ark.cn-beijing.volces.com/api/v3",
             api_key=os.environ.get("ARK_API_KEY"))
# 图像
resp = client.images.generate(model="doubao-seedream-5-0-pro-260628", prompt="...", image=[...], size="2K")
# 视频
t = client.content_generation.tasks.create(model="doubao-seedance-2-0-260128", content=[...], ratio="16:9", duration=5)
r = client.content_generation.tasks.get(task_id=t.id)  # 轮询
```

也兼容 OpenAI SDK：`from openai import OpenAI; client=OpenAI(base_url=".../api/v3", api_key=...)`，图片用 `client.images.generate(..., extra_body={"image":[...],"watermark":False})`。

> TrainerAI 现状：`backend/.venv` 已装 httpx，探针脚本用裸 httpx 调用（见 `backend/scripts/test_ark_*.py`）。生产接入可用 SDK 或继续 httpx。

---

## 8. 接入注意点（对 TrainerAI 现状的修正）

1. **人脸审查**：字节 Ark 直连 seedance **同样拦真实人物照片**（§3.1），不是 360 独有。ViMax 真人角色场景须用：① Seedream 生成的人物图作输入（信任产物，§3.2）② `asset://` 虚拟人像 ③ 已授权真人素材。直接传真实照片会被字节安全审核拦。
2. **调用格式修正**：原 BYTEDANCE 计划部分基于 360 网关概念（`/images/edits`、`extra_body.role=reference`）。Ark 直连正确格式：seedream 用 `image:[list]`（§1.3）；seedance 用 content 内 `role:first_frame/last_frame/reference_image`（§2.2）+ request body 参数（§2.3，非 prompt 后缀）。
3. **ViMax 可平移**：ViMax-main 的 seedream/seedance generator 代码（yunwu 中转）API 形态与 Ark 直连一致，`provider_bridge.py` 接入时把 base_url 换 `ark.cn-beijing.volces.com/api/v3`、key 换 `ARK_API_KEY`、model 换 5.0 Pro / 2.0 即可。
4. **Seedream 5.0 Pro 限制**：不支持组图/联网搜索/流式（ViMax 单图生成不受影响）。若需组图（多分镜一次生成）用 5.0 Lite。
5. **产物 URL 仅 24h**：ViMax 长流程须及时下载/转存 TOS，否则中间帧失效。
6. **限流**：个人用户并发仅 3（非4k），ViMax 并行镜头生成需控并发或升级企业账号。
