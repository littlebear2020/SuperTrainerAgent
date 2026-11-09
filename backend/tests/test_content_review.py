# backend/tests/test_content_review.py
import pytest

from app.gateway.video_gen.providers.content_review import is_content_review_rejection, is_image_content_rejection


@pytest.mark.parametrize(
    "msg",
    [
        # 360 提交期 4xx：code 12020 + 中文话术（服务器实测形态）
        '360 video submit failed (400): {"error":{"code":12020,"message":"命中敏感[色情:女裸露:女胸裸露]"}}',
        # 360 轮询期 FAILED：failure_reason 原文
        "Video task vg_8f31c56ed149 failed: 命中敏感[色情:女裸露:女胸裸露]",
        "360 image API 400: 内容审核不通过",
        "generate failed: 该请求包含敏感词，已被拦截",
        # 既有英文话术不回归
        "green net check rejected",
        "content policy violation",
        "sensitive content detected",
    ],
)
def test_review_rejections_detected(msg):
    assert is_content_review_rejection(RuntimeError(msg)) is True


@pytest.mark.parametrize(
    "msg",
    [
        "connection reset by peer",
        "HTTP 500: internal server error",
        "timeout after 30s",
    ],
)
def test_unrelated_errors_not_detected(msg):
    assert is_content_review_rejection(RuntimeError(msg)) is False


# ── 2026-08-19：图片输入内容检测拒绝（code 10940）——拦截对象是图不是提示词 ──


@pytest.mark.parametrize(
    "msg",
    [
        # 提交期 400 真实报文：code 10940 + 官方字面量双保险（探针实测形态）
        '360 seedance submit failed (400): {"error":{"code":10940,"message":"InputImageSensitiveContentDetected.PrivacyInformation, input image may contain privacy information"}}',
        # 轮询期 FAILED 形态：failure_reason 只透传文案、无 code 10940（P2-1）
        "Video task cgt-xxx failed: InputImageSensitiveContentDetected.PrivacyInformation",
        # 措辞漂移形态：去掉 Image 前缀（Input\w* 放宽的回归保护）
        "Video task cgt-xxx failed: InputSensitiveContentDetected.PrivacyInformation",
        # code 带空格形态
        '360 seedance submit failed (400): {"error": {"code": 10940, "message": "rejected"}}',
        # 纯字面量文本
        "InputImageSensitiveContentDetected",
    ],
)
def test_image_content_rejections_detected(msg):
    assert is_image_content_rejection(RuntimeError(msg)) is True


def test_image_content_detects_real_qihu_client_error():
    """提交期 surface 是 QihuClientError（RuntimeError 子类）——isinstance 链守护。"""
    from app.gateway.video_gen.providers.qihu_video import QihuClientError

    exc = QihuClientError(
        '360 seedance submit failed (400): {"error":{"code":10940,"message":"InputImageSensitiveContentDetected.PrivacyInformation"}}',
        400,
    )
    assert is_image_content_rejection(exc) is True


@pytest.mark.parametrize(
    "msg",
    [
        "connection reset by peer",
        "HTTP 500: internal server error",
        "timeout after 30s",
        # 负向前瞻：109400 不得被 10940 吞掉
        '360 seedance submit failed (400): {"error":{"code":109400,"message":"other"}}',
        # 裸 SensitiveContent 无 Input 前缀（放宽不误伤）
        "sensitive content detected in text",
        # 纯文本审核报文：归 is_content_review_rejection 管，两谓词职责分离
        "360 video submit failed (400): 命中敏感[色情:女裸露:女胸裸露]",
    ],
)
def test_image_content_not_detected_for_unrelated(msg):
    assert is_image_content_rejection(RuntimeError(msg)) is False


def test_timeout_error_not_image_content_rejection():
    """TimeoutError 是 OSError 子类非 RuntimeError——不进任何审核谓词。"""
    assert is_image_content_rejection(TimeoutError("InputImageSensitiveContentDetected")) is False


def test_text_review_routes_to_review_predicate_not_image():
    """职责分离：纯文本审核报文命中 is_content_review_rejection 而不命中图片谓词。"""
    exc = RuntimeError("360 video submit failed (400): 命中敏感[色情:女裸露:女胸裸露]")
    assert is_content_review_rejection(exc) is True
    assert is_image_content_rejection(exc) is False


def test_10940_report_hits_both_predicates_image_wins_by_order():
    """10940 报文同时命中两谓词（sensitive\s*content 分支）——梯子上图片分支
    前置是硬性约束（顺序约束的谓词层证据，接线顺序由 test_content_review_retry 守护）。"""
    msg = '360 seedance submit failed (400): {"error":{"code":10940,"message":"InputImageSensitiveContentDetected.PrivacyInformation"}}'
    assert is_image_content_rejection(RuntimeError(msg)) is True
    assert is_content_review_rejection(RuntimeError(msg)) is True
