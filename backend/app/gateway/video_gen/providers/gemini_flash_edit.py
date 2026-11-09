"""360 google/gemini-3.1-flash-image-preview-edit provider (备选).

Subclasses :class:`NanoBananaImageProvider` — same wire format (POST
/v1/images/edits + top-level ``images`` array + ``extra_body.generation_config``),
only the default model id differs.

⚠️ UNVALIDATED: the 2026-07-06 A/B rounds tested ``gemini-3-pro-image-preview``
(nano-banana) and ``openai/gpt-image-2`` — NOT this flash-edit model. Whether
``gemini-3.1-flash-image-preview-edit`` accepts the top-level ``images`` format
and how its character consistency compares are UNKNOWN. Registered as a backup
``ai_image_provider`` option only; do NOT wire it as any path's default until
a dedicated connectivity + consistency test passes. See spec §3.3.
"""

from __future__ import annotations

from typing import Any

from .nano_banana import NanoBananaImageProvider

_DEFAULT_MODEL = "google/gemini-3.1-flash-image-preview-edit"


class GeminiFlashEditProvider(NanoBananaImageProvider):
    """Backup image provider backed by google/gemini-3.1-flash-image-preview-edit."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.360.cn",
        **kwargs: Any,
    ) -> None:
        kwargs.setdefault("model", _DEFAULT_MODEL)
        super().__init__(api_key=api_key, base_url=base_url, **kwargs)
