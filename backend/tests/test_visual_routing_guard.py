"""content-match T6：normalize_visual_routing 确定性护栏（六规则 + 组合）。

- 枚举校验：未知/空/None → 任务默认类型
- D8 零跨引擎偏移：visual_source 只允许 ∈ {任务主引擎, graphic}（ai_image 任务永不产生生视频费用）
- graphic 占比 ≤30%（2026-08-31 统一上限，超出按帧序回退，确定性）
- 同类型连续 ≤3（第 4 个连续 graphic 回退默认——防全片像 PPT）
- 首/末镜不 graphic（开场收尾要氛围画面）
- 开关 off → 清全部帧 visual_source（回滚语义：新规划任务回到任务级统一源）
- 阈值 env 可调；vimax 等非 ai_image/ai_video 任务不触碰
"""

from __future__ import annotations

import pytest

from app.gateway.video_gen.orchestration.visual_routing import content_routing_enabled, normalize_visual_routing
from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame


def _sb(sources: list[str | None], video_type: str = "ai_image") -> Storyboard:
    cfg = StoryboardConfig(video_type=video_type, width=1920, height=1080)
    frames = [StoryboardFrame(index=i, narration=f"n{i}", visual_source=s) for i, s in enumerate(sources)]
    return Storyboard(title="t", config=cfg, frames=frames)


@pytest.fixture(autouse=True)
def _routing_on(monkeypatch):
    monkeypatch.setenv("VIDEO_GEN_CONTENT_ROUTING", "1")


class TestSwitch:
    def test_default_on(self, monkeypatch):
        monkeypatch.delenv("VIDEO_GEN_CONTENT_ROUTING", raising=False)
        assert content_routing_enabled() is True

    @pytest.mark.parametrize("off_val", ["0", "false", "off", "no", ""])
    def test_off_values(self, monkeypatch, off_val):
        monkeypatch.setenv("VIDEO_GEN_CONTENT_ROUTING", off_val)
        assert content_routing_enabled() is False

    def test_switch_off_clears_all_sources(self, monkeypatch):
        """回滚语义：off → 清全部帧 visual_source（≠只拦新写入——已写回的也清）。"""
        monkeypatch.setenv("VIDEO_GEN_CONTENT_ROUTING", "0")
        sb = _sb(["graphic", "ai_image", "graphic"])
        normalize_visual_routing(sb)
        assert all(f.visual_source is None for f in sb.frames)


class TestEnumAndZeroCrossEngine:
    def test_unknown_enum_falls_to_default(self):
        sb = _sb(["hologram", "ai_image"])
        normalize_visual_routing(sb)
        assert sb.frames[0].visual_source == "ai_image"

    def test_none_and_empty_fall_to_default(self):
        sb = _sb([None, "", "graphic", "ai_image"])
        normalize_visual_routing(sb)
        assert sb.frames[0].visual_source == "ai_image"
        assert sb.frames[1].visual_source == "ai_image"
        assert sb.frames[2].visual_source == "graphic"

    def test_zero_cross_engine_ai_image_task(self):
        """D8（已拍板）：ai_image 任务中 LLM 推荐 ai_video → 回退 ai_image，永不产生生视频费用。"""
        sb = _sb(["ai_video", "ai_video", "ai_image"])
        normalize_visual_routing(sb)
        assert all(f.visual_source == "ai_image" for f in sb.frames)

    def test_zero_cross_engine_ai_video_task(self):
        sb = _sb(["ai_image", "ai_video"], video_type="ai_video")
        normalize_visual_routing(sb)
        assert all(f.visual_source == "ai_video" for f in sb.frames)

    def test_legacy_remotion_value_falls_to_default(self):
        sb = _sb(["remotion", "ai_image"])
        normalize_visual_routing(sb)
        assert sb.frames[0].visual_source == "ai_image"


class TestRatioCap:
    def test_ratio_over_cap_reverts_by_frame_order(self):
        """14 帧 8 个 graphic（>30%；交错排布使连续/首镜规则零贡献）
        → 占比规则按帧序回退最前面的 4 个：最终 3 个（8 − 占比4 − 末镜1）。"""
        sb = _sb(["ai_image", "graphic", "ai_image", "graphic", "ai_image", "graphic", "ai_image", "graphic", "ai_image", "graphic", "ai_image", "graphic", "graphic", "graphic"])
        normalize_visual_routing(sb)
        vs = [f.visual_source for f in sb.frames]
        assert vs.count("graphic") == 3, f"14 帧 8 graphic → 占比回退 4（f1/3/5/7）+ 末镜回退 1（f13）= 3，实际 {vs.count('graphic')}：{vs}"
        assert vs[1] == "ai_image", "超出部分按帧序从头回退（确定性，非随机）"

    def test_ratio_at_30_percent_untouched(self):
        """恰 30%（3/10）不触发占比回退（2026-08-31 统一上限的边界 pin）。"""
        sb = _sb(["ai_image", "graphic", "ai_image", "graphic", "ai_image", "graphic", "ai_image", "ai_image", "ai_image", "ai_image"])
        normalize_visual_routing(sb)
        vs = [f.visual_source for f in sb.frames]
        assert vs.count("graphic") == 3, f"恰 30%（3/10）不触发回退，实际 {vs.count('graphic')}：{vs}"

    def test_ratio_env_override(self, monkeypatch):
        monkeypatch.setenv("VIDEO_GEN_GRAPHIC_MAX_RATIO", "0.2")
        sb = _sb(["ai_image", "graphic", "graphic", "graphic", "ai_image"])
        normalize_visual_routing(sb)
        vs = [f.visual_source for f in sb.frames]
        assert vs.count("graphic") == 1, f"ratio=0.2 → 5 帧上限 1，实际 {vs}"


class TestRunCap:
    """连续规则的输入排布须与占比规则隔离（30% 上限下 4/8 graphic 也会触发占比
    回退——2026-08-31 起 fixture 显式放开 RATIO env，同 test_run_env_override 先例），
    graphic 不落首末镜，单独验证连续截断；组合语义见 TestCombinedRules。"""

    def test_four_consecutive_graphics_broken(self, monkeypatch):
        """连续 ≤3：第 4 个连续 graphic 回退默认（同口径=验收「无连续 >3 镜同类型」）。"""
        monkeypatch.setenv("VIDEO_GEN_GRAPHIC_MAX_RATIO", "1.0")
        sb = _sb(["ai_image", "graphic", "graphic", "graphic", "graphic", "ai_image", "ai_image", "ai_image"])
        normalize_visual_routing(sb)
        assert [f.visual_source for f in sb.frames] == ["ai_image", "graphic", "graphic", "graphic", "ai_image", "ai_image", "ai_image", "ai_image"]

    def test_three_consecutive_allowed(self, monkeypatch):
        monkeypatch.setenv("VIDEO_GEN_GRAPHIC_MAX_RATIO", "1.0")
        sb = _sb(["ai_image", "graphic", "graphic", "graphic", "ai_image", "ai_image", "ai_image", "ai_image"])
        normalize_visual_routing(sb)
        vs = [f.visual_source for f in sb.frames]
        assert vs.count("graphic") == 3

    def test_run_env_override(self, monkeypatch):
        monkeypatch.setenv("VIDEO_GEN_GRAPHIC_MAX_RATIO", "1.0")
        monkeypatch.setenv("VIDEO_GEN_GRAPHIC_MAX_RUN", "2")
        sb = _sb(["ai_image", "graphic", "graphic", "graphic", "ai_image"])
        normalize_visual_routing(sb)
        assert [f.visual_source for f in sb.frames] == ["ai_image", "graphic", "graphic", "ai_image", "ai_image"]


class TestEdges:
    def test_first_and_last_frames_never_graphic(self):
        sb = _sb(["graphic", "ai_image", "graphic"])
        normalize_visual_routing(sb)
        assert sb.frames[0].visual_source == "ai_image"
        assert sb.frames[-1].visual_source == "ai_image"

    def test_single_frame_is_both_first_and_last(self):
        sb = _sb(["graphic"])
        normalize_visual_routing(sb)
        assert sb.frames[0].visual_source == "ai_image"

    def test_empty_frames_noop(self):
        sb = _sb([])
        normalize_visual_routing(sb)  # 不抛即通过


class TestNonAiTasksUntouched:
    def test_vimax_not_touched(self):
        """vimax 走引擎层不消费 frame.visual_source——护栏不触碰（T5 现状不变量）。"""
        sb = _sb(["remotion", "ai_video", "graphic"], video_type="vimax")
        normalize_visual_routing(sb)
        assert [f.visual_source for f in sb.frames] == ["remotion", "ai_video", "graphic"]

    def test_switch_off_also_skips_vimax(self, monkeypatch):
        monkeypatch.setenv("VIDEO_GEN_CONTENT_ROUTING", "0")
        sb = _sb(["remotion", "ai_image"], video_type="vimax")
        normalize_visual_routing(sb)
        assert sb.frames[0].visual_source == "remotion"


class TestCombinedRules:
    def test_all_rules_together(self):
        """组合：跨引擎回退 + 占比 + 连续 + 首末镜，最终盘面同时满足全部约束。"""
        sources = ["graphic", "ai_video", "graphic", "graphic", "graphic", "graphic", "graphic", "ai_video"]
        sb = _sb(sources)  # ai_image 任务
        normalize_visual_routing(sb)
        vs = [f.visual_source for f in sb.frames]
        assert set(vs) <= {"ai_image", "graphic"}
        assert vs.count("graphic") <= 2  # 8 帧 ≤30%（int(8*0.3)=2）
        assert vs[0] != "graphic" and vs[-1] != "graphic"
        run = 0
        for v in vs:
            run = run + 1 if v == "graphic" else 0
            assert run <= 3, f"连续 graphic 超 3：{vs}"


class TestProtectManualEdits:
    """P0-5 不变量（content-match T5 修订）：规划前已存在的 visual_source = 用户手改值。

    D8 管 LLM 推荐漂移，不管用户手选（手动切 ai_video 是现行产品能力）——
    全部规则（枚举/占比/连续/首末镜/off 清空）对 protect 集豁免；
    plan_course 负责快照规划前值传入（见 test_video_gen_explicit_type 集成测试）。"""

    def test_manual_cross_engine_survives_d8(self):
        sb = _sb(["ai_video", "ai_image", "graphic"])
        normalize_visual_routing(sb, protect={0})
        assert sb.frames[0].visual_source == "ai_video", "protect 帧的手动跨引擎值不被 D8 回退"

    def test_manual_graphic_edges_and_run_exempt(self):
        sb = _sb(["graphic", "graphic", "graphic", "graphic", "ai_image"])
        normalize_visual_routing(sb, protect={0, 1, 2, 3})
        assert all(sb.frames[i].visual_source == "graphic" for i in range(4)), "protect 帧不受首末镜/连续规则约束"

    def test_off_keeps_manual_values(self, monkeypatch):
        monkeypatch.setenv("VIDEO_GEN_CONTENT_ROUTING", "0")
        sb = _sb(["graphic", "ai_image"])
        normalize_visual_routing(sb, protect={0})
        assert sb.frames[0].visual_source == "graphic", "off 回滚只清推荐，不清用户手改"
        assert sb.frames[1].visual_source is None

    def test_unprotected_frames_still_normalized_alongside_protected(self):
        sb = _sb(["ai_video", "ai_video", "ai_video"])
        normalize_visual_routing(sb, protect={0})
        assert sb.frames[0].visual_source == "ai_video"
        assert sb.frames[1].visual_source == "ai_image" and sb.frames[2].visual_source == "ai_image"


class TestRatioEnvChain:
    """2026-08-31 路由并入批：占比上限统一 0.3，不再按文稿有无数字浮动；
    env 链 = RATIO > NO_DIGITS（向后兼容存量覆盖）> 默认 0.3。"""

    def _sb_graphic_heavy(self, n: int = 10) -> Storyboard:
        cfg = StoryboardConfig(video_type="ai_image", width=1920, height=1080)
        # ⚠️ 尾帧补一个非 graphic——全 graphic 输入的末帧必被首末镜规则回退，占比断言会差 1
        frames = [StoryboardFrame(index=i, narration=f"第{i}段讲稿，增长37%很有说服力。", visual_source="graphic") for i in range(n)]
        frames.append(StoryboardFrame(index=n, narration="结尾段。", visual_source="ai_image"))
        return Storyboard(title="t", config=cfg, frames=frames)

    # 隔离占比规则：放开连续上限（两规则在高位占比下互相咬——TestRunCap 同款先例）
    def test_no_digit_script_caps_at_30_percent(self, monkeypatch):
        monkeypatch.setenv("VIDEO_GEN_GRAPHIC_MAX_RUN", "10")
        sb = self._sb_graphic_heavy(10)
        for f in sb.frames:  # 无数字变体：汉字数字不匹配 \d
            f.narration = "".join(ch for ch in f.narration if not ch.isdigit() and ch != "%")
        normalize_visual_routing(sb)
        assert sum(1 for f in sb.frames if f.visual_source == "graphic") == 3, "11 帧无数字 → 上限 floor(11*0.3)=3"

    def test_digit_script_also_caps_at_30_percent(self, monkeypatch):
        """2026-08-31 拍板：取消「有数字 → 0.5」分支——有数字文稿同样 30%。"""
        monkeypatch.setenv("VIDEO_GEN_GRAPHIC_MAX_RUN", "10")
        sb = self._sb_graphic_heavy(10)
        assert any(ch.isdigit() for f in sb.frames for ch in (f.narration or "")), "夹具须真含数字"
        normalize_visual_routing(sb)
        assert sum(1 for f in sb.frames if f.visual_source == "graphic") == 3, "有数字文稿 → 同样 floor(11*0.3)=3"

    def test_explicit_ratio_env_wins(self, monkeypatch):
        monkeypatch.setenv("VIDEO_GEN_GRAPHIC_MAX_RUN", "10")
        monkeypatch.setenv("VIDEO_GEN_GRAPHIC_MAX_RATIO", "0.4")
        sb = self._sb_graphic_heavy(10)
        normalize_visual_routing(sb)
        assert sum(1 for f in sb.frames if f.visual_source == "graphic") == 4, "显式 RATIO env 最优先"

    def test_no_digit_env_still_honored_for_compat(self, monkeypatch):
        """向后兼容：RATIO 未设时 NO_DIGITS env 仍生效（存量部署的显式覆盖不断链）。"""
        monkeypatch.setenv("VIDEO_GEN_GRAPHIC_MAX_RUN", "10")
        monkeypatch.setenv("VIDEO_GEN_GRAPHIC_MAX_RATIO_NO_DIGITS", "0.1")  # ≠ 默认 0.3 才有区分度
        sb = self._sb_graphic_heavy(10)
        normalize_visual_routing(sb)
        assert sum(1 for f in sb.frames if f.visual_source == "graphic") == 1


class TestRatioOverride:
    """T3（2026-09-01）：任务级占比上限 ratio_override。

    优先级 = 任务级 > RATIO env > NO_DIGITS env > 默认 0.3；None/0 = 走 env/默认链
    （行为与改动前完全一致）。上限≠保障——只抬规则 1 天花板，连续 ≤3 与首末镜
    规则不受影响（protect 豁免亦不动）。
    """

    def _sb_graphic_heavy(self, n: int = 10) -> Storyboard:
        """与 TestRatioEnvChain 同款夹具：n 个 graphic + 1 个非 graphic 尾帧。"""
        cfg = StoryboardConfig(video_type="ai_image", width=1920, height=1080)
        frames = [StoryboardFrame(index=i, narration=f"第{i}段讲稿，增长37%很有说服力。", visual_source="graphic") for i in range(n)]
        frames.append(StoryboardFrame(index=n, narration="结尾段。", visual_source="ai_image"))
        return Storyboard(title="t", config=cfg, frames=frames)

    def test_override_40_beats_env_and_default(self, monkeypatch):
        """任务 40% 同时压过 env（0.2）与默认（0.3）：11 帧上限 int(11*0.4)=4。"""
        monkeypatch.setenv("VIDEO_GEN_GRAPHIC_MAX_RUN", "10")
        monkeypatch.setenv("VIDEO_GEN_GRAPHIC_MAX_RATIO", "0.2")
        sb = self._sb_graphic_heavy(10)
        normalize_visual_routing(sb, ratio_override=0.4)
        assert sum(1 for f in sb.frames if f.visual_source == "graphic") == 4, "任务级 40% 最优先"

    def test_none_falls_back_to_env_chain(self, monkeypatch):
        """None = 未指定 → 走现状 env 链（0.2 生效，行为与改动前一致）。"""
        monkeypatch.setenv("VIDEO_GEN_GRAPHIC_MAX_RUN", "10")
        monkeypatch.setenv("VIDEO_GEN_GRAPHIC_MAX_RATIO", "0.2")
        sb = self._sb_graphic_heavy(10)
        normalize_visual_routing(sb, ratio_override=None)
        assert sum(1 for f in sb.frames if f.visual_source == "graphic") == 2, "None 时 env 0.2 照常生效"

    def test_zero_override_treated_as_unset(self, monkeypatch):
        """0 是 falsy → 与 None 同义（防 0 被当成「上限 0 全灭」的钝器）。"""
        monkeypatch.setenv("VIDEO_GEN_GRAPHIC_MAX_RUN", "10")
        sb = self._sb_graphic_heavy(10)
        normalize_visual_routing(sb, ratio_override=0)
        assert sum(1 for f in sb.frames if f.visual_source == "graphic") == 3, "0 = 未指定 → 默认 30%"

    def test_override_70_does_not_touch_run_and_edge_rules(self, monkeypatch):
        """上限≠保障的另一面：override 只抬规则 1——70% 下连续 ≤3 与首末镜照常截断。"""
        monkeypatch.setenv("VIDEO_GEN_GRAPHIC_MAX_RUN", "3")
        sb = _sb(["ai_image", "graphic", "graphic", "graphic", "graphic", "ai_image", "ai_image", "ai_image", "ai_image", "ai_image", "ai_image"])
        normalize_visual_routing(sb, ratio_override=0.7)
        vs = [f.visual_source for f in sb.frames]
        assert vs.count("graphic") == 3, f"连续规则仍截断第 4 个（占比 7/11 ≤ 70% 不动），实际 {vs}"
        assert vs[0] != "graphic" and vs[-1] != "graphic", "首末镜规则不受 override 影响"

    def test_protect_exemption_unchanged_under_override(self, monkeypatch):
        """protect 豁免与 override 正交：手改 graphic 帧不被任何上限回退。"""
        monkeypatch.setenv("VIDEO_GEN_GRAPHIC_MAX_RUN", "10")
        sb = self._sb_graphic_heavy(10)
        normalize_visual_routing(sb, ratio_override=0.2, protect={0})
        assert sb.frames[0].visual_source == "graphic", "protect 帧不受任务级上限约束"
