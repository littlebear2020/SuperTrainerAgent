from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tarfile
from pathlib import Path

import yaml

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "release_archive.py"
REPO_ROOT = SCRIPT.parents[1]


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def commit(repo: Path, message: str = "change") -> str:
    git(repo, "add", ".")
    git(repo, "commit", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.name", "Test User")
    git(repo, "config", "user.email", "test@example.com")
    (repo / "artifact.txt").write_text("version A\n", encoding="utf-8")
    (repo / "docs").mkdir()
    (repo / "docs/tracked.md").write_text("tracked\n", encoding="utf-8")
    commit(repo, "base")
    git(repo, "update-ref", "refs/remotes/origin/main", "main")
    return repo


def run_cli(repo: Path, output: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--ref", "origin/main", "--output", str(output), *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )


def read_text_member(archive: Path, name: str) -> str:
    with tarfile.open(archive, "r:gz") as bundle:
        member = bundle.extractfile(name)
        assert member is not None
        return member.read().decode()


def manifest(archive: Path) -> dict:
    return json.loads(read_text_member(archive, "release-manifest.json"))


def test_archive_contains_only_committed_content_and_manifest(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    source_sha = git(repo, "rev-parse", "HEAD")
    (repo / "artifact.txt").write_text("version B\n", encoding="utf-8")
    (repo / "secret-placeholder.txt").write_text("DO_NOT_SHIP\n", encoding="utf-8")
    output = tmp_path / "release.tar.gz"

    result = run_cli(repo, output)

    assert result.returncode == 0, result.stderr
    assert read_text_member(output, "artifact.txt") == "version A\n"
    with tarfile.open(output, "r:gz") as bundle:
        names = bundle.getnames()
    assert "secret-placeholder.txt" not in names
    assert names.count("release-manifest.json") == 1
    metadata = manifest(output)
    assert metadata["source_sha"] == source_sha
    assert metadata["requested_ref"] == "origin/main"
    assert metadata["base_ref"] == "origin/main"
    assert metadata["base_sha"] == source_sha
    assert metadata["includes_local_config"] is False
    assert metadata["config_files"] == []


def test_unmerged_ref_requires_opt_in_and_failure_preserves_output(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    git(repo, "checkout", "-b", "feature")
    (repo / "artifact.txt").write_text("feature\n", encoding="utf-8")
    feature_sha = commit(repo, "feature")
    output = tmp_path / "release.tar.gz"
    output.write_bytes(b"previous-release")

    rejected = subprocess.run(
        [sys.executable, str(SCRIPT), "--ref", "feature", "--output", str(output)],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    assert rejected.returncode == 1
    assert output.read_bytes() == b"previous-release"

    allowed = subprocess.run(
        [sys.executable, str(SCRIPT), "--ref", "feature", "--allow-unmerged", "--output", str(output)],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    assert allowed.returncode == 0, allowed.stderr
    assert manifest(output)["source_sha"] == feature_sha
    assert read_text_member(output, "artifact.txt") == "feature\n"


def test_exclude_and_explicit_local_config(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    cache = repo / "docker/sandbox/cache"
    (cache / "chrome-linux").mkdir(parents=True)
    (cache / "chrome-linux/chrome").write_text("chrome", encoding="utf-8")
    for relative in ("python-wheels/pkg.whl", "tiktoken/base.bpe", "fonts/font.ttf"):
        path = cache / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(relative, encoding="utf-8")
    commit(repo, "cache")
    git(repo, "update-ref", "refs/remotes/origin/main", "main")
    (repo / "config.actual.yaml").write_text("version: test\n", encoding="utf-8")
    (repo / "config.yaml").symlink_to("config.actual.yaml")
    (repo / ".env").write_text("TEST_PLACEHOLDER=not-a-secret\n", encoding="utf-8")
    (repo / "frontend").mkdir()
    (repo / "frontend/.env").write_text("FRONTEND_PLACEHOLDER=not-a-secret\n", encoding="utf-8")
    output = tmp_path / "release.tar.gz"

    result = run_cli(
        repo,
        output,
        "--include-local-config",
        "--exclude",
        "docker/sandbox/cache/chrome-linux",
    )

    assert result.returncode == 0, result.stderr
    with tarfile.open(output, "r:gz") as bundle:
        names = bundle.getnames()
    assert "docker/sandbox/cache/chrome-linux/chrome" not in names
    assert "docker/sandbox/cache/python-wheels/pkg.whl" in names
    assert "docker/sandbox/cache/tiktoken/base.bpe" in names
    assert "docker/sandbox/cache/fonts/font.ttf" in names
    assert read_text_member(output, "config.yaml") == "version: test\n"
    assert read_text_member(output, ".env") == "TEST_PLACEHOLDER=not-a-secret\n"
    assert read_text_member(output, "frontend/.env") == "FRONTEND_PLACEHOLDER=not-a-secret\n"
    assert manifest(output)["config_files"] == ["config.yaml", ".env", "frontend/.env"]


def test_invalid_config_and_exclude_never_replace_existing_output(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    output = tmp_path / "release.tar.gz"

    (repo / "config.yaml").write_text("version: test\n", encoding="utf-8")
    missing_env = run_cli(repo, output, "--include-local-config")
    assert missing_env.returncode == 1
    assert not output.exists()

    (repo / ".env").write_text("TEST_PLACEHOLDER=not-a-secret\n", encoding="utf-8")
    (repo / "config.yaml").unlink()
    outside = tmp_path / "outside.yaml"
    outside.write_text("outside\n", encoding="utf-8")
    (repo / "config.yaml").symlink_to(outside)
    output.write_bytes(b"previous-release")
    outside_link = run_cli(repo, output, "--include-local-config")
    assert outside_link.returncode == 1
    assert output.read_bytes() == b"previous-release"

    invalid_exclude = run_cli(repo, output, "--exclude", "../outside")
    assert invalid_exclude.returncode == 1
    assert output.read_bytes() == b"previous-release"


def test_wrapper_scripts_delegate_to_immutable_packaging() -> None:
    production = (REPO_ROOT / "deploy_on_server.sh").read_text(encoding="utf-8")
    test = (REPO_ROOT / "deploy_to_test_server.sh").read_text(encoding="utf-8")

    assert "scripts/release_archive.py" in production
    assert "scripts/pre_deploy_check.py" in production
    assert "DEPLOY_REF:-origin/main" in production
    assert "DEPLOY_INCLUDE_LOCAL_CONFIG" in production
    assert "scripts/release_archive.py" in test
    assert "TEST_DEPLOY_REF:-origin/main" in test
    assert "TEST_ALLOW_UNMERGED" in test
    assert "TEST_INCLUDE_LOCAL_CONFIG" in test
    for script in (production, test):
        assert "docker/sandbox/cache/chrome-linux" in script
        assert "--exclude=docker/sandbox/cache'" not in script
        assert "release-manifest.json" in script


def test_ci_workflows_cover_development_infrastructure() -> None:
    backend_workflow = (REPO_ROOT / ".github/workflows/backend-unit-tests.yml").read_text(encoding="utf-8")
    quick_checks = (REPO_ROOT / ".github/workflows/quick-checks.yml").read_text(encoding="utf-8")
    backend_paths = (
        "skills/**",
        "docker/**",
        "config*.yaml",
        "scripts/agent_context.py",
        "scripts/release_archive.py",
        "scripts/pre_deploy_check.py",
        "deploy_on_server.sh",
        "deploy_to_test_server.sh",
    )
    for path in backend_paths:
        assert path in backend_workflow
    for path in backend_paths:
        quick_path = path.replace("/**", "/*")
        assert quick_path in quick_checks


def test_tracked_manifest_is_replaced_not_duplicated(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    (repo / "release-manifest.json").write_text('{"source_sha":"stale"}\n', encoding="utf-8")
    source_sha = commit(repo, "tracked stale manifest")
    git(repo, "update-ref", "refs/remotes/origin/main", "main")
    output = tmp_path / "release.tar.gz"

    result = run_cli(repo, output)

    assert result.returncode == 0, result.stderr
    with tarfile.open(output, "r:gz") as bundle:
        names = bundle.getnames()
    assert names.count("release-manifest.json") == 1
    assert manifest(output)["source_sha"] == source_sha


def test_release_sha_failures_cannot_be_hidden_by_echo() -> None:
    for name in ("deploy_on_server.sh", "deploy_to_test_server.sh"):
        script = (REPO_ROOT / name).read_text(encoding="utf-8")
        calls = [line.strip() for line in script.splitlines() if "$(release_sha" in line]
        assert len(calls) == 3
        for line in calls:
            assert not line.startswith("echo")
            assert "||" in line


def test_packaging_guards_config_warning_and_stale_upload() -> None:
    for name, ref_variable in (("deploy_on_server.sh", "DEPLOY_REF"), ("deploy_to_test_server.sh", "TEST_DEPLOY_REF")):
        script = (REPO_ROOT / name).read_text(encoding="utf-8")
        assert "release_archive.py" in script
        assert "||" in script.split("scripts/release_archive.py", 1)[1].splitlines()[0]
        assert "本包不含 config.yaml/.env" in script
        assert "EXPECTED_SHA" in script
        assert "ARCHIVE_SHA_FILE" in script
        assert 'EXPECTED_SHA="$(sed -n' in script
        assert "EXPECTED_ARCHIVE_SHA" in script
        assert "ACTUAL_ARCHIVE_SHA" in script
        assert "archive_sha256" in script
        assert ref_variable in script
        assert 'RELEASE_SHA" != "$EXPECTED_SHA' in script
        assert "CONFIG_FROM_ARCHIVE" in script
        assert "跳过旧配置恢复" in script


def test_git_archive_stderr_is_not_an_unconsumed_pipe() -> None:
    script = (REPO_ROOT / "scripts/release_archive.py").read_text(encoding="utf-8")
    assert "stderr_file = tempfile.TemporaryFile()" in script
    assert "stderr=stderr_file" in script
    assert "stderr_file.seek(0)" in script


def test_workflow_paths_are_cross_checked_and_config_glob_stays_at_root() -> None:
    workflow_path = REPO_ROOT / ".github/workflows/backend-unit-tests.yml"
    document = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    triggers = document.get("on", document.get(True))
    assert triggers["push"]["paths"] == triggers["pull_request"]["paths"]
    backend_workflow = workflow_path.read_text(encoding="utf-8")
    quick_checks = (REPO_ROOT / ".github/workflows/quick-checks.yml").read_text(encoding="utf-8")
    assert "quick-checks.yml" in backend_workflow
    assert "backend-unit-tests.yml" in quick_checks
    assert 'path" != */*' in quick_checks
    assert '&& "$path" == config*.yaml' not in quick_checks


def test_archive_stdout_has_explicit_check_and_close() -> None:
    script = (REPO_ROOT / "scripts/release_archive.py").read_text(encoding="utf-8")
    assert "assert process.stdout" not in script
    assert "process.stdout is None" in script
    assert "process.stdout.close()" in script


def test_upload_uses_pack_sidecar_and_runs_from_repo_root() -> None:
    for name in ("deploy_on_server.sh", "deploy_to_test_server.sh"):
        script = (REPO_ROOT / name).read_text(encoding="utf-8")
        upload = script.split("upload() {", 1)[1].split("# ============================================================", 1)[0]
        assert 'cd "$LOCAL_DIR"' in upload
        assert "git rev-parse" not in upload
        assert "ARCHIVE_SHA_FILE" in upload
        assert "包摘要与侧车校验通过" in script


def add_lfs_pointer(repo: Path, payload: bytes) -> str:
    oid = hashlib.sha256(payload).hexdigest()
    (repo / ".gitattributes").write_text("binary.dat filter=lfs\n", encoding="utf-8")
    (repo / "binary.dat").write_text(
        f"version https://git-lfs.github.com/spec/v1\noid sha256:{oid}\nsize {len(payload)}\n",
        encoding="utf-8",
    )
    commit(repo, "LFS pointer")
    git(repo, "update-ref", "refs/remotes/origin/main", "main")
    return oid


def test_lfs_pointer_is_replaced_with_verified_worktree_object(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    payload = bytes(range(256)) * 4096
    add_lfs_pointer(repo, payload)
    (repo / "binary.dat").write_bytes(payload)
    output = tmp_path / "release.tar.gz"

    result = run_cli(repo, output)

    assert result.returncode == 0, result.stderr
    with tarfile.open(output, "r:gz") as bundle:
        member = bundle.extractfile("binary.dat")
        assert member is not None
        assert member.read() == payload


def test_lfs_object_mismatch_fails_without_replacing_output(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    add_lfs_pointer(repo, b"expected object")
    (repo / "binary.dat").write_bytes(b"modified local object")
    output = tmp_path / "release.tar.gz"
    output.write_bytes(b"previous-release")

    result = run_cli(repo, output)

    assert result.returncode == 1
    assert output.read_bytes() == b"previous-release"


def test_sidecar_and_server_verify_archive_digest() -> None:
    for name in ("deploy_on_server.sh", "deploy_to_test_server.sh"):
        script = (REPO_ROOT / name).read_text(encoding="utf-8")
        assert "printf '%s\\n%s\\n'" in script
        assert "EXPECTED_ARCHIVE_SHA" in script
        assert "ACTUAL_ARCHIVE_SHA" in script
        assert 'ACTUAL_ARCHIVE_SHA" != "$EXPECTED_ARCHIVE_SHA' in script
        assert '"$ARCHIVE_SHA_FILE" "${SERVER}:${PROJECT_DIR}/"' in script
        upload = script.split("upload() {", 1)[1].split("# ============================================================", 1)[0]
        assert "与 ${DEPLOY_REF" not in upload
        assert "与 ${TEST_DEPLOY_REF" not in upload


def test_non_lfs_file_with_unaligned_tail_streams_exactly(tmp_path: Path) -> None:
    repo = make_repo(tmp_path)
    payload = bytes(range(256)) * 16 + b"tail"
    (repo / "unaligned.txt").write_bytes(payload)
    commit(repo, "unaligned file")
    git(repo, "update-ref", "refs/remotes/origin/main", "main")
    output = tmp_path / "release.tar.gz"

    result = run_cli(repo, output)

    assert result.returncode == 0, result.stderr
    with tarfile.open(output, "r:gz") as bundle:
        member = bundle.extractfile("unaligned.txt")
        assert member is not None
        assert member.read() == payload
