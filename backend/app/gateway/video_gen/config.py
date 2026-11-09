"""Video generation module configuration."""

from __future__ import annotations

from pydantic import BaseModel, Field


class StallBudgetScalars(BaseModel):
    """W4/P4（2026-08-24 看门狗活性重构）：分阶段预算的标量集。

    配置只放标量（裕量/下限/每帧系数），公式在代码里推导（video_gen/service.py
    的 ``_stage_budget_*`` 方法，``_tts_gather_timeout`` 为范本）--配置里
    不放公式字符串。

    - ``margin``：全程裕量系数（1.25 = 25% 裕量盖住 gather 之后的 concat/
      字幕预切等附加耗时）；
    - ``llm_floor``：单轮 LLM 调用链预算 ≈ 8min（单调用 120s × tenacity 3
      次 + 退避，配置变预算自动变）；
    - ``frame_floor``：单帧渲染最坏 ≈ 20min（生成 300s × (1+续写段数) +
      本地化 180s + 裕量的公式化定稿值，代码注释 13.5min 账）；
    - ``tts_per_frame``：TTS 整批每帧预算（60s，与 _tts_gather_timeout 同源）；
    - ``compose_per_minute``：合成吞吐系数（每分钟视频给 30s 合成预算）。
    """

    margin: float = 1.25
    llm_floor: int = 480
    frame_floor: int = 1200
    tts_per_frame: int = 60
    compose_per_minute: int = 30


class VideoGenConfig(BaseModel):
    """Video generation settings, loaded from config.yaml video_gen section."""

    enabled: bool = False
    storage_dir: str = ".deer-flow/video-gen"

    # Provider selections
    ai_image_provider: str = "gpt-image-2"  # 通用文生图默认 (gpt-image-2=360 文生图 | seedream=字节Ark直连 参考图场景 | seedream-360=360通道 | dashscope=通义万相)
    # 2026-08-03 Task 7：注释里删掉 kling（纯桩，已从 registry 四处接线摘除）。
    # kling_api_key 字段本身保留（存量 config.yaml 可能仍有该键，Pydantic 会拒绝未知键）。
    # E3/E10（2026-08-21 360 主力切换）：360 seedance 为主力（公示 10,000 RPM；
    # Ark 账号级 3 并发撑不起 20 人团队），seedance-ark 降为降级兜底
    # （ark_api_key 存在时自动挂）。回滚 = config.yaml 改回 seedance-ark。
    ai_video_provider: str = "seedance"  # AI video (seedance=360主力 | seedance-ark=Ark兜底 | minimax-h3 | happyhorse)

    # API keys (resolved from env vars by config system)
    dashscope_api_key: str = ""
    kling_api_key: str = ""
    qihu_api_key: str = ""
    ark_api_key: str = ""  # ByteDance Ark 直连 (Chat/生图/视频); empty→未启用, 360 主路不变 (Stage 1.2/4.1/5.1)

    # 360 API endpoints / models
    qihu_base_url: str = "https://api.360.cn"
    qihu_image_model: str = "google/gemini-3.1-flash-image-preview"
    qihu_video_model: str = "minimax/MiniMax-Hailuo-2.3-no-safety_checker"
    # E10（2026-08-21 360 主力切换）：官方在册 id（2026-08-21 GET /v1/models 探针，
    # HTTP 200）。旧值 "volcengine/doubao-seedance-2-0" 亦可用（网关内部归一），
    # 但按在册名对齐。
    seedance_video_model: str = "bytedance/seedance-2-0"  # C3: r2v 多参考图视频（360 主力）
    # E1/E2（2026-08-21 360 主力切换）：resolution 接配置（None=不发交 360 默认；
    # 官方口径 480p~4k，fast/mini 仅 480p/720p）；fast/mini 型号 id 为 2026-08-21
    # GET /v1/models 探针实测（HTTP 200 在册），非猜测。主力 seedance-2-0 不变。
    seedance_video_resolution: str | None = None
    seedance_fast_video_model: str = "bytedance/seedance-2-0-fast"
    seedance_mini_video_model: str = "bytedance/doubao-seedance-2-0-mini"
    happyhorse_video_model: str = "alibaba/happyhorse-1.0-i2v"  # 写实人物 i2v（不拦真人）
    # Task 6（2026-08-01）：360 通道新增两个可选模型，供任务级「生成模型」下拉选用。
    # 两个 id 均于 2026-08-01 打 GET https://api.360.cn/v1/models 实测确认
    # （HTTP 200，214 个模型），**非猜测**。
    # ⚠️ 360 通道的 seedream 是 "5.0"（点号），与 Ark 直连的
    # "doubao-seedream-5-0-pro-260628"（连字符+日期）命名不同 —— 照抄勿手改。
    seedream_360_image_model: str = "bytedance/doubao-seedream-5.0-pro"
    # ⚠️ 大小写敏感：MiniMax 驼峰 + H3 大写。与现有 qihu(Hailuo 2.3) **并存而非替换**。
    minimax_h3_video_model: str = "minimax/MiniMax-H3"

    # ByteDance Ark (Volcengine) 直连 — 字节默认首选, 360 作降级备用.
    # Code defaults 分离: flip 发生在用户 config.yaml (设 ark_api_key / vimax_llm_base_url=Ark /
    # ai_image_provider=seedream / ai_video_provider=seedance-ark); rollback = 撤回 config.yaml 即恢复 360.
    ark_base_url: str = "https://ark.cn-beijing.volces.com/api/v3"
    seedream_image_model: str = "doubao-seedream-5-0-pro-260628"  # Stage 4.1 Ark 生图
    seedance_ark_video_model: str = "doubao-seedance-2-0-260128"  # Stage 5.1 Ark 视频
    # E3（2026-08-21 360 主力切换）：Ark 降级兜底的在途闸上限。Ark 个人档账号级
    # 仅 3 并发（官方口径，不升级企业认证），360 故障转移的并发洪峰须闸在这。
    ark_video_max_inflight: int = 3

    # ViMax chat_model (LLM) — runs on the same 360 gateway + qihu key (unified).
    # Tunable so a stronger/different model can drive ViMax's specialists.
    # 2026-10-12 对齐：豆包聊天删册（doubao-seed-2-1-pro-260628）后生产 = qwen/qwen3.8-max
    # （注册名同值反查可命中）；此前默认 qwen/qwen3.6-plus 是花名册悬空值（无同名注册）。
    vimax_llm_model: str = "qwen/qwen3.8-max"
    vimax_llm_base_url: str = "https://api.360.cn/v1"
    # vimax 视频后端（C5a 2026-10-12 默认翻转）: "seedance"（默认 = 360 主力
    # r2v，bytedance/seedance-2-0）；"auto" 语义同指 seedance；显式
    # "seedance-ark" = Ark 直连回滚位（2026-07 Stage 5.2 曾因 360 拦人像迁 Ark，
    # 2026-10-12 决策迁回 360——拦截风险以 C5c 角色 spike 实测为准）。
    # 未知值在 _resolve_vimax_video_backend 抛 ValueError（防 typo 静默落分支）。
    vimax_video_backend: str = "seedance"
    # vimax 图像后端（C5b 2026-10-12 新增）: "360"（默认 = 360 网关 seedream 5.0
    # Pro，点号命名 bytedance/doubao-seedream-5.0-pro，走 Seedream360GeneratorForViMax
    # 桥 + qihu 凭据）| "ark" = Ark 直连回滚位（连字符命名 doubao-seedream-5-0-pro-260628）。
    # 未知值在 _vimax_config_factory 抛 ValueError。⚠️ YAML 里 "360" 必须带引号
    # （裸 360 会被解析成 int）。
    vimax_image_backend: str = "360"
    # HR-T3: burn subtitles into ViMax videos. Default ON (2026-07-15: narration now
    # generates subtitle.srt, so burn is safe; was False pending A/B sync verification).
    vimax_burn_subtitles: bool = True
    # Cap on concurrent 360 image+video API calls from one ViMax render. ViMax gathers
    # all shots concurrently by default → hammers the 360 gateway (per-shot slowdown,
    # happyhorse 600s timeouts under load). 3 keeps throughput without overloading.
    vimax_api_concurrency: int = 3
    # 阶段 4.5 budget hard-cutoff: per-task caps on the expensive image/video API
    # calls. The bridge generators raise VimaxBudgetExceeded when exceeded, stopping
    # a runaway ViMax run mid-pipeline. Defaults allow a normal ≤30-shot video.
    # (20 was too tight — a 20-shot story hit 20/20 exactly; raised for headroom.)
    # E1/E6 (Task 13): ai_image / ai_video 的成本闸，与下面 vimax_* 三重闸对称。
    # 修前这两条管线**零拦截**：schemas 允许 n_scenes ≤ 200 × video_count ≤ 5 =
    # 单次请求最多 1000 次付费生成，无任何阻拦。
    # 默认值按「200 帧长任务仍可完成」留足余量，只挡真正失控的情况。
    # 2026-07-31 产品决策（审查发现 9）：重试**计费**（供应商侧真实产生了付费调用，
    # 成本闸必须诚实），上限 400→600 = 200 帧 × 2 次尝试 + 50% 余量给单帧重生 /
    # recompose 的跨 render 累积。旧值 400 恰好 = 200×2，全帧重试一次即顶格零余量，
    # 与「仍可完成」的设计意图矛盾。
    ai_image_max_gen: int = 600
    ai_video_max_gen: int = 240
    # ¥ 预算上限。A13 用户拍板（2026-08-27 成本可信化批次 v4.2）：**金额闸默认关闭**——
    # 按次预扣（生图 ≈¥0.3/次、生视频 ≈¥1.5/次，兜底常量）与真实单镜头成本（~¥5-9/次）
    # 差一个量级，估不准的指标不作为拦截标准；次数闸（ai_image/video_max_gen）为唯一硬顶。
    # 金额闸能力保留：运营显式配置才启用（测试服 config.yaml 已核未显式配置该项）。
    ai_visual_budget_cny: float | None = None
    vimax_max_image_gen: int = 160
    vimax_max_video_gen: int = 40
    # ¥ cost budget per vimax task (plan §8.3). Caps image+video API spend.
    # 30-shot ≈ ¥90; ¥200 default gives headroom.
    vimax_budget_cny: float = 200.0
    # Max seconds to wait for ONE Hailuo i2v (video) task to finish polling.
    # Hailuo occasionally stalls; the poll loop raises TimeoutError after this budget.
    # Was hardcoded 600s at every layer — now tunable so it can be lowered when the
    # bridge's per-shot retry (B1) should kick in sooner, or raised when Hailuo is
    # systematically slow. Flows config → build_vimax_config → QihuVideoProvider.
    vimax_video_max_wait: int = 400
    # novel2movie 长片预算三死字段已随管线拆除删除（T1，2026-10-16；字段名见
    # docs/plans/2026-10-16-vimax-degradation-contract-cleanup-plan.md）——旧
    # config.yaml 残留键由 Pydantic extra='ignore' 容忍，部署侧可从容清理
    # （见 changelog 部署注意）。

    # Model for LLM-based script splitting
    script_model: str | None = None

    # ── S5（2026-08-26 镜头调度表计划）────────────────────────────────────
    # 两阶段提示词生成的阶段 1（全片调度表，一次轻量调用）。总开关：false = 纯现状
    # 逐帧（AC3 回滚位——配置复位即回退，代码可留镜像）。调度表模型默认跟随
    # script_model（2026-10-12 起生产 = qwen3.8-flash——deepseek-v4-flash-vision-exp
    # 删册迁移；2026-09-08 十轮×2负载实测其为备选位：逐帧 3.29s/30行调度表 33.3s，
    # 契约校验全过；8s/行超时公式裕度充足覆盖 qwen3.8-flash 分镜耗时，公式不变，
    # 部署后首周随 R7 观察期复核分镜耗时分布）；显式配置 shot_table_model 则阶段 1
    # 用独立实例（须在 models[] 注册）。
    shot_table_enabled: bool = True
    shot_table_model: str | None = None

    # T8 (2026-08-13): LLM 语义重切字幕（D3 默认开启；失败/超时回退规则链）
    # 默认值历史：gemini-3.5-flash（2026-09-08 撤册，gemini 系 360 通道实测双模态
    # 停顿失格管线默认）→ deepseek-v4-flash-vision-exp（09-08 迁入，当时实测 Flash
    # 梯队最快）→ 2026-10-12 迁 qwen3.8-flash（deepseek 删册；09-08 实测备选位 +
    # 2026-10-12 压测默认位胜者，见 docs/2026-10-12-flash-trio-benchmark.md）。
    # 迁移后语义重切必须真实走 LLM 生效，不得静默退回规则链（规则链只是异常兜底，
    # 不是常态）——本地 config.yaml 的 video_gen 段已显式覆盖同名键；config.yaml
    # 是 gitignored，部署/新环境须手改同步（scripts/apply_config_increments_2026-10-12.py），
    # 否则走此代码默认值。
    subtitle_llm_enabled: bool = True
    subtitle_llm_model: str = "qwen3.8-flash"

    # 闸0: cap on in-flight orchestration (script→storyboard→TTS→image/video gen)
    # across ALL tasks in the Gateway process. Orchestration is IO-bound (LLM/TTS/
    # Ark image gen), so this can run higher than the Worker's ffmpeg concurrency.
    # v3: raised 5→8 for the real 10-user load (10 users x 2-3 tasks = 20-30
    # concurrent); at 5 the surplus blocked in _wrap_pipeline's
    # `async with self._concurrency`, sitting in pending — the "submitted but
    # nothing happens under load" symptom. v4: config.yaml's video_gen section
    # explicitly sets this value too, which overrides this default at runtime via
    # VideoGenConfig(**yaml) — both must agree. Raise to 10 after Task 8 monitoring.
    max_concurrent_tasks: int = 20  # T-G4（D5）：与 config.yaml/D11 口径三处一致（2026-09-09 8→20）

    # 闸0b: per-user in-flight cap. One user opening 2-3 tasks could otherwise hog
    # all global slots (闸0), leaving other users queued behind. The per-user
    # asyncio.Semaphore is acquired OUTSIDE the global gate in _wrap_pipeline, so a
    # same-user task over the cap blocks without holding a global slot. Default 3
    # matches the "每人最多同时跑 3 个" requirement; raise if a power user needs more.
    max_tasks_per_user: int = 6  # T-G4（D5）：同上（3→6）

    # TTS (360 preset = higher quality; edge-tts = free fallback)
    tts_voice: str = "preset:146"  # 360 TTS 知性女声（培训场景默认）
    tts_speed: float = 1.2
    # Stage 2a: 360 fallback voice for when doubao TTS fails (used by orchestration
    # fallback chain). NOT the default — the doubao flip happens via
    # resolve_default_voice + DOUBAO_TTS_DEFAULT_SPEAKER env. See
    # backend/docs/BYTEDANCE_MODELS_INTEGRATION_PLAN.md §阶段 2a.
    tts_fallback_voice: str = "preset:146"

    # Orchestration
    default_aspect: str = "16:9"
    frame_concurrency: int = 3
    # E8（2026-08-21 360 主力切换）：分镜提示词逐帧化。1 = 逐帧（默认；T6/T7 实测
    # 三维全胜，线上 28% 分镜失败主因是 bulk 长输出撞 120s 超时）；>1 = 批量回退
    # （配置级，不对用户暴露）。逐帧并发闸默认 8（对齐既有 Semaphore(8) 口径）。
    prompt_chunk_size: int = 1
    prompt_frame_concurrency: int = 8
    # T2（2026-08-31 批量预览并发化）：批量预览生图并发闸。E2 时代的 3 路保守值
    # 已被实测推翻（360 服务端 12 路并发生图 12/12 成功、零排队、33s 全部完成，
    # 单张耗时不随并发上涨）——按拍板默认 12；改小回退 = 配置此项。
    preview_all_concurrency: int = 12
    # Phase 2: Seedance 续写（Tier E）——大缺口付费延长。默认开启（已拍板）。
    # 仅 gap ≥ extend_min_gap (3s) 且 provider 支持 tail_frame 时触发。
    video_extend_enabled: bool = True
    video_extend_min_gap: float = 3.0
    video_extend_max_segments: int = 1
    # E9 超发裁剪保险系数（2026-08-25 补齐）：请求时长 = 旁白 × 系数后对齐 provider
    # 栅格（封顶单次上限——旁白超上限的帧走续写而非超发）。模型欠交付（产物 < 旁白）
    # 时 worker 只能 tpad 定格；×1.25 让请求恒 ≥ 旁白，合成端永远走无损裁剪。
    # 保险项：欠交付率长期为 0 可调回 1.0（= 旧行为）；<1.0 会放大欠交付，勿配。
    duration_safety_factor: float = 1.25
    # 闸1: Process-wide cap on concurrent upstream AI image/video API calls across ALL
    # running ai_image/ai_video tasks. Per-task frame_concurrency alone lets 20-30
    # tasks × 3 = 60-90 concurrent calls hammer the upstream (字节 Ark by default,
    # 360 as fallback) → 429 rate-limiting + cascade timeouts. 20 keeps throughput
    # while staying under Ark's concurrency quota. ViMax path has its own cap
    # (vimax_api_concurrency); this covers only the ai_image/ai_video legacy path.
    ai_api_global_concurrency: int = 20

    # Remotion engine mode — LEGACY engine_arch ONLY. Under engine_arch="multi_engine"
    # (the active path) this field is IGNORED: RemotionEngine always delegates to the
    # course-video agent + auto-video-flow skill (the user's original design:
    # script → agent → skill → video). A data-driven template path exists
    # (run_remotion_storyboard) but is unreachable under multi_engine. Do NOT set this
    # under multi_engine expecting the fast template path — it is silently ignored.
    remotion_engine: str = "template"
    # course-video agent writing Remotion code + esbuild + render takes well over
    # 10min for a non-trivial video; 600s timed out mid-render. 1500s (25min) headroom.
    # ⚠️ Do NOT lower below 1500 in config.yaml — the nested agent WILL be killed
    # mid-render ("course-video agent timed out after Ns"). 600 is a known-bad stale override.
    remotion_agent_timeout: int = 1500
    # remotion_agent_assistant 已删除（T1，2026-10-16）——零消费者死字段（remotion
    # 渲染链 2026-07-15 起走 chat direct-write，不读本值选 assistant）；旧
    # config.yaml 残留键由 Pydantic extra='ignore' 容忍。

    # Architecture switch (阶段 1.5): "legacy" (current service.py monolith
    # orchestration, default) | "multi_engine" (route through engines/ layer:
    # get_engine(video_type).render(ctx) → mode dispatch). Gray-roll: flip to
    # multi_engine to opt in; legacy path stays byte-identical for rollback.
    engine_arch: str = "legacy"

    # Stall watchdog: a non-terminal video task with no DB update for longer
    # than this (seconds) is considered stuck and flipped to `failed` by the
    # periodic reaper. vimax genuinely needs 15-30min (cloud image+video gen),
    # so the default is generous; set 0 to disable. Lower for short-form types.
    stall_timeout_seconds: int = 1800

    # ── 2026-08-24 看门狗活性重构（W5）────────────────────────────────────
    # P1：主谓词（死亡判定）阈值--仅对「已覆盖」（活跃 with_liveness）且
    # updated_at 沉默超过该窗口的任务收尸。0 = 禁用（P9 kill-switch）。
    liveness_threshold_seconds: int = 120
    # P5：B/C 兜底窗口（无覆盖阶段的 progress 冻结固定窗口）。回滚到旧行为
    # = 1200（T6 的 20 分钟窗口）。
    stall_progress_freeze_seconds: int = 1800
    # P10：composing 行的 gateway 双条件兜底开关（W3b）。回滚可单独关。
    composing_backstop_enabled: bool = True
    # P4：分阶段预算标量（公式在 video_gen/service.py 代码里推导）。
    stall_budget_scalars: StallBudgetScalars = Field(default_factory=StallBudgetScalars)

    # W5/P8：帧并发分档（旧硬编码 <=20->frame_concurrency / <=50->2 / >50->1）。
    # 360 通道配额（10,000 RPM）下 >50 帧档放开串行：默认 2 起步（p99 复核后
    # 可升 4），直接缩小进度冻结窗口（窗口错位最大的放大器）。
    frame_concurrency_tier1_frames: int = 20
    frame_concurrency_tier2_frames: int = 50
    frame_concurrency_mid: int = 2
    frame_concurrency_large: int = 2

    def effective_frame_concurrency(self, total_frames: int) -> int:
        """Dynamically adjust concurrency based on total frame count.

        Long videos get lower concurrency to avoid API rate limits.
        W5/P8（2026-08-24 活性重构）：分档阈值与档位值配置化--
        旧硬编码（<=20 -> frame_concurrency / <=50 -> 2 / >50 -> 1）在 360
        通道（10,000 RPM）下过于保守，>50 帧档默认放开到 2。
        """
        if total_frames <= self.frame_concurrency_tier1_frames:
            return self.frame_concurrency
        if total_frames <= self.frame_concurrency_tier2_frames:
            return self.frame_concurrency_mid
        return self.frame_concurrency_large

    def effective_stall_timeout(self, total_frames: int) -> int:
        """E5b: 按帧数缩放 stall_timeout——``stall_timeout_seconds + 60*total_frames``。

        75% 卡死根因：>50 帧强制并发=1 串行，54 帧 ai_video（seedance ~120s/帧）=108min >> 1800s
        必触发 stall reaper。E5a（每帧 progress）已让两谓词不触发；本函数是真长渲染的安全网
        （即使 progress 异常也有足够 timeout）。

        Task 17 (E17): 基数取 ``stall_timeout_seconds`` 而非硬编码 1800——旧实现把该配置项
        变成死字段（改小无效、改大无效）。且字段注释写明「set 0 to disable」，故 0 时返回 0
        真正禁用 reaper，不再返回 60*N。
        """
        base = max(0, self.stall_timeout_seconds)
        if base == 0:
            return 0  # 显式禁用 stall reaper（字段注释承诺的语义）
        return base + 60 * max(0, total_frames)

    def vimax_llm_api_key(self) -> str:
        """API key for ViMax's LLM, matched to ``vimax_llm_base_url``.

        ``vimax_llm_*`` defaults stay on the 360 gateway (rollback-safe: the flip
        to Ark happens in config.yaml, not in code). When ``vimax_llm_base_url``
        points at Ark, ViMax must authenticate with ``ark_api_key``; otherwise it
        uses ``qihu_api_key`` (preserves the pre-Stage-1.2 behavior, so a config
        that hasn't flipped keeps working unchanged). See
        ``backend/docs/BYTEDANCE_MODELS_INTEGRATION_PLAN.md`` §阶段 1.2.
        """
        if self.vimax_llm_base_url.rstrip("/").startswith("https://ark.cn-beijing.volces.com"):
            return self.ark_api_key
        return self.qihu_api_key
