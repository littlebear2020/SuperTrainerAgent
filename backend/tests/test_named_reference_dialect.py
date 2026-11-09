"""二期 M4：带名指代方言（TC-M4-01/02）。

带名消费点：seedance（vocab.seedance_reference_injection ← seedance.translate
← translate_video_prompt）+ h3（2026-09-04 r2v 计划 S3 起，vocab.h3_reference_injection
← h3.translate ← translate_video_prompt 的 elif h3 分支）；legacy 方言保持不消费
entity_names（窄签名保护）。文案模板为计划定稿，逐字断言防漂移。
"""

from __future__ import annotations

from app.gateway.video_gen.prompt_translators import translate_video_prompt
from app.gateway.video_gen.prompt_translators.vocab import seedance_reference_injection
from app.gateway.video_gen.storyboard import StoryboardFrame

# 现状文案快照（TC-M4-02 逐字一致断言的基准——改文案必须过计划，不许顺手改）
_LEGACY_CHARACTER = "参考图为角色外观参考，角色形象与服装保持一致，场景与动作按本镜描述生成。"
_LEGACY_PREVIEW = "图片1为本镜预览图，作为首帧参考，构图与色调保持一致。"
_LEGACY_GROUP_ANCHOR = "图片1为同组锚定场景参考，场景与光线保持一致，主体动作按本镜描述执行。"
_LEGACY_GENERIC_1 = "图片1作为首帧与构图参考，画面主体保持一致。"
_LEGACY_GENERIC_2 = "图片1作为首帧与构图参考，图片2及以后作为角色外观参考，各参考图分工使用，画面主体保持一致。"


def _ir_frame() -> StoryboardFrame:
    """带 visual_ir 的帧（方言层走 seedance 五槽而非 legacy 透传）。"""
    return StoryboardFrame(
        index=0,
        narration="n",
        duration=5.0,
        image_prompt="p",
        visual_ir={"subject": "一位讲师", "action": "讲解产品", "environment": "教室", "style": "写实"},
    )


# ── TC-M4-01：带名文案 ─────────────────────────────────────────────────────


def test_tc_m4_01_named_copy_contains_per_image_references():
    s = seedance_reference_injection(2, "character", entity_names=["角色A", "产品B"], prop_count=1)
    assert "图片1为角色A的三视图" in s, f"文案须含「图片1为角色A的三视图」，实际：{s}"
    assert "图片2为产品B的产品参考图" in s, f"文案须含「图片2为产品B的产品参考图」，实际：{s}"


def test_tc_m4_01_named_copy_exact_template():
    """定稿模板逐字断言（计划 M4 卡文案 + 2026-08-27 X8 合成图方案修订——
    角色「三视图」（注入合成图）/道具「产品参考图」（注入单张主图）区分措辞）。"""
    s = seedance_reference_injection(2, "character", entity_names=["角色A", "产品B"], prop_count=1)
    assert s == ("参考图分工：图片1为角色A的三视图，图片2为产品B的产品参考图。仅生成被提及的角色与产品，外观与服装保持与对应参考图一致，场景与动作按本镜描述生成。")


def test_named_branch_only_for_character_kind():
    """带名分支仅 kind="character" 触发：preview/group_anchor 传 names 不生效。"""
    assert seedance_reference_injection(1, "preview", entity_names=["A"]) == _LEGACY_PREVIEW
    assert seedance_reference_injection(1, "group_anchor", entity_names=["A"]) == _LEGACY_GROUP_ANCHOR


def test_named_defensive_slice_to_reference_count():
    """names 长于参考图张数（调用方错位）→ 截到张数，不虚报不存在的图片序号。"""
    s = seedance_reference_injection(1, "character", entity_names=["A", "B"])
    assert "图片1为A" in s
    assert "图片2为B" not in s


# ── TC-M4-02：兼容（names 缺省 → 现状文案逐字一致）─────────────────────────


def test_tc_m4_02_names_none_output_identical_to_current():
    """names=None → 全部分支输出与现状逐字一致（向后兼容铁律）。"""
    assert seedance_reference_injection(0, "character", entity_names=None) == ""
    assert seedance_reference_injection(3, "character") == _LEGACY_CHARACTER
    assert seedance_reference_injection(3, "character", entity_names=None) == _LEGACY_CHARACTER
    assert seedance_reference_injection(1, "preview") == _LEGACY_PREVIEW
    assert seedance_reference_injection(1, "group_anchor") == _LEGACY_GROUP_ANCHOR
    assert seedance_reference_injection(1) == _LEGACY_GENERIC_1
    assert seedance_reference_injection(2) == _LEGACY_GENERIC_2


def test_tc_m4_02_empty_names_list_falls_back_to_current():
    """entity_names=[]（明确空）≠ 带名模式 → 现状文案（无「参考图分工：」前缀）。"""
    assert seedance_reference_injection(2, "character", entity_names=[]) == _LEGACY_CHARACTER


# ── translate_video_prompt 透传（方言层入口）──────────────────────────────


def test_translate_video_prompt_passes_names_to_seedance_dialect():
    """入口层：entity_names 经 translate_video_prompt → seedance 五槽（端到端带名）。
    X8：prop_count 一并透传（角色/道具区分措辞）。"""
    s = translate_video_prompt(
        _ir_frame(),
        provider_name="seedance",
        reference_count=2,
        reference_kind="character",
        entity_names=["角色A", "产品B"],
        prop_count=1,
    )
    assert "图片1为角色A的三视图，图片2为产品B的产品参考图" in s
    assert "仅生成被提及的角色与产品" in s


def test_translate_video_prompt_without_names_seedance_unchanged():
    """入口层：不传 names → seedance 方言输出与现状一致（首槽=现状 character 文案）。"""
    s = translate_video_prompt(_ir_frame(), provider_name="seedance", reference_count=3, reference_kind="character")
    assert s.startswith(_LEGACY_CHARACTER)


def test_entity_names_consumed_by_h3_but_not_legacy():
    """2026-09-04 r2v 计划 S3 起 h3 也消费 entity_names：带名 → 指代声明生效，
    输出 ≠ 不带名（M4 旧「h3 不消费」边界由该计划刻意打破）。legacy（无 IR）
    仍不透传：传与不传输出一致、不崩。"""
    fr = _ir_frame()
    a = translate_video_prompt(fr, provider_name="minimax-h3", reference_count=2, reference_kind="character", entity_names=["A", "B"])
    b = translate_video_prompt(fr, provider_name="minimax-h3", reference_count=2, reference_kind="character")
    assert a != b, "h3 带名 → 注入指代声明（S3 起消费）"
    assert "图片1为A的三视图" in a

    no_ir = StoryboardFrame(index=0, narration="旁白", duration=5.0, image_prompt="p")
    c = translate_video_prompt(no_ir, provider_name="seedance", reference_count=2, reference_kind="character", entity_names=["A"])
    d = translate_video_prompt(no_ir, provider_name="seedance", reference_count=2, reference_kind="character")
    assert c == d, "无 IR → legacy 透传，entity_names 不得引发崩溃或文案变化"
