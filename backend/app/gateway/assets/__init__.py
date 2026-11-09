"""平台级资产注册表（Phase 3.5，2026-09-11 ppt-master 6.3.0 批次）。

资产中心的后端：按形态组织的平台资产（首个类型 `ppt_template`），独立于
CourseProject 存在（自有存储/读取端点）。未来视频/声音资产迁入时以新
type 键扩展，不改数据模型与页面结构（按形态组织 Tab 的演化通路）。
"""

from .distiller import distill_pptx, remotion_theme_heuristic
from .store import AssetRecord, AssetStore

__all__ = ["AssetRecord", "AssetStore", "distill_pptx", "remotion_theme_heuristic"]
