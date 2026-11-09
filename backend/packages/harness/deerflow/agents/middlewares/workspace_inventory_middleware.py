"""Middleware to inject a workspace/outputs file inventory into agent context.

After a run error or context compression the agent has no signal about which
artifacts already exist in the thread workspace, so it re-does finished work
(re-probing media, re-parsing documents, re-running TTS). This middleware
prepends a compact <workspace_files> block to the last human message, listing
what is already on disk so the agent can reuse instead of redo.

Only ``before_agent`` is implemented (runs once per agent invocation, so it
adds no per-round graph node). Mirrors UploadsMiddleware's prepend mechanics.
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import override

from langchain.agents import AgentState
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import HumanMessage
from langgraph.runtime import Runtime

from deerflow.config.paths import Paths, get_paths
from deerflow.runtime.user_context import get_effective_user_id

logger = logging.getLogger(__name__)

_MAX_ENTRIES = 50
_MAX_CHARS = 4000
_WORKSPACE_MAX_DEPTH = 2
_OUTPUTS_MAX_DEPTH = 1
_KNOWN_MANIFESTS = ("sections_manifest.json", "clips_manifest.json")
_GUIDANCE_LINE = "续作时先盘点以上已有产物，能复用就复用，不要重做已完成的步骤。"


def _format_size(size: int) -> str:
    size_kb = size / 1024
    return f"{size_kb:.1f} KB" if size_kb < 1024 else f"{size_kb / 1024:.1f} MB"


class _ListingBudget:
    """Shared entry/char budget across the workspace and outputs listings."""

    def __init__(self, max_entries: int = _MAX_ENTRIES, max_chars: int = _MAX_CHARS) -> None:
        self._max_entries = max_entries
        self._max_chars = max_chars
        self.entries = 0
        self.chars = 0
        self.truncated = False

    def try_add(self, line: str) -> bool:
        """Account for one listing line; return False when the budget is spent."""
        if self.entries >= self._max_entries or self.chars + len(line) + 1 > self._max_chars:
            self.truncated = True
            return False
        self.entries += 1
        self.chars += len(line) + 1
        return True


def _walk_dir(root: Path, *, max_depth: int, budget: _ListingBudget, lines: list[str], depth: int = 1) -> None:
    """Append a tree listing of *root* to *lines*, bounded by depth and budget.

    Directories render with a trailing slash and are recursed into while
    ``depth < max_depth``. Hidden (dot) entries are skipped. Individual
    unreadable entries are skipped without failing the whole listing.
    """
    try:
        children = sorted(p for p in root.iterdir() if not p.name.startswith("."))
    except OSError:
        logger.debug("Cannot list directory %s", root, exc_info=True)
        return
    indent = "  " * (depth - 1)
    for child in children:
        if budget.truncated:
            return
        try:
            if child.is_dir():
                line = f"{indent}- {child.name}/"
                if not budget.try_add(line):
                    return
                lines.append(line)
                if depth < max_depth:
                    _walk_dir(child, max_depth=max_depth, budget=budget, lines=lines, depth=depth + 1)
            else:
                size = child.stat().st_size
                line = f"{indent}- {child.name} ({_format_size(size)})"
                if not budget.try_add(line):
                    return
                lines.append(line)
        except OSError:
            logger.debug("Cannot stat %s", child, exc_info=True)


def _manifest_lines(workspace_dir: Path, budget: _ListingBudget) -> list[str]:
    """Detect manifest files in the workspace root (known names + *_manifest.json glob).

    Lines are accounted against the shared *budget* like the listings, so the
    block's 50-entry/4000-char promise holds for arbitrarily many manifests.
    """
    candidates = [workspace_dir / name for name in _KNOWN_MANIFESTS]
    try:
        candidates.extend(sorted(workspace_dir.glob("*_manifest.json")))
    except OSError:
        logger.debug("Cannot glob manifests in %s", workspace_dir, exc_info=True)
    lines: list[str] = []
    seen: set[str] = set()
    for path in candidates:
        if path.name in seen:
            continue
        seen.add(path.name)
        try:
            if not path.is_file():
                continue
            stat = path.stat()
        except OSError:
            logger.debug("Cannot stat manifest %s", path, exc_info=True)
            continue
        mtime = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        line = f"Manifest: {path.name} ({_format_size(stat.st_size)}, mtime {mtime})"
        if not budget.try_add(line):
            break
        lines.append(line)
    return lines


class WorkspaceInventoryMiddleware(AgentMiddleware[AgentState]):
    """Inject a <workspace_files> inventory of the thread's workspace/outputs dirs.

    Runs once per agent invocation via ``before_agent`` and prepends the block
    to the last human message, so the agent knows which artifacts already
    exist and can reuse them instead of redoing finished steps. Injects
    nothing when both directories are empty or missing, and never raises.
    """

    def __init__(self, base_dir: str | None = None):
        """Initialize the middleware.

        Args:
            base_dir: Base directory for thread data. Defaults to Paths resolution.
        """
        super().__init__()
        self._paths = Paths(base_dir) if base_dir else get_paths()

    def _create_inventory_message(self, workspace_dir: Path, outputs_dir: Path) -> str | None:
        """Build the <workspace_files> block, or None when there is nothing to report."""
        budget = _ListingBudget()
        workspace_lines: list[str] = []
        if workspace_dir.is_dir():
            _walk_dir(workspace_dir, max_depth=_WORKSPACE_MAX_DEPTH, budget=budget, lines=workspace_lines)
        outputs_lines: list[str] = []
        if not budget.truncated and outputs_dir.is_dir():
            _walk_dir(outputs_dir, max_depth=_OUTPUTS_MAX_DEPTH, budget=budget, lines=outputs_lines)

        if not workspace_lines and not outputs_lines:
            return None

        lines = ["<workspace_files>"]
        if workspace_lines:
            lines.append("Workspace (/mnt/user-data/workspace/):")
            lines.extend(workspace_lines)
        if outputs_lines:
            lines.append("Outputs (/mnt/user-data/outputs/):")
            lines.extend(outputs_lines)
        if workspace_dir.is_dir():
            lines.extend(_manifest_lines(workspace_dir, budget))
        if budget.truncated:
            lines.append("... (truncated)")
        lines.append(_GUIDANCE_LINE)
        lines.append("</workspace_files>")
        return "\n".join(lines)

    @override
    def before_agent(self, state: AgentState, runtime: Runtime) -> dict | None:
        """Inject the workspace inventory before agent execution.

        Resolves the thread's physical workspace/outputs directories the same
        way UploadsMiddleware resolves its uploads dir (runtime context
        thread_id, falling back to langgraph config), then prepends the
        <workspace_files> block to the last human message content.

        Args:
            state: Current agent state.
            runtime: Runtime context containing thread_id.

        Returns:
            State update with the modified messages, or None to inject nothing.
        """
        try:
            messages = list(state.get("messages", []))
            if not messages:
                return None

            last_message_index = len(messages) - 1
            last_message = messages[last_message_index]
            if not isinstance(last_message, HumanMessage):
                return None

            thread_id = (runtime.context or {}).get("thread_id")
            if thread_id is None:
                try:
                    from langgraph.config import get_config

                    thread_id = get_config().get("configurable", {}).get("thread_id")
                except RuntimeError:
                    pass  # get_config() raises outside a runnable context (e.g. unit tests)
            if not thread_id:
                return None

            user_id = get_effective_user_id()
            workspace_dir = self._paths.sandbox_work_dir(thread_id, user_id=user_id)
            outputs_dir = self._paths.sandbox_outputs_dir(thread_id, user_id=user_id)

            inventory_message = self._create_inventory_message(workspace_dir, outputs_dir)
            if inventory_message is None:
                return None

            logger.debug("Injecting workspace inventory for thread %s (%d chars)", thread_id, len(inventory_message))

            # Prepend to the last human message content (same mechanics as
            # UploadsMiddleware): string content gets a text prefix, list
            # content gets a leading text block, everything else is untouched.
            original_content = last_message.content
            if isinstance(original_content, str):
                updated_content = f"{inventory_message}\n\n{original_content}"
            elif isinstance(original_content, list):
                updated_content = [{"type": "text", "text": f"{inventory_message}\n\n"}, *original_content]
            else:
                updated_content = original_content

            # model_copy preserves every other field (response_metadata, name,
            # additional_kwargs, ...) — same fix style as the uploads middleware.
            updated_message = last_message.model_copy(update={"content": updated_content})
            messages[last_message_index] = updated_message
            return {"messages": messages}
        except Exception:
            logger.debug("WorkspaceInventoryMiddleware failed; injecting nothing", exc_info=True)
            return None
