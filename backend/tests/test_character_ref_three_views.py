"""B5a: ai_image 角色参考传三视图（front+side+back），不再只传正面。

修前 ``_character_ref_paths_from_payload`` 只返 ``[front]``，side/back 在 gateway 边界被丢。
Ark seedream 支持 ``image: [list]``（Pro≤10），故三视图可全传做角色一致性 i2i。
（ai_video 的三视图由 B3'+B0 覆盖：image_urls 经 _assign_roles 分 first_frame+reference_image。）
"""

from app.gateway.video_gen.service import _character_ref_paths_from_payload


def test_three_views_collected() -> None:
    refs = _character_ref_paths_from_payload({"character_sheets": [{"front_url": "/f", "side_url": "/s", "back_url": "/b"}]})
    assert refs == ["/f", "/s", "/b"]


def test_filters_empty_views() -> None:
    refs = _character_ref_paths_from_payload({"character_sheets": [{"front_url": "/f", "side_url": None, "back_url": None}]})
    assert refs == ["/f"]


def test_fallback_to_character_reference_single(tmp_path) -> None:
    """X11-4 坏路径守卫后：回退分支只收「既有文件/URL/data:」——纯文本不作 ref。"""
    f = tmp_path / "r.png"
    f.write_bytes(b"\x89PNG")
    assert _character_ref_paths_from_payload({"character_reference": str(f)}) == [str(f)]
    assert _character_ref_paths_from_payload({"character_reference": "https://x/c.png"}) == ["https://x/c.png"]
    # 非路径纯文本（字段 UI 语义=角色描述）→ 不作 ref（对齐 vimax 桥守卫语义）
    assert _character_ref_paths_from_payload({"character_reference": "一位戴眼镜的年轻女讲师"}) is None
    assert _character_ref_paths_from_payload({"character_reference": "/nonexistent/r.png"}) is None


def test_none_when_no_character() -> None:
    assert _character_ref_paths_from_payload({}) is None
