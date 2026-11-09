"""Upload router for handling file uploads."""

import asyncio
import logging
import os
import shutil
import stat
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from pydantic import BaseModel

from app.gateway.authz import require_permission
from app.gateway.deps import get_config
from deerflow.config.app_config import AppConfig
from deerflow.config.paths import get_paths
from deerflow.runtime.user_context import get_effective_user_id
from deerflow.sandbox.sandbox_provider import SandboxProvider, get_sandbox_provider
from deerflow.uploads.manager import (
    UPLOAD_STAGING_PREFIX,
    UPLOAD_STAGING_SUFFIX,
    PathTraversalError,
    delete_file_safe,
    enrich_file_listing,
    ensure_uploads_dir,
    get_uploads_dir,
    list_files_in_dir,
    normalize_filename,
    upload_artifact_url,
    upload_virtual_path,
)
from deerflow.utils.file_conversion import CONVERTIBLE_EXTENSIONS, convert_file_to_markdown

logger = logging.getLogger(__name__)

# ── Default upload limits ──────────────────────────────────────────────────

_DEFAULT_MAX_FILE_SIZE_BYTES = 2 * 1024**3  # 2 GiB
_DEFAULT_MAX_THREAD_TOTAL_BYTES = 10 * 1024**3  # 10 GiB
_DEFAULT_MAX_CONVERT_SIZE_BYTES = 50 * 1024**2  # 50 MiB

# R2-1 (2026-10-07, D2 拍板): thread uploads gain an extension allowlist.
# Before this, any file type entered the uploads tree (only filename
# normalization + size quotas applied) — exe/sh/srt/... all landed on disk and
# mounted into sandboxes. Default scope per D2: images / audio / video / common
# documents / zip / html. ttf/otf/srt/vtt/heic are intentionally NOT allowed by
# default — override via `uploads.allowed_extensions` in config.yaml if a
# workflow needs them (准入白名单与 CONVERTIBLE_EXTENSIONS 是两套：前者管
# 「能否进」，后者管「进后是否转 markdown」，准入 ⊇ 可转换).
_DEFAULT_ALLOWED_EXTENSIONS = frozenset(
    {
        # images
        "jpg",
        "jpeg",
        "png",
        "webp",
        "gif",
        # video
        "mp4",
        "mov",
        "webm",
        # audio
        "mp3",
        "wav",
        "m4a",
        # documents
        "doc",
        "docx",
        "pdf",
        "txt",
        "md",
        "csv",
        "xlsx",
        "pptx",
        "html",
        # archives
        "zip",
    }
)

router = APIRouter(prefix="/api/threads/{thread_id}/uploads", tags=["uploads"])


class UploadResponse(BaseModel):
    """Response model for file upload."""

    success: bool
    files: list[dict[str, str]]
    message: str
    skipped_files: list[str] = []


def _make_file_sandbox_writable(file_path: os.PathLike[str] | str) -> None:
    """Ensure uploaded files remain writable when mounted into non-local sandboxes.

    In AIO sandbox mode, the gateway writes the authoritative host-side file
    first, then the sandbox runtime may rewrite the same mounted path. Granting
    world-writable access here prevents permission mismatches between the
    gateway user and the sandbox runtime user.
    """
    file_stat = os.lstat(file_path)
    if stat.S_ISLNK(file_stat.st_mode):
        logger.warning("Skipping sandbox chmod for symlinked upload path: %s", file_path)
        return

    writable_mode = stat.S_IMODE(file_stat.st_mode) | stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH | stat.S_IRGRP | stat.S_IROTH
    chmod_kwargs = {"follow_symlinks": False} if os.chmod in os.supports_follow_symlinks else {}
    os.chmod(file_path, writable_mode, **chmod_kwargs)


def _make_file_sandbox_readable(file_path: os.PathLike[str] | str) -> None:
    """Ensure uploaded files are readable by the sandbox process.

    For Docker sandboxes (AIO), the gateway writes files as root with 0o600
    permissions, then bind-mounts the host directory into the container. The
    sandbox process inside the container runs as a non-root user and cannot
    read those files without group/other read bits. This function adds
    ``S_IRGRP | S_IROTH`` so the sandbox can read the uploaded content.
    """
    file_stat = os.lstat(file_path)
    if stat.S_ISLNK(file_stat.st_mode):
        logger.warning("Skipping sandbox chmod for symlinked upload path: %s", file_path)
        return

    readable_mode = stat.S_IMODE(file_stat.st_mode) | stat.S_IRGRP | stat.S_IROTH
    chmod_kwargs = {"follow_symlinks": False} if os.chmod in os.supports_follow_symlinks else {}
    os.chmod(file_path, readable_mode, **chmod_kwargs)


def _uses_thread_data_mounts(sandbox_provider: SandboxProvider) -> bool:
    return bool(getattr(sandbox_provider, "uses_thread_data_mounts", False))


def _get_uploads_config_value(app_config: AppConfig, key: str, default: object) -> object:
    """Read a value from the uploads config, supporting dict and attribute access."""
    uploads_cfg = getattr(app_config, "uploads", None)
    if isinstance(uploads_cfg, dict):
        return uploads_cfg.get(key, default)
    return getattr(uploads_cfg, key, default)


def _auto_convert_documents_enabled(app_config: AppConfig) -> bool:
    """Return whether automatic host-side document conversion is enabled.

    The secure default is disabled unless an operator explicitly opts in via
    uploads.auto_convert_documents in config.yaml.
    """
    try:
        raw = _get_uploads_config_value(app_config, "auto_convert_documents", False)
        if isinstance(raw, str):
            return raw.strip().lower() in {"1", "true", "yes", "on"}
        return bool(raw)
    except Exception:
        return False


def _get_max_file_size(app_config: AppConfig) -> int:
    """Return the per-file upload size limit in bytes."""
    try:
        raw = _get_uploads_config_value(app_config, "max_file_size_bytes", _DEFAULT_MAX_FILE_SIZE_BYTES)
        if isinstance(raw, (int, float)):
            return max(1, int(raw))
        return _DEFAULT_MAX_FILE_SIZE_BYTES
    except Exception:
        return _DEFAULT_MAX_FILE_SIZE_BYTES


def _get_max_thread_total(app_config: AppConfig) -> int:
    """Return the total upload quota per thread in bytes."""
    try:
        raw = _get_uploads_config_value(app_config, "max_thread_total_bytes", _DEFAULT_MAX_THREAD_TOTAL_BYTES)
        if isinstance(raw, (int, float)):
            return max(1, int(raw))
        return _DEFAULT_MAX_THREAD_TOTAL_BYTES
    except Exception:
        return _DEFAULT_MAX_THREAD_TOTAL_BYTES


def _get_max_convert_size(app_config: AppConfig) -> int:
    """Return the size threshold above which auto document conversion is skipped."""
    try:
        raw = _get_uploads_config_value(app_config, "max_convert_size_bytes", _DEFAULT_MAX_CONVERT_SIZE_BYTES)
        if isinstance(raw, (int, float)):
            return max(0, int(raw))
        return _DEFAULT_MAX_CONVERT_SIZE_BYTES
    except Exception:
        return _DEFAULT_MAX_CONVERT_SIZE_BYTES


def _get_allowed_extensions(app_config: AppConfig) -> frozenset[str]:
    """Return the extension allowlist for thread uploads (R2-1).

    ``uploads.allowed_extensions`` in config.yaml overrides the default (D2).
    An empty or malformed override falls back to the default rather than
    blocking every upload — blocking-all is never the operator's intent.
    """
    try:
        raw = _get_uploads_config_value(app_config, "allowed_extensions", None)
        if raw is None:
            return _DEFAULT_ALLOWED_EXTENSIONS
        if not isinstance(raw, (list, tuple, set)):
            # str 是唯一现实漏网（YAML 标量覆盖）：可迭代且不抛错，逐字符迭代
            # 得到 {'j','p','g',',','n'} 形态的非空垃圾集合——既不触发下方空
            # 回落也不进 except，全站上传 400 且无日志可查。按 D2「畸形即回落」
            # 保守处理，不猜逗号分隔语义。
            logger.warning(
                "uploads.allowed_extensions has malformed type %s (expected a list); falling back to the default allowlist",
                type(raw).__name__,
            )
            return _DEFAULT_ALLOWED_EXTENSIONS
        exts = {str(ext).strip().lstrip(".").lower() for ext in raw if str(ext).strip()}
        if not exts:
            logger.warning("uploads.allowed_extensions is empty; falling back to the default allowlist")
            return _DEFAULT_ALLOWED_EXTENSIONS
        return frozenset(exts)
    except Exception:
        return _DEFAULT_ALLOWED_EXTENSIONS


def _compute_uploads_dir_size(uploads_dir: os.PathLike) -> int:
    """Sum file sizes in *uploads_dir* (non-recursive)."""
    total = 0
    try:
        with os.scandir(uploads_dir) as entries:
            for entry in entries:
                if entry.is_file(follow_symlinks=False):
                    total += entry.stat(follow_symlinks=False).st_size
    except FileNotFoundError:
        return 0
    return total


def _cleanup_file(file_path: os.PathLike) -> None:
    """Remove *file_path* if it exists, logging any failure."""
    try:
        if os.path.isfile(file_path):
            os.unlink(file_path)
    except OSError:
        logger.warning("Failed to clean up partial upload: %s", file_path, exc_info=True)


# Private directory prefix for conversion copies of staged upload bytes: those
# copies live outside the sandbox-writable uploads tree (upstream #5611).
_CONVERT_TEMP_PREFIX = "-trainerai-convert-"


def _close_fd(fd: int | None) -> None:
    """Close a conversion-source descriptor, tolerating an already-closed one."""
    if fd is None:
        return
    try:
        os.close(fd)
    except OSError:
        logger.warning("Failed to close upload conversion descriptor", exc_info=True)


def _copy_fd_to_path(fd: int, dest: Path) -> None:
    """Copy the bytes behind *fd* to *dest*, then close *fd*.

    Reads through the descriptor, not the name the upload was committed under,
    so the copy holds the content this request wrote even if that name has
    since been swapped for a symlink (upstream #5611). The descriptor is
    closed even when the copy fails — the caller has handed over ownership
    and must not touch it again (review follow-up terminal state).
    """
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        with open(dest, "wb") as out:
            while chunk := os.read(fd, 1 << 20):
                out.write(chunk)
    finally:
        _close_fd(fd)


def _sync_to_sandbox_streaming(
    sandbox,
    virtual_path: str,
    file_path: os.PathLike,
) -> None:
    """Invoke :meth:`sandbox.upload_file_stream` (synchronous I/O wrapper)."""
    sandbox.upload_file_stream(virtual_path, str(file_path))


@router.post("", response_model=UploadResponse)
@require_permission("threads", "write", owner_check=True, require_existing=False)
async def upload_files(
    thread_id: str,
    request: Request,
    files: list[UploadFile] = File(...),
    config: AppConfig = Depends(get_config),
) -> UploadResponse:
    """Upload multiple files to a thread's uploads directory."""
    if not files:
        raise HTTPException(status_code=400, detail="No files provided")

    try:
        uploads_dir = ensure_uploads_dir(thread_id)
    except ValueError as e:
        logger.warning("SEC-M12: uploads dir rejected (thread=%s): %s", thread_id, e)
        raise HTTPException(status_code=400, detail="Invalid thread id")

    max_file_size = _get_max_file_size(config)
    max_thread_total = _get_max_thread_total(config)
    max_convert_size = _get_max_convert_size(config)
    allowed_extensions = _get_allowed_extensions(config)
    sandbox_uploads = get_paths().sandbox_uploads_dir(thread_id, user_id=get_effective_user_id())
    uploaded_files = []
    skipped_files: list[str] = []

    sandbox_provider = get_sandbox_provider()
    sync_to_sandbox = not _uses_thread_data_mounts(sandbox_provider)
    sandbox = None
    if sync_to_sandbox:
        sandbox_id = sandbox_provider.acquire(thread_id)
        sandbox = sandbox_provider.get(sandbox_id)
    auto_convert_documents = _auto_convert_documents_enabled(config)

    for file in files:
        if not file.filename:
            continue

        try:
            safe_filename = normalize_filename(file.filename)
        except ValueError:
            logger.warning("Skipping file with unsafe filename: %r", file.filename)
            skipped_files.append(file.filename)
            continue

        # ── Extension allowlist (R2-1): admission control, before any byte
        # is staged. Rejected types 400 the whole request — same semantics as
        # the quota 413s below (partial uploads from earlier loop iterations
        # stay; the client shows the error).
        file_ext = Path(safe_filename).suffix.lstrip(".").lower()
        if file_ext not in allowed_extensions:
            logger.warning("Rejected upload with disallowed extension: %r (thread=%s)", file.filename, thread_id)
            raise HTTPException(
                status_code=400,
                detail=(f"不支持的文件类型：{safe_filename}（支持：{'/'.join(sorted(allowed_extensions))}；如需扩展可在 config.yaml 的 uploads.allowed_extensions 调整）"),
            )

        file_path = uploads_dir / safe_filename
        # Conversion-source scratch for this file (upstream #5611): a
        # descriptor on the staged inode, then a private copy outside the
        # uploads tree once the name is committed. Both are released in the
        # finally below on every path.
        convert_source_fd: int | None = None
        private_convert_dir: Path | None = None

        try:
            # ── Thread-level quota check (before writing) ──────────────
            existing_total = _compute_uploads_dir_size(uploads_dir)
            if existing_total >= max_thread_total:
                raise HTTPException(
                    status_code=413,
                    detail="Uploads directory has reached its total size limit for this thread.",
                )

            # ── Stream upload to disk with per-file size guard ─────────
            # Write to a UNIQUE staging file (mkstemp), then atomically
            # rename on success. Unique staging names keep concurrent
            # same-name uploads from interleaving into one file (R-10,
            # 2026-08-13); the ``.upload-*.part`` namespace lets the startup
            # sweeper tell transient staging from user files legitimately
            # named ``*.tmp`` (R-09). This also protects the previous
            # version of an overwritten file from being truncated and then
            # deleted if the upload fails partway through.
            tmp_fd, tmp_path_str = tempfile.mkstemp(prefix=UPLOAD_STAGING_PREFIX, suffix=UPLOAD_STAGING_SUFFIX, dir=uploads_dir)
            tmp_path = Path(tmp_path_str)
            total_size = 0
            try:
                dst = os.fdopen(tmp_fd, "wb")
            except Exception:
                try:
                    os.close(tmp_fd)
                except OSError:
                    pass
                _cleanup_file(tmp_path)
                raise
            try:
                with dst:
                    while chunk := await file.read(1024 * 1024):
                        dst.write(chunk)
                        total_size += len(chunk)
                        if total_size > max_file_size:
                            # Abort — clean up temp file, old file untouched
                            raise HTTPException(
                                status_code=413,
                                detail=f"File exceeds maximum upload size ({max_file_size} bytes).",
                            )
                    if auto_convert_documents and file_path.suffix.lower() in CONVERTIBLE_EXTENSIONS and total_size <= max_convert_size:
                        # Conversion must read the bytes this request staged
                        # (upstream #5611): once the name is committed, a
                        # sandbox process can swap it for a symlink to a host
                        # file, and converting by name would pull that host
                        # file into this thread's uploads. A descriptor on the
                        # staged inode cannot be redirected by replacing a
                        # name. Duplicated here while the staging handle is
                        # still open, before the commit below.
                        convert_source_fd = os.dup(tmp_fd)
            except HTTPException:
                _cleanup_file(tmp_path)
                raise
            except Exception:
                _cleanup_file(tmp_path)
                raise

            # ── Thread-level quota check (after writing) ───────────────
            if existing_total + total_size > max_thread_total:
                _cleanup_file(tmp_path)
                raise HTTPException(
                    status_code=413,
                    detail="Uploads directory has reached its total size limit for this thread.",
                )

            os.replace(tmp_path, file_path)

            # Copy the staged bytes (through the duplicated descriptor) into a
            # private directory outside the uploads tree and convert there.
            # Nothing is awaited between the duplication above and the copy
            # completing here, so the descriptor is always handed over or
            # closed by this point — the copy itself owns it and closes it
            # even when it fails.
            if convert_source_fd is not None:
                private_convert_dir = Path(tempfile.mkdtemp(prefix=_CONVERT_TEMP_PREFIX))
                staged_fd, convert_source_fd = convert_source_fd, None
                _copy_fd_to_path(staged_fd, private_convert_dir / safe_filename)

            # Ensure uploaded files are readable by the sandbox process.
            _make_file_sandbox_readable(file_path)

            virtual_path = upload_virtual_path(safe_filename)

            # ── Sandbox sync (streaming — never buffers full file) ─────
            if sync_to_sandbox and sandbox is not None:
                _make_file_sandbox_writable(file_path)
                await asyncio.to_thread(_sync_to_sandbox_streaming, sandbox, virtual_path, file_path)

            file_info = {
                "filename": safe_filename,
                "size": str(total_size),
                "path": str(sandbox_uploads / safe_filename),
                "virtual_path": virtual_path,
                "artifact_url": upload_artifact_url(thread_id, safe_filename),
            }

            logger.info("Saved file: %s (%d bytes) to %s", safe_filename, total_size, file_info["path"])

            # ── Auto document conversion with size guard ───────────────
            # Converts the private copy of the staged bytes, never the
            # committed name: the uploads dir is sandbox-writable and that
            # name may by now be a symlink (upstream #5611). The companion
            # .md is written back into the uploads dir by the converter
            # through write_upload_file_no_symlink (no-follow).
            if private_convert_dir is not None:
                md_path = await convert_file_to_markdown(private_convert_dir / safe_filename, output_path=file_path.with_suffix(".md"))
                if md_path:
                    md_virtual_path = upload_virtual_path(md_path.name)

                    if sync_to_sandbox and sandbox is not None:
                        _make_file_sandbox_writable(md_path)
                        await asyncio.to_thread(_sync_to_sandbox_streaming, sandbox, md_virtual_path, md_path)

                    file_info["markdown_file"] = md_path.name
                    file_info["markdown_path"] = str(sandbox_uploads / md_path.name)
                    file_info["markdown_virtual_path"] = md_virtual_path
                    file_info["markdown_artifact_url"] = upload_artifact_url(thread_id, md_path.name)

            uploaded_files.append(file_info)

        except HTTPException:
            raise
        except Exception as e:
            logger.error("Failed to upload %s: %s", file.filename, e)
            _cleanup_file(file_path)
            raise HTTPException(status_code=500, detail=f"Failed to upload {file.filename}")
        finally:
            # Per-request conversion scratch: the descriptor closes (unless
            # the copy already took ownership of it) and the private copy is
            # removed, on every exit path — success, failure, cancellation.
            _close_fd(convert_source_fd)
            if private_convert_dir is not None:
                shutil.rmtree(private_convert_dir, ignore_errors=True)

    # Mirrors the DeerFlowClient.upload_files message contract for skipped files.
    message = f"Successfully uploaded {len(uploaded_files)} file(s)"
    if skipped_files:
        message += f"; skipped {len(skipped_files)} unsafe file(s)"

    return UploadResponse(
        success=True,
        files=uploaded_files,
        message=message,
        skipped_files=skipped_files,
    )


@router.get("/list", response_model=dict)
@require_permission("threads", "read", owner_check=True)
async def list_uploaded_files(thread_id: str, request: Request) -> dict:
    """List all files in a thread's uploads directory."""
    try:
        uploads_dir = get_uploads_dir(thread_id)
    except ValueError as e:
        logger.warning("SEC-M12: uploads dir rejected (thread=%s): %s", thread_id, e)
        raise HTTPException(status_code=400, detail="Invalid thread id")
    result = list_files_in_dir(uploads_dir)
    enrich_file_listing(result, thread_id)

    # Gateway additionally includes the sandbox-relative path.
    sandbox_uploads = get_paths().sandbox_uploads_dir(thread_id, user_id=get_effective_user_id())
    for f in result["files"]:
        f["path"] = str(sandbox_uploads / f["filename"])

    return result


@router.delete("/{filename}")
@require_permission("threads", "delete", owner_check=True, require_existing=True)
async def delete_uploaded_file(thread_id: str, filename: str, request: Request) -> dict:
    """Delete a file from a thread's uploads directory."""
    try:
        uploads_dir = get_uploads_dir(thread_id)
    except ValueError as e:
        logger.warning("SEC-M12: uploads dir rejected (thread=%s): %s", thread_id, e)
        raise HTTPException(status_code=400, detail="Invalid thread id")
    try:
        return delete_file_safe(uploads_dir, filename)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail=f"File not found: {filename}")
    except PathTraversalError:
        raise HTTPException(status_code=400, detail="Invalid path")
    except Exception as e:
        logger.error(f"Failed to delete {filename}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to delete {filename}")
