"""B0: seedance-ark 多图角色分配。

旧实现 `["first_frame","last_frame"][:n] if n<=2 else ["first_frame"]+["last_frame"]*(n-1)`
对 3+ 图给多个 last_frame（Ark 只允许 1 个 last_frame），且从不使用 reference_image 角色。
修复后：1→first_frame，2→first+last（ViMax 双帧回归保护），3+→first_frame+reference_image×(n-1)
（§2.2 多模态参考图 1-9，继承角色/风格/构图）。
"""

from app.gateway.video_gen.providers.seedance_ark import _assign_roles


def test_zero() -> None:
    assert _assign_roles(0) == []


def test_one_is_first_frame() -> None:
    assert _assign_roles(1) == ["first_frame"]


def test_two_is_first_and_last() -> None:  # ViMax 双帧回归保护
    assert _assign_roles(2) == ["first_frame", "last_frame"]


def test_three_is_first_plus_reference() -> None:
    assert _assign_roles(3) == [
        "first_frame",
        "reference_image",
        "reference_image",
    ]


def test_five_no_duplicate_last_frame() -> None:
    r = _assign_roles(5)
    assert r.count("last_frame") <= 1
    assert r[0] == "first_frame"
    assert r[1:] == ["reference_image"] * 4


def test_nine_within_reference_cap() -> None:  # §2.2 reference_image 1-9
    r = _assign_roles(9)
    assert r[0] == "first_frame"
    assert len(r) == 9
    assert all(x == "reference_image" for x in r[1:])


def test_eleven_images_capped_to_nine_references() -> None:
    """§2.2 reference_image 1-9；>10 图截断到 9 个 reference_image（first_frame+9=10 角色）。"""
    from app.gateway.video_gen.providers.seedance_ark import _assign_roles

    r = _assign_roles(11)
    assert r[0] == "first_frame"
    assert r.count("reference_image") == 9
    assert r.count("last_frame") == 0
    assert len(r) == 10
