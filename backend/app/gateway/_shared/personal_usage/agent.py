from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from sqlalchemy import or_, select

from app.gateway._shared.media_pricing_resolver import _load_media_pricing
from app.gateway._shared.personal_usage.types import (
    UsageCapability,
    UsageCostSource,
    UsageModel,
    UsageTask,
    UsageTaskEntry,
    UsageWindow,
    canonical_agent_capability,
    merge_cost_source,
    normalize_usage_status,
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
    except (TypeError, ValueError):
        return None
    return count if count >= 0 else None


def _real_amount(usage: dict) -> tuple[bool, float | None]:
    if usage.get("amount") is None:
        return False, None
    try:
        return True, float(usage["amount"])
    except (TypeError, ValueError):
        return False, None


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
            token_usage_by_model=usage_map,
        )
        if run_cost is not None:
            cost += run_cost
            has_cost = True

        bucket_has_cost = False
        if isinstance(usage_map, dict):
            for model, usage in usage_map.items():
                if not isinstance(usage, dict):
                    continue
                input_tokens = int(usage.get("input_tokens") or 0)
                output_tokens = int(usage.get("output_tokens") or 0)
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
                        model_cost = _token_cost(input_tokens, output_tokens, price, int(usage.get("cache_read_tokens") or 0))
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


def _aggregate_media_rows(rows: list[ExternalApiCallRow]) -> tuple[float | None, str | None, UsageCostSource, bool]:
    cost = 0.0
    has_cost = False
    currencies: set[str] = set()
    source: UsageCostSource = None
    for row in rows:
        priced = evaluate_media_call(row.provider, row.method, row.status, row.metadata_json)
        if priced.cost is None:
            continue
        cost += priced.cost
        has_cost = True
        currencies.add(priced.currency or "CNY")
        if priced.source in ("real", "estimated"):
            source = merge_cost_source(source, priced.source)
    mixed_currencies = len(currencies) > 1
    if not has_cost or mixed_currencies:
        return None, None, source, mixed_currencies
    return cost, next(iter(currencies)), source, False


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


async def aggregate_agent_usage(session, *, user_id: str, window: UsageWindow, capability: UsageCapability | str) -> list[UsageTask]:
    threads = (await session.execute(select(ThreadMetaRow).where(ThreadMetaRow.user_id == user_id))).scalars().all()
    if not threads or capability in {"ai_image", "ai_video"}:
        return []
    thread_ids = [thread.thread_id for thread in threads]
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
    if not included:
        return []

    latest_candidates = (
        (
            await session.execute(
                select(RunRow)
                .where(
                    RunRow.thread_id.in_(included),
                    RunRow.operation_kind == "run",
                    run_user_filter,
                )
                .order_by(RunRow.created_at.desc(), RunRow.run_id.desc())
            )
        )
        .scalars()
        .all()
    )
    all_runs_by_thread: dict[str, list[RunRow]] = defaultdict(list)
    for row in latest_candidates:
        all_runs_by_thread[row.thread_id].append(row)

    selected_threads = []
    for thread in threads:
        if thread.thread_id not in included:
            continue
        metadata = thread.metadata_json if isinstance(thread.metadata_json, dict) else {}
        candidates = [thread.assistant_id, metadata.get("agent_name"), metadata.get("assistant_id")]
        candidates.extend(row.assistant_id for row in all_runs_by_thread[thread.thread_id])
        task_capability = canonical_agent_capability(*candidates)
        if task_capability is None or task_capability in {"ai_image", "ai_video"}:
            continue
        if capability != "all" and task_capability != capability:
            continue
        selected_threads.append((thread, task_capability))
    if not selected_threads:
        return []

    pricing = await _build_pricing_map(get_session_factory())
    if media_rows:
        await _load_media_pricing()

    tasks: list[UsageTaskEntry] = []
    for thread, task_capability in selected_threads:
        llm = _aggregate_llm_rows(runs_by_thread[thread.thread_id], pricing)
        media_cost, media_currency, media_source, media_mixed = _aggregate_media_rows(media_by_thread[thread.thread_id])
        cost, currency = _combine_costs(llm.cost, llm.currency, media_cost, media_currency, media_mixed)
        latest_run = all_runs_by_thread[thread.thread_id][0] if all_runs_by_thread[thread.thread_id] else None
        activity_candidates = [thread.created_at]
        activity_candidates.extend(row.created_at for row in runs_by_thread[thread.thread_id])
        activity_candidates.extend(row.created_at for row in media_by_thread[thread.thread_id])
        activity_at = max(_as_utc(value) or value for value in activity_candidates)
        title = thread.display_name or (latest_run.first_human_message if latest_run else None) or thread.thread_id
        tasks.append(
            UsageTaskEntry(
                task_key=f"agent:{thread.thread_id}",
                source_id=thread.thread_id,
                capability=task_capability,
                title=title,
                status=normalize_usage_status(latest_run.status if latest_run else thread.status),
                cost=cost,
                llm_cost=llm.cost,
                media_cost=media_cost,
                currency=currency,
                cost_source=merge_cost_source(llm.cost_source, media_source),
                total_tokens=llm.total_tokens,
                models=llm.models,
                updated_at=_as_utc(latest_run.updated_at if latest_run else thread.updated_at),
                activity_at=activity_at,
            )
        )
    tasks.sort(key=lambda task: (task.activity_at, task.task_key), reverse=True)
    return tasks
