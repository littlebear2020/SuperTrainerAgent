"""P-04（两反馈批 2026-08-18）：分块规划提示词语言锚。

事故：实物 storyboard 18 帧 image_prompt 语言漂移（0-7 英文 / 8-15 中文 /
16-17 英文），与 CHUNK=8 分块边界逐帧对齐——根因是块级并发 + 块间互不可见，
而 _generate_course_prompts_enhanced 的 system prompt 是全 planner 唯一没有
语言指令的 prompt。拍板④：强制锁语言与旁白一致（中文稿→中文描述，
英文稿→英文描述）。LLM 是否遵循是真实模型行为，单测锁定的是**锚点指令本身
必须进入每块 system prompt**（锚是无条件的旁白相对措辞，两种语言的稿都带锁）。
"""

import json

import pytest

from app.gateway.video_gen.orchestration import storyboard_planner as planner_mod
from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame

# 语言锚判别子串：用子串而非整句断言，锚点措辞微调不牵连测试；
# 四条合起来保证锚点语义完整（旁白相对 + 双语双向 + 全套逐帧一致）。
ANCHOR_MARK = "与旁白相同的语言"
ANCHOR_CN = "旁白中文→中文"
ANCHOR_EN = "旁白英文→英文"
ANCHOR_CONSISTENT = "逐帧语言一致"


def _anchor_marks(system: str) -> list[str]:
    return [m for m in (ANCHOR_MARK, ANCHOR_CN, ANCHOR_EN, ANCHOR_CONSISTENT) if m not in system]


class _RecordingLLM:
    """记录每次 ainvoke 入参；按块内旁白数返回对齐的 frames JSON。

    对齐才不触发单帧兜底——兜底 system 的锚由
    test_fallback_prompt_systems_carry_language_anchor 单独锁。echo 语言由参数
    控制，便于中文/英文稿各验一条。
    """

    def __init__(self, echo: str = "中文描述占位"):
        self.calls: list[list[dict]] = []
        self._echo = echo

    async def ainvoke(self, messages):
        self.calls.append(messages)
        narrations = json.loads(messages[1]["content"])["narrations"]
        # T6（2026-08-18）：planner 分组后块 user 混入【组标题】段头——段头不是
        # 旁白，剥掉再计数（否则块恒不对齐落单帧兜底，块数断言全崩）。
        n = len([ln for ln in narrations.split("\n") if ln.strip() and not (ln.startswith("【") and ln.endswith("】"))])
        frames = [{"image_prompt": f"{self._echo} {i}", "visual_source": "ai_image"} for i in range(n)]
        content = json.dumps({"frames": frames}, ensure_ascii=False)

        class _Resp:
            pass

        resp = _Resp()
        resp.content = content
        return resp


def _make_storyboard(narrations: list[str]) -> Storyboard:
    return Storyboard(
        title="语言锁测试",
        config=StoryboardConfig(video_type="ai_image", width=1920, height=1080),
        frames=[StoryboardFrame(index=i, narration=t) for i, t in enumerate(narrations)],
    )


@pytest.mark.asyncio
async def test_enhanced_system_prompt_contains_language_anchor():
    """单次调用直击 system：四个锚点子串齐全（含英文→英文方向，防止锚点退化成硬编码中文）。"""
    sb = _make_storyboard(["第一步说明", "第二步说明"])
    llm = _RecordingLLM()
    await planner_mod._generate_course_prompts_enhanced(llm, sb, "")
    assert len(llm.calls) == 1
    system = llm.calls[0][0]["content"]
    missing = _anchor_marks(system)
    assert not missing, f"system prompt 缺语言锚子串: {missing}; got: {system!r}"


@pytest.mark.asyncio
async def test_chunked_bulk_every_chunk_carries_language_anchor():
    """事故形态复现：18 帧无分组 → CHUNK=8 三块（8/8/2），每块 system 都必须带锁。

    事故里恰好是第 2 块（8-15）才中文、前后两块英文——任何一块漏锁都是回归。
    T5（2026-08-18）起 plan_course 分块前多一次全片锚定调用（锚定卡语言锁由
    test_storyboard_global_anchor.py 覆盖）：calls = 1 锚定 + 3 块，块断言只看后 3 个。
    """
    narrations = [f"第{i}帧旁白：产品实拍要点" for i in range(18)]
    sb = _make_storyboard(narrations)
    llm = _RecordingLLM()
    await planner_mod.plan_course(llm, storyboard=sb, chunk_size=8)  # E8: 块级语言锚语义走批量路径
    chunk_calls = llm.calls[1:]  # calls[0] = T5 锚定调用（其 frames 应答解析不出锚定卡，跳过注入）
    assert len(chunk_calls) == 3, f"18 帧应恰好 3 块 bulk 调用（+1 锚定调用），got {len(llm.calls)}"
    for ci, messages in enumerate(chunk_calls):
        missing = _anchor_marks(messages[0]["content"])
        assert not missing, f"第 {ci} 块 system 缺语言锚: {missing}"
    # sanity：全部帧拿到 echo 回的画面词（分块对齐，未落单帧兜底）
    assert all(f.image_prompt for f in sb.frames)


@pytest.mark.asyncio
async def test_chinese_narration_script_carries_language_anchor():
    """中文稿：锚进入**块** system（LLM 服从性不归单测管，见文件头）。

    T5 起 calls[0] 是锚定调用——断言必须落在块调用（calls[-1]），锚定 system 也带
    语言锁但那是 test_storyboard_global_anchor 的验收面，这里锁块级。
    """
    sb = _make_storyboard(["镜头一：清晨的城市街道", "镜头二：工坊里的手工制作"])
    llm = _RecordingLLM()
    await planner_mod.plan_course(llm, storyboard=sb, chunk_size=8)  # E8: 块级语言锚语义走批量路径
    assert len(llm.calls) == 2  # 1 锚定 + 1 块
    assert not _anchor_marks(llm.calls[-1][0]["content"])


@pytest.mark.asyncio
async def test_english_narration_script_carries_language_anchor():
    """英文稿：锚同样进入块 system——锚是旁白相对措辞，不是中文专属指令。"""
    sb = _make_storyboard(["Wide shot of a city street at dawn", "Close-up of hands crafting leather"])
    llm = _RecordingLLM(echo="english description placeholder")
    await planner_mod.plan_course(llm, storyboard=sb, chunk_size=8)  # E8: 块级语言锚语义走批量路径
    assert len(llm.calls) == 2  # 1 锚定 + 1 块
    assert not _anchor_marks(llm.calls[-1][0]["content"])


def test_fallback_prompt_systems_carry_language_anchor():
    """单帧兜底/单帧重生 system（_generate_image/video_prompts）也必须带旁白相对锚。

    审查偏离项 2（2026-08-19）：主路径（分块）锚定后，这两个 system 仍硬编码
    「**中文**描述」——英文稿任务走兜底帧或单帧重生端点会拿到中文指令，与拍板④
    「英文稿→英文描述」在边缘路径不一致。兜底是 bulk 失败后的兜底通道，锚点
    措辞与块级保持同源（四个判别子串齐全）。
    """
    for name, system in (
        ("_IMAGE_PROMPT_SYSTEM", planner_mod._IMAGE_PROMPT_SYSTEM),
        ("_VIDEO_PROMPT_SYSTEM", planner_mod._VIDEO_PROMPT_SYSTEM),
    ):
        missing = _anchor_marks(system)
        assert not missing, f"{name} 缺语言锚子串: {missing}; got: {system[:120]!r}..."
        # 不得残留「必须输出中文」式的绝对措辞（旁白相对锚取代硬编码中文）
        assert "**中文**" not in system, f"{name} 不得再硬编码「**中文**描述」"
