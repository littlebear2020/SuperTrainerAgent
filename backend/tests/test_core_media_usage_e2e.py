"""显式离线贯通（媒体计量计划 Task 4 Step 4）：技能发射 → drain → SQLite → ``GET /api/usage``。

- podcast / course-video 经**真实** ``_doubao_tts.py`` 镜像、PPT 经**真实** ``backend_trainerai.py`` /
  ``backend_qihu360.py``（假供应商）写账本，再经 ``drain_agent_media_usage`` 落库，最后走真实路由；
- 直接两卡（AI 生图 / AI 生视频）经真实 throttle 包裹 + provider 夹具落到同一接口；
- creative-studio 只用与 ``qihu360.ts`` 同形的账本行做契约贯通（真实 TS 由 ``main.test.ts`` 与 Task 5 验证）。

断言：任务归属、DB 调用数 = 供应商成功数、费用公式、``unpriced_media_calls``、``cost_source``；
非核心 Agent 的账本不入库；重复读取不重复计费。不发任何真实付费请求。
"""

from __future__ import annotations

import json
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
import pytest_asyncio
from _router_auth_helpers import make_authed_test_app
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from test_sandbox_media_usage_scripts import FakeStream, _audio_event, _finish_event, _load, _sse, _subtitle_event

from app.gateway._shared import agent_media_usage as amu
from app.gateway._shared import llm_usage_sink
from app.gateway._shared.personal_usage.types import make_usage_window
from app.gateway._shared.task_framework.models import TaskRow
from app.gateway.routers import personal_usage
from app.gateway.routers.console import invalidate_media_pricing_cache
from app.gateway.video_gen.providers.base import ImageRef, VideoRef
from app.gateway.video_gen.providers.throttle import ThrottledAIImageProvider, ThrottledAIVideoProvider
from deerflow.config.paths import Paths
from deerflow.persistence.engine import close_engine, get_session_factory, init_engine
from deerflow.persistence.external_api_calls.model import ExternalApiCallRow
from deerflow.persistence.media_pricing.model import MediaPricingRow
from deerflow.persistence.thread_meta.model import ThreadMetaRow
from deerflow.runtime import media_usage as mu

pytestmark = pytest.mark.e2e

REPO_ROOT = Path(__file__).resolve().parents[2]
PPT_SCRIPTS = REPO_ROOT / "skills" / "public" / "ppt-master" / "scripts"
DOUBAO = REPO_ROOT / "skills" / "public" / "podcast-studio" / "scripts" / "_doubao_tts.py"
COURSE_DOUBAO = REPO_ROOT / "skills" / "custom" / "course-video" / "scripts" / "_doubao_tts.py"
USER = "11111111-1111-1111-1111-111111111111"
NOW = datetime(2026, 11, 23, 12, tzinfo=UTC)
IN_WINDOW = datetime(2026, 11, 23, 10, tzinfo=UTC)

# 价目（与 DEFAULT_MEDIA_PRICING 对齐；估算只读 DB 行）
PER_IMAGE = 0.5
PER_KILO_CHAR = 0.12
PER_KILO_TOKEN = 0.046
IMAGE_MODEL = "bytedance/doubao-seedream-5.0-pro"


def _tts_cost(chars: int) -> float:
    return chars / 1000.0 * PER_KILO_CHAR


@pytest_asyncio.fixture
async def env(tmp_path, monkeypatch):
    monkeypatch.setattr(mu, "get_paths", lambda: Paths(base_dir=tmp_path))
    # 发射端（技能脚本）与读取端（sink）指向同一线程工作区账本
    monkeypatch.setenv("DEER_FLOW_MEDIA_LEDGER_DIR", str(mu.ledger_path("th-podcast", USER).parent))
    monkeypatch.setenv("VOLCENGINE_VOICE_ACCESS_KEY", "test-key")
    monkeypatch.setenv("DOUBAO_TTS_DEFAULT_SPEAKER", "zh_male_test_bigtts")
    monkeypatch.setenv("QIHU_API_KEY", "test-key")
    monkeypatch.delenv("QIHU_MODEL", raising=False)
    monkeypatch.delenv("QIHU_BASE_URL", raising=False)
    monkeypatch.setattr("app.gateway.routers.console.get_app_config", lambda: SimpleNamespace(models=[]))
    monkeypatch.setattr("app.gateway._shared.personal_usage.service.make_usage_window", lambda days, tz: make_usage_window(days, tz, now=NOW))
    await init_engine("sqlite", url=f"sqlite+aiosqlite:///{tmp_path / 'core_media_usage_e2e.db'}", sqlite_dir=str(tmp_path))
    sf = get_session_factory()
    async with sf() as session:
        session.add_all(
            [
                MediaPricingRow(provider="seedream-360", media_type="image", unit="per_image", unit_price=PER_IMAGE, currency="CNY"),
                MediaPricingRow(provider="doubao_tts", media_type="tts", unit="per_kilo_char", unit_price=PER_KILO_CHAR, currency="CNY"),
                MediaPricingRow(provider="seedance", media_type="video", unit="per_kilo_token", unit_price=PER_KILO_TOKEN, currency="CNY"),
            ]
        )
        for thread_id, agent in (("th-podcast", "podcast-studio"), ("th-course", "course-video"), ("th-ppt", "ppt-master"), ("th-creative", "creative-studio"), ("th-vimax", "vimax-video")):
            session.add(ThreadMetaRow(thread_id=thread_id, user_id=USER, assistant_id=agent, display_name=thread_id, status="idle", metadata_json={}, created_at=IN_WINDOW, updated_at=IN_WINDOW))
        await session.commit()
    invalidate_media_pricing_cache()
    yield sf
    await close_engine()
    invalidate_media_pricing_cache()


# --- fake suppliers (count real "paid" successes) --------------------------------------


class Supplier:
    def __init__(self) -> None:
        self.tts_calls = 0
        self.image_calls = 0

    def install_doubao(self, mod, monkeypatch, *, with_subtitle: bool = False) -> None:
        events = [_audio_event(), _subtitle_event(), _finish_event()] if with_subtitle else [_audio_event(), _finish_event()]
        supplier = self

        def fake_urlopen(req, timeout=0):
            supplier.tts_calls += 1
            return FakeStream(_sse(events))

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    def install_ppt_tts(self, mod, monkeypatch) -> None:
        supplier = self

        def fake_urlopen(req, timeout=0):
            supplier.tts_calls += 1
            return FakeStream(_sse([_audio_event(), _finish_event()]))

        monkeypatch.setattr(mod, "urlopen", fake_urlopen)
        monkeypatch.setattr(mod.time, "sleep", lambda seconds: None)

    def install_ppt_image(self, mod, monkeypatch) -> None:
        supplier = self
        payload = {"data": [{"b64_json": "iVBORw0KGgo="}]}

        class _Resp:
            status_code = 200
            text = json.dumps(payload)

            def json(self):
                return payload

        monkeypatch.setattr(mod.requests, "post", lambda *a, **k: (supplier._count_image(), _Resp())[1])
        monkeypatch.setattr(mod.requests, "get", lambda *a, **k: (_ for _ in ()).throw(AssertionError("b64 path must not download")))

    def _count_image(self) -> None:
        self.image_calls += 1


class _FakeImageInner:
    async def generate_image(self, prompt, **kwargs):  # noqa: ANN001, ANN201
        return ImageRef(url="http://x/1.png", source="qihu", raw_usage={"total_tokens": 9216})


class _FakeVideoInner:
    async def submit_video(self, prompt, *, duration=5.0, **kwargs):  # noqa: ANN001, ANN202
        return "task-e2e"

    async def poll_video(self, task_id, *, duration=5.0):  # noqa: ANN001, ANN202
        return VideoRef(url="http://x/1.mp4", duration=duration, source="seedance", raw_usage={"completion_tokens": 460})


async def _rows(sf) -> list[ExternalApiCallRow]:
    async with sf() as session:
        return list((await session.execute(select(ExternalApiCallRow).order_by(ExternalApiCallRow.created_at, ExternalApiCallRow.call_id))).scalars().all())


async def _count(sf) -> int:
    async with sf() as session:
        return int((await session.execute(select(func.count()).select_from(ExternalApiCallRow))).scalar_one())


def _usage_tasks(sf) -> dict[str, dict]:
    app = make_authed_test_app(user_factory=lambda: _user())
    app.include_router(personal_usage.router)
    payload = TestClient(app).get("/api/usage").json()
    assert payload["total_tasks"] >= 1
    return {item["source_id"]: item for item in payload["tasks"]}


def _user():
    from app.gateway.auth.models import User

    return User(id=USER, email="e2e@example.com", password_hash="x", system_role="user")


def _ledger_dir(thread_id: str) -> Path:
    return mu.ledger_path(thread_id, USER).parent


def _write_ts_shaped_lines(thread_id: str, *events: dict) -> None:
    """creative-studio 的真实发射在 TS（qihu360.ts）；这里写同形行做契约贯通。"""
    path = mu.ledger_path(thread_id, USER)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for event in events:
            fh.write(json.dumps(event, separators=(",", ":")) + "\n")


@pytest.mark.asyncio
async def test_six_card_media_usage_flows_through_one_usage_endpoint(env, monkeypatch, tmp_path):
    supplier = Supplier()

    # ① podcast-studio：真实 _doubao_tts.py 镜像，两次成功合成（含一次重试后的成功）
    podcast = _load(DOUBAO, "e2e_podcast_doubao")
    supplier.install_doubao(podcast, monkeypatch)
    text_a, text_b = "播客第一段。", "播客第二段，稍长一点。"
    assert podcast.synthesize(text_a) and podcast.synthesize(text_b)
    stats = await amu.drain_agent_media_usage(thread_id="th-podcast", user_id=USER, agent_name="podcast-studio")
    assert (stats.written, stats.duplicate, stats.error) == (2, 0, 0)

    # ② course-video：字幕版（UI 形态由 worker 解析；这里直传核心 Agent 名）
    monkeypatch.setenv("DEER_FLOW_MEDIA_LEDGER_DIR", str(_ledger_dir("th-course")))
    course = _load(COURSE_DOUBAO, "e2e_course_doubao")
    supplier.install_doubao(course, monkeypatch, with_subtitle=True)
    long_sentence = "课程逐句合成的长句" * 50  # 500 字
    assert course.synthesize_with_timestamps(long_sentence)[1]
    stats = await amu.drain_agent_media_usage(thread_id="th-course", user_id=USER, agent_name="course-video")
    assert stats.written == 1

    # ③ ppt-master：两个 TTS 分片 + 一张 360 生图，均经真实 backend
    monkeypatch.setenv("DEER_FLOW_MEDIA_LEDGER_DIR", str(_ledger_dir("th-ppt")))
    monkeypatch.syspath_prepend(str(PPT_SCRIPTS))
    ppt_tts = _load(PPT_SCRIPTS / "tts_backends" / "backend_trainerai.py", "e2e_ppt_tts")
    supplier.install_ppt_tts(ppt_tts, monkeypatch)
    monkeypatch.setattr(ppt_tts, "_merge_mp3_chunks", lambda chunk_paths, output_path: output_path.write_bytes(chunk_paths[0].read_bytes()))
    narration = ("测" * 77 + "。") * 26
    chunks = ppt_tts._split_text(narration, ppt_tts.MAX_TEXT_CHARS)
    assert len(chunks) >= 2
    ppt_tts.generate(narration, tmp_path / "narration.mp3", api_key="k", voice_id=None)
    ppt_image = _load(PPT_SCRIPTS / "image_backends" / "backend_qihu360.py", "e2e_ppt_image")
    supplier.install_ppt_image(ppt_image, monkeypatch)
    ppt_image.generate("封面图", aspect_ratio="16:9", output_dir=str(tmp_path), filename="cover.png")
    stats = await amu.drain_agent_media_usage(thread_id="th-ppt", user_id=USER, agent_name="ppt-master")
    assert (stats.written, stats.error) == (len(chunks) + 1, 0)

    # ④ creative-studio：TS 同形账本行（两张，其中一张非默认 Seedream）
    monkeypatch.setenv("DEER_FLOW_MEDIA_LEDGER_DIR", str(_ledger_dir("th-creative")))
    nondefault = "doubao-seedream-5-0-pro-260628"
    _write_ts_shaped_lines(
        "th-creative",
        {"event_id": "a" * 32, "kind": "image", "model": IMAGE_MODEL, "width": 2048, "height": 1152, "elapsed_ms": 1200},
        {"event_id": "b" * 32, "kind": "image", "model": nondefault, "width": 2048, "height": 2048, "elapsed_ms": 1300},
    )
    stats = await amu.drain_agent_media_usage(thread_id="th-creative", user_id=USER, agent_name="creative-studio")
    assert stats.written == 2

    # ⑤⑥ 直接两卡：真实 throttle 包裹 + provider 夹具（sink 行经 record_external_api_call）
    async with env() as session:
        session.add_all(
            [
                TaskRow(id="task-image", user_id=USER, domain="video_gen", task_type="ai_image", status="ready", title="直接生图", payload="{}", result="{}", created_at=IN_WINDOW, updated_at=IN_WINDOW),
                TaskRow(id="task-video", user_id=USER, domain="video_gen", task_type="ai_video", status="ready", title="直接生视频", payload="{}", result="{}", created_at=IN_WINDOW, updated_at=IN_WINDOW),
            ]
        )
        await session.commit()
    token = llm_usage_sink._accounting_ctx.set(None)
    try:
        llm_usage_sink.set_llm_accounting_context("task-image", USER, "video_gen")
        image_provider = ThrottledAIImageProvider(_FakeImageInner(), sink_label="seedream-360", qps=100, max_retries=0)
        await image_provider.generate_image("p")
        await _settle()
        llm_usage_sink.set_llm_accounting_context("task-video", USER, "video_gen")
        video_provider = ThrottledAIVideoProvider(_FakeVideoInner(), sink_label="seedance", qps=100, max_retries=0)
        await video_provider.generate_video("p", duration=5.0)
        await _settle()
    finally:
        llm_usage_sink._accounting_ctx.reset(token)

    # ── DB 调用数 = 供应商成功数 ───────────────────────────────────────────
    rows = await _rows(env)
    sandbox_rows = [r for r in rows if (r.metadata_json or {}).get("metering_source") == "sandbox_reported"]
    creative_images = 2  # ④ 的 TS 同形账本行（不经 Python 供应商计数）
    assert supplier.tts_calls == 2 + 1 + len(chunks), "TTS 供应商成功次数"
    assert supplier.image_calls == 1
    assert len(sandbox_rows) == supplier.tts_calls + supplier.image_calls + creative_images == 6 + len(chunks)
    assert len(rows) == len(sandbox_rows) + 2, "再加直接两卡各一行"
    assert all(r.user_id == USER for r in rows)

    # ── GET /api/usage：归属、费用公式、来源、未计价计数 ─────────────────
    tasks = _usage_tasks(env)
    expected_podcast = _tts_cost(len(text_a)) + _tts_cost(len(text_b))
    assert tasks["th-podcast"]["task_key"] == "agent:th-podcast"
    assert tasks["th-podcast"]["capability"] == "podcast-studio"
    assert tasks["th-podcast"]["media_cost"] == pytest.approx(expected_podcast, abs=1e-9)
    assert tasks["th-podcast"]["unpriced_media_calls"] == 0
    assert tasks["th-podcast"]["cost_source"] == "estimated"
    assert tasks["th-podcast"]["cost"] == pytest.approx(expected_podcast, abs=1e-9)

    assert tasks["th-course"]["media_cost"] == pytest.approx(_tts_cost(len(long_sentence)), abs=1e-9)
    course_rows = [r for r in sandbox_rows if r.task_id == "th-course"]
    assert [r.method for r in course_rows] == ["synthesize_sse_with_timestamps"]
    assert course_rows[0].metadata_json == {"model": "seed-tts-2.0", "char_count": len(long_sentence), "metering_source": "sandbox_reported"}

    expected_ppt = sum(_tts_cost(len(c)) for c in chunks) + PER_IMAGE
    assert tasks["th-ppt"]["capability"] == "ppt-master"
    assert tasks["th-ppt"]["media_cost"] == pytest.approx(expected_ppt, abs=1e-9)
    ppt_rows = [r for r in sandbox_rows if r.task_id == "th-ppt"]
    assert sorted((r.provider, r.method) for r in ppt_rows) == sorted([("doubao_tts", "synthesize_sse")] * len(chunks) + [("seedream-360", "generate_image")])
    assert [r.metadata_json["model"] for r in ppt_rows if r.provider == "seedream-360"] == [IMAGE_MODEL]
    assert tasks["th-ppt"]["unpriced_media_calls"] == 0

    assert tasks["th-creative"]["media_cost"] == pytest.approx(2 * PER_IMAGE, abs=1e-9), "非默认 Seedream 无模型级行 → provider 级 per-image 兜底"
    creative_models = sorted(r.metadata_json["model"] for r in sandbox_rows if r.task_id == "th-creative")
    assert creative_models == sorted([IMAGE_MODEL, nondefault]), "真实模型原样入库"
    assert tasks["th-creative"]["cost_source"] == "estimated"

    assert tasks["task-image"]["task_key"] == "video:task-image"
    assert tasks["task-image"]["capability"] == "ai_image"
    assert tasks["task-image"]["media_cost"] == pytest.approx(PER_IMAGE, abs=1e-9)
    assert tasks["task-video"]["capability"] == "ai_video"
    assert tasks["task-video"]["media_cost"] == pytest.approx(0.46 * PER_KILO_TOKEN, abs=1e-9), "completion-only 视频按 completion 计价"
    assert tasks["task-video"]["unpriced_media_calls"] == 0
    assert "th-vimax" not in tasks


async def _settle() -> None:
    """throttle 的 sink 行经 loop.create_task 发后即忘；等在途任务跑完（aiosqlite 跨线程）。"""
    import asyncio

    current = asyncio.current_task()
    pending = [task for task in asyncio.all_tasks() if task is not current and not task.done()]
    if pending:
        await asyncio.wait(pending, timeout=5)


@pytest.mark.asyncio
async def test_non_core_agent_ledger_is_never_stored_and_repeated_drains_do_not_double_bill(env, monkeypatch):
    monkeypatch.setenv("DEER_FLOW_MEDIA_LEDGER_DIR", str(_ledger_dir("th-vimax")))
    _write_ts_shaped_lines("th-vimax", {"event_id": "c" * 32, "kind": "image", "model": IMAGE_MODEL, "width": 2048, "height": 1152, "elapsed_ms": 10})
    stats = await amu.drain_agent_media_usage(thread_id="th-vimax", user_id=USER, agent_name="vimax-video")
    assert (stats.written, stats.duplicate, stats.error) == (0, 0, 0)
    assert await _count(env) == 0

    monkeypatch.setenv("DEER_FLOW_MEDIA_LEDGER_DIR", str(_ledger_dir("th-podcast")))
    podcast = _load(DOUBAO, "e2e_podcast_doubao_dup")
    Supplier().install_doubao(podcast, monkeypatch)
    podcast.synthesize("只记一次")
    first = await amu.drain_agent_media_usage(thread_id="th-podcast", user_id=USER, agent_name="podcast-studio")
    before = _usage_tasks(env)["th-podcast"]
    second = await amu.drain_agent_media_usage(thread_id="th-podcast", user_id=USER, agent_name="podcast-studio")
    assert (first.written, second.written, second.duplicate) == (1, 0, 1)
    assert await _count(env) == 1
    after = _usage_tasks(env)["th-podcast"]
    assert after["media_cost"] == before["media_cost"] == pytest.approx(_tts_cost(len("只记一次")), abs=1e-9)
