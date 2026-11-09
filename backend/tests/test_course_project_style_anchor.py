"""课程项目一致性补全一期：风格锚图字段（C1）+ scene_style_refs 拼接退役（C2）。

计划：docs/plans/2026-08-24-course-project-consistency-plan.md
- TC-C1-01 存量兼容：无 style_anchor_image 的项目 JSON → from_dict 落 None 不崩
- TC-C1-02 端到端读写：PUT 写入 → GET 读回一致（经 CourseProjectInput，extra="forbid"）
- TC-C2-01 拼接退役：含 scene_style_refs 的项目 → style_prefix 无 URL、无「参考场景风格图」
"""

from __future__ import annotations

import base64
import inspect
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI

from app.gateway.video_gen.providers.base import ImageRef, VideoRef
from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame

# ── C1: CourseProject.style_anchor_image 字段 ────────────────────────────────


def test_tc_c1_01_legacy_project_without_style_anchor_image_loads(tmp_path):
    """存量项目 JSON 无 style_anchor_image 字段 → from_dict 落 None，不崩。"""
    from app.gateway.video_gen.course_models import CourseProject

    legacy = {
        "id": "cp_legacy000001",
        "user_id": "u1",
        "title": "旧项目",
        "visual_style_preset": "education_3d",
        "character_sheets": [],
        "scene_style_refs": [],
    }
    (tmp_path / "legacy.json").write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")

    proj = CourseProject.from_dict(json.loads((tmp_path / "legacy.json").read_text(encoding="utf-8")))
    assert proj.style_anchor_image is None

    # to_dict/from_dict 往返无损
    d = proj.to_dict()
    assert d.get("style_anchor_image") is None
    proj2 = CourseProject.from_dict(d)
    assert proj2.style_anchor_image is None


def test_tc_c1_01_roundtrip_with_value(tmp_path):
    """设值后 to_dict/from_dict 往返无损（本地绝对路径，同三视图纪律）。"""
    from app.gateway.video_gen.course_models import CourseProject

    proj = CourseProject(id="cp_x", user_id="u1", title="t", style_anchor_image="/storage/style_anchor/cp_x/final.png")
    d = proj.to_dict()
    assert d["style_anchor_image"] == "/storage/style_anchor/cp_x/final.png"
    proj2 = CourseProject.from_dict(d)
    assert proj2.style_anchor_image == "/storage/style_anchor/cp_x/final.png"


def _app_with_router(storage_dir):
    from unittest.mock import MagicMock

    from fastapi import FastAPI

    from app.gateway.routers.video_gen import router

    app = FastAPI()
    app.include_router(router)
    app.state.video_gen_service = MagicMock(_storage_dir=str(storage_dir))
    return app


@pytest.mark.asyncio
async def test_tc_c1_02_put_then_get_roundtrip(tmp_path):
    """API PUT 写入 style_anchor_image → GET 读回一致（端到端，经 Pydantic 白名单）。"""
    from httpx import ASGITransport, AsyncClient

    app = _app_with_router(tmp_path)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 先建项目（字段在 create 即可设置——CourseProjectInput 白名单内）
        resp = await client.post("/api/video/projects", json={"title": "锚图项目"})
        assert resp.status_code == 200, resp.text
        pid = resp.json()["id"]

        # PUT 写入
        resp = await client.put(f"/api/video/projects/{pid}", json={"style_anchor_image": "/storage/style_anchor/x/anchor.png"})
        assert resp.status_code == 200, resp.text
        assert resp.json()["style_anchor_image"] == "/storage/style_anchor/x/anchor.png"

        # GET 读回一致
        resp = await client.get(f"/api/video/projects/{pid}")
        assert resp.status_code == 200, resp.text
        assert resp.json()["style_anchor_image"] == "/storage/style_anchor/x/anchor.png"

        # 显式清空（决策 21 清除语义：清空必须显式传 ""）
        resp = await client.put(f"/api/video/projects/{pid}", json={"style_anchor_image": ""})
        assert resp.status_code == 200, resp.text
        resp = await client.get(f"/api/video/projects/{pid}")
        assert resp.json()["style_anchor_image"] == ""


# ── C2: scene_style_refs 文本拼接退役（决策 7）──────────────────────────────


def test_tc_c2_01_style_prefix_drops_scene_style_refs_concat():
    """含 scene_style_refs 的项目 → style_prefix 不含「参考场景风格图」与任何 URL。"""
    from app.gateway.video_gen.course_models import CourseProject

    p = CourseProject(
        id="cp_x",
        user_id="u1",
        title="t",
        visual_style_preset="education_3d",
        scene_style_refs=["https://cdn.example.com/a.png", "https://cdn.example.com/b.jpg"],
    )
    s = p.style_prefix
    assert "参考场景风格图" not in s, f"拼接须退役，实际={s!r}"
    assert "https://" not in s, f"URL 不得以文字形式进 prompt，实际={s!r}"
    assert "a.png" not in s and "b.jpg" not in s
    # 其余组成部分不受影响（风格前缀查表仍生效）
    assert s, "无场景参考图时 prefix 仍应有风格前缀"


def test_tc_c2_01_dataclass_field_kept_for_legacy_compat():
    """dataclass 字段保留不删（存量 JSON 兼容）：from_dict 不崩、值保留。"""
    from app.gateway.video_gen.course_models import CourseProject

    legacy = {
        "id": "cp_old",
        "user_id": "u1",
        "title": "t",
        "scene_style_refs": ["https://old.example.com/x.png"],
    }
    proj = CourseProject.from_dict(legacy)
    assert proj.scene_style_refs == ["https://old.example.com/x.png"]


# ── C10: 参考图预算双阈值闸门 + sidecar schema（决策 17/19）─────────────────


def test_tc_c10_01_working_limit_truncation():
    """输入 7 张 → 输出 6 张 + truncated 含第 7 张。"""
    from app.gateway.video_gen.service import apply_ref_cap

    refs = [f"/refs/r{i}.png" for i in range(7)]
    keep, sidecar = apply_ref_cap(refs)
    assert keep == refs[:6]
    assert sidecar["truncated"] == ["/refs/r6.png"]
    assert sidecar["reason"] == "budget"


def test_tc_c10_02_no_truncation_under_working_limit():
    """输入 ≤6 → 原样通过，truncated 空、reason 空。"""
    from app.gateway.video_gen.service import apply_ref_cap

    refs = [f"/refs/r{i}.png" for i in range(6)]
    keep, sidecar = apply_ref_cap(refs)
    assert keep == refs
    assert sidecar["truncated"] == []
    assert sidecar["reason"] == ""

    # 空输入
    keep, sidecar = apply_ref_cap([])
    assert keep == []
    assert sidecar["truncated"] == []


def test_tc_c10_03_order_preserved_char_props_anchor():
    """混合输入（已按 角色>道具>锚图 排序）→ 输出顺序保持。"""
    from app.gateway.video_gen.service import apply_ref_cap

    # 3 角色 + 2 道具 + 1 锚图 = 6（工作点内）→ 全过且顺序不变
    refs = ["/char/a.png", "/char/b.png", "/char/c.png", "/prop/p1.png", "/prop/p2.png", "/anchor/x.png"]
    keep, _ = apply_ref_cap(refs)
    assert keep == refs

    # 4 角色 + 2 道具 + 1 锚图 = 7 → 截掉的是末尾锚图（顺序优先级即截断优先级）
    refs7 = ["/char/a.png", "/char/b.png", "/char/c.png", "/char/d.png", "/prop/p1.png", "/prop/p2.png", "/anchor/x.png"]
    keep7, sidecar = apply_ref_cap(refs7)
    assert keep7 == refs7[:6]
    assert sidecar["truncated"] == ["/anchor/x.png"]


def test_tc_c10_04_sidecar_schema_complete(tmp_path):
    """sidecar ref_injection.json 含全部 schema 字段（按帧合并写）。"""
    from app.gateway.video_gen.service import record_ref_injection_sidecar

    record_ref_injection_sidecar(
        tmp_path,
        frame_index=3,
        declared_entities=["角色A"],
        injected_refs=[{"entity": "角色A", "path": "/char/a.png", "view": "front"}, {"entity": "style_anchor", "path": "/anchor/x.png"}],
        truncated=[],
        skipped=[],
        reason="",
    )
    # 第二帧再写一条 → 同文件多条记录合并
    record_ref_injection_sidecar(
        tmp_path,
        frame_index=5,
        declared_entities=[],
        injected_refs=[{"entity": "style_anchor", "path": "/anchor/x.png"}],
        truncated=[{"entity": "style_anchor", "path": "/anchor/y.png"}],
        skipped=[{"entity": "style_anchor", "reason": "file_missing"}],
        reason="budget",
    )

    data = json.loads((tmp_path / "sidecar" / "ref_injection.json").read_text(encoding="utf-8"))
    records = {r["frame_index"]: r for r in data}
    assert set(records) == {3, 5}
    for r in records.values():
        # 机制 M（2026-08-31）新增 first_frame 字段（该帧是否注入预览图首帧）
        assert set(r) == {"frame_index", "declared_entities", "injected_refs", "truncated", "skipped", "reason", "first_frame"}
    assert records[5]["skipped"] == [{"entity": "style_anchor", "reason": "file_missing"}]


# ── C4/C5: 锚图专用参数通道 + 回退位（决策 10/13/20）───────────────────────

_DATA_PNG = "data:image/png;base64," + base64.b64encode(b"x" * 700).decode()


def _anchor_png(tmp_path, pid="cp_abcdef00000a", name="anchor.png") -> str:
    d = tmp_path / "style_anchor" / pid
    d.mkdir(parents=True, exist_ok=True)
    p = d / name
    p.write_bytes(b"\x89PNG fake")
    return str(p)


def _seed_project_with_anchor(tmp_path, anchor_path, pid="cp_abcdef00000a", user="default") -> str:
    d = tmp_path / "course_projects" / user
    d.mkdir(parents=True, exist_ok=True)
    data = {"id": pid, "user_id": user, "title": "锚图项目", "visual_style_preset": "watercolor", "style_anchor_image": anchor_path}
    (d / f"{pid}.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return pid


def test_tc_c4_02_helper_returns_none_for_detached_or_off(tmp_path, monkeypatch):
    """脱离双保险 + 开关关：helper 均返回 None。"""
    from app.gateway.video_gen.service import _style_anchor_image_from_project

    anchor = _anchor_png(tmp_path)
    pid = _seed_project_with_anchor(tmp_path, anchor)

    # 显式脱离 → None（C3 门控之外的第二道保险）
    assert _style_anchor_image_from_project(str(tmp_path), {"course_project_id": pid, "effective_style_source": "task"}, "default") is None
    # 未仲裁（旧任务缺键）→ None（无法判定不注入）
    assert _style_anchor_image_from_project(str(tmp_path), {"course_project_id": pid}, "default") is None
    # 跟随项目 + 文件存在 → 路径
    assert _style_anchor_image_from_project(str(tmp_path), {"course_project_id": pid, "effective_style_source": "project"}, "default") == anchor


def test_tc_c5_01_anchor_switch_off(tmp_path, monkeypatch):
    """VIDEO_GEN_STYLE_ANCHOR_IMAGE=0 → 无锚图注入（回退位，决策 20 同款形态）。"""
    from app.gateway.video_gen.service import _style_anchor_image_from_project

    anchor = _anchor_png(tmp_path)
    pid = _seed_project_with_anchor(tmp_path, anchor)

    monkeypatch.setenv("VIDEO_GEN_STYLE_ANCHOR_IMAGE", "0")
    assert _style_anchor_image_from_project(str(tmp_path), {"course_project_id": pid, "effective_style_source": "project"}, "default") is None

    monkeypatch.setenv("VIDEO_GEN_STYLE_ANCHOR_IMAGE", "1")
    assert _style_anchor_image_from_project(str(tmp_path), {"course_project_id": pid, "effective_style_source": "project"}, "default") == anchor


def test_tc_c4_03_missing_anchor_file_skips_with_sidecar(tmp_path):
    """锚图文件不存在 → helper None + sidecar skipped 留痕（跳过≠失败）。"""
    from app.gateway.video_gen.service import _style_anchor_image_from_project

    pid = _seed_project_with_anchor(tmp_path, "/nonexistent/style_anchor/missing.png")
    task_dir = tmp_path / "default" / "tasks" / "vg_c403"
    task_dir.mkdir(parents=True)

    got = _style_anchor_image_from_project(str(tmp_path), {"course_project_id": pid, "effective_style_source": "project"}, "default", task_dir=task_dir)
    assert got is None

    sidecar = json.loads((task_dir / "sidecar" / "ref_injection.json").read_text(encoding="utf-8"))
    assert any(r.get("skipped") for r in sidecar), f"应记录 skipped（file_missing），实际={sidecar!r}"
    assert any(r["skipped"][0].get("reason") == "file_missing" for r in sidecar if r.get("skipped"))


def test_tc_c4_04_non_i2i_provider_warns_with_anchor(tmp_path):
    """非白名单 provider + 锚图（无角色图）→ warning + sidecar，不静默。"""
    from app.gateway.video_gen.config import VideoGenConfig
    from app.gateway.video_gen.service import _warn_if_image_provider_not_i2i

    cfg = VideoGenConfig(qihu_api_key="t", ai_image_provider="qihu")  # qihu 不在 i2i 白名单
    task_dir = tmp_path / "vg_c404"
    task_dir.mkdir(parents=True)

    # 锚图存在但 provider 不支持 i2i → 告警条件从「有角色参考图」放宽为「任意参考图（含锚图）非空」
    _warn_if_image_provider_not_i2i(cfg, {}, task_dir, anchor_path="/storage/style_anchor/x.png")
    assert (task_dir / "ai_image_i2i_warning.json").exists(), "锚图被丢弃必须有 sidecar 告警"

    # 无任何参考图 → 不告警（现状不变）
    task_dir2 = tmp_path / "vg_c404b"
    task_dir2.mkdir(parents=True)
    _warn_if_image_provider_not_i2i(cfg, {}, task_dir2, anchor_path=None)
    assert not (task_dir2 / "ai_image_i2i_warning.json").exists()


@pytest.mark.asyncio
async def test_tc_c4_01_anchor_appended_to_refs(tmp_path):
    """跟随项目 → 生图 refs 末尾含锚图（ai_image 合并 + 顺序 角色>锚图）。"""
    from app.gateway.video_gen.visual_sources.ai_image import AIImageVisualSource

    class _Cap:
        def __init__(self):
            self.kwargs: list[dict] = []

        async def generate_image(self, prompt: str, **kwargs):
            self.kwargs.append(kwargs)
            return ImageRef(url=_DATA_PNG, width=1080, height=1920, source="test")

    provider = _Cap()
    src = AIImageVisualSource(provider)
    sb = Storyboard(
        title="t",
        config=StoryboardConfig(video_type="ai_image", width=1080, height=1920),
        frames=[StoryboardFrame(index=0, narration="一", image_prompt="场景", duration=5.0)],
    )
    await src.render(
        sb,
        task_dir=tmp_path,
        reference_image_paths=["/char/f.png", "/char/s.png"],
        style_anchor_image_paths=["/anchor/x.png"],
    )
    assert provider.kwargs[0].get("reference_image_paths") == ["/char/f.png", "/char/s.png", "/anchor/x.png"]
    assert sb.frames[0].visual_type == "image", "帧生成成功"


@pytest.mark.asyncio
async def test_tc_c4_06_ai_video_ignores_anchor_param():
    """ai_video render 收到参数但 refs/方言零变化（视频侧隔离，决策 13）。"""
    from app.gateway.video_gen.visual_sources.ai_video import AIVideoVisualSource

    class _Cap:
        def __init__(self):
            self.calls: list[tuple[str, dict]] = []

        async def generate_video(self, prompt: str, *, duration: float = 8.0, **kwargs):
            self.calls.append((prompt, kwargs))
            return VideoRef(url="https://cdn/a.mp4", duration=8.0, source="test")

    provider = _Cap()
    src = AIVideoVisualSource(provider)
    sb = Storyboard(
        title="t",
        config=StoryboardConfig(video_type="ai_video", width=1080, height=1920),
        frames=[StoryboardFrame(index=0, narration="一", image_prompt="场景", duration=8.0)],
    )
    await src.render(sb, reference_image_paths=["/char/f.png"], style_anchor_image_paths=["/anchor/x.png"])

    prompt, kwargs = provider.calls[0]
    refs = kwargs.get("image_urls") or kwargs.get("reference_image_paths") or []
    assert "/anchor/x.png" not in refs, "锚图不得进视频侧 refs"
    assert "锚图" not in prompt and "风格参考" not in prompt, "视频方言不得因锚图变化"

    # 无锚图参数的对照组：prompt 逐字一致（方言零变化）
    provider2 = _Cap()
    src2 = AIVideoVisualSource(provider2)
    sb2 = Storyboard(
        title="t",
        config=StoryboardConfig(video_type="ai_video", width=1080, height=1920),
        frames=[StoryboardFrame(index=0, narration="一", image_prompt="场景", duration=8.0)],
    )
    await src2.render(sb2, reference_image_paths=["/char/f.png"])
    assert provider2.calls[0][0] == prompt


def test_tc_c4_05_injection_point_coverage():
    """注入点覆盖（两叉断言）：3 个 render 点传 style_anchor_image_paths + 预览点 service 层并入。"""

    from app.gateway.video_gen import service as service_mod

    for fn_name in ("_prepare_storyboard_video", "_render_from_draft", "_regenerate_frame"):
        src = inspect.getsource(getattr(service_mod.VideoGenService, fn_name))
        # 条件传参形态：_anchor_kwargs = {"style_anchor_image_paths": [_anchor]} if _anchor else {}
        # （有锚图才传——既有窄签名 render 桩/第三方实现天然兼容）
        assert '"style_anchor_image_paths"' in src and "**_anchor_kwargs" in src, f"{fn_name} 须向 visual.render 条件传 style_anchor_image_paths"
        assert "_style_anchor_image_from_project" in src, f"{fn_name} 须计算锚图"

    # 预览点不走 render：service 层并入 refs
    preview_src = inspect.getsource(service_mod.VideoGenService._generate_frame_preview)
    assert "_style_anchor_image_from_project" in preview_src, "预览点须在 service 层计算锚图并入 refs"


# ── C6: 项目资产页风格锚图管理端点（决策 4）───────────────────────────────


def _make_service(tmp_path):
    from app.gateway.video_gen.config import VideoGenConfig
    from app.gateway.video_gen.providers.registry import ProviderRegistry
    from app.gateway.video_gen.service import VideoGenService

    cfg = VideoGenConfig(qihu_api_key="t", script_model="m")
    repo = MagicMock()
    repo.update = AsyncMock(return_value=None)
    return VideoGenService(repo=repo, config=cfg, providers=ProviderRegistry(cfg), storage_dir=str(tmp_path), sandbox_provider=AsyncMock())


def _seed_project(tmp_path, user="test-user-autouse", pid="cp_abcdef00000c", **fields) -> str:
    # user 对齐 tests/conftest.py 的 autouse user contextvar（get_effective_user_id 在
    # pytest 下 = test-user-autouse，非 "default"）——C1-02 经 API create 无此问题。
    d = tmp_path / "course_projects" / user
    d.mkdir(parents=True, exist_ok=True)
    data = {"id": pid, "user_id": user, "title": "锚图项目", "visual_style_preset": "watercolor", **fields}
    (d / f"{pid}.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return pid


def _c6_service(tmp_path):
    from app.gateway.video_gen.config import VideoGenConfig
    from app.gateway.video_gen.providers.registry import ProviderRegistry
    from app.gateway.video_gen.service import VideoGenService

    cfg = VideoGenConfig(qihu_api_key="t", script_model="m")
    repo = MagicMock()
    repo.update = AsyncMock(return_value=None)
    return VideoGenService(repo=repo, config=cfg, providers=ProviderRegistry(cfg), storage_dir=str(tmp_path), sandbox_provider=AsyncMock())


@pytest.mark.asyncio
async def test_tc_c6_01_generate_endpoint_writes_candidate_only(tmp_path, monkeypatch):
    """mock provider → 出图落盘 candidates/，项目字段未变。"""
    from httpx import ASGITransport, AsyncClient

    svc = _c6_service(tmp_path)

    class _FakeProvider:
        def __init__(self):
            self.prompts: list[str] = []

        async def generate_image(self, prompt: str, **kw):
            self.prompts.append(prompt)
            return ImageRef(url=_DATA_PNG, width=1024, height=1024, source="test")

    fake = _FakeProvider()
    monkeypatch.setattr(svc._providers, "make_ai_image", lambda name=None: fake)

    pid = _seed_project(tmp_path, pid="cp_c60000000001", visual_style_preset="watercolor")

    app = FastAPI()
    app.include_router(_vg_router())
    app.state.video_gen_service = svc
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(f"/api/video/projects/{pid}/style-anchor-image/generate", json={"prompt": "抽象几何场景"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body.get("image_url"), "应返回 serve URL"

        # 落盘 candidates/（目录拍死：style_anchor/{pid}/candidates/，无 user 维度）
        cand_dir = tmp_path / "style_anchor" / pid / "candidates"
        assert cand_dir.exists() and list(cand_dir.glob("*.png")), "候选图应落盘 candidates/"

        # 项目字段未变（不写 style_anchor_image）
        resp = await client.get(f"/api/video/projects/{pid}")
        assert resp.json().get("style_anchor_image") in (None, "")

    # prompt = 项目风格前缀 + 用户描述
    assert fake.prompts, "provider 应被调用"
    assert "水彩" in fake.prompts[0] and "抽象几何场景" in fake.prompts[0], f"prompt 须含项目风格前缀+用户描述，实际={fake.prompts[0]!r}"


@pytest.mark.asyncio
async def test_tc_c6_02_set_and_replace_deletes_old_file(tmp_path):
    """固化 → 字段更新；再固化 → 旧文件删除。"""
    from httpx import ASGITransport, AsyncClient

    svc = _c6_service(tmp_path)
    pid = _seed_project(tmp_path, pid="cp_c60000000002")

    cand_dir = tmp_path / "style_anchor" / pid / "candidates"
    cand_dir.mkdir(parents=True)
    c1 = cand_dir / "c1.png"
    c1.write_bytes(b"\x89PNG old")
    c2 = cand_dir / "c2.png"
    c2.write_bytes(b"\x89PNG new")

    app = FastAPI()
    app.include_router(_vg_router())
    app.state.video_gen_service = svc
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 固化候选 1
        resp = await client.post(f"/api/video/projects/{pid}/style-anchor-image", json={"candidate_path": str(c1)})
        assert resp.status_code == 200, resp.text
        assert resp.json()["style_anchor_image"] == str(c1)

        # 再固化候选 2 → 旧文件删除
        resp = await client.post(f"/api/video/projects/{pid}/style-anchor-image", json={"candidate_path": str(c2)})
        assert resp.status_code == 200, resp.text
        assert resp.json()["style_anchor_image"] == str(c2)

        assert not c1.exists(), "替换后旧锚图文件必须删除（新资产清理纪律）"
        assert c2.exists()


@pytest.mark.asyncio
async def test_tc_c6_03_serve_404_then_200(tmp_path):
    """serve：无锚图 404；有锚图 200。"""
    from httpx import ASGITransport, AsyncClient

    svc = _c6_service(tmp_path)
    pid = _seed_project(tmp_path, pid="cp_c60000000003")

    anchor = tmp_path / "style_anchor" / pid / "anchor_1.png"
    anchor.parent.mkdir(parents=True)
    anchor.write_bytes(b"\x89PNG anchor")

    app = FastAPI()
    app.include_router(_vg_router())
    app.state.video_gen_service = svc
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get(f"/api/video/projects/{pid}/style-anchor-image")
        assert resp.status_code == 404

        resp = await client.put(f"/api/video/projects/{pid}", json={"style_anchor_image": str(anchor)})
        assert resp.status_code == 200

        resp = await client.get(f"/api/video/projects/{pid}/style-anchor-image")
        assert resp.status_code == 200
        assert resp.content == b"\x89PNG anchor"

        # DELETE → 清字段 + 删文件 → 再 GET 404
        resp = await client.delete(f"/api/video/projects/{pid}/style-anchor-image")
        assert resp.status_code == 200
        assert not anchor.exists()
        resp = await client.get(f"/api/video/projects/{pid}")
        assert resp.json().get("style_anchor_image") in (None, "")
        resp = await client.get(f"/api/video/projects/{pid}/style-anchor-image")
        assert resp.status_code == 404


# ── C6-方案B（2026-08-30）：风格锚图「包含人物」开关，默认关 ────────────────


@pytest.mark.asyncio
async def test_tc_c6_04_no_people_default_strips_person_terms_and_appends_directive(tmp_path, monkeypatch):
    """默认（不传 include_people）→ corporate_live 剥离人物词 + 追加无人物硬约束。"""
    from httpx import ASGITransport, AsyncClient

    from app.gateway.video_gen.style_presets import NO_PEOPLE_DIRECTIVE

    svc = _c6_service(tmp_path)

    class _FakeProvider:
        def __init__(self):
            self.prompts: list[str] = []

        async def generate_image(self, prompt: str, **kw):
            self.prompts.append(prompt)
            return ImageRef(url=_DATA_PNG, width=1024, height=1024, source="test")

    fake = _FakeProvider()
    monkeypatch.setattr(svc._providers, "make_ai_image", lambda name=None: fake)
    pid = _seed_project(tmp_path, pid="cp_c60000000004", visual_style_preset="corporate_live")

    app = FastAPI()
    app.include_router(_vg_router())
    app.state.video_gen_service = svc
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 存量兼容：旧前端/脚本不传 include_people → 默认 False 走无人物路径，不 422
        resp = await client.post(f"/api/video/projects/{pid}/style-anchor-image/generate", json={"prompt": "现代办公桌面场景"})
        assert resp.status_code == 200, resp.text

    prompt = fake.prompts[0]
    assert NO_PEOPLE_DIRECTIVE in prompt, f"默认必须追加无人物硬约束，实际={prompt!r}"
    assert "人物保持中远景" not in prompt, f"预设人物词须被剥离，实际={prompt!r}"
    assert "现代办公桌面场景" in prompt, "用户输入须保留"
    # 用户输入位于 directive 之前（directive 兜底盖过输入里的带人物倾向）
    assert prompt.index("现代办公桌面场景") < prompt.index(NO_PEOPLE_DIRECTIVE)


def test_tc_c6_05_no_people_variant_character_3d():
    """纯函数级：含人物词 key 的去人物变体 + 无变体 key 回落原前缀。"""
    from app.gateway.video_gen.style_presets import style_prefix_for

    assert "Q版角色" not in style_prefix_for("character_3d", no_people=True)
    assert "人物" not in style_prefix_for("corporate_live", no_people=True)
    assert "Q版角色" in style_prefix_for("character_3d")  # 默认路径不变
    # 无变体 key → 回落原 prefix
    assert style_prefix_for("photorealistic", no_people=True) == style_prefix_for("photorealistic")
    assert style_prefix_for(None, no_people=True) == ""


@pytest.mark.asyncio
async def test_tc_c6_05b_no_people_character_3d_route(tmp_path, monkeypatch):
    """character_3d 经路由默认生成 → 「Q版角色」剥离、directive 追加、风格本体保留。"""
    from httpx import ASGITransport, AsyncClient

    from app.gateway.video_gen.style_presets import NO_PEOPLE_DIRECTIVE

    svc = _c6_service(tmp_path)

    class _FakeProvider:
        def __init__(self):
            self.prompts: list[str] = []

        async def generate_image(self, prompt: str, **kw):
            self.prompts.append(prompt)
            return ImageRef(url=_DATA_PNG, width=1024, height=1024, source="test")

    fake = _FakeProvider()
    monkeypatch.setattr(svc._providers, "make_ai_image", lambda name=None: fake)
    pid = _seed_project(tmp_path, pid="cp_c60000000005", visual_style_preset="character_3d")

    app = FastAPI()
    app.include_router(_vg_router())
    app.state.video_gen_service = svc
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            f"/api/video/projects/{pid}/style-anchor-image/generate",
            json={"prompt": "温馨客厅一角", "include_people": False},
        )
        assert resp.status_code == 200, resp.text

    prompt = fake.prompts[0]
    assert NO_PEOPLE_DIRECTIVE in prompt
    assert "Q版角色" not in prompt, f"Q版角色词须被剥离，实际={prompt!r}"
    assert "皮克斯风格3D卡通渲染" in prompt, "风格本体须保留"


@pytest.mark.asyncio
async def test_tc_c6_06_include_people_keeps_prefix_unchanged(tmp_path, monkeypatch):
    """勾选「包含人物」→ 预设原样保留（人物词回来）、无 directive。"""
    from httpx import ASGITransport, AsyncClient

    from app.gateway.video_gen.style_presets import NO_PEOPLE_DIRECTIVE

    svc = _c6_service(tmp_path)

    class _FakeProvider:
        def __init__(self):
            self.prompts: list[str] = []

        async def generate_image(self, prompt: str, **kw):
            self.prompts.append(prompt)
            return ImageRef(url=_DATA_PNG, width=1024, height=1024, source="test")

    fake = _FakeProvider()
    monkeypatch.setattr(svc._providers, "make_ai_image", lambda name=None: fake)
    pid = _seed_project(tmp_path, pid="cp_c60000000006", visual_style_preset="corporate_live")

    app = FastAPI()
    app.include_router(_vg_router())
    app.state.video_gen_service = svc
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            f"/api/video/projects/{pid}/style-anchor-image/generate",
            json={"prompt": "会议室全景", "include_people": True},
        )
        assert resp.status_code == 200, resp.text

    prompt = fake.prompts[0]
    assert "人物保持中远景" in prompt, f"勾选后预设人物词须原样保留，实际={prompt!r}"
    assert NO_PEOPLE_DIRECTIVE not in prompt, "勾选后不得追加无人物约束"
    assert "会议室全景" in prompt


def test_tc_c6_07_no_people_variant_papercraft_theater():
    """B1：纸艺剧场前缀含「纸偶」（纸偶即角色）——默认路径必须有去人物变体。"""
    from app.gateway.video_gen.style_presets import style_prefix_for

    variant = style_prefix_for("papercraft_theater", no_people=True)
    assert "纸偶" not in variant, f"纸偶角色词须被剥离，实际={variant!r}"
    assert "纸艺定格剧场风格" in variant, "剧场风格灵魂须保留"
    assert "纸偶关节与折痕可见" in style_prefix_for("papercraft_theater")  # 默认路径不变


def test_tc_c6_07b_no_people_variant_completeness_guard():
    """Guard：STYLE_PREFIXES 含人物词的 key 必须都在 STYLE_PREFIX_NO_PEOPLE 有变体。

    防未来新增预设再漏（B1 即 papercraft_theater 漏网）。词表 = style_presets.
    PERSON_TERMS（单一来源，2026-08-31 从本文件内联迁出——初版只列 3 词差点放走
    「模特/真人」类同义词），运行时告警（style_prefix_for）共用同一词表。
    """
    from app.gateway.video_gen.style_presets import PERSON_TERMS, STYLE_PREFIX_NO_PEOPLE, STYLE_PREFIXES

    assert len(PERSON_TERMS) >= 10, "人物词表异常缩小——词表只应增不应减"
    missing = [key for key, prefix in STYLE_PREFIXES.items() if any(term in prefix for term in PERSON_TERMS) and key not in STYLE_PREFIX_NO_PEOPLE]
    assert not missing, f"含人物/角色词的前缀缺少无人物变体: {missing}"
    # 反向：变体表不得引入人物词（corporate_live 的 directive 场景除外——
    # 变体自身不能含人物词，否则去人物路径自相矛盾）
    for key, variant in STYLE_PREFIX_NO_PEOPLE.items():
        assert not any(term in variant for term in PERSON_TERMS), f"{key} 变体含人物词: {variant!r}"


def test_style_prefix_for_warns_on_people_word_without_variant(monkeypatch, caplog):
    """运行时告警出口：无变体但前缀含人物词（词表漏收/新预设漏配）→ warning 留痕，
    不静默拼出自相矛盾 prompt（B1 制度化第二道防线；第一道是 07b guard 测试）。"""
    import logging

    import app.gateway.video_gen.style_presets as sp

    monkeypatch.setitem(sp.STYLE_PREFIXES, "probe_style", "测试风格，模特特写，柔和光线")
    with caplog.at_level(logging.WARNING, logger="app.gateway.video_gen.style_presets"):
        out = sp.style_prefix_for("probe_style", no_people=True)
    assert "模特特写" in out  # 行为不变：回落原 prefix
    assert any("STYLE_PREFIX_NO_PEOPLE" in r.getMessage() for r in caplog.records), "漏配变体须触发 warning"

    # 反向：有变体的 key（corporate_live）不告警
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="app.gateway.video_gen.style_presets"):
        sp.style_prefix_for("corporate_live", no_people=True)
    assert not caplog.records


@pytest.mark.asyncio
async def test_tc_c6_08_include_people_null_treated_as_false(tmp_path, monkeypatch):
    """R4：显式传 include_people=null → 不 422，按 False（无人物路径）处理。"""
    from httpx import ASGITransport, AsyncClient

    from app.gateway.video_gen.style_presets import NO_PEOPLE_DIRECTIVE

    svc = _c6_service(tmp_path)

    class _FakeProvider:
        def __init__(self):
            self.prompts: list[str] = []

        async def generate_image(self, prompt: str, **kw):
            self.prompts.append(prompt)
            return ImageRef(url=_DATA_PNG, width=1024, height=1024, source="test")

    fake = _FakeProvider()
    monkeypatch.setattr(svc._providers, "make_ai_image", lambda name=None: fake)
    pid = _seed_project(tmp_path, pid="cp_c60000000008", visual_style_preset="photorealistic")

    app = FastAPI()
    app.include_router(_vg_router())
    app.state.video_gen_service = svc
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            f"/api/video/projects/{pid}/style-anchor-image/generate",
            json={"prompt": "清晨海边", "include_people": None},
        )
        assert resp.status_code == 200, resp.text

    assert NO_PEOPLE_DIRECTIVE in fake.prompts[0], "null 须按 False 处理（无人物路径）"


@pytest.mark.asyncio
async def test_tc_c6_09_jpeg_data_uri_returns_actual_suffix(tmp_path, monkeypatch):
    """B3：jpeg data URI 落盘改 .jpg 后，candidate_path 必须指向实际写入文件（固化不 404）。"""
    from httpx import ASGITransport, AsyncClient

    svc = _c6_service(tmp_path)
    jpeg_ref = ImageRef(
        url="data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8\xff jpeg-bytes" * 40).decode(),
        width=1024,
        height=1024,
        source="test",
    )

    class _FakeProvider:
        async def generate_image(self, prompt: str, **kw):
            return jpeg_ref

    monkeypatch.setattr(svc._providers, "make_ai_image", lambda name=None: _FakeProvider())
    pid = _seed_project(tmp_path, pid="cp_c60000000009", visual_style_preset="photorealistic")

    app = FastAPI()
    app.include_router(_vg_router())
    app.state.video_gen_service = svc
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            f"/api/video/projects/{pid}/style-anchor-image/generate",
            json={"prompt": "抽象色块"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()

    assert body["candidate_path"].endswith(".jpg"), f"须返回实际写入的 .jpg 路径，实际={body['candidate_path']!r}"
    from pathlib import Path as _P

    assert _P(body["candidate_path"]).exists(), "candidate_path 指向的文件必须真实存在"
    assert "candidate=" in body["image_url"] and ".jpg" in body["image_url"]


# ── C7: 项目级音色/字幕设置生效（决策 21 后端链路，前端零后端改动）────────


@pytest.mark.asyncio
async def test_tc_c7_01_project_voice_subtitle_followed_by_task(tmp_path, monkeypatch):
    """项目设置音色/字幕 → 跟随项目任务生效；显式覆盖 → 任务值优先。"""
    svc = _make_service(tmp_path)
    pid = _seed_project(tmp_path, pid="cp_c70000000001", voice_id="preset:332", subtitle_style="bold-yellow")
    monkeypatch.setattr("app.gateway.video_gen.service.create_script_llm", lambda p, c: MagicMock())
    monkeypatch.setattr(
        "app.gateway.video_gen.orchestration.storyboard_planner.plan_course",
        AsyncMock(),
    )

    _u = "test-user-autouse"  # 与 _seed_project 一致（直调 service 方法不经过 conftest 的 contextvar）

    def _task_dir(name):
        d = tmp_path / _u / "tasks" / name
        d.mkdir(parents=True)
        return d

    # 跟随项目 → 盘面 config 拾取项目音色/字幕（生效链：盘面 config 是 TTS/合成的读取点）
    p1 = {"script": "跟随项目旁白。", "ai_normalize_script": False, "course_project_id": pid}
    d1 = _task_dir("vg_c701a")
    await svc._generate_draft("ai_image", p1, d1, _noop_prog, user_id=_u, task_id="vg_c701a")
    cfg1 = json.loads((d1 / "storyboard.json").read_text(encoding="utf-8"))["config"]
    assert cfg1["voice_id"] == "preset:332"
    assert cfg1["subtitle_style"] == "bold-yellow"

    # 显式覆盖 → 任务值优先
    p2 = {"script": "显式覆盖旁白。", "ai_normalize_script": False, "course_project_id": pid, "voice_id": "preset:111", "subtitle_style": "clean-white"}
    d2 = _task_dir("vg_c701b")
    await svc._generate_draft("ai_image", p2, d2, _noop_prog, user_id=_u, task_id="vg_c701b")
    cfg2 = json.loads((d2 / "storyboard.json").read_text(encoding="utf-8"))["config"]
    assert cfg2["voice_id"] == "preset:111"
    assert cfg2["subtitle_style"] == "clean-white"


def _vg_router():
    from app.gateway.routers.video_gen import router

    return router


async def _noop_prog(**kw):
    pass


# ── B3 补全（2026-08-31 第三轮复核）：真实格式后缀贯通到 serve 与错误契约 ──

_DATA_JPEG = "data:image/jpeg;base64," + base64.b64encode(b"\xff\xd8\xff\xe0" + b"j" * 200).decode()


@pytest.mark.asyncio
async def test_tc_c6_09_jpeg_candidate_served_with_real_content_type(tmp_path, monkeypatch):
    """B3 补全①：jpeg data URI → 落盘 .jpg → serve 按后缀回 Content-Type: image/jpeg。

    修前 serve 写死 media_type='image/png'，JPEG 字节被错标（浏览器 sniff 掩盖，
    下载/严格消费方拿到 .png 名的 JPEG）。
    """
    from httpx import ASGITransport, AsyncClient

    svc = _c6_service(tmp_path)

    class _FakeProvider:
        async def generate_image(self, prompt: str, **kw):
            return ImageRef(url=_DATA_JPEG, width=1024, height=1024, source="test")

    monkeypatch.setattr(svc._providers, "make_ai_image", lambda name=None: _FakeProvider())
    pid = _seed_project(tmp_path, pid="cp_c60000000009")

    app = FastAPI()
    app.include_router(_vg_router())
    app.state.video_gen_service = svc
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(f"/api/video/projects/{pid}/style-anchor-image/generate", json={"prompt": "暖光工作台"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["candidate_path"].endswith(".jpg"), f"jpeg data URI 须改后缀落盘，实际={body['candidate_path']}"
        resp = await client.get(f"/api/video/projects/{pid}/style-anchor-image", params={"candidate": Path(body["candidate_path"]).name})
        assert resp.status_code == 200, resp.text
        assert resp.headers["content-type"].startswith("image/jpeg"), f"serve 须按后缀回真实类型，实际={resp.headers['content-type']}"


@pytest.mark.asyncio
async def test_tc_c6_10_malformed_data_uri_returns_502_not_500(tmp_path, monkeypatch):
    """B3 补全②：畸形 data URI（无逗号）→ 502「生成失败请重试」，不裸 500。

    修前 _write_image_ref_to_path 在 try/except 之外调用，url.split 解包 ValueError
    直接冒成 FastAPI 500。
    """
    from httpx import ASGITransport, AsyncClient

    svc = _c6_service(tmp_path)

    class _FakeProvider:
        async def generate_image(self, prompt: str, **kw):
            return ImageRef(url="data:image/png;base64", width=8, height=8, source="test")  # 无逗号

    monkeypatch.setattr(svc._providers, "make_ai_image", lambda name=None: _FakeProvider())
    pid = _seed_project(tmp_path, pid="cp_c60000000010")

    app = FastAPI()
    app.include_router(_vg_router())
    app.state.video_gen_service = svc
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(f"/api/video/projects/{pid}/style-anchor-image/generate", json={"prompt": "x"})
        assert resp.status_code == 502, f"畸形 ImageRef 须走 502 契约，实际={resp.status_code}: {resp.text}"
        assert "重试" in resp.json()["detail"]


# ── T5（2026-08-31 候选持久化）：生成成功落字段 / 固化·移除·弃用·失败清字段 ────


def _t5_app(svc):
    app = FastAPI()
    app.include_router(_vg_router())
    app.state.video_gen_service = svc
    return app


@pytest.mark.asyncio
async def test_t5_generate_persists_candidate_filename(tmp_path, monkeypatch):
    """生成成功 → 候选**文件名**落 style_anchor_candidate（退出/刷新不丢的根治位）。"""
    from httpx import ASGITransport, AsyncClient

    svc = _c6_service(tmp_path)

    class _FakeProvider:
        async def generate_image(self, prompt: str, **kw):
            return ImageRef(url=_DATA_PNG, width=1024, height=1024, source="test")

    monkeypatch.setattr(svc._providers, "make_ai_image", lambda name=None: _FakeProvider())
    pid = _seed_project(tmp_path, pid="cp_t50000000001")

    async with AsyncClient(transport=ASGITransport(_t5_app(svc)), base_url="http://test") as client:
        resp = await client.post(f"/api/video/projects/{pid}/style-anchor-image/generate", json={"prompt": "x"})
        assert resp.status_code == 200, resp.text
        name = Path(resp.json()["candidate_path"]).name

        proj = (await client.get(f"/api/video/projects/{pid}")).json()
        assert proj.get("style_anchor_candidate") == name, "候选文件名须落项目字段"
        assert not proj.get("style_anchor_image"), "生成不固化（C6 语义不变）"


@pytest.mark.asyncio
async def test_t5_set_by_candidate_name_restores_and_clears(tmp_path):
    """恢复态固化：只持文件名（body ``{candidate}``）→ 按名解析固化 + 清候选字段。"""
    from httpx import ASGITransport, AsyncClient

    svc = _c6_service(tmp_path)
    pid = _seed_project(tmp_path, pid="cp_t50000000002")
    cand = tmp_path / "style_anchor" / pid / "candidates" / "c_restored.png"
    cand.parent.mkdir(parents=True)
    cand.write_bytes(b"\x89PNG restored")

    async with AsyncClient(transport=ASGITransport(_t5_app(svc)), base_url="http://test") as client:
        resp = await client.post(f"/api/video/projects/{pid}/style-anchor-image", json={"candidate": "c_restored.png"})
        assert resp.status_code == 200, resp.text
        proj = resp.json()
        assert Path(proj["style_anchor_image"]).name == "c_restored.png"
        assert proj.get("style_anchor_candidate") in (None, ""), "固化即清候选字段"

        # traversal：按名逃逸出 candidates/ → 404
        resp = await client.post(f"/api/video/projects/{pid}/style-anchor-image", json={"candidate": "../../secret.png"})
        assert resp.status_code == 404


@pytest.mark.asyncio
async def test_t5_generate_failure_clears_stale_candidate(tmp_path, monkeypatch):
    """生成失败（502 路径）→ 清候选字段，防挂载脏恢复旧候选。"""
    from httpx import ASGITransport, AsyncClient

    svc = _c6_service(tmp_path)

    class _Boom:
        async def generate_image(self, prompt: str, **kw):
            raise RuntimeError("provider down")

    monkeypatch.setattr(svc._providers, "make_ai_image", lambda name=None: _Boom())
    pid = _seed_project(tmp_path, pid="cp_t50000000003", style_anchor_candidate="stale.png")

    async with AsyncClient(transport=ASGITransport(_t5_app(svc)), base_url="http://test") as client:
        resp = await client.post(f"/api/video/projects/{pid}/style-anchor-image/generate", json={"prompt": "x"})
        assert resp.status_code == 502
        proj = (await client.get(f"/api/video/projects/{pid}")).json()
        assert proj.get("style_anchor_candidate") is None, "失败须清候选字段（防脏恢复）"


@pytest.mark.asyncio
async def test_t5_delete_anchor_clears_candidate_field(tmp_path):
    """移除锚图 → 候选字段同清（删完锚图不应凭空恢复旧候选条）。"""
    from httpx import ASGITransport, AsyncClient

    svc = _c6_service(tmp_path)
    pid = _seed_project(tmp_path, pid="cp_t50000000004", style_anchor_candidate="c1.png")
    anchor = tmp_path / "style_anchor" / pid / "anchor_1.png"
    anchor.parent.mkdir(parents=True)
    anchor.write_bytes(b"\x89PNG anchor")

    async with AsyncClient(transport=ASGITransport(_t5_app(svc)), base_url="http://test") as client:
        resp = await client.put(f"/api/video/projects/{pid}", json={"style_anchor_image": str(anchor)})
        assert resp.status_code == 200
        resp = await client.delete(f"/api/video/projects/{pid}/style-anchor-image")
        assert resp.status_code == 200
        assert resp.json().get("style_anchor_candidate") is None


@pytest.mark.asyncio
async def test_t5_discard_candidate_endpoint_clears_field_and_file(tmp_path):
    """「换一张」弃用端点：清字段 + 删候选文件 + 幂等；traversal 不越界。"""
    from httpx import ASGITransport, AsyncClient

    svc = _c6_service(tmp_path)
    pid = _seed_project(tmp_path, pid="cp_t50000000005", style_anchor_candidate="c_discard.png")
    cand = tmp_path / "style_anchor" / pid / "candidates" / "c_discard.png"
    cand.parent.mkdir(parents=True)
    cand.write_bytes(b"\x89PNG discard")
    outside = tmp_path / "style_anchor" / pid / "anchor_keep.png"
    outside.write_bytes(b"\x89PNG keep")

    async with AsyncClient(transport=ASGITransport(_t5_app(svc)), base_url="http://test") as client:
        resp = await client.delete(f"/api/video/projects/{pid}/style-anchor-candidate")
        assert resp.status_code == 200, resp.text
        assert resp.json().get("style_anchor_candidate") is None
        assert not cand.exists(), "弃用候选文件应删除"
        assert outside.exists(), "不得越界删 candidates/ 之外的文件"

        # 幂等：字段已空 → 200 no-op
        resp = await client.delete(f"/api/video/projects/{pid}/style-anchor-candidate")
        assert resp.status_code == 200
