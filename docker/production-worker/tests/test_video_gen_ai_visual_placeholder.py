"""P0-4 layer 3: worker _collect_visual_assets 不丢帧——失败帧返回 placeholder asset。

根因：当前 `if not url and not local_path: continue` 跳过失败帧，但音频全长拼接
→ 视频短于音频 → 后半无画面。改为返回 placeholder=True 占位 asset。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from pipelines.video_gen_ai_visual import _collect_visual_assets  # noqa: E402


def test_failed_frame_kept_as_placeholder_not_skipped():
    """帧1 既无 url 无 local_path → 返回 placeholder asset，不丢失。"""
    payload = {
        "frames": [
            {"index": 0, "visual_url": "http://x/0.png", "duration": 5.0},
            {"index": 1, "duration": 5.0, "narration": "失败帧旁白"},  # 失败帧
            {"index": 2, "local_visual_path": "/tmp/2.png", "duration": 5.0},
        ]
    }
    assets = _collect_visual_assets(payload)
    assert len(assets) == 3, f"应返回 3 asset（含占位），实际 {len(assets)}"
    placeholders = [a for a in assets if a.get("placeholder")]
    assert len(placeholders) == 1, "失败帧应有 1 个占位"
    assert placeholders[0]["frame_index"] == 1
    assert placeholders[0]["duration"] == 5.0
    assert placeholders[0]["narration"] == "失败帧旁白"


def test_total_duration_preserved_with_placeholder():
    """占位帧 duration 计入，总时长不被缩短（音画等长）。"""
    payload = {
        "frames": [
            {"index": 0, "visual_url": "http://x/0.png", "duration": 10.0},
            {"index": 1, "duration": 20.0},  # 失败帧占位 20s
        ]
    }
    assets = _collect_visual_assets(payload)
    total = sum(a["duration"] for a in assets)
    assert total == 30.0


def test_normal_frames_unaffected():
    """有 url/local_path 的帧不受影响（placeholder=False）。"""
    payload = {
        "frames": [
            {"index": 0, "visual_url": "http://x/0.png", "duration": 5.0},
            {"index": 1, "local_visual_path": "/tmp/1.png", "duration": 5.0},
        ]
    }
    assets = _collect_visual_assets(payload)
    assert all(not a.get("placeholder") for a in assets)


def test_ai_visual_imports_shared_finalizer():
    """v1 改调共享 finalizer：模块须 import _apply_colorgrade_and_subtitles / _apply_intro_outro_fade。"""
    import pipelines.video_gen_ai_visual as mod
    assert "_apply_colorgrade_and_subtitles" in dir(mod)
    assert "_apply_intro_outro_fade" in dir(mod)


def test_legacy_shots_image_branch_no_seg_idx_nameerror(tmp_path):
    """B2: legacy `shots` 分支 image shot 不再 seg_idx NameError。

    修前该分支仅 `i`（enumerate）定义，line 384 却用 `frame_index=seg_idx`→NameError（latent：
    gateway 总发 frames 不触发，但 landmine）。桩 pipeline helpers（非 subprocess.run——真实 ffmpeg
    由 Task6 覆盖），仅验编排不再 NameError。
    """
    from unittest.mock import patch
    from pipelines.video_gen_ai_visual import compose_ai_visual
    payload = {"shots": [{"url": "http://x/0.png", "type": "image", "duration": 5.0}]}
    with patch("pipelines.video_gen_ai_visual._download_asset"), \
         patch("pipelines.video_gen_ai_visual._image_to_video"), \
         patch("pipelines.video_gen_ai_visual._ffmpeg_concat"), \
         patch("pipelines.video_gen_ai_visual._apply_colorgrade_and_subtitles", side_effect=lambda c, **k: c), \
         patch("pipelines.video_gen_ai_visual._apply_intro_outro_fade", side_effect=lambda c, **k: c), \
         patch("pipelines.video_gen_ai_visual._ffmpeg_convert_format", side_effect=lambda c, out, **k: out), \
         patch("pipelines.video_gen_ai_visual._finalize_output", side_effect=lambda r, fr: fr), \
         patch("pipelines.video_gen_ai_visual._ffmpeg_extract_thumbnail"):
        result = compose_ai_visual(tmp_path, payload, {}, lambda v: None)
    assert result is not None  # 修前 NameError；修后返回 final_raw 路径
