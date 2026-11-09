"""Resource-failure signature detection for bash tool outputs.

F3（2026-09-29 长任务中断修复计划）：资源饥饿击杀（容器 cgroup OOM 的 SIGKILL、
Chrome 多进程死亡抛的 Target closed、内存分配失败）此前被 bash 工具统一的
``Error: ...`` 回执吞掉，agent 看不到特异性信号，只会原样重跑——同一命令再崩，
直到撞上循环检测硬停并烧掉大量 token。本模块按签名清单识别这类失败，命中时在
回执尾部追加中文指引块（降并发 / 分段 / 清缓存，不要原样重跑）。

误报控制（计划风险 R-4）：
  * 只扫描**尾部窗口**——击杀证据几乎总在输出的最后几行（崩溃前的日志尾部），
    头部出现而尾部没有的字符串视为历史噪音；
  * ``Killed`` 只认行首独立词（OOM killer / shell 的击杀回执形态），普通日志
    中句中出现的 "Killed" 不触发；
  * ``chrome-headless`` 单独出现不触发，须与崩溃证据共现。

纯函数、零依赖：bash_tool 本地/远程两分支在 truncate 前调用
``append_failure_guidance(output)``，未命中时原样返回（零开销、零行为变化）。
"""

from __future__ import annotations

import re

# 尾部窗口大小：资源击杀证据在末尾；头部的历史匹配按噪音处理。
_TAIL_WINDOW_CHARS = 2000

# 独立签名（尾部窗口内子串/正则命中即判定）。
_RE_SIGNAL_KILL = re.compile(r"signal:?\s*(?:SIGKILL|9)\b")
_RE_KILLED_LINE_START = re.compile(r"^\s*Killed\b", re.MULTILINE)
_RE_OUT_OF_MEMORY = re.compile(r"out of memory", re.IGNORECASE)
_SUBSTRING_SIGNATURES = (
    "Target closed",
    "Cannot allocate memory",
)
# chrome-headless 与崩溃证据须共现（单独出现在健康日志里不判资源失败）。
_RE_CHROME_HEADLESS = re.compile(r"chrome-headless", re.IGNORECASE)
_RE_CRASH_EVIDENCE = re.compile(r"crash|segmentation fault|core dumped|aborted|died", re.IGNORECASE)

_GUIDANCE_BLOCK = (
    "\n\n<resource_failure_guidance>\n"
    "⚠️ 疑似资源不足被杀（沙箱内存/共享内存上限）：以上输出包含资源类失败签名"
    "（SIGKILL / Target closed / 无法分配内存等）。\n"
    "不要原样重跑——原样重跑大概率再次被杀。按以下降级序处置：\n"
    "1. 降并发重试：`--concurrency=1`（最有效）；\n"
    "2. 分段渲染：加 `--frames=起-止`，每段 ≤6000 帧，逐段渲染、只重跑失败段；\n"
    "3. 清理后重试：`rm -rf node_modules/.cache`，必要时 `pkill -f chrome-headless`；\n"
    "4. 渲染前自检内存/磁盘（见 auto-video-flow 技能「第四步」资源自检命令）。\n"
    "</resource_failure_guidance>"
)


def detect_resource_failure(output: str) -> str | None:
    """Return a short label for the matched resource-failure signature, or None.

    Scans only the tail window of *output* (see module docstring for the
    false-positive rationale). Never raises.
    """
    if not output:
        return None
    tail = output[-_TAIL_WINDOW_CHARS:]

    if "Target closed" in tail:
        return "target_closed"
    if _RE_SIGNAL_KILL.search(tail):
        return "sigkill"
    if "Cannot allocate memory" in tail:
        return "cannot_allocate_memory"
    if _RE_KILLED_LINE_START.search(tail):
        return "killed"
    if _RE_OUT_OF_MEMORY.search(tail):
        return "out_of_memory"
    if _RE_CHROME_HEADLESS.search(tail) and _RE_CRASH_EVIDENCE.search(tail):
        return "chrome_headless_crash"
    return None


def append_failure_guidance(output: str) -> str:
    """Return *output* unchanged, or with the Chinese guidance block appended.

    The block is appended at the very end so it survives bash output
    middle-truncation (which preserves head + tail).
    """
    if detect_resource_failure(output) is None:
        return output
    return output + _GUIDANCE_BLOCK
