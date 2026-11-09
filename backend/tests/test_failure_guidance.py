"""F3（2026-09-29 长任务中断修复计划）：bash 工具资源类失败感知回执。

资源饥饿击杀（cgroup OOM / SIGKILL / Chrome 死亡的 Target closed）此前被统一
"Error: ..." 回执吞掉，agent 原样重跑再崩、烧穿循环检测与 token。本模块按签名
清单（尾部窗口匹配，降误报）识别资源类失败，命中时在回执尾部追加中文指引块。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from deerflow.sandbox.failure_guidance import (
    append_failure_guidance,
    detect_resource_failure,
)


class TestDetectSignatures:
    """命中矩阵：每个签名至少一个真实形态样本。"""

    @pytest.mark.parametrize(
        "output",
        [
            "Rendering frame 9120...\nError: Exec failed, signal SIGKILL",
            "Rendering frame 9120...\nError: process exited with signal: 9",
            "npx remotion render ...\nError: Target closed",
            "Error: TypeError: Cannot read properties of null (reading 'from')\nFatal: Cannot allocate memory",
            "Memory cgroup out of memory: Killed process 3141 (chrome-headless)",
            "FATAL ERROR: Reached heap limit - Out of Memory",
            "Killed\n",
            "bundle.js\n  ✗ chrome-headless shell crashed (segmentation fault)",
        ],
        ids=["sigkill", "signal-9", "target-closed", "alloc-mem", "cgroup-oom", "heap-oom", "killed-line-start", "chrome-crash"],
    )
    def test_signatures_hit(self, output):
        assert detect_resource_failure(output) is not None

    @pytest.mark.parametrize(
        "output",
        [
            "ok\nbundle.js  120 kB\n",
            "2 files changed, 10 insertions(+)",
            "Info: channel was Killed mid-flight by peer, reconnecting",  # "Killed" 非行首独立词
            "Error: Exec failed, signal SIGTERM",  # 超时非 OOM，处置不同（gotchas 口径）
            "Error: process exited with signal: 15",
            "The killer process found nothing killed yet",  # 小写 killed、非行首
            "",
        ],
        ids=["normal", "normal-diff", "killed-midline", "sigterm", "signal-15", "killed-lowercase", "empty"],
    )
    def test_signatures_miss(self, output):
        assert detect_resource_failure(output) is None

    def test_signature_outside_tail_window_not_detected(self):
        """尾部窗口：击杀证据几乎总在末尾；头部出现而尾部没有 → 不判定（降误报）。"""
        filler = "x" * 4000
        output = "Error: Target closed\n" + filler
        assert detect_resource_failure(output) is None

    def test_signature_inside_tail_window_detected(self):
        filler = "x" * 4000
        output = filler + "\nError: Target closed"
        assert detect_resource_failure(output) is not None

    def test_chrome_headless_alone_not_enough(self):
        """chrome-headless 正常出现（无崩溃证据）不触发。"""
        assert detect_resource_failure("chrome-headless shell is up and healthy") is None


class TestAppendGuidance:
    def test_no_signature_returns_output_unchanged(self):
        output = "all good\n"
        assert append_failure_guidance(output) == output

    def test_signature_appends_guidance_block(self):
        output = "Rendering frame 9120...\nError: Exec failed, signal SIGKILL"
        result = append_failure_guidance(output)
        assert result.startswith(output)
        tail = result[len(output) :]
        # 关键指引要素（计划定稿文案）：不要原样重跑 + 降并发 + 分段 + 清缓存
        assert "资源不足" in tail
        assert "不要原样重跑" in tail
        assert "--concurrency=1" in tail
        assert "--frames" in tail
        assert "6000" in tail
        assert "node_modules/.cache" in tail


class TestBashToolWiring:
    """bash_tool 两分支接线：命中签名的回执必须携带指引块。"""

    @staticmethod
    def _patch_common(monkeypatch, sandbox):
        from deerflow.sandbox import tools as sandbox_tools

        monkeypatch.setattr(sandbox_tools, "ensure_sandbox_initialized", lambda runtime: sandbox)
        monkeypatch.setattr(
            "deerflow.config.app_config.get_app_config",
            lambda: SimpleNamespace(sandbox=SimpleNamespace(bash_output_max_chars=20000, bash_command_timeout=300)),
        )
        runtime = SimpleNamespace(context={"thread_id": "t"}, state={}, config={})
        return sandbox_tools, runtime

    def test_remote_branch_appends_guidance(self, monkeypatch):
        from deerflow.sandbox import tools as sandbox_tools

        class _FakeSandbox:
            def execute_command(self, command, timeout=None, env=None):
                return "Rendering frame 9120...\nError: Exec failed, signal SIGKILL"

        sandbox_tools_mod, runtime = self._patch_common(monkeypatch, _FakeSandbox())
        monkeypatch.setattr(sandbox_tools_mod, "is_local_sandbox", lambda runtime: False)

        result = sandbox_tools.bash_tool.func(runtime, description="渲染", command="npx remotion render X")
        assert "Error: Exec failed, signal SIGKILL" in result
        assert "不要原样重跑" in result
        assert "--concurrency=1" in result

    def test_local_branch_appends_guidance(self, monkeypatch):
        from deerflow.sandbox import tools as sandbox_tools

        class _FakeLocalSandbox:
            def execute_command(self, command, timeout=None, env=None):
                return "npx remotion render ...\nError: Target closed"

        sandbox_tools_mod, runtime = self._patch_common(monkeypatch, _FakeLocalSandbox())
        monkeypatch.setattr(sandbox_tools_mod, "is_local_sandbox", lambda runtime: True)
        monkeypatch.setattr(sandbox_tools_mod, "is_host_bash_allowed", lambda config=None: True)
        monkeypatch.setattr(sandbox_tools_mod, "ensure_thread_directories_exist", lambda runtime: None)
        monkeypatch.setattr(sandbox_tools_mod, "get_thread_data", lambda runtime: None)
        monkeypatch.setattr(sandbox_tools_mod, "validate_local_bash_command_paths", lambda command, thread_data: None)
        monkeypatch.setattr(sandbox_tools_mod, "replace_virtual_paths_in_command", lambda command, thread_data: command)
        monkeypatch.setattr(sandbox_tools_mod, "_apply_cwd_prefix", lambda command, thread_data: command)
        monkeypatch.setattr(sandbox_tools_mod, "mask_local_paths_in_output", lambda output, thread_data: output)

        result = sandbox_tools.bash_tool.func(runtime, description="渲染", command="npx remotion render X")
        assert "Error: Target closed" in result
        assert "不要原样重跑" in result
        assert "--concurrency=1" in result

    def test_remote_branch_clean_output_has_no_guidance(self, monkeypatch):
        from deerflow.sandbox import tools as sandbox_tools

        class _FakeSandbox:
            def execute_command(self, command, timeout=None, env=None):
                return "rendered 100 frames, all fine"

        sandbox_tools_mod, runtime = self._patch_common(monkeypatch, _FakeSandbox())
        monkeypatch.setattr(sandbox_tools_mod, "is_local_sandbox", lambda runtime: False)

        result = sandbox_tools.bash_tool.func(runtime, description="渲染", command="echo ok")
        assert result == "rendered 100 frames, all fine"
