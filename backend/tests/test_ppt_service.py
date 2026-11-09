"""ppt_gen must NOT render a thumbnail (P1-14): the LibreOffice full-deck
render for an unread cover.jpg was pure dead-code latency on every export."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.gateway.content.ppt_service import ContentTaskService


@pytest.mark.asyncio
async def test_run_ppt_gen_does_not_generate_thumbnail(tmp_path: Path):
    svc = ContentTaskService.__new__(ContentTaskService)  # skip BaseTaskService init

    async def progress(**kwargs):
        return None

    progress.set_result = MagicMock()  # type: ignore[attr-defined]

    async def set_result(**kwargs):
        return None

    progress.set_result = set_result  # type: ignore[attr-defined]

    with patch(
        "app.gateway.content.ppt_pipeline.build_pptx_from_doc",
        return_value={"output_path": str(tmp_path / "output.pptx")},
    ) as build:
        result = await svc._run_ppt_gen(
            "ct_test",
            {"doc_json": "{}", "template": "default"},
            tmp_path,
            progress,
        )

    assert result is None  # gateway-complete
    assert build.called
    # No cover.jpg side effect, no thumbnail conversion attempt
    assert not (tmp_path / "cover.jpg").exists()
    assert not hasattr(svc, "_generate_thumbnail")
