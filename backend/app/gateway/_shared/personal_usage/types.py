from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta, timezone
from typing import Literal, cast

from app.gateway._shared.retired_agents import resolve_retired_agent

UsageCapability = Literal[
    "course-video",
    "creative-studio",
    "ppt-master",
    "podcast-studio",
    "ai_image",
    "ai_video",
]
UsageStatus = Literal["completed", "running", "incomplete"]
UsageCostSource = Literal["real", "estimated", "mixed"] | None


@dataclass(frozen=True)
class UsageWindow:
    days: int
    start_utc: datetime
    end_utc: datetime
    start_date: str
    end_date: str


@dataclass
class UsageModel:
    name: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int | None
    total_tokens: int
    cost: float | None
    cost_source: UsageCostSource


@dataclass
class UsageTask:
    task_key: str
    source_id: str
    capability: UsageCapability
    title: str | None
    status: UsageStatus
    cost: float | None
    llm_cost: float | None
    media_cost: float | None
    currency: str | None
    cost_source: UsageCostSource
    total_tokens: int | None
    models: list[UsageModel]
    updated_at: datetime | None


@dataclass
class UsageTaskEntry(UsageTask):
    activity_at: datetime


@dataclass
class UsageSummary:
    cost: float | None
    currency: str | None
    cost_source: UsageCostSource
    total_tokens: int | None


@dataclass
class PersonalUsageResult:
    window: UsageWindow
    summary: UsageSummary
    tasks: list[UsageTask]
    total_tasks: int
    has_more: bool


def make_usage_window(days: int, tz_offset_minutes: int, now: datetime | None = None) -> UsageWindow:
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    local_tz = timezone(timedelta(minutes=tz_offset_minutes))
    local_today = current.astimezone(local_tz).date()
    local_start = local_today - timedelta(days=days - 1)
    start_utc = datetime.combine(local_start, time.min, tzinfo=local_tz).astimezone(UTC)
    end_utc = datetime.combine(local_today + timedelta(days=1), time.min, tzinfo=local_tz).astimezone(UTC)
    return UsageWindow(
        days=days,
        start_utc=start_utc,
        end_utc=end_utc,
        start_date=local_start.isoformat(),
        end_date=local_today.isoformat(),
    )


def canonical_agent_capability(*candidates: str | None) -> UsageCapability | None:
    supported = {"course-video", "creative-studio", "ppt-master", "podcast-studio", "ai_image", "ai_video"}
    for candidate in candidates:
        if not candidate:
            continue
        resolved = resolve_retired_agent(candidate).strip().lower()
        if resolved in supported:
            return cast(UsageCapability, resolved)
    return None


def normalize_usage_status(raw_status: str | None) -> UsageStatus:
    status = (raw_status or "").strip().lower()
    if status in {"success", "ready"}:
        return "completed"
    if status in {
        "pending",
        "running",
        "draft",
        "extracting",
        "converting",
        "synthesizing",
        "scripting",
        "generating",
        "rendering",
        "story_ready",
        "composing",
    }:
        return "running"
    return "incomplete"


def merge_cost_source(current: UsageCostSource, incoming: UsageCostSource) -> UsageCostSource:
    if current == "mixed" or incoming == "mixed":
        return "mixed"
    if current is None:
        return incoming
    if incoming is None or current == incoming:
        return current
    return "mixed"
