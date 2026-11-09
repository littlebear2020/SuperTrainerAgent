"""GatewayPipeline protocol — for pipelines that run inside Gateway."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from .service import ProgressUpdater


class GatewayPipeline(Protocol):
    """Pipeline executed in Gateway (not Worker).

    Service._run_pipeline() typically delegates to a GatewayPipeline instance.
    """

    async def run(
        self,
        payload: dict,
        task_dir: Path,
        update: ProgressUpdater,
    ) -> None: ...
