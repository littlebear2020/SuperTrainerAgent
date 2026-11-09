# Prompt Dialect Reference（提示词方言层参考表）

> Phase 1（2026-08-17）落地，2026-08-18 盲评定稿默认 v2。代码：
> `app/gateway/video_gen/prompt_translators/`（纯代码翻译器：0 额外 LLM 调用/确定性/
> 可离线测试）。设计依据：MiniMax 官方 skill + 火山官方/社区指南交叉验证
> （详见 `docs/plans/archive/video-gen-prompt-translator-plan-2026-08-16.md` §一）。
> 本文件供未来新增 provider 方言参考（Phase 1 T8）。

## 架构与路由

```
storyboard_planner（唯一 LLM 调用）产出 visual_ir（白名单五键）
    ↓ prompt_translators.translate_video_prompt(frame, *, provider_name, reference_count=0, reference_kind=None, entity_names=None, prop_count=0)（keyword-only）
    ├─ minimax-h3         -> h3.py（中文三字段，D-B 拍板中文直出）
    ├─ seedance / seedance-ark -> seedance.py（中文五槽）
    └─ 其余 / 无 IR / env=legacy -> legacy.py（现行 compose_video_prompt 透传，P0-5）
env VIDEO_GEN_PROMPT_DIALECT = v2（默认，2026-08-18 定稿）| legacy（一键回滚）
```

**IR 白名单**（`StoryboardFrame.visual_ir`，None=旧数据/手改帧[编辑端点清 IR]/拆帧子帧
[不继承，同 graphic_spec 先例]）：`subject` / `action` / `environment` / `style` / `audio`。
`shot_size`/`camera_move` 仍是 Task 8 帧字段的唯一事实源，翻译器直接读帧。

## 两方言对照（实现要点）

| 维度 | H3（h3.py，中文三字段） | Seedance 2.0（seedance.py，中文五槽） |
|---|---|---|
| 结构 | `integrated_multimodal_description` + `overall_soundscape` + `non_diegetic_music`（字段名保 wire 契约，内容中文） | 五槽线性：[参考图指代] 主体+动作 环境 景别+运镜 风格 约束尾（前置优先） |
| 分镜/时间 | `[Shot N] At mm:ss.mmm`（≤8s 单 Shot；>8s 两 Shot 切点严格递增） | 无精确秒（官方明示不支持稳定） |
| 运镜 | 中文三维语法等效（类型+幅度+速度，vocab.H3_CAMERA_TERMS） | 官方术语一镜一运镜（vocab.SEEDANCE_CAMERA_TERMS） |
| 音频 | soundscape=IR audio（D-A 一句动作耦合音效，缺则 N/A）；music 恒 N/A（BGM 管线混，音频整轨合成时丢弃） | 无音频字段（Ark generate_audio=false 兜底） |
| 约束尾 | 禁对白/无字幕水印文字（vocab.H3_CONSTRAINT_TAIL） | 保持无字幕/无水印/无文字（vocab.SEEDANCE_CONSTRAINT_TAIL） |
| 参考图 | r2v 起消费 `character`/`style_anchor` 来源（含 M4 带名指代/X8 道具措辞，`vocab.h3_reference_injection`）；`preview`/`preview_first`/`group_anchor`/None 返回空串 | `vocab.seedance_reference_injection(count, kind)` |

## 参考图指代（R1 修订 2026-08-18：按来源区分文案）

| 来源（reference_kind） | 文案要点 |
|---|---|
| `character`（角色三视图） | 角色外观参考，形象服装一致；**不得**说构图/首帧（图片1 是人物肖像）；M4 下 `entity_names` 存在时带名逐图分工（角色「{name}的三视图」/道具「{name}的产品参考图」） |
| `preview`（预览图首帧继承） | 本镜预览图作首帧参考，构图色调一致 |
| `preview_first`（M 混合形态） | 图片1=本镜预览图首帧（构图色调锚定）+ 图片2..n=角色/道具外观参考（`entity_names` 1:1 对应图片 2..n，不含预览图） |
| `group_anchor`（T-S2 组锚） | 同组锚定场景参考，场景光线一致、动作按本镜描述 |
| `style_anchor`（M5 风格锚） | 系列风格参考，只约束色调画风、非首帧（「图片N 为本系列风格参考，色调与画风保持一致」） |
| None/generic | 中性「图片1 首帧+构图参考」；≥2 张时图片1 首帧 + 其余角色外观分工 |

## 新增 provider 方言的步骤

1. `vocab.py` 加该方言的镜头/约束词表（纯数据）；
2. 新建 `{provider}.py` 实现 `translate(frame, *, reference_count=0, reference_kind=None) -> str`
   （空 IR/全空字段必须降级调 `legacy.translate`）；
3. `__init__.translator_for` 加名字路由（未知一律 legacy，不猜）；
4. 离线单测 ≥6 用例 + 变异验证（结构/术语映射/时间戳/约束尾/指代/空字段降级）。

## 关联文档

- 设计与 A/B 结论：`docs/changelog/changelog-2026-08-17-phase1-prompt-dialect.md` +
  `docs/changelog/changelog-2026-08-17-phase5-ab-materials.md`
- 摄影规则库（指令级，planner 消费）：`app/gateway/video_gen/cinematography_rules.py`
- 五风格词表：`app/gateway/video_gen/style_presets.py`（STYLE_PREFIXES + VIDEO/IMAGE_STYLE_KEYS）
