"""Shared path resolution for thread virtual paths (e.g. mnt/user-data/outputs/...)."""

import logging
from pathlib import Path

from fastapi import HTTPException

from app.gateway.utils import sanitize_log_param
from deerflow.config.paths import get_paths
from deerflow.runtime.user_context import get_effective_user_id

logger = logging.getLogger(__name__)


def resolve_thread_virtual_path(thread_id: str, virtual_path: str) -> Path:
    """Resolve a virtual path to the actual filesystem path under thread user-data.

    Args:
        thread_id: The thread ID.
        virtual_path: The virtual path as seen inside the sandbox
                      (e.g., /mnt/user-data/outputs/file.txt).

    Returns:
        The resolved filesystem path.

    Raises:
        HTTPException: If the path is invalid or outside allowed directories.
    """
    try:
        return get_paths().resolve_virtual_path(thread_id, virtual_path, user_id=get_effective_user_id())
    except ValueError as e:
        # SEC-M12（批次 2 项 2.10）：异常与路径原文可能携带敏感名称或控制字符，
        # 通用文案 + 仅脱敏线程/原因类别日志。403/400 分流语义保留。
        status = 403 if "traversal" in str(e) else 400
        reason = "traversal" if status == 403 else "invalid"
        logger.warning("SEC-M12: virtual path rejected (thread=%s reason=%s)", sanitize_log_param(thread_id), reason)
        raise HTTPException(status_code=status, detail="Invalid or disallowed path")
