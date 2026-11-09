import asyncio
import logging
import os
import threading
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.gateway.auth_middleware import AuthMiddleware
from app.gateway.config import get_gateway_config
from app.gateway.csrf_middleware import CSRFMiddleware
from app.gateway.deps import langgraph_runtime
from app.gateway.routers import (
    agents,
    ai_assist,
    artifacts,
    assets,
    assistants_compat,
    auth,
    channels,
    console,
    content_tasks,
    feedback,
    gallery,
    mcp,
    media_pricing,
    memory,
    model_pricing,
    models,
    runs,
    skills,
    suggestions,
    tasks,
    thread_runs,
    threads,
    uploads,
    user_channels,
    video_gen,
    voice_twin,
    voice_twin_production,
    writer,
)
from deerflow.config import app_config as deerflow_app_config
from deerflow.config.app_config import apply_logging_level

AppConfig = deerflow_app_config.AppConfig
get_app_config = deerflow_app_config.get_app_config

# Default logging; lifespan overrides from config.yaml log_level.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)

logger = logging.getLogger(__name__)

# Upper bound (seconds) each lifespan shutdown hook is allowed to run.
# Bounds worker exit time so uvicorn's reload supervisor does not keep
# firing signals into a worker that is stuck waiting for shutdown cleanup.
_SHUTDOWN_HOOK_TIMEOUT_SECONDS = 5.0


async def _refresh_queue_pending_gauge(reg: object) -> None:
    """On-demand: count pending/composing tasks and update the gauge."""
    try:
        from sqlalchemy import text

        from app.gateway._shared.task_framework.metrics import _parse_key
        from deerflow.persistence.session import get_engine

        # Clear stale gauge values first so completed categories go to zero
        gauge = reg.gauge("task_queue_pending")
        for key in list(gauge.snapshot().keys()):
            gauge.set(0, labels=_parse_key(key))

        engine = get_engine()
        async with engine.connect() as conn:
            rows = await conn.execute(text("SELECT domain, task_type, status, COUNT(*) as cnt FROM unified_tasks WHERE status IN ('pending','composing','running') GROUP BY domain, task_type, status"))
            for row in rows:
                labels = {"domain": row[0], "task_type": row[1], "status": row[2]}
                reg.set("task_queue_pending", row[3], labels=labels)
    except Exception:
        logger.debug("Failed to refresh queue pending gauge", exc_info=True)


async def _ensure_admin_user(app: FastAPI) -> None:
    """Startup hook: handle first boot and migrate orphan threads otherwise.

    After admin creation, migrate orphan threads from the LangGraph
    store (metadata.user_id unset) to the admin account. This is the
    "no-auth → with-auth" upgrade path: users who ran DeerFlow without
    authentication have existing LangGraph thread data that needs an
    owner assigned.
        First boot (no admin exists):
            - Does NOT create any user accounts automatically.
            - The operator must visit ``/setup`` to create the first admin.

    Subsequent boots (admin already exists):
      - Runs the one-time "no-auth → with-auth" orphan thread migration for
        existing LangGraph thread metadata that has no owner_id.

    No SQL persistence migration is needed: the four user_id columns
    (threads_meta, runs, run_events, feedback) only come into existence
    alongside the auth module via create_all, so freshly created tables
    never contain NULL-owner rows.
    """
    from sqlalchemy import select

    from app.gateway.deps import get_local_provider
    from deerflow.persistence.engine import get_session_factory
    from deerflow.persistence.user.model import UserRow

    try:
        provider = get_local_provider()
    except RuntimeError:
        # Auth persistence may not be initialized in some test/boot paths.
        # Skip admin migration work rather than failing gateway startup.
        logger.warning("Auth persistence not ready; skipping admin bootstrap check")
        return

    sf = get_session_factory()
    if sf is None:
        return

    admin_count = await provider.count_admin_users()

    if admin_count == 0:
        logger.info("=" * 60)
        logger.info("  First boot detected — no admin account exists.")
        logger.info("  Visit /setup to complete admin account creation.")
        logger.info("=" * 60)
        return

    # Admin already exists — run orphan thread migration for any
    # LangGraph thread metadata that pre-dates the auth module.
    async with sf() as session:
        stmt = select(UserRow).where(UserRow.system_role == "admin").limit(1)
        row = (await session.execute(stmt)).scalar_one_or_none()

    if row is None:
        return  # Should not happen (admin_count > 0 above), but be safe.

    admin_id = str(row.id)

    # LangGraph store orphan migration — non-fatal.
    # This covers the "no-auth → with-auth" upgrade path for users
    # whose existing LangGraph thread metadata has no user_id set.
    store = getattr(app.state, "store", None)
    if store is not None:
        try:
            migrated = await _migrate_orphaned_threads(store, admin_id)
            if migrated:
                logger.info("Migrated %d orphan LangGraph thread(s) to admin", migrated)
        except Exception:
            logger.exception("LangGraph thread migration failed (non-fatal)")


async def _iter_store_items(store, namespace, *, page_size: int = 500):
    """Paginated async iterator over a LangGraph store namespace.

    Replaces the old hardcoded ``limit=1000`` call with a cursor-style
    loop so that environments with more than one page of orphans do
    not silently lose data. Terminates when a page is empty OR when a
    short page arrives (indicating the last page).
    """
    offset = 0
    while True:
        batch = await store.asearch(namespace, limit=page_size, offset=offset)
        if not batch:
            return
        for item in batch:
            yield item
        if len(batch) < page_size:
            return
        offset += page_size


async def _migrate_orphaned_threads(store, admin_user_id: str) -> int:
    """Migrate LangGraph store threads with no user_id to the given admin.

    Uses cursor pagination so all orphans are migrated regardless of
    count. Returns the number of rows migrated.
    """
    migrated = 0
    async for item in _iter_store_items(store, ("threads",)):
        metadata = item.value.get("metadata", {})
        if not metadata.get("user_id"):
            metadata["user_id"] = admin_user_id
            item.value["metadata"] = metadata
            await store.aput(("threads",), item.key, item.value)
            migrated += 1
    return migrated


async def _warm_tiktoken_encoding_cache(timeout: float = 5.0) -> None:
    """Pre-warm the tiktoken encoding cache without ever aborting startup.

    Runs the sync warm-up in a worker thread with a bounded timeout: the BPE
    data load may hit an unreachable URL in restricted networks. On
    timeout/failure we only warn — token counting falls back to
    character-based estimation until tiktoken loads successfully.
    """
    try:
        from deerflow.agents.memory.prompt import warm_tiktoken_encoding_cache

        warmed = await asyncio.wait_for(asyncio.to_thread(warm_tiktoken_encoding_cache), timeout=timeout)
        if warmed is None:
            logger.info("tiktoken not installed; skipping warm-up (token counting uses character fallback)")
        elif warmed:
            logger.info("tiktoken encoding cache warmed successfully")
        else:
            logger.warning("tiktoken encoding cache warm-up failed; token counting will use character-based fallback until tiktoken loads successfully")
    except TimeoutError:
        logger.warning("tiktoken encoding cache warm-up timed out; token counting will use character-based fallback until tiktoken loads successfully")
    except Exception:
        logger.warning("tiktoken warm-up skipped", exc_info=True)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan handler."""

    # Load config and check necessary environment variables at startup
    try:
        startup_config = get_app_config()
        # Kept on app.state for startup-only consumers (channels, voice_clone,
        # video_gen singletons, apply_logging_level) — per-request config reads
        # go through deps.get_config() which hot-reloads from disk (R-07).
        app.state.config = startup_config
        apply_logging_level(startup_config.log_level)
        logger.info("Configuration loaded successfully")
    except Exception as e:
        error_msg = f"Failed to load configuration during gateway startup: {e}"
        logger.exception(error_msg)
        raise RuntimeError(error_msg) from e
    config = get_gateway_config()
    logger.info(f"Starting API Gateway on {config.host}:{config.port}")

    # Pre-warm the tiktoken encoding cache so the first memory-injection
    # request never blocks on a cold BPE load (warn-only, never aborts boot).
    await _warm_tiktoken_encoding_cache()

    # Remove orphaned upload staging files (*.tmp) left by a hard crash.
    try:
        from deerflow.uploads.manager import cleanup_stale_upload_staging_files

        removed_upload_staging_files = await asyncio.to_thread(cleanup_stale_upload_staging_files)
        if removed_upload_staging_files:
            logger.info("Removed %d stale upload staging file(s)", removed_upload_staging_files)
    except Exception:
        logger.warning("Upload staging file cleanup skipped", exc_info=True)

    # Initialize LangGraph runtime components (StreamBridge, RunManager, checkpointer, store)
    async with langgraph_runtime(app, startup_config):
        logger.info("LangGraph runtime initialised")

        # F1: register the main event loop for fire-and-forget LLM accounting
        # from sync callers (ViMax novel_compressor thread-pool paths).
        from app.gateway._shared.llm_usage_sink import register_main_loop

        register_main_loop(asyncio.get_running_loop())

        # C3: Warm up the media pricing cache so the budget gate has real prices
        # from the first request (not hardcoded fallback constants).
        try:
            from app.gateway.routers.console import _load_media_pricing

            await _load_media_pricing()
            logger.debug("media pricing cache warmed up")
        except Exception:
            logger.debug("media pricing warmup skipped", exc_info=True)

        # Ensure admin user exists (auto-create on first boot)
        # Must run AFTER langgraph_runtime so app.state.store is available for thread migration
        await _ensure_admin_user(app)

        # Start IM channel service if any channels are configured
        try:
            from app.channels.service import start_channel_service

            channel_service = await start_channel_service(app.state.config)
            logger.info("Channel service started: %s", channel_service.get_status())
        except Exception:
            logger.exception("No IM channels configured or channel service failed to start")

        # Initialize UserChannelRegistry for user-configured channels
        try:
            from app.channels.user_registry import init_user_channel_registry
            from deerflow.persistence.engine import get_session_factory

            session_factory = get_session_factory()
            encryption = None
            try:
                from app.gateway.auth.crypto import CredentialEncryption

                encryption = CredentialEncryption()
            except ValueError:
                logger.warning("CREDENTIAL_ENCRYPTION_KEY not set; user channel credentials will not be decrypted on startup")

            registry = await init_user_channel_registry(
                session_factory=session_factory,
                encryption_service=encryption,
            )
            logger.info("UserChannelRegistry initialized: %d active instances", registry.get_status()["total_instances"])
        except Exception:
            logger.exception("Failed to initialize UserChannelRegistry")

        # Obtain session factory early — shared by voice_clone and task framework
        from deerflow.persistence.engine import get_session_factory as _gsf_vc

        _sf_vc = _gsf_vc()

        # Initialize Voice Clone service if enabled
        try:
            vc_cfg_raw = getattr(app.state.config, "voice_clone", None)
            if vc_cfg_raw and (getattr(vc_cfg_raw, "enabled", False) if hasattr(vc_cfg_raw, "enabled") else (isinstance(vc_cfg_raw, dict) and vc_cfg_raw.get("enabled", False))):
                from app.gateway.voice_twin.config import VoiceCloneConfig
                from app.gateway.voice_twin.service import VoiceCloneService

                if isinstance(vc_cfg_raw, dict):
                    vc_config = VoiceCloneConfig(**vc_cfg_raw)
                else:
                    vc_config = VoiceCloneConfig(**vc_cfg_raw.model_dump())

                app.state.voice_clone_service = VoiceCloneService(
                    config=vc_config,
                    session_factory=_sf_vc,
                )
                # 训练巡检 reaper：用户关页后无人轮询，超时 training 行靠它收敛 failed。
                app.state.voice_clone_service.start_training_reaper()
                # 存储清理 reaper（VT-2/R3，2026-09-09）：outputs/temp 过期产物周期回收。
                app.state.voice_clone_service.start_storage_reaper()
                logger.info("Voice clone service initialized")
            else:
                app.state.voice_clone_service = None
        except Exception:
            logger.exception("Failed to initialize voice clone service")
            app.state.voice_clone_service = None

        # Startup honesty check: DOUBAO_TTS_DEFAULT_SPEAKER without the openspeech
        # access key silently degrades every doubao TTS call to the 360 fallback
        # (tts_fallback_voice). Surface it at boot instead of letting users wonder
        # why cloned/doubao voices all sound like the 360 preset.
        if os.environ.get("DOUBAO_TTS_DEFAULT_SPEAKER") and not os.environ.get("VOLCENGINE_VOICE_ACCESS_KEY"):
            logger.warning(
                "DOUBAO_TTS_DEFAULT_SPEAKER=%s set but VOLCENGINE_VOICE_ACCESS_KEY missing — doubao TTS will degrade to 360 preset (check sandbox.environment whitelist)",
                os.environ["DOUBAO_TTS_DEFAULT_SPEAKER"],
            )

        # Initialize shared task framework + Production service
        try:
            from app.gateway._shared.task_framework.repository import TaskRepository

            task_repo = TaskRepository(session_factory=_sf_vc)
            app.state.task_repository = task_repo
        except Exception:
            logger.exception("Failed to initialize task repository")
            task_repo = None
            app.state.task_repository = None

        try:
            vc_svc = getattr(app.state, "voice_clone_service", None)
            if vc_svc is not None and task_repo is not None:
                from app.gateway.voice_twin.production_service import ProductionService

                prod_svc = ProductionService(
                    repo=task_repo,
                    voice_service=vc_svc,
                    storage_dir=getattr(vc_svc.config, "storage_dir", ".deer-flow/voice-twins"),
                )
                recovered = await prod_svc.recover_stale_tasks()
                prod_svc.start_stall_reaper()
                app.state.production_service = prod_svc
                logger.info("Production service initialized (recovered %d stale tasks)", recovered)
            else:
                app.state.production_service = None
        except Exception:
            logger.exception("Failed to initialize production service")
            app.state.production_service = None

        # Initialize WriterDoc service (uses same session factory)
        try:
            from app.gateway.content.writer_service import WriterDocService

            app.state.writer_doc_service = WriterDocService(session_factory=_sf_vc)
            logger.info("Writer doc service initialized")
        except Exception:
            logger.exception("Failed to initialize writer doc service")
            app.state.writer_doc_service = None

        # Initialize AssistService (lightweight LLM, no DB)
        try:
            from app.gateway.content.assist_service import AssistService

            app.state.assist_service = AssistService(concurrency=50)
            logger.info("Assist service initialized")
        except Exception:
            logger.exception("Failed to initialize assist service")
            app.state.assist_service = None

        # Initialize ContentTaskService (PPT gen, inherits BaseTaskService)
        try:
            from app.gateway.content.ppt_service import ContentTaskService

            if task_repo is not None:
                ct_svc = ContentTaskService(repo=task_repo, storage_dir=".deer-flow/content")
                await ct_svc.recover_stale_tasks()
                ct_svc.start_stall_reaper()
                app.state.content_task_service = ct_svc
                logger.info("Content task service initialized")
            else:
                logger.warning("Content task service disabled: task repository not available (DB not configured?)")
                app.state.content_task_service = None
        except Exception:
            logger.exception("Failed to initialize content task service")
            app.state.content_task_service = None

        # Initialize VideoGenService (inherits BaseTaskService)
        try:
            if task_repo is not None:
                vg_cfg_raw = getattr(app.state.config, "video_gen", None)
                if vg_cfg_raw and (getattr(vg_cfg_raw, "enabled", False) if hasattr(vg_cfg_raw, "enabled") else (isinstance(vg_cfg_raw, dict) and vg_cfg_raw.get("enabled", False))):
                    from app.gateway.video_gen.config import VideoGenConfig
                    from app.gateway.video_gen.providers.registry import build_provider_registry
                    from app.gateway.video_gen.service import VideoGenService

                    if isinstance(vg_cfg_raw, dict):
                        vg_config = VideoGenConfig(**vg_cfg_raw)
                    else:
                        vg_config = VideoGenConfig(**vg_cfg_raw.model_dump())

                    providers = build_provider_registry(vg_config)
                    vg_svc = VideoGenService(
                        repo=task_repo,
                        config=vg_config,
                        providers=providers,
                        tts_service=vc_svc,
                        storage_dir=vg_config.storage_dir,
                        app_state=app.state,
                    )
                    await vg_svc.recover_stale_tasks()
                    # B4（2026-09-28 edits 修复批）：regenerate-audio 后台批量随进程
                    # 重启即死——把残留 audio_regen_running 清为失败态并附引导文案。
                    try:
                        await vg_svc.recover_audio_regen_flags()
                    except Exception:
                        logger.warning("recover audio_regen_running flags failed", exc_info=True)
                    # P3（2026-08-24 看门狗活性重构）：video_gen 的 reaper 扫描周期收紧到
                    # 60s（全局默认 300s 会把死亡检测时效拖到 ~7 分钟）。voice_twin/PPT
                    # 保持默认 300s（app.py:372/:408 一带不传 interval）。
                    vg_svc.start_stall_reaper(interval=60)
                    app.state.video_gen_service = vg_svc
                    logger.info("Video gen service initialized")

                    # 阶段 1.8: bind the generate_video + plan_storyboard tool
                    # executors to the service so an agent can plan a blueprint
                    # (plan_storyboard) then render it (generate_video) over the
                    # full multi-engine path (progress via task SSE, status
                    # transitions, retry, cancel+drain on tool timeout).
                    from app.gateway.video_gen.engines.check_status_tool import (
                        check_video_status_tool,
                        wire_check_status_executor,
                    )
                    from app.gateway.video_gen.engines.plan_storyboard_tool import (
                        plan_storyboard_tool,
                        wire_plan_storyboard_executor,
                    )
                    from app.gateway.video_gen.engines.tool import (
                        generate_video_tool,
                        wire_generate_video_executor,
                    )

                    wire_generate_video_executor(generate_video_tool, vg_svc)
                    wire_plan_storyboard_executor(plan_storyboard_tool, vg_svc)
                    wire_check_status_executor(check_video_status_tool, vg_svc)
                    logger.info("generate_video + plan_storyboard + check_video_status tool executors wired to VideoGenService")
                else:
                    app.state.video_gen_service = None
            else:
                app.state.video_gen_service = None
        except Exception:
            logger.exception("Failed to initialize video gen service")
            app.state.video_gen_service = None

        # Seed pre-built agents (course-assistant, creative-studio etc.) into .deer-flow/agents/
        try:
            from app.gateway.seed import seed_agents

            seeded = seed_agents()
            if seeded:
                logger.info("Seeded %d agent(s) from seed/agents/", seeded)
        except Exception:
            logger.exception("Failed to seed agents")

        # Daily checkpoint retention prune (storage plan 2026-09-02 A1):
        # sqlite-only, skips threads with inflight runs, warn-only failures.
        try:
            from app.gateway.checkpoint_prune import start_checkpoint_prune_task

            start_checkpoint_prune_task(app)
        except Exception:
            logger.exception("Failed to start checkpoint prune scheduler")

        yield

        # Stop the daily checkpoint prune scheduler (no-op when never started)
        try:
            from app.gateway.checkpoint_prune import stop_checkpoint_prune_task

            await stop_checkpoint_prune_task(app)
        except Exception:
            logger.exception("Failed to stop checkpoint prune scheduler")

        # Cancel the per-domain stall watchdogs (BaseTaskService background reapers)
        for attr in ("production_service", "content_task_service", "video_gen_service"):
            svc = getattr(app.state, attr, None)
            if svc is not None and hasattr(svc, "stop_stall_reaper"):
                try:
                    await asyncio.wait_for(svc.stop_stall_reaper(), timeout=_SHUTDOWN_HOOK_TIMEOUT_SECONDS)
                except TimeoutError:
                    logger.warning("Stall reaper stop for %s exceeded %.1fs; proceeding.", attr, _SHUTDOWN_HOOK_TIMEOUT_SECONDS)
                except Exception:
                    logger.exception("Failed to stop stall reaper for %s", attr)

        # Voice clone 训练巡检/存储清理 reaper 对称停止（VoiceCloneService 不继承
        # BaseTaskService，不在上面的 stop_stall_reaper 循环覆盖范围内）。
        try:
            vc_svc = getattr(app.state, "voice_clone_service", None)
            if vc_svc is not None and hasattr(vc_svc, "stop_training_reaper"):
                await asyncio.wait_for(vc_svc.stop_training_reaper(), timeout=_SHUTDOWN_HOOK_TIMEOUT_SECONDS)
            if vc_svc is not None and hasattr(vc_svc, "stop_storage_reaper"):
                await asyncio.wait_for(vc_svc.stop_storage_reaper(), timeout=_SHUTDOWN_HOOK_TIMEOUT_SECONDS)
        except TimeoutError:
            logger.warning("Voice clone reaper stop exceeded %.1fs; proceeding.", _SHUTDOWN_HOOK_TIMEOUT_SECONDS)
        except Exception:
            logger.exception("Failed to stop voice clone reapers")

        # Shutdown VideoGen provider clients
        try:
            vg_svc = getattr(app.state, "video_gen_service", None)
            if vg_svc is not None:
                providers = getattr(vg_svc, "_providers", None)
                if providers and hasattr(providers, "close"):
                    await asyncio.wait_for(
                        providers.close(),
                        timeout=_SHUTDOWN_HOOK_TIMEOUT_SECONDS,
                    )
                    logger.info("VideoGen provider cleanup complete")
        except TimeoutError:
            logger.warning(
                "VideoGen provider cleanup exceeded %.1fs; proceeding.",
                _SHUTDOWN_HOOK_TIMEOUT_SECONDS,
            )
        except Exception:
            logger.exception("Failed to close VideoGen providers")

        # Shutdown ffmpeg executor
        try:
            from app.gateway._shared.executors import ffmpeg_executor

            await asyncio.wait_for(
                asyncio.get_running_loop().run_in_executor(None, ffmpeg_executor.shutdown, True),
                timeout=_SHUTDOWN_HOOK_TIMEOUT_SECONDS,
            )
            logger.info("ffmpeg executor shutdown complete")
        except TimeoutError:
            logger.warning(
                "ffmpeg executor shutdown exceeded %.1fs; proceeding.",
                _SHUTDOWN_HOOK_TIMEOUT_SECONDS,
            )
        except Exception:
            logger.exception("Failed to shutdown ffmpeg executor")

        # Shutdown UserChannelRegistry
        try:
            from app.channels.user_registry import shutdown_user_channel_registry

            await asyncio.wait_for(
                shutdown_user_channel_registry(),
                timeout=_SHUTDOWN_HOOK_TIMEOUT_SECONDS,
            )
            logger.info("UserChannelRegistry shutdown complete")
        except TimeoutError:
            logger.warning(
                "UserChannelRegistry shutdown exceeded %.1fs; proceeding with worker exit.",
                _SHUTDOWN_HOOK_TIMEOUT_SECONDS,
            )
        except Exception:
            logger.exception("Failed to shutdown UserChannelRegistry")

        # Stop channel service on shutdown (bounded to prevent worker hang)
        try:
            from app.channels.service import stop_channel_service

            await asyncio.wait_for(
                stop_channel_service(),
                timeout=_SHUTDOWN_HOOK_TIMEOUT_SECONDS,
            )
        except TimeoutError:
            logger.warning(
                "Channel service shutdown exceeded %.1fs; proceeding with worker exit.",
                _SHUTDOWN_HOOK_TIMEOUT_SECONDS,
            )
        except Exception:
            logger.exception("Failed to stop channel service")

        # Flush pending memory updates before the process exits.
        # MemoryUpdateQueue debounces for `memory.debounce_seconds` (30s by
        # default) on a daemon timer thread, which the interpreter kills
        # outright at exit — so without this, every deploy restart silently
        # discards the preferences a user stated in the last half minute.
        #
        # flush() is synchronous and calls the LLM-backed updater inline, so it
        # runs on its own daemon thread rather than the default executor:
        # `asyncio.run`/uvicorn *join* the default executor during shutdown, so
        # a stuck flush there would pin the worker open — exactly the hang this
        # timeout exists to prevent. Only the bounded `Event.wait` goes to the
        # executor, and that always returns within the timeout.
        try:
            from deerflow.agents.memory.queue import get_memory_queue

            flush_done = threading.Event()
            flush_ok = False

            def _flush_memory_queue() -> None:
                nonlocal flush_ok
                try:
                    get_memory_queue().flush()
                    flush_ok = True
                except Exception:
                    logger.exception("Failed to flush memory queue")
                finally:
                    flush_done.set()

            threading.Thread(
                target=_flush_memory_queue,
                name="memory-flush-shutdown",
                daemon=True,
            ).start()

            flushed = await asyncio.get_running_loop().run_in_executor(
                None,
                flush_done.wait,
                _SHUTDOWN_HOOK_TIMEOUT_SECONDS,
            )
            if flushed and flush_ok:
                logger.info("Memory queue flush complete")
            elif not flushed:
                logger.warning(
                    "Memory queue flush exceeded %.1fs; proceeding with worker exit.",
                    _SHUTDOWN_HOOK_TIMEOUT_SECONDS,
                )
        except Exception:
            logger.exception("Failed to flush memory queue")

    logger.info("Shutting down API Gateway")


def create_app() -> FastAPI:
    """Create and configure the FastAPI application.

    Returns:
        Configured FastAPI application instance.
    """
    config = get_gateway_config()
    docs_kwargs = {"docs_url": "/docs", "redoc_url": "/redoc", "openapi_url": "/openapi.json"} if config.enable_docs else {"docs_url": None, "redoc_url": None, "openapi_url": None}

    app = FastAPI(
        title="DeerFlow API Gateway",
        description="""
## DeerFlow API Gateway

API Gateway for DeerFlow - A LangGraph-based AI agent backend with sandbox execution capabilities.

### Features

- **Models Management**: Query and retrieve available AI models
- **MCP Configuration**: Manage Model Context Protocol (MCP) server configurations
- **Memory Management**: Access and manage global memory data for personalized conversations
- **Skills Management**: Query and manage skills and their enabled status
- **Artifacts**: Access thread artifacts and generated files
- **Health Monitoring**: System health check endpoints

### Architecture

LangGraph requests are handled by nginx reverse proxy.
This gateway provides custom endpoints for models, MCP configuration, skills, and artifacts.
        """,
        version="0.1.0",
        lifespan=lifespan,
        **docs_kwargs,
        openapi_tags=[
            {
                "name": "models",
                "description": "Operations for querying available AI models and their configurations",
            },
            {
                "name": "mcp",
                "description": "Manage Model Context Protocol (MCP) server configurations",
            },
            {
                "name": "memory",
                "description": "Access and manage global memory data for personalized conversations",
            },
            {
                "name": "skills",
                "description": "Manage skills and their configurations",
            },
            {
                "name": "artifacts",
                "description": "Access and download thread artifacts and generated files",
            },
            {
                "name": "uploads",
                "description": "Upload and manage user files for threads",
            },
            {
                "name": "threads",
                "description": "Manage DeerFlow thread-local filesystem data",
            },
            {
                "name": "agents",
                "description": "Create and manage custom agents with per-agent config and prompts",
            },
            {
                "name": "suggestions",
                "description": "Generate follow-up question suggestions for conversations",
            },
            {
                "name": "channels",
                "description": "Manage IM channel integrations (Feishu, Slack, Telegram)",
            },
            {
                "name": "assistants-compat",
                "description": "LangGraph Platform-compatible assistants API (stub)",
            },
            {
                "name": "runs",
                "description": "LangGraph Platform-compatible runs lifecycle (create, stream, cancel)",
            },
            {
                "name": "health",
                "description": "Health check and system status endpoints",
            },
        ],
    )

    # Auth: reject unauthenticated requests to non-public paths (fail-closed safety net)
    app.add_middleware(AuthMiddleware)

    # CSRF: Double Submit Cookie pattern for state-changing requests
    app.add_middleware(CSRFMiddleware)

    # CORS: when GATEWAY_CORS_ORIGINS is set (dev without nginx), add CORS middleware.
    # In production, nginx handles CORS and no middleware is needed.
    cors_origins_env = os.environ.get("GATEWAY_CORS_ORIGINS", "")
    if cors_origins_env:
        cors_origins = [o.strip() for o in cors_origins_env.split(",") if o.strip()]
        # Validate: wildcard origin with credentials is a security misconfiguration
        for origin in cors_origins:
            if origin == "*":
                logger.error("GATEWAY_CORS_ORIGINS contains wildcard '*' with allow_credentials=True. This is a security misconfiguration — browsers will reject the response. Use explicit scheme://host:port origins instead.")
                cors_origins = [o for o in cors_origins if o != "*"]
                break
        if cors_origins:
            app.add_middleware(
                CORSMiddleware,
                allow_origins=cors_origins,
                allow_credentials=True,
                allow_methods=["*"],
                allow_headers=["*"],
            )

    # Include routers
    # Models API is mounted at /api/models
    app.include_router(models.router)

    # MCP API is mounted at /api/mcp
    app.include_router(mcp.router)

    # Memory API is mounted at /api/memory
    app.include_router(memory.router)

    # Skills API is mounted at /api/skills
    app.include_router(skills.router)

    # Gallery API is mounted at /api/gallery
    app.include_router(gallery.router)

    # Artifacts API is mounted at /api/threads/{thread_id}/artifacts
    app.include_router(artifacts.router)

    # Uploads API is mounted at /api/threads/{thread_id}/uploads
    app.include_router(uploads.router)

    # Thread cleanup API is mounted at /api/threads/{thread_id}
    app.include_router(threads.router)

    # Agents API is mounted at /api/agents
    app.include_router(agents.router)

    # Suggestions API is mounted at /api/threads/{thread_id}/suggestions
    app.include_router(suggestions.router)

    # Channels API is mounted at /api/channels
    app.include_router(channels.router)

    # Assistants compatibility API (LangGraph Platform stub)
    app.include_router(assistants_compat.router)

    # Auth API is mounted at /api/v1/auth
    app.include_router(auth.router)

    # Feedback API is mounted at /api/threads/{thread_id}/runs/{run_id}/feedback
    app.include_router(feedback.router)

    # Thread Runs API (LangGraph Platform-compatible runs lifecycle)
    app.include_router(thread_runs.router)

    # Stateless Runs API (stream/wait without a pre-existing thread)
    app.include_router(runs.router)

    # User Channel Configuration API (multi-user IM support)
    app.include_router(user_channels.router)

    # Voice Twin API (audio clone + TTS synthesis)
    app.include_router(voice_twin.router)

    # Voice Production API (script-to-audio, ppt-to-video)
    app.include_router(voice_twin_production.router)

    # Writer Document API (Markdown Writer CRUD + Sharing)
    app.include_router(writer.router)
    app.include_router(writer.share_router)

    # Content Tasks API (PPT generation, task polling, download)
    app.include_router(content_tasks.router)
    app.include_router(assets.router)  # 资产中心（Phase 3.5，2026-09-11）

    # AI Assist API (lightweight LLM rewrite)
    app.include_router(ai_assist.router)

    # Video Generation API (stock_footage, ai_visual, remotion)
    app.include_router(video_gen.router)

    # Generic cross-domain task API (framework doc §6.3)
    app.include_router(tasks.router)

    # Operations console API (read-only run history / token spend reporting)
    app.include_router(console.router)

    # Admin model pricing API (writes the prices the console reports on)
    app.include_router(model_pricing.router)
    app.include_router(media_pricing.router)

    # Writer Assets API (image search + AI generation)
    from app.gateway.content.assets_router import router as writer_assets_router

    app.include_router(writer_assets_router)

    @app.get("/health", tags=["health"])
    async def health_check() -> dict:
        """Health check endpoint.

        Returns:
            Service health status information.
        """
        return {"status": "healthy", "service": "deer-flow-gateway"}

    @app.get("/metrics", tags=["monitoring"])
    async def prometheus_metrics() -> str:
        """Prometheus-format metrics endpoint."""
        from starlette.responses import PlainTextResponse

        from app.gateway._shared.task_framework.metrics import metrics as reg

        # Update on-demand gauges before rendering
        await _refresh_queue_pending_gauge(reg)

        # Update circuit breaker gauges
        from app.gateway.video_gen.providers.throttle import snapshot_circuit_gauges

        snapshot_circuit_gauges()

        return PlainTextResponse(reg.render(), media_type="text/plain; version=0.0.4; charset=utf-8")

    return app


# Create app instance for uvicorn
app = create_app()
