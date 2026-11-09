"""Tests for startup housekeeping: stale upload staging cleanup + tiktoken warm-up.

Covers the two lifespan startup tasks ported from upstream:
- ``cleanup_stale_upload_staging_files`` removes orphaned ``.upload-*.part``
  staging files left by a hard crash during upload streaming, without touching
  real uploads — a bare ``*.tmp`` file is a USER file, not staging (R-09,
  2026-08-13).
- The tiktoken warm-up wrapper never raises — timeout/failure only logs, so a
  network-restricted deployment still boots (token counting falls back to
  character estimation).
"""

import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from deerflow.uploads.manager import cleanup_stale_upload_staging_files


def _make_uploads_dir(base: Path, rel: str) -> Path:
    uploads_dir = base / rel
    uploads_dir.mkdir(parents=True)
    return uploads_dir


def test_cleanup_removes_only_upload_part_staging_files(tmp_path: Path) -> None:
    legacy = _make_uploads_dir(tmp_path, "threads/t1/user-data/uploads")
    scoped = _make_uploads_dir(tmp_path, "users/u1/threads/t2/user-data/uploads")

    (legacy / "report.pdf").write_bytes(b"real upload")
    (legacy / ".upload-ab12cd.part").write_bytes(b"partial upload")
    (scoped / "notes.txt").write_bytes(b"real upload")
    (scoped / "notes.txt.tmp").write_bytes(b"user file ending in .tmp is not staging (R-09)")
    (scoped / ".upload-xyz.part").write_bytes(b"partial upload")
    # A directory ending in .part must not be treated as a staging file.
    (scoped / ".upload-dir.part").mkdir()

    removed = cleanup_stale_upload_staging_files(base_dir=tmp_path)

    assert removed == 2
    assert (legacy / "report.pdf").exists()
    assert not (legacy / ".upload-ab12cd.part").exists()
    assert (scoped / "notes.txt").exists()
    assert (scoped / "notes.txt.tmp").exists()  # user *.tmp must survive (R-09)
    assert not (scoped / ".upload-xyz.part").exists()
    assert (scoped / ".upload-dir.part").exists()


def test_cleanup_counts_staging_files_across_both_dir_layouts(tmp_path: Path) -> None:
    _make_uploads_dir(tmp_path, "threads/t1/user-data/uploads")
    (tmp_path / "threads/t1/user-data/uploads/.upload-a.part").write_bytes(b"x")
    # Files outside uploads dirs are never touched.
    (tmp_path / "threads/t1/keep.tmp").write_bytes(b"x")

    removed = cleanup_stale_upload_staging_files(base_dir=tmp_path)

    assert removed == 1
    assert (tmp_path / "threads/t1/keep.tmp").exists()


def test_cleanup_missing_base_dir_returns_zero(tmp_path: Path) -> None:
    assert cleanup_stale_upload_staging_files(base_dir=tmp_path / "missing") == 0


def test_cleanup_defaults_to_paths_base_dir(tmp_path: Path) -> None:
    uploads = _make_uploads_dir(tmp_path, "threads/t1/user-data/uploads")
    (uploads / ".upload-orphan.part").write_bytes(b"x")

    with patch("deerflow.uploads.manager.get_paths", return_value=SimpleNamespace(base_dir=tmp_path)):
        removed = cleanup_stale_upload_staging_files()

    assert removed == 1
    assert not (uploads / ".upload-orphan.part").exists()


def test_warm_tiktoken_encoding_cache_success() -> None:
    from deerflow.agents.memory.prompt import warm_tiktoken_encoding_cache

    assert warm_tiktoken_encoding_cache() is True


def test_warm_tiktoken_encoding_cache_failure_returns_false() -> None:
    from deerflow.agents.memory.prompt import warm_tiktoken_encoding_cache

    with patch("deerflow.agents.memory.prompt.tiktoken") as mock_tiktoken:
        mock_tiktoken.get_encoding.side_effect = RuntimeError("network blocked")
        assert warm_tiktoken_encoding_cache() is False


def test_warm_tiktoken_encoding_cache_unavailable_returns_none() -> None:
    from deerflow.agents.memory import prompt

    with patch.object(prompt, "TIKTOKEN_AVAILABLE", False):
        assert prompt.warm_tiktoken_encoding_cache() is None


@pytest.mark.anyio
async def test_lifespan_warmup_timeout_does_not_raise() -> None:
    from app.gateway.app import _warm_tiktoken_encoding_cache

    def slow_warm() -> bool:
        time.sleep(1.0)
        return True

    with patch("deerflow.agents.memory.prompt.warm_tiktoken_encoding_cache", side_effect=slow_warm):
        # Must swallow the timeout: a restricted network must not block boot.
        await _warm_tiktoken_encoding_cache(timeout=0.05)


@pytest.mark.anyio
async def test_lifespan_warmup_failure_does_not_raise() -> None:
    from app.gateway.app import _warm_tiktoken_encoding_cache

    with patch("deerflow.agents.memory.prompt.warm_tiktoken_encoding_cache", side_effect=RuntimeError("boom")):
        await _warm_tiktoken_encoding_cache(timeout=1.0)


@pytest.mark.anyio
async def test_lifespan_warmup_success_completes() -> None:
    from app.gateway.app import _warm_tiktoken_encoding_cache

    with patch("deerflow.agents.memory.prompt.warm_tiktoken_encoding_cache", return_value=True) as mock_warm:
        await _warm_tiktoken_encoding_cache(timeout=5.0)

    assert mock_warm.called
