"""Task 16 (E4 + E5 + course_project_id): 封住越权读与 OOM 入口。

三条核实结果**与审计描述不同**，按实测修：

| 审计说法 | 实测 |
|---|---|
| course_project_store 可读写删任意文件 | ⚠️ **夸大**：``create()`` 用服务端 ``cp_<uuid>``，
路径参数经 ASGI 拒绝编码斜杠（``..%2F`` → 404）。**但** ``course_project_id`` 走**请求体**
（schemas.py）无此限制，service 拼 ``base / user_id / f"{id}.json"`` → **可跨用户读他人项目
json**。即跨租户读，不是任意文件读写 |
| E4 serve_character_sheet IDOR | ✅ 属实：按路径 glob 无 user 校验。**同文件的 by-index 版本已做**归属+越界双校验，对齐它即可 |
| E5 /script/extract 无大小限制 | ✅ 属实：只有扩展名白名单与**事后**字数 warning，``await file.read()`` **前无字节上限**；而 BGM（30MB）、素材（50MB）都有 |
"""

import io

import pytest

from app.gateway.video_gen.service import _is_safe_course_project_id

# ── course_project_id 跨租户读 ─────────────────────────────────────────────


@pytest.mark.parametrize(
    "bad",
    [
        "../../other-user/cp_deadbeef1234",
        "../cp_deadbeef1234",
        "..",
        "cp_x/../../etc/passwd",
        "/absolute/cp_x",
        "cp_x\\..\\..\\win",
        "",
        "cp_" + "z" * 200,
    ],
)
def test_malicious_course_project_id_rejected(bad):
    """穿越/绝对路径/超长 id 一律拒绝。"""
    assert not _is_safe_course_project_id(bad), f"{bad!r} 应被拒绝"


@pytest.mark.parametrize("good", ["cp_a1b2c3d4e5f6", "cp_000000000000"])
def test_wellformed_course_project_id_accepted(good):
    """服务端生成的 cp_<12 hex> 必须放行。"""
    assert _is_safe_course_project_id(good), f"{good!r} 应被接受"


def test_traversal_id_cannot_read_other_users_project(tmp_path):
    """端到端：拿 ../../victim/cp_x 读不到别人的项目。"""
    from app.gateway.video_gen.service import VideoGenService

    storage = tmp_path / "storage"
    victim_dir = storage / "course_projects" / "victim"
    victim_dir.mkdir(parents=True)
    (victim_dir / "cp_secret123456.json").write_text(
        '{"id": "cp_secret123456", "user_id": "victim", "title": "victim secret", "voice_id": "preset:999"}',
        encoding="utf-8",
    )

    svc = VideoGenService.__new__(VideoGenService)
    svc._storage_dir = str(storage)

    proj = svc._read_course_project("../victim/cp_secret123456", user_id="attacker")
    assert proj is None, "跨用户读成功了 —— 越权读他人项目"


def test_legitimate_own_project_still_readable(tmp_path):
    """自己的项目照常可读（不得因加固而误伤）。"""
    from app.gateway.video_gen.service import VideoGenService

    storage = tmp_path / "storage"
    own = storage / "course_projects" / "alice"
    own.mkdir(parents=True)
    (own / "cp_abcdef123456.json").write_text(
        '{"id": "cp_abcdef123456", "user_id": "alice", "title": "mine", "voice_id": "preset:146"}',
        encoding="utf-8",
    )

    svc = VideoGenService.__new__(VideoGenService)
    svc._storage_dir = str(storage)

    proj = svc._read_course_project("cp_abcdef123456", user_id="alice")
    assert proj is not None and proj.voice_id == "preset:146"


# ── /script/extract 字节上限 ────────────────────────────────────────────────


def test_script_upload_size_limit_constant_exists():
    """须有显式上限常量（条目 18：30MB → 200MB，生产 64C/128G/1T 基准放宽）。"""
    from app.gateway.routers import video_gen as vg

    assert hasattr(vg, "SCRIPT_UPLOAD_MAX_BYTES")
    assert vg.SCRIPT_UPLOAD_MAX_BYTES == 200 * 1024 * 1024


@pytest.mark.asyncio
async def test_oversized_script_upload_rejected():
    """超过上限的上传 → 413，且**不得**先把整个文件读进内存。"""
    from fastapi import HTTPException, UploadFile

    from app.gateway.routers.video_gen import extract_script_from_upload

    big = io.BytesIO(b"x" * (201 * 1024 * 1024))
    upload = UploadFile(filename="huge.txt", file=big)

    with pytest.raises(HTTPException) as exc:
        await extract_script_from_upload(file=upload)
    assert exc.value.status_code == 413, f"应返回 413，实际 {exc.value.status_code}"


@pytest.mark.asyncio
async def test_normal_script_upload_still_works():
    """正常大小的脚本上传照常работа（不得误伤）。"""
    from fastapi import UploadFile

    from app.gateway.routers.video_gen import extract_script_from_upload

    content = "第一段。\n第二段。".encode()
    upload = UploadFile(filename="ok.txt", file=io.BytesIO(content))

    result = await extract_script_from_upload(file=upload)
    assert "text" in result and result["text"]


# ── E4: character sheet IDOR ───────────────────────────────────────────────


def test_serve_character_sheet_checks_ownership():
    """结构断言：legacy 路由必须做归属校验，对齐 by-index 版。

    修前按路径 glob 无 user 校验 —— 任何登录用户拿到 project_id 就能读他人角色表图。
    """
    import inspect

    from app.gateway.routers import video_gen as vg

    src = inspect.getsource(vg.serve_character_sheet)
    assert "get_effective_user_id()" in src, "legacy 路由未取当前用户 —— IDOR 未修"
    assert "store.get(" in src and "user_id=user_id" in src, "未按 user_id 校验项目归属"
    # 归属校验必须在读盘之前
    assert src.index("store.get(") < src.index(".glob("), "归属校验应早于文件枚举"


def test_serve_character_sheet_has_traversal_guard():
    """越界校验须与 by-index 版一致。"""
    import inspect

    from app.gateway.routers import video_gen as vg

    src = inspect.getsource(vg.serve_character_sheet)
    assert "outside storage" in src, "缺越界校验（resolved 须在 character_sheets 之下）"


# ── 审查发现 1：另外 4 个 reader 仍裸奔（Task 16 只接了 1/5） ──────────────
#
# `_is_safe_course_project_id` 当初只接进 `_read_course_project`，另 4 个 reader
# 仍只判 `if not project_id:` 就直接拼 `base / user_id / f"{id}.json"`。
# 实跑坐实可跨租户读（前提：攻击者自己有项目目录，否则 OS 解析 `..` 时因中间
# 目录不存在而失败——这也是初次验证误判为「不可利用」的原因）。


def _seed_victim(tmp_path):
    """造出「攻击者有自己目录 + 受害者有项目」的真实布局。"""
    import json as _json

    (tmp_path / "course_projects" / "attacker").mkdir(parents=True)
    victim = tmp_path / "course_projects" / "victim"
    victim.mkdir(parents=True)
    (victim / "cp_secret123456.json").write_text(
        _json.dumps(
            {
                "id": "cp_secret123456",
                "user_id": "victim",
                "title": "受害者项目",
                "voice_id": "preset:999",
                "character_reference": "VICTIM_SECRET",
                "color_palette": {"primary": "#VICTIM"},
                "character_sheets": [{"name": "受害者角色", "front_url": "/v/f.png"}],
            }
        ),
        encoding="utf-8",
    )
    return "../victim/cp_secret123456"


def test_character_reference_reader_rejects_traversal(tmp_path):
    from app.gateway.video_gen.service import _read_course_project_character_reference

    pid = _seed_victim(tmp_path)
    assert _read_course_project_character_reference(str(tmp_path), pid, user_id="attacker") is None


def test_color_palette_reader_rejects_traversal(tmp_path):
    from app.gateway.video_gen.service import _read_course_project_color_palette

    pid = _seed_victim(tmp_path)
    assert not _read_course_project_color_palette(str(tmp_path), pid, user_id="attacker")


def test_character_sheets_reader_rejects_traversal(tmp_path):
    from app.gateway.video_gen.service import _read_course_project_character_sheets

    pid = _seed_victim(tmp_path)
    assert _read_course_project_character_sheets(str(tmp_path), pid, user_id="attacker") == []


def test_style_loader_rejects_traversal(tmp_path):
    from app.gateway.video_gen.service import VideoGenService

    pid = _seed_victim(tmp_path)
    svc = VideoGenService.__new__(VideoGenService)
    svc._storage_dir = str(tmp_path)
    assert not svc._load_course_project_style(pid, user_id="attacker")


def test_legitimate_reads_still_work_for_all_readers(tmp_path):
    """加固不得误伤自己的项目（四个 reader 都要能正常读）。"""
    import json as _json

    from app.gateway.video_gen.service import (
        _read_course_project_character_reference,
        _read_course_project_character_sheets,
        _read_course_project_color_palette,
    )

    own = tmp_path / "course_projects" / "alice"
    own.mkdir(parents=True)
    (own / "cp_abcdef123456.json").write_text(
        _json.dumps(
            {
                "id": "cp_abcdef123456",
                "user_id": "alice",
                "title": "我的项目",
                "character_reference": "MY_REF",
                "color_palette": {"primary": "#112233"},
                "character_sheets": [{"name": "我的角色", "front_url": "/a/f.png"}],
            }
        ),
        encoding="utf-8",
    )

    pid = "cp_abcdef123456"
    assert _read_course_project_character_reference(str(tmp_path), pid, user_id="alice") == "MY_REF"
    assert _read_course_project_color_palette(str(tmp_path), pid, user_id="alice")["primary"] == "#112233"
    assert len(_read_course_project_character_sheets(str(tmp_path), pid, user_id="alice")) == 1
