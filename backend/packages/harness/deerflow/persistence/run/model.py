"""ORM model for run metadata."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from deerflow.persistence.base import Base


class RunRow(Base):
    __tablename__ = "runs"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    thread_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    assistant_id: Mapped[str | None] = mapped_column(String(128))
    user_id: Mapped[str | None] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    # "pending" | "running" | "success" | "error" | "timeout" | "interrupted"
    # llm_bookkeeping rows use a subset: "success" | "error_downstream" | "error"

    # What kind of work this row represents. Only "run" rows are real agent
    # runs; the operations console filters on this so bookkeeping rows can
    # never inflate run counts or spend. Existing rows backfill to "run".
    operation_kind: Mapped[str] = mapped_column(String(32), nullable=False, default="run", server_default="run")

    model_name: Mapped[str | None] = mapped_column(String(128))
    multitask_strategy: Mapped[str] = mapped_column(String(20), default="reject")
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    kwargs_json: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text)

    # Convenience fields (for listing pages without querying RunEventStore)
    message_count: Mapped[int] = mapped_column(default=0)
    first_human_message: Mapped[str | None] = mapped_column(Text)
    last_ai_message: Mapped[str | None] = mapped_column(Text)

    # Token usage (accumulated in-memory by RunJournal, written on run completion)
    total_input_tokens: Mapped[int] = mapped_column(default=0)
    total_output_tokens: Mapped[int] = mapped_column(default=0)
    total_tokens: Mapped[int] = mapped_column(default=0)
    llm_call_count: Mapped[int] = mapped_column(default=0)
    lead_agent_tokens: Mapped[int] = mapped_column(default=0)
    subagent_tokens: Mapped[int] = mapped_column(default=0)
    middleware_tokens: Mapped[int] = mapped_column(default=0)

    # Follow-up association
    follow_up_to_run_id: Mapped[str | None] = mapped_column(String(64))

    # Stop reason: why the run ended (safety_filter / model_length / normal / etc.)
    stop_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)

    # P1-9: True when the run ended only because the LLM-error middleware
    # substituted a fallback AIMessage for a failed provider call. Such a run is
    # not a task success even though the graph terminated cleanly.
    llm_error_fallback: Mapped[bool | None] = mapped_column(Boolean, nullable=True, default=False)

    # W6: number of LLM calls in this run that exceeded slow_llm_warn_seconds
    # (stamped by SlowLLMCallMiddleware into runtime.context, persisted by the
    # run worker only when non-zero). NULL = "no slow calls observed OR row
    # predates tracking" (two states deliberately conflated); a positive integer
    # always means slow calls occurred.
    slow_llm_call_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # 2026-09-23 plan A4: number of empty-terminal-response fallbacks persisted
    # by TerminalResponseMiddleware in this run (stamped into runtime.context,
    # written here only when non-zero). Feeds the OBS-02 weekly SQL report and
    # the retry-recovery-rate KPI; same NULL conflation as slow_llm_call_count.
    empty_fallback_count: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Per-model token breakdown: {model_name: {input: N, output: N}}
    token_usage_by_model: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC))

    __table_args__ = (Index("ix_runs_thread_status", "thread_id", "status"),)
