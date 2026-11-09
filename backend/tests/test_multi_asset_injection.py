"""课程项目一致性补全二期：多资产机制（M1/M6/M2/M3 上半场）。

计划：docs/plans/2026-08-24-course-project-consistency-plan.md 第四节 T1。
- TC-M1-01~03：角色表稳定 id + name 唯一 + 端点 id 寻址
- TC-M6-01~04：道具资产管理（上传/删除/serve + 张数校验 + 缺失跳过 + 存量兼容）
- TC-M2-01~04：逐镜出场声明（visible_* 对象数组 + 名册归一化 + None/[ ] 语义铁律）
- TC-M3-01~05：按帧子集注入 + 过渡开关（roster 通道 + 帧字段选择 + 逐帧预算）
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI

from app.gateway.video_gen.config import VideoGenConfig
from app.gateway.video_gen.providers.registry import ProviderRegistry
from app.gateway.video_gen.service import VideoGenService

_USER = "test-user-autouse"  # tests/conftest.py autouse contextvar


def _make_service(tmp_path) -> VideoGenService:
    cfg = VideoGenConfig(qihu_api_key="t", script_model="m")
    repo = MagicMock()
    repo.update = AsyncMock(return_value=None)
    return VideoGenService(repo=repo, config=cfg, providers=ProviderRegistry(cfg), storage_dir=str(tmp_path), sandbox_provider=AsyncMock())


def _app(svc) -> FastAPI:
    from app.gateway.routers.video_gen import router

    app = FastAPI()
    app.include_router(router)
    app.state.video_gen_service = svc
    return app


def _seed_project(tmp_path, pid: str, sheets=None, user: str = _USER, **fields) -> str:
    # user 同时决定目录与 user_id（B2 测试发现：修前只改 JSON 内容、目录恒 _USER，
    # 端点按路径用户读取时种子不可见）
    d = tmp_path / "course_projects" / user
    d.mkdir(parents=True, exist_ok=True)
    data = {"id": pid, "user_id": user, "title": "多资产项目", "visual_style_preset": "watercolor", "character_sheets": sheets or [], **fields}
    (d / f"{pid}.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return pid


def _sheet_dict(name: str, sid: str = "", front: str = "", **kw) -> dict:
    return {"id": sid, "name": name, "front_url": front or f"/img/{name}_f.png", "side_url": f"/img/{name}_s.png", "back_url": f"/img/{name}_b.png", "features": "", **kw}


# ── M1: 角色表稳定 id + name 唯一 + id 寿址 ────────────────────────────────


def test_tc_m1_02_legacy_sheets_get_backfilled_id(tmp_path):
    """无 id 存量 sheet → 加载后均有 id（from_dict 回填 uuid hex[:8]，惰性落盘）。"""
    from app.gateway.video_gen.course_models import CharacterSheet, CourseProject

    proj = CourseProject.from_dict(
        {
            "id": "cp_abcdef0000m1",
            "user_id": "u1",
            "title": "t",
            "character_sheets": [
                {"name": "小明", "front_url": "/f.png", "side_url": "/s.png", "back_url": "/b.png", "features": ""},
                {"name": "小红", "front_url": "/f2.png", "side_url": "/s2.png", "back_url": "/b2.png", "features": ""},
            ],
        }
    )
    ids = [s.id for s in proj.character_sheets]
    assert all(i and len(i) == 8 for i in ids), f"存量 sheet 须回填 8 位 id，实际={ids}"
    assert len(set(ids)) == 2, "回填 id 不得重复"
    # 显式 id 保留不覆盖
    proj2 = CourseProject.from_dict({"id": "x", "user_id": "u", "title": "t", "character_sheets": [{"id": "abc12345", "name": "n", "front_url": "/f.png", "side_url": "/s.png", "back_url": "/b.png", "features": ""}]})
    assert proj2.character_sheets[0].id == "abc12345"
    assert isinstance(proj.character_sheets[0], CharacterSheet)


@pytest.mark.asyncio
async def test_tc_m1_01_duplicate_name_409(tmp_path, monkeypatch):
    """同项目重名（strip + 大小写不敏感归一化）→ HTTP 409「角色名已存在」。"""
    from app.gateway.video_gen.character_sheet_generator import CharacterSheet

    svc = _make_service(tmp_path)
    pid = _seed_project(tmp_path, "cp_abcdef0000m1", sheets=[_sheet_dict("小明", sid="aaaa1111")])
    app = _app(svc)

    def _fake_gen(**kw):
        # 调用方按 kwargs 传 description/name/features/image_gen/...
        name = kw.get("name") or ""
        return CharacterSheet(name=name, front_url="/new_f.png", side_url="/new_s.png", back_url="/new_b.png", features="")

    monkeypatch.setattr("app.gateway.video_gen.character_sheet_generator.generate_character_sheet", AsyncMock(side_effect=lambda **kw: _fake_gen(**kw)))

    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # strip + 大小写归一化命中
        resp = await client.post(f"/api/video/projects/{pid}/character-sheets/generate", json={"name": "  小明  ", "description": "d", "features": ""})
        assert resp.status_code == 409, f"重名（strip）应 409，实际={resp.status_code} {resp.text}"
        assert "角色名已存在" in resp.json().get("detail", "")

        # 英文大小写变体同样命中
        _seed_project(tmp_path, "cp_abcdef0000m2", sheets=[_sheet_dict("Anchor", sid="bbbb2222")])
        resp = await client.post("/api/video/projects/cp_abcdef0000m2/character-sheets/generate", json={"name": "ANCHOR", "description": "d", "features": ""})
        assert resp.status_code == 409, f"重名（大小写）应 409，实际={resp.status_code}"

        # 不重名 → 200
        resp = await client.post(f"/api/video/projects/{pid}/character-sheets/generate", json={"name": "新角色", "description": "d", "features": ""})
        assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_tc_m1_03_id_addressing_order_independent(tmp_path, monkeypatch):
    """删除/替换/排序按 id，与数组顺序无关；serve 亦按 id。"""
    from app.gateway.video_gen.character_sheet_generator import CharacterSheet

    svc = _make_service(tmp_path)
    pid = _seed_project(
        tmp_path,
        "cp_abcdef0000m1",
        sheets=[_sheet_dict("小明", sid="aaaa1111"), _sheet_dict("小红", sid="bbbb2222"), _sheet_dict("老王", sid="cccc3333")],
    )
    app = _app(svc)

    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 删除按 id（删中间项，断言其余完好——与下标无关）
        resp = await client.delete(f"/api/video/projects/{pid}/character-sheets/bbbb2222")
        assert resp.status_code == 200, resp.text
        resp = await client.get(f"/api/video/projects/{pid}")
        names = [s["name"] for s in resp.json()["character_sheets"]]
        assert names == ["小明", "老王"], f"按 id 删除后应剩 [小明, 老王]，实际={names}"

        # 未知 id → 404
        resp = await client.delete(f"/api/video/projects/{pid}/character-sheets/zzzz9999")
        assert resp.status_code == 404

        # 替换按 id（generate replace_id）：替换「小明」→ 名字可复用自身名，位置不变
        def _fake_gen(**kw):
            name = kw.get("name") or ""
            return CharacterSheet(name=name, front_url="/rep_f.png", side_url="/rep_s.png", back_url="/rep_b.png", features="")

        monkeypatch.setattr("app.gateway.video_gen.character_sheet_generator.generate_character_sheet", AsyncMock(side_effect=lambda **kw: _fake_gen(**kw)))
        resp = await client.post(f"/api/video/projects/{pid}/character-sheets/generate", json={"name": "小明", "description": "d", "features": "", "replace_id": "aaaa1111"})
        assert resp.status_code == 200, resp.text
        sheets = resp.json().get("sheets") or (await client.get(f"/api/video/projects/{pid}")).json()["character_sheets"]
        assert [s["name"] for s in sheets] == ["小明", "老王"], "替换应原位，不追加"
        assert all(s.get("id") for s in sheets), "新/存 sheets 均有 id"
        replaced = sheets[0]
        assert replaced["front_url"] == "/rep_f.png"

        # 排序 body 传 id 数组
        resp = await client.post(
            f"/api/video/projects/{pid}/character-sheets/reorder",
            json={"ids": [sheets[1]["id"], sheets[0]["id"]]},
        )
        assert resp.status_code == 200, resp.text
        reordered = resp.json()["sheets"]
        assert [s["name"] for s in reordered] == ["老王", "小明"]

        # serve 按 id（文件存在 → 200；未知 id → 404）
        # serve 按 sheet.front_url 解析（带 traversal 守卫）——种子须指向真实存储文件
        f = tmp_path / "character_sheets" / pid / "front.png"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"\x89PNG front")
        proj_file = tmp_path / "course_projects" / _USER / f"{pid}.json"
        data = json.loads(proj_file.read_text(encoding="utf-8"))
        for _s in data["character_sheets"]:
            if _s["name"] == "小明":
                _s["front_url"] = str(f)
        proj_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        resp = await client.get(f"/api/video/projects/{pid}/character-sheets/{reordered[1]['id']}/front")
        assert resp.status_code == 200, resp.text
        assert resp.content == b"\x89PNG front"
        resp = await client.get(f"/api/video/projects/{pid}/character-sheets/unknown0/front")
        assert resp.status_code == 404


# ── M6: 道具资产管理（决策 12/15/16/18）──────────────────────────────────


# SEC-M7（批次 2 项 2.6）：上传端点按魔数校验——fixture 须为真 PNG 魔数
_PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32


def test_tc_m6_04_legacy_project_without_prop_assets(tmp_path):
    """无 prop_assets 的项目 JSON → 加载不崩（post_init 兜底 []）。"""
    from app.gateway.video_gen.course_models import CourseProject

    proj = CourseProject.from_dict({"id": "cp_abcdef0000m6", "user_id": "u1", "title": "t"})
    assert proj.prop_assets == []


@pytest.mark.asyncio
async def test_tc_m6_01_upload_serve_delete_full_chain(tmp_path):
    """上传/删除/serve 全链路 + 落盘目录拍死 props/{pid}/{prop_id}/。"""
    svc = _make_service(tmp_path)
    pid = _seed_project(tmp_path, "cp_abcdef0000m6")
    app = _app(svc)

    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            f"/api/video/projects/{pid}/props/upload",
            data={"name": "演示手机", "description": "旗舰机演示"},
            files=[
                ("main_image", ("main.png", _PNG, "image/png")),
                ("extra_images", ("e0.png", _PNG + b"0", "image/png")),
            ],
        )
        assert resp.status_code == 200, resp.text
        prop = resp.json()
        assert prop["name"] == "演示手机"
        assert prop["id"] and len(prop["id"]) == 8
        assert prop["main_image"].endswith(".png")
        assert len(prop["extra_images"]) == 1
        # 目录拍死：props/{pid}/{prop_id}/（无 user 维度）
        assert f"props/{pid}/{prop['id']}/" in prop["main_image"].replace("\\", "/")
        prop_dir = tmp_path / "props" / pid / prop["id"]
        assert (prop_dir / "main.png").exists()
        assert (prop_dir / "extra0.png").exists()

        # serve：main 200 + extra0 200 + 未知 tag 404
        resp = await client.get(f"/api/video/projects/{pid}/props/{prop['id']}/main")
        assert resp.status_code == 200 and resp.content == _PNG
        resp = await client.get(f"/api/video/projects/{pid}/props/{prop['id']}/extra0")
        assert resp.status_code == 200
        resp = await client.get(f"/api/video/projects/{pid}/props/{prop['id']}/extra9")
        assert resp.status_code == 404

        # 重名 → 409（同 M1 归一化）
        resp = await client.post(
            f"/api/video/projects/{pid}/props/upload",
            data={"name": " 演示手机 ", "description": ""},
            files=[("main_image", ("m.png", _PNG, "image/png"))],
        )
        assert resp.status_code == 409, f"道具重名应 409，实际={resp.status_code}"

        # 删除 → 字段清 + 磁盘目录清
        resp = await client.delete(f"/api/video/projects/{pid}/props/{prop['id']}")
        assert resp.status_code == 200, resp.text
        resp = await client.get(f"/api/video/projects/{pid}")
        assert resp.json()["prop_assets"] == []
        assert not prop_dir.exists(), "删除须同步清理磁盘文件（新资产纪律）"

        # 未知 id → 404
        resp = await client.delete(f"/api/video/projects/{pid}/props/zzzz9999")
        assert resp.status_code == 404


@pytest.mark.asyncio
async def test_tc_m6_02_extra_images_cap_422(tmp_path):
    """补充角度第 3 张 → 422（决策 16：主图 1 + 补角度 0~2）。"""
    svc = _make_service(tmp_path)
    pid = _seed_project(tmp_path, "cp_abcdef0000m6")
    app = _app(svc)

    from httpx import ASGITransport, AsyncClient

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post(
            f"/api/video/projects/{pid}/props/upload",
            data={"name": "p", "description": ""},
            files=[
                ("main_image", ("m.png", _PNG, "image/png")),
                ("extra_images", ("a.png", _PNG, "image/png")),
                ("extra_images", ("b.png", _PNG, "image/png")),
                ("extra_images", ("c.png", _PNG, "image/png")),
            ],
        )
        assert resp.status_code == 422, f"extra 第 3 张应 422，实际={resp.status_code}"

        # 2 张补角度恰好合法
        resp = await client.post(
            f"/api/video/projects/{pid}/props/upload",
            data={"name": "p2", "description": ""},
            files=[
                ("main_image", ("m.png", _PNG, "image/png")),
                ("extra_images", ("a.png", _PNG, "image/png")),
                ("extra_images", ("b.png", _PNG, "image/png")),
            ],
        )
        assert resp.status_code == 200, resp.text
        assert len(resp.json()["extra_images"]) == 2


# ── M2: 逐镜出场声明（对象数组 + 名册归一化 + None/[] 语义铁律）────────────


_ROSTER = {
    "characters": [
        {"name": "小明", "features": "短发男孩，蓝衣"},
        {"name": "小红", "features": "长发女孩，红裙"},
    ],
    "props": [
        {"name": "演示手机", "description": "黑色直板旗舰机"},
    ],
}


def test_tc_m2_01_hallucinated_name_dropped_with_sidecar(tmp_path):
    """LLM 输出名册外名 → 丢弃该条目 + sidecar declared_invalid（不阻断生成）。"""
    from app.gateway.video_gen.orchestration.storyboard_planner import _apply_visible_fields
    from app.gateway.video_gen.storyboard import StoryboardFrame

    frames = [StoryboardFrame(index=0, narration="n0"), StoryboardFrame(index=1, narration="n1")]
    parsed = [
        {"image_prompt": "p0", "visible_characters": [{"name": "小明"}, {"name": "不存在的人"}], "visible_props": []},
        {"image_prompt": "p1", "visible_characters": [{"name": "  小明  "}], "visible_props": [{"name": "幻觉产品"}]},
    ]
    _apply_visible_fields(frames, parsed, _ROSTER, task_dir=tmp_path)

    # 名册外名被丢弃；大小写/空白归一化可命中
    assert frames[0].visible_characters == [{"name": "小明"}]
    assert frames[1].visible_characters == [{"name": "小明"}], "strip+大小写归一化应命中名册"
    assert frames[1].visible_props == [], "幻觉道具名丢弃后为空数组（[] 语义保留）"

    # sidecar 记录 declared_invalid（帧 0：不存在的人；帧 1：幻觉产品——「  小明  」归一化后命中名册不算幻觉）
    sidecar = json.loads((tmp_path / "sidecar" / "ref_injection.json").read_text(encoding="utf-8"))
    reasons = {r["frame_index"]: [s.get("reason") for s in r.get("skipped", [])] for r in sidecar}
    assert "declared_invalid" in reasons.get(0, []), f"帧 0 应记 declared_invalid，实际={sidecar}"
    assert "declared_invalid" in reasons.get(1, []), f"帧 1 幻觉道具应记 declared_invalid，实际={sidecar}"


def test_tc_m2_02_missing_fields_none_not_empty_list(tmp_path):
    """新 planner 输出缺 visible_* / 值非列表 / 条目非对象 → None（不是 []）。"""
    from app.gateway.video_gen.orchestration.storyboard_planner import _apply_visible_fields
    from app.gateway.video_gen.storyboard import StoryboardFrame

    frames = [StoryboardFrame(index=i, narration=f"n{i}") for i in range(4)]
    parsed = [
        {"image_prompt": "p0"},  # 完全缺字段
        {"image_prompt": "p1", "visible_characters": None, "visible_props": None},  # 显式 null
        {"image_prompt": "p2", "visible_characters": "小明", "visible_props": 42},  # 非列表
        {"image_prompt": "p3", "visible_characters": ["小明"], "visible_props": []},  # 列表内非对象条目
    ]
    _apply_visible_fields(frames, parsed, _ROSTER, task_dir=tmp_path)
    for i, f in enumerate(frames):
        assert f.visible_characters is None, f"帧 {i} 缺字段/不合作 → None（不是 []）"
        if i == 3:
            # 帧 3 显式传了 []（列表内无非对象条目可留）→ [] 语义保留（铁律：[] = 明确空）
            assert f.visible_props == [], f"帧 3 显式 [] 必须保留为 []，实际={f.visible_props}"
        else:
            assert f.visible_props is None, f"帧 {i}：缺字段/显式 null/非列表 → None（不是 []），实际={f.visible_props}"


def test_tc_m2_04_empty_declaration_means_explicit_empty(tmp_path):
    """[] = 本镜明确无人物/无道具（与 None=回退旧行为严格区分）。"""
    from app.gateway.video_gen.orchestration.storyboard_planner import _apply_visible_fields
    from app.gateway.video_gen.storyboard import StoryboardFrame

    frames = [StoryboardFrame(index=0, narration="n0"), StoryboardFrame(index=1, narration="n1")]
    parsed = [
        {"image_prompt": "p0", "visible_characters": [], "visible_props": []},
        {"image_prompt": "p1", "visible_characters": [{"name": "小明", "view": "side"}], "visible_props": [{"name": "演示手机", "view": 1}]},
    ]
    _apply_visible_fields(frames, parsed, _ROSTER, task_dir=tmp_path)
    assert frames[0].visible_characters == [] and frames[0].visible_props == []
    # view 合法值保留；非法 view 回退缺省（键删除）
    assert frames[1].visible_characters == [{"name": "小明", "view": "side"}]
    assert frames[1].visible_props == [{"name": "演示手机", "view": 1}]

    frames2 = [StoryboardFrame(index=0, narration="n")]
    _apply_visible_fields(
        frames2,
        [{"image_prompt": "p", "visible_characters": [{"name": "小明", "view": "above"}], "visible_props": [{"name": "演示手机", "view": 9}]}],
        _ROSTER,
        task_dir=tmp_path,
    )
    assert frames2[0].visible_characters == [{"name": "小明"}], "非法 view → 回退缺省（不带 view 键）"
    assert frames2[0].visible_props == [{"name": "演示手机"}], "非法道具 view → 回退缺省 0"


@pytest.mark.asyncio
async def test_tc_m2_01_enhanced_end_to_end_with_fake_llm(tmp_path, monkeypatch):
    """_generate_course_prompts_enhanced 端到端（fake LLM JSON）：名册进 system、声明写回帧。"""
    from types import SimpleNamespace

    from app.gateway.video_gen.orchestration import storyboard_planner as sp
    from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame

    class _RosterLLM:
        """fake llm：_invoke_llm_bounded 走 ainvoke——断言名册/契约进 system。"""

        def __init__(self, roster_expected: bool):
            self._roster_expected = roster_expected

        async def ainvoke(self, messages):
            system = messages[0]["content"]
            if self._roster_expected:
                assert "小明" in system and "演示手机" in system, "名册必须进 system prompt"
                assert "visible_characters" in system, "输出契约必须声明 visible_* 字段"
            else:
                assert "visible_characters" not in system, "无名册时不得追加出场声明契约"
            return SimpleNamespace(
                content=('{"frames": [{"image_prompt": "小明手持演示手机", "shot_size": "medium", "camera_move": "static", "visual_source": "ai_image", "visible_characters": [{"name": "小明"}], "visible_props": [{"name": "演示手机"}]}]}')
            )

    fake_llm = _RosterLLM(roster_expected=True)

    sb = Storyboard(
        title="t",
        config=StoryboardConfig(video_type="ai_image", width=1080, height=1920),
        frames=[StoryboardFrame(index=0, narration="n")],
    )
    prompts, sources = await sp._generate_course_prompts_enhanced(fake_llm, sb, "", entity_roster=_ROSTER, task_dir=tmp_path)
    assert prompts and prompts[0] == "小明手持演示手机"
    assert sb.frames[0].visible_characters == [{"name": "小明"}]
    assert sb.frames[0].visible_props == [{"name": "演示手机"}]

    # 无名册（无项目任务）：输出契约零变化（回归自查：无项目链路不变），visible_* 保持 None
    sb2 = Storyboard(
        title="t",
        config=StoryboardConfig(video_type="ai_image", width=1080, height=1920),
        frames=[StoryboardFrame(index=0, narration="n")],
    )
    await sp._generate_course_prompts_enhanced(_RosterLLM(roster_expected=False), sb2, "")
    assert sb2.frames[0].visible_characters is None and sb2.frames[0].visible_props is None


# ── M3: 按帧子集注入 + 过渡开关（roster 通道 + 帧字段选择）──────────────────
# 含 TC-M2-03（存量 None 语义）/ TC-M2-04（[] 空镜注入侧）/ TC-M6-03（道具缺失跳过）
# 2026-08-27 合成图方案（X10）：断言更新为 composite 语义——roster 桩带 composite 键，
# 角色 view 提示退役（决策 3），每实体恒 1 张三视图合成图。


_CHAR_ROSTER = {
    "小明": {"front_url": "/sheets/xm_f.png", "side_url": "/sheets/xm_s.png", "back_url": "/sheets/xm_b.png", "composite": "/sheets/xm_c.png"},
    "小红": {"front_url": "/sheets/xh_f.png", "side_url": "/sheets/xh_s.png", "back_url": "/sheets/xh_b.png", "composite": "/sheets/xh_c.png"},
    "老王": {"front_url": "/sheets/lw_f.png", "side_url": "/sheets/lw_s.png", "back_url": "/sheets/lw_b.png", "composite": "/sheets/lw_c.png"},
    "阿七": {"front_url": "/sheets/q7_f.png", "side_url": "/sheets/q7_s.png", "back_url": "/sheets/q7_b.png", "composite": "/sheets/q7_c.png"},
}
_PROP_ROSTER = {
    "演示手机": {"main_image": "/props/p1/main.png", "extra_images": ["/props/p1/extra0.png", "/props/p1/extra1.png"]},
    "白板": {"main_image": "/props/p2/main.png", "extra_images": []},
}


def _frame_with(vc=None, vp=None):
    from app.gateway.video_gen.storyboard import StoryboardFrame

    return StoryboardFrame(index=0, narration="n", visible_characters=vc, visible_props=vp)


def test_tc_m3_01_subset_injection_two_declared():
    """声明 2 人 → refs 恰 2 张 composite（每实体恒 1 张合成图，2026-08-27 X10 语义）。"""
    from app.gateway.video_gen.visual_sources.ai_image import _frame_character_ref_paths

    f = _frame_with(vc=[{"name": "小红"}, {"name": "老王"}])
    refs = _frame_character_ref_paths(f, _CHAR_ROSTER)
    assert refs == ["/sheets/xh_c.png", "/sheets/lw_c.png"], f"声明 2 人各 1 张合成图，实际={refs}"


def test_tc_m3_02_view_hint_retired_for_characters():
    """角色 view 提示退役（合成图方案决策 3）：view=side/非法 → 均 composite（恒含全视角）。"""
    from app.gateway.video_gen.visual_sources.ai_image import _frame_character_ref_paths

    f = _frame_with(vc=[{"name": "小明", "view": "side"}, {"name": "小红", "view": "above"}])
    refs = _frame_character_ref_paths(f, _CHAR_ROSTER)
    assert refs == ["/sheets/xm_c.png", "/sheets/xh_c.png"], f"view 提示对角色不再生效，实际={refs}"


def test_tc_m2_03_legacy_none_semantics_first_protagonist_full():
    """存量 storyboard 无字段（None）→ 首主角 1 张 composite（与旧三视图全传信息等价）。"""
    from app.gateway.video_gen.visual_sources.ai_image import _frame_character_ref_paths

    f = _frame_with()  # visible_characters=None（存量帧）
    refs = _frame_character_ref_paths(f, _CHAR_ROSTER)
    assert refs == ["/sheets/xm_c.png"], "None → 首主角（roster 首键）1 张合成图"


def test_tc_m2_04_empty_declaration_no_refs():
    """[] = 本镜明确无人物 → 无角色参考。"""
    from app.gateway.video_gen.visual_sources.ai_image import _frame_character_ref_paths

    f = _frame_with(vc=[])
    assert _frame_character_ref_paths(f, _CHAR_ROSTER) == []


@pytest.mark.asyncio
async def test_tc_m3_03_character_truncation_over_three():
    """声明 4 人 → 3 张 composite + sidecar（决策 17：每镜角色 ≤3；第 4 人纯文本生成属预期）。"""
    import base64

    from app.gateway.video_gen.providers.base import ImageRef
    from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig
    from app.gateway.video_gen.visual_sources.ai_image import AIImageVisualSource

    captured: list[dict] = []

    class _Cap:
        async def generate_image(self, prompt: str, **kw):
            captured.append(kw)
            return ImageRef(url="data:image/png;base64," + base64.b64encode(b"y" * 700).decode(), width=10, height=10, source="t")

    sb = Storyboard(
        title="t",
        config=StoryboardConfig(video_type="ai_image", width=1080, height=1920),
        frames=[_frame_with(vc=[{"name": n} for n in ("小明", "小红", "老王", "阿七")])],
    )
    src = AIImageVisualSource(_Cap())
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as td:
        await src.render(sb, task_dir=Path(td), character_roster=_CHAR_ROSTER)
        refs = captured[0].get("reference_image_paths")
        assert refs == ["/sheets/xm_c.png", "/sheets/xh_c.png", "/sheets/lw_c.png"], f"4 人声明 → 3 张 composite，实际={refs}"
        sidecar = json.loads((Path(td) / "sidecar" / "ref_injection.json").read_text(encoding="utf-8"))
        rec = [r for r in sidecar if r.get("frame_index") == 0][0]
        assert rec["truncated"], f"截断须记 sidecar，实际={rec}"
        assert any("阿七" in str(t) for t in rec["truncated"]), "被截断实体（第 4 人）须留痕"


def test_tc_m3_04_composite_wins_and_fallback_without_it(monkeypatch):
    """合成图在场 → 恒 composite（实验开关不再参与角色选择）；无 composite →
    回退视图逻辑（三视图全传，实验开关仅此兜底路径生效——X9 口径）。"""
    from app.gateway.video_gen.visual_sources.ai_image import _frame_character_ref_paths

    monkeypatch.setenv("VIDEO_GEN_SINGLE_VIEW_PER_ENTITY", "0")
    f = _frame_with(vc=[{"name": "小红"}, {"name": "老王", "view": "back"}])
    assert _frame_character_ref_paths(f, _CHAR_ROSTER) == ["/sheets/xh_c.png", "/sheets/lw_c.png"], "有 composite → 开关不参与"

    roster_no_c = {name: {k: v for k, v in entry.items() if k != "composite"} for name, entry in _CHAR_ROSTER.items()}
    refs = _frame_character_ref_paths(f, roster_no_c)
    assert refs == [
        "/sheets/xh_f.png",
        "/sheets/xh_s.png",
        "/sheets/xh_b.png",
        "/sheets/lw_f.png",
        "/sheets/lw_s.png",
        "/sheets/lw_b.png",
    ], f"无 composite → 回退三视图全传（view 提示失效），实际={refs}"

    # None 帧语义：有 composite → 首主角 1 张
    f_none = _frame_with()
    assert _frame_character_ref_paths(f_none, _CHAR_ROSTER) == ["/sheets/xm_c.png"]


def test_tc_m3_05_prop_view_selection():
    """道具视图：默认 main_image；view=1 → extra_images[0]。"""
    from app.gateway.video_gen.visual_sources.ai_image import _frame_prop_ref_paths

    # 3 条声明 → 每镜道具 ≤2 截断（决策 17）：保前 2 条（main + extra0），「白板」截掉
    f = _frame_with(vp=[{"name": "演示手机"}, {"name": "演示手机", "view": 1}, {"name": "白板"}])
    refs = _frame_prop_ref_paths(f, _PROP_ROSTER)
    assert refs == ["/props/p1/main.png", "/props/p1/extra0.png"]

    # >2 截断
    f4 = _frame_with(vp=[{"name": "演示手机"}, {"name": "白板"}, {"name": "演示手机", "view": 2}])
    assert _frame_prop_ref_paths(f4, _PROP_ROSTER) == ["/props/p1/main.png", "/props/p2/main.png"]

    # None → 无道具参考（旧行为）；[] → 无道具
    assert _frame_prop_ref_paths(_frame_with(), _PROP_ROSTER) == []
    assert _frame_prop_ref_paths(_frame_with(vp=[]), _PROP_ROSTER) == []


@pytest.mark.asyncio
async def test_tc_m6_03_prop_file_missing_skips_with_sidecar():
    """道具图文件不存在 → 跳过 + sidecar，帧成功（同锚图纪律，不炸整帧）。"""
    import base64
    import tempfile
    from pathlib import Path

    from app.gateway.video_gen.providers.base import ImageRef
    from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig
    from app.gateway.video_gen.visual_sources.ai_image import AIImageVisualSource

    class _Cap:
        async def generate_image(self, prompt: str, **kw):
            return ImageRef(url="data:image/png;base64," + base64.b64encode(b"z" * 700).decode(), width=10, height=10, source="t")

    sb = Storyboard(
        title="t",
        config=StoryboardConfig(video_type="ai_image", width=1080, height=1920),
        frames=[_frame_with(vp=[{"name": "演示手机"}])],  # /props/p1/*.png 均不存在
    )
    src = AIImageVisualSource(_Cap())
    with tempfile.TemporaryDirectory() as td:
        await src.render(sb, task_dir=Path(td), character_roster={}, prop_roster=_PROP_ROSTER)
        assert sb.frames[0].visual_type == "image", "帧生成成功（跳过≠失败）"
        refs_side = json.loads((Path(td) / "sidecar" / "ref_injection.json").read_text(encoding="utf-8"))
        rec = [r for r in refs_side if r.get("frame_index") == 0][0]
        assert any(s.get("reason") == "file_missing" and "演示手机" in str(s.get("entity", "")) for s in rec["skipped"]), f"缺失道具须记 sidecar，实际={rec}"


@pytest.mark.asyncio
async def test_tc_m3_roster_mode_disables_task_level_char_refs():
    """过渡关系：roster 生效 → 任务级 reference_image_paths 的角色用途停用（帧级选择接管）。
    每声明实体恒 1 张 composite（X10 语义）。"""
    import base64
    import tempfile

    from app.gateway.video_gen.providers.base import ImageRef
    from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig
    from app.gateway.video_gen.visual_sources.ai_image import AIImageVisualSource

    captured: list[dict] = []

    class _Cap:
        async def generate_image(self, prompt: str, **kw):
            captured.append(kw)
            return ImageRef(url="data:image/png;base64," + base64.b64encode(b"w" * 700).decode(), width=10, height=10, source="t")

    sb = Storyboard(
        title="t",
        config=StoryboardConfig(video_type="ai_image", width=1080, height=1920),
        frames=[_frame_with(vc=[{"name": "小红"}])],
    )
    src = AIImageVisualSource(_Cap())
    with tempfile.TemporaryDirectory() as td:
        await src.render(
            sb,
            task_dir=Path(td),
            reference_image_paths=["/sheets/task_level_f.png", "/sheets/task_level_b.png"],  # 旧整表语义
            character_roster=_CHAR_ROSTER,
        )
        refs = captured[0].get("reference_image_paths")
        assert refs == ["/sheets/xh_c.png"], f"roster 模式下任务级角色 refs 须停用（帧级 1 张 composite），实际={refs}"


# ── 复核阻断项修复的回归测试（B1 id 稳定 / B2 重推活性）────────────────────


def test_b1_backfilled_ids_stable_across_reads(tmp_path):
    """B1：存量无 id 项目经 store 读取 → 首读固化 id，跨 get/list 稳定（serve/delete/replace 可靠寻址）。"""
    from app.gateway.video_gen.course_project_store import CourseProjectStore

    pid = _seed_project(
        tmp_path,
        "cp_abcdef0000b1",
        sheets=[_sheet_dict("小明", sid="")],  # 无 id（存量形态）
        prop_assets=[{"id": "", "name": "手机", "main_image": "/m.png", "extra_images": [], "description": ""}],
    )
    st = CourseProjectStore(base_dir=tmp_path)
    a = st.get(pid, _USER)
    b = st.get(pid, _USER)
    c = st.list(_USER)[0]
    assert a.character_sheets[0].id == b.character_sheets[0].id == c.character_sheets[0].id, "sheet id 跨读稳定"
    assert a.prop_assets[0].id == b.prop_assets[0].id == c.prop_assets[0].id, "prop id 跨读稳定"
    # 固化后盘面 JSON 带 id（后续读不再回填）
    on_disk = json.loads((tmp_path / "course_projects" / _USER / f"{pid}.json").read_text(encoding="utf-8"))
    assert on_disk["character_sheets"][0]["id"] == a.character_sheets[0].id
    assert on_disk["prop_assets"][0]["id"] == a.prop_assets[0].id


@pytest.mark.asyncio
async def test_b2_rederive_fires_for_pending_narration_edit(tmp_path, monkeypatch):
    """B2：pending_regen_frames 帧重生 → 出场声明重推并落盘；未编辑帧不重推（保留原声明）。"""
    from types import SimpleNamespace
    from unittest.mock import patch

    from app.gateway._shared.task_framework.schemas import DRAFT
    from app.gateway.routers.video_gen import regenerate_frame_prompt
    from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame

    svc = _make_service(tmp_path)
    pid = _seed_project(tmp_path, "cp_abcdef0000b2", user="u1", sheets=[_sheet_dict("小明", sid="aaaa1111")])
    task = SimpleNamespace(status=DRAFT, task_type="ai_image", payload=json.dumps({"course_project_id": pid}))
    svc.get_task = AsyncMock(return_value=task)

    task_dir = tmp_path / "u1" / "tasks" / "vg_b2"
    task_dir.mkdir(parents=True)
    sb = Storyboard(
        title="t",
        config=StoryboardConfig(video_type="ai_image", width=1080, height=1920),
        frames=[StoryboardFrame(index=0, narration="改过的旁白"), StoryboardFrame(index=1, narration="未改旁白", visible_characters=[{"name": "小明"}])],
    )
    raw = sb.to_dict()
    raw["pending_regen_frames"] = [0]  # 帧 0 旁白被编辑（B2：键在盘面 JSON，不在 Storyboard dataclass）
    (task_dir / "storyboard.json").write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    async def _fake_rederive(llm, *, title, narration, entity_roster):
        assert "小明" in entity_roster.get("characters", [{}])[0].get("name", ""), "名册必须到位"
        return [{"name": "小明"}], []

    with (
        patch("app.gateway.routers.video_gen.get_effective_user_id", return_value="u1"),
        patch("deerflow.models.create_chat_model", return_value=MagicMock()),
        patch("app.gateway.video_gen.orchestration.storyboard_planner._generate_image_prompts", new=AsyncMock(return_value=["regenerated"])),
        patch("app.gateway.video_gen.orchestration.storyboard_planner.rederive_visible_declarations", new=AsyncMock(side_effect=_fake_rederive)),
    ):
        await regenerate_frame_prompt(_request_with_svc(svc), "vg_b2", 0)

    on_disk = json.loads((task_dir / "storyboard.json").read_text(encoding="utf-8"))
    assert on_disk["frames"][0]["visible_characters"] == [{"name": "小明"}], "旁白已编辑帧须重推并落盘"
    assert on_disk["frames"][0]["visible_props"] == []
    # 未编辑帧不触碰（保留原声明）
    assert on_disk["frames"][1]["visible_characters"] == [{"name": "小明"}]

    with (
        patch("app.gateway.routers.video_gen.get_effective_user_id", return_value="u1"),
        patch("deerflow.models.create_chat_model", return_value=MagicMock()),
        patch("app.gateway.video_gen.orchestration.storyboard_planner._generate_image_prompts", new=AsyncMock(return_value=["regenerated"])),
    ):
        await regenerate_frame_prompt(_request_with_svc(svc), "vg_b2", 1)
    on_disk2 = json.loads((task_dir / "storyboard.json").read_text(encoding="utf-8"))
    assert on_disk2["frames"][1].get("visible_props") is None, "未编辑帧不重推（无 visible_props 键）"


def _request_with_svc(svc):
    req = MagicMock()
    req.app.state.video_gen_service = svc
    return req


# ── 2026-08-28 AC2 实证修复：名册在场时「外观以参考图为准、禁止虚构」契约 ────────
# 事故链：锚定卡 LLM 凭空写「小林戴圆框眼镜/老王橙色围裙」→ 广播 30 帧 → 文字与
# 合成图参考打架，部分帧文字胜出（f17 性别 / f22 眼镜失真）。

_PLANNING_ROSTER = {
    "characters": [{"name": "小林", "features": ""}, {"name": "老王", "features": ""}],
    "props": [{"name": "保温杯", "description": ""}],
}


@pytest.mark.asyncio
async def test_anchor_roster_clause_injected_only_with_roster():
    """锚定卡：有名册 → system 含「锁定外观/禁止虚构」条款与名单；无名册 → 零该段（契约不变）。"""
    from types import SimpleNamespace

    from app.gateway.video_gen.orchestration import storyboard_planner as sp

    captured: list[str] = []

    class _LLM:
        async def ainvoke(self, messages):  # noqa: ANN001
            captured.append(messages[0]["content"])
            return SimpleNamespace(content='{"anchor_card": "锚", "style_string": "s", "graphic_layout_card": "", "scene_groups": []}')

    await sp._generate_global_anchor(_LLM(), title="t", narrations=["n"], style_prefix="", entity_roster=_PLANNING_ROSTER)
    assert "项目参考图锁定外观" in captured[0] and "小林" in captured[0] and "保温杯" in captured[0]
    assert "禁止虚构" in captured[0]

    await sp._generate_global_anchor(_LLM(), title="t", narrations=["n"], style_prefix="")
    assert "项目参考图锁定外观" not in captured[1], "无名册 = 锚定契约逐字节不变"


@pytest.mark.asyncio
async def test_enhanced_roster_no_appearance_invention_rule():
    """逐帧扩写：有名册 → system 含「外观以参考图为准」硬规则；无名册 → 无该句。"""
    from types import SimpleNamespace

    from app.gateway.video_gen.orchestration import storyboard_planner as sp
    from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame

    captured: list[str] = []

    class _LLM:
        async def ainvoke(self, messages):  # noqa: ANN001
            captured.append(messages[0]["content"])
            return SimpleNamespace(content='{"frames": [{"image_prompt": "p", "shot_size": "medium", "camera_move": "static", "visual_source": "ai_image"}]}')

    def _sb():
        return Storyboard(
            title="t",
            config=StoryboardConfig(video_type="ai_image", width=1080, height=1920),
            frames=[StoryboardFrame(index=0, narration="n")],
        )

    await sp._generate_course_prompts_enhanced(_LLM(), _sb(), "", entity_roster=_PLANNING_ROSTER)
    assert "外观以参考图为准" in captured[0] and "禁止" in captured[0]

    await sp._generate_course_prompts_enhanced(_LLM(), _sb(), "")
    assert "外观以参考图为准" not in captured[1], "无名册 = 逐帧契约零变化"
