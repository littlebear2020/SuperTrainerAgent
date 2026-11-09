"""Lightweight Prometheus-format metrics collector for task framework.

No external dependencies — pure Python implementation using threading locks.
Exposes counters, gauges, and histograms in Prometheus text exposition format.

Usage:
    from app.gateway._shared.task_framework.metrics import metrics

    metrics.inc("video_gen_task_total", labels={"type": "stock_footage", "status": "completed"})
    metrics.observe("video_gen_task_duration_seconds", 12.5, labels={"type": "stock_footage", "phase": "composing"})
    metrics.set("task_queue_pending", 3, labels={"domain": "video_gen"})

    # Prometheus scrape endpoint
    text = metrics.render()
"""

from __future__ import annotations

import threading
from typing import Any


class _Metric:
    """Base metric with labels."""

    def __init__(self, name: str, metric_type: str, help_text: str) -> None:
        self.name = name
        self.metric_type = metric_type
        self.help_text = help_text


class _Counter(_Metric):
    """Monotonically increasing counter."""

    def __init__(self, name: str, help_text: str) -> None:
        super().__init__(name, "counter", help_text)
        self._values: dict[str, float] = {}
        self._lock = threading.Lock()

    def inc(self, value: float = 1.0, labels: dict[str, str] | None = None) -> None:
        key = _label_key(labels)
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) + value

    def get(self, labels: dict[str, str] | None = None) -> float:
        key = _label_key(labels)
        with self._lock:
            return self._values.get(key, 0.0)

    def snapshot(self) -> dict[str, float]:
        with self._lock:
            return dict(self._values)


class _Gauge(_Metric):
    """Value that can go up or down."""

    def __init__(self, name: str, help_text: str) -> None:
        super().__init__(name, "gauge", help_text)
        self._values: dict[str, float] = {}
        self._lock = threading.Lock()

    def set(self, value: float, labels: dict[str, str] | None = None) -> None:
        key = _label_key(labels)
        with self._lock:
            self._values[key] = value

    def inc(self, value: float = 1.0, labels: dict[str, str] | None = None) -> None:
        key = _label_key(labels)
        with self._lock:
            self._values[key] = self._values.get(key, 0.0) + value

    def dec(self, value: float = 1.0, labels: dict[str, str] | None = None) -> None:
        self.inc(-value, labels=labels)

    def get(self, labels: dict[str, str] | None = None) -> float:
        key = _label_key(labels)
        with self._lock:
            return self._values.get(key, 0.0)

    def snapshot(self) -> dict[str, float]:
        with self._lock:
            return dict(self._values)


class _Histogram(_Metric):
    """Observation histogram with configurable buckets."""

    DEFAULT_BUCKETS = (0.5, 1.0, 2.0, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0, 600.0)

    def __init__(self, name: str, help_text: str, buckets: tuple[float, ...] | None = None) -> None:
        super().__init__(name, "histogram", help_text)
        self._buckets = buckets or self.DEFAULT_BUCKETS
        self._data: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def observe(self, value: float, labels: dict[str, str] | None = None) -> None:
        key = _label_key(labels)
        with self._lock:
            if key not in self._data:
                self._data[key] = {
                    "count": 0,
                    "sum": 0.0,
                    "buckets": {b: 0 for b in self._buckets},
                }
            d = self._data[key]
            d["count"] += 1
            d["sum"] += value
            for b in self._buckets:
                if value <= b:
                    d["buckets"][b] += 1

    def snapshot(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            return {k: dict(v) for k, v in self._data.items()}


def _label_key(labels: dict[str, str] | None) -> str:
    if not labels:
        return ""
    return ",".join(f"{k}={v}" for k, v in sorted(labels.items()))


def _format_labels(labels: dict[str, str] | None) -> str:
    if not labels:
        return ""
    pairs = ",".join(f'{k}="{v}"' for k, v in sorted(labels.items()))
    return "{" + pairs + "}"


class MetricsRegistry:
    """Thread-safe metrics registry with Prometheus text exposition."""

    def __init__(self) -> None:
        self._counters: dict[str, _Counter] = {}
        self._gauges: dict[str, _Gauge] = {}
        self._histograms: dict[str, _Histogram] = {}
        self._lock = threading.Lock()

    def counter(self, name: str, help_text: str = "") -> _Counter:
        with self._lock:
            if name not in self._counters:
                self._counters[name] = _Counter(name, help_text)
            return self._counters[name]

    def gauge(self, name: str, help_text: str = "") -> _Gauge:
        with self._lock:
            if name not in self._gauges:
                self._gauges[name] = _Gauge(name, help_text)
            return self._gauges[name]

    def histogram(self, name: str, help_text: str = "", buckets: tuple[float, ...] | None = None) -> _Histogram:
        with self._lock:
            if name not in self._histograms:
                self._histograms[name] = _Histogram(name, help_text, buckets)
            return self._histograms[name]

    # ── Convenience methods ────────────────────────────────────────

    def inc(self, name: str, value: float = 1.0, labels: dict[str, str] | None = None, help_text: str = "") -> None:
        c = self.counter(name, help_text=help_text)
        c.inc(value, labels=labels)

    def set(self, name: str, value: float, labels: dict[str, str] | None = None, help_text: str = "") -> None:
        g = self.gauge(name, help_text=help_text)
        g.set(value, labels=labels)

    def observe(self, name: str, value: float, labels: dict[str, str] | None = None, help_text: str = "") -> None:
        h = self.histogram(name, help_text=help_text)
        h.observe(value, labels=labels)

    # ── Prometheus exposition ──────────────────────────────────────

    def render(self) -> str:
        """Render all metrics in Prometheus text exposition format."""
        lines: list[str] = []

        for name, c in sorted(self._counters.items()):
            lines.append(f"# HELP {name} {c.help_text}")
            lines.append(f"# TYPE {name} counter")
            for key, value in sorted(c.snapshot().items()):
                labels = _parse_key(key)
                lines.append(f"{name}{_format_labels(labels)} {value}")

        for name, g in sorted(self._gauges.items()):
            lines.append(f"# HELP {name} {g.help_text}")
            lines.append(f"# TYPE {name} gauge")
            for key, value in sorted(g.snapshot().items()):
                labels = _parse_key(key)
                lines.append(f"{name}{_format_labels(labels)} {value}")

        for name, h in sorted(self._histograms.items()):
            lines.append(f"# HELP {name} {h.help_text}")
            lines.append(f"# TYPE {name} histogram")
            for key, data in sorted(h.snapshot().items()):
                labels = _parse_key(key)
                label_suffix = _format_labels(labels)
                # Bucket lines (cumulative)
                for b in h._buckets:
                    b_labels = {**labels, "le": str(b)}
                    lines.append(f"{name}_bucket{_format_labels(b_labels)} {data['buckets'][b]}")
                # +Inf bucket
                inf_labels = {**labels, "le": "+Inf"}
                lines.append(f"{name}_bucket{_format_labels(inf_labels)} {data['count']}")
                lines.append(f"{name}_count{label_suffix} {data['count']}")
                lines.append(f"{name}_sum{label_suffix} {data['sum']}")

        return "\n".join(lines) + "\n"


def _parse_key(key: str) -> dict[str, str]:
    if not key:
        return {}
    result = {}
    for pair in key.split(","):
        k, v = pair.split("=", 1)
        result[k] = v
    return result


# Global singleton
metrics = MetricsRegistry()
