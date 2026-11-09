"""Object filters used by ``env.py`` to scope alembic to DeerFlow tables.

LangGraph checkpointer tables live in the same database but are owned by
LangGraph. Without this filter, ``alembic revision --autogenerate`` would
reflect them and emit spurious ``drop_table`` ops every revision.

Locally this is not hypothetical: ``checkpoints`` (63k rows) and ``writes``
(83k rows) account for essentially all of the dev DB's 112 GB. An autogenerate
run that proposed dropping them would be catastrophic.

Kept in its own module (instead of inlined in ``env.py``) so it can be
unit-tested without dragging in alembic's import-time machinery.
"""

from __future__ import annotations

# Tables owned by LangGraph -- alembic must never propose DDL for them.
#
# ``checkpoints`` / ``writes`` are what langgraph's AsyncSqliteSaver creates
# locally; the ``checkpoint_*`` names are the Postgres saver's equivalents.
# Both naming families are listed so switching backends does not silently
# re-expose the other family to autogenerate.
LANGGRAPH_OWNED_TABLES: frozenset[str] = frozenset(
    {
        "checkpoints",
        "writes",
        "checkpoint_blobs",
        "checkpoint_writes",
        "checkpoint_migrations",
    }
)


def include_object(object_, name, type_, reflected, compare_to):  # noqa: ARG001 - alembic contract
    """Returns False for any LangGraph-owned table or for an index/constraint
    whose parent table is LangGraph-owned. Returns True otherwise.

    Signature matches alembic's ``include_object`` callable contract:
    ``(object, name, type_, reflected, compare_to)``.
    """
    if type_ == "table" and name in LANGGRAPH_OWNED_TABLES:
        return False
    parent_table = getattr(object_, "table", None)
    if parent_table is not None and getattr(parent_table, "name", None) in LANGGRAPH_OWNED_TABLES:
        return False
    return True
