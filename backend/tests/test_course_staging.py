"""remotion_course 独立管线 T6：docx 图片/表格提取 + staging 认领/TTL/expires_at。

覆盖计划 §7：
- fixture docx（2 张合格图 + 1 张小图 + 1 个表格）：标记位置正确、表格单元格文本
  按序出现、图片落 staging、小图过滤且 ignored_images=1、响应含 expires_at
- 非 docx 忽略 with_images（pdf 附提示）
- staging 认领 / 重复认领 / TTL 清理 / 路径穿越拒绝
- 过期认领 → embedded_images_expired degradation + warning 标记
"""

from __future__ import annotations

import io
import json
import time
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.gateway.routers.video_gen import router
from app.gateway.video_gen.orchestration import course_staging

# ── fixture docx 构造（手写 OOXML——不引 python-docx 依赖）──────────────────

_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_A_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"


def _noise_png(size: int = 400) -> bytes:
    """>15KB 且边长 ≥120px 的合格图（噪声压缩不动）。"""
    import numpy as np  # noqa: PLC0415
    from PIL import Image

    arr = np.random.default_rng(42).integers(0, 255, (size, size, 3), dtype=np.uint8)
    buf = io.BytesIO()
    Image.fromarray(arr, "RGB").save(buf, format="PNG")
    return buf.getvalue()


def _tiny_png(size: int = 50) -> bytes:
    """<15KB 且边长 <120px 的小图（logo/装饰，应被过滤）。"""
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (size, size), (255, 0, 0)).save(buf, format="PNG")
    return buf.getvalue()


def _p(text: str = "", rids: list[str] | None = None) -> str:
    runs = f"<w:r><w:t>{text}</w:t></w:r>"
    for rid in rids or []:
        runs += f'<w:r><w:drawing><a:blip r:embed="{rid}"/></w:drawing></w:r>'
    return f"<w:p>{runs}</w:p>"


def _tbl(rows: list[list[str]]) -> str:
    trs = "".join("<w:tr>" + "".join(f"<w:tc><w:p><w:r><w:t>{cell}</w:t></w:r></w:p></w:tc>" for cell in row) + "</w:tr>" for row in rows)
    return f"<w:tbl>{trs}</w:tbl>"


def _docx(paragraph_xml: str, media: dict[str, bytes], rels: dict[str, str]) -> bytes:
    rel_entries = "".join(f'<Relationship Id="{rid}" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/image" Target="{target}"/>' for rid, target in rels.items())
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Default Extension="png" ContentType="image/png"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        "</Types>"
    )
    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
        "</Relationships>"
    )
    doc_rels = f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{rel_entries}</Relationships>'
    document = f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="{_W_NS}" xmlns:r="{_R_NS}" xmlns:a="{_A_NS}"><w:body>{paragraph_xml}</w:body></w:document>'
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", root_rels)
        z.writestr("word/document.xml", document)
        z.writestr("word/_rels/document.xml.rels", doc_rels)
        for name, data in media.items():
            z.writestr(f"word/{name}", data)
    return buf.getvalue()


def _fixture_docx() -> bytes:
    """两段文字 + 表格 + 3 图（其中 1 张小图）。"""
    body = (
        _p("课程开篇：数据驱动决策。", rids=["rId10"])  # 图1（合格）原位
        + _p("第二段介绍指标体系。")
        + _tbl([["指标", "数值"], ["准确率", "92%"], ["召回率", "85%"]])
        + _p("第三段讲落地案例，", rids=["rId11", "rId12"])  # 图2（合格）+ 图3（小图）
    )
    media = {
        "media/image1.png": _noise_png(),
        "media/image2.png": _noise_png(300),
        "media/image3.png": _tiny_png(),
    }
    rels = {"rId10": "media/image1.png", "rId11": "media/image2.png", "rId12": "media/image3.png"}
    return _docx(body, media, rels)


# ── 提取（纯函数）──────────────────────────────────────────────────────────


def test_extract_docx_markers_tables_and_filter(tmp_path):
    result = course_staging.extract_docx_with_images(_fixture_docx())
    text = result["text"]
    # 标记原位：图1 在第一段句尾；图2 在第三段（图3 被过滤不给标记）
    assert "课程开篇：数据驱动决策。【图片1】" in text
    assert "第三段讲落地案例，【图片2】" in text
    assert "【图片3】" not in text
    # 表格：逐行单元格文本按序出现（与段落相对位置保持）
    assert text.index("第二段介绍指标体系。") < text.index("指标 数值") < text.index("准确率 92%") < text.index("召回率 85%") < text.index("第三段")
    # 两张合格图 + 1 张过滤
    assert [img["ref"] for img in result["images"]] == ["img_1", "img_2"]
    assert all(len(img["bytes"]) > 15 * 1024 for img in result["images"])
    assert result["ignored_images"] == 1


# ── extract 端点（with_images）─────────────────────────────────────────────


def _extract_app(tmp_path, *, with_images: bool, filename: str, content: bytes, content_type: str):
    app = FastAPI()
    app.include_router(router)
    mock_svc = MagicMock()
    mock_svc._storage_dir = str(tmp_path / "storage")
    (tmp_path / "storage").mkdir(exist_ok=True)
    app.state.video_gen_service = mock_svc
    client = TestClient(app)
    resp = client.post(
        "/api/video/script/extract",
        params={"with_images": with_images} if with_images else None,
        files={"file": (filename, content, content_type)},
    )
    return resp


def test_extract_endpoint_with_images_docx(tmp_path):
    resp = _extract_app(tmp_path, with_images=True, filename="课程.docx", content=_fixture_docx(), content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "【图片1】" in data["text"]
    assert [img["ref"] for img in data["images"]] == ["img_1", "img_2"]
    assert data["ignored_images"] == 1
    assert data["extract_id"]
    assert data["expires_at"]
    # staging 落盘（条目 18：按用户命名空间 script-uploads/{user}/{extract_id}/；
    # TestClient 无认证上下文 → get_effective_user_id 回落 "default"）
    matches = list((Path(tmp_path) / "storage" / "script-uploads").glob(f"*/{data['extract_id']}"))
    assert len(matches) == 1, "staging 必须落在用户子目录下"
    files = sorted(p.name for p in matches[0].iterdir())
    assert len(files) == 2


def test_extract_endpoint_without_images_unchanged(tmp_path):
    """默认关 = 现状逐字节不变：响应无 images/extract_id 键（text-only 口径）。"""
    resp = _extract_app(tmp_path, with_images=False, filename="课程.docx", content=_fixture_docx(), content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert "images" not in data and "extract_id" not in data
    assert "【图片" not in data["text"]  # markitdown 口径不含标记


def test_extract_endpoint_pdf_ignores_with_images(tmp_path):
    """非 docx 忽略 with_images：pdf 返回提示（图片不自动分配）。"""
    resp = _extract_app(tmp_path, with_images=True, filename="课程.pdf", content=b"%PDF-1.4 fake", content_type="application/pdf")
    # pdf 走 markitdown 口径——fixture 字节非真 pdf，文本提取失败或空均可接受；
    # 关键断言：不 500、不带 extract_id/images（with_images 被忽略）
    assert resp.status_code in (200, 400, 422)
    if resp.status_code == 200:
        data = resp.json()
        assert not data.get("images")
        assert "extract_id" not in data


# ── staging 认领 ───────────────────────────────────────────────────────────


def _stage_two_images(storage: Path, user_id: str = "default") -> str:
    """经真实 stage_extracted_images 落盘（条目 18 用户命名空间布局）。"""
    result = course_staging.extract_docx_with_images(_fixture_docx())
    images = [{"ref": f"img_{i}", "name": "img.png", "bytes": img["bytes"]} for i, img in enumerate(result["images"], start=1)]
    return course_staging.stage_extracted_images(images, storage / "script-uploads", user_id=user_id)


def test_stage_layout_is_user_namespaced(tmp_path):
    """条目 18：staging 必须落在 script-uploads/{user_id}/{extract_id}/。"""
    storage = tmp_path / "storage"
    storage.mkdir()
    extract_id = _stage_two_images(storage, user_id="alice")
    staging_dir = storage / "script-uploads" / "alice" / extract_id
    assert staging_dir.is_dir() and len(list(staging_dir.iterdir())) == 2


def test_claim_moves_images_and_cleans_staging(tmp_path):
    storage = tmp_path / "storage"
    storage.mkdir()
    extract_id = _stage_two_images(storage, user_id="u1")
    task_dir = tmp_path / "task"
    task_dir.mkdir()

    paths, expired = course_staging.claim_script_images(extract_id, task_dir, storage_dir=str(storage), user_id="u1")
    assert expired is False
    assert paths == {"img_1": "visuals/embedded/img_1.png", "img_2": "visuals/embedded/img_2.png"}
    # 搬进任务目录
    assert (task_dir / "visuals/embedded/img_1.png").read_bytes()
    # 认领后 staging 删除
    assert not (storage / "script-uploads" / "u1" / extract_id).exists()


def test_claim_twice_second_is_expired(tmp_path):
    storage = tmp_path / "storage"
    storage.mkdir()
    extract_id = _stage_two_images(storage)
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    course_staging.claim_script_images(extract_id, task_dir, storage_dir=str(storage))
    # 重复认领：staging 已删 → expired + degradation 留痕 + 素材保留
    paths2, expired2 = course_staging.claim_script_images(extract_id, task_dir, storage_dir=str(storage))
    assert expired2 is True and paths2 == {}
    deg = json.loads((task_dir / "degradations.json").read_text(encoding="utf-8"))
    assert any(i["kind"] == "embedded_images_expired" for i in deg["items"])
    # 第一次认领的素材不被二次认领动
    assert (task_dir / "visuals/embedded/img_1.png").exists()


def test_claim_cross_user_is_expired_not_claimed(tmp_path):
    """条目 18：属主校验——A 暂存 B 认领 → 按不存在处理（空映射 + expired 留痕），
    A 的 staging 原样保留。"""
    storage = tmp_path / "storage"
    storage.mkdir()
    extract_id = _stage_two_images(storage, user_id="alice")
    task_dir = tmp_path / "task"
    task_dir.mkdir()

    paths, expired = course_staging.claim_script_images(extract_id, task_dir, storage_dir=str(storage), user_id="bob")
    assert expired is True and paths == {}
    deg = json.loads((task_dir / "degradations.json").read_text(encoding="utf-8"))
    assert any(i["kind"] == "embedded_images_expired" for i in deg["items"])
    # B 认领不得动 A 的 staging（也搬不走任何素材）
    assert (storage / "script-uploads" / "alice" / extract_id).is_dir()
    assert not (task_dir / "visuals" / "embedded").exists()


def test_claim_rejects_path_traversal(tmp_path):
    storage = tmp_path / "storage"
    storage.mkdir()
    task_dir = tmp_path / "task"
    task_dir.mkdir()
    for bad in ("../../etc", "foo/bar", "abc def", "x" * 100):
        paths, expired = course_staging.claim_script_images(bad, task_dir, storage_dir=str(storage))
        assert paths == {}
        # 非法 id 不应造成 expired 留痕（区别于合法但过期的 id）
        assert expired is False or not (task_dir / "degradations.json").exists()


def test_cleanup_stale_staging(tmp_path):
    storage = tmp_path / "storage" / "script-uploads"
    storage.mkdir(parents=True)
    old = storage / "OLDULIDOLDULIDOLDULIDOLDULOD"
    old.mkdir()
    (old / "img_1.png").write_bytes(b"x")
    fresh = storage / "FRESHULIDFRESHULIDFRESHULIDFRE"
    fresh.mkdir()
    (fresh / "img_1.png").write_bytes(b"x")
    # 把 old 的 mtime 拨回 25h 前
    ts = time.time() - 25 * 3600
    import os

    os.utime(old, (ts, ts))
    removed = course_staging.cleanup_stale_staging(storage.parent, ttl_seconds=24 * 3600)
    assert not old.exists()
    assert fresh.exists()
    assert removed == 1


@pytest.mark.asyncio
async def test_service_build_course_storyboard_claims_staging(tmp_path):
    """认领接线：_build_course_storyboard 传 script_extract_id → 帧带 local_visual_path。"""
    from unittest.mock import patch

    from app.gateway.video_gen.config import VideoGenConfig
    from app.gateway.video_gen.orchestration.course_script import CourseUnit
    from app.gateway.video_gen.providers.registry import ProviderRegistry
    from app.gateway.video_gen.service import VideoGenService

    cfg = VideoGenConfig(qihu_api_key="t", script_model="m")
    svc = VideoGenService(repo=MagicMock(), config=cfg, providers=ProviderRegistry(cfg), storage_dir=str(tmp_path / "storage"), sandbox_provider=AsyncMock())
    (tmp_path / "storage").mkdir()
    extract_id = _stage_two_images(tmp_path / "storage")
    task_dir = tmp_path / "task"
    task_dir.mkdir()

    units = [
        CourseUnit(layout="CoverPage", narration="封面。"),
        CourseUnit(layout="ImageTextLeft", narration="带图单元。", image_ref="img_2"),
    ]
    script = "这是一段足够长的课程脚本。" * 10
    with (
        patch("app.gateway.video_gen.orchestration.course_script.parse_course_script", new=AsyncMock(return_value=units)),
        patch("app.gateway.video_gen.service.create_script_llm", return_value=MagicMock()),
    ):
        frames, groups = await svc._build_course_storyboard(script, {"script_extract_id": extract_id, "course_parse_mode": "faithful"}, task_dir)
    assert frames[1].local_visual_path == "visuals/embedded/img_2.png"
    assert frames[1].visual_source is None
    # 认领后 staging 清空
    assert not (tmp_path / "storage" / "script-uploads" / "default" / extract_id).exists()


# ── 2026-09-11 审查修复回归 ───────────────────────────────────────────────


def test_image_edges_ok_unidentifiable_false():
    """Minor：PIL 识别不出的格式（EMF/WMF/纯垃圾字节）判不过（修前宽 except 放行）。"""
    assert course_staging._image_edges_ok(b"garbage-not-an-image" * 1000) is False


def test_image_edges_ok_size_gate():
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (300, 200), "white").save(buf, format="PNG")
    assert course_staging._image_edges_ok(buf.getvalue()) is True
    small = io.BytesIO()
    Image.new("RGB", (50, 50), "white").save(small, format="PNG")
    assert course_staging._image_edges_ok(small.getvalue()) is False


def test_claim_partial_failure_cleans_dest_and_keeps_staging(tmp_path, monkeypatch):
    """Minor：认领非原子——copy 中途失败清残留 dest（防重试误判已认领），staging 保留供重试。"""
    storage = tmp_path / "storage"
    extract_id = _stage_two_images(storage)
    task_dir = tmp_path / "task"
    task_dir.mkdir()

    import shutil as _shutil

    real_copy2 = _shutil.copy2
    calls = {"n": 0}

    def _flaky_copy(src, dst, *a, **kw):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("disk full")
        return real_copy2(src, dst, *a, **kw)

    monkeypatch.setattr(course_staging.shutil, "copy2", _flaky_copy)
    with pytest.raises(OSError):
        course_staging.claim_script_images(extract_id, task_dir, storage_dir=storage)
    # dest 半成品被清、staging 未删（可重试）
    assert not (task_dir / "visuals" / "embedded").exists() or not any((task_dir / "visuals" / "embedded").iterdir())
    assert (storage / "script-uploads" / "default" / extract_id).is_dir()
    # 重试（不再失败）可完整认领
    paths, expired = course_staging.claim_script_images(extract_id, task_dir, storage_dir=storage)
    assert expired is False and len(paths) == 2


def test_extract_endpoint_corrupt_docx_falls_back_with_rewound_stream(tmp_path, monkeypatch):
    """M4：with_images 提取失败回退 markitdown 前必须 seek(0)——修前流已被 read()
    消费，回退路径读到空流把 200 变 500。"""
    import io as _io
    import zipfile as _zip

    buf = _io.BytesIO()
    with _zip.ZipFile(buf, "w") as z:
        z.writestr("unrelated.txt", "not a docx")
    corrupt = buf.getvalue()

    captured = {}

    async def _capture(file):
        captured["content"] = await file.read()
        return "fallback-text"

    monkeypatch.setattr("app.gateway.routers.video_gen._extract_script_text", _capture)
    resp = _extract_app(tmp_path, with_images=True, filename="坏.docx", content=corrupt, content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    assert resp.status_code == 200, resp.text
    assert resp.json()["text"] == "fallback-text"
    # 回退路径拿到的是倒带后的完整流（修前为空字节串）
    assert captured["content"] == corrupt


def test_extract_endpoint_response_images_meta_only(tmp_path):
    """m16（2026-09-11 二轮审查）：响应 images 只回 {ref,name} 元信息（base64 字节
    前端零消费，最坏把响应撑到几十 MB）。"""
    resp = _extract_app(tmp_path, with_images=True, filename="课程.docx", content=_fixture_docx(), content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    assert resp.status_code == 200, resp.text
    for img in resp.json()["images"]:
        assert set(img.keys()) == {"ref", "name"}


def test_extract_endpoint_pdf_warning_not_overwritten(tmp_path, monkeypatch):
    """m15（2026-09-11 二轮审查）：超长 pdf 的两条 warning 拼接而非覆盖——
    修前 SCRIPT_MAX_CHARS 分支把"图片不自动分配"提示顶掉。"""
    from app.gateway.routers.video_gen import SCRIPT_MAX_CHARS

    async def _long_text(file):
        return "长" * (SCRIPT_MAX_CHARS + 10)

    monkeypatch.setattr("app.gateway.routers.video_gen._extract_script_text", _long_text)
    resp = _extract_app(tmp_path, with_images=True, filename="课程.pdf", content=b"%PDF-1.4 fake", content_type="application/pdf")
    assert resp.status_code == 200, resp.text
    warning = resp.json()["warning"]
    assert "PDF 中的图片暂不支持自动分配" in warning
    assert "脚本超过" in warning


# ── 2026-09-12 缺陷批条目 14/18 ───────────────────────────────────────────


def test_extract_docx_literal_blip_text_preserved():
    """条目 14：正文含字面 "BLIP1"/"BLIP99" 的 docx → 提取文本原样保留、图标记不错位。

    钉行为：占位符是 U+E000 定界的私用区形态（3415ffd33 起即如此——2026-09-12 计划
    核实时被不可见字符渲染误导记为「纯 ASCII」，本用例防回归也防再误判）。
    """
    body = _p("版本 BLIP1 的规格说明。", rids=["rId10"]) + _p("对照组：BLIP99 与 BLIP0 都是普通正文。")
    media = {"media/image1.png": _noise_png()}
    rels = {"rId10": "media/image1.png"}
    result = course_staging.extract_docx_with_images(_docx(body, media, rels))
    text = result["text"]
    # 字面 BLIP 文本原样保留，不被占位符还原逻辑误改写/误删除
    assert "版本 BLIP1 的规格说明。" in text
    assert "对照组：BLIP99 与 BLIP0 都是普通正文。" in text
    # 真实图片标记仍原位（不错位、不与字面 BLIP 混淆）
    assert "【图片1】" in text
    assert text.index("【图片1】") < text.index("BLIP99")


def test_staging_usage_and_quota_per_user(tmp_path):
    """条目 18：每用户配额 ≤10 个在存且总量 ≤2GB——staging_usage 只数自己命名空间。"""
    storage = tmp_path / "storage"
    storage.mkdir()
    # 他人占位不计入
    _stage_two_images(storage, user_id="alice")
    count, total = course_staging.staging_usage(storage / "script-uploads", "bob")
    assert (count, total) == (0, 0)
    assert course_staging.staging_quota_exceeded(storage / "script-uploads", "bob") is None
    # 撑满 10 个 → 超限文案
    for i in range(course_staging.MAX_STAGING_PER_USER):
        extract_id = _stage_two_images(storage, user_id="bob")
        assert extract_id
    msg = course_staging.staging_quota_exceeded(storage / "script-uploads", "bob")
    assert msg == "暂存空间已满，请等 24h 自动过期或联系管理员"


def test_staging_quota_bytes_limit(tmp_path, monkeypatch):
    """条目 18：总量 ≥2GB 超限（monkeypatch 阈值避免真写 2GB）。"""
    storage = tmp_path / "storage"
    storage.mkdir()
    _stage_two_images(storage, user_id="bob")
    monkeypatch.setattr(course_staging, "MAX_STAGING_BYTES_PER_USER", 1)
    msg = course_staging.staging_quota_exceeded(storage / "script-uploads", "bob")
    assert msg is not None and "暂存空间已满" in msg


def test_staging_quota_incoming_bytes_counted(tmp_path, monkeypatch):
    """复审修订（2026-09-13）：预检计入本次上传——「落盘后越顶（>）」才 429，
    正好到顶（==）放行；不再把峰值放到 2GB+单件。"""
    storage = tmp_path / "storage"
    storage.mkdir()
    _stage_two_images(storage, user_id="bob")
    root = storage / "script-uploads"
    _, total = course_staging.staging_usage(root, "bob")
    assert total > 0
    monkeypatch.setattr(course_staging, "MAX_STAGING_BYTES_PER_USER", total + 100)
    # 在存 + incoming 越顶 → 超限
    assert course_staging.staging_quota_exceeded(root, "bob", incoming_bytes=101) is not None
    # 正好到顶 → 放行
    assert course_staging.staging_quota_exceeded(root, "bob", incoming_bytes=100) is None
    # 纯查询口径（不传 incoming）：在存未越顶 → 放行
    assert course_staging.staging_quota_exceeded(root, "bob") is None


def test_extract_endpoint_quota_429(tmp_path):
    """条目 18：extract 端点配额预检——在存满 10 个 → 429 + 可读文案，不落新 staging。

    conftest autouse fixture 注入 test-user-autouse（非 "default"），按端点同源
    get_effective_user_id() 取用户命名空间，避免与 fixture 假设耦合。
    """
    from deerflow.runtime.user_context import get_effective_user_id

    user = get_effective_user_id()
    storage = tmp_path / "storage" / "script-uploads"
    storage.mkdir(parents=True)
    for i in range(course_staging.MAX_STAGING_PER_USER):
        d = storage / user / f"EXISTING{i:02d}ULIDEXISTING"
        d.mkdir(parents=True)
        (d / "img_1.png").write_bytes(b"x")
    before = sorted(p.name for p in (storage / user).iterdir())
    resp = _extract_app(tmp_path, with_images=True, filename="课程.docx", content=_fixture_docx(), content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    assert resp.status_code == 429, resp.text
    assert "暂存空间已满" in resp.json()["detail"]
    # 拒绝后未新增 staging 目录
    assert sorted(p.name for p in (storage / user).iterdir()) == before


def test_cleanup_stale_staging_user_namespaced(tmp_path):
    """条目 18：新布局两级清理（{user}/{extract} 只清过期 extract 目录，用户目录保留）。"""
    storage = tmp_path / "storage" / "script-uploads"
    storage.mkdir(parents=True)
    import os

    old = storage / "alice" / "OLDULIDOLDULIDOLDULIDOLDULOD"
    old.mkdir(parents=True)
    (old / "img_1.png").write_bytes(b"x")
    fresh = storage / "alice" / "FRESHULIDFRESHULIDFRESHULIDFRE"
    fresh.mkdir(parents=True)
    (fresh / "img_1.png").write_bytes(b"x")
    ts = time.time() - 25 * 3600
    os.utime(old, (ts, ts))
    removed = course_staging.cleanup_stale_staging(storage.parent, ttl_seconds=24 * 3600)
    assert removed == 1
    assert not old.exists()
    assert fresh.exists()
    assert (storage / "alice").is_dir()  # 用户目录本身保留


# ── with_images 分支线程纪律（2026-09-29 计划阶段 3）────────────────────────


def test_extract_endpoint_sync_calls_off_event_loop_thread(tmp_path, monkeypatch):
    """docx with_images 分支的 extract/stage 两处同步函数不得跑在事件循环线程上。

    E5 上限 200MB：大 docx 的 zipfile+XML 解析 / staging 落盘是秒~十秒级阻塞，
    在事件循环线程上执行会卡死整个 gateway。断言口径：两处同步函数记录的
    threading.get_ident() 必须都 ≠ 端点协程所在的事件循环线程 ident（以协程
    体内同步调用 `_get_service` 记录的 ident 为事件循环线程基准）。
    """
    import threading

    loop_ident: dict[str, int] = {}
    call_idents: dict[str, int] = {}

    def _fake_get_service(request):
        # 端点协程体内的同步调用 = 事件循环线程 → 作为基准 ident。
        loop_ident["v"] = threading.get_ident()
        mock_svc = MagicMock()
        mock_svc._storage_dir = str(tmp_path / "storage")
        return mock_svc

    real_extract = course_staging.extract_docx_with_images
    real_stage = course_staging.stage_extracted_images

    def _fake_extract(content):
        call_idents["extract"] = threading.get_ident()
        return real_extract(content)

    def _fake_stage(images, staging_root, *, user_id="default"):
        call_idents["stage"] = threading.get_ident()
        return real_stage(images, staging_root, user_id=user_id)

    monkeypatch.setattr("app.gateway.routers.video_gen._get_service", _fake_get_service)
    monkeypatch.setattr(course_staging, "extract_docx_with_images", _fake_extract)
    monkeypatch.setattr(course_staging, "stage_extracted_images", _fake_stage)
    (tmp_path / "storage").mkdir(exist_ok=True)

    resp = _extract_app(tmp_path, with_images=True, filename="课程.docx", content=_fixture_docx(), content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["extract_id"]
    assert [img["ref"] for img in data["images"]] == ["img_1", "img_2"]
    # 修前（同步调用在事件循环线程上）：ident 与基准相同 → 红；修后（to_thread 工作线程）→ 不同
    assert call_idents["extract"] != loop_ident["v"], "extract 必须移出事件循环线程"
    assert call_idents["stage"] != loop_ident["v"], "stage 必须移出事件循环线程"
