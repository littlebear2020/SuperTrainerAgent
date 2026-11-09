from pydantic import BaseModel, Field
from typing import List, Optional, Literal, Tuple

# TrainerAI patch（2026-10-14 配音格式漂移事故，登记于 VENDOR_PATCHES.md P27）：
# audio_desc 中文格式硬约束——分镜 LLM 只给英文标签 few-shot 时倾向输出中文变体格式
# （qwen3.8-max：``[说话人] 船夫 （焦急） 快停桨`` 无冒号无引号），宿主解析器漏识别
# → 标签/角色/情绪被 TTS 念出并烧字幕。格式与宿主 dialogue.py 解析器（T1 词表）对齐；
# 音效不打标会被旁白念出是本约束的动机句。
# PR AI 评审七轮：抽取模块级常量供 ShotBriefDescription/ShotDescription 两处共用
# （复制粘贴是格式漂移类事故的温床）；标签 token 为语言无关字面量——宿主词表两种
# 语言都认，但统一中文形态可杜绝英文脚本输出英文变体标签的歧义。
_AUDIO_DESC_DESCRIPTION = (
    "A detailed description of the audio in the shot. The FORMAT is MANDATORY because a TTS parser "
    "reads this field aloud to generate the voice track — a malformed label WILL be spoken and burned "
    "into the subtitles:\n"
    "- Character dialogue (required shape): [说话人] 角色名（情绪）: 「台词」 — e.g. "
    "[说话人] 船夫（焦急）: 「快停桨，剑掉水里了。」 The parenthesized emotion is a short emotion "
    "word (焦急/平静/开心/悲伤/愤怒…), recommended for each speaking character.\n"
    "- Sound effects: EVERY ambient/sound description MUST start with the [音效] prefix — e.g. "
    "[音效] 小刀刻木声木屑落水声. An unlabeled sound description WILL be read aloud by the narrator "
    "as if it were a line of dialogue.\n"
    "- Narration (voice-over): plain text WITHOUT any bracket label.\n"
    "The bracket tags are language-independent tokens: use [说话人]/[音效] verbatim even when the "
    "script language is English.\n"
    "Use an empty string when the shot has no audio at all (do NOT emit null — the field is a plain string)."
)
_AUDIO_DESC_EXAMPLES = [
    "[音效] 小刀刻木声木屑落水声",
    "[说话人] 船夫（焦急）: 「快停桨，剑掉水里了。」",
    "水面一片沉寂，只有船桨划水的节奏",
    "",
]


class ShotBriefDescription(BaseModel):
    idx: int = Field(
        description="The index of the shot in the sequence, starting from 0.",
        examples=[0, 1, 2],
    )
    is_last: bool = Field(
        description="Whether this is the last shot. If True, the story of the script has ended and no more shots will be planned after this one.",
        examples=[False, True],
    )

    # visual
    cam_idx: int = Field(
        description="The index of the camera in the scene.",
        examples=[0, 1, 2],
    )
    visual_desc: str = Field(
        description='''A vivid and detailed visual description of the shot that convey rich visual information through text. The character identifiers in the description must match those in the character list and be enclosed in angle brackets (e.g., <Alice>, <Bob>). All visible characters should be described.
        If there is a conversation, please write down the content of the conversation), when you meet some dialogue, you should write into the visual content description with :" " symbols and the character's features (eg. <SLING> (male, late 20s, Texan accent softened by military precision, confident and energetic.) says: "Gear retracted. Flaps transitioning. Flight path stable. You are clear to climb."). 
        ''',
        examples=[
            "An over-the-shoulder shot at eye level, positioned behind <Alice>. The foreground, including <Alice>'s shoulder and head, is softly blurred, directing focus onto <Bob>'s face. <Bob>'s subtle reactions—shifting from surprise to delight—are clearly visible. The supermarket background is gently blurred with cool fluorescent lighting.",
        ]
    )


    # audio
    # 格式约束与示例见模块级 _AUDIO_DESC_DESCRIPTION/_AUDIO_DESC_EXAMPLES（两处共用）
    audio_desc: str = Field(
        description=_AUDIO_DESC_DESCRIPTION,
        examples=_AUDIO_DESC_EXAMPLES,
    )

    # sound_effect: Optional[str] = Field(
    #     default=None,
    #     description="The sound effects used in the shot.",
    #     examples=[
    #         "Ambient sound (supermarket background noise, shopping cart wheels rolling)",
    #         None,
    #     ],
    # )
    # speaker: Optional[str] = Field(
    #     default=None,
    #     description="The speaker in the shot, if applicable. If there is no speaker, this field should be set to None.",
    #     examples=[
    #         "Alice",
    #         None,
    #     ],
    # )
    # is_speaker_lip_visible: Optional[bool] = Field(
    #     default=None,
    #     description="Indicates whether the speaker's lips are visible in the shot. If there is no speaker, this field should be set to None.",
    #     examples=[
    #         True,
    #         False,
    #         None,
    #     ],
    # )
    # line: Optional[str] = Field(
    #     default=None,
    #     description="The dialogue or monologue in the shot, if applicable. If there is a speaker, there must be a line. If there is no speaker, this field should be set to None.",
    #     examples=[
    #         "Hello, how are you?",
    #         None,
    #     ],
    # )
    # emotion: Optional[str] = Field(
    #     default=None,
    #     description="The emotion of the speaker when delivering the line, if applicable. If there is a speaker, there must be an emotion. If there is no speaker, this field should be set to None.",
    #     examples=[
    #         "Happy",
    #         None,
    #     ],
    # )

    def __str__(self):
        s = f"Shot {self.idx}:\n"
        s += f"Camera Index: {self.cam_idx}\n"
        s += f"Visual: {self.visual_desc}\n"
        if self.audio_desc:
            s += f"Audio: {self.audio_desc}"
        return s


class ShotDescription(BaseModel):
    idx: int = Field(
        description="The index of the shot in the sequence, starting from 0."
    )
    is_last: bool = Field(
        description="Whether this is the last shot in the sequence. If True, no more shots will be planned after this one."
    )

    # visual
    cam_idx: int = Field(
        description="The index of the camera in the scene.",
        examples=[0, 1, 2],
    )
    visual_desc: str = Field(
        description='''A vivid and detailed visual description of the shot that convey rich visual information through text. The character identifiers in the description must match those in the character list and be enclosed in angle brackets (e.g., <Alice>, <Bob>).
        If there is a conversation, please write down the content of the conversation), when you meet some dialogue, you should write into the visual content description with :" " symbols and the character's features (eg. <SLING> (male, late 20s, Texan accent softened by military precision, confident and energetic.) says: "Gear retracted. Flaps transitioning. Flight path stable. You are clear to climb."). ''',
        examples=[
            "An over-the-shoulder shot at eye level, positioned behind <Alice>. The foreground, including <Alice>'s shoulder and head, is softly blurred, directing focus onto <Bob>'s face. <Bob>'s subtle reactions—shifting from surprise to delight—are clearly visible. The supermarket background is gently blurred with cool fluorescent lighting.",
        ]
    )
    variation_type: Literal["large", "medium", "small"] = Field(
        description="Indicates the degree of change in the shot's content.",
        examples=["large", "medium", "small"],
    )
    variation_reason: str = Field(
        description="The reason for the variation type of the shot.",
        examples=[
            "This is a transition shot where the content of the first frame and the last frame differs dramatically. So the variation type is large.",
            "Compared to the first frame, a new character appears in the last frame, and there are no significant changes in the composition. So the variation type is medium.",
            "Compared to the first frame, there are only minor changes in the composition. So the variation type is small.",
            "This shot only shows Alice speaking and the changes in her facial expressions, thus the variation type is small.",
        ],
    )

    ff_desc: str = Field(
        description="The first frame of the shot.",
        examples=[
            "Medium shot of a supermarket aisle at eye level. Bob(a tall man wearing a blue shirt and jeans) is positioned on the right side of the frame, captured in profile and facing right, while Alice(a young woman with short hair, wearing a green dress) is on the left, shown pushing a shopping cart with her gaze lowered toward the ground. They are arranged in a front-to-back spatial relationship. Shelves line both sides of the frame, and cool-toned fluorescent lighting from above washes over the scene. The vibrant colors of product packaging contrast with the metallic gray of the shopping cart, all contained within a stable, horizontally balanced composition.",
            "Extreme long shot. Aerial view from hundreds of meters above the ground. The boundless golden desert resembles undulating frozen waves, occupying the vast majority of the frame. At the very center of the image, a tiny, solitary explorer appears only as a faint dark speck, dragging a long, lonely trail of footprints behind him, stretching all the way to the edge of the frame.",
            "Medium shot at eye level angle. Designer A(with a beard, wearing a white suit) leans forward passionately, speaking emphatically. Product Manager B(with a beard, wearing a white T-shirt) sits with crossed arms, looking skeptical. Between them, Development Engineer C(brown hair, wearing a blue T-shirt) appears anxious, glancing between the two. Project Manager D(curly hair, wearing a red T-shirt) prepares to mediate, focusing on a whiteboard. Bright overhead lighting highlights their expressions, with a blurred whiteboard and glass wall in the background.",
            "A low-angle close-up shot captures the figure from below, framing him from the chest up. His face appears resolute and commanding, his eyes piercing as he speaks passionately. Flecks of saliva are visible, emphasizing his intensity. The overcast sky breaks with occasional light, casting him as a heroic, almost monumental figure against the gloom.",
            "An extremely close-up of an old, motionless pocket watch. Soft light highlights scratches on its brass case and the enamel dial with Roman numerals. The second hand remains fixed at 'VIII', casting a sharp shadow. A wrinkled finger gently touches the glass surface, evoking a tangible sense of stillness and time.",
            "An over-the-shoulder shot at eye level, positioned behind Character A(red hair, wearing a white T-shirt). The foreground, including A's shoulder and head, is softly blurred, directing focus onto Character B(with a beard, wearing a white T-shirt)'s face. B's subtle reactions—shifting from surprise to confusion, then to a glimmer of understanding—are clearly visible. The café background is gently blurred with warm lighting.",
        ]
    )
    ff_vis_char_idxs: List[int] = Field(
        default=[],
        description="The indices of the characters in the first frame.",
        examples=[
            [0, 1],
            [0],
            [],
        ],
    )
    lf_desc: str = Field(
        description="The last frame of the shot.",
    )
    lf_vis_char_idxs: List[int] = Field(
        default=[],
        description="The indices of the characters in the last frame.",
    )
    motion_desc: str = Field(
        description='''The motion description of the shot.
        If there is a conversation, please write down the content of the conversation), when you meet some dialogue, you should write into the visual content description with :" " symbols and the character's features (eg. SLING (male, late 20s, Texan accent softened by military precision, confident and energetic.) says: "Gear retracted. Flaps transitioning. Flight path stable. You are clear to climb."). If there is a narration, you should write into the visual content description with :" " symbols and the narration's features (eg. Narration: "Everything is looking good. "). ''',
    )

    # audio
    # 格式约束与示例见模块级 _AUDIO_DESC_DESCRIPTION/_AUDIO_DESC_EXAMPLES（两处共用）
    audio_desc: str = Field(
        description=_AUDIO_DESC_DESCRIPTION,
        examples=_AUDIO_DESC_EXAMPLES,
    )
    # sound_effect: Optional[str] = Field(
    #     default=None,
    #     description="The sound effects used in the shot. For example, a door creaking or footsteps approaching.",
    # )
    # speaker: Optional[str] = Field(
    #     default=None,
    #     description="The speaker in the shot, if applicable. If there is no speaker, this field should be set to None.",
    # )
    # is_speaker_lip_visible: Optional[bool] = Field(
    #     default=None,
    #     description="Indicates whether the speaker's lips are visible in the shot. If there is no speaker, this field should be set to None.",
    # )
    # line: Optional[str] = Field(
    #     default=None,
    #     description="The dialogue or monologue in the shot, if applicable. If there is a speaker, there must be a line. If there is no speaker, this field should be set to None.",
    # )
    # emotion: Optional[str] = Field(
    #     default=None,
    #     description="The emotion of the speaker when delivering the line, if applicable. If there is a speaker, there must be an emotion. If there is no speaker, this field should be set to None.",
    # )
