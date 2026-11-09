import pytest

from app.gateway.video_gen.schemas import CreateVideoRequest


class _FakeTask:
    id = "vg_abc123"
    user_id = "default"
    domain = "video_gen"
    task_type = "ai_image"
    status = "running"
    progress = 1
    priority = 40
    payload = "{}"
    result = "{}"
    error_message = None
    created_at = None
    updated_at = None


class _FakeSvc:
    def __init__(self):
        self.submitted = None

    async def submit(self, *, user_id, task_type, payload, priority=50):
        self.submitted = {"task_type": task_type, "payload": payload}
        return _FakeTask()


@pytest.mark.asyncio
async def test_create_video_maps_type_to_task_type():
    from app.gateway.routers.video_gen import create_video

    svc = _FakeSvc()

    class _Req:
        class app:
            class state:
                video_gen_service = svc

    body = CreateVideoRequest(video_type="ai_image", mode="generate", topic="金钱")
    await create_video(_Req(), body)
    assert svc.submitted["task_type"] == "ai_image"
    assert svc.submitted["payload"]["topic"] == "金钱"


def test_preview_voice_does_not_use_mktemp():
    """preview_voice must not use deprecated tempfile.mktemp."""
    import inspect

    from app.gateway.routers import video_gen as router_mod

    source = inspect.getsource(router_mod.preview_voice)
    assert "mktemp" not in source, "preview_voice still uses tempfile.mktemp"
