#!/usr/bin/env python3
"""Strip legacy base64 image payloads from stored checkpoints (T26, 2026-08-13).

Background (R-11 history): before the view_image checkpoint fix (T12), the
``view_image`` tool stored full base64 image data in ``viewed_images`` state
and the ViewImageMiddleware injected image-detail HumanMessages with base64
``image_url`` blocks — every checkpoint carried a copy. That accumulated into
a 112GB local SQLite DB (97% base64 images, one repeated 24x).

Post-T12, new checkpoints carry only metadata, but OLD checkpoints still hold
the payloads. This script rewrites them:

  * ``viewed_images`` entries: ``{base64, mime_type}`` -> keep only the
    metadata keys (``mime_type``/``size``/``actual_path``); entries with
    nothing left after stripping are dropped entirely.
  * Image-detail HumanMessages (marker text or ``image_url`` blocks carrying
    ``data:`` URLs): ``image_url`` blocks are replaced with a compact text
    stub (same message id, so the add_messages reducer sees an update, not a
    new message). Messages whose content is ONLY image payloads get stub text.

Behaviour:
  * Idempotent: checkpoints with nothing to strip are skipped (no rewrite).
  * Resumable: threads are processed one at a time; a ``--state`` sidecar
    records completed thread ids. Re-running continues where it left off.
  * Batched: commits once per thread (SQLite WAL-friendly).

SAFETY — READ BEFORE RUNNING:
  * The checkpoint blob is decoded and re-encoded with langgraph's own
    ``JsonPlusSerializer`` (type-tag column + payload, exactly as the
    checkpointer stores it) so the round-trip preserves message types.
  * Run ONLY against a stopped gateway, or on a COPY of the DB.
  * Take a backup first; disk must have headroom (VACUUM rewrites the file).
  * ``--dry-run`` reports what WOULD be stripped without writing anything.
  * Server DB: evaluate separately (T24 data pull) — do not point this at the
    production DB until the T12 fix is deployed and verified there.

Usage:
  uv run python scripts/cleanup_checkpoint_base64.py --dry-run
  uv run python scripts/cleanup_checkpoint_base64.py --db path/to/deerflow.db
  uv run python scripts/cleanup_checkpoint_base64.py --vacuum
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

_IMAGE_DETAIL_MARKERS = (
    "Here are the images you've viewed",
    "Here are the details of the images you've viewed",
)
_SLIM_STUB = "[image previously viewed — content omitted to save context]"
_STATE_FILENAME = ".cleanup_checkpoint_base64_state.json"


def _load_serializer():
    from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer

    return JsonPlusSerializer()


def _strip_viewed_images(viewed_images):
    """Return (new_value, changed) for a viewed_images channel value."""
    if not isinstance(viewed_images, dict):
        return viewed_images, False
    changed = False
    out: dict = {}
    for path, entry in viewed_images.items():
        if not isinstance(entry, dict):
            out[path] = entry
            continue
        if "base64" not in entry:
            out[path] = entry
            continue
        changed = True
        stripped = {k: v for k, v in entry.items() if k != "base64"}
        if stripped:
            out[path] = stripped
        # Entry had nothing but base64 — drop it entirely.
    return out, changed


def _strip_message(message) -> tuple[object, bool]:
    """Strip base64 image blocks from one message. Returns (msg, changed).

    ``loads_typed`` reconstructs real message objects (HumanMessage etc.), so
    we operate on object content, not the raw serde dicts.
    """
    from langchain_core.messages import HumanMessage

    if not isinstance(message, HumanMessage):
        return message, False
    content = message.content
    if not isinstance(content, list):
        return message, False

    new_blocks = []
    had_image = False
    for block in content:
        if not isinstance(block, dict):
            new_blocks.append(block)
            continue
        if block.get("type") == "image_url":
            url = (block.get("image_url") or {}).get("url", "")
            if isinstance(url, str) and url.startswith("data:"):
                had_image = True
                continue  # drop the base64 payload block
            new_blocks.append(block)
            continue
        new_blocks.append(block)

    if not had_image:
        return message, False
    # Same message id via model_copy — the add_messages reducer treats this as
    # an update of the existing message, not a new one.
    new_blocks.append({"type": "text", "text": _SLIM_STUB})
    return message.model_copy(update={"content": new_blocks}), True


def _strip_checkpoint(serializer, type_col, blob: bytes) -> tuple[tuple[str, bytes] | None, int]:
    """Deserialize, strip, re-serialize. Returns ((new_type, new_blob) | None, bytes_freed).

    The checkpointer stores the serde type tag in the ``type`` column and the
    payload in the ``checkpoint`` column; ``loads_typed`` expects the pair.

    Old checkpoints written by earlier langgraph versions may not round-trip;
    those are skipped with a warning so the sweep keeps going.
    """
    try:
        checkpoint = serializer.loads_typed((type_col, blob))
    except Exception as exc:
        print(f"  ! skipping checkpoint (deserialize failed): {type(exc).__name__}: {exc}")
        return None, 0
    channel_values = checkpoint.get("channel_values", {})
    changed = False

    viewed = channel_values.get("viewed_images")
    if viewed is not None:
        new_viewed, v_changed = _strip_viewed_images(viewed)
        if v_changed:
            channel_values["viewed_images"] = new_viewed
            changed = True

    messages = channel_values.get("messages")
    if messages is not None:
        new_messages = []
        for message in messages:
            msg, m_changed = _strip_message(message)
            new_messages.append(msg)
            changed = changed or m_changed
        if changed:
            channel_values["messages"] = new_messages

    if not changed:
        return None, 0
    new_type, new_blob = serializer.dumps_typed(checkpoint)
    return (new_type, new_blob), len(blob)


def _iter_threads(conn: sqlite3.Connection):
    rows = conn.execute("SELECT DISTINCT thread_id FROM checkpoints ORDER BY thread_id").fetchall()
    return [r[0] for r in rows]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db", default=".deer-flow/data/deerflow.db", help="Path to the SQLite DB (default: backend/.deer-flow/data/deerflow.db)")
    parser.add_argument("--dry-run", action="store_true", help="Report only; do not write")
    parser.add_argument("--vacuum", action="store_true", help="Run VACUUM after stripping (reclaims file space; needs disk headroom)")
    parser.add_argument("--reset-state", action="store_true", help="Forget completed-thread progress and start over")
    parser.add_argument("--limit-threads", type=int, default=None, help="Process at most N threads (for pilot batches)")
    args = parser.parse_args()

    db_path = Path(args.db).resolve()
    if not db_path.exists():
        print(f"DB not found: {db_path}", file=sys.stderr)
        return 1

    serializer = _load_serializer()
    state_path = db_path.parent / _STATE_FILENAME
    done_threads: set[str] = set()
    if state_path.exists() and not args.reset_state:
        done_threads = set(json.loads(state_path.read_text(encoding="utf-8")))

    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA query_only = ON" if args.dry_run else "PRAGMA busy_timeout = 30000")
    total_freed = 0
    total_rewritten = 0
    threads_done = 0
    try:
        threads = _iter_threads(conn)
        todo = [t for t in threads if t not in done_threads]
        if args.limit_threads is not None:
            todo = todo[: args.limit_threads]
        print(f"{len(threads)} threads total, {len(todo)} to process, {len(done_threads)} already done")

        for idx, thread_id in enumerate(todo, 1):
            rows = conn.execute(
                "SELECT checkpoint_id, type, checkpoint FROM checkpoints WHERE thread_id = ? ORDER BY checkpoint_id",
                (thread_id,),
            ).fetchall()
            thread_freed = 0
            thread_rewritten = 0
            updates = []
            for checkpoint_id, type_col, blob in rows:
                if blob is None:
                    continue
                new_pair, freed = _strip_checkpoint(serializer, type_col, blob)
                if new_pair is None:
                    continue
                updates.append((new_pair, checkpoint_id))
                thread_freed += freed
                thread_rewritten += 1

            if updates and not args.dry_run:
                with conn:
                    conn.executemany(
                        "UPDATE checkpoints SET type = ?, checkpoint = ? WHERE checkpoint_id = ? AND thread_id = ?",
                        [(new_type, new_blob, cid, thread_id) for (new_type, new_blob), cid in updates],
                    )
            total_freed += thread_freed
            total_rewritten += thread_rewritten
            done_threads.add(thread_id)
            threads_done += 1

            if not args.dry_run and idx % 20 == 0:
                state_path.write_text(json.dumps(sorted(done_threads)), encoding="utf-8")

            print(f"[{idx}/{len(todo)}] {thread_id}: {thread_rewritten} checkpoints rewritten, freed {thread_freed / 1024 / 1024:.1f} MB")

        if not args.dry_run:
            state_path.write_text(json.dumps(sorted(done_threads)), encoding="utf-8")
            if args.vacuum:
                print("Running VACUUM (this can take a long time on a large DB)...")
                conn.execute("VACUUM")
                print("VACUUM complete")
        else:
            print(f"DRY RUN — would rewrite {total_rewritten} checkpoints, freeing {total_freed / 1024 / 1024 / 1024:.2f} GB")
    finally:
        conn.close()

    print(f"Done: {threads_done} threads processed this run, {total_rewritten} checkpoints rewritten, {total_freed / 1024 / 1024 / 1024:.2f} GB stripped in total")
    return 0


if __name__ == "__main__":
    sys.exit(main())
