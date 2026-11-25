import asyncio
import os
import time
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest
from _router_auth_helpers import make_authed_test_app
from fastapi import FastAPI
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

import app.gateway.routers.works as works_router
from app.gateway.auth.models import User
from app.gateway.authz import AuthContext, Permissions
from app.gateway.works import service as works_service


def make_client(*, authenticated: bool = True, permissions: list[str] | None = None) -> tuple[TestClient, FastAPI]:
    if authenticated and permissions is None:
        app = make_authed_test_app()
    else:
        app = FastAPI()
        app.state.thread_store = SimpleNamespace(check_access=AsyncMock(return_value=True))

        @app.middleware("http")
        async def set_auth(request, call_next):
            if authenticated:
                user = User(email="works@example.com", password_hash="x", system_role="user", id=uuid4())
                request.state.user = user
                request.state.auth = AuthContext(user=user, permissions=permissions or [])
            return await call_next(request)

    app.include_router(works_router.router)
    app.state.checkpointer = SimpleNamespace(aget_tuple=AsyncMock(return_value=None))
    return TestClient(app), app


def checkpoint_with(artifacts: object) -> SimpleNamespace:
    return SimpleNamespace(checkpoint={"channel_values": {"artifacts": artifacts, "messages": ["private message"]}})


def test_requires_authentication() -> None:
    client, _ = make_client(authenticated=False)

    response = client.post("/api/works/artifact-summaries", json={"thread_ids": ["thread-1"]})

    assert response.status_code == 401


def test_requires_threads_read_permission() -> None:
    client, app = make_client(permissions=[])

    response = client.post("/api/works/artifact-summaries", json={"thread_ids": ["thread-1"]})

    assert response.status_code == 403
    app.state.thread_store.check_access.assert_not_called()
    app.state.checkpointer.aget_tuple.assert_not_called()


@pytest.mark.parametrize(
    "thread_ids",
    [
        [],
        [f"thread-{index}" for index in range(51)],
        ["   "],
        ["x" * 129],
        ["duplicate"] * 51,
    ],
)
def test_rejects_invalid_raw_batches(thread_ids: list[str]) -> None:
    client, app = make_client()

    response = client.post("/api/works/artifact-summaries", json={"thread_ids": thread_ids})

    assert response.status_code == 422
    app.state.thread_store.check_access.assert_not_called()
    app.state.checkpointer.aget_tuple.assert_not_called()


def test_trims_and_deduplicates_ids_before_sequential_ordered_reads() -> None:
    client, app = make_client()
    active_reads = 0
    max_active_reads = 0

    async def read_checkpoint(config: dict) -> SimpleNamespace:
        nonlocal active_reads, max_active_reads
        active_reads += 1
        max_active_reads = max(max_active_reads, active_reads)
        await asyncio.sleep(0)
        active_reads -= 1
        return checkpoint_with([])

    app.state.checkpointer.aget_tuple = AsyncMock(side_effect=read_checkpoint)

    response = client.post(
        "/api/works/artifact-summaries",
        json={"thread_ids": [" thread-b ", "thread-a", "thread-b"]},
    )

    assert response.status_code == 200
    assert [item["thread_id"] for item in response.json()] == ["thread-b", "thread-a"]
    assert [item["delivery_state"] for item in response.json()] == ["not_delivered", "not_delivered"]
    assert max_active_reads == 1
    access_calls = app.state.thread_store.check_access.await_args_list
    assert [call.args[0] for call in access_calls] == ["thread-b", "thread-a"]
    assert all(call.kwargs == {"require_existing": True} for call in access_calls)
    assert [call.args[0]["configurable"]["thread_id"] for call in app.state.checkpointer.aget_tuple.await_args_list] == ["thread-b", "thread-a"]


def test_returns_file_summaries_without_messages_or_host_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    client, app = make_client()
    output = tmp_path / "private-host" / "deck.pptx"
    output.parent.mkdir(parents=True)
    output.write_bytes(b"deck")
    os.utime(output, (1_700_000_000, 1_700_000_000))

    def resolve(_thread_id: str, virtual_path: str) -> Path:
        assert virtual_path == "/mnt/user-data/outputs/deck.pptx"
        return output

    monkeypatch.setattr(works_service, "resolve_thread_virtual_path", resolve)
    app.state.checkpointer.aget_tuple = AsyncMock(return_value=checkpoint_with(["/mnt/user-data/outputs/deck.pptx"]))

    response = client.post("/api/works/artifact-summaries", json={"thread_ids": ["thread-1"]})

    assert response.status_code == 200
    payload = response.json()
    assert payload == [
        {
            "thread_id": "thread-1",
            "delivery_state": "delivered",
            "files": [
                {
                    "path": "/mnt/user-data/outputs/deck.pptx",
                    "name": "deck.pptx",
                    "media_kind": "document",
                    "size": 4,
                    "modified_at": "2023-11-14T22:13:20Z",
                    "exists": True,
                }
            ],
        }
    ]
    assert str(tmp_path) not in response.text
    assert "private message" not in response.text


def test_foreign_thread_rejects_whole_batch_before_checkpoint_or_stat(monkeypatch: pytest.MonkeyPatch) -> None:
    client, app = make_client()
    app.state.thread_store.check_access = AsyncMock(side_effect=[True, False])
    get_checkpointer = Mock(return_value=app.state.checkpointer)
    monkeypatch.setattr(works_router, "get_checkpointer", get_checkpointer)
    stat = Mock(side_effect=AssertionError("stat must not run"))
    monkeypatch.setattr(Path, "stat", stat)

    response = client.post("/api/works/artifact-summaries", json={"thread_ids": ["mine", "foreign"]})

    assert response.status_code == 404
    assert app.state.thread_store.check_access.await_count == 2
    get_checkpointer.assert_not_called()
    app.state.checkpointer.aget_tuple.assert_not_called()
    stat.assert_not_called()


def test_missing_thread_meta_is_rejected_with_require_existing() -> None:
    client, app = make_client()
    app.state.thread_store.check_access = AsyncMock(return_value=False)

    response = client.post("/api/works/artifact-summaries", json={"thread_ids": ["missing"]})

    assert response.status_code == 404
    app.state.thread_store.check_access.assert_awaited_once()
    assert app.state.thread_store.check_access.await_args.args[0] == "missing"
    assert app.state.thread_store.check_access.await_args.kwargs == {"require_existing": True}
    app.state.checkpointer.aget_tuple.assert_not_called()


def test_accepts_fifty_threads_and_preserves_response_order() -> None:
    client, app = make_client()
    thread_ids = [f"thread-{index}" for index in range(50)]

    async def read_checkpoint(config: dict) -> SimpleNamespace:
        return checkpoint_with([])

    app.state.checkpointer.aget_tuple = AsyncMock(side_effect=read_checkpoint)

    response = client.post("/api/works/artifact-summaries", json={"thread_ids": thread_ids})

    assert response.status_code == 200
    assert [item["thread_id"] for item in response.json()] == thread_ids
    assert app.state.checkpointer.aget_tuple.await_count == 50


@pytest.mark.asyncio
async def test_real_asgi_two_users_file_updates_and_50_thread_batch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from langgraph.checkpoint.base import empty_checkpoint, uuid6
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    from deerflow.persistence.engine import close_engine, get_session_factory, init_engine
    from deerflow.persistence.thread_meta import ThreadMetaRepository

    await init_engine("sqlite", url=f"sqlite+aiosqlite:///{tmp_path / 'meta.db'}", sqlite_dir=str(tmp_path))
    output_root = tmp_path / "outputs"
    output_root.mkdir()
    outputs_prefix = "/mnt/user-data/outputs/"

    def resolve(_thread_id: str, virtual_path: str) -> Path:
        relative = PurePosixPath(virtual_path).relative_to(outputs_prefix)
        return output_root.joinpath(*relative.parts)

    monkeypatch.setattr(works_service, "resolve_thread_virtual_path", resolve)
    alice = User(email="alice-works@example.com", password_hash="x", system_role="user", id=uuid4())
    bob = User(email="bob-works@example.com", password_hash="x", system_role="user", id=uuid4())
    users = {"alice": alice, "bob": bob}

    try:
        repository = ThreadMetaRepository(get_session_factory())
        async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "checkpoints.db")) as checkpointer:
            await checkpointer.setup()

            async def seed(thread_id: str, content: bytes) -> None:
                await repository.create(thread_id, user_id=str(alice.id), metadata={"agent_name": "ppt-master"})
                virtual_path = f"{outputs_prefix}{thread_id}.txt"
                actual_path = output_root / f"{thread_id}.txt"
                actual_path.write_bytes(content)
                os.utime(actual_path, (1_700_000_100, 1_700_000_100))
                checkpoint = empty_checkpoint()
                checkpoint["id"] = str(uuid6())
                checkpoint["channel_values"] = {"artifacts": [virtual_path], "messages": ["private message"]}
                checkpoint["channel_versions"] = {"artifacts": 1}
                await checkpointer.aput(
                    {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}},
                    checkpoint,
                    {"step": 1, "source": "loop", "writes": {"test": {}}, "parents": {}},
                    {"artifacts": 1},
                )

            await seed("owned", b"before")
            app = FastAPI()
            app.state.thread_store = repository
            app.state.checkpointer = checkpointer
            app.include_router(works_router.router)

            @app.middleware("http")
            async def set_real_user(request, call_next):
                user = users[request.headers.get("x-test-user", "alice")]
                request.state.user = user
                request.state.auth = AuthContext(user=user, permissions=[Permissions.THREADS_READ])
                return await call_next(request)

            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://works.test") as client:
                first = await client.post(
                    "/api/works/artifact-summaries",
                    json={"thread_ids": ["owned"]},
                    headers={"x-test-user": "alice"},
                )
                assert first.status_code == 200
                first_file = first.json()[0]["files"][0]
                assert first_file["size"] == len(b"before")

                foreign = await client.post(
                    "/api/works/artifact-summaries",
                    json={"thread_ids": ["owned"]},
                    headers={"x-test-user": "bob"},
                )
                assert foreign.status_code == 404
                assert str(output_root) not in foreign.text

                owned_output = output_root / "owned.txt"
                owned_output.write_bytes(b"after update")
                os.utime(owned_output, (1_700_000_200, 1_700_000_200))
                second = await client.post(
                    "/api/works/artifact-summaries",
                    json={"thread_ids": ["owned"]},
                    headers={"x-test-user": "alice"},
                )
                second_file = second.json()[0]["files"][0]
                assert second_file["size"] == len(b"after update")
                assert second_file["modified_at"] > first_file["modified_at"]

                bulk_ids = [f"bulk-{index:03d}" for index in range(50)]
                for thread_id in bulk_ids:
                    await seed(thread_id, thread_id.encode())
                started = time.perf_counter()
                bulk = await client.post(
                    "/api/works/artifact-summaries",
                    json={"thread_ids": bulk_ids},
                    headers={"x-test-user": "alice"},
                )
                elapsed_ms = (time.perf_counter() - started) * 1000
                assert bulk.status_code == 200
                assert [item["thread_id"] for item in bulk.json()] == bulk_ids
                assert all(item["delivery_state"] == "delivered" for item in bulk.json())
                print(f"WORKS_BATCH_50_MS={elapsed_ms:.3f}")
    finally:
        await close_engine()
