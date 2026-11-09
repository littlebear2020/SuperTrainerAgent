"""Unit tests for ThreadState reducers."""

from typing import get_type_hints

from deerflow.agents.thread_state import (
    ThreadState,
    ViewedImageData,
    merge_artifacts,
    merge_todos,
    merge_viewed_images,
)


class TestMergeArtifacts:
    """Sanity check for the existing artifacts reducer."""

    def test_dedupes_and_preserves_order(self):
        assert merge_artifacts(["a", "b"], ["b", "c"]) == ["a", "b", "c"]

    def test_none_new_preserves_existing(self):
        assert merge_artifacts(["a"], None) == ["a"]

    def test_none_existing_accepts_new(self):
        assert merge_artifacts(None, ["a"]) == ["a"]


class TestMergeViewedImages:
    """Sanity check for the existing viewed_images reducer."""

    def test_merges_dicts(self):
        existing = {"k1": {"base64": "x", "mime_type": "image/png"}}
        new = {"k2": {"base64": "y", "mime_type": "image/jpeg"}}
        merged = merge_viewed_images(existing, new)
        assert set(merged.keys()) == {"k1", "k2"}

    def test_empty_dict_clears(self):
        existing = {"k1": {"base64": "x", "mime_type": "image/png"}}
        assert merge_viewed_images(existing, {}) == {}


class TestMergeViewedImagesRecency:
    """2026-08-18 (view_image 膨胀修复): reducer 改 recency 语义。

    ViewImageMiddleware 的 K 窗口按 dict 迭代序近似"最近 K 回合"——旧实现
    ``{**existing, **new}`` 对重看键保持**首见位置**（existing={A,B,C} +
    new={A,D} → A,B,C,D），导致已看 ≥K 张新图后重看旧图落在窗口外：模型刚调
    view_image(A) 却收到 A 的占位符，按指引重调位置不变 → view_image 自循环。
    新语义：被 new 覆盖的键 delete-then-reinsert 移到末尾。
    """

    @staticmethod
    def _meta(tag: str) -> dict:
        return {"mime_type": "image/png", "size": len(tag), "actual_path": f"/tmp/{tag}.png"}

    def test_new_keys_append_preserving_existing_order(self):
        existing = {"a": self._meta("a"), "b": self._meta("b")}
        new = {"c": self._meta("c")}
        assert list(merge_viewed_images(existing, new)) == ["a", "b", "c"]

    def test_reviewed_key_moves_to_end_and_new_value_wins(self):
        existing = {"a": self._meta("a"), "b": self._meta("b"), "c": self._meta("c")}
        re_viewed = self._meta("a-new")
        merged = merge_viewed_images(existing, {"a": re_viewed})
        assert list(merged) == ["b", "c", "a"]  # 重看键移末尾（旧实现为 a,b,c → 红）
        assert merged["a"] == re_viewed

    def test_mixed_new_and_reviewed(self):
        """v2 复审实证场景: existing={A,B,C} + new={A,D} → 旧实现 A,B,C,D。"""
        existing = {"A": self._meta("A"), "B": self._meta("B"), "C": self._meta("C")}
        new = {"A": self._meta("A2"), "D": self._meta("D")}
        assert list(merge_viewed_images(existing, new)) == ["B", "C", "A", "D"]

    def test_clear_semantics_unchanged(self):
        """``{}`` 全清分支不受 recency 改造影响。"""
        existing = {"a": self._meta("a"), "b": self._meta("b")}
        assert merge_viewed_images(existing, {}) == {}

    def test_none_existing_returns_new(self):
        new = {"a": self._meta("a")}
        assert merge_viewed_images(None, new) == new

    def test_none_new_returns_existing(self):
        existing = {"a": self._meta("a")}
        assert merge_viewed_images(existing, None) == existing


class TestViewedImageDataFields:
    """TypedDict 必须与 view_image_tool 实际写入的字段一致（R-11 元数据化）。"""

    def test_fields_match_tool_write_shape(self):
        assert set(ViewedImageData.__annotations__) == {"mime_type", "size", "actual_path"}


class TestThreadStateAnnotations:
    """Regression guards: ensure reducer wiring on ThreadState fields."""

    def test_artifacts_field_is_wired_to_merge_artifacts(self):
        """Sanity check that existing reducer wiring is preserved."""
        hints = get_type_hints(ThreadState, include_extras=True)
        assert merge_artifacts in hints["artifacts"].__metadata__


class TestMergeTodos:
    """R-03 defensive reducer (2026-08-13)."""

    def test_new_none_preserves_existing(self):
        existing = [{"id": "1", "content": "a", "status": "in_progress"}]
        assert merge_todos(existing, None) == existing

    def test_new_wins_over_existing(self):
        existing = [{"id": "1", "content": "a", "status": "in_progress"}]
        new = [{"id": "1", "content": "a", "status": "completed"}]
        assert merge_todos(existing, new) == new

    def test_explicit_empty_clears(self):
        existing = [{"id": "1", "content": "a", "status": "in_progress"}]
        assert merge_todos(existing, []) == []

    def test_both_none_returns_none(self):
        assert merge_todos(None, None) is None

    def test_todos_field_is_wired_to_merge_todos(self):
        """ThreadState.todos must carry the merge_todos reducer annotation."""
        hints = get_type_hints(ThreadState, include_extras=True)
        assert merge_todos in hints["todos"].__metadata__


class TestTodoMiddlewareStateSchema:
    def test_state_schema_is_thread_state(self):
        from deerflow.agents.middlewares.todo_middleware import TodoMiddleware

        assert TodoMiddleware.state_schema is ThreadState
