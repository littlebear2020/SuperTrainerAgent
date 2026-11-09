"""Real-behavior tests for ``deerflow.skills.projection``.

The projection layer materializes the enabled-only skill tree into the
directories sandbox providers mount. It is the fail-closed boundary between
the canonical skill store and what an agent may see: manifest versioning
decides when a view is stale, flock-serialized rebuilds reconcile a staged
copy into the live view without blanking it, and every failure path must
leave the scope cleared rather than expose a partial or revoked skill set.
These tests run the real filesystem state machine against ``tmp_path`` with
real storages; only genuinely external inputs (the extensions config file,
mid-rebuild source instability, a competing process holding the flock) are
controlled.
"""

from __future__ import annotations

import itertools
import json
import os
import shutil
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from deerflow.config.paths import Paths
from deerflow.skills import projection
from deerflow.skills.projection import (
    ensure_public_skill_projection,
    ensure_skill_projections,
    ensure_thread_skill_projection,
    get_skill_projection_paths,
    get_thread_skill_projection_paths,
    rebuild_skill_projections,
    skill_projection_mutation,
    skill_projection_read_lock,
    thread_skill_projection_exists,
)
from deerflow.skills.storage import reset_skill_storage
from deerflow.skills.storage.local_skill_storage import LocalSkillStorage
from deerflow.skills.storage.user_scoped_skill_storage import UserScopedSkillStorage
from deerflow.skills.types import SkillCategory

_MANIFEST_NAME = ".projection-manifest.json"


def _skill_content(name: str, description: str = "Demo skill") -> str:
    return f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n"


def _write_skill(category_root: Path, relative_path: str, description: str = "Demo skill") -> Path:
    skill_dir = category_root / relative_path
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(_skill_content(skill_dir.name, description), encoding="utf-8")
    return skill_dir


def _write_extensions_state(paths: Paths, skills: dict) -> None:
    (paths.base_dir / "extensions_config.json").write_text(json.dumps({"mcpServers": {}, "skills": skills}), encoding="utf-8")


def _manifest_file(scope_root: Path) -> Path:
    return scope_root / _MANIFEST_NAME


def _read_manifest(scope_root: Path) -> dict:
    return json.loads(_manifest_file(scope_root).read_text(encoding="utf-8"))


def _file_fingerprint(path: Path) -> tuple:
    stat = path.stat()
    return (stat.st_ino, stat.st_mtime_ns, stat.st_size)


@pytest.fixture(autouse=True)
def deerflow_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Paths:
    """Isolate DeerFlow state: point the paths singleton and the extensions
    config at tmp_path so tests never touch the repo's real config files."""
    paths = Paths(base_dir=tmp_path)
    paths.base_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr("deerflow.config.paths.get_paths", lambda: paths)
    monkeypatch.setattr("deerflow.config.paths._paths", paths)
    _write_extensions_state(paths, {})
    monkeypatch.setenv("DEER_FLOW_EXTENSIONS_CONFIG_PATH", str(paths.base_dir / "extensions_config.json"))
    reset_skill_storage()
    yield paths
    reset_skill_storage()


@pytest.fixture
def skills_root(deerflow_paths: Paths) -> Path:
    root = deerflow_paths.base_dir / "skills"
    (root / "public").mkdir(parents=True)
    (root / "custom").mkdir(parents=True)
    return root


@pytest.fixture
def public_storage(skills_root: Path) -> LocalSkillStorage:
    """Storage without a user scope: only the public projection applies."""
    return LocalSkillStorage(host_path=str(skills_root))


@pytest.fixture
def user_storage(skills_root: Path) -> UserScopedSkillStorage:
    return UserScopedSkillStorage("alice", host_path=str(skills_root))


def _storage_config(skills_root: Path) -> SimpleNamespace:
    return SimpleNamespace(
        skills=SimpleNamespace(
            get_skills_path=lambda: skills_root,
            container_path="/mnt/skills",
            use="deerflow.skills.storage.local_skill_storage:LocalSkillStorage",
        ),
    )


class TestProjectionPaths:
    """Path routing: global scopes for anonymous storage, per-user scopes otherwise."""

    def test_global_and_user_scoped_paths(self, deerflow_paths: Paths, public_storage: LocalSkillStorage, user_storage: UserScopedSkillStorage):
        global_paths = get_skill_projection_paths(public_storage)
        assert global_paths.public == deerflow_paths.public_skills_view_dir
        assert global_paths.custom == deerflow_paths.skills_view_dir / "custom"
        assert global_paths.legacy == deerflow_paths.skills_view_dir / "legacy"

        user_paths = get_skill_projection_paths(user_storage)
        assert user_paths.public == deerflow_paths.public_skills_view_dir
        assert user_paths.custom == deerflow_paths.user_custom_skills_view_dir("alice")
        assert user_paths.legacy == deerflow_paths.user_legacy_skills_view_dir("alice")
        assert user_paths.integrations == deerflow_paths.user_integration_skills_view_dir("alice")

    def test_thread_paths_require_user_scoped_storage(self, public_storage: LocalSkillStorage):
        with pytest.raises(ValueError, match="user-scoped skill storage"):
            get_thread_skill_projection_paths(public_storage, "thread-1")


class TestPublicProjectionManifest:
    """Manifest versioning: fresh write, same-state no-op, staleness triggers."""

    def test_first_ensure_materializes_enabled_skills_and_writes_manifest(self, deerflow_paths: Paths, skills_root: Path, public_storage: LocalSkillStorage):
        _write_skill(skills_root / "public", "alpha")
        _write_skill(skills_root / "public", "beta")
        _write_extensions_state(deerflow_paths, {"beta": {"enabled": False}})

        result = ensure_skill_projections(public_storage)

        assert result == get_skill_projection_paths(public_storage)
        view = deerflow_paths.public_skills_view_dir
        assert (view / "alpha" / "SKILL.md").is_file()
        assert not (view / "beta").exists()

        source = skills_root / "public" / "alpha" / "SKILL.md"
        projected = view / "alpha" / "SKILL.md"
        assert projected.read_text(encoding="utf-8") == source.read_text(encoding="utf-8")
        # Copies, never hardlinks: a sandbox write through the view must not mutate the canonical file.
        assert projected.stat().st_ino != source.stat().st_ino

        manifest = _read_manifest(deerflow_paths.skills_view_dir)
        assert manifest["version"] == projection._MANIFEST_VERSION
        assert set(manifest) == {"version", "source_signature", "view_signature"}

    def test_repeat_ensure_leaves_fresh_projection_untouched(self, deerflow_paths: Paths, skills_root: Path, public_storage: LocalSkillStorage):
        _write_skill(skills_root / "public", "alpha")
        ensure_skill_projections(public_storage)
        projected = deerflow_paths.public_skills_view_dir / "alpha" / "SKILL.md"
        fingerprint = _file_fingerprint(projected)
        manifest_bytes = _manifest_file(deerflow_paths.skills_view_dir).read_bytes()

        ensure_skill_projections(public_storage)

        # Consistent state -> skip: inodes and the manifest stay byte-identical.
        assert _file_fingerprint(projected) == fingerprint
        assert _manifest_file(deerflow_paths.skills_view_dir).read_bytes() == manifest_bytes

    def test_outdated_manifest_version_triggers_rebuild(self, deerflow_paths: Paths, skills_root: Path, public_storage: LocalSkillStorage):
        _write_skill(skills_root / "public", "alpha")
        ensure_skill_projections(public_storage)
        projected = deerflow_paths.public_skills_view_dir / "alpha" / "SKILL.md"
        stale_inode = projected.stat().st_ino
        _manifest_file(deerflow_paths.skills_view_dir).write_text(
            json.dumps({"version": projection._MANIFEST_VERSION - 1, "source_signature": "stale", "view_signature": "stale"}),
            encoding="utf-8",
        )

        ensure_skill_projections(public_storage)

        manifest = _read_manifest(deerflow_paths.skills_view_dir)
        assert manifest["version"] == projection._MANIFEST_VERSION
        assert manifest["source_signature"] != "stale"
        # The view was genuinely re-materialized (atomic rename -> new inode), not just re-signed.
        assert projected.stat().st_ino != stale_inode

    def test_tampered_signature_with_current_version_triggers_rebuild(self, deerflow_paths: Paths, skills_root: Path, public_storage: LocalSkillStorage):
        _write_skill(skills_root / "public", "alpha")
        ensure_skill_projections(public_storage)
        manifest = _read_manifest(deerflow_paths.skills_view_dir)
        manifest["source_signature"] = "0" * 64
        _manifest_file(deerflow_paths.skills_view_dir).write_text(json.dumps(manifest), encoding="utf-8")

        ensure_skill_projections(public_storage)

        assert _read_manifest(deerflow_paths.skills_view_dir)["source_signature"] != "0" * 64

    @pytest.mark.parametrize("payload", ["{not valid json", '["a", "list"]'], ids=["invalid-json", "non-dict"])
    def test_corrupted_manifest_triggers_rebuild(self, payload: str, deerflow_paths: Paths, skills_root: Path, public_storage: LocalSkillStorage):
        _write_skill(skills_root / "public", "alpha")
        ensure_skill_projections(public_storage)
        _manifest_file(deerflow_paths.skills_view_dir).write_text(payload, encoding="utf-8")

        ensure_skill_projections(public_storage)

        manifest = _read_manifest(deerflow_paths.skills_view_dir)
        assert manifest["version"] == projection._MANIFEST_VERSION
        assert (deerflow_paths.public_skills_view_dir / "alpha" / "SKILL.md").is_file()

    def test_missing_projected_file_triggers_rebuild(self, deerflow_paths: Paths, skills_root: Path, public_storage: LocalSkillStorage):
        source = _write_skill(skills_root / "public", "alpha") / "SKILL.md"
        ensure_skill_projections(public_storage)
        projected = deerflow_paths.public_skills_view_dir / "alpha" / "SKILL.md"
        projected.unlink()

        ensure_skill_projections(public_storage)

        # View drift (view_signature mismatch) is repaired from the source.
        assert projected.is_file()
        assert projected.read_text(encoding="utf-8") == source.read_text(encoding="utf-8")

    def test_source_edit_triggers_rebuild(self, deerflow_paths: Paths, skills_root: Path, public_storage: LocalSkillStorage):
        skill_dir = _write_skill(skills_root / "public", "alpha", description="First description")
        ensure_skill_projections(public_storage)
        manifest_before = _read_manifest(deerflow_paths.skills_view_dir)

        (skill_dir / "SKILL.md").write_text(_skill_content("alpha", "A second, substantially longer description"), encoding="utf-8")
        ensure_skill_projections(public_storage)

        projected = deerflow_paths.public_skills_view_dir / "alpha" / "SKILL.md"
        assert projected.read_text(encoding="utf-8") == (skill_dir / "SKILL.md").read_text(encoding="utf-8")
        assert _read_manifest(deerflow_paths.skills_view_dir)["source_signature"] != manifest_before["source_signature"]

    def test_extensions_toggle_triggers_rebuild(self, deerflow_paths: Paths, skills_root: Path, public_storage: LocalSkillStorage):
        _write_skill(skills_root / "public", "alpha")
        ensure_skill_projections(public_storage)
        view = deerflow_paths.public_skills_view_dir
        assert (view / "alpha" / "SKILL.md").is_file()

        # The extensions state feeds the source signature: an admin disable must drop the skill from the view.
        _write_extensions_state(deerflow_paths, {"alpha": {"enabled": False}})
        ensure_skill_projections(public_storage)
        assert not (view / "alpha").exists()

        _write_extensions_state(deerflow_paths, {})
        ensure_skill_projections(public_storage)
        assert (view / "alpha" / "SKILL.md").is_file()


class TestUserProjection:
    """User scope: custom/legacy/integrations categories, per-user state, freshness."""

    def test_materializes_custom_and_integrations_while_legacy_stays_shadowed(self, deerflow_paths: Paths, skills_root: Path, user_storage: UserScopedSkillStorage):
        _write_skill(user_storage.get_user_custom_root(), "my-custom")
        _write_skill(deerflow_paths.integration_skills_dir(), "provider-x/integrated-skill")
        # The user already owns a custom skill, so the global-custom legacy fallback does not apply.
        _write_skill(skills_root / "custom", "legacy-one")

        ensure_skill_projections(user_storage)

        view = deerflow_paths.user_skills_view_dir("alice")
        assert (view / "custom" / "my-custom" / "SKILL.md").is_file()
        # Integration skills default to disabled: only public/custom default-enable.
        assert not (view / "integrations" / "provider-x" / "integrated-skill").exists()
        assert list((view / "legacy").iterdir()) == []
        assert _read_manifest(view)["version"] == projection._MANIFEST_VERSION
        # The public scope is ensured in the same call.
        assert _read_manifest(deerflow_paths.skills_view_dir)["version"] == projection._MANIFEST_VERSION

        # Enabling the integration changes the source signature and rebuilds the view.
        _write_extensions_state(deerflow_paths, {"integrated-skill": {"enabled": True}})
        ensure_skill_projections(user_storage)
        assert (view / "integrations" / "provider-x" / "integrated-skill" / "SKILL.md").is_file()
        assert list((view / "legacy").iterdir()) == []

    def test_legacy_fallback_projects_enabled_legacy_only_when_user_has_no_custom_skills(self, deerflow_paths: Paths, skills_root: Path, user_storage: UserScopedSkillStorage):
        _write_skill(skills_root / "custom", "legacy-one")

        ensure_skill_projections(user_storage)
        view = deerflow_paths.user_skills_view_dir("alice")
        # Legacy skills also default to disabled until explicitly enabled.
        assert list((view / "legacy").iterdir()) == []

        _write_extensions_state(deerflow_paths, {"legacy-one": {"enabled": True}})
        ensure_skill_projections(user_storage)
        assert (view / "legacy" / "legacy-one" / "SKILL.md").is_file()
        assert list((view / "custom").iterdir()) == []

    def test_per_user_disable_state_removes_skill_and_invalidates_signature(self, deerflow_paths: Paths, user_storage: UserScopedSkillStorage):
        _write_skill(user_storage.get_user_custom_root(), "my-custom")
        ensure_skill_projections(user_storage)
        view = deerflow_paths.user_skills_view_dir("alice")
        assert (view / "custom" / "my-custom").is_dir()
        manifest_before = _read_manifest(view)

        states_file = deerflow_paths.user_skills_dir("alice") / "_skill_states.json"
        states_file.parent.mkdir(parents=True, exist_ok=True)
        states_file.write_text(json.dumps({"my-custom": {"enabled": False}}), encoding="utf-8")
        ensure_skill_projections(user_storage)

        assert not (view / "custom" / "my-custom").exists()
        assert _read_manifest(view)["source_signature"] != manifest_before["source_signature"]
        # The canonical source is never touched by projection maintenance.
        assert (user_storage.get_user_custom_root() / "my-custom" / "SKILL.md").is_file()

    def test_repeat_ensure_leaves_fresh_user_projection_untouched(self, deerflow_paths: Paths, user_storage: UserScopedSkillStorage):
        _write_skill(user_storage.get_user_custom_root(), "my-custom")
        ensure_skill_projections(user_storage)
        view = deerflow_paths.user_skills_view_dir("alice")
        fingerprint = _file_fingerprint(view / "custom" / "my-custom" / "SKILL.md")
        manifest_bytes = _manifest_file(view).read_bytes()

        ensure_skill_projections(user_storage)

        assert _file_fingerprint(view / "custom" / "my-custom" / "SKILL.md") == fingerprint
        assert _manifest_file(view).read_bytes() == manifest_bytes

    def test_user_view_drift_triggers_rebuild(self, deerflow_paths: Paths, user_storage: UserScopedSkillStorage):
        _write_skill(user_storage.get_user_custom_root(), "my-custom")
        ensure_skill_projections(user_storage)
        view = deerflow_paths.user_skills_view_dir("alice")
        (view / "custom" / "my-custom" / "SKILL.md").unlink()

        ensure_skill_projections(user_storage)

        assert (view / "custom" / "my-custom" / "SKILL.md").is_file()

    def test_rebuild_respects_include_flags(self, deerflow_paths: Paths, skills_root: Path, user_storage: UserScopedSkillStorage):
        _write_skill(skills_root / "public", "alpha")
        _write_skill(user_storage.get_user_custom_root(), "my-custom")

        returned = rebuild_skill_projections(user_storage, include_public=True, include_user=False)
        assert returned == get_skill_projection_paths(user_storage)
        assert (deerflow_paths.public_skills_view_dir / "alpha" / "SKILL.md").is_file()
        assert not deerflow_paths.user_skills_view_dir("alice").exists()

        rebuild_skill_projections(user_storage, include_public=False, include_user=True)
        assert (deerflow_paths.user_skills_view_dir("alice") / "custom" / "my-custom" / "SKILL.md").is_file()

    def test_rebuild_with_userless_storage_skips_user_scope(self, deerflow_paths: Paths, skills_root: Path, public_storage: LocalSkillStorage):
        _write_skill(skills_root / "public", "alpha")

        returned = rebuild_skill_projections(public_storage)

        assert (deerflow_paths.public_skills_view_dir / "alpha" / "SKILL.md").is_file()
        assert not (deerflow_paths.skills_view_dir / "custom").exists()
        assert returned.custom == deerflow_paths.skills_view_dir / "custom"


class TestThreadProjection:
    """Thread policy views: allow-list materialization, reuse, fail-closed validation."""

    def test_unrestricted_thread_without_existing_view_returns_none(self, deerflow_paths: Paths, user_storage: UserScopedSkillStorage):
        result = ensure_thread_skill_projection(user_storage, "thread-9", None)

        assert result is None
        assert not deerflow_paths.thread_skills_view_dir("thread-9", user_id="alice").exists()
        assert not thread_skill_projection_exists(user_storage, "thread-9")

    def test_allowed_policy_materializes_only_allowed_skills(self, deerflow_paths: Paths, skills_root: Path, user_storage: UserScopedSkillStorage):
        _write_skill(skills_root / "public", "alpha")
        _write_skill(skills_root / "public", "beta")
        _write_skill(user_storage.get_user_custom_root(), "my-custom")
        assert not thread_skill_projection_exists(user_storage, "thread-1")

        result = ensure_thread_skill_projection(user_storage, "thread-1", {"alpha", "my-custom"})

        assert result == get_thread_skill_projection_paths(user_storage, "thread-1")
        assert thread_skill_projection_exists(user_storage, "thread-1")
        assert (result.public / "alpha" / "SKILL.md").is_file()
        assert not (result.public / "beta").exists()
        assert (result.custom / "my-custom" / "SKILL.md").is_file()
        assert _read_manifest(result.public.parent)["version"] == projection._MANIFEST_VERSION

    def test_fresh_thread_projection_is_reused(self, skills_root: Path, user_storage: UserScopedSkillStorage):
        _write_skill(skills_root / "public", "alpha")
        first = ensure_thread_skill_projection(user_storage, "thread-1", {"alpha"})
        projected = first.public / "alpha" / "SKILL.md"
        fingerprint = _file_fingerprint(projected)
        manifest_bytes = _manifest_file(first.public.parent).read_bytes()

        second = ensure_thread_skill_projection(user_storage, "thread-1", {"alpha"})

        assert second == first
        assert _file_fingerprint(projected) == fingerprint
        assert _manifest_file(first.public.parent).read_bytes() == manifest_bytes

    def test_policy_change_and_reset_to_unrestricted_rebuild(self, skills_root: Path, user_storage: UserScopedSkillStorage):
        _write_skill(skills_root / "public", "alpha")
        _write_skill(skills_root / "public", "beta")

        narrowed = ensure_thread_skill_projection(user_storage, "thread-1", {"alpha"})
        assert (narrowed.public / "alpha").is_dir()
        assert not (narrowed.public / "beta").exists()

        swapped = ensure_thread_skill_projection(user_storage, "thread-1", {"beta"})
        assert not (swapped.public / "alpha").exists()
        assert (swapped.public / "beta" / "SKILL.md").is_file()

        # Once a thread has a scoped view, a later unrestricted run must rebuild it
        # with all enabled skills rather than leave the thread accidentally restricted.
        unrestricted = ensure_thread_skill_projection(user_storage, "thread-1", None)
        assert (unrestricted.public / "alpha" / "SKILL.md").is_file()
        assert (unrestricted.public / "beta" / "SKILL.md").is_file()

    def test_relative_symlink_inside_package_is_allowed(self, skills_root: Path, user_storage: UserScopedSkillStorage):
        package = _write_skill(skills_root / "public", "linked")
        (package / "references").mkdir()
        (package / "references" / "guide.md").write_text("guide-body", encoding="utf-8")
        (package / "latest.md").symlink_to(Path("references") / "guide.md")

        result = ensure_thread_skill_projection(user_storage, "thread-1", {"linked"})

        projected = result.public / "linked"
        assert (projected / "latest.md").is_symlink()
        assert os.readlink(projected / "latest.md") == str(Path("references") / "guide.md")
        assert (projected / "latest.md").read_text(encoding="utf-8") == "guide-body"

    @pytest.mark.parametrize(
        ("link_kind", "match"),
        [("absolute", "absolute symlink"), ("escape", "escapes its package"), ("dangling", "invalid symlink")],
    )
    def test_unsafe_symlinks_fail_closed(
        self,
        link_kind: str,
        match: str,
        deerflow_paths: Paths,
        skills_root: Path,
        user_storage: UserScopedSkillStorage,
    ):
        package = _write_skill(skills_root / "public", "evil")
        secret = skills_root / "public" / "secret.txt"
        secret.write_text("secret", encoding="utf-8")
        if link_kind == "absolute":
            (package / "leak.txt").symlink_to(secret.resolve())
        elif link_kind == "escape":
            (package / "leak.txt").symlink_to(Path("..") / "secret.txt")
        else:
            (package / "leak.txt").symlink_to("missing.txt")

        with pytest.raises(ValueError, match=match):
            ensure_thread_skill_projection(user_storage, "thread-1", {"evil"})

        # Fail closed: the rejected package never reaches the view, the scope is
        # cleared, and the out-of-package target is never touched.
        thread_root = deerflow_paths.thread_skills_view_dir("thread-1", user_id="alice")
        assert list((thread_root / "public").iterdir()) == []
        assert not _manifest_file(thread_root).exists()
        assert secret.read_text(encoding="utf-8") == "secret"


class TestProjectionLocking:
    """The projection lock serializes writers in-process and across POSIX workers."""

    def test_projection_lock_serializes_concurrent_entrants(self, deerflow_paths: Paths):
        scope_root = deerflow_paths.skills_view_dir
        holder_inside = threading.Event()
        release_holder = threading.Event()
        contender_inside = threading.Event()
        events: list[str] = []

        def _holder() -> None:
            with projection._projection_lock(scope_root):
                events.append("holder-enter")
                holder_inside.set()
                assert release_holder.wait(timeout=5)
                events.append("holder-exit")

        def _contender() -> None:
            with projection._projection_lock(scope_root):
                events.append("contender-enter")
                contender_inside.set()

        holder = threading.Thread(target=_holder, daemon=True)
        contender = threading.Thread(target=_contender, daemon=True)
        holder.start()
        assert holder_inside.wait(timeout=5)
        contender.start()
        try:
            assert not contender_inside.wait(timeout=0.3), "second entrant acquired the projection lock while it was held"
        finally:
            release_holder.set()
        assert contender_inside.wait(timeout=5)
        holder.join(timeout=5)
        contender.join(timeout=5)
        assert not holder.is_alive() and not contender.is_alive()
        assert events == ["holder-enter", "holder-exit", "contender-enter"]

    @pytest.mark.skipif(projection.fcntl is None, reason="fcntl flock semantics are POSIX-only")
    def test_read_lock_times_out_against_externally_held_flock(self, deerflow_paths: Paths, user_storage: UserScopedSkillStorage):
        # Simulate another worker process: hold the flock on a separate file
        # description without touching the in-process RLock.
        view_root = deerflow_paths.user_skills_view_dir("alice")
        lock_path = view_root.parent / f".{view_root.name}.projection.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)

        with lock_path.open("a", encoding="utf-8") as external:
            projection.fcntl.flock(external, projection.fcntl.LOCK_EX)
            started = time.monotonic()
            with pytest.raises(TimeoutError, match="Skill projection lock timeout"):
                with skill_projection_read_lock(user_storage, timeout=0.3):
                    pass
            assert time.monotonic() - started < 2

    @pytest.mark.skipif(projection.fcntl is None, reason="fcntl flock semantics are POSIX-only")
    def test_read_lock_holds_flock_and_releases_it(self, deerflow_paths: Paths, user_storage: UserScopedSkillStorage):
        view_root = deerflow_paths.user_skills_view_dir("alice")
        lock_path = view_root.parent / f".{view_root.name}.projection.lock"
        check_calls: list[str] = []

        with skill_projection_read_lock(user_storage, timeout=1.0, check=lambda: check_calls.append("tick")):
            assert check_calls, "the cancellation check must run during acquisition"
            with lock_path.open("a", encoding="utf-8") as competitor:
                with pytest.raises(OSError):
                    projection.fcntl.flock(competitor, projection.fcntl.LOCK_EX | projection.fcntl.LOCK_NB)

        # After release, an exclusive lock is immediately available again.
        with lock_path.open("a", encoding="utf-8") as competitor:
            projection.fcntl.flock(competitor, projection.fcntl.LOCK_EX | projection.fcntl.LOCK_NB)
            projection.fcntl.flock(competitor, projection.fcntl.LOCK_UN)

    def test_read_lock_check_abort_prevents_yield(self, user_storage: UserScopedSkillStorage):
        def _cancelled() -> None:
            raise RuntimeError("cancelled")

        with pytest.raises(RuntimeError, match="cancelled"):
            with skill_projection_read_lock(user_storage, timeout=1.0, check=_cancelled):
                pytest.fail("read lock must not yield after the check raises")


class TestRebuildFailureCleanup:
    """Rebuild retries and fail-closed cleanup after partial materialization."""

    def test_unstable_source_retries_then_fails_closed(self, deerflow_paths: Paths, skills_root: Path, public_storage: LocalSkillStorage, monkeypatch: pytest.MonkeyPatch):
        _write_skill(skills_root / "public", "alpha")
        counter = itertools.count()
        monkeypatch.setattr(projection, "_source_signature", lambda storage, scope: f"unstable-{next(counter)}")

        with pytest.raises(RuntimeError, match="changed repeatedly"):
            rebuild_skill_projections(public_storage, include_user=False)

        # Exactly _MAX_REBUILD_ATTEMPTS attempts, each signing before and after.
        assert next(counter) == 2 * projection._MAX_REBUILD_ATTEMPTS
        # Fail closed: the scope is emptied rather than left half-rebuilt.
        assert deerflow_paths.public_skills_view_dir.is_dir()
        assert list(deerflow_paths.public_skills_view_dir.iterdir()) == []
        assert not _manifest_file(deerflow_paths.skills_view_dir).exists()

    def test_unstable_thread_source_retries_then_fails_closed(self, skills_root: Path, user_storage: UserScopedSkillStorage, monkeypatch: pytest.MonkeyPatch):
        _write_skill(skills_root / "public", "alpha")
        counter = itertools.count()
        monkeypatch.setattr(projection, "_thread_source_signature", lambda storage, allowed: f"unstable-{next(counter)}")

        with pytest.raises(RuntimeError, match="changed repeatedly"):
            ensure_thread_skill_projection(user_storage, "thread-1", {"alpha"})
        assert next(counter) >= 2 * projection._MAX_REBUILD_ATTEMPTS

        thread_paths = get_thread_skill_projection_paths(user_storage, "thread-1")
        for category_root in (thread_paths.public, thread_paths.custom, thread_paths.legacy, thread_paths.integrations):
            assert category_root.is_dir()
            assert list(category_root.iterdir()) == []
        assert not _manifest_file(thread_paths.public.parent).exists()

    def test_staging_failure_clears_preexisting_view(self, deerflow_paths: Paths, skills_root: Path, public_storage: LocalSkillStorage, monkeypatch: pytest.MonkeyPatch):
        _write_skill(skills_root / "public", "alpha")
        ensure_skill_projections(public_storage)
        assert (deerflow_paths.public_skills_view_dir / "alpha" / "SKILL.md").is_file()
        _write_skill(skills_root / "public", "beta")

        def _broken_stage(source: Path, target: Path, nested_skill_roots: set[Path], *, enforce_symlink_boundary: bool = False) -> None:
            target.mkdir(parents=True, exist_ok=True)
            (target / "SKILL.md").write_text("partial junk", encoding="utf-8")
            raise OSError("simulated copy failure")

        monkeypatch.setattr(projection, "_stage_skill", _broken_stage)
        with pytest.raises(OSError, match="simulated copy failure"):
            rebuild_skill_projections(public_storage, include_user=False)

        # Staging junk never reaches the live root, and the previously good view
        # is cleared instead of being left stale beside a corrupt manifest.
        assert list(deerflow_paths.public_skills_view_dir.iterdir()) == []
        assert not _manifest_file(deerflow_paths.skills_view_dir).exists()


class TestSkillProjectionMutation:
    """The mutation context: revoke old authority first, rebuild after, fail closed."""

    def test_remove_tuple_revokes_before_body_and_rebuilds_after(self, deerflow_paths: Paths, user_storage: UserScopedSkillStorage):
        _write_skill(user_storage.get_user_custom_root(), "keep-me")
        _write_skill(user_storage.get_user_custom_root(), "drop-me")
        ensure_skill_projections(user_storage)
        view = deerflow_paths.user_skills_view_dir("alice")
        assert (view / "custom" / "drop-me" / "SKILL.md").is_file()

        with skill_projection_mutation(user_storage, "user", remove=((SkillCategory.CUSTOM, Path("drop-me")),)):
            # Old authority is revoked before the mutation body runs.
            assert not (view / "custom" / "drop-me").exists()
            shutil.rmtree(user_storage.get_user_custom_root() / "drop-me")

        assert (view / "custom" / "keep-me" / "SKILL.md").is_file()
        assert not (view / "custom" / "drop-me").exists()
        assert _read_manifest(view)["version"] == projection._MANIFEST_VERSION

    def test_remove_names_resolves_skills_by_name(self, deerflow_paths: Paths, user_storage: UserScopedSkillStorage):
        _write_skill(user_storage.get_user_custom_root(), "keep-me")
        _write_skill(user_storage.get_user_custom_root(), "drop-me")
        ensure_skill_projections(user_storage)
        view = deerflow_paths.user_skills_view_dir("alice")

        with skill_projection_mutation(user_storage, "user", remove_names=("drop-me",)):
            assert not (view / "custom" / "drop-me").exists()
            shutil.rmtree(user_storage.get_user_custom_root() / "drop-me")

        assert (view / "custom" / "keep-me" / "SKILL.md").is_file()
        assert not (view / "custom" / "drop-me").exists()

    def test_invalid_remove_inputs_rejected_before_any_mutation(self, deerflow_paths: Paths, public_storage: LocalSkillStorage, user_storage: UserScopedSkillStorage):
        _write_skill(user_storage.get_user_custom_root(), "keep-me")
        ensure_skill_projections(user_storage)
        view = deerflow_paths.user_skills_view_dir("alice")
        manifest_bytes = _manifest_file(view).read_bytes()

        with pytest.raises(ValueError, match="must identify a package"):
            with skill_projection_mutation(user_storage, "user", remove=((SkillCategory.CUSTOM, Path("..")),)):
                pass
        with pytest.raises(ValueError, match="must identify a package"):
            with skill_projection_mutation(user_storage, "user", remove=((SkillCategory.CUSTOM, Path("/absolute")),)):
                pass
        with pytest.raises(ValueError, match="does not belong to projection scope"):
            with skill_projection_mutation(public_storage, "public", remove=((SkillCategory.CUSTOM, Path("alpha")),)):
                pass
        with pytest.raises(ValueError, match="Unknown skill projection scope"):
            with skill_projection_mutation(public_storage, "sideways"):
                pass

        # Rejected before the lock: the live projection is byte-identical.
        assert _manifest_file(view).read_bytes() == manifest_bytes
        assert (view / "custom" / "keep-me" / "SKILL.md").is_file()

    def test_body_failure_clears_scope(self, deerflow_paths: Paths, user_storage: UserScopedSkillStorage):
        _write_skill(user_storage.get_user_custom_root(), "my-custom")
        ensure_skill_projections(user_storage)
        view = deerflow_paths.user_skills_view_dir("alice")

        with pytest.raises(RuntimeError, match="boom"):
            with skill_projection_mutation(user_storage, "user"):
                raise RuntimeError("boom")

        for category in ("custom", "legacy", "integrations"):
            assert list((view / category).iterdir()) == []
        assert not _manifest_file(view).exists()
        # The canonical source survives: only the projection is cleared.
        assert (user_storage.get_user_custom_root() / "my-custom" / "SKILL.md").is_file()

    def test_rebuild_failure_clears_scope(self, deerflow_paths: Paths, user_storage: UserScopedSkillStorage, monkeypatch: pytest.MonkeyPatch):
        _write_skill(user_storage.get_user_custom_root(), "my-custom")
        ensure_skill_projections(user_storage)
        view = deerflow_paths.user_skills_view_dir("alice")

        def _broken_rebuild(storage, paths) -> None:
            raise OSError("rebuild exploded")

        monkeypatch.setattr(projection, "_rebuild_user_locked", _broken_rebuild)
        with pytest.raises(OSError, match="rebuild exploded"):
            with skill_projection_mutation(user_storage, "user"):
                pass

        assert list((view / "custom").iterdir()) == []
        assert not _manifest_file(view).exists()

    def test_mock_storage_compatibility_branch_yields_without_locking(self):
        # The SkillStorage contract requires a Path; lightweight test doubles that
        # return a MagicMock take the no-lock compatibility branch.
        storage = SimpleNamespace(get_skills_root_path=lambda: MagicMock(), user_id="alice")
        entered = False
        with skill_projection_mutation(storage, "user"):
            entered = True
        assert entered

    def test_symlinked_namespace_removal_does_not_follow_link(self, deerflow_paths: Paths, user_storage: UserScopedSkillStorage):
        _write_skill(user_storage.get_user_custom_root(), "ns/skill-a")
        ensure_skill_projections(user_storage)
        view_custom = deerflow_paths.user_skills_view_dir("alice") / "custom"
        assert (view_custom / "ns" / "skill-a" / "SKILL.md").is_file()

        # Drift: the namespace directory was externally replaced by a symlink.
        outside = deerflow_paths.base_dir / "outside-tree"
        (outside / "skill-a").mkdir(parents=True)
        sentinel = outside / "skill-a" / "sentinel.txt"
        sentinel.write_text("untouched", encoding="utf-8")
        shutil.rmtree(view_custom / "ns")
        (view_custom / "ns").symlink_to(outside, target_is_directory=True)

        with skill_projection_mutation(user_storage, "user", remove=((SkillCategory.CUSTOM, Path("ns/skill-a")),)):
            shutil.rmtree(user_storage.get_user_custom_root() / "ns")

        # The link itself is unlinked; its target tree is never followed or harmed.
        assert not (view_custom / "ns").exists()
        assert sentinel.read_text(encoding="utf-8") == "untouched"

    def test_file_drifted_namespace_removal_fails_closed(self, deerflow_paths: Paths, user_storage: UserScopedSkillStorage):
        _write_skill(user_storage.get_user_custom_root(), "ns/skill-a")
        ensure_skill_projections(user_storage)
        view = deerflow_paths.user_skills_view_dir("alice")

        # Drift: the namespace component became a regular file.
        shutil.rmtree(view / "custom" / "ns")
        (view / "custom" / "ns").write_text("drifted", encoding="utf-8")

        with pytest.raises(NotADirectoryError):
            with skill_projection_mutation(user_storage, "user", remove=((SkillCategory.CUSTOM, Path("ns/skill-a")),)):
                pass

        # Fail closed on every platform (POSIX reports ENOTDIR, Windows ENOENT).
        assert list((view / "custom").iterdir()) == []
        assert not _manifest_file(view).exists()
        assert (user_storage.get_user_custom_root() / "ns" / "skill-a" / "SKILL.md").is_file()


class TestBootEnsure:
    """Boot-time public ensure: success, and fail-closed degradation on error."""

    def test_boot_ensure_success(self, deerflow_paths: Paths, skills_root: Path):
        _write_skill(skills_root / "public", "alpha")

        assert ensure_public_skill_projection(app_config=_storage_config(skills_root)) is True
        assert (deerflow_paths.public_skills_view_dir / "alpha" / "SKILL.md").is_file()

    def test_boot_ensure_failure_returns_false_and_clears_view(self, deerflow_paths: Paths, skills_root: Path, monkeypatch: pytest.MonkeyPatch):
        (deerflow_paths.public_skills_view_dir / "stale").mkdir(parents=True)

        def _explode(storage) -> None:
            raise RuntimeError("simulated boot failure")

        monkeypatch.setattr(projection, "ensure_skill_projections", _explode)

        assert ensure_public_skill_projection(app_config=_storage_config(skills_root)) is False
        assert list(deerflow_paths.public_skills_view_dir.iterdir()) == []
