"""Volcengine Mega TTS error code to user-friendly message mapping."""

ERROR_MESSAGES: dict[int, str] = {
    0: "成功",
    1001: "请求参数有误，请检查后重试",
    1101: "音频上传失败，请重新尝试",
    1102: "语音识别失败，请确保音频清晰可辨",
    1103: "声纹检测失败",
    1104: "声纹与名人相似度过高，无法克隆",
    1105: "获取音频数据失败",
    1106: "SpeakerID 重复",
    1107: "SpeakerID 未找到",
    1108: "音频转码失败，请尝试其他格式",
    1109: "音频与文本匹配度过低，请对着提词文本清晰朗读",
    1111: "未检测到说话声，请确保音频中有人声",
    1112: "音频噪声过大，建议在安静环境重新录制",
    1113: "降噪处理失败",
    1114: "音频质量过低，降噪处理失败",
    1122: "未检测到人声",
    1123: "该音色上传次数已达上限(10次)",
}

UNKNOWN_ERROR = "未知错误，请稍后重试"


def get_friendly_error(code: int | None, *, for_failure: bool = False) -> str:
    """Return a Chinese-friendly error message for a Volcengine error code.

    for_failure=True 时排除 0（"成功"）——失败路径显示"成功"是 2026-07-22 审计发现的陷阱。
    """
    if code is None or (for_failure and code == 0):
        return UNKNOWN_ERROR
    return ERROR_MESSAGES.get(code, UNKNOWN_ERROR)
