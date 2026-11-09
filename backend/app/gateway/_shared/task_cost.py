"""Per-task cost computation (2026-08-13, 批次 C Phase B/C).

``compute_task_costs`` aggregates per-task spend from two sources:

- **LLM cost** (Phase B): ``runs`` rows with ``operation_kind="llm_bookkeeping"``
  whose ``thread_id`` carries the task id (``llm_usage_sink`` writes
  ``thread_id = ctx.task_id``). Bucketed tokens are priced via the console's
  pricing map (config.yaml fallback + ``model_pricing`` DB overlay,
  alias-aware — see ``routers/console.py``).
- **Media cost** (Phase C): ``external_api_calls`` rows with ``task_id`` set
  (same accounting ctx). Real-bill ``metadata_json.amount`` wins; otherwise
  ``_estimate_call_cost`` against ``media_pricing`` (failed calls are not
  estimated — same rule as console /external-calls P1-2).

**Currency assumption (P3-1)**: this deployment is single-currency (CNY).
The LLM currency comes from the first configured pricing entry
(``console._pricing_currency``); the media currency from the ``media_pricing``
entry (default CNY). If the two disagree for one task, that task's cost is
reported as ``None`` with a warning — never silently sum mixed currencies
(same posture as console.py's mixed-currency pricing guard).
（P3-3，2026-08-18 评估维持单币假设：LLM 侧价格唯一写路径是 admin PUT 全量替换
（``routers/model_pricing.py:118-164`` 先 delete 后全量插入，全仓唯一写入点；
console.py 只有 GET 路由——评估二审勘误定位），``validate_pricing_payload``
（``pricing_schema.py:153-161``）已拒混币 payload（422），同一快照内结构上不会
出现 LLM 混币；残余场景仅 config.yaml 与 DB 交叉配置错误，症状是显示币种
标错、无计费影响，不设按任务检测。）

**Historical rows**: ``external_api_calls`` written before the Phase C
migration have ``task_id IS NULL`` — those tasks show LLM cost only.
Forward-effective by design, not a bug.

Cost semantics: ``None`` means "cannot price this task" (no bookkeeping rows
or no pricing for its models) — the frontend renders it as "—", distinct from
a priced zero.

**Partial pricing (D1, 2026-08-14)**: when some of a task's runs price and
others do not (unknown model, missing pricing row), the reported cost is the
sum of the priced runs — never ``None``. Returning ``None`` for a partially
priced task would silently under-report more than the partial sum does.
"""

from __future__ import annotations

import logging
from typing import Any, NamedTuple

from sqlalchemy import select

from app.gateway._shared.media_constants import FREE_METHODS, METHOD_TO_MEDIA_TYPE
from app.gateway._shared.media_pricing_resolver import (
    _estimate_call_cost,
    _load_media_pricing,
    resolve_media_price,
)
from app.gateway.routers.console import _build_pricing_map, _pricing_currency, _run_cost
from deerflow.persistence.engine import get_session_factory
from deerflow.persistence.external_api_calls.model import ExternalApiCallRow
from deerflow.persistence.run.model import RunRow

logger = logging.getLogger(__name__)


class MediaCallCost(NamedTuple):
    """一行 external_api_calls 的计价结果（C5 共享行语义）。

    - ``cost``：真账（metadata.amount）优先，否则估算；None=不计费
      （免费行/失败行）或无法计价（``source="unpriced"``）。
    - ``currency``：价目行币种；真账无价目行时按部署单币假设记 "CNY"
      （与历史行为一致）。
    - ``source``：``real`` / ``estimated`` / ``unpriced`` / ``None``（跳过）。
    """

    cost: float | None
    currency: str | None
    source: str | None


def evaluate_media_call(provider: str | None, method: str | None, status: str | None, metadata: Any) -> MediaCallCost:
    """单行媒体调用的计价语义——三条聚合链（按任务 / 按窗口 stats·usage / 外部
    调用面板）共用这一处，禁止各自内联第二份（C5 收口）。

    真账优先（供应商 amount 为准）；估算走 ``resolve_media_price`` 单点
    （C2 模型级→provider 级）；失败调用不估算（P1-2）；免费方法不计费。
    """
    meta = metadata if isinstance(metadata, dict) else {}
    media_type = METHOD_TO_MEDIA_TYPE.get(method or "", "unknown")
    entry = resolve_media_price(provider, meta.get("model"), media_type)
    entry_currency = str(entry.get("currency") or "CNY").strip().upper() if entry else "CNY"

    raw_amount = meta.get("amount")
    if raw_amount is not None:
        try:
            return MediaCallCost(float(raw_amount), entry_currency, "real")
        except (TypeError, ValueError):
            pass  # 落到估算分支（amount 不可读按无真账处理）
    if method in FREE_METHODS:
        return MediaCallCost(None, None, None)
    if status not in ("ok", "success"):
        # P1-2: only estimate successful calls — failed calls likely didn't
        # produce a bill.
        return MediaCallCost(None, None, None)
    cost = _estimate_call_cost(meta, method or "", entry)
    if cost is None:
        return MediaCallCost(None, None, "unpriced")
    return MediaCallCost(cost, entry_currency, "estimated")


async def aggregate_media_cost(
    session,
    *,
    scope: Any = None,
    window_start=None,
    include_estimates: bool = True,
    include_tts: bool = True,
) -> dict:
    """按窗口/scope 聚合媒体成本（C5 共享聚合——console stats/usage 与外部调用
    面板统一改调，禁止出现第四条聚合链）。

    - ``scope``：必传维度，与 console ``_Scope`` 对齐（duck-typed：读
      ``filter_user_id``，None=平台全量）；调用方不得自行再包一层用户过滤。
    - ``window_start``：datetime | None（None=全时段）。
    - ``include_estimates``：False=只算真账 amount 行（旧 /usage 口径，对账用）。
    - ``include_tts``：False=排除 TTS 行（旧 /usage 口径）。

    返回 ``{by_provider, total_real, total_estimated, total, unpriced_calls,
    currencies}``；``by_provider[p] = {calls, cost_real, cost_estimated,
    has_real, has_estimated}``。
    """
    stmt = select(ExternalApiCallRow)
    if window_start is not None:
        stmt = stmt.where(ExternalApiCallRow.created_at >= window_start)
    filter_user_id = getattr(scope, "filter_user_id", None) if scope is not None else None
    if filter_user_id is not None:
        stmt = stmt.where(ExternalApiCallRow.user_id == filter_user_id)
    if not include_tts:
        stmt = stmt.where(ExternalApiCallRow.method.notin_(["synthesize_sse", "voice_clone_upload"]))

    rows = (await session.execute(stmt)).scalars().all()

    # 估算路径依赖进程缓存——先 warm（真账行不受影响）。
    if include_estimates:
        await _load_media_pricing()

    by_provider: dict[str, dict] = {}
    total_real = 0.0
    total_estimated = 0.0
    unpriced_calls = 0
    currencies: set[str] = set()

    for row in rows:
        pdata = by_provider.setdefault(row.provider, {"calls": 0, "cost_real": 0.0, "cost_estimated": 0.0, "has_real": False, "has_estimated": False})
        pdata["calls"] += 1
        result = evaluate_media_call(row.provider, row.method, row.status, row.metadata_json)
        if result.source == "real":
            pdata["cost_real"] += result.cost or 0.0
            pdata["has_real"] = True
            total_real += result.cost or 0.0
            if result.currency:
                currencies.add(result.currency)
        elif result.source == "estimated" and include_estimates:
            pdata["cost_estimated"] += result.cost or 0.0
            pdata["has_estimated"] = True
            total_estimated += result.cost or 0.0
            if result.currency:
                currencies.add(result.currency)
        elif result.source == "unpriced":
            unpriced_calls += 1

    return {
        "by_provider": by_provider,
        "total_real": round(total_real, 6),
        "total_estimated": round(total_estimated, 6),
        "total": round(total_real + (total_estimated if include_estimates else 0.0), 6),
        "unpriced_calls": unpriced_calls,
        "currencies": sorted(currencies),
    }


async def compute_task_costs(
    session,
    task_ids: list[str],
) -> dict[str, tuple[float | None, str | None, str | None]]:
    """Per-task cost (LLM + media) for ``task_ids`` — batched queries, no N+1.

    Returns ``{task_id: (cost, currency, amount_source)}``（C4 来源标注：
    real=全真账 / estimated=全估算 / mixed=两者皆有 / None=未计价）。
    Tasks with no priced rows get ``(None, None, None)``. Mixed-currency tasks
    get ``(None, None, None)`` + a warning. Failures degrade to all-``None``
    — cost is auxiliary display data and must never break task listing.
    """
    result: dict[str, tuple[float | None, str | None, str | None]] = {tid: (None, None, None) for tid in task_ids}
    if not task_ids:
        return result
    try:
        sf = get_session_factory()
        pricing = await _build_pricing_map(sf)

        # ── LLM cost: runs.thread_id = task_id (llm_bookkeeping rows) ──────
        llm_rows = (
            await session.execute(
                select(
                    RunRow.thread_id,
                    RunRow.model_name,
                    RunRow.total_input_tokens,
                    RunRow.total_output_tokens,
                    RunRow.token_usage_by_model,
                ).where(
                    RunRow.operation_kind == "llm_bookkeeping",
                    RunRow.thread_id.in_(task_ids),
                )
            )
        ).all()
        llm_totals: dict[str, float] = {}
        llm_priced: set[str] = set()
        # C4：来源标注——LLM 分量按 token×价目估算，天然属于 estimated。
        task_sources: dict[str, set[str]] = {}
        for thread_id, model_name, input_tokens, output_tokens, usage_map in llm_rows:
            cost = _run_cost(
                pricing,
                model_name=model_name,
                total_input_tokens=input_tokens,
                total_output_tokens=output_tokens,
                token_usage_by_model=usage_map,
            )
            if cost is None:
                continue
            llm_totals[thread_id] = llm_totals.get(thread_id, 0.0) + cost
            llm_priced.add(thread_id)
            task_sources.setdefault(thread_id, set()).add("estimated")
        llm_currency = _pricing_currency(pricing)

        # ── Media cost: external_api_calls.task_id ─────────────────────────
        # C2: 先 warm 进程缓存；C5: 行级计价语义收口到 evaluate_media_call
        # （与 console stats/usage/外部调用面板同源，禁止第二份内联）。
        await _load_media_pricing()
        media_rows = (
            await session.execute(
                select(
                    ExternalApiCallRow.task_id,
                    ExternalApiCallRow.provider,
                    ExternalApiCallRow.method,
                    ExternalApiCallRow.status,
                    ExternalApiCallRow.metadata_json,
                ).where(ExternalApiCallRow.task_id.in_(task_ids))
            )
        ).all()
        media_totals: dict[str, float] = {}
        media_currencies: dict[str, set[str]] = {}
        for task_id, provider, method, status, metadata in media_rows:
            priced = evaluate_media_call(provider, method, status, metadata)
            if priced.cost is None:
                continue
            if priced.source in ("real", "estimated"):
                task_sources.setdefault(task_id, set()).add(priced.source)
            media_totals[task_id] = media_totals.get(task_id, 0.0) + priced.cost
            # D2 (2026-08-14): collect ALL currencies seen on the task's media
            # rows — the old last-row-wins overwrite hid media-media mixing.
            media_currencies.setdefault(task_id, set()).add(priced.currency or "CNY")

        # ── Merge; P3-1 mixed-currency guard ───────────────────────────────
        for tid in task_ids:
            has_llm = tid in llm_priced
            has_media = tid in media_totals
            if not has_llm and not has_media:
                continue
            mcurrencies = media_currencies.get(tid, set())
            if len(mcurrencies) > 1:
                logger.warning(
                    "task_cost: task %s mixes currencies across media rows (%s); reporting cost as None rather than summing mixed currencies",
                    tid,
                    ", ".join(sorted(mcurrencies)),
                )
                continue
            media_cur = next(iter(mcurrencies)) if mcurrencies else None
            if has_llm and has_media and llm_currency and media_cur and llm_currency != media_cur:
                logger.warning(
                    "task_cost: task %s mixes currencies (LLM %s vs media %s); reporting cost as None rather than summing mixed currencies",
                    tid,
                    llm_currency,
                    media_cur,
                )
                continue
            total = round(llm_totals.get(tid, 0.0) + media_totals.get(tid, 0.0), 6)
            currency = (llm_currency if has_llm else None) or (media_cur if has_media else None)
            srcs = task_sources.get(tid) or set()
            source = None if not srcs else ("mixed" if len(srcs) > 1 else next(iter(srcs)))
            result[tid] = (total, currency, source)
        return result
    except Exception:
        logger.warning("compute_task_costs failed", exc_info=True)
        return result


async def attach_task_costs(briefs: list) -> None:
    """Batch-compute costs for a list of TaskBrief-like objects and attach
    ``cost``/``currency`` in place (post-hoc assignment — TaskRow has no cost
    column, so this must never feed ``from_attributes`` validation).

    No-op when there is no SQL session factory (memory backend) or the list
    is empty.
    """
    if not briefs:
        return
    sf = get_session_factory()
    if sf is None:
        return
    task_ids = [getattr(b, "id") for b in briefs]
    async with sf() as session:
        costs = await compute_task_costs(session, task_ids)
    for b in briefs:
        cost, currency, amount_source = costs.get(getattr(b, "id"), (None, None, None))
        b.cost = cost
        b.currency = currency
        b.amount_source = amount_source
