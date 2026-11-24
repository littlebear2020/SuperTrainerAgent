"""Thread media usage ledger — host-side reader for sandbox-emitted paid media calls.

Sandbox-side emitters (the shared ``_doubao_tts.py`` mirrors, PPT backends and
``qihu360.ts``) append one JSON line per **successful** paid media call to
``<workspace>/.usage/media-usage.jsonl``. Inside the sandbox that is
``/mnt/user-data/workspace/.usage/media-usage.jsonl``; on the host it is
``get_paths().sandbox_work_dir(thread_id, user_id=...)`` — the same directory the
gateway already mounts, so no new API, token or stdout scraping is involved.

This module owns the host path, the bounded tail reader, the parser and the
server-side mapping. It is pure (no ``app.*`` import, no database) so the
harness boundary holds; :mod:`app.gateway._shared.agent_media_usage` drains it
into ``external_api_calls`` after a run ends.

Guards are the minimum needed to keep the feature usable, not adversarial
hardening: the file is opened with ``O_NOFOLLOW | O_NONBLOCK`` and must be a
regular file; only the last :data:`TAIL_WINDOW_BYTES` are read; a line over
:data:`MAX_LINE_BYTES` is an error, never a memory risk. Bad lines are counted
in ``LedgerRead.errors`` and skipped. Callers must never log line content.

Event contract (one JSON object per line; unknown fields are ignored):

- ``event_id: str`` (required), ``kind: "tts" | "image"`` (required)
- tts: ``char_count: int`` in ``1..100000`` (required — course narration is
  synthesized sentence by sentence with no per-sentence cap, so no tighter bound
  applies), ``with_timestamps: bool`` (default ``False``)
- image: ``model: str`` (required, must contain ``seedream``, ≤128 chars — the
  model actually sent, recorded verbatim so model-level pricing can match)
- optional on both: ``elapsed_ms: int``; image also ``width`` / ``height``

The ledger only ever grows; the reader's cost is bounded by the window.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import stat
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal

from deerflow.config.paths import get_paths

logger = logging.getLogger(__name__)

LEDGER_DIR_NAME = ".usage"
LEDGER_FILE_NAME = "media-usage.jsonl"

#: Only the tail of the ledger is read. One line is ~120–150 B, so the window
#: holds roughly 7000 events; a real run emits a few hundred. If more than a
#: window's worth of events lands between two drains the ones outside are lost —
#: the sink logs ``ledger window overflow`` when it can tell.
TAIL_WINDOW_BYTES = 1024 * 1024
MAX_LINE_BYTES = 4096
#: Upper bound on *new* rows written by one drain; the rest stay in the window
#: and are written by the next drain (duplicates never consume this budget).
MAX_EVENTS_PER_DRAIN = 1000

TTS_CHAR_COUNT_MAX = 100_000
IMAGE_MODEL_MARKER = "seedream"
IMAGE_MODEL_MAX_LEN = 128
#: Bounds that keep parsed events inside what ``external_api_calls`` can store
#: (INTEGER columns are 32-bit on Postgres; text must be encodable/printable).
#: Anything past them is a reader ``error`` once, instead of a DB error on every
#: drain while the line stays in the window (终审 M-4).
ELAPSED_MS_MAX = 2**31 - 1
DIMENSION_MAX = 10**6
METERING_SOURCE = "sandbox_reported"

TTS_PROVIDER = "doubao_tts"
TTS_MODEL = "seed-tts-2.0"
TTS_METHOD_PLAIN = "synthesize_sse"
TTS_METHOD_TIMESTAMPS = "synthesize_sse_with_timestamps"
IMAGE_PROVIDER = "seedream-360"
IMAGE_METHOD = "generate_image"

MediaUsageKind = Literal["tts", "image"]


@dataclass(frozen=True)
class MediaUsageEvent:
    """One parsed ledger line. Only the fields of its ``kind`` are populated."""

    event_id: str
    kind: MediaUsageKind
    with_timestamps: bool | None = None  # tts only
    char_count: int | None = None  # tts only
    model: str | None = None  # image only (verbatim)
    width: int | None = None  # image only
    height: int | None = None  # image only
    elapsed_ms: int | None = None


@dataclass(frozen=True)
class LedgerRead:
    events: list[MediaUsageEvent]
    errors: int
    truncated: bool  # file larger than the window: events outside may be lost


@dataclass(frozen=True)
class MappedMediaCall:
    """Server-side mapping of one event onto an ``external_api_calls`` row.

    Provider / method / status are fixed here — the sandbox never chooses them.
    """

    provider: str
    method: str
    model: str
    metadata: dict[str, Any]
    elapsed_ms: int | None
    status: str = "success"


def ledger_path(thread_id: str, user_id: str) -> Path:
    """Host path of a thread's ledger (inside its sandbox workspace)."""
    return get_paths().sandbox_work_dir(thread_id, user_id=user_id) / LEDGER_DIR_NAME / LEDGER_FILE_NAME


def media_call_id(thread_id: str, event_id: str) -> str:
    """Stable 46-char ``call_id`` (``media_`` + 40 hex) from thread + event.

    Idempotent across drains and threads-safe; fits the ``String(64)`` column
    unlike ``media_{run_id}_{event_id}``.
    """
    return "media_" + hashlib.sha256(f"{thread_id}:{event_id}".encode()).hexdigest()[:40]


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _optional_int(raw: dict, key: str, *, maximum: int) -> int | None:
    value = raw.get(key)
    if value is None:
        return None
    if not _is_int(value) or not 0 <= value <= maximum:
        raise ValueError(key)
    return value


def _printable_text(value: str) -> bool:
    """No control characters and no lone surrogates (would fail UTF-8 encoding on insert)."""
    return all(ch.isprintable() for ch in value)


def parse_ledger_line(line: str) -> MediaUsageEvent:
    """Parse one ledger line; raise :class:`ValueError` on any contract breach."""
    try:
        raw = json.loads(line)
    except json.JSONDecodeError as exc:
        raise ValueError("not json") from exc
    if not isinstance(raw, dict):
        raise ValueError("not an object")

    event_id = raw.get("event_id")
    if not isinstance(event_id, str) or not event_id:
        raise ValueError("event_id")
    kind = raw.get("kind")

    if kind == "tts":
        char_count = raw.get("char_count")
        if not _is_int(char_count) or not 1 <= char_count <= TTS_CHAR_COUNT_MAX:
            raise ValueError("char_count")
        with_timestamps = raw.get("with_timestamps", False)
        if not isinstance(with_timestamps, bool):
            raise ValueError("with_timestamps")
        event = MediaUsageEvent(event_id=event_id, kind="tts", with_timestamps=with_timestamps, char_count=char_count)
    elif kind == "image":
        model = raw.get("model")
        if not isinstance(model, str) or IMAGE_MODEL_MARKER not in model.lower() or len(model) > IMAGE_MODEL_MAX_LEN or not _printable_text(model):
            raise ValueError("model")
        event = MediaUsageEvent(event_id=event_id, kind="image", model=model)
    else:
        raise ValueError("kind")

    event = replace(event, elapsed_ms=_optional_int(raw, "elapsed_ms", maximum=ELAPSED_MS_MAX))
    if kind == "image":
        event = replace(event, width=_optional_int(raw, "width", maximum=DIMENSION_MAX), height=_optional_int(raw, "height", maximum=DIMENSION_MAX))
    # Everything else (provider / method / amount, or ``model`` on a tts event)
    # is deliberately dropped: the sandbox never picks provider, method or price.
    return event


def map_event_to_call(event: MediaUsageEvent) -> MappedMediaCall:
    """Map a parsed event to the provider / method / metadata the sink records."""
    if event.kind == "tts":
        return MappedMediaCall(
            provider=TTS_PROVIDER,
            method=TTS_METHOD_TIMESTAMPS if event.with_timestamps else TTS_METHOD_PLAIN,
            model=TTS_MODEL,
            metadata={"model": TTS_MODEL, "char_count": event.char_count, "metering_source": METERING_SOURCE},
            elapsed_ms=event.elapsed_ms,
        )
    metadata: dict[str, Any] = {"model": event.model, "metering_source": METERING_SOURCE}
    if event.width is not None:
        metadata["width"] = event.width
    if event.height is not None:
        metadata["height"] = event.height
    return MappedMediaCall(provider=IMAGE_PROVIDER, method=IMAGE_METHOD, model=event.model or "", metadata=metadata, elapsed_ms=event.elapsed_ms)


def read_ledger_events(path: Path, *, window_bytes: int | None = None, max_line_bytes: int | None = None) -> LedgerRead:
    """Read the ledger tail without ever blocking or raising.

    Missing file → empty read. Symlink / FIFO / directory / unreadable → one
    error, no events. A trailing line without a newline is a write in progress
    and is left for the next read. When the file exceeds the window the first
    (possibly partial) line in the window is dropped and ``truncated`` is set.
    Duplicate ``event_id`` within one read counts once. Never logs line content.
    """
    window = TAIL_WINDOW_BYTES if window_bytes is None else window_bytes
    max_line = MAX_LINE_BYTES if max_line_bytes is None else max_line_bytes

    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return LedgerRead([], 0, False)
    except OSError:
        return LedgerRead([], 1, False)

    try:
        try:
            st = os.fstat(fd)
            if not stat.S_ISREG(st.st_mode):
                return LedgerRead([], 1, False)
            size = st.st_size
            truncated = size > window
            start = size - window if truncated else 0
            drop_first = False
            if truncated and start > 0:
                # One byte before the window tells whether ``start`` is mid-line.
                os.lseek(fd, start - 1, os.SEEK_SET)
                drop_first = os.read(fd, 1) != b"\n"
            os.lseek(fd, start, os.SEEK_SET)
            remaining = min(size, window)
            chunks: list[bytes] = []
            while remaining > 0:
                chunk = os.read(fd, min(remaining, 1 << 20))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
        except OSError:
            # EIO / ESTALE on a vanishing mount after a successful open: an error, never a raise (终审 M-5).
            return LedgerRead([], 1, False)
    finally:
        os.close(fd)

    text = b"".join(chunks).decode("utf-8", errors="replace")
    lines = text.split("\n")
    # The final element is "" when the file ends with a newline, otherwise the
    # half line still being written — either way it is not an event yet.
    lines.pop()
    if drop_first and lines:
        lines.pop(0)

    events: list[MediaUsageEvent] = []
    seen: set[str] = set()
    errors = 0
    for line in lines:
        if not line:
            continue
        if len(line.encode("utf-8")) > max_line:
            errors += 1
            continue
        try:
            event = parse_ledger_line(line)
        except ValueError:
            errors += 1
            continue
        if event.event_id in seen:
            continue
        seen.add(event.event_id)
        events.append(event)
    return LedgerRead(events, errors, truncated)
