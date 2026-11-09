"""Test ensure_task_framework_schema adds missing columns to unified_tasks.

Mirrors the ensure_writer_schema pattern: existing tables missing model
columns get them added + backfilled with the scalar default.
"""

from sqlalchemy import create_engine, inspect, text

from app.gateway._shared.task_framework.ensure_schema import _reconcile_sync


def _create_partial_unified_tasks(db_path):
    """Create unified_tasks WITHOUT dirty_frames (simulate pre-deploy drift)."""
    engine = create_engine(f"sqlite:///{db_path}")
    with engine.begin() as conn:
        conn.execute(
            text(
                """
            CREATE TABLE unified_tasks (
                id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                domain TEXT NOT NULL,
                task_type TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                progress INTEGER DEFAULT 0,
                priority INTEGER DEFAULT 50,
                payload TEXT NOT NULL DEFAULT '{}',
                result TEXT NOT NULL DEFAULT '{}',
                error_message TEXT,
                retry_count INTEGER NOT NULL DEFAULT 0,
                claimed_by TEXT,
                claimed_at DATETIME,
                lease_expires_at DATETIME,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL
            )
            """
            )
        )
        conn.execute(
            text(
                "INSERT INTO unified_tasks (id, user_id, domain, task_type, status, "
                "progress, priority, payload, result, retry_count, created_at, updated_at) "
                "VALUES ('t1', 'u1', 'video_gen', 'stock', 'ready', 100, 50, '{}', '{}', 0, "
                "'2026-01-01 00:00:00', '2026-01-01 00:00:00')"
            )
        )
    return engine


def test_self_heal_adds_dirty_frames_to_existing_table(tmp_path):
    engine = _create_partial_unified_tasks(tmp_path / "tasks.db")

    # Pre-condition: column absent
    insp = inspect(engine)
    cols = {c["name"] for c in insp.get_columns("unified_tasks")}
    assert "dirty_frames" not in cols

    with engine.begin() as conn:
        added = _reconcile_sync(conn)

    assert added == {"unified_tasks": ["dirty_frames", "title", "thumbnail_url"]}

    # Column present + existing row backfilled to "[]" (scalar default)
    with engine.begin() as conn:
        row = conn.execute(text("SELECT dirty_frames FROM unified_tasks WHERE id='t1'")).fetchone()
    assert row[0] == "[]"


def test_self_heal_idempotent_when_column_present(tmp_path):
    from app.gateway._shared.task_framework.models import TaskRow

    engine = create_engine(f"sqlite:///{tmp_path}/full.db")
    TaskRow.__table__.create(engine)  # full schema incl. dirty_frames

    with engine.begin() as conn:
        added = _reconcile_sync(conn)

    assert added == {}


def test_self_heal_skips_missing_table(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/empty.db")
    with engine.begin() as conn:
        added = _reconcile_sync(conn)
    assert added == {}
