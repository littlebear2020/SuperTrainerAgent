"""Tests for voice production task modules."""

import asyncio
import io
import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from _task_drain import drain_service_tasks
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, sessionmaker

from app.gateway._shared.task_framework.models import TaskRow
from app.gateway._shared.task_framework.repository import TaskRepository
from app.gateway._shared.task_framework.schemas import (
    COMPOSING,
    FAILED,
    PENDING,
    READY,
)
from app.gateway._shared.task_framework.service import ProgressUpdater
from app.gateway.voice_twin.doubao_tts_client import DoubaoTTSHTTPError
from app.gateway.voice_twin.production_schemas import (
    ProductionTaskResponse,
    ScriptToAudioRequest,
)
from app.gateway.voice_twin.production_service import ProductionService, split_text, strip_markdown
from app.gateway.voice_twin.volcengine_client import VolcengineTTSHTTPError

# ---------------------------------------------------------------------------
# Text splitting tests
# ---------------------------------------------------------------------------


class TestSplitText:
    def test_short_text_no_split(self):
        assert split_text("Hello") == ["Hello"]

    def test_exact_max_chars(self):
        text = "a" * 5000
        assert split_text(text) == [text]

    def test_split_by_paragraph(self):
        text = "a" * 3000 + "\n\n" + "b" * 3000
        chunks = split_text(text, max_chars=5000)
        assert len(chunks) == 2
        assert chunks[0].startswith("aaa")
        assert chunks[1].startswith("bbb")

    def test_split_by_sentence(self):
        text = "x" * 3000 + "。" + "y" * 3000 + "。"
        chunks = split_text(text, max_chars=5000)
        assert len(chunks) == 2

    def test_hard_split_fallback(self):
        text = "a" * 12000
        chunks = split_text(text, max_chars=5000)
        assert len(chunks) == 3
        assert all(len(c) <= 5000 for c in chunks)
        assert "".join(chunks) == text

    def test_multiple_paragraphs(self):
        paras = [f"Paragraph {i} content here." for i in range(10)]
        text = "\n\n".join(paras)
        chunks = split_text(text, max_chars=100)
        assert len(chunks) >= 2
        for c in chunks:
            assert len(c) <= 100

    def test_empty_text(self):
        assert split_text("") == [""]

    def test_mixed_paragraph_and_sentence(self):
        text = "a" * 2000 + "\n\n" + "b" * 4500 + "。" + "c" * 2000
        chunks = split_text(text, max_chars=5000)
        assert len(chunks) >= 2
        for c in chunks:
            assert len(c) <= 5000

    def test_split_protects_english_abbreviations(self):
        """D8e：Mr./Dr./e.g./etc. 等缩写点不作切分点（曾误切长英文段落）。"""
        # 长段落强制走句子切分；缩写必须在 chunk 内保持完整
        filler = "The presentation covers many topics in detail today. " * 130  # >5000 chars
        text = "Mr. Smith said e.g. this matters. " + filler
        chunks = split_text(text, max_chars=5000)
        joined = "".join(chunks)
        assert "Mr. Smith" in joined
        assert "e.g. this" in joined
        # 没有 chunk 以裸缩写开头（即没在缩写点后被切开）
        for c in chunks:
            assert not c.startswith(("Smith", "g. this")), c[:30]

    def test_split_protects_decimal_points(self):
        """D8e：小数点（3.14）不作切分点。"""
        filler = "Numbers appear throughout this long paragraph repeatedly. " * 130
        text = "The value 3.14 is pi. " + filler
        chunks = split_text(text, max_chars=5000)
        assert "3.14" in "".join(chunks)
        for c in chunks:
            assert not c.startswith("14 is pi"), c[:30]

    def test_split_by_english_sentence(self):
        """A long English paragraph with multiple sentences splits on . ! ?
        (was previously hard-split mid-word because only Chinese 。！？ were
        recognised). Each resulting chunk must respect max_chars."""
        # One paragraph (>max_chars) containing several English sentences.
        text = "The quick brown fox jumps over the lazy dog. " * 120
        chunks = split_text(text, max_chars=5000)
        assert len(chunks) >= 2
        for c in chunks:
            assert len(c) <= 5000
            # No chunk should be cut mid-word: each ends at a sentence boundary
            # (". ") except possibly a final fragment, which still ends on ".".
            assert c.rstrip().endswith(".")


# ---------------------------------------------------------------------------
# TaskRow model tests
# ---------------------------------------------------------------------------


@pytest.fixture
def production_session():
    engine = create_engine("sqlite:///:memory:", echo=False)
    TaskRow.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    yield session
    session.close()


class TestTaskRowModel:
    def test_create_task(self, production_session: Session):
        task = TaskRow(
            id="prod_test1",
            user_id="user_a",
            domain="voice_production",
            task_type="script_to_audio",
            payload='{"voice_id":"v1","voice_name":"讲师音色"}',
            status="pending",
        )
        production_session.add(task)
        production_session.commit()
        production_session.refresh(task)

        assert task.id == "prod_test1"
        assert task.task_type == "script_to_audio"
        assert task.status == "pending"
        assert task.progress == 0
        assert task.domain == "voice_production"

    def test_repr(self, production_session: Session):
        task = TaskRow(id="prod_repr", user_id="u", domain="voice_production", task_type="ppt_to_video", payload="{}")
        production_session.add(task)
        production_session.commit()
        r = repr(task)
        assert "TaskRow" in r
        assert "ppt_to_video" in r


# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------


class TestProductionSchemas:
    def test_script_request_defaults(self):
        req = ScriptToAudioRequest(voice_id="v1", text="hello")
        assert req.speed_ratio == 1.0
        assert req.output_format == "mp3"

    def test_script_request_validation(self):
        with pytest.raises(Exception):
            ScriptToAudioRequest(voice_id="", text="hello")

    def test_task_response_from_task_row(self):
        class FakeRow:
            id = "prod_abc"
            user_id = "user1"
            task_type = "script_to_audio"
            status = "ready"
            progress = 100
            payload = '{"voice_id":"v1","voice_name":"讲师","text":"hello"}'
            result = '{"output_path":"/tmp/out.mp3","file_size":12345}'
            error_message = None
            created_at = "2026-06-07T12:00:00"
            updated_at = "2026-06-07T12:01:00"

        resp = ProductionTaskResponse.from_task_row(FakeRow())
        assert resp.id == "prod_abc"
        assert resp.status == "ready"
        assert resp.voice_name == "讲师"
        assert resp.voice_id == "v1"
        assert resp.output_path == "/tmp/out.mp3"
        assert resp.file_size == 12345

    def test_status_constants(self):
        assert PENDING == "pending"
        assert READY == "ready"
        assert FAILED == "failed"
        assert COMPOSING == "composing"


# ---------------------------------------------------------------------------
# ProductionService tests (using unified TaskRow)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(loop_scope="function")
async def async_production_svc(tmp_path):
    """Async fixture: setup, the async test body, and teardown all run on the
    pytest-asyncio loop. Avoids ``get_event_loop()``, which raises
    ``RuntimeError: There is no current event loop`` once an earlier async-test
    file in the same process has left the thread without a current loop.
    """
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(TaskRow.metadata.create_all)
    sf = async_sessionmaker(engine, expire_on_commit=False)
    repo = TaskRepository(session_factory=sf)
    mock_voice_svc = MagicMock()
    mock_voice_svc.repo = MagicMock()
    # 显式置 None：MagicMock 自动属性是 truthy mock，会让 A2 降级链误判
    # "edge 兜底可用"而返回垃圾——测试要用降级链时显式赋 mock。
    mock_voice_svc.edge_client = None
    svc = ProductionService(
        repo=repo,
        voice_service=mock_voice_svc,
        storage_dir=str(tmp_path / "vt"),
    )
    yield svc
    # 先停掉测试拉起的后台管线（submit/start_ppt_pipeline 的 _wrap_pipeline 重试
    # 链仍在跑），再 dispose——否则泄漏任务在 aiosqlite close 的 await 窗口里再碰
    # StaticPool 唯一连接，CI 上 StaticPool.dispose() 抛 KeyError: 'connection'。
    await drain_service_tasks(svc)
    await engine.dispose()


class TestProductionService:
    async def _create_task(self, svc, user_id, task_type, payload):
        """Helper: create task directly via repo (no background pipeline)."""
        return await svc.repo.create(
            id=svc._gen_id(),
            user_id=user_id,
            domain=svc.domain,
            task_type=task_type,
            payload=json.dumps(payload, ensure_ascii=False),
        )

    @pytest.mark.asyncio
    async def test_submit_and_get_task(self, async_production_svc: ProductionService):
        task = await self._create_task(
            async_production_svc,
            "user_a",
            "script_to_audio",
            {"voice_id": "v1", "voice_name": "讲师音色", "text": "hello"},
        )
        assert task.id.startswith("prod_")
        assert task.status == "pending"

        fetched = await async_production_svc.get_task(task.id, "user_a")
        assert fetched is not None
        payload = json.loads(fetched.payload)
        assert payload["voice_name"] == "讲师音色"

    @pytest.mark.asyncio
    async def test_list_tasks_filters_by_domain(self, async_production_svc: ProductionService):
        await self._create_task(async_production_svc, "user_a", "script_to_audio", {"voice_id": "v1", "voice_name": "A"})
        await self._create_task(async_production_svc, "user_a", "script_to_audio", {"voice_id": "v2", "voice_name": "B"})
        await self._create_task(async_production_svc, "user_b", "script_to_audio", {"voice_id": "v3", "voice_name": "C"})

        tasks_a = await async_production_svc.list_tasks("user_a")
        assert len(tasks_a) == 2
        tasks_b = await async_production_svc.list_tasks("user_b")
        assert len(tasks_b) == 1

    @pytest.mark.asyncio
    async def test_cross_user_access_returns_none(self, async_production_svc: ProductionService):
        task = await self._create_task(async_production_svc, "user_a", "script_to_audio", {"voice_id": "v1", "voice_name": "A"})
        fetched = await async_production_svc.get_task(task.id, "user_b")
        assert fetched is None

    @pytest.mark.asyncio
    async def test_delete_task(self, async_production_svc: ProductionService):
        task = await self._create_task(async_production_svc, "user_a", "script_to_audio", {"voice_id": "v1", "voice_name": "A"})
        deleted = await async_production_svc.delete_task(task.id, "user_a")
        assert deleted is True
        fetched = await async_production_svc.get_task(task.id, "user_a")
        assert fetched is None

    @pytest.mark.asyncio
    async def test_delete_nonexistent_task(self, async_production_svc: ProductionService):
        deleted = await async_production_svc.delete_task("nonexistent", "user_a")
        assert deleted is False

    @pytest.mark.asyncio
    async def test_recover_stale_tasks(self, async_production_svc: ProductionService):
        task = await self._create_task(async_production_svc, "user_a", "script_to_audio", {"voice_id": "v1", "voice_name": "A"})
        await async_production_svc.repo.update(task.id, status="synthesizing", progress=50, retry_count=async_production_svc.max_retries)

        task2 = await self._create_task(async_production_svc, "user_a", "script_to_audio", {"voice_id": "v2", "voice_name": "B"})
        await async_production_svc.repo.update(task2.id, status="ready", progress=100)

        recovered = await async_production_svc.recover_stale_tasks()
        assert recovered == 1

        fetched = await async_production_svc.get_task(task.id, "user_a")
        assert fetched.status == "failed"
        assert "Gateway restarted" in fetched.error_message

        fetched2 = await async_production_svc.get_task(task2.id, "user_a")
        assert fetched2.status == "ready"

    @pytest.mark.asyncio
    async def test_recover_stale_tasks_all_statuses(self, async_production_svc: ProductionService):
        """Verify recover marks Gateway-managed statuses as failed."""
        incomplete = ("pending", "extracting", "converting", "synthesizing")
        tasks = []
        for status in incomplete:
            t = await self._create_task(async_production_svc, "user_a", "ppt_to_video", {"voice_id": "v1"})
            await async_production_svc.repo.update(t.id, status=status, progress=10, retry_count=async_production_svc.max_retries)
            tasks.append(t)

        ok_task = await self._create_task(async_production_svc, "user_a", "script_to_audio", {"voice_id": "v1"})
        await async_production_svc.repo.update(ok_task.id, status="ready", progress=100)

        recovered = await async_production_svc.recover_stale_tasks()
        assert recovered == 4

        for t in tasks:
            fetched = await async_production_svc.get_task(t.id, "user_a")
            assert fetched.status == "failed"

        ok_fetched = await async_production_svc.get_task(ok_task.id, "user_a")
        assert ok_fetched.status == "ready"

    @pytest.mark.asyncio
    async def test_recover_stale_tasks_cleans_dirs(self, async_production_svc: ProductionService, tmp_path):
        """Verify recover cleans up task directories."""
        async_production_svc._storage_dir = str(tmp_path / "vt")
        task = await self._create_task(async_production_svc, "user_a", "ppt_to_video", {"voice_id": "v1"})
        await async_production_svc.repo.update(task.id, status="synthesizing", progress=50, retry_count=async_production_svc.max_retries)

        task_dir = tmp_path / "vt" / "user_a" / "tasks" / task.id
        task_dir.mkdir(parents=True, exist_ok=True)
        (task_dir / "audio").mkdir(exist_ok=True)
        (task_dir / "audio" / "dummy.txt").write_text("test")

        recovered = await async_production_svc.recover_stale_tasks()
        assert recovered == 1

    @pytest.mark.asyncio
    async def test_recover_stale_tasks_nothing_to_recover(self, async_production_svc: ProductionService):
        t1 = await self._create_task(async_production_svc, "user_a", "script_to_audio", {"voice_id": "v1"})
        await async_production_svc.repo.update(t1.id, status="ready", progress=100)
        t2 = await self._create_task(async_production_svc, "user_a", "script_to_audio", {"voice_id": "v2"})
        await async_production_svc.repo.update(t2.id, status="failed", error_message="test")

        recovered = await async_production_svc.recover_stale_tasks()
        assert recovered == 0

    @pytest.mark.asyncio
    async def test_gateway_recover_preserves_composing_tasks(self, async_production_svc: ProductionService, tmp_path):
        """Gateway recover must NOT touch composing tasks."""
        async_production_svc._storage_dir = str(tmp_path / "vt")
        task = await self._create_task(async_production_svc, "user_a", "ppt_to_video", {"voice_id": "v1"})
        await async_production_svc.repo.update(task.id, status="composing", progress=80)

        task_dir = tmp_path / "vt" / "user_a" / "tasks" / task.id
        task_dir.mkdir(parents=True, exist_ok=True)
        (task_dir / "audio").mkdir(exist_ok=True)
        (task_dir / "audio" / "dummy.txt").write_text("test")

        recovered = await async_production_svc.recover_stale_tasks()
        assert recovered == 0

        fetched = await async_production_svc.get_task(task.id, "user_a")
        assert fetched.status == "composing"

    @pytest.mark.asyncio
    async def test_get_output_path(self, async_production_svc: ProductionService, tmp_path):
        task_dir = tmp_path / "vt" / "user_a" / "tasks" / "prod_test123"
        task_dir.mkdir(parents=True)
        (task_dir / "output.mp3").write_bytes(b"fake_audio")

        path = async_production_svc.get_output_path("prod_test123", "user_a")
        assert path is not None
        assert path.suffix == ".mp3"

    @pytest.mark.asyncio
    async def test_get_output_path_not_found(self, async_production_svc: ProductionService):
        path = async_production_svc.get_output_path("nonexistent", "user_a")
        assert path is None


# ---------------------------------------------------------------------------
# Multi-user isolation tests
# ---------------------------------------------------------------------------


class TestProductionMultiUserIsolation:
    @pytest.mark.asyncio
    async def test_user_cannot_see_other_tasks(self, async_production_svc: ProductionService):
        # auto_start=False: these tests verify list isolation, not pipeline execution.
        # Spawning _wrap_pipeline would leak a failing background task that hangs the
        # suite when another async-fixture file runs earlier in the same process.
        await async_production_svc.submit(user_id="alice", task_type="script_to_audio", payload={"voice_id": "v1", "voice_name": "Alice Voice", "text": "a"}, auto_start=False)
        await async_production_svc.submit(user_id="bob", task_type="script_to_audio", payload={"voice_id": "v2", "voice_name": "Bob Voice", "text": "b"}, auto_start=False)

        alice_tasks = await async_production_svc.list_tasks("alice")
        bob_tasks = await async_production_svc.list_tasks("bob")
        assert len(alice_tasks) == 1
        assert len(bob_tasks) == 1
        assert alice_tasks[0].user_id == "alice"
        assert bob_tasks[0].user_id == "bob"

    @pytest.mark.asyncio
    async def test_user_cannot_delete_other_task(self, async_production_svc: ProductionService):
        task = await async_production_svc.submit(user_id="alice", task_type="script_to_audio", payload={"voice_id": "v1", "voice_name": "A", "text": "a"}, auto_start=False)
        deleted = await async_production_svc.delete_task(task.id, "bob")
        assert deleted is False
        fetched = await async_production_svc.get_task(task.id, "alice")
        assert fetched is not None


# ---------------------------------------------------------------------------
# PPT extraction tests
# ---------------------------------------------------------------------------


class TestAnalyzeNotes:
    def test_sufficient_notes_60pct_30chars(self):
        from app.gateway.voice_twin.ppt_extract import SlideData, analyze_notes

        note = "这是一段足够长的备注文字，超过了三十个字符的最低要求，用于测试分析功能"
        slides = [
            SlideData(1, "Title", "", note),
            SlideData(2, "Title", "", note),
            SlideData(3, "Title", "", note),
        ]
        result = analyze_notes(slides)
        assert result.is_sufficient is True
        assert result.total_slides == 3
        assert result.slides_with_notes == 3

    def test_insufficient_notes_empty(self):
        from app.gateway.voice_twin.ppt_extract import SlideData, analyze_notes

        slides = [SlideData(1, "Title", "Body text", ""), SlideData(2, "Title", "Body", "")]
        result = analyze_notes(slides)
        assert result.is_sufficient is False
        assert result.slides_with_notes == 0

    def test_insufficient_notes_too_short(self):
        from app.gateway.voice_twin.ppt_extract import SlideData, analyze_notes

        slides = [
            SlideData(1, "Title", "", "短备注"),
            SlideData(2, "Title", "", "短备注"),
        ]
        result = analyze_notes(slides)
        assert result.is_sufficient is False

    def test_sufficient_notes_40pct_50chars(self):
        from app.gateway.voice_twin.ppt_extract import SlideData, analyze_notes

        long_note = "这是一段非常长的备注文字，它超过了五十个字符的最低要求，满足第二档的充分条件判断逻辑测试通过，所以结果是充分的"
        slides = [
            SlideData(1, "Title", "", long_note),
            SlideData(2, "Title", "", long_note),
            SlideData(3, "Title", "Body", ""),
            SlideData(4, "Title", "Body", ""),
        ]
        result = analyze_notes(slides)
        assert result.is_sufficient is True

    def test_body_fallback_sufficient_without_notes(self):
        """D8d：备注为零但正文充足的课件放行（gate 与 body fallback 执行一致）。"""
        from app.gateway.voice_twin.ppt_extract import SlideData, analyze_notes

        body = "本页正文包含足够丰富的讲解要点内容，超过二十个字符的最低要求"
        slides = [SlideData(i, "Title", body, "") for i in range(1, 4)]
        result = analyze_notes(slides)
        assert result.slides_with_notes == 0
        assert result.slides_with_content == 3
        assert result.is_sufficient is True

    def test_body_fallback_below_content_ratio_still_rejected(self):
        """D8d：正文达标页占比不足 60% 仍拒绝。"""
        from app.gateway.voice_twin.ppt_extract import SlideData, analyze_notes

        body = "本页正文包含足够丰富的讲解要点内容，超过二十个字符的最低要求"
        slides = [
            SlideData(1, "Title", body, ""),
            SlideData(2, "Title", "短", ""),
            SlideData(3, "Title", "", ""),
        ]
        result = analyze_notes(slides)
        assert result.is_sufficient is False

    def test_short_body_does_not_count_as_content(self):
        """正文 <20 字不算可朗读内容（防标题党课件放行）。"""
        from app.gateway.voice_twin.ppt_extract import SlideData, analyze_notes

        slides = [SlideData(1, "Title", "只有一点点", ""), SlideData(2, "Title", "也很少", "")]
        result = analyze_notes(slides)
        assert result.slides_with_content == 0
        assert result.is_sufficient is False


class TestSubmitPptToVideo:
    @pytest.mark.asyncio
    async def test_submit_creates_task_with_preset(self, async_production_svc: ProductionService):
        task = await async_production_svc.submit_ppt_to_video(
            user_id="user_a",
            voice_id="v1",
            voice_name="Test",
            pptx_path="/tmp/test.pptx",
            preset="zoom_in",
        )
        assert task.task_type == "ppt_to_video"
        payload = json.loads(task.payload)
        assert payload["preset"] == "zoom_in"
        assert payload["voice_name"] == "Test"

    def test_start_ppt_pipeline_method_exists(self):
        assert hasattr(ProductionService, "start_ppt_pipeline")

    @pytest.mark.asyncio
    async def test_submit_ppt_with_accept_layout_diff(self, async_production_svc: ProductionService):
        """accept_layout_diff=True stored as bool in payload (P0-2: not string)."""
        task = await async_production_svc.submit_ppt_to_video(
            user_id="user_a",
            voice_id="v1",
            voice_name="Test",
            pptx_path="/tmp/test.pptx",
            accept_layout_diff=True,
        )
        payload = json.loads(task.payload)
        assert payload["accept_layout_diff"] is True
        assert isinstance(payload["accept_layout_diff"], bool)

    @pytest.mark.asyncio
    async def test_submit_ppt_without_accept_layout_diff_defaults_false(self, async_production_svc: ProductionService):
        """Default accept_layout_diff is False (gate active)."""
        task = await async_production_svc.submit_ppt_to_video(
            user_id="user_a",
            voice_id="v1",
            voice_name="Test",
            pptx_path="/tmp/test.pptx",
        )
        payload = json.loads(task.payload)
        assert payload["accept_layout_diff"] is False


class TestResumePptToVideo:
    """C2: resume endpoint — create new task from a font-gate-failed task."""

    async def _make_failed_task(self, svc, user_id="user_a", error_message="missing-font-detected: test"):
        """Create a task directly via repo (no background pipeline race)."""
        task = await svc.repo.create(
            id=svc._gen_id(),
            user_id=user_id,
            domain=svc.domain,
            task_type="ppt_to_video",
            payload=json.dumps(
                {
                    "voice_id": "v1",
                    "voice_name": "VN",
                    "pptx_path": "/tmp/fake.pptx",
                    "preset": "none",
                    "subtitle": False,
                    "accept_layout_diff": False,
                },
                ensure_ascii=False,
            ),
        )
        await svc.repo.update(task.id, status="failed", error_message=error_message)
        return task

    @pytest.mark.asyncio
    async def test_resume_creates_new_task_with_accept_flag(self, async_production_svc: ProductionService):
        """Resume: failed font-gate task → new task with accept_layout_diff=True."""
        svc = async_production_svc
        orig = await self._make_failed_task(svc)
        # Create source file on disk
        task_dir = Path(svc._storage_dir) / "user_a" / "tasks" / orig.id
        task_dir.mkdir(parents=True, exist_ok=True)
        source = task_dir / "source.pptx"
        source.write_bytes(b"fake pptx")

        from app.gateway.routers.voice_twin_production import _try_resume_ppt

        new_task = await _try_resume_ppt(svc, orig.id, "user_a")

        assert new_task is not None
        assert new_task.id != orig.id
        payload = json.loads(new_task.payload)
        assert payload["accept_layout_diff"] is True
        # Source file copied to new task dir
        new_dir = Path(svc._storage_dir) / "user_a" / "tasks" / new_task.id
        assert (new_dir / "source.pptx").exists()

    @pytest.mark.asyncio
    async def test_resume_404_wrong_user(self, async_production_svc: ProductionService):
        """Resume: task belongs to another user → returns None (router maps to 404)."""
        svc = async_production_svc
        orig = await self._make_failed_task(svc)
        from app.gateway.routers.voice_twin_production import _try_resume_ppt

        result = await _try_resume_ppt(svc, orig.id, "user_b")
        assert result is None  # router maps to 404

    @pytest.mark.asyncio
    async def test_resume_409_not_font_gate_failure(self, async_production_svc: ProductionService):
        """Resume: task failed but not from font gate → raises ValueError (router maps to 409)."""
        svc = async_production_svc
        orig = await self._make_failed_task(svc, error_message="some other error")
        from app.gateway.routers.voice_twin_production import _try_resume_ppt

        with pytest.raises(ValueError, match="not eligible"):
            await _try_resume_ppt(svc, orig.id, "user_a")

    @pytest.mark.asyncio
    async def test_resume_410_source_deleted(self, async_production_svc: ProductionService):
        """Resume: source pptx cleaned up → raises FileNotFoundError (router maps to 410)."""
        svc = async_production_svc
        orig = await self._make_failed_task(svc)
        # No source file on disk (deliberately)
        from app.gateway.routers.voice_twin_production import _try_resume_ppt

        with pytest.raises(FileNotFoundError, match="源文件"):
            await _try_resume_ppt(svc, orig.id, "user_a")


class TestAcceptLayoutDiffRouterConversion:
    """P2 (reviewer): router endpoint converts accept_layout_diff Form string to bool
    before passing to service. If this conversion breaks, bool("false")==True
    would bypass the font gate (P0-2).

    Mutation target: replace `accept_bool = accept_layout_diff.lower() in ("true","1","yes")`
    with `accept_bool = accept_layout_diff` → service receives string "false" (truthy).
    """

    def _setup_mocks(self, tmp_path, monkeypatch):
        from app.gateway.routers import voice_twin_production as router

        captured: dict = {}

        svc = MagicMock()
        svc._storage_dir = str(tmp_path)

        fake_task = MagicMock()
        fake_task.id = "task_123"
        fake_task.user_id = "user_a"
        fake_task.task_type = "ppt_to_video"
        fake_task.status = "pending"
        fake_task.progress = 0
        fake_task.payload = "{}"
        fake_task.result = None
        fake_task.error_message = None
        fake_task.created_at = datetime.now(UTC)
        fake_task.updated_at = datetime.now(UTC)

        async def fake_submit(**kwargs):
            captured.update(kwargs)
            return fake_task

        svc.submit_ppt_to_video = fake_submit
        svc.repo = MagicMock()
        svc.repo.get = AsyncMock(return_value=fake_task)
        svc.repo.update = AsyncMock()
        svc.start_ppt_pipeline = MagicMock()

        monkeypatch.setattr(router, "_get_voice_twin_ids", lambda _req: (svc, "user_a"))
        monkeypatch.setattr(router, "_verify_voice", AsyncMock(return_value=MagicMock(voice_name="VN")))

        file_mock = MagicMock()
        file_mock.filename = "test.pptx"
        file_mock.read = AsyncMock(return_value=b"")

        return captured, file_mock, router

    def test_false_string_converted_to_bool_false(self, tmp_path, monkeypatch):
        """accept_layout_diff='false' → service receives bool False (not string)."""
        from _router_auth_helpers import call_unwrapped

        captured, file_mock, router = self._setup_mocks(tmp_path, monkeypatch)

        asyncio.run(
            call_unwrapped(
                router.submit_ppt_to_video,
                request=MagicMock(headers={}),
                file=file_mock,
                voice_id="preset:146",
                preset="none",
                subtitle="false",
                accept_layout_diff="false",
            )
        )

        assert captured.get("accept_layout_diff") is False
        assert isinstance(captured.get("accept_layout_diff"), bool)

    def test_true_string_converted_to_bool_true(self, tmp_path, monkeypatch):
        """accept_layout_diff='true' → service receives bool True."""
        from _router_auth_helpers import call_unwrapped

        captured, file_mock, router = self._setup_mocks(tmp_path, monkeypatch)

        asyncio.run(
            call_unwrapped(
                router.submit_ppt_to_video,
                request=MagicMock(headers={}),
                file=file_mock,
                voice_id="preset:146",
                preset="none",
                subtitle="false",
                accept_layout_diff="true",
            )
        )

        assert captured.get("accept_layout_diff") is True
        assert isinstance(captured.get("accept_layout_diff"), bool)


class TestFontGatePipelineIntegration:
    """P1 (reviewer): _run_ppt_to_video must actually call analyze_fonts and
    raise on risky fonts — unit tests of analyze_fonts alone don't prove the
    pipeline is wired. Mutation: disable the font gate raise → these tests go red.
    """

    @pytest.mark.asyncio
    async def test_pipeline_blocks_on_risky_font(self, async_production_svc: ProductionService, tmp_path, monkeypatch):
        """risky font + accept_layout_diff=False → ValueError("missing-font-detected").

        Mutation target: replace `if font_analysis.risky and not accept_layout_diff:`
        with `if False and ...` → this test goes red (no ValueError raised).
        """
        import app.gateway.voice_twin.ppt_extract as ppt_extract
        from app.gateway._shared.task_framework.service import ProgressUpdater
        from app.gateway.voice_twin.ppt_extract import FontAnalysis, NotesAnalysis, SlideData

        svc = async_production_svc
        task = await svc.repo.create(
            id=svc._gen_id(),
            user_id="user_a",
            domain=svc.domain,
            task_type="ppt_to_video",
            payload=json.dumps({"voice_id": "preset:146", "pptx_path": str(tmp_path / "x.pptx")}),
        )
        progress = ProgressUpdater(svc.repo, task.id)

        # Mock ppt_extract functions that _run_ppt_to_video imports at call time
        monkeypatch.setattr(
            ppt_extract,
            "extract_pptx_content",
            lambda _p: [
                SlideData(1, "Title", "Body text sufficient for fallback check here", "Notes narration content sufficient here"),
            ],
        )
        monkeypatch.setattr(ppt_extract, "analyze_notes", lambda _s: NotesAnalysis(1, 1, 50.0, True, 1))
        monkeypatch.setattr(
            ppt_extract,
            "analyze_fonts",
            lambda _p: FontAnalysis(
                declared={"微软雅黑"},
                embedded=set(),
                risky=[("微软雅黑", "Noto Sans CJK SC")],
            ),
        )

        with pytest.raises(ValueError, match="missing-font-detected"):
            await svc._run_ppt_to_video(
                task_id=task.id,
                user_id="user_a",
                payload={"voice_id": "preset:146", "voice_name": "T", "pptx_path": str(tmp_path / "x.pptx"), "preset": "none", "subtitle": False, "accept_layout_diff": False},
                task_dir=tmp_path,
                progress=progress,
            )

    @pytest.mark.asyncio
    async def test_pipeline_passes_font_gate_with_accept(self, async_production_svc: ProductionService, tmp_path, monkeypatch):
        """risky font + accept_layout_diff=True → pipeline gets PAST the font gate.

        A sentinel RuntimeError from the mocked convert function proves the gate
        was bypassed (otherwise we'd get "missing-font-detected" instead).

        Mutation target: remove `not accept_layout_diff` from the condition →
        this test goes red (ValueError("missing-font-detected") instead of RuntimeError).
        """
        import app.gateway.voice_twin.ppt_extract as ppt_extract
        from app.gateway._shared.task_framework.service import ProgressUpdater
        from app.gateway.voice_twin.ppt_extract import FontAnalysis, NotesAnalysis, SlideData

        svc = async_production_svc
        task = await svc.repo.create(
            id=svc._gen_id(),
            user_id="user_a",
            domain=svc.domain,
            task_type="ppt_to_video",
            payload=json.dumps({"voice_id": "preset:146", "pptx_path": str(tmp_path / "x.pptx")}),
        )
        progress = ProgressUpdater(svc.repo, task.id)

        monkeypatch.setattr(
            ppt_extract,
            "extract_pptx_content",
            lambda _p: [
                SlideData(1, "Title", "Body text sufficient for fallback check here", "Notes narration content sufficient here"),
            ],
        )
        monkeypatch.setattr(ppt_extract, "analyze_notes", lambda _s: NotesAnalysis(1, 1, 50.0, True, 1))
        monkeypatch.setattr(
            ppt_extract,
            "analyze_fonts",
            lambda _p: FontAnalysis(
                declared={"微软雅黑"},
                embedded=set(),
                risky=[("微软雅黑", "Noto Sans CJK SC")],
            ),
        )

        # Sentinel: proves we reached Step 2 (past the font gate)
        def _sentinel_convert(*a, **kw):
            raise RuntimeError("PAST_FONT_GATE")

        monkeypatch.setattr(ppt_extract, "convert_pptx_to_images_auto", _sentinel_convert)

        with pytest.raises(RuntimeError, match="PAST_FONT_GATE"):
            await svc._run_ppt_to_video(
                task_id=task.id,
                user_id="user_a",
                payload={"voice_id": "preset:146", "voice_name": "T", "pptx_path": str(tmp_path / "x.pptx"), "preset": "none", "subtitle": False, "accept_layout_diff": True},
                task_dir=tmp_path,
                progress=progress,
            )


class TestPptSubtitleZeroDuration:
    """T14（P0-5）：探时长失败（dur=0）→ 跳过字幕生成，worker 落回旧路径。"""

    _FAKE_MP3 = b"\xff\xfb\x00\x00" * 16  # 非 RIFF 头 → _looks_like_mp3 True

    @pytest.mark.asyncio
    async def test_zero_duration_skips_subtitle_files(self, async_production_svc: ProductionService, tmp_path, monkeypatch):
        """_get_audio_duration 返回 0.0 → 该 slide 不写 srt/ass，subtitles.json ass_file=None。"""
        import app.gateway.voice_twin.ppt_extract as ppt_extract
        from app.gateway._shared.task_framework.service import ProgressUpdater
        from app.gateway.voice_twin.ppt_extract import FontAnalysis, NotesAnalysis, SlideData

        svc = async_production_svc
        task = await svc.repo.create(
            id=svc._gen_id(),
            user_id="user_a",
            domain=svc.domain,
            task_type="ppt_to_video",
            payload=json.dumps({"voice_id": "preset:146", "pptx_path": str(tmp_path / "x.pptx")}),
        )
        progress = ProgressUpdater(svc.repo, task.id)

        monkeypatch.setattr(
            ppt_extract,
            "extract_pptx_content",
            lambda _p: [
                SlideData(1, "Title", "Body text sufficient for fallback check here", "Notes narration content sufficient here"),
            ],
        )
        monkeypatch.setattr(ppt_extract, "analyze_notes", lambda _s: NotesAnalysis(1, 1, 50.0, True, 1))
        monkeypatch.setattr(ppt_extract, "analyze_fonts", lambda _p: FontAnalysis(declared=set(), embedded=set(), risky=[]))
        monkeypatch.setattr(ppt_extract, "convert_pptx_to_images_auto", lambda _p, _d, _w: [tmp_path / "slide-1.png"])

        async def _fake_tts(**kw):
            return self._FAKE_MP3

        monkeypatch.setattr(svc, "_do_tts_synthesis", _fake_tts)
        # 探时长失败 → 0.0（探测失败/文件缺失的既有语义）
        monkeypatch.setattr("app.gateway.video_gen.orchestration.tts._get_audio_duration", lambda _p: 0.0)

        await svc._run_ppt_to_video(
            task_id=task.id,
            user_id="user_a",
            payload={"voice_id": "preset:146", "voice_name": "T", "pptx_path": str(tmp_path / "x.pptx"), "preset": "none", "subtitle": True, "accept_layout_diff": True},
            task_dir=tmp_path,
            progress=progress,
        )

        subs = json.loads((tmp_path / "subtitles.json").read_text(encoding="utf-8"))
        assert subs[0]["ass_file"] is None, f"零时长仍写出 ass 引用：{subs!r}"
        assert not (tmp_path / "sub-01.ass").exists(), "零时长仍写 ass 文件"
        assert not (tmp_path / "sub-01.srt").exists(), "零时长仍写 srt 文件"


# ---------------------------------------------------------------------------
# R5 (2026-09-09): per-slide TTS gather concurrency in _run_ppt_to_video
# ---------------------------------------------------------------------------


_MP3_BYTES = b"\xff\xfb\x00\x00" * 16  # 非 RIFF 头 → _looks_like_mp3 True
_WAV_BYTES = b"RIFF$\x00\x00\x00WAVEfmt " + b"\x00" * 32  # RIFF 头 → 走转码分支


class _RecordingProgress(ProgressUpdater):
    """记录原始 progress 入参（高水位钳制前），用于断言乱序完成不产生回退。"""

    def __init__(self, repo, task_id):
        super().__init__(repo, task_id)
        self.raw: list[int] = []

    async def __call__(self, **kwargs):
        if "progress" in kwargs:
            self.raw.append(kwargs["progress"])
        await super().__call__(**kwargs)


class TestPptSlideTtsConcurrency:
    """R5：逐页 TTS gather 并发。变异验证：还原为串行 for 循环时
    test_concurrent_slide_tts_runs_in_parallel 变红（max_active==1）。"""

    def _wire_ppt(self, monkeypatch, tmp_path, n_slides: int):
        import app.gateway.voice_twin.ppt_extract as ppt_extract
        from app.gateway.voice_twin.ppt_extract import FontAnalysis, NotesAnalysis, SlideData

        slides = [SlideData(i + 1, f"Title {i + 1}", f"Body {i + 1}", f"notes-{i + 1}") for i in range(n_slides)]
        monkeypatch.setattr(ppt_extract, "extract_pptx_content", lambda _p: slides)
        monkeypatch.setattr(ppt_extract, "analyze_notes", lambda _s: NotesAnalysis(n_slides, n_slides, 100.0, True, n_slides))
        monkeypatch.setattr(ppt_extract, "analyze_fonts", lambda _p: FontAnalysis(declared=set(), embedded=set(), risky=[]))
        monkeypatch.setattr(
            ppt_extract,
            "convert_pptx_to_images_auto",
            lambda _p, _d, _w: [tmp_path / f"slide-{i}.png" for i in range(1, n_slides + 1)],
        )
        return slides

    async def _run(self, svc, task, tmp_path, progress):
        return await svc._run_ppt_to_video(
            task_id=task.id,
            user_id="user_a",
            payload={"voice_id": "preset:146", "voice_name": "T", "pptx_path": str(tmp_path / "x.pptx"), "preset": "none", "subtitle": False, "accept_layout_diff": True},
            task_dir=tmp_path,
            progress=progress,
        )

    @pytest.mark.asyncio
    async def test_concurrent_slide_tts_runs_in_parallel(self, async_production_svc: ProductionService, tmp_path, monkeypatch):
        """4 页并发合成：TTS 并发度 >1（证明不再是串行 for），且受 _tts_semaphore=3 封顶；
        产物按页唯一、内容与页对应（页序正确性）。"""
        svc = async_production_svc
        task = await svc.repo.create(
            id=svc._gen_id(),
            user_id="user_a",
            domain=svc.domain,
            task_type="ppt_to_video",
            payload=json.dumps({"voice_id": "preset:146", "pptx_path": str(tmp_path / "x.pptx")}),
        )
        self._wire_ppt(monkeypatch, tmp_path, 4)

        active = 0
        max_active = 0

        async def _fake_tts(**kw):
            nonlocal active, max_active
            # 忠实于生产结构：_do_tts_synthesis 内部先 async with self._tts_semaphore
            # 再发起合成——mock 掉整个方法会绕开信号量，必须在这里手动复刻。
            async with svc._tts_semaphore:
                active += 1
                max_active = max(max_active, active)
                try:
                    await asyncio.sleep(0.03)
                    slide_no = kw["text"].rsplit("-", 1)[-1]
                    return _MP3_BYTES + f"-{slide_no}".encode()
                finally:
                    active -= 1

        monkeypatch.setattr(svc, "_do_tts_synthesis", _fake_tts)

        await self._run(svc, task, tmp_path, ProgressUpdater(svc.repo, task.id))

        assert 2 <= max_active <= 3, f"TTS 并发度异常: {max_active}（期望 >1 且受信号量 3 封顶）"
        audio_dir = tmp_path / "audio"
        for i in range(1, 5):
            content = (audio_dir / f"slide-{i:02d}.mp3").read_bytes()
            assert content == _MP3_BYTES + f"-{i}".encode(), f"slide-{i:02d} 内容错位"

    @pytest.mark.asyncio
    async def test_mp3_and_transcode_branches_per_slide(self, async_production_svc: ProductionService, tmp_path, monkeypatch):
        """页级分支：mp3 字节直写；RIFF 字节走 _transcode_wav 转码，且临时 wav 清理。"""
        import subprocess as _sp

        import app.gateway.voice_twin.production_service as ps_mod

        svc = async_production_svc
        task = await svc.repo.create(
            id=svc._gen_id(),
            user_id="user_a",
            domain=svc.domain,
            task_type="ppt_to_video",
            payload=json.dumps({"voice_id": "preset:146", "pptx_path": str(tmp_path / "x.pptx")}),
        )
        self._wire_ppt(monkeypatch, tmp_path, 2)

        async def _fake_tts(**kw):
            slide_no = int(kw["text"].rsplit("-", 1)[-1])
            return _MP3_BYTES if slide_no == 1 else _WAV_BYTES

        transcode_calls: list[tuple[str, str]] = []

        def _fake_transcode(wav_path, out_path):
            transcode_calls.append((str(wav_path), str(out_path)))
            Path(out_path).write_bytes(b"transcoded-mp3")
            return _sp.CompletedProcess(args=[], returncode=0)

        monkeypatch.setattr(svc, "_do_tts_synthesis", _fake_tts)
        monkeypatch.setattr(ps_mod, "_transcode_wav", _fake_transcode)

        await self._run(svc, task, tmp_path, ProgressUpdater(svc.repo, task.id))

        audio_dir = tmp_path / "audio"
        assert (audio_dir / "slide-01.mp3").read_bytes() == _MP3_BYTES  # 直写分支
        assert (audio_dir / "slide-02.mp3").read_bytes() == b"transcoded-mp3"  # 转码分支
        assert len(transcode_calls) == 1 and transcode_calls[0][0].endswith("_slide-02.wav")
        assert not (audio_dir / "_slide-02.wav").exists(), "转码临时 wav 未清理（D6）"

    @pytest.mark.asyncio
    async def test_progress_monotonic_under_out_of_order_completion(self, async_production_svc: ProductionService, tmp_path, monkeypatch):
        """乱序完成（后面的页先完成）：进度必须按完成计数单调递增——
        记录的是 ProgressUpdater 高水位钳制前的原始入参。"""
        svc = async_production_svc
        task = await svc.repo.create(
            id=svc._gen_id(),
            user_id="user_a",
            domain=svc.domain,
            task_type="ppt_to_video",
            payload=json.dumps({"voice_id": "preset:146", "pptx_path": str(tmp_path / "x.pptx")}),
        )
        self._wire_ppt(monkeypatch, tmp_path, 3)

        async def _fake_tts(**kw):
            slide_no = int(kw["text"].rsplit("-", 1)[-1])
            await asyncio.sleep(0.02 * (4 - slide_no))  # 页越大越快 → 乱序完成
            return _MP3_BYTES

        monkeypatch.setattr(svc, "_do_tts_synthesis", _fake_tts)

        progress = _RecordingProgress(svc.repo, task.id)
        await self._run(svc, task, tmp_path, progress)

        synth_phase = [p for p in progress.raw if p >= 25]
        assert synth_phase, "未捕获合成阶段进度"
        assert all(a <= b for a, b in zip(synth_phase, synth_phase[1:])), f"乱序完成导致进度回退: {progress.raw}"

    @pytest.mark.asyncio
    async def test_single_slide_failure_raises_first_error_and_cleans_up(self, async_production_svc: ProductionService, tmp_path, monkeypatch):
        """多页失败：收集全部异常后 raise 按页序的第一个（与串行版首个失败语义一致）；
        已成功页的音频清理。"""
        svc = async_production_svc
        task = await svc.repo.create(
            id=svc._gen_id(),
            user_id="user_a",
            domain=svc.domain,
            task_type="ppt_to_video",
            payload=json.dumps({"voice_id": "preset:146", "pptx_path": str(tmp_path / "x.pptx")}),
        )
        self._wire_ppt(monkeypatch, tmp_path, 3)

        async def _fake_tts(**kw):
            slide_no = int(kw["text"].rsplit("-", 1)[-1])
            if slide_no == 2:
                await asyncio.sleep(0.05)  # 后失败（墙钟上晚于第 3 页）
                raise RuntimeError("TTS boom slide-2")
            if slide_no == 3:
                raise RuntimeError("TTS boom slide-3")  # 先失败（墙钟上早）
            return _MP3_BYTES

        monkeypatch.setattr(svc, "_do_tts_synthesis", _fake_tts)

        with pytest.raises(RuntimeError, match="TTS boom slide-2"):
            await self._run(svc, task, tmp_path, ProgressUpdater(svc.repo, task.id))

        audio_dir = tmp_path / "audio"
        assert not (audio_dir / "slide-01.mp3").exists(), "失败后未清理已成功页音频"

    @pytest.mark.asyncio
    async def test_multi_page_concurrent_transcode_no_deadlock(self, async_production_svc: ProductionService, tmp_path, monkeypatch):
        """评审补项：多页同时触发转码（WAV 降级产物 ×5 并发走 run_ffmpeg 共享线程池）
        不死锁不超时；并发度 >1 证明真走了池并发。"""
        import subprocess as _sp
        import time as _time

        import app.gateway.voice_twin.production_service as ps_mod

        svc = async_production_svc
        task = await svc.repo.create(
            id=svc._gen_id(),
            user_id="user_a",
            domain=svc.domain,
            task_type="ppt_to_video",
            payload=json.dumps({"voice_id": "preset:146", "pptx_path": str(tmp_path / "x.pptx")}),
        )
        self._wire_ppt(monkeypatch, tmp_path, 5)

        async def _fake_tts(**kw):
            return _WAV_BYTES  # 全部页触发转码分支

        transcode_active = 0
        transcode_max = 0

        def _slow_transcode(wav_path, out_path):
            nonlocal transcode_active, transcode_max
            transcode_active += 1
            transcode_max = max(transcode_max, transcode_active)
            try:
                _time.sleep(0.05)  # 真占 ffmpeg 池线程——模拟转码耗时
                Path(out_path).write_bytes(b"transcoded")
                return _sp.CompletedProcess(args=[], returncode=0)
            finally:
                transcode_active -= 1

        monkeypatch.setattr(svc, "_do_tts_synthesis", _fake_tts)
        monkeypatch.setattr(ps_mod, "_transcode_wav", _slow_transcode)

        # wait_for 兜底：死锁时 10s 内显式失败，而不是挂死整个套件
        await asyncio.wait_for(self._run(svc, task, tmp_path, ProgressUpdater(svc.repo, task.id)), timeout=10)

        audio_dir = tmp_path / "audio"
        assert len(list(audio_dir.glob("slide-*.mp3"))) == 5
        assert transcode_max >= 2, f"转码未并发执行（max={transcode_max}），未覆盖共享池并发路径"


# ---------------------------------------------------------------------------
# strip_markdown tests
# ---------------------------------------------------------------------------


class TestStripMarkdown:
    def test_plain_text_unchanged(self):
        assert strip_markdown("Hello world") == "Hello world"

    def test_bold_stripped(self):
        assert strip_markdown("This is **bold** text") == "This is bold text"

    def test_italic_stripped(self):
        assert strip_markdown("This is *italic* text") == "This is italic text"

    def test_heading_stripped(self):
        assert strip_markdown("# Main Title") == "Main Title"
        assert strip_markdown("## Section") == "Section"
        assert strip_markdown("### Subsection") == "Subsection"

    def test_link_stripped(self):
        assert strip_markdown("[Click here](https://example.com)") == "Click here"

    def test_image_stripped(self):
        assert strip_markdown("![Alt text](image.png)") == "Alt text"

    def test_code_block_stripped(self):
        text = "Before\n```python\nprint('hello')\n```\nAfter"
        result = strip_markdown(text)
        assert "print" not in result
        assert "Before" in result
        assert "After" in result

    def test_inline_code_stripped(self):
        assert strip_markdown("Use `pip install` to install") == "Use pip install to install"

    def test_list_markers_stripped(self):
        assert strip_markdown("- Item one\n- Item two") == "Item one\nItem two"
        assert strip_markdown("1. First\n2. Second") == "First\nSecond"

    def test_blockquote_stripped(self):
        assert strip_markdown("> This is a quote") == "This is a quote"

    def test_horizontal_rule_stripped(self):
        text = "Above\n---\nBelow"
        result = strip_markdown(text)
        assert "---" not in result
        assert "Above" in result
        assert "Below" in result

    def test_html_stripped(self):
        assert strip_markdown("<b>Bold</b> text") == "Bold text"

    def test_table_pipes_stripped(self):
        result = strip_markdown("A | B | C")
        assert "|" not in result
        assert "A" in result and "B" in result and "C" in result

    def test_multiple_blank_lines_collapsed(self):
        text = "Para 1\n\n\n\n\nPara 2"
        assert "\n\n\n" not in strip_markdown(text)

    def test_combined_markdown(self):
        text = "# Title\n\n## Section\n\nThis is **bold** and *italic*.\n\n- List item\n\n[Link](http://example.com)"
        result = strip_markdown(text)
        assert "**" not in result
        assert "*" not in result
        assert "#" not in result
        assert "- " not in result
        assert "[" not in result
        assert "Title" in result
        assert "Section" in result
        assert "bold" in result
        assert "Link" in result

    def test_empty_string(self):
        assert strip_markdown("") == ""

    def test_chinese_text_preserved(self):
        text = "这是一段中文文字，不需要做任何处理。"
        assert strip_markdown(text) == text

    def test_chinese_markdown_stripped(self):
        text = "# 第一章 概述\n\n这是**重点内容**和*次要内容*。\n\n- 项目一\n- 项目二"
        result = strip_markdown(text)
        assert result == "第一章 概述\n\n这是重点内容和次要内容。\n\n项目一\n项目二"


# ---------------------------------------------------------------------------
# Concurrent TTS synthesis tests
# ---------------------------------------------------------------------------


class TestDoTtsSynthesis:
    """Tests for _do_tts_synthesis: the production TTS router with retry and
    rate-limit handling. (Previously named TestSynthesizeWithRetry, which tested
    the now-removed dead-code wrapper; the retry logic lives here now.)
    """

    @pytest.mark.asyncio
    async def test_cloned_success_first_try(self, async_production_svc: ProductionService):
        from types import SimpleNamespace

        mock_client = MagicMock()
        mock_client.synthesize_sse = AsyncMock(return_value=b"audio_data")
        async_production_svc.voice_service.client = mock_client
        row = SimpleNamespace(speaker_id="spk_1", model_type=5, language=0)

        result = await async_production_svc._do_tts_synthesis("vt-1", "hello", row=row)
        assert result == b"audio_data"
        assert mock_client.synthesize_sse.call_count == 1

    @pytest.mark.asyncio
    async def test_cloned_retries_on_rate_limit(self, async_production_svc: ProductionService):
        from types import SimpleNamespace

        mock_client = MagicMock()
        mock_client.synthesize_sse = AsyncMock(
            side_effect=[
                VolcengineTTSHTTPError(429, "Too Many Requests"),
                b"audio_data",
            ]
        )
        async_production_svc.voice_service.client = mock_client
        async_production_svc._TTS_RETRY_BASE_DELAY = 0.01
        row = SimpleNamespace(speaker_id="spk_1", model_type=5, language=0)

        result = await async_production_svc._do_tts_synthesis("vt-1", "hello", row=row)
        assert result == b"audio_data"
        assert mock_client.synthesize_sse.call_count == 2

    @pytest.mark.asyncio
    async def test_cloned_raises_non_rate_error_immediately(self, async_production_svc: ProductionService):
        from types import SimpleNamespace

        mock_client = MagicMock()
        mock_client.synthesize_sse = AsyncMock(
            side_effect=RuntimeError("Internal server error"),
        )
        async_production_svc.voice_service.client = mock_client
        row = SimpleNamespace(speaker_id="spk_1", model_type=5, language=0)

        with pytest.raises(RuntimeError, match="Internal server error"):
            await async_production_svc._do_tts_synthesis("vt-1", "hello", row=row)
        assert mock_client.synthesize_sse.call_count == 1

    @pytest.mark.asyncio
    async def test_cloned_exhausts_retries(self, async_production_svc: ProductionService):
        from types import SimpleNamespace

        mock_client = MagicMock()
        mock_client.synthesize_sse = AsyncMock(
            side_effect=VolcengineTTSHTTPError(429, "rate limit"),
        )
        async_production_svc.voice_service.client = mock_client
        async_production_svc._TTS_RETRY_BASE_DELAY = 0.01
        row = SimpleNamespace(speaker_id="spk_1", model_type=5, language=0)

        with pytest.raises(RuntimeError, match="failed after 3 retries"):
            await async_production_svc._do_tts_synthesis("vt-1", "hello", row=row)
        assert mock_client.synthesize_sse.call_count == 3

    @pytest.mark.asyncio
    async def test_semaphore_limits_concurrency(self, async_production_svc: ProductionService):
        """Verify Semaphore(3) allows at most 3 concurrent synthesize calls."""
        from types import SimpleNamespace

        async_production_svc._tts_semaphore = asyncio.Semaphore(3)
        active_count = 0
        max_active = 0

        async def _mock_synthesize(**kwargs):
            nonlocal active_count, max_active
            active_count += 1
            max_active = max(max_active, active_count)
            await asyncio.sleep(0.05)  # Simulate work
            active_count -= 1
            return b"audio"

        mock_client = MagicMock()
        mock_client.synthesize_sse = _mock_synthesize
        async_production_svc.voice_service.client = mock_client
        row = SimpleNamespace(speaker_id="spk_1", model_type=5, language=0)

        # Launch 6 concurrent synthesize calls
        tasks = [async_production_svc._do_tts_synthesis("vt-1", f"chunk_{i}", row=row) for i in range(6)]
        results = await asyncio.gather(*tasks)
        assert len(results) == 6
        assert max_active <= 3

    @pytest.mark.asyncio
    async def test_retry_ignores_accurate_substring(self, async_production_svc: ProductionService):
        """错误消息含 "accurate"（如质量校验）不得被 "rate" 子串误判为限流而无效重试。"""
        from types import SimpleNamespace

        mock_client = MagicMock()
        mock_client.synthesize_sse = AsyncMock(
            side_effect=RuntimeError("audio not accurate enough"),
        )
        async_production_svc.voice_service.client = mock_client
        async_production_svc._TTS_RETRY_BASE_DELAY = 0.01
        row = SimpleNamespace(speaker_id="spk_1", model_type=5, language=0)

        with pytest.raises(RuntimeError, match="audio not accurate enough"):
            await async_production_svc._do_tts_synthesis("vt-1", "hello", row=row)
        assert mock_client.synthesize_sse.call_count == 1

    @pytest.mark.asyncio
    async def test_retry_covers_transport_errors(self, async_production_svc: ProductionService):
        """httpx.ConnectError 等传输错误纳入瞬时重试（曾一次抖动即失败）。"""
        from types import SimpleNamespace

        import httpx

        mock_client = MagicMock()
        mock_client.synthesize_sse = AsyncMock(
            side_effect=[
                httpx.ConnectError("connection reset by peer"),
                b"audio_data",
            ]
        )
        async_production_svc.voice_service.client = mock_client
        async_production_svc._TTS_RETRY_BASE_DELAY = 0.01
        row = SimpleNamespace(speaker_id="spk_1", model_type=5, language=0)

        result = await async_production_svc._do_tts_synthesis("vt-1", "hello", row=row)
        assert result == b"audio_data"
        assert mock_client.synthesize_sse.call_count == 2

    @pytest.mark.asyncio
    async def test_retry_covers_timeout(self, async_production_svc: ProductionService):
        """asyncio.TimeoutError（T1 SSE 整调用超时）必须可重试——修前 production 路径超时后不重试。"""
        from types import SimpleNamespace

        mock_client = MagicMock()
        mock_client.synthesize_sse = AsyncMock(side_effect=TimeoutError())
        async_production_svc.voice_service.client = mock_client
        async_production_svc._TTS_RETRY_BASE_DELAY = 0.01
        row = SimpleNamespace(speaker_id="spk_1", model_type=5, language=0)

        with pytest.raises(RuntimeError, match="failed after 3 retries"):
            await async_production_svc._do_tts_synthesis("vt-1", "hello", row=row)
        assert mock_client.synthesize_sse.call_count == 3


class TestDoubaoTtsSynthesis:
    """Tests for doubao voice routing in _do_tts_synthesis (Stage 2a breadth).

    doubao: prefixed voice_ids route to DoubaoTTSClient (openspeech seed-tts-2.0).
    The speaker is parsed from the voice_id (the part after 'doubao:').
    Access key comes from VoiceCloneConfig.access_key (single openspeech account).
    """

    @pytest.mark.asyncio
    async def test_doubao_uses_doubao_client(self, async_production_svc: ProductionService):
        """doubao: voice_id → DoubaoTTSClient.synthesize_sse called with speaker."""
        mock_doubao = MagicMock()
        mock_doubao.synthesize_sse = AsyncMock(return_value=b"doubao_audio")
        async_production_svc.voice_service.doubao_client = mock_doubao

        result = await async_production_svc._do_tts_synthesis("doubao:zh_female_qingxin", "你好世界")
        assert result == b"doubao_audio"
        mock_doubao.synthesize_sse.assert_called_once()
        call_kwargs = mock_doubao.synthesize_sse.call_args.kwargs
        assert call_kwargs["speaker"] == "zh_female_qingxin"
        assert call_kwargs["text"] == "你好世界"

    @pytest.mark.asyncio
    async def test_doubao_raises_when_client_not_configured(self, async_production_svc: ProductionService):
        """doubao 未配置且整条降级链（qihu/edge）也不可用 → RuntimeError（user-visible）。

        A2 起 "not configured" 是可降级错误：doubao 缺 key 会依次尝试 qihu→edge；
        三引擎全缺才抛错（cause 链保留原始的 not configured）。
        """
        async_production_svc.voice_service.doubao_client = None
        async_production_svc.voice_service.qihu_client = None
        async_production_svc.voice_service.edge_client = None

        with pytest.raises(RuntimeError, match="edge fallback unavailable"):
            await async_production_svc._do_tts_synthesis("doubao:zh_female_qingxin", "hello")

    @pytest.mark.asyncio
    async def test_doubao_retries_on_rate_limit(self, async_production_svc: ProductionService):
        """429 errors are retried (consistent with qihu/clone paths)."""
        mock_doubao = MagicMock()
        mock_doubao.synthesize_sse = AsyncMock(
            side_effect=[
                DoubaoTTSHTTPError(429, "Too Many Requests"),
                b"doubao_audio",
            ]
        )
        async_production_svc.voice_service.doubao_client = mock_doubao
        async_production_svc._TTS_RETRY_BASE_DELAY = 0.01

        result = await async_production_svc._do_tts_synthesis("doubao:spk1", "hello")
        assert result == b"doubao_audio"
        assert mock_doubao.synthesize_sse.call_count == 2

    @pytest.mark.asyncio
    async def test_doubao_retries_on_5xx(self, async_production_svc: ProductionService):
        """5xx server errors are retried (consistent with qihu/clone paths)."""
        mock_doubao = MagicMock()
        mock_doubao.synthesize_sse = AsyncMock(
            side_effect=[
                DoubaoTTSHTTPError(503, "Service Unavailable"),
                b"doubao_audio",
            ]
        )
        async_production_svc.voice_service.doubao_client = mock_doubao
        async_production_svc._TTS_RETRY_BASE_DELAY = 0.01

        result = await async_production_svc._do_tts_synthesis("doubao:spk1", "hello")
        assert result == b"doubao_audio"
        assert mock_doubao.synthesize_sse.call_count == 2

    @pytest.mark.asyncio
    async def test_preset_still_uses_qihu(self, async_production_svc: ProductionService):
        """preset: voice_id still routes to qihu_client (no regression)."""
        mock_qihu = MagicMock()
        mock_qihu.synthesize_sse = AsyncMock(return_value=b"qihu_audio")
        async_production_svc.voice_service.qihu_client = mock_qihu

        result = await async_production_svc._do_tts_synthesis("preset:146", "hello")
        assert result == b"qihu_audio"
        mock_qihu.synthesize_sse.assert_called_once()

    @pytest.mark.asyncio
    async def test_clone_still_uses_volcengine(self, async_production_svc: ProductionService):
        """Clone voice_id still routes to volcengine client (no regression)."""
        from types import SimpleNamespace

        mock_client = MagicMock()
        mock_client.synthesize_sse = AsyncMock(return_value=b"clone_audio")
        async_production_svc.voice_service.client = mock_client
        row = SimpleNamespace(speaker_id="spk_1", model_type=5, language=0)

        result = await async_production_svc._do_tts_synthesis("vt-1", "hello", row=row)
        assert result == b"clone_audio"
        mock_client.synthesize_sse.assert_called_once()


class TestIsDoubaoVoiceId:
    """Tests for is_doubao_voice_id helper (Stage 2a breadth)."""

    def test_doubao_prefix_returns_true(self):
        from app.gateway.voice_twin.preset_voices import is_doubao_voice_id

        assert is_doubao_voice_id("doubao:zh_female_qingxin") is True

    def test_preset_prefix_returns_false(self):
        from app.gateway.voice_twin.preset_voices import is_doubao_voice_id

        assert is_doubao_voice_id("preset:146") is False

    def test_plain_voice_id_returns_false(self):
        from app.gateway.voice_twin.preset_voices import is_doubao_voice_id

        assert is_doubao_voice_id("zh-CN-YunjianNeural") is False
        assert is_doubao_voice_id("vt-abc123") is False

    def test_empty_string_returns_false(self):
        from app.gateway.voice_twin.preset_voices import is_doubao_voice_id

        assert is_doubao_voice_id("") is False

    """Tests for _run_script_to_audio concurrent chunk synthesis."""

    @pytest.fixture
    def mock_voice_row(self):
        row = MagicMock()
        row.speaker_id = "spk_1"
        row.model_type = 5
        row.language = 0
        row.user_id = "user_a"
        row.status = "ready"
        return row

    @pytest.mark.asyncio
    async def test_concurrent_synthesis_orders_chunks(self, async_production_svc: ProductionService, tmp_path, mock_voice_row):
        """Verify chunks are synthesized concurrently and ordered correctly."""

        async def _mock_synthesize(**kwargs):
            await asyncio.sleep(0.01)
            return b"audio_chunk"

        mock_client = MagicMock()
        mock_client.synthesize_sse = _mock_synthesize
        async_production_svc.voice_service.client = mock_client
        async_production_svc.voice_service.repo.get_by_id = AsyncMock(return_value=mock_voice_row)

        task_dir = tmp_path / "task_test"
        task_dir.mkdir()

        async def _progress(**kwargs):
            pass

        _progress.set_result = AsyncMock()

        # Use 6000-char paragraphs so split_text produces 2 chunks
        text = "A" * 3000 + "\n\n" + "B" * 3000

        def _fake_ffmpeg_concat(chunk_paths, output_path):
            output_path.write_bytes(b"concatenated_audio")

        with patch(
            "app.gateway.voice_twin.production_service._ffmpeg_concat",
            side_effect=_fake_ffmpeg_concat,
        ):
            result = await async_production_svc._run_script_to_audio(
                task_id="test_concurrent",
                user_id="user_a",
                payload={
                    "voice_id": "v1",
                    "text": text,
                    "speed_ratio": 1.0,
                    "output_format": "mp3",
                },
                task_dir=task_dir,
                progress=_progress,
            )
        assert result is None

        # Output file should exist
        output = task_dir / "output.mp3"
        assert output.exists()

        # Chunk files should be cleaned up after concat
        audio_dir = task_dir / "audio"
        assert not audio_dir.exists() or not any(audio_dir.iterdir())

    @pytest.mark.asyncio
    async def test_partial_failure_cleans_up(self, async_production_svc: ProductionService, tmp_path, mock_voice_row):
        """Verify partial failures clean up successful chunk files."""
        call_count = 0

        async def _mock_synthesize(**kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise RuntimeError("TTS failed for chunk 2")
            return b"audio_data"

        mock_client = MagicMock()
        mock_client.synthesize_sse = _mock_synthesize
        async_production_svc.voice_service.client = mock_client
        async_production_svc.voice_service.repo.get_by_id = AsyncMock(return_value=mock_voice_row)

        task_dir = tmp_path / "task_fail"
        task_dir.mkdir()

        async def _progress(**kwargs):
            pass

        _progress.set_result = AsyncMock()

        # 3 paragraphs, each long enough to be its own chunk
        text = "A" * 3000 + "\n\n" + "B" * 3000 + "\n\n" + "C" * 3000

        with pytest.raises(RuntimeError, match="TTS synthesis failed"):
            await async_production_svc._run_script_to_audio(
                task_id="test_fail",
                user_id="user_a",
                payload={
                    "voice_id": "v1",
                    "text": text,
                    "speed_ratio": 1.0,
                    "output_format": "mp3",
                },
                task_dir=task_dir,
                progress=_progress,
            )

        # Chunk files should be cleaned up after failure
        audio_dir = task_dir / "audio"
        remaining = list(audio_dir.iterdir()) if audio_dir.exists() else []
        assert len(remaining) == 0

    @pytest.mark.asyncio
    async def test_preset_voice_transcodes_wav_to_mp3(self, async_production_svc: ProductionService, tmp_path):
        """Preset (360 TTS) returns WAV bytes; chunks must be transcoded to mp3.

        Regression guard for the bug where preset chunks were stored as .wav but
        renamed/concatenated into output.mp3, producing a corrupt container.
        """
        from types import SimpleNamespace

        # 360 TTS returns WAV; we do NOT need real ffmpeg — mock the transcoder.
        async_production_svc.voice_service.qihu_client = MagicMock()
        async_production_svc.voice_service.qihu_client.synthesize_sse = AsyncMock(return_value=b"RIFF...WAV-bytes")

        task_dir = tmp_path / "task_preset"
        task_dir.mkdir()

        async def _progress(**kwargs):
            pass

        _progress.set_result = AsyncMock()

        transcode_inputs: list = []

        def _fake_wav_to_mp3(wav_path, mp3_path):
            transcode_inputs.append((str(wav_path), str(mp3_path)))
            # Simulate ffmpeg writing a real mp3 file
            mp3_path.write_bytes(b"FAKE-MP3-FRAMES")
            return SimpleNamespace(returncode=0, stderr=b"")

        with patch(
            "app.gateway.voice_twin.production_service._transcode_wav",
            side_effect=_fake_wav_to_mp3,
        ):
            result = await async_production_svc._run_script_to_audio(
                task_id="test_preset",
                user_id="user_a",
                payload={
                    "voice_id": "preset:146",
                    "text": "短文本单 chunk",
                    "speed_ratio": 1.0,
                    "output_format": "mp3",
                },
                task_dir=task_dir,
                progress=_progress,
            )
        assert result is None

        # Output must be output.mp3 (not a .wav renamed to .mp3)
        output = task_dir / "output.mp3"
        assert output.exists()
        assert output.read_bytes() == b"FAKE-MP3-FRAMES"

        # 360 TTS WAS called (preset path), Volcengine was NOT
        async_production_svc.voice_service.qihu_client.synthesize_sse.assert_awaited_once()
        async_production_svc.voice_service.client.synthesize_sse.assert_not_called()

        # The transcoder was invoked with a _chunk-*.wav input → *.mp3 output
        assert len(transcode_inputs) == 1
        assert transcode_inputs[0][0].endswith(".wav")
        assert transcode_inputs[0][1].endswith("chunk-000.mp3")

    @pytest.mark.asyncio
    async def test_doubao_degraded_to_qihu_transcodes_wav(self, async_production_svc: ProductionService, tmp_path):
        """doubao→qihu 降级产物是 WAV——script chunks 消费层必须转码为 mp3（bug #1 核心场景）。

        此前 needs_transcode 按请求 voice_id (doubao) 判定为 False，降级产的 WAV
        不转码直写 chunk-NNN.mp3 → 多 chunk concat -c copy 容器不匹配必败。
        修复后按实际字节嗅探：WAV 字节正确走转码分支。
        """
        from types import SimpleNamespace

        from app.gateway.voice_twin.doubao_tts_client import DoubaoTTSHTTPError

        async_production_svc._TTS_RETRY_BASE_DELAY = 0.001
        async_production_svc.voice_service.doubao_client = MagicMock()
        async_production_svc.voice_service.doubao_client.synthesize_sse = AsyncMock(
            side_effect=DoubaoTTSHTTPError(503, "service down"),
        )
        async_production_svc.voice_service.qihu_client = MagicMock()
        async_production_svc.voice_service.qihu_client.synthesize_sse = AsyncMock(
            return_value=b"RIFF\x00\x00\x00\x00WAVEfmt ",
        )

        task_dir = tmp_path / "task_degrade"
        task_dir.mkdir()

        async def _progress(**kwargs):
            pass

        _progress.set_result = AsyncMock()

        transcode_inputs: list = []

        def _fake_wav_to_mp3(wav_path, mp3_path):
            transcode_inputs.append((str(wav_path), str(mp3_path)))
            mp3_path.write_bytes(b"FAKE-MP3-FRAMES")
            return SimpleNamespace(returncode=0, stderr=b"")

        with patch(
            "app.gateway.voice_twin.production_service._transcode_wav",
            side_effect=_fake_wav_to_mp3,
        ):
            result = await async_production_svc._run_script_to_audio(
                task_id="test_degrade",
                user_id="user_a",
                payload={
                    "voice_id": "doubao:zh_male_m191_uranus_bigtts",
                    "text": "短文本",
                    "speed_ratio": 1.0,
                    "output_format": "mp3",
                },
                task_dir=task_dir,
                progress=_progress,
            )
        assert result is None

        # Output must be valid mp3 (transcoded), not raw WAV in a .mp3 container
        output = task_dir / "output.mp3"
        assert output.exists()
        assert output.read_bytes() == b"FAKE-MP3-FRAMES"

        # Transcoder was invoked with .wav source (qihu returned WAV, not mp3)
        assert len(transcode_inputs) == 1
        assert transcode_inputs[0][0].endswith(".wav")
        assert transcode_inputs[0][1].endswith("chunk-000.mp3")

        # doubao was retried 3× (call_with_tts_retry), then degraded to qihu
        assert async_production_svc.voice_service.doubao_client.synthesize_sse.await_count == 3
        async_production_svc.voice_service.qihu_client.synthesize_sse.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_script_to_audio_transcode_failure_raises(self, async_production_svc, tmp_path):
        """转码 rc≠0 → RuntimeError（曾第二调用点 save raw bytes as-is 静默兜底，T9 改 fail-loud；评审跟进补失败路径）。"""
        from types import SimpleNamespace

        async_production_svc.voice_service.qihu_client = MagicMock()
        async_production_svc.voice_service.qihu_client.synthesize_sse = AsyncMock(return_value=b"RIFF...WAV-bytes")

        task_dir = tmp_path / "task_preset_fail"
        task_dir.mkdir()

        async def _progress(**kwargs):
            pass

        _progress.set_result = AsyncMock()

        def _fake_transcode_fail(wav_path, mp3_path):
            return SimpleNamespace(returncode=1, stderr=b"ffmpeg boom")

        with (
            patch(
                "app.gateway.voice_twin.production_service._transcode_wav",
                side_effect=_fake_transcode_fail,
            ),
            pytest.raises(RuntimeError, match="transcode failed"),
        ):
            await async_production_svc._run_script_to_audio(
                task_id="test_preset_fail",
                user_id="user_a",
                payload={
                    "voice_id": "preset:146",
                    "text": "短文本单 chunk",
                    "speed_ratio": 1.0,
                    "output_format": "mp3",
                },
                task_dir=task_dir,
                progress=_progress,
            )


# ---------------------------------------------------------------------------


class TestTaskTitle:
    """Verify title is generated and stored correctly."""

    def test_short_text_uses_full_text(self):
        """Short text (< 20 chars) should use full text as title."""
        title = ProductionService._make_title("Hello world")
        assert title == "Hello world"

    def test_long_text_truncated(self):
        """Long text should be truncated to 20 chars + '...'."""
        text = "A" * 30
        title = ProductionService._make_title(text)
        assert title == "A" * 20 + "..."
        assert len(title) == 23

    def test_custom_title_preserved(self):
        """Custom title should be used as-is."""
        title = ProductionService._make_title("anything", title="My Custom Title")
        assert title == "My Custom Title"

    def test_newlines_stripped(self):
        """Newlines in text should be replaced with spaces for title."""
        title = ProductionService._make_title("line1\nline2\nline3")
        assert "\n" not in title

    def test_empty_custom_title_falls_back(self):
        """Empty string title should fall back to auto-generation."""
        title = ProductionService._make_title("Some text here", title="")
        assert title == "Some text here"

    def test_title_in_response(self):
        """ProductionTaskResponse.from_task_row should include title."""
        from app.gateway.voice_twin.production_schemas import ProductionTaskResponse

        row = MagicMock()
        row.id = "t1"
        row.user_id = "u1"
        row.task_type = "script_to_audio"
        row.status = "ready"
        row.progress = 100
        row.payload = json.dumps({"voice_id": "v1", "voice_name": "wjx", "title": "测试标题"})
        row.result = "{}"
        row.error_message = None
        row.created_at = None
        row.updated_at = None

        resp = ProductionTaskResponse.from_task_row(row)
        assert resp.title == "测试标题"

    def test_response_without_title(self):
        """If no title in payload, response title should be None."""
        from app.gateway.voice_twin.production_schemas import ProductionTaskResponse

        row = MagicMock()
        row.id = "t1"
        row.user_id = "u1"
        row.task_type = "script_to_audio"
        row.status = "ready"
        row.progress = 100
        row.payload = json.dumps({"voice_id": "v1", "voice_name": "wjx"})
        row.result = "{}"
        row.error_message = None
        row.created_at = None
        row.updated_at = None

        resp = ProductionTaskResponse.from_task_row(row)
        assert resp.title is None


# ---------------------------------------------------------------------------
# Timezone tests
# ---------------------------------------------------------------------------


class TestEnsureUtc:
    """Verify datetime timezone handling."""

    def test_naive_datetime_gets_utc(self):
        from app.gateway._shared.task_framework.schemas import ensure_utc

        naive = datetime(2026, 6, 10, 6, 52, 55)
        aware = ensure_utc(naive)
        assert aware is not None
        assert aware.tzinfo is not None
        assert str(aware) == "2026-06-10 06:52:55+00:00"

    def test_aware_datetime_unchanged(self):
        from app.gateway._shared.task_framework.schemas import ensure_utc

        aware_in = datetime(2026, 6, 10, 6, 52, 55, tzinfo=UTC)
        aware_out = ensure_utc(aware_in)
        assert aware_out == aware_in

    def test_none_returns_none(self):
        from app.gateway._shared.task_framework.schemas import ensure_utc

        assert ensure_utc(None) is None

    def test_iso_string_gets_utc(self):
        """ISO-8601 str (e.g. FakeRow/SQLite created_at) must be parsed to aware UTC.

        Guards the str branch of ensure_utc exercised by from_task_row().
        """
        from app.gateway._shared.task_framework.schemas import ensure_utc

        aware = ensure_utc("2026-06-07T12:00:00")
        assert aware is not None
        assert aware.tzinfo is not None
        assert aware == datetime(2026, 6, 7, 12, 0, 0, tzinfo=UTC)

    def test_response_serializes_with_z_suffix(self):
        """Pydantic serialization of aware datetime should end with 'Z'."""
        from app.gateway.voice_twin.production_schemas import ProductionTaskResponse

        row = MagicMock()
        row.id = "t1"
        row.user_id = "u1"
        row.task_type = "script_to_audio"
        row.status = "ready"
        row.progress = 100
        row.payload = "{}"
        row.result = "{}"
        row.error_message = None
        row.created_at = datetime(2026, 6, 10, 6, 52, 55)
        row.updated_at = datetime(2026, 6, 10, 6, 52, 56)

        resp = ProductionTaskResponse.from_task_row(row)
        json_str = resp.model_dump_json()
        assert '"created_at":"2026-06-10T06:52:55Z"' in json_str
        assert '"updated_at":"2026-06-10T06:52:56Z"' in json_str


# ---------------------------------------------------------------------------
# ProductionService.preview_voice
# ---------------------------------------------------------------------------


def _make_prod_service(tmp_path) -> ProductionService:
    """Build a ProductionService with mocked voice_service + in-memory repo."""
    repo = MagicMock()
    repo.get_by_id = AsyncMock()

    voice_service = MagicMock()
    voice_service.client = MagicMock()
    voice_service.client.synthesize_sse = AsyncMock(return_value=b"CLONED-MP3")
    voice_service.qihu_client = MagicMock()
    # 真实 360 产物是 RIFF 头 WAV——preview 按内容嗅探媒体类型（A2 降级兜底产物
    # 是 mp3），mock 必须像真实字节，否则测的是生产里不存在的输入。
    voice_service.qihu_client.synthesize_sse = AsyncMock(return_value=b"RIFF\x24\x00\x00\x00WAVEfmt PRESET")
    voice_service.repo = repo

    svc = ProductionService(
        repo=MagicMock(),  # task repo (unused by preview)
        voice_service=voice_service,
        storage_dir=str(tmp_path / "prod"),
    )
    return svc


class TestPreviewVoice:
    def test_preset_voice_routes_to_qihu_and_returns_wav(self, tmp_path):
        svc = _make_prod_service(tmp_path)
        audio, media_type = asyncio.run(svc.preview_voice("preset:146", "你好", user_id="u1"))
        assert audio == b"RIFF\x24\x00\x00\x00WAVEfmt PRESET"
        assert media_type == "audio/wav"
        svc.voice_service.qihu_client.synthesize_sse.assert_awaited_once()
        svc.voice_service.client.synthesize_sse.assert_not_called()

    def test_cloned_voice_routes_to_volcengine_and_returns_mp3(self, tmp_path):
        from types import SimpleNamespace

        svc = _make_prod_service(tmp_path)
        svc.voice_service.repo.get_by_id = AsyncMock(return_value=SimpleNamespace(id="vt-1", user_id="u1", status="ready", speaker_id="S_x", model_type=5, language=0))
        audio, media_type = asyncio.run(svc.preview_voice("vt-1", "你好", user_id="u1"))
        assert audio == b"CLONED-MP3"
        assert media_type == "audio/mpeg"
        svc.voice_service.client.synthesize_sse.assert_awaited_once()
        svc.voice_service.qihu_client.synthesize_sse.assert_not_called()

    def test_cloned_voice_not_found_raises(self, tmp_path):
        svc = _make_prod_service(tmp_path)
        svc.voice_service.repo.get_by_id = AsyncMock(return_value=None)
        with pytest.raises(FileNotFoundError):
            asyncio.run(svc.preview_voice("missing", "你好", user_id="u1"))

    def test_cloned_voice_not_owned_raises_permission(self, tmp_path):
        from types import SimpleNamespace

        svc = _make_prod_service(tmp_path)
        svc.voice_service.repo.get_by_id = AsyncMock(return_value=SimpleNamespace(id="vt-1", user_id="other", status="ready"))
        with pytest.raises(PermissionError):
            asyncio.run(svc.preview_voice("vt-1", "你好", user_id="u1"))

    def test_cloned_voice_not_ready_raises_value(self, tmp_path):
        from types import SimpleNamespace

        svc = _make_prod_service(tmp_path)
        svc.voice_service.repo.get_by_id = AsyncMock(return_value=SimpleNamespace(id="vt-1", user_id="u1", status="training"))
        with pytest.raises(ValueError):
            asyncio.run(svc.preview_voice("vt-1", "你好", user_id="u1"))

    def test_cloned_voice_no_repo_raises(self, tmp_path):
        svc = _make_prod_service(tmp_path)
        svc.voice_service.repo = None
        with pytest.raises(FileNotFoundError):
            asyncio.run(svc.preview_voice("vt-1", "你好", user_id="u1"))


# ---------------------------------------------------------------------------
# Router endpoint: GET /voices/{voice_id}/preview
# ---------------------------------------------------------------------------


class TestPreviewVoiceEndpoint:
    def _svc_mock(self, audio=b"AUDIO", exc=None):
        svc = MagicMock()
        if exc is None:
            svc.preview_voice = AsyncMock(return_value=(audio, "audio/wav"))
        else:
            svc.preview_voice = AsyncMock(side_effect=exc)
        return svc

    def test_returns_cached_file_without_synthesizing(self, tmp_path, monkeypatch):
        import hashlib

        from _router_auth_helpers import call_unwrapped

        from app.gateway.routers import voice_twin_production as router

        monkeypatch.setattr(router, "_preview_storage_dir", lambda: tmp_path)
        key = "preset_146_" + hashlib.md5("你好".encode()).hexdigest()[:8]
        (tmp_path / f"{key}.wav").write_bytes(b"CACHED")

        svc = self._svc_mock()
        with patch.object(router, "_get_service", return_value=svc):
            with patch.object(router, "get_effective_user_id", return_value="u1"):
                resp = asyncio.run(call_unwrapped(router.preview_voice, request=MagicMock(), voice_id="preset:146", text="你好"))
        assert resp.status_code == 200
        assert resp.media_type == "audio/wav"
        svc.preview_voice.assert_not_awaited()  # served from cache, no synthesis

    def test_generates_and_caches_on_first_request(self, tmp_path, monkeypatch):
        import hashlib

        from _router_auth_helpers import call_unwrapped

        from app.gateway.routers import voice_twin_production as router

        monkeypatch.setattr(router, "_preview_storage_dir", lambda: tmp_path)
        svc = self._svc_mock(audio=b"FRESH-WAV")

        with patch.object(router, "_get_service", return_value=svc):
            with patch.object(router, "get_effective_user_id", return_value="u1"):
                resp = asyncio.run(call_unwrapped(router.preview_voice, request=MagicMock(), voice_id="preset:146", text="你好"))
        assert resp.status_code == 200
        svc.preview_voice.assert_awaited_once()
        # File now cached on disk (":" sanitized to "_").
        key = "preset_146_" + hashlib.md5("你好".encode()).hexdigest()[:8]
        assert (tmp_path / f"{key}.wav").read_bytes() == b"FRESH-WAV"

    def test_not_found_maps_to_404(self, tmp_path, monkeypatch):
        from _router_auth_helpers import call_unwrapped

        from app.gateway.routers import voice_twin_production as router

        monkeypatch.setattr(router, "_preview_storage_dir", lambda: tmp_path)
        with patch.object(router, "_get_service", return_value=self._svc_mock(exc=FileNotFoundError())):
            with patch.object(router, "get_effective_user_id", return_value="u1"):
                with pytest.raises(HTTPException) as ei:
                    asyncio.run(call_unwrapped(router.preview_voice, request=MagicMock(), voice_id="missing", text="你好"))
        assert ei.value.status_code == 404

    def test_not_owned_maps_to_403(self, tmp_path, monkeypatch):
        from _router_auth_helpers import call_unwrapped

        from app.gateway.routers import voice_twin_production as router

        monkeypatch.setattr(router, "_preview_storage_dir", lambda: tmp_path)
        with patch.object(router, "_get_service", return_value=self._svc_mock(exc=PermissionError())):
            with patch.object(router, "get_effective_user_id", return_value="u1"):
                with pytest.raises(HTTPException) as ei:
                    asyncio.run(call_unwrapped(router.preview_voice, request=MagicMock(), voice_id="vt-1", text="你好"))
        assert ei.value.status_code == 403

    def test_not_ready_maps_to_400(self, tmp_path, monkeypatch):
        from _router_auth_helpers import call_unwrapped

        from app.gateway.routers import voice_twin_production as router

        monkeypatch.setattr(router, "_preview_storage_dir", lambda: tmp_path)
        with patch.object(router, "_get_service", return_value=self._svc_mock(exc=ValueError("not ready"))):
            with patch.object(router, "get_effective_user_id", return_value="u1"):
                with pytest.raises(HTTPException) as ei:
                    asyncio.run(call_unwrapped(router.preview_voice, request=MagicMock(), voice_id="vt-1", text="你好"))
        assert ei.value.status_code == 400

    # -- Task 8：缓存命中分支也必须过归属校验 -------------------------------

    def _svc_with_repo_row(self, row, audio=b"AUDIO"):
        svc = self._svc_mock(audio=audio)
        svc.voice_service.repo.get_by_id = AsyncMock(return_value=row)
        return svc

    def _seed_cache(self, tmp_path, voice_id: str, text: str, ext: str = "mp3") -> None:
        import hashlib

        key = f"{voice_id.replace(':', '_')}_{hashlib.md5(text.encode()).hexdigest()[:8]}"
        (tmp_path / f"{key}.{ext}").write_bytes(b"CACHED")

    def test_cache_hit_other_user_maps_to_403(self, tmp_path, monkeypatch):
        """他人克隆音色命中缓存 → 403 不外发（曾命中即放行 = 越权取音色）。"""
        from types import SimpleNamespace

        from _router_auth_helpers import call_unwrapped

        from app.gateway.routers import voice_twin_production as router

        monkeypatch.setattr(router, "_preview_storage_dir", lambda: tmp_path)
        self._seed_cache(tmp_path, "vt-1", "你好")

        svc = self._svc_with_repo_row(SimpleNamespace(id="vt-1", user_id="other", status="ready"))
        with patch.object(router, "_get_service", return_value=svc):
            with patch.object(router, "get_effective_user_id", return_value="u1"):
                with pytest.raises(HTTPException) as ei:
                    asyncio.run(call_unwrapped(router.preview_voice, request=MagicMock(), voice_id="vt-1", text="你好"))
        assert ei.value.status_code == 403
        svc.preview_voice.assert_not_awaited()

    def test_cache_hit_deleted_voice_maps_to_404(self, tmp_path, monkeypatch):
        """音色已删但缓存残留 → 404（不得外发孤儿缓存）。"""
        from _router_auth_helpers import call_unwrapped

        from app.gateway.routers import voice_twin_production as router

        monkeypatch.setattr(router, "_preview_storage_dir", lambda: tmp_path)
        self._seed_cache(tmp_path, "vt-1", "你好")

        svc = self._svc_with_repo_row(None)
        with patch.object(router, "_get_service", return_value=svc):
            with patch.object(router, "get_effective_user_id", return_value="u1"):
                with pytest.raises(HTTPException) as ei:
                    asyncio.run(call_unwrapped(router.preview_voice, request=MagicMock(), voice_id="vt-1", text="你好"))
        assert ei.value.status_code == 404
        svc.preview_voice.assert_not_awaited()

    def test_cache_hit_owner_serves_cached_file(self, tmp_path, monkeypatch):
        """本人克隆音色命中缓存 → 200 放行，不重复合成。"""
        from types import SimpleNamespace

        from _router_auth_helpers import call_unwrapped

        from app.gateway.routers import voice_twin_production as router

        monkeypatch.setattr(router, "_preview_storage_dir", lambda: tmp_path)
        self._seed_cache(tmp_path, "vt-1", "你好")

        svc = self._svc_with_repo_row(SimpleNamespace(id="vt-1", user_id="u1", status="ready"))
        with patch.object(router, "_get_service", return_value=svc):
            with patch.object(router, "get_effective_user_id", return_value="u1"):
                resp = asyncio.run(call_unwrapped(router.preview_voice, request=MagicMock(), voice_id="vt-1", text="你好"))
        assert resp.status_code == 200
        assert resp.media_type == "audio/mpeg"
        svc.preview_voice.assert_not_awaited()


# ---------------------------------------------------------------------------
# zip-slip protection in ppt_extract
# ---------------------------------------------------------------------------


class TestSafeExtractall:
    """Verify _safe_extractall blocks path traversal (zip-slip)."""

    def _zip_bytes(self, entries: list[tuple[str, bytes]]) -> bytes:
        import io
        import zipfile

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            for name, data in entries:
                zf.writestr(name, data)
        return buf.getvalue()

    def test_rejects_traversal_entry(self, tmp_path):
        import zipfile

        from app.gateway.voice_twin.ppt_extract import _safe_extractall

        zf = zipfile.ZipFile(io.BytesIO(self._zip_bytes([("../evil.txt", b"x")])))
        with pytest.raises(RuntimeError, match="outside target dir"):
            _safe_extractall(zf, tmp_path)

    def test_accepts_nested_valid_entries(self, tmp_path):
        import zipfile

        from app.gateway.voice_twin.ppt_extract import _safe_extractall

        entries = [("slide-01.jpg", b"img1"), ("sub/slide-02.jpg", b"img2")]
        zf = zipfile.ZipFile(io.BytesIO(self._zip_bytes(entries)))
        _safe_extractall(zf, tmp_path)
        assert (tmp_path / "slide-01.jpg").read_bytes() == b"img1"
        assert (tmp_path / "sub" / "slide-02.jpg").read_bytes() == b"img2"


# ---------------------------------------------------------------------------
# _reject_oversized_upload：Content-Length 预检（Task 8 内存 DoS 防线）
# ---------------------------------------------------------------------------


class TestRejectOversizedUpload:
    def _request(self, content_length: str | None):
        request = MagicMock()
        request.headers = {} if content_length is None else {"content-length": content_length}
        return request

    def test_over_limit_raises_413(self):
        from app.gateway.routers.voice_twin_production import MAX_DOC_SIZE, _reject_oversized_upload

        with pytest.raises(HTTPException) as ei:
            _reject_oversized_upload(self._request(str(MAX_DOC_SIZE + 1)), MAX_DOC_SIZE)
        assert ei.value.status_code == 413

    def test_exact_limit_passes(self):
        from app.gateway.routers.voice_twin_production import MAX_DOC_SIZE, _reject_oversized_upload

        _reject_oversized_upload(self._request(str(MAX_DOC_SIZE)), MAX_DOC_SIZE)  # no raise

    def test_missing_header_passes(self):
        """chunked 传输无 Content-Length → 放行，由读取后的长度检查兜底。"""
        from app.gateway.routers.voice_twin_production import MAX_DOC_SIZE, _reject_oversized_upload

        _reject_oversized_upload(self._request(None), MAX_DOC_SIZE)  # no raise

    def test_non_numeric_header_passes(self):
        from app.gateway.routers.voice_twin_production import MAX_DOC_SIZE, _reject_oversized_upload

        _reject_oversized_upload(self._request("not-a-number"), MAX_DOC_SIZE)  # no raise


# ---------------------------------------------------------------------------
# _transcode_wav：按输出格式选编码器（Task 9，审计 P1#6）
# ---------------------------------------------------------------------------


def test_transcode_wav_selects_codec_by_extension(monkeypatch, tmp_path):
    from app.gateway.voice_twin import production_service as ps

    cmds: list[list[str]] = []
    import subprocess as sp

    monkeypatch.setattr(sp, "run", lambda cmd, **k: (cmds.append(cmd), sp.CompletedProcess(args=cmd, returncode=0, stderr=b""))[1])

    ps._transcode_wav(tmp_path / "a.wav", tmp_path / "o.ogg")
    ps._transcode_wav(tmp_path / "a.wav", tmp_path / "o.wav")
    ps._transcode_wav(tmp_path / "a.wav", tmp_path / "o.mp3")
    assert "libvorbis" in cmds[0]
    assert "pcm_s16le" in cmds[1]
    assert "libmp3lame" in cmds[2]

    import pytest

    with pytest.raises(ValueError, match="unsupported output format"):
        ps._transcode_wav(tmp_path / "a.wav", tmp_path / "o.flac")


# ---------------------------------------------------------------------------
# _verify_voice：preset 语法化 + doubao 分支（Task 15）
# ---------------------------------------------------------------------------


class TestVerifyVoice:
    """_verify_voice accepts any 'preset:{int}' (whitelist is display-name only)
    and 'doubao:{speaker}' voices; cloned ids still fall through to repo checks."""

    @pytest.mark.asyncio
    async def test_verify_voice_accepts_doubao(self, async_production_svc: ProductionService):
        """doubao: 音色提交生产任务不再 404（合成层 preview/pipeline 均支持）。"""
        from app.gateway.routers.voice_twin_production import _verify_voice

        voice = await _verify_voice(async_production_svc, "doubao:zh_female_qingxin", "user_a")
        assert voice.voice_name == "豆包音色 zh_female_qingxin"
        assert voice.speaker_id == "doubao:zh_female_qingxin"
        assert voice.user_id == "user_a"
        # doubao 分支不得触碰克隆音色 repo
        async_production_svc.voice_service.repo.get_by_id.assert_not_called()

    @pytest.mark.asyncio
    async def test_verify_voice_unknown_preset_gets_fallback_name(self, async_production_svc: ProductionService):
        """目录外 preset:999 → 伪音色名回退 "360 预设音色 999" 而非 404。"""
        from app.gateway.routers.voice_twin_production import _verify_voice

        voice = await _verify_voice(async_production_svc, "preset:999", "user_a")
        assert voice.voice_name == "360 预设音色 999"
        assert voice.speaker_id == "preset:999"
        assert voice.user_id == "user_a"
        async_production_svc.voice_service.repo.get_by_id.assert_not_called()

    @pytest.mark.asyncio
    async def test_verify_voice_whitelisted_preset_keeps_display_name(self, async_production_svc: ProductionService):
        """目录内 preset 用共享目录展示名（2026-08-09 A1：以共享目录名为准）。"""
        from app.gateway.routers.voice_twin_production import _verify_voice

        voice = await _verify_voice(async_production_svc, "preset:146", "user_a")
        assert voice.voice_name == "知性女声（360）"
        assert voice.speaker_id == "preset:146"

    @pytest.mark.asyncio
    async def test_verify_voice_cloned_id_still_hits_repo(self, async_production_svc: ProductionService):
        """克隆音色 id（uuid hex，无冒号）不匹配 preset 语法 → 仍走 repo 校验（fail-closed，
        不得因 preset 语法化而绕过归属/状态检查）。"""
        from app.gateway.routers.voice_twin_production import _verify_voice

        async_production_svc.voice_service.repo.get_by_id = AsyncMock(return_value=None)
        with pytest.raises(HTTPException) as ei:
            await _verify_voice(async_production_svc, "a1b2c3d4e5f60708", "user_a")
        assert ei.value.status_code == 404
