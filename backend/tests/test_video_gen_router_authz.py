"""Authorization tests for video-gen task endpoints (IDOR hardening)."""

import pytest
from fastapi import HTTPException


class _DraftTask:
    """A draft task owned by ``owner`` — simulates a task fetched without ownership filter."""

    id = "t1"
    user_id = "owner"
    status = "draft"
    payload = "{}"


class _FakeSvc:
    """Fake VideoGenService.

    - ``get_task(task_id, user_id)`` honors ownership (returns task only for owner),
      mirroring the real BaseTaskService.get_task contract (non-owner → None → 404).
    - ``repo.get(task_id)`` returns the task *regardless of user* — this is the IDOR
      path (pre-fix): it bypasses the ownership filter entirely.
    """

    _storage_dir = "/tmp/vg-authz-test"

    def __init__(self):
        self.spawned = None

    async def get_task(self, task_id, user_id):
        # 真实契约：按 user_id 过滤，非属主 → None
        return _DraftTask() if user_id == "owner" else None

    class _Repo:
        def __init__(self, svc):
            self._svc = svc

        async def get(self, task_id, user_id=None):
            # IDOR 路径：忽略 user_id，永远返回 task
            return _DraftTask()

    def __init_repo__(self):  # not used; repo set via property below
        pass

    @property
    def repo(self):
        return self._Repo(self)

    async def _generate_draft_audio(self, task_id, task_dir, user_id=None):
        self.spawned = (task_id, str(task_dir), user_id)


class _Req:
    class app:
        class state:
            video_gen_service = None  # set per-test


def _make_request(svc):
    inst = _Req()
    inst.app = type("A", (), {"state": type("S", (), {"video_gen_service": svc})()})
    return inst


@pytest.mark.asyncio
async def test_generate_audio_rejects_non_owner():
    """E1: 非属主对他人 draft 任务触发 generate-audio 须 404。

    修前用 ``svc.repo.get(task_id)`` 绕过 user_id 过滤 → 返回 started（IDOR）。
    修后用 ``svc.get_task(task_id, user_id)`` → 非属主 None → 404。
    """
    from app.gateway.routers.video_gen import trigger_draft_audio

    svc = _FakeSvc()

    # 默认 autouse user = "test-user-autouse"（非属主 "owner"）
    request = _make_request(svc)

    with pytest.raises(HTTPException) as exc:
        await trigger_draft_audio("t1", request)

    assert exc.value.status_code == 404, "非属主触发 generate-audio 必须 404（修 IDOR）"
    # 关键不变量：绝不派发后台 TTS（不消耗他人配额、不写他人目录）
    assert svc.spawned is None, "非属主请求不得触发 _generate_draft_audio"


@pytest.mark.asyncio
async def test_generate_audio_owner_proceeds():
    """属主（owner）触发 generate-audio 正常派发后台 TTS 且返回 started（不回归）。"""
    import asyncio
    from types import SimpleNamespace

    from app.gateway.routers import video_gen as router_mod
    from deerflow.runtime.user_context import reset_current_user, set_current_user

    token = set_current_user(SimpleNamespace(id="owner", email="o@local"))
    try:
        svc = _FakeSvc()
        request = _make_request(svc)
        result = await router_mod.trigger_draft_audio("t1", request)
        assert result["status"] == "started"
        # 让被 create_task 调度的后台协程跑完，确认以属主 user_id 派发
        await asyncio.sleep(0)
        assert svc.spawned is not None, "属主请求应派发后台 TTS"
        assert svc.spawned[2] == "owner", "派发的后台 TTS 必须带属主 user_id"
    finally:
        reset_current_user(token)
