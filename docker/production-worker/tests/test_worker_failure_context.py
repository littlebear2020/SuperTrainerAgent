"""Tests for worker failure-context diagnostic snapshot."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def test_write_failure_context_records_diagnostic(tmp_path):
    """On failure, a _failure_context.txt is written with the error + file list."""
    import worker

    (tmp_path / "concat_video.mp4").write_bytes(b"v")
    (tmp_path / "segments").mkdir()
    (tmp_path / "segments" / "seg_000.mp4").write_bytes(b"s")

    try:
        raise RuntimeError("subtitle burn failed: libass missing")
    except RuntimeError:
        worker._write_failure_context(
            tmp_path,
            sys.exc_info(),
            task_id="t1",
            domain="video_gen",
            task_type="v2_mixed",
            elapsed=12.3,
        )

    ctx = (tmp_path / "_failure_context.txt").read_text(encoding="utf-8")
    assert "RuntimeError" in ctx
    assert "libass missing" in ctx
    assert "concat_video.mp4" in ctx
    assert "video_gen" in ctx
    assert "v2_mixed" in ctx


def test_write_failure_context_never_raises(tmp_path):
    """A broken/missing task_dir must not make the failure path itself raise."""
    import worker

    try:
        raise ValueError("boom")
    except ValueError:
        # Should swallow internally; not write to a non-existent dir is fine.
        worker._write_failure_context(
            tmp_path / "does-not-exist",
            sys.exc_info(),
            task_id="t2",
            domain="video_gen",
            task_type="v2_mixed",
            elapsed=1.0,
        )


def test_failure_context_survives_cleanup(tmp_path):
    """_failure_context.txt is not in _cleanup_task_dir's removal list."""
    import worker

    (tmp_path / "_failure_context.txt").write_text("ctx", encoding="utf-8")
    (tmp_path / "segments").mkdir()
    worker._cleanup_task_dir(tmp_path)
    assert (tmp_path / "_failure_context.txt").exists()
    assert not (tmp_path / "segments").exists()
