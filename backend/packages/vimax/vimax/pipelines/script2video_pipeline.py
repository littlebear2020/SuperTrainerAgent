import asyncio
import json
import os
import shutil
import tempfile
import time
from collections.abc import Callable
from typing import Any, Literal

import yaml
from langchain.chat_models import init_chat_model
from PIL import Image

from vimax._trainerai_llm_accounting import wrap_for_accounting  # VENDOR PATCH (TrainerAI, P26)
from vimax.agents import *
from vimax.agents.character_portraits_generator import generate_portrait_with_gate
from vimax.interfaces import *
from vimax.tools.render_backend import RenderBackend
from vimax.utils.provider_presets import resolve_chat_model_config
from vimax.utils.text import safe_path_component
from vimax.utils.video import concatenate_video_files


def _pipeline_print(quiet: bool, message: str) -> None:
    if not quiet:
        print(message)


def _emit_text_plan_progress(progress, stage: str, message: str, metadata: dict[str, Any] | None = None) -> None:
    if progress is not None:
        progress(stage, message, metadata or {})


def _emit_render_progress(progress, stage: str, message: str, metadata: dict[str, Any] | None = None) -> None:
    if progress is not None:
        progress(stage, message, metadata or {})


def _scoped_progress(progress, **scope):
    if progress is None:
        return None

    def emit(stage: str, message: str, metadata: dict[str, Any] | None = None) -> None:
        payload = dict(scope)
        payload.update(metadata or {})
        _emit_render_progress(progress, stage, message, payload)

    return emit


def _atomic_json_dump(payload, path: str) -> None:  # VENDOR PATCH (TrainerAI, P34): atomic JSON artifact write
    """Write a JSON artifact atomically so an interrupted save cannot poison resume."""
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, temporary_path = tempfile.mkstemp(prefix=f".{os.path.basename(path)}.", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=4)
        os.chmod(temporary_path, 0o644)  # VENDOR PATCH (TrainerAI, P34): mkstemp defaults to 0600
        os.replace(temporary_path, path)
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(temporary_path)
        except FileNotFoundError:
            pass
        raise


def _raise_if_empty_video_paths(video_paths: list[str], output_path: str) -> None:  # VENDOR PATCH (TrainerAI, P31): fail before empty concat
    if not video_paths:
        raise ValueError(f"No video clips available to concatenate for {output_path}")


def _atomic_text_dump(text: str, path: str) -> None:  # VENDOR PATCH (TrainerAI, P37): atomic text artifact write
    """Write a text artifact atomically（``_atomic_json_dump`` 同款文本版）。

    idea2video 的 ``story.txt`` 修前直写——写盘中断留半截文件，resume 见存在即
    跳过 develop_story，故事被静默截断。临时文件 + ``os.replace`` 原子换名。
    """
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, temporary_path = tempfile.mkstemp(prefix=f".{os.path.basename(path)}.", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.chmod(temporary_path, 0o644)
        os.replace(temporary_path, path)
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.unlink(temporary_path)
        except FileNotFoundError:
            pass
        raise


def _write_placeholder_frame(path: str, image_generator) -> None:  # VENDOR PATCH (TrainerAI, P28): aspect-correct first-frame fallback
    from app.gateway.video_gen.engines.vimax._static_frame import resolve_target_size

    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)  # VENDOR PATCH (TrainerAI, P28): tolerate bare filenames
    width, height = resolve_target_size(image_generator)
    Image.new("RGB", (width, height), color=(128, 128, 128)).save(path)


def _write_placeholder_marker(video_path: str) -> None:  # VENDOR PATCH (TrainerAI, P29): mark regenerable static video
    marker_path = f"{video_path}.placeholder"
    with open(marker_path, "w", encoding="utf-8") as handle:
        handle.write("static placeholder\n")


def _write_failed_marker(frame_path: str, reason: str) -> None:  # VENDOR PATCH (TrainerAI, P36): mark degraded frames
    """写 ``<frame>.failed`` 侧车（reason + timestamp）——降级帧 resume 可重生。

    修前首帧兜底只写灰帧不落标记 → resume 被 frame_exists 复用永不重生。标记
    本身写失败不阻断兜底产出（best-effort；丢标记的后果是少一次重生，不是任务失败）。
    """
    try:
        os.makedirs(os.path.dirname(frame_path) or ".", exist_ok=True)
        with open(f"{frame_path}.failed", "w", encoding="utf-8") as handle:
            json.dump({"reason": str(reason)[:240], "timestamp": time.time()}, handle, ensure_ascii=False)
    except OSError:
        pass


def _read_failed_marker_reason(frame_path: str) -> str | None:  # VENDOR PATCH (TrainerAI, P36)
    """读 ``<frame>.failed`` 的 reason；无标记/损坏标记返回 None（按未降级处理）。"""
    try:
        data = json.loads(open(f"{frame_path}.failed", encoding="utf-8").read())
        return str(data.get("reason") or "") if isinstance(data, dict) else None
    except (OSError, ValueError):
        return None


def _clear_failed_frame(frame_path: str) -> bool:  # VENDOR PATCH (TrainerAI, P36): resume regen for degraded frames
    """``.failed`` 侧车在场 → 删侧车与降级帧，返回 True（调用方走重生分支）。"""
    marker_path = f"{frame_path}.failed"
    if not os.path.exists(marker_path):
        return False
    cleared = True
    for victim in (marker_path, frame_path):
        try:
            if os.path.exists(victim):
                os.unlink(victim)
        except OSError:
            cleared = False  # 删除失败仍返回 True：帧即使残留也不再被无条件复用
    return True


def _is_systemic_pipeline_error(error: BaseException) -> bool:  # VENDOR PATCH (TrainerAI, P36): narrow degradation scope
    """系统性错误不占位降级（T2 收口批）。

    OSError（磁盘满/写盘失败——含 to_thread 里的 PIL save）、KeyError（角色注册表
    缺 key 等装配/数据错）、TypeError（vendored↔host 契约破裂）静默降级会产出
    「成功」的灰帧任务且无事件无重生（PR#25 OCR 实锤磁盘满→灰帧）。provider/
    生成类异常（桥接层统一以 RuntimeError/ValueError/TimeoutError 形态冒泡：
    HTTP 4xx/5xx/审核拒绝/解析失败）保持占位降级 + .failed 标记。
    VimaxBudgetExceeded 由调用方在更前方的显式 except 放行，不进本判定。
    """
    return isinstance(error, (OSError, KeyError, TypeError))


async def _gather_cancelling_remaining(tasks):  # VENDOR PATCH (TrainerAI, P28): cancel siblings on fatal gather error
    """Gather render tasks and cancel siblings when a fatal exception escapes.

    Per-shot provider failures are handled inside their tasks. Anything reaching
    this boundary (notably ``VimaxBudgetExceeded``) is fatal: leaving sibling API
    calls running would continue charging after the task has already failed.
    """
    owned_tasks = [asyncio.ensure_future(task) for task in tasks]
    try:
        return await asyncio.gather(*owned_tasks)
    except BaseException:
        for task in owned_tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*owned_tasks, return_exceptions=True)
        raise


class Script2VideoPipeline:

    def __init__(
        self,
        chat_model: str,
        image_generator,
        video_generator,
        working_dir: str,
        character_reference: str | None = None,
        character_sheets: list[dict] | None = None,
    ):
        # Per-instance coordination events; these were once class attributes,
        # which leaked shot/frame state across scenes when one pipeline object
        # (or several) rendered more than one scene.
        self.character_portrait_events = {}
        self.shot_desc_events = {}
        self.frame_events = {}

        self.chat_model = chat_model
        self.image_generator = image_generator
        self.video_generator = video_generator

        self.character_extractor = CharacterExtractor(chat_model=self.chat_model)
        self.character_portraits_generator = CharacterPortraitsGenerator(
            image_generator=self.image_generator,
            character_reference=character_reference,
            character_sheets=character_sheets)
        # VENDOR PATCH (TrainerAI, 2026-10-20 P40)：分镜重试策略按 pipeline 配置。
        # 直接构造（含 idea2video 子 pipeline）默认 3 attempts × 300s + 可任务级重试；
        # engine 仅对 script2video 调 configure_storyboard_retry(2, 420, False)。
        self.storyboard_max_attempts = 3
        self.storyboard_task_retryable: bool | None = True
        self.storyboard_artist = StoryboardArtist(chat_model=self.chat_model, max_attempts=self.storyboard_max_attempts)
        self.camera_image_generator = CameraImageGenerator(chat_model=self.chat_model, image_generator=self.image_generator, video_generator=self.video_generator)
        self.reference_image_selector = ReferenceImageSelector(chat_model=self.chat_model)

        self.working_dir = working_dir
        os.makedirs(self.working_dir, exist_ok=True)

        # ViMax LLM structured-output per-attempt timeout (B1). doubao-seed-2.1-pro
        # routinely needs >150s for a full storyboard / per-shot visual decompose,
        # especially when decompose runs concurrently per shot; the old caller
        # overrides 150/120 caused the 78% TimeoutError. Named attr (not a magic
        # literal) so it is testable + tunable. NOTE: storyboard_artist.py method
        # defaults are already 300 (:184/:218); the bug was the CALLER hardcoding
        # 150/120 here — this attr replaces those overrides.
        self.llm_retry_timeout = 300
        # VENDOR PATCH (TrainerAI, 2026-10-20 P40)：分镜设计单次 attempt deadline
        # 独立于 decompose；本类默认 300 仅作直接构造时的兜底，宿主 engine 对
        # script2video 覆盖为 VIMAX_STORYBOARD_TIMEOUT_S（默认 420；每次 attempt
        # 各自 420s，不是总预算）。
        self.storyboard_timeout = 300

        # Concurrency cap for per-shot visual-description decomposition (B3). The
        # old code asyncio.gather'd ALL shots at once → N concurrent Ark LLM calls
        # competing for quota → each slows down → 78% TimeoutError. 3 keeps
        # throughput while staying under Ark's concurrency ceiling.
        self._decompose_concurrency = 3
        self._decompose_sem = asyncio.Semaphore(self._decompose_concurrency)

    def configure_storyboard_retry(self, *, max_attempts: int, timeout: int, task_retryable: bool | None) -> None:
        """配置分镜 attempt 次数 / 单次 deadline / 任务级可重放性。"""
        if max_attempts < 1:
            raise ValueError("storyboard max_attempts must be >= 1")
        self.storyboard_max_attempts = max_attempts
        self.storyboard_timeout = timeout
        self.storyboard_task_retryable = task_retryable
        self.storyboard_artist.max_attempts = max_attempts


    async def plan_text_artifacts(
        self,
        script: str,
        user_requirement: str,
        style: str,
        characters: list[CharacterInScene] = None,
        progress: Callable[[str, str, dict[str, Any] | None], None] | None = None,
        quiet: bool = False,
    ):
        """Generate only structured text artifacts required before rendering.

        This helper intentionally stops before character portraits, frame generation,
        video generation, and final concatenation so an agent loop can pause for
        user review after narrative planning.
        """
        self.character_portrait_events = {}
        self.shot_desc_events = {}
        self.frame_events = {}

        if characters is None:
            _emit_text_plan_progress(progress, "extract_characters", "Extracting characters from script")
            characters = await self.extract_characters(script=script, quiet=quiet)
        else:
            _emit_text_plan_progress(progress, "extract_characters", "Using provided characters", {"provided": True, "count": len(characters)})
            characters_path = os.path.join(self.working_dir, "characters.json")
            if not os.path.exists(characters_path):
                _atomic_json_dump([character.model_dump() for character in characters], characters_path)  # VENDOR PATCH (TrainerAI, P34)
            for character in characters:
                self.character_portrait_events[character.idx] = asyncio.Event()

        _emit_text_plan_progress(progress, "design_storyboard", "Designing storyboard")
        storyboard = await self.design_storyboard(
            script=script,
            characters=characters,
            user_requirement=user_requirement,
            quiet=quiet,
        )
        _emit_text_plan_progress(progress, "decompose_shots", "Decomposing shot visual descriptions", {"shot_count": len(storyboard)})
        shot_descriptions = await self.decompose_visual_descriptions(
            shot_brief_descriptions=storyboard,
            characters=characters,
            quiet=quiet,
        )
        _emit_text_plan_progress(progress, "construct_camera_tree", "Constructing camera tree", {"shot_count": len(shot_descriptions)})
        camera_tree = await self.construct_camera_tree(
            shot_descriptions=shot_descriptions,
            quiet=quiet,
        )
        return {
            "characters": characters,
            "storyboard": storyboard,
            "shot_descriptions": shot_descriptions,
            "camera_tree": camera_tree,
        }


    @classmethod
    def init_from_config(cls, config_path: str):
        with open(config_path) as f:
            config = yaml.safe_load(f)

        chat_model_args = resolve_chat_model_config(config["chat_model"]["init_args"])
        chat_model = wrap_for_accounting(init_chat_model(**chat_model_args), tag="script2video")
        backend = RenderBackend.from_config(config)

        return cls(
            chat_model=chat_model,
            image_generator=backend.image_generator,
            video_generator=backend.video_generator,
            working_dir=config["working_dir"],
            character_reference=config.get("character_reference"),
            character_sheets=config.get("character_sheets"),
        )

    async def __call__(
        self,
        script: str,
        user_requirement: str,
        style: str,
        characters: list[CharacterInScene] = None,
        character_portraits_registry: dict[str, dict[str, dict[str, str]]] | None = None,
        quiet: bool = False,
        progress: Callable[[str, str, dict[str, Any] | None], None] | None = None,
        global_shot_budget: int | None = None,
    ):
        _emit_render_progress(progress, "render_start", "Starting script2video render")
        self.character_portrait_events = {}
        self.shot_desc_events = {}
        self.frame_events = {}
        if characters is None:
            _emit_render_progress(progress, "extract_characters", "Extracting characters before render")
            characters = await self.extract_characters(script=script, quiet=quiet)

            # characters_path = os.path.join(self.working_dir, "characters.json")
            # if os.path.exists(characters_path):
            #     with open(characters_path, "r", encoding="utf-8") as f:
            #         characters = [CharacterInScene.model_validate(c) for c in json.load(f)]
            #     print(f"🚀 Loaded {len(characters)} characters from existing file.")
            # else:
            #     print(f"🔍 Extracting characters from script...")
            #     characters = await self.extract_characters(script=script)
            #     with open(characters_path, "w", encoding="utf-8") as f:
            #         json.dump([c.model_dump() for c in characters], f, ensure_ascii=False, indent=4)
            #     print(f"☑️ Extracted {len(characters)} characters from script and saved to {characters_path}.")
        else:
            _emit_render_progress(progress, "extract_characters", "Using provided characters for render", {"provided": True, "count": len(characters)})
            for character in characters:
                self.character_portrait_events[character.idx] = asyncio.Event()

        if character_portraits_registry is None:
            character_portraits_registry_path = os.path.join(self.working_dir, "character_portraits_registry.json")
            if os.path.exists(character_portraits_registry_path):
                with open(character_portraits_registry_path, encoding="utf-8") as f:
                    character_portraits_registry = json.load(f)
                print(f"🚀 Loaded {len(character_portraits_registry)} character portraits from existing file.")
                _emit_render_progress(progress, "character_portraits_loaded", "Loaded existing character portraits", {"count": len(character_portraits_registry)})
            else:
                print("🔍 Generating character portraits...")
                _emit_render_progress(progress, "character_portraits_start", "Generating character portraits", {"character_count": len(characters)})
                character_portraits_registry = await self.generate_character_portraits(
                    characters=characters,
                    character_portraits_registry=None,
                    style=style,
                    progress=progress,
                )

                _atomic_json_dump(character_portraits_registry, character_portraits_registry_path)  # VENDOR PATCH (TrainerAI, P34)
                print(f"☑️ Generated {len(character_portraits_registry)} character portraits and saved to {character_portraits_registry_path}.")
                _emit_render_progress(progress, "character_portraits_done", "Character portraits ready", {"count": len(character_portraits_registry)})



        # design shots
        _emit_render_progress(progress, "load_storyboard", "Loading or designing storyboard")
        storyboard = await self.design_storyboard(
            script=script,
            characters=characters,
            user_requirement=user_requirement,
            quiet=quiet,
            progress=progress,
            global_shot_budget=global_shot_budget,
        )
        _emit_render_progress(progress, "storyboard_ready", "Storyboard ready", {"shot_count": len(storyboard)})

        # decompose visual descriptions of shots
        _emit_render_progress(progress, "load_shot_descriptions", "Loading or decomposing shot descriptions", {"shot_count": len(storyboard)})
        shot_descriptions = await self.decompose_visual_descriptions(
            shot_brief_descriptions=storyboard,
            characters=characters,
            quiet=quiet,
        )
        _emit_render_progress(progress, "shot_descriptions_ready", "Shot descriptions ready", {"shot_count": len(shot_descriptions)})

        # construct camera tree
        _emit_render_progress(progress, "load_camera_tree", "Loading or constructing camera tree", {"shot_count": len(shot_descriptions)})
        camera_tree = await self.construct_camera_tree(
            shot_descriptions=shot_descriptions,
            quiet=quiet,
        )
        _emit_render_progress(progress, "camera_tree_ready", "Camera tree ready", {"camera_count": len(camera_tree)})

        priority_shot_idxs = _collect_priority_shot_idxs(camera_tree)
        _emit_render_progress(progress, "frames_start", "Generating frames for cameras", {"camera_count": len(camera_tree), "shot_count": len(shot_descriptions)})
        tasks = [
            self.generate_frames_for_single_camera(
                camera=camera,
                shot_descriptions=shot_descriptions,
                characters=characters,
                character_portraits_registry=character_portraits_registry,
                priority_shot_idxs=priority_shot_idxs,
                progress=progress,
                style=style,
            )
            for camera in camera_tree
        ]

        _emit_render_progress(progress, "video_clips_start", "Generating video clips for shots", {"shot_count": len(shot_descriptions)})
        total_shots = len(shot_descriptions)
        # per-shot progress: each video task wraps with a counter so the percent
        # advances across the i2v band (60→90) instead of stalling flat (the
        # "stuck at 75%" bug). concurrent gather → track done count.
        clip_done_count = 0

        async def _tracked_video_clip(sd):
            nonlocal clip_done_count
            result = await self.generate_video_for_single_shot(
                shot_description=sd,
                progress=progress,
                characters=characters,
                style=style,
                character_portraits_registry=character_portraits_registry,
            )
            clip_done_count += 1
            _emit_render_progress(
                progress,
                "video_clip_done",
                f"Video clip {clip_done_count}/{total_shots} generated",
                {"idx": clip_done_count - 1, "total": total_shots},
            )
            return result

        video_tasks = [_tracked_video_clip(sd) for sd in shot_descriptions]
        tasks.extend(video_tasks)
        await _gather_cancelling_remaining(tasks)  # VENDOR PATCH (TrainerAI, P28): prevent orphan API calls

        final_video_path = os.path.join(self.working_dir, "final_video.mp4")
        if os.path.exists(final_video_path):
            print("🚀 Skipped concatenating videos, already exists.")
            _emit_render_progress(progress, "final_video_exists", "Final video already exists", {"path": final_video_path})
        else:
            print("🎬 Starting concatenating videos...")
            _emit_render_progress(progress, "concat_start", "Concatenating video clips", {"shot_count": len(shot_descriptions)})
            video_paths = [os.path.join(self.working_dir, "shots", f"{shot_description.idx}", "video.mp4") for shot_description in shot_descriptions]
            _raise_if_empty_video_paths(video_paths, final_video_path)  # VENDOR PATCH (TrainerAI, P31)
            concatenate_video_files(video_paths, final_video_path)
            print(f"☑️ Concatenated videos, saved to {final_video_path}.")
            _emit_render_progress(progress, "concat_done", "Final video concatenated", {"path": final_video_path})

        _emit_render_progress(progress, "render_done", "Script2video render complete", {"final_video_path": final_video_path})
        return final_video_path


    async def generate_frames_for_single_camera(
        self,
        camera: Camera,
        shot_descriptions: list[ShotDescription],
        characters: list[CharacterInScene],
        character_portraits_registry: dict[str, dict[str, dict[str, str]]],
        priority_shot_idxs: list[int],
        progress: Callable[[str, str, dict[str, Any] | None], None] | None = None,
        style: str | None = None,
    ):
        # 1. generate the first_frame of the first shot of the camera
        first_shot_idx = camera.active_shot_idxs[0]
        first_shot_ff_path = os.path.join(self.working_dir, "shots", f"{first_shot_idx}", "first_frame.png")
        _emit_render_progress(progress, "camera_frames_start", f"Generating frames for camera {camera.idx}", {"camera_idx": camera.idx, "active_shot_idxs": camera.active_shot_idxs})

        _ff_degraded_cleared = _clear_failed_frame(first_shot_ff_path)  # VENDOR PATCH (TrainerAI, P36): .failed → regen
        if os.path.exists(first_shot_ff_path) and not _ff_degraded_cleared:
            print(f"🚀 Skipped generating first_frame for shot {first_shot_idx}, already exists.")
            self.frame_events[first_shot_idx]["first_frame"].set()
            _emit_render_progress(progress, "frame_exists", f"First frame for shot {first_shot_idx} already exists", {"camera_idx": camera.idx, "shot_idx": first_shot_idx, "frame_type": "first_frame", "path": first_shot_ff_path})

        else:
            if _ff_degraded_cleared:
                # VENDOR PATCH (TrainerAI, P36): 上一轮降级帧（.failed）不复用——删帧重生。
                print(f"♻️ Degraded first_frame for shot {first_shot_idx} detected (.failed marker); regenerating.")
                _emit_render_progress(progress, "frame_degraded_regenerate", f"Regenerating degraded first frame for shot {first_shot_idx}", {"camera_idx": camera.idx, "shot_idx": first_shot_idx, "frame_type": "first_frame"})
            # VENDOR PATCH (TrainerAI, P28): first-frame fallback always sets dependency events.
            from app.gateway.video_gen.engines.vimax.provider_bridge import VimaxBudgetExceeded  # VENDOR PATCH (TrainerAI, P29): lazy host import

            os.makedirs(os.path.dirname(first_shot_ff_path), exist_ok=True)
            try:
                print(f"🖼️ Starting first_frame generation for shot {first_shot_idx}...")
                _emit_render_progress(progress, "frame_start", f"Generating first frame for shot {first_shot_idx}", {"camera_idx": camera.idx, "shot_idx": first_shot_idx, "frame_type": "first_frame"})
                available_image_path_and_text_pairs = []

                for character_idx in shot_descriptions[first_shot_idx].ff_vis_char_idxs:
                    identifier_in_scene = characters[character_idx].identifier_in_scene
                    registry_item = character_portraits_registry[identifier_in_scene]
                    for view, item in registry_item.items():
                        available_image_path_and_text_pairs.append((item["path"], item["description"]))
            
                # generate the first_frame based on the shot_description.ff_desc
                if camera.parent_shot_idx is not None:
                    # generate the first_frame based on the transition video
                    parent_shot_idx = camera.parent_shot_idx
                    await self.frame_events[parent_shot_idx]["first_frame"].wait()
                    parent_shot_ff_path = os.path.join(self.working_dir, "shots", f"{parent_shot_idx}", "first_frame.png")
                    transition_video_path = os.path.join(self.working_dir, "shots", f"{first_shot_idx}", f"transition_video_from_shot_{parent_shot_idx}.mp4")

                    if os.path.exists(transition_video_path):
                        print(f"🚀 Skipped generating transition video for shot {first_shot_idx} from shot {parent_shot_idx}, already exists.")
                        _emit_render_progress(progress, "transition_video_exists", f"Transition video for shot {first_shot_idx} already exists", {"camera_idx": camera.idx, "shot_idx": first_shot_idx, "parent_shot_idx": parent_shot_idx, "path": transition_video_path})
                    else:
                        print(f"🖼️ Starting transition video generation for shot {first_shot_idx} from shot {parent_shot_idx}...")
                        _emit_render_progress(progress, "transition_video_start", f"Generating transition video for shot {first_shot_idx}", {"camera_idx": camera.idx, "shot_idx": first_shot_idx, "parent_shot_idx": parent_shot_idx})
                        transition_video_output = await self.camera_image_generator.generate_transition_video(
                            first_shot_visual_desc=shot_descriptions[parent_shot_idx].visual_desc,
                            second_shot_visual_desc=shot_descriptions[first_shot_idx].visual_desc,
                            first_shot_ff_path=parent_shot_ff_path,
                            progress=_scoped_progress(progress, camera_idx=camera.idx, shot_idx=first_shot_idx, parent_shot_idx=parent_shot_idx, artifact="transition_video"),
                        )
                        await asyncio.to_thread(transition_video_output.save, transition_video_path)  # VENDOR PATCH (TrainerAI, P34)
                        print(f"☑️ Generated transition video for shot {first_shot_idx} from shot {parent_shot_idx}, saved to {transition_video_path}.")
                        _emit_render_progress(progress, "transition_video_done", f"Transition video for shot {first_shot_idx} generated", {"camera_idx": camera.idx, "shot_idx": first_shot_idx, "parent_shot_idx": parent_shot_idx, "path": transition_video_path})

                    new_camera_image_path = os.path.join(self.working_dir, "shots", f"{first_shot_idx}", f"new_camera_{camera.idx}.png")
                    if os.path.exists(new_camera_image_path):
                        print(f"🚀 Skipped generating new camera image for shot {first_shot_idx}, already exists.")
                        _emit_render_progress(progress, "new_camera_image_exists", f"New camera image for shot {first_shot_idx} already exists", {"camera_idx": camera.idx, "shot_idx": first_shot_idx, "path": new_camera_image_path})
                    else:
                        print(f"🖼️ Starting new camera image generation for shot {first_shot_idx}...")
                        _emit_render_progress(progress, "new_camera_image_start", f"Extracting new camera image for shot {first_shot_idx}", {"camera_idx": camera.idx, "shot_idx": first_shot_idx})
                        new_camera_image = self.camera_image_generator.get_new_camera_image(transition_video_path)
                        await asyncio.to_thread(new_camera_image.save, new_camera_image_path)  # VENDOR PATCH (TrainerAI, P34)
                        print(f"☑️ Generated new camera image for shot {first_shot_idx} (not completed), saved to {new_camera_image_path}.")
                        _emit_render_progress(progress, "new_camera_image_done", f"New camera image for shot {first_shot_idx} extracted", {"camera_idx": camera.idx, "shot_idx": first_shot_idx, "path": new_camera_image_path})

                    # Offer the new-camera image regardless of whether it was just
                    # generated or already on disk: when this sat in the else branch
                    # above, resumed runs silently dropped the key composition
                    # reference and produced different frames than fresh runs.
                    available_image_path_and_text_pairs.append(
                        (
                            new_camera_image_path,
                            f"The composition and background are correct but some elements may be wrong. The wrong elements should be replaced.\nWrong elements: {camera.missing_info}.\nYou must select this image as the main reference and replace the characters in the image with the provided character portraits. Don't change the background."
                        )
                    )


                # 如果子镜头缺少信息，则需要选择参考图像生成
                if camera.parent_shot_idx is None or camera.missing_info is not None:
                    ff_selector_output_path = os.path.join(self.working_dir, "shots", f"{first_shot_idx}", "first_frame_selector_output.json")
                    if os.path.exists(ff_selector_output_path):
                        with open(ff_selector_output_path, encoding='utf-8') as f:
                            ff_selector_output = json.load(f)
                        print(f"🚀 Loaded existing reference image selection and prompt for first_frame of shot {first_shot_idx} from {ff_selector_output_path}.")
                        _emit_render_progress(progress, "frame_prompt_exists", f"First frame prompt for shot {first_shot_idx} already exists", {"camera_idx": camera.idx, "shot_idx": first_shot_idx, "frame_type": "first_frame", "path": ff_selector_output_path})
                    else:
                        print(f"🔍 Selecting reference images and generating prompt for first_frame of shot {first_shot_idx}...")
                        _emit_render_progress(progress, "frame_prompt_start", f"Selecting references for first frame of shot {first_shot_idx}", {"camera_idx": camera.idx, "shot_idx": first_shot_idx, "frame_type": "first_frame"})
                        ff_selector_output = await self.reference_image_selector.select_reference_images_and_generate_prompt(
                            available_image_path_and_text_pairs=available_image_path_and_text_pairs,
                            frame_description=shot_descriptions[first_shot_idx].ff_desc
                        )
                        _atomic_json_dump(ff_selector_output, ff_selector_output_path)  # VENDOR PATCH (TrainerAI, P34)

                        print(f"☑️ Selected reference images and generated prompt for first_frame of shot {first_shot_idx}, saved to {ff_selector_output_path}.")
                        _emit_render_progress(progress, "frame_prompt_done", f"Selected references for first frame of shot {first_shot_idx}", {"camera_idx": camera.idx, "shot_idx": first_shot_idx, "frame_type": "first_frame", "path": ff_selector_output_path})

                    reference_image_path_and_text_pairs, prompt = ff_selector_output["reference_image_path_and_text_pairs"], ff_selector_output["text_prompt"]
                    prefix_prompt = ""
                    for i, (image_path, text) in enumerate(reference_image_path_and_text_pairs):
                        prefix_prompt += f"Image {i}: {text}\n"
                    # VENDOR PATCH (TrainerAI) B3: strong identity-consistency directive.
                    # 61/63 shots in vg_cda1b579a220 selected portrait references but
                    # NanoBanana still drifted across shots — add an explicit lock.
                    identity_lock = (
                        "\n[Identity Lock] The character's face, hairstyle, and clothing "
                        "must be EXACTLY consistent with Image 0 (the front portrait). "
                        "Do not change the character's identity, age, or ethnicity. "
                        "角色五官/发型/服装必须与 Image 0 完全一致，不得改变人物身份。\n"
                    )
                    prompt = f"{prefix_prompt}\n{prompt}{identity_lock}"
                    # Anchor the user-selected visual style in the TEXT prompt, not just the
                    # portrait reference images: real run vg_f23954591917 (style="Cartoon")
                    # showed NanoBanana weights the 写实 frame description over cartoon
                    # reference images, so every frame rendered photorealistic even though
                    # the portraits were cartoon. An explicit style line fixes that.
                    prompt = _with_style_prefix(prompt, style)
                    reference_image_paths = [item[0] for item in reference_image_path_and_text_pairs]
                    ff_image: ImageOutput = await self.image_generator.generate_single_image(
                        prompt=prompt,
                        reference_image_paths=reference_image_paths,
                    )
                    await asyncio.to_thread(ff_image.save, first_shot_ff_path)  # VENDOR PATCH (TrainerAI, P34)
                    self.frame_events[first_shot_idx]["first_frame"].set()
                    print(f"☑️ Generated first_frame for shot {first_shot_idx}, saved to {first_shot_ff_path}.")
                    _emit_render_progress(progress, "frame_done", f"Generated first frame for shot {first_shot_idx}", {"camera_idx": camera.idx, "shot_idx": first_shot_idx, "frame_type": "first_frame", "path": first_shot_ff_path})
                else:
                    shutil.copy(new_camera_image_path, first_shot_ff_path)
                    self.frame_events[first_shot_idx]["first_frame"].set()
                    print(f"☑️ Generated first_frame for shot {first_shot_idx}, saved to {first_shot_ff_path}.")
                    _emit_render_progress(progress, "frame_done", f"Generated first frame for shot {first_shot_idx}", {"camera_idx": camera.idx, "shot_idx": first_shot_idx, "frame_type": "first_frame", "path": first_shot_ff_path})
            except VimaxBudgetExceeded:  # VENDOR PATCH (TrainerAI, P29): never wake paid dependents after budget exhaustion
                raise
            except Exception as error:
                if _is_systemic_pipeline_error(error):  # VENDOR PATCH (TrainerAI, P36): 磁盘/装配/契约错不降级
                    raise
                reason = f"{type(error).__name__}: {error}"
                print(f"⚠️ First-frame generation failed for shot {first_shot_idx} ({reason}); using placeholder image.")
                _emit_render_progress(progress, "video_clip_static_fallback", f"First frame for shot {first_shot_idx} fell back to static placeholder", {"shot_idx": first_shot_idx, "reason": reason, "artifact": "first_frame"})
                # VENDOR PATCH (TrainerAI, P28): prefer an already extracted composition over a gray frame.
                new_camera_candidate = os.path.join(self.working_dir, "shots", f"{first_shot_idx}", f"new_camera_{camera.idx}.png")
                if camera.parent_shot_idx is not None and os.path.exists(new_camera_candidate):
                    shutil.copy(new_camera_candidate, first_shot_ff_path)
                else:
                    _write_placeholder_frame(first_shot_ff_path, self.image_generator)  # VENDOR PATCH (TrainerAI, P28)
                _write_failed_marker(first_shot_ff_path, reason)  # VENDOR PATCH (TrainerAI, P36): resume 可重生
                self.frame_events[first_shot_idx]["first_frame"].set()
                print(f"☑️ Generated placeholder first_frame for shot {first_shot_idx}, saved to {first_shot_ff_path}.")
                _emit_render_progress(progress, "frame_done", f"Generated placeholder first frame for shot {first_shot_idx}", {"camera_idx": camera.idx, "shot_idx": first_shot_idx, "frame_type": "first_frame", "path": first_shot_ff_path})


        # 2. generate the following frames of the camera
        priority_tasks = []
        normal_tasks = []

        if shot_descriptions[first_shot_idx].variation_type in ["medium", "large"]:
            task = self.generate_frame_for_single_shot(
                shot_idx=first_shot_idx, 
                frame_type="last_frame", 
                first_shot_ff_path_and_text_pair=(first_shot_ff_path, shot_descriptions[first_shot_idx].ff_desc),
                frame_desc=shot_descriptions[first_shot_idx].lf_desc,
                visible_characters=[characters[idx] for idx in shot_descriptions[first_shot_idx].lf_vis_char_idxs],
                character_portraits_registry=character_portraits_registry,
                progress=progress,
                style=style,
            )
            normal_tasks.append(task)

        for shot_idx in camera.active_shot_idxs[1:]:
            first_frame_task = self.generate_frame_for_single_shot(
                    shot_idx=shot_idx, 
                    frame_type="first_frame", 
                    first_shot_ff_path_and_text_pair=(first_shot_ff_path, shot_descriptions[first_shot_idx].ff_desc),
                    frame_desc=shot_descriptions[shot_idx].ff_desc,
                    visible_characters=[characters[idx] for idx in shot_descriptions[shot_idx].ff_vis_char_idxs],
                    character_portraits_registry=character_portraits_registry,
                    progress=progress,
                    style=style,
                )
            if shot_idx in priority_shot_idxs:
                priority_tasks.append(first_frame_task)
            else:
                normal_tasks.append(first_frame_task)


            if shot_descriptions[shot_idx].variation_type in ["medium", "large"]:
                last_frame_task = self.generate_frame_for_single_shot(
                    shot_idx=shot_idx, 
                    frame_type="last_frame", 
                    first_shot_ff_path_and_text_pair=(first_shot_ff_path, shot_descriptions[first_shot_idx].ff_desc),
                    frame_desc=shot_descriptions[shot_idx].lf_desc,
                    visible_characters=[characters[idx] for idx in shot_descriptions[shot_idx].lf_vis_char_idxs],
                    character_portraits_registry=character_portraits_registry,
                    progress=progress,
                    style=style,
                )
                normal_tasks.append(last_frame_task)


        await asyncio.gather(*priority_tasks)
        await asyncio.gather(*normal_tasks)
        _emit_render_progress(progress, "camera_frames_done", f"Frames for camera {camera.idx} ready", {"camera_idx": camera.idx, "active_shot_idxs": camera.active_shot_idxs})



    async def generate_video_for_single_shot(
        self,
        shot_description: ShotDescription,
        progress: Callable[[str, str, dict[str, Any] | None], None] | None = None,
        # VENDOR PATCH (TrainerAI) P3a/3b: characters/style/registry forwarded
        # from __call__'s closure (see _tracked_video_clip call site).
        characters: list[CharacterInScene] | None = None,
        style: str | None = None,
        character_portraits_registry: dict[str, dict[str, dict[str, str]]] | None = None,
    ):
        video_path = os.path.join(self.working_dir, "shots", f"{shot_description.idx}", "video.mp4")
        placeholder_marker_path = f"{video_path}.placeholder"
        if os.path.exists(video_path) and not os.path.exists(placeholder_marker_path):
            print(f"🚀 Skipped generating video for shot {shot_description.idx}, already exists.")
            _emit_render_progress(progress, "video_clip_exists", f"Video clip for shot {shot_description.idx} already exists", {"shot_idx": shot_description.idx, "path": video_path})
        else:
            _emit_render_progress(progress, "video_clip_waiting_for_frames", f"Waiting for frames before video clip {shot_description.idx}", {"shot_idx": shot_description.idx})
            # VENDOR PATCH (TrainerAI, P29): marked placeholders are regenerable on non-budget resume.
            if os.path.exists(placeholder_marker_path):
                print(f"♻️ Regenerating static placeholder video for shot {shot_description.idx}.")
                _emit_render_progress(progress, "video_clip_placeholder_regenerate", f"Regenerating static placeholder video for shot {shot_description.idx}", {"shot_idx": shot_description.idx})
                os.unlink(placeholder_marker_path)
                if os.path.exists(video_path):
                    os.unlink(video_path)
            await self.frame_events[shot_description.idx]["first_frame"].wait()
            if shot_description.variation_type in ["medium", "large"]:
                await self.frame_events[shot_description.idx]["last_frame"].wait()

            # VENDOR PATCH (TrainerAI, P36): 首帧已降级（.failed 侧车）→ 不拿灰帧喂
            # i2v——灰帧做参考图会「成功」产出废镜（无事件、无留痕、白烧一单）。
            # 直接静态视频兜底 + .placeholder 标记（resume 重生后真镜恢复），事件照发。
            _ff_path = os.path.join(self.working_dir, "shots", f"{shot_description.idx}", "first_frame.png")
            _ff_failed_reason = _read_failed_marker_reason(_ff_path)
            if _ff_failed_reason is not None:
                _reason = f"first frame degraded: {_ff_failed_reason}"[:240]
                print(f"⚠️ Shot {shot_description.idx} first frame is degraded (.failed); writing static placeholder video instead of i2v.")
                _emit_render_progress(progress, "video_clip_static_fallback", f"Video clip for shot {shot_description.idx} fell back to static frame (degraded first frame)", {"shot_idx": shot_description.idx, "reason": _reason, "artifact": "video_clip"})
                _write_placeholder_marker(video_path)  # VENDOR PATCH (TrainerAI, P29): marker precedes placeholder write
                from app.gateway.video_gen.engines.vimax._static_frame import _make_solid_color_video, make_static_video_from_frame, resolve_target_size

                _ph_w, _ph_h = resolve_target_size(self.image_generator)
                if os.path.exists(_ff_path):
                    make_static_video_from_frame(_ff_path, video_path, duration=5.2, width=_ph_w, height=_ph_h)
                else:
                    _make_solid_color_video(video_path, 5.2, width=_ph_w, height=_ph_h)
                print(f"☑️ Generated static placeholder video for shot {shot_description.idx}, saved to {video_path}.")
                _emit_render_progress(progress, "video_clip_done", f"Generated video clip for shot {shot_description.idx}", {"shot_idx": shot_description.idx, "path": video_path})
                return

            # VENDOR PATCH (TrainerAI): delegate prompt+ref construction to the
            # additive helper so this vendored edit stays minimal (rebase-safe).
            from vimax._trainerai_video_prompt import build_anchored_video_prompt_and_refs

            prompt, frame_paths = build_anchored_video_prompt_and_refs(
                shot_description=shot_description,
                working_dir=self.working_dir,
                characters=characters,
                style=style,
                character_portraits_registry=character_portraits_registry,
            )

            print(f"🎬 Starting video generation for shot {shot_description.idx}...")
            _emit_render_progress(progress, "video_clip_start", f"Generating video clip for shot {shot_description.idx}", {"shot_idx": shot_description.idx, "frame_count": len(frame_paths)})
            from app.gateway.video_gen.engines.vimax.provider_bridge import VimaxBudgetExceeded  # VENDOR PATCH (TrainerAI, P29): lazy host import

            # VENDOR PATCH (TrainerAI) B1: per-shot failure → static frame
            # placeholder, not pipeline abort. One shot's happyhorse latent
            # failure must not cascade into a whole-pipeline retry that
            # regenerates 117 first_frames (vg_cda1b579a220).
            try:
                video_output = await self.video_generator.generate_single_video(
                    prompt=prompt,
                    reference_image_paths=frame_paths,
                    progress=_scoped_progress(progress, shot_idx=shot_description.idx, artifact="video_clip"),
                )
                await asyncio.to_thread(video_output.save, video_path)  # VENDOR PATCH (TrainerAI, P34)
            except VimaxBudgetExceeded:  # VENDOR PATCH (TrainerAI, P29): budget failure is terminal
                raise
            except Exception as e:
                if _is_systemic_pipeline_error(e):  # VENDOR PATCH (TrainerAI, P36): 磁盘/装配/契约错不降级
                    raise
                print(f"⚠️ Video gen failed for shot {shot_description.idx} ({type(e).__name__}); writing static frame placeholder")
                reason = f"{type(e).__name__}: {e}"[:240]
                _emit_render_progress(progress, "video_clip_static_fallback", f"Video clip for shot {shot_description.idx} fell back to static frame", {"shot_idx": shot_description.idx, "reason": reason, "error": str(e)[:120], "artifact": "video_clip"})
                _write_placeholder_marker(video_path)  # VENDOR PATCH (TrainerAI, P29): marker precedes placeholder write
                from app.gateway.video_gen.engines.vimax._static_frame import _make_solid_color_video, make_static_video_from_frame, resolve_target_size

                ff_path = os.path.join(self.working_dir, "shots", f"{shot_description.idx}", "first_frame.png")
                _ph_w, _ph_h = resolve_target_size(self.image_generator)
                if os.path.exists(ff_path):
                    # 审查发现 6：首帧 PNG 损坏时该函数内部会走 solid-color 兜底，
                    # 不传宽高则兜底出 1920x1080 污染 9:16 画布。
                    make_static_video_from_frame(
                        ff_path, video_path, duration=5.2, width=_ph_w, height=_ph_h
                    )
                else:
                    # No first_frame either — solid-color mp4 so concat works.
                    # VENDOR PATCH (TrainerAI) NEW-1: 关键字传参 —— helper 的
                    # width/height 是 keyword-only，原位置调用永远传不进去，占位
                    # 恒 1920x1080，与竖屏真镜混拼毁画面。
                    _make_solid_color_video(video_path, 5.2, width=_ph_w, height=_ph_h)
            print(f"☑️ Generated video for shot {shot_description.idx}, saved to {video_path}.")
            _emit_render_progress(progress, "video_clip_done", f"Generated video clip for shot {shot_description.idx}", {"shot_idx": shot_description.idx, "path": video_path})

    async def generate_frame_for_single_shot(
        self,
        shot_idx: int,
        frame_type: Literal["first_frame", "last_frame"],
        first_shot_ff_path_and_text_pair: tuple[str, str],
        frame_desc: str,
        visible_characters: list[CharacterInScene],
        character_portraits_registry: dict[str, dict[str, dict[str, str]]],
        progress: Callable[[str, str, dict[str, Any] | None], None] | None = None,
        style: str | None = None,
    ) -> ImageOutput:

        frame_image_path = os.path.join(self.working_dir, "shots", f"{shot_idx}", f"{frame_type}.png")

        _frame_degraded_cleared = _clear_failed_frame(frame_image_path)  # VENDOR PATCH (TrainerAI, P36): .failed → regen
        if os.path.exists(frame_image_path) and not _frame_degraded_cleared:
            print(f"🚀 Skipped generating {frame_type} for shot {shot_idx}, already exists.")
            _emit_render_progress(progress, "frame_exists", f"{frame_type} for shot {shot_idx} already exists", {"shot_idx": shot_idx, "frame_type": frame_type, "path": frame_image_path})

        else:
            if _frame_degraded_cleared:
                # VENDOR PATCH (TrainerAI, P36): 上一轮降级帧（.failed）不复用——删帧重生。
                print(f"♻️ Degraded {frame_type} for shot {shot_idx} detected (.failed marker); regenerating.")
                _emit_render_progress(progress, "frame_degraded_regenerate", f"Regenerating degraded {frame_type} for shot {shot_idx}", {"shot_idx": shot_idx, "frame_type": frame_type})
            print(f"🖼️ Starting {frame_type} generation for shot {shot_idx}...")
            _emit_render_progress(progress, "frame_start", f"Generating {frame_type} for shot {shot_idx}", {"shot_idx": shot_idx, "frame_type": frame_type})
            available_image_path_and_text_pairs = []
            for visible_character in visible_characters:
                identifier_in_scene = visible_character.identifier_in_scene
                registry_item = character_portraits_registry[identifier_in_scene]
                for view, item in registry_item.items():
                    available_image_path_and_text_pairs.append((item["path"], item["description"]))

            available_image_path_and_text_pairs.append(first_shot_ff_path_and_text_pair)

            selector_output_path = os.path.join(self.working_dir, "shots", f"{shot_idx}", f"{frame_type}_selector_output.json")
            if os.path.exists(selector_output_path):
                with open(selector_output_path, encoding='utf-8') as f:
                    selector_output = json.load(f)
                print(f"🚀 Loaded existing reference image selection and prompt for {frame_type} frame of shot {shot_idx} from {selector_output_path}.")
                _emit_render_progress(progress, "frame_prompt_exists", f"Prompt for {frame_type} of shot {shot_idx} already exists", {"shot_idx": shot_idx, "frame_type": frame_type, "path": selector_output_path})
            else:
                print(f"🔍 Selecting reference images and generating prompt for {frame_type} frame of shot {shot_idx}...")
                _emit_render_progress(progress, "frame_prompt_start", f"Selecting references for {frame_type} of shot {shot_idx}", {"shot_idx": shot_idx, "frame_type": frame_type})
                selector_output = await self.reference_image_selector.select_reference_images_and_generate_prompt(
                    available_image_path_and_text_pairs=available_image_path_and_text_pairs,
                    frame_description=frame_desc
                )
                _atomic_json_dump(selector_output, selector_output_path)  # VENDOR PATCH (TrainerAI, P34)
                print(f"☑️ Selected reference images and generated prompt for {frame_type} frame of shot {shot_idx}, saved to {selector_output_path}.")
                _emit_render_progress(progress, "frame_prompt_done", f"Selected references for {frame_type} of shot {shot_idx}", {"shot_idx": shot_idx, "frame_type": frame_type, "path": selector_output_path})

            reference_image_path_and_text_pairs, prompt = selector_output["reference_image_path_and_text_pairs"], selector_output["text_prompt"]
            prefix_prompt = ""
            for i, (image_path, text) in enumerate(reference_image_path_and_text_pairs):
                prefix_prompt += f"Image {i}: {text}\n"
            # VENDOR PATCH (TrainerAI) B3: strong identity-consistency directive
            # (same as the camera first-frame branch above).
            identity_lock = (
                "\n[Identity Lock] The character's face, hairstyle, and clothing "
                "must be EXACTLY consistent with Image 0 (the front portrait). "
                "Do not change the character's identity, age, or ethnicity. "
                "角色五官/发型/服装必须与 Image 0 完全一致，不得改变人物身份。\n"
            )
            prompt = f"{prefix_prompt}\n{prompt}{identity_lock}"
            # Same style anchor as the first-frame branch: without it, follow-on
            # frames drift back to 写实 because the reference_image_selector output
            # never carries the user's style (see vg_f23954591917 root-cause note).
            prompt = _with_style_prefix(prompt, style)
            reference_image_paths = [item[0] for item in reference_image_path_and_text_pairs]
            from app.gateway.video_gen.engines.vimax.provider_bridge import VimaxBudgetExceeded  # VENDOR PATCH (TrainerAI, P29): lazy host import

            try:
                # VENDOR PATCH (TrainerAI) B5: frame-generation fallback — mirror the video
                # side's B1 (:541-562). A frame failure (seedream 429/5xx/content-review/
                # VimaxBudgetExceeded) must NOT bubble to _wrap_pipeline's whole-pipeline
                # retry (max_retries=2) which regenerates all frames + amplifies render time.
                frame_image: ImageOutput = await self.image_generator.generate_single_image(
                    prompt=prompt,
                    reference_image_paths=reference_image_paths,
                )
                await asyncio.to_thread(frame_image.save, frame_image_path)  # VENDOR PATCH (TrainerAI, P34)
            except VimaxBudgetExceeded:  # VENDOR PATCH (TrainerAI, P29): budget failure is terminal
                raise
            except Exception as e:
                if _is_systemic_pipeline_error(e):  # VENDOR PATCH (TrainerAI, P36): 磁盘/装配/契约错不降级
                    raise
                reason = f"{type(e).__name__}: {e}"
                print(f"⚠️ [B5] Frame generation failed for {frame_type} shot {shot_idx} ({reason}); using placeholder image.")
                _emit_render_progress(progress, "video_clip_static_fallback", f"{frame_type} for shot {shot_idx} fell back to placeholder image", {"shot_idx": shot_idx, "frame_type": frame_type, "reason": reason[:240], "artifact": frame_type})
                # Placeholder: reuse this shot's first_frame (if last_frame failed), else the
                # first shot's first_frame, else a solid-color PNG. The video side (B1) handles
                # a placeholder first_frame via solid-color video.
                _ff_candidate = os.path.join(self.working_dir, "shots", f"{shot_idx}", "first_frame.png")
                if frame_type != "first_frame" and os.path.exists(_ff_candidate):
                    shutil.copy(_ff_candidate, frame_image_path)
                elif os.path.exists(first_shot_ff_path_and_text_pair[0]):
                    shutil.copy(first_shot_ff_path_and_text_pair[0], frame_image_path)
                else:
                    _write_placeholder_frame(frame_image_path, self.image_generator)  # VENDOR PATCH (TrainerAI, P28)
                _write_failed_marker(frame_image_path, reason)  # VENDOR PATCH (TrainerAI, P36): resume 可重生
            print(f"☑️ Generated {frame_type} frame for shot {shot_idx}, saved to {frame_image_path}.")
            _emit_render_progress(progress, "frame_done", f"Generated {frame_type} for shot {shot_idx}", {"shot_idx": shot_idx, "frame_type": frame_type, "path": frame_image_path})


        self.frame_events[shot_idx][frame_type].set()
        return frame_image_path


    async def construct_camera_tree(
        self,
        shot_descriptions: list[ShotDescription],
        quiet: bool = False,
    ):
        camera_tree_path = os.path.join(self.working_dir, "camera_tree.json")

        if os.path.exists(camera_tree_path):
            with open(camera_tree_path, encoding="utf-8") as f:
                camera_tree = json.load(f)
            camera_tree = [Camera.model_validate(camera) for camera in camera_tree]
            _pipeline_print(quiet, f"🚀 Loaded {len(camera_tree)} cameras from existing file.")
            return camera_tree

        cameras = _group_shots_into_cameras(shot_descriptions)

        camera_tree = await self.camera_image_generator.construct_camera_tree(cameras=cameras, shot_descs=shot_descriptions)
        _atomic_json_dump([camera.model_dump() for camera in camera_tree], camera_tree_path)  # VENDOR PATCH (TrainerAI, P34)
        _pipeline_print(quiet, f"✅ Constructed camera tree and saved to {camera_tree_path}.")
        return camera_tree




    async def extract_characters(
        self,
        script: str,
        quiet: bool = False,
    ):
        save_path = os.path.join(self.working_dir, "characters.json")

        if os.path.exists(save_path):
            with open(save_path, encoding="utf-8") as f:
                characters = json.load(f)
            characters = [CharacterInScene.model_validate(character) for character in characters]
            _pipeline_print(quiet, f"🚀 Loaded {len(characters)} characters from existing file.")
        else:
            characters = await self.character_extractor.extract_characters(script)
            _atomic_json_dump([character.model_dump() for character in characters], save_path)  # VENDOR PATCH (TrainerAI, P34)
            _pipeline_print(quiet, f"✅ Extracted {len(characters)} characters from script and saved to {save_path}.")

        for character in characters:
            self.character_portrait_events[character.idx] = asyncio.Event()

        return characters


    async def generate_character_portraits(
        self,
        characters: list[CharacterInScene],
        character_portraits_registry: dict[str, dict[str, dict[str, str]]] | None,
        style: str,
        progress: Callable[[str, str, dict[str, Any] | None], None] | None = None,
    ):
        character_portraits_registry_path = os.path.join(self.working_dir, "character_portraits_registry.json")
        if character_portraits_registry is None:
            if os.path.exists(character_portraits_registry_path):
                with open(character_portraits_registry_path, encoding='utf-8') as f:
                    character_portraits_registry = json.load(f)
            else:
                character_portraits_registry = {}


        tasks = [
            self.generate_portraits_for_single_character(character, style, progress=progress)
            for character in characters
            if character.identifier_in_scene not in character_portraits_registry
        ]
        if tasks:
            for future in asyncio.as_completed(tasks):
                character_portraits_registry.update(await future)
                _atomic_json_dump(character_portraits_registry, character_portraits_registry_path)  # VENDOR PATCH (TrainerAI, P34)

            print(f"✅ Completed character portrait generation for {len(characters)} characters.")
            _emit_render_progress(progress, "character_portraits_done", "Completed character portrait generation", {"character_count": len(characters)})
        else:
            print("🚀 All characters already have portraits, skipping portrait generation.")
            _emit_render_progress(progress, "character_portraits_exist", "All character portraits already exist", {"character_count": len(characters)})
        return character_portraits_registry


    async def generate_portraits_for_single_character(
        self,
        character: CharacterInScene,
        style: str,
        progress: Callable[[str, str, dict[str, Any] | None], None] | None = None,
    ):
        character_dir = os.path.join(self.working_dir, "character_portraits", f"{character.idx}_{safe_path_component(character.identifier_in_scene)}")
        os.makedirs(character_dir, exist_ok=True)
        _emit_render_progress(progress, "character_portrait_start", f"Generating portraits for {character.identifier_in_scene}", {"character_idx": character.idx, "identifier": character.identifier_in_scene})

        front_portrait_path = os.path.join(character_dir, "front.png")
        if os.path.exists(front_portrait_path):
            pass
        else:
            _emit_render_progress(progress, "character_portrait_front_start", f"Generating front portrait for {character.identifier_in_scene}", {"character_idx": character.idx, "identifier": character.identifier_in_scene})
            # Task 3o wiring (2026-07-22): 质量门——拒绝则重生一次，仍差则带警告继续
            # （warn-continue：降级完成 > 整管线崩，与 B1/B5 一致）；拒绝原因透传 meta。
            gate = await generate_portrait_with_gate(
                lambda: self.character_portraits_generator.generate_front_portrait(character, style),
                front_portrait_path,
                identifier=character.identifier_in_scene,
                view="front",
            )
            if not gate["acceptable"]:
                _emit_render_progress(progress, "character_portrait_quality_warning", f"Portrait quality gate rejected {character.identifier_in_scene} front: {gate['reasons']}", {"identifier": character.identifier_in_scene, "view": "front", "reasons": gate["reasons"]})
            _emit_render_progress(progress, "character_portrait_front_done", f"Generated front portrait for {character.identifier_in_scene}", {"character_idx": character.idx, "identifier": character.identifier_in_scene, "path": front_portrait_path})


        side_portrait_path = os.path.join(character_dir, "side.png")
        if os.path.exists(side_portrait_path):
            pass
        else:
            _emit_render_progress(progress, "character_portrait_side_start", f"Generating side portrait for {character.identifier_in_scene}", {"character_idx": character.idx, "identifier": character.identifier_in_scene})
            # Task 3o wiring (2026-07-22): 质量门（同 front 块注释）
            gate = await generate_portrait_with_gate(
                lambda: self.character_portraits_generator.generate_side_portrait(character, front_portrait_path, style),
                side_portrait_path,
                identifier=character.identifier_in_scene,
                view="side",
            )
            if not gate["acceptable"]:
                _emit_render_progress(progress, "character_portrait_quality_warning", f"Portrait quality gate rejected {character.identifier_in_scene} side: {gate['reasons']}", {"identifier": character.identifier_in_scene, "view": "side", "reasons": gate["reasons"]})
            _emit_render_progress(progress, "character_portrait_side_done", f"Generated side portrait for {character.identifier_in_scene}", {"character_idx": character.idx, "identifier": character.identifier_in_scene, "path": side_portrait_path})

        back_portrait_path = os.path.join(character_dir, "back.png")
        if os.path.exists(back_portrait_path):
            pass
        else:
            _emit_render_progress(progress, "character_portrait_back_start", f"Generating back portrait for {character.identifier_in_scene}", {"character_idx": character.idx, "identifier": character.identifier_in_scene})
            # Task 3o wiring (2026-07-22): 质量门（同 front 块注释）
            gate = await generate_portrait_with_gate(
                lambda: self.character_portraits_generator.generate_back_portrait(character, front_portrait_path, style),
                back_portrait_path,
                identifier=character.identifier_in_scene,
                view="back",
            )
            if not gate["acceptable"]:
                _emit_render_progress(progress, "character_portrait_quality_warning", f"Portrait quality gate rejected {character.identifier_in_scene} back: {gate['reasons']}", {"identifier": character.identifier_in_scene, "view": "back", "reasons": gate["reasons"]})
            _emit_render_progress(progress, "character_portrait_back_done", f"Generated back portrait for {character.identifier_in_scene}", {"character_idx": character.idx, "identifier": character.identifier_in_scene, "path": back_portrait_path})

        self.character_portrait_events[character.idx].set()

        print(f"☑️ Completed character portrait generation for {character.identifier_in_scene}.")
        _emit_render_progress(progress, "character_portrait_done", f"Portraits for {character.identifier_in_scene} ready", {"character_idx": character.idx, "identifier": character.identifier_in_scene})

        return {
            character.identifier_in_scene: {
                "front": {
                    "path": front_portrait_path,
                    "description": f"A front view portrait of {character.identifier_in_scene}.",
                },
                "side": {
                    "path": side_portrait_path,
                    "description": f"A side view portrait of {character.identifier_in_scene}.",
                },
                "back": {
                    "path": back_portrait_path,
                    "description": f"A back view portrait of {character.identifier_in_scene}.",
                },
            }
        }



    async def design_storyboard(
        self,
        script: str,
        characters: list[CharacterInScene],
        user_requirement: str,
        quiet: bool = False,
        progress: "Callable[[str, str, dict[str, Any] | None], None] | None" = None,
        global_shot_budget: int | None = None,
    ):
        """VENDOR PATCH (TrainerAI, Plan C 2026-10-19): structured-budget batching.

        ``global_shot_budget``（宿主 engine 结构化传入，仅 script2video）> 12 时按
        ``plan_storyboard_batches`` 确定性分批：批次循环在本方法内（artist 的
        tenacity 之外）顺序执行；所有批共享同一 ``characters``；每批原子落盘
        ``storyboard_batches/batch_N.json``（manifest 记录 script/characters/
        budget/prompt-version 指纹，失配即整体作废）；断点复用只重跑缺失批；
        根 ``storyboard.json`` 只在全部批次成功合并后原子写出。缺省/预算 ≤12 =
        原单次调用（提示词零变化）。
        """
        storyboard_path = os.path.join(self.working_dir, "storyboard.json")
        if os.path.exists(storyboard_path):
            with open(storyboard_path, encoding='utf-8') as f:
                storyboard = json.load(f)
            storyboard = [ShotBriefDescription.model_validate(shot) for shot in storyboard]
            _pipeline_print(quiet, f"🚀 Loaded {len(storyboard)} shot brief descriptions from existing file.")
        else:
            import hashlib

            from vimax.utils.text import (
                _STORYBOARD_BATCH_PROMPT_VERSION,
                SINGLE_BATCH_SHOT_THRESHOLD,
                _json_dumps_compact,
                merge_storyboard_batches,
                plan_storyboard_batches,
            )

            if isinstance(global_shot_budget, int) and global_shot_budget > SINGLE_BATCH_SHOT_THRESHOLD:
                _pipeline_print(quiet, f"🔍 Designing storyboard in batches (global budget {global_shot_budget})...")
                plans = plan_storyboard_batches(script, global_shot_budget=global_shot_budget)
                batches_dir = os.path.join(self.working_dir, "storyboard_batches")
                os.makedirs(batches_dir, exist_ok=True)
                fingerprint_src = _json_dumps_compact({
                    "script": script,
                    "characters": [c.model_dump() for c in characters],
                    "budget": global_shot_budget,
                    "prompt_version": _STORYBOARD_BATCH_PROMPT_VERSION,
                })
                fingerprint = hashlib.md5(fingerprint_src.encode("utf-8")).hexdigest()
                manifest_path = os.path.join(batches_dir, "manifest.json")
                manifest = None
                if os.path.exists(manifest_path):
                    try:
                        with open(manifest_path, encoding='utf-8') as f:
                            manifest = json.load(f)
                    except Exception:
                        manifest = None
                if not (manifest and manifest.get("fingerprint") == fingerprint):
                    # 指纹失配：整体作废重来（输入变化不得复用旧批缓存）
                    import shutil

                    for stale in os.listdir(batches_dir):
                        if stale != "manifest.json":
                            shutil.rmtree(os.path.join(batches_dir, stale), ignore_errors=True) if os.path.isdir(os.path.join(batches_dir, stale)) else os.remove(os.path.join(batches_dir, stale))
                    manifest = {"fingerprint": fingerprint, "batches": len(plans)}
                    _atomic_json_dump(manifest, manifest_path)

                batch_total = len(plans)
                collected: list[list[ShotBriefDescription]] = []
                for i, plan in enumerate(plans):
                    batch_path = os.path.join(batches_dir, f"batch_{i}.json")
                    if os.path.exists(batch_path):
                        try:
                            with open(batch_path, encoding='utf-8') as f:
                                batch_shots = [ShotBriefDescription.model_validate(s) for s in json.load(f)]
                            _pipeline_print(quiet, f"🚀 Reusing batch {i + 1}/{batch_total} from cache ({len(batch_shots)} shots).")
                            collected.append(batch_shots)
                            continue
                        except Exception:
                            # 评审修复：损坏缓存按缺失处理重跑（与 manifest 容错对称）
                            _pipeline_print(quiet, f"⚠️ Cached batch {i + 1}/{batch_total} is corrupt; re-running.")
                    _emit_render_progress(progress, "load_storyboard", f"Designing storyboard batch {i + 1}/{batch_total}", {"batch_idx": i, "batch_total": batch_total})
                    batch_shots = await self.storyboard_artist.design_storyboard(
                        script=script,
                        characters=characters,
                        user_requirement=user_requirement,
                        retry_timeout=self.storyboard_timeout,
                        target_script=plan.text,
                        shot_budget=plan.shot_budget,
                        # P1-4：仅当实际多批时批次缓存可复用（单批调用保持 False）
                        batches_reusable=batch_total > 1,
                        task_retryable=self.storyboard_task_retryable,
                    )
                    _atomic_json_dump([s.model_dump() for s in batch_shots], batch_path)  # 每批原子落盘
                    _emit_render_progress(progress, "storyboard_batch_ready", f"Storyboard batch {i + 1}/{batch_total} ready", {"batch_idx": i, "batch_total": batch_total, "shot_count": len(batch_shots)})
                    collected.append(batch_shots)
                try:
                    storyboard = merge_storyboard_batches(collected, global_shot_budget=global_shot_budget)
                except Exception:
                    # 评审修复（2026-10-19，critical）：merge 硬校验失败（超预算/空批/乱序）
                    # 时批次缓存已全部落盘——若不作废，用户重试会全部命中毒缓存原样再炸，
                    # 永久死循环。作废后重试重新生成（批次费已花，但任务可自愈）。
                    import shutil

                    shutil.rmtree(batches_dir, ignore_errors=True)
                    raise
                _atomic_json_dump([shot.model_dump() for shot in storyboard], storyboard_path)  # 全部成功后原子写根文件
                _pipeline_print(quiet, f"✅ Designed storyboard in {batch_total} batches and saved to {storyboard_path}.")
            else:
                _pipeline_print(quiet, "🔍 Designing storyboard...")
                storyboard = await self.storyboard_artist.design_storyboard(
                    script=script,
                    characters=characters,
                    user_requirement=user_requirement,
                    retry_timeout=self.storyboard_timeout,
                    batches_reusable=False,  # 无批次断点缓存（仅文案/缓存语义）
                    task_retryable=self.storyboard_task_retryable,  # script2video=False；idea2video 默认 True
                )
                _atomic_json_dump([shot.model_dump() for shot in storyboard], storyboard_path)  # VENDOR PATCH (TrainerAI, P34)
                _pipeline_print(quiet, f"✅ Designed storyboard and saved to {storyboard_path}.")

        for shot_brief_description in storyboard:
            self.shot_desc_events[shot_brief_description.idx] = asyncio.Event()

        return storyboard



    async def decompose_visual_descriptions(
        self,
        shot_brief_descriptions: list[ShotBriefDescription],
        characters: list[CharacterInScene],
        quiet: bool = False,
    ):
        tasks = [
            self.decompose_visual_description_for_single_shot_brief_description(shot_brief_description, characters, quiet=quiet)
            for shot_brief_description in shot_brief_descriptions
        ]

        shot_descriptions = await asyncio.gather(*tasks)
        return shot_descriptions


    async def decompose_visual_description_for_single_shot_brief_description(
        self,
        shot_brief_description: ShotBriefDescription,
        characters: list[CharacterInScene],
        quiet: bool = False,
    ):
        shot_description_path = os.path.join(self.working_dir, "shots", f"{shot_brief_description.idx}", "shot_description.json")
        os.makedirs(os.path.dirname(shot_description_path), exist_ok=True)

        if os.path.exists(shot_description_path):
            with open(shot_description_path, encoding='utf-8') as f:
                shot_description = ShotDescription.model_validate(json.load(f))
            _pipeline_print(quiet, f"🚀 Loaded shot {shot_brief_description.idx} description from existing file.")
        else:
            async with self._decompose_sem:
                shot_description = await self.storyboard_artist.decompose_visual_description(
                    shot_brief_desc=shot_brief_description,
                    characters=characters,
                    retry_timeout=self.llm_retry_timeout,
                )
            _atomic_json_dump(shot_description.model_dump(), shot_description_path)  # VENDOR PATCH (TrainerAI, P34)
            _pipeline_print(quiet, f"✅ Decomposed visual description for shot {shot_brief_description.idx} and saved to {shot_description_path}.")

        self.shot_desc_events[shot_brief_description.idx].set()

        if shot_description.variation_type in ["medium", "large"]:
            self.frame_events[shot_brief_description.idx] = {
                "first_frame": asyncio.Event(),
                "last_frame": asyncio.Event(),
            }
        else:
            self.frame_events[shot_brief_description.idx] = {
                "first_frame": asyncio.Event(),
            }

        return shot_description


def _with_style_prefix(prompt: str, style: str | None) -> str:
    """Prepend an explicit visual-style directive to a frame prompt.

    ViMax's frame-generation prompt is assembled purely from
    ``reference_image_selector`` output, which never carries the user's ``style``.
    Style previously reached frames only *implicitly* via cartoon portrait
    reference images — but the image model weights the写实的 frame description over
    those references, so non-realistic styles (Cartoon/Anime) silently fell back
    to photoreal (real run vg_f23954591917). Putting the style at the top of the
    prompt makes the image model honor it on every frame.

    A leading directive is stronger than a trailing one for instruction-tuned
    image models; an empty/None style is a no-op so callers don't need to guard.
    """
    if not style or not style.strip():
        return prompt
    return f"Visual style: {style.strip()}.\n{prompt}"


def _group_shots_into_cameras(shot_descriptions: list[ShotDescription]) -> list[Camera]:
    """Group shots by their camera index.

    Cameras are looked up by their idx field, not by list position: cameras are
    appended in order of first appearance, so positional indexing would attach
    shots to the wrong camera whenever the LLM emits cam indices out of order.
    """
    cameras: list[Camera] = []
    cameras_by_idx: dict[int, Camera] = {}
    for shot_description in shot_descriptions:
        camera = cameras_by_idx.get(shot_description.cam_idx)
        if camera is None:
            camera = Camera(idx=shot_description.cam_idx, active_shot_idxs=[shot_description.idx])
            cameras_by_idx[shot_description.cam_idx] = camera
            cameras.append(camera)
        else:
            camera.active_shot_idxs.append(shot_description.idx)
    return cameras


def _collect_priority_shot_idxs(camera_tree: list[Camera]) -> list[int]:
    """Shot indices that other cameras depend on (compared against shot idxs, so
    they must come from parent_shot_idx, not the camera index space)."""
    return [camera.parent_shot_idx for camera in camera_tree if camera.parent_shot_idx is not None]
