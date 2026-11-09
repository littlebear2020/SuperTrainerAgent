"""Phase 2 Task 6: provider 支持 return_last_frame + 能力属性透传。

铁律（来自记忆 [[provider-capability-must-test-through-wrappers]]）：
- 新增能力属性必须补 Throttle/Fallback 两处透传
- 扩展 test_provider_capability_passthrough.py 参数化守护
- 禁止裸 MagicMock——用真实包裹链
- 变异验证：撤回透传看测试真红
"""

from app.gateway.video_gen.providers.base import VideoRef
from app.gateway.video_gen.providers.qihu_video import QihuVideoProvider
from app.gateway.video_gen.providers.registry import FallbackAIVideoProvider
from app.gateway.video_gen.providers.seedance_ark import SeedanceArkVideoProvider
from app.gateway.video_gen.providers.throttle import ThrottledAIVideoProvider


def _ark() -> SeedanceArkVideoProvider:
    return SeedanceArkVideoProvider(api_key="x", base_url="x", model="m")


def _qihu() -> QihuVideoProvider:
    return QihuVideoProvider(api_key="x", base_url="x", model="m")


def _throttled(inner):  # noqa: ANN001,ANN202
    return ThrottledAIVideoProvider(inner, qps=0.5, max_retries=5)


# ---------------------------------------------------------------------------
# supports_tail_frame 能力属性透传
# ---------------------------------------------------------------------------


class TestSupportsTailFramePassthrough:
    """supports_tail_frame 必须穿透 Throttle→Fallback 包裹链。"""

    def test_ark_has_supports_tail_frame(self):
        """SeedanceArkVideoProvider 声明 supports_tail_frame=True。"""
        assert _ark().supports_tail_frame is True

    def test_qihu_does_not_have_supports_tail_frame(self):
        """QihuVideoProvider 未声明 → getattr 兜底 False。"""
        assert getattr(_qihu(), "supports_tail_frame", False) is False

    def test_survives_throttle_wrapper(self):
        t = _throttled(_ark())
        assert t.supports_tail_frame is True, "Throttle 包裹后 supports_tail_frame 丢失（BUG-B 重演）"

    def test_survives_throttle_and_fallback_wrappers(self):
        chain = FallbackAIVideoProvider(_throttled(_ark()), _throttled(_qihu()))
        assert chain.supports_tail_frame is True

    def test_fallback_chain_qihu_primary_no_tail_frame(self):
        """primary 是 qihu（无 supports_tail_frame）→ 链上也为 False。"""
        chain = FallbackAIVideoProvider(_throttled(_qihu()), _throttled(_ark()))
        assert chain.supports_tail_frame is False


# ---------------------------------------------------------------------------
# VideoRef.last_frame_url 字段
# ---------------------------------------------------------------------------


class TestVideoRefLastFrameUrl:
    """VideoRef 增加可选 last_frame_url 字段。"""

    def test_default_none(self):
        ref = VideoRef(url="http://x", duration=5.0)
        assert ref.last_frame_url is None

    def test_can_set(self):
        ref = VideoRef(url="http://x", duration=5.0, last_frame_url="http://frame.png")
        assert ref.last_frame_url == "http://frame.png"
