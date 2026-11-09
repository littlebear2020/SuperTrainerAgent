#!/usr/bin/env python3
"""PreToolUse hook: 拦截以 sleep 开头的前台等待/轮询命令。

协议：Claude Code PreToolUse 钩子，stdin 收 JSON，
退出码 2 = 拦截，stderr 内容回传给模型作为纠偏指令；退出码 0 = 放行。

判定（2026-10-10 v2，经 Trae 冒烟测试修正）：
- 拦截：前台行首 sleep（裸等，或 `sleep N; tail x.log` 轮询形态）；
- 拦截：后台行首 sleep 后接其他命令（延迟采样仍是轮询）；
- 放行：后台**裸** `sleep N`（run_in_background=true 且后不接命令）——
  本环境无阻塞读取原语，这是合法的定时器，配合完成通知做事件驱动等待；
- 放行：非行首 sleep（for/while 有界循环内、功能性退避、多行脚本中部）。
"""
import json
import re
import sys

LEADING = re.compile(r"^\s*(?:sudo\s+)?sleep\s+[0-9]")
BARE_TIMER = re.compile(r"^\s*(?:sudo\s+)?sleep\s+\d+(?:\.\d+)?\s*(?:#.*)?$")  # 前缀/数值形态与 LEADING 对齐（PR#14 评审修复）

MESSAGE = (
    "已拦截：以 sleep 开头的等待/轮询命令。按 AGENTS.md「长命令执行规范」改用：\n"
    "1) 长命令（>120s）一律加 run_in_background: true 后台执行，完成通知到达后续接；\n"
    "2) 纯等待 = 后台裸定时器：run_in_background: true 启动单独的 sleep N（后不接其他命令），"
    "随后结束本轮，完成通知会唤醒你；\n"
    "3) 就绪探测用单条有界循环：\n"
    '   for i in $(seq 1 60); do curl -sf -o /dev/null <URL> && { echo READY; exit 0; }; sleep 2; done; echo "NOT READY" >&2; exit 1\n'
    "注意：sleep N; tail/查看 即使后台运行也属延迟采样——直接等被等任务的完成通知即可。"
)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        # 有意 fail-open（钩子不该堵死工具调用），但留 stderr 痕迹防「协议变化导致从未生效」不可观测（PR#14 评审）
        print("guard_sleep_poll：stdin JSON 解析失败，按 fail-open 放行", file=sys.stderr)
        return 0
    if payload.get("tool_name") != "Bash":
        return 0
    tool_input = payload.get("tool_input") or {}
    command = tool_input.get("command") or ""
    if not LEADING.match(command):
        return 0
    # 后台裸 sleep = 合法定时器（本环境无阻塞读取，靠完成通知做事件驱动等待）
    if tool_input.get("run_in_background") and BARE_TIMER.match(command):
        return 0
    sys.stderr.write(MESSAGE)
    return 2


if __name__ == "__main__":
    sys.exit(main())
