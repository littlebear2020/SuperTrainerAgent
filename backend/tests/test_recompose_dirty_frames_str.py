"""recompose_task 必须按真实 DB 形态解析 dirty_frames（TaskRow Text 列存 JSON 字符串）。

根因：TaskRow.dirty_frames 是 Text 列（task_framework/models.py:43），repo.get 返回
裸行、不过 TaskBrief 的 validator（schemas.py:86 只管 API 层）。F5 增量引入
``dirty = task.dirty_frames or []`` → 字符串 "[]" 非空即真 → list("[]") = ['[', ']']
传入 pipeline → ``int('[')`` 秒崩（vg_f74c8fa2331a 实测：progress=1、自动重试 ×2、
终态 failed，用户再点撞 pending 守卫 409）。既有 stub 用 list 恰好掩盖真实类型。
"""

import asyncio

import pytest


def _make_svc(storage_dir, dirty_frames):
    from app.gateway.video_gen.service import VideoGenService

    svc = VideoGenService.__new__(VideoGenService)
    svc._active_tasks = {}
    svc._storage_dir = str(storage_dir)

    class _Task:
        status = "failed"
        task_type = "ai_image"
        payload = "{}"

    _Task.dirty_frames = dirty_frames

    class _Repo:
        async def get(self, task_id, user_id=None):
            return _Task()

    svc.repo = _Repo()
    return svc


def _prepare_task_dir(tmp_path):
    task_dir = tmp_path / "u1" / "tasks" / "vg_x"
    task_dir.mkdir(parents=True)
    (task_dir / "audio.mp3").write_bytes(b"au")
    (task_dir / "storyboard.json").write_text('{"title":"t","config":{},"frames":[]}', encoding="utf-8")
    return task_dir


def _capture_dispatch(svc):
    captured = {}

    async def _fake_wrap(task_id, user_id, task_type, payload):
        captured["payload"] = payload

    svc._wrap_pipeline = _fake_wrap
    return captured


@pytest.mark.asyncio
async def test_recompose_empty_dirty_frames_string_no_incremental(tmp_path):
    """DB 原样 '[]'（空 JSON 字符串）→ 不得标增量。

    修前 list('[]') = ['[', ']'] 传入 pipeline → int('[') 崩（本断言修前 FAIL）。
    """
    svc = _make_svc(tmp_path, "[]")
    _prepare_task_dir(tmp_path)
    captured = _capture_dispatch(svc)

    result = await svc.recompose_task("vg_x", user_id="u1")
    await asyncio.sleep(0)

    assert result["status"] == "composing"
    payload = captured["payload"]
    assert payload.get("incremental") is not True, f"空 dirty 不得标增量: {payload.get('changed_frames')}"
    assert payload.get("changed_frames") is None, f"修前 list('[]') = ['[', ']'] → pipeline int('[') 崩: {payload.get('changed_frames')}"


@pytest.mark.asyncio
async def test_recompose_dirty_frames_string_parsed_to_ints(tmp_path):
    """DB 原样 '[2, 5]'（JSON 字符串）→ changed_frames == [2, 5]（int 列表）。

    修前 list('[2, 5]') = ['[', '2', ',', ' ', '5', ']'] → int('[') 崩（本断言修前 FAIL）。
    """
    svc = _make_svc(tmp_path, "[2, 5]")
    _prepare_task_dir(tmp_path)
    captured = _capture_dispatch(svc)

    result = await svc.recompose_task("vg_x", user_id="u1")
    await asyncio.sleep(0)

    assert result["status"] == "composing"
    payload = captured["payload"]
    assert payload.get("incremental") is True
    assert payload.get("changed_frames") == [2, 5], f"须解析为 int 列表: {payload.get('changed_frames')}"
