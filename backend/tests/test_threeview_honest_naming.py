# backend/tests/test_threeview_honest_naming.py
#
# 注意（2026-10-05 评审口径）：本文件只钉 service.py 里那条误导性中文注释
# （「传完整三视图」）已被删除——防注释复活，不证明任何运行时行为。
# provider_bridge 的实际行为（不读 character_sheets）由 provider_bridge 测试覆盖。
import inspect

from app.gateway.video_gen import service as svc


def test_misleading_threeview_comment_removed():
    """D: service.py 误导性「传完整三视图」注释须删除/改正（仅注释钉，见文件头注）。"""
    src = inspect.getsource(svc)
    assert "传完整三视图" not in src, "误导注释仍在：provider_bridge 实际不读 character_sheets"
