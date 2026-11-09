"""Task 2: 时长栅格对齐 + 缺口计算。"""

import pytest

from app.gateway.video_gen.duration_policy import align_to_provider_grid, duration_shortfall


def test_rounds_up_to_integer_second() -> None:
    """8.2s 旁白 → 请求 9s。向上取整：长了 worker 会 -t 截断（无损），短了才要定格。"""
    assert align_to_provider_grid(8.2, cap=15.0) == 9.0


def test_exact_integer_stays() -> None:
    assert align_to_provider_grid(9.0, cap=15.0) == 9.0


def test_clamped_by_cap() -> None:
    """>cap 的旁白必然要定格——但请求值必须钳到 cap，否则 Ark 报错。"""
    assert align_to_provider_grid(20.0, cap=15.0) == 15.0


def test_respects_floor() -> None:
    """Ark 下限 4s：2.5s 旁白也得请求 4s。"""
    assert align_to_provider_grid(2.5, cap=15.0, floor=4.0) == 4.0


def test_non_integer_provider_keeps_fraction() -> None:
    assert align_to_provider_grid(6.4, cap=8.0, integer_seconds=False) == 6.4


def test_zero_desired_falls_back_to_floor() -> None:
    assert align_to_provider_grid(0.0, cap=15.0, floor=4.0) == 4.0


@pytest.mark.parametrize(
    ("target", "actual", "expected"),
    [
        (8.9, 5.0, pytest.approx(3.9)),  # 用户实测那一帧
        (5.0, 5.0, 0.0),  # 精确
        (5.0, 5.02, 0.0),  # 容差内不算缺口
        (5.0, 9.0, 0.0),  # 源更长 → worker trim，无缺口
        (8.9, None, 0.0),  # 未探测 → 不猜
        (8.9, 0.0, 0.0),  # 探测失败 → 不猜
    ],
)
def test_shortfall(target, actual, expected) -> None:  # noqa: ANN001
    assert duration_shortfall(target, actual) == expected
