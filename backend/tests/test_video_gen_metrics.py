"""Tests for video gen service metrics and Phase 4/5 instrumentation."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.gateway._shared.task_framework.metrics import MetricsRegistry, _parse_key
from app.gateway.video_gen.pipelines.remotion_run import run_remotion

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class _FakeSandbox:
    """Fake sandbox for testing."""

    def __init__(self, output_exists=True):
        self._files = {}
        self._output_exists = output_exists
        self.id = "fake-sandbox"

    def write_file(self, path: str, content: str) -> None:
        self._files[path] = content

    def execute_command(self, cmd: str) -> str:
        import base64

        return base64.b64encode(b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00mp42isom").decode()

    def read_file(self, path: str) -> str:
        return self._files.get(path, "")

    def list_dir(self, path: str, max_depth=2) -> list[str]:
        return ["out.mp4"] if self._output_exists else []


class _FakeProgress:
    def __init__(self):
        self.updates = []
        self._result = {}

    async def __call__(self, **kwargs):
        self.updates.append(kwargs)

    async def set_result(self, **kwargs):
        self._result.update(kwargs)


# ---------------------------------------------------------------------------
# VideoGenService metrics tests — use delta assertions
# ---------------------------------------------------------------------------


def _get_counter(reg: MetricsRegistry, name: str, labels: dict) -> float:
    return reg.counter(name).get(labels=labels)


@pytest.mark.asyncio
async def test_service_records_task_metrics_on_success(tmp_path):
    """VideoGenService._run_pipeline records video_gen_task_total on success."""
    from app.gateway._shared.task_framework.metrics import metrics as reg
    from app.gateway.video_gen.service import VideoGenService

    labels_ok = {"type": "ai_visual", "status": "completed"}
    before = _get_counter(reg, "video_gen_task_total", labels_ok)

    mock_repo = AsyncMock()
    mock_config = MagicMock()
    mock_config.max_concurrent_tasks = 2
    mock_providers = MagicMock()

    svc = VideoGenService(
        repo=mock_repo,
        config=mock_config,
        providers=mock_providers,
        storage_dir=str(tmp_path),
    )

    with patch.object(svc, "_prepare_storyboard_video", new_callable=AsyncMock) as mock_prep:
        mock_prep.return_value = {"clips": [], "sentences": [], "audio": {}}
        mock_progress = _FakeProgress()

        result = await svc._run_pipeline(
            "test-id",
            "user-1",
            "ai_visual",
            {"script": "test"},
            tmp_path,
            mock_progress,
        )

    assert result == {"clips": [], "sentences": [], "audio": {}}

    after = _get_counter(reg, "video_gen_task_total", labels_ok)
    assert after == before + 1, f"Expected counter to increment by 1, got {before} -> {after}"

    # Verify duration was observed for ai_visual prepare phase
    h = reg.histogram("video_gen_task_duration_seconds")
    snap = h.snapshot()
    found = any("ai_visual" in k and "prepare" in k for k in snap)
    assert found, f"Expected ai_visual prepare duration, got keys: {list(snap.keys())}"


@pytest.mark.asyncio
async def test_service_records_task_metrics_on_failure(tmp_path):
    """VideoGenService._run_pipeline records video_gen_task_total with failed on exception."""
    from app.gateway._shared.task_framework.metrics import metrics as reg
    from app.gateway.video_gen.service import VideoGenService

    labels_fail = {"type": "ai_visual", "status": "failed"}
    before = _get_counter(reg, "video_gen_task_total", labels_fail)

    mock_repo = AsyncMock()
    mock_config = MagicMock()
    mock_config.max_concurrent_tasks = 2
    mock_providers = MagicMock()

    svc = VideoGenService(
        repo=mock_repo,
        config=mock_config,
        providers=mock_providers,
        storage_dir=str(tmp_path),
    )

    with patch.object(svc, "_prepare_storyboard_video", new_callable=AsyncMock) as mock_prep:
        mock_prep.side_effect = RuntimeError("AI provider down")
        mock_progress = _FakeProgress()

        with pytest.raises(RuntimeError, match="AI provider down"):
            await svc._run_pipeline(
                "test-id",
                "user-1",
                "ai_visual",
                {"prompt": "test"},
                tmp_path,
                mock_progress,
            )

    after = _get_counter(reg, "video_gen_task_total", labels_fail)
    assert after == before + 1, f"Expected failed counter to increment by 1, got {before} -> {after}"

    # ai_visual failure should still record duration in prepare phase
    h = reg.histogram("video_gen_task_duration_seconds")
    snap = h.snapshot()
    found = any("ai_visual" in k and "prepare" in k for k in snap)
    assert found, f"Expected ai_visual prepare duration, got keys: {list(snap.keys())}"


@pytest.mark.asyncio
async def test_service_no_double_observe_for_remotion(tmp_path):
    """Remotion tasks should NOT record a second observe from service.py finally block."""
    from app.gateway._shared.task_framework.metrics import metrics as reg
    from app.gateway.video_gen.pipelines.remotion_props import StoryboardResult
    from app.gateway.video_gen.service import VideoGenService
    from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig

    mock_repo = AsyncMock()
    mock_config = MagicMock()
    mock_config.max_concurrent_tasks = 2
    mock_providers = MagicMock()

    svc = VideoGenService(
        repo=mock_repo,
        config=mock_config,
        providers=mock_providers,
        storage_dir=str(tmp_path),
    )

    sb = Storyboard(title="test", config=StoryboardConfig(video_type="remotion", width=1080, height=1920))
    sb_result = StoryboardResult(
        storyboard=sb,
        subtitle_path=None,
        audio_dir=str(tmp_path / "audio"),
    )

    with patch.object(svc, "_prepare_storyboard_video", new_callable=AsyncMock, return_value=sb_result), patch.object(svc, "_render_remotion_storyboard", new_callable=AsyncMock, return_value=None):
        mock_progress = _FakeProgress()

        await svc._run_pipeline(
            "test-id",
            "user-1",
            "remotion",
            {"script": "test", "mode": "fixed"},
            tmp_path,
            mock_progress,
        )

    # The service finally block should NOT observe for remotion
    h = reg.histogram("video_gen_task_duration_seconds")
    snap = h.snapshot()
    prepare_keys = [k for k in snap if "remotion" in k and "prepare" in k]
    assert len(prepare_keys) == 0, f"remotion should not have phase=prepare observation from service, got: {prepare_keys}"


# ---------------------------------------------------------------------------
# Remotion metrics tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_remotion_records_duration_metrics(tmp_path):
    """Remotion pipeline records video_gen_task_duration_seconds."""
    from app.gateway._shared.task_framework.metrics import metrics as reg

    # Capture baseline
    h = reg.histogram("video_gen_task_duration_seconds")
    baseline_count = sum(d["count"] for k, d in h.snapshot().items() if "remotion" in k)

    sandbox = _FakeSandbox(output_exists=True)
    progress = _FakeProgress()

    await run_remotion(
        {"composition": "TestComp", "props": {"title": "Metrics Test"}},
        task_dir=tmp_path,
        sandbox=sandbox,
        progress=progress,
    )

    snap = h.snapshot()
    remotion_keys = [k for k in snap if "remotion" in k]
    assert len(remotion_keys) >= 1, f"Expected remotion duration metric, got keys: {list(snap.keys())}"

    total_count = sum(snap[k]["count"] for k in remotion_keys)
    assert total_count == baseline_count + 1, f"Expected exactly 1 new remotion observation, got {total_count - baseline_count}"


# ---------------------------------------------------------------------------
# MetricsRegistry integration tests (use fresh instances — no state leakage)
# ---------------------------------------------------------------------------


def test_metrics_render_includes_video_gen_metrics():
    """Full Prometheus render includes video_gen metrics."""
    reg = MetricsRegistry()
    reg.inc("video_gen_task_total", labels={"type": "ai_visual", "status": "completed"}, help_text="Total video gen tasks by type and status")
    reg.observe("video_gen_task_duration_seconds", 12.5, labels={"type": "ai_visual", "phase": "prepare"}, help_text="Duration")

    output = reg.render()
    assert "video_gen_task_total" in output
    assert "video_gen_task_duration_seconds" in output
    assert "# TYPE video_gen_task_total counter" in output
    assert "# TYPE video_gen_task_duration_seconds histogram" in output
    assert "video_gen_task_duration_seconds_count" in output
    assert "video_gen_task_duration_seconds_sum" in output
    assert "video_gen_task_duration_seconds_bucket" in output


def test_metrics_render_includes_provider_metrics():
    """Full Prometheus render includes provider metrics."""
    reg = MetricsRegistry()
    reg.inc("video_gen_provider_calls_total", labels={"provider": "qihu", "method": "search", "result": "ok"}, help_text="Total provider API calls")
    reg.observe("video_gen_provider_latency_seconds", 0.5, labels={"provider": "qihu"}, help_text="Provider latency")

    output = reg.render()
    assert "video_gen_provider_calls_total" in output
    assert "video_gen_provider_latency_seconds" in output


def test_queue_pending_gauge_clearing():
    """Pre-clear stale gauge values before setting new ones."""
    reg = MetricsRegistry()
    reg.set("task_queue_pending", 5, labels={"domain": "video_gen", "task_type": "ai_visual", "status": "pending"})
    reg.set("task_queue_pending", 3, labels={"domain": "content", "task_type": "ppt_gen", "status": "pending"})

    gauge = reg.gauge("task_queue_pending")
    for key in list(gauge.snapshot().keys()):
        gauge.set(0, labels=_parse_key(key))

    reg.set("task_queue_pending", 2, labels={"domain": "video_gen", "task_type": "ai_visual", "status": "pending"})

    assert gauge.get(labels={"domain": "content", "task_type": "ppt_gen", "status": "pending"}) == 0
    assert gauge.get(labels={"domain": "video_gen", "task_type": "ai_visual", "status": "pending"}) == 2
