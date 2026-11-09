"""ContentTaskService — handles content-domain async tasks (ppt_gen)."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from app.gateway._shared.task_framework.service import BaseTaskService, ProgressUpdater

logger = logging.getLogger(__name__)


class ContentTaskService(BaseTaskService):
    """Content domain async tasks. PPT gen runs in Gateway (asyncio.to_thread)."""

    domain = "content"
    id_prefix = "ct"

    def __init__(self, *args, stall_timeout: int = 600, **kwargs) -> None:
        # PPT gen is fast (seconds-to-minutes); a 10min stall window is generous
        # and catches truly wedged runs. Pass through any other BaseTaskService
        # kwargs (repo/storage_dir/max_concurrent_tasks).
        super().__init__(*args, stall_timeout=stall_timeout, **kwargs)

    def _should_handoff_to_worker(self, task_type: str) -> bool:
        return False

    async def _run_pipeline(
        self,
        task_id: str,
        user_id: str,
        task_type: str,
        payload: dict,
        task_dir: Path,
        progress: ProgressUpdater,
    ) -> dict | None:
        if task_type == "ppt_gen":
            return await self._run_ppt_gen(task_id, payload, task_dir, progress)
        if task_type == "pdf_gen":
            return await self._run_pdf_gen(task_id, payload, task_dir, progress)
        raise ValueError(f"Unknown task_type: {task_type}")

    async def _run_ppt_gen(
        self,
        task_id: str,
        payload: dict,
        task_dir: Path,
        progress: ProgressUpdater,
    ) -> dict | None:
        """Generate PPT from Markdown or legacy TipTap doc JSON. Runs in Gateway via asyncio.to_thread."""
        from app.gateway.content.ppt_pipeline import THEMES, build_pptx_from_doc

        # P2-43: reject unknown templates up front with the allowed list —
        # previously THEMES.get(name, THEMES["default"]) silently produced a
        # default-styled deck while the user thought they picked something else.
        template = payload.get("template", "default")
        if template not in THEMES:
            raise ValueError(f"Unknown template '{template}' (allowed: {sorted(THEMES)})")

        await progress(progress=5)

        loop = asyncio.get_running_loop()

        def _on_progress(pct: int, msg: str = "") -> None:
            logger.debug("ppt_gen %s pct=%s msg=%s", task_id, pct, msg)
            asyncio.run_coroutine_threadsafe(progress(progress=10 + int(pct * 0.85)), loop)

        result = await asyncio.to_thread(
            build_pptx_from_doc,
            doc_json=payload.get("doc_json", "{}"),
            template=template,
            out_dir=str(task_dir),
            progress=_on_progress,
            title=payload.get("title"),
        )

        await progress(progress=99)
        await progress.set_result(**result)
        return None  # Gateway-complete → base class sets ready

    async def _run_pdf_gen(
        self,
        task_id: str,
        payload: dict,
        task_dir: Path,
        progress: ProgressUpdater,
    ) -> dict | None:
        """Generate PDF from Markdown or ProseMirror doc JSON. Runs in Gateway via asyncio.to_thread."""
        from app.gateway.content.pdf_pipeline import build_pdf_from_doc

        await progress(progress=5)

        loop = asyncio.get_running_loop()

        def _on_progress(pct: int, msg: str = "") -> None:
            logger.debug("pdf_gen %s pct=%s msg=%s", task_id, pct, msg)
            asyncio.run_coroutine_threadsafe(progress(progress=10 + int(pct * 0.85)), loop)

        # B4-T2: 120s timeout for long documents (weasyprint can be slow)
        result = await asyncio.wait_for(
            asyncio.to_thread(
                build_pdf_from_doc,
                doc_json=payload.get("doc_json", "{}"),
                out_dir=str(task_dir),
                title=payload.get("title"),
                progress=_on_progress,
            ),
            timeout=120,
        )

        await progress(progress=99)
        await progress.set_result(**result)
        return None  # Gateway-complete -> base class sets ready
