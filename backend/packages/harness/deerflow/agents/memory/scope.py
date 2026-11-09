"""Scope gate for memory writes.

Memory is injected into the system prompt of every future session, so it may
only hold statements that remain true once the current thread is gone. The
extractor, left ungated, also wrote one-off task output: paths under
``/mnt/user-data/`` that are destroyed with the thread, "task complete"
acknowledgements, and file sizes of artifacts produced once.

Those entries are actively harmful, not merely useless:

- The extractor scores them 0.90-0.99 — higher than genuine preferences — so
  ``max_facts`` trimming (which sorts by confidence) keeps the artifacts and
  evicts the real preferences.
- A later session then asserts a file exists at a path that no longer does.

The gate is intentionally a conservative keyword/pattern filter rather than an
LLM judgement: it runs on every extracted fact, must be cheap and
deterministic, and a wrong reject only loses one fact while a wrong accept
pollutes memory permanently.
"""

from __future__ import annotations

import re

# Thread-scoped directories. Anything naming one is describing a file that
# ceases to exist when the thread is cleaned up.
_EPHEMERAL_PATH_RE = re.compile(
    r"/mnt/(user-data|acp-workspace)/|/mnt/user-data\b|\boutputs?/[\w\-.]+\.(pptx?|docx?|pdf|mp[34]|wav|html?|jpe?g|png|svg|md|txt|zip)\b",
    re.IGNORECASE,
)

# A bare filename with a produced-artifact extension, e.g. "ai-org-podcast.mp3".
_ARTIFACT_FILENAME_RE = re.compile(
    r"\b[\w\-一-鿿]+\.(pptx|docx|pdf|mp3|mp4|wav|html|jpg|jpeg|png|svg|zip)\b",
    re.IGNORECASE,
)

# Statements about a finished unit of work rather than a standing truth.
_COMPLETION_MARKERS = (
    "任务完成",
    "已完成",
    "制作完成",
    "生成成功",
    "成功生成",
    "成功重新生成",
    "全流程生成",
    "输出文件",
    "输出路径",
    "文件路径",
    "文件大小",
    # Past-tense reports of work done on one specific artifact ("已为X添加
    # 52条字幕"). The action is over; only its output would be described.
    "已为",
    "task complete",
    "successfully generated",
)


def is_durable_fact(content: object, category: object) -> bool:
    """Whether a fact is worth persisting beyond the current thread.

    Args:
        content: The extracted fact text.
        category: Its category. ``"correction"`` bypasses the gate — a
            correction encodes a lesson about how to behave, which stays
            valid even when the example that produced it named a file.

    Returns:
        ``True`` to store the fact, ``False`` to drop it.
    """
    if not isinstance(content, str):
        return False
    text = content.strip()
    if not text:
        return False

    # Corrections are the one category that is always durable: their whole
    # purpose is to stop a mistake recurring in a later session.
    if category == "correction":
        return True

    if _EPHEMERAL_PATH_RE.search(text):
        return False
    if _ARTIFACT_FILENAME_RE.search(text):
        return False
    if any(marker in text.lower() for marker in _COMPLETION_MARKERS):
        return False

    return True
