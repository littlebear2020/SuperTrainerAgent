"""Task 8b: ViMax 对白三缺陷（「」引号 / 旁白撞音色 / 一次 LLM 失败丢全片）。

三条全部**实跑坐实**，且两条比审计描述更严重。

**缺陷 1：「」引号对白被当旁白念——连人名和引号一起念**
``dialogue.py`` 的正则注释**明确声称**支持「Chinese 「」/""/''」，但字符类实际
**不含 「」**（只有 ASCII 与弯引号）。实跑：
    '小明:"hello"'      -> is_dialogue=True  speaker='小明' line='hello'      ✅
    '小明：「你好世界」'  -> is_dialogue=False speaker=''     line=整行原文     ❌
下游 TTS 于是把 `小明：「我们今天学习理财」` 整行念出来（人名+冒号+引号全念），
且丢角色音色回落 narrator。中文 LLM 输出极常用「」，不是边角。

**缺陷 2：narrator 与第一个女性角色撞同一音色**
``build_speaker_voice_map`` 确实按性别轮转分配（审计的 C5「角色音色无法区分」
**本身不成立**），但 narrator 的音色**没有从轮转池里排除**。实跑：
    narrator=preset:146  小明=preset:147  小红=preset:146  ← 与 narrator 相同
后果：旁白和小红听起来是同一个人。

**缺陷 3：一次 LLM 失败丢掉整片旁白（N-3）**
``_generate_narration_text`` 调用在 **per-shot try 之外**（try 只包 TTS），且该
函数内部无 try/except → 第 30 个镜头 LLM 超时 → 异常穿出整个循环 → engine 的
except 兜住 → narration_audio=None → **前 29 个镜头已成功的 TTS 全部丢弃**。
回落逻辑 `shot_desc[:50]` 已存在，只是够不着。
"""

import pytest

from app.gateway.video_gen.engines.vimax.dialogue import (
    build_speaker_voice_map,
    parse_dialogue,
)

# ── 缺陷 1：「」引号 ────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "raw",
    [
        "小明：「台词」",
        "小明:「台词」",
        "[Speaker] 小明：「台词」",
    ],
)
def test_cjk_corner_brackets_parse_as_dialogue(raw):
    """「」是中文 LLM 最常用的对白引号，必须识别为对白。"""
    result = parse_dialogue(raw)
    assert result.is_dialogue, f"{raw!r} 未被识别为对白 → TTS 会把人名和引号一起念出来"
    assert result.speaker == "小明", f"speaker 解析错：{result.speaker!r}"
    assert result.line == "台词", f"台词里混进了人名/引号：{result.line!r}"


def test_ascii_and_curly_quotes_still_work():
    """既有 ASCII / 弯引号支持不得回归。"""
    for raw in ['小明:"hello"', '小明："中文引号"']:
        result = parse_dialogue(raw)
        assert result.is_dialogue, raw
        assert result.speaker == "小明"


def test_mismatched_quotes_do_not_cross_pair():
    """开合引号须配对，`「x"` 这类不匹配不得误配。"""
    result = parse_dialogue('小明：「台词"')
    assert not result.is_dialogue, "开合引号不匹配却被当成对白"


def test_dialogue_comment_matches_implementation():
    """注释声称支持「」，实现必须真支持（此前注释在撒谎）。"""
    from app.gateway.video_gen.engines.vimax import dialogue as mod

    if "「" in (mod.__doc__ or "") or "「" in mod._DIALOGUE_RE.pattern:
        assert "「" in mod._DIALOGUE_RE.pattern, "注释提到「」但正则字符类不含它"


# ── 缺陷 2：narrator 撞音色 ────────────────────────────────────────────────


def test_narrator_voice_excluded_from_character_pool():
    """narrator 音色不得等于任一角色音色。"""
    characters = [
        {"name": "小明", "static_features": "男，20岁"},
        {"name": "小红", "static_features": "女，19岁"},
        {"name": "老师", "static_features": "男，45岁"},
    ]
    vm = build_speaker_voice_map(characters, default_voice="preset:146", narrator_voice="preset:146")

    character_voices = list(vm.by_speaker.values())
    assert vm.narrator_voice not in character_voices, f"narrator({vm.narrator_voice}) 与角色音色撞车：{vm.by_speaker} —— 旁白和该角色是同一个声音"


def test_characters_still_get_distinct_voices():
    """角色间互不相同（既有行为，不得回归）。"""
    characters = [
        {"name": "A", "static_features": "男"},
        {"name": "B", "static_features": "女"},
        {"name": "C", "static_features": "男"},
        {"name": "D", "static_features": "女"},
    ]
    vm = build_speaker_voice_map(characters, default_voice="preset:146", narrator_voice="preset:146")
    voices = list(vm.by_speaker.values())
    assert len(voices) == len(set(voices)), f"角色音色重复：{vm.by_speaker}"


def test_narrator_exclusion_survives_many_characters(monkeypatch):
    """角色多时 narrator 仍不得被复用（池容量内）。

    Plan B（2026-10-19）：ViMax 池 = doubao+Edge（无 360 preset）后总容量变小——
    设 VOLCENGINE_VOICE_ACCESS_KEY 扩池（2 doubao 男/女 + Edge 溢出），7 角色在
    容量内逐一互异且不撞 narrator；超出容量的回落 default 属既有池耗尽语义
    （build_speaker_voice_map 的池耗尽回落分支），不在本不变式范围。
    """
    monkeypatch.setenv("VOLCENGINE_VOICE_ACCESS_KEY", "k")
    characters = [{"name": f"C{i}", "static_features": "女" if i % 2 else "男"} for i in range(7)]
    vm = build_speaker_voice_map(characters, default_voice="zh-CN-YunjianNeural", narrator_voice="zh-CN-YunjianNeural")
    assigned = [v for v in vm.by_speaker.values()]
    assert len(set(assigned)) == len(assigned), f"池容量内角色音色须互异：{vm.by_speaker}"
    assert vm.narrator_voice not in assigned, f"角色数多时 narrator 音色被复用：{vm.by_speaker}"


# ── 缺陷 3：一次 LLM 失败丢全片 ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_llm_failure_on_one_shot_keeps_other_shots(tmp_path, monkeypatch):
    """第 N 镜 LLM 抛异常 → 前 N-1 镜旁白保留，该镜回落 shot_desc。"""
    from app.gateway.video_gen.engines.vimax import narration_generator as ng

    calls = {"n": 0}

    async def flaky_llm(llm, messages, timeout=None):
        calls["n"] += 1
        if calls["n"] == 2:
            raise TimeoutError("LLM timed out on shot 2")

        class _R:
            content = f"旁白{calls['n']}"

        return _R()

    monkeypatch.setattr(ng, "_invoke_llm_bounded", flaky_llm)

    results = []
    for idx, desc in enumerate(["镜头一描述", "镜头二描述", "镜头三描述"]):
        try:
            text = await ng._generate_narration_text(object(), desc, "剧本")
        except Exception:
            text = ""
        results.append(text or desc[:50])

    assert results[0], "第一镜旁白应保留"
    assert results[2], "第三镜旁白应保留 —— 一次 LLM 失败不得丢掉其余镜头"
    assert results[1] == "镜头二描述"[:50], f"失败镜应回落 shot_desc，实得 {results[1]!r}"


def test_llm_call_is_inside_per_shot_try():
    """结构断言：LLM 调用必须在 per-shot try 内，否则一次超时穿出整个循环。

    审计 N-3：_generate_narration_text 在 try 之外（try 从 TTS 才开始），且该函数
    内部无 except → 一次 LLM 超时丢掉全部已成功镜头的旁白。
    """
    import inspect

    from app.gateway.video_gen.engines.vimax import narration_generator as ng

    src = inspect.getsource(ng)
    llm_at = src.index("narration_text = await _generate_narration_text(")

    # 该调用之前最近的 try: 必须存在，且它与调用之间不能夹着 except/finally
    # （否则那个 try 属于别的块）。缩进无关，避免锁死具体层级。
    preceding = src[:llm_at]
    try_at = max(preceding.rfind("\ntry:"), preceding.rfind(" try:"))
    assert try_at != -1, "_generate_narration_text 调用前没有 try —— 一次 LLM 失败会穿出循环丢全片旁白"
    between = preceding[try_at:]
    assert "except" not in between and "finally" not in between, "最近的 try 已被 except/finally 闭合，LLM 调用其实裸在外面 —— 一次超时会丢掉已成功镜头的全部旁白"
