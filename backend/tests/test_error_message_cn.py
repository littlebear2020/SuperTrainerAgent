"""P2-2: 错误文案中文化映射层。"""

from app.gateway._shared.task_framework.error_messages import to_user_message


def test_content_review_10940_mapped():
    assert "审核" in to_user_message(RuntimeError('360 video submit failed (400): {"error":{"code":"10940"}}'))


def test_rate_limit_429_mapped():
    assert "限流" in to_user_message(RuntimeError("rate limit exceeded (429)"))


def test_coverage_too_low_mapped():
    assert "画面" in to_user_message(RuntimeError("coverage too low: 3/10 frames"))


def test_budget_exceeded_mapped():
    assert "上限" in to_user_message(RuntimeError("video generation budget exceeded"))


def test_value_error_mapped():
    assert "参数" in to_user_message(ValueError("bad config"))


def test_unknown_error_preserved():
    """未知错误保留原文，不得吞掉排查信息。"""
    msg = to_user_message(RuntimeError("some unknown error xyz"))
    assert "some unknown error xyz" in msg


def test_frame_429_not_misjudged_as_rate_limit():
    """2026-08-08 P2-2：含数字 429 的无关错误不得误判为限流。"""
    msg = to_user_message(RuntimeError("Frame 429 generation failed"))
    assert "限流" not in msg
    assert "Frame 429" in msg


def test_value_error_keeps_raw_detail():
    """2026-08-08 P2-2：ValueError 兜底友好前缀 + 保留原文（修前吞原文）。"""
    msg = to_user_message(ValueError("bad param x"))
    assert "参数或配置错误" in msg
    assert "bad param x" in msg


def test_missing_font_detected_no_prefix():
    """missing-font-detected: font gate errors pass through raw — no ValueError prefix,
    font names preserved for the user.

    Mutation target: remove the special-case → ValueError fallback adds '参数或配置错误' prefix.
    """
    raw = "missing-font-detected: 检测到未嵌入字体「微软雅黑」、「宋体」，转视频可能出现文字换行错乱。"
    msg = to_user_message(ValueError(raw))
    assert "missing-font-detected" in msg
    assert "微软雅黑" in msg
    assert "参数或配置错误" not in msg  # no prefix — message is user-actionable as-is


def test_missing_font_detected_preserved_on_string_input():
    """to_user_message also accepts raw strings."""
    raw = "missing-font-detected: 检测到未嵌入字体「等线」"
    msg = to_user_message(raw)
    assert "missing-font-detected" in msg
    assert "等线" in msg
    assert "参数或配置错误" not in msg


# ── T1（2026-08-31 错误分诊）：coverage 错误抽 First error 递归分诊 ──────────
# 触发任务 vg_a01c4ceb1a3c：H3 比例硬校验 [0.4,2.5] 拒收 2.783 横排合成图，
# 8 帧全败 → coverage 错误整表映射「服务商繁忙」——参数类错误被伪装成基础设施
# 故障，用户重试必然再败。


def test_coverage_invalid_param_surfaces_real_reason():
    """参数类失败透出真实原因（含原始数值），不再误报「服务商繁忙」；
    误导性英文尾句（Provider may be slow...）丢弃。"""
    raw = (
        "AI video generation coverage too low: 0/8 frames (0% < 50%), 8 placeholder(s). "
        'First error: Frame 3: 360 video submit failed (400): {"error":{"code":"1004",'
        '"message":"invalid param: image aspect ratio 2.783 (=1536/552), expected in [0.4, 2.5]"}}. '
        "Provider may be slow, rate-limited, or down — please retry."
    )
    msg = to_user_message(RuntimeError(raw))
    assert "参考图比例超限" in msg
    assert "image aspect ratio 2.783 (=1536/552), expected in [0.4, 2.5]" in msg
    assert "视频生成失败" in msg
    assert "服务商" not in msg  # 参数错误不得伪装成服务商故障
    assert "Provider may be slow" not in msg


def test_coverage_invalid_param_non_ratio_label():
    """invalid param 但非比例类 → 标「参数不合规」，原因原样透出。"""
    raw = "AI video generation coverage too low: 0/4 frames (0% < 50%), 4 placeholder(s). First error: Frame 0: submit failed (400): invalid param: duration must be an integer. Provider may be slow, rate-limited, or down — please retry."
    msg = to_user_message(RuntimeError(raw))
    assert "参数不合规" in msg
    assert "duration must be an integer" in msg


def test_coverage_timeout_still_maps_provider_busy():
    """分诊递归映射：首个失败是超时 → 服务商类文案（该类指认仍然成立）；
    「服务商繁忙」类文案只经真实原因分诊产生，不整表误挂。"""
    raw = "AI video generation coverage too low: 2/8 frames (25% < 50%), 6 placeholder(s). First error: Frame 1: connection timeout after 120s. Provider may be slow, rate-limited, or down — please retry."
    msg = to_user_message(RuntimeError(raw))
    assert "服务商响应超时" in msg
    assert "Provider may be slow" not in msg


def test_coverage_without_first_error_has_fallback():
    """无 First error 的 coverage 错误 → 中文兜底，不指认服务商。"""
    msg = to_user_message(RuntimeError("coverage too low: 3/10 frames"))
    assert "成功率过低" in msg
    assert "服务商" not in msg
