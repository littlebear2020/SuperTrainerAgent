"""Core behaviour tests for WorkspaceInventoryMiddleware.

Covers:
- before_agent: injection of a <workspace_files> block listing the thread's
  workspace/outputs directories (entries, sizes, dir markers, depth limits,
  manifest detection, guidance line)
- entry/char caps with truncation marker
- empty/missing dirs → no injection, never raises
- prepend mechanics (string & list content, preserved id/additional_kwargs)
- zero per-round graph node guarantee (no before_model/after_model overrides)
- registration in build_lead_runtime_middlewares right after UploadsMiddleware
"""

import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import AIMessage, HumanMessage

from deerflow.agents.middlewares.uploads_middleware import UploadsMiddleware
from deerflow.agents.middlewares.workspace_inventory_middleware import (
    _MAX_CHARS,
    _MAX_ENTRIES,
    WorkspaceInventoryMiddleware,
)
from deerflow.config.paths import Paths

THREAD_ID = "thread-abc123"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _middleware(tmp_path: Path) -> WorkspaceInventoryMiddleware:
    return WorkspaceInventoryMiddleware(base_dir=str(tmp_path))


def _runtime(thread_id: str | None = THREAD_ID) -> MagicMock:
    rt = MagicMock()
    rt.context = {"thread_id": thread_id}
    return rt


def _workspace_dir(tmp_path: Path, thread_id: str = THREAD_ID) -> Path:
    from deerflow.runtime.user_context import get_effective_user_id

    d = Paths(str(tmp_path)).sandbox_work_dir(thread_id, user_id=get_effective_user_id())
    d.mkdir(parents=True, exist_ok=True)
    return d


def _outputs_dir(tmp_path: Path, thread_id: str = THREAD_ID) -> Path:
    from deerflow.runtime.user_context import get_effective_user_id

    d = Paths(str(tmp_path)).sandbox_outputs_dir(thread_id, user_id=get_effective_user_id())
    d.mkdir(parents=True, exist_ok=True)
    return d


def _state(*messages):
    return {"messages": list(messages)}


def _injected_content(result) -> str:
    updated = result["messages"][-1].content
    if isinstance(updated, str):
        return updated
    return "\n".join(block.get("text", "") for block in updated if isinstance(block, dict))


# ---------------------------------------------------------------------------
# before_agent — happy path
# ---------------------------------------------------------------------------


class TestBeforeAgentInjection:
    def test_populated_workspace_and_outputs_produce_block(self, tmp_path):
        mw = _middleware(tmp_path)
        ws = _workspace_dir(tmp_path)
        (ws / "notes.md").write_bytes(b"hello world")
        clips = ws / "clips"
        clips.mkdir()
        (clips / "clip_001.mp4").write_bytes(b"x" * 2048)
        out = _outputs_dir(tmp_path)
        (out / "final.mp4").write_bytes(b"y" * 4096)

        result = mw.before_agent(_state(HumanMessage(content="继续")), _runtime())

        assert result is not None
        content = _injected_content(result)
        assert "<workspace_files>" in content
        assert "</workspace_files>" in content
        assert "/mnt/user-data/workspace/" in content
        assert "/mnt/user-data/outputs/" in content
        assert "notes.md" in content
        assert "clips/" in content  # dirs carry a trailing slash
        assert "clip_001.mp4" in content
        assert "final.mp4" in content
        assert "KB" in content  # sizes are rendered

    def test_manifest_detection_known_and_glob(self, tmp_path):
        mw = _middleware(tmp_path)
        ws = _workspace_dir(tmp_path)
        (ws / "sections_manifest.json").write_bytes(b"{}")
        (ws / "clips_manifest.json").write_bytes(b"{}")
        (ws / "custom_manifest.json").write_bytes(b"{}")

        result = mw.before_agent(_state(HumanMessage(content="go")), _runtime())

        content = _injected_content(result)
        assert "Manifest: sections_manifest.json" in content
        assert "Manifest: clips_manifest.json" in content
        assert "Manifest: custom_manifest.json" in content
        assert "mtime" in content

    def test_manifest_glob_deduplicates_known_names(self, tmp_path):
        """Known manifests also match the *_manifest.json glob — report once."""
        mw = _middleware(tmp_path)
        ws = _workspace_dir(tmp_path)
        (ws / "sections_manifest.json").write_bytes(b"{}")

        result = mw.before_agent(_state(HumanMessage(content="go")), _runtime())

        content = _injected_content(result)
        assert content.count("Manifest: sections_manifest.json") == 1

    def test_no_manifest_line_when_absent(self, tmp_path):
        mw = _middleware(tmp_path)
        ws = _workspace_dir(tmp_path)
        (ws / "notes.md").write_bytes(b"x")

        result = mw.before_agent(_state(HumanMessage(content="go")), _runtime())

        assert "Manifest:" not in _injected_content(result)

    def test_manifest_in_subdirectory_not_detected(self, tmp_path):
        mw = _middleware(tmp_path)
        ws = _workspace_dir(tmp_path)
        sub = ws / "sub"
        sub.mkdir()
        (sub / "sections_manifest.json").write_bytes(b"{}")

        result = mw.before_agent(_state(HumanMessage(content="go")), _runtime())

        assert "Manifest:" not in _injected_content(result)

    def test_guidance_line_present(self, tmp_path):
        mw = _middleware(tmp_path)
        ws = _workspace_dir(tmp_path)
        (ws / "notes.md").write_bytes(b"x")

        result = mw.before_agent(_state(HumanMessage(content="go")), _runtime())

        assert "续作时先盘点以上已有产物，能复用就复用，不要重做已完成的步骤。" in _injected_content(result)

    def test_workspace_recurses_two_levels_only(self, tmp_path):
        mw = _middleware(tmp_path)
        ws = _workspace_dir(tmp_path)
        level1 = ws / "level1"
        level2 = level1 / "level2"
        level2.mkdir(parents=True)
        (level1 / "l1.txt").write_bytes(b"x")
        (level2 / "deep.txt").write_bytes(b"x")

        result = mw.before_agent(_state(HumanMessage(content="go")), _runtime())

        content = _injected_content(result)
        assert "level1/" in content
        assert "l1.txt" in content
        assert "level2/" in content
        assert "deep.txt" not in content  # depth 3 is beyond the limit

    def test_outputs_listing_is_one_level(self, tmp_path):
        mw = _middleware(tmp_path)
        _workspace_dir(tmp_path)  # workspace exists but empty
        out = _outputs_dir(tmp_path)
        sub = out / "subdir"
        sub.mkdir()
        (sub / "nested.txt").write_bytes(b"x")

        result = mw.before_agent(_state(HumanMessage(content="go")), _runtime())

        content = _injected_content(result)
        assert "subdir/" in content
        assert "nested.txt" not in content

    def test_hidden_entries_are_skipped(self, tmp_path):
        mw = _middleware(tmp_path)
        ws = _workspace_dir(tmp_path)
        (ws / ".DS_Store").write_bytes(b"junk")
        (ws / "visible.txt").write_bytes(b"x")

        result = mw.before_agent(_state(HumanMessage(content="go")), _runtime())

        content = _injected_content(result)
        assert ".DS_Store" not in content
        assert "visible.txt" in content

    def test_workspace_only_without_outputs_dir(self, tmp_path):
        mw = _middleware(tmp_path)
        ws = _workspace_dir(tmp_path)
        (ws / "a.txt").write_bytes(b"x")

        result = mw.before_agent(_state(HumanMessage(content="go")), _runtime())

        content = _injected_content(result)
        assert "/mnt/user-data/workspace/" in content
        assert "/mnt/user-data/outputs/" not in content

    def test_outputs_only_without_workspace_dir(self, tmp_path):
        mw = _middleware(tmp_path)
        out = _outputs_dir(tmp_path)
        (out / "b.txt").write_bytes(b"x")

        result = mw.before_agent(_state(HumanMessage(content="go")), _runtime())

        content = _injected_content(result)
        assert "/mnt/user-data/outputs/" in content
        assert "b.txt" in content


# ---------------------------------------------------------------------------
# Caps
# ---------------------------------------------------------------------------


class TestCaps:
    def test_entry_cap_truncates(self, tmp_path):
        mw = _middleware(tmp_path)
        ws = _workspace_dir(tmp_path)
        for i in range(_MAX_ENTRIES + 10):
            (ws / f"f{i:03d}.txt").write_bytes(b"x")

        result = mw.before_agent(_state(HumanMessage(content="go")), _runtime())

        content = _injected_content(result)
        listed = [line for line in content.splitlines() if line.strip().startswith("- ")]
        assert len(listed) == _MAX_ENTRIES
        assert "... (truncated)" in content

    def test_char_cap_truncates(self, tmp_path):
        mw = _middleware(tmp_path)
        ws = _workspace_dir(tmp_path)
        # Long filenames: each listing line is ~230 chars, so the char budget
        # is exhausted well before the entry budget.
        for i in range(30):
            (ws / (f"f{i:03d}_" + "a" * 200 + ".txt")).write_bytes(b"x")

        result = mw.before_agent(_state(HumanMessage(content="go")), _runtime())

        content = _injected_content(result)
        listing_chars = sum(len(line) + 1 for line in content.splitlines() if line.strip().startswith("- "))
        assert listing_chars <= _MAX_CHARS
        assert "... (truncated)" in content
        assert "f029_" not in content  # later entries never made it in

    def test_no_truncation_marker_when_under_caps(self, tmp_path):
        mw = _middleware(tmp_path)
        ws = _workspace_dir(tmp_path)
        (ws / "a.txt").write_bytes(b"x")

        result = mw.before_agent(_state(HumanMessage(content="go")), _runtime())

        assert "... (truncated)" not in _injected_content(result)

    def test_manifest_lines_share_the_budget(self, tmp_path):
        """Manifest lines must not break the block's char promise.

        40 manifests with 74-char names: the walk lists them all (40 entries,
        ~3440 chars, under both caps), leaving ~560 chars — so only ~4 of the
        40 manifest lines fit; the rest must be cut instead of overflowing the
        4000-char block promise.
        """
        mw = _middleware(tmp_path)
        ws = _workspace_dir(tmp_path)
        for i in range(40):
            (ws / (f"m{i:02d}_" + "a" * 55 + "_manifest.json")).write_bytes(b"{}")

        result = mw.before_agent(_state(HumanMessage(content="go")), _runtime())

        content = _injected_content(result)
        manifest_lines = [line for line in content.splitlines() if line.startswith("Manifest: ")]
        assert 0 < len(manifest_lines) < 40
        assert "... (truncated)" in content


# ---------------------------------------------------------------------------
# Empty / missing / error paths
# ---------------------------------------------------------------------------


class TestEmptyAndMissing:
    def test_empty_workspace_and_outputs_inject_nothing(self, tmp_path):
        mw = _middleware(tmp_path)
        _workspace_dir(tmp_path)
        _outputs_dir(tmp_path)

        assert mw.before_agent(_state(HumanMessage(content="go")), _runtime()) is None

    def test_missing_dirs_inject_nothing(self, tmp_path):
        mw = _middleware(tmp_path)  # no thread dirs created at all

        assert mw.before_agent(_state(HumanMessage(content="go")), _runtime()) is None

    def test_returns_none_when_messages_empty(self, tmp_path):
        mw = _middleware(tmp_path)
        assert mw.before_agent({"messages": []}, _runtime()) is None

    def test_returns_none_when_last_message_is_not_human(self, tmp_path):
        mw = _middleware(tmp_path)
        ws = _workspace_dir(tmp_path)
        (ws / "a.txt").write_bytes(b"x")
        state = _state(HumanMessage(content="q"), AIMessage(content="a"))

        assert mw.before_agent(state, _runtime()) is None

    def test_returns_none_when_thread_id_missing(self, tmp_path):
        mw = _middleware(tmp_path)
        assert mw.before_agent(_state(HumanMessage(content="go")), _runtime(thread_id=None)) is None

    def test_filesystem_error_never_raises(self, tmp_path, monkeypatch):
        mw = _middleware(tmp_path)

        def _boom(*args, **kwargs):
            raise OSError("disk exploded")

        monkeypatch.setattr(mw._paths, "sandbox_work_dir", _boom)

        assert mw.before_agent(_state(HumanMessage(content="go")), _runtime()) is None

    def test_unreadable_directory_is_skipped(self, tmp_path):
        # Root-safety: chmod(0) does not make a directory unreadable for root
        # (CAP_DAC_OVERRIDE bypasses file permission bits), so under rootful CI
        # containers the walk would succeed, "secret.txt" would show up, and
        # this test would false-red. Skip there — the OSError-swallow contract
        # stays covered everywhere by test_filesystem_error_never_raises.
        geteuid = getattr(os, "geteuid", None)
        if geteuid is not None and geteuid() == 0:
            pytest.skip("chmod 0 is ineffective as root; cannot simulate an unreadable directory via fs perms")
        mw = _middleware(tmp_path)
        ws = _workspace_dir(tmp_path)
        locked = ws / "locked"
        locked.mkdir()
        (locked / "secret.txt").write_bytes(b"x")
        (ws / "ok.txt").write_bytes(b"x")
        os.chmod(locked, 0)
        try:
            result = mw.before_agent(_state(HumanMessage(content="go")), _runtime())
        finally:
            os.chmod(locked, 0o755)

        assert result is not None
        content = _injected_content(result)
        assert "ok.txt" in content
        assert "secret.txt" not in content


# ---------------------------------------------------------------------------
# Prepend mechanics
# ---------------------------------------------------------------------------


class TestPrependMechanics:
    def test_block_prepended_to_string_content(self, tmp_path):
        mw = _middleware(tmp_path)
        ws = _workspace_dir(tmp_path)
        (ws / "a.txt").write_bytes(b"x")

        result = mw.before_agent(_state(HumanMessage(content="继续制作视频")), _runtime())

        content = result["messages"][-1].content
        assert isinstance(content, str)
        assert content.startswith("<workspace_files>")
        assert content.endswith("继续制作视频")

    def test_block_prepended_to_list_content(self, tmp_path):
        mw = _middleware(tmp_path)
        ws = _workspace_dir(tmp_path)
        (ws / "a.txt").write_bytes(b"x")

        msg = HumanMessage(content=[{"type": "text", "text": "analyse this"}])
        result = mw.before_agent(_state(msg), _runtime())

        updated = result["messages"][-1].content
        assert isinstance(updated, list)
        assert updated[0]["type"] == "text"
        assert updated[0]["text"].startswith("<workspace_files>")
        assert updated[1] == {"type": "text", "text": "analyse this"}

    def test_message_id_and_additional_kwargs_preserved(self, tmp_path):
        mw = _middleware(tmp_path)
        ws = _workspace_dir(tmp_path)
        (ws / "a.txt").write_bytes(b"x")

        msg = HumanMessage(content="go", additional_kwargs={"element": "task"})
        msg.id = "original-id-42"
        result = mw.before_agent(_state(msg), _runtime())

        updated = result["messages"][-1]
        assert updated.id == "original-id-42"
        assert updated.additional_kwargs.get("element") == "task"

    def test_inventory_update_preserves_response_metadata(self, tmp_path):
        """The content-update rewrite must keep response_metadata/name, not
        just id/additional_kwargs — same model_copy pattern as batch 1's
        uploads fix (local-only middleware, no upstream counterpart)."""
        mw = _middleware(tmp_path)
        ws = _workspace_dir(tmp_path)
        (ws / "a.txt").write_bytes(b"x")

        msg = HumanMessage(
            content="go",
            id="user-turn",
            name="caller",
            additional_kwargs={"element": "task"},
            response_metadata={"finish_reason": "stop"},
        )
        result = mw.before_agent(_state(msg), _runtime())

        updated = result["messages"][-1]
        assert updated.id == "user-turn"
        assert updated.name == "caller"
        assert updated.additional_kwargs.get("element") == "task"
        assert updated.response_metadata == {"finish_reason": "stop"}
        assert isinstance(updated.content, str)
        assert updated.content.index("<workspace_files>") < updated.content.index("go")

    def test_original_message_object_not_mutated(self, tmp_path):
        mw = _middleware(tmp_path)
        ws = _workspace_dir(tmp_path)
        (ws / "a.txt").write_bytes(b"x")

        msg = HumanMessage(content="go")
        result = mw.before_agent(_state(msg), _runtime())

        assert result["messages"][-1] is not msg
        assert msg.content == "go"

    def test_composes_with_uploads_middleware(self, tmp_path):
        """Both blocks land on the same message when the chain runs in order."""
        from deerflow.runtime.user_context import get_effective_user_id

        uploads_mw = UploadsMiddleware(base_dir=str(tmp_path))
        inventory_mw = _middleware(tmp_path)
        ws = _workspace_dir(tmp_path)
        (ws / "a.txt").write_bytes(b"x")
        uploads_dir = Paths(str(tmp_path)).sandbox_uploads_dir(THREAD_ID, user_id=get_effective_user_id())
        uploads_dir.mkdir(parents=True, exist_ok=True)
        (uploads_dir / "up.txt").write_bytes(b"u")

        state = _state(HumanMessage(content="go"))
        state = {**state, **(uploads_mw.before_agent(state, _runtime()) or {})}
        result = inventory_mw.before_agent(state, _runtime())

        content = _injected_content(result)
        assert "<workspace_files>" in content
        assert "<uploaded_files>" in content
        assert content.index("<workspace_files>") < content.index("<uploaded_files>")


# ---------------------------------------------------------------------------
# Graph-node guarantee & async parity
# ---------------------------------------------------------------------------


class TestHookSurface:
    def test_no_before_model_or_after_model_overrides(self):
        """before_model/after_model each add a per-round graph node — forbidden."""
        assert WorkspaceInventoryMiddleware.before_model is AgentMiddleware.before_model
        assert WorkspaceInventoryMiddleware.abefore_model is AgentMiddleware.abefore_model
        assert WorkspaceInventoryMiddleware.after_model is AgentMiddleware.after_model
        assert WorkspaceInventoryMiddleware.aafter_model is AgentMiddleware.aafter_model

    def test_only_sync_before_agent_overridden(self):
        """Mirrors UploadsMiddleware: sync-only before_agent. LangGraph's
        RunnableCallable falls back to the sync hook in async runs, so sync
        and async execution share one code path."""
        assert WorkspaceInventoryMiddleware.before_agent is not AgentMiddleware.before_agent
        assert WorkspaceInventoryMiddleware.abefore_agent is AgentMiddleware.abefore_agent
        assert UploadsMiddleware.abefore_agent is AgentMiddleware.abefore_agent


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


# CI 守卫（2026-09-23 收尾批）：标注用例经 AppConfig 读真实 config.yaml
# （gitignored，CI fresh checkout 不存在）——缺失时跳过，本地在位照常执行。
_REAL_CONFIG_EXISTS = any((Path(__file__).resolve().parents[i] / "config.yaml").exists() for i in (1, 2))
requires_real_config = pytest.mark.skipif(not _REAL_CONFIG_EXISTS, reason="依赖本地 gitignored config.yaml，CI 跳过")


@requires_real_config
class TestRegistration:
    def test_registered_right_after_uploads_middleware(self):
        from deerflow.agents.middlewares.tool_error_handling_middleware import build_lead_runtime_middlewares
        from deerflow.config import get_app_config

        names = [type(m).__name__ for m in build_lead_runtime_middlewares(app_config=get_app_config(), lazy_init=True)]
        assert "WorkspaceInventoryMiddleware" in names
        assert names.index("WorkspaceInventoryMiddleware") == names.index("UploadsMiddleware") + 1

    def test_not_in_subagent_chain(self):
        from deerflow.agents.middlewares.tool_error_handling_middleware import build_subagent_runtime_middlewares
        from deerflow.config import get_app_config

        names = [type(m).__name__ for m in build_subagent_runtime_middlewares(app_config=get_app_config(), lazy_init=True)]
        assert "WorkspaceInventoryMiddleware" not in names
