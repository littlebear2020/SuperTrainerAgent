from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta, timezone
from typing import TYPE_CHECKING, Literal, cast

from app.gateway._shared.retired_agents import resolve_retired_agent

if TYPE_CHECKING:
    from deerflow.persistence.run.model import RunRow
    from deerflow.persistence.thread_meta.model import ThreadMetaRow

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
    #: Known unpriced media rows (``source == "unpriced"``) on this task. Required
    #: (no default) so ``UsageTaskEntry`` inherits cleanly; failed / free rows are
    #: not counted and un-instrumented channels are invisible here by definition.
    unpriced_media_calls: int
    currency: str | None
    cost_source: UsageCostSource
    total_tokens: int | None
    models: list[UsageModel]
    updated_at: datetime | None
    #: 作品/会话已删除：费用与 Token 仍按账本计入，条目来自删除快照。必填（无默认值），
    #: 否则子类 ``UsageTaskEntry`` 的 ``activity_at`` 会因「无默认值字段排在有默认值字段之后」报错。
    deleted: bool


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


def resolve_agent_capability(thread: ThreadMetaRow, runs: Iterable[RunRow]) -> UsageCapability | None:
    """Agent 会话的能力归属：聚合与删除快照共用，保证两处口径一致。

    候选顺序：线程的 ``assistant_id``、``metadata.agent_name``、``metadata.assistant_id``，
    再按传入顺序取各 run 的 ``assistant_id``；``ai_image`` / ``ai_video`` 走任务口径，不属于会话。
    """
    metadata = thread.metadata_json if isinstance(thread.metadata_json, dict) else {}
    candidates = [thread.assistant_id, metadata.get("agent_name"), metadata.get("assistant_id")]
    candidates.extend(run.assistant_id for run in runs)
    capability = canonical_agent_capability(*candidates)
    if capability is None or capability in {"ai_image", "ai_video"}:
        return None
    return capability


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
