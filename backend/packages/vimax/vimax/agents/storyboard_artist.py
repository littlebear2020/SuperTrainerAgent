import asyncio
from typing import Literal

from langchain.chat_models.base import BaseChatModel
from langchain_core.output_parsers import PydanticOutputParser
from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field
from tenacity import AsyncRetrying, retry, stop_after_attempt, wait_exponential

from vimax.interfaces import CharacterInScene, ShotBriefDescription, ShotDescription
from vimax.utils.retry import after_func

_STORYBOARD_RETRY_WAIT = wait_exponential(min=1, max=10)

# TrainerAI patch（2026-10-14 配音修复批，登记于 VENDOR_PATCHES.md P27）：本模板
# [Guidelines] 首条为「剧本忠于硬约束」（用户决策 3）——<SCRIPT> 是用户已确认的
# 故事，角色/情节/台词不得新增/改写，只允许镜头语言层展开（事故中 storyboard
# 阶段改写了确认蓝图）。合并上游时保留本条。
system_prompt_template_design_storyboard = \
"""
[Role]
You are a professional storyboard artist with the following core skills:
- Script Analysis: Ability to quickly interpret a script's text, identifying the setting, character actions, dialogue, emotions, and narrative pacing.
- Visualization: Expertise in translating written descriptions into visual frames, including composition, lighting, and spatial arrangement.
- Storyboarding: Proficiency in cinematic language, such as shot types (e.g., close-up, medium shot, wide shot), camera angles (e.g., high angle, eye-level), camera movements (e.g., zoom, pan), and transitions.
- Narrative Continuity: Ability to ensure the storyboard sequence is logically smooth, highlights key plot points, and maintains emotional consistency.
- Technical Knowledge: Understanding of basic storyboard formats and industry standards, such as using numbered shots and concise descriptions.

[Task]
Your task is to design a complete storyboard based on a user-provided script (which contains only one scene). The storyboard should be presented in text form, clearly displaying the visual elements and narrative flow of each shot to help the user visualize the scene.

[Input]
The user will provide the following input.
- Script:A complete scene script containing dialogue, action descriptions, and scene settings. The script focuses on only one scene; there is no need to handle multiple scene transitions. The script input is enclosed within <SCRIPT> and </SCRIPT>.
- Characters List: A list describing basic information for each character, such as name, personality traits, appearance (if relevant). The character list is enclosed within <CHARACTERS> and </CHARACTERS>.
- User requirement: The user requirement (optional) is enclosed within <USER_REQUIREMENT> and </USER_REQUIREMENT>, which may include:
    - Target audience (e.g., children, teenagers, adults).
    - Storyboard style (e.g., realistic, cartoon, abstract).
    - Desired number of shots (e.g., "not more than 10 shots").
    - Other specific instructions (e.g., emphasize the characters' actions).

[Output]
{format_instructions}

[Guidelines]
- Script fidelity (HARD CONSTRAINT): the script in <SCRIPT> is the confirmed, user-approved story. Do NOT add, remove, rename, or replace characters; do NOT invent, rewrite, or alter plot points or dialogue lines. You may ONLY expand at the cinematic-language level (shot types, camera angles, composition, pacing, visual detail). Dialogue that exists in the script must reach the storyboard's audio descriptions essentially verbatim; dialogue that does not exist in the script must NOT appear.
- Ensure all output values (except keys) match the language used in the script.
- Each shot must have a clear narrative purpose—such as establishing the setting, showing character relationships, or highlighting reactions.
- Use cinematic language deliberately: close-ups for emotion, wide shots for context, and varied angles to direct audience attention.
- When designing a new shot, first consider whether it can be filmed using an existing camera position. Introduce a new one only if the shot size, angle, and focus differ significantly. If the camera undergoes significant movement, it cannot be used thereafter.
- Keep character names in visual descriptions and speaker fields consistent with the character list. In visual descriptions, enclose names in angle brackets (e.g., <Alice>), but not in dialogue or speaker fields.
- When describing visual elements, it is necessary to indicate the position of the element within the frame. For example, Character A is on the left side of the frame, facing toward the right, with a table in front of him. The table is positioned slightly to the left of the center of the frame. Ensure that invisible elements are not included. For instance, do not describe someone behind a closed door if they cannot be seen.
- Avoid unsafe content (violence, discrimination, etc.) in visual descriptions. Use indirect methods like sound or suggestive imagery when needed, and substitute sensitive elements (e.g., ketchup for blood).
- Assign at most one dialogue line per character per shot. Each line of dialogue should correspond to a shot.
- Each shot requires an independent description without reference to each other.
- When the shot focuses on a character, describe which specific body part the focus is on.
- When describing a character, it is necessary to indicate the direction they are facing.
"""


human_prompt_template_design_storyboard = \
"""
<SCRIPT>
{script_str}
</SCRIPT>

<CHARACTERS>
{characters_str}
</CHARACTERS>

<USER_REQUIREMENT>
{user_requirement_str}
</USER_REQUIREMENT>
"""



system_prompt_template_decompose_visual_description = \
"""
[Role]
You are a professional visual text analyst, proficient in cinematic language and shot narration. Your expertise lies in deconstructing a comprehensive shot description accurately into three core components: the static first frame, the static last frame, and the dynamic motion that connects them.

[Task]
Your task is to dissect and rewrite a user-provided visual text description of a shot strictly and insightfully into three distinct parts:
- First Frame Description: Describe the static image at the very beginning of the shot. Focus on compositional elements, initial character postures, environmental layout, lighting, color, and other static visual aspects.
- Last Frame Description: Describe the static image at the very end of the shot. Similarly, focus on the static composition, but it must reflect the final state after changes caused by camera movement or internal element motion.
- Motion Description: Describe all movements that occur between the first frame and the last frame. This includes camera movement (e.g., static, push-in, pull-out, pan, track, follow, tilt, etc.) and movement of elements within the shot (e.g., character movement, object displacement, changes in lighting, etc.). This is the most dynamic part of the entire description. For the movement and changes of a character, you cannot directly use the character's name to refer to them. Instead, you need to refer to the character by their external features, especially noticeable ones like clothing characteristics.

[Input]
You will receive a single visual text description of a shot that typically implicitly or explicitly contains information about the starting state, the motion process, and the ending state.
Additionally, you will receive a sequence of potential characters, each containing an identifier and a feature.
- The description is enclosed within <VISUAL_DESC> and </VISUAL_DESC>.
- The character list is enclosed within <CHARACTERS> and </CHARACTERS>.


[Output]
{format_instructions}

[Guidelines]
- Ensure all output values (except keys) match the language used in the script.
- Ensure the first and last frame descriptions are pure "snapshots," containing no ongoing actions (e.g., "He is about to stand up" is unacceptable; it should be "He is sitting on the chair, leaning slightly forward").
- In the motion description, you must clearly distinguish between camera movement and on-screen movement. Use professional cinematic terminology (e.g., dolly shot, pan, zoom, etc.) as precisely as possible to describe camera movement.
- In the motion description, you cannot directly use character names to refer to characters; instead, you should use the characters' visible characteristics to refer to them. For example, "Alice is walking" is unacceptable; it should be "Alice (short hair, wearing a green dress) is walking".
- The last frame description must be logically consistent with the first frame description and the motion description. All actions described in the motion section should be reflected in the static image of the last frame.
- If the input description is ambiguous about certain details, you may make reasonable inferences and additions based on the context to make all three sections complete and fluent. However, core elements must strictly adhere to the input text.
- Use accurate, concise, and professional descriptive language. Avoid overly literary rhetoric such as metaphors or emotional flourishes; focus on providing information that can be visualized.
- Similar to the input visual description, the first and last frame descriptions should include details such as shot type, angle, composition, etc.
- Below are the three types of variation within a shot (not between two shots):
(1) 'large' cases typically involve the exaggerated transition shots which means a significant change in the composition and focus, such as smoothly changing from a wide shot to a close-up. It is usually accompanied by significant camera movement (e.g., drone perspective shots across the city).
(2) 'medium' cases often involve the introduction of new characters and a character turns from the back to face the front (facing the camera).
(3) 'small' cases usually involve minor changes, such as expression changes, movement and pose changes of existing characters(e.g., walking, sitting down, standing up), moderate camera movements(e.g., pan, tilt, track).
- When describing a character, it is necessary to indicate the direction they are facing.
- The first shot must establish the overall scene environment, using the widest possible shot.
- Use as few camera positions as possible.
"""


human_prompt_template_decompose_visual_description = \
"""
<VISUAL_DESC>
{visual_desc}
</VISUAL_DESC>

<CHARACTERS>
{characters_str}
</CHARACTERS>
"""


class VisDescDecompositionResponse(BaseModel):
    ff_desc: str = Field(
        description="A detailed description of the first frame of the shot, capturing the initial visual elements and composition.",
        # examples=[
        #     "Medium shot of a supermarket aisle at eye level. Bob(a tall man wearing a blue shirt and jeans) is positioned on the right side of the frame, captured in profile and facing right, while Alice(a young woman with short hair, wearing a green dress) is on the left, shown pushing a shopping cart with her gaze lowered toward the ground. They are arranged in a front-to-back spatial relationship. Shelves line both sides of the frame, and cool-toned fluorescent lighting from above washes over the scene. The vibrant colors of product packaging contrast with the metallic gray of the shopping cart, all contained within a stable, horizontally balanced composition.",
        #     "Extreme long shot. Aerial view from hundreds of meters above the ground. The boundless golden desert resembles undulating frozen waves, occupying the vast majority of the frame. At the very center of the image, a tiny, solitary explorer appears only as a faint dark speck, dragging a long, lonely trail of footprints behind him, stretching all the way to the edge of the frame.",
        #     "Medium shot at eye level angle. Designer A(with a beard, wearing a white suit) leans forward passionately, speaking emphatically. Product Manager B(with a beard, wearing a white T-shirt) sits with crossed arms, looking skeptical. Between them, Development Engineer C(brown hair, wearing a blue T-shirt) appears anxious, glancing between the two. Project Manager D(curly hair, wearing a red T-shirt) prepares to mediate, focusing on a whiteboard. Bright overhead lighting highlights their expressions, with a blurred whiteboard and glass wall in the background.",
        #     "A low-angle close-up shot captures the figure from below, framing him from the chest up. His face appears resolute and commanding, his eyes piercing as he speaks passionately. Flecks of saliva are visible, emphasizing his intensity. The overcast sky breaks with occasional light, casting him as a heroic, almost monumental figure against the gloom.",
        #     "An extremely close-up of an old, motionless pocket watch. Soft light highlights scratches on its brass case and the enamel dial with Roman numerals. The second hand remains fixed at 'VIII', casting a sharp shadow. A wrinkled finger gently touches the glass surface, evoking a tangible sense of stillness and time.",
        #     "An over-the-shoulder shot at eye level, positioned behind Character A(red hair, wearing a white T-shirt). The foreground, including A's shoulder and head, is softly blurred, directing focus onto Character B(with a beard, wearing a white T-shirt)'s face. B's subtle reactions—shifting from surprise to confusion, then to a glimmer of understanding—are clearly visible. The café background is gently blurred with warm lighting.",
        # ]
    )
    ff_vis_char_idxs: list[int] = Field(
        description="A list of indices of characters that are visible in the first frame of the shot, corresponding to the character list provided in the input.",
        examples=[[0], [1], [0, 1], []]
    )
    lf_desc: str = Field(
        description="A detailed description of the last frame of the shot, capturing the concluding visual elements and composition.",
    )
    lf_vis_char_idxs: list[int] = Field(
        description="A list of indices of characters that are visible in the last frame of the shot, corresponding to the character list provided in the input.",
        examples=[[0], [1], [0, 1], []]
    )
    motion_desc: str = Field(
        description="The motion description of the shot. Describe the dynamic visual changes within the shot (camera movement and the movement of elements within the frame)",
        examples=[
            "Static camera. Alice (short hair, wearing a green dress) is walking towards the camera.",
            "Dolly in from meidum shot to close-up. Bob (with a beard, wearing a white T-shirt) smiles to the camera.",
        ]
    )
    variation_type: Literal["large", "medium", "small"] = Field(
        description="Indicates the degree of change between the first frame and the last frame.",
    )
    variation_reason: str = Field(
        description="The reason for the variation type of the shot.",
        examples=[
            "This is a smooth transition shot from the sky to the ground. The content of the shot has changed significantly, so the variation type is large.",
            "Compared to the first frame, a new character appears in the last frame, and there are no significant changes in the composition. So the variation type is medium.",
            "Compared to the first frame, there are only minor changes in the composition. So the variation type is small.",
            "This shot only shows Alice speaking and the changes in her facial expressions, thus the variation type is small.",
        ],
    )



class StoryboardArtist:
    def __init__(
        self,
        chat_model: BaseChatModel,
        max_attempts: int = 3,
    ):
        if max_attempts < 1:
            raise ValueError("StoryboardArtist.max_attempts must be >= 1")
        self.chat_model = chat_model
        self.max_attempts = max_attempts


    async def design_storyboard(
        self,
        script: str,
        characters: list[CharacterInScene],
        user_requirement: str | None = None,
        retry_timeout: int = 300,
        *,
        target_script: str | None = None,
        shot_budget: int | None = None,
        batches_reusable: bool = False,
        task_retryable: bool | None = None,
    ) -> list[ShotBriefDescription]:
        """VENDOR PATCH (TrainerAI, 2026-10-20 P40)：运行时 per-instance 重试。

        默认 3 次（idea2video/旧调用一致）；script2video pipeline 将其配置为 2 次。
        单次超时仍由 caller 的 ``retry_timeout`` 决定（script2video engine 传 420s，
        本文件方法默认参数保持 300s）。
        """
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(self.max_attempts),
            wait=_STORYBOARD_RETRY_WAIT,
            reraise=True,
            after=after_func,
        ):
            with attempt:
                return await self._design_storyboard_once(
                    script=script,
                    characters=characters,
                    user_requirement=user_requirement,
                    retry_timeout=retry_timeout,
                    target_script=target_script,
                    shot_budget=shot_budget,
                    batches_reusable=batches_reusable,
                    task_retryable=task_retryable,
                )
        raise AssertionError("unreachable")

    async def _design_storyboard_once(
        self,
        script: str,
        characters: list[CharacterInScene],
        user_requirement: str | None = None,
        retry_timeout: int = 300,
        *,
        target_script: str | None = None,
        shot_budget: int | None = None,
        batches_reusable: bool = False,
        task_retryable: bool | None = None,
    ) -> list[ShotBriefDescription]:
        """VENDOR PATCH (TrainerAI, Plan C 2026-10-19): optional batch-scope knobs.

        ``target_script``：只为本片段产镜，``script`` 全文仅作只读上下文（连续性）；
        ``shot_budget``：本次调用的镜头数上限。两者缺省 = 原单批行为（提示词零变化）。
        批次循环本身在 pipeline 层（本方法的 tenacity 之外）。
        """

        class StoryboardResponse(BaseModel):
            storyboard: list[ShotBriefDescription] = Field(
                description="A complete storyboard of the scene, including the visual and audio description of each shot.",
            )

        script_str = script.strip()
        characters_str = "\n".join([f"Character {index}: {char}" for index, char in enumerate(characters)])
        user_requirement_str = user_requirement.strip() if user_requirement else ""

        # VENDOR PATCH (TrainerAI, Plan C 2026-10-19): batch context injection —
        # 缺省（两个参数都 None）不追加任何文本，提示语义零变化。
        _batch_notes: list[str] = []
        if target_script is not None:
            _batch_notes.append(
                "[TrainerAI batching] The script section below is ONE SEGMENT of a longer story. "
                "Produce shots ONLY for this segment; the full script is read-only context for continuity.\n"
                f"Full script (read-only):\n{script_str}"
            )
            script_str = target_script.strip()
        if shot_budget is not None:
            _batch_notes.append(f"[TrainerAI batching] Shot budget for this call: at most {shot_budget} shots.")
        if _batch_notes:
            user_requirement_str = (user_requirement_str + "\n\n" + "\n".join(_batch_notes)).strip()

        parser = PydanticOutputParser(pydantic_object=StoryboardResponse)
        messages = [
            ('system', system_prompt_template_design_storyboard.format(format_instructions=parser.get_format_instructions())),
            ('human', human_prompt_template_design_storyboard.format(script_str=script_str, characters_str=characters_str, user_requirement_str=user_requirement_str)),
        ]
        chain = self.chat_model | parser
        # VENDOR PATCH (TrainerAI, 2026-10-20 P40): 本地 deadline 用专用 helper——
        # 到期抛 LocalStoryboardDeadlineError，pipeline_retryable 由 task_retryable
        # 三态决定：显式 True/False 直接生效；None 保持旧行为 not batches_reusable。
        # provider 自身 TimeoutError 仍原样传播（瞬时重试语义）。
        from vimax.utils.retry import await_with_local_deadline

        response: StoryboardResponse = await await_with_local_deadline(
            chain.ainvoke(messages),
            timeout=retry_timeout,
            batches_reusable=batches_reusable,
            task_retryable=task_retryable,
        )
        storyboard = response.storyboard
        actual_idxs = sorted(shot.idx for shot in storyboard)  # VENDOR PATCH (TrainerAI, P31)
        expected_idxs = list(range(len(storyboard)))
        if actual_idxs != expected_idxs:
            raise ValueError(f"Storyboard shot indices must be contiguous and zero-based; got {actual_idxs}, expected {expected_idxs}")

        return storyboard




    @retry(stop=stop_after_attempt(3), wait=wait_exponential(min=1, max=10), reraise=True, after=after_func)
    async def decompose_visual_description(
        self,
        shot_brief_desc: ShotBriefDescription,
        characters: list[CharacterInScene],
        retry_timeout: int = 300,
    ) -> ShotDescription:
        parser = PydanticOutputParser(pydantic_object=VisDescDecompositionResponse)
        prompt_template = ChatPromptTemplate.from_messages(
            [
                ('system', system_prompt_template_decompose_visual_description),
                ('human', human_prompt_template_decompose_visual_description),
            ]
        )
        chain = prompt_template | self.chat_model | parser

        visual_desc = shot_brief_desc.visual_desc.strip()

        # VENDOR PATCH (TrainerAI, P30): numbered characters prevent multi-role index misreads.
        characters_str = "\n".join([f"Character {index}: {char.identifier_in_scene}: (static) {char.static_features}; (dynamic) {char.dynamic_features}" for index, char in enumerate(characters)])

        decomposition: VisDescDecompositionResponse = await asyncio.wait_for(
            chain.ainvoke(
                input={
                    "format_instructions": parser.get_format_instructions(),
                    "visual_desc": visual_desc,
                    "characters_str": characters_str,
                },
            ),
            timeout=retry_timeout,
        )

        validate_char_idxs(decomposition.ff_vis_char_idxs, len(characters), "ff_vis_char_idxs")
        validate_char_idxs(decomposition.lf_vis_char_idxs, len(characters), "lf_vis_char_idxs")

        return ShotDescription(
            idx=shot_brief_desc.idx,
            is_last=shot_brief_desc.is_last,
            cam_idx=shot_brief_desc.cam_idx,
            visual_desc=shot_brief_desc.visual_desc,
            variation_type=decomposition.variation_type,
            variation_reason=decomposition.variation_reason,
            ff_desc=decomposition.ff_desc,
            ff_vis_char_idxs=decomposition.ff_vis_char_idxs,
            lf_desc=decomposition.lf_desc,
            lf_vis_char_idxs=decomposition.lf_vis_char_idxs,
            motion_desc=decomposition.motion_desc,
            audio_desc=shot_brief_desc.audio_desc,
        )


def validate_char_idxs(idxs, num_characters, field_name):
    """Reject LLM-emitted character indices outside [0, num_characters).

    Negative values would silently select the wrong character via Python
    indexing; out-of-range values would crash deep inside the render gather.
    Raising here lets the @retry on decompose_visual_description re-ask.
    """
    invalid = [idx for idx in idxs if idx < 0 or idx >= num_characters]
    if invalid:
        raise ValueError(
            f"{field_name} contains invalid character indices {invalid}; "
            f"valid range is 0..{num_characters - 1}"
        )
