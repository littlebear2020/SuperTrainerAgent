"""Phase 1 方言层（2026-08-17）：H3/Seedance/legacy 翻译器纯函数测试。

覆盖计划 §六：每方言 ≥6 用例（结构/运镜映射/时长切点/约束尾/参考图注入/空字段降级）
+ 路由与 P0-5 不变量（无 IR/未知 provider/env legacy -> legacy 透传）+ 确定性。
"""

from __future__ import annotations

import pytest

from app.gateway.video_gen.prompt_translators import h3, legacy, seedance, translate_video_prompt
from app.gateway.video_gen.storyboard import StoryboardFrame


def _frame(**kw) -> StoryboardFrame:
    base = dict(
        index=0,
        narration="n",
        image_prompt="旧式画面描述",
        duration=5.0,
        shot_size="medium",
        camera_move="push_in",
        visual_ir={
            "subject": "一台拉丝金属质感的机械臂",
            "action": "机械臂末端拾起一枚发光的蓝色小球，小球表面泛起涟漪",
            "environment": "灰白色实验室，冷白主光从侧上方打入",
            "style": "写实3D精密工程风",
            "audio": "金属伺服电机的低鸣与球体接触的清脆声",
        },
    )
    base.update(kw)
    return StoryboardFrame(**base)


# ── H3：结构 / 三字段 / 约束尾 ──────────────────────────────────────────


def test_h3_three_field_structure():
    out = h3.translate(_frame())
    assert out.startswith("integrated_multimodal_description: ")
    assert "\noverall_soundscape: " in out
    assert out.endswith("non_diegetic_music: N/A"), "BGM 由管线混，music 恒 N/A"
    assert "金属伺服电机" in out, "IR audio 进 soundscape（D-A 写一句动作耦合音效）"


def test_h3_no_dialogue_constraint_tail():
    out = h3.translate(_frame())
    assert "无人物对白" in out and "水印" in out, "公共不变量：禁台词/对白+无字幕水印"


def test_h3_camera_three_dim_syntax():
    out = h3.translate(_frame())
    assert "缓慢" in out and "推近" in out, "push_in -> 类型+幅度+速度的中文三维语法"
    assert "中景" in out, "shot_size 映射"


def test_h3_camera_unknown_not_invented():
    out = h3.translate(_frame(camera_move="crane_shot"))
    assert "crane_shot" not in out, "未知运镜不得凭空造词（词表外忽略）"


def test_h3_single_shot_short_duration():
    out = h3.translate(_frame(duration=4.0))
    assert "[Shot 1]" in out and "[Shot 2]" not in out, "≤8s 单 Shot"


def test_h3_two_shots_long_duration():
    out = h3.translate(_frame(duration=12.0))
    assert "[Shot 1]" in out and "[Shot 2]" in out, ">8s 两 Shot"
    t1 = out.index("At 00:00.000")
    t2 = out.index("At 00:06.000")
    assert t2 > t1, "切点严格递增（12s 中点 6s）"


def test_h3_audio_missing_falls_to_na():
    f = _frame()
    f.visual_ir = {**f.visual_ir, "audio": None}
    out = h3.translate(f)
    assert "\noverall_soundscape: N/A" in out, "IR 缺 audio -> N/A（不编造）"


def test_h3_empty_ir_degrades_to_legacy():
    assert h3.translate(_frame(visual_ir=None)) == legacy.translate(_frame(visual_ir=None))


# ── Seedance：五槽 / 官方术语 / 参考图指代 ───────────────────────────────


def test_seedance_five_slot_order_and_tail():
    out = seedance.translate(_frame())
    assert out.index("机械臂") < out.index("实验室") < out.index("写实3D精密工程风"), "槽序：主体动作->环境->风格"
    assert "缓慢推镜" in out and "中景" in out, "官方运镜/景别术语"
    assert out.endswith("保持无字幕，不要生成任何水印或Logo标识，画面中不出现文字。"), "约束尾"


def test_seedance_no_reference_no_injection():
    out = seedance.translate(_frame())
    assert "图片1" not in out, "无参考图不注入指代"


def test_seedance_single_reference_injection():
    out = seedance.translate(_frame(), reference_count=1)
    assert "图片1作为首帧与构图参考" in out
    assert out.index("图片1") < out.index("机械臂"), "指代前置（位置递减服从率->前置优先）"


def test_seedance_multi_reference_role_split():
    out = seedance.translate(_frame(), reference_count=3)
    assert "图片1作为首帧与构图参考" in out and "角色外观参考" in out, "≥2 张分工：首帧+角色外观"


def test_seedance_unknown_camera_ignored():
    out = seedance.translate(_frame(camera_move=None, shot_size=None))
    assert "推镜" not in out and "中景" not in out, "缺镜头语言不造词"


def test_seedance_empty_ir_degrades_to_legacy():
    f = _frame(visual_ir={})
    assert seedance.translate(f) == legacy.translate(f)


def test_seedance_no_dialogue_words_absent():
    """Seedance generate_audio=false 参数兜底音频；提示词不含台词/音乐符号指令。"""
    out = seedance.translate(_frame())
    assert "台词" not in out and "音乐" not in out


# ── 路由与 P0-5 不量 ──────────────────────────────────────────────────────


def test_routing_h3_and_seedance_and_unknown():
    from app.gateway.video_gen.prompt_translators import translator_for

    assert translator_for("minimax-h3") is h3
    assert translator_for("MiniMax-H3") is h3
    assert translator_for("seedance") is seedance
    assert translator_for("seedance-ark") is seedance
    assert translator_for("qihu") is legacy
    assert translator_for("") is legacy


def test_translate_env_legacy_wins_over_ir(monkeypatch):
    """env=legacy -> 即使有 IR 也走 legacy（一键回滚语义）。"""
    monkeypatch.setenv("VIDEO_GEN_PROMPT_DIALECT", "legacy")
    f = _frame()
    assert translate_video_prompt(f, provider_name="minimax-h3") == legacy.translate(f)


def test_translate_default_is_v2(monkeypatch):
    """默认 v2（2026-08-18 盲评定稿切默认）--有 IR 的帧直接走方言层。"""
    monkeypatch.delenv("VIDEO_GEN_PROMPT_DIALECT", raising=False)
    f = _frame()
    assert translate_video_prompt(f, provider_name="minimax-h3") == h3.translate(f)


def test_translate_env_v2_uses_dialect(monkeypatch):
    monkeypatch.setenv("VIDEO_GEN_PROMPT_DIALECT", "v2")
    f = _frame()
    assert translate_video_prompt(f, provider_name="minimax-h3") == h3.translate(f)
    assert translate_video_prompt(f, provider_name="seedance") == seedance.translate(f)
    assert translate_video_prompt(f, provider_name="qihu") == legacy.translate(f), "未知 provider 不猜"


def test_translate_no_ir_legacy_passthrough(monkeypatch):
    monkeypatch.setenv("VIDEO_GEN_PROMPT_DIALECT", "v2")
    f = _frame(visual_ir=None)
    assert translate_video_prompt(f, provider_name="minimax-h3") == legacy.translate(f)


def test_translate_deterministic(monkeypatch):
    """逐帧 redo/重渲染确定性：同输入两次输出 byte-identical（A/B 可归因前提）。"""
    monkeypatch.setenv("VIDEO_GEN_PROMPT_DIALECT", "v2")
    f = _frame(duration=12.0)
    a = translate_video_prompt(f, provider_name="minimax-h3", reference_count=2)
    b = translate_video_prompt(f, provider_name="minimax-h3", reference_count=2)
    assert a == b


@pytest.mark.parametrize("mod", [h3, seedance])
def test_legacy_translation_unchanged_semantics(mod):
    """legacy 透传 = compose_video_prompt 原文（Task 8 拼接，含手改帧场景）。"""
    from app.gateway.video_gen.orchestration.storyboard_planner import compose_video_prompt

    f = _frame(visual_ir=None, image_prompt="用户手改的场景描述")
    assert legacy.translate(f) == compose_video_prompt(f)


# ── R1（2026-08-18 评审）：参考图指代按来源区分 ─────────────────────────


def test_reference_injection_character_kind():
    """角色三视图来源：不得说「构图/首帧参考」（图片1是人物肖像，误导模型）。"""
    out = seedance.translate(_frame(), reference_count=1, reference_kind="character")
    assert "角色外观参考" in out and "构图参考" not in out and "首帧" not in out


def test_reference_injection_preview_kind():
    out = seedance.translate(_frame(), reference_count=1, reference_kind="preview")
    assert "预览图" in out and "首帧参考" in out


def test_reference_injection_group_anchor_kind():
    out = seedance.translate(_frame(), reference_count=1, reference_kind="group_anchor")
    assert "同组锚定场景" in out and "主体动作按本镜描述" in out


def test_reference_injection_generic_backward_compatible():
    """无 kind（generic）：维持既有中性首帧+构图表述。"""
    out = seedance.translate(_frame(), reference_count=1)
    assert "图片1作为首帧与构图参考" in out


# ── 2026-09-04 H3 r2v（S3）：参考图指代声明 ──

_H3_BASELINE_5S = (
    "integrated_multimodal_description: [Shot 1] At 00:00.000，一台拉丝金属质感的机械臂。"
    "机械臂末端拾起一枚发光的蓝色小球，小球表面泛起涟漪。灰白色实验室，冷白主光从侧上方打入。"
    "写实3D精密工程风。中景。镜头以缓慢的速度小幅推近。 全程无人物对白与口型动作，画面不出现字幕、水印与任何文字。\n"
    "overall_soundscape: 金属伺服电机的低鸣与球体接触的清脆声\n"
    "non_diegetic_music: N/A"
)


def test_h3_no_injection_byte_identical_to_baseline():
    """无声明（preview/preview_first/group_anchor/None/0 张/未知 kind）输出与
    2026-09-04 r2v 改动前逐字节一致（基线字面量取自改动前实跑输出）。"""
    assert h3.translate(_frame()) == _H3_BASELINE_5S
    for kw in (
        {"reference_count": 0, "reference_kind": None},
        {"reference_count": 1, "reference_kind": "preview"},
        {"reference_count": 2, "reference_kind": "preview_first", "entity_names": ["小安"]},
        {"reference_count": 1, "reference_kind": "group_anchor"},
        {"reference_count": 1, "reference_kind": "made_up_kind"},
    ):
        assert h3.translate(_frame(), **kw) == _H3_BASELINE_5S, f"{kw} 不得注入声明"


def test_h3_character_named_injection_with_prop_wording():
    out = h3.translate(_frame(), reference_count=2, reference_kind="character", entity_names=["小安", "全息投影仪"], prop_count=1)
    assert "图片1为小安的三视图" in out and "图片2为全息投影仪的产品参考图" in out, "1:1 同序 + 道具分措辞"
    assert "仅生成被提及的角色与产品，外观与对应参考图一致" in out
    assert out.index("参考图分工") < out.index("[Shot 1]"), "声明前置进 integrated_multimodal_description 头部"
    assert out.startswith("integrated_multimodal_description: 参考图分工：")


def test_h3_character_unnamed_generic_injection():
    """非 roster（无 entity_names）的角色参考：通用外观措辞，不逐图编号。"""
    out = h3.translate(_frame(), reference_count=2, reference_kind="character")
    assert "参考图为角色外观参考" in out
    assert "图片1" not in out


def test_h3_style_anchor_injection():
    out = h3.translate(_frame(), reference_count=1, reference_kind="style_anchor")
    assert "图片1为场景/风格参考" in out
    assert "首帧" not in out, "锚图是风格参考不是首帧（A/B 实测定稿走 r2v）"


def test_dispatch_h3_elif_consumes_entity_names(monkeypatch):
    """调度层 h3 elif 分支：entity_names 正常消费（带名声明生效）。"""
    monkeypatch.setenv("VIDEO_GEN_PROMPT_DIALECT", "v2")
    out = translate_video_prompt(_frame(), provider_name="minimax-h3", reference_count=2, reference_kind="character", entity_names=["甲", "乙"])
    assert "图片1为甲的三视图" in out and "图片2为乙的三视图" in out


def test_dispatch_h3_prop_count_conditional(monkeypatch):
    """prop_count 条件传参同 seedance 规（>0 才带——窄签名兼容）。"""
    monkeypatch.setenv("VIDEO_GEN_PROMPT_DIALECT", "v2")
    out = translate_video_prompt(_frame(), provider_name="minimax-h3", reference_count=2, reference_kind="character", entity_names=["甲", "乙"], prop_count=1)
    assert "图片2为乙的产品参考图" in out


def test_dispatch_legacy_still_no_passthrough(monkeypatch):
    """窄签名保护：legacy 仍不透传 entity_names（不崩、输出等价窄调用）。"""
    monkeypatch.setenv("VIDEO_GEN_PROMPT_DIALECT", "v2")
    f = _frame()
    out = translate_video_prompt(f, provider_name="qihu", reference_count=1, reference_kind="character", entity_names=["甲"])
    assert out == legacy.translate(f, reference_count=1, reference_kind="character")


def test_seedance_named_path_unchanged_by_h3_branch(monkeypatch):
    """红线锁：新增 h3 elif 不得影响 seedance 带名路径（两方言独立维护）。
    seedance 收尾「外观与服装保持…」与 h3「外观与对应参考图一致」刻意不同。"""
    monkeypatch.setenv("VIDEO_GEN_PROMPT_DIALECT", "v2")
    out = translate_video_prompt(_frame(), provider_name="seedance", reference_count=2, reference_kind="character", entity_names=["甲", "乙"], prop_count=1)
    assert "图片1为甲的三视图" in out and "图片2为乙的产品参考图" in out
    assert "外观与服装保持与对应参考图一致" in out
