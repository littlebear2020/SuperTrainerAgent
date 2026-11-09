# VENDOR PATCH (TrainerAI, P35): novel2movie 死模型（Event/Scene/Frame/
# EnvironmentInScene/CharacterInEvent/CharacterInNovel）已随管线拆除（2026-10-16，
# 用户拍板决策①）移出导出；活路径模型（script2video/idea2video 消费面）原样保留。
from .camera import Camera
from .character import CharacterInScene
from .image_output import ImageOutput
from .shot_description import ShotDescription, ShotBriefDescription
from .video_output import VideoOutput

__all__ = [
    "Camera",
    "CharacterInScene",
    "ImageOutput",
    "ShotBriefDescription",
    "ShotDescription",
    "VideoOutput",
]
