"""Adapter: wraps legacy compose_ppt_video into new PipelineFn signature."""

from __future__ import annotations

from pathlib import Path

from pipeline import compose_ppt_video as _legacy_compose


def compose_ppt_video(
    task_dir: Path,
    payload: dict,
    options: dict,
    progress_cb: object,
    *,
    lease_remaining: float | None = None,
) -> Path:
    """Adapt new PipelineFn signature to legacy compose_ppt_video.

    lease_remaining 暂不使用（legacy PPT 管线 xfade 自有用时核算）；与 registry
    统一签名，供 worker 统一透传。
    """
    # preset/subtitle are stored at payload top-level by the Gateway pipeline,
    # not nested under "options".
    preset = payload.get("preset", options.get("preset", "none"))
    subtitle = bool(payload.get("subtitle", options.get("subtitle", False)))

    def _wrap_progress(pct: int, msg: str = "") -> None:
        progress_cb(pct)  # type: ignore[operator]

    return _legacy_compose(task_dir, preset, subtitle, _wrap_progress)
