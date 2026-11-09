import asyncio
import base64
import json
import logging
import os
import re
import tempfile
import uuid
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

import httpx
import yaml
from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field, ValidationError, field_validator

from app.gateway._shared.safe_download import safe_download
from app.gateway.deps import get_checkpointer, get_config, get_current_user_from_request, get_optional_user_from_request
from app.gateway.gallery.prompts import (
    RECIPE_EXTRACTION_SYSTEM_PROMPT,
    RECIPE_EXTRACTION_USER_PROMPT,
    RECIPE_RETRY_SUFFIX,
    format_messages_for_extraction,
    strip_markdown_code_fence,
)
from app.gateway.gallery.schemas import Recipe, ShowcaseConfig
from app.gateway.video_gen.pricing import record_image_cost
from deerflow.config.app_config import AppConfig
from deerflow.models import create_chat_model
from deerflow.runtime import serialize_channel_values_for_api

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/gallery", tags=["gallery"])

# Concurrency Locks to prevent race conditions during read-modify-write sequences
ugc_write_lock = asyncio.Lock()
likes_write_lock = asyncio.Lock()

# Project root is 4 levels up from this file (backend/app/gateway/routers/gallery.py)
PROJECT_ROOT = Path(__file__).resolve().parents[4]
_GALLERY_DIR = PROJECT_ROOT / "skills"
GALLERY_FILE_PATH = _GALLERY_DIR / "gallery-cases.yaml"

# UGC Storage & Likes Paths
DB_DIR = PROJECT_ROOT / "backend" / ".deer-flow" / "gallery"
UGC_FILE_PATH = DB_DIR / "ugc-cases.json"
LIKES_FILE_PATH = DB_DIR / "likes.json"


class VarConfig(BaseModel):
    """Configuration for a single template variable."""

    label: str = Field(..., description="Display label for the form field")
    type: str = Field("string", description="Field type: 'string', 'select' or 'file'")
    required: bool = Field(False, description="Whether this field is required")
    placeholder: str | None = Field(None, description="Placeholder text")
    options: list[str] | None = Field(None, description="Options for 'select' type")
    accept: str | None = Field(None, description="File type filter for 'file' type, e.g. '.pdf,.docx'")
    multiple: bool = Field(False, description="Allow multiple files ('file' type)")


class GalleryCaseResponse(BaseModel):
    id: str = Field(..., description="Unique identifier for the case")
    title: str = Field(..., description="Title of the case")
    skill_name: str | None = Field("", description="The name of the skill this case belongs to")
    skill_description: str = Field("", description="Description of the associated skill")
    category: str = Field(..., description="Category for the case")
    thumbnail: str | None = Field(None, description="URL to the case thumbnail")
    prompt: str = Field("", description="The fallback prompt (used when no template)")
    featured: bool = Field(False, description="Whether this case is featured")
    prompt_template: str | None = Field(None, description="Prompt template with {variable} placeholders")
    default_vars: dict[str, str] | None = Field(None, description="Default values for template variables")
    vars_config: dict[str, VarConfig] | None = Field(None, description="Form field configuration for template variables")

    # UGC / Interaction Extension Fields (with backward compatible defaults)
    source_type: str = Field("official", description="'official' or 'ugc'")
    author_name: str | None = Field(None, description="Author display name (only for UGC)")
    likes: int = Field(0, description="Number of likes")
    liked_by_me: bool = Field(False, description="Whether current user liked this case")
    author_id: str | None = Field(None, description="Author user id (UGC only; needed for ownership UI)")
    editable_by_me: bool = Field(False, description="Server-computed: current user may edit/delete this case")
    created_at: str | None = Field(None, description="ISO timestamp of creation")

    # Recipe & Showcase (Phase 1 additions)
    recipe: dict | None = Field(None, description="Recipe Card data (structured methodology)")
    showcase: dict | None = Field(None, description="Showcase configuration for result display")


class GalleryListResponse(BaseModel):
    cases: list[GalleryCaseResponse]
    total: int


# Single source of truth for gallery categories. The frontend share dialog and
# the gallery filter both fetch this via GET /api/gallery/categories so the
# official YAML, the UGC picker, and validation never drift apart.
ALLOWED_CATEGORIES: list[str] = ["办公效率", "内容创作", "视觉设计", "数据分析", "视频创作"]

# 每个分类的差异化视觉方向——裸标签（"办公效率风格"）出图高度趋同（2026-07-24 封面雷同），
# 这里给模型具体的画面语言。增删分类时必须同步。
CATEGORY_VISUAL_MAP: dict[str, str] = {
    "办公效率": "现代极简办公场景，扁平插画风，蓝白清爽配色，文档与效率工具元素",
    "内容创作": "温暖的创作台场景，杂志感构图，柔和自然光，笔与灵感便签元素",
    "视觉设计": "大胆几何色块与渐变，孟菲斯设计风格，高饱和度，艺术感构图",
    "数据分析": "深色科技背景，发光的数据图表与仪表盘元素，蓝紫渐变，未来感",
    "视频创作": "电影感光影，胶片与镜头元素，暖橙与青蓝对比色调，叙事氛围",
}


def _build_cover_prompt(title: str, category: str, content_hint: str | None) -> str:
    """内容感知封面 prompt：分类给视觉方向，灵感实际内容给画面主题（防千篇一律）。"""
    style = CATEGORY_VISUAL_MAP.get(category, "极简高质感")
    prompt = f"为《{title}》生成一张封面图，16:9，无文字，{style}"
    if content_hint:
        prompt += f"。画面主题体现：{content_hint[:200]}"
    return prompt


class CreateCaseRequest(BaseModel):
    title: str = Field(..., max_length=100, description="Case title")
    prompt: str = Field(..., max_length=8000, description="Prompt content to share")
    category: str = Field(..., description="Category for the case")
    skill_name: str | None = Field(None, description="Optional associated skill name")
    thumbnail: str | None = Field(None, description="Local thumbnail URL (/api/gallery/thumbnails/...) for the case")
    recipe: dict | None = Field(None, description="Optional Recipe Card (structured methodology) extracted from the conversation")

    @field_validator("category")
    @classmethod
    def _category_must_be_allowed(cls, v: str) -> str:
        if v not in ALLOWED_CATEGORIES:
            raise ValueError(f"category must be one of {ALLOWED_CATEGORIES}")
        return v


class UpdateCaseRequest(BaseModel):
    """Fields a case author may edit after publishing. All optional; only provided fields are written."""

    title: str | None = Field(None, max_length=100)
    prompt: str | None = Field(None, max_length=8000)
    category: str | None = None
    thumbnail: str | None = None
    recipe: dict | None = None

    @field_validator("category")
    @classmethod
    def _category_must_be_allowed(cls, v: str | None) -> str | None:
        if v is not None and v not in ALLOWED_CATEGORIES:
            raise ValueError(f"category must be one of {ALLOWED_CATEGORIES}")
        return v


class ExtractRecipeRequest(BaseModel):
    thread_id: str = Field(..., description="Thread ID to extract recipe from")
    title: str = Field(..., max_length=100, description="Case title")
    category: str = Field(..., description="Case category")


class InstantiateRequest(BaseModel):
    vars: dict[str, str] = Field(default_factory=dict, description="Template/recipe variables")
    defer: bool = Field(False, description="Create thread but do NOT start the run (file-upload two-step flow)")


class GallerySettingsResponse(BaseModel):
    enabled: bool = Field(True, description="Whether the gallery is enabled")
    ugc_enabled: bool = Field(True, description="Whether UGC sharing is enabled")


# ---------------------------------------------------------------------------
# Helper functions for Safe Concurrent JSON Reads/Writes
# ---------------------------------------------------------------------------


def _atomic_write_json(file_path: Path, data: list | dict):
    """Safely write data to JSON using Tempfile + Replace to prevent corruption."""
    file_path.parent.mkdir(parents=True, exist_ok=True)
    dir_path = file_path.parent
    with tempfile.NamedTemporaryFile("w", dir=dir_path, delete=False, encoding="utf-8") as tf:
        json.dump(data, tf, ensure_ascii=False, indent=2)
        temp_name = tf.name
    try:
        os.replace(temp_name, file_path)
    except Exception as e:
        logger.error(f"Atomic write replacement failed: {e}")
        if os.path.exists(temp_name):
            os.unlink(temp_name)
        raise


def _compile_template(template: str, vars: dict[str, str]) -> str:
    """Compile a prompt template by replacing {variable} placeholders."""

    def replacer(match: re.Match) -> str:
        key = match.group(1)
        return vars.get(key, match.group(0))

    return re.sub(r"\{(\w+)\}", replacer, template)


def _find_case(case_id: str) -> dict | None:
    """Find a case by ID from all sources (official + UGC)."""
    for case in load_gallery_cases():
        if case.get("id") == case_id:
            return dict(case)
    for case in load_ugc_cases():
        if case.get("id") == case_id:
            return dict(case)
    return None


def _load_ugc_cases_raw() -> list[dict]:
    """Load ALL community cases regardless of status (internal write-path use).

    Write paths MUST use this — round-tripping the filtered load_ugc_cases()
    would silently drop any non-active case on the next write."""
    if not UGC_FILE_PATH.exists():
        return []
    try:
        content = UGC_FILE_PATH.read_text(encoding="utf-8")
        if not content.strip():
            return []
        data = json.loads(content)
        if isinstance(data, list):
            return [c for c in data if isinstance(c, dict)]
        elif isinstance(data, dict) and "cases" in data:
            return [c for c in data["cases"] if isinstance(c, dict)]
        return []
    except Exception as e:
        logger.error(f"Failed to load UGC cases: {e}")
        return []


def load_ugc_cases() -> list[dict]:
    """Load community cases (active only, read paths)."""
    return [c for c in _load_ugc_cases_raw() if c.get("status", "active") == "active"]


def load_likes() -> dict[str, dict[str, str]]:
    """Load like data structured as {case_id: {user_id: timestamp}}."""
    if not LIKES_FILE_PATH.exists():
        return {}
    try:
        content = LIKES_FILE_PATH.read_text(encoding="utf-8")
        if not content.strip():
            return {}
        data = json.loads(content)
        if isinstance(data, dict):
            return data
        return {}
    except Exception as e:
        logger.error(f"Failed to load likes: {e}")
        return {}


@lru_cache(maxsize=1)
def load_gallery_cases() -> list[dict]:
    """Load official gallery cases from the YAML file."""
    if not GALLERY_FILE_PATH.exists():
        logger.warning(f"Gallery cases file not found at {GALLERY_FILE_PATH}")
        return []

    try:
        content = GALLERY_FILE_PATH.read_text(encoding="utf-8")
        data = yaml.safe_load(content)
        if not data or not isinstance(data, dict):
            return []

        cases = data.get("cases", [])
        if not isinstance(cases, list):
            logger.warning("'cases' is not a list in gallery YAML")
            return []

        return cases
    except yaml.YAMLError as e:
        logger.error(f"Failed to parse gallery YAML: {e}")
        return []
    except Exception as e:
        logger.error(f"Unexpected error loading gallery cases: {e}")
        return []


def _mask_author_name(name: str | None) -> str | None:
    """SEC-M23 (批次 2 项 2.11): 掩码公开列表里的作者名（源自邮箱前缀）。

    邮箱 local part 常含真名/工号模式，公开列表匿名可读——保留前 2 字符
    供辨识，其余掩码；≤2 字符全掩。读侧统一掩码（覆盖存量明文行），
    写侧（create_ugc_case）落库前同样掩码。
    """
    if not name:
        return name
    if len(name) <= 2:
        return "*" * len(name)
    return name[:2] + "***"


def _build_case_response(case_data: dict, likes_db: dict, current_user_id: str | None) -> GalleryCaseResponse | None:
    """Build a GalleryCaseResponse with vars_config validation and degradation."""
    if not isinstance(case_data, dict):
        return None

    # Required fields check
    if not case_data.get("id") or not case_data.get("title"):
        return None

    # Validate vars_config: if present, try to parse; on failure degrade to no-template mode
    vars_config_data = case_data.get("vars_config")
    validated_vars_config: dict[str, VarConfig] | None = None
    if vars_config_data and isinstance(vars_config_data, dict):
        try:
            validated_vars_config = {key: VarConfig(**cfg) for key, cfg in vars_config_data.items()}
        except Exception as e:
            logger.warning(f"Invalid vars_config for case {case_data.get('id')}, degrading to no-template mode: {e}")
            validated_vars_config = None
            # Also clear template-related fields on degradation
            case_data = {**case_data, "prompt_template": None, "default_vars": None}

    case_id = case_data["id"]
    case_likes = likes_db.get(case_id, {})
    likes_count = len(case_likes)
    liked_by_me = current_user_id in case_likes if current_user_id else False

    # Parse recipe (with graceful degradation)
    recipe_data = None
    raw_recipe = case_data.get("recipe")
    if raw_recipe and isinstance(raw_recipe, dict):
        try:
            validated_recipe = Recipe(**raw_recipe)
            recipe_data = validated_recipe.model_dump(exclude={"source"})
        except Exception as e:
            logger.warning(f"Invalid recipe for case {case_id}, degrading to None: {e}")
            recipe_data = None

    # Parse showcase (with graceful degradation)
    showcase_data = None
    raw_showcase = case_data.get("showcase")
    if raw_showcase and isinstance(raw_showcase, dict):
        try:
            validated_showcase = ShowcaseConfig(**raw_showcase)
            showcase_data = validated_showcase.model_dump()
        except Exception as e:
            logger.warning(f"Invalid showcase for case {case_id}, degrading to None: {e}")
            showcase_data = None

    # Build response
    try:
        response = GalleryCaseResponse(
            id=case_id,
            title=case_data["title"],
            skill_name=case_data.get("skill_name") or "",
            skill_description=case_data.get("skill_description", ""),
            category=case_data.get("category", ""),
            thumbnail=case_data.get("thumbnail"),
            prompt=case_data.get("prompt", ""),
            featured=case_data.get("featured", False),
            prompt_template=case_data.get("prompt_template"),
            default_vars=case_data.get("default_vars"),
            vars_config=validated_vars_config,
            source_type=case_data.get("source_type", "official"),
            author_name=_mask_author_name(case_data.get("author_name")),
            likes=likes_count,
            liked_by_me=liked_by_me,
            author_id=case_data.get("author_id"),
            editable_by_me=bool(current_user_id and case_data.get("source_type") == "ugc" and case_data.get("author_id") == current_user_id),
            created_at=case_data.get("created_at"),
            recipe=recipe_data,
            showcase=showcase_data,
        )
        return response
    except Exception as e:
        logger.warning(f"Failed to build case response for {case_data.get('id')}: {e}")
        return None


# ---------------------------------------------------------------------------
# API Endpoints
# ---------------------------------------------------------------------------


@router.get("/settings", response_model=GallerySettingsResponse, summary="Get Gallery Settings", description="Retrieve dynamic feature flag settings for the gallery and UGC features.")
async def get_gallery_settings(config: AppConfig = Depends(get_config)) -> GallerySettingsResponse:
    # Read from config.yaml dynamically
    gallery_config = config.model_dump().get("gallery", {})
    if not isinstance(gallery_config, dict):
        gallery_config = {}
    enabled = gallery_config.get("enabled", True)
    ugc_enabled = gallery_config.get("ugc_enabled", True)
    return GallerySettingsResponse(enabled=enabled, ugc_enabled=ugc_enabled)


@router.get("/categories", response_model=None, summary="List allowed gallery categories")
async def get_gallery_categories():
    """Return the single-source-of-truth category whitelist.

    The frontend share dialog and gallery filter both consume this so UGC
    submissions and the official YAML stay aligned.
    """
    return {"categories": list(ALLOWED_CATEGORIES)}


@router.get(
    "/cases",
    response_model=GalleryListResponse,
    summary="List Gallery Cases",
    description="Retrieve cases for the skill inspiration gallery, merging official and UGC sources.",
)
async def list_gallery_cases(
    request: Request,
    category: str | None = Query(None, description="Filter cases by category"),
    skill_name: str | None = Query(None, description="Filter cases by skill_name"),
    featured: bool | None = Query(None, description="Filter featured cases only"),
    liked_only: bool = Query(False, description="Only return cases liked by the current user"),
    config: AppConfig = Depends(get_config),
    current_user=Depends(get_optional_user_from_request),
) -> GalleryListResponse:
    try:
        # Load user context if present (optional auth)
        current_user_id = str(current_user.id) if current_user else None

        # Determine feature flag
        gallery_config = config.model_dump().get("gallery", {})
        gallery_enabled = gallery_config.get("enabled", True) if isinstance(gallery_config, dict) else True

        # Load likes database
        likes_db = load_likes()

        # Resolve the set of cases the current user has liked (for liked_only filtering)
        liked_case_ids: set[str] = set()
        if current_user_id:
            for cid, users in likes_db.items():
                if current_user_id in users:
                    liked_case_ids.add(cid)
        # Anonymous user with liked_only -> nothing to show
        if liked_only and not current_user_id:
            return GalleryListResponse(cases=[], total=0)

        # Accumulate cases
        raw_cases = []

        # 1. Load official cases
        raw_official_cases = load_gallery_cases()
        for c in raw_official_cases:
            # Shallow copy the dict to avoid mutating lru_cache values
            case_copy = dict(c)
            case_copy["source_type"] = "official"
            raw_cases.append(case_copy)

        # 2. Load UGC cases if feature flag enabled
        if gallery_enabled:
            raw_ugc_cases = load_ugc_cases()
            for c in raw_ugc_cases:
                # Defensive copy to prevent mutation issues
                case_copy = dict(c)
                case_copy["source_type"] = "ugc"
                raw_cases.append(case_copy)

        # Build and filter responses
        valid_cases = []
        for case_data in raw_cases:
            case = _build_case_response(case_data, likes_db, current_user_id)
            if case is None:
                continue

            # Filtering
            if category and case.category != category:
                continue
            if skill_name and case.skill_name != skill_name:
                continue
            if featured is not None and case.featured != featured:
                continue
            if liked_only and case.id not in liked_case_ids:
                continue

            valid_cases.append(case)

        # Sort: featured first, then created_at descending (newest first).
        # Fallback timestamp for official cases is empty string so they sort cleanly.
        valid_cases.sort(key=lambda x: (1 if x.featured else 0, x.created_at or ""), reverse=True)

        return GalleryListResponse(cases=valid_cases, total=len(valid_cases))
    except Exception as e:
        logger.error(f"Failed to retrieve gallery cases: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to retrieve gallery cases")


@router.post(
    "/cases",
    response_model=GalleryCaseResponse,
    summary="Create UGC Gallery Case",
    description="Allows authenticated users to share their chat prompt to the inspiration gallery.",
)
async def create_ugc_case(
    request: Request,
    case_req: CreateCaseRequest,
    config: AppConfig = Depends(get_config),
    user=Depends(get_current_user_from_request),
) -> GalleryCaseResponse:
    # 1. Verify feature flag
    gallery_config = config.model_dump().get("gallery", {})
    gallery_enabled = gallery_config.get("enabled", True) if isinstance(gallery_config, dict) else True
    if not gallery_enabled:
        raise HTTPException(status_code=403, detail="Gallery features are currently disabled")

    # 2. Enforce active authentication
    current_user_id = str(user.id)
    # Safe defensive parsing of email for OAuth users that might have null email
    # SEC-M23（批次 2 项 2.11）：落库前掩码（读侧另有一道覆盖存量行）。
    author_name = _mask_author_name((user.email or "").split("@")[0] or "anonymous") or "anonymous"

    # 3. Create Case
    new_case_id = str(uuid.uuid4())
    now_iso = datetime.now(UTC).isoformat()

    # Only allow locally-served thumbnails; reject arbitrary URLs (SSRF / external links).
    thumbnail = case_req.thumbnail if (case_req.thumbnail or "").startswith(_THUMB_URL_PREFIX) else None

    # Validate recipe before persisting: invalid recipes degrade to None rather
    # than polluting the UGC store with unstructured data.
    validated_recipe: dict | None = None
    if case_req.recipe and isinstance(case_req.recipe, dict):
        try:
            validated_recipe = Recipe(**case_req.recipe).model_dump(exclude={"source"})
        except Exception as e:
            logger.warning(f"Invalid recipe on UGC create, degrading to None: {e}")
            validated_recipe = None

    new_case_data = {
        "id": new_case_id,
        "title": case_req.title,
        "prompt": case_req.prompt,
        "category": case_req.category,
        "skill_name": case_req.skill_name,
        "thumbnail": thumbnail,
        "recipe": validated_recipe,
        "author_id": current_user_id,
        "author_name": author_name,
        "source_type": "ugc",
        "created_at": now_iso,
        "status": "active",
        "featured": False,
    }

    # 4. Concurrency-safe Write UGC Cases using asyncio Lock
    async with ugc_write_lock:
        try:
            # 必须用 raw 版（含非 active）——过滤版 load_ugc_cases() 会在写回时
            # 静默丢弃所有非 active 案例（软删除/下架），与 PATCH/DELETE 对齐。
            cases = _load_ugc_cases_raw()
            cases.append(new_case_data)
            _atomic_write_json(UGC_FILE_PATH, cases)
        except Exception as e:
            logger.error(f"Failed to write UGC case: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail="Failed to persist community case data")

    # 5. Build and return response
    likes_db = load_likes()
    response = _build_case_response(new_case_data, likes_db, current_user_id)
    if response is None:
        raise HTTPException(status_code=500, detail="Failed to build case response")

    return response


def _find_owned_ugc_case(cases: list[dict], case_id: str, current_user_id: str) -> dict:
    """Locate a UGC case and enforce ownership. Raises 403/404 with user-safe detail."""
    for case in cases:
        if case.get("id") == case_id:
            if case.get("source_type") != "ugc":
                # 官方案例不在 UGC 文件里——走到这说明调用方传了官方 id，明确拒绝。
                raise HTTPException(status_code=403, detail="Official cases cannot be modified")
            if case.get("author_id") != current_user_id:
                raise HTTPException(status_code=403, detail="Only the author can modify this case")
            return case
    raise HTTPException(status_code=404, detail="Case not found")


@router.patch(
    "/cases/{case_id}",
    response_model=GalleryCaseResponse,
    summary="Update a UGC Gallery Case (author only)",
)
async def update_ugc_case(
    case_id: str,
    request: Request,
    body: UpdateCaseRequest,
    config: AppConfig = Depends(get_config),
    user=Depends(get_current_user_from_request),
) -> GalleryCaseResponse:
    current_user_id = str(user.id)
    async with ugc_write_lock:
        cases = _load_ugc_cases_raw()
        case = _find_owned_ugc_case(cases, case_id, current_user_id)
        if body.title is not None:
            case["title"] = body.title
        if body.prompt is not None:
            case["prompt"] = body.prompt
        if body.category is not None:
            case["category"] = body.category
        if body.thumbnail is not None:
            case["thumbnail"] = body.thumbnail if body.thumbnail.startswith(_THUMB_URL_PREFIX) else None
        if body.recipe is not None:
            try:
                case["recipe"] = Recipe(**body.recipe).model_dump(exclude={"source"})
            except Exception:
                logger.warning(f"Invalid recipe on UGC update for {case_id}, keeping old")
        try:
            _atomic_write_json(UGC_FILE_PATH, cases)
        except Exception as e:
            logger.error(f"Failed to persist UGC update: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail="Failed to persist case update")
    likes_db = load_likes()
    response = _build_case_response(case, likes_db, current_user_id)
    if response is None:
        raise HTTPException(status_code=500, detail="Failed to build case response")
    return response


@router.delete("/cases/{case_id}", summary="Delete a UGC Gallery Case (author only)")
async def delete_ugc_case(
    case_id: str,
    request: Request,
    config: AppConfig = Depends(get_config),
    user=Depends(get_current_user_from_request),
) -> dict:
    current_user_id = str(user.id)
    async with ugc_write_lock:
        cases = _load_ugc_cases_raw()
        _find_owned_ugc_case(cases, case_id, current_user_id)  # 403/404 guards
        remaining = [c for c in cases if c.get("id") != case_id]
        try:
            _atomic_write_json(UGC_FILE_PATH, remaining)
        except Exception as e:
            logger.error(f"Failed to persist UGC delete: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail="Failed to persist case deletion")
        # likes 清理必须持 likes_write_lock——toggle_like 用同一把锁做
        # read-modify-write，不持锁会与其交错产生 lost-update（孤儿 likes 条目
        # 或丢并发点赞）。锁顺序固定 ugc→likes（全仓唯一嵌套点），无反向获取。
        async with likes_write_lock:
            likes = load_likes()
            if case_id in likes:
                likes.pop(case_id)
                _atomic_write_json(LIKES_FILE_PATH, likes)
    return {"deleted": True}


# ---------------------------------------------------------------------------
# UGC thumbnails (upload + AI generate + serve)
# ---------------------------------------------------------------------------

_THUMB_MAX_BYTES = 2 * 1024 * 1024  # 2MB
# Local URL prefix that create_ugc_case trusts for stored thumbnails.
_THUMB_URL_PREFIX = "/api/gallery/thumbnails/"


class GenerateThumbnailRequest(BaseModel):
    title: str = Field(..., max_length=100, description="Case title used to build the cover prompt")
    category: str = Field("", description="Case category used to build the cover prompt")
    prompt: str | None = Field(None, max_length=500, description="Optional extra direction for the cover")


def _thumbnail_dir() -> Path:
    """Thumbnail storage dir (derived from DB_DIR so test monkeypatching works)."""
    return DB_DIR / "thumbnails"


def _detect_image_ext(head: bytes) -> str | None:
    """Detect an allowed image type from magic bytes. SVG is never allowed."""
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if head.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if head.startswith(b"GIF87a") or head.startswith(b"GIF89a"):
        return ".gif"
    if head.startswith(b"RIFF") and head[8:12] == b"WEBP":
        return ".webp"
    return None


def _resolve_image_provider(request: Request):
    """Get the shared AI image provider from app state, or None when unconfigured.

    P1-8: 改用共享 provider（Throttle/断路器/重试/共享 httpx client），
    此前裸 new QihuImageProvider 无包裹、每请求新 client。
    Patchable in tests so generate-thumbnail can be exercised without real keys.
    """
    try:
        vg_svc = getattr(request.app.state, "video_gen_service", None)
        if vg_svc is None:
            return None
        return vg_svc.providers.ai_image
    except Exception:
        logger.exception("Failed to resolve image provider for thumbnail generation")
        return None


async def _persist_thumbnail_from_ref(ref, http_client: httpx.AsyncClient | None = None) -> str:
    """Download/decode an ImageRef.url to the thumbnail dir; return the local URL.

    External URLs are downloaded (so short-lived OSS links don't rot into broken
    images); ``data:`` URLs are base64-decoded. Never returns the raw external URL.
    """
    url = getattr(ref, "url", None)
    if not url:
        raise RuntimeError("image provider returned no url")

    _thumbnail_dir().mkdir(parents=True, exist_ok=True)
    filename = f"{uuid.uuid4().hex}.png"
    dest = _thumbnail_dir() / filename

    if url.startswith("data:"):
        _, _, b64 = url.partition(",")
        dest.write_bytes(base64.b64decode(b64))
    else:
        # C2（2026-10-05 审计 §2-1）：出站下载统一走 SSRF 守卫（私网拒绝/钉 IP/
        # 逐跳复核/体积上限），不再裸 httpx.get。
        content = await safe_download(url, timeout=60.0)
        dest.write_bytes(content)

    return f"{_THUMB_URL_PREFIX}{filename}"


@router.post("/upload-thumbnail", summary="Upload a thumbnail image for a UGC case")
async def upload_thumbnail(
    request: Request,
    file: UploadFile = File(...),
    user=Depends(get_current_user_from_request),
) -> dict:
    raw = await file.read()
    if len(raw) > _THUMB_MAX_BYTES:
        raise HTTPException(status_code=413, detail="Thumbnail too large (max 2MB)")
    ext = _detect_image_ext(raw[:16])
    if ext is None:
        # Rejects SVG (and any non-allowlisted type); ext derived from magic bytes,
        # never the (forgeable) uploaded filename/Content-Type.
        raise HTTPException(status_code=400, detail="Unsupported image type (allowed: PNG/JPEG/GIF/WebP; SVG disallowed)")
    filename = f"{uuid.uuid4().hex}{ext}"
    _thumbnail_dir().mkdir(parents=True, exist_ok=True)
    (_thumbnail_dir() / filename).write_bytes(raw)
    return {"thumbnail": f"{_THUMB_URL_PREFIX}{filename}"}


@router.post("/generate-thumbnail", summary="AI-generate a thumbnail for a UGC case")
async def generate_thumbnail(
    request: Request,
    body: GenerateThumbnailRequest,
    config: AppConfig = Depends(get_config),
    user=Depends(get_current_user_from_request),
) -> dict:
    provider = _resolve_image_provider(request)
    if provider is None:
        raise HTTPException(status_code=500, detail="AI image provider not configured (set qihu_api_key)")
    cover_prompt = _build_cover_prompt(body.title, body.category, body.prompt)
    try:
        ref = await provider.generate_image(cover_prompt, size="1280x720")
        record_image_cost("gallery", provider=getattr(ref, "source", None))  # P1-8: 成本可见
        local_url = await _persist_thumbnail_from_ref(ref)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to generate thumbnail: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to generate thumbnail")
    # P1-8: 不再 close provider--它是共享单例，close 会打挂后续所有 video_gen/writer 请求
    return {"thumbnail": local_url}


@router.get("/thumbnails/{filename}", summary="Serve a UGC thumbnail")
async def serve_thumbnail(filename: str):
    # Guard against path traversal: only a bare filename is allowed.
    if not filename or "/" in filename or "\\" in filename or ".." in filename:
        raise HTTPException(status_code=400, detail="Invalid filename")
    path = _thumbnail_dir() / filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Thumbnail not found")
    return FileResponse(path)


@router.post(
    "/cases/{case_id}/like",
    summary="Toggle Like on a Case",
    description="Likes or unlikes a gallery case using user credentials for strict deduplication.",
)
async def toggle_like_case(
    case_id: str,
    request: Request,
    config: AppConfig = Depends(get_config),
    user=Depends(get_current_user_from_request),
) -> dict:
    # 1. Verify feature flag
    gallery_config = config.model_dump().get("gallery", {})
    gallery_enabled = gallery_config.get("enabled", True) if isinstance(gallery_config, dict) else True
    if not gallery_enabled:
        raise HTTPException(status_code=403, detail="Gallery features are currently disabled")

    # 2. Enforce active authentication
    current_user_id = str(user.id)

    # 3. Load UGC and Official cases to verify valid ID
    official_cases = load_gallery_cases()
    ugc_cases = load_ugc_cases()
    all_case_ids = {c["id"] for c in official_cases} | {c["id"] for c in ugc_cases}

    if case_id not in all_case_ids:
        raise HTTPException(status_code=404, detail="Case not found")

    # 4. Concurrency-safe Toggle Like in likes.json using asyncio Lock
    async with likes_write_lock:
        try:
            likes_db = load_likes()
            case_likes = likes_db.get(case_id, {})

            if current_user_id in case_likes:
                # Unlike
                del case_likes[current_user_id]
                liked = False
            else:
                # Like
                case_likes[current_user_id] = datetime.now(UTC).isoformat()
                liked = True

            if not case_likes:
                if case_id in likes_db:
                    del likes_db[case_id]
            else:
                likes_db[case_id] = case_likes

            _atomic_write_json(LIKES_FILE_PATH, likes_db)

            return {"likes": len(case_likes), "liked_by_me": liked}
        except Exception as e:
            logger.error(f"Failed to toggle like: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail="Failed to persist like interaction")


@router.post(
    "/extract-recipe",
    summary="Extract Recipe from Conversation",
    description="Uses LLM to analyze a thread's conversation history and distill it into a reusable Recipe Card.",
)
async def extract_recipe(
    request: Request,
    req: ExtractRecipeRequest,
    user=Depends(get_current_user_from_request),
    config: AppConfig = Depends(get_config),
) -> dict:
    try:
        # SEC-C1 third entry (security plan 2026-09-02 B1, found by the
        # thread_id-entry cross-check): the body's thread_id was read from the
        # checkpointer with no ownership gate — any authenticated user could
        # distill another user's conversation into a recipe. Same
        # check_access(require_existing=False) semantics as the stateless runs
        # endpoints: other owner → 404; missing row / NULL owner → allowed.
        if user is not None:
            from app.gateway.deps import get_thread_store

            allowed = await get_thread_store(request).check_access(req.thread_id, str(user.id), require_existing=False)
            if not allowed:
                raise HTTPException(status_code=404, detail=f"Thread {req.thread_id} not found")

        # 1. Load conversation history from the persistent checkpointer (same source
        #    the chat UI reads), not the in-memory RunEventStore which is lost on
        #    restart — mirroring threads.py's aget_tuple + serialize_channel_values_for_api.
        checkpointer = get_checkpointer(request)
        config_ckpt = {"configurable": {"thread_id": req.thread_id, "checkpoint_ns": ""}}
        checkpoint_tuple = await checkpointer.aget_tuple(config_ckpt)
        if checkpoint_tuple is None or not checkpoint_tuple.checkpoint:
            raise HTTPException(status_code=404, detail="No messages found in thread")

        channel_values = (checkpoint_tuple.checkpoint or {}).get("channel_values", {})
        messages = serialize_channel_values_for_api(channel_values).get("messages", [])
        if not messages:
            raise HTTPException(status_code=404, detail="No messages found in thread")

        formatted = format_messages_for_extraction(messages)

        # 2. Call LLM for extraction
        model = create_chat_model(
            name=None,
            thinking_enabled=False,
            app_config=config,
        )

        user_prompt = RECIPE_EXTRACTION_USER_PROMPT.format(
            title=req.title,
            category=req.category,
            conversation=formatted,
        )

        # D2: Track LLM calls for accounting
        from app.gateway._shared.llm_usage_sink import record_llm_usage

        _gallery_user_id = str(getattr(user, "id", "") or "")

        # D2: narrow try/except around each ainvoke for error status recording
        try:
            response = await model.ainvoke(
                [
                    SystemMessage(content=RECIPE_EXTRACTION_SYSTEM_PROMPT),
                    HumanMessage(content=user_prompt),
                ],
                config={"run_name": "recipe_extractor"},
            )
        except Exception:
            await record_llm_usage(model_name=None, caller_tag="gallery_recipe", domain="gallery_recipe", status="error", user_id=_gallery_user_id)
            raise

        # 3. Parse JSON output
        raw_text = response.content
        if isinstance(raw_text, list):
            raw_text = "".join(block.get("text", "") for block in raw_text if isinstance(block, dict))

        cleaned = strip_markdown_code_fence(raw_text)

        try:
            recipe_dict = json.loads(cleaned)
        except json.JSONDecodeError:
            # D2: first call consumed tokens but JSON failed → error_downstream
            await record_llm_usage(
                model_name=(rm.get("model_name") or rm.get("model")) if isinstance(rm := getattr(response, "response_metadata", None), dict) else None,
                usage_metadata=getattr(response, "usage_metadata", None),
                response_metadata=getattr(response, "response_metadata", None),
                caller_tag="gallery_recipe",
                domain="gallery_recipe",
                status="error_downstream",
                user_id=_gallery_user_id,
            )
            # Retry once with stricter instruction
            logger.warning(f"Recipe extraction JSON parse failed, retrying for thread {req.thread_id}")
            try:
                retry_response = await model.ainvoke(
                    [
                        SystemMessage(content=RECIPE_EXTRACTION_SYSTEM_PROMPT),
                        HumanMessage(content=user_prompt + RECIPE_RETRY_SUFFIX),
                    ],
                    config={"run_name": "recipe_extractor_retry"},
                )
            except Exception:
                await record_llm_usage(model_name=None, caller_tag="gallery_recipe", domain="gallery_recipe", status="error", user_id=_gallery_user_id)
                raise
            retry_raw = retry_response.content
            if isinstance(retry_raw, list):
                retry_raw = "".join(block.get("text", "") for block in retry_raw if isinstance(block, dict))
            cleaned = strip_markdown_code_fence(retry_raw)
            try:
                recipe_dict = json.loads(cleaned)
            except json.JSONDecodeError:
                # D2: retry call consumed tokens but JSON still failed → error_downstream
                await record_llm_usage(
                    model_name=(rm.get("model_name") or rm.get("model")) if isinstance(rm := getattr(retry_response, "response_metadata", None), dict) else None,
                    usage_metadata=getattr(retry_response, "usage_metadata", None),
                    response_metadata=getattr(retry_response, "response_metadata", None),
                    caller_tag="gallery_recipe",
                    domain="gallery_recipe",
                    status="error_downstream",
                    user_id=_gallery_user_id,
                )
                raise HTTPException(status_code=422, detail="Recipe extraction failed: invalid JSON output")

        # Backfill `title` from the user-provided request when the LLM omits it.
        # The extraction prompt's JSON template does not ask the LLM for a
        # `title` field (the user already typed one in the share dialog), but
        # `Recipe` schema requires it. Without this backfill real LLM runs
        # return 422 "Field required: title" (A1 bug).
        if isinstance(recipe_dict, dict) and not recipe_dict.get("title"):
            recipe_dict["title"] = req.title

        # 4. Validate against schema
        try:
            recipe = Recipe(**recipe_dict)
        except ValidationError as e:
            logger.warning(f"Recipe validation failed: {e}")
            # D2: Recipe validation failed → error_downstream for the last LLM call
            _last_resp = locals().get("retry_response", response)
            await record_llm_usage(
                model_name=(rm.get("model_name") or rm.get("model")) if isinstance(rm := getattr(_last_resp, "response_metadata", None), dict) else None,
                usage_metadata=getattr(_last_resp, "usage_metadata", None),
                response_metadata=getattr(_last_resp, "response_metadata", None),
                caller_tag="gallery_recipe",
                domain="gallery_recipe",
                status="error_downstream",
                user_id=_gallery_user_id,
            )
            raise HTTPException(status_code=422, detail=f"Recipe extraction failed: {e.errors()[0]['msg'] if e.errors() else 'validation error'}")

        # D2: Record successful LLM call(s)
        _last_resp = locals().get("retry_response", response)
        await record_llm_usage(
            model_name=(rm.get("model_name") or rm.get("model")) if isinstance(rm := getattr(_last_resp, "response_metadata", None), dict) else None,
            usage_metadata=getattr(_last_resp, "usage_metadata", None),
            response_metadata=getattr(_last_resp, "response_metadata", None),
            caller_tag="gallery_recipe",
            domain="gallery_recipe",
            status="success",
            user_id=_gallery_user_id,
        )

        return {"recipe": recipe.model_dump(exclude={"source"})}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to extract recipe: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to extract recipe")


def _build_case_messages(case: dict, vars: dict[str, str]) -> list[dict]:
    """Build the thread-seeding messages for a case (recipe guidance + first user
    message, or compiled template, or raw prompt). Extracted from instantiate_case
    so the defer path can reuse the same compilation without starting a run."""
    messages_for_thread: list[dict] = []

    raw_recipe = case.get("recipe")
    if raw_recipe and isinstance(raw_recipe, dict):
        try:
            recipe = Recipe(**raw_recipe)
            case_title = case.get("title", "创作案例")
            total_steps = len(recipe.steps)
            guidance = (
                f"你正在协助用户复刻灵感库案例《{case_title}》。"
                f"该配方共 {total_steps} 步，用户刚完成第 1 步的表单填写并提交。"
                "请在首轮回复中：1) 用一两句确认你理解这个案例的方法论目标；"
                "2) 明确告诉用户接下来你会引导完成剩余步骤，让 TA 知道这是多轮创作。"
            )
            # Recipe mode: ALWAYS inject guidance (+ context_blocks) as a system
            # message, so the AI's first reply names the case + step count and
            # owns the follow-up — the user just landed in a fresh, titleless
            # thread and otherwise has no idea this is a multi-step recipe.
            context_text = "\n".join(recipe.context_blocks)
            system_content = guidance + ("\n\n以下是配方的方法论上下文，请据此调整你的专业水平与引导方式：\n\n" + context_text if context_text else "")
            messages_for_thread.append({"role": "system", "content": system_content})
            # Compile first step template
            if recipe.steps:
                first_step = recipe.steps[0]
                user_msg = _compile_template(first_step.template, vars)
                messages_for_thread.append({"role": "user", "content": user_msg})
        except Exception as e:
            logger.warning(f"Recipe instantiation failed for {case.get('id')}, falling back to template/prompt: {e}")
            messages_for_thread = []

    if not messages_for_thread:
        # Template mode or direct prompt mode
        prompt_template = case.get("prompt_template")
        if prompt_template:
            compiled = _compile_template(prompt_template, vars)
            messages_for_thread.append({"role": "user", "content": compiled})
        else:
            messages_for_thread.append({"role": "user", "content": case.get("prompt", "")})

    return messages_for_thread


@router.post(
    "/cases/{case_id}/instantiate",
    summary="Instantiate a Gallery Case",
    description="Creates a new thread with the case's context injected and first prompt sent.",
)
async def instantiate_case(
    case_id: str,
    request: Request,
    body: InstantiateRequest,
    user=Depends(get_current_user_from_request),
) -> dict:
    from langgraph.checkpoint.base import empty_checkpoint

    from app.gateway.deps import get_checkpointer, get_thread_store
    from app.gateway.routers.thread_runs import RunCreateRequest
    from app.gateway.services import start_run

    # 1. Find the case
    case = _find_case(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="Case not found")

    # 2. Build messages based on case mode
    messages_for_thread = _build_case_messages(case, body.vars)

    # 3. Create new thread
    thread_id = str(uuid.uuid4())
    thread_store = get_thread_store(request)
    checkpointer = get_checkpointer(request)

    try:
        await thread_store.create(
            thread_id,
            metadata={"gallery_case_id": case_id, "title": case.get("title", "")},
        )
        config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
        await checkpointer.aput(
            config,
            empty_checkpoint(),
            {
                "step": -1,
                "source": "input",
                "writes": None,
                "parents": {},
            },
            {},
        )
    except Exception as e:
        logger.error(f"Failed to create thread for case {case_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to create thread")

    # 4. defer：文件类灵感两步流——只建线程，把首条用户消息交还前端预填，
    #    用户在聊天框上传文件后手动发送（2026-07-27，gallery 文件变量）。
    #    system guidance 无从注入（run 未启动）→ 前缀一行案例标识作替代，
    #    AI 首轮仍知道上下文。
    if body.defer:
        compiled_prompt = next(
            (m["content"] for m in messages_for_thread if m.get("role") == "user"),
            "",
        )
        compiled_prompt = f"【灵感库案例《{case.get('title', '')}》】\n{compiled_prompt}"
        return {"thread_id": thread_id, "compiled_prompt": compiled_prompt}

    # 5. Start run with messages
    try:
        run_body = RunCreateRequest(
            input={"messages": messages_for_thread},
            metadata={"gallery_case_id": case_id},
        )
        run_record = await start_run(run_body, thread_id, request)
    except Exception as e:
        logger.error(f"Failed to start run for case {case_id}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to start conversation")

    # 返回 run_id：前端写入 useStream 重连键（lg:stream:{thread_id}），
    # 聊天页 mount 自动 joinStream 接续这个服务端创建的 run
    # （2026-07-31 灵感库「假死」修复——此前 run 在后台跑但前端不可见，重发撞 409）。
    return {"thread_id": thread_id, "run_id": run_record.run_id}
