"""退役 agent 别名映射（条目 6，2026-09-12 缺陷批）。

后端单一事实源；与 ``frontend/src/core/agents/retired-mapping.ts`` 内容一致——
跨仓守护测试 ``tests/test_retired_agent_alias.py`` 解析 TS 源文件断言两侧键值
同步（参照 layout-content-schema 跨仓守卫模式）。

消费点：
- 前端 ``resolveAgentName``：agents/[agent_name]/chats 页、agent-card、
  threads/utils、project-list（旧线程/旧 URL 的续跑通路）。
- 后端 ``services._apply_retired_agent_aliases``（``start_run`` 入口）：API 直调
  旧名的 run 被改写到承接 agent 启动，而非后台裸 FileNotFoundError（带服务器
  绝对路径）经 SSE 暴露。

与 ``backend/scripts/migrate_retired_agent_memory.py`` 的 MIGRATION_TARGETS 目标
一致（该脚本另带归档后缀，语义不同，不共用常量）。
"""

from __future__ import annotations

RETIRED_AGENT_ALIAS: dict[str, str] = {
    "card-poster": "creative-studio",
    "case-article": "creative-studio",
    "design-specialist": "creative-studio",
    "knowledge-extraction": "creative-studio",
    "video-clip": "creative-studio",
    "course-ppt": "ppt-master",
}


def resolve_retired_agent(name: str) -> str:
    """退役名 → 承接 agent；未知名原样返回（与前端 resolveAgentName 同语义）。

    查找前做与 ``services._gate_agent_runnable`` 同款归一化（strip + lower +
    下划线→连字符；复审二轮 2026-09-13）：API 直调 ``Course_PPT``/``COURSE-PPT``
    也须改写到承接 agent，而非落进 gate 的 FileNotFoundError 404。
    """
    key = (name or "").strip().lower().replace("_", "-")
    return RETIRED_AGENT_ALIAS.get(key, name)
