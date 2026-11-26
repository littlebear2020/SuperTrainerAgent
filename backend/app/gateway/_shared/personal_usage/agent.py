from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

from sqlalchemy import func, or_, select
from sqlalchemy.orm import aliased

from app.gateway._shared.media_pricing_resolver import _load_media_pricing
from app.gateway._shared.personal_usage.tombstones import load_tombstones
from app.gateway._shared.personal_usage.types import (
    UsageCapability,
    UsageCostSource,
    UsageModel,
    UsageStatus,
    UsageTask,
    UsageTaskEntry,
    UsageWindow,
    merge_cost_source,
    normalize_usage_status,
    resolve_agent_capability,
)
from app.gateway._shared.task_cost import evaluate_media_call
from app.gateway.routers.console import _as_utc, _build_pricing_map, _lookup_pricing, _pricing_currency, _run_cost, _token_cost
from deerflow.persistence.engine import get_session_factory
from deerflow.persistence.external_api_calls.model import ExternalApiCallRow
from deerflow.persistence.run.model import RunRow
from deerflow.persistence.thread_meta.model import ThreadMetaRow


@dataclass
class _LLMUsage:
    total_tokens: int | None
    cost: float | None
    currency: str | None
    cost_source: UsageCostSource
    models: list[UsageModel]


def _token_count(value: Any) -> int | None:
    try:
        count = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return count if count >= 0 else None


def _real_amount(usage: dict) -> tuple[bool, float | None]:
    if usage.get("amount") is None:
        return False, None
    try:
        return True, float(usage["amount"])
    except (TypeError, ValueError):
        return False, None


def _usage_for_cost(usage_map: dict | None) -> dict | None:
    if not isinstance(usage_map, dict):
        return usage_map
    normalized = {}
    for model, usage in usage_map.items():
        if not isinstance(usage, dict):
            normalized[model] = usage
            continue
        safe_usage = dict(usage)
        for field in ("input_tokens", "output_tokens", "cache_read_tokens"):
            if field not in safe_usage:
                continue
            try:
                int(safe_usage[field] or 0)
            except (TypeError, ValueError, OverflowError):
                safe_usage[field] = 0
        normalized[model] = safe_usage
    return normalized


def _aggregate_llm_rows(rows: list[RunRow], pricing: dict) -> _LLMUsage:
    if not rows:
        return _LLMUsage(None, None, None, None, [])

    total_tokens = 0
    cost = 0.0
    has_cost = False
    cost_source: UsageCostSource = None
    has_real = False
    models_complete = True
    model_rows: dict[str, dict[str, Any]] = {}

    for row in rows:
        run_input = int(row.total_input_tokens or 0)
        run_output = int(row.total_output_tokens or 0)
        total_tokens += run_input + run_output
        usage_map = row.token_usage_by_model
        run_has_tokens = run_input + run_output > 0
        parsed_buckets: dict[str, tuple[int, int, int | None]] = {}
        has_buckets = isinstance(usage_map, dict) and bool(usage_map)
        buckets_valid = has_buckets

        if isinstance(usage_map, dict):
            for model, usage in usage_map.items():
                if not isinstance(usage, dict) or "input_tokens" not in usage or "output_tokens" not in usage:
                    buckets_valid = False
                    continue
                input_tokens = _token_count(usage.get("input_tokens"))
                output_tokens = _token_count(usage.get("output_tokens"))
                if input_tokens is None or output_tokens is None:
                    buckets_valid = False
                    continue
                cache_read_tokens = _token_count(usage.get("cache_read_tokens")) if "cache_read_tokens" in usage else None
                parsed_buckets[model] = (input_tokens, output_tokens, cache_read_tokens)

        if (run_has_tokens or has_buckets) and (not buckets_valid or sum(value[0] for value in parsed_buckets.values()) != run_input or sum(value[1] for value in parsed_buckets.values()) != run_output):
            models_complete = False

        run_cost = _run_cost(
            pricing,
            model_name=row.model_name,
            total_input_tokens=run_input,
            total_output_tokens=run_output,
            token_usage_by_model=_usage_for_cost(usage_map),
        )
        if run_cost is not None:
            cost += run_cost
            has_cost = True

        bucket_has_cost = False
        if isinstance(usage_map, dict):
            for model, usage in usage_map.items():
                if not isinstance(usage, dict):
                    continue
                input_tokens = _token_count(usage.get("input_tokens")) or 0
                output_tokens = _token_count(usage.get("output_tokens")) or 0
                valid_amount, amount = _real_amount(usage)
                model_cost: float | None = None
                model_source: UsageCostSource = None
                if valid_amount:
                    model_cost = amount
                    model_source = "real"
                    has_real = True
                else:
                    price = _lookup_pricing(pricing, model)
                    if price is not None and (input_tokens != 0 or output_tokens != 0):
                        model_cost = _token_cost(input_tokens, output_tokens, price, _token_count(usage.get("cache_read_tokens")) or 0)
                        model_source = "estimated"
                if model_cost is not None:
                    bucket_has_cost = True
                    cost_source = merge_cost_source(cost_source, model_source)

                if model not in parsed_buckets:
                    continue
                parsed_input, parsed_output, cache_read = parsed_buckets[model]
                entry = model_rows.setdefault(
                    model,
                    {
                        "input": 0,
                        "output": 0,
                        "cache": 0,
                        "cache_known": True,
                        "cost": 0.0,
                        "has_cost": False,
                        "source": None,
                    },
                )
                entry["input"] += parsed_input
                entry["output"] += parsed_output
                if cache_read is None:
                    entry["cache_known"] = False
                else:
                    entry["cache"] += cache_read
                if model_cost is not None:
                    entry["cost"] += model_cost
                    entry["has_cost"] = True
                    entry["source"] = merge_cost_source(entry["source"], model_source)

        if run_cost is not None and not bucket_has_cost:
            cost_source = merge_cost_source(cost_source, "estimated")

    models: list[UsageModel] = []
    if models_complete:
        for model in sorted(model_rows):
            entry = model_rows[model]
            models.append(
                UsageModel(
                    name=model,
                    input_tokens=entry["input"],
                    output_tokens=entry["output"],
                    cache_read_tokens=entry["cache"] if entry["cache_known"] else None,
                    total_tokens=entry["input"] + entry["output"],
                    cost=entry["cost"] if entry["has_cost"] else None,
                    cost_source=entry["source"],
                )
            )

    currency = _pricing_currency(pricing) if has_cost else None
    if has_cost and currency is None and has_real:
        currency = "CNY"
    return _LLMUsage(total_tokens, cost if has_cost else None, currency, cost_source, models)


def _aggregate_media_rows(rows: list[ExternalApiCallRow]) -> tuple[float | None, str | None, UsageCostSource, bool, int]:
    """→ (cost, currency, source, mixed_currencies, unpriced).

    ``cost`` is the sum of priced rows only (a known lower bound — never None
    just because some rows are unpriced); ``unpriced`` counts successful rows
    the estimator could not price, so partial pricing is visible rather than
    hidden behind ``media_cost=None``.
    """
    cost = 0.0
    has_cost = False
    currencies: set[str] = set()
    source: UsageCostSource = None
    unpriced = 0
    for row in rows:
        priced = evaluate_media_call(row.provider, row.method, row.status, row.metadata_json)
        if priced.source == "unpriced":
            unpriced += 1
        if priced.cost is None:
            continue
        cost += priced.cost
        has_cost = True
        currencies.add(priced.currency or "CNY")
        if priced.source in ("real", "estimated"):
            source = merge_cost_source(source, priced.source)
    mixed_currencies = len(currencies) > 1
    if not has_cost or mixed_currencies:
        return None, None, source, mixed_currencies, unpriced
    return cost, next(iter(currencies)), source, False, unpriced


def _combine_costs(
    llm_cost: float | None,
    llm_currency: str | None,
    media_cost: float | None,
    media_currency: str | None,
    media_mixed_currencies: bool = False,
) -> tuple[float | None, str | None]:
    if media_mixed_currencies:
        return None, None
    known = [(cost, currency) for cost, currency in ((llm_cost, llm_currency), (media_cost, media_currency)) if cost is not None]
    if not known:
        return None, None
    currencies = {currency for _, currency in known if currency is not None}
    if len(currencies) > 1:
        return None, None
    return sum(cost for cost, _ in known if cost is not None), next(iter(currencies), None)


@dataclass
class _AgentSubject:
    """现存会话或删除快照的统一视图：后面的计价与聚合不区分来源。"""

    id: str
    capability: UsageCapability
    title: str | None
    status: UsageStatus
    created_at: datetime
    updated_at: datetime | None
    deleted: bool


async def aggregate_agent_usage(session, *, user_id: str, window: UsageWindow, capability: UsageCapability | str) -> list[UsageTask]:
    if capability in {"ai_image", "ai_video"}:
        return []
    threads = (await session.execute(select(ThreadMetaRow).where(ThreadMetaRow.user_id == user_id))).scalars().all()
    # 删除快照只补「没有现存线程」的会话（现存行优先）；能力筛选按快照里记的能力。
    live_ids = {thread.thread_id for thread in threads}
    orphans = [row for row in await load_tombstones(session, user_id=user_id, kind="agent", capabilities=None if capability == "all" else [capability]) if row.subject_id not in live_ids]
    if not threads and not orphans:
        return []
    thread_ids = [*live_ids, *(row.subject_id for row in orphans)]
    run_user_filter = or_(RunRow.user_id == user_id, RunRow.user_id.is_(None))
    window_runs = (
        (
            await session.execute(
                select(RunRow).where(
                    RunRow.thread_id.in_(thread_ids),
                    RunRow.operation_kind == "run",
                    run_user_filter,
                    RunRow.created_at >= window.start_utc,
                    RunRow.created_at < window.end_utc,
                )
            )
        )
        .scalars()
        .all()
    )
    media_rows = (
        (
            await session.execute(
                select(ExternalApiCallRow).where(
                    ExternalApiCallRow.task_id.in_(thread_ids),
                    ExternalApiCallRow.user_id == user_id,
                    ExternalApiCallRow.created_at >= window.start_utc,
                    ExternalApiCallRow.created_at < window.end_utc,
                )
            )
        )
        .scalars()
        .all()
    )
    runs_by_thread: dict[str, list[RunRow]] = defaultdict(list)
    media_by_thread: dict[str, list[ExternalApiCallRow]] = defaultdict(list)
    for row in window_runs:
        runs_by_thread[row.thread_id].append(row)
    for row in media_rows:
        if row.task_id is not None:
            media_by_thread[row.task_id].append(row)

    included = {thread.thread_id for thread in threads if (window.start_utc <= (_as_utc(thread.created_at) or thread.created_at) < window.end_utc) or runs_by_thread.get(thread.thread_id) or media_by_thread.get(thread.thread_id)}
    # 快照对象没有「窗口内创建」这条入口：只有窗口内确有账本行才列出。
    included_orphans = [row for row in orphans if runs_by_thread.get(row.subject_id) or media_by_thread.get(row.subject_id)]
    if not included and not included_orphans:
        return []

    all_runs_by_thread: dict[str, list[RunRow]] = defaultdict(list)
    if included:
        latest_ranked = (
            select(
                RunRow,
                func.row_number().over(partition_by=RunRow.thread_id, order_by=(RunRow.created_at.desc(), RunRow.run_id.desc())).label("usage_run_rank"),
            )
            .where(
                RunRow.thread_id.in_(included),
                RunRow.operation_kind == "run",
                run_user_filter,
            )
            .subquery()
        )
        latest_run = aliased(RunRow, latest_ranked)
        latest_candidates = (await session.execute(select(latest_run).where(latest_ranked.c.usage_run_rank == 1).order_by(latest_ranked.c.created_at.desc(), latest_ranked.c.run_id.desc()))).scalars().all()
        for row in latest_candidates:
            all_runs_by_thread[row.thread_id].append(row)

    subjects: list[_AgentSubject] = []
    for thread in threads:
        if thread.thread_id not in included:
            continue
        task_capability = resolve_agent_capability(thread, [*all_runs_by_thread[thread.thread_id], *runs_by_thread[thread.thread_id]])
        if task_capability is None:
            continue
        if capability != "all" and task_capability != capability:
            continue
        latest = all_runs_by_thread[thread.thread_id][0] if all_runs_by_thread[thread.thread_id] else None
        display_name = thread.display_name if thread.display_name and thread.display_name != thread.thread_id else None
        subjects.append(
            _AgentSubject(
                id=thread.thread_id,
                capability=task_capability,
                title=display_name or (latest.first_human_message if latest else None),
                status=normalize_usage_status(latest.status if latest else thread.status),
                created_at=thread.created_at,
                updated_at=_as_utc(latest.updated_at if latest else thread.updated_at),
                deleted=False,
            )
        )
    subjects.extend(
        _AgentSubject(
            id=row.subject_id,
            capability=cast(UsageCapability, row.capability),
            title=row.title,
            status=normalize_usage_status(row.status),
            created_at=row.subject_created_at,
            updated_at=_as_utc(row.deleted_at),
            deleted=True,
        )
        for row in included_orphans
    )
    if not subjects:
        return []

    pricing = await _build_pricing_map(get_session_factory())
    if media_rows:
        await _load_media_pricing()

    tasks: list[UsageTaskEntry] = []
    for subject in subjects:
        llm = _aggregate_llm_rows(runs_by_thread[subject.id], pricing)
        media_cost, media_currency, media_source, media_mixed, unpriced_media = _aggregate_media_rows(media_by_thread[subject.id])
        cost, currency = _combine_costs(llm.cost, llm.currency, media_cost, media_currency, media_mixed)
        activity_candidates = [subject.created_at]
        activity_candidates.extend(row.created_at for row in runs_by_thread[subject.id])
        activity_candidates.extend(row.created_at for row in media_by_thread[subject.id])
        activity_at = max(_as_utc(value) or value for value in activity_candidates)
        tasks.append(
            UsageTaskEntry(
                task_key=f"agent:{subject.id}",
                source_id=subject.id,
                capability=subject.capability,
                title=subject.title,
                status=subject.status,
                cost=cost,
                llm_cost=llm.cost,
                media_cost=media_cost,
                unpriced_media_calls=unpriced_media,
                currency=currency,
                cost_source=merge_cost_source(llm.cost_source, media_source),
                total_tokens=llm.total_tokens,
                models=llm.models,
                updated_at=subject.updated_at,
                deleted=subject.deleted,
                activity_at=activity_at,
            )
        )
    tasks.sort(key=lambda task: (task.activity_at, task.task_key), reverse=True)
    return tasks
