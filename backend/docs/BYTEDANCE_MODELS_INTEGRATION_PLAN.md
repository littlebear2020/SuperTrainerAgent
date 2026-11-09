# 字节火山方舟模型接入实施计划（v3 · 二次评审优化版）

> **目标**：在保留现有 360 智脑（qihu）能力前提下，接入字节火山方舟（Volcengine Ark / 豆包 Doubao）模型，使字节成为**默认首选**、360 作为**自动降级备用**；Chat 模型支持**用户前端自由选择**字节或 360。
>
> **动机**：360 网关接口不稳定、生成效果不佳；直连字节更稳定、效果更好。
>
> **v2 修订说明**：v1 经逐行代码核实（5 路并行核查 + 关键断言复核），发现并修正以下问题——
> - **Chat**：doubao 在 `config.yaml` 已走 360 网关（非直连）；`api_base`/`base_url` 字段随 provider 不同；🔴**实测修正**：360 网关版 vision 不工作但 **Ark 直连版 vision 可用**（2026-07-10 实测），Ark 条目可设 `supports_vision:true`。
> - **TTS**：v1 漏掉 `DEFAULT_VOICES["zh"]` 覆盖（致中文切换静默失效）、`CURATED_VOICES`/`build_speaker_voice_map`（ViMax 对话音色割裂）、5 个直调 edge_tts 的 skill、前端/路由/voice_twin preset 目录、并发判断、两处兜底 `preset:146`；且新 client 应抄 `volcengine_client.py` 而非 `qihu_tts_client.py`。
> - **生图**：v1"全走 360"错（dashscope 走阿里云）；`FallbackAIImageProvider` 是死代码需真正装配；ViMax 硬编码 NanoBanana，**不受 `ai_image_provider` 影响**（v1 回归担忧多余）。
> - **生视频**：seedance 拦人像仅在 r2v/i2v（图片输入）有记录，t2v 未验证；视频 fallback 仅 kling 生效；内容审查 `RuntimeError` 绕过 fallback。
> - **播客**：client 放后端 `app/` 则 skill 沙箱调不到（不能 import `app.*`）。
> - **ASR**：v1"临时托管"描述过时（base64 已是主路径）；两个 `transcribe.py` 各自漂移非副本。
> - openspeech 鉴权复用现有 `VoiceCloneConfig.access_key`（已是单头 `X-Api-Key`），无需新 env。
>
> **v3 修订说明**（基于二次评审 + 内容审查处理代码核实）：
> - **新增阶段 0（前置核对）**：把所有"上线前核对"的外部未知项（GLM/DeepSeek model id、TTS/播客 speaker id、ASR endpoint）集中到前置阶段，避免每个阶段卡在同一处。
> - **seedance t2v 人像实测前移到阶段 0**：仅需 API key + 含人物 prompt，不依赖代码；结论决定阶段 5"seedance 作默认"是否成立。
> - **阶段 2 拆为 2a/2b**：2a=后端核心路由+DEFAULT_VOICES+client（中文切换成败关键，先上），2b=5 skill 镜像+前端+voice_twin preset（广度覆盖）。
> - **🔴 内容审查 fallback 语义更正**：v2 曾建议"把内容审查 RuntimeError 转 ProviderUnavailableError 以触发 fallback"——**这是错的**，违反代码库既有哲学（`provider_bridge.py:167-172`"内容审查不可重试，重试浪费计费且不会自愈"；`_FALLBACK_EXC` 刻意排除 RuntimeError）。**正确做法：内容审查失败不做 fallback，转成清晰的用户可见错误；仅基础设施错误（429/5xx/timeout/conn）才降级**。
> - **§0.1 表格 + CLAUDE.md**：§0.1 表格 doubao Chat 标注 **Ark 直连 vision 可用**（实测修正，推翻旧标注）；CLAUDE.md"gpt-image-2 是 ViMax 依赖"过时描述纳入阶段 4 改动清单。
>
> · 编写日期：2026-07-10 · 已核对火山官方文档 + 项目代码（v3 二次核实内容审查处理）

---

## ★ v4 增量说明（2026-07-12，仅针对 ViMax 引擎字节化，非 ViMax 部分保持 v3 原样）

> **增量范围**：本 v4 增量**只新增 ViMax 引擎字节化**（生图改 seedream、视频改 seedance-2.0），**不改 v3 的 Chat/TTS/播客/ASR/通用生图视频方案**（那些已审定，原样保留）。整体目标仍是项目全切字节、360 全备选。
>
> **为什么只增量 ViMax**：v3 §1.3/§2/阶段 4/阶段 5 多处断言"ViMax 零回归 / 不受 `ai_image_provider` 影响"——这是**当前代码事实**（ViMax 硬编码 NanoBanana + happyhorse 独立路由，`provider_bridge.py:109-129 / 202-270`）。但调研 ViMax-main 原始项目（`/Users/littlebear/Documents/ViMax-main`）发现：**原始 ViMax 本就有 `image_generator_doubao_seedream_yunwu_api.py` + `video_generator_doubao_seedance_yunwu_api.py`**（走 yunwu 中转，API 形态与 Ark 直连一致），可平移接入。故新增 ViMax 字节化，原 v3 非 ViMax 内容不动。
>
> **依据**：① ViMax-main 调用链调研（§1.7）② 字节官方 API 文档 `backend/docs/BYTEDANCE_ARK_API_REFERENCE.md` ③ 实测：seedream `image:[list]` 参考图编辑可用 + doubao vision 判定变体同一人；seedance i2v 4 类人像（写实/AI/卡通/多人）Ark 直连全 succeeded。
>
> **关键修正（人脸审查）**：字节官方明确 Seedance 2.0 不支持直接传真人人脸参考图——**Ark 直连也拦真人**（非 360 独有）。之前测试"Ark 不拦"是因用 Seedream 产物（字节"信任产物"机制）。ViMax 角色首帧须由 seedream 生成（信任产物），不直接接受真实人物照片。详见 §0.4。
>
> **v4 ViMax 增量阶段**：阶段 4.2（ViMax 生图切 seedream）、阶段 5.2（ViMax 视频切 seedance-ark）、阶段 7（ViMax 端到端验证）。

---

## 0. 已确认的官方 API 事实（评审依据）

### 0.1 Chat / 视频 / 生图 —— 火山方舟 Ark（OpenAI 兼容）

| 能力 | 端点 | 鉴权 | Model ID（用户已确认） |
|---|---|---|---|
| Chat | `https://ark.cn-beijing.volces.com/api/v3`（`/chat/completions`、`/responses`） | `Authorization: Bearer $ARK_API_KEY` | **`doubao-seed-2-1-pro-260628`**（✅ vision 实测可用，Ark 直连版） |
| 视频生成 | Ark `/api/v3`（内容生成任务：提交 + 轮询） | `Bearer $ARK_API_KEY` | **`doubao-seedance-2-0-260128`** |
| 生图 | Ark `/api/v3/images/generations` | `Bearer $ARK_API_KEY` | **`doubao-seedream-5-0-pro-260628`** |

> ⚠️ Ark 用**原生 endpoint id**（如 `doubao-seed-1-8-251228` 风格），**不是** 360 命名空间的 `bytedance/doubao-seed-2-1-pro`。GLM/DeepSeek 直连 id 需从火山控制台确认后填入。

### 0.2 语音 —— openspeech.bytedance.com（**非** Ark，独立鉴权）

> **关键（v2 修正）**：项目 `voice_twin/volcengine_client.py:56-78` 已实现新版单头 `X-Api-Key` 鉴权，key 取自 `VoiceCloneConfig.access_key`（`config.py`）。新 TTS client **复用同一 `access_key`**，无需新增 env。

| 能力 | 端点 / 协议 | 鉴权头 | 模型 / Resource-Id |
|---|---|---|---|
| **TTS 单向流式**（视频旁白/稿件配音首选） | `POST https://openspeech.bytedance.com/api/v3/tts/unidirectional`（HTTP Chunked） | `X-Api-Key` + `X-Api-Resource-Id: seed-tts-2.0` + `X-Api-Request-Id` | `seed-tts-2.0`；speaker 从控制台音色库取 |
| **音频生成**（非流式，≤120s，参考音频/图片） | `POST https://openspeech.bytedance.com/api/v3/tts/create` | `X-Api-Key` + `X-Api-Request-Id` | `model: seed-audio-1.0` |
| **语音播客大模型**（双人播客首选） | `wss://openspeech.bytedance.com/api/v3/sami/podcasttts`（WebSocket **二进制协议**） | `X-Api-App-Id` + `X-Api-Access-Key` + `X-Api-Resource-Id: volc.service_type.10050` + `X-Api-App-Key`(固定 `aGjiRDfUWi`) | 播客专属音色系列（如 `dayi`/`mizai`） |
| **声音复刻**（项目已用） | `https://openspeech.bytedance.com/api/v3/tts/voice_clone` | `X-Api-Key` + `X-Api-Request-Id` | Resource-Id `seed-icl-2.0`（`volcengine_client.py` 已用） |

> ⚠️ **端点协议（v3 实测修正）**：现有 voice_clone TTS 用 `/api/v3/tts/unidirectional/sse`（SSE，`volcengine_client.py:335`）。v1 曾断言 seed-tts-2.0 用 `/api/v3/tts/unidirectional`（HTTP Chunked，非 SSE）——**实测推翻**：`/sse`（SSE event 153）与 `/unidirectional`（plain JSON）对 seed-tts-2.0 均可达、同 req_params body、同鉴权。**新 doubao_tts_client 直接抄 `volcengine_client.py` 的 `synthesize_sse`，用 `/sse` SSE 端点，仅改 resource_id + speaker 来源即可，无需独立 Chunked 实现。**
>
> ⚠️ **播客接口是本次最复杂项**：WebSocket 二进制帧协议（4 字节可变 header + payload size + payload，大端序），需实现 StartSession / 收 PodcastRoundResponse(361) / PodcastEnd(363) / FinishConnection 全套事件循环。

### 0.3 ASR —— 现状是「360 网关代理的火山模型」

| 能力 | 现状端点 | 现状 model |
|---|---|---|
| 录音识别（现有 skill） | `https://api.360.cn/v1/audios/generations`（360 网关） | `volcengine/asr-turbo`（火山模型，经 360 转发） |

火山**直连** ASR 走 `openspeech.bytedance.com` 的 ASR 接口（大模型录音识别 / 极速版），鉴权同 §0.2 的 `X-Api-Key`，具体 endpoint 需上线前从控制台核对。

> ⚠️ **上线前必做**：核对 GLM/DeepSeek 真实 model id、播客/TTS 音色 speaker id、ASR 直连 endpoint。

### 0.4 人脸审查约束（v4 增量，ViMax 相关）

> 字节官方明确：「Seedance 2.0 系列模型**不支持直接上传含有真人人脸的参考图/视频**」。

- **直接传真实人物照片** → Ark 直连 seedance i2v/r2v 也被字节安全审核拦（**非 360 独有**）。之前测试"Ark 直连 i2v succeeded"是用 **Seedream 生成的人物图**，属字节"信任产物"机制，不矛盾。
- **信任产物机制**：本账号近 30 天内以下含人脸产物可作 Seedance 输入不触发输入审核：① Seedance 2.0 含人脸视频 ② 其尾帧图 ③ **Seedream 5.0 Lite 文生图含人脸图片**（官方表所列）。仅同账号、原始产物、未超期、未压缩转发。
- **Seedream 5.0 Pro 实测确认（2026-07-12）**：官方信任产物表未显式列 5.0 Pro，但经项目方实测——**5.0 Pro 生成的角色图同样不会被 seedance 拦截**，作 seedance i2v/r2v 输入安全。→ ViMax 角色首帧可直接用 5.0 Pro（一致性优于 Lite），无需退 Lite。
- **替代方案**：① Seedream 生成角色肖像（信任产物，Pro/Lite 均可用）② `asset://` 预置虚拟人像 ③ `asset://` 已授权真人素材。
- **输出仍可能被拦**：信任仅对输入审核生效，输出仍受方舟安全策略约束。

> 对 ViMax：ViMax 角色首帧由 seedream（阶段 4.2）生成 → 属信任产物 → 可作 seedance i2v 输入不拦。**禁止 ViMax 直接接受用户上传的真实人物照片做角色**（或强制走 `asset://` 已授权路径）。详见 `backend/docs/BYTEDANCE_ARK_API_REFERENCE.md` §3。

---

## 1. 现状摸底（本项目实际代码，已核实并修正 v1）

项目基于 **DeerFlow / LangGraph** harness，模型接入分**五条彼此独立**通道。

### 1.1 Chat LLM —— 纯配置驱动（v2 修正：doubao 已走 360，非直连）

- 配置源：`config.yaml`（gitignored）`models:` 段；实例化 `deerflow.models.factory.create_chat_model`（`factory.py:67` `resolve_class` 动态实例化）。工厂是**通用透传**：`ModelConfig` 有 `extra="allow"`（`model_config.py:15`），yaml 任意字段都透传给 provider 构造器（`factory.py:68-82,152`）。
- 前端可选已天然支持：`routers/models.py:40,76-86` `list_models` 全量暴露 `config.models`（仅过滤 secrets）。
- **🔴 v1 漏点 1 — doubao 已在 config.yaml 走 360 网关**（`config.yaml:26-31`）：
  ```yaml
  - name: doubao-seed-2-1-pro
    use: deerflow.models.patched_openai:PatchedChatOpenAI   # ← 不是 DeepSeek
    model: bytedance/doubao-seed-2-1-pro                    # ← 360 命名空间
    api_key: $QIHU_API_KEY                                   # ← 360 key
    base_url: https://api.360.cn/v1                          # ← 360 网关
    supports_vision: false
  ```
  直连 Ark = 推翻 2026-07-07 的 360 整合决策（见 memory `model-consolidation-360-zhinao-plan`），需明确为有意反转。
- **🟢 v3 实测修正 — doubao vision（区分 360 网关 vs Ark 直连）**：`config.example.yaml:41` 的"doubao-seed-2-1-pro vision 不工作"是 2026-07-07 针对**360 网关版**（`bytedance/doubao-seed-2-1-pro` via api.360.cn）的实测，**仅适用于 360 网关版**。2026-07-10 实测 **Ark 直连版**（`doubao-seed-2-1-pro-260628` via ark.cn-beijing）**支持 vision**——三重确认（纯色图 / 复杂真实图 URL / 复杂真实图 base64 均准确描述 + OCR，见阶段 0 实测）→ Ark doubao 条目**设 `supports_vision: true` 已验证，生产 view_image 路径安全**。vision 既已确认，doubao-ark **可作多模态默认主力**（换主力影响所有对话，建议小流量灰度后全量切）。
- **🟢 默认主力（可 go）**：`models[0]` 现为 `google/gemini-3.5-flash`（Lead Agent 主力 + ViMax LLM，多模态）。Ark 直连 doubao vision 已三重实测可用（纯色/复杂真实图/base64），**可作多模态默认主力**——go/no-go：换主力影响所有对话，建议小流量灰度验证后再全量切（见阶段 1）。
- **🟠 字段名随 provider 不同**：`PatchedChatDeepSeek` 吃 `api_base`（langchain DeepSeek 源码）；`PatchedChatOpenAI` 吃 `base_url`（alias `openai_api_base`）。两者**不通用**，工厂不翻译（`factory.py:70-81` 不排除这三者）。
- 可复用 provider：`deerflow.models.patched_deepseek:PatchedChatDeepSeek`（`patched_deepseek.py:17`，Ark 兼容，已有 config.example 示例）、`deerflow.models.patched_openai:PatchedChatOpenAI`（`patched_openai.py:31`）。

### 1.2 视频生成 —— 代码 + 配置（v2 修正：seedance 拦人像仅 r2v/i2v 有记录）

- Registry：`video_gen/providers/registry.py`，`_try_make_ai_video_provider`（L188-228）硬编码分支：`qihu` / `seedance` / `happyhorse` / `kling`，全部用 `config.qihu_api_key`。
- 已内置 `FallbackAIVideoProvider`（`registry.py:48-62`），触发异常（`registry.py:28`）：`ProviderUnavailableError / RateLimitError / TimeoutError / ConnectionError`（**不含** `RuntimeError`，即内容审查不触发 fallback）。
- **🟠 v1 漏点 — fallback 仅 kling 生效**：`_build_ai_video`（`registry.py:105-125`）只在 `ai_video_provider=="kling"` 时装配 fallback，且硬编码 fallback 目标 = `seedance`（`registry.py:119-120`）。要"字节失败降级 360"需扩展。
- ⚠️ 现有 `seedance.py`（L42 `base_url=https://api.360.cn` + `qihu_api_key`，model `volcengine/doubao-seedance-2-0` L43，subclasses `QihuVideoProvider` L35）走 360 网关。**直连 Ark 是新 provider，不改它（保留作备用）。**
- **🔴 v1 漏点 — seedance 拦人像的范围未验证**：`service.py:249-254` docstring 记录 seedance 拦人像是针对**图片输入 r2v/i2v**（`InputImageSensitiveContentDetected.PrivacyInformation`）。通用 `ai_video` 路径是**纯 t2v 不传图**（`visual_sources/ai_video.py:40-43`）。**t2v 模式是否也拦人像，代码无法判断，须上线前实测**（见阶段 5 验证闸门）。
- ViMax 视频走独立路由 `_resolve_vimax_video_backend`（`service.py:440-463`，签名 `(style, override, config)`），`auto`（默认）→ happyhorse（**已有 `seedance`/`hailuo` 显式分支，`seedance` 返回 `config.seedance_video_model`**），**不读 `ai_video_provider`**。
- `seedance_api_key`（`config.py:21`）是**死字段**（全代码无消费方），registry 用 `qihu_api_key` 给 seedance。

### 1.3 文生图 —— 代码 + 配置（v2 修正：非全走 360；ViMax 不受影响）

- `_try_make_ai_image_provider`（`registry.py:129-186`）**5 个分支**：`qihu` / `dashscope` / `gpt-image-2` / `nano-banana` / `gemini-flash-edit`。
- **🔴 v1 错误 — "现有 provider 全走 360"是错的**：`dashscope`（`registry.py:141`）走 `config.dashscope_api_key` + 阿里云 DashScope 网关（`dashscope.py:18` `dashscope.aliyuncs.com`），与 360 完全独立。fallback-to-qihu 假设对 dashscope 不成立。
- **🔴 v1 漏点 — 图片 fallback 是死代码**：`FallbackAIImageProvider` 类存在（`registry.py:31-45`）但 `_build_ai_image`（`registry.py:94-103`）**从未装配**（L103 注释 "fallback = None"）。要"字节图片失败降级 360"需**真正写装配代码**，不是"仅需装配"。
- **🟢 v1 回归担忧多余 — ViMax 不受 `ai_image_provider` 影响**：ViMax 硬编码 `NanoBananaImageProvider`（`engines/vimax/provider_bridge.py:109` `NanoBananaGeneratorForViMax`，model 硬编码 `google/gemini-3-pro-image-preview` L117，`build_vimax_config` L384-385 `class_path` 硬编码）。`_vimax_config_factory`（`service.py:1708-1749`）image 侧只传 `image_api_key=cfg.qihu_api_key` / `image_base_url=cfg.qihu_base_url`，**不传 image_model、不读 `ai_image_provider`**（image model 在 generator 类内硬编码）。CLAUDE.md"gpt-image-2 是 ViMax 链路依赖"**过时**（`registry.py:147-152` 注释"gpt-image-2 reference editing total identity failure… vimax uses nano-banana"）。
- `ai_image_provider` 唯一消费者 = `AIImageVisualSource`（`visual_sources/ai_image.py:40`），纯文生图，**从不传 `reference_image_paths`**。因此 Seedream 是否支持 `/images/edits` 参考图编辑**对现状无影响**（唯一用参考图编辑的是 ViMax，它不走 seedream）。
- 协议差异：image 用 `generate_image(prompt, *, reference_image_paths, **kwargs) -> ImageRef`（`base.py:35-41`）；video 用 `generate_video(prompt, *, duration, **kwargs) -> VideoRef`（`base.py:48-49`）。`FallbackAIImageProvider` / `FallbackAIVideoProvider` 镜像但不互换。

### 1.4 TTS / 语音 / 播客 —— 代码（v2 修正：7 处遗漏 + client 抄错对象）

- 视频 TTS 路由：`orchestration/tts.py:43-57` `synthesize` 按 `voice_id` 前缀：`preset:` → 360 TTS（`_synthesize_qihu` L108-142，失败降级 edge-tts）；其余 → edge-tts。**仅认 `preset:` 前缀**，无字节分支。
- **🔴 v1 漏点 1（最致命）— `DEFAULT_VOICES["zh"]` 覆盖 config**：`orchestration/voices.py:107-108` 硬编码 `"zh": "preset:146"`，在 `service.py:554-560` 与 `service.py:906-911` **两处**覆盖 `config.tts_voice`：
  ```python
  voice_id = payload.get("voice_id") or self._config.tts_voice   # L554
  if not payload.get("voice_id"):                                  # L556
      voice_id = DEFAULT_VOICES.get(lang, voice_id)                # L560 zh→preset:146
  ```
  用户不指定 voice_id（最常见）时中文被强制 `preset:146`。v1 阶段 2 只改 `config.py` 的 `tts_voice` —— **对中文完全无效**。注释 `voices.py:101-106` 说"zh MUST stay on preset"是因 edge-tts/bing 被墙，但 doubao 走 openspeech 国内可达，约束不成立。
- **🔴 v1 漏点 2 — ViMax 对话音色池无 doubao**：`voices.py:46-87` `CURATED_VOICES`（主音色目录，全是 `preset:` qihu + edge-tts Neural，**无 doubao**，同时被前端选择器 + ViMax 对话用）；`engines/vimax/dialogue.py:159-160` `_MALE/_FEMALE_PRESET_VOICES` 从中按 gender+zh 过滤；`dialogue.py:179-224` `build_speaker_voice_map` 轮转分配。后果：ViMax 对话旁白用 doubao，角色音色仍 qihu/edge-tts → 割裂。
- **🟠 v1 漏点 3 — 另两处兜底 `preset:146`**：`storyboard.py:49` `StoryboardConfig.voice_id` 默认；`engines/vimax/engine.py:279` `voice_id or "preset:146"`。
- **🟠 v1 漏点 4 — 5 个直调 edge_tts 的 skill**（绕过后端 `synthesize`，后端加 doubao 分支对它们零影响）：
  - `skills/public/ppt-to-video/scripts/generate_tts.py`（默认 `zh-CN-YunyangNeural`）
  - `skills/public/corporate-video/scripts/generate_tts.py`（默认 `zh-CN-YunjianNeural`）
  - `skills/public/tech-explainer-video/`（`SKILL.md:187` 指令式 `python3 -m edge_tts`）
  - `skills/public/auto-video-flow/scripts/generate_audio_template.py`（**Remotion 模板 TTS 脚本**）
  - `skills/custom/course-video/SKILL.md:25-29`（**remotion_engine="agent" 委托的 skill**，`config.py:85`）
- **🟠 v1 漏点 5 — 前端 + 路由**：`frontend/.../video-gen-page.tsx:615-620` provider 映射硬编码 edge-tts/qihu；`routers/video_gen.py:268-285` GET `/voices` 返回 CURATED_VOICES + edge-tts + qihu preset。
- **🟠 v1 漏点 6 — voice_twin 独立 preset 目录**：`voice_twin/preset_voices.py:17-24` `PRESET_VOICES` 仅 6 个（146/158/142/284/292/340），与 video_gen 的 CURATED_VOICES **列表不同**；`is_preset_voice_id`（L29-36）只认这 6 个。
- **🟠 v1 漏点 7 — 并发判断只认 `preset:`**：`service.py:718` `concurrency = 3 if voice_id.startswith("preset:") else 5`，doubao 走 else（并发 5）。
- 稿件→音频：`voice_twin/production_service.py:154-211` `_do_tts_synthesis`：`is_preset_voice_id` → 360（`qihu_client.synthesize_sse`，返回 WAV）；克隆音色 → Volcengine。带 429/5xx 重试。
- 声音复刻：`voice_twin/volcengine_client.py` **已直连字节 openspeech**（seed-icl-2.0，`volcengine_client.py:56-78` 单头 `X-Api-Key` 取自 `access_key`）——**已有 openspeech 鉴权样例可复用**。
- **🟠 v1 错误 — 新 client 该抄谁**：v1 说"抄 `qihu_tts_client.py`"是错的。`qihu_tts_client.py` 是 360 API（`api.360.cn`、Bearer、SSE base64 WAV 合并），与 openspeech 协议完全不同。新 doubao client 应抄 **`volcengine_client.py`**（同 openspeech 域、同单头鉴权、`_v3_tts_headers` L68-78 可直接复用）。
- 播客：能力在 skill，`skills/public/podcast-generation/`（`scripts/_qihu_tts.py` 是 360 TTS 同步镜像，`scripts/generate.py` 逐句拼接，JSON 结构 `{speaker, paragraph}`）；`daily-news-broadcast` 有**逐字节相同**的 `_qihu_tts.py` 镜像副本。当前播客用 360 TTS 拼接，非字节播客大模型。

### 1.5 ASR —— 已存在（v2 修正：base64 已是主路径；双实现各自漂移）

- 现有 ASR 通过 skill 提供，统一走 `volcengine/asr-turbo`（经 360 网关 `api.360.cn/v1/audios/generations`）：
  - `skills/public/video-dl/scripts/transcribe.py`（277 行）+ `references/asr-guide.md`
  - `skills/public/lecture-to-docx/scripts/transcribe.py`（133 行）
- 环境变量：`ASR_360_API_KEY`（`QIHU_API_KEY` 回退，`transcribe.py:28`）、`ASR_360_BASE_URL`（L29）。
- **🔴 v1 描述过时 — base64 已是主路径**：`video-dl/transcribe.py` 现在**先走 base64 直传**（`_call_asr_api_base64` L197-201），临时托管（uguu.se/tmpfiles.org/litterbox）只是 fallback（L206-223）。`lecture-to-docx/transcribe.py` **只走 base64**，SKILL.md 明确"禁止使用外部文件托管服务"。v1"上传到临时托管"描述应改为"base64 直传优先，托管 fallback"。
- **🟠 v1 漏点 — 两个 transcribe.py 是各自漂移的独立实现**（277 vs 133 行，不同参数解析 argparse vs sys.argv），非副本，须分别改。
- 🔴 **安全问题**：`test_asr.py`（仓库根，`/Users/littlebear/Documents/trainer ai/test_asr.py:8`）硬编码明文 360 API key（`fk761027095...`），应删除或改环境变量。

### 1.6 配置加载

- `VideoGenConfig`（`video_gen/config.py:8-114`，36 字段）/ `VoiceCloneConfig`（`voice_twin/config.py`）在 `app.py` 从 `config.yaml` 构造；`$XXX` 由 harness `app_config.py:266-289` 解析。
- 现有默认：`ai_video_provider="qihu"`（L16，即 Hailuo）、`ai_image_provider="qihu"`（L15）、`tts_voice="preset:146"`（L71）。**无 `ark_api_key` 字段**。

### 1.7 ViMax 完整调用链（v4 新增，来自 ViMax-main 调研）

ViMax-main（`/Users/littlebear/Documents/ViMax-main`）原始 generator 走 yunwu 中转，**API 形态与 Ark 直连一致**：

```
script → CharacterExtractor(chat_model) → CharacterPortraitsGenerator(image_gen)
       生成 front/side/back 肖像（front 无参考；side/back ref=[front]）→ registry 复用全片
       → StoryboardArtist(chat_model) 分镜 + decompose（ff/lf/motion/vis_char_idxs）
       → CameraImageGenerator 机位树
       → per camera: ReferenceImageSelector(chat_model) 选≤8 参考 + text_prompt 元素映射
                   → image_gen.generate_single_image(refs=≤8) → first/last_frame.png
       → per shot: video_gen.generate_single_video(refs=1-2 own frames) → video.mp4
       → concatenate
```

- **图像 generator**（`tools/image_generator_doubao_seedream_yunwu_api.py`）：`POST /v1/images/generations`，`image:[b64_list]`（`image_path_to_b64`）+ `prompt` + `sequential_image_generation:"disabled"` + `response_format:"url"` + `size`。→ Ark 直连等价端点 `/api/v3/images/generations`，同样字段。
- **视频 generator**（`tools/video_generator_doubao_seedance_yunwu_api.py`）：按 0/1/2 张参考图选 t2v/ff2v/flf2v 模型（1.0 lite），content `[{text},{image_url,url:b64,role:first_frame},{image_url,url:b64,role:last_frame}]`，参数 prompt 后缀 `--rs --rt --dur --fps`。→ Ark 直连用 Seedance 2.0 单模型 + role 区分 + request body 参数。
- **协议**（`tools/protocols.py`）：`ImageGenerator.generate_single_image(prompt, reference_image_paths:List[str], **kwargs)→ImageOutput`；`VideoGenerator.generate_single_video(prompt, reference_image_paths:List[str], **kwargs)→VideoOutput`。
- **后端组装**（`tools/render_backend.py`）：`from_config` 读 yaml `image_generator.class_path`+`init_args` → `importlib` 实例化。TrainerAI `provider_bridge.py` 即此桥接入口。
- **一致性机制**（`agents/reference_image_selector.py`）：LLM 从角色肖像+前序帧选≤8 + 生成 text_prompt（"主体1面部参考图片1，妆造参考图片2"）。两阶段：≥8 张先文本筛，再多模态精筛。
- **参考图数量不矛盾**：image gen 消费 selector 选的≤8 张；video gen 只消费该镜头自己生成的 1-2 张首/尾帧。两阶段输入集不同。
- **TrainerAI 现状对应**（行号已 2026-07-12 刷新）：
  - `provider_bridge.py:109` `NanoBananaGeneratorForViMax`（生图，model 硬编码 `google/gemini-3-pro-image-preview`）。
  - `provider_bridge.py:202` `SeedanceVideoGeneratorForViMax`（视频，`__init__` 已有 `backend` 参数，按 `hailuo`/`seedance`(360)/`happyhorse` 分支选 provider）。
  - **`provider_bridge.py:326` `build_vimax_config` 是 ViMax 生图/视频 generator 的真正装配点**：L384-385 `image_generator.class_path` 硬编码 `NanoBananaGeneratorForViMax`，L388-389 `video_generator.class_path` 硬编码 `SeedanceVideoGeneratorForViMax`（`video_init` 含 `backend` 透传 L365）。阶段 4.2/5.2 的核心落点是改这里（新增 `SeedreamGeneratorForViMax` / 在 video generator 加 `seedance-ark` backend 分支），不只是 `_vimax_config_factory`。
  - `service.py:440-463` `_resolve_vimax_video_backend`（`auto`→happyhorse；**已有 `seedance`/`hailuo` 显式分支**，`seedance` 返回 `config.seedance_video_model`）。
  - `service.py:1708-1749` `_vimax_config_factory`：**已调用 `_resolve_vimax_video_backend` 解析 `video_backend`+`video_model` 并传入 `build_vimax_config`（L1738-1739）**；image 侧只传 `image_api_key=cfg.qihu_api_key`/`image_base_url=cfg.qihu_base_url`，**不传 image_model、不读 ai_image_provider**（image model 在 `NanoBananaGeneratorForViMax` 内硬编码）。→ 阶段 4.2 须改 `build_vimax_config` image 装配 + `_vimax_config_factory` 传 `ark_api_key`；阶段 5.2 是在 `_resolve_vimax_video_backend` 加 `seedance-ark` 分支（与已有 `seedance` 并列），非"从不传 model 到传 model"。

### 1.8 沙箱独立 skill 调用点（v4 补，穷尽盘点发现，计划 v3 完全遗漏）

> 以下 skill 脚本跑在沙箱 `/mnt/skills`，**独立进程，直接读环境变量、硬编码 endpoint，不 import `app.*`、不走 `create_chat_model`/video_gen provider**——不在主链路统一切换范围内，须逐个改。

**生图 skill**：
- **`skills/public/trainerai-imagine/scripts/`（多 provider 生图核心引擎，被 7 个 skill 委托）**：`main.ts:719-728` 的 `available` 数组**默认优先级 `qihu360` 排第一**——即使配了 `ARK_API_KEY`，只要 `QIHU_API_KEY` 存在，多 key 时 `available[0]` 仍返回 qihu360。已内置 5 家 provider（`providers/seedream.ts:57` 字节 ARK seedream 已存在、`qihu360.ts`、`openai.ts`、`azure.ts`、`zai.ts`）。参考图场景（`main.ts:710-716`）已含 seedream 选项。**委托它的 skill**：`trainerai-article-illustrator`、`trainerai-infographic`、`trainerai-comic`、`trainerai-image-cards`、`trainerai-slide-deck`、`trainerai-cover-image`（+ trainerai-imagine 自身）。→ 改 `available` 数组把 `seedream` 排到 `qihu360` 前即可（已有 provider，改动小但影响 7 个 skill）。
- **`skills/public/image-generation/scripts/generate.py:56-61`**：硬编码 `QIHU_API_KEY`+`api.360.cn`，纯文生图，**只有 360 一个 provider**，参考图直接抛错（L71-75 引导走 trainerai-imagine）。→ 加 seedream 分支或直接委托 trainerai-imagine。

**生视频 skill**：
- **`skills/public/video-generation/scripts/generate.py:20-21`**：硬编码 `QIHU_BASE_URL=api.360.cn`+`MODEL=minimax/MiniMax-Hailuo-2.3-no-safety_checker`，**只有 360 海螺一个 provider**，无字节分支。→ 加 seedance-ark 分支。

### 1.9 ViMax LLM 字段（v4 补，独立于 models[0]）

- **`config.py:33-34`**：`vimax_llm_model: str = "qwen/qwen3.6-plus"` + `vimax_llm_base_url: str = "https://api.360.cn/v1"`。
- **注入链**：`service.py:1731-1732` → `build_vimax_config`(L376-382) 把 `model_provider="openai"`+base_url(360)+api_key 写入 ViMax `chat_model.init_args` → ViMax vendor `packages/vimax/vimax/agents/*`（script_planner/scene_extractor/character_extractor/screenwriter/reference_image_selector/camera_image_generator 等）全用 `langchain.chat_models.init_chat_model`（**非 `create_chat_model`**）消费。
- **后果**：ViMax 的编剧/分镜/角色抽取/参考图选择等全部 LLM 仍在 360，**独立于 `models[0]`**。不改 `vimax_llm_model`/`vimax_llm_base_url`，ViMax 全程 LLM 不切字节。v3 计划完全没提 ViMax LLM 切换。
- **残留**：`packages/vimax/vimax/utils/provider_presets.py:19-31` 内置 `minimax` preset（`api.minimax.io`）——TrainerAI 不走此路径（bridge 强制 `model_provider="openai"`），迁移时留意残留。

### 1.10 models[0] 决策（v4 补，LLM 全切字节的关键）

- 现状 `models[0]` = `google/gemini-3.5-flash` via 360（Lead Agent 主力 + 一批非对话 LLM 消费方默认）。
- 非对话 LLM 消费方（都通过 `create_chat_model` 吃默认模型）：lead_agent、title/memory/suggestions middleware、content_agents（script/outline/quiz）、assist_service、gallery、video_gen script/storyboard_planner。
- v3 阶段 1"只加 doubao-ark 条目、不挪 models[0]"→ 执行完语言模型主力仍是 360。**与"语言模型切字节为主"目标直接偏差**。→ 需在阶段 1 规划 models[0] 换 doubao-ark（含灰度闸门，见 §6），不只加条目。

---

## 2. 总体设计原则

1. **不删除任何现有能力**：360 / qihu / edge-tts / kling / asr-turbo(360) 全部保留作可选或降级备用。
2. **字节为默认首选**：改各通道默认值（`ai_video_provider` / `ai_image_provider` / TTS 默认音色 + `DEFAULT_VOICES["zh"]` + `CURATED_VOICES` / 播客/ASR 首选）。
3. **自动降级备用**：复用/扩展 `FallbackAIVideoProvider`，**真正装配** `FallbackAIImageProvider`（当前死代码）；TTS client 内部捕获错误回落 360。
   > ⚠️ **fallback 边界（v3 更正）**：仅**基础设施错误**降级——`ProviderUnavailableError / RateLimitError / TimeoutError / ConnectionError`（`registry.py:28` `_FALLBACK_EXC`）。**内容审查失败不降级**：seedance 拦人像等内容审查抛 `RuntimeError`，`_FALLBACK_EXC` 刻意排除它（`provider_bridge.py:167-172`："内容审查不可重试，重试浪费计费且不会自愈"）。正确做法是转成**清晰的用户可见错误**（提示改 prompt），而非丢给备用通道——两家 Volcengine 提供商内容策略相近，重试多半再被拦一次。
4. **Chat 用户可选**：字节 + 360 条目都进 `models:`，前端自动可选。Ark 直连 doubao 已三重实测支持 vision（纯色/复杂真实图/base64），**可作多模态默认主力**（建议小流量灰度后全量切，见 §1.1/阶段 1）。
5. **抄现成模板**：TTS client 抄 `volcengine_client.py`（同 openspeech 域）；生图抄 `qihu_image.py`；视频抄 `seedance.py` 提交+轮询骨架。
6. **区分「Ark 网关」与「openspeech 网关」两套鉴权**，不要混用。openspeech key 复用 `VoiceCloneConfig.access_key`。

---

## 3. 分阶段实施计划

按风险从低到高 / 依赖顺序排列，每阶段独立可交付、可回滚（改回 config 默认值即恢复 360）。**每阶段须配单元测试**（项目 TDD 强制，见 backend/CLAUDE.md）。

### 阶段 0：前置核对 + seedance t2v 人像实测（无代码，最先做）

**目标**：把所有"上线前核对"的外部未知项一次性拉齐，并实测 seedance t2v 是否拦人像——结论决定后续阶段能否启动。

**改动清单（纯核对 + 实测，不改代码）**
1. **火山控制台拉取并确认**（v3 抽离自原各阶段"上线前核对"）：
   - GLM / DeepSeek 直连 Ark 的**原生 endpoint id**（doubao/seedance/seedream 三个已确认，见 §0.1）。
   - TTS `seed-tts-2.0` 的 **speaker id**（男女各若干，填 `DOUBAO_TTS_DEFAULT_SPEAKER` + `CURATED_VOICES`）。
   - 播客大模型双人音色 id（确认默认 `zh_male_dayixiansheng_v2_saturn_bigtts` + `zh_female_mizaitongxue_v2_saturn_bigtts` 可用）。
   - openspeech **ASR 直连 endpoint**（录音文件识别 / 极速版）。
   - 确认账号是**新版单头 `X-Api-Key`** 还是**旧版双头**（TTS/ASR 用单头，播客用双头）。
2. **🔑 seedance t2v 人像实测**（v3 前移自阶段 5 闸门）：
   - 用 `ARK_API_KEY` + Ark seedance 直连，提交一个**纯文本含人物**的 prompt（如"一个女孩在咖啡厅看书"），**不传任何参考图**。
   - 结论 A（不拦）→ 阶段 5 可继续"seedance-ark 作默认 ai_video"。
   - 结论 B（拦）→ **阶段 5 方案调整**：seedance-ark 不作默认（视频产品大部分含人），保留 qihu/Hailuo 作默认、seedance-ark 作可选 opt-in；阶段 5 只做 provider 接入不切默认。
3. 确认 `ARK_API_KEY` / `voice_clone.access_key`（openspeech）已就位。
4. 🔴 **安全清理（v4 前移自阶段 6，与功能改造解耦，最早做）**：删除根目录 `test_asr.py:8` 硬编码明文 360 API key（`fk761027095...`）——改环境变量或删文件。这是安全问题，不应排到阶段 6。

**🔴 阶段 0 硬闸门（v4 补）**：下面 4 项未确认，对应阶段**不得开工**——避免"写完 client 卡在没有 speaker id / 沙箱读不到 key 上"：
- seed-tts-2.0 speaker id 未确认 → 阶段 2a 不得开工。
- 播客双人音色 id 未确认 → 阶段 3 不得开工（阶段 3 可先上退化方案，见阶段 3）。
- ASR 直连 endpoint 未确认 → 阶段 6 不得开工。
- 🔴 **沙箱 env 注入 ARK_API_KEY 未验证** → 阶段 4.3/5.3 不得开工。沙箱 skill（`/mnt/skills`）现读 `QIHU_API_KEY`（`os.getenv`/`process.env`）能工作，说明有注入路径，但 `ARK_API_KEY` 是新增变量须确认注入。**注意 `trainerai-imagine` 另有独立 env 加载链**（`main.ts:137`：`<cwd>/.baoyu-skills/.env` > `~/.baoyu-skills/.env`，非纯沙箱注入）——4.3 须确认 ARK_API_KEY 同时覆盖沙箱注入（python skill）和 `.baoyu-skills/.env`（trainerai-imagine）两条路径。

**验收**：一份《前置核对结果》记录所有 id/endpoint/账号形态 + seedance t2v 实测结论（A/B） + 明文 key 已清理 + **沙箱 ARK_API_KEY 注入已验证**（python skill `os.getenv` + trainerai-imagine `process.env` 均能读到）。**后续每个阶段开工前先查这份记录 + 确认自身硬闸门已过。**

**为什么单独成阶段**：这些是每个阶段的前置条件而非收尾细节；分散在各阶段会导致每个阶段都卡在同一处外部未知上。t2v 实测仅需 key+prompt 无代码，越早做越省事（结论 B 可省掉阶段 5 的一半工作）。

#### ✅ 实测结果（2026-07-10，`ARK_API_KEY` 直连 Ark，脚本 `backend/scripts/test_ark_stage0.py`）

Ark 三项（Chat/生图/视频）已实测，**关键闸门通过**：

| 测试 | 模型 | 结果 |
|---|---|---|
| Chat 文本 | doubao-seed-2-1-pro-260628 | ✅ HTTP 200，返回 `content`+`reasoning_content`（思考链）+`encrypted_content`。证实 `supports_thinking:true` 配置有效 |
| Chat vision | doubao-seed-2-1-pro-260628 | ✅ **重大发现**：64×64 纯色图被准确描述 → **Ark 直连 doubao 支持 vision**（推翻 §1.1 旧结论，详见下） |
| 生图 t2i | doubao-seedream-5-0-pro-260628 | ✅ HTTP 200，`/images/generations` 返回 TOS 签名 URL（86400s），1024×1024 jpeg |
| 视频 t2v 人像 🔑闸门 | doubao-seedance-2-0-260128 | ✅ **CONCLUSION A：t2v 不拦人像**（含人 prompt 成功生成 video_url） |

**视频 API 形态（实测确认，供阶段 5 实现用）**：
- 提交：`POST /api/v3/contents/generations/tasks`，body `{"model":"doubao-seedance-2-0-260128","content":[{"type":"text","text":"<prompt>"}]}` → 返回 `{"id":"cgt-..."}`。
- 轮询：`GET /api/v3/contents/generations/tasks/{id}`，`status` running→succeeded。
- 响应：`{"id","model","status","content":{"video_url":...},"usage":{"completion_tokens":108900},"resolution":"720p","ratio":"16:9","duration":5,"framespersecond":24,"generate_audio":true,"execution_expires_after":172800}`。
- 默认参数（无需显式传）：720p / 16:9 / 5s / 24fps / **`generate_audio=true`（seedance 自带音频，实现时注意与项目 TTS 音频处理的关系）**。
- 生成耗时 ~155s → provider 轮询超时须 ≥180s（参考 `vimax_video_max_wait=400`）。

**🔴 阶段 5 闸门结论：A（t2v 不拦人像）→ seedance-ark 可设为默认 ai_video，阶段 5 按方案推进。**

**✅ 重大修正 — doubao vision（已实测确认）**：§1.1/config.example.yaml:41 的"doubao-seed-2-1-pro vision 不工作"是 2026-07-07 针对**360 网关版**（`bytedance/doubao-seed-2-1-pro` via api.360.cn）的实测；**Ark 直连版（`doubao-seed-2-1-pro-260628`）支持 vision**——2026-07-10 三重实测确认：① 64×64 纯色图准确描述；② 复杂真实图（seedream 橘猫图）URL 传入→详细准确描述 + 书封 OCR（"THE..."）；③ **同一复杂图 base64 传入（= 生产 view_image 路径）→准确描述**。脚本 `backend/scripts/test_ark_vision_realphoto.py` + `test_ark_vision_base64.py`。唯一失败：合成 PIL 文本图（服务端关闭连接，疑似合成图边缘 case，非 vision 能力问题）。→ **Ark doubao 条目 `supports_vision: true` 已验证，生产 view_image 路径安全；可作多模态默认主力**。

**openspeech TTS 实测（2026-07-10，`backend/scripts/test_openspeech_tts.py`）**：用 `voice_clone.access_key` 探 `seed-tts-2.0`，`/api/v3/tts/unidirectional/sse`（SSE event 153）+ `/unidirectional`（plain JSON）均 HTTP 200，返回 code 55000000 "resource ID mismatched with speaker"——**endpoint + auth(X-Api-Key) + req_params body 全确认 OK**，仅缺真实 seed-tts-2.0 speaker id（控制台音色库任务）。且实测 **client 可直接抄 `volcengine_client.py` 的 `synthesize_sse`**（同 SSE 端点 / 同 body / 同鉴权），无需独立 Chunked 实现——简化了阶段 2a。

**剩余 Stage 0 项**：① seed-tts-2.0 speaker id（控制台取，填 `DOUBAO_TTS_DEFAULT_SPEAKER` + `CURATED_VOICES`）；② 播客双人音色 id（WebSocket，较重）；③ ASR 直连 endpoint（控制台核对）。

---

### 阶段 1：Chat 直连（纯配置，但 v1 低估了风险）

**目标**：`doubao-seed-2-1-pro-260628` 直连 Ark 作为**可选**条目（不作默认主力）；GLM/DeepSeek 直连版作备选；360 全部保留。

**改动清单**
1. `.env` / `.env.example`：新增 `ARK_API_KEY`。
2. `config.yaml` `models:` 段**新增**字节直连条目（**追加，不替换现有 360 doubao 条目，不挪 models[0]**）：
   ```yaml
   - name: doubao-seed-2.1-pro-ark          # ← 新名，与 360 版 doubao-seed-2-1-pro 区分
     display_name: 豆包 Seed 2.1 Pro (字节直连)
     use: deerflow.models.patched_deepseek:PatchedChatDeepSeek   # DeepSeek 吃 api_base
     model: doubao-seed-2-1-pro-260628                          # ← Ark 原生 id，非 bytedance/ 前缀
     api_base: https://ark.cn-beijing.volces.com/api/v3         # ← api_base（DeepSeek），非 base_url
     api_key: $ARK_API_KEY
     max_tokens: 32768
     supports_vision: true                # ← v3 实测：Ark 直连版 vision 可用（64×64 图准确描述）；360 网关版才不工作
     supports_thinking: true
     supports_reasoning_effort: true
     when_thinking_enabled:  { extra_body: { thinking: { type: enabled } } }
     when_thinking_disabled: { extra_body: { thinking: { type: disabled } } }
   - name: glm-5.2-ark
     use: deerflow.models.patched_openai:PatchedChatOpenAI       # OpenAI 吃 base_url
     model: glm-5-2-260617                                        # ← Ark 原生 id（用户确认 2026-07-12）
     base_url: https://ark.cn-beijing.volces.com/api/v3          # ← base_url（OpenAI）
     api_key: $ARK_API_KEY
     max_tokens: 32768
     supports_vision: false              # glm-5.2 实测 vision 也不工作（config.example.yaml:41）
   - name: deepseek-v4-pro-ark
     use: deerflow.models.patched_deepseek:PatchedChatDeepSeek   # DeepSeek 吃 api_base
     model: deepseek-v4-pro-260425                                # ← Ark 原生 id（用户确认 2026-07-12）
     api_base: https://ark.cn-beijing.volces.com/api/v3
     api_key: $ARK_API_KEY
     max_tokens: 32768
     supports_vision: false              # DeepSeek V4 无 vision
   ```
   > doubao vision 已实测可用（见阶段 0），上述 `supports_vision: true` 已验证；是否换为默认主力（models[0]）属**后续灰度决策**（本阶段 1.1 仅新增条目，1.2 处理 ViMax LLM，models[0] 换主力见 1.3 + §6 灰度闸门）。
3. 无代码改动（条目新增）。

#### 阶段 1.2：ViMax LLM 切字节（v4 补，§1.9 遗漏）

**目标**：把 ViMax 的编剧/分镜/角色抽取/参考图选择等 LLM 从 360 切到 Ark doubao。现状 `vimax_llm_model="qwen/qwen3.6-plus"`+`vimax_llm_base_url=api.360.cn`（独立于 models[0]，走 `init_chat_model` 非 `create_chat_model`），不改则 ViMax 全程 LLM 仍在 360。

**改动清单**
1. `config.py:33-34`：`vimax_llm_model` 改 `doubao-seed-2-1-pro-260628`；`vimax_llm_base_url` 改 `https://ark.cn-beijing.volces.com/api/v3`。
2. `service.py:1731-1732` `_vimax_config_factory`：`llm_api_key` 改传 `cfg.ark_api_key`（不再 `qihu_api_key`）；`llm_model`/`llm_base_url` 已透传到 `build_vimax_config`（L376-382），改 config 即生效。
3. 留意残留：`packages/vimax/vimax/utils/provider_presets.py:19-31` 内置 minimax preset 不走（bridge 强制 `model_provider="openai"`），无需改。
4. 配置回退：`vimax_llm_*` 可配回 360 作 fallback（ViMax LLM 失败不像视频/生图有 FallbackProvider，靠重试）。

**验收**：ViMax 全流程 LLM（脚本/分镜/角色/参考图选择）走 Ark doubao；vision 相关步骤（`reference_image_selector.py` 多模态精筛）doubao vision 实测可用；配置可回退 360。

**测试**：`tests/test_vimax_*` 回归；ViMax LLM 调用链断点确认走 Ark（mock base_url 断言）。

#### 阶段 1.3：models[0] 换 doubao-ark 灰度（v4 补，§1.10 遗漏）

**目标**：把默认主力 LLM 从 `google/gemini-3.5-flash`(360) 换成 `doubao-seed-2.1-pro-ark`。v3 阶段 1"不挪 models[0]"会导致语言模型主力仍在 360，与"语言模型切字节为主"目标偏差。

**改动清单**
1. `config.yaml` `models:` 段：把 `doubao-seed-2.1-pro-ark`（阶段 1.1 新增）挪到 `models[0]`，`google/gemini-3.5-flash` 降为 `models[1]`（备选）。
2. 影响范围（都吃 models[0]）：lead_agent、title/memory/suggestions middleware、content_agents、assist_service、gallery、video_gen script/storyboard_planner。
3. **灰度闸门（§6）**：5-10% 流量先切，观测 vision 成功率 ≥ 现状、P95 延迟不劣化、报错率 < 1%，达标才全量；不达标回 `gemini-3.5-flash`。
4. 注意 doubao-ark vision 已实测可用（阶段 0），作 models[0] 后所有 `view_image` 路径安全。

**验收**：默认对话 + 非对话 LLM 消费方走 doubao-ark；vision 路径正常；灰度指标达标。

**测试**：`tests/test_model_factory.py` models[0] 实例化；端到端对话回归。

**🔴 验收闸门（思考链回归，v4 升级）**：doubao 返回 `reasoning_content`（思考链），换 models[0] 后所有解析 LLM 输出的下游都须验证不破——灰度达标前**必须**回归以下，任一破即回滚：
- `title` 中间件（`title_middleware.py:143`）生成的 thread 标题（JSON/文本解析）
- `suggestions`（`routers/suggestions.py:132`）追问建议（rich list/block content 解析）
- `memory` updater（`memory/updater.py:291`）fact 抽取（Pydantic 解析）
- content_agents（`script.py`/`outline.py`/`quiz.py`）结构化输出解析
- video_gen `script.py`/`storyboard_planner.py` 的 narration/image_prompt/video_prompt 解析
- 确认 doubao 在 `supports_thinking:true` + `when_thinking_enabled/disabled` 配置下，思考链不污染这些下游的 content 字段（必要时配 `supports_thinking:false` 或 thinking disabled 作 models[0]）。

**风险**：换主力影响所有对话，须严格灰度；上述思考链回归是 1.3 的**硬验收**（非可选）。

**验收（阶段 1 整体）**：`GET /api/models` 列字节 3 条 + 360 全量；前端切换 doubao-ark 对话正常；`supports_vision:true` 下 `view_image` 看图正常；ViMax LLM 走 Ark；models[0] 灰度达标。

**测试（阶段 1 整体）**：`tests/test_model_factory.py` 已覆盖 `api_base`/`base_url` 透传（L566-600、L634-662）；ViMax LLM 调用链断点。

---

### 阶段 2a：TTS 后端核心路由（中文切换成败关键，先上）

**目标**：视频旁白 + 稿件→音频 + ViMax 对话**旁白**默认字节 `seed-tts-2.0`，失败降级 360→edge。这是让"中文视频切 doubao"真正生效的最小集——**先于 2b 上线验证**。

**改动清单**
1. 新增 `app/gateway/voice_twin/doubao_tts_client.py`（**直接抄 `volcengine_client.py` 的 `synthesize_sse`，仅改 resource_id + speaker 来源**——v3 实测：endpoint/auth/body/SSE 解析全同 voice_clone）：
   - 复用 `_v3_tts_headers`（`volcengine_client.py:68-78`，`X-Api-Key` 取 `access_key`）。
   - `POST https://openspeech.bytedance.com/api/v3/tts/unidirectional/sse`（**SSE，与 voice_clone 同端点同协议**——v3 实测确认；v1 说的"非 SSE Chunked"是错的，无需独立 Chunked 实现）。
   - headers：`X-Api-Key`（复用 `VoiceCloneConfig.access_key`）+ `X-Api-Resource-Id: seed-tts-2.0` + `X-Api-Request-Id: <uuid>`。
   - body：`user.uid` + `req_params.{text, speaker, speed_ratio, audio_params.{format=mp3, sample_rate=24000}, additions}`——与 `volcengine_client.py:292-304` 完全一致。
   - 🔑 **缺 speaker id**（Stage 0 待办）：seed-tts-2.0 预设音色需从火山控制台音色库取（实测用占位 speaker 返回 code 55000000 "resource ID mismatched with speaker"，证明 endpoint+auth+body 全 OK，只缺真实 speaker）。
2. 🔴 `orchestration/tts.py::synthesize`：新增 `voice_id.startswith("doubao:")` → `_synthesize_doubao`；try 字节 → 失败降级 `_synthesize_qihu`（360）→ 再失败 `_synthesize_edge`。保留旧分支。
3. 🔴 `orchestration/voices.py:108` `DEFAULT_VOICES["zh"]` 改 `"doubao:<speaker id>"`（id 来自阶段 0）；同步改注释 L101-106（doubao 走 openspeech 国内可达，不再受 bing 被墙约束）。
4. 🔴 `voices.py:46-87` `CURATED_VOICES` 新增 doubao 音色条目（男女各若干，id 来自阶段 0），带 `gender`+`locale` 元数据供 `build_speaker_voice_map` 过滤。
5. 🟠 `storyboard.py:49` + `engines/vimax/engine.py:279` 两处兜底 `preset:146` 改 doubao 默认（或读 config）。
6. 🟠 `service.py:718` 并发判断加 doubao：`if voice_id.startswith(("preset:", "doubao:")): concurrency = 3`。
7. `production_service.py::_do_tts_synthesis`：新增 doubao 分支为首选（`is_doubao_voice_id`），360/克隆保留。
8. `video_gen/config.py`：默认 `tts_voice` 改 `"doubao:<id>"`；新增 `tts_fallback_voice: str = "preset:146"`；新增 `ark_api_key`/`ark_base_url`/`seedance_ark_video_model`（阶段 4/5 复用）。
9. `voice_twin/config.py`：新增 `DoubaoTTSConfig`（`default_speaker`/`resource_id="seed-tts-2.0"`/`enabled`），`api_key` 默认复用 `VoiceCloneConfig.access_key`。
10. `routers/video_gen.py:268-285` GET `/voices` 返回追加 doubao 音色列表。

**验收**：视频/稿件音频默认字节音色；**中文视频**（不指定 voice_id）也走 doubao（验证 DEFAULT_VOICES 覆盖已修）；ViMax 对话**旁白**走 doubao；字节失败降级 360→edge；配置可显式回退。

**测试**：`tests/test_orchestration_tts.py`（doubao 路由 + 降级链）；`tests/test_voices.py`（DEFAULT_VOICES["zh"] 命中 doubao）。

---

### 阶段 2b：TTS 广度覆盖（5 skill 镜像 + 前端 + voice_twin preset）

**目标**：把 2a 没覆盖的"直调 edge_tts 的 5 个 skill" + 前端音色选择 + voice_twin 独立 preset 目录补齐，实现"全部配音"切 doubao。**依赖 2a 已上线验证**。

**改动清单**
1. 逐个改 5 个直调 edge_tts 的 skill 脚本（镜像 `_doubao_tts.py` 进各 skill scripts 目录，同 `_qihu_tts.py` 镜像模式）：
   - `skills/public/ppt-to-video/scripts/generate_tts.py`
   - `skills/public/corporate-video/scripts/generate_tts.py`
   - `skills/public/tech-explainer-video/`（SKILL.md 指令）
   - `skills/public/auto-video-flow/scripts/generate_audio_template.py`（**Remotion 模板 TTS**）
   - `skills/custom/course-video/SKILL.md`（**remotion_engine="agent" 委托的 skill**，`config.py:85`）
   > 这些 skill 跑在沙箱 `/mnt/skills`，**不能 import `app.*`**（harness→app 边界，`test_harness_boundary.py` 强制），必须镜像脚本进 skill 目录。
2. `frontend/.../video-gen-page.tsx:615-620` provider 映射加 `doubao`。
3. `voice_twin/preset_voices.py`：在 `PRESET_VOICES` 加 doubao + `is_preset_voice_id`/新增 `is_doubao_voice_id` 判断（voice_twin preset 目录与 video_gen CURATED_VOICES 列表不同，见 §1.4 漏点 6）。
4. `.env.example`：新增 `DOUBAO_TTS_DEFAULT_SPEAKER`（`access_key` 复用现有 voice_clone 配置，无需新 env）。

**验收**：5 个 skill 脚本输出 doubao 音频；前端音色选择器含 doubao；voice_twin 稿件→音频可选 doubao。

**测试**：`tests/test_dialogue.py`（`build_speaker_voice_map` 含 doubao 池）；skill 脚本各自可运行自检。

**为什么拆 2a/2b**：2a 跨越后端路由/硬编码常量/config，是"中文切换是否生效"的命脉，须单独上线验证不静默失效；2b 是纯广度（5 个独立 skill + 前端），即使延后也不影响核心链路。

---

### 阶段 3：播客改用字节语音播客大模型（v2 修正：client 落点；v4：默认先退化方案）

**目标**：播客 skill 切字节语音。**v4 调整：默认先上退化方案**（用阶段 2a 的 `seed-tts-2.0` 逐句合成双人对白，复用现有 `_qihu_tts.py` 拼接流水线），WebSocket 播客大模型版作为后续增强——避免二进制帧协议（本次最重实现风险）阻塞主线。退化方案牺牲整体韵律但实现简单、依赖少（只需阶段 2a 的 doubao TTS client + speaker id）。

**🔴 v2 修正 — client 落点**：v1 把 `doubao_podcast_client.py` 放后端 `app/gateway/voice_twin/`"供 skill 调用"。但 **skill 跑在沙箱 `/mnt/skills`，是独立进程，不能 import `app.*`**（harness→app 边界，`test_harness_boundary.py` 强制；现有 `_qihu_tts.py` 之所以逐字节镜像进两个 skill 目录，正是此限制）。**二选一**：
- **方案 A（镜像，推荐先做）**：把 `_doubao_podcast.py` 镜像进 `skills/public/podcast-generation/scripts/` + `skills/public/daily-news-broadcast/scripts/`（同 `_qihu_tts.py` 双份维护）。WebSocket 作为一次性脚本可行（开 WS → 收帧 → 写文件 → 退出），无需常驻。
- **方案 B（后端 HTTP 端点）**：后端加 `POST /api/voice-twins/podcast-synthesize`，skill 用 `requests` 调。但**现有 podcast skill 是直接调 Python 不走后端 HTTP**，方案 B 要改 skill 调用方式，改动更大。

**改动清单**
1. 新增 WebSocket 播客客户端（按方案 A 镜像进 skill scripts）：
   - `wss://openspeech.bytedance.com/api/v3/sami/podcasttts`，二进制帧协议（大端）。
   - headers：`X-Api-App-Id` / `X-Api-Access-Key` / `X-Api-Resource-Id: volc.service_type.10050` / `X-Api-App-Key: aGjiRDfUWi` / `X-Api-Request-Id`（**播客用旧版双头，与 TTS 单头不同**）。
   - 事件循环：StartSession → 收 361(音频帧) / 362(轮次时长) / 363(PodcastEnd，取 `audio_url`) → 发 FinishConnection(2)。
   - 模式 `action=3`（用 skill 已生成的双人对白，映射 `{speaker, paragraph}` → `nlp_texts[{speaker, text}]`）。
2. 音色：默认 `zh_male_dayixiansheng_v2_saturn_bigtts` + `zh_female_mizaitongxue_v2_saturn_bigtts`，写入环境变量。
3. `skills/public/podcast-generation/scripts/`：新增 `_doubao_podcast.py`，`generate.py` 优先字节播客大模型，失败回落 `_qihu_tts.py`（360 逐句拼接）。
4. 同步维护 `daily-news-broadcast` 镜像副本（`_qihu_tts.py` 已是逐字节镜像，`_doubao_podcast.py` 同模式）。
5. 更新 `SKILL.md` 说明默认字节播客大模型。

> **实施修正（2026-07-12, commit ecef49c4）**：实际未新建 `_doubao_podcast.py`，改为复用 Stage 2b 已镜像的 `_doubao_tts.py`（函数等价，减少双份维护），doubao 分支内联进 `podcast-generation/generate.py::_tts_single` 与 `daily-news-broadcast/generate_broadcast.py::generate_tts`（doubao seed-tts-2.0 首选、`_qihu_tts` 360 兜底）。功能与计划 §3 退化方案一致；WebSocket 播客大模型版仍为后续增强。

**验收**：播客默认字节双人音色、整段生成自然；断字节鉴权时回落 360 逐句拼接仍可产出。

**风险/退化方案**：WebSocket 二进制协议实现量大；若过重，**退化方案**：用阶段 2 的 `seed-tts-2.0` 逐句合成双人对白（复用现有拼接流水线），牺牲整体韵律但实现简单。

---

### 阶段 4：Seedream 5.0 Pro 生图直连 + 图片降级（v2 修正：ViMax 不受影响；fallback 死代码须装配）

**目标**：`ai_image_provider` 默认字节 Seedream，失败降级 360。

**🟢 v2 澄清 — ViMax 零回归**：ViMax 硬编码 NanoBanana（`provider_bridge.py:109-129`），不读 `ai_image_provider`。切 seedream **不影响 ViMax 首帧/肖像生成**。唯一消费者是 `AIImageVisualSource`（纯文生图）。

**改动清单**
1. 新增 `app/gateway/video_gen/providers/seedream.py`（抄 `qihu_image.py`）：
   - `base_url="https://ark.cn-beijing.volces.com/api/v3"`，`model="doubao-seedream-5-0-pro-260628"`，`Bearer $ARK_API_KEY`，`POST /images/generations`。
   - 429→`RateLimitError`、5xx→`ProviderUnavailableError`（保证 fallback 生效）。
   - 实现 `generate_image(self, prompt, *, reference_image_paths=None, **kwargs)`：`reference_image_paths` 用 `**kwargs` 吸收并忽略（与 qihu/dashscope 一致，seedream 仅文生图）。
2. 🔴 `registry.py` `_try_make_ai_image_provider`（L129-186）新增 `if name == "seedream" and config.ark_api_key:` 分支。
3. 🔴 `registry.py` `_build_ai_image`（L94-103）**真正装配 `FallbackAIImageProvider`**（当前 L103 是死代码 `fallback=None`）：主 seedream，fallback 360 qihu。
   ```python
   primary = self._try_make_ai_image_provider(config, config.ai_image_provider)
   fallback = self._try_make_ai_image_provider(config, "qihu") if config.ai_image_provider != "qihu" else None
   throttled_primary = ThrottledAIImageProvider(primary, **_DEFAULT_THROTTLE["ai_image"])
   if fallback is not None:
       throttled_fallback = ThrottledAIImageProvider(fallback, **_DEFAULT_THROTTLE["ai_image"])
       return FallbackAIImageProvider(throttled_primary, throttled_fallback)
   return throttled_primary
   ```
4. `config.py`：新增 `ark_api_key`、`ark_base_url="https://ark.cn-beijing.volces.com/api/v3"`、`seedream_image_model="doubao-seedream-5-0-pro-260628"`；默认 `ai_image_provider="seedream"`。
5. `config.example.yaml` 补 `ark_api_key: $ARK_API_KEY` + 注释。
6. 🟠 **修订 backend/CLAUDE.md**：video_gen 模块说明里"GptImage2Provider uses it for character-consistency reference editing (ViMax chain dependency)"是**过时描述**（实际 ViMax 硬编码 NanoBanana，见 §1.3）。改为说明 gpt-image-2 仅作 text-to-image 备选、ViMax 走 nano-banana。

**验收**：生图默认 Seedream；模拟 5xx/429 自动切 qihu；配置回退 `qihu` 行为不变；ViMax 链路不受影响（回归测试 `tests/test_vimax_*`）。

**测试**：`tests/test_qihu_image.py` 模式新增 `tests/test_seedream.py`；`tests/test_registry.py` 覆盖 fallback 装配（图片 fallback 此前无测试）。

#### 阶段 4.2：ViMax 生图切 Seedream（v4 增量，NanoBanana → Seedream）

**目标**：把 ViMax 生图从硬编码 NanoBanana 切到 Ark 直连 Seedream 5.0 Pro，保留角色一致性机制（ReferenceImageSelector + `image:[list]` 多参考图）。**依赖阶段 4.1 的 `seedream.py` provider 已建。**

**改动清单**（核心落点 = `build_vimax_config` image 装配 + generator 类，不只 `_vimax_config_factory`）
1. `provider_bridge.py` 新增 `SeedreamGeneratorForViMax`（与 `NanoBananaGeneratorForViMax` L109 并列，不删后者——留作 fallback/回退）：
   - 调阶段 4.1 的 `seedream.py` provider（Ark 直连），model `doubao-seedream-5-0-pro-260628`。
   - `generate_single_image(prompt, reference_image_paths, **kwargs)`：`reference_image_paths` 转 base64 data-URI list 放 `image` 字段（ViMax-main generator 同款机制，`image_path_to_b64`）；首帧无参考图时不带 `image`（纯 t2i）。
   - 复用 ViMax-main 的 `image_path_to_b64`（vendoring 进 TrainerAI 或用现有 `_to_image_url`/`nano_banana._normalize_reference`）。
   - 返回 `ImageOutput(fmt="url", ext="png", data=ref.url)`（与 NanoBanana 一致，下游不变）。
   - **Ark 请求体**：`{model:"doubao-seedream-5-0-pro-260628", prompt, image:[b64_list], size:"2K", output_format:"png", response_format:"url", watermark:false, sequential_image_generation:"disabled"}`。
2. 🔴 **`provider_bridge.py:384-385` `build_vimax_config` 的 `image_generator.class_path`** 改指向 `SeedreamGeneratorForViMax`（这是 ViMax 生图的真正装配点；现状硬编码 `NanoBananaGeneratorForViMax`）。`image_init` 加 `api_key`/`base_url`/`model` 透传（现状 image_init 只 `api_key`+`base_url`，model 在类内硬编码——seedream 须显式传 `doubao-seedream-5-0-pro-260628`，或让 `SeedreamGeneratorForViMax` 默认该 model）。
3. `service.py:1708-1749` `_vimax_config_factory`：image 侧传 `image_api_key=cfg.ark_api_key`/`image_base_url=cfg.ark_base_url`（不再传 `qihu_api_key`/`qihu_base_url`）；image_model 经 `build_vimax_config` 透传到 `SeedreamGeneratorForViMax`（注意 `_vimax_config_factory` 现状已传 `video_model`+`video_backend`，image_model 须新增透传参数）。
4. **一致性保留**：`ReferenceImageSelector` 不变（仍选≤8 参考 + text_prompt 元素映射）；seedream `image:[list]` 接收多参考图（官方 Pro≤10 张，ViMax≤8 在限内）。
5. **人脸合规（§0.4）**：`CharacterPortraitsGenerator` 角色肖像由 seedream 生成 → 属信任产物 → 可作阶段 5.2 seedance 输入不拦。**不直接接受用户上传真实人物照片**（或加 `asset://` 路径）。

**验收**：ViMax 生图走 seedream；`ReferenceImageSelector` 一致性机制不变；角色肖像属信任产物；跨镜角色脸一致（vision 判定 + 肉眼）；seetdream 5xx/429 失败时 ViMax 单镜静态占位（`_static_frame.py` 既有）。

**测试**：`tests/test_vimax_*` 回归生图链路；一致性 vision 判定回归（仿 `backend/scripts/test_ark_seedream_consistency_vision2.py`）；首帧无参考 + 1~8 参考图分支。

**风险**：✅ **信任产物已实测定调（2026-07-12）**：官方信任产物表虽只列「Seedream 5.0 Lite 文生图含人脸」，经项目方实测**5.0 Pro 生成的角色图不会被 seedance 拦截**，可作 seedance i2v/r2v 输入安全。→ `SeedreamGeneratorForViMax` 直接用 5.0 Pro（一致性优于 Lite），无需退 Lite 或保留 nano-banana 作角色图源；后续官方若调整信任范围需回归（详见 §0.4 / `BYTEDANCE_ARK_API_REFERENCE.md` §3.2）。

#### 阶段 4.3：沙箱独立生图 skill 切 Seedream（v4 补，穷尽盘点发现）

**目标**：把沙箱里独立调用生图 API 的 skill 切到字节 Seedream，360 备选。覆盖 §1.8 两处遗漏入口。**依赖阶段 4.1（seedream 调用形态已验证）。**

**改动清单**
1. 🔴 **`skills/public/trainerai-imagine/scripts/main.ts:719-725`**：把 `available` 数组的 `seedream` 提到 `qihu360` **之前**（默认优先级改 seedream 优先；现状 qihu360 排第一，配 ARK_API_KEY 仍走 360）。seedream provider（`providers/seedream.ts:57`）已存在，无需新增 provider。
   ```ts
   // 改前
   const available = [hasQihu360 && "qihu360", hasOpenai && "openai", hasAzure && "azure", hasZai && "zai", hasSeedream && "seedream"].filter(Boolean) as Provider[];
   // 改后（seedream 优先）
   const available = [hasSeedream && "seedream", hasQihu360 && "qihu360", hasOpenai && "openai", hasAzure && "azure", hasZai && "zai"].filter(Boolean) as Provider[];
   ```
   - 影响面：`trainerai-article-illustrator`/`infographic`/`comic`/`image-cards`/`slide-deck`/`cover-image`/`trainerai-imagine` 共 7 个 skill 默认走 seedream。
   - 参考图场景（`main.ts:710-716`）已在 seedream 选项，无需改。
2. 🟠 **`skills/public/image-generation/scripts/generate.py`**：现状只有 360 一个 provider（L37-86），参考图直接抛错（L71-75）。二选一：
   - **方案 A（推荐）**：加 seedream 分支（仿 `generate_with_qihu360` 结构，调 `POST ark.../images/generations`，model `doubao-seedream-5-0-pro-260628`，参考图转 base64 放 `image` 字段）；保留 360 作 fallback。
   - **方案 B**：直接委托 `trainerai-imagine`（参考图场景现状已如此引导），删掉本地 360 实现。
3. `.env.example`（项目根 + skill 沙箱 env）：确认 `ARK_API_KEY` 对沙箱可见（沙箱 env 注入机制见 harness sandbox 配置）。

**验收**：`trainerai-imagine` 配 ARK_API_KEY 默认走 seedream（不再回退 360）；7 个委托 skill 生图走 seedream；`image-generation` skill 走 seedream（或委托 trainerai-imagine）；断 ARK_API_KEY 时回退 360。

**测试**：trainerai-imagine 各 provider 选择单测（仿其现有 test）；`image-generation` 端到端自检脚本。

**为什么单独成阶段**：这 7 个 skill 是用户高频生图入口（封面/插图/信息图/漫画等），不切则用户生图仍走 360，与"生图全切字节"目标偏差。改动小（main.ts 一行数组顺序），但必须显式列入。

---

### 阶段 5：Seedance 2.0 视频直连 + 视频降级（v3：t2v 闸门移至阶段 0 + 内容审查不降级）

**目标**：`ai_video_provider` 默认字节直连 Seedance，失败降级 360。

**✅ 验证闸门（阶段 0 已实测 = CONCLUSION A）**：seedance 拦人像在代码中仅针对 **r2v/i2v（图片输入）**有记录（`service.py:249-254`）；通用 `ai_video` 是纯 t2v 不传图。2026-07-10 实测含人 t2v prompt **成功生成** → **t2v 不拦人像**。本阶段按结论 A 推进"seedance-ark 作默认 ai_video"。（API 形态见阶段 0 实测记录：提交 `POST /api/v3/contents/generations/tasks` + 轮询 `GET .../tasks/{id}`，生成 ~155s。）

**改动清单**
1. 新增 `app/gateway/video_gen/providers/seedance_ark.py`（**区别于走 360 的 `seedance.py`**）：
   - 直连 Ark 内容生成任务（提交 + 轮询），`base_url` Ark、`Bearer $ARK_API_KEY`、`model="doubao-seedance-2-0-260128"`。
   - 参考现有 `seedance.py` 的提交+轮询骨架，替换 base_url/鉴权/请求体为 Ark 规范。
   - 🔴 **`generate_audio=false`（防双音轨）**：Ark seedance 默认 `generate_audio=true`（自带音频，见阶段 0 实测），但通用 ai_video 走 worker `_ffmpeg_mix_audio`（`video_gen_ai_visual.py:30/497`）会把项目 TTS 旁白 `audio_concat_path` 叠到视频上——若 seedance 也带音频会**双音轨叠加/冲突**。`seedance_ark.py` 请求体**必须显式传 `generate_audio:false`**（项目自管 TTS）。360 现有 seedance.py 不传（走 360 默认），切 Ark 后默认行为变了，此点不可漏。
   - 错误分类同阶段 4（429→RateLimitError、5xx→ProviderUnavailableError）。
2. 🔴 `registry.py` `_try_make_ai_video_provider`（L188-228）新增 `if name == "seedance-ark" and config.ark_api_key:`。
3. 🔴 `registry.py` `_build_ai_video`（L105-125）扩展 fallback：当前仅 `kling` 装配且硬编码 fallback=seedance（L119-120），改为 `seedance-ark` 时主 seedance-ark、fallback 360 qihu(Hailuo)。
4. 🔴 **内容审查处理（v3 更正语义）**：seedance 拦人像等内容审查抛 `RuntimeError`（`provider_bridge.py:104-106`），**不在 `_FALLBACK_EXC`（`registry.py:28`）内，且不应纳入**——代码库既有哲学是"内容审查不可重试/不降级"（`provider_bridge.py:167-172`：重试浪费计费且不会自愈；两家 Volcengine 提供商内容策略相近，降级多半再被拦）。**正确做法**：在 `seedance_ark.py` 内复用 `_is_content_review_rejection`（`provider_bridge.py:104`）判别，内容审查失败转成**清晰的用户可见错误**（提示"内容审核未通过，请修改 prompt"），**不降级**；只有基础设施错误（429→`RateLimitError`、5xx→`ProviderUnavailableError`、超时→`TimeoutError`、连接→`ConnectionError`）才走 `FallbackAIVideoProvider` 降级到 Hailuo。
5. `config.py`：默认 `ai_video_provider="seedance-ark"`（仅阶段 0 结论 A 时）；补 `seedance_ark_video_model`（`ark_*` 阶段 4 已加 `ark_api_key`/`ark_base_url`）。🟠 顺手清理死字段 `seedance_api_key`（`config.py:21`，全代码无消费方）。
6. `config.example.yaml` 补注释。

**验收**：视频默认字节直连 Seedance（结论 A 时）；模拟 5xx/429 降级 360 Hailuo；**内容审查失败不降级、转用户可见错误**；配置回退 `qihu` 行为不变；ViMax 不受影响（走 happyhorse 独立路由）。

**测试**：`tests/test_qihu_video.py` 模式新增 `tests/test_seedance_ark.py`；`tests/test_registry.py` 覆盖 seedance-ark→qihu fallback（仅基础设施错误触发）；内容审查 `RuntimeError` 不触发 fallback、转用户错误的单测。

#### 阶段 5.2：ViMax 视频切 Seedance-ark（v4 增量，happyhorse/360 seedance → Seedance-ark）

**目标**：把 ViMax 视频从 happyhorse（360 网关阿里 happyhorse）+ 360 seedance 切到 Ark 直连 Seedance 2.0。**依赖阶段 5.1 的 `seedance_ark.py` provider 已建。**

**🔴 推翻 v3 断言 + 现状澄清**：v3 阶段 5 验收写"ViMax 不受影响（走 happyhorse 独立路由）"——那是切通用 `ai_video` 时的正确陈述。本 5.2 主动改 ViMax 路由，把 happyhorse 降为 fallback。**现状**：`SeedanceVideoGeneratorForViMax`（L202）的 `__init__` **已有 `backend` 参数**，按 `hailuo`/`seedance`(360)/`happyhorse` 分支选 provider；`_resolve_vimax_video_backend`（L440-463）**已有 `seedance`/`hailuo`/`happyhorse` 分支**（`seedance` 返回 `config.seedance_video_model`），`auto`→happyhorse。→ 本阶段是**新增 `seedance-ark` backend 值**（并列于现有三值），不是另起 generator 类，也不是"从不传 model 到传 model"（model/backend 早已透传）。

**改动清单**（核心落点 = `_resolve_vimax_video_backend` 加分支 + `SeedanceVideoGeneratorForViMax` 加 backend 分支 + `_vimax_config_factory` 传 ark 凭据）
1. `provider_bridge.py:202` `SeedanceVideoGeneratorForViMax`：新增 `seedance-ark` backend 分支（与现有 `hailuo`/`seedance`/`happyhorse` 并列），该分支调阶段 5.1 的 `seedance_ark.py` provider（Ark 直连 Seedance 2.0）：
   - `generate_single_video(prompt, reference_image_paths, **kwargs)`：1 张参考图 → `role:first_frame`；2 张 → `first_frame`+`last_frame`；0 张 → 纯 t2v（content 仅 text）。
   - 参考图用 `_to_image_url` 转本地帧为 data URL（base64，与现有 bridge 一致；Ark 接受 base64）。
   - 参数走 request body（`resolution`/`ratio`/`duration`/`watermark`/`seed`/`camera_fixed`），从 ViMax `ShotBrief` 透传。
   - **Ark 请求体**：`{model:"doubao-seedance-2-0-260128", content:[{type:text,text:prompt}, {type:image_url,image_url:{url:b64},role:first_frame}, {type:image_url,image_url:{url:b64},role:last_frame}], resolution, ratio, duration, watermark:false, generate_audio:false}`（ViMax 自带 TTS，关 generate_audio）。
   - 内容审查 RuntimeError 转用户可见错误（仿 `_is_retryable_runtime_error` 防御，不冒泡整管线重试）。
2. 🔴 `service.py:440-463` `_resolve_vimax_video_backend` **新增 `seedance-ark` 分支**（与已有 `seedance`/`hailuo`/`happyhorse` 并列），`auto` 默认改 `seedance-ark`（替代 happyhorse）；保留 happyhorse 作 fallback。现状三分支不动，新增一条：
   ```python
   # 新增（v4）：auto 默认改 seedance-ark（信任产物入参不拦，推翻下方旧 docstring）
   if explicit == "seedance-ark":
       return "seedance-ark", config.seedance_ark_video_model
   if explicit == "seedance":            # 已有，保留作 360 网关 fallback
       return "seedance", config.seedance_video_model
   if explicit == "hailuo":              # 已有
       return "hailuo", config.qihu_video_model
   if explicit == "happyhorse":          # 已有
       return "happyhorse", config.happyhorse_video_model
   return "seedance-ark", config.seedance_ark_video_model   # auto 默认改此
   ```
3. 🔴 **同步改 `_resolve_vimax_video_backend` docstring（L441-456）**：现状写"seedance blocks ANY human-like portrait… unusable for ViMax… happyhorse default"——与 v4"信任产物可解锁 seedance-ark"**直接冲突**。改为说明"seedance-ark 对 Seedream 生成的角色首帧（信任产物）不拦，作 auto 默认；happyhorse 降为 fallback（seedance-ark 基础设施失败或内容审查时降级）"。**不改旧 docstring 后人会困惑为何 auto 路由到被 docstring 标'不可用'的 backend**。
4. `provider_bridge.py:388-389` `build_vimax_config` `video_generator` 装配：`video_init` 已含 `backend`（L365），新增 `seedance-ark` 经此透传；video 凭据在 `_vimax_config_factory` 传 `ark`（见下）。
5. `service.py:1708-1749` `_vimax_config_factory`：video 侧 `video_api_key`/`video_base_url` 改传 `cfg.ark_api_key`/`cfg.ark_base_url`（当 backend==seedance-ark 时）；注意**现状已传 `video_model`+`video_backend`**（L1738-1739，由 `_resolve_vimax_video_backend` 解析），本阶段只需让 ark 凭据随 backend 选择切换（可加条件：backend 含 `ark` 用 ark 凭据，否则 qihu）。
6. **人脸合规（§0.4）**：ViMax 角色首帧由阶段 4.2 seedream 生成（信任产物）→ seedance-ark i2v 不拦。若角色是真实人物（用户上传），seedance-ark 会拦 → 须用 `asset://` 已授权素材或 seedream 生成角色。在 `CharacterPortraitsGenerator` 加约束：不直接接受真实人物照片，或引导用户走 `asset://`。
7. `service.py:1722-1723` 注释更新（auto → seedance-ark，happyhorse 作 fallback，同步 docstring）。

**验收**：ViMax 视频走 seedance-ark；ViMax 角色首帧（seedream 产物）不被拦；seedance-ark 5xx/429 失败降级 happyhorse；内容审查不降级转用户错误；4 类人像（写实/AI/卡通/多人）i2v 不拦回归（用 Seedream 产物）。

**测试**：`tests/test_vimax_*` 回归 ViMax 视频链路；仿 `backend/scripts/test_ark_seedance_i2v_variants.py` 跑 4 类人像不拦回归。

**风险**：seedance-ark i2v ~280s（比 t2v 155s 久），provider 轮询超时 ≥300s（参考 `vimax_video_max_wait=400`）；个人用户并发仅 3，ViMax 并行镜头生成受限。

#### 阶段 5.3：沙箱独立生视频 skill 切 Seedance-ark（v4 补，穷尽盘点发现）

**目标**：把沙箱独立调用生视频 API 的 skill 切到字节 Seedance-ark，360 备选。覆盖 §1.8 遗漏入口。**依赖阶段 5.1（seedance-ark 调用形态已验证）。**

**改动清单**
1. 🔴 **`skills/public/video-generation/scripts/generate.py:20-21`**：现状硬编码 `QIHU_BASE_URL=api.360.cn`+`MODEL=minimax/MiniMax-Hailuo-2.3`（只有 360 海螺一个 provider）。加 seedance-ark 分支：
   - 默认走 `POST ark.../contents/generations/tasks`（提交 + `GET .../tasks/{id}` 轮询），model `doubao-seedance-2-0-260128`，`Bearer $ARK_API_KEY`。
   - `ARK_API_KEY` 存在时优先 seedance-ark，失败回退 360 海螺。
   - 参考阶段 5.1 的 Ark 请求体（content + role + request body 参数）。
2. 仿 image-generation 的 provider 路由模式：`_generate_with_ark` / `_generate_with_qihu360` 两函数 + 选择逻辑。

**验收**：`video-generation` skill 配 ARK_API_KEY 默认走 seedance-ark；断 ARK_API_KEY 回退 360 海螺；SRT/输出格式不变。

**测试**：skill 端到端自检脚本（无 pytest 约束）。

---

### 阶段 6：ASR 改为字节直连优先 + 360 降级（v2 修正：base64 已是主路径；双实现分别改）

**目标**：把现有 ASR 从「360 网关代理」升级为「字节 openspeech 直连优先，360 asr-turbo 降级备用」。

**现状复用（v2 修正）**：ASR 逻辑在两个 skill 的 `transcribe.py`，**base64 直传已是主路径**（video-dl），lecture-to-docx 纯 base64。改动集中在这两处（各自漂移，须分别改）。

**改动清单**
1. 新增字节直连 ASR 调用（镜像 `skills/public/*/scripts/_doubao_asr.py`，同 `_qihu_tts.py` 镜像模式）：
   - 火山 openspeech ASR（录音文件识别 / 极速版），`X-Api-Key` 鉴权（复用 `VoiceCloneConfig.access_key`），endpoint 上线前核对。
   - 复用现有 ffmpeg 抽音频 + base64 直传逻辑（**非临时托管**，托管已是 fallback）。
2. `video-dl/scripts/transcribe.py`（277 行）与 `lecture-to-docx/scripts/transcribe.py`（133 行）：**分别改**（非副本）——默认走字节直连；捕获失败后回落现有 `volcengine/asr-turbo`（360）。保留 base64 直传策略。
3. 新增 `ASR_VOLC_API_KEY` 环境变量（可复用 `VoiceCloneConfig.access_key`）。
4. 更新 `references/asr-guide.md`：说明「字节直连优先，360 降级」+ 更正"base64 直传优先，托管 fallback"（v1 描述过时）。
5. 🔴 明文 key 清理已在阶段 0 前移完成（根目录 `test_asr.py:8`）；阶段 6 只确认无残留 360 key 硬编码。

**验收**：转写默认字节直连、更稳更准；断字节 key 时回落 360 asr-turbo；SRT 输出格式不变。

**测试**：skill 层无 pytest 约束，但新增 `_doubao_asr.py` 应有独立可运行的自检脚本。

---

### 阶段 7：ViMax 端到端验证 + 一致性回归（v4 增量，依赖 4.2 + 5.2）

**目标**：阶段 4.2/5.2 的 ViMax 字节化改造在真实 ViMax 流程里端到端验证，确认角色一致性 + 不拦 + 降级链完整。这是 ViMax 字节化的收尾闸门。

**改动清单（无新代码，纯验证 + 微调）**
1. 用 ViMax 真实产出流程跑一个 10+ 镜头样本视频：
   - 角色肖像由 seedream（4.2）生成 → 验证 front/side/back 一致性。
   - 每镜头 `ReferenceImageSelector` 选≤8 参考 → seedream 生成首/尾帧（一致性 vision 判定）。
   - 每镜头 seedance-ark i2v 用首/尾帧（信任产物，不拦）→ 验证视频生成。
   - 拼接成片。
2. 回归点：
   - **角色一致性**：跨镜头角色脸不变（vision 判定 + 肉眼）。
   - **不拦**：seedance-ark 对 seedream 角色首帧全 succeeded（4/4 类不拦回归）。
   - **降级**：模拟 seedance-ark 5xx → 降级 happyhorse；模拟 seedream 5xx → 降级 qihu。
   - **内容审查**：模拟 seedance 内容审查 RuntimeError → 转用户错误（不降级、不冒泡）。
   - **预算**：`max_calls`/`max_cost_cny` 在 seedream/seedance-ark 下仍生效。
3. 微调：若 seedream 角色一致性在 10+ 镜头真实流程下不达标（单角色 2 变体测试 vs 多镜头生产差异），评估是否回退 ViMax 生图为 nano-banana、保留 seedance-ark 视频；或加 `[Identity Lock]` prompt 强化。
4. 部署约束（memory `deployment-constraint-domestic-only`）：Ark/openspeech 国内可达，符合公司服务器约束；不像 Pexels/bing 被墙。

**验收**：10+ 镜头 ViMax 视频成片；角色跨镜一致；seedance-ark 零拦截；降级链完整；预算可控。

**测试**：端到端无单测（真实 API 调用），靠探针脚本 + 人工肉眼；记录到《ViMax 端到端验证结果》。

**风险/退化方案**：seedream 一致性在长流程可能不如 NanoBanana（NanoBanana 是 Gemini 专门图像编辑模型）。若不达标，退化方案：ViMax 生图保留 nano-banana（360 网关），仅视频切 seedance-ark（seedream 产物仍可作 seedance 输入，但 nano-banana 产物是否属字节信任产物未确认——见 §5 风险表）。

---

## 4. 环境变量汇总（`.env`）

```bash
# 字节火山方舟 Ark —— Chat / 视频 / 生图
ARK_API_KEY=your-ark-api-key
# 字节 openspeech —— TTS / 音频生成 / 声音复刻 / ASR
#   v2：复用现有 VoiceCloneConfig.access_key（config.yaml voice_clone.access_key），
#   无需单独 VOLCENGINE_TTS_API_KEY；若 voice_clone 与 TTS 想用不同 key 再分开。
DOUBAO_TTS_DEFAULT_SPEAKER=<控制台音色id>
# 字节 openspeech 播客（旧版双头鉴权，与 TTS 单头不同）
VOLCENGINE_PODCAST_APP_ID=your-app-id
VOLCENGINE_PODCAST_ACCESS_KEY=your-access-key
DOUBAO_PODCAST_SPEAKER_MALE=zh_male_dayixiansheng_v2_saturn_bigtts
DOUBAO_PODCAST_SPEAKER_FEMALE=zh_female_mizaitongxue_v2_saturn_bigtts
# 字节 ASR 直连（可复用 voice_clone.access_key）
ASR_VOLC_API_KEY=your-openspeech-api-key
# 现有 360（保留作备用/可选）
QIHU_API_KEY=your-qihu-api-key
ASR_360_API_KEY=your-360-api-key   # 可由 QIHU_API_KEY 回退
```

---

## 5. 风险与依赖清单（v2 更新）

| 项 | 风险 | 缓解 |
|---|---|---|
| ✅ doubao vision（已实测） | 360 网关版不工作；**Ark 直连版可用**（三重实测：纯色/复杂真实图 URL/base64 均准确描述 + OCR） | Ark 条目设 `supports_vision:true`（已验证，生产 view_image 路径安全）；可作默认主力，建议小流量灰度 |
| 🔴 TTS 中文切换静默失效 | `DEFAULT_VOICES["zh"]` 覆盖 config（`voices.py:108`+`service.py:554-560,906-911`） | 阶段 2 必改 `DEFAULT_VOICES["zh"]` + `CURATED_VOICES` |
| ✅ seedance t2v 人像闸门（已实测） | 2026-07-10 实测：含人 t2v prompt 成功生成 video_url | **CONCLUSION A**：t2v 不拦人像 → seedance-ark 可设默认 ai_video，阶段 5 按方案推进 |
| 🔴 图片 fallback 是死代码 | `FallbackAIImageProvider` 从未装配（`registry.py:103`） | 阶段 4 真正写装配代码 |
| 🟠 内容审查 fallback 语义 | v2 曾误建议"转 ProviderUnavailableError 触发 fallback"——违反 `provider_bridge.py:167-172` 既有哲学 | **v3 更正**：内容审查不降级，转用户可见错误；仅基础设施错误（`_FALLBACK_EXC`）降级 |
| 🟠 5 个 edge_tts skill 漏改 | 后端 doubao 分支对它们零影响 | 阶段 2.E 逐个镜像 `_doubao_tts.py` |
| 🟠 播客 client 落点 | 后端 `app/` skill 沙箱调不到 | 阶段 3 方案 A 镜像进 skill scripts |
| 🟠 dashscope 非 360 | "全走 360"错误（`registry.py:141`） | fallback-to-qihu 对 dashscope 单独处理或文档标注 |
| GLM/DeepSeek 直连 model id + speaker id + ASR endpoint | 一批外部未知项 | **阶段 0** 统一从控制台拉取核对，记录到《前置核对结果》 |
| 播客 WebSocket 协议 | 二进制帧实现量大 | 退化方案：seed-tts-2.0 逐句合成 |
| openspeech 鉴权双制式 | TTS 单头 vs 播客双头 | TTS 复用 `access_key`；播客明确双头 |
| ASR 双实现漂移 | 两个 transcribe.py 各自独立 | 阶段 6 分别改 |
| 明文 key 泄漏 | `test_asr.py:8` 硬编码 key | 阶段 6 删除/改环境变量 |
| 计费 | TTS/播客/视频/生图按量或音色年费 | 灰度放量 + 复用 video_gen budget 机制 |
| `seedance_api_key` 死字段 | `config.py:21` 定义无消费方 | 顺手清理 |
| 🔴 人脸审查字节原生（v4） | Seedance 2.0 不支持直接传真人人脸，Ark 直连也拦（非 360 独有） | ViMax 角色首帧由 seedream 生成（信任产物）；禁止直接接受真实人物照片（或 `asset://` 已授权）；内容审查转用户错误不降级 |
| ✅ 信任产物含 Pro（v4 实测定调） | 官方信任产物表只列「Seedream 5.0 Lite 文生图含人脸」，**未显式列 5.0 Pro** | **2026-07-12 实测确认 5.0 Pro 角色图不被 seedance 拦截**；ViMax `SeedreamGeneratorForViMax` 直接用 5.0 Pro（一致性优），无需退 Lite；官方若调整信任范围需回归（见 §0.4） |
| 🟠 seedance i2v ~280s（v4） | 比 t2v 155s 久，360 对照 180s 轮询太短 | provider 轮询超时 ≥300s（参考 `vimax_video_max_wait=400`） |
| 🟠 seedream 一致性长流程（v4） | 单角色 2 变体测试 vs 10+ 镜头生产可能差异 | 阶段 7 端到端验证；不达标则 ViMax 生图回退 nano-banana、仅视频切 seedance-ark |
| 🟠 ViMax 并发限流（v4） | 个人用户并发仅 3（非4k），ViMax 并行镜头生成受限 | 控并发或升级企业账号（RPM 600/并发 10） |
| 🔴 trainerai-imagine 默认优先级（v4） | `main.ts:719` `available` 数组 qihu360 排第一，配 ARK_API_KEY 仍走 360 | 阶段 4.3 把 seedream 提到 qihu360 前；影响 7 个委托 skill |
| 🔴 2 个独立生图/生视频 skill（v4） | `image-generation`/`video-generation` 硬编码 360，无字节分支 | 阶段 4.3/5.3 加 seedream/seedance-ark 分支，360 作 fallback |
| 🔴 ViMax LLM 独立于 models[0]（v4） | `vimax_llm_model/base_url` 走 360，ViMax 全程 LLM 不切字节 | 阶段 1.2 改 config + `_vimax_config_factory` 传 ark 凭据 |
| 🟠 models[0] 换主力（v4） | v3 不挪 models[0]→语言模型主力仍 360 | 阶段 1.3 灰度换 doubao-ark；影响所有对话+非对话 LLM 消费方 |
| 🟠 dashscope/kling 去留（v4） | dashscope(阿里)、kling(快手 stub) 非 360 也非字节 | 明确：两者降为纯备选/可选（代码保留），不计入"字节默认"主线；`config.example.yaml:1149-1150` 字段保留 |

---

## 6. 落地顺序建议（v4 更新；含穷尽盘点补的遗漏阶段）

0. **阶段 0（前置核对 + t2v 实测 + 明文 key 清理）**：无代码功能改造，最先做；硬闸门（speaker id/播客音色/ASR endpoint）决定 2a/3/6 开工。v4 补：虚拟人像库开通。
1. **阶段 1（Chat）**：1.1 新增 doubao-ark 条目（纯配置）→ **1.2 ViMax LLM 切字节**（v4 补，`vimax_llm_*`）→ **1.3 models[0] 灰度换 doubao-ark**（v4 补，5-10% 流量 + vision成功率/延迟/报错率闸门 + 回滚到 gemini-flash）。注意 doubao 思考链对 content_agents/title/memory 解析的回归。
2. **阶段 2a（TTS 后端核心）**：配音效果优先级最高；中文切换成败关键（`DEFAULT_VOICES["zh"]`），先于 2b 上线验证。
3. **阶段 6（ASR）**：2 个 skill，base64 已主路径，工作量小。
4. **阶段 4（生图）**：4.1 通用 seedream + 装配图片 fallback + 修订 CLAUDE.md → **4.2 ViMax 生图切 seedream** → **4.3 沙箱独立生图 skill**（v4 补，trainerai-imagine 优先级 + image-generation，影响 7 个 skill）。⚠️ **4.3 只依赖 4.1（seedream 调用形态），不依赖 4.2（ViMax 一致性）——可与 4.2 并行**，4.2 卡住不阻塞 4.3。
5. **阶段 5（视频）**：5.1 通用 seedance-ark + 扩展 fallback + 内容审查转用户错误 + **`generate_audio=false` 防双音轨** → **5.2 ViMax 视频切 seedance-ark** → **5.3 沙箱独立生视频 skill**（v4 补，video-generation）。⚠️ **5.3 只依赖 5.1，不依赖 5.2——可与 5.2 并行**。
6. **阶段 7（ViMax 端到端验证）**：依赖 4.2/5.2，10+ 镜头真实流程验证角色一致性 + 不拦 + 降级链。
7. **阶段 2b（TTS 广度覆盖）**：5 skill 镜像 + 前端 + voice_twin preset，依赖 2a。
8. **阶段 3（播客）**：默认先上退化方案（seed-tts-2.0 逐句），WebSocket 后续增强。

每阶段可单独上线、单独回滚（改回 config 默认值即恢复 360）。**阶段 2a 是 TTS 成败关键**——漏改 `DEFAULT_VOICES["zh"]` 会让中文静默失效。**阶段 4.2 + 5.2 + 7 是 ViMax 字节化核心**。**阶段 4.3 + 5.3 是沙箱独立 skill**（v4 补，不切则用户生图/生视频仍走 360）。**阶段 1.2 + 1.3 是 LLM 全切字节的关键**（v4 补，否则 ViMax LLM + 默认主力仍 360）。dashscope/kling 降为纯备选，不计入主线。
