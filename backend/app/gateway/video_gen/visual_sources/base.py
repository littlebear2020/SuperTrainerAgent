"""VisualSource protocol: fills each storyboard frame's visual_url/visual_type."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol

from ..storyboard import Storyboard


class VisualSource(Protocol):
    async def render(
        self,
        storyboard: Storyboard,
        *,
        only_frames: list[int] | None = None,
        on_frame_done: Callable[[int], Awaitable[None]] | None = None,
    ) -> None:
        """Populate frame.visual_url and frame.visual_type.

        If only_frames is given, only process frames whose index is in the list.
        If on_frame_done is given, it is awaited after each frame's visual is set.
        """
        ...
