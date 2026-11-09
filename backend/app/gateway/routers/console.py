"""Read-only operations-console endpoints.

Aggregates observability data across threads: run history, token spend over
time, and per-model cost breakdown — the data layer for the operations
dashboard at ``/console``.

This is a reporting layer, not a runtime path: it issues short-lived read-only
queries against the harness-owned ``runs`` / ``threads_meta`` tables instead of
widening the runtime ``RunStore`` surface. Requires a SQL database backend
(``database.backend: sqlite | postgres``); returns 503 on the memory backend,
which persists no run history to report on.

Visibility model (local addition — upstream is per-user only)
------------------------------------------------------------

Upstream scopes every query to the calling user and has no notion of a
platform-wide view. Operating this deployment needs both, so scope resolution
goes through :func:`_resolve_scope`:

- **Regular user** — always filtered to their own ``user_id``. Identical to
  upstream behavior. Passing ``user_id`` for somebody else is **403**, not a
  silent fallback to their own data: a silent downgrade would make a
  cross-user probe look like it succeeded.
- **Admin** (``UserRow.system_role == "admin"``, the same flag
  ``routers/auth.py`` already gates admin endpoints on) — sees every user by
  default, and can pass ``user_id`` to drill into one.

Admin status is read from the request's already-resolved ``AuthContext``
(stamped by ``AuthMiddleware``), so this adds no second auth pipeline and no
new role vocabulary.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, time, timedelta
from typing import NamedTuple

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.gateway._shared.media_constants import FREE_METHODS, METHOD_TO_MEDIA_TYPE
from app.gateway.authz import require_permission
from app.gateway.deps import get_current_user
from deerflow.config import get_app_config
from deerflow.persistence.engine import get_session_factory
from deerflow.persistence.external_api_calls.model import ExternalApiCallRow
from deerflow.persistence.model_pricing.model import ModelPricingRow
from deerflow.persistence.run.model import RunRow
from deerflow.persistence.thread_meta.model import ThreadMetaRow
from deerflow.persistence.user.model import UserRow

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/console", tags=["console"])

_ACTIVE_STATUSES = ("pending", "running")
_FAILED_STATUSES = ("error", "timeout")

# Cap the error excerpt in list responses; the full text stays on the run row.
_ERROR_EXCERPT_CHARS = 300


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class ConsoleScope(BaseModel):
    """Who the caller is and how wide the returned numbers are."""

    is_admin: bool = Field(..., description="True when the caller may query across users")
    all_users: bool = Field(..., description="True when the response covers every user rather than one")
    user_id: str | None = Field(default=None, description="The user the response is scoped to, if any")


class ConsoleCostBreakdown(BaseModel):
    """LLM / media cost split（C3）——对账用，与 total_cost_all 同窗口同 scope。"""

    llm: float | None = Field(default=None, description="LLM spend (runs × model_pricing)；None=无定价配置")
    media: float = Field(default=0.0, description="Media spend (external_api_calls；真账优先+估算+TTS)")


class ConsoleStatsResponse(BaseModel):
    """Headline counters for the console dashboard."""

    total_runs: int = Field(..., description="All recorded runs in scope")
    active_runs: int = Field(..., description="Runs currently pending or running")
    failed_runs: int = Field(..., description="Runs that ended in error or timeout")
    # No ``total_agents`` / ``total_threads`` here by design (A4, 2026-08-04).
    # ``total_agents`` leaked: ``list_custom_agents()`` takes no arguments and
    # scans one global agents dir, so it reported the platform-wide count to
    # every caller while its description claimed "visible to the caller".
    # ``total_threads`` was correctly scoped but never rendered. Neither was
    # used by the frontend, so both were removed rather than reimplemented.
    # If a per-user agent count is ever wanted, filter through
    # ``agents.py``'s ``_can_see_agent`` — that is the promised semantic.
    total_tokens: int = Field(..., description="Tokens consumed across all recorded runs in scope")
    total_cost: float | None = Field(default=None, description="Estimated spend across priced runs; null when no models[*].pricing is configured")
    # C3（2026-08-27 成本可信化）：全链路口径增量字段——老 total_cost（LLM-only）
    # 不动（A12 回滚零成本），前端总花费卡片改读 total_cost_all。
    total_cost_all: float | None = Field(default=None, description="LLM + media spend for the window (含媒体生成；真账优先+估算)")
    cost_breakdown: ConsoleCostBreakdown = Field(default_factory=lambda: ConsoleCostBreakdown(), description="LLM / media split for对账")
    unpriced_calls: int = Field(default=0, description="C5: billable media calls with no pricing row (visible, not silently zero)")
    currency: str | None = Field(default=None, description="Display currency taken from the first configured pricing entry")
    over_threshold_runs: int = Field(default=0, description="Runs whose token usage crossed the attention threshold (T20)")
    scope: ConsoleScope


class ConsoleRunItem(BaseModel):
    """One run in the cross-thread run listing."""

    run_id: str
    thread_id: str
    thread_title: str | None = Field(default=None, description="Display name from threads_meta, if tracked")
    assistant_id: str | None = None
    user_id: str | None = Field(default=None, description="Owner; only populated for admin callers")
    user_email: str | None = Field(default=None, description="Owner email; only populated for admin callers")
    status: str
    model_name: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    duration_seconds: float | None = Field(default=None, description="Wall-clock duration; live elapsed time for active runs")
    total_tokens: int = 0
    subagent_tokens: int = 0
    message_count: int = 0
    cost: float | None = Field(default=None, description="Estimated spend for this run; null when its models are unpriced")
    error: str | None = Field(default=None, description="Error excerpt for failed runs")
    stop_reason: str | None = Field(default=None, description="Why the run ended, when a middleware capped it")
    over_threshold: bool = Field(default=False, description="Token usage crossed the attention threshold (T20)")


class ConsoleRunsResponse(BaseModel):
    """Paginated cross-thread run listing."""

    runs: list[ConsoleRunItem]
    has_more: bool
    scope: ConsoleScope


class ConsoleUsageDay(BaseModel):
    """Token usage aggregated over one local-time day."""

    date: str = Field(..., description="Local date (YYYY-MM-DD) per the requested tz offset")
    total_tokens: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    runs: int = 0
    # B1: split chat vs llm_bookkeeping tokens so the frontend can render a
    # stacked bar. chat_tokens is conversation runs; llm_tokens is
    # video-gen/vimax/writer etc. total_tokens = chat_tokens + llm_tokens.
    chat_tokens: int = Field(default=0, description="Tokens from chat runs (operation_kind=run)")
    llm_tokens: int = Field(default=0, description="Tokens from llm_bookkeeping rows (video-gen, vimax, writer, etc.)")
    llm_calls: int = Field(default=0, description="Number of llm_bookkeeping calls on this day")
    # Nullable for the same reason as the window's ``total_cost``: an unpriced
    # deployment must report "unknown", not 0.0. Upstream types this as a
    # non-null float defaulting to 0.0, which makes every day of an unpriced
    # window claim the work was free while the window total correctly says
    # null — one response contradicting itself.
    cost: float | None = Field(default=None, description="Estimated spend for the day across priced runs; null when its runs are unpriced")


class ConsoleUsageModelBreakdown(BaseModel):
    """Token usage attributed to one model."""

    tokens: int = 0
    runs: int = Field(default=0, description="Runs that used this model (non-exclusive)")
    cost: float | None = Field(default=None, description="Estimated spend for this model; null when unpriced")
    input_tokens: int = Field(default=0, description="Input tokens attributed to this model")
    output_tokens: int = Field(default=0, description="Output tokens attributed to this model")
    cache_read_tokens: int = Field(default=0, description="Prompt-cache-hit input tokens attributed to this model")
    llm_calls: int = Field(default=0, description="Non-conversation LLM calls (llm_bookkeeping) that used this model; does not increment `runs`")


class ConsoleUsageResponse(BaseModel):
    """Daily token-usage series plus per-model breakdown for the window."""

    days: list[ConsoleUsageDay]
    by_model: dict[str, ConsoleUsageModelBreakdown]
    total_tokens: int
    total_runs: int
    # B1: split totals for the stacked-bar frontend.
    total_chat_tokens: int = Field(default=0, description="Tokens from chat runs only (operation_kind=run)")
    total_llm_tokens: int = Field(default=0, description="Tokens from llm_bookkeeping rows (video-gen, vimax, writer, etc.)")
    total_llm_calls: int = Field(default=0, description="Total non-conversation LLM calls in the window")
    total_cost: float | None = Field(default=None, description="Estimated spend for the window; null when no pricing is configured")
    # C3：全链路口径（与 /stats 同字段同源）；C5：媒体聚合统一走共享 aggregate_media_cost。
    total_cost_all: float | None = Field(default=None, description="LLM + media spend for the window (含媒体生成；真账优先+估算+TTS)")
    cost_breakdown: ConsoleCostBreakdown = Field(default_factory=lambda: ConsoleCostBreakdown(), description="LLM / media split for对账")
    unpriced_calls: int = Field(default=0, description="C5: billable media calls with no pricing row")
    media_cost_by_provider: dict[str, float] = Field(default_factory=dict, description="Media API cost by provider — real bills first, estimation for rows without one (C5 全口径，旧口径只算 amount 行)")
    media_cost_by_provider_split: dict[str, dict[str, float]] = Field(default_factory=dict, description="C4: per-provider {real, estimated} split — feeds the frontend's real-vs-estimated coloring and the 估 marker")
    currency: str | None = Field(default=None, description="Display currency taken from the first configured pricing entry")
    subagent_tokens: int = Field(default=0, description="Portion of the window's tokens spent inside subagents")
    scope: ConsoleScope


class ConsoleUserItem(BaseModel):
    """One selectable user in the admin drill-down picker."""

    user_id: str
    email: str | None = None
    system_role: str | None = None
    total_runs: int = 0
    total_tokens: int = 0


class ConsoleUsersResponse(BaseModel):
    users: list[ConsoleUserItem]


# C2: Non-conversation LLM usage reporting


class ConsoleLlmUsageDomain(BaseModel):
    """Aggregated LLM usage for one domain (e.g. video_gen, vimax)."""

    domain: str = Field(..., description="Domain identifier from metadata_json.domain")
    calls: int = Field(..., description="Total LLM calls in this domain")
    failed_calls: int = Field(..., description="Calls with status != 'success'")
    tokens: int = Field(..., description="Total tokens consumed")
    input_tokens: int = Field(default=0)
    output_tokens: int = Field(default=0)
    cost: float | None = Field(default=None, description="Estimated spend; null when unpriced")


class ConsoleLlmUsageResponse(BaseModel):
    domains: list[ConsoleLlmUsageDomain]
    total_cost: float | None = None
    currency: str | None = None
    scope: ConsoleScope


# F2: External API call counts


class ConsoleExternalCallsResponse(BaseModel):
    """Aggregated external API call counts and costs."""

    total_calls: int
    by_provider: list[dict] = Field(
        default_factory=list,
        description="[{provider, calls, cost_real, cost_estimated, cost, priced, unit}]",
    )
    by_date: list[dict] = Field(
        default_factory=list,
        description="[{date, calls, cost_real, cost_estimated}]",
    )
    by_type: list[dict] = Field(
        default_factory=list,
        description="[{media_type, calls, cost}]",
    )
    unpriced_calls: int = 0
    scope: ConsoleScope


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _session_factory_or_503():
    sf = get_session_factory()
    if sf is None:
        raise HTTPException(
            status_code=503,
            detail="Console requires a SQL database backend; set database.backend to sqlite or postgres in config.yaml.",
        )
    return sf


def _as_utc(dt: datetime | None) -> datetime | None:
    """Normalize DB timestamps: SQLite round-trips them naive, Postgres aware."""
    if dt is None:
        return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt


# ---------------------------------------------------------------------------
# C3: Media pricing cache — moved to the single resolver (C2 收口, 2026-08-27
# 成本可信化批次). Re-exported here so every existing importer (app.py warmup,
# media_pricing.py cache invalidation, budget gates' ``_load_media_pricing_sync``)
# keeps working unchanged; new consumers should import the resolver directly.
# ---------------------------------------------------------------------------

from app.gateway._shared.media_pricing_resolver import (  # noqa: E402,F401
    _estimate_call_cost,
    _load_media_pricing,
    _load_media_pricing_sync,
    invalidate_media_pricing_cache,
    resolve_media_price,
)


def _auth_user(request: Request):
    """The caller resolved by ``AuthMiddleware`` / ``@require_permission``.

    Both the admin check and the scope filter read identity from here, so they
    can never disagree — deriving the role from ``request.state.auth`` but the
    id from the cookie would let the two drift apart.
    """
    auth = getattr(request.state, "auth", None)
    return getattr(auth, "user", None) if auth is not None else None


def _is_admin(request: Request) -> bool:
    """True when the caller holds the platform admin role.

    Uses the same ``system_role == "admin"`` flag ``routers/auth.py`` already
    gates its admin endpoints on, rather than introducing a second role
    vocabulary.
    """
    return getattr(_auth_user(request), "system_role", None) == "admin"


class _Scope(NamedTuple):
    """Resolved visibility for one console request."""

    is_admin: bool
    # None means "no user filter" (admin platform-wide view). A string means
    # filter to exactly that user.
    filter_user_id: str | None

    @property
    def all_users(self) -> bool:
        return self.filter_user_id is None

    def response(self) -> ConsoleScope:
        return ConsoleScope(is_admin=self.is_admin, all_users=self.all_users, user_id=self.filter_user_id)

    def apply(self, stmt, column):
        """Add the user filter to *stmt* unless this is a platform-wide view."""
        return stmt if self.filter_user_id is None else stmt.where(column == self.filter_user_id)


async def _resolve_scope(request: Request, requested_user_id: str | None) -> _Scope:
    """Decide which rows this caller may see.

    - Admin, no ``user_id`` → platform-wide (no filter).
    - Admin with ``user_id`` → that user only.
    - Non-admin → own rows only. Asking for another user's ``user_id`` raises
      403 rather than silently returning their own data.
    """
    user = _auth_user(request)
    # Fall back to the cookie resolver only when the middleware left no context
    # (direct-call unit tests, no-auth mode).
    caller_id = str(user.id) if user is not None else await get_current_user(request)
    is_admin = _is_admin(request)
    requested = requested_user_id.strip() if requested_user_id else None

    if is_admin:
        return _Scope(is_admin=True, filter_user_id=requested or None)

    if requested and requested != caller_id:
        raise HTTPException(status_code=403, detail="Only an admin may query another user's usage.")

    return _Scope(is_admin=False, filter_user_id=caller_id)


# ---------------------------------------------------------------------------
# Attention threshold (T20) — recording only, never enforcement
# ---------------------------------------------------------------------------


def _attention_threshold() -> int:
    """Token count above which a run is flagged as worth a look.

    Sourced from ``token_usage.attention_threshold_tokens``. Zero / missing
    disables the flag. See ``TokenUsageConfig`` for how the default was
    derived from this deployment's real run distribution.
    """
    try:
        cfg = getattr(get_app_config(), "token_usage", None)
        return max(int(getattr(cfg, "attention_threshold_tokens", 0) or 0), 0)
    except Exception:  # pragma: no cover - defensive: the console must not 500 on config trouble
        logger.warning("console: failed to read token attention threshold from config", exc_info=True)
        return 0


# ---------------------------------------------------------------------------
# Pricing — real spend estimation
# ---------------------------------------------------------------------------


class _ModelPricing(NamedTuple):
    input_per_million: float
    output_per_million: float
    currency: str
    # Price for prompt-cache-hit input tokens. None → hits are billed at the
    # full input price (conservative upper bound for providers that don't
    # discount, or when the operator hasn't configured the hit price).
    input_cache_hit_per_million: float | None = None


def _build_yaml_pricing_map() -> dict[str, _ModelPricing]:
    """Collect per-model prices from ``models[*].pricing`` in config.yaml.

    This is the **fallback** layer; ``_build_pricing_map`` overlays the
    ``model_pricing`` table on top. The yaml stays useful as the shipped
    default and as an offline bootstrap for a deployment whose table is
    still empty.

    ``ModelConfig`` allows extra fields, so operators can annotate each model
    with e.g. ``pricing: {currency: CNY, input_per_million: 8,
    output_per_million: 32, input_cache_hit_per_million: 0.8}`` without any
    schema change. Entries are keyed by both the config ``name`` and the
    provider ``model`` id (plus lowercase variants), because
    ``token_usage_by_model`` buckets carry the provider-reported model name.
    """
    try:
        models = get_app_config().models
    except Exception:  # pragma: no cover - defensive: cost display must not break the console
        logger.warning("console: failed to load model pricing from config", exc_info=True)
        return {}

    pricing: dict[str, _ModelPricing] = {}
    pricing_currency: str | None = None
    pricing_currency_model: str | None = None
    for model_cfg in models or []:
        raw = getattr(model_cfg, "pricing", None)
        if not isinstance(raw, dict):
            continue
        try:
            input_price = float(raw.get("input_per_million") or 0)
            output_price = float(raw.get("output_per_million") or 0)
            raw_hit_price = raw.get("input_cache_hit_per_million")
            cache_hit_price = float(raw_hit_price) if raw_hit_price is not None else None
        except (TypeError, ValueError):
            logger.warning("console: ignoring malformed pricing on model %s", model_cfg.name)
            continue
        if input_price <= 0 and output_price <= 0:
            continue
        model_currency = str(raw.get("currency") or "USD").strip().upper() or "USD"
        if pricing_currency is None:
            pricing_currency = model_currency
            pricing_currency_model = model_cfg.name
        elif model_currency != pricing_currency:
            logger.warning(
                "console: disabling cost reporting because model pricing mixes currencies (%s on %s, %s on %s)",
                pricing_currency,
                pricing_currency_model,
                model_currency,
                model_cfg.name,
            )
            return {}
        entry = _ModelPricing(input_price, output_price, model_currency, cache_hit_price)
        for key in (model_cfg.name, getattr(model_cfg, "model", None)):
            if key:
                pricing.setdefault(key, entry)
                pricing.setdefault(key.lower(), entry)
    return pricing


def _model_key_aliases() -> dict[str, set[str]]:
    """Map any configured model key (config ``name`` or provider ``model`` id)
    to its whole alias group (both spellings, plus lowercase variants).

    ``token_usage_by_model`` buckets carry the provider-reported model id while
    operators write ``model_pricing`` rows under the config ``name`` — this
    bridges the two so a DB row written under either spelling prices runs
    bucketed under the other (2026-08-13 P0: Ark-direct models like doubao
    showed no spend because only the config name was registered).

    Collision rule (C3, 2026-08-14): when the same key lands in two different
    model configs' groups (e.g. A.name == B.model), silently merging the
    groups would let a row written for one model price the other. The key is
    instead dropped from every group — explicit abstention, plus a warning
    naming the colliding configs.
    """
    try:
        models = get_app_config().models
    except Exception:  # pragma: no cover - defensive: cost display must not break the console
        logger.warning("console: failed to load model alias groups from config", exc_info=True)
        return {}
    # Two-phase collect: key -> {owner config name: group}. A key claimed by
    # more than one config collides.
    owners: dict[str, dict[str, set[str]]] = {}
    for cfg_i, model_cfg in enumerate(models or []):
        cfg_name = getattr(model_cfg, "name", None) or getattr(model_cfg, "model", None) or "<unnamed>"
        keys = {getattr(model_cfg, "name", None), getattr(model_cfg, "model", None)}
        keys = {k for k in keys if k}
        keys |= {k.lower() for k in keys}
        for key in keys:
            # P2-3 (2026-08-18): 内层键用「cfg_name#下标」而非 cfg_name——同名不同
            # model 的两个 config 会互相覆盖成一个 entry，碰撞漏检 → 写在该 name 的
            # DB 行把价格静默扩张到后写入者的厂商 id（正是本检测要防的串价）。
            # 下标保证同名 config 各自成组；cfg_name 前缀保证 warning 可读；
            # 键仍是 str——评估二审指出 int 裸下标会让 warning 处
            # `", ".join(sorted(groups))` 必 TypeError。
            owners.setdefault(key, {})[f"{cfg_name}#{cfg_i}"] = keys
    collisions = {key: list(groups) for key, groups in owners.items() if len(groups) > 1}
    for key, groups in collisions.items():
        logger.warning(
            "console: model key %r appears in multiple model configs (%s); excluding it from alias expansion",
            key,
            ", ".join(sorted(groups)),
        )
    aliases: dict[str, set[str]] = {}
    for key, groups in owners.items():
        if key in collisions:
            continue
        group = next(iter(groups.values()))
        aliases[key] = {k for k in group if k not in collisions}
    return aliases


async def _build_pricing_map(session_factory) -> dict[str, _ModelPricing]:
    """Effective prices: the ``model_pricing`` table over the yaml fallback.

    Merging happens **per model and only on the pricing dimension** — no
    other ``ModelConfig`` field is touched, so this cannot become a second,
    diverging model registry.

    The DB layer is what makes prices editable at runtime: Gateway
    dependencies read a startup config snapshot, so a ``config.yaml`` edit
    would not take effect until a restart (and the file is mounted read-only
    in the deployment anyway).

    A DB read failure degrades to the yaml map rather than to "no pricing" —
    reporting spend as ``—`` because of a transient DB hiccup would look
    exactly like "nobody has configured prices".
    """
    pricing = _build_yaml_pricing_map()

    if session_factory is None:
        return pricing

    try:
        async with session_factory() as session:
            # C1 (2026-08-14): deterministic order — pass 1 精确 key 并列时
            # ORDER BY 末位行赢（+ P2-4 case-fold warning）；pass 2 别名争夺
            # 先到先得（alias_claimed，ORDER BY 靠前的行赢）。没有 ORDER BY 时
            # 两者都会随 DB 行序漂移。
            rows = (await session.execute(select(ModelPricingRow).order_by(ModelPricingRow.model_name))).scalars().all()
    except Exception:  # pragma: no cover - defensive: cost display must not break the console
        logger.warning("console: failed to load model pricing from the database; using config.yaml only", exc_info=True)
        return pricing

    aliases = _model_key_aliases()

    def _entry_for(row: ModelPricingRow) -> _ModelPricing:
        # ``is not None`` rather than a truthiness test: 0.0 is a real hit
        # price ("hits are free") and must not collapse into None ("bill
        # hits at the miss price").
        return _ModelPricing(
            input_per_million=float(row.input_per_million or 0),
            output_per_million=float(row.output_per_million or 0),
            currency=str(row.currency or "CNY").strip().upper() or "CNY",
            input_cache_hit_per_million=(float(row.input_cache_hit_per_million) if row.input_cache_hit_per_million is not None else None),
        )

    # C2 (2026-08-14): two passes over the same in-memory rows. A row's own
    # spellings are the operator's direct decision and must win over whatever
    # another row's alias expansion would reach; alias keys only fill gaps
    # between rows. Both passes must ASSIGN (not setdefault): the DB row is
    # the operator's decision and outranks the yaml fallback, and setdefault
    # would let a yaml price silently beat the DB row on alias keys
    # (2026-08-14 review fix — alias_claimed keeps row-vs-row determinism,
    # with ORDER BY deciding among alias contenders).
    exact_keys: set[str] = set()
    exact_origins: dict[str, str] = {}  # P2-4: key → 首个写入行的 model_name（大小写并存检测）
    for row in rows:
        for key in (row.model_name, row.model_name.lower()):
            origin = exact_origins.get(key)
            if origin is not None and origin != row.model_name:
                # P2-4: "FOO"/"foo" 两行并存争同一小写 key——确定（ORDER BY 末位赢）
                # 但无警告会让运维误以为只有一个拼写。PG 本地化 collation 下胜者
                # 还可能翻转，warning 让并存可见、可行动。
                logger.warning(
                    "console: model pricing rows %r and %r both claim key %r (case-fold collision); later row in sort order wins",
                    origin,
                    row.model_name,
                    key,
                )
            pricing[key] = _entry_for(row)
            exact_origins[key] = row.model_name
            exact_keys.add(key)
    alias_claimed: set[str] = set()
    for row in rows:
        alias_keys = aliases.get(row.model_name, set()) | aliases.get(row.model_name.lower(), set())
        for key in alias_keys:
            if key in exact_keys or key in alias_claimed:
                continue
            alias_claimed.add(key)
            pricing[key] = _entry_for(row)

    return pricing


def _pricing_currency(pricing: dict[str, _ModelPricing]) -> str | None:
    """Display currency: the first configured entry's (one currency per deployment)."""
    return next(iter(pricing.values())).currency if pricing else None


def _lookup_pricing(pricing: dict[str, _ModelPricing], model: str | None) -> _ModelPricing | None:
    """Resolve a pricing row for ``model`` (read-side normalization).

    Candidate order: exact → lowercase → segment-by-segment prefix peel
    (each peeled segment also tried lowercased). The peel exists because the
    360 gateway reports vendor-prefixed ids (``alibaba/qwen/qwen3.8-max``)
    while operators write ``model_pricing`` rows under the config ``name``
    (``qwen3.8-max``); without it every such bucket prices as None and the
    console shows no spend (P1, 2026-08-18). Exact keys always win over
    peeled candidates, so DB row precedence is unaffected.
    """
    if not model:
        return None
    price = pricing.get(model) or pricing.get(model.lower())
    if price is not None:
        return price
    candidate = model
    while "/" in candidate:
        candidate = candidate.split("/", 1)[1]
        price = pricing.get(candidate) or pricing.get(candidate.lower())
        if price is not None:
            return price
    return None


def _token_cost(input_tokens: int, output_tokens: int, price: _ModelPricing, cache_read_tokens: int = 0) -> float:
    """Cache-aware spend: cache-hit input tokens are billed at the hit price.

    ``cache_read_tokens`` is clamped into ``[0, input_tokens]``; the remainder
    is billed at the full (cache-miss) input price. Without a configured hit
    price all input is billed at the miss price.
    """
    cache_read = min(max(int(cache_read_tokens or 0), 0), max(int(input_tokens or 0), 0))
    uncached = max(int(input_tokens or 0), 0) - cache_read
    hit_price = price.input_cache_hit_per_million if price.input_cache_hit_per_million is not None else price.input_per_million
    return (uncached / 1_000_000) * price.input_per_million + (cache_read / 1_000_000) * hit_price + (output_tokens / 1_000_000) * price.output_per_million


def _run_cost(
    pricing: dict[str, _ModelPricing],
    *,
    model_name: str | None,
    total_input_tokens: int | None,
    total_output_tokens: int | None,
    token_usage_by_model: dict | None,
) -> float | None:
    """Estimate one run's spend, or None when none of its models are priced.

    Prefers the per-model breakdown (accurate for multi-model runs, e.g.
    subagents on a different model); falls back to run-level totals priced at
    ``model_name`` for legacy rows. Buckets without an input/output split are
    skipped rather than guessed.

    B2: when a bucket carries a real-bill ``amount`` (from the 360 gateway),
    that value is used directly instead of estimating via ``_token_cost``.
    Buckets without ``amount`` fall through to estimation as before. This
    means a mixed run (360 models with amount + Ark models without) reports
    the sum of real-bill and estimated.
    """
    cost = 0.0
    priced = False
    if isinstance(token_usage_by_model, dict):
        for model, usage in token_usage_by_model.items():
            if not isinstance(usage, dict):
                continue
            # B2: real-bill amount takes priority over estimation.
            raw_amount = usage.get("amount")
            if raw_amount is not None:
                try:
                    cost += float(raw_amount)
                    priced = True
                    continue
                except (TypeError, ValueError):
                    pass  # Fall through to estimation
            price = _lookup_pricing(pricing, model)
            if price is None:
                continue
            input_tokens = int(usage.get("input_tokens") or 0)
            output_tokens = int(usage.get("output_tokens") or 0)
            if input_tokens == 0 and output_tokens == 0:
                continue
            cost += _token_cost(input_tokens, output_tokens, price, int(usage.get("cache_read_tokens") or 0))
            priced = True
    if priced:
        return cost
    price = _lookup_pricing(pricing, model_name)
    if price is None:
        return None
    input_tokens = int(total_input_tokens or 0)
    output_tokens = int(total_output_tokens or 0)
    if input_tokens == 0 and output_tokens == 0:
        return None
    return _token_cost(input_tokens, output_tokens, price)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get(
    "/stats",
    response_model=ConsoleStatsResponse,
    summary="Console Stats",
    description=(
        "Headline counters (runs, threads, agents, tokens, spend). Scoped to the caller unless an admin "
        "asks for the platform-wide view. Pass `days` to window the counters (absent = all-time for "
        "backward compatibility); the frontend's day selector always passes it explicitly (default 7)."
    ),
)
@require_permission("runs", "read")
async def console_stats(
    request: Request,
    user_id: str | None = Query(default=None, description="Admin-only: scope to one user instead of the whole platform"),
    days: int | None = Query(default=None, ge=1, le=90, description="C5: window the counters to the last N days; absent = all-time (back-compat)"),
) -> ConsoleStatsResponse:
    """Return the dashboard's headline counters."""
    sf = _session_factory_or_503()
    scope = await _resolve_scope(request, user_id)
    threshold = _attention_threshold()

    run_where = (RunRow.operation_kind == "run",)
    if scope.filter_user_id is not None:
        run_where += (RunRow.user_id == scope.filter_user_id,)
    # C5 窗口契约：days 传入 → 全部计数器随窗口（A5 统计卡随选择器）。
    window_start_utc = None
    if days is not None:
        window_start_utc = datetime.now(UTC) - timedelta(days=days)
        run_where += (RunRow.created_at >= window_start_utc,)

    pricing = await _build_pricing_map(sf)

    async with sf() as session:
        total_runs = await session.scalar(select(func.count()).select_from(RunRow).where(*run_where)) or 0
        active_runs = await session.scalar(select(func.count()).select_from(RunRow).where(RunRow.status.in_(_ACTIVE_STATUSES), *run_where)) or 0
        failed_runs = await session.scalar(select(func.count()).select_from(RunRow).where(RunRow.status.in_(_FAILED_STATUSES), *run_where)) or 0
        total_tokens = await session.scalar(select(func.coalesce(func.sum(RunRow.total_tokens), 0)).where(*run_where)) or 0

        over_threshold_runs = 0
        if threshold > 0:
            over_threshold_runs = await session.scalar(select(func.count()).select_from(RunRow).where(RunRow.total_tokens >= threshold, *run_where)) or 0

        total_cost: float | None = None
        if pricing:
            cost_rows = (
                await session.execute(
                    select(
                        RunRow.model_name,
                        RunRow.total_input_tokens,
                        RunRow.total_output_tokens,
                        RunRow.token_usage_by_model,
                    ).where(*run_where)
                )
            ).all()
            cost_sum = 0.0
            for model_name, input_tokens, output_tokens, usage_map in cost_rows:
                cost = _run_cost(
                    pricing,
                    model_name=model_name,
                    total_input_tokens=input_tokens,
                    total_output_tokens=output_tokens,
                    token_usage_by_model=usage_map,
                )
                if cost is not None:
                    cost_sum += cost
            total_cost = round(cost_sum, 6)

            # C3（A7 全链路总成本）：老 total_cost 只数对话 run（审计问题 3
            # 「统计卡仅对话 run」）——新增口径把 llm_bookkeeping（video-gen/
            # vimax/writer 等非对话 LLM）一并计入，喂 total_cost_all 与
            # cost_breakdown.llm；老字段语义不动（A12 回滚零成本）。
            book_where = (RunRow.operation_kind == "llm_bookkeeping",)
            if scope.filter_user_id is not None:
                book_where += (RunRow.user_id == scope.filter_user_id,)
            if window_start_utc is not None:
                book_where += (RunRow.created_at >= window_start_utc,)
            book_rows = (
                await session.execute(
                    select(
                        RunRow.model_name,
                        RunRow.total_input_tokens,
                        RunRow.total_output_tokens,
                        RunRow.token_usage_by_model,
                    ).where(*book_where)
                )
            ).all()
            book_cost = 0.0
            for model_name, input_tokens, output_tokens, usage_map in book_rows:
                cost = _run_cost(
                    pricing,
                    model_name=model_name,
                    total_input_tokens=input_tokens,
                    total_output_tokens=output_tokens,
                    token_usage_by_model=usage_map,
                )
                if cost is not None:
                    book_cost += cost
            llm_total_cost = round((total_cost or 0.0) + book_cost, 6)
        else:
            llm_total_cost = None

        # C3/C5：媒体分量走共享聚合（真账优先+估算+TTS；scope 必传维度），
        # 与 /usage、外部调用面板同源。
        from app.gateway._shared.task_cost import aggregate_media_cost

        media_agg = await aggregate_media_cost(session, scope=scope, window_start=window_start_utc)
        media_total = float(media_agg["total"])

    # 混币守卫（与 task_cost 的 P3-1 同姿势）：多币种宁可报 None 不静默相加。
    llm_currency = _pricing_currency(pricing)
    media_currencies = media_agg["currencies"]
    mixed = len(media_currencies) > 1 or (llm_currency and media_currencies and llm_currency not in media_currencies)
    if mixed:
        logger.warning(
            "console stats: mixed currencies (llm=%s media=%s); total_cost_all reported as None",
            llm_currency,
            media_currencies,
        )

    total_cost_all: float | None = None
    if not mixed:
        if llm_total_cost is not None and media_total > 0:
            total_cost_all = round(llm_total_cost + media_total, 6)
        elif llm_total_cost is not None:
            total_cost_all = llm_total_cost
        elif media_total > 0:
            total_cost_all = round(media_total, 6)

    return ConsoleStatsResponse(
        total_runs=total_runs,
        active_runs=active_runs,
        failed_runs=failed_runs,
        total_tokens=total_tokens,
        total_cost=total_cost,
        total_cost_all=total_cost_all,
        cost_breakdown=ConsoleCostBreakdown(llm=llm_total_cost, media=round(media_total, 6)),
        unpriced_calls=int(media_agg["unpriced_calls"]),
        currency=_pricing_currency(pricing),
        over_threshold_runs=over_threshold_runs,
        scope=scope.response(),
    )


_RUN_SORT_COLUMNS = {
    "created_at": RunRow.created_at,
    "updated_at": RunRow.updated_at,
    "total_tokens": RunRow.total_tokens,
    "status": RunRow.status,
}


@router.get(
    "/runs",
    response_model=ConsoleRunsResponse,
    summary="List Runs Across Threads",
    description="Cross-thread run history joined with thread titles. Scoped to the caller unless an admin asks for the platform-wide view.",
)
@require_permission("runs", "read")
async def console_runs(
    request: Request,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    status: str | None = Query(default=None, description="Filter by run status (e.g. running, success, error)"),
    sort_by: str = Query(default="created_at", description="Sort column: created_at | updated_at | total_tokens | status"),
    sort_dir: str = Query(default="desc", description="Sort direction: asc | desc"),
    user_id: str | None = Query(default=None, description="Admin-only: scope to one user instead of the whole platform"),
) -> ConsoleRunsResponse:
    """Return a page of runs across all threads in scope."""
    sf = _session_factory_or_503()
    scope = await _resolve_scope(request, user_id)
    threshold = _attention_threshold()

    sort_column = _RUN_SORT_COLUMNS.get(sort_by, RunRow.created_at)
    descending = sort_dir.lower() != "asc"
    # run_id is the tiebreaker so paging is stable when the sort column ties
    # (869 rows share very few distinct created_at values at second precision).
    order = (sort_column.desc(), RunRow.run_id.desc()) if descending else (sort_column.asc(), RunRow.run_id.asc())

    stmt = (
        select(RunRow, ThreadMetaRow.display_name, UserRow.email)
        .join(ThreadMetaRow, ThreadMetaRow.thread_id == RunRow.thread_id, isouter=True)
        .join(UserRow, UserRow.id == RunRow.user_id, isouter=True)
        .where(RunRow.operation_kind == "run")
        .order_by(*order)
        .limit(limit + 1)
        .offset(offset)
    )
    stmt = scope.apply(stmt, RunRow.user_id)
    if status:
        stmt = stmt.where(RunRow.status == status)

    async with sf() as session:
        rows = (await session.execute(stmt)).all()

    pricing = await _build_pricing_map(sf)
    has_more = len(rows) > limit
    now = datetime.now(UTC)
    items: list[ConsoleRunItem] = []
    for row, display_name, owner_email in rows[:limit]:
        created = _as_utc(row.created_at)
        updated = _as_utc(row.updated_at)
        if row.status in _ACTIVE_STATUSES:
            duration = (now - created).total_seconds() if created else None
        else:
            duration = (updated - created).total_seconds() if created and updated else None
        cost = _run_cost(
            pricing,
            model_name=row.model_name,
            total_input_tokens=row.total_input_tokens,
            total_output_tokens=row.total_output_tokens,
            token_usage_by_model=row.token_usage_by_model,
        )
        total_tokens = row.total_tokens or 0
        items.append(
            ConsoleRunItem(
                run_id=row.run_id,
                thread_id=row.thread_id,
                thread_title=display_name,
                assistant_id=row.assistant_id,
                # Owner identity is an admin-only column: a regular user's
                # response would only ever echo their own id back.
                user_id=row.user_id if scope.is_admin else None,
                user_email=owner_email if scope.is_admin else None,
                status=row.status,
                model_name=row.model_name,
                created_at=created,
                updated_at=updated,
                duration_seconds=max(duration, 0.0) if duration is not None else None,
                total_tokens=total_tokens,
                subagent_tokens=row.subagent_tokens or 0,
                message_count=row.message_count or 0,
                cost=round(cost, 6) if cost is not None else None,
                error=row.error[:_ERROR_EXCERPT_CHARS] if row.error else None,
                stop_reason=row.stop_reason,
                over_threshold=threshold > 0 and total_tokens >= threshold,
            )
        )
    return ConsoleRunsResponse(runs=items, has_more=has_more, scope=scope.response())


@router.get(
    "/usage",
    response_model=ConsoleUsageResponse,
    summary="Token Usage Over Time",
    description="Daily token-usage series (zero-filled) plus per-model breakdown over the requested window.",
)
@require_permission("runs", "read")
async def console_usage(
    request: Request,
    days: int = Query(default=14, ge=1, le=90),
    tz_offset_minutes: int = Query(default=0, ge=-840, le=840, description="Local-time offset from UTC for day bucketing"),
    user_id: str | None = Query(default=None, description="Admin-only: scope to one user instead of the whole platform"),
) -> ConsoleUsageResponse:
    """Aggregate token usage by local day and by model."""
    sf = _session_factory_or_503()
    scope = await _resolve_scope(request, user_id)

    tz_delta = timedelta(minutes=tz_offset_minutes)
    today_local = (datetime.now(UTC) + tz_delta).date()
    start_local = today_local - timedelta(days=days - 1)
    window_start_utc = datetime.combine(start_local, time.min, tzinfo=UTC) - tz_delta

    # B1: include llm_bookkeeping rows alongside chat runs. Narrow the select
    # to only the columns we read - llm_bookkeeping rows can be ~1000/day and
    # pulling metadata_json etc. on a 90-day window is wasteful.
    stmt = select(
        RunRow.created_at,
        RunRow.operation_kind,
        RunRow.total_tokens,
        RunRow.total_input_tokens,
        RunRow.total_output_tokens,
        RunRow.subagent_tokens,
        RunRow.model_name,
        RunRow.token_usage_by_model,
    ).where(
        RunRow.operation_kind.in_(["run", "llm_bookkeeping"]),
        RunRow.created_at >= window_start_utc,
    )
    stmt = scope.apply(stmt, RunRow.user_id)

    async with sf() as session:
        rows = (await session.execute(stmt)).all()

    day_buckets: dict[str, ConsoleUsageDay] = {}
    for i in range(days):
        d = (start_local + timedelta(days=i)).isoformat()
        day_buckets[d] = ConsoleUsageDay(date=d)

    pricing = await _build_pricing_map(sf)
    by_model: dict[str, ConsoleUsageModelBreakdown] = {}
    total_tokens = 0
    total_runs = 0
    total_chat_tokens = 0
    total_llm_tokens = 0
    total_llm_calls = 0
    subagent_tokens = 0
    total_cost = 0.0 if pricing else None
    for row in rows:
        created = _as_utc(row.created_at)
        if created is None:
            continue
        local_date = ((created + tz_delta).date()).isoformat()
        bucket = day_buckets.get(local_date)
        if bucket is None:
            # Row sits just outside the local window (UTC-window over-fetch); skip.
            continue
        is_llm = row.operation_kind == "llm_bookkeeping"
        run_tokens = row.total_tokens or 0
        bucket.total_tokens += run_tokens
        bucket.input_tokens += row.total_input_tokens or 0
        bucket.output_tokens += row.total_output_tokens or 0
        total_tokens += run_tokens

        if is_llm:
            # llm_bookkeeping rows are individual LLM calls, not agent runs.
            # They contribute tokens and cost, but must NOT increment `runs` -
            # otherwise the day's run count and the stats card's total_runs
            # (chat-only) would diverge by orders of magnitude.
            bucket.llm_tokens += run_tokens
            bucket.llm_calls += 1
            total_llm_tokens += run_tokens
            total_llm_calls += 1
        else:
            bucket.chat_tokens += run_tokens
            bucket.runs += 1
            total_runs += 1
            total_chat_tokens += run_tokens
            subagent_tokens += row.subagent_tokens or 0

        run_cost = _run_cost(
            pricing,
            model_name=row.model_name,
            total_input_tokens=row.total_input_tokens,
            total_output_tokens=row.total_output_tokens,
            token_usage_by_model=row.token_usage_by_model,
        )
        if run_cost is not None and total_cost is not None:
            # ``bucket.cost`` starts as None ("no priced run seen yet") and only
            # becomes a number once one contributes, so a day of unpriced runs
            # stays null instead of reporting 0.0.
            bucket.cost = round((bucket.cost or 0.0) + run_cost, 6)
            total_cost = round(total_cost + run_cost, 6)

        usage_map = row.token_usage_by_model or {}
        if isinstance(usage_map, dict) and usage_map:
            for model, usage in usage_map.items():
                # Skip before touching ``by_model``: an unreadable bucket must
                # neither count as a run of that model nor conjure a row for
                # it. Doing either produces a plausible-looking line the
                # operator has no way to recognize as wrong — inflated runs
                # against zero tokens, or a phantom model with all-zero stats.
                # This column is provider-fed JSON, so a non-dict here means
                # an upstream shape change, not a rare corruption.
                if not isinstance(usage, dict):
                    continue
                entry = by_model.setdefault(model, ConsoleUsageModelBreakdown())
                # B1: branch on operation_kind - llm rows count as llm_calls,
                # NOT runs. Otherwise the model breakdown's runs column would
                # jump to call-count levels, tearing the口径 from the stats
                # card's total_runs (chat run count).
                if is_llm:
                    entry.llm_calls += 1
                else:
                    entry.runs += 1
                entry.tokens += int(usage.get("total_tokens", 0) or 0)
                entry.input_tokens += int(usage.get("input_tokens") or 0)
                entry.output_tokens += int(usage.get("output_tokens") or 0)
                entry.cache_read_tokens += int(usage.get("cache_read_tokens") or 0)
                # B2: prefer real-bill amount over estimation.
                raw_amount = usage.get("amount")
                if raw_amount is not None:
                    try:
                        entry.cost = round((entry.cost or 0.0) + float(raw_amount), 6)
                    except (TypeError, ValueError):
                        pass
                else:
                    price = _lookup_pricing(pricing, model)
                    if price is not None:
                        model_cost = _token_cost(int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0), price, int(usage.get("cache_read_tokens") or 0))
                        entry.cost = round((entry.cost or 0.0) + model_cost, 6)
        elif row.model_name and run_tokens > 0:
            # Legacy rows predating token_usage_by_model: fall back to the run's model.
            entry = by_model.setdefault(row.model_name, ConsoleUsageModelBreakdown())
            entry.tokens += run_tokens
            entry.input_tokens += row.total_input_tokens or 0
            entry.output_tokens += row.total_output_tokens or 0
            if is_llm:
                entry.llm_calls += 1
            else:
                entry.runs += 1
            if run_cost is not None:
                entry.cost = round((entry.cost or 0.0) + run_cost, 6)

    # C5: 媒体聚合统一改调共享 aggregate_media_cost（scope 必传维度，禁止第四条
    # 聚合链）——真账优先+估算+TTS 全口径，消除旧「只算 amount 行」的静默少报
    # 与 TTS 漏项。media_cost_by_provider = 该 provider 真账+估算之和。
    media_agg: dict = {"by_provider": {}, "total": 0.0, "unpriced_calls": 0, "currencies": []}
    try:
        from app.gateway._shared.task_cost import aggregate_media_cost

        async with sf() as session:
            media_agg = await aggregate_media_cost(session, scope=scope, window_start=window_start_utc)
    except Exception:
        logger.debug("console: media cost aggregation failed", exc_info=True)
    media_cost_by_provider = {p: round(v["cost_real"] + v["cost_estimated"], 6) for p, v in media_agg["by_provider"].items() if (v["cost_real"] or v["cost_estimated"])}
    media_cost_by_provider_split = {p: {"real": round(v["cost_real"], 6), "estimated": round(v["cost_estimated"], 6)} for p, v in media_agg["by_provider"].items() if (v["cost_real"] or v["cost_estimated"])}

    # C3：total_cost_all（LLM+媒体），混币守卫同 /stats。
    llm_currency = _pricing_currency(pricing)
    media_currencies = media_agg.get("currencies", [])
    mixed = len(media_currencies) > 1 or (llm_currency and media_currencies and llm_currency not in media_currencies)
    if mixed:
        logger.warning("console usage: mixed currencies (llm=%s media=%s); total_cost_all reported as None", llm_currency, media_currencies)
    media_total = float(media_agg.get("total", 0.0))
    total_cost_all: float | None = None
    if not mixed:
        if total_cost is not None and media_total > 0:
            total_cost_all = round(total_cost + media_total, 6)
        elif total_cost is not None:
            total_cost_all = total_cost
        elif media_total > 0:
            total_cost_all = round(media_total, 6)

    return ConsoleUsageResponse(
        days=list(day_buckets.values()),
        by_model=by_model,
        total_tokens=total_tokens,
        total_runs=total_runs,
        total_chat_tokens=total_chat_tokens,
        total_llm_tokens=total_llm_tokens,
        total_llm_calls=total_llm_calls,
        total_cost=total_cost,
        total_cost_all=total_cost_all,
        cost_breakdown=ConsoleCostBreakdown(llm=total_cost, media=round(media_total, 6)),
        unpriced_calls=int(media_agg.get("unpriced_calls", 0)),
        media_cost_by_provider=media_cost_by_provider,
        media_cost_by_provider_split=media_cost_by_provider_split,
        currency=_pricing_currency(pricing),
        subagent_tokens=subagent_tokens,
        scope=scope.response(),
    )


@router.get(
    "/users",
    response_model=ConsoleUsersResponse,
    summary="List Users For Drill-Down",
    description="Admin-only: users with their run/token totals, for the console's user picker.",
)
@require_permission("runs", "read")
async def console_users(request: Request) -> ConsoleUsersResponse:
    """Return the selectable users for the admin drill-down."""
    sf = _session_factory_or_503()
    if not _is_admin(request):
        raise HTTPException(status_code=403, detail="Admin access required")

    totals = (
        select(
            RunRow.user_id.label("user_id"),
            func.count().label("total_runs"),
            func.coalesce(func.sum(RunRow.total_tokens), 0).label("total_tokens"),
        )
        .where(RunRow.operation_kind == "run")
        .group_by(RunRow.user_id)
        .subquery()
    )
    stmt = select(UserRow.id, UserRow.email, UserRow.system_role, totals.c.total_runs, totals.c.total_tokens).join(totals, totals.c.user_id == UserRow.id, isouter=True).order_by(UserRow.email)

    async with sf() as session:
        rows = (await session.execute(stmt)).all()

    return ConsoleUsersResponse(
        users=[
            ConsoleUserItem(
                user_id=str(uid),
                email=email,
                system_role=system_role,
                total_runs=int(total_runs or 0),
                total_tokens=int(total_tokens or 0),
            )
            for uid, email, system_role, total_runs, total_tokens in rows
        ]
    )


@router.get(
    "/llm-usage",
    response_model=ConsoleLlmUsageResponse,
    summary="Non-conversation LLM Usage",
    description="Aggregated LLM usage for non-conversation callers (video-gen, ViMax, writer/gallery/suggestions), grouped by domain. Visible to all users (scoped).",
)
@require_permission("runs", "read")
async def console_llm_usage(
    request: Request,
    days: int = Query(default=14, ge=1, le=90),
    tz_offset_minutes: int = Query(default=0, ge=-840, le=840, description="Local-time offset from UTC for day bucketing"),
    user_id: str | None = Query(default=None, description="Admin-only: scope to one user"),
) -> ConsoleLlmUsageResponse:
    """Aggregate non-conversation LLM usage by domain."""
    sf = _session_factory_or_503()
    scope = await _resolve_scope(request, user_id)

    tz_delta = timedelta(minutes=tz_offset_minutes)
    today_local = (datetime.now(UTC) + tz_delta).date()
    start_local = today_local - timedelta(days=days - 1)
    window_start_utc = datetime.combine(start_local, time.min, tzinfo=UTC) - tz_delta

    stmt = select(RunRow).where(
        RunRow.operation_kind == "llm_bookkeeping",
        RunRow.created_at >= window_start_utc,
    )
    stmt = scope.apply(stmt, RunRow.user_id)

    async with sf() as session:
        rows = (await session.execute(stmt)).scalars().all()

    pricing = await _build_pricing_map(sf)

    # Aggregate by domain
    domain_data: dict[str, dict] = {}
    for row in rows:
        meta = row.metadata_json or {}
        domain = meta.get("domain", "unknown")
        entry = domain_data.setdefault(domain, {"calls": 0, "failed_calls": 0, "tokens": 0, "input_tokens": 0, "output_tokens": 0, "cost": 0.0, "priced": False})

        entry["calls"] += 1
        if row.status != "success":
            entry["failed_calls"] += 1
        entry["tokens"] += row.total_tokens or 0
        entry["input_tokens"] += row.total_input_tokens or 0
        entry["output_tokens"] += row.total_output_tokens or 0

        # Cost: prefer amount (real-bill), fall back to estimation
        usage_map = row.token_usage_by_model or {}
        if isinstance(usage_map, dict):
            for model, usage in usage_map.items():
                if not isinstance(usage, dict):
                    continue
                raw_amount = usage.get("amount")
                if raw_amount is not None:
                    try:
                        entry["cost"] = round(entry["cost"] + float(raw_amount), 6)
                        entry["priced"] = True
                        continue
                    except (TypeError, ValueError):
                        pass
                price = _lookup_pricing(pricing, model)
                if price is not None:
                    input_tk = int(usage.get("input_tokens") or 0)
                    output_tk = int(usage.get("output_tokens") or 0)
                    if input_tk > 0 or output_tk > 0:
                        model_cost = _token_cost(input_tk, output_tk, price, int(usage.get("cache_read_tokens") or 0))
                        entry["cost"] = round(entry["cost"] + model_cost, 6)
                        entry["priced"] = True

    domains = [
        ConsoleLlmUsageDomain(
            domain=d,
            calls=v["calls"],
            failed_calls=v["failed_calls"],
            tokens=v["tokens"],
            input_tokens=v["input_tokens"],
            output_tokens=v["output_tokens"],
            cost=v["cost"] if v["priced"] else None,
        )
        for d, v in sorted(domain_data.items())
    ]

    total_cost = sum(d.cost or 0 for d in domains) if any(d.cost is not None for d in domains) else None

    return ConsoleLlmUsageResponse(
        domains=domains,
        total_cost=total_cost,
        currency=_pricing_currency(pricing),
        scope=scope.response(),
    )


@router.get(
    "/external-calls",
    response_model=ConsoleExternalCallsResponse,
    summary="External API Call Counts and Costs",
    description="Aggregated external API call counts and estimated costs by provider. Visible to all users (scoped).",
)
@require_permission("runs", "read")
async def console_external_calls(
    request: Request,
    days: int = Query(default=14, ge=1, le=90),
    tz_offset_minutes: int = Query(default=0, ge=-840, le=840, description="Timezone offset for local day boundaries"),
    user_id: str | None = Query(default=None, description="Admin-only: scope to one user"),
) -> ConsoleExternalCallsResponse:
    """Aggregate external API call counts with cost estimation."""
    sf = _session_factory_or_503()
    scope = await _resolve_scope(request, user_id)

    # Local day boundary using tz_offset
    tz_delta = timedelta(minutes=tz_offset_minutes)
    now_local = datetime.now(UTC) + tz_delta
    today_local = now_local.date()
    start_local = today_local - timedelta(days=days - 1)
    window_start_utc = datetime.combine(start_local, time.min, tzinfo=UTC) - tz_delta

    # Load all rows in the window (need per-row metadata for cost estimation)
    row_stmt = select(ExternalApiCallRow).where(ExternalApiCallRow.created_at >= window_start_utc).order_by(ExternalApiCallRow.created_at)
    row_stmt = scope.apply(row_stmt, ExternalApiCallRow.user_id)

    async with sf() as session:
        all_rows = (await session.execute(row_stmt)).scalars().all()

    # Warm the media pricing process cache (provider-level + model-level maps)
    # for the sync resolve_media_price lookups below.
    await _load_media_pricing()

    # --- by_provider aggregation ---
    # B1（2026-08-27 评审修复）：按 (provider, media_type) 聚合——双类型 provider
    # （如 qihu 生图+生视频同一 Ref.source 名，qihu_image.py/qihu_video.py 均
    # source="qihu"）出两行、各自带正确类型；修前按 provider 折叠为"首个非免费
    # method"的单值，前端按类型过滤时另一边整行消失且合计漏算。
    provider_data: dict[tuple[str, str], dict] = {}
    # --- by_date aggregation ---
    date_data: dict[str, dict] = {}
    # --- by_type aggregation ---
    type_data: dict[str, dict] = {}
    unpriced_calls = 0

    for row in all_rows:
        provider = row.provider
        method = row.method
        metadata = row.metadata_json if isinstance(row.metadata_json, dict) else {}
        media_type = METHOD_TO_MEDIA_TYPE.get(method, "unknown")

        # Provider aggregation (B1: keyed by (provider, media_type))
        pdata = provider_data.setdefault((provider, media_type), {"calls": 0, "cost_real": 0.0, "cost_estimated": 0.0, "has_real": False, "has_estimated": False})
        pdata["calls"] += 1

        # C5 收口：行级计价语义走共享 evaluate_media_call（与 task_cost /
        # aggregate_media_cost 同源）——真账优先、失败不估算、模型级别名归一。
        from app.gateway._shared.task_cost import evaluate_media_call

        result = evaluate_media_call(provider, method, row.status, metadata)
        cost_real_val: float | None = None
        cost_est_val: float | None = None

        if result.source == "real":
            cost_real_val = result.cost
            pdata["cost_real"] += cost_real_val or 0.0
            pdata["has_real"] = True
        elif result.source == "estimated":
            cost_est_val = result.cost
            pdata["cost_estimated"] += cost_est_val or 0.0
            pdata["has_estimated"] = True
        elif result.source == "unpriced":
            unpriced_calls += 1

        # Date aggregation (local day)
        local_dt = row.created_at + tz_delta if row.created_at else None
        date_key = local_dt.strftime("%Y-%m-%d") if local_dt else "unknown"
        ddata = date_data.setdefault(date_key, {"calls": 0, "cost_real": 0.0, "cost_estimated": 0.0})
        ddata["calls"] += 1
        if cost_real_val is not None:
            ddata["cost_real"] += cost_real_val
        if cost_est_val is not None:
            ddata["cost_estimated"] += cost_est_val

        # Type aggregation
        if method not in FREE_METHODS:
            tdata = type_data.setdefault(media_type, {"calls": 0, "cost": 0.0})
            tdata["calls"] += 1
            if cost_real_val is not None:
                tdata["cost"] += cost_real_val
            if cost_est_val is not None:
                tdata["cost"] += cost_est_val

    # Build response lists
    by_provider: list[dict] = []
    for (provider, media_type_for_provider), pdata in sorted(provider_data.items(), key=lambda x: -x[1]["calls"]):
        cost_real = pdata["cost_real"] if pdata["has_real"] else None
        cost_est = pdata["cost_estimated"] if pdata["has_estimated"] else None
        cost: float | None = None
        if cost_real is not None and cost_est is not None:
            cost = cost_real + cost_est
        elif cost_real is not None:
            cost = cost_real
        elif cost_est is not None:
            cost = cost_est

        # B1：media_type 已按行聚合（双类型 provider 出多行）；价目显示走同一
        # resolver（provider 级语义——面板行聚合多模型，展示 provider 默认价；
        # 逐行估算见上方 model-aware 调用点）
        pricing_entry = resolve_media_price(provider, None, media_type_for_provider)
        by_provider.append(
            {
                "provider": provider,
                "calls": pdata["calls"],
                "cost_real": round(cost_real, 4) if cost_real is not None else None,
                "cost_estimated": round(cost_est, 4) if cost_est is not None else None,
                "cost": round(cost, 4) if cost is not None else None,
                "priced": pricing_entry is not None or pdata["has_real"],
                "unit": pricing_entry["unit"] if pricing_entry else None,
                # C6：provider→类型映射后端化——前端删三个硬编码 Set（新 provider
                # 漏显 / qihu 重复出现的根源）。
                "media_type": media_type_for_provider,
            }
        )

    # Fill missing dates with zeros
    by_date: list[dict] = []
    for i in range(days):
        d = start_local + timedelta(days=i)
        key = d.strftime("%Y-%m-%d")
        ddata = date_data.get(key, {"calls": 0, "cost_real": 0.0, "cost_estimated": 0.0})
        by_date.append(
            {
                "date": key,
                "calls": ddata["calls"],
                "cost_real": round(ddata["cost_real"], 4) if ddata["cost_real"] else 0.0,
                "cost_estimated": round(ddata["cost_estimated"], 4) if ddata["cost_estimated"] else 0.0,
            }
        )

    by_type: list[dict] = [{"media_type": mt, "calls": tdata["calls"], "cost": round(tdata["cost"], 4) if tdata["cost"] else None} for mt, tdata in sorted(type_data.items(), key=lambda x: -x[1]["calls"])]

    total = sum(p["calls"] for p in by_provider)

    return ConsoleExternalCallsResponse(
        total_calls=total,
        by_provider=by_provider,
        by_date=by_date,
        by_type=by_type,
        unpriced_calls=unpriced_calls,
        scope=scope.response(),
    )
