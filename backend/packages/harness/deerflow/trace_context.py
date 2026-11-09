"""Minimal trace-context constants (P1-S2 port landing pad).

The full upstream trace-context module (ContextVar trace id, header plumbing,
log enhancement) belongs to a later port phase. What the request-scoped secret
carrier needs today is exactly one exported constant: the metadata key under
which a derived trace id may appear — ``redact_config_secrets`` strips it from
persisted/echoed config surfaces so a caller cannot manufacture disagreement
with the server-issued id.
"""

from __future__ import annotations

from typing import Final

DEERFLOW_TRACE_METADATA_KEY: Final[str] = "deerflow_trace_id"
