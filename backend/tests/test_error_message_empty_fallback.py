"""W6b/D1（2026-08-24 看门狗活性重构）：error_message 空串统一兜底。

08-20 事故：任务 failed 且 error_message 为空串。TimeoutError 空消息产物
``TimeoutError: `` 经 to_user_message 后 raw 为空，现有 "timeout" 中文映射
匹配不上 -> 用户看到空错误。修法：**仅当 str(exc) 为空时**前置类型名
（空产物 ``TimeoutError`` 命中 "timeout" 映射）；非空原文零回归。
"""

from __future__ import annotations

from app.gateway._shared.task_framework.error_messages import to_user_message


def test_timeout_error_message_not_empty():
    """空串 TimeoutError -> 前置类型名 -> 命中既有 "timeout" 中文映射。"""
    msg = to_user_message(TimeoutError())
    assert msg, "空消息异常不得产出空 error_message"
    assert msg == "服务商响应超时，请重试", msg


def test_empty_string_message_gets_type_prefix():
    """str(exc) 为空串的非异常输入 -> 用类型名兜底（RuntimeError 等）。"""
    msg = to_user_message(RuntimeError())
    assert "RuntimeError" in msg
    # ValueError/TypeError 有既有友好前缀分支（非空原文路径）——空串时类型名先行
    msg2 = to_user_message(ValueError())
    assert msg2, "不得为空"


def test_nonempty_message_unchanged():
    """非空原文零回归：映射逻辑与原文透传完全不变。"""
    assert to_user_message(RuntimeError("boom 500")) == "boom 500"
    assert to_user_message("360 video submit failed (429): limited") == "服务商限流，请稍后重试"
    # T1（2026-08-31 错误分诊）：coverage 整表映射废除——无 First error 时中文兜底，
    # 不再指认服务商（参数类失败曾因此被伪装成基础设施故障，见 test_error_message_cn.py）
    assert to_user_message("coverage too low") == "画面生成成功率过低，请重试；若持续失败请稍后再试或减少分镜数量"
    assert to_user_message(ValueError("bad thing")) == "参数或配置错误：bad thing"
    # 空串字符串输入（DB 里直接存的空 error）-> 类型兜底不可用，保留空（调用方另有兜底）
    assert to_user_message("") == ""


def test_vimax_storyboard_deadline_message():
    """Plan C：本地分镜 deadline → 可行动文案（批次可复用），不落「服务商响应超时」。"""
    from app.gateway._shared.task_framework.error_messages import to_user_message

    assert to_user_message("vimax_storyboard_deadline: local storyboard deadline of 300s exceeded") == "分镜生成超时，请重试；已完成批次会被复用"


def test_vimax_deadline_message_branches_on_batches():
    """[P1-4] 异常元数据分支：批次可复用→承诺复用；单批→不承诺（防虚假承诺）。"""
    from app.gateway._shared.task_framework.error_messages import to_user_message

    class _Batched(TimeoutError):
        error_code = "vimax_storyboard_deadline"
        batches_reusable = True

    class _Single(TimeoutError):
        error_code = "vimax_storyboard_deadline"
        batches_reusable = False

    assert to_user_message(_Batched("vimax_storyboard_deadline")) == "分镜生成超时，请重试；已完成批次会被复用"
    assert to_user_message(_Single("vimax_storyboard_deadline")) == "分镜生成超时，请重试"
