import os
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.gateway.works import service as works_service
from app.gateway.works.service import build_thread_artifact_summary, load_thread_artifact_summary

OUTPUTS_PREFIX = "/mnt/user-data/outputs/"


@pytest.fixture
def output_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "outputs"
    root.mkdir()

    def resolve(_thread_id: str, virtual_path: str) -> Path:
        relative = PurePosixPath(virtual_path).relative_to(OUTPUTS_PREFIX)
        if ".." in relative.parts:
            raise HTTPException(status_code=403, detail="Invalid or disallowed path")
        return root.joinpath(*relative.parts)

    monkeypatch.setattr(works_service, "resolve_thread_virtual_path", resolve)
    return root


def write_output(root: Path, name: str, content: bytes = b"content", mtime: float = 1_700_000_000) -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    os.utime(path, (mtime, mtime))
    return path


def test_empty_artifacts_are_not_delivered(output_root: Path) -> None:
    summary = build_thread_artifact_summary("thread-1", [])

    assert summary.delivery_state == "not_delivered"
    assert summary.files == []


def test_delivery_states_distinguish_complete_partial_and_missing(output_root: Path) -> None:
    write_output(output_root, "ready.mp4")

    complete = build_thread_artifact_summary("thread-1", [f"{OUTPUTS_PREFIX}ready.mp4"])
    partial = build_thread_artifact_summary(
        "thread-1",
        [f"{OUTPUTS_PREFIX}ready.mp4", f"{OUTPUTS_PREFIX}missing.mp4"],
    )
    missing = build_thread_artifact_summary("thread-1", [f"{OUTPUTS_PREFIX}missing.mp4"])

    assert complete.delivery_state == "delivered"
    assert partial.delivery_state == "partial"
    assert missing.delivery_state == "missing"
    assert [file.exists for file in partial.files] == [True, False]


@pytest.mark.parametrize("artifacts", [None, "ready.mp4", {}, [42], ["/mnt/user-data/outputs/ready.mp4", None]])
def test_malformed_artifacts_make_the_whole_thread_unknown(output_root: Path, monkeypatch: pytest.MonkeyPatch, artifacts: object) -> None:
    resolver = AsyncMock()
    monkeypatch.setattr(works_service, "resolve_thread_virtual_path", resolver)

    summary = build_thread_artifact_summary("thread-1", artifacts)

    assert summary.delivery_state == "unknown"
    assert summary.files == []
    resolver.assert_not_called()


def test_non_outputs_path_is_rejected_before_resolution_or_file_reads(output_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write_output(output_root, "ok.txt")
    resolver_calls: list[str] = []
    stat_calls: list[Path] = []

    def forbidden_resolver(_thread_id: str, path: str) -> Path:
        resolver_calls.append(path)
        raise AssertionError("non-output path reached the resolver")

    original_stat = Path.stat

    def tracking_stat(self: Path, *args: object, **kwargs: object) -> os.stat_result:
        stat_calls.append(self)
        return original_stat(self, *args, **kwargs)

    monkeypatch.setattr(works_service, "resolve_thread_virtual_path", forbidden_resolver)
    monkeypatch.setattr(Path, "stat", tracking_stat)
    secret = "/mnt/user-data/workspace/private.txt"

    summary = build_thread_artifact_summary("thread-1", [f"{OUTPUTS_PREFIX}ok.txt", secret])

    assert summary.delivery_state == "unknown"
    assert summary.files == []
    assert secret not in str(summary.model_dump())
    assert resolver_calls == []
    assert stat_calls == []


def test_traversal_or_resolver_rejection_prevents_all_file_reads(output_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write_output(output_root, "ok.txt")
    stat_calls: list[Path] = []
    original_stat = Path.stat

    def tracking_stat(self: Path, *args: object, **kwargs: object) -> os.stat_result:
        stat_calls.append(self)
        return original_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", tracking_stat)
    traversal = "/mnt/user-data/outputs/../private.txt"

    summary = build_thread_artifact_summary("thread-1", [f"{OUTPUTS_PREFIX}ok.txt", traversal])

    assert summary.delivery_state == "unknown"
    assert summary.files == []
    assert traversal not in str(summary.model_dump())
    assert stat_calls == []


def test_files_are_deduplicated_and_sorted_by_readability_mtime_then_path(output_root: Path) -> None:
    write_output(output_root, "b.txt", b"bb", 1_700_000_100)
    write_output(output_root, "a.txt", b"a", 1_700_000_100)
    write_output(output_root, "new.txt", b"newest", 1_700_000_200)

    summary = build_thread_artifact_summary(
        "thread-1",
        [
            f"{OUTPUTS_PREFIX}b.txt",
            f"{OUTPUTS_PREFIX}gone.txt",
            f"{OUTPUTS_PREFIX}a.txt",
            f"{OUTPUTS_PREFIX}new.txt",
            f"{OUTPUTS_PREFIX}b.txt",
        ],
    )

    assert [file.path for file in summary.files] == [
        f"{OUTPUTS_PREFIX}new.txt",
        f"{OUTPUTS_PREFIX}a.txt",
        f"{OUTPUTS_PREFIX}b.txt",
        f"{OUTPUTS_PREFIX}gone.txt",
    ]
    assert summary.files[1].name == "a.txt"
    assert summary.files[1].size == 1
    assert summary.files[1].modified_at == datetime.fromtimestamp(1_700_000_100, UTC)
    assert summary.files[1].modified_at.tzinfo == UTC
    assert summary.files[-1].size is None
    assert summary.files[-1].modified_at is None


@pytest.mark.parametrize(
    ("filename", "expected"),
    [
        ("movie.MP4", "video"),
        ("photo.webp", "image"),
        ("voice.m4a", "audio"),
        ("slides.pptx", "document"),
        ("notes.md", "document"),
        ("vector.svg", "other"),
        ("page.html", "other"),
        ("page.xhtml", "other"),
        ("README", "other"),
        ("archive.zip", "other"),
    ],
)
def test_media_kind_uses_only_the_safe_extension_mapping(output_root: Path, filename: str, expected: str) -> None:
    write_output(output_root, filename)

    summary = build_thread_artifact_summary("thread-1", [f"{OUTPUTS_PREFIX}{filename}"])

    assert summary.files[0].media_kind == expected


def test_one_stat_oserror_does_not_hide_other_files_or_leak_host_paths(output_root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    good = write_output(output_root, "good.txt", b"good")
    broken = output_root / "broken.txt"
    original_stat = Path.stat

    def flaky_stat(self: Path, *args: object, **kwargs: object) -> os.stat_result:
        if self == broken:
            raise OSError(f"cannot inspect {broken}")
        return original_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", flaky_stat)

    summary = build_thread_artifact_summary(
        "thread-1",
        [f"{OUTPUTS_PREFIX}broken.txt", f"{OUTPUTS_PREFIX}good.txt"],
    )

    assert summary.delivery_state == "partial"
    assert [(file.name, file.exists) for file in summary.files] == [("good.txt", True), ("broken.txt", False)]
    assert summary.files[0].size == good.stat().st_size
    assert summary.files[1].size is None
    assert summary.files[1].modified_at is None
    assert str(broken) not in str(summary.model_dump())


@pytest.mark.asyncio
async def test_checkpoint_missing_is_unknown(output_root: Path) -> None:
    checkpointer = SimpleNamespace(aget_tuple=AsyncMock(return_value=None))

    summary = await load_thread_artifact_summary("thread-1", checkpointer)

    assert summary.delivery_state == "unknown"
    checkpointer.aget_tuple.assert_awaited_once_with({"configurable": {"thread_id": "thread-1", "checkpoint_ns": ""}})


@pytest.mark.asyncio
async def test_checkpoint_artifacts_are_projected_without_returning_messages(output_root: Path) -> None:
    write_output(output_root, "deck.pptx", b"deck")
    checkpoint_tuple = SimpleNamespace(
        checkpoint={
            "channel_values": {
                "artifacts": [f"{OUTPUTS_PREFIX}deck.pptx"],
                "messages": ["private message"],
            }
        }
    )
    checkpointer = SimpleNamespace(aget_tuple=AsyncMock(return_value=checkpoint_tuple))

    summary = await load_thread_artifact_summary("thread-1", checkpointer)

    assert summary.delivery_state == "delivered"
    assert summary.files[0].name == "deck.pptx"
    assert "private message" not in str(summary.model_dump())


def test_real_resolver_rejection_does_not_log_path_or_control_characters(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    import logging

    import app.gateway.path_utils as path_utils

    secret = "sensitive-name"
    raw_path = f"/mnt/user-data/outputs/../{secret}\nforged-line"

    class RejectingPaths:
        def resolve_virtual_path(self, *_args: object, **_kwargs: object) -> Path:
            raise ValueError(f"traversal rejected: {raw_path}")

    monkeypatch.setattr(path_utils, "get_paths", lambda: RejectingPaths())
    with caplog.at_level(logging.WARNING):
        summary = build_thread_artifact_summary("thread\nforged-thread", [raw_path])

    assert summary.delivery_state == "unknown"
    assert secret not in caplog.text
    assert "forged-line" not in caplog.text
    assert "thread\nforged-thread" not in caplog.text
    assert "threadforged-thread" in caplog.text
