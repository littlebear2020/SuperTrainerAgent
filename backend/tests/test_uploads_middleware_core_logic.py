"""Core behaviour tests for UploadsMiddleware.

Covers:
- _files_from_kwargs: parsing, validation, existence check, virtual-path construction
- _create_files_message: output format with new-only and new+historical files
- before_agent: full injection pipeline (string & list content, preserved
  additional_kwargs, historical files from uploads dir, edge-cases)
"""

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage, HumanMessage

from deerflow.agents.middlewares.uploads_middleware import UploadsMiddleware
from deerflow.config.paths import Paths

THREAD_ID = "thread-abc123"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _middleware(tmp_path: Path) -> UploadsMiddleware:
    return UploadsMiddleware(base_dir=str(tmp_path))


def _runtime(thread_id: str | None = THREAD_ID) -> MagicMock:
    rt = MagicMock()
    rt.context = {"thread_id": thread_id}
    return rt


def _uploads_dir(tmp_path: Path, thread_id: str = THREAD_ID) -> Path:
    from deerflow.runtime.user_context import get_effective_user_id

    d = Paths(str(tmp_path)).sandbox_uploads_dir(thread_id, user_id=get_effective_user_id())
    d.mkdir(parents=True, exist_ok=True)
    return d


def _human(content, files=None, **extra_kwargs):
    additional_kwargs = dict(extra_kwargs)
    if files is not None:
        additional_kwargs["files"] = files
    return HumanMessage(content=content, additional_kwargs=additional_kwargs)


# ---------------------------------------------------------------------------
# _files_from_kwargs
# ---------------------------------------------------------------------------


class TestFilesFromKwargs:
    def test_returns_none_when_files_field_absent(self, tmp_path):
        mw = _middleware(tmp_path)
        msg = HumanMessage(content="hello")
        assert mw._files_from_kwargs(msg) is None

    def test_returns_none_for_empty_files_list(self, tmp_path):
        mw = _middleware(tmp_path)
        msg = _human("hello", files=[])
        assert mw._files_from_kwargs(msg) is None

    def test_returns_none_for_non_list_files(self, tmp_path):
        mw = _middleware(tmp_path)
        msg = _human("hello", files="not-a-list")
        assert mw._files_from_kwargs(msg) is None

    def test_skips_non_dict_entries(self, tmp_path):
        mw = _middleware(tmp_path)
        msg = _human("hi", files=["bad", 42, None])
        assert mw._files_from_kwargs(msg) is None

    def test_skips_entries_with_empty_filename(self, tmp_path):
        mw = _middleware(tmp_path)
        msg = _human("hi", files=[{"filename": "", "size": 100, "path": "/mnt/user-data/uploads/x"}])
        assert mw._files_from_kwargs(msg) is None

    def test_always_uses_virtual_path(self, tmp_path):
        """path field must be /mnt/user-data/uploads/<filename> regardless of what the frontend sent."""
        mw = _middleware(tmp_path)
        msg = _human(
            "hi",
            files=[{"filename": "report.pdf", "size": 1024, "path": "/some/arbitrary/path/report.pdf"}],
        )
        result = mw._files_from_kwargs(msg)
        assert result is not None
        assert result[0]["path"] == "/mnt/user-data/uploads/report.pdf"

    def test_skips_file_that_does_not_exist_on_disk(self, tmp_path):
        mw = _middleware(tmp_path)
        uploads_dir = _uploads_dir(tmp_path)
        # file is NOT written to disk
        msg = _human("hi", files=[{"filename": "missing.txt", "size": 50, "path": "/mnt/user-data/uploads/missing.txt"}])
        assert mw._files_from_kwargs(msg, uploads_dir) is None

    def test_accepts_file_that_exists_on_disk(self, tmp_path):
        mw = _middleware(tmp_path)
        uploads_dir = _uploads_dir(tmp_path)
        (uploads_dir / "data.csv").write_text("a,b,c")
        msg = _human("hi", files=[{"filename": "data.csv", "size": 5, "path": "/mnt/user-data/uploads/data.csv"}])
        result = mw._files_from_kwargs(msg, uploads_dir)
        assert result is not None
        assert len(result) == 1
        assert result[0]["filename"] == "data.csv"
        assert result[0]["path"] == "/mnt/user-data/uploads/data.csv"

    def test_skips_nonexistent_but_accepts_existing_in_mixed_list(self, tmp_path):
        mw = _middleware(tmp_path)
        uploads_dir = _uploads_dir(tmp_path)
        (uploads_dir / "present.txt").write_text("here")
        msg = _human(
            "hi",
            files=[
                {"filename": "present.txt", "size": 4, "path": "/mnt/user-data/uploads/present.txt"},
                {"filename": "gone.txt", "size": 4, "path": "/mnt/user-data/uploads/gone.txt"},
            ],
        )
        result = mw._files_from_kwargs(msg, uploads_dir)
        assert result is not None
        assert [f["filename"] for f in result] == ["present.txt"]

    def test_no_existence_check_when_uploads_dir_is_none(self, tmp_path):
        """Without an uploads_dir argument the existence check is skipped entirely."""
        mw = _middleware(tmp_path)
        msg = _human("hi", files=[{"filename": "phantom.txt", "size": 10, "path": "/mnt/user-data/uploads/phantom.txt"}])
        result = mw._files_from_kwargs(msg, uploads_dir=None)
        assert result is not None
        assert result[0]["filename"] == "phantom.txt"

    def test_size_is_coerced_to_int(self, tmp_path):
        mw = _middleware(tmp_path)
        msg = _human("hi", files=[{"filename": "f.txt", "size": "2048", "path": "/mnt/user-data/uploads/f.txt"}])
        result = mw._files_from_kwargs(msg)
        assert result is not None
        assert result[0]["size"] == 2048

    def test_missing_size_defaults_to_zero(self, tmp_path):
        mw = _middleware(tmp_path)
        msg = _human("hi", files=[{"filename": "f.txt", "path": "/mnt/user-data/uploads/f.txt"}])
        result = mw._files_from_kwargs(msg)
        assert result is not None
        assert result[0]["size"] == 0

    @pytest.mark.parametrize(
        "size",
        ["abc", float("inf"), float("nan"), None, True, False, -5, [1]],
        ids=["word", "inf", "nan", "none", "bool-true", "bool-false", "negative", "list"],
    )
    def test_unusable_size_falls_back_to_zero(self, tmp_path, size):
        """``files[*].size`` is client-supplied display metadata; every other field in this
        loop is validated fail-soft, so a value int() cannot take must degrade to 0
        instead of raising out of before_agent and failing the whole run."""
        mw = _middleware(tmp_path)
        msg = _human("hi", files=[{"filename": "f.txt", "size": size, "path": "/mnt/user-data/uploads/f.txt"}])
        result = mw._files_from_kwargs(msg)
        assert result is not None
        assert result[0]["filename"] == "f.txt"
        assert result[0]["size"] == 0
        assert type(result[0]["size"]) is int

    @pytest.mark.parametrize(
        "size, expected",
        [(2048, 2048), ("2048", 2048), (2048.0, 2048)],
        ids=["int", "numeric-string", "float"],
    )
    def test_usable_size_is_kept(self, tmp_path, size, expected):
        mw = _middleware(tmp_path)
        msg = _human("hi", files=[{"filename": "f.txt", "size": size, "path": "/mnt/user-data/uploads/f.txt"}])
        result = mw._files_from_kwargs(msg)
        assert result is not None
        assert result[0]["size"] == expected
        assert type(result[0]["size"]) is int


# ---------------------------------------------------------------------------
# _create_files_message
# ---------------------------------------------------------------------------


class TestCreateFilesMessage:
    def _new_file(self, filename="notes.txt", size=1024):
        return {"filename": filename, "size": size, "path": f"/mnt/user-data/uploads/{filename}"}

    def test_new_files_section_always_present(self, tmp_path):
        mw = _middleware(tmp_path)
        msg = mw._create_files_message([self._new_file()], [])
        assert "<uploaded_files>" in msg
        assert "</uploaded_files>" in msg
        assert "uploaded in this message" in msg
        assert "notes.txt" in msg
        assert "/mnt/user-data/uploads/notes.txt" in msg

    def test_historical_section_present_only_when_non_empty(self, tmp_path):
        mw = _middleware(tmp_path)

        msg_no_hist = mw._create_files_message([self._new_file()], [])
        assert "previous messages" not in msg_no_hist

        hist = self._new_file("old.txt")
        msg_with_hist = mw._create_files_message([self._new_file()], [hist])
        assert "previous messages" in msg_with_hist
        assert "old.txt" in msg_with_hist

    def test_size_formatting_kb(self, tmp_path):
        mw = _middleware(tmp_path)
        msg = mw._create_files_message([self._new_file(size=2048)], [])
        assert "2.0 KB" in msg

    def test_size_formatting_mb(self, tmp_path):
        mw = _middleware(tmp_path)
        msg = mw._create_files_message([self._new_file(size=2 * 1024 * 1024)], [])
        assert "2.0 MB" in msg

    def test_read_file_instruction_included(self, tmp_path):
        mw = _middleware(tmp_path)
        msg = mw._create_files_message([self._new_file()], [])
        assert "read_file" in msg

    def test_empty_new_files_produces_empty_marker(self, tmp_path):
        mw = _middleware(tmp_path)
        msg = mw._create_files_message([], [])
        assert "(empty)" in msg
        assert "<uploaded_files>" in msg
        assert "</uploaded_files>" in msg


# ---------------------------------------------------------------------------
# before_agent
# ---------------------------------------------------------------------------


class TestBeforeAgent:
    def _state(self, *messages):
        return {"messages": list(messages)}

    def test_returns_none_when_messages_empty(self, tmp_path):
        mw = _middleware(tmp_path)
        assert mw.before_agent({"messages": []}, _runtime()) is None

    def test_returns_none_when_last_message_is_not_human(self, tmp_path):
        mw = _middleware(tmp_path)
        state = self._state(HumanMessage(content="q"), AIMessage(content="a"))
        assert mw.before_agent(state, _runtime()) is None

    def test_returns_none_when_no_files_in_kwargs(self, tmp_path):
        mw = _middleware(tmp_path)
        state = self._state(_human("plain message"))
        assert mw.before_agent(state, _runtime()) is None

    def test_returns_none_when_all_files_missing_from_disk(self, tmp_path):
        mw = _middleware(tmp_path)
        _uploads_dir(tmp_path)  # directory exists but is empty
        msg = _human("hi", files=[{"filename": "ghost.txt", "size": 10, "path": "/mnt/user-data/uploads/ghost.txt"}])
        state = self._state(msg)
        assert mw.before_agent(state, _runtime()) is None

    def test_unusable_size_does_not_fail_the_run(self, tmp_path):
        """A malformed size on an existing upload must still inject the file,
        not abort before_agent with ValueError/OverflowError before the model is called."""
        mw = _middleware(tmp_path)
        uploads_dir = _uploads_dir(tmp_path)
        (uploads_dir / "present.txt").write_text("hello", encoding="utf-8")
        msg = _human("hi", files=[{"filename": "present.txt", "size": "abc", "path": "/mnt/user-data/uploads/present.txt"}])
        state = self._state(msg)

        result = mw.before_agent(state, _runtime())

        assert result is not None
        assert [f["filename"] for f in result["uploaded_files"]] == ["present.txt"]
        assert result["uploaded_files"][0]["size"] == 0
        assert type(result["uploaded_files"][0]["size"]) is int
        content = result["messages"][0].content
        assert "present.txt" in content

    def test_injects_uploaded_files_tag_into_string_content(self, tmp_path):
        mw = _middleware(tmp_path)
        uploads_dir = _uploads_dir(tmp_path)
        (uploads_dir / "report.pdf").write_bytes(b"pdf")

        msg = _human("please analyse", files=[{"filename": "report.pdf", "size": 3, "path": "/mnt/user-data/uploads/report.pdf"}])
        state = self._state(msg)
        result = mw.before_agent(state, _runtime())

        assert result is not None
        updated_msg = result["messages"][-1]
        assert isinstance(updated_msg.content, str)
        assert "<uploaded_files>" in updated_msg.content
        assert "report.pdf" in updated_msg.content
        assert "please analyse" in updated_msg.content

    def test_injects_uploaded_files_tag_into_list_content(self, tmp_path):
        mw = _middleware(tmp_path)
        uploads_dir = _uploads_dir(tmp_path)
        (uploads_dir / "data.csv").write_bytes(b"a,b")

        msg = _human(
            [{"type": "text", "text": "analyse this"}],
            files=[{"filename": "data.csv", "size": 3, "path": "/mnt/user-data/uploads/data.csv"}],
        )
        state = self._state(msg)
        result = mw.before_agent(state, _runtime())

        assert result is not None
        updated_msg = result["messages"][-1]
        assert isinstance(updated_msg.content, list)
        combined_text = "\n".join(block.get("text", "") for block in updated_msg.content if isinstance(block, dict))
        assert "<uploaded_files>" in combined_text
        assert "analyse this" in combined_text

    def test_preserves_additional_kwargs_on_updated_message(self, tmp_path):
        mw = _middleware(tmp_path)
        uploads_dir = _uploads_dir(tmp_path)
        (uploads_dir / "img.png").write_bytes(b"png")

        files_meta = [{"filename": "img.png", "size": 3, "path": "/mnt/user-data/uploads/img.png", "status": "uploaded"}]
        msg = _human("check image", files=files_meta, element="task")
        state = self._state(msg)
        result = mw.before_agent(state, _runtime())

        assert result is not None
        updated_kwargs = result["messages"][-1].additional_kwargs
        assert updated_kwargs.get("files") == files_meta
        assert updated_kwargs.get("element") == "task"

    def test_preserves_response_metadata_on_updated_message(self, tmp_path):
        """The model_copy rewrite must keep response_metadata while prepending
        the files message to the content."""
        mw = _middleware(tmp_path)
        uploads_dir = _uploads_dir(tmp_path)
        (uploads_dir / "doc.txt").write_bytes(b"doc")

        msg = HumanMessage(
            content="see file",
            additional_kwargs={"files": [{"filename": "doc.txt", "size": 3, "path": "/mnt/user-data/uploads/doc.txt"}]},
            response_metadata={"finish_reason": "stop"},
        )
        result = mw.before_agent(self._state(msg), _runtime())

        assert result is not None
        updated_msg = result["messages"][-1]
        assert updated_msg.response_metadata == {"finish_reason": "stop"}
        assert isinstance(updated_msg.content, str)
        assert updated_msg.content.index("<uploaded_files>") < updated_msg.content.index("see file")

    def test_uploaded_files_returned_in_state_update(self, tmp_path):
        mw = _middleware(tmp_path)
        uploads_dir = _uploads_dir(tmp_path)
        (uploads_dir / "notes.txt").write_bytes(b"hello")

        msg = _human("review", files=[{"filename": "notes.txt", "size": 5, "path": "/mnt/user-data/uploads/notes.txt"}])
        result = mw.before_agent(self._state(msg), _runtime())

        assert result is not None
        assert result["uploaded_files"] == [
            {
                "filename": "notes.txt",
                "size": 5,
                "path": "/mnt/user-data/uploads/notes.txt",
                "extension": ".txt",
                "outline": [],
                "outline_preview": [],
            }
        ]

    def test_historical_files_from_uploads_dir_excluding_new(self, tmp_path):
        mw = _middleware(tmp_path)
        uploads_dir = _uploads_dir(tmp_path)
        (uploads_dir / "old.txt").write_bytes(b"old")
        (uploads_dir / "new.txt").write_bytes(b"new")

        msg = _human("go", files=[{"filename": "new.txt", "size": 3, "path": "/mnt/user-data/uploads/new.txt"}])
        result = mw.before_agent(self._state(msg), _runtime())

        assert result is not None
        content = result["messages"][-1].content
        assert "uploaded in this message" in content
        assert "new.txt" in content
        assert "previous messages" in content
        assert "old.txt" in content

    def test_no_historical_section_when_upload_dir_is_empty(self, tmp_path):
        mw = _middleware(tmp_path)
        uploads_dir = _uploads_dir(tmp_path)
        (uploads_dir / "only.txt").write_bytes(b"x")

        msg = _human("go", files=[{"filename": "only.txt", "size": 1, "path": "/mnt/user-data/uploads/only.txt"}])
        result = mw.before_agent(self._state(msg), _runtime())

        content = result["messages"][-1].content
        assert "previous messages" not in content

    def test_no_historical_scan_when_thread_id_is_none(self, tmp_path):
        mw = _middleware(tmp_path)
        msg = _human("go", files=[{"filename": "f.txt", "size": 1, "path": "/mnt/user-data/uploads/f.txt"}])
        # thread_id=None → _files_from_kwargs skips existence check, no dir scan
        result = mw.before_agent(self._state(msg), _runtime(thread_id=None))
        # With no existence check, the file passes through and injection happens
        assert result is not None
        content = result["messages"][-1].content
        assert "previous messages" not in content

    def test_message_id_preserved_on_updated_message(self, tmp_path):
        mw = _middleware(tmp_path)
        uploads_dir = _uploads_dir(tmp_path)
        (uploads_dir / "f.txt").write_bytes(b"x")

        msg = _human("go", files=[{"filename": "f.txt", "size": 1, "path": "/mnt/user-data/uploads/f.txt"}])
        msg.id = "original-id-42"
        result = mw.before_agent(self._state(msg), _runtime())

        assert result["messages"][-1].id == "original-id-42"

    def test_outline_injected_when_md_file_exists(self, tmp_path):
        """When a converted .md file exists alongside the upload, its outline is injected."""
        mw = _middleware(tmp_path)
        uploads_dir = _uploads_dir(tmp_path)
        (uploads_dir / "report.pdf").write_bytes(b"%PDF fake")
        # Simulate the .md produced by the conversion pipeline
        (uploads_dir / "report.md").write_text(
            "# PART I\n\n## ITEM 1. BUSINESS\n\nBody text.\n\n## ITEM 2. RISK\n",
            encoding="utf-8",
        )

        msg = _human("summarise", files=[{"filename": "report.pdf", "size": 9, "path": "/mnt/user-data/uploads/report.pdf"}])
        result = mw.before_agent(self._state(msg), _runtime())

        assert result is not None
        content = result["messages"][-1].content
        assert "Document outline" in content
        assert "PART I" in content
        assert "ITEM 1. BUSINESS" in content
        assert "ITEM 2. RISK" in content
        assert "read_file" in content

    def test_no_outline_when_no_md_file(self, tmp_path):
        """Files without a sibling .md have no outline section."""
        mw = _middleware(tmp_path)
        uploads_dir = _uploads_dir(tmp_path)
        (uploads_dir / "data.xlsx").write_bytes(b"fake-xlsx")

        msg = _human("analyse", files=[{"filename": "data.xlsx", "size": 9, "path": "/mnt/user-data/uploads/data.xlsx"}])
        result = mw.before_agent(self._state(msg), _runtime())

        assert result is not None
        content = result["messages"][-1].content
        assert "Document outline" not in content

    def test_outline_truncation_hint_shown(self, tmp_path):
        """When outline is truncated, a hint line is appended after the last visible entry."""
        from deerflow.utils.file_conversion import MAX_OUTLINE_ENTRIES

        mw = _middleware(tmp_path)
        uploads_dir = _uploads_dir(tmp_path)
        (uploads_dir / "big.pdf").write_bytes(b"%PDF fake")
        # Write MAX_OUTLINE_ENTRIES + 5 headings so truncation is triggered
        headings = "\n".join(f"# Heading {i}" for i in range(MAX_OUTLINE_ENTRIES + 5))
        (uploads_dir / "big.md").write_text(headings, encoding="utf-8")

        msg = _human("read", files=[{"filename": "big.pdf", "size": 9, "path": "/mnt/user-data/uploads/big.pdf"}])
        result = mw.before_agent(self._state(msg), _runtime())

        assert result is not None
        content = result["messages"][-1].content
        assert f"showing first {MAX_OUTLINE_ENTRIES} headings" in content
        assert "use `read_file` to explore further" in content

    def test_no_truncation_hint_for_short_outline(self, tmp_path):
        """Short outlines (under the cap) must not show a truncation hint."""
        mw = _middleware(tmp_path)
        uploads_dir = _uploads_dir(tmp_path)
        (uploads_dir / "short.pdf").write_bytes(b"%PDF fake")
        (uploads_dir / "short.md").write_text("# Intro\n\n# Conclusion\n", encoding="utf-8")

        msg = _human("read", files=[{"filename": "short.pdf", "size": 9, "path": "/mnt/user-data/uploads/short.pdf"}])
        result = mw.before_agent(self._state(msg), _runtime())

        assert result is not None
        content = result["messages"][-1].content
        assert "showing first" not in content

    def test_historical_file_outline_injected(self, tmp_path):
        """Outline is also shown for historical (previously uploaded) files."""
        mw = _middleware(tmp_path)
        uploads_dir = _uploads_dir(tmp_path)
        # Historical file with .md
        (uploads_dir / "old_report.pdf").write_bytes(b"%PDF old")
        (uploads_dir / "old_report.md").write_text(
            "# Chapter 1\n\n# Chapter 2\n",
            encoding="utf-8",
        )
        # New file without .md
        (uploads_dir / "new.txt").write_bytes(b"new")

        msg = _human("go", files=[{"filename": "new.txt", "size": 3, "path": "/mnt/user-data/uploads/new.txt"}])
        result = mw.before_agent(self._state(msg), _runtime())

        assert result is not None
        content = result["messages"][-1].content
        assert "Chapter 1" in content
        assert "Chapter 2" in content

    def test_fallback_preview_shown_when_outline_empty(self, tmp_path):
        """When .md exists but has no headings, first lines are shown as a preview."""
        mw = _middleware(tmp_path)
        uploads_dir = _uploads_dir(tmp_path)
        (uploads_dir / "report.pdf").write_bytes(b"%PDF fake")
        # .md with no # headings — plain prose only
        (uploads_dir / "report.md").write_text(
            "Annual Financial Report 2024\n\nThis document summarises key findings.\n\nRevenue grew by 12%.\n",
            encoding="utf-8",
        )

        msg = _human("analyse", files=[{"filename": "report.pdf", "size": 9, "path": "/mnt/user-data/uploads/report.pdf"}])
        result = mw.before_agent(self._state(msg), _runtime())

        assert result is not None
        content = result["messages"][-1].content
        # Outline section must NOT appear
        assert "Document outline" not in content
        # Preview lines must appear
        assert "Annual Financial Report 2024" in content
        assert "No structural headings detected" in content
        # grep hint must appear
        assert "grep" in content

    def test_fallback_grep_hint_shown_when_no_md_file(self, tmp_path):
        """Files with no sibling .md still get the grep hint (outline is empty)."""
        mw = _middleware(tmp_path)
        uploads_dir = _uploads_dir(tmp_path)
        (uploads_dir / "data.csv").write_bytes(b"a,b,c\n1,2,3\n")

        msg = _human("analyse", files=[{"filename": "data.csv", "size": 12, "path": "/mnt/user-data/uploads/data.csv"}])
        result = mw.before_agent(self._state(msg), _runtime())

        assert result is not None
        content = result["messages"][-1].content
        assert "Document outline" not in content
        assert "grep" in content


# ---------------------------------------------------------------------------
# Historical listing dedupe (unchanged set collapses to a one-line reference)
# ---------------------------------------------------------------------------


class TestHistoricalDedupe:
    SHORT_LINE = "上传文件集未变化"

    def _turn(self, mw, content="next question", files=None, thread_id=THREAD_ID):
        msg = _human(content, files=files)
        result = mw.before_agent({"messages": [msg]}, _runtime(thread_id))
        assert result is not None
        return result["messages"][-1].content

    def test_first_listing_is_full(self, tmp_path):
        mw = _middleware(tmp_path)
        uploads_dir = _uploads_dir(tmp_path)
        (uploads_dir / "a.txt").write_bytes(b"aaa")

        content = self._turn(mw)
        assert "previous messages" in content
        assert "a.txt" in content
        assert "To work with these files:" in content
        assert self.SHORT_LINE not in content

    def test_unchanged_set_collapses_to_short_line(self, tmp_path):
        mw = _middleware(tmp_path)
        uploads_dir = _uploads_dir(tmp_path)
        (uploads_dir / "a.txt").write_bytes(b"aaa")

        # Turn 1: a.txt arrives attached to the message (new-file section).
        c1 = self._turn(mw, "analyse", files=[{"filename": "a.txt", "size": 3, "path": "/mnt/user-data/uploads/a.txt"}])
        assert "uploaded in this message" in c1
        assert "a.txt" in c1

        # Turn 2: plain message — historical set rendered in full the first time.
        c2 = self._turn(mw)
        assert "previous messages" in c2
        assert "To work with these files:" in c2

        # Turn 3: unchanged set → one short line, no repeated listing/boilerplate.
        c3 = self._turn(mw)
        assert "上传文件集未变化（共 1 个文件，清单与使用指引同上文；也可用 glob(path='/mnt/user-data/uploads/') 查看清单）。" in c3
        assert "previous messages" not in c3
        assert "To work with these files:" not in c3
        assert "Path: /mnt/user-data/uploads/a.txt" not in c3
        assert "<uploaded_files>" in c3
        assert "</uploaded_files>" in c3

    def test_changed_set_renders_full_listing_again(self, tmp_path):
        mw = _middleware(tmp_path)
        uploads_dir = _uploads_dir(tmp_path)
        (uploads_dir / "a.txt").write_bytes(b"aaa")

        self._turn(mw)  # first full listing
        c2 = self._turn(mw)  # deduped
        assert self.SHORT_LINE in c2

        (uploads_dir / "b.txt").write_bytes(b"bb")
        c3 = self._turn(mw)
        assert self.SHORT_LINE not in c3
        assert "previous messages" in c3
        assert "b.txt" in c3
        assert "To work with these files:" in c3

        c4 = self._turn(mw)
        assert "上传文件集未变化（共 2 个文件，清单与使用指引同上文；也可用 glob(path='/mnt/user-data/uploads/') 查看清单）。" in c4

    def test_size_change_counts_as_change(self, tmp_path):
        mw = _middleware(tmp_path)
        uploads_dir = _uploads_dir(tmp_path)
        (uploads_dir / "a.txt").write_bytes(b"aaa")

        self._turn(mw)
        c2 = self._turn(mw)
        assert self.SHORT_LINE in c2

        (uploads_dir / "a.txt").write_bytes(b"aaa-longer")
        c3 = self._turn(mw)
        assert self.SHORT_LINE not in c3
        assert "previous messages" in c3

    def test_new_upload_section_verbatim_while_historical_deduped(self, tmp_path):
        mw = _middleware(tmp_path)
        uploads_dir = _uploads_dir(tmp_path)
        (uploads_dir / "a.txt").write_bytes(b"aaa")

        self._turn(mw, "analyse", files=[{"filename": "a.txt", "size": 3, "path": "/mnt/user-data/uploads/a.txt"}])
        self._turn(mw)  # full historical listing of a.txt

        # New upload b.txt: its own section stays verbatim; unchanged historical collapses.
        (uploads_dir / "b.txt").write_bytes(b"bb")
        c3 = self._turn(mw, "and this", files=[{"filename": "b.txt", "size": 2, "path": "/mnt/user-data/uploads/b.txt"}])
        assert "uploaded in this message" in c3
        assert "Path: /mnt/user-data/uploads/b.txt" in c3
        assert "上传文件集未变化（共 1 个文件，清单与使用指引同上文；也可用 glob(path='/mnt/user-data/uploads/') 查看清单）。" in c3
        assert "previous messages" not in c3

    def test_dedupe_state_is_per_thread(self, tmp_path):
        mw = _middleware(tmp_path)
        uploads_dir = _uploads_dir(tmp_path)
        (uploads_dir / "a.txt").write_bytes(b"aaa")
        other_dir = _uploads_dir(tmp_path, thread_id="thread-other")
        (other_dir / "b.txt").write_bytes(b"bb")

        self._turn(mw)  # thread A: full
        c_a2 = self._turn(mw)  # thread A: deduped
        assert self.SHORT_LINE in c_a2

        # thread B has no recorded signature → full listing despite A's state.
        c_b1 = self._turn(mw, thread_id="thread-other")
        assert self.SHORT_LINE not in c_b1
        assert "previous messages" in c_b1
        assert "b.txt" in c_b1

        c_b2 = self._turn(mw, thread_id="thread-other")
        assert self.SHORT_LINE in c_b2

    def test_signature_cache_is_fifo_capped(self, tmp_path):
        from deerflow.agents.middlewares.uploads_middleware import _MAX_SIGNATURE_CACHE_ENTRIES

        mw = _middleware(tmp_path)
        # Fill the cache directly: entries are (thread_id -> signature).
        for i in range(_MAX_SIGNATURE_CACHE_ENTRIES):
            mw._last_uploads_signature[f"thread-{i}"] = (("f.txt", 1),)

        uploads_dir = _uploads_dir(tmp_path)
        (uploads_dir / "a.txt").write_bytes(b"aaa")
        self._turn(mw)  # one more distinct thread → oldest evicted

        assert len(mw._last_uploads_signature) == _MAX_SIGNATURE_CACHE_ENTRIES
        assert "thread-0" not in mw._last_uploads_signature
        assert "thread-1" in mw._last_uploads_signature
        assert THREAD_ID in mw._last_uploads_signature

    def test_signature_cache_update_does_not_evict(self, tmp_path):
        """Re-setting an existing thread's signature must not shrink the cache."""
        mw = _middleware(tmp_path)
        uploads_dir = _uploads_dir(tmp_path)
        (uploads_dir / "a.txt").write_bytes(b"aaa")
        self._turn(mw)
        before = dict(mw._last_uploads_signature)
        self._turn(mw)  # same thread, same files → update in place
        assert mw._last_uploads_signature == before


class TestCreateFilesMessageDedupe:
    def _hist(self, filename="old.txt", size=3):
        return {"filename": filename, "size": size, "path": f"/mnt/user-data/uploads/{filename}"}

    def test_historical_unchanged_renders_short_line(self, tmp_path):
        mw = _middleware(tmp_path)
        msg = mw._create_files_message([], [self._hist()], historical_unchanged=True)
        assert "上传文件集未变化（共 1 个文件，清单与使用指引同上文；也可用 glob(path='/mnt/user-data/uploads/') 查看清单）。" in msg
        assert "previous messages" not in msg
        assert "To work with these files:" not in msg
        assert "old.txt" not in msg
        assert msg.startswith("<uploaded_files>")
        assert msg.endswith("</uploaded_files>")

    def test_historical_unchanged_ignored_when_no_historical_files(self, tmp_path):
        mw = _middleware(tmp_path)
        new = {"filename": "n.txt", "size": 1, "path": "/mnt/user-data/uploads/n.txt"}
        msg = mw._create_files_message([new], [], historical_unchanged=True)
        # Nothing was elided, so the usage guidance must stay.
        assert "To work with these files:" in msg
        assert "上传文件集未变化" not in msg
