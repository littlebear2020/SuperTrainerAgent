"""F5 守护测试（2026-09-29 长任务中断修复计划，提案开放问题 #4 裁决采纳）。

auto-video-flow 的长片渲染纪律是 09-28 事故（长视频任务反复中断）的技能层
减损防线——分段渲染 / 后台轮询 / 失败重试配方 / 静默超时纪律。技能文档由 LLM
执行、bind mount 即时生效，一次"顺手精简"就可能整节蒸发且零测试信号。本测试
钉住关键锚点：误删任何一条，先在这里红。

同口味先例：test_skill_script_copies.py（md5 守护）。锚点口径与 SKILL.md
2026-09-29 版对齐；改锚点措辞须同步改这里（intentional，走 review）。
"""

from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL_MD = REPO_ROOT / "skills" / "public" / "auto-video-flow" / "SKILL.md"
GOTCHAS_MD = REPO_ROOT / "skills" / "public" / "auto-video-flow" / "references" / "gotchas.md"

# F5-a/b/c/d/e 锚点（P-1「轮询命令须带微小变化」为三轮评审补记，单独列出防漂移）
SKILL_ANCHORS = (
    "长片渲染纪律",
    "--frames=",
    "后台执行 + 轮询",
    "Target closed",
    "静默超过 300 秒",
    "轮询命令须带微小变化",
    "渲染失败重试配方",
    "渲染前资源自检",
    "nohup",
    "--concurrency=1",
    "memory.usage_in_bytes",
)
# 长片纪律的具体数值条款
SKILL_NUMERIC_ANCHORS = (
    "14,400 帧",
    "5,000-6,000 帧",
    "sleep 180",
)
GOTCHAS_ANCHORS = (
    "Target closed",
    "chrome-headless",
)


def test_skill_md_exists():
    assert SKILL_MD.exists(), f"SKILL.md missing: {SKILL_MD}"
    assert GOTCHAS_MD.exists(), f"gotchas.md missing: {GOTCHAS_MD}"


@pytest.mark.parametrize("anchor", SKILL_ANCHORS, ids=lambda a: a)
def test_skill_md_contains_anchor(anchor: str):
    content = SKILL_MD.read_text(encoding="utf-8")
    assert anchor in content, f"SKILL.md 缺锚点 {anchor!r}——长片渲染纪律被误删/漂移？"


@pytest.mark.parametrize("anchor", SKILL_NUMERIC_ANCHORS, ids=lambda a: a)
def test_skill_md_contains_numeric_clauses(anchor: str):
    content = SKILL_MD.read_text(encoding="utf-8")
    assert anchor in content, f"SKILL.md 缺数值条款 {anchor!r}"


@pytest.mark.parametrize("anchor", GOTCHAS_ANCHORS, ids=lambda a: a)
def test_gotchas_md_contains_anchor(anchor: str):
    content = GOTCHAS_MD.read_text(encoding="utf-8")
    assert anchor in content, f"gotchas.md 缺锚点 {anchor!r}——Target closed 条目被误删？"


def test_polling_sleep_upper_bound_below_silence_kill_line():
    """F5-b（二轮评审修正）：轮询 sleep 上限必须 < 300s 静默击杀线——硬上限 240s。"""
    content = SKILL_MD.read_text(encoding="utf-8")
    assert re.search(r"硬上限\s*240\s*s\b", content), "SKILL.md 丢失 240s 硬上限表述"
    assert "sleep 300" not in content, "轮询命令出现 sleep 300——自踩 no_change_timeout 击杀线"


def test_tts_redline_sentence_untouched():
    """红线句必须仍在前置要求区（改写/漂移会破坏 doubao-only 纪律）。"""
    content = SKILL_MD.read_text(encoding="utf-8")
    assert "沙箱内只用豆包 TTS" in content
    assert "edge-tts 必失败" in content


# ---------------------------------------------------------------------------
# 视觉质量批锚点（2026-09-29 auto-video-flow 视觉质量计划 Batch A，计划 §6）
# 与上方 F5 锚点互相独立；锚点措辞与 SKILL.md / VideoTemplate.tsx 同源维护
# （锚点选取原则：≥4 字、无符号、语义独特）。Batch B（B1/B4）落地后按计划追加。
# ---------------------------------------------------------------------------

VIDEO_TEMPLATE_TSX = REPO_ROOT / "skills" / "public" / "auto-video-flow" / "references" / "VideoTemplate.tsx"

# SKILL.md 视觉流程锚点：分镜表（第一步）、静帧预览门（第四步）、
# 动效密度/屏显与旁白分离（美学规则）、逐句入场（第三步默认要求）
VISUAL_QUALITY_SKILL_ANCHORS = (
    "分镜表",
    "静帧预览门",
    "动效密度",
    "屏显与旁白分离",
    "逐句入场",
)

# VideoTemplate.tsx 防回退锚点：模板回退为静态样板（E3 形态）时在此先红
VIDEO_TEMPLATE_ANCHORS = (
    "Reveal",
    "at={",
    "useCurrentFrame",
)


def test_video_template_exists():
    assert VIDEO_TEMPLATE_TSX.exists(), f"VideoTemplate.tsx missing: {VIDEO_TEMPLATE_TSX}"


@pytest.mark.parametrize("anchor", VISUAL_QUALITY_SKILL_ANCHORS, ids=lambda a: a)
def test_visual_quality_skill_md_contains_anchor(anchor: str):
    content = SKILL_MD.read_text(encoding="utf-8")
    assert anchor in content, f"SKILL.md 缺视觉质量锚点 {anchor!r}——Batch A 流程被误删/漂移？"


@pytest.mark.parametrize("anchor", VIDEO_TEMPLATE_ANCHORS, ids=lambda a: a)
def test_video_template_contains_anchor(anchor: str):
    content = VIDEO_TEMPLATE_TSX.read_text(encoding="utf-8")
    assert anchor in content, f"VideoTemplate.tsx 缺锚点 {anchor!r}——模板回退为低动效静态样板（E3）？"


# ---------------------------------------------------------------------------
# B6（2026-10-08 auto-video-flow 视觉质量 Batch B）：generate_audio_template.py
# 词级时间戳 mock 单测——钉 captions 形状（@remotion/captions 契约，消费端
# templates/remotion-base/src/KaraokeCaptions.tsx 的 WordCaption）与全局毫秒换算
# （SKILL.md 第二步：startMs = round((句全局起始秒 + word.startTime) * 1000)），
# 以及词级失败降级后 segments/segments_global 输出形状不变。live 合成需凭证，非必须。
# ---------------------------------------------------------------------------

AUDIO_TEMPLATE_PY = REPO_ROOT / "skills" / "public" / "auto-video-flow" / "scripts" / "generate_audio_template.py"


def _load_audio_template():
    """把模板脚本 import 为无副作用的模块（main() 由 __main__ 守卫，import 只定义常量与函数）。"""
    spec = importlib.util.spec_from_file_location("generate_audio_template_under_test", AUDIO_TEMPLATE_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _configure_template(mod, tmp_path: Path, scene_texts: list) -> None:
    """把模块配置指向 tmp_path（main() 的全部落盘不越界），并注入最小场景数据。"""
    mod.VIDEO_NAME = "unittest"
    mod.AUDIO_DIR = str(tmp_path / "audio")
    mod.TMP_DIR = str(tmp_path / "tmp")
    mod.SCENE_TEXTS = scene_texts
    mod.NAMES = [f"s{index}" for index in range(len(scene_texts))]


def _forbid_real_doubao(mod, monkeypatch: pytest.MonkeyPatch) -> None:
    """hermetic tripwire（2026-10-12 CI 假绿教训）：本组测试只 mock 模板内 helper——
    任何未被拦截而落到真实 `_doubao_tts.py` 外呼的路径（本地有 key 时会静默打
    真实付费 API 而"绿"，CI 无 key 才红——正是当日 CI 红的根因形态）必须当场炸响。"""

    def _tripwire(*args, **kwargs):
        raise AssertionError("真实豆包 TTS 外呼未拦截干净（stub 签名漂移或降级路径漏 mock）——hermetic tripwire")

    monkeypatch.setattr(mod, "_load_doubao_module", _tripwire)


def test_words_to_captions_uses_global_ms_contract():
    """纯换算函数：session 相对秒 + 句全局起始秒 → 全局毫秒，形状对齐 WordCaption。"""
    mod = _load_audio_template()
    words = [
        {"word": "大家好", "startTime": 0.0, "endTime": 0.52, "confidence": 0.98},
        {"word": "今天", "startTime": 0.6, "endTime": 0.81, "confidence": 0.95},
    ]

    captions = mod.words_to_captions(words, sentence_global_start_sec=12.5)

    assert captions == [
        {"text": "大家好", "startMs": 12500, "endMs": 13020, "timestampMs": 12500, "confidence": 0.98},
        {"text": "今天", "startMs": 13100, "endMs": 13310, "timestampMs": 13100, "confidence": 0.95},
    ]


def test_main_emits_global_ms_captions_and_keeps_segments(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """全链路（mock 合成）：captions 跨场景全局毫秒正确 + segments/segments_global 形状不变。

    场景0 两句（ffprobe 实测 1.0s/0.9s → 87 帧），场景1 一句（1.2s → 66 帧）。
    逐句合成 session 相对秒必须叠上：场景起点（Σ 前序场景帧 / fps）+ 句内偏移（前序句时长和）。
    """
    mod = _load_audio_template()
    _configure_template(mod, tmp_path, [["甲句。", "乙句。"], ["丙句。"]])

    sentence_words = {
        "甲句。": [{"word": "甲句。", "startTime": 0.0, "endTime": 0.8, "confidence": 1.0}],
        "乙句。": [
            {"word": "乙", "startTime": 0.1, "endTime": 0.4, "confidence": 0.9},
            {"word": "句。", "startTime": 0.4, "endTime": 0.7, "confidence": 0.9},
        ],
        "丙句。": [{"word": "丙句。", "startTime": 0.05, "endTime": 1.1, "confidence": 0.99}],
    }
    sentence_durations = {"甲句。": 1.0, "乙句。": 0.9, "丙句。": 1.2}

    def fake_timestamped(text: str, output_path, speaker=None) -> list:
        # speaker：C1（2026-10-12）起模板显式透传音色，stub 同步接受
        Path(output_path).write_bytes(text.encode("utf-8"))
        return [dict(word) for word in sentence_words[text]]

    def fake_get_mp3_duration(path) -> float:
        # 单句文件 = 该句时长；场景 concat 文件 = 所含各句时长之和
        text = Path(path).read_bytes().decode("utf-8")
        return sum(dur for known, dur in sentence_durations.items() if known in text)

    def fake_concat(mp3_files, output_path) -> None:
        Path(output_path).write_bytes(b"".join(Path(p).read_bytes() for p in mp3_files))

    monkeypatch.setattr(mod, "_doubao_available", lambda: True)
    monkeypatch.setattr(mod, "_doubao_tts_generate_timestamped", fake_timestamped)
    monkeypatch.setattr(mod, "get_mp3_duration", fake_get_mp3_duration)
    monkeypatch.setattr(mod, "concat_mp3s", fake_concat)
    _forbid_real_doubao(mod, monkeypatch)
    monkeypatch.chdir(tmp_path)

    output = mod.main()

    # 既有输出形状不变（现状契约：segments=场景内局部帧、segments_global=全局帧，
    # 两套段别口径见 SKILL.md 第二步——scene 1 局部帧从 0 重计）
    assert output["durations"] == [87, 66]
    assert output["segments"] == [
        [0, 0, 30, "甲句。"],
        [0, 30, 57, "乙句。"],
        [1, 0, 36, "丙句。"],
    ]
    assert output["segments_global"] == [
        [0, 0, 30, "甲句。"],
        [0, 30, 57, "乙句。"],
        [1, 87, 123, "丙句。"],
    ]

    # captions：词级全局毫秒（@remotion/captions 形状），场景1 起点 = 87/30 = 2.9s
    assert output["captions"] == [
        {"text": "甲句。", "startMs": 0, "endMs": 800, "timestampMs": 0, "confidence": 1.0},
        {"text": "乙", "startMs": 1100, "endMs": 1400, "timestampMs": 1100, "confidence": 0.9},
        {"text": "句。", "startMs": 1400, "endMs": 1700, "timestampMs": 1400, "confidence": 0.9},
        {"text": "丙句。", "startMs": 2950, "endMs": 4000, "timestampMs": 2950, "confidence": 0.99},
    ]

    # 落盘 JSON 与返回值一致（文件才是真实交付物）
    saved = json.loads((tmp_path / "scripts" / "tts_unittest.json").read_text(encoding="utf-8"))
    assert saved == output


def test_word_level_failure_degrades_and_keeps_segments(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """词级合成失败 → 该句降级普通合成，整体不发 captions 字段；segments 照常输出。"""

    def failing_timestamped(text: str, output_path) -> list:
        raise RuntimeError("doubao TTS session failed (code=1): mock")

    def fake_plain(text: str, output_path, speaker=None) -> bool:
        # speaker：C1（2026-10-12）起降级路径同样显式透传音色，stub 同步接受
        Path(output_path).write_bytes(text.encode("utf-8"))
        return True

    def fake_concat(mp3_files, output_path) -> None:
        Path(output_path).write_bytes(b"".join(Path(p).read_bytes() for p in mp3_files))

    mod = _load_audio_template()
    _configure_template(mod, tmp_path, [["甲句。"]])
    monkeypatch.setattr(mod, "_doubao_available", lambda: True)
    monkeypatch.setattr(mod, "_doubao_tts_generate_timestamped", failing_timestamped)
    monkeypatch.setattr(mod, "_doubao_tts_generate", fake_plain)
    monkeypatch.setattr(mod, "get_mp3_duration", lambda path: 1.0)
    monkeypatch.setattr(mod, "concat_mp3s", fake_concat)
    _forbid_real_doubao(mod, monkeypatch)
    monkeypatch.chdir(tmp_path)

    output = mod.main()

    assert "captions" not in output
    assert output["segments"] == [[0, 0, 30, "甲句。"]]
    assert output["segments_global"] == [[0, 0, 30, "甲句。"]]
    saved = json.loads((tmp_path / "scripts" / "tts_unittest.json").read_text(encoding="utf-8"))
    assert "captions" not in saved


def test_empty_words_degrades_without_captions(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """无 subtitle event（cache_config 命中 / ssml）→ 音频正常、words 空 → 不发 captions。"""

    def fake_timestamped_no_subtitle(text: str, output_path, speaker=None) -> list:
        # speaker：C1（2026-10-12）起模板显式透传音色，stub 同步接受
        Path(output_path).write_bytes(text.encode("utf-8"))
        return []

    def fake_concat(mp3_files, output_path) -> None:
        Path(output_path).write_bytes(b"".join(Path(p).read_bytes() for p in mp3_files))

    mod = _load_audio_template()
    _configure_template(mod, tmp_path, [["甲句。"]])
    monkeypatch.setattr(mod, "_doubao_available", lambda: True)
    monkeypatch.setattr(mod, "_doubao_tts_generate_timestamped", fake_timestamped_no_subtitle)
    monkeypatch.setattr(mod, "get_mp3_duration", lambda path: 1.0)
    monkeypatch.setattr(mod, "concat_mp3s", fake_concat)
    _forbid_real_doubao(mod, monkeypatch)
    monkeypatch.chdir(tmp_path)

    output = mod.main()

    assert "captions" not in output
    assert output["segments"] == [[0, 0, 30, "甲句。"]]


def test_malformed_words_degrade_instead_of_crashing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """畸形 word dict（缺时间键/非数值）→ 词级换算降级而非崩溃——音频已全量合成完，
    崩在换算步会丢掉整份 tts JSON 与既有 segments 输出（B6 降级契约：失败不破坏现状输出）。"""

    def fake_timestamped_malformed(text: str, output_path, speaker=None) -> list:
        # speaker：C1（2026-10-12）起模板显式透传音色，stub 同步接受
        Path(output_path).write_bytes(text.encode("utf-8"))
        return [
            {"word": "正常词", "startTime": 0.0, "endTime": 0.5, "confidence": 1.0},
            {"word": "缺时间键"},
            {"word": "坏值", "startTime": "not-a-number", "endTime": 0.9, "confidence": 1.0},
        ]

    def fake_concat(mp3_files, output_path) -> None:
        Path(output_path).write_bytes(b"".join(Path(p).read_bytes() for p in mp3_files))

    mod = _load_audio_template()
    _configure_template(mod, tmp_path, [["甲句。"]])
    monkeypatch.setattr(mod, "_doubao_available", lambda: True)
    monkeypatch.setattr(mod, "_doubao_tts_generate_timestamped", fake_timestamped_malformed)
    monkeypatch.setattr(mod, "get_mp3_duration", lambda path: 1.0)
    monkeypatch.setattr(mod, "concat_mp3s", fake_concat)
    _forbid_real_doubao(mod, monkeypatch)
    monkeypatch.chdir(tmp_path)

    output = mod.main()  # 不得抛异常

    assert "captions" not in output
    assert output["segments"] == [[0, 0, 30, "甲句。"]]
    saved = json.loads((tmp_path / "scripts" / "tts_unittest.json").read_text(encoding="utf-8"))
    assert "captions" not in saved
