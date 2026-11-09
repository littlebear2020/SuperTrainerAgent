"""二期 M5：视频侧帧级参考子集 + 锚图兜底 + 全量留痕 + 任务详情标签数据源。

TC-M5-01 视频侧隔离：角色帧 → 视频侧 refs 无锚图（优先级：角色/道具 > 组锚 > 预览 > 锚图兜底）。
TC-M5-02 兜底锚图：无角色/组锚/预览帧 → refs=[锚图] + style_anchor 方言文案。
TC-M5-03 预览优先：有预览继承条件帧 → 预览图优先于锚图。
TC-M5-04 留痕全量：sidecar 含 declared/injected/truncated/skipped（C10 schema 全字段）。

附带：_assign_roles 实体语义对齐（多实体参考全 reference_image，首尾帧映射保留给
ViMax 双帧）；任务详情响应并入 ref_injection.json（分镜级标签数据源，不新增端点）。
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from app.gateway.video_gen.storyboard import Storyboard, StoryboardConfig, StoryboardFrame
from app.gateway.video_gen.visual_sources import ai_video as mod
from app.gateway.video_gen.visual_sources.ai_video import AIVideoVisualSource

_IR = {"subject": "一位讲师", "action": "讲解产品", "environment": "教室", "style": "写实"}


def _frame(**kw) -> StoryboardFrame:
    kw.setdefault("index", 0)
    kw.setdefault("narration", "旁白")
    kw.setdefault("duration", 5.0)
    kw.setdefault("image_prompt", "p")
    kw.setdefault("visual_ir", dict(_IR))
    return StoryboardFrame(**kw)


def _sb(frames: list[StoryboardFrame]) -> Storyboard:
    cfg = StoryboardConfig(video_type="ai_video", width=720, height=1280)
    return Storyboard(title="t", config=cfg, frames=frames)


@pytest.fixture()
def _no_localize(monkeypatch):
    async def fake_localize(url, frame, task_dir):  # noqa: ANN001
        return None

    monkeypatch.setattr(mod, "_localize_video", fake_localize)


@pytest.fixture()
def _cap():
    """捕获 provider 收到的 prompt/image_urls/role（记录型 fake，非裸 mock 断言）。"""

    class _Capture:
        def __init__(self) -> None:
            self.calls: list[dict] = []

        async def gen(self, prompt, *, duration=5.0, image_urls=None, role=None, **kw):  # noqa: ANN001,ANN003
            self.calls.append({"prompt": prompt, "image_urls": image_urls, "role": role})
            return MagicMock(url="http://v/1")

    return _Capture()


def _src(capture) -> AIVideoVisualSource:
    return AIVideoVisualSource(
        MagicMock(generate_video=capture.gen, max_video_duration=15.0, supports_image_urls=True),
        concurrency=1,
        provider_name="seedance",
    )


_ROSTER = {
    "小林": {"front_url": "/chars/xiaolin_front.png", "side_url": "/chars/xiaolin_side.png", "back_url": "/chars/xiaolin_back.png"},
    "老王": {"front_url": "/chars/laowang_front.png", "side_url": "", "back_url": ""},
    "阿珍": {"front_url": "/chars/azhen_front.png", "side_url": "", "back_url": ""},
    "阿强": {"front_url": "/chars/aqiang_front.png", "side_url": "", "back_url": ""},
}


@pytest.fixture()
def _prop_roster(tmp_path):
    """道具名册：魔方两图落盘、遥控器主图缺失（file_missing 留痕路径）。"""
    d = tmp_path / "props"
    d.mkdir()
    (d / "mofang_main.png").write_bytes(b"m" * 64)
    (d / "mofang_e1.png").write_bytes(b"e" * 64)
    return {
        "魔方": {"main_image": str(d / "mofang_main.png"), "extra_images": [str(d / "mofang_e1.png")]},
        "遥控器": {"main_image": str(d / "missing_remote.png"), "extra_images": []},
    }


def _read_sidecar(tmp_path, frame_index: int) -> dict:
    data = json.loads((tmp_path / "sidecar" / "ref_injection.json").read_text(encoding="utf-8"))
    rec = [r for r in data if r.get("frame_index") == frame_index]
    assert rec, f"frame {frame_index} 须有 ref_injection 记录，实际 {data}"
    return rec[0]


# ── TC-M5-01：视频侧隔离（角色帧 → 无锚图 + 带名方言端到端）────────────────


@pytest.mark.asyncio
async def test_tc_m5_01_character_frame_excludes_anchor(tmp_path, _no_localize, _cap):
    frame = _frame(visible_characters=[{"name": "小林"}, {"name": "老王"}])
    # 生产形态（X6）：roster 条目带 composite → 视频侧恒注合成图 + 带名方言「三视图」
    roster = {
        "小林": {**_ROSTER["小林"], "composite": "/chars/xiaolin_composite.png"},
        "老王": {**_ROSTER["老王"], "composite": "/chars/laowang_composite.png"},
    }
    await _src(_cap).render(
        _sb([frame]),
        task_dir=tmp_path,
        style_anchor_image_paths=["/anchor.png"],
        character_roster=roster,
    )
    assert len(_cap.calls) == 1
    urls = _cap.calls[0]["image_urls"]
    assert urls == ["/chars/xiaolin_composite.png", "/chars/laowang_composite.png"], f"帧级子集每实体 1 张合成图（X7 同构），实际 {urls}"
    assert "/anchor.png" not in (urls or []), "角色帧不得注入锚图（视频侧隔离，M5 优先级铁律）"
    assert _cap.calls[0]["role"] == "reference", "角色参考走 r2v reference 语义"
    assert "图片1为小林的三视图，图片2为老王的三视图" in _cap.calls[0]["prompt"], "带名方言须随帧级子集下发（M4 接线；X8 合成图方案后角色条目带「三视图」措辞）"


@pytest.mark.asyncio
async def test_declared_view_selects_single_view(tmp_path, _no_localize, _cap):
    frame = _frame(visible_characters=[{"name": "小林", "view": "side"}])
    await _src(_cap).render(_sb([frame]), task_dir=tmp_path, character_roster=dict(_ROSTER))
    assert _cap.calls[0]["image_urls"] == ["/chars/xiaolin_side.png"]


@pytest.mark.asyncio
async def test_legacy_none_frame_gets_protagonist_three_views(tmp_path, _no_localize, _cap):
    """None 帧（存量/无声明）→ 首主角三视图全传（M3 语义铁律，视频侧同款）。"""
    frame = _frame()  # visible_characters 缺省 None
    await _src(_cap).render(_sb([frame]), task_dir=tmp_path, character_roster=dict(_ROSTER))
    urls = _cap.calls[0]["image_urls"]
    assert urls == ["/chars/xiaolin_front.png", "/chars/xiaolin_side.png", "/chars/xiaolin_back.png"]
    assert "图片1为小林" not in _cap.calls[0]["prompt"], "三视图非 1:1 对应 → 不带名（现状文案）"


# ── TC-M5-02：兜底锚图 ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tc_m5_02_style_anchor_fallback(tmp_path, _no_localize, _cap):
    frame = _frame(visible_characters=[], visible_props=[])  # 明确空：无角色/道具
    await _src(_cap).render(
        _sb([frame]),
        task_dir=tmp_path,
        style_anchor_image_paths=["/anchor.png"],
        character_roster=dict(_ROSTER),
    )
    assert _cap.calls[0]["image_urls"] == ["/anchor.png"], "前三者皆空 → 锚图兜底"
    assert "图片1为本系列风格参考，色调与画风保持一致，主体动作按本镜描述生成。" in _cap.calls[0]["prompt"]
    assert _cap.calls[0]["role"] == "reference", "风格锚图是 r2v 参考，不是首帧 i2v"
    rec = _read_sidecar(tmp_path, 0)
    assert rec["injected_refs"] == [{"entity": "style_anchor", "path": "/anchor.png"}]


@pytest.mark.asyncio
async def test_anchor_fallback_respects_switch(tmp_path, _no_localize, _cap, monkeypatch):
    monkeypatch.setenv("VIDEO_GEN_STYLE_ANCHOR_IMAGE", "0")
    frame = _frame(visible_characters=[], visible_props=[])
    await _src(_cap).render(
        _sb([frame]),
        task_dir=tmp_path,
        style_anchor_image_paths=["/anchor.png"],
        character_roster=dict(_ROSTER),
    )
    assert _cap.calls[0]["image_urls"] is None, "开关关 → 兜底不生效（C5 回退位复用）"


@pytest.mark.asyncio
async def test_anchor_fallback_without_roster(tmp_path, _no_localize, _cap):
    """无 roster 任务（非项目/未仲裁）也可兜底：开关开 + 三者皆空即生效。"""
    frame = _frame()
    await _src(_cap).render(_sb([frame]), task_dir=tmp_path, style_anchor_image_paths=["/anchor.png"])
    assert _cap.calls[0]["image_urls"] == ["/anchor.png"]


@pytest.mark.asyncio
async def test_no_anchor_no_refs_stays_t2v(tmp_path, _no_localize, _cap):
    """无锚图参数 → 空帧保持纯 t2v（现状行为零回归）。"""
    frame = _frame(visible_characters=[], visible_props=[])
    await _src(_cap).render(_sb([frame]), task_dir=tmp_path, character_roster=dict(_ROSTER))
    assert _cap.calls[0]["image_urls"] is None


# ── TC-M5-03：预览优先 ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tc_m5_03_preview_wins_over_anchor(tmp_path, _no_localize, _cap):
    (tmp_path / "visuals").mkdir()
    (tmp_path / "visuals" / "frame_0000.png").write_bytes(b"x" * 2048)
    frame = _frame(
        visible_characters=[],
        visible_props=[],
        local_visual_path="visuals/frame_0000.png",
        visual_type="image",
    )
    await _src(_cap).render(
        _sb([frame]),
        task_dir=tmp_path,
        style_anchor_image_paths=["/anchor.png"],
        character_roster=dict(_ROSTER),
    )
    urls = _cap.calls[0]["image_urls"]
    assert urls and urls[0].endswith("frame_0000.png"), f"预览继承优先于锚图兜底，实际 {urls}"
    assert "/anchor.png" not in urls
    assert "图片1为本镜预览图" in _cap.calls[0]["prompt"]


# ── TC-M5-04：留痕全量 ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tc_m5_04_sidecar_full_schema(tmp_path, _no_localize, _cap, _prop_roster):
    frame = _frame(
        visible_characters=[
            {"name": "小林"},
            {"name": "老王"},
            {"name": "阿珍"},
            {"name": "阿强"},  # 第 4 个 → char_cap_3 截断
        ],
        visible_props=[{"name": "魔方"}, {"name": "遥控器"}],  # 遥控器文件缺失 → skipped
    )
    await _src(_cap).render(
        _sb([frame]),
        task_dir=tmp_path,
        character_roster=dict(_ROSTER),
        prop_roster=_prop_roster,
    )
    urls = _cap.calls[0]["image_urls"]
    assert urls == [
        "/chars/xiaolin_front.png",
        "/chars/laowang_front.png",
        "/chars/azhen_front.png",
        _prop_roster["魔方"]["main_image"],
    ], f"3 角色（第 4 截断）+ 1 道具（缺失跳过），实际 {urls}"

    rec = _read_sidecar(tmp_path, 0)
    assert rec["declared_entities"] == ["小林", "老王", "阿珍", "阿强", "魔方", "遥控器"]
    injected = rec["injected_refs"]
    assert [i["entity"] for i in injected] == ["小林", "老王", "阿珍", "魔方"]
    assert injected[0]["view"] == "front", "injected_refs 含 view（C10 schema 全字段）"
    assert injected[3]["view"] == 0, "道具缺省 view=0（main_image）"
    trunc_names = [t["entity"] for t in rec["truncated"]]
    assert "character:阿强" in trunc_names, "声明超限（角色 ≤3）须截断留痕"
    skip = [s for s in rec["skipped"] if s.get("reason") == "file_missing"]
    assert skip and "prop:遥控器" in skip[0]["entity"], "道具文件缺失须 skipped 留痕"


@pytest.mark.asyncio
async def test_prop_view_index_selects_extra(tmp_path, _no_localize, _cap, _prop_roster):
    frame = _frame(visible_props=[{"name": "魔方", "view": 1}])
    await _src(_cap).render(_sb([frame]), task_dir=tmp_path, prop_roster=_prop_roster)
    assert _cap.calls[0]["image_urls"] == [_prop_roster["魔方"]["extra_images"][0]]


# ── 互斥规则回归钉（非 roster 路径零变化）─────────────────────────────────


@pytest.mark.asyncio
async def test_non_roster_task_level_character_refs_unchanged(tmp_path, _no_localize, _cap):
    """无 roster：任务级 reference_image_paths 语义原样（互斥规则除 M5 卡外零变化）。"""
    frame = _frame()
    await _src(_cap).render(_sb([frame]), task_dir=tmp_path, reference_image_paths=["/a.png", "/b.png", "/c.png"])
    assert _cap.calls[0]["image_urls"] == ["/a.png", "/b.png", "/c.png"]
    assert _cap.calls[0]["role"] == "reference"
    assert "参考图为角色外观参考" in _cap.calls[0]["prompt"], "三视图非 1:1 → 现状文案"


# ── _assign_roles 语义对齐（seedance-ark 直连）───────────────────────────


def test_assign_roles_entity_refs_all_reference():
    from app.gateway.video_gen.providers.seedance_ark import _assign_roles

    assert _assign_roles(1, entity_refs=True) == ["reference_image"]
    assert _assign_roles(2, entity_refs=True) == ["reference_image", "reference_image"], "2 实体不得映射 first+last（第二实体是角色参考不是尾帧——Finding 6 语义错配）"
    assert _assign_roles(12, entity_refs=True) == ["reference_image"] * 9


def test_assign_roles_default_mapping_unchanged():
    """默认（非实体语义）映射保持现状：ViMax 双帧 first/last 回归保护。"""
    from app.gateway.video_gen.providers.seedance_ark import _assign_roles

    assert _assign_roles(1) == ["first_frame"]
    assert _assign_roles(2) == ["first_frame", "last_frame"]
    assert _assign_roles(3) == ["first_frame", "reference_image", "reference_image"]


@pytest.mark.asyncio
async def test_seedance_ark_entity_refs_wired_via_role(monkeypatch):
    """generate_video：role="reference"（r2v 语义）→ 全 reference_image；默认不变。"""
    from app.gateway.video_gen.providers.seedance_ark import SeedanceArkVideoProvider

    p = SeedanceArkVideoProvider.__new__(SeedanceArkVideoProvider)
    captured: list = []

    async def fake_submit(content, *, duration, ratio=None):  # noqa: ANN001
        captured.append(content)
        return "tid"

    async def fake_poll(tid):  # noqa: ANN001
        return ("http://v/1", None, {})

    monkeypatch.setattr(p, "_submit", fake_submit)
    monkeypatch.setattr(p, "_poll", fake_poll)

    await p.generate_video("p", image_urls=["u1", "u2"], role="reference")
    roles = [c["role"] for c in captured[0] if c.get("type") == "image_url"]
    assert roles == ["reference_image", "reference_image"]

    captured.clear()
    await p.generate_video("p", image_urls=["u1", "u2"])
    roles2 = [c["role"] for c in captured[0] if c.get("type") == "image_url"]
    assert roles2 == ["first_frame", "last_frame"], "默认路径（ViMax 双帧）零回归"


# ── 任务详情响应并入 ref_injection（分镜级标签数据源）────────────────────


def _fake_task(task_id: str = "vg_ri1"):
    task = MagicMock()
    task.id = task_id
    task.user_id = "u1"
    task.domain = "video_gen"
    task.task_type = "ai_video"
    task.status = "ready"
    task.progress = 100
    task.error_message = None
    task.retry_count = 0
    task.title = None
    task.created_at = None
    task.updated_at = None
    task.thumbnail_url = None
    task.cost = None
    task.currency = None
    # C4（2026-08-27 成本可信化）：amount_source 与 cost/currency 同为
    # attach_task_costs 事后赋值字段--mock 必须显式置 None（MagicMock 自动属性
    # 过不了 pydantic 的 str 校验）。
    task.amount_source = None
    task.payload = "{}"
    task.result = "{}"
    return task


def test_task_detail_merges_ref_injection_sidecar(tmp_path):
    from app.gateway.routers.video_gen import _task_with_thumbnail

    task_dir = tmp_path / "u1" / "tasks" / "vg_ri1"
    (task_dir / "sidecar").mkdir(parents=True)
    (task_dir / "sidecar" / "ref_injection.json").write_text(
        json.dumps([{"frame_index": 0, "declared_entities": ["小林"], "injected_refs": [{"entity": "小林", "path": "/f.png", "view": "front"}], "truncated": [], "skipped": [], "reason": ""}], ensure_ascii=False),
        encoding="utf-8",
    )

    brief = _task_with_thumbnail(_fake_task(), storage_dir=str(tmp_path), user_id="u1", include_frame_refs=True)
    ri = brief.result.get("ref_injection")
    assert ri and ri[0]["frame_index"] == 0, "任务详情响应须并入 sidecar 内容（M5 标签数据源）"
    assert ri[0]["injected_refs"][0]["entity"] == "小林"


def test_task_list_does_not_merge_ref_injection(tmp_path):
    """列表端点不并入（零参数默认）——防列表响应膨胀。"""
    from app.gateway.routers.video_gen import _task_with_thumbnail

    task_dir = tmp_path / "u1" / "tasks" / "vg_ri1"
    (task_dir / "sidecar").mkdir(parents=True)
    (task_dir / "sidecar" / "ref_injection.json").write_text(json.dumps([{"frame_index": 0}]), encoding="utf-8")

    brief = _task_with_thumbnail(_fake_task(), storage_dir=str(tmp_path), user_id="u1")
    assert "ref_injection" not in brief.result


def test_task_detail_no_sidecar_no_field(tmp_path):
    from app.gateway.routers.video_gen import _task_with_thumbnail

    (tmp_path / "u1" / "tasks" / "vg_ri2").mkdir(parents=True)
    brief = _task_with_thumbnail(_fake_task("vg_ri2"), storage_dir=str(tmp_path), user_id="u1", include_frame_refs=True)
    assert "ref_injection" not in brief.result
