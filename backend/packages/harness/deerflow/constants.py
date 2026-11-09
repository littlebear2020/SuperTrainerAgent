"""Shared runtime protocol constants.

P1-S2/S3（2026-09-30 上游移植）落位文件：上游 constants.py 携带大量本仓
尚未移植的子系统常量（conversation/browser/mcp/run-event），这里只落
skills 存储层（deerflow.skills.storage）依赖的这一项；后续批次移植对应
子系统时再各自补齐，保持本模块 dependency-free 的定位。
"""

DEFAULT_SKILLS_CONTAINER_PATH = "/mnt/skills"
