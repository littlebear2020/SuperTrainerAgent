"""F2: POST /preview-all 批量预览端点 + T2（2026-08-31）批量循环并发化语义。"""

import asyncio
import json

import pytest


@pytest.fixture(autouse=True)
def _clean_preview_state():
    from app.gateway.routers import video_gen as vg

    vg._preview_inflight.clear()
    vg._preview_all_running.clear()
    yield
    vg._preview_inflight.clear()
    vg._preview_all_running.clear()


def _make_request(svc):
    class _Req:
        class app:
            class state:
                video_gen_service = svc

    return _Req()


@pytest.mark.asyncio
async def test_preview_all_returns_started_count(tmp_path):
    """draft 任务有 2 帧无 visual_url → POST /preview-all 返回 started count=2。"""
    from app.gateway.routers.video_gen import preview_all_frames

    task_dir = tmp_path / "test-user-autouse" / "tasks" / "vg_f2"
    task_dir.mkdir(parents=True)
    sb = {
        "title": "t",
        "config": {"video_type": "ai_image", "width": 1080, "height": 1920},
        "frames": [
            {"index": 0, "narration": "n0", "visual_url": "http://x/0.png"},  # 已有预览
            {"index": 1, "narration": "n1"},  # 无预览
            {"index": 2, "narration": "n2"},  # 无预览
        ],
    }
    (task_dir / "storyboard.json").write_text(json.dumps(sb), encoding="utf-8")

    class _Task:
        status = "draft"

    class _Svc:
        _storage_dir = str(tmp_path)

        async def get_task(self, task_id, user_id):
            return _Task()

    result = await preview_all_frames(_make_request(_Svc()), "vg_f2")
    assert result["status"] == "started"
    assert result["count"] == 2


@pytest.mark.asyncio
async def test_preview_all_already_done(tmp_path):
    """所有帧都有 visual_url → already_done count=0。"""
    from app.gateway.routers.video_gen import preview_all_frames

    task_dir = tmp_path / "test-user-autouse" / "tasks" / "vg_ok"
    task_dir.mkdir(parents=True)
    sb = {
        "title": "t",
        "config": {"video_type": "ai_image", "width": 1080, "height": 1920},
        "frames": [
            {"index": 0, "narration": "n0", "visual_url": "http://x/0.png"},
            {"index": 1, "narration": "n1", "visual_url": "http://x/1.png"},
        ],
    }
    (task_dir / "storyboard.json").write_text(json.dumps(sb), encoding="utf-8")

    class _Task:
        status = "draft"

    class _Svc:
        _storage_dir = str(tmp_path)

        async def get_task(self, task_id, user_id):
            return _Task()

    result = await preview_all_frames(_make_request(_Svc()), "vg_ok")
    assert result["status"] == "already_done"
    assert result["count"] == 0


@pytest.mark.asyncio
async def test_preview_all_dedup_already_running(tmp_path):
    """同任务已有 preview-all 循环在跑 → already_running count=0，不起第二个循环。"""
    from app.gateway.routers import video_gen as vg

    task_dir = tmp_path / "test-user-autouse" / "tasks" / "vg_dup"
    task_dir.mkdir(parents=True)
    sb = {
        "title": "t",
        "config": {"video_type": "ai_image", "width": 1080, "height": 1920},
        "frames": [{"index": 0, "narration": "n0"}],
    }
    (task_dir / "storyboard.json").write_text(json.dumps(sb), encoding="utf-8")

    class _Task:
        status = "draft"

    class _Svc:
        _storage_dir = str(tmp_path)

        async def get_task(self, task_id, user_id):
            return _Task()

    vg._preview_all_running["vg_dup"] = object()  # 哨兵：模拟在跑循环
    result = await vg.preview_all_frames(_make_request(_Svc()), "vg_dup")
    assert result == {"status": "already_running", "count": 0}


@pytest.mark.asyncio
async def test_preview_all_bg_skips_prefilled_frames(tmp_path):
    """任务内重查（T2 并发化后语义）：帧任务启动检查时已有 visual_url → 跳过不重复生成。

    2026-08-31 并发化说明：批量循环从串行 for 改为每帧 create_task 同时派发——
    「循环期间被后续帧填 visual_url」的窄窗不再存在（全帧同时启动），重查防线
    保护的是启动时点已填/在途的帧（手动单帧预览、前一轮残留）。
    """
    from app.gateway.routers import video_gen as vg

    task_dir = tmp_path / "test-user-autouse" / "tasks" / "vg_skip"
    task_dir.mkdir(parents=True)
    sb = {
        "title": "t",
        "config": {"video_type": "ai_image", "width": 1080, "height": 1920},
        # image_prompt 必填：2026-08-19 起空 prompt 帧在循环头部即跳过（明确状态，
        # 不再反复尝试失败）——本用例验证的是 visual_url 抢先跳过，帧须可预览。
        "frames": [
            {"index": 0, "narration": "n0", "image_prompt": "p0"},
            {"index": 1, "narration": "n1", "image_prompt": "p1", "visual_url": "http://x/manual.png"},
        ],
    }
    (task_dir / "storyboard.json").write_text(json.dumps(sb), encoding="utf-8")

    class _Svc:
        _storage_dir = str(tmp_path)

        def __init__(self):
            self.generated = []

        async def _generate_frame_preview(self, task_id, frame_index, user_id):
            self.generated.append(frame_index)
            return "http://x/gen.png"

        async def _write_preview_error(self, *a):
            return None

    svc = _Svc()
    await vg._preview_all_bg(svc, "vg_skip", [0, 1], "test-user-autouse")
    assert svc.generated == [0]
    assert "vg_skip" not in vg._preview_all_running


@pytest.mark.asyncio
async def test_preview_all_bg_skips_inflight_frames(tmp_path):
    """去重保持（T2 计划§四.7）：_preview_inflight 已占位（单帧预览在途）的帧被跳过。"""
    from app.gateway.routers import video_gen as vg

    task_dir = tmp_path / "test-user-autouse" / "tasks" / "vg_dedup"
    task_dir.mkdir(parents=True)
    sb = {
        "title": "t",
        "config": {"video_type": "ai_image", "width": 1080, "height": 1920},
        "frames": [
            {"index": 0, "narration": "n0", "image_prompt": "p0"},
            {"index": 1, "narration": "n1", "image_prompt": "p1"},
        ],
    }
    (task_dir / "storyboard.json").write_text(json.dumps(sb), encoding="utf-8")

    class _Svc:
        _storage_dir = str(tmp_path)

        def __init__(self):
            self.generated = []

        async def _generate_frame_preview(self, task_id, frame_index, user_id):
            self.generated.append(frame_index)
            return "http://x/gen.png"

        async def _write_preview_error(self, *a):
            return None

    svc = _Svc()
    vg._preview_inflight[("vg_dedup", 1)] = object()  # 哨兵：模拟单帧预览在途
    await vg._preview_all_bg(svc, "vg_dedup", [0, 1], "test-user-autouse")
    assert svc.generated == [0], "在途占位帧被跳过（不重复烧配额、不抢同一帧）"
    assert ("vg_dedup", 1) in vg._preview_inflight, "占位登记不被批量循环误清"


@pytest.mark.asyncio
async def test_preview_all_bg_runs_concurrently(tmp_path):
    """T2 计划§四.6（串行根因回归钉）：12 帧批量预览并发跑——峰值在途 >1 且 ≤12。"""
    from app.gateway.routers import video_gen as vg

    task_dir = tmp_path / "test-user-autouse" / "tasks" / "vg_conc"
    task_dir.mkdir(parents=True)
    sb = {
        "title": "t",
        "config": {"video_type": "ai_image", "width": 1080, "height": 1920},
        "frames": [{"index": i, "narration": f"n{i}", "image_prompt": f"p{i}"} for i in range(12)],
    }
    (task_dir / "storyboard.json").write_text(json.dumps(sb), encoding="utf-8")

    in_flight = {"n": 0, "max": 0}

    class _Svc:
        _storage_dir = str(tmp_path)

        async def _generate_frame_preview(self, task_id, frame_index, user_id):
            in_flight["n"] += 1
            in_flight["max"] = max(in_flight["max"], in_flight["n"])
            await asyncio.sleep(0.02)
            in_flight["n"] -= 1
            return "http://x/gen.png"

        async def _write_preview_error(self, *a):
            return None

    await vg._preview_all_bg(_Svc(), "vg_conc", list(range(12)), "test-user-autouse")
    assert in_flight["max"] > 1, f"批量预览须并发跑（修前串行 for = 90 帧 ≈90 分钟根因），实测峰值 {in_flight['max']}"
    assert in_flight["max"] <= 12, f"派发不超帧数（12 帧 ≤12），实测峰值 {in_flight['max']}"
    assert not vg._preview_inflight, "全部帧的在途登记须随任务结束清理"


@pytest.mark.asyncio
async def test_preview_all_bg_failure_writes_preview_error(tmp_path):
    """批量路径单帧失败 → 也写 preview_error（不再静默跳过），其余帧继续。"""
    from app.gateway.routers import video_gen as vg

    task_dir = tmp_path / "test-user-autouse" / "tasks" / "vg_berr"
    task_dir.mkdir(parents=True)
    sb = {
        "title": "t",
        "config": {"video_type": "ai_image", "width": 1080, "height": 1920},
        # image_prompt 必填：2026-08-19 起空 prompt 帧在循环头部即跳过——本用例
        # 验证的是失败写 preview_error + 其余帧继续。
        "frames": [
            {"index": 0, "narration": "n0", "image_prompt": "p0"},
            {"index": 1, "narration": "n1", "image_prompt": "p1"},
        ],
    }
    (task_dir / "storyboard.json").write_text(json.dumps(sb), encoding="utf-8")

    class _Svc:
        _storage_dir = str(tmp_path)

        def __init__(self):
            self.errors = []

        async def _generate_frame_preview(self, task_id, frame_index, user_id):
            if frame_index == 0:
                raise RuntimeError("ark 429")
            return "http://x/ok.png"

        async def _write_preview_error(self, task_id, frame_index, user_id, exc):
            self.errors.append((frame_index, str(exc)))

    svc = _Svc()
    await vg._preview_all_bg(svc, "vg_berr", [0, 1], "test-user-autouse")
    assert svc.errors == [(0, "ark 429")]


# ── T6（2026-09-01 审查 #3）：派发快照——修前每帧全量重读+parse（82 帧 ~164 次）


@pytest.mark.asyncio
async def test_preview_all_bg_reads_storyboard_once(tmp_path, monkeypatch):
    """帧级检查基于派发前一次快照：storyboard.json 读取次数 = 1（多帧批量下不随帧数放大）。"""
    import pathlib

    from app.gateway.routers import video_gen as vg

    task_dir = tmp_path / "test-user-autouse" / "tasks" / "vg_snap"
    task_dir.mkdir(parents=True)
    sb = {
        "title": "t",
        "config": {"video_type": "ai_image", "width": 1080, "height": 1920},
        "frames": [{"index": i, "narration": f"n{i}", "image_prompt": f"p{i}"} for i in range(4)],
    }
    (task_dir / "storyboard.json").write_text(json.dumps(sb), encoding="utf-8")

    reads = {"n": 0}
    _orig_read_text = pathlib.Path.read_text

    def _counting_read_text(self, *args, **kwargs):  # noqa: ANN001
        if self.name == "storyboard.json":
            reads["n"] += 1
        return _orig_read_text(self, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "read_text", _counting_read_text)

    class _Svc:
        _storage_dir = str(tmp_path)

        def __init__(self):
            self.generated = []

        async def _generate_frame_preview(self, task_id, frame_index, user_id):
            self.generated.append(frame_index)
            return "http://x/gen.png"

        async def _write_preview_error(self, *a):
            return None

    svc = _Svc()
    await vg._preview_all_bg(svc, "vg_snap", [0, 1, 2, 3], "test-user-autouse")
    assert svc.generated == [0, 1, 2, 3], "快照不改变生成语义"
    assert reads["n"] == 1, f"storyboard.json 须只读 1 次（快照派发），实际 {reads['n']} 次"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["missing", "corrupt"])
async def test_preview_all_bg_snapshot_failure_aborts_clean(tmp_path, mode):
    """快照读取失败（并发窗口：端点校验后文件被删/损坏）→ warning 留痕 + 清理登记
    + 不派发任何帧（绝不带脏快照跑批）。"""
    from app.gateway.routers import video_gen as vg

    task_dir = tmp_path / "test-user-autouse" / "tasks" / f"vg_{mode}"
    task_dir.mkdir(parents=True)
    if mode == "corrupt":
        (task_dir / "storyboard.json").write_text("{这不是合法JSON", encoding="utf-8")

    class _Svc:
        _storage_dir = str(tmp_path)

        def __init__(self):
            self.generated = []

        async def _generate_frame_preview(self, task_id, frame_index, user_id):
            self.generated.append(frame_index)
            return "http://x/gen.png"

        async def _write_preview_error(self, *a):
            return None

    svc = _Svc()
    vg._preview_all_running[f"vg_{mode}"] = object()  # 模拟端点侧登记
    await vg._preview_all_bg(svc, f"vg_{mode}", [0, 1], "test-user-autouse")
    assert svc.generated == [], "快照失败不派发任何帧"
    assert f"vg_{mode}" not in vg._preview_all_running, "失败路径也要清理 _preview_all_running 登记"
