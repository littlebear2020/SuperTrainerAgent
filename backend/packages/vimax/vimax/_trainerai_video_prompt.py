"""TrainerAI vendor-add: build an anchored video-gen prompt + reference list.

Extracted from Script2VideoPipeline.generate_video_for_single_shot so the
vendored edit to script2video_pipeline.py stays minimal (signature + one call).
This file is ADDITIVE — upstream ViMax never touches it, so rebases are safe.

3a (main lever): prepend ``Visual style: <style>.`` + the visible characters'
    static_features to the video prompt. Before this, the video prompt was bare
    ``motion_desc + audio_desc`` and _with_style_prefix was applied to IMAGE
    generation only → i2v had no style or identity anchor, so cross-shot
    character drift (vg_44e7e21427e6).
3b (experimental): append visible characters' front-portrait paths as extra
    reference images. Direction is right (extra identity anchor) but happyhorse
    i2v's weight on multi-ref is unverified — see plan Risk 5. A/B-validate
    in Task 8; drop if ineffective.
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional

from vimax.interfaces import CharacterInScene, ShotDescription


def build_anchored_video_prompt_and_refs(
    *,
    shot_description: ShotDescription,
    working_dir: str,
    characters: Optional[List[CharacterInScene]] = None,
    style: Optional[str] = None,
    character_portraits_registry: Optional[Dict[str, Dict[str, Dict[str, str]]]] = None,
) -> tuple[str, list[str]]:
    """Return (prompt, reference_image_paths) for one shot's video generation.

    Pure function — no IO, no side effects. The caller owns skip-if-exists,
    frame-event waiting, and progress emission.
    """
    # Base reference paths: the shot's own first_frame (+ last_frame if medium/large).
    frame_paths: list[str] = [
        os.path.join(working_dir, "shots", f"{shot_description.idx}", "first_frame.png")
    ]
    if shot_description.variation_type in ["medium", "large"]:
        frame_paths.append(
            os.path.join(working_dir, "shots", f"{shot_description.idx}", "last_frame.png")
        )

    # 3b: append visible characters' front portraits as extra reference images.
    # front view is the strongest identity anchor.
    # A/B switch (2026-07-22): VIMAX_3B_PORTRAIT_REFS=off 关闭 3b 追加——dialogue
    # plan Task 8 的 A/B 需要不改代码对照"有/无肖像参考"两组。默认开（现状）。
    if os.environ.get("VIMAX_3B_PORTRAIT_REFS", "on").strip().lower() not in ("off", "0", "false"):
        if character_portraits_registry and characters:
            for vis_idx in shot_description.ff_vis_char_idxs:
                if vis_idx < len(characters):
                    identifier = characters[vis_idx].identifier_in_scene
                    front = character_portraits_registry.get(identifier, {}).get("front")
                    if front and front["path"] not in frame_paths:
                        frame_paths.append(front["path"])

    # 3a: character identity anchor in the prompt.
    char_anchor = ""
    if characters:
        anchor_parts = []
        for vis_idx in shot_description.ff_vis_char_idxs:
            if vis_idx < len(characters):
                c = characters[vis_idx]
                anchor_parts.append(f"{c.identifier_in_scene}: {c.static_features}")
        if anchor_parts:
            char_anchor = "\n[Characters] " + "; ".join(anchor_parts)
    prompt = shot_description.motion_desc + "\n" + shot_description.audio_desc + char_anchor

    # 3a: style directive (mirrors _with_style_prefix to keep this module
    # self-contained — avoids a circular import on script2video_pipeline).
    if style and style.strip():
        prompt = f"Visual style: {style.strip()}.\n{prompt}"

    return prompt, frame_paths
