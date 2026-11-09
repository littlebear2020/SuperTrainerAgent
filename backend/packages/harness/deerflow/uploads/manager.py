"""Shared upload management logic.

Pure business logic — no FastAPI/HTTP dependencies.
Both Gateway and Client delegate to these functions.
"""

import errno
import logging
import os
import re
import shutil
import stat
from pathlib import Path
from urllib.parse import quote

from deerflow.config.paths import VIRTUAL_PATH_PREFIX, get_paths
from deerflow.runtime.user_context import get_effective_user_id

logger = logging.getLogger(__name__)


class PathTraversalError(ValueError):
    """Raised when a path escapes its allowed base directory."""


class UnsafeUploadPathError(ValueError):
    """Raised when an upload destination is not a safe regular file path."""


# Staging-file namespace used by the uploads router (app/gateway/routers/
# uploads.py): mkstemp'd ``.upload-*.part`` files are transient by
# construction, unlike ``*.tmp`` which may be a user's real file (R-09,
# 2026-08-13).
UPLOAD_STAGING_PREFIX = ".upload-"
UPLOAD_STAGING_SUFFIX = ".part"


def is_upload_staging_file(filename: str) -> bool:
    """Return whether *filename* is a transient Gateway upload staging file.

    The uploads router streams each upload to a mkstemp'd ``.upload-*.part``
    file and atomically renames it on success (app/gateway/routers/uploads.py);
    a hard crash mid-stream leaves the ``.part`` orphan behind. The dual-affix
    check is deliberate: a bare ``.tmp`` suffix is not staging evidence — a
    user may legitimately upload a file named ``notes.tmp`` (R-09).
    """
    return filename.startswith(UPLOAD_STAGING_PREFIX) and filename.endswith(UPLOAD_STAGING_SUFFIX)


def _iter_upload_dirs(base_dir: Path):
    yield from base_dir.glob("threads/*/user-data/uploads")
    yield from base_dir.glob("users/*/threads/*/user-data/uploads")


def cleanup_stale_upload_staging_files(base_dir: Path | str | None = None) -> int:
    """Remove orphaned Gateway upload staging files left by a hard crash.

    Scans every thread uploads directory under *base_dir* (both the legacy
    ``threads/*`` layout and the per-user ``users/*/threads/*`` layout) and
    unlinks ``*.tmp`` staging files that never got renamed into place.
    Regular files are never touched. Returns the number of files removed.
    """
    root = Path(base_dir) if base_dir is not None else get_paths().base_dir
    removed = 0
    for uploads_dir in _iter_upload_dirs(root):
        if not uploads_dir.is_dir():
            continue
        try:
            with os.scandir(uploads_dir) as entries:
                for entry in entries:
                    if not is_upload_staging_file(entry.name) or not entry.is_file(follow_symlinks=False):
                        continue
                    try:
                        os.unlink(entry.path)
                        removed += 1
                    except FileNotFoundError:
                        pass
                    except OSError:
                        logger.warning("Failed to remove stale upload staging file: %s", entry.path, exc_info=True)
        except FileNotFoundError:
            continue
        except OSError:
            logger.warning("Failed to scan uploads directory for stale staging files: %s", uploads_dir, exc_info=True)
    return removed


# thread_id must be alphanumeric, hyphens, underscores, or dots only.
_SAFE_THREAD_ID = re.compile(r"^[a-zA-Z0-9._-]+$")


def validate_thread_id(thread_id: str) -> None:
    """Reject thread IDs containing characters unsafe for filesystem paths.

    Raises:
        ValueError: If thread_id is empty or contains unsafe characters.
    """
    if not thread_id or not _SAFE_THREAD_ID.match(thread_id):
        raise ValueError(f"Invalid thread_id: {thread_id!r}")


def get_uploads_dir(thread_id: str) -> Path:
    """Return the uploads directory path for a thread (no side effects)."""
    validate_thread_id(thread_id)
    return get_paths().sandbox_uploads_dir(thread_id, user_id=get_effective_user_id())


def ensure_uploads_dir(thread_id: str) -> Path:
    """Return the uploads directory for a thread, creating it if needed."""
    base = get_uploads_dir(thread_id)
    base.mkdir(parents=True, exist_ok=True)
    return base


def normalize_filename(filename: str) -> str:
    """Sanitize a filename by extracting its basename.

    Strips any directory components and rejects traversal patterns.

    Args:
        filename: Raw filename from user input (may contain path components).

    Returns:
        Safe filename (basename only).

    Raises:
        ValueError: If filename is empty or resolves to a traversal pattern.
    """
    if not filename:
        raise ValueError("Filename is empty")
    safe = Path(filename).name
    if not safe or safe in {".", ".."}:
        raise ValueError(f"Filename is unsafe: {filename!r}")
    if "\x00" in safe:
        raise ValueError(f"Filename contains NUL: {filename!r}")
    # Reject backslashes — on Linux Path.name keeps them as literal chars,
    # but they indicate a Windows-style path that should be stripped or rejected.
    if "\\" in safe:
        raise ValueError(f"Filename contains backslash: {filename!r}")
    if len(safe.encode("utf-8")) > 255:
        raise ValueError(f"Filename too long: {len(safe)} chars")
    return safe


def claim_unique_filename(name: str, seen: set[str]) -> str:
    """Generate a unique filename by appending ``_N`` suffix on collision.

    Automatically adds the returned name to *seen* so callers don't need to.

    Args:
        name: Candidate filename.
        seen: Set of filenames already claimed (mutated in place).

    Returns:
        A filename not present in *seen* (already added to *seen*).
    """
    if name not in seen:
        seen.add(name)
        return name
    stem, suffix = Path(name).stem, Path(name).suffix
    counter = 1
    candidate = f"{stem}_{counter}{suffix}"
    while candidate in seen:
        counter += 1
        candidate = f"{stem}_{counter}{suffix}"
    seen.add(candidate)
    return candidate


def validate_path_traversal(path: Path, base: Path) -> None:
    """Verify that *path* is inside *base*.

    Raises:
        PathTraversalError: If a path traversal is detected.
    """
    try:
        path.resolve().relative_to(base.resolve())
    except ValueError:
        raise PathTraversalError("Path traversal detected") from None


def open_upload_file_no_symlink(base_dir: Path, filename: str) -> tuple[Path, object]:
    """Open an upload destination for safe streaming writes.

    Upload directories may be mounted into local sandboxes. A sandbox process can
    therefore leave a symlink at a future upload filename. Normal ``Path.write_bytes``
    follows that link and can overwrite files outside the uploads directory with
    gateway privileges. This helper rejects symlink destinations and uses
    ``O_NOFOLLOW`` where available so the final path component cannot be raced into
    a symlink between validation and open.
    """
    safe_name = normalize_filename(filename)
    dest = base_dir / safe_name

    try:
        st = os.lstat(dest)
    except FileNotFoundError:
        st = None

    if st is not None and not stat.S_ISREG(st.st_mode):
        raise UnsafeUploadPathError(f"Upload destination is not a regular file: {safe_name}")

    validate_path_traversal(dest, base_dir)

    if not hasattr(os, "O_NOFOLLOW"):
        raise UnsafeUploadPathError("Upload writes require O_NOFOLLOW support")

    flags = os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW
    if hasattr(os, "O_NONBLOCK"):
        flags |= os.O_NONBLOCK

    try:
        fd = os.open(dest, flags, 0o600)
    except OSError as exc:
        if exc.errno in {errno.ELOOP, errno.EISDIR, errno.ENOTDIR, errno.ENXIO, errno.EAGAIN}:
            raise UnsafeUploadPathError(f"Unsafe upload destination: {safe_name}") from exc
        raise

    try:
        opened_stat = os.fstat(fd)
        if not stat.S_ISREG(opened_stat.st_mode) or opened_stat.st_nlink != 1:
            raise UnsafeUploadPathError(f"Upload destination is not an exclusive regular file: {safe_name}")
        os.ftruncate(fd, 0)
        fh = os.fdopen(fd, "wb")
        fd = -1
    finally:
        if fd >= 0:
            os.close(fd)
    return dest, fh


def write_upload_file_no_symlink(base_dir: Path, filename: str, data: bytes) -> Path:
    """Write upload bytes without following a pre-existing destination symlink."""
    dest, fh = open_upload_file_no_symlink(base_dir, filename)
    with fh:
        fh.write(data)
    return dest


def _reject_same_file(base_dir: Path, filename: str, src: Path, src_stat: os.stat_result) -> None:
    """Raise :class:`shutil.SameFileError` when *filename* already is *src*.

    Compares identity with ``os.path.samestat`` — what ``copy2`` itself uses —
    rather than the path text, so a hardlink or a differently spelled path to
    the same file is caught too.
    ``lstat`` keeps a planted symlink from being resolved here; the open
    itself rejects that destination.
    """
    dest = base_dir / normalize_filename(filename)
    try:
        dest_stat = os.lstat(dest)
    except (FileNotFoundError, NotADirectoryError):
        return
    if os.path.samestat(src_stat, dest_stat):
        raise shutil.SameFileError(f"{src!r} and {dest!r} are the same file")


def copy_upload_file_no_symlink(base_dir: Path, filename: str, src: Path) -> Path:
    """Copy *src* into an upload destination without following a destination symlink.

    Matches ``shutil.copy2`` for content, permission bits and timestamps, but
    opens the destination through :func:`open_upload_file_no_symlink` and
    applies the metadata to that descriptor, never to the name. The source is
    opened first, so a missing source leaves an existing destination intact.
    Where descriptor-based ``chmod``/``utime`` are unavailable (Windows), the
    destination keeps its default mode and the copy time.

    Copying a file onto itself raises :class:`shutil.SameFileError` as
    ``copy2`` does, and does so before the destination is opened: opening it
    truncates, which would otherwise leave the caller copying an emptied file
    over itself. Re-uploading a file that already sits in the uploads
    directory takes exactly that path.
    """
    with open(src, "rb") as src_fh:
        src_stat = os.fstat(src_fh.fileno())
        _reject_same_file(base_dir, filename, src, src_stat)
        dest, fh = open_upload_file_no_symlink(base_dir, filename)
        with fh:
            shutil.copyfileobj(src_fh, fh)
            fh.flush()
            if os.chmod in os.supports_fd:
                os.chmod(fh.fileno(), stat.S_IMODE(src_stat.st_mode))
            if os.utime in os.supports_fd:
                os.utime(fh.fileno(), ns=(src_stat.st_atime_ns, src_stat.st_mtime_ns))
    return dest


def list_files_in_dir(directory: Path) -> dict:
    """List files (not directories) in *directory*.

    Args:
        directory: Directory to scan.

    Returns:
        Dict with "files" list (sorted by name) and "count".
        Each file entry has ``size`` as *int* (bytes).  Call
        :func:`enrich_file_listing` to stringify sizes and add
        virtual / artifact URLs.
    """
    if not directory.is_dir():
        return {"files": [], "count": 0}

    files = []
    with os.scandir(directory) as entries:
        for entry in sorted(entries, key=lambda e: e.name):
            if not entry.is_file(follow_symlinks=False):
                continue
            # Never expose in-flight upload staging files (R-09, 2026-08-13).
            if is_upload_staging_file(entry.name):
                continue
            st = entry.stat(follow_symlinks=False)
            files.append(
                {
                    "filename": entry.name,
                    "size": st.st_size,
                    "path": entry.path,
                    "extension": Path(entry.name).suffix,
                    "modified": st.st_mtime,
                }
            )
    return {"files": files, "count": len(files)}


def delete_file_safe(base_dir: Path, filename: str) -> dict:
    """Delete a file inside *base_dir* after path-traversal validation.

    Only the requested file is removed. A companion ``.md`` produced during
    upload conversion is deliberately left in place: it may belong to another
    same-stem document or to the user themselves, and it stays in the listing
    so it can be deleted separately.

    Args:
        base_dir: Directory containing the file.
        filename: Name of file to delete.

    Returns:
        Dict with success and message.

    Raises:
        FileNotFoundError: If the file does not exist or is a symlink.
        PathTraversalError: If path traversal is detected.
    """
    # Do not resolve(): unlink must not follow a symlinked final component.
    file_path = base_dir / filename
    validate_path_traversal(file_path, base_dir)

    if file_path.is_symlink() or not file_path.is_file():
        raise FileNotFoundError(f"File not found: {filename}")

    file_path.unlink()

    return {"success": True, "message": f"Deleted {filename}"}


def upload_artifact_url(thread_id: str, filename: str) -> str:
    """Build the artifact URL for a file in a thread's uploads directory.

    *filename* is percent-encoded so that spaces, ``#``, ``?`` etc. are safe.
    """
    return f"/api/threads/{thread_id}/artifacts{VIRTUAL_PATH_PREFIX}/uploads/{quote(filename, safe='')}"


def upload_virtual_path(filename: str) -> str:
    """Build the virtual path for a file in the uploads directory."""
    return f"{VIRTUAL_PATH_PREFIX}/uploads/{filename}"


def enrich_file_listing(result: dict, thread_id: str) -> dict:
    """Add virtual paths, artifact URLs, and stringify sizes on a listing result.

    Mutates *result* in place and returns it for convenience.
    """
    for f in result["files"]:
        filename = f["filename"]
        f["size"] = str(f["size"])
        f["virtual_path"] = upload_virtual_path(filename)
        f["artifact_url"] = upload_artifact_url(thread_id, filename)
    return result
