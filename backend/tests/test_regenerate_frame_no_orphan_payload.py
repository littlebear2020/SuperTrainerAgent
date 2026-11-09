"""Regression: _regenerate_frame must NOT write compose_payload.json.

The orphan json was the root cause of the original 'silent edit loss' bug
(plan-2026-07-02 §1.1): _regenerate_frame wrote a compose_payload.json that
no consumer ever read. Composition is now /recompose's job (B0). This test
prevents the orphan from being re-introduced.
"""

import inspect

from app.gateway.video_gen import service as svc


def test_regenerate_frame_does_not_write_orphan_compose_payload():
    # Target the write CALL, not the bare string — a stale comment at :1135
    # used to mention compose_payload.json by name, and `inspect.getsource`
    # would false-fail on it. Checking the write statement is also more
    # precise: it asserts the orphan write is gone, not just the literal.
    src = inspect.getsource(svc.VideoGenService._regenerate_frame)
    assert "compose_json.write_text" not in src, "_regenerate_frame must not write compose_payload.json — it is an orphan with no consumer. Composition is /recompose's job."
    assert 'task_dir / "compose_payload.json"' not in src, "the compose_payload.json path binding must also be removed"


def test_regenerate_frame_does_not_set_incremental_payload():
    """The incremental/changed_frames keys were only for the orphan json.
    If re-added, they must go through the recompose payload, not here."""
    src = inspect.getsource(svc.VideoGenService._regenerate_frame)
    assert 'worker_payload["incremental"]' not in src
    assert 'worker_payload["changed_frames"]' not in src


def test_regenerate_frame_docstring_no_longer_claims_incremental_compose():
    src = inspect.getsource(svc.VideoGenService._regenerate_frame)
    assert "Uses incremental compose" not in src, "_regenerate_frame docstring must not claim 'incremental compose' — it no longer composes at all; it refreshes on-disk assets for /recompose."
