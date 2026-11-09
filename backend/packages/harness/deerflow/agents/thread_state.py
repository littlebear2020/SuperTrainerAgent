from typing import Annotated, NotRequired, TypedDict

from langchain.agents import AgentState


class SandboxState(TypedDict):
    sandbox_id: NotRequired[str | None]


class ThreadDataState(TypedDict):
    workspace_path: NotRequired[str | None]
    uploads_path: NotRequired[str | None]
    outputs_path: NotRequired[str | None]


class ViewedImageData(TypedDict):
    """Per-viewed-image metadata written by view_image_tool (R-11 / #4138).

    Deliberately base64-free: the middleware reads the file on demand from
    ``actual_path`` right before the model call, so checkpoints never carry
    the image payload.
    """

    mime_type: str
    size: int
    actual_path: str


class SummarizationCheckpoint(TypedDict):
    """Persisted summary checkpoint written by DeerFlowSummarizationMiddleware.

    Records the summary produced by the most recent summarization pass plus the
    conversation size at that moment, so the middleware's reuse guard can skip
    redundant summary-model calls while growth stays under the configured
    thresholds. ``cutoff_index`` freezes the exact safe cutoff the summary was
    built over, so reuse windows keep every message since that point (the tail
    grows, bounded by the reuse thresholds, until the next fresh summary).
    Never used to mutate ``messages`` — the full history is kept.
    """

    summary: str
    message_count: int  # len(state["messages"]) at summarization time
    approx_tokens: int  # token_counter total at summarization time
    cutoff_index: int  # summary covers messages[:cutoff_index]
    created_at: str  # ISO 8601 timestamp


def merge_artifacts(existing: list[str] | None, new: list[str] | None) -> list[str]:
    """Reducer for artifacts list - merges and deduplicates artifacts."""
    if existing is None:
        return new or []
    if new is None:
        return existing
    # Use dict.fromkeys to deduplicate while preserving order
    return list(dict.fromkeys(existing + new))


def merge_viewed_images(existing: dict[str, ViewedImageData] | None, new: dict[str, ViewedImageData] | None) -> dict[str, ViewedImageData]:
    """Reducer for viewed_images dict - merges image dictionaries with recency order.

    Special case: If new is an empty dict {}, it clears the existing images
    (capability retained for future use; nothing currently clears it).

    Recency semantics (2026-08-18): keys touched by ``new`` move to the END of
    the dict (delete-then-reinsert), so iteration order approximates "most
    recently viewed last". ViewImageMiddleware's K-image sliding window relies
    on this — a re-viewed image must re-enter the window, otherwise the model
    gets a placeholder for the very image it just asked to see and can loop on
    view_image. Plain ``{**existing, **new}`` keeps re-viewed keys at their
    first-seen position, which breaks that guarantee.
    """
    if existing is None:
        return new or {}
    if new is None:
        return existing
    # Special case: empty dict means clear all viewed images
    if len(new) == 0:
        return {}
    # Merge with recency: drop keys being updated from their old position so
    # they re-insert at the end; untouched keys keep their relative order.
    merged = {k: v for k, v in existing.items() if k not in new}
    return {**merged, **new}


def merge_todos(existing: list | None, new: list | None) -> list | None:
    """Reducer for todos list - keeps the last non-None value.

    Semantics:
    - If `new` is None (node didn't touch todos), preserve `existing`.
    - If `new` is provided (even empty list), it represents an explicit
      update and wins over `existing`.
    """
    if new is None:
        return existing
    return new


class ThreadState(AgentState):
    sandbox: NotRequired[SandboxState | None]
    thread_data: NotRequired[ThreadDataState | None]
    title: NotRequired[str | None]
    artifacts: Annotated[list[str], merge_artifacts]
    todos: Annotated[list | None, merge_todos]
    uploaded_files: NotRequired[list[dict] | None]
    viewed_images: Annotated[dict[str, ViewedImageData], merge_viewed_images]  # image_path -> {mime_type, size, actual_path}, recency order
    summarization_checkpoint: NotRequired[SummarizationCheckpoint | None]  # plain overwrite; written by DeerFlowSummarizationMiddleware.after_model
