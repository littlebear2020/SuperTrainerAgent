"""E6（360 通道切换，2026-08-21）：script_model 显式化 + 任务级覆盖。

依据 docs/plans/2026-08-21-video-gen-360-switch-impl-plan.md E6：
- 分镜/脚本全部调用点改走 ``payload.script_model 或 config.script_model``；
- **任何分支不得回落 ``models[0]``**（``create_chat_model(None)`` 即 models[0]——
  静默用聊天主力模型出分镜，昂贵且未按分镜任务调优）；
- 任务级覆盖照 ``image_provider`` 同款模式（payload 字段 + 校验）。
"""

from __future__ import annotations

import pytest


@pytest.fixture()
def _fake_chat_model(monkeypatch):
    """记录 create_chat_model 入参的桩（三处调用点均在函数内局部 import，
    打 deerflow.models 属性即可拦截）。"""
    calls: list[str | None] = []

    def _fake(name=None, thinking_enabled=False, **kwargs):
        calls.append(name)
        return object()

    import deerflow.models as m

    monkeypatch.setattr(m, "create_chat_model", _fake)
    return calls


def test_config_script_model_used_for_construction(_fake_chat_model):
    from app.gateway.video_gen.config import VideoGenConfig
    from app.gateway.video_gen.service import create_script_llm

    llm = create_script_llm({}, VideoGenConfig(script_model="qwen3.8-flash"))
    assert llm is not None
    assert _fake_chat_model == ["qwen3.8-flash"]


def test_payload_script_model_overrides_config(_fake_chat_model):
    from app.gateway.video_gen.config import VideoGenConfig
    from app.gateway.video_gen.service import create_script_llm

    create_script_llm({"script_model": "qwen/qwen3.8-max"}, VideoGenConfig(script_model="qwen3.8-flash"))
    assert _fake_chat_model == ["qwen/qwen3.8-max"]


def test_unset_script_model_raises_instead_of_models0(_fake_chat_model):
    """未配置时显式报错——不再读 models[0]（create_chat_model(None) 路径锁死）。"""
    from app.gateway.video_gen.config import VideoGenConfig
    from app.gateway.video_gen.service import create_script_llm

    with pytest.raises(ValueError, match="script_model"):
        create_script_llm({}, VideoGenConfig(script_model=None))
    assert _fake_chat_model == [], "未配置时不得构造任何模型（尤其不得传 None=models[0]）"


def test_blank_script_model_treated_as_unset(_fake_chat_model):
    from app.gateway.video_gen.config import VideoGenConfig
    from app.gateway.video_gen.service import create_script_llm

    with pytest.raises(ValueError, match="script_model"):
        create_script_llm({"script_model": "  "}, VideoGenConfig(script_model=""))


def test_schema_accepts_task_level_script_model(monkeypatch):
    """任务级覆盖照 image_provider 同款模式：payload 字段可传。

    条目 7（2026-09-12 缺陷批）：script_model 现加花名册校验——本用例钉 roster
    为固定集合（不耦合部署 config；CI 无 config.yaml 时 roster=None 本就跳过）。
    """
    from app.gateway.video_gen import schemas
    from app.gateway.video_gen.schemas import CreateVideoRequest

    monkeypatch.setattr(schemas, "_model_roster", lambda: {"qwen3.8-max", "qwen3.8-flash"})
    req = CreateVideoRequest(video_type="ai_video", mode="fixed", script="x" * 100, script_model="qwen3.8-max")
    assert req.script_model == "qwen3.8-max"
    # 默认 None = 跟随 config（语义与 image_provider 一致）
    req2 = CreateVideoRequest(video_type="ai_video", mode="fixed", script="x" * 100)
    assert req2.script_model is None


def test_schema_rejects_blank_script_model():
    """空串是配置错误的信号（用户想指定但没填）——422 拒绝，不静默当 None。"""
    from pydantic import ValidationError

    from app.gateway.video_gen.schemas import CreateVideoRequest

    with pytest.raises(ValidationError):
        CreateVideoRequest(video_type="ai_video", mode="fixed", script="x" * 100, script_model="  ")


# ── 条目 7（2026-09-12 缺陷批）：script_model 花名册校验 + config 级撤册留痕 ──


def test_schema_rejects_roster_miss(monkeypatch):
    """花名册外模型名建单 → 422 + 可操作文案（不再受理后硬 ValueError 立刻 failed）。"""
    from pydantic import ValidationError

    from app.gateway.video_gen import schemas
    from app.gateway.video_gen.schemas import CreateVideoRequest

    monkeypatch.setattr(schemas, "_model_roster", lambda: {"qwen3.8-max"})
    with pytest.raises(ValidationError, match="花名册"):
        CreateVideoRequest(video_type="ai_video", mode="fixed", script="x" * 100, script_model="ghost-model")


def test_schema_roster_skip_when_no_deployment_config(monkeypatch):
    """无部署配置（roster=None，CI/全新 clone）跳过校验——只拒可判定的错。"""
    from app.gateway.video_gen import schemas
    from app.gateway.video_gen.schemas import CreateVideoRequest

    monkeypatch.setattr(schemas, "_model_roster", lambda: None)
    req = CreateVideoRequest(video_type="ai_video", mode="fixed", script="x" * 100, script_model="any-unregistered")
    assert req.script_model == "any-unregistered"


def test_create_records_config_roster_miss_degradation(tmp_path, monkeypatch):
    """config 级模型名撤册（subtitle/shot_table）→ 建单受理 + 任务 degradation
    留痕（回退链继续，不 422）。

    vimax_llm_model 不参与建单预检（复审修订 2026-09-13）：双用途字段——裸
    model id 经 models[] 反查命中即零回退（2026-10-12 起缺省值 qwen/qwen3.8-max
    即此类）、「非花名册名」≠配置错误。真实回退点的留痕见
    test_video_gen_vimax_story_model.py 的 fallback 用例。
    """
    import json as _json
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.gateway.routers import video_gen as vg_router
    from app.gateway.video_gen.config import VideoGenConfig

    app = FastAPI()
    app.include_router(vg_router.router)
    svc = MagicMock()
    svc._config = VideoGenConfig(
        qihu_api_key="t",
        script_model="real-model",
        subtitle_llm_model="ghost-subtitle",
        shot_table_model="ghost-shot",
        vimax_llm_model="vimax-bare-id",  # 非花名册名——建单侧也不得留痕
    )
    svc.submit = AsyncMock(return_value=SimpleNamespace(id="vg_roster1", user_id="test-user-autouse", domain="video_gen", task_type="ai_video", status="pending", progress=0, title="t", error_message=None, retry_count=0, dirty_frames=[]))
    svc._storage_dir = str(tmp_path)
    app.state.video_gen_service = svc
    monkeypatch.setattr(vg_router, "_model_roster", lambda: {"real-model"})

    client = TestClient(app)
    resp = client.post("/api/video/create", json={"video_type": "ai_video", "mode": "fixed", "script": "一段足够长的固定脚本内容。" * 5})
    assert resp.status_code == 200, resp.text
    deg_path = tmp_path / "test-user-autouse" / "tasks" / "vg_roster1" / "degradations.json"
    items = _json.loads(deg_path.read_text(encoding="utf-8"))["items"]
    roster_items = [i for i in items if i["kind"] == "model_roster_miss"]
    # sidecar 同 kind 幂等去重 → 双字段同缺聚合为单条 detail（信息不丢）
    assert len(roster_items) == 1
    assert "ghost-subtitle" in roster_items[0]["detail"]
    assert "ghost-shot" in roster_items[0]["detail"]
    # vimax 双用途字段：建单侧零留痕（系统性误报已移除）
    assert all("vimax-bare-id" not in i["detail"] for i in items)


def test_create_records_config_script_model_miss_with_truthful_detail(tmp_path, monkeypatch):
    """复审二轮（2026-09-13）：计划①四字段补齐——config 级 script_model 撤册也
    留痕，且 detail 如实写「无回退」（不得沿用「已回退默认链路」套话——该字段
    无回退，任务将在分镜生成时失败）。"""
    import json as _json
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.gateway.routers import video_gen as vg_router
    from app.gateway.video_gen.config import VideoGenConfig

    app = FastAPI()
    app.include_router(vg_router.router)
    svc = MagicMock()
    svc._config = VideoGenConfig(qihu_api_key="t", script_model="ghost-script", subtitle_llm_model="real-model", shot_table_model=None)
    svc.submit = AsyncMock(return_value=SimpleNamespace(id="vg_roster2", user_id="test-user-autouse", domain="video_gen", task_type="ai_video", status="pending", progress=0, title="t", error_message=None, retry_count=0, dirty_frames=[]))
    svc._storage_dir = str(tmp_path)
    app.state.video_gen_service = svc
    monkeypatch.setattr(vg_router, "_model_roster", lambda: {"real-model"})

    client = TestClient(app)
    resp = client.post("/api/video/create", json={"video_type": "ai_video", "mode": "fixed", "script": "一段足够长的固定脚本内容。" * 5})
    assert resp.status_code == 200, resp.text
    deg_path = tmp_path / "test-user-autouse" / "tasks" / "vg_roster2" / "degradations.json"
    items = _json.loads(deg_path.read_text(encoding="utf-8"))["items"]
    miss = [i for i in items if i["kind"] == "model_roster_miss"]
    assert len(miss) == 1
    assert "ghost-script" in miss[0]["detail"]
    assert "无回退" in miss[0]["detail"]
    assert "已回退默认链路" not in miss[0]["detail"]


def test_create_records_shot_table_miss_with_truthful_detail(tmp_path, monkeypatch):
    """三轮复审（2026-09-13）：shot_table_model 显式撤册名也是硬失败——
    「已回退跟随分镜 LLM」只是未配置（None）时的语义，显式名直调
    create_chat_model 无兜底。detail 必须与 script_model 同口径写「无回退」。"""
    import json as _json
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.gateway.routers import video_gen as vg_router
    from app.gateway.video_gen.config import VideoGenConfig

    app = FastAPI()
    app.include_router(vg_router.router)
    svc = MagicMock()
    svc._config = VideoGenConfig(qihu_api_key="t", script_model="real-model", subtitle_llm_model="real-model", shot_table_model="ghost-shot")
    svc.submit = AsyncMock(return_value=SimpleNamespace(id="vg_roster3", user_id="test-user-autouse", domain="video_gen", task_type="ai_video", status="pending", progress=0, title="t", error_message=None, retry_count=0, dirty_frames=[]))
    svc._storage_dir = str(tmp_path)
    app.state.video_gen_service = svc
    monkeypatch.setattr(vg_router, "_model_roster", lambda: {"real-model"})

    client = TestClient(app)
    resp = client.post("/api/video/create", json={"video_type": "ai_video", "mode": "fixed", "script": "一段足够长的固定脚本内容。" * 5})
    assert resp.status_code == 200, resp.text
    deg_path = tmp_path / "test-user-autouse" / "tasks" / "vg_roster3" / "degradations.json"
    items = _json.loads(deg_path.read_text(encoding="utf-8"))["items"]
    miss = [i for i in items if i["kind"] == "model_roster_miss"]
    assert len(miss) == 1
    assert "ghost-shot" in miss[0]["detail"]
    assert "无回退" in miss[0]["detail"]
    assert "已回退跟随分镜 LLM" not in miss[0]["detail"]
