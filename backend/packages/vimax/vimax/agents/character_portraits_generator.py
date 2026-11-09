import logging
import os
import asyncio
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.output_parsers import PydanticOutputParser
from langchain.chat_models.base import BaseChatModel
from langchain.chat_models import init_chat_model
from pydantic import BaseModel, Field
from typing import List, Optional, Dict
from tenacity import retry, stop_after_attempt
from vimax.interfaces import CharacterInScene, ImageOutput
from langchain_core.messages import HumanMessage, SystemMessage
from vimax.utils.retry import after_func
from PIL import Image

logger = logging.getLogger(__name__)


prompt_template_front = """
Generate a full-body, front-view portrait of character {identifier} based on the following description, with a pure white background. The character should be centered in the image, occupying most of the frame. Gazing straight ahead. Standing with arms relaxed at sides. Natural expression.
Features: {features}
Style: {style}
"""

prompt_template_side = """
Generate a full-body, side-view portrait of character {identifier} based on the provided front-view portrait, with a pure white background. The character should be centered in the image, occupying most of the frame. Facing left. Standing with arms relaxed at sides.
IMPORTANT — keep the character's costume and accessories identical to the front-view portrait (do NOT change clothing, badges, helmets, or props).
Features: {features}
Style: {style}
"""

prompt_template_back = """
Generate a full-body, back-view portrait of character {identifier} based on the provided front-view portrait, with a pure white background. The character should be centered in the image, occupying most of the frame. No facial features should be visible.
IMPORTANT — keep the character's costume and accessories identical to the front-view portrait (do NOT change clothing, badges, helmets, or props).
Features: {features}
Style: {style}
"""


def _is_image_reference(value: str | None) -> bool:
    """True only when `value` can be an image input to /v1/images/edits.

    `character_reference` is a free-text description by UI design
    (CourseProject.style_prefix already injects it as prompt text). Passing
    raw text as an image path crashes the 360 edits API — see
    NanoBananaImageProvider._normalize_reference, which returns non-URL /
    non-file values unchanged.
    """
    if not value:
        return False
    if value.startswith(("http://", "https://", "data:")):
        return True
    return os.path.exists(value)


def check_portrait_quality(url: str, width: int = 0, height: int = 0) -> dict:
    """Task 3o: portrait 质量门禁——分辨率/比例校验。

    ViMax 全片角色一致性建立在初始 portrait 上；portrait 差→全片角色歪。
    返回 {acceptable, reasons}。差则调用方重生或引导用户在项目里手动上传三视图。
    """
    reasons: List[str] = []
    if width and height:
        if width < 512 or height < 512:
            reasons.append("resolution")
        # 宽高比应接近 1:1（正面肖像）或合理人像比
        if width and height and max(width, height) / max(min(width, height), 1) > 2.0:
            reasons.append("aspect_ratio")
    if not url:
        reasons.append("missing_url")
    return {"acceptable": len(reasons) == 0, "reasons": reasons}


async def _face_present_async(image_path: str) -> bool | None:
    """Vision 校验肖像是否包含清晰可辨的人物面部（2026-07-27 T17）。

    返回 True/False；None = 校验不可用（异常/超时）→ 调用方放行+告警。
    主 LLM（Ark 直连 doubao vision）实测 vision 可用；lazy import 与
    pipelines/script2video_pipeline.py 的 app.gateway lazy import 同模式——
    vimax 包保持可独立安装，deerflow 只在运行时存在。
    """
    try:
        import base64
        from pathlib import Path

        from deerflow.models import create_chat_model

        model = create_chat_model()  # 默认模型（须 supports_vision: true）
        b64 = base64.b64encode(Path(image_path).read_bytes()).decode()
        resp = await model.ainvoke(
            [
                HumanMessage(
                    content=[
                        {"type": "text", "text": "这张图片是否包含一个清晰可辨的人物面部？只回答 yes 或 no。"},
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                    ]
                )
            ]
        )
        answer = str(resp.content).strip().lower()
        return answer.startswith("yes")
    except Exception:
        logger.exception("face check unavailable for %s — treating as unknown (pass with warning)", image_path)
        return None


async def generate_portrait_with_gate(
    generate_fn,
    save_path: str,
    *,
    identifier: str,
    view: str,
    max_attempts: int = 2,
) -> dict:
    """Task 3o wiring (2026-07-22): generate → save → quality gate → regenerate
    once on reject → warn-continue.

    ViMax 全片角色一致性建立在初始 portrait 上；一张低清/畸形的 front 会毒化
    后续所有帧。门禁拒绝只重生一次（generate_fn 自身还有 @retry(stop=3)）——
    两次都差则带警告继续（与 B 系列韧性一致：降级完成 > 整管线崩），调用方负责
    把拒绝原因透传 progress meta。

    generate_fn: 无参 async callable，返回带 .save(path) 的 ImageOutput。
    返回最后一次 check_portrait_quality 的结果 dict {acceptable, reasons}。
    """
    result: dict = {"acceptable": False, "reasons": ["not_attempted"]}
    for attempt in range(1, max_attempts + 1):
        output = await generate_fn()
        await asyncio.to_thread(output.save, save_path)  # VENDOR PATCH (TrainerAI, P34): offload artifact save
        width = height = 0
        unreadable = False
        try:
            with Image.open(save_path) as im:
                im.load()  # VENDOR PATCH (TrainerAI, P33): detect valid-header/truncated pixels
                width, height = im.size
        except Exception:
            unreadable = True  # VENDOR PATCH (TrainerAI, P33): unreadable portraits fail closed
            logger.exception("portrait gate: failed to open %s for size check", save_path)
        result = check_portrait_quality(save_path, width, height)
        if unreadable:
            result = {"acceptable": False, "reasons": [*result["reasons"], "unreadable"]}
        if (
            result["acceptable"]
            and view == "front"
            and os.environ.get("VIMAX_PORTRAIT_FACE_CHECK", "1") != "0"
        ):
            # 2026-07-27 T17：分辨率合规 ≠ 有脸。front 肖像追加 vision 人脸校验——
            # 无人脸按拒绝走重试；校验不可用（None）放行+告警（降级完成 > 整管线崩）。
            # side/back 视角本就看不到正脸（prompt_template_back 明确要求无面部），跳过。
            face = await _face_present_async(save_path)
            if face is False:
                result = {"acceptable": False, "reasons": [*result["reasons"], "no_face"]}
            elif face is None:
                logger.warning("portrait gate: face check unavailable for %s — accepting on resolution only", save_path)
        if result["acceptable"]:
            return result
        logger.warning(
            "portrait gate: %s %s attempt %d/%d rejected (%s)",
            identifier, view, attempt, max_attempts, result["reasons"],
        )
    return result


class CharacterPortraitsGenerator:
    def __init__(
        self,
        image_generator,
        character_reference: str | None = None,
        character_sheets: list[dict] | None = None,
    ):
        self.image_generator = image_generator
        # HR-T4: 用户在 CourseProject 里指定的角色参考图路径，注入 front portrait
        # 作 reference_image（bridge generate_single_image 已支持 reference_image_paths）。
        self.character_reference = character_reference
        # B5c: 用户三视图的 side/back（front 已是 character_reference 生成了 front portrait），
        # 作 side/back portrait 的额外参考图，提升跨视角一致性——修前 side/back 在 ViMax 边界被丢。
        # 2026-10-15：多角色必须按 name 精确匹配；未命名单表保留旧兼容行为。
        self._character_view_refs: dict[str, dict[str, list[str | None]]] = {}  # VENDOR PATCH (TrainerAI, P33)
        first: dict = {}
        for sheet in character_sheets or []:
            item = sheet if isinstance(sheet, dict) else {}
            first = first or item
            name = str(item.get("name", "")).strip()
            if not name:
                continue
            refs = self._character_view_refs.setdefault(name, {"side": [], "back": []})
            refs["side"].append(item.get("side_url"))
            refs["back"].append(item.get("back_url"))
        self._user_side = first.get("side_url")
        self._user_back = first.get("back_url")

    def _extra_view_refs(self, character: CharacterInScene | None = None, view: str | None = None) -> list[str]:  # VENDOR PATCH (TrainerAI, P33)
        """B5c: 按角色返回用户 side/back 视图路径。

        命名角色要求 name 与 identifier_in_scene 唯一匹配；匹配不到或歧义时只传
        front，避免 A 角色拿到 B 角色的三视图造成跨角色污染。未命名单表保持
        旧语义（side/back 都注入），兼容既有单角色任务。
        """
        if character is not None and self._character_view_refs:
            matches = self._character_view_refs.get(character.identifier_in_scene)
            if matches is None or view not in matches:
                return []
            references = list(dict.fromkeys(matches[view]))
            if len(references) != 1:
                return []
            reference = references[0]
            return [reference] if reference and _is_image_reference(reference) else []
        return [r for r in (self._user_side, self._user_back) if r and _is_image_reference(r)]

    @retry(stop=stop_after_attempt(3), after=after_func, reraise=True)
    async def generate_front_portrait(
        self,
        character: CharacterInScene,
        style: str,
    ) -> ImageOutput:
        features = "(static) " + character.static_features + "; (dynamic) " + character.dynamic_features
        prompt = prompt_template_front.format(
            identifier=character.identifier_in_scene,
            features=features,
            style=style,
        )
        # HR-T4: 注入用户角色参考图（CourseProject.character_reference）作 reference_image，
        # 让首张肖像遵循用户指定的人物长相（bridge generate_single_image 已支持 reference_image_paths）。
        gen_kwargs: dict = {"prompt": prompt}
        if self.character_reference and _is_image_reference(self.character_reference):
            # URL / data-URL / 磁盘文件 → 作为参考图（i2i edits）
            gen_kwargs["reference_image_paths"] = [self.character_reference]
        elif self.character_reference:
            # 纯文本描述（字段 UI 语义）→ 注入 prompt，绝不当图片路径
            gen_kwargs["prompt"] = f"{prompt}\nCharacter reference: {self.character_reference}"
        image_output = await self.image_generator.generate_single_image(**gen_kwargs)
        return image_output

    @retry(stop=stop_after_attempt(3), after=after_func, reraise=True)
    async def generate_side_portrait(
        self,
        character: CharacterInScene,
        front_image_path: str,
        style: str = "realistic movie style",
    ) -> ImageOutput:
        # Anchor style + clothing (dynamic_features) in the text prompt, not just the
        # reference image: real-run vg_8159cd6cc3fa showed the side view "换装" (lost
        # the courier's badge/helmet) because the old prompt only carried {identifier}.
        features = "(static) " + character.static_features + "; (dynamic) " + character.dynamic_features
        prompt = prompt_template_side.format(
            identifier=character.identifier_in_scene,
            features=features,
            style=style,
        )
        # B5c: 注入用户 side/back 视图作额外参考图（front 已是 character_reference 生成了 front portrait）
        image_output = await self.image_generator.generate_single_image(
            prompt=prompt,
            reference_image_paths=[front_image_path, *self._extra_view_refs(character, "side")],  # VENDOR PATCH (TrainerAI, P33)
            # size="1024x1024",
        )
        return image_output

    @retry(stop=stop_after_attempt(3), after=after_func, reraise=True)
    async def generate_back_portrait(
        self,
        character: CharacterInScene,
        front_image_path: str,
        style: str = "realistic movie style",
    ) -> ImageOutput:
        # Same costume-anchor rationale as generate_side_portrait (see above).
        features = "(static) " + character.static_features + "; (dynamic) " + character.dynamic_features
        prompt = prompt_template_back.format(
            identifier=character.identifier_in_scene,
            features=features,
            style=style,
        )
        # B5c: 注入用户 side/back 视图作额外参考图
        image_output = await self.image_generator.generate_single_image(
            prompt=prompt,
            reference_image_paths=[front_image_path, *self._extra_view_refs(character, "back")],  # VENDOR PATCH (TrainerAI, P33)
            # size="512x512",
        )
        return image_output
