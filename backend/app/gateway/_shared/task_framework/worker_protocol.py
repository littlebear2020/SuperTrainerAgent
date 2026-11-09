"""Worker-side constants and protocol definitions.

This module defines the contract between Gateway and Worker without
importing any ORM or app code. Worker container never installs the app package;
it stays in sync through shared SQL field names and status strings.
"""

from __future__ import annotations

# ── Worker claim contract ────────────────────────────────────────
# Worker only claims tasks where status = COMPOSING and claimed_by IS NULL.
COMPOSING = "composing"

# ── Worker pipeline signature ────────────────────────────────────
# PipelineFn = Callable[[Path, dict, dict, Callable[[int], None]], Path]
# task_dir, payload, options, progress_cb → output Path
# Defined in docker/production-worker/pipelines/registry.py
