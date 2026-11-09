"""T1（2026-10-16 收口批）：novel2movie 管线拆除后的不可达守卫。

用户拍板（决策①）：novel2movie 不修、直接删除（langchain 1.x 断裂 + 零消费）。
本文件钉死拆除后契约：

- 删除候选模块（pipeline + 7 个 novel agents + 4 个死模型文件）import 必须失败
  ——复活须走 VENDOR_PATCHES 补丁口径重新登记，不允许静默回潮。
- ``vimax.interfaces`` 不再导出 novel 死模型，但活路径模型（Camera/
  CharacterInScene/ShotDescription 等）不受牵连。
- engine 路由对 ``"novel2movie"`` 响亮报错（指向收口 plan），不静默落
  script2video——静默换管线 = 用户提交长篇小说、拿回 30 镜短片的静默错版。
- config 四个死字段（``vimax_longform_*``×3 + ``remotion_agent_assistant``）从
  schema 删除；旧 config.yaml 残留这些键时加载不炸（Pydantic v2 BaseModel 默认
  ``extra='ignore'``——部署侧可从容清理，见 changelog 部署注意）。
- service/config 源码不再残留 ``vimax_longform`` 消费分支。
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest


def _module_import_fails(module: str) -> bool:
    try:
        importlib.import_module(module)
    except ImportError:
        return True
    return False


_DELETED_MODULES = [
    "vimax.pipelines.novel2movie_pipeline",
    "vimax.agents.novel_compressor",
    "vimax.agents.event_extractor",
    "vimax.agents.scene_extractor",
    "vimax.agents.global_information_planner",
    "vimax.agents.best_image_selector",
    "vimax.agents.script_planner",
    "vimax.agents.script_enhancer",
    "vimax.interfaces.event",
    "vimax.interfaces.frame",
    "vimax.interfaces.scene",
    "vimax.interfaces.environment",
]


def test_deleted_modules_raise_import_error():
    """拆除面 import 全部失败（含 interfaces 死模型文件整删）。"""
    survivors = [m for m in _DELETED_MODULES if not _module_import_fails(m)]
    assert not survivors, f"已拆除模块仍可 import：{survivors}"


def test_interfaces_exports_novel_models_removed_but_live_models_intact():
    """死模型出导出；活路径模型（script2video/idea2video 消费面）原样保留。"""
    import vimax.interfaces as interfaces

    for dead in ("Event", "Frame", "Scene", "EnvironmentInScene", "CharacterInEvent", "CharacterInNovel"):
        assert not hasattr(interfaces, dead), f"novel 死模型 {dead} 仍在 vimax.interfaces 导出"
    for live in ("Camera", "CharacterInScene", "ShotDescription", "ShotBriefDescription", "ImageOutput", "VideoOutput"):
        assert hasattr(interfaces, live), f"活路径模型 {live} 被误删"


def test_agents_exports_only_live_pipeline_agents():
    """agents 导出面只剩 script2video/idea2video 需要的 6 个 agent。"""
    import vimax.agents as agents

    expected = {"Screenwriter", "StoryboardArtist", "CameraImageGenerator", "CharacterExtractor", "CharacterPortraitsGenerator", "ReferenceImageSelector"}
    assert set(agents.__all__) == expected


def test_resolve_pipeline_class_raises_for_novel2movie():
    """路由层响亮报错（不静默回落 script2video），文案指向收口 plan。"""
    from app.gateway.video_gen.engines.vimax.engine import _resolve_pipeline_class

    with pytest.raises(RuntimeError, match=r"2026-10-16\s*拆除"):
        _resolve_pipeline_class({"pipeline": "novel2movie"})


def test_resolve_pipeline_class_live_knobs_unaffected():
    from vimax.pipelines.idea2video_pipeline import Idea2VideoPipeline
    from vimax.pipelines.script2video_pipeline import Script2VideoPipeline

    from app.gateway.video_gen.engines.vimax.engine import _resolve_pipeline_class

    assert _resolve_pipeline_class({"pipeline": "idea2video"}) is Idea2VideoPipeline
    assert _resolve_pipeline_class({"pipeline": "script2video"}) is Script2VideoPipeline
    assert _resolve_pipeline_class({}) is Script2VideoPipeline  # 默认档


def test_config_death_fields_removed_and_stale_yaml_tolerated():
    """四个死字段出 schema；旧 config.yaml 残留键加载不炸（extra=ignore）。"""
    from app.gateway.video_gen.config import VideoGenConfig

    cfg = VideoGenConfig.model_validate(
        {
            "enabled": True,
            "vimax_longform_max_image_gen": 999,
            "vimax_longform_max_video_gen": 1,
            "vimax_longform_budget_cny": 1.0,
            "remotion_agent_assistant": "course-video",
        }
    )
    assert cfg.enabled is True
    for dead in ("vimax_longform_max_image_gen", "vimax_longform_max_video_gen", "vimax_longform_budget_cny", "remotion_agent_assistant"):
        assert not hasattr(cfg, dead), f"死字段 {dead} 仍在 VideoGenConfig schema"


def test_source_no_longform_consumption_left():
    """service.py / config.py 源码不残留 vimax_longform 消费分支。"""
    import app.gateway.video_gen.config as config_mod
    import app.gateway.video_gen.service as service_mod

    for mod in (service_mod, config_mod):
        src = Path(mod.__file__).read_text(encoding="utf-8")
        assert "vimax_longform" not in src, f"{mod.__name__} 仍残留 vimax_longform 引用"
