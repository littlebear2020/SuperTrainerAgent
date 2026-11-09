# Vendored ViMax — Patch 清单

> 本文件记录 `backend/packages/vimax/` 相对上游原始项目（`/Users/littlebear/Documents/ViMax-main`，HKUDS/ViMax v1.1.0，2026-06-13 快照）的所有改动。
> 用途：未来合并上游更新、回滚某个改动、或排查"为什么 vendor 行为和上游不同"时，按此清单逐项核对。
> 生成方法：`diff <orig> <vendored>` 逐文件比对（见维护脚本节末）。
> 重建日期：2026-07-22（上一版冻结于 2026-06-29，漏记其后 16 个补丁 commit，且两处陈述错误——本次重建修正）。
>
> **基线钉死（2026-10-05 审计 E6）**：vendored 树 manifest_sha256 =
> `ad43ec803cf96d216d83465e4c7e9d6ff39ef5ed2cd4e8e17ad984fd8cb80958`
> （46 文件；算法与生成命令同 ppt-master：`cd backend && uv run python scripts/ppt_master_manifest.py packages/vimax`）。
> 改动 vendored 树前先复算本值留痕，防"补丁漂移无感知"。
>
> **⚠️ 锚点自指缺陷（2026-10-14 配音修复批实锤）**：本清单文件自身在扫描树内，
> 任何登记（含 E6 写下钉值那 5 行本身）都会改变 manifest——ad43ec80 实为
> 「不含钉基线块」的树哈希，钉值从落笔起即不可能与复算值相等。修复（扫描排除
> 本清单文件）已登记 findings-backlog，脚本未改前按下方**代码树锚点**核对：
> - P27 落盘后代码树锚点（排除本清单文件、`__pycache__`，45 文件，同算法）：
>   `e9f6d8440faa731c9e90ca5c194b4e7055c943f3c0ae365640a8cbb124c605f5`
>   （PR AI 评审修复后再落盘为 `8fe5c422c5ca7f24f813d3a1188718b4639dba8cfba447160fca4ba375da0788`；
>   七轮评审修复（常量抽取去重）后为 `1e0f8e1579d6bd4e590c1a242c65d01e79b69ec371b9d17b5ab2cf163484c11b`）
> - P37 落盘后代码树锚点（排除本清单文件、`__pycache__`，30 文件，同算法）：
>   `7dcf08ba2e503da3b0d3833a47f9d9b19fa38d83d8360b15a6da299639d67ff4`
>   （2026-10-16 收口批：P35 删 12 文件 + P36/P37 逻辑改动后的终态）

## 总览

- vendored 30 个 .py（上游 81 个）：**3 个 vendored-only（根 `__init__.py`、`_trainerai_video_prompt.py`、`_trainerai_llm_accounting.py`）、5 个仅 import 重写、8 个与上游字节级相同、14 个有逻辑改动**（2026-10-16 T1/P35 复算：novel2movie 死面 12 文件已删除；上游快照路径缺失时以初始 vendor commit `77aee3537` 与补丁账本迁移记录兜底）。
- 适配层在包外：`backend/app/gateway/video_gen/engines/vimax/`（engine.py 351L / provider_bridge.py 593L / narration_generator.py 440L / dialogue.py 237L / audio_overlay.py 200L / _static_frame.py 85L）——不在本清单范围。
- vendor 化方式：`agents/` `pipelines/` `interfaces/` `tools/`（仅协议） `utils/` 整合进 `vimax/` 包，统一 `from vimax.xxx`。

## 逻辑补丁清单（仅这些是真实代码改动）

### 初始 vendor（2026-06-24，77cc17cc8/77aee3537）

| # | 文件 | 改动 | 根因 / 验证 |
|---|------|------|------------|
| **P0** | `tools/__init__.py` | 裁掉全部 8 个具体 provider 类导出，只留协议 + RenderBackend（36 行 diff）——provider 能力截肢点，具体生成器由 host 经 provider_bridge 注入 | 架构选择（国内部署约束 + host 注入）；风险见「未 vendor 模块」表 |
| **P1** | `agents/character_portraits_generator.py` | side/back 肖像 prompt 加入 `{features}` + `{style}` 锚点；`generate_side/back_portrait` 新增 `style` 参数 | 换装漂移：real-run `vg_8159cd6cc3fa` 快递员侧面丢失徽章/头盔 |
| **P2** | `agents/storyboard_artist.py` | `retry_timeout: 150 → 300`（2 处） | `design_storyboard` 结构化输出 150s 超时 |
| **P3** | `pipelines/script2video_pipeline.py` | (a) 按镜头进度：`video_clip_done` 事件带 `{idx,total}`；(b) style 全链路注入 `_with_style_prefix`（帧 prompt 顶部加 `Visual style: {style}.`）；(c) style 传给 side/back portrait | "卡在 75%"；"Cartoon 不生效"（`vg_f23954591917`，f42eac76） |
| **P4** | `pipelines/novel2movie_pipeline.py` | (a) langchain 1.x guarded imports；(b) `init_from_config` 工厂；(c) embedding 余弦 reranker 回退替代 BGE | langchain 版本漂移；360 网关无 reranker |

### 后续补丁（2026-06-29 → 2026-07-16，上一版全部漏记）

| # | commit | 文件 | 改动 | 根因 |
|---|--------|------|------|------|
| **P5** | 7e74820fa | `pipelines/script2video_pipeline.py` | 每镜头 `video_clip_done` 进度事件（P3a 扩展） | i2v 阶段进度停滞 |
| **P6** | 042a31bf3 | `agents/screenwriter.py` | `develop_story` 补 `@retry(stop=3, wait_exponential)`（上游无重试——上游 bug） | 上游缺失 |
| **P7** | d373ddd89 | `pipelines/script2video_pipeline.py` + **新增** `vimax/_trainerai_video_prompt.py` | 3a：i2v prompt 注入 `Visual style:` + 可见角色 static_features（主 lever）；3b（实验性）：可见角色 front 肖像作额外参考图（2026-07-22 增补：`VIMAX_3B_PORTRAIT_REFS=off` 环境开关，供 Task 8 A/B） | 跨镜角色漂移 `vg_44e7e21427e6` |
| **P8** | 9ae98ef84 | `pipelines/script2video_pipeline.py` | B1：单镜视频生成失败 → 静态帧兜底 mp4，任务继续不冒泡 | 一镜失败→117 首帧重生成级联（`vg_cda1b579a220`） |
| **P9** | e88972f6f | `pipelines/script2video_pipeline.py` | B3：first_frame prompt 追加 `[Identity Lock]` 强约束 | 跨镜一致性 |
| **P10** | 0fe7d7957 / 7b9773dcc | `agents/character_portraits_generator.py` + `pipelines/script2video_pipeline.py` | HR-T4：`character_reference` 注入 front portrait（URL/文件→参考图；纯文本→prompt 后缀）；pipeline ctor 加 `character_reference`/`character_sheets` 参数 | 用户角色参考在 ViMax 边界被丢 |
| **P11** | 68d12f6f6 | `agents/character_portraits_generator.py` | 3o：新增 `check_portrait_quality()` | portrait 差→全片角色歪。✅ 2026-07-22 已由 P21 接线 |
| **P12** | c29be3689 | `utils/provider_presets.py` + `pipelines/script2video_pipeline.py` | B1（一个 commit 两处）：每个 chat model config 注入 `request_timeout=320`（HTTP 180→320 > 300，让 asyncio.wait_for 先于 httpx.ReadTimeout 触发）+ pipeline `llm_retry_timeout=300` 命名参数替换 150/120 魔数 | 全局挂起防护 + 78% 假冻结。⚠️ **旧版误称 provider_presets vendored-only——上游存在此文件，这是 11 行修改** |
| **P13** | 11bb9db68 | `agents/storyboard_artist.py` | 两处 `@retry` 加 `wait_exponential(min=1,max=10)` + `reraise=True` | 78% 假冻结 + 无退避重试雪崩 |
| **P14** | 1301cf72f | `pipelines/script2video_pipeline.py` | B3：decompose 并发 `Semaphore(3)`（全镜头无上限 gather → N 并发 LLM 抢配额互拖） | LLM 阶段超时雪崩 |
| **P15** | dd88a35db | `pipelines/script2video_pipeline.py` | B5：帧生成失败 → 占位图兜底（复用首帧/灰图） | 一帧失败杀整条管线 |
| **P16** | c4e2b3660 | `agents/character_portraits_generator.py` | B5c：用户三视图 side/back 作 side/back portrait 额外参考图 | 用户三视图在 ViMax 边界被丢 |
| **P17** | （对话质量系列） | `agents/reference_image_selector.py` | `retry_timeout=300` 参数 + `asyncio.wait_for`（2 处） | LLM 调用无界 |
| **P18** | （对话质量系列） | `agents/camera_image_generator.py` | `construct_camera_tree` 加 `@retry`；self-parent 相机自动归根 | LLM 畸形相机树（`vg_78a149025efe` gemini 怪癖） |
| **P19** | （idea2video） | `pipelines/idea2video_pipeline.py` | 每场景 scoped progress 包装（跨场景单调进度）+ `progress` 参数转发 + `character_reference` 透传（2026-07-22 增补：develop_story/write_script 文本阶段补 4 个进度事件，配合 engine `_stage_to_percent` E2 映射） | 进度条回退 `vg_4eea9dfd7215`。⚠️ **旧版误称本文件"全是 import 重写"——实际 54 行逻辑改动** |
| **P21** | （本批，2026-07-22） | `agents/character_portraits_generator.py` + `pipelines/script2video_pipeline.py` | 3o 接线：新增 `generate_portrait_with_gate`（生成→门禁→拒绝重生一次→warn-continue），三视图生成全部过门；拒绝原因透传 progress meta | P11 门禁定义后长期零调用，肖像无人检查 |
| **P22** | （本批，2026-07-22） | `agents/{character_extractor,event_extractor,scene_extractor,global_information_planner,reference_image_selector,script_enhancer,screenwriter}.py` | M4：9 处 `@retry` 补 `reraise=True` + `wait_exponential`（B2/P13 只修了 storyboard_artist/portraits，其余同类雪崩敞口） | 78% 假冻结重试雪崩机制的残余敞口 |
| **P23** | （本批，2026-07-30） | `utils/video.py` | NEW-1：`concatenate_videoclips(clips)` → `method="compose"`。moviepy 2.1.2 默认 `chain` **不做尺寸归一**，而是把后续 clip **拉伸**填满首个 clip 的画布。占位帧恒 1920x1080（见 P24），排在竖屏真镜之前时整片画布被定为 16:9，其后每个真镜横向拉伸 3.16 倍 | 实测量化：chain 下真镜 aspect 0.562→1.778；compose 保持 0.562。⚠️ 主审计称「彩色扫描线花屏」在 moviepy 2.1.2 **未复现**，真实机制是拉伸失真。测试 `test_vimax_concat_size_normalization.py` |
| **P24** | （本批，2026-07-30） | `pipelines/script2video_pipeline.py` + `agents/camera_image_generator.py` | G15/NEW-1：(a) 删除 3 处显式 `size="1600x900"` kwarg——它短路掉桥接层正确的 `_aspect_to_size`；(b) 占位 `Image.new` 与 `_make_solid_color_video` 改用 `resolve_target_size(self.image_generator)` 得到的目标宽高，后者原为**位置传参**而 helper 的 `width/height` 是 keyword-only → 占位恒 1920x1080 | 用户选 9:16 成片出 16:9，手机端不可用。测试 `test_vimax_aspect_propagation.py` |
| **P25** | （决策 2，2026-07-31） | `utils/video.py` | G4：`concatenate_video_files` 在**全部输入编码参数一致**时先试 ffmpeg `concat` demuxer + `-c copy`，失败或参数不一致回落原 moviepy compose 路径。新增 `_streams_are_uniform()`（比对 codec/分辨率/帧率/pix_fmt/time_base，探测失败保守返 False）与 `_concat_stream_copy()` | 修前每次拼接全量重编码，付了「慢 + 掉一档画质」却没换到转场（镜头间仍硬切）= 两个缺点同时存在。实测 3 段 2s 片 **1.206s → 0.027s（44x）**且不掉画质。参数不一致（如 P24 的占位帧混竖屏真镜）时回落，Task 1 的宽高比不变量不受影响。测试 `test_vimax_concat_stream_copy.py` |

### 机械 import 重写（5 文件，零逻辑改动；P35 删 3）

`interfaces/image_output.py`、`interfaces/video_output.py`、`tools/protocols.py`、`tools/render_backend.py`、`utils/image.py`。（P35 删除：`agents/best_image_selector.py`、`agents/script_planner.py`、`interfaces/scene.py`。）

（P23 后：`utils/video.py` 已转入逻辑改动类。）

（P22 后：`character_extractor.py`/`event_extractor.py`/`global_information_planner.py`/`scene_extractor.py` 已转入逻辑改动类。）

### 字节级相同（8 文件；P35 删 3 + 移出 1）

`agents/__init__.py`、`interfaces/camera.py`、`pipelines/__init__.py`、`utils/__init__.py`、`utils/rate_limiter.py`、`utils/timer.py`。（P35 删除：`interfaces/environment.py`、`interfaces/event.py`、`interfaces/frame.py`；`interfaces/__init__.py` 因 P35 改导出面移入逻辑改动类。P26 后 `agents/novel_compressor.py` 已转入逻辑改动类；P27/P28 后 `interfaces/shot_description.py`、`interfaces/character.py` 亦移出。P35/P40 后 `utils/text.py`、`utils/retry.py` 已转入逻辑改动类。）

（P22 后：`agents/script_enhancer.py` 已转入逻辑改动类。）

（P26 后：`agents/novel_compressor.py`、`pipelines/novel2movie_pipeline.py` 已转入逻辑改动类。）

| **P26** | （2026-08-08） | **新增** `vimax/_trainerai_llm_accounting.py` + 10 处接线：`agents/{script_planner,novel_compressor,scene_extractor,event_extractor,script_enhancer,global_information_planner,best_image_selector}.py` + `pipelines/{novel2movie,script2video,idea2video}_pipeline.py` | C/D/E 统一记账：每处 `init_chat_model(...)` 外包 `wrap_for_accounting(...)`，LLM 调用经 AccountingChatModel 委托 wrapper 记录 token/amount 到 runs 表 `operation_kind="llm_bookkeeping"`。每处仅 1-2 行 diff + 1 行 import。根因：ViMax 的 10 处 LLM 构造点不经过 video-gen 瓶颈 `_invoke_llm_bounded`，是记账盲区。测试 `test_vimax_accounting_wrapper.py` |
| **P27** | （2026-10-14 配音格式漂移事故） | `interfaces/shot_description.py`（两处 `audio_desc` Field description+examples 同步改）+ `agents/storyboard_artist.py`（design_storyboard 系统提示 [Guidelines] 首条） | ① audio_desc 中文格式硬约束 + 中文 few-shot：对白 `[说话人] 角色（情绪）: 「台词」`（与宿主 dialogue.py T1 解析器词表对齐）、音效必须 `[音效]` 前缀、旁白不得带标签，并明示「音效描述不打标会被旁白念出来」——qwen3.8-max 迁移后 LLM 输出中文标签变体格式，宿主解析器漏识别致标签/角色/情绪被念出并烧字幕（刻舟求剑事故）。② storyboard 剧本忠于硬约束（用户决策 3）：已确认剧本的角色/情节/台词不得新增/改写，只允许镜头语言层展开——事故中 storyboard 阶段改写了用户确认的蓝图。测试 `test_vimax_dubbing_format_repair.py`（宿主侧）；prompt 变更无逻辑改动。PR AI 评审修正：storyboard_artist 补本 patch 标记注释；audio_desc 无声指示由 None 改空串（str 类型下 LLM 产 null 会 Pydantic 校验失败）；七轮再修：两处 description/examples 抽取为模块级常量 _AUDIO_DESC_DESCRIPTION/_AUDIO_DESC_EXAMPLES 共用（防复制粘贴漂移）并注明标签 token 语言无关 |
| **P28** | （2026-10-15 vendored 缺陷修复） | `pipelines/script2video_pipeline.py` | T1：首帧块（selector / transition video / new-camera extraction / image gen）统一 B5 式兜底；异常路径写比例正确占位图、发 `video_clip_static_fallback({shot_idx, reason})`、finally set `frame_events`；渲染 gather 改为异常时取消剩余任务，防预算终止后兄弟 API 孤儿继续烧钱 | 首帧异常曾穿透 gather 使依赖镜头永久等待。测试 `test_vimax_vendored_remediation.py` |
| **P29** | （2026-10-15 用户拍板 fail-loud） | `pipelines/script2video_pipeline.py` | T2：视频/帧 per-shot `except Exception` 前显式放行反向 import 的 `VimaxBudgetExceeded`；静态 mp4 旁写 `<video>.placeholder`；resume 见标记且非预算终止时删除占位并重生成真镜 | 超预算不得静默产出静态片；普通占位必须可重生。测试同 P28 |
| **P30** | （2026-10-15 LLM 契约修复） | `interfaces/character.py` + `agents/storyboard_artist.py` + `pipelines/idea2video_pipeline.py` | T3：`dynamic_features` 缺失/不可见一律空串且禁止 null；decompose 角色列表补 `Character {index}:` 编号；idea2video side/back portrait 调用透传 style | 防 Pydantic 契约断裂、多角色编号误读、三视图风格漂移。测试同 P28 |
| **P31** | （2026-10-15 契约校验） | `agents/storyboard_artist.py` + `agents/camera_image_generator.py` + `pipelines/{script2video,idea2video}_pipeline.py` | T4：storyboard idx 必须 0..N-1 连续；camera `parent_shot_idx` 显式值必须属于父相机 active shots（缺失按 P18 先例自纠为父相机首个 active shot）；两条 active 管线 concat 前拒绝空列表 | 防 LLM 错位索引、相机死锁与空输入收尾迟炸。行为级测试覆盖首轮畸形/次轮正常与持续畸形响亮失败 |
| **P32** | （2026-10-15 retry 补漏） | `agents/{camera_image_generator,best_image_selector}.py` | T5：两处漏网 `@retry` 补 `wait_exponential(min=1,max=10)` + `reraise=True` | 与 M4 全局重试硬化口径一致。测试 `test_vimax_llm_timeout.py` |
| **P33** | （2026-10-15 肖像一致性） | `agents/character_portraits_generator.py` | T6：肖像门禁对 `Image.open/load` 失败 fail-closed 为 `unreadable` 并重生；B5c side/back 按 `CharacterSheet.name ↔ identifier_in_scene` 唯一匹配，未匹配/歧义只传 front；未命名单表保留旧 side/back 兼容 | 损坏图不得放行；多角色不得互拿三视图。测试 `test_vimax_vendored_remediation.py` |
| **P34** | （2026-10-15 持久化/事件循环修复） | `pipelines/{script2video,idea2video}_pipeline.py` + `agents/character_portraits_generator.py` | T7：6 处同步 `save()` 改 `await asyncio.to_thread(...)`；active 管线 JSON 产物统一临时文件 + `os.replace` 原子落盘 | 防下载/编码阻塞事件循环与写盘中断留下腐败 resume checkpoint。测试同 P28 |
| **P35** | （2026-10-16 遗留收口，用户拍板决策①） | **删除** `pipelines/novel2movie_pipeline.py`、`agents/{novel_compressor,event_extractor,scene_extractor,global_information_planner,best_image_selector,script_planner,script_enhancer}.py`、`interfaces/{event,frame,scene,environment}.py`；`interfaces/character.py` 删 `CharacterInEvent`/`CharacterInNovel`；`interfaces/__init__.py` 清死模型导出 | novel2movie 拆除：langchain 1.x 删 CacheBackedEmbeddings 致 :251/:702 必崩、`_embedding_reranker` 返回 (索引, 分数) 被当文本用（TypeError）、`merge_characters_*` 对 LLM 输出零校验且原地变异挂 @retry——摘牌期零消费，用户拍板不修直接删。app 侧同步：engine 路由对 `"novel2movie"` 响亮报错（T1）、service 长片预算分支删除、config 四死字段删除（`config_version` 28→29）。测试 `test_vimax_novel2movie_removed.py` |
| **P36** | （2026-10-16 遗留收口 T2/T3） | `pipelines/script2video_pipeline.py` | 首帧占位契约：①兜底产物旁写 `<frame>.failed` 侧车（reason+timestamp，`_write_failed_marker`）；`frame_exists` 断点见 `.failed` 删帧重生（`_clear_failed_frame`，camera 首帧与 B5 两处）；②降级口径收窄（`_is_systemic_pipeline_error`：OSError/KeyError/TypeError 上抛不降级，provider/生成类保持占位降级）；③视频段开头检测首帧 `.failed` → 跳过 i2v 直落静态视频 + `.placeholder`（灰帧喂 i2v = 白烧一单产废镜）；④`video_clip_static_fallback` 事件 payload 加 `artifact` 字段（first_frame/last_frame/video_clip），B5 帧兜底补发事件 | 修前：灰帧无标记被 resume 复用永不重生；磁盘满/注册表缺 key 也静默降级成「成功」废镜（PR#25 OCR）。app 侧消费（engine `_consume_static_fallback_event` 按 artifact 分 kind 分计数 + scene_idx→全局 gid 去重 + 透出链 detail 上屏）随本批 T3。测试 `test_vimax_first_frame_contract.py`（含 T7 行为级 resume 重生/响亮失败）+ `test_vimax_static_fallback_degradation.py` |
| **P37** | （2026-10-16 遗留收口 T6） | `pipelines/idea2video_pipeline.py` + `pipelines/script2video_pipeline.py`（新增 `_atomic_text_dump`） | Lane 1 plan T7 枚举漏列的残余：①三处角色肖像 `save()`（PIL 同步编码/写盘）改 `await asyncio.to_thread(...)`；②`story.txt` 直写改临时文件 + `os.replace` 原子落盘（半截 story.txt 会毒化 resume——resume 见存在即跳过 develop_story） | 肖像编码阻塞事件循环；写盘中断静默截断故事。测试 `test_vimax_idea2video_hygiene.py` |
| **P40** | （2026-10-20 ViMax 自动化测试优化） | `agents/storyboard_artist.py` + `utils/retry.py` + `pipelines/script2video_pipeline.py` | 分镜超时/重试参数化：①`StoryboardArtist.max_attempts` 默认 3，script2video pipeline 配置 2（不再全局改 decorator）；②`LocalStoryboardDeadlineError.task_retryable` 三态（script2video False / idea2video True / None 旧行为），`batches_reusable` 仅文案/缓存；③`configure_storyboard_retry(max_attempts,timeout,task_retryable)` 默认 3/300/True，engine 仅 script2video 调 2/420/False。idea2video 子 pipeline 保持 3×300 + 原任务级重试 | 测服 script2video 3×300s×3 轮最坏 ~47min 且重复计费；script2video 2×420 收敛，idea2video 语义不外溢。测试 `test_vimax_storyboard_timeout.py` + `test_vimax_llm_timeout.py` + `test_task_framework_retry_policy.py` |


## 反向依赖清单（vendored → TrainerAI app；合并上游时必须保留/重审）

- `app.gateway.video_gen.engines.vimax._static_frame`：2 个调用面——`_write_placeholder_frame`（T1 首帧与 B5 帧占位）与 `generate_video_for_single_shot` 静态视频兜底。
- `vimax/_trainerai_llm_accounting.py`：1 个文件级反向依赖，内部 lazy import `app.gateway._shared.llm_usage_sink` 记录 LLM 用量。
- `app.gateway.video_gen.engines.vimax.provider_bridge.VimaxBudgetExceeded`：`script2video_pipeline.py` 函数级延迟导入，用于预算异常 fail-loud 穿透（P29；Lane 2 消费同一异常类型）。

## 死代码 / 未接线清单

| 项 | 状态 |
|---|---|
| `check_portrait_quality`（P11） | ✅ 2026-07-22 已由 P21 接线（三视图生成全过门） |
| `verify_timing_alignment` | 在包外适配层 `audio_overlay.py:20`，零调用（对齐已由 `_align_chunk_to_shot` 保证），暂留 |
| ~~`BestImageSelector` / `ScriptEnhancer` / `ScriptPlanner`~~ | ✅ 2026-10-16 已随 novel2movie 拆除删除（P35） |

## 新增文件（上游无对应）

- `vimax/__init__.py` 及各子包 `__init__.py`：包化必需。
- `vimax/_trainerai_video_prompt.py`（75L）：3a/3b anchored i2v prompt 构建（P7），纯函数、rebase 安全。
- `vimax/_trainerai_llm_accounting.py`：TrainerAI LLM 记账 wrapper（P26），依赖宿主 usage sink。

## 未 vendor 的上游模块（架构选择 + 风险登记）

| 上游模块 | 替代方案 | 风险 |
|----------|---------|------|
| `agent_runtime/`（11 文件：上下文压缩/会话索引/仅规划模式） | TrainerAI task framework | ⚠️ 无 context compaction / session index 等价物（已知差距） |
| `tools/` 8 个具体 provider：Veo 3.1（Google 官方 + yunwu）、Seedance(yunwu)、Omni(yunwu)、OpenRouter（`google/veo-3.1-lite`）、NanoBanana×2、Seedream(yunwu) | `provider_bridge.py` 3 个适配器（Seedream/Seedance-Ark/NanoBanana 回滚位） | ⚠️ **单 modality 单 provider 锁定，无 Veo = 无原生音频**（上游对白/音效/唇同步由 Veo 随视频生成）。Veo-yunwu 走国内中转，可达性实测见 2026-07-22 修复计划 Task 2/13 |
| `tools/reranker_bge_silicon_api.py` | P4(c) embedding 余弦回退 | ⚠️ novel2movie 修复时需评估检索质量 |
| `tests/`（19 文件：crash/hang/robustness 回归）+ `vimax_benchmark/`（35 故事） | TrainerAI 自有 `test_vimax_*`（30 文件） | ⚠️ 无上游行为回归网 + 无质量基准，provider 切换回归不可测 |
| `main_*.py` / `ui/` / `prompts/` / `configs/` | TrainerAI 前端 + gateway | ❌ 不补 |

## 维护脚本（重新生成对比）

```bash
ORIG="/Users/littlebear/Documents/ViMax-main"
VEND="<repo>/backend/packages/vimax/vimax"
export LC_ALL=en_US.UTF-8  # ⚠️ macOS 默认 C locale 会把含中文注释的文件当 binary diff → 分类全错（2026-07-22 实证踩过）
for f in $(find "$VEND" -type f -name "*.py" | sed "s|$VEND/||" | sort); do
  case "$f" in
    agents/*|pipelines/*|interfaces/*|tools/*|utils/*) ORIG_FILE="$ORIG/$f";;
    *) ORIG_FILE="";;
  esac
  [ -n "$ORIG_FILE" ] && [ -f "$ORIG_FILE" ] && diff -aq "$ORIG_FILE" "$VEND/$f" >/dev/null 2>&1 || echo "CHANGED: $f"
done
```

## 合并上游更新的流程

1. 拉取上游新版本到 `/tmp/vimax-upstream`。
2. 跑上面的维护脚本，列出差异文件。
3. **机械 import 文件**：直接覆盖，再批量 `sed` 重写 `from X` → `from vimax.X`。
4. **逻辑补丁文件**（P1-P20+）：手动三方合并，**保留我们的补丁**（按上表根因核对），仅采纳上游的逻辑改进。
5. 跑 `backend/tests/test_vimax_*.py` 全套确认无回归。

## P38 (TrainerAI, 2026-10-19, Plan C Task 1): 长脚本确定性分批与安全合并
- `vimax/utils/text.py`：新增 `SINGLE_BATCH_SHOT_THRESHOLD=12` / `STORYBOARD_BATCH_SHOT_CAP=8` / `StoryboardBatchPlan` / `plan_storyboard_batches`（段落/句子保字符切分、顺序均衡装填、单元不足减批不硬切、单批 cap 8）/ `merge_storyboard_batches`（全局 idx 重编号、仅全局末镜 is_last、(batch, cam_idx) 独立命名空间、空批/乱序/负 cam/超限 raise）。
- `vimax/agents/storyboard_artist.py`：`design_storyboard` 增可选 `target_script`/`shot_budget` 关键字参数——批次上下文经 user_requirement 注入（片段产镜 + 全文只读 + 预算上限），缺省参数提示词零变化。批次循环在 pipeline 层（tenacity 之外）。
- `vimax/utils/retry.py`（Task 3）：`LocalStoryboardDeadlineError(TimeoutError)`（`pipeline_retryable=False`，消息含稳定标记 `vimax_storyboard_deadline`）+ `await_with_local_deadline`（done/pending 语义：到期 cancel+drain 后抛专用异常；provider 自身 TimeoutError 原样；外层取消传播）。`storyboard_artist.design_storyboard` 的 `asyncio.wait_for` 改经此 helper。
- `vimax/utils/text.py`（边界收口 P0，2026-10-19）：`_SENTENCE_SPLIT_RE` CJK/全半角句末标点不再要求后随空白（中文无空格书写曾静默退化单批）；ASCII `.` 保留 `(?=\s|$)` 前瞻防小数误切。
- `vimax/utils/retry.py` + `storyboard_artist.py` + `script2video_pipeline.py`（边界收口 P1-4，2026-10-19）：`LocalStoryboardDeadlineError` 增 `error_code`/`batches_reusable` 元数据，`pipeline_retryable = not batches_reusable`（拆批=断点可复用不重试；单批=保留外层重试）；`await_with_local_deadline(..., batches_reusable=False)` 与 `design_storyboard(..., batches_reusable=)` 透传，pipeline 仅 `batch_total>1` 传 True。**该重试口径已由 P40 的 `task_retryable` 三态取代（script2video False / idea2video True / None 旧行为）。**
- `vimax/utils/text.py`（边界收口 P2，2026-10-19）：空批收缩 while 循环 batches/budgets 同步 pop（修前错位致末批偏小预算）；返回改 zip 对齐。
- 边界收口评审修复（2026-10-19）：`text.py` `_STORYBOARD_BATCH_PROMPT_VERSION` 递增 "2"（P0 中文句界 + P1-3 预算注入改了批次语义，旧二进制缓存跨升级作废）；`script2video_pipeline.py` 单批调用显式 `batches_reusable=False`。
- `vimax/pipelines/script2video_pipeline.py`（Task 2）：`__call__`/`design_storyboard` 增 `global_shot_budget` 可选参数（宿主 engine 结构化传入，仅 script2video）——>12 时确定性分批顺序执行（共享 characters）、批次缓存 `storyboard_batches/`（manifest 指纹=script+characters+budget+prompt_version，失配作废；只重跑缺失批）、每批原子写 + `load_storyboard`/`storyboard_batch_ready` 进度事件（meta batch_idx/batch_total）、全部成功后 `merge_storyboard_batches` 合并并原子写根 `storyboard.json`。缺省/≤12 = 原单次调用。

## P39 (TrainerAI, 2026-10-20, 边界热修复 P0 Critical): 纯空白不得独立成分镜批
- `vimax/utils/text.py`：新增 `_fold_whitespace_units`（尾部/中置空白并入前一单元、前导空白并入后一非空白单元，字符零丢失）——`_split_units` 段切与句切两分支统一过折叠（PR#36 复审 Critical：修前句切产物尾部 `"\n"` 独立成批、段切前导 `"\n\n"` 独占批 0，纯空白被当剧本喂 LLM）；`plan_storyboard_batches` 返回前显式 if/raise 校验批/预算对齐 + 每批非空白（替换 assert——`python -O` 下失效）；`_STORYBOARD_BATCH_PROMPT_VERSION` "2"→"3"（批次内容语义变化，旧二进制缓存含空白批须作废重跑）。

## P40 (TrainerAI, 2026-10-20, ViMax 自动化测试优化): 分镜超时/重试收敛
- `vimax/agents/storyboard_artist.py`：`design_storyboard` 从静态 tenacity decorator 改为 `AsyncRetrying` 运行时循环，`StoryboardArtist.max_attempts` 默认 3；script2video pipeline 配置为 2，idea2video/直接构造保持 3。
- `vimax/utils/retry.py`：`LocalStoryboardDeadlineError` 增 `task_retryable` 三态——`False`（script2video，2×420 不任务级重放）/`True`（idea2video，3×300 + 原任务级重试）/`None`（旧单批 True、多批 False）。`batches_reusable` 仅保留文案/缓存语义。
- `vimax/pipelines/script2video_pipeline.py`：新增 `configure_storyboard_retry` 与 `storyboard_max_attempts/storyboard_task_retryable` 默认值（3/True/300s）；engine 仅 script2video 调 `(2, VIMAX_STORYBOARD_TIMEOUT_S 默认420, False)`。`llm_retry_timeout`（decompose）保持 300。
- 测试：`test_vimax_storyboard_timeout.py`、`test_vimax_llm_timeout.py`、`test_task_framework_retry_policy.py`。
