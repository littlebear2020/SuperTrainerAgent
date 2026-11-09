"""Shared constants for media cost classification (C3/C8).

These are the single source of truth for mapping ``external_api_calls.method``
to a media type and for identifying free operations. Both the console
``/external-calls`` endpoint (C3 cost estimation, C8 type grouping) and any
guard tests reference these constants — never inline a duplicate.
"""

from __future__ import annotations

#: Maps every ``method`` value that appears in ``external_api_calls`` rows to
#: its media type. When a new tracking method is added, update this dict and
#: the guard test (``test_media_constants.py``) will catch the omission.
METHOD_TO_MEDIA_TYPE: dict[str, str] = {
    "generate_image": "image",
    "generate_video": "video",
    "synthesize_sse": "tts",
    # 条目 10①（2026-09-12 缺陷批）：与 synthesize_sse 同端点同计费类（仅多
    # enable_subtitle=True），531e2945e 引入时漏登记 → fail-loud 守卫红。
    "synthesize_sse_with_timestamps": "tts",
    "voice_clone_upload": "tts",
}

#: Free operations: excluded from cost estimation and ``unpriced_calls``
#: counting. ``voice_clone_upload`` is always free but the call count is still
#: tracked so operators can see volume.
FREE_METHODS: frozenset[str] = frozenset({"voice_clone_upload"})

#: C2（2026-08-27 成本可信化批次）：``metadata_json.model`` → 规范化定价键
#: ``(provider, model_id)`` 的别名表。**代码契约而非运营参数**（故落常量不落配置）。
#:
#: 实测三套命名空间并存（step 0 值域探针，测试服 DB 2026-08-27）：
#: - throttle 路径写**注册名**（sink_label，如 ``seedance-fast``、``seedance``）；
#: - provider 实发**真实模型 id**（如 ``bytedance/seedance-2-0-fast``、
#:   ``doubao-seedream-5-0-pro-260628``，ViMax image 桥直接落 provider 桥的 model）；
#: - ViMax video 桥写 **backend 名**（如 ``seedance-ark``，即注册名兼 backend 名）。
#: 另有存量 ``provider="qihu"`` 历史行 model=``minimax-h3``（A15 实测修正）。
#:
#: 刻意**不映射**：``preset:*``（qihu_tts 的音色 id，非模型名——必须回落
#: provider 级行）；``nano-banana``/``seed-icl-2.0``（实测不在场且已弃用，
#: 落 unpriced 计数可见）。
PROVIDER_MODEL_ALIASES: dict[str, tuple[str, str]] = {
    # seedance 系（360 通道 + Ark 直连同模型两通道，§一 三档价）
    "seedance": ("seedance", "bytedance/seedance-2-0"),
    "seedance-fast": ("seedance", "bytedance/seedance-2-0-fast"),
    "seedance-mini": ("seedance", "bytedance/doubao-seedance-2-0-mini"),
    "bytedance/seedance-2-0": ("seedance", "bytedance/seedance-2-0"),
    "bytedance/seedance-2-0-fast": ("seedance", "bytedance/seedance-2-0-fast"),
    "bytedance/doubao-seedance-2-0-mini": ("seedance", "bytedance/doubao-seedance-2-0-mini"),
    "seedance-ark": ("seedance-ark", "doubao-seedance-2-0-260128"),
    "doubao-seedance-2-0-260128": ("seedance-ark", "doubao-seedance-2-0-260128"),
    # minimax-h3（360 渠道；注册名/模型 id；含存量 qihu 历史行）
    "minimax-h3": ("minimax-h3", "minimax/MiniMax-H3"),
    "minimax/MiniMax-H3": ("minimax-h3", "minimax/MiniMax-H3"),
    # 生图（gpt-image-2 / seedream 双通道）
    "gpt-image-2": ("gpt-image-2", "openai/gpt-image-2"),
    "openai/gpt-image-2": ("gpt-image-2", "openai/gpt-image-2"),
    "seedream-360": ("seedream-360", "bytedance/doubao-seedream-5.0-pro"),
    "bytedance/doubao-seedream-5.0-pro": ("seedream-360", "bytedance/doubao-seedream-5.0-pro"),
    "seedream": ("seedream", "doubao-seedream-5-0-pro-260628"),
    "doubao-seedream-5-0-pro-260628": ("seedream", "doubao-seedream-5-0-pro-260628"),
    # dashscope（通义万相，现役可选）
    "dashscope": ("dashscope", "wanx-v1"),
    "wanx-v1": ("dashscope", "wanx-v1"),
}
