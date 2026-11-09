"""Runtime limits for ppt-master subprocess output capture.

Long decks feed svg_to_pptx / source_to_md subprocesses whose stdout can grow
unbounded and OOM the sandbox. BoundedTextBuffer keeps only the tail (most
recent) chunk and records how much was dropped, so the agent still sees the
end of the output (where errors usually are) without holding the whole stream
in memory.
"""

from __future__ import annotations

from collections import deque


class BoundedTextBuffer:
    """Append-only text buffer that retains only the tail up to max_chars.

    Truncates from the HEAD (drops oldest), keeping the most recent output.
    truncated_chars reports total dropped so callers can warn that output was
    elided.
    """

    def __init__(self, max_chars: int) -> None:
        if max_chars <= 0:
            raise ValueError("max_chars must be positive")
        self._max = max_chars
        self._chunks: deque[str] = deque()
        self._len = 0
        self.truncated_chars = 0

    def append(self, text: str) -> None:
        if not text:
            return
        self._chunks.append(text)
        self._len += len(text)
        while self._len > self._max and self._chunks:
            over = self._len - self._max
            head = self._chunks[0]
            if len(head) <= over:
                self._chunks.popleft()
                self._len -= len(head)
                self.truncated_chars += len(head)
            else:
                self._chunks[0] = head[over:]
                self._len -= over
                self.truncated_chars += over

    def getvalue(self) -> str:
        return "".join(self._chunks)

    def __len__(self) -> int:
        return self._len


def rss_mb() -> float:
    """Return current process RSS in MB (best-effort, cross-platform)."""
    try:
        import resource

        usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        # Linux reports KB, macOS reports bytes.
        return usage / (1024 * 1024) if usage > 10**7 else usage / 1024
    except Exception:
        return 0.0
