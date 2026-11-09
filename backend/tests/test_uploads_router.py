import asyncio
import os
import stat
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from _router_auth_helpers import call_unwrapped
from fastapi import HTTPException, UploadFile

from app.gateway.routers import uploads


def test_upload_files_writes_thread_storage_and_skips_local_sandbox_sync(tmp_path):
    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)

    provider = MagicMock()
    provider.uses_thread_data_mounts = True
    provider.acquire.return_value = "local"
    sandbox = MagicMock()
    provider.get.return_value = sandbox

    with (
        patch.object(uploads, "get_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "ensure_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "get_sandbox_provider", return_value=provider),
    ):
        file = UploadFile(filename="notes.txt", file=BytesIO(b"hello uploads"))
        result = asyncio.run(call_unwrapped(uploads.upload_files, "thread-local", request=MagicMock(), files=[file], config=SimpleNamespace()))

    assert result.success is True
    assert len(result.files) == 1
    assert result.files[0]["filename"] == "notes.txt"
    assert (thread_uploads_dir / "notes.txt").read_bytes() == b"hello uploads"

    sandbox.update_file.assert_not_called()


def test_upload_files_skips_acquire_when_thread_data_is_mounted(tmp_path):
    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)

    provider = MagicMock()
    provider.uses_thread_data_mounts = True

    with (
        patch.object(uploads, "get_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "ensure_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "get_sandbox_provider", return_value=provider),
    ):
        file = UploadFile(filename="notes.txt", file=BytesIO(b"hello uploads"))
        result = asyncio.run(call_unwrapped(uploads.upload_files, "thread-mounted", request=MagicMock(), files=[file], config=SimpleNamespace()))

    assert result.success is True
    assert (thread_uploads_dir / "notes.txt").read_bytes() == b"hello uploads"
    provider.acquire.assert_not_called()
    provider.get.assert_not_called()


def test_upload_files_does_not_auto_convert_documents_by_default(tmp_path):
    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)

    provider = MagicMock()
    provider.uses_thread_data_mounts = True
    provider.acquire.return_value = "local"
    sandbox = MagicMock()
    provider.get.return_value = sandbox

    with (
        patch.object(uploads, "get_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "ensure_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "get_sandbox_provider", return_value=provider),
        patch.object(uploads, "_auto_convert_documents_enabled", return_value=False),
        patch.object(uploads, "convert_file_to_markdown", AsyncMock()) as convert_mock,
    ):
        file = UploadFile(filename="report.pdf", file=BytesIO(b"pdf-bytes"))
        result = asyncio.run(call_unwrapped(uploads.upload_files, "thread-local", request=MagicMock(), files=[file], config=SimpleNamespace()))

    assert result.success is True
    assert len(result.files) == 1
    assert result.files[0]["filename"] == "report.pdf"
    assert "markdown_file" not in result.files[0]
    convert_mock.assert_not_called()
    assert not (thread_uploads_dir / "report.md").exists()


def test_upload_files_syncs_non_local_sandbox_and_marks_markdown_file(tmp_path):
    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)

    provider = MagicMock()
    provider.uses_thread_data_mounts = False
    provider.acquire.return_value = "aio-1"
    sandbox = MagicMock()
    provider.get.return_value = sandbox

    async def fake_convert(file_path: Path, output_path: Path | None = None) -> Path:
        md_path = output_path if output_path is not None else file_path.with_suffix(".md")
        md_path.write_text("converted", encoding="utf-8")
        return md_path

    with (
        patch.object(uploads, "get_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "ensure_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "get_sandbox_provider", return_value=provider),
        patch.object(uploads, "_auto_convert_documents_enabled", return_value=True),
        patch.object(uploads, "convert_file_to_markdown", AsyncMock(side_effect=fake_convert)),
    ):
        file = UploadFile(filename="report.pdf", file=BytesIO(b"pdf-bytes"))
        result = asyncio.run(call_unwrapped(uploads.upload_files, "thread-aio", request=MagicMock(), files=[file], config=SimpleNamespace()))

    assert result.success is True
    assert len(result.files) == 1
    file_info = result.files[0]
    assert file_info["filename"] == "report.pdf"
    assert file_info["markdown_file"] == "report.md"

    assert (thread_uploads_dir / "report.pdf").read_bytes() == b"pdf-bytes"
    assert (thread_uploads_dir / "report.md").read_text(encoding="utf-8") == "converted"

    sandbox.upload_file_stream.assert_any_call("/mnt/user-data/uploads/report.pdf", str(thread_uploads_dir / "report.pdf"))
    sandbox.upload_file_stream.assert_any_call("/mnt/user-data/uploads/report.md", str(thread_uploads_dir / "report.md"))


def test_upload_files_makes_non_local_files_sandbox_writable(tmp_path):
    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)

    provider = MagicMock()
    provider.uses_thread_data_mounts = False
    provider.acquire.return_value = "aio-1"
    sandbox = MagicMock()
    provider.get.return_value = sandbox

    async def fake_convert(file_path: Path, output_path: Path | None = None) -> Path:
        md_path = output_path if output_path is not None else file_path.with_suffix(".md")
        md_path.write_text("converted", encoding="utf-8")
        return md_path

    with (
        patch.object(uploads, "get_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "ensure_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "get_sandbox_provider", return_value=provider),
        patch.object(uploads, "_auto_convert_documents_enabled", return_value=True),
        patch.object(uploads, "convert_file_to_markdown", AsyncMock(side_effect=fake_convert)),
        patch.object(uploads, "_make_file_sandbox_writable") as make_writable,
    ):
        file = UploadFile(filename="report.pdf", file=BytesIO(b"pdf-bytes"))
        result = asyncio.run(call_unwrapped(uploads.upload_files, "thread-aio", request=MagicMock(), files=[file], config=SimpleNamespace()))

    assert result.success is True
    make_writable.assert_any_call(thread_uploads_dir / "report.pdf")
    make_writable.assert_any_call(thread_uploads_dir / "report.md")


def test_upload_files_does_not_adjust_permissions_for_local_sandbox(tmp_path):
    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)

    provider = MagicMock()
    provider.uses_thread_data_mounts = True
    provider.acquire.return_value = "local"
    sandbox = MagicMock()
    provider.get.return_value = sandbox

    with (
        patch.object(uploads, "get_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "ensure_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "get_sandbox_provider", return_value=provider),
        patch.object(uploads, "_make_file_sandbox_writable") as make_writable,
    ):
        file = UploadFile(filename="notes.txt", file=BytesIO(b"hello uploads"))
        result = asyncio.run(call_unwrapped(uploads.upload_files, "thread-local", request=MagicMock(), files=[file], config=SimpleNamespace()))

    assert result.success is True
    make_writable.assert_not_called()


def test_make_file_sandbox_writable_adds_write_bits_for_regular_files(tmp_path):
    file_path = tmp_path / "report.pdf"
    file_path.write_bytes(b"pdf-bytes")
    os_chmod_mode = stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH
    file_path.chmod(os_chmod_mode)

    uploads._make_file_sandbox_writable(file_path)

    updated_mode = stat.S_IMODE(file_path.stat().st_mode)
    assert updated_mode & stat.S_IWUSR
    assert updated_mode & stat.S_IWGRP
    assert updated_mode & stat.S_IWOTH


def test_make_file_sandbox_writable_skips_symlinks(tmp_path):
    file_path = tmp_path / "target-link.txt"
    file_path.write_text("hello", encoding="utf-8")
    symlink_stat = MagicMock(st_mode=stat.S_IFLNK)

    with (
        patch.object(uploads.os, "lstat", return_value=symlink_stat),
        patch.object(uploads.os, "chmod") as chmod,
    ):
        uploads._make_file_sandbox_writable(file_path)

    chmod.assert_not_called()


def test_upload_files_rejects_dotdot_and_dot_filenames(tmp_path):
    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)

    provider = MagicMock()
    provider.acquire.return_value = "local"
    sandbox = MagicMock()
    provider.get.return_value = sandbox

    with (
        patch.object(uploads, "get_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "ensure_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "get_sandbox_provider", return_value=provider),
    ):
        # These filenames must be rejected outright
        for bad_name in ["..", "."]:
            file = UploadFile(filename=bad_name, file=BytesIO(b"data"))
            result = asyncio.run(call_unwrapped(uploads.upload_files, "thread-local", request=MagicMock(), files=[file], config=SimpleNamespace()))
            assert result.success is True
            assert result.files == [], f"Expected no files for unsafe filename {bad_name!r}"

        # Path-traversal prefixes are stripped to the basename and accepted safely
        file = UploadFile(filename="../etc/passwd.txt", file=BytesIO(b"data"))
        result = asyncio.run(call_unwrapped(uploads.upload_files, "thread-local", request=MagicMock(), files=[file], config=SimpleNamespace()))
        assert result.success is True
        assert len(result.files) == 1
        assert result.files[0]["filename"] == "passwd.txt"

    # Only the safely normalised file should exist
    assert [f.name for f in thread_uploads_dir.iterdir()] == ["passwd.txt"]


def test_upload_files_reports_skipped_unsafe_filenames(tmp_path):
    """A rejected filename must be reported through skipped_files — silently
    dropping a file from a multi-file upload leaves the caller without a clue
    (mirrors the DeerFlowClient upload_files contract)."""
    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)

    provider = MagicMock()
    provider.uses_thread_data_mounts = True

    with (
        patch.object(uploads, "get_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "ensure_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "get_sandbox_provider", return_value=provider),
    ):
        good = UploadFile(filename="notes.txt", file=BytesIO(b"hello uploads"))
        # Rejected outright by normalize_filename (ValueError) — unlike
        # "../evil.txt", which is basename-stripped to a safe name and accepted.
        evil = UploadFile(filename="..", file=BytesIO(b"data"))
        result = asyncio.run(call_unwrapped(uploads.upload_files, "thread-skip", request=MagicMock(), files=[good, evil], config=SimpleNamespace()))

    payload = result.model_dump()
    assert payload["success"] is True
    assert len(payload["files"]) == 1
    assert payload["files"][0]["filename"] == "notes.txt"
    assert payload["skipped_files"] == [".."]
    assert "skipped 1 unsafe file(s)" in payload["message"]
    assert (thread_uploads_dir / "notes.txt").read_bytes() == b"hello uploads"


def test_upload_files_skipped_files_defaults_to_empty(tmp_path):
    """An all-clean upload reports skipped_files == [] (additive field stays
    backward compatible)."""
    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)

    provider = MagicMock()
    provider.uses_thread_data_mounts = True

    with (
        patch.object(uploads, "get_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "ensure_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "get_sandbox_provider", return_value=provider),
    ):
        file = UploadFile(filename="notes.txt", file=BytesIO(b"hello uploads"))
        result = asyncio.run(call_unwrapped(uploads.upload_files, "thread-clean", request=MagicMock(), files=[file], config=SimpleNamespace()))

    payload = result.model_dump()
    assert payload["success"] is True
    assert payload["skipped_files"] == []
    assert payload["message"] == "Successfully uploaded 1 file(s)"


def test_delete_uploaded_file_keeps_markdown_companion(tmp_path):
    """删除只针对被请求的文件：伴生 .md 可能属于同词干其它文档或用户自己，
    留在列表中可单独删（上游 f9f3127d 三件套行为）。"""
    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)
    (thread_uploads_dir / "report.pdf").write_bytes(b"pdf-bytes")
    (thread_uploads_dir / "report.md").write_text("converted", encoding="utf-8")

    with patch.object(uploads, "get_uploads_dir", return_value=thread_uploads_dir):
        result = asyncio.run(call_unwrapped(uploads.delete_uploaded_file, "thread-aio", "report.pdf", request=MagicMock()))

    assert result == {"success": True, "message": "Deleted report.pdf"}
    assert not (thread_uploads_dir / "report.pdf").exists()
    assert (thread_uploads_dir / "report.md").exists()


def test_auto_convert_documents_enabled_defaults_to_false_on_config_errors():
    class BrokenConfig:
        def __getattribute__(self, name):
            if name == "uploads":
                raise RuntimeError("boom")
            return super().__getattribute__(name)

    assert uploads._auto_convert_documents_enabled(BrokenConfig()) is False


def test_auto_convert_documents_enabled_reads_dict_backed_uploads_config():
    cfg = MagicMock()
    cfg.uploads = {"auto_convert_documents": True}

    assert uploads._auto_convert_documents_enabled(cfg) is True


def test_auto_convert_documents_enabled_accepts_boolean_and_string_truthy_values():
    false_cfg = MagicMock()
    false_cfg.uploads = MagicMock(auto_convert_documents=False)

    true_cfg = MagicMock()
    true_cfg.uploads = MagicMock(auto_convert_documents=True)

    string_true_cfg = MagicMock()
    string_true_cfg.uploads = MagicMock(auto_convert_documents="YES")

    string_false_cfg = MagicMock()
    string_false_cfg.uploads = MagicMock(auto_convert_documents="false")

    assert uploads._auto_convert_documents_enabled(false_cfg) is False
    assert uploads._auto_convert_documents_enabled(true_cfg) is True
    assert uploads._auto_convert_documents_enabled(string_true_cfg) is True
    assert uploads._auto_convert_documents_enabled(string_false_cfg) is False


# ── Size guard tests ──────────────────────────────────────────────────────


def test_max_file_size_exceeded_returns_413_and_cleans_up_half_written_file(tmp_path):
    """When a file exceeds max_file_size_bytes the partial file is deleted."""
    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)

    provider = MagicMock()
    provider.uses_thread_data_mounts = True
    provider.acquire.return_value = "local"
    provider.get.return_value = MagicMock()

    # Set a tiny per-file limit: 10 bytes
    config = SimpleNamespace()
    config.uploads = {"max_file_size_bytes": 10}

    with (
        patch.object(uploads, "get_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "ensure_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "get_sandbox_provider", return_value=provider),
    ):
        file = UploadFile(filename="big.txt", file=BytesIO(b"A" * 100))
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(call_unwrapped(uploads.upload_files, "thread-sz", request=MagicMock(), files=[file], config=config))

    assert exc_info.value.status_code == 413
    assert "exceeds maximum upload size" in exc_info.value.detail
    assert not (thread_uploads_dir / "big.txt").exists()


def test_max_thread_total_exceeded_returns_413_and_cleans_up_new_file(tmp_path):
    """When an upload would push the thread past its total quota, reject it."""
    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)
    # Pre-populate: 70 bytes already on disk, quota is 100
    (thread_uploads_dir / "existing.txt").write_bytes(b"X" * 70)

    provider = MagicMock()
    provider.uses_thread_data_mounts = True
    provider.acquire.return_value = "local"
    provider.get.return_value = MagicMock()

    config = SimpleNamespace()
    config.uploads = {"max_thread_total_bytes": 100}

    with (
        patch.object(uploads, "get_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "ensure_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "get_sandbox_provider", return_value=provider),
    ):
        # 40-byte file → 70 + 40 = 110 > 100
        file = UploadFile(filename="extra.txt", file=BytesIO(b"Y" * 40))
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(call_unwrapped(uploads.upload_files, "thread-quota", request=MagicMock(), files=[file], config=config))

    assert exc_info.value.status_code == 413
    assert not (thread_uploads_dir / "extra.txt").exists()
    # Existing file untouched
    assert (thread_uploads_dir / "existing.txt").read_bytes() == b"X" * 70


def test_upload_file_stream_called_when_sync_enabled_and_update_file_not(tmp_path):
    """When sync_to_sandbox is True, upload_file_stream is used instead of
    update_file."""
    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)

    provider = MagicMock()
    provider.uses_thread_data_mounts = False
    provider.acquire.return_value = "aio-1"
    sandbox = MagicMock()
    provider.get.return_value = sandbox

    with (
        patch.object(uploads, "get_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "ensure_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "get_sandbox_provider", return_value=provider),
    ):
        file = UploadFile(filename="video.mp4", file=BytesIO(b"frame data"))
        result = asyncio.run(call_unwrapped(uploads.upload_files, "thread-sync", request=MagicMock(), files=[file], config=SimpleNamespace()))

    assert result.success is True
    # upload_file_stream must be called with the virtual path and local path
    sandbox.upload_file_stream.assert_called_once_with(
        "/mnt/user-data/uploads/video.mp4",
        str(thread_uploads_dir / "video.mp4"),
    )
    # update_file must NOT be called
    sandbox.update_file.assert_not_called()


def test_auto_convert_skipped_when_file_exceeds_convert_size_limit(tmp_path):
    """Files larger than max_convert_size_bytes skip automatic conversion."""
    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)

    provider = MagicMock()
    provider.uses_thread_data_mounts = True
    provider.acquire.return_value = "local"
    provider.get.return_value = MagicMock()

    config = SimpleNamespace()
    config.uploads = {"auto_convert_documents": True, "max_convert_size_bytes": 5}

    async def fake_convert(file_path: Path, output_path: Path | None = None) -> Path:
        md_path = output_path if output_path is not None else file_path.with_suffix(".md")
        md_path.write_text("converted", encoding="utf-8")
        return md_path

    with (
        patch.object(uploads, "get_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "ensure_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "get_sandbox_provider", return_value=provider),
        patch.object(uploads, "convert_file_to_markdown", AsyncMock(side_effect=fake_convert)),
    ):
        # 10-byte file > 5-byte convert limit
        file = UploadFile(filename="slides.pptx", file=BytesIO(b"Y" * 10))
        result = asyncio.run(call_unwrapped(uploads.upload_files, "thread-conv", request=MagicMock(), files=[file], config=config))

    assert result.success is True
    assert "markdown_file" not in result.files[0]
    assert not (thread_uploads_dir / "slides.md").exists()


def test_overwrite_failure_preserves_original_file(tmp_path):
    """When an overwriting upload fails (size guard or quota), the original
    file must be preserved — the write happens to a .tmp file first, and
    ``os.replace`` only executes on success."""
    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)

    # Pre-populate the "old" file
    existing = thread_uploads_dir / "report.pdf"
    existing.write_bytes(b"original content")

    provider = MagicMock()
    provider.uses_thread_data_mounts = True

    config = SimpleNamespace()
    config.uploads = {"max_file_size_bytes": 10}  # tiny limit

    with (
        patch.object(uploads, "get_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "ensure_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "get_sandbox_provider", return_value=provider),
    ):
        file = UploadFile(filename="report.pdf", file=BytesIO(b"A" * 100))
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(call_unwrapped(uploads.upload_files, "thread-ow", request=MagicMock(), files=[file], config=config))

    assert exc_info.value.status_code == 413
    # Original file must still be intact
    assert existing.read_bytes() == b"original content"
    # Temp file must have been cleaned up
    assert not (thread_uploads_dir / "report.tmp").exists()


def test_default_limits_are_sensible():
    """Sanity-check that the module-level defaults are large enough for real
    files but not unreasonably large."""
    assert uploads._DEFAULT_MAX_FILE_SIZE_BYTES == 2 * 1024**3
    assert uploads._DEFAULT_MAX_THREAD_TOTAL_BYTES == 10 * 1024**3
    assert uploads._DEFAULT_MAX_CONVERT_SIZE_BYTES == 50 * 1024**2


def test_concurrent_same_name_uploads_do_not_interleave(tmp_path):
    """R-10 (2026-08-13): two concurrent uploads of the same filename must each
    write to their OWN mkstemp staging file — the deterministic ``<name>.tmp``
    staging let them interleave into one corrupted file."""
    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)

    provider = MagicMock()
    provider.uses_thread_data_mounts = True

    async def _run_upload(content: bytes) -> None:
        file = UploadFile(filename="same.txt", file=BytesIO(content))
        result = await call_unwrapped(uploads.upload_files, "thread-cc", request=MagicMock(), files=[file], config=SimpleNamespace())
        assert result.success is True

    async def _drive():
        await asyncio.gather(
            _run_upload(b"A" * 100_000),
            _run_upload(b"B" * 100_000),
        )

    with (
        patch.object(uploads, "get_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "ensure_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "get_sandbox_provider", return_value=provider),
    ):
        asyncio.run(_drive())

    final = (thread_uploads_dir / "same.txt").read_bytes()
    # Last commit wins atomically — the file is one of the two full payloads,
    # never an interleaved mix.
    assert final == b"A" * 100_000 or final == b"B" * 100_000
    # No staging orphans left behind.
    assert [p.name for p in thread_uploads_dir.iterdir()] == ["same.txt"]


def test_upload_staging_uses_unique_part_files(tmp_path):
    """Staging files must land in the .upload-*.part namespace (mkstemp)."""
    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)

    provider = MagicMock()
    provider.uses_thread_data_mounts = True

    created_staging = []

    real_mkstemp = __import__("tempfile").mkstemp

    def _spy_mkstemp(*args, **kwargs):
        fd, path = real_mkstemp(*args, **kwargs)
        created_staging.append(Path(path).name)
        return fd, path

    with (
        patch.object(uploads, "get_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "ensure_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "get_sandbox_provider", return_value=provider),
        patch("tempfile.mkstemp", _spy_mkstemp),
    ):
        file = UploadFile(filename="report.pdf", file=BytesIO(b"pdf bytes"))
        asyncio.run(call_unwrapped(uploads.upload_files, "thread-spy", request=MagicMock(), files=[file], config=SimpleNamespace()))

    assert len(created_staging) == 1
    assert created_staging[0].startswith(".upload-") and created_staging[0].endswith(".part")
    assert (thread_uploads_dir / "report.pdf").read_bytes() == b"pdf bytes"


# ── Conversion-source TOCTOU regression (upstream #5611 = 29d285731) ──────


def test_upload_files_converts_staged_bytes_not_landed_name(tmp_path):
    """After os.replace commits the upload, the uploads dir is sandbox-writable:
    a watching process can swap the landed name for a symlink to a host file
    before the converter opens it. Conversion must read the bytes this request
    staged (the descriptor copy outside the uploads tree), so the companion .md
    holds the uploaded content — never the host file's (upstream #5611)."""
    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)
    host_file = tmp_path / "host-secret.txt"
    host_file.write_text("HOST SECRET PAYLOAD", encoding="utf-8")

    provider = MagicMock()
    provider.uses_thread_data_mounts = True

    convert_sources: list[Path] = []

    async def fake_convert(file_path: Path, output_path: Path | None = None) -> Path:
        # The attacker's move: swap the just-committed name for a symlink to a
        # host file inside the conversion window, before the source is read.
        landed = thread_uploads_dir / "report.pdf"
        assert landed.exists()
        os.unlink(landed)
        os.symlink(host_file, landed)
        convert_sources.append(Path(file_path))
        md_path = output_path if output_path is not None else Path(file_path).with_suffix(".md")
        md_path.write_text(Path(file_path).read_bytes().decode("utf-8", "replace"), encoding="utf-8")
        return md_path

    config = SimpleNamespace()
    config.uploads = {"auto_convert_documents": True}

    with (
        patch.object(uploads, "get_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "ensure_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "get_sandbox_provider", return_value=provider),
        patch.object(uploads, "convert_file_to_markdown", AsyncMock(side_effect=fake_convert)),
    ):
        file = UploadFile(filename="report.pdf", file=BytesIO(b"uploaded-pdf-bytes"))
        result = asyncio.run(call_unwrapped(uploads.upload_files, "thread-tmp", request=MagicMock(), files=[file], config=config))

    assert result.success is True
    assert len(convert_sources) == 1
    source = convert_sources[0]
    # The conversion input must be the staged-bytes copy outside the uploads
    # tree, not the committed (and now attacker-controlled) name.
    assert source.name == "report.pdf"
    assert source != thread_uploads_dir / "report.pdf"
    assert thread_uploads_dir not in source.parents
    # The companion .md therefore carries the uploaded bytes, not the host file's.
    assert (thread_uploads_dir / "report.md").read_text(encoding="utf-8") == "uploaded-pdf-bytes"
    assert result.files[0]["markdown_file"] == "report.md"
    assert host_file.read_text(encoding="utf-8") == "HOST SECRET PAYLOAD"


def test_copy_fd_to_path_closes_fd_after_copy(tmp_path):
    """_copy_fd_to_path owns the duplicated descriptor: it closes it once the
    bytes are copied, so the caller must never see it left open."""
    staged = tmp_path / "staged.bin"
    staged.write_bytes(b"staged bytes")
    fd = os.open(staged, os.O_RDONLY)
    dest = tmp_path / "out.bin"

    uploads._copy_fd_to_path(fd, dest)

    assert dest.read_bytes() == b"staged bytes"
    with pytest.raises(OSError):
        os.fstat(fd)


def test_copy_fd_to_path_closes_fd_when_copy_fails(tmp_path):
    """Upstream #5611 review follow-up (terminal state): a failure staging the
    copy must still close the duplicated descriptor — the upload's own cleanup
    only unlinks names, so an unclosed fd would pin the unlinked staged bytes
    for the life of the process."""
    staged = tmp_path / "staged.bin"
    staged.write_bytes(b"staged bytes")
    fd = os.open(staged, os.O_RDONLY)
    blocked_dir = tmp_path / "blocked"
    blocked_dir.mkdir()
    blocked_dir.chmod(0o500)

    try:
        with pytest.raises(OSError):
            uploads._copy_fd_to_path(fd, blocked_dir / "out.bin")
        with pytest.raises(OSError):
            os.fstat(fd)
    finally:
        blocked_dir.chmod(0o700)


# ── R2-1 (2026-10-07): thread upload extension allowlist ────────────────────


def _allowlist_test_env(tmp_path):
    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)

    provider = MagicMock()
    provider.uses_thread_data_mounts = True

    patches = (
        patch.object(uploads, "get_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "ensure_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "get_sandbox_provider", return_value=provider),
    )
    return thread_uploads_dir, patches


def test_upload_files_rejects_disallowed_extension_with_400(tmp_path):
    """exe 请求 400：准入控制在写盘前拦截（R2-1 核心）。"""
    thread_uploads_dir, patches = _allowlist_test_env(tmp_path)

    with patches[0], patches[1], patches[2]:
        file = UploadFile(filename="payload.exe", file=BytesIO(b"MZ"))
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(call_unwrapped(uploads.upload_files, "thread-r21", request=MagicMock(), files=[file], config=SimpleNamespace()))

    assert exc_info.value.status_code == 400
    assert "payload.exe" in exc_info.value.detail
    assert not any(thread_uploads_dir.iterdir())


def test_upload_files_accepts_png_and_html_by_default(tmp_path):
    """png/html 按 D2 默认白名单放行。"""
    thread_uploads_dir, patches = _allowlist_test_env(tmp_path)

    with patches[0], patches[1], patches[2]:
        files = [
            UploadFile(filename="photo.PNG", file=BytesIO(b"png-bytes")),
            UploadFile(filename="page.html", file=BytesIO(b"<p>html</p>")),
        ]
        result = asyncio.run(call_unwrapped(uploads.upload_files, "thread-r21", request=MagicMock(), files=files, config=SimpleNamespace()))

    assert result.success is True
    assert [f["filename"] for f in result.files] == ["photo.PNG", "page.html"]
    assert (thread_uploads_dir / "photo.PNG").read_bytes() == b"png-bytes"
    assert (thread_uploads_dir / "page.html").read_bytes() == b"<p>html</p>"


def test_upload_files_allowed_extensions_config_override(tmp_path):
    """uploads.allowed_extensions 覆盖：srt 放行、png 反被拒。"""
    thread_uploads_dir, patches = _allowlist_test_env(tmp_path)
    config = SimpleNamespace(uploads={"allowed_extensions": ["srt", "vtt"]})

    with patches[0], patches[1], patches[2]:
        ok_file = UploadFile(filename="sub.srt", file=BytesIO(b"1\n00:00 --> x\nhi"))
        result = asyncio.run(call_unwrapped(uploads.upload_files, "thread-r21", request=MagicMock(), files=[ok_file], config=config))
        assert result.success is True
        assert (thread_uploads_dir / "sub.srt").exists()

        bad_file = UploadFile(filename="img.png", file=BytesIO(b"png-bytes"))
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(call_unwrapped(uploads.upload_files, "thread-r21", request=MagicMock(), files=[bad_file], config=config))
        assert exc_info.value.status_code == 400


def test_get_allowed_extensions_falls_back_on_empty_or_broken_override():
    """空/畸形覆盖回落默认白名单——blocking-all 不会是运维本意。"""
    assert uploads._get_allowed_extensions(SimpleNamespace()) == uploads._DEFAULT_ALLOWED_EXTENSIONS

    empty = SimpleNamespace(uploads={"allowed_extensions": []})
    assert uploads._get_allowed_extensions(empty) == uploads._DEFAULT_ALLOWED_EXTENSIONS

    broken = SimpleNamespace(uploads={"allowed_extensions": 42})
    assert uploads._get_allowed_extensions(broken) == uploads._DEFAULT_ALLOWED_EXTENSIONS


def test_upload_files_string_allowed_extensions_override_falls_back(tmp_path):
    """字符串型覆盖（如 allowed_extensions: "jpg,png"）回落默认白名单。

    str 可迭代且不抛错——修前逐字符迭代得到 {'j','p','g',',','n'} 形态的
    非空垃圾集合，既不触发空回落也不进 except，全站上传 400 且无日志。
    """
    thread_uploads_dir = tmp_path / "uploads"
    thread_uploads_dir.mkdir(parents=True)

    provider = MagicMock()
    provider.uses_thread_data_mounts = True
    config = SimpleNamespace(uploads={"allowed_extensions": "jpg,png"})

    with (
        patch.object(uploads, "get_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "ensure_uploads_dir", return_value=thread_uploads_dir),
        patch.object(uploads, "get_sandbox_provider", return_value=provider),
    ):
        file = UploadFile(filename="photo.jpg", file=BytesIO(b"jpg-bytes"))
        result = asyncio.run(call_unwrapped(uploads.upload_files, "thread-str-ov", request=MagicMock(), files=[file], config=config))

    assert result.success is True
    assert (thread_uploads_dir / "photo.jpg").read_bytes() == b"jpg-bytes"


def test_get_allowed_extensions_string_override_warns_and_falls_back(caplog):
    """字符串型覆盖必须落 warning——否则运维无从排查上传为何回落默认。"""
    config = SimpleNamespace(uploads={"allowed_extensions": "jpg,png"})

    with caplog.at_level("WARNING", logger="app.gateway.routers.uploads"):
        result = uploads._get_allowed_extensions(config)

    assert result == uploads._DEFAULT_ALLOWED_EXTENSIONS
    assert "jpg" in result
    assert any("allowed_extensions" in rec.message and "malformed" in rec.message for rec in caplog.records)
