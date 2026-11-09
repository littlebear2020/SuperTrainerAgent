"""T5（2026-08-14 计划）：帧拆分落盘 + 合并错位修复。

修前 ``_split_long_narration_frames`` 只改内存，``_merge_audio_into_storyboard`` 按
``frame.index`` 键合并进磁盘 storyboard.json——拆分后磁盘 125 帧 vs 内存 140 帧重编号，
第一个拆分点之后的所有帧音频元数据全部错位（磁盘帧 j 拿到的是内存帧 j 的音频，但两者
旁白已不同）。渲染当轮用内存对象不受影响，但磁盘 storyboard 的 audio 字段被污染 →
GET 分镜音频预览与 recompose 的时长真值都错。
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.gateway.video_gen.config import VideoGenConfig
from app.gateway.video_gen.providers.registry import ProviderRegistry
from app.gateway.video_gen.service import VideoGenService
from app.gateway.video_gen.storyboard import SceneGroup, Storyboard, StoryboardConfig, StoryboardFrame

# 30 字无标点段 ×3，共 92 字 > 预算 round(12s×3.33×1.2)=48 → 在标点边界拆成 3 段
_SEG = "一二三四五六七八九十一二三四五六七八九十一二三四五六七八九十"
_LONG = f"{_SEG}。{_SEG}。{_SEG}"


def _make_service(tmp_path) -> VideoGenService:
    cfg = VideoGenConfig(qihu_api_key="t")
    providers = ProviderRegistry(cfg)
    return VideoGenService(
        repo=MagicMock(),
        config=cfg,
        providers=providers,
        storage_dir=str(tmp_path),
        sandbox_provider=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_split_frames_persisted_and_merge_aligned(tmp_path):
    """磁盘 3 帧 → 内存拆成 5 帧 → 磁盘必须同步 5 帧且逐帧音频元数据与**自己的**旁白一致。"""
    from app.gateway.video_gen.orchestration.tts import TTSResult

    svc = _make_service(tmp_path)
    sb = Storyboard(
        title="t",
        config=StoryboardConfig(video_type="ai_image", width=1080, height=1920),
        frames=[
            StoryboardFrame(index=0, narration="开场白。"),
            StoryboardFrame(index=1, narration=_LONG),
            StoryboardFrame(index=2, narration="结尾白。"),
        ],
    )
    (tmp_path / "storyboard.json").write_text(json.dumps(sb.to_dict(), ensure_ascii=False), encoding="utf-8")

    counter = {"n": 0}

    async def _fake_synth(text, **kwargs):
        counter["n"] += 1
        out = tmp_path / f"tts_{counter['n']}.mp3"
        out.write_bytes(b"fake")
        # 时长 = 旁白长度——错位时磁盘帧拿到的 duration 会暴露「拿到了别人的音频」
        return TTSResult(audio_path=str(out), srt="", duration=float(len(text)))

    with (
        patch("app.gateway.video_gen.orchestration.tts.synthesize", side_effect=_fake_synth),
        patch("app.gateway.video_gen.service.run_ffmpeg", new=AsyncMock()),
        patch("app.gateway.video_gen.service._write_storyboard_srt"),
        patch("app.gateway.video_gen.service._precompute_llm_chunks", new=AsyncMock(return_value={})),
    ):
        await svc._synthesize_audio(sb, tmp_path, "zh-CN-YunjianNeural")
        await svc._merge_audio_into_storyboard(sb, tmp_path)

    disk = json.loads((tmp_path / "storyboard.json").read_text(encoding="utf-8"))
    disk_frames = disk["frames"]

    # 拆分后内存 5 帧：开场白 / 段1 / 段2 / 段3 / 结尾白
    assert len(sb.frames) == 5
    # 修复核心断言①：磁盘帧数与内存同代（修前磁盘仍 3 帧——拆分不落盘）
    assert len(disk_frames) == 5, "拆分结果必须落盘（修前磁盘仍 3 帧，merge 的 index 键两侧不同代）"
    # 修复核心断言②：逐帧音频元数据与**自己的**旁白一致
    # （修前拆分点后磁盘帧拿到内存同 index 帧的音频，duration 与自身旁白长度不符）
    for f in disk_frames:
        narration = f["narration"]
        assert f["duration"] == float(len(narration)), f"磁盘帧 {f['index']} 的 duration 与自身旁白不符：{narration[:20]}... -> {f['duration']}"
    # 旁白序列与内存一致（重编号后 0..4 连续）
    assert [f["narration"] for f in disk_frames] == [f.narration for f in sb.frames]


@pytest.mark.asyncio
async def test_split_not_applied_when_spf_set(tmp_path):
    """spf 显式设置时是用户明确意愿——不拆帧、不落盘改动（C1 豁免语义保留）。"""
    svc = _make_service(tmp_path)
    sb = Storyboard(
        title="t",
        config=StoryboardConfig(video_type="ai_image", width=1080, height=1920),
        frames=[StoryboardFrame(index=0, narration=_LONG)],
    )
    (tmp_path / "storyboard.json").write_text(json.dumps(sb.to_dict(), ensure_ascii=False), encoding="utf-8")

    from app.gateway.video_gen.orchestration.tts import TTSResult

    async def _fake_synth(text, **kwargs):
        out = tmp_path / "tts.mp3"
        out.write_bytes(b"fake")
        return TTSResult(audio_path=str(out), srt="", duration=float(len(text)))

    with (
        patch("app.gateway.video_gen.orchestration.tts.synthesize", side_effect=_fake_synth),
        patch("app.gateway.video_gen.service.run_ffmpeg", new=AsyncMock()),
        patch("app.gateway.video_gen.service._write_storyboard_srt"),
        patch("app.gateway.video_gen.service._precompute_llm_chunks", new=AsyncMock(return_value={})),
    ):
        await svc._synthesize_audio(sb, tmp_path, "zh-CN-YunjianNeural", seconds_per_frame=8.0)

    assert len(sb.frames) == 1, "spf 显式设置时不拆帧"
    disk = json.loads((tmp_path / "storyboard.json").read_text(encoding="utf-8"))
    assert len(disk["frames"]) == 1


# ---------------------------------------------------------------------------
# 审查 Minor 2（2026-08-14）：草稿后台音频的 merge 必须持合成锁。
# 修前 _generate_draft_audio 的 [synth → merge] 不原子——merge 在锁外，
# 「草稿 TTS 未跑完时用户确认渲染」可与 render 代交错、用旧代音频元数据覆写编辑帧。
# ---------------------------------------------------------------------------


class _DraftRepo:
    """_generate_draft_audio 所需的最小 repo：get 返回带 payload 的行，update 记录。"""

    def __init__(self, payload: str = "{}"):
        self._payload = payload
        self.updated: list = []

    async def get(self, task_id, user_id=None):
        from types import SimpleNamespace

        return SimpleNamespace(payload=self._payload)

    async def update(self, task_id, **kwargs):
        self.updated.append(kwargs)


@pytest.mark.asyncio
async def test_draft_audio_merge_runs_under_synth_lock(tmp_path):
    """草稿 merge 执行时合成锁必须被持有（synth+merge 整段原子）。"""

    from app.gateway.video_gen import service as svc_mod
    from app.gateway.video_gen.orchestration.tts import TTSResult

    repo = _DraftRepo()
    svc = _make_service(tmp_path)
    svc.repo = repo  # type: ignore[assignment]

    sb = Storyboard(
        title="t",
        config=StoryboardConfig(video_type="ai_image", width=1080, height=1920),
        frames=[StoryboardFrame(index=0, narration="你好。", duration=5.0)],
    )
    (tmp_path / "storyboard.json").write_text(json.dumps(sb.to_dict(), ensure_ascii=False), encoding="utf-8")

    observed = {}

    async def _spy_merge(storyboard, task_dir, **kwargs):
        observed["locked"] = svc_mod._synth_lock_for(str(task_dir)).locked()
        observed["verify_drift"] = kwargs.get("verify_drift")
        return 1, []

    async def _fake_synth(text, **kwargs):
        out = tmp_path / "audio" / "f.mp3"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(b"fake")
        return TTSResult(audio_path=str(out), srt="", duration=1.0)

    with (
        patch.object(svc, "_merge_audio_into_storyboard", side_effect=_spy_merge),
        patch("app.gateway.video_gen.orchestration.tts.synthesize", side_effect=_fake_synth),
        patch("app.gateway.video_gen.service.run_ffmpeg", new=AsyncMock()),
        patch("app.gateway.video_gen.service._write_storyboard_srt"),
        patch("app.gateway.video_gen.service._precompute_llm_chunks", new=AsyncMock(return_value={})),
    ):
        await svc._generate_draft_audio("vg_t1", tmp_path, user_id="u1")

    assert observed["locked"] is True, "草稿 merge 必须在合成锁内（synth+merge 整段原子，修前在锁外）"
    # 2026-10-06 漂移批 v2：draft 调用点必须显式传 verify_drift=True（其余 4 调用点默认关）
    assert observed["verify_drift"] is True, "draft 路径必须启用合并漂移校验"
    assert repo.updated and repo.updated[-1]["payload"], "audio_ready 落库"


@pytest.mark.asyncio
async def test_split_remaps_scene_groups_and_persists(tmp_path):
    """A-1: 拆帧后 scene_groups.frame_indices 重映射——内存与磁盘一致、连续覆盖新帧表、无旧序号残留、无帧遗漏。"""
    svc = _make_service(tmp_path)
    sb = Storyboard(
        title="t",
        config=StoryboardConfig(video_type="ai_image", width=1080, height=1920),
        frames=[
            StoryboardFrame(index=0, narration="开场白。"),
            StoryboardFrame(index=1, narration=_LONG),  # 拆成 3 段（见文件头注释）
            StoryboardFrame(index=2, narration="结尾白。"),
        ],
        scene_groups=[
            SceneGroup(index=0, title="g0", frame_indices=[0, 1]),
            SceneGroup(index=1, title="g1", frame_indices=[2]),
        ],
    )
    (tmp_path / "storyboard.json").write_text(json.dumps(sb.to_dict(), ensure_ascii=False), encoding="utf-8")

    await svc._split_long_narration_frames(sb, tmp_path)

    # 新帧表 5 帧（0=开场白, 1-3=拆段, 4=结尾白）；帧 1 的新段归属原 group 0
    assert len(sb.frames) == 5
    assert [g.frame_indices for g in sb.scene_groups] == [[0, 1, 2, 3], [4]], "内存 groups 已重映射"
    disk = json.loads((tmp_path / "storyboard.json").read_text(encoding="utf-8"))
    assert [g["frame_indices"] for g in disk["scene_groups"]] == [[0, 1, 2, 3], [4]], "磁盘 groups 与内存一致"
    covered = sorted(i for g in disk["scene_groups"] for i in g["frame_indices"])
    assert covered == list(range(len(sb.frames))), "新帧全被分组覆盖、无遗漏"


@pytest.mark.asyncio
async def test_split_new_frames_carry_estimated_duration(tmp_path):
    """B-4.1: 拆出新帧预置估算时长——修前 duration=0.0，拆分瞬间落盘的
    total_duration 偏低（TTS merge 前的窗口内）。"""
    svc = _make_service(tmp_path)
    sb = Storyboard(
        title="t",
        config=StoryboardConfig(video_type="ai_image", width=1080, height=1920),
        frames=[StoryboardFrame(index=0, narration=_LONG, duration=9.0)],
    )
    (tmp_path / "storyboard.json").write_text(json.dumps(sb.to_dict(), ensure_ascii=False), encoding="utf-8")
    await svc._split_long_narration_frames(sb, tmp_path)
    assert len(sb.frames) == 3
    assert all(f.duration > 0 for f in sb.frames), "新帧须有估算时长"
    assert sb.total_duration == sum(f.duration for f in sb.frames)
    disk = json.loads((tmp_path / "storyboard.json").read_text(encoding="utf-8"))
    assert disk["total_duration"] == sb.total_duration


@pytest.mark.asyncio
async def test_split_count_excludes_empty_segments(tmp_path, monkeypatch):
    """B-4.2: 空段 continue 不计入拆镜数——修前 len(segments)-1 虚高，sidecar 误报。"""
    svc = _make_service(tmp_path)
    sb = Storyboard(
        title="t",
        config=StoryboardConfig(video_type="ai_image", width=1080, height=1920),
        frames=[StoryboardFrame(index=0, narration="占位长旁白。")],
    )
    (tmp_path / "storyboard.json").write_text(json.dumps(sb.to_dict(), ensure_ascii=False), encoding="utf-8")
    details: list[str] = []
    monkeypatch.setattr("app.gateway.video_gen.service.record_degradation", lambda *a, **k: details.append(k.get("detail", "")))
    monkeypatch.setattr(
        "app.gateway.video_gen.orchestration.script.split_long_narration",
        lambda text, budget_chars: ["段一。", "段二。", ""],  # 尾空段被 strip 掉
    )
    await svc._split_long_narration_frames(sb, tmp_path)
    assert len(sb.frames) == 2, "空段不落表"
    assert any("1 个超长旁白帧" in d for d in details), f"拆镜数按实际落表计（2-1=1）：{details}"


@pytest.mark.asyncio
async def test_visual_artifacts_inherit_first_landed_segment(tmp_path, monkeypatch):
    """B-4.3: 视觉产物由首个**实际落表**的段继承——修前按 seg_idx==0 判定，
    首段 strip 为空的极端边界下视觉产物全丢。（T1 起，后续段同样继承预览图
    三字段——见 test_split_new_segments_inherit_preview_fields。）"""
    svc = _make_service(tmp_path)
    sb = Storyboard(
        title="t",
        config=StoryboardConfig(video_type="ai_image", width=1080, height=1920),
        frames=[StoryboardFrame(index=0, narration="占位。", visual_url="https://cdn/x.mp4", visual_type="video", local_visual_path="visuals/shot_0000.mp4")],
    )
    (tmp_path / "storyboard.json").write_text(json.dumps(sb.to_dict(), ensure_ascii=False), encoding="utf-8")
    monkeypatch.setattr(
        "app.gateway.video_gen.orchestration.script.split_long_narration",
        lambda text, budget_chars: ["", "段一。", "段二。"],  # 首空段
    )
    await svc._split_long_narration_frames(sb, tmp_path)
    assert sb.frames[0].visual_url == "https://cdn/x.mp4", "首个落表段（原 seg_idx=1）继承视觉产物"
    assert sb.frames[0].local_visual_path == "visuals/shot_0000.mp4"
    assert sb.frames[1].visual_url == "https://cdn/x.mp4", "T1：后续段同样继承（同场景延续）"
    assert sb.frames[1].local_visual_path == "visuals/shot_0000.mp4"


@pytest.mark.asyncio
async def test_split_new_segments_inherit_preview_fields(tmp_path):
    """T1（2026-09-01 拆帧计划）：拆出的**每一段**继承原帧预览图三字段（引用
    同一文件，不复制）——修前仅首个落表段继承，后续段无预览图 → 机制 M 的
    i2v 首帧条件（visual_type=="image" and local_visual_path）不成立 →
    角色三视图合成图兜底作 H3 首帧（vg_22ee06b39d19 开头闪现三视图）。

    actual_visual_duration 仍只归首个落表段——它是原资产 ffprobe 实测时长，
    后续段视频将新生成，继承会毒化 shortfall 判定。"""
    svc = _make_service(tmp_path)
    sb = Storyboard(
        title="t",
        config=StoryboardConfig(video_type="ai_video", width=1080, height=1920),
        frames=[
            StoryboardFrame(
                index=0,
                narration=_LONG,
                visual_url="https://cdn/preview_0001.png",
                visual_type="image",
                local_visual_path="visuals/preview_0001.png",
                actual_visual_duration=4.5,
            )
        ],
    )
    (tmp_path / "storyboard.json").write_text(json.dumps(sb.to_dict(), ensure_ascii=False), encoding="utf-8")
    await svc._split_long_narration_frames(sb, tmp_path)

    assert len(sb.frames) == 3
    for i, f in enumerate(sb.frames):
        assert f.visual_url == "https://cdn/preview_0001.png", f"段 {i} 须继承预览图 URL"
        assert f.visual_type == "image", f"段 {i} 须继承 visual_type"
        assert f.local_visual_path == "visuals/preview_0001.png", f"段 {i} 须继承本地预览图路径"
    assert sb.frames[0].actual_visual_duration == 4.5, "首个落表段保留原资产实测时长"
    assert sb.frames[1].actual_visual_duration is None, "后续段不继承实测时长（视频将新生成）"
    assert sb.frames[2].actual_visual_duration is None
    # 落盘一致（T5 拆分即落盘——磁盘新段同样携带预览图字段）
    disk = json.loads((tmp_path / "storyboard.json").read_text(encoding="utf-8"))
    assert all(f["visual_url"] == "https://cdn/preview_0001.png" for f in disk["frames"]), "磁盘新段同样继承"


@pytest.mark.asyncio
async def test_split_no_visual_product_new_segments_stay_empty(tmp_path):
    """T1：原帧本身无预览图 → 新段保持空（现状兜底链不变，不更坏）。"""
    svc = _make_service(tmp_path)
    sb = Storyboard(
        title="t",
        config=StoryboardConfig(video_type="ai_video", width=1080, height=1920),
        frames=[StoryboardFrame(index=0, narration=_LONG)],
    )
    (tmp_path / "storyboard.json").write_text(json.dumps(sb.to_dict(), ensure_ascii=False), encoding="utf-8")
    await svc._split_long_narration_frames(sb, tmp_path)

    assert len(sb.frames) == 3
    assert all(f.visual_url is None and f.local_visual_path is None and f.visual_type is None for f in sb.frames), "无产物不凭空造"
