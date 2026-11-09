"""Provider 能力声明表（2026-08-03 Task 13）。

修前能力散落且不成契约：只有 ``seedance_ark`` 声明 ``supports_image_urls`` /
``max_video_duration``，其余 provider 什么都不声明，调用方靠 ``getattr(p, "...", 8.0)``
猜（``visual_sources/ai_video.py``、``throttle.py``、``registry.py`` 等多处）。
``providers/base.py`` 的 Protocol 里也没有任何能力字段。

为什么是**静态表**而不是实例化后反射：``available_video_providers`` 刻意不构造
provider——曾因构造泄漏句柄（2→29，commit 4b3413471）。前端要能力元数据，就必须
不付构造代价拿到。

新增 provider 时：在此登记一行 + 在 provider 类上声明同名属性，
``test_capability_matches_provider_class_attrs`` 与
``test_every_known_video_provider_is_registered`` 会锁死二者一致。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class VideoCapability:
    max_duration: float = 8.0
    min_duration: float = 0.0
    integer_seconds: bool = False
    supports_image_urls: bool = False
    supported_ratios: tuple[str, ...] = field(default=("16:9", "9:16", "1:1"))


# 保守默认：与 ai_video.py 的 getattr fallback 一致（8s、无 i2v）。
_CONSERVATIVE = VideoCapability()

_VIDEO_CAPABILITIES: dict[str, VideoCapability] = {
    "seedance-ark": VideoCapability(
        max_duration=15.0,
        min_duration=4.0,
        integer_seconds=True,
        supports_image_urls=True,
        supported_ratios=("16:9", "9:16", "1:1", "4:3", "3:4"),
    ),
    # T1（2026-08-17 H3 duration 400 修复批）：[4,15] 整数秒。
    # minimax-h3 = 网关 400 报文实锤（supported durations: 4s...15s）；
    # seedance-360 = 与 Ark 同模型按官方规格声明（D2；实探针见修复计划 E2E）。
    # T3（2026-08-18）：两者 supports_image_urls=True——实际都消费 image_urls
    # （seedance r2v 多参考/i2v 首帧；H3 官方示例9 首帧），修前漏声明致
    # ai_video 闸门误判「不支持图生视频」。
    "seedance": VideoCapability(
        max_duration=15.0,
        min_duration=4.0,
        integer_seconds=True,
        supports_image_urls=True,
        # E1（2026-08-21 360 主力切换）：ratio 起真实下发（T1 实测 9:16 生效），
        # 与 Ark 同模型族对齐官方比例集（21:9/16:9/4:3/1:1/3:4/9:16）——修前缺
        # 1:1/4:3/3:4/21:9，前端按能力校验会拒 1:1 任务（AC3 三档之一）。
        supported_ratios=("16:9", "9:16", "1:1", "4:3", "3:4", "21:9"),
    ),
    # E2（2026-08-21）：fast/mini 型号选项——同一 SeedanceVideoProvider 类
    # （model id 不同），能力与主力一致（2.0 族 [4,15] 整数秒 + r2v/i2v）；
    # 分辨率上限差异（仅 480p/720p）走 E5 选择器标注，不在能力表硬限。
    "seedance-fast": VideoCapability(
        max_duration=15.0,
        min_duration=4.0,
        integer_seconds=True,
        supports_image_urls=True,
        supported_ratios=("16:9", "9:16", "1:1", "4:3", "3:4", "21:9"),
    ),
    "seedance-mini": VideoCapability(
        max_duration=15.0,
        min_duration=4.0,
        integer_seconds=True,
        supports_image_urls=True,
        supported_ratios=("16:9", "9:16", "1:1", "4:3", "3:4", "21:9"),
    ),
    # T3（2026-08-18）：H3 图生视频声明——官方实锤（create-video-generation.md
    # 示例9：单图无 role 首帧 i2v / 示例10：双图按序首尾帧）；类侧 supports_image_urls
    # property 按 _is_h3_model 门控（一致性锁见 test_provider_capabilities）。
    "minimax-h3": VideoCapability(
        max_duration=15.0,
        min_duration=4.0,
        integer_seconds=True,
        supports_image_urls=True,
        supported_ratios=("16:9", "9:16"),
    ),
    # T3（2026-08-18）：happyhorse 图生视频声明——官方示例3 media:[{type:first_frame}]；
    # 修前漏声明（同类 bug，拍板⑤）。是否接受 data-URI 按探针 3 结论。
    "happyhorse": VideoCapability(max_duration=8.0, supports_image_urls=True, supported_ratios=("16:9", "9:16")),
}


def video_capability(provider_name: str) -> VideoCapability:
    """查 provider 能力；未知名字返回保守默认（不乐观假设）。"""
    return _VIDEO_CAPABILITIES.get((provider_name or "").lower(), _CONSERVATIVE)
