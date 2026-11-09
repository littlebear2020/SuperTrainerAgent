"""Phase 2 Task 7: 续写编排测试（Tier E Seedance i2v extension）。

测试维度：
- 触发条件（gap ≥ 3s + supports_tail_frame + extend_enabled）
- 阈值不足跳过（gap < 3s）
- provider 不支持 tail_frame 跳过
- extend_enabled=False 跳过
- 尾帧获取（官方 URL 优先 / 本地抽帧 fallback）
- concat 时长校验失败 → 回落 SHORTFALL
- 成功时只记 EXTENDED 不记 SHORTFALL
- 变异测试必备
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.gateway.video_gen.providers.base import VideoRef
from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame
from app.gateway.video_gen.visual_sources.ai_video import AIVideoVisualSource


def _make_frame(index: int = 0, duration: float = 12.0, image_prompt: str = "test prompt") -> StoryboardFrame:
    return StoryboardFrame(
        index=index,
        duration=duration,
        narration="narration",
        image_prompt=image_prompt,
    )


def _make_storyboard(frames: list[StoryboardFrame] | None = None) -> Storyboard:
    return Storyboard(
        title="test",
        config=StoryboardConfig(width=1280, height=720, video_type="ai_video"),
        frames=frames or [_make_frame()],
    )


def _make_source(
    *,
    extend_enabled: bool = True,
    extend_min_gap: float = 3.0,
    supports_tail_frame: bool = True,
    budget=None,
) -> AIVideoVisualSource:
    provider = MagicMock()
    provider.supports_tail_frame = supports_tail_frame
    provider.max_video_duration = 15.0
    provider.min_video_duration = 4.0
    provider.integer_seconds_only = True
    provider.generate_video = AsyncMock(return_value=VideoRef(url="http://ext", duration=5.0, source="seedance-ark"))
    return AIVideoVisualSource(
        provider,
        concurrency=1,
        extend_enabled=extend_enabled,
        extend_min_gap=extend_min_gap,
        budget=budget,
    )


class TestExtendTriggerConditions:
    """续写触发条件判定（2026-08-14 C3：gate 下沉到 _try_extend_video 首行）。"""

    def _make_env(self, tmp_path):
        """原片文件存在 + 抽帧/拼接成功——若 gate 缺失会一路走到续写成功返 True。"""
        orig_path = tmp_path / "visuals" / "shot_0000.mp4"
        orig_path.parent.mkdir(parents=True, exist_ok=True)
        orig_path.write_bytes(b"x" * 2048)
        return orig_path

    @pytest.mark.asyncio
    async def test_gap_below_threshold_skips_extend(self, tmp_path):
        """gap < 3s → gate 跳过（断言因 gate 而非抽帧失败跳过）。"""
        source = _make_source(extend_min_gap=3.0)
        frame = _make_frame(duration=6.0)
        ref = VideoRef(url="http://v", duration=5.0, last_frame_url=None)
        storyboard = _make_storyboard([frame])
        self._make_env(tmp_path)

        with (
            patch("app.gateway.video_gen.visual_sources.ai_video._extract_tail_frame", return_value=True) as mock_extract,
            patch("app.gateway.video_gen.visual_sources.ai_video._concat_videos_with_check", return_value=True),
        ):
            result = await source._try_extend_video(frame, ref, gap=1.0, task_dir=tmp_path, original_rel="visuals/shot_0000.mp4", storyboard=storyboard)

        assert result is False, "gap < min_gap should not extend"
        mock_extract.assert_not_called(), "gate 应短路在抽帧之前（去 gate 此断言红）"

    @pytest.mark.asyncio
    async def test_provider_no_tail_frame_skips_extend(self, tmp_path):
        """provider 不支持 tail_frame → gate 跳过（原文件存在也抽不到帧）。"""
        source = _make_source(supports_tail_frame=False)
        frame = _make_frame(duration=12.0)
        ref = VideoRef(url="http://v", duration=5.0)
        storyboard = _make_storyboard([frame])
        self._make_env(tmp_path)

        with (
            patch("app.gateway.video_gen.visual_sources.ai_video._extract_tail_frame", return_value=True) as mock_extract,
            patch("app.gateway.video_gen.visual_sources.ai_video._concat_videos_with_check", return_value=True),
        ):
            result = await source._try_extend_video(frame, ref, gap=7.0, task_dir=tmp_path, original_rel="visuals/shot_0000.mp4", storyboard=storyboard)

        assert result is False, "provider without supports_tail_frame should not extend"
        mock_extract.assert_not_called(), "gate 应短路在抽帧之前"

    @pytest.mark.asyncio
    async def test_extend_disabled_skips(self, tmp_path):
        """extend_enabled=False → gate 跳过，不抽帧不调用 provider。"""
        source = _make_source(extend_enabled=False)
        frame = _make_frame(duration=12.0)
        ref = VideoRef(url="http://v", duration=5.0)
        storyboard = _make_storyboard([frame])
        self._make_env(tmp_path)

        with (
            patch("app.gateway.video_gen.visual_sources.ai_video._extract_tail_frame", return_value=True) as mock_extract,
            patch("app.gateway.video_gen.visual_sources.ai_video._concat_videos_with_check", return_value=True),
        ):
            result = await source._try_extend_video(frame, ref, gap=7.0, task_dir=tmp_path, original_rel="visuals/shot_0000.mp4", storyboard=storyboard)

        assert result is False, "extend_enabled=False 应跳过"
        mock_extract.assert_not_called()
        source._provider.generate_video.assert_not_awaited(), "跳过时不得发起续写生成"

    @pytest.mark.asyncio
    async def test_budget_exhausted_skips_without_raising(self, tmp_path):
        """预算耗尽 → 跳过续写返 False，不抛异常（主流程落回 SHORTFALL）。"""
        from app.gateway.video_gen.visual_sources.budget import VisualBudget

        budget = VisualBudget(max_calls=1)
        budget.charge()  # 耗尽
        source = _make_source(budget=budget)
        frame = _make_frame(duration=12.0)
        ref = VideoRef(url="http://v", duration=5.0)
        storyboard = _make_storyboard([frame])
        self._make_env(tmp_path)

        with (
            patch("app.gateway.video_gen.visual_sources.ai_video._extract_tail_frame", return_value=True),
            patch("app.gateway.video_gen.visual_sources.ai_video._concat_videos_with_check", return_value=True),
            patch("app.gateway.video_gen.visual_sources.ai_video._localize_video", new_callable=AsyncMock, return_value="visuals/ext_0000.mp4"),
        ):
            result = await source._try_extend_video(frame, ref, gap=7.0, task_dir=tmp_path, original_rel="visuals/shot_0000.mp4", storyboard=storyboard)

        assert result is False, "预算耗尽应跳过续写"
        source._provider.generate_video.assert_not_awaited(), "跳过时不得发起续写生成"


class TestExtendTailFrameSource:
    """尾帧来源：官方 URL 优先 / 本地抽帧 fallback。"""

    @pytest.mark.asyncio
    async def test_uses_official_tail_frame_url(self, tmp_path):
        """有 last_frame_url → 下载官方尾帧，不本地抽帧。"""
        source = _make_source()
        frame = _make_frame(duration=12.0)
        ref = VideoRef(url="http://v", duration=5.0, last_frame_url="http://tail.png")
        storyboard = _make_storyboard([frame])

        # Create a fake original video
        orig_path = tmp_path / "visuals" / "shot_0000.mp4"
        orig_path.parent.mkdir(parents=True, exist_ok=True)
        orig_path.write_bytes(b"x" * 2048)

        with (
            patch("app.gateway.video_gen.visual_sources.ai_video.httpx.AsyncClient") as mock_client_cls,
            patch("app.gateway.video_gen.visual_sources.ai_video._extract_tail_frame") as mock_extract,
            patch("app.gateway.video_gen.visual_sources.ai_video._concat_videos_with_check", return_value=True),
            patch("app.gateway.video_gen.visual_sources.ai_video._probe_video_seconds", return_value=12.0),
            patch("app.gateway.video_gen.visual_sources.ai_video._localize_video", new_callable=AsyncMock, return_value="visuals/ext_0000.mp4"),
        ):
            # Mock httpx to return fake tail frame
            mock_resp = MagicMock()
            mock_resp.content = b"x" * 2048
            mock_resp.raise_for_status = MagicMock()
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            await source._try_extend_video(
                frame,
                ref,
                gap=7.0,
                task_dir=tmp_path,
                original_rel="visuals/shot_0000.mp4",
                storyboard=storyboard,
            )

        # extract_tail_frame should NOT be called (official URL was used)
        mock_extract.assert_not_called()

    @pytest.mark.asyncio
    async def test_fallback_to_local_extraction(self, tmp_path):
        """无 last_frame_url → 本地抽帧 fallback。"""
        source = _make_source()
        frame = _make_frame(duration=12.0)
        ref = VideoRef(url="http://v", duration=5.0, last_frame_url=None)
        storyboard = _make_storyboard([frame])

        orig_path = tmp_path / "visuals" / "shot_0000.mp4"
        orig_path.parent.mkdir(parents=True, exist_ok=True)
        orig_path.write_bytes(b"x" * 2048)

        with (
            patch("app.gateway.video_gen.visual_sources.ai_video._extract_tail_frame", return_value=True) as mock_extract,
            patch("app.gateway.video_gen.visual_sources.ai_video._concat_videos_with_check", return_value=True),
            patch("app.gateway.video_gen.visual_sources.ai_video._probe_video_seconds", return_value=12.0),
            patch("app.gateway.video_gen.visual_sources.ai_video._localize_video", new_callable=AsyncMock, return_value="visuals/ext_0000.mp4"),
        ):
            await source._try_extend_video(
                frame,
                ref,
                gap=7.0,
                task_dir=tmp_path,
                original_rel="visuals/shot_0000.mp4",
                storyboard=storyboard,
            )

        mock_extract.assert_called_once()


class TestExtendConcatFailure:
    """concat 失败 → 回落。"""

    @pytest.mark.asyncio
    async def test_concat_failure_returns_false(self, tmp_path):
        """concat 校验失败 → 返 False（主流程记 SHORTFALL）。"""
        source = _make_source()
        frame = _make_frame(duration=12.0)
        ref = VideoRef(url="http://v", duration=5.0, last_frame_url=None)
        storyboard = _make_storyboard([frame])

        orig_path = tmp_path / "visuals" / "shot_0000.mp4"
        orig_path.parent.mkdir(parents=True, exist_ok=True)
        orig_path.write_bytes(b"x" * 2048)

        with (
            patch("app.gateway.video_gen.visual_sources.ai_video._extract_tail_frame", return_value=True),
            patch("app.gateway.video_gen.visual_sources.ai_video._concat_videos_with_check", return_value=False),
            patch("app.gateway.video_gen.visual_sources.ai_video._localize_video", new_callable=AsyncMock, return_value="visuals/ext_0000.mp4"),
        ):
            result = await source._try_extend_video(
                frame,
                ref,
                gap=7.0,
                task_dir=tmp_path,
                original_rel="visuals/shot_0000.mp4",
                storyboard=storyboard,
            )

        assert result is False


class TestExtendPathCollisionFix:
    """C2 修复：续写片文件名不撞原片（dest_name 透传）。"""

    @pytest.mark.asyncio
    async def test_localize_video_receives_dest_name(self, tmp_path):
        """_localize_video 被调用时传了 dest_name='visuals/ext_NNNN.mp4'。"""
        source = _make_source()
        frame = _make_frame(duration=12.0)
        ref = VideoRef(url="http://v", duration=5.0, last_frame_url=None)
        storyboard = _make_storyboard([frame])

        orig_path = tmp_path / "visuals" / "shot_0000.mp4"
        orig_path.parent.mkdir(parents=True, exist_ok=True)
        orig_path.write_bytes(b"x" * 2048)

        localize_calls = []

        async def fake_localize(url, frame_arg, task_dir_arg, **kw):
            localize_calls.append({"url": url, "kw": kw})
            # 写假文件让后续 concat 能找到
            dest = kw.get("dest_name") or f"visuals/shot_{frame_arg.index:04d}.mp4"
            out = task_dir_arg / dest
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(b"x" * 2048)
            return dest

        with (
            patch("app.gateway.video_gen.visual_sources.ai_video._extract_tail_frame", return_value=True),
            patch("app.gateway.video_gen.visual_sources.ai_video._concat_videos_with_check", return_value=True),
            patch("app.gateway.video_gen.visual_sources.ai_video._probe_video_seconds", return_value=12.0),
            patch("app.gateway.video_gen.visual_sources.ai_video._localize_video", side_effect=fake_localize),
        ):
            result = await source._try_extend_video(
                frame,
                ref,
                gap=7.0,
                task_dir=tmp_path,
                original_rel="visuals/shot_0000.mp4",
                storyboard=storyboard,
            )

        assert result is True
        assert len(localize_calls) == 1
        assert localize_calls[0]["kw"].get("dest_name") == "visuals/ext_0000.mp4", "续写片必须用 dest_name 避免覆盖原片（C2）"

    @pytest.mark.asyncio
    async def test_concat_receives_different_paths(self, tmp_path):
        """concat 的两个入参路径必须不同（C2：撞名 = 丢原片）。"""
        source = _make_source()
        frame = _make_frame(duration=12.0)
        ref = VideoRef(url="http://v", duration=5.0, last_frame_url=None)
        storyboard = _make_storyboard([frame])

        orig_path = tmp_path / "visuals" / "shot_0000.mp4"
        orig_path.parent.mkdir(parents=True, exist_ok=True)
        orig_path.write_bytes(b"x" * 2048)

        concat_calls = []

        def fake_concat(a, b, out, **kw):
            concat_calls.append({"a": str(a), "b": str(b)})
            return True

        async def fake_localize(url, frame_arg, task_dir_arg, **kw):
            dest = kw.get("dest_name") or f"visuals/shot_{frame_arg.index:04d}.mp4"
            out = task_dir_arg / dest
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(b"x" * 2048)
            return dest

        with (
            patch("app.gateway.video_gen.visual_sources.ai_video._extract_tail_frame", return_value=True),
            patch("app.gateway.video_gen.visual_sources.ai_video._concat_videos_with_check", side_effect=fake_concat),
            patch("app.gateway.video_gen.visual_sources.ai_video._probe_video_seconds", return_value=12.0),
            patch("app.gateway.video_gen.visual_sources.ai_video._localize_video", side_effect=fake_localize),
        ):
            await source._try_extend_video(
                frame,
                ref,
                gap=7.0,
                task_dir=tmp_path,
                original_rel="visuals/shot_0000.mp4",
                storyboard=storyboard,
            )

        assert len(concat_calls) == 1
        assert concat_calls[0]["a"] != concat_calls[0]["b"], f"concat 入参路径相同（{concat_calls[0]['a']}）→ 续写片覆盖原片（C2）"


class TestExtendTempFileCleanup:
    """A-2（2026-08-18）：续写失败路径的临时文件清理（try/finally）。"""

    @staticmethod
    def _fake_localize_writes_ext(url, frame_arg, task_dir_arg, **kw):
        """fake _localize_video：真的写 ext 文件（否则「无残留」断言空转）。"""
        dest = kw.get("dest_name") or f"visuals/shot_{frame_arg.index:04d}.mp4"
        out = task_dir_arg / dest
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"x" * 2048)
        return dest

    @pytest.mark.asyncio
    async def test_concat_failure_cleans_temp_files(self, tmp_path):
        """A-2: concat 失败不留 ext_/_tail_ 残留（修前只在成功分支清理）。"""
        source = _make_source()
        frame = _make_frame(duration=12.0)
        ref = VideoRef(url="http://v", duration=5.0, last_frame_url="http://tail.png")
        storyboard = _make_storyboard([frame])

        orig_path = tmp_path / "visuals" / "shot_0000.mp4"
        orig_path.parent.mkdir(parents=True, exist_ok=True)
        orig_path.write_bytes(b"x" * 2048)

        with (
            # 官方尾帧：mock httpx 下载（>512B 真的写 _tail 文件）
            patch("app.gateway.video_gen.visual_sources.ai_video.httpx.AsyncClient") as mock_client_cls,
            patch("app.gateway.video_gen.visual_sources.ai_video._extract_tail_frame") as mock_extract,
            patch("app.gateway.video_gen.visual_sources.ai_video._concat_videos_with_check", return_value=False),
            patch("app.gateway.video_gen.visual_sources.ai_video._localize_video", new_callable=AsyncMock, side_effect=self._fake_localize_writes_ext),
        ):
            mock_resp = MagicMock()
            mock_resp.content = b"x" * 2048
            mock_resp.raise_for_status = MagicMock()
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            result = await source._try_extend_video(
                frame,
                ref,
                gap=7.0,
                task_dir=tmp_path,
                original_rel="visuals/shot_0000.mp4",
                storyboard=storyboard,
            )

        assert result is False
        assert not list(tmp_path.rglob("ext_*")), "concat 失败不得残留续写片"
        assert not list(tmp_path.rglob("_tail_*")), "concat 失败不得残留尾帧临时文件"
        mock_extract.assert_not_called(), "官方尾帧可用时不走本地抽帧"

    @pytest.mark.asyncio
    async def test_generate_video_exception_cleans_tail(self, tmp_path):
        """A-2: generate_video 抛异常时尾帧临时文件同样清理。"""

        def fake_extract(original_path, tail_local):
            tail_local.parent.mkdir(parents=True, exist_ok=True)
            tail_local.write_bytes(b"x" * 2048)
            return True

        source = _make_source()
        source._provider.generate_video = AsyncMock(side_effect=RuntimeError("boom"))
        frame = _make_frame(duration=12.0)
        ref = VideoRef(url="http://v", duration=5.0, last_frame_url=None)
        storyboard = _make_storyboard([frame])

        orig_path = tmp_path / "visuals" / "shot_0000.mp4"
        orig_path.parent.mkdir(parents=True, exist_ok=True)
        orig_path.write_bytes(b"x" * 2048)

        with (
            patch("app.gateway.video_gen.visual_sources.ai_video._extract_tail_frame", side_effect=fake_extract),
            patch("app.gateway.video_gen.visual_sources.ai_video._concat_videos_with_check", return_value=True),
        ):
            with pytest.raises(RuntimeError):
                await source._try_extend_video(
                    frame,
                    ref,
                    gap=7.0,
                    task_dir=tmp_path,
                    original_rel="visuals/shot_0000.mp4",
                    storyboard=storyboard,
                )

        assert not list(tmp_path.rglob("_tail_*")), "异常路径不得残留尾帧临时文件"


class TestExtendProbeFailure:
    """A-3（2026-08-18）：续写后探测失败置 None，消灭假「仍短 X 秒」告警。"""

    @pytest.mark.asyncio
    async def test_probe_failure_resets_actual_duration(self, tmp_path, caplog):
        """A-3: concat 成功但 probe 返回 0 → actual_visual_duration 置 None（不得保留
        续写前旧值——旧值会让调用方算出假残余缺口记误报 SHORTFALL sidecar）。"""
        source = _make_source()
        frame = _make_frame(duration=12.0)
        frame.actual_visual_duration = 5.0  # 续写前旧值
        ref = VideoRef(url="http://v", duration=5.0, last_frame_url="http://tail.png")
        storyboard = _make_storyboard([frame])

        orig_path = tmp_path / "visuals" / "shot_0000.mp4"
        orig_path.parent.mkdir(parents=True, exist_ok=True)
        orig_path.write_bytes(b"x" * 2048)

        with (
            patch("app.gateway.video_gen.visual_sources.ai_video.httpx.AsyncClient") as mock_client_cls,
            patch("app.gateway.video_gen.visual_sources.ai_video._extract_tail_frame"),
            patch("app.gateway.video_gen.visual_sources.ai_video._concat_videos_with_check", return_value=True),
            patch("app.gateway.video_gen.visual_sources.ai_video._probe_video_seconds", return_value=0.0),
            patch("app.gateway.video_gen.visual_sources.ai_video._localize_video", new_callable=AsyncMock, return_value="visuals/ext_0000.mp4"),
        ):
            mock_resp = MagicMock()
            mock_resp.content = b"x" * 2048
            mock_resp.raise_for_status = MagicMock()
            mock_client = AsyncMock()
            mock_client.get = AsyncMock(return_value=mock_resp)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_client_cls.return_value = mock_client

            result = await source._try_extend_video(
                frame,
                ref,
                gap=7.0,
                task_dir=tmp_path,
                original_rel="visuals/shot_0000.mp4",
                storyboard=storyboard,
            )

        assert result is True
        assert frame.actual_visual_duration is None, "probe 失败须置 None（不得保留续写前旧值 5.0）"
        assert any("probe" in r.getMessage().lower() for r in caplog.records)
        # 调用方残余缺口计算随 None 归零（duration_shortfall None→0.0）：
        from app.gateway.video_gen.duration_policy import duration_shortfall

        assert duration_shortfall(frame.duration or 0.0, frame.actual_visual_duration) == 0.0


class TestExtendSidecar:
    """C3 ③④：续写成功/残余缺口的降级留痕（render 全链路）。"""

    def _read_sidecar(self, task_dir) -> list[dict]:
        import json

        from app.gateway.video_gen.degradations import DEGRADATION_SIDECAR_NAME

        sidecar = task_dir / DEGRADATION_SIDECAR_NAME
        if not sidecar.exists():
            return []
        return json.loads(sidecar.read_text(encoding="utf-8")).get("items", [])

    @pytest.mark.asyncio
    async def test_extend_success_records_extended_not_shortfall(self, tmp_path):
        """③ 续写成功补齐缺口 → 只记 KIND_VIDEO_EXTENDED，不记 SHORTFALL。"""
        source = _make_source()
        frame = _make_frame(duration=12.0)
        storyboard = _make_storyboard([frame])
        ref = VideoRef(url="http://v", duration=5.0, last_frame_url=None)
        source._provider.generate_video = AsyncMock(return_value=ref)

        with (
            patch("app.gateway.video_gen.visual_sources.ai_video._localize_video", new_callable=AsyncMock, return_value="visuals/shot_0000.mp4"),
            # 主生成探测 5.0（gap=7.0 触发续写）→ 续写拼接产物 12.0（补齐）
            patch("app.gateway.video_gen.visual_sources.ai_video._probe_video_seconds", side_effect=[5.0, 12.0]),
            patch("app.gateway.video_gen.visual_sources.ai_video._extract_tail_frame", return_value=True),
            patch("app.gateway.video_gen.visual_sources.ai_video._concat_videos_with_check", return_value=True),
        ):
            await source.render(storyboard, task_dir=tmp_path)

        kinds = [e.get("kind") for e in self._read_sidecar(tmp_path)]
        assert "video_extended" in kinds, f"续写成功应记 video_extended，实得 {kinds}"
        assert "visual_shortfall" not in kinds, f"缺口已补齐不得再记 SHORTFALL，实得 {kinds}"

    @pytest.mark.asyncio
    async def test_extend_with_residual_gap_records_both(self, tmp_path):
        """④ 续写后仍有残余缺口 → 同时记 EXTENDED 与 SHORTFALL（M2）。"""
        source = _make_source()
        frame = _make_frame(duration=12.0)
        storyboard = _make_storyboard([frame])
        ref = VideoRef(url="http://v", duration=5.0, last_frame_url=None)
        source._provider.generate_video = AsyncMock(return_value=ref)

        with (
            patch("app.gateway.video_gen.visual_sources.ai_video._localize_video", new_callable=AsyncMock, return_value="visuals/shot_0000.mp4"),
            # 主生成 5.0 → 续写后探测仍 10.0（残余缺口 2.0s > 0.5）
            patch("app.gateway.video_gen.visual_sources.ai_video._probe_video_seconds", side_effect=[5.0, 10.0]),
            patch("app.gateway.video_gen.visual_sources.ai_video._extract_tail_frame", return_value=True),
            patch("app.gateway.video_gen.visual_sources.ai_video._concat_videos_with_check", return_value=True),
        ):
            await source.render(storyboard, task_dir=tmp_path)

        kinds = [e.get("kind") for e in self._read_sidecar(tmp_path)]
        assert "video_extended" in kinds, f"续写成功应记 video_extended，实得 {kinds}"
        assert "visual_shortfall" in kinds, f"续写后残余缺口须同时记 SHORTFALL，实得 {kinds}"


class TestConcatToleranceAsymmetric:
    """C2（2026-08-14）：_concat_videos_with_check 容差非对称 [−0.3, +0.5]。

    只信不丢时长的产物：短 0.4s 超下限 0.3 必须拒绝（混规格段静默丢时长教训）；
    长 0.4s 在上限 0.5 内接受（截断无损）。
    """

    def _setup(self, tmp_path, monkeypatch, got: float):
        from app.gateway.video_gen.visual_sources import ai_video as mod

        seg_a = tmp_path / "a.mp4"
        seg_b = tmp_path / "b.mp4"
        out = tmp_path / "out.mp4"
        for p in (seg_a, seg_b):
            p.write_bytes(b"x" * 64)

        def _fake_run(cmd, **kw):
            out.write_bytes(b"y")
            return MagicMock(returncode=0, stderr=b"")

        monkeypatch.setattr(mod.subprocess, "run", _fake_run)
        monkeypatch.setattr(
            mod,
            "_probe_video_seconds",
            lambda p: {str(seg_a): 2.0, str(seg_b): 2.0, str(out): got}.get(str(p), 0.0),
        )
        return seg_a, seg_b, out

    def test_short_by_0_4s_rejected(self, tmp_path, monkeypatch):
        """expected=4.0，产物 3.6（−0.4 超下限 0.3）→ 拒绝。"""
        from app.gateway.video_gen.visual_sources.ai_video import _concat_videos_with_check

        seg_a, seg_b, out = self._setup(tmp_path, monkeypatch, got=3.6)
        assert _concat_videos_with_check(seg_a, seg_b, out) is False, "短 0.4s 超下限应拒绝"

    def test_long_by_0_4s_accepted(self, tmp_path, monkeypatch):
        """expected=4.0，产物 4.4（+0.4 在上限 0.5 内）→ 接受。"""
        from app.gateway.video_gen.visual_sources.ai_video import _concat_videos_with_check

        seg_a, seg_b, out = self._setup(tmp_path, monkeypatch, got=4.4)
        assert _concat_videos_with_check(seg_a, seg_b, out) is True, "长 0.4s 在上限内应接受"


class TestExtendSuccessUpdates:
    """续写成功 → 更新 frame。"""

    @pytest.mark.asyncio
    async def test_success_updates_frame_and_returns_true(self, tmp_path):
        source = _make_source()
        frame = _make_frame(duration=12.0)
        ref = VideoRef(url="http://v", duration=5.0, last_frame_url=None)
        storyboard = _make_storyboard([frame])

        orig_path = tmp_path / "visuals" / "shot_0000.mp4"
        orig_path.parent.mkdir(parents=True, exist_ok=True)
        orig_path.write_bytes(b"x" * 2048)

        with (
            patch("app.gateway.video_gen.visual_sources.ai_video._extract_tail_frame", return_value=True),
            patch("app.gateway.video_gen.visual_sources.ai_video._concat_videos_with_check", return_value=True),
            patch("app.gateway.video_gen.visual_sources.ai_video._probe_video_seconds", return_value=12.0),
            patch("app.gateway.video_gen.visual_sources.ai_video._localize_video", new_callable=AsyncMock, return_value="visuals/ext_0000.mp4"),
        ):
            result = await source._try_extend_video(
                frame,
                ref,
                gap=7.0,
                task_dir=tmp_path,
                original_rel="visuals/shot_0000.mp4",
                storyboard=storyboard,
            )

        assert result is True
        assert frame.local_visual_path == "visuals/extended_0000.mp4"
        assert frame.actual_visual_duration == 12.0
