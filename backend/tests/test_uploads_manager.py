"""Tests for deerflow.uploads.manager — shared upload management logic."""

import errno
import os
import shutil
import stat
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from deerflow.uploads.manager import (
    PathTraversalError,
    claim_unique_filename,
    delete_file_safe,
    list_files_in_dir,
    normalize_filename,
    validate_path_traversal,
)

# ---------------------------------------------------------------------------
# normalize_filename
# ---------------------------------------------------------------------------


class TestNormalizeFilename:
    def test_safe_filename(self):
        assert normalize_filename("report.pdf") == "report.pdf"

    def test_strips_path_components(self):
        assert normalize_filename("../../etc/passwd") == "passwd"

    def test_rejects_empty(self):
        with pytest.raises(ValueError, match="empty"):
            normalize_filename("")

    def test_rejects_dot_dot(self):
        with pytest.raises(ValueError, match="unsafe"):
            normalize_filename("..")

    def test_strips_separators(self):
        assert normalize_filename("path/to/file.txt") == "file.txt"

    def test_dot_only(self):
        with pytest.raises(ValueError, match="unsafe"):
            normalize_filename(".")


# ---------------------------------------------------------------------------
# claim_unique_filename
# ---------------------------------------------------------------------------


class TestDeduplicateFilename:
    def test_no_collision(self):
        seen: set[str] = set()
        assert claim_unique_filename("data.txt", seen) == "data.txt"
        assert "data.txt" in seen

    def test_single_collision(self):
        seen = {"data.txt"}
        assert claim_unique_filename("data.txt", seen) == "data_1.txt"
        assert "data_1.txt" in seen

    def test_triple_collision(self):
        seen = {"data.txt", "data_1.txt", "data_2.txt"}
        assert claim_unique_filename("data.txt", seen) == "data_3.txt"
        assert "data_3.txt" in seen

    def test_mutates_seen(self):
        seen: set[str] = set()
        claim_unique_filename("a.txt", seen)
        claim_unique_filename("a.txt", seen)
        assert seen == {"a.txt", "a_1.txt"}


# ---------------------------------------------------------------------------
# validate_path_traversal
# ---------------------------------------------------------------------------


class TestValidatePathTraversal:
    def test_inside_base_ok(self, tmp_path):
        child = tmp_path / "file.txt"
        child.touch()
        validate_path_traversal(child, tmp_path)  # no exception

    def test_outside_base_raises(self, tmp_path):
        outside = tmp_path / ".." / "evil.txt"
        with pytest.raises(PathTraversalError, match="traversal"):
            validate_path_traversal(outside, tmp_path)

    def test_symlink_escape(self, tmp_path):
        target = tmp_path.parent / "secret.txt"
        target.touch()
        link = tmp_path / "escape"
        try:
            link.symlink_to(target)
        except OSError as exc:
            if getattr(exc, "winerror", None) == 1314:
                pytest.skip("symlink creation requires Developer Mode or elevated privileges on Windows")
            raise
        with pytest.raises(PathTraversalError, match="traversal"):
            validate_path_traversal(link, tmp_path)


# ---------------------------------------------------------------------------
# list_files_in_dir
# ---------------------------------------------------------------------------


class TestListFilesInDir:
    def test_empty_dir(self, tmp_path):
        result = list_files_in_dir(tmp_path)
        assert result == {"files": [], "count": 0}

    def test_nonexistent_dir(self, tmp_path):
        result = list_files_in_dir(tmp_path / "nope")
        assert result == {"files": [], "count": 0}

    def test_multiple_files_sorted(self, tmp_path):
        (tmp_path / "b.txt").write_text("b")
        (tmp_path / "a.txt").write_text("a")
        result = list_files_in_dir(tmp_path)
        assert result["count"] == 2
        assert result["files"][0]["filename"] == "a.txt"
        assert result["files"][1]["filename"] == "b.txt"
        for f in result["files"]:
            assert set(f.keys()) == {"filename", "size", "path", "extension", "modified"}

    def test_ignores_subdirectories(self, tmp_path):
        (tmp_path / "file.txt").write_text("data")
        (tmp_path / "subdir").mkdir()
        result = list_files_in_dir(tmp_path)
        assert result["count"] == 1
        assert result["files"][0]["filename"] == "file.txt"


# ---------------------------------------------------------------------------
# delete_file_safe
# ---------------------------------------------------------------------------


class TestDeleteFileSafe:
    def test_delete_existing_file(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("data")
        result = delete_file_safe(tmp_path, "test.txt")
        assert result["success"] is True
        assert not f.exists()

    def test_delete_nonexistent_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            delete_file_safe(tmp_path, "nope.txt")

    def test_delete_traversal_raises(self, tmp_path):
        with pytest.raises(PathTraversalError, match="traversal"):
            delete_file_safe(tmp_path, "../outside.txt")

    def test_delete_symlink_refused_and_target_intact(self, tmp_path):
        """符号链接拒删：unlink 不得顺链接删到目标文件本体。"""
        target = tmp_path / "secret.txt"
        target.write_text("keep me")
        link = tmp_path / "link.txt"
        try:
            link.symlink_to(target)
        except OSError as exc:
            if getattr(exc, "winerror", None) == 1314:
                pytest.skip("symlink creation requires Developer Mode or elevated privileges on Windows")
            raise
        with pytest.raises(FileNotFoundError):
            delete_file_safe(tmp_path, "link.txt")
        assert target.read_text() == "keep me"
        assert link.is_symlink()

    def test_delete_keeps_companion_md(self, tmp_path):
        """删 a.pdf 不连带删同词干 a.md——伴生 .md 可能属于其它文档或用户自己。"""
        pdf = tmp_path / "a.pdf"
        md = tmp_path / "a.md"
        pdf.write_text("pdf")
        md.write_text("md")
        result = delete_file_safe(tmp_path, "a.pdf")
        assert result["success"] is True
        assert not pdf.exists()
        assert md.exists()

    def test_normalize_filename_rejects_nul(self):
        with pytest.raises(ValueError, match="NUL"):
            normalize_filename("evil\x00.txt")


# ---------------------------------------------------------------------------
# Staging-file namespace (R-09, 2026-08-13)
# ---------------------------------------------------------------------------


class TestUploadStagingDetection:
    def test_upload_part_is_staging(self):
        from deerflow.uploads.manager import is_upload_staging_file

        assert is_upload_staging_file(".upload-ab12cd.part")
        assert is_upload_staging_file(".upload-x.part")

    def test_user_tmp_file_is_not_staging(self):
        """R-09: a user's ``notes.tmp`` must not be classified as transient
        staging and deleted at startup."""
        from deerflow.uploads.manager import is_upload_staging_file

        assert not is_upload_staging_file("notes.tmp")
        assert not is_upload_staging_file("report.pdf.tmp")
        assert not is_upload_staging_file(".tmp")
        assert not is_upload_staging_file("dir.tmp")
        assert not is_upload_staging_file(".upload-orphan")  # no suffix
        assert not is_upload_staging_file("x.part")  # no prefix


class TestListFilesSkipsStaging:
    def test_list_files_in_dir_filters_staging_files(self, tmp_path):
        (tmp_path / "real.txt").write_text("real")
        (tmp_path / ".upload-abc.part").write_text("partial")
        (tmp_path / "notes.tmp").write_text("user tmp file")

        result = list_files_in_dir(tmp_path)
        names = [entry["filename"] for entry in result["files"]]

        assert "real.txt" in names
        assert "notes.tmp" in names  # user file must stay visible
        assert ".upload-abc.part" not in names
        assert result["count"] == 2


# ---------------------------------------------------------------------------
# write_upload_file_no_symlink / copy_upload_file_no_symlink（上游 #5578）
# ---------------------------------------------------------------------------


class TestWriteUploadFileNoSymlink:
    def test_writes_bytes_to_fresh_destination(self, tmp_path):
        from deerflow.uploads.manager import write_upload_file_no_symlink

        dest = write_upload_file_no_symlink(tmp_path, "notes.txt", b"hello")

        assert dest == tmp_path / "notes.txt"
        assert dest.read_bytes() == b"hello"

    def test_rejects_symlink_destination(self, tmp_path):
        from deerflow.uploads.manager import UnsafeUploadPathError, write_upload_file_no_symlink

        outside = tmp_path / "outside.txt"
        outside.write_bytes(b"original")
        link = tmp_path / "notes.txt"
        try:
            link.symlink_to(outside)
        except OSError as exc:
            if getattr(exc, "winerror", None) == 1314:
                pytest.skip("symlink creation requires Developer Mode or elevated privileges on Windows")
            raise

        with pytest.raises(UnsafeUploadPathError):
            write_upload_file_no_symlink(tmp_path, "notes.txt", b"attacker bytes")

        assert outside.read_bytes() == b"original"
        assert link.is_symlink()


class TestCopyUploadFileNoSymlink:
    def test_copies_content_mode_and_timestamps(self, tmp_path):
        from deerflow.uploads.manager import copy_upload_file_no_symlink

        uploads = tmp_path / "uploads"
        uploads.mkdir()
        src = tmp_path / "notes.txt"
        src.write_bytes(b"hello")
        os.chmod(src, 0o640)
        os.utime(src, ns=(1_700_000_000_000_000_000, 1_700_000_000_000_000_000))

        dest = copy_upload_file_no_symlink(uploads, "notes.txt", src)

        assert dest == uploads / "notes.txt"
        assert dest.read_bytes() == b"hello"
        if os.chmod in os.supports_fd:
            assert stat.S_IMODE(os.stat(dest).st_mode) == 0o640
        if os.utime in os.supports_fd:
            assert os.stat(dest).st_mtime_ns == 1_700_000_000_000_000_000

    def test_overwrites_existing_regular_file(self, tmp_path):
        from deerflow.uploads.manager import copy_upload_file_no_symlink

        uploads = tmp_path / "uploads"
        uploads.mkdir()
        (uploads / "notes.txt").write_bytes(b"old contents")
        src = tmp_path / "notes.txt"
        src.write_bytes(b"new contents")

        dest = copy_upload_file_no_symlink(uploads, "notes.txt", src)

        assert dest.read_bytes() == b"new contents"

    def test_rejects_symlink_destination(self, tmp_path):
        from deerflow.uploads.manager import UnsafeUploadPathError, copy_upload_file_no_symlink

        uploads = tmp_path / "uploads"
        uploads.mkdir()
        outside = tmp_path / "outside.txt"
        outside.write_bytes(b"original")
        link = uploads / "notes.txt"
        try:
            link.symlink_to(outside)
        except OSError as exc:
            if getattr(exc, "winerror", None) == 1314:
                pytest.skip("symlink creation requires Developer Mode or elevated privileges on Windows")
            raise
        src = tmp_path / "notes.txt"
        src.write_bytes(b"attacker-chosen target")

        with pytest.raises(UnsafeUploadPathError):
            copy_upload_file_no_symlink(uploads, "notes.txt", src)

        assert outside.read_bytes() == b"original"
        assert link.is_symlink()

    def test_rejects_copying_a_file_onto_itself(self, tmp_path):
        from deerflow.uploads.manager import copy_upload_file_no_symlink

        uploads = tmp_path / "uploads"
        uploads.mkdir()
        src = uploads / "notes.txt"
        src.write_bytes(b"IMPORTANT")

        with pytest.raises(shutil.SameFileError):
            copy_upload_file_no_symlink(uploads, "notes.txt", src)

        assert src.read_bytes() == b"IMPORTANT"

    def test_missing_source_leaves_existing_destination_untouched(self, tmp_path):
        from deerflow.uploads.manager import copy_upload_file_no_symlink

        uploads = tmp_path / "uploads"
        uploads.mkdir()
        (uploads / "notes.txt").write_bytes(b"keep me")

        with pytest.raises(FileNotFoundError):
            copy_upload_file_no_symlink(uploads, "notes.txt", tmp_path / "missing.txt")

        assert (uploads / "notes.txt").read_bytes() == b"keep me"


class TestOpenUploadFileErrnoMapping:
    """open 的 OSError errno 映射钉子（上游 #5578 收尾批）。

    纯覆盖新增——实现（manager.open_upload_file_no_symlink 的 except OSError
    分支）已正确，本类只把映射集合与"其余原样重抛"钉进测试，非红绿驱动。
    """

    @pytest.mark.parametrize("mapped_errno", [errno.ELOOP, errno.EISDIR, errno.ENOTDIR, errno.ENXIO, errno.EAGAIN])
    def test_mapped_errno_raises_unsafe_upload_path(self, tmp_path, monkeypatch, mapped_errno):
        from deerflow.uploads.manager import UnsafeUploadPathError, open_upload_file_no_symlink

        def _raise_open(*args, **kwargs):
            raise OSError(mapped_errno, os.strerror(mapped_errno))

        monkeypatch.setattr("deerflow.uploads.manager.os.open", _raise_open)

        with pytest.raises(UnsafeUploadPathError):
            open_upload_file_no_symlink(tmp_path, "notes.txt")

    def test_mapped_errno_raises_unsafe_upload_path_through_write(self, tmp_path, monkeypatch):
        """同映射经 write_upload_file_no_symlink 转发路径同样成立。"""
        from deerflow.uploads.manager import UnsafeUploadPathError, write_upload_file_no_symlink

        def _raise_open(*args, **kwargs):
            raise OSError(errno.ELOOP, os.strerror(errno.ELOOP))

        monkeypatch.setattr("deerflow.uploads.manager.os.open", _raise_open)

        with pytest.raises(UnsafeUploadPathError):
            write_upload_file_no_symlink(tmp_path, "notes.txt", b"data")

    def test_unmapped_errno_reraises_bare_oserror(self, tmp_path, monkeypatch):
        from deerflow.uploads.manager import UnsafeUploadPathError, open_upload_file_no_symlink

        def _raise_open(*args, **kwargs):
            raise OSError(errno.EACCES, os.strerror(errno.EACCES))

        monkeypatch.setattr("deerflow.uploads.manager.os.open", _raise_open)

        with pytest.raises(OSError) as exc_info:
            open_upload_file_no_symlink(tmp_path, "notes.txt")

        assert not isinstance(exc_info.value, UnsafeUploadPathError)
        assert exc_info.value.errno == errno.EACCES


class TestClientUploadSkipsUnsafeDestination:
    """client 层：上传目的地为 symlink 时跳过并上报 skipped_files（上游 #5578）。"""

    @pytest.fixture
    def uploads_client(self):
        """最小化 mock 配置的 DeerFlowClient（对齐 tests/test_client.py 的 client fixture）。"""
        from deerflow.client import DeerFlowClient

        model = MagicMock()
        model.name = "test-model"
        model.model = "test-model"
        model.supports_thinking = False
        model.supports_reasoning_effort = False
        model.model_dump.return_value = {"name": "test-model", "use": "langchain_openai:ChatOpenAI"}
        config = MagicMock()
        config.models = [model]
        config.token_usage.enabled = False
        with patch("deerflow.client.get_app_config", return_value=config):
            return DeerFlowClient()

    def test_upload_files_skips_symlinked_destination(self, uploads_client):
        """A symlink planted at an upload name is skipped, not written through."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            uploads_dir = tmp_path / "uploads"
            uploads_dir.mkdir()

            outside = tmp_path / "outside.txt"
            outside.write_text("original")
            link = uploads_dir / "note.txt"
            try:
                link.symlink_to(outside)
            except OSError as exc:
                if getattr(exc, "winerror", None) == 1314:
                    pytest.skip("symlink creation requires Developer Mode or elevated privileges on Windows")
                raise

            src_dir = tmp_path / "src"
            src_dir.mkdir()
            (src_dir / "note.txt").write_text("uploaded")
            (src_dir / "other.txt").write_text("other")

            with patch("deerflow.client.get_uploads_dir", return_value=uploads_dir), patch("deerflow.client.ensure_uploads_dir", return_value=uploads_dir):
                result = uploads_client.upload_files("thread-1", [src_dir / "note.txt", src_dir / "other.txt"])

            assert result["success"] is False
            assert result["skipped_files"] == ["note.txt"]
            assert [f["filename"] for f in result["files"]] == ["other.txt"]
            assert result["message"] == "Successfully uploaded 1 file(s); skipped 1 unsafe file(s)"
            # 未写穿：symlink 目标保持原内容，链接本身仍在，常规文件照常落地。
            assert outside.read_text() == "original"
            assert link.is_symlink()
            assert (uploads_dir / "other.txt").read_text() == "other"
