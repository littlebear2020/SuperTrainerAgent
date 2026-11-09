"""资产中心 API（Phase 3.5，2026-09-11 ppt-master 6.3.0 批次）。

平台级资产注册表端点（§十一 gateway 明确开口）。首个资产类型：
`ppt_template`——上传 PPT → 确定性品牌蒸馏（ooxml-v1）→ 预览解析要素 →
确认保存；「应用到课程项目」把 trainer_brand 字段映射进 CourseProject
品牌包（palette→color_palette.primary、typography→font_family、logo→
logo_path 拷贝进项目资产目录、style_tendency→remotion_theme），复用既有
CourseProject 更新通路。
"""

from __future__ import annotations

import json
import logging
import re
import shutil
import unicodedata
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from app.gateway.assets.distiller import distill_pptx
from app.gateway.assets.store import AssetStore

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/assets", tags=["assets"])

# 条目 21（2026-09-12 缺陷批）：50MB → 100MB（用户按生产 64C/128G/1T 基准指示放宽；
# probe 峰值内存 100MB/请求在 128G 下无压力）。nginx 联动：catch-all /api/ 50M 会先
# 413 截胡，docker/nginx/nginx.conf 已加 dedicated location `= /api/assets/ppt-templates`
# （110M，让后端可读报错先生效）。前端预检常量同步：frontend/src/core/assets/api.ts。
MAX_PPTX_BYTES = 100 * 1024 * 1024


def _get_store() -> AssetStore:
    from deerflow.config.paths import get_paths

    return AssetStore(base_dir=get_paths().base_dir)


def _require_record(store: AssetStore, asset_id: str, user_id: str):
    rec = store.get(asset_id, user_id)
    if rec is None:
        raise HTTPException(status_code=404, detail="Asset not found")
    if rec.type != "ppt_template":
        raise HTTPException(status_code=404, detail="Not a ppt_template asset")
    return rec


def _summary(rec) -> dict:
    data = rec.to_dict()
    brand = data.get("brand") or {}
    return {
        "id": data["id"],
        "type": data["type"],
        "name": data.get("name"),
        "status": data.get("status"),
        "created_at": data.get("created_at"),
        "updated_at": data.get("updated_at"),
        "palette": brand.get("palette"),
        "font_family": brand.get("font_family"),
        "remotion_theme": (brand.get("style_tendency") or {}).get("remotion_theme"),
        "has_logo": bool((brand.get("logo") or {}).get("filename")),
    }


class PptTemplateUpdate(BaseModel):
    """确认保存：改名 + 品牌要素人工订正（映射字段白名单）。"""

    name: str | None = Field(None, min_length=1, max_length=120)
    brand_overrides: dict | None = None


# 条目 22（2026-09-12 缺陷批）：品牌要素写入校验。修前 brand_overrides 自由 dict
# 直灌 remotion 模板 CSS（primary 只查 startswith("#")——"#WINDOW"/"#zz" 全通过）。
# 与 :remotion_theme 白名单校验同风格（400）。
_PRIMARY_HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
FONT_FAMILY_MAX_CHARS = 120  # 同文件 name 上限口径（distiller/前端文本字段惯例）


def _validated_primary(raw) -> str | None:
    """primary 校验 + 大写归一（对齐 distiller._hex 产出形态）；空值 → None。"""
    val = str(raw or "").strip()
    if not val:
        return None
    if not _PRIMARY_HEX_RE.fullmatch(val):
        raise HTTPException(status_code=400, detail="primary 必须是 #RRGGBB 六位十六进制色值（如 #2563EB）")
    return val.upper()


def _validated_font_family(raw) -> str | None:
    """font_family 校验：≤120 字 + 去控制字符（直灌模板 CSS fontFamily 的字段）。"""
    val = str(raw or "")
    if len(val) > FONT_FAMILY_MAX_CHARS:
        raise HTTPException(status_code=400, detail=f"font_family 超过 {FONT_FAMILY_MAX_CHARS} 字上限")
    val = "".join(ch for ch in val if unicodedata.category(ch) != "Cc")
    return val.strip() or None


class ApplyToProjectBody(BaseModel):
    project_id: str


@router.post("/ppt-templates")
async def create_ppt_template(
    request: Request,
    file: UploadFile = File(...),
    name: str = Form(default=""),
):
    """上传 PPTX → 同步确定性蒸馏 → 返回解析出的品牌要素（表单预览数据）。

    上传即建记录（status=ready）；用户在表单里预览/订正后走 PUT 确认保存。
    校验：zip 魔数 + ppt/ 部件存在 + ≤100MB。
    条目 21（2026-09-12 缺陷批）：修前 `await file.read()` 整读进内存后才比上限；
    改两段式（对齐 routers/video_gen.py script/extract 先例）——先查 declared
    `file.size` → 413；缺失时 probe 读 MAX+1 字节超限即 413 + seek(0) 倒带
    （不得先把整个文件读进内存）。
    """
    from deerflow.runtime.user_context import get_effective_user_id

    user_id = get_effective_user_id()
    declared = getattr(file, "size", None)
    if not isinstance(declared, int):
        declared = None  # Starlette 未填 / 非数值（含测试替身）→ 走读后校验
    if declared is not None and declared > MAX_PPTX_BYTES:
        raise HTTPException(status_code=413, detail=f"文件超过 {MAX_PPTX_BYTES // (1024 * 1024)}MB 上限")
    if declared is None:
        try:
            probe = await file.read(MAX_PPTX_BYTES + 1)
        except TypeError:
            probe = b""  # 测试替身的 read() 不接受 size 参数
        if isinstance(probe, (bytes, bytearray)) and len(probe) > MAX_PPTX_BYTES:
            raise HTTPException(status_code=413, detail=f"文件超过 {MAX_PPTX_BYTES // (1024 * 1024)}MB 上限")
        try:
            await file.seek(0)
        except TypeError:
            pass
    data = await file.read()
    if len(data) > MAX_PPTX_BYTES:
        # seek 不可用（测试替身）且 probe 恰好等于上限的兜底；生产路径不会走到
        raise HTTPException(status_code=413, detail=f"文件超过 {MAX_PPTX_BYTES // (1024 * 1024)}MB 上限")
    store = _get_store()
    rec = store.create(user_id=user_id, type="ppt_template", name=name or (file.filename or "未命名模板"), source_filename=file.filename)
    asset_dir = store.files_dir(rec.id)
    source = asset_dir / "source.pptx"
    source.write_bytes(data)
    try:
        brand = distill_pptx(source, asset_dir)
    except ValueError as exc:
        store.delete(rec.id, user_id)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        # 非 ValueError 的解析失败（zip CRC 损坏等）：同样清理已建记录，
        # 不留 status=ready/brand=None 的孤儿条目挂在模板列表里
        store.delete(rec.id, user_id)
        logger.warning("brand template parse failed: %s", exc)
        raise HTTPException(status_code=500, detail="模板解析失败（原因见服务端日志）") from exc
    # trainer_brand.json 落库（单一事实源形状；应用侧与表单回读同一份）
    (asset_dir / "trainer_brand.json").write_text(json.dumps(brand, ensure_ascii=False, indent=2), encoding="utf-8")
    rec = store.update(rec.id, user_id, brand=brand)
    return {"asset": rec.to_dict()}


@router.get("/ppt-templates")
async def list_ppt_templates(request: Request):
    from deerflow.runtime.user_context import get_effective_user_id

    store = _get_store()
    items = [_summary(r) for r in store.list(user_id=get_effective_user_id(), type="ppt_template")]
    return {"assets": items}


@router.get("/ppt-templates/{asset_id}")
async def get_ppt_template(request: Request, asset_id: str):
    from deerflow.runtime.user_context import get_effective_user_id

    store = _get_store()
    rec = _require_record(store, asset_id, get_effective_user_id())
    data = rec.to_dict()
    data["logo_url"] = f"/api/assets/ppt-templates/{asset_id}/logo"
    return data


@router.put("/ppt-templates/{asset_id}")
async def update_ppt_template(request: Request, asset_id: str, body: PptTemplateUpdate):
    """确认保存：改名 + 品牌要素订正（白名单：font_family / primary / theme）。

    E0（DEF-01，2026-09-23 批 2）：白名单三字段写入时同步维护 brand 顶层
    ``_confirmed: list[str]`` 元数据（"font_family" / "palette.primary" /
    "style_tendency.remotion_theme"），redistill 据此保留用户订正值。
    remotion_theme 既有 ``reason="用户确认"`` 标记保留。字段结构不动（兼容
    brand_pack 跨产品约定）；``_confirmed`` 不上屏（前端具名渲染 + TS 类型
    未声明该键）。
    """
    from deerflow.runtime.user_context import get_effective_user_id

    user_id = get_effective_user_id()
    store = _get_store()
    rec = _require_record(store, asset_id, user_id)
    data = rec.to_dict()
    brand = dict(data.get("brand") or {})
    confirmed = [k for k in (brand.get("_confirmed") or []) if isinstance(k, str)]
    overrides = body.brand_overrides or {}

    def _is_clear(raw) -> bool:
        """R11（2026-09-24 批 2）：显式清空（null/空串）——摘除订正标记，不写值。"""
        return raw is None or (isinstance(raw, str) and not raw.strip())

    if "font_family" in overrides:
        # 条目 22：≤120 字 + 去控制字符（修前自由 str() 直灌模板 CSS）
        if _is_clear(overrides["font_family"]):
            # R11：null 不做永久订正——从 _confirmed 摘除，brand 现值不动，
            # 下次 redistill 该字段回到新蒸馏值（修前会把 None 写进 brand 并
            # 被 _confirmed 永久保留，redistill 恢复 None）。
            confirmed = [k for k in confirmed if k != "font_family"]
        else:
            brand["font_family"] = _validated_font_family(overrides["font_family"])
            confirmed = [k for k in confirmed if k != "font_family"] + ["font_family"]
    if "primary" in overrides:
        # 条目 22：#RRGGBB 校验 + 大写归一（修前 "#zz"/非 # 开头全通过）
        if _is_clear(overrides["primary"]):
            # R11：同 font_family——null 摘除订正，palette.primary 不落 None
            confirmed = [k for k in confirmed if k != "palette.primary"]
        else:
            palette = dict(brand.get("palette") or {})
            palette["primary"] = _validated_primary(overrides["primary"])
            brand["palette"] = palette
            confirmed = [k for k in confirmed if k != "palette.primary"] + ["palette.primary"]
    if "remotion_theme" in overrides:
        if _is_clear(overrides["remotion_theme"]):
            # R11：枚举字段同语义——null 摘除订正标记（tendency 现值不动）
            confirmed = [k for k in confirmed if k != "style_tendency.remotion_theme"]
        else:
            if overrides["remotion_theme"] not in ("dark-blue", "light", "dark-green", "warm"):
                raise HTTPException(status_code=400, detail="remotion_theme 必须是四主题之一")
            tendency = dict(brand.get("style_tendency") or {})
            tendency["remotion_theme"] = overrides["remotion_theme"]
            tendency["reason"] = "用户确认"
            brand["style_tendency"] = tendency
            if "style_tendency.remotion_theme" not in confirmed:
                confirmed.append("style_tendency.remotion_theme")
    # E0 附带修正（2026-09-23 review #6）：仅当确有白名单字段被确认过才落
    # ``_confirmed``——无条件写空列表会让存量资产的第一次仅改名 PUT 也带上
    # ``_confirmed: []``，redistill 的 ``legacy_asset`` 随之判 False、
    # legacy 覆盖警示被静默绕过。旧版已落盘的空列表也在此摘除（零信息）。
    if confirmed:
        brand["_confirmed"] = confirmed
    else:
        brand.pop("_confirmed", None)
    asset_dir = store.files_dir(asset_id)
    (asset_dir / "trainer_brand.json").write_text(json.dumps(brand, ensure_ascii=False, indent=2), encoding="utf-8")
    rec = store.update(asset_id, user_id, name=body.name, brand=brand)
    return {"asset": rec.to_dict()}


@router.post("/ppt-templates/{asset_id}/redistill")
async def redistill_ppt_template(request: Request, asset_id: str):
    """重新蒸馏（条目 36 + DEF-01 / E1，2026-09-23 批 2）：source.pptx 重跑确定性蒸馏。

    语义（E1 修订）：**逐字段合并**——brand 顶层 ``_confirmed`` 列出的字段
    （font_family / palette.primary / style_tendency.remotion_theme，PUT 确认时
    维护）保留用户订正值；其余（palette 辅色 / logo / layouts / style_tendency
    档案）用新蒸馏值刷新；``_confirmed`` 随 brand 保留。响应 ``brand_diff``
    增加 ``preserved_user_confirmed`` 标注。

    **存量无 ``_confirmed`` 的历史资产不迁移**：redistill 时三字段照旧全量刷新，
    ``brand_diff.legacy_overwrite_warning`` 明示"本次覆盖可能包含历史人工订正，
    请重做"。source.pptx 缺失 → 409；蒸馏失败（ValueError → 400 / 其他 → 500）
    不动存量 brand。palette.colors（dk1 缺失）/logo candidates 这类无人工订正
    通路的字段，redistill 仍是唯一修复面。
    """
    from deerflow.runtime.user_context import get_effective_user_id

    user_id = get_effective_user_id()
    store = _get_store()
    rec = _require_record(store, asset_id, user_id)
    asset_dir = store.files_dir(asset_id)
    source = asset_dir / "source.pptx"
    if not source.is_file():
        raise HTTPException(status_code=409, detail="源文件已缺失，无法重新蒸馏（请删除后重新上传）")
    old_brand = rec.to_dict().get("brand") or {}
    try:
        brand = distill_pptx(source, asset_dir)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        logger.warning("brand template parse failed: %s", exc)
        raise HTTPException(status_code=500, detail="模板解析失败（原因见服务端日志）") from exc

    # E1 逐字段合并：仅当旧 brand 带新版标记（PUT 已维护 _confirmed）才保留；
    # 存量无键 = 全量刷新 + 响应明示。
    legacy_asset = "_confirmed" not in old_brand
    confirmed = [k for k in (old_brand.get("_confirmed") or []) if isinstance(k, str)]
    if not legacy_asset:
        for field in confirmed:
            _restore_confirmed_field(brand, old_brand, field)
        brand["_confirmed"] = confirmed

    (asset_dir / "trainer_brand.json").write_text(json.dumps(brand, ensure_ascii=False, indent=2), encoding="utf-8")
    rec = store.update(asset_id, user_id, brand=brand)
    diff = _brand_diff(old_brand, brand)
    if not legacy_asset:
        diff["preserved_user_confirmed"] = confirmed
    else:
        diff["legacy_overwrite_warning"] = "本次覆盖可能包含历史人工订正，请重做（保存后重新确认主色/字体/主题）"
    return {"asset": rec.to_dict(), "brand_diff": diff}


def _restore_confirmed_field(brand: dict, old_brand: dict, field: str) -> None:
    """把用户确认过的字段值从旧 brand 恢复进新蒸馏 brand（E1）。"""
    if field == "font_family":
        if "font_family" in old_brand:
            brand["font_family"] = old_brand["font_family"]
    elif field == "palette.primary":
        old_value = (old_brand.get("palette") or {}).get("primary")
        if old_value is not None:
            palette = dict(brand.get("palette") or {})
            palette["primary"] = old_value
            brand["palette"] = palette
    elif field == "style_tendency.remotion_theme":
        old_value = (old_brand.get("style_tendency") or {}).get("remotion_theme")
        if old_value is not None:
            tendency = dict(brand.get("style_tendency") or {})
            tendency["remotion_theme"] = old_value
            # 保留字段即用户确认语义（与 PUT 写入时同款标记）
            tendency["reason"] = "用户确认"
            brand["style_tendency"] = tendency


def _brand_diff(old: dict, new: dict) -> dict:
    """顶层字段级 diff（changed/before/after）——redistill 合并语义的明示契约。

    E0（2026-09-23）：``_confirmed`` 是元数据不是品牌要素，计算 diff 时排除，
    避免它成为 changed 噪声。
    """
    keys = (set(old) | set(new)) - {"_confirmed"}
    changed = sorted(k for k in keys if old.get(k) != new.get(k))
    return {"changed": changed, "before": {k: old.get(k) for k in changed}, "after": {k: new.get(k) for k in changed}}


@router.delete("/ppt-templates/{asset_id}")
async def delete_ppt_template(request: Request, asset_id: str):
    from deerflow.runtime.user_context import get_effective_user_id

    store = _get_store()
    user_id = get_effective_user_id()
    _require_record(store, asset_id, user_id)
    store.delete(asset_id, user_id)
    return {"deleted": asset_id}


@router.get("/ppt-templates/{asset_id}/logo")
async def get_ppt_template_logo(request: Request, asset_id: str):
    """提取出的 logo 原图（表单/列表预览用）。"""
    from deerflow.runtime.user_context import get_effective_user_id

    store = _get_store()
    rec = _require_record(store, asset_id, get_effective_user_id())
    logo = (rec.to_dict().get("brand") or {}).get("logo") or {}
    filename = logo.get("filename")
    if not filename:
        raise HTTPException(status_code=404, detail="No logo")
    path = store.files_dir(asset_id) / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="No logo file")
    suffix = path.suffix.lower()
    media = {
        ".svg": "image/svg+xml",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".gif": "image/gif",
    }.get(suffix, "application/octet-stream")
    headers = {"Content-Disposition": "attachment; filename=logo.svg"} if suffix == ".svg" else None
    return FileResponse(str(path), media_type=media, headers=headers)


@router.post("/ppt-templates/{asset_id}/apply-to-project")
async def apply_ppt_template_to_project(request: Request, asset_id: str, body: ApplyToProjectBody):
    """「应用到课程项目」：trainer_brand → CourseProject 品牌包字段 + logo 拷贝。

    复用 video_gen 的 CourseProjectStore 更新通路与 brand_assets 目录语义
    （logo_path 相对路径，remotion_course props 构建时物化）。WMF/EMF 等
    CourseProject 不接受的 logo 格式跳过拷贝并在返回中说明（其余字段照常）。
    """
    from deerflow.runtime.user_context import get_effective_user_id

    user_id = get_effective_user_id()
    store = _get_store()
    rec = _require_record(store, asset_id, user_id)
    brand = rec.to_dict().get("brand") or {}

    svc = getattr(request.app.state, "video_gen_service", None)
    if svc is None:
        raise HTTPException(status_code=503, detail="Video generation service not available")
    from app.gateway.video_gen.service import _is_safe_course_project_id

    # 条目 5（2026-09-12 缺陷批）：project_id 复用仓内白名单 ^cp_[0-9a-f]{12}$
    # （service.py _COURSE_PROJECT_ID_RE，7 处复用的同款穿越防护）——修前裸 str
    # 直接拼进 brand_assets/ 路径，"../{own_user}/{own_cp_id}" 自我穿越即可逃逸。
    # 不合规按 404（与未知 id 同口径，不暴露校验语义）。
    if not _is_safe_course_project_id(body.project_id):
        raise HTTPException(status_code=404, detail="Project not found")
    from app.gateway.video_gen.course_project_store import CourseProjectStore

    project_store = CourseProjectStore(base_dir=svc._storage_dir)
    project = project_store.get(body.project_id, user_id=user_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found")

    updates: dict = {}
    primary = ((brand.get("palette") or {}).get("primary") or "").strip()
    if primary:
        palette = dict(getattr(project, "color_palette", None) or {})
        palette["primary"] = primary
        updates["color_palette"] = palette
    font = (brand.get("font_family") or "").strip()
    if font:
        updates["font_family"] = font
    theme = ((brand.get("style_tendency") or {}).get("remotion_theme") or "").strip()
    if theme:
        updates["remotion_theme"] = theme

    logo_note = None
    logo_filename = ((brand.get("logo") or {}).get("filename") or "").strip()
    if logo_filename:
        src = store.files_dir(asset_id) / logo_filename
        if src.is_file():
            logo_bytes = src.read_bytes()
            try:
                from app.gateway.video_gen.upload_validation import validate_logo_bytes

                suffix = validate_logo_bytes(logo_bytes, max_bytes=5 * 1024 * 1024, label="logo")
                assets_dir = Path(svc._storage_dir) / "brand_assets" / body.project_id
                assets_dir.mkdir(parents=True, exist_ok=True)
                dest = assets_dir / f"logo{suffix}"
                shutil.copyfile(src, dest)
                updates["logo_path"] = f"brand_assets/{body.project_id}/{dest.name}"
            except ValueError as exc:
                logo_note = f"logo 未导入（{exc}）"
        else:
            logo_note = "logo 文件缺失，未导入"

    project = project_store.update(body.project_id, user_id=user_id, **updates)
    return {"project": project.to_dict(), "applied_fields": sorted(updates.keys()), "logo_note": logo_note}
