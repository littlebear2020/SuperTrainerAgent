from __future__ import annotations

import importlib.util
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "pre_deploy_check.py"
BASE_SHA = "a" * 40
FEATURE_SHA = "b" * 40


def load_module():
    spec = importlib.util.spec_from_file_location("pre_deploy_check", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_root(tmp_path: Path, backlog: str = "") -> Path:
    root = tmp_path / "repo"
    (root / "docs/changelog").mkdir(parents=True)
    (root / "docs/findings-backlog.md").write_text(backlog, encoding="utf-8")
    (root / "config.example.yaml").write_text("config_version: 32\n", encoding="utf-8")
    return root


class FakeRunner:
    def __init__(
        self,
        *,
        fetch_returncode: int = 0,
        ancestor_returncode: int = 0,
        invalid_ref: bool = False,
    ) -> None:
        self.fetch_returncode = fetch_returncode
        self.ancestor_returncode = ancestor_returncode
        self.invalid_ref = invalid_ref
        self.calls: list[tuple[list[str], dict | None]] = []

    def __call__(self, cmd: list[str], env=None) -> subprocess.CompletedProcess[str]:
        self.calls.append((cmd, env))

        def result(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess[str]:
            return subprocess.CompletedProcess(cmd, returncode, stdout, "")

        if cmd[:2] == ["git", "fetch"]:
            return result(returncode=self.fetch_returncode)
        if cmd[:2] == ["gh", "pr"]:
            return result('[{"number": 1, "title": "other agent task"}]')
        if cmd[:3] == ["git", "worktree", "list"]:
            return result("worktree /repo/main\nworktree /repo/agent-task\n")
        if cmd[:4] == ["git", "rev-list", "--count", "origin/main..main"]:
            return result("1\n")
        if cmd[:4] == ["git", "rev-list", "--count", "main..origin/main"]:
            return result("2\n")
        if cmd[:3] == ["git", "rev-parse", "--verify"]:
            ref = cmd[-1].split("^", 1)[0]
            if ref == "origin/main":
                return result(f"{BASE_SHA}\n")
            if ref == "feature" and not self.invalid_ref:
                return result(f"{FEATURE_SHA}\n")
            return result("unknown ref\n", 1)
        if cmd[:3] == ["git", "merge-base", "--is-ancestor"]:
            return result(returncode=self.ancestor_returncode)
        if cmd[:3] == ["git", "remote", "get-url"]:
            return result("https://github.com/example/project.git\n")
        raise AssertionError(f"unexpected command: {cmd}")


def run_main(module, root: Path, runner: FakeRunner, monkeypatch: pytest.MonkeyPatch, ref: str = "origin/main") -> int:
    monkeypatch.setattr(module, "REPO_ROOT", root)
    monkeypatch.setattr(module, "run", runner)
    return module.main(["--ref", ref])


def test_release_blocker_controls_exit_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_module()
    blocker = "- [ ] 2026-11-22 [release-blocker] 行为未验证；指针：docs/changelog/example.md\n"
    root = make_root(tmp_path, blocker)

    assert module.find_release_blockers(root) == [blocker.strip()]
    assert run_main(module, root, FakeRunner(), monkeypatch) == 1

    checked = blocker.replace("- [ ]", "- [x]", 1) + "复测证据：真实 P01 首读通过。\n"
    root.joinpath("docs/findings-backlog.md").write_text(checked, encoding="utf-8")
    assert module.find_release_blockers(root) == []
    assert run_main(module, root, FakeRunner(), monkeypatch) == 0


def test_other_agent_work_and_main_drift_are_notes_not_blockers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    module = load_module()
    root = make_root(tmp_path)
    runner = FakeRunner()

    assert run_main(module, root, runner, monkeypatch) == 0

    output = capsys.readouterr().out
    assert "开放 PR #1" in output
    assert "其他 worktree" in output
    assert "本地 main 领先" in output
    assert "本地 main 落后" in output
    assert "无开放 release-blocker" in output
    gh_calls = [(cmd, env) for cmd, env in runner.calls if cmd[0] == "gh"]
    assert gh_calls and gh_calls[0][1]["GH_REPO"] == "example/project"


def test_invalid_and_unmerged_refs_are_blocked(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_module()
    root = make_root(tmp_path)

    assert run_main(module, root, FakeRunner(invalid_ref=True), monkeypatch, "feature") == 1
    assert run_main(module, root, FakeRunner(ancestor_returncode=1), monkeypatch, "feature") == 1


def test_fetch_failure_blocks_formal_release(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_module()
    root = make_root(tmp_path)

    assert run_main(module, root, FakeRunner(fetch_returncode=1), monkeypatch) == 1
