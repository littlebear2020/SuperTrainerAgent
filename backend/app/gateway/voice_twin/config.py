"""Voice clone configuration model."""

from pydantic import BaseModel, Field


class QihuTTSConfig(BaseModel):
    """Configuration for 360 TTS preset voices."""

    api_key: str = Field(default="", description="360 TTS API key")
    enabled: bool = Field(default=True, description="Enable 360 TTS preset voices")


class DoubaoTTSConfig(BaseModel):
    """Configuration for ByteDance doubao seed-tts-2.0 TTS (Stage 2a).

    The API key is NOT a separate field — it reuses
    ``VoiceCloneConfig.access_key`` (single openspeech account, same
    ``X-Api-Key`` header). The speaker comes from the ``doubao:<speaker>``
    voice id (resolve_default_voice reads DOUBAO_TTS_DEFAULT_SPEAKER); the
    openspeech resource id is fixed inside DoubaoTTSClient. See
    backend/docs/BYTEDANCE_MODELS_INTEGRATION_PLAN.md §阶段 2a + §0.2.
    """

    enabled: bool = Field(default=True, description="Enable doubao TTS path")


class EdgeTTSConfig(BaseModel):
    """edge-tts 配置（A2，2026-08-09）：配音模块的免费多语种引擎 + 降级链末端兜底。

    默认开启——公司服务器实测 edge 直连可达（不稳定），兜底链是有效安全网
    （memory edge-tts-reachable-on-company-server）；不可达部署可关。
    """

    enabled: bool = Field(default=True, description="Enable edge-tts voices and fallback")
    timeout: float = Field(default=60.0, description="edge-tts stream timeout in seconds")


class VoiceCloneConfig(BaseModel):
    """Configuration for Volcengine Mega TTS voice cloning."""

    enabled: bool = Field(default=False, description="Enable voice clone feature")
    appid: str = Field(default="", description="Volcengine app ID")
    token: str = Field(default="", description="Volcengine access token (V1 legacy)")
    access_key: str = Field(default="", description="Volcengine API Key for V3 auth (shared by doubao TTS)")
    domain: str = Field(default="https://openspeech.bytedance.com", description="Volcengine TTS API domain")
    # B2（2026-08-09）：default_model_type 死配置已删除——全后端无消费点，
    # 默认值五方统一为 5（ICL 3.0）：schemas.py / models.py DB 列 / 前端选择器。
    default_language: int = Field(default=0, description="Default language (0=Chinese)")
    max_upload_bytes: int = Field(default=10485760, description="Max upload file size in bytes (10MB)")
    min_duration_seconds: float = Field(default=30.0, description="Minimum audio duration in seconds")
    training_timeout_minutes: int = Field(default=30, description="克隆训练超时（分钟），超时未就绪标 failed")
    training_reaper_interval_minutes: int = Field(default=5, description="训练行巡检间隔（分钟），0=关闭 reaper")
    storage_reaper_interval_minutes: int = Field(default=60, description="存储清理巡检间隔（分钟），0=关闭 reaper（VT-2/R3，2026-09-09）")
    status_query_timeout: float = Field(default=30.0, description="训练状态查询 HTTP 超时（秒）（D8b：10s→30s，高峰期慢响应不再误判轮询失败）")
    storage_dir: str = Field(default=".deer-flow/voice-twins", description="Base directory for voice twin files")
    qihu_tts: QihuTTSConfig = Field(default_factory=QihuTTSConfig)
    doubao_tts: DoubaoTTSConfig = Field(default_factory=DoubaoTTSConfig)
    edge_tts: EdgeTTSConfig = Field(default_factory=EdgeTTSConfig)
