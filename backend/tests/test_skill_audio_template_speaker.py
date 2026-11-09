"""C1/C3（2026-10-12 配音音色加固批）：技能 TTS 模板必须显式携带 speaker 音色参数。

事故背景（2026-10-12 FDE 课程视频）：模板裸调 ``synthesize(text)`` → 音色静默落
``DOUBAO_TTS_DEFAULT_SPEAKER`` env（女声），agent 照模板写代码物理上不可能产出
男声 → 成片女声 + 假注释谎称男声。修复 = 模板恢复显式 ``SPEAKER`` 常量 +
``speaker`` 形参全链透传（不设 None 兜底——None = 静默落 env，正是事故形态）。

本测试 importlib 按路径加载两个模板模块，只断言签名/常量形态，**不发起任何
真实 TTS 调用**（离线可跑，CI 假绿口径同 conftest hermetic_tts 约定）。
"""

import importlib.util
import inspect
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
AVF_TEMPLATE = REPO_ROOT / "skills" / "public" / "auto-video-flow" / "scripts" / "generate_audio_template.py"
CORP_TEMPLATE = REPO_ROOT / "skills" / "public" / "corporate-video" / "scripts" / "generate_tts.py"


def _load(path: Path, name: str):
    """按路径加载技能脚本模块（技能脚本不在 backend 包内，无真实调用副作用）。"""
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _assert_registered_male_doubao(speaker: str) -> None:
    """SPEAKER 常量必须在 voice_catalog 在册、provider=doubao、gender=male。

    防的是「凭记忆编造 speaker id」——2026-10-12 事故会话 1 的
    "resource ID is mismatched" 即 agent 测了未授权音色 id。
    """
    from app.gateway._shared.voice_catalog import CURATED_VOICES

    matches = [v for v in CURATED_VOICES if v.id == speaker or v.id.endswith(f":{speaker}")]
    assert matches, f"SPEAKER={speaker!r} 不在 voice_catalog.CURATED_VOICES 在册清单（勿凭记忆编造音色 id）"
    v = matches[0]
    assert str(v.provider) == "doubao", f"SPEAKER={speaker!r} 在册但非豆包音色（provider={v.provider}）"
    assert v.gender == "male", f"SPEAKER={speaker!r} 在册但非男声（gender={v.gender}）"


def _assert_speaker_param(mod, *fn_names: str) -> None:
    """每个命名函数都必须有 speaker 形参，且默认值 = SPEAKER 常量（非 None）。"""
    for fn_name in fn_names:
        fn = getattr(mod, fn_name, None)
        assert callable(fn), f"{mod.__name__}.{fn_name} 不存在（模板结构漂移？）"
        sig = inspect.signature(fn)
        assert "speaker" in sig.parameters, f"{fn_name} 缺 speaker 形参——模板裸调 = 音色静默落 env 默认（2026-10-12 事故根因）"
        default = sig.parameters["speaker"].default
        assert default is not None and default == mod.SPEAKER, f"{fn_name}.speaker 默认值必须是 SPEAKER 常量而非 None——None 兜底 = 静默回落 env 默认女声，正是事故形态"


# ── C1: auto-video-flow/generate_audio_template.py ─────────────────────────


def test_avf_speaker_constant_is_registered_male_doubao():
    mod = _load(AVF_TEMPLATE, "avf_audio_template_speaker_test")
    assert hasattr(mod, "SPEAKER"), "模板缺 SPEAKER 常量（CONFIG 区应有显式默认音色）"
    _assert_registered_male_doubao(mod.SPEAKER)


def test_avf_tts_functions_carry_speaker():
    """generate_tts / generate_tts_timestamped / 两个内部 helper 全链透传 speaker。"""
    mod = _load(AVF_TEMPLATE, "avf_audio_template_speaker_test2")
    _assert_speaker_param(
        mod,
        "generate_tts",
        "generate_tts_timestamped",
        "_doubao_tts_generate",
        "_doubao_tts_generate_timestamped",
    )


# ── C3: corporate-video/generate_tts.py（同构缺陷，同法修复）────────────────


def test_corporate_speaker_constant_is_registered_male_doubao():
    mod = _load(CORP_TEMPLATE, "corp_tts_speaker_test")
    assert hasattr(mod, "SPEAKER"), "模板缺 SPEAKER 常量（CONFIG 区应有显式默认音色）"
    _assert_registered_male_doubao(mod.SPEAKER)


def test_corporate_tts_functions_carry_speaker():
    mod = _load(CORP_TEMPLATE, "corp_tts_speaker_test2")
    _assert_speaker_param(mod, "tts_one", "_doubao_tts_one")
