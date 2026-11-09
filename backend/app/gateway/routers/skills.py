import asyncio
import errno
import json
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.gateway.deps import get_config, get_optional_user_from_request
from app.gateway.path_utils import resolve_thread_virtual_path

# extensions_config.json has two writers: this router and routers/mcp. They
# must share one lock and one writer, otherwise a skill toggle and an MCP
# edit can interleave read-merge-write and drop each other's data.
from app.gateway.routers.mcp import _atomic_write_json
from app.gateway.routers.mcp import _config_write_lock as _mcp_config_write_lock
from deerflow.agents.lead_agent.prompt import refresh_skills_system_prompt_cache_async
from deerflow.config.app_config import AppConfig
from deerflow.config.extensions_config import ExtensionsConfig, SkillStateConfig, get_extensions_config, reload_extensions_config
from deerflow.runtime.user_context import get_effective_user_id
from deerflow.skills import Skill
from deerflow.skills.installer import SkillSecurityScanError
from deerflow.skills.manager import enforce_created_by, extract_created_by
from deerflow.skills.security_scanner import scan_skill_content
from deerflow.skills.storage import get_or_new_user_skill_storage

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["skills"])

# The *same* lock object routers/mcp uses — not a second one. Both endpoints
# read-merge-write extensions_config.json, so separate locks would provide no
# mutual exclusion at all. Aliased under a local name so the sharing is
# visible at the use site.
_config_write_lock = _mcp_config_write_lock


def _user_storage(config: AppConfig | None = None):
    """P1-S3（2026-09-30 上游移植，修 B5）：当前用户的 user-scoped 技能存储。

    custom 技能读写全部落到 ``{base_dir}/users/{user_id}/skills/custom/``；
    public 技能仍从全局根读取（只读）；旧全局 custom 技能以 LEGACY 只读
    兜底出现在 load_skills 的合并视图里（迁移平滑）。
    """
    return get_or_new_user_skill_storage(get_effective_user_id(), **({"app_config": config} if config is not None else {}))


def _load_skills_sync(config: AppConfig):
    """加载当前用户的合并技能视图（public 全局 + 本用户 custom + legacy 兜底）。

    阻塞型文件 IO：一律经 ``asyncio.to_thread`` 调用。
    """
    return _user_storage(config).load_skills(enabled_only=False)


async def _check_skill_ownership(skill: Skill, request: Request | None = None) -> None:
    """Raise 403 if current user is not allowed to modify the skill.

    Uses get_effective_user_id() for consistent user resolution
    across both API routes and agent tools.

    SEC-M22 (批次 2 项 2.9): legacy skills without an owner are admin-only to
    modify — skill content enters EVERY user's system prompt, so a random
    user editing an ownerless skill is a cross-user prompt-injection
    primitive. 前置盘点：无自动化流程以普通用户身份改 legacy skill（seed
    直接落盘，不走本 API）。

    P1-S3：LEGACY 类（旧全局 custom 只读兜底）同按 admin-only 处置。
    """
    category = getattr(skill.category, "value", skill.category)
    if category == "legacy" or not skill.created_by:
        user = None
        if request is not None:
            user = await get_optional_user_from_request(request)
        if user is None or user.system_role != "admin":
            raise HTTPException(status_code=403, detail="Legacy skills without an owner can only be modified by admin")
        return
    user_id = get_effective_user_id()
    if skill.created_by != user_id:
        raise HTTPException(status_code=403, detail="Only the skill creator can edit/delete this skill")


class SkillResponse(BaseModel):
    """Response model for skill information."""

    name: str = Field(..., description="Name of the skill")
    description: str = Field(..., description="Description of what the skill does")
    license: str | None = Field(None, description="License information")
    category: str = Field(..., description="Category of the skill (public or custom)")
    enabled: bool = Field(default=True, description="Whether the skill is enabled")
    created_by: str | None = Field(None, description="User ID of the skill creator")


class SkillsListResponse(BaseModel):
    """Response model for listing all skills."""

    skills: list[SkillResponse]


class SkillUpdateRequest(BaseModel):
    """Request model for updating a skill."""

    enabled: bool = Field(..., description="Whether to enable or disable the skill")


class SkillInstallRequest(BaseModel):
    """Request model for installing a skill from a .skill file."""

    thread_id: str = Field(..., description="The thread ID where the .skill file is located")
    path: str = Field(..., description="Virtual path to the .skill file (e.g., mnt/user-data/outputs/my-skill.skill)")


class SkillInstallResponse(BaseModel):
    """Response model for skill installation."""

    success: bool = Field(..., description="Whether the installation was successful")
    skill_name: str = Field(..., description="Name of the installed skill")
    message: str = Field(..., description="Installation result message")


class CustomSkillContentResponse(SkillResponse):
    content: str = Field(..., description="Raw SKILL.md content")


class CustomSkillUpdateRequest(BaseModel):
    content: str = Field(..., description="Replacement SKILL.md content")


class CustomSkillHistoryResponse(BaseModel):
    history: list[dict]


class SkillRollbackRequest(BaseModel):
    history_index: int = Field(default=-1, description="History entry index to restore from, defaulting to the latest change.")


def _skill_to_response(skill: Skill) -> SkillResponse:
    """Convert a Skill object to a SkillResponse."""
    return SkillResponse(
        name=skill.name,
        description=skill.description,
        license=skill.license,
        category=getattr(skill.category, "value", skill.category),
        enabled=skill.enabled,
        created_by=skill.created_by,
    )


@router.get(
    "/skills",
    response_model=SkillsListResponse,
    summary="List All Skills",
    description="Retrieve a list of all available skills for the current user (global public + user custom + legacy fallback).",
)
async def list_skills(config: AppConfig = Depends(get_config)) -> SkillsListResponse:
    try:
        skills = await asyncio.to_thread(_load_skills_sync, config)
        return SkillsListResponse(skills=[_skill_to_response(skill) for skill in skills])
    except Exception as e:
        logger.error(f"Failed to load skills: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to load skills")


@router.post(
    "/skills/install",
    response_model=SkillInstallResponse,
    summary="Install Skill",
    description="Install a skill from a .skill file (ZIP archive) located in the thread's user-data directory.",
)
async def install_skill(request: SkillInstallRequest, config: AppConfig = Depends(get_config)) -> SkillInstallResponse:
    try:
        skill_file_path = resolve_thread_virtual_path(request.thread_id, request.path)
        user_id = get_effective_user_id()
        storage = _user_storage(config)
        result = await storage.ainstall_skill_from_archive(skill_file_path)

        # created_by 注入（fork 保留语义）：存储层落盘到用户目录后再补盖
        # 户主标记，供列表展示与 SEC-M22 归属闸使用。
        skill_file = storage.get_custom_skill_file(result["skill_name"])

        def _stamp_created_by() -> None:
            if skill_file.exists():
                content = skill_file.read_text(encoding="utf-8")
                skill_file.write_text(enforce_created_by(content, user_id), encoding="utf-8")

        await asyncio.to_thread(_stamp_created_by)
        await refresh_skills_system_prompt_cache_async()
        return SkillInstallResponse(**result)
    except FileNotFoundError as e:
        logger.warning("SEC-M12: skill install source missing: %s", e)
        raise HTTPException(status_code=404, detail="Skill archive not found")
    except SkillSecurityScanError as e:
        logger.warning("SEC-M12/P1-S3: skill install blocked by security scan: %s", e)
        raise HTTPException(status_code=400, detail=str(e))
    except ValueError as e:
        logger.warning("SEC-M12: skill install rejected: %s", e)
        raise HTTPException(status_code=400, detail="Invalid skill install request")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to install skill: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to install skill")


@router.get("/skills/custom", response_model=SkillsListResponse, summary="List Custom Skills")
async def list_custom_skills(config: AppConfig = Depends(get_config)) -> SkillsListResponse:
    try:
        skills = [skill for skill in await asyncio.to_thread(_load_skills_sync, config) if getattr(skill.category, "value", skill.category) in ("custom", "legacy")]
        return SkillsListResponse(skills=[_skill_to_response(skill) for skill in skills])
    except Exception as e:
        logger.error(f"Failed to list custom skills: {e}", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to list custom skills")


@router.get("/skills/custom/{skill_name}", response_model=CustomSkillContentResponse, summary="Get Custom Skill Content")
async def get_custom_skill(skill_name: str, config: AppConfig = Depends(get_config)) -> CustomSkillContentResponse:
    try:
        skill_name = skill_name.replace("\r\n", "").replace("\n", "")
        storage = _user_storage(config)

        def _load_skill_and_content() -> tuple[Skill | None, str | None]:
            skill = next((s for s in storage.load_skills(enabled_only=False) if s.name == skill_name and getattr(s.category, "value", s.category) in ("custom", "legacy")), None)
            if skill is None:
                return None, None
            return skill, storage.read_custom_skill(skill_name)

        skill, content = await asyncio.to_thread(_load_skill_and_content)
        if skill is None:
            raise HTTPException(status_code=404, detail=f"Custom skill '{skill_name}' not found")
        return CustomSkillContentResponse(**_skill_to_response(skill).model_dump(), content=content)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to get custom skill %s: %s", skill_name, e, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get custom skill")


@router.put("/skills/custom/{skill_name}", response_model=CustomSkillContentResponse, summary="Edit Custom Skill")
async def update_custom_skill(skill_name: str, request: CustomSkillUpdateRequest, http_request: Request, config: AppConfig = Depends(get_config)) -> CustomSkillContentResponse:
    try:
        skill_name = skill_name.replace("\r\n", "").replace("\n", "")
        storage = _user_storage(config)
        await asyncio.to_thread(storage.ensure_custom_skill_is_editable, skill_name)
        # Ownership check: only creator can edit
        skill = next((s for s in await asyncio.to_thread(_load_skills_sync, config) if s.name == skill_name and getattr(s.category, "value", s.category) in ("custom", "legacy")), None)
        if skill:
            await _check_skill_ownership(skill, http_request)
        await asyncio.to_thread(storage.validate_skill_markdown_content, skill_name, request.content)
        scan = await scan_skill_content(request.content, executable=False, location=f"{skill_name}/SKILL.md", app_config=config)
        if scan.decision == "block":
            raise HTTPException(status_code=400, detail=f"Security scan blocked the edit: {scan.reason}")
        # After validation, enforce original created_by
        skill_file = storage.get_custom_skill_file(skill_name)

        def _write_skill_update() -> str:
            prev_content = skill_file.read_text(encoding="utf-8")
            new_content = enforce_created_by(request.content, extract_created_by(prev_content))
            storage.write_custom_skill(skill_name, "SKILL.md", new_content)
            storage.append_history(
                skill_name,
                {
                    "action": "human_edit",
                    "author": "human",
                    "thread_id": None,
                    "file_path": "SKILL.md",
                    "prev_content": prev_content,
                    "new_content": new_content,
                    "scanner": {"decision": scan.decision, "reason": scan.reason},
                },
            )
            return new_content

        request.content = await asyncio.to_thread(_write_skill_update)
        await refresh_skills_system_prompt_cache_async()
        return await get_custom_skill(skill_name, config)
    except HTTPException:
        raise
    except FileNotFoundError as e:
        logger.warning("SEC-M12: skill update target missing (%s): %s", skill_name, e)
        raise HTTPException(status_code=404, detail="Skill not found")
    except ValueError as e:
        logger.warning("SEC-M12: skill update rejected (%s): %s", skill_name, e)
        raise HTTPException(status_code=400, detail="Invalid skill update request")
    except Exception as e:
        logger.error("Failed to update custom skill %s: %s", skill_name, e, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to update custom skill")


@router.delete("/skills/custom/{skill_name}", summary="Delete Custom Skill")
async def delete_custom_skill(skill_name: str, http_request: Request, config: AppConfig = Depends(get_config)) -> dict[str, bool]:
    try:
        skill_name = skill_name.replace("\r\n", "").replace("\n", "")
        storage = _user_storage(config)
        await asyncio.to_thread(storage.ensure_custom_skill_is_editable, skill_name)
        # Ownership check: only creator can delete
        skill = next((s for s in await asyncio.to_thread(_load_skills_sync, config) if s.name == skill_name and getattr(s.category, "value", s.category) in ("custom", "legacy")), None)
        if skill:
            await _check_skill_ownership(skill, http_request)

        def _delete_with_history() -> None:
            prev_content = None
            try:
                prev_content = storage.read_custom_skill(skill_name)
            except FileNotFoundError:
                prev_content = None
            try:
                storage.append_history(
                    skill_name,
                    {
                        "action": "human_delete",
                        "author": "human",
                        "thread_id": None,
                        "file_path": "SKILL.md",
                        "prev_content": prev_content,
                        "new_content": None,
                        "scanner": {"decision": "allow", "reason": "Deletion requested."},
                    },
                )
            except OSError as e:
                if not isinstance(e, PermissionError) and e.errno not in {errno.EACCES, errno.EPERM, errno.EROFS}:
                    raise
                logger.warning("Skipping delete history write for custom skill %s due to readonly/permission failure; continuing with skill directory removal: %s", skill_name, e)
            storage.delete_custom_skill(skill_name)

        await asyncio.to_thread(_delete_with_history)
        await refresh_skills_system_prompt_cache_async()
        return {"success": True}
    except HTTPException:
        # SEC-M22 复核修复（2026-09-04）：归属校验的 403/404 必须先于宽
        # except Exception 透传——修前被吞成 500（宽 except 前置 re-raise 惯例，
        # 与 update/rollback 两端点对齐）。
        raise
    except FileNotFoundError as e:
        logger.warning("SEC-M12: skill delete target missing (%s): %s", skill_name, e)
        raise HTTPException(status_code=404, detail="Skill not found")
    except ValueError as e:
        logger.warning("SEC-M12: skill delete rejected (%s): %s", skill_name, e)
        raise HTTPException(status_code=400, detail="Invalid skill delete request")
    except Exception as e:
        logger.error("Failed to delete custom skill %s: %s", skill_name, e, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to delete custom skill")


@router.get("/skills/custom/{skill_name}/history", response_model=CustomSkillHistoryResponse, summary="Get Custom Skill History")
async def get_custom_skill_history(skill_name: str, config: AppConfig = Depends(get_config)) -> CustomSkillHistoryResponse:
    try:
        skill_name = skill_name.replace("\r\n", "").replace("\n", "")
        storage = _user_storage(config)

        def _exists_and_read_history() -> list[dict] | None:
            if not storage.custom_skill_exists(skill_name) and not storage.get_skill_history_file(skill_name).exists():
                return None
            return storage.read_history(skill_name)

        history = await asyncio.to_thread(_exists_and_read_history)
        if history is None:
            raise HTTPException(status_code=404, detail=f"Custom skill '{skill_name}' not found")
        return CustomSkillHistoryResponse(history=history)
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Failed to read history for %s: %s", skill_name, e, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to read history")


@router.post("/skills/custom/{skill_name}/rollback", response_model=CustomSkillContentResponse, summary="Rollback Custom Skill")
async def rollback_custom_skill(skill_name: str, request: SkillRollbackRequest, http_request: Request, config: AppConfig = Depends(get_config)) -> CustomSkillContentResponse:
    try:
        skill_name = skill_name.replace("\r\n", "").replace("\n", "")
        storage = _user_storage(config)

        def _load_rollback_target() -> tuple[bool, Skill | None]:
            exists = storage.custom_skill_exists(skill_name) or storage.get_skill_history_file(skill_name).exists()
            skill = next((s for s in storage.load_skills(enabled_only=False) if s.name == skill_name and getattr(s.category, "value", s.category) in ("custom", "legacy")), None)
            return exists, skill

        target_exists, skill = await asyncio.to_thread(_load_rollback_target)
        if not target_exists:
            raise HTTPException(status_code=404, detail=f"Custom skill '{skill_name}' not found")
        # Ownership check: only creator can rollback
        if skill:
            await _check_skill_ownership(skill, http_request)

        def _read_history_and_current() -> tuple[list[dict], Path, str | None]:
            history = storage.read_history(skill_name)
            skill_file = storage.get_custom_skill_file(skill_name)
            current_content = skill_file.read_text(encoding="utf-8") if skill_file.exists() else None
            return history, skill_file, current_content

        history, skill_file, current_content = await asyncio.to_thread(_read_history_and_current)
        if not history:
            raise HTTPException(status_code=400, detail=f"Custom skill '{skill_name}' has no history")
        record = history[request.history_index]
        target_content = record.get("prev_content")
        if target_content is None:
            raise HTTPException(status_code=400, detail="Selected history entry has no previous content to roll back")
        await asyncio.to_thread(storage.validate_skill_markdown_content, skill_name, target_content)
        scan = await scan_skill_content(target_content, executable=False, location=f"{skill_name}/SKILL.md", app_config=config)
        # After validation, enforce current created_by
        if current_content is not None:
            target_content = enforce_created_by(target_content, extract_created_by(current_content))
        history_entry = {
            "action": "rollback",
            "author": "human",
            "thread_id": None,
            "file_path": "SKILL.md",
            "prev_content": current_content,
            "new_content": target_content,
            "rollback_from_ts": record.get("ts"),
            "scanner": {"decision": scan.decision, "reason": scan.reason},
        }
        if scan.decision == "block":
            await asyncio.to_thread(storage.append_history, skill_name, history_entry)
            raise HTTPException(status_code=400, detail=f"Rollback blocked by security scanner: {scan.reason}")

        def _write_rollback() -> None:
            storage.write_custom_skill(skill_name, "SKILL.md", target_content)
            storage.append_history(skill_name, history_entry)

        await asyncio.to_thread(_write_rollback)
        await refresh_skills_system_prompt_cache_async()
        return await get_custom_skill(skill_name, config)
    except HTTPException:
        raise
    except IndexError:
        raise HTTPException(status_code=400, detail="history_index is out of range")
    except FileNotFoundError as e:
        logger.warning("SEC-M12: skill rollback target missing (%s): %s", skill_name, e)
        raise HTTPException(status_code=404, detail="Skill not found")
    except ValueError as e:
        logger.warning("SEC-M12: skill rollback rejected (%s): %s", skill_name, e)
        raise HTTPException(status_code=400, detail="Invalid skill rollback request")
    except Exception as e:
        logger.error("Failed to roll back custom skill %s: %s", skill_name, e, exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to roll back custom skill")


@router.get(
    "/skills/{skill_name}",
    response_model=SkillResponse,
    summary="Get Skill Details",
    description="Retrieve detailed information about a specific skill by its name.",
)
async def get_skill(skill_name: str, config: AppConfig = Depends(get_config)) -> SkillResponse:
    try:
        skill_name = skill_name.replace("\r\n", "").replace("\n", "")
        skills = await asyncio.to_thread(_load_skills_sync, config)
        skill = next((s for s in skills if s.name == skill_name), None)

        if skill is None:
            raise HTTPException(status_code=404, detail=f"Skill '{skill_name}' not found")

        return _skill_to_response(skill)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get skill {skill_name}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to get skill")


async def _persist_skill_state(skill_name: str, enabled: bool) -> None:
    """Flip one skill's ``enabled`` flag in extensions_config.json.

    Rewriting a shared config file to change one boolean has two traps, and
    the previous implementation hit both.

    **Merge from disk, not from memory.** ``get_extensions_config()`` returns
    an object that has already been through
    ``ExtensionsConfig.resolve_env_variables``, which substitutes ``$VAR``
    placeholders — and replaces *unresolved* ones with empty strings. Building
    ``mcpServers`` from that object and writing it back therefore destroyed
    every placeholder in the file: either baking a live secret into it, or
    silently blanking the variable so that MCP server broke on next launch.
    Re-reading the raw JSON (as ``mcp.update_mcp_configuration`` already does)
    keeps the file's own text authoritative for everything this endpoint does
    not own — including top-level keys neither endpoint models.

    **Write atomically, under the shared lock.** A bare ``open(w)`` truncates
    the live config before writing, so a crash mid-write wipes every
    configured MCP server; and without ``mcp``'s lock, a concurrent
    ``PUT /api/mcp/config`` could interleave with this read-merge-write.
    """
    config_path = ExtensionsConfig.resolve_config_path()
    if config_path is None:
        config_path = Path.cwd().parent / "extensions_config.json"

    with _config_write_lock:
        raw: dict = {}
        if config_path.exists():
            with open(config_path, encoding="utf-8") as f:
                raw = json.load(f)

        # Everything except `skills` is carried over verbatim from disk.
        config_data = {key: value for key, value in raw.items() if key != "skills"}

        # Skill states come from the in-memory view (it knows about skills
        # discovered on disk that the file has never mentioned), with this
        # request's change applied on top. Only booleans cross over, so no
        # resolved secret can ride along.
        extensions_config = get_extensions_config()
        extensions_config.skills[skill_name] = SkillStateConfig(enabled=enabled)
        config_data["skills"] = {name: {"enabled": cfg.enabled} for name, cfg in extensions_config.skills.items()}

        _atomic_write_json(config_path, config_data)


@router.put(
    "/skills/{skill_name}",
    response_model=SkillResponse,
    summary="Update Skill",
    description="Update a skill's enabled status by modifying the extensions_config.json file.",
)
async def update_skill(skill_name: str, request: SkillUpdateRequest, http_request: Request, config: AppConfig = Depends(get_config)) -> SkillResponse:
    try:
        skill_name = skill_name.replace("\r\n", "").replace("\n", "")
        skills = await asyncio.to_thread(_load_skills_sync, config)
        skill = next((s for s in skills if s.name == skill_name), None)

        if skill is None:
            raise HTTPException(status_code=404, detail=f"Skill '{skill_name}' not found")

        # §4.4（2026-09-30，§1.3 新发现）：启停也是写操作——enabled 状态是全局
        # 共享的（extensions_config.json），非 admin 启停无主/他人 skill 会改掉
        # 所有人的视图。对齐 _check_skill_ownership：无主/legacy → admin-only；
        # 他人有主 → 403；自有 → 放行。
        await _check_skill_ownership(skill, http_request)

        # 评审修复（2026-09-30，P0）：启停写入面按类别分流。读取面自 P1-S3 起
        # 就是 per-user AND 全局（load_skills / _is_disabled_skill_path 都先读
        # per-user _skill_states.json），写入面若仍一律落全局 extensions_config，
        # 用户 A 禁用 CUSTOM/LEGACY 会同步压掉所有用户的 AND 视图。
        #   PUBLIC        → 全局 extensions_config.json（seed 公共技能全员共享）
        #   CUSTOM/LEGACY → 本用户 _skill_states.json（storage.set_skill_enabled_state）
        category = getattr(skill.category, "value", skill.category)
        if category == "public":
            config_path = ExtensionsConfig.resolve_config_path()
            if config_path is None:
                config_path = Path.cwd().parent / "extensions_config.json"
                logger.info(f"No existing extensions config found. Creating new config at: {config_path}")

            await _persist_skill_state(skill_name, request.enabled)

            logger.info(f"Skills configuration updated and saved to: {config_path}")
            reload_extensions_config()
        else:
            await asyncio.to_thread(_user_storage(config).set_skill_enabled_state, skill_name, request.enabled)
            logger.info("Skill %r enabled=%s persisted to per-user _skill_states.json", skill_name, request.enabled)
        await refresh_skills_system_prompt_cache_async()

        skills = await asyncio.to_thread(_load_skills_sync, config)
        updated_skill = next((s for s in skills if s.name == skill_name), None)

        if updated_skill is None:
            raise HTTPException(status_code=500, detail=f"Failed to reload skill '{skill_name}' after update")

        logger.info(f"Skill '{skill_name}' enabled status updated to {request.enabled}")
        return _skill_to_response(updated_skill)

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to update skill {skill_name}: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Failed to update skill")
