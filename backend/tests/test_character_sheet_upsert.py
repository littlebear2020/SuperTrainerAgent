"""R2: _upsert_sheet 纯函数——append 或 replace at index（原子替换，修 Finding 2）。"""

from app.gateway.routers.video_gen import _upsert_sheet


def test_append_when_no_replace_index() -> None:
    assert _upsert_sheet([{"n": "a"}], {"n": "b"}) == [{"n": "a"}, {"n": "b"}]


def test_replace_at_index() -> None:
    assert _upsert_sheet([{"n": "a"}, {"n": "old"}], {"n": "new"}, 1) == [{"n": "a"}, {"n": "new"}]


def test_replace_out_of_range_falls_back_append() -> None:
    assert _upsert_sheet([{"n": "a"}], {"n": "b"}, 5) == [{"n": "a"}, {"n": "b"}]


def test_replace_negative_falls_back_append() -> None:
    assert _upsert_sheet([{"n": "a"}], {"n": "b"}, -1) == [{"n": "a"}, {"n": "b"}]


def test_non_int_replace_index_falls_back_append() -> None:
    """非整数 replace_index（客户端误传字符串/浮点/布尔）→ 安全兜底 append，不抛 TypeError→500。"""
    assert _upsert_sheet([{"n": "a"}], {"n": "b"}, "1") == [{"n": "a"}, {"n": "b"}]
    assert _upsert_sheet([{"n": "a"}], {"n": "b"}, 1.5) == [{"n": "a"}, {"n": "b"}]
    assert _upsert_sheet([{"n": "a"}], {"n": "b"}, True) == [{"n": "a"}, {"n": "b"}]
