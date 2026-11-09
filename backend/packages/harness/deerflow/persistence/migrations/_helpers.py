"""Idempotent helpers for alembic column revisions.

Column revisions in ``versions/`` should use these helpers instead of raw
``op.add_column`` / ``op.drop_column`` so re-running a column change against a
DB that already has (or has already removed) the column is a safe no-op.

Two reasons we need idempotency:

1. **Defence-in-depth on top of bootstrap locking.** ``bootstrap_schema()``
   serialises Postgres with an advisory lock and SQLite within one process
   with an ``asyncio.Lock``. If a retry happens anyway (manual ALTER,
   misconfiguration, SQLite cross-process contention), the revision must still
   be safe to re-run.

2. **Same posture that made ``Base.metadata.create_all`` forgiving.**
   ``create_all`` skips existing tables. Column migrations should mirror that
   forgiving behavior by skipping columns already in the desired state. This
   matters locally because the three ``ensure_*_schema`` startup patches spent
   a year adding columns by hand -- every DB they touched has columns alembic
   is about to be told it owns.

Drift warning
-------------

Name-match alone can hide a column that a manual ``ALTER`` left in a shape
that diverges from what ``Base.metadata.create_all`` would produce on a fresh
DB. The local ``ensure_*_schema`` patches did exactly this: SQLite cannot
``ADD COLUMN ... NOT NULL`` on a non-empty table, so they added every column
as nullable and backfilled the default with an ``UPDATE`` -- leaving
``unified_tasks.dirty_frames`` and ``writer_docs.doc_type`` nullable in the
dev DB where the model declares them ``NOT NULL``. To surface that silent
drift, ``safe_add_column`` compares the existing column's ``nullable`` /
``server_default`` / ``type`` against the desired ``sa.Column`` and emits
``logger.warning`` on mismatch. Type comparison goes through
``_type_equivalent``, which treats known dialect-synonym pairs (e.g. ``JSON``
vs ``JSONB``) as equivalent to avoid false positives while still catching
wholesale type mismatches like ``TEXT`` vs ``JSON``. We do not auto-repair --
a warning is enough for operators to notice and decide.
"""

from __future__ import annotations

import logging

import sqlalchemy as sa
from alembic import op

logger = logging.getLogger(__name__)


def _inspector():
    return sa.inspect(op.get_bind())


def _normalize_default(value: object) -> str | None:
    """Normalize a server-default value for cross-source comparison.

    The desired value comes from ``sa.Column.server_default`` (a
    ``DefaultClause`` / ``TextClause`` literal, ``None``, or a Python literal);
    the reflected value comes from ``Inspector.get_columns()['default']`` as a
    dialect-rendered string. Strip outer parens / whitespace / Postgres-style
    type casts so textually-equivalent forms compare equal across dialects.
    """
    if value is None:
        return None
    if isinstance(value, sa.sql.elements.TextClause):
        text = value.text
    elif isinstance(value, sa.schema.DefaultClause) and isinstance(value.arg, sa.sql.elements.TextClause):
        text = value.arg.text
    elif isinstance(value, sa.schema.DefaultClause):
        text = str(value.arg)
    else:
        text = str(value)
    text = text.strip()
    # Strip a single layer of outer parens that some dialects wrap defaults in.
    if text.startswith("(") and text.endswith(")"):
        text = text[1:-1].strip()
    # Strip Postgres-style type casts like ``'{}'::jsonb``.
    if "::" in text:
        text = text.split("::", 1)[0].strip()
    # SQLite reflects string defaults with the quotes included; the model side
    # carries the bare literal. Normalize both to the unquoted form.
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        text = text[1:-1]
    return text or None


def _normalize_type(value: object) -> str:
    """Normalize a SQLAlchemy ``TypeEngine`` (or reflected type) for comparison.

    Returns the upper-cased type-class name with any parameters stripped
    (e.g. ``JSON()`` -> ``"JSON"``, ``VARCHAR(255)`` -> ``"VARCHAR"``). Length
    parameters are dropped on purpose: drift warnings target wholesale type
    misconfigurations, not dialect-rendered size defaults. An empty string
    signals "missing info" -- callers should not equality-check empty strings.
    """
    if value is None:
        return ""
    s = value if isinstance(value, str) else repr(value)
    return s.upper().split("(", 1)[0].strip()


# Known dialect-synonym pairs that must NOT fire a type-drift warning.
# Postgres reflects ``JSON`` as ``JSONB`` (and vice versa depending on how
# the column was provisioned); the model's ``sa.JSON`` plus this allowlist
# keeps a Postgres deployment quiet while still catching genuine type errors.
#
# Add a new pair here ONLY when a real reflection-vs-model mismatch is
# proven to be a false positive in a deployment -- not pre-emptively, since
# overly broad equivalence would re-open the silent-drift hole this helper
# exists to close.
_EQUIVALENT_TYPE_FAMILIES: tuple[frozenset[str], ...] = (frozenset({"JSON", "JSONB"}),)


def _type_equivalent(actual: object, desired: object) -> bool:
    """True if *actual* and *desired* are the same type or a known equivalent.

    Returns True when either side is missing reflected info so missing-data
    cases never false-positive into a noisy warning.
    """
    a = _normalize_type(actual)
    d = _normalize_type(desired)
    if not a or not d:
        return True
    if a == d:
        return True
    pair = frozenset({a, d})
    return any(pair <= fam for fam in _EQUIVALENT_TYPE_FAMILIES)


def _check_column_drift(table: str, desired: sa.Column, actual: dict) -> None:
    """Warn if an existing column's attributes diverge from the desired model.

    Equality is checked on ``nullable`` and ``server_default`` directly, and
    on ``type`` via ``_type_equivalent``. The reflected and desired type reprs
    are echoed in the warning payload regardless of which dimension failed, so
    an operator triaging the log line sees the type context at a glance.
    """
    diffs: list[str] = []

    desired_nullable = True if desired.nullable is None else bool(desired.nullable)
    actual_nullable = bool(actual.get("nullable", True))
    if desired_nullable != actual_nullable:
        diffs.append(f"nullable actual={actual_nullable} desired={desired_nullable}")

    desired_default = _normalize_default(desired.server_default)
    actual_default = _normalize_default(actual.get("default"))
    if desired_default != actual_default:
        diffs.append(f"server_default actual={actual_default!r} desired={desired_default!r}")

    if not _type_equivalent(actual.get("type"), desired.type):
        diffs.append(f"type actual={_normalize_type(actual.get('type'))!r} desired={_normalize_type(desired.type)!r}")

    if diffs:
        logger.warning(
            "safe_add_column: %s.%s already exists but drifts from the model definition (%s); actual_type=%r desired_type=%r; leaving as-is -- a manual ALTER may be needed to match the model.",
            table,
            desired.name,
            "; ".join(diffs),
            actual.get("type"),
            desired.type,
        )


def safe_add_column(table: str, column: sa.Column) -> None:
    """``op.add_column`` that no-ops when the table or column is missing/present.

    - Missing table => nothing to add to. Skip silently because bootstrap only
      supports legacy DBs that already have the baseline table set.
    - Column already exists => no-op. Before returning, ``_check_column_drift``
      compares the existing column's nullability / server_default / type
      against the desired ``column`` and ``logger.warning``\\ s on mismatch so
      manually-applied workarounds do not silently survive as latent drift.
    """
    insp = _inspector()
    if table not in insp.get_table_names():
        return
    existing = {c["name"]: c for c in insp.get_columns(table)}
    if column.name in existing:
        _check_column_drift(table, column, existing[column.name])
        return
    with op.batch_alter_table(table) as batch:
        batch.add_column(column)


def safe_drop_column(table: str, column_name: str) -> None:
    """``op.drop_column`` that no-ops when the table or column is already gone."""
    insp = _inspector()
    if table not in insp.get_table_names():
        return
    existing = {c["name"] for c in insp.get_columns(table)}
    if column_name not in existing:
        return
    with op.batch_alter_table(table) as batch:
        batch.drop_column(column_name)


def safe_create_index(table: str, index_name: str, columns: list[str], *, unique: bool = False, **kw) -> None:
    """``op.create_index`` that no-ops when the table or index is already there.

    Local counterpart to ``safe_add_column`` for the index case. The
    ``ensure_*_schema`` patches created some indexes by hand (see
    ``ensure_writer_versions_unique``), so an index revision must tolerate
    finding its index already present.
    """
    insp = _inspector()
    if table not in insp.get_table_names():
        return
    existing = {ix["name"] for ix in insp.get_indexes(table)}
    if index_name in existing:
        return
    op.create_index(index_name, table, columns, unique=unique, **kw)


def safe_drop_index(table: str, index_name: str) -> None:
    """``op.drop_index`` that no-ops when the table or index is already gone.

    A bare ``op.drop_index`` explodes on a partially rolled-back DB (index
    already dropped by hand, column still present), which aborts the whole
    downgrade. Mirror ``safe_drop_column``: check first, drop second.
    """
    insp = _inspector()
    if table not in insp.get_table_names():
        return
    existing = {ix["name"] for ix in insp.get_indexes(table)}
    if index_name not in existing:
        return
    op.drop_index(index_name, table_name=table)


def safe_drop_table(table: str) -> None:
    """``op.drop_table`` that no-ops when the table is already gone.

    Used by the orphan-table cleanup revision: fresh DBs never had those
    tables, so the revision must be a no-op there rather than an error.
    """
    insp = _inspector()
    if table not in insp.get_table_names():
        return
    op.drop_table(table)
