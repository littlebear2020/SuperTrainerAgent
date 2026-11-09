# backend/tests/test_video_gen_preview_consistency.py
import inspect

from app.gateway.video_gen import service as svc


def test_preview_call_contract():
    """I 契约：preview 的 generate_image 调用须含 size 与 reference_image_paths 键
    （与 _regenerate_frame 对齐）。"""
    src = inspect.getsource(svc.VideoGenService._generate_frame_preview)
    assert "reference_image_paths=" in src, "preview 须透传 reference_image_paths（角色一致性）"
    assert "size=" in src, "preview 须透传 size（比例与 regenerate 一致）"


def test_preview_injects_character_context():
    """I: preview 须调 _inject_project_character_context + _character_ref_paths_from_payload。"""
    src = inspect.getsource(svc.VideoGenService._generate_frame_preview)
    assert "_inject_project_character_context" in src
    assert "_character_ref_paths_from_payload" in src
