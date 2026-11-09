"""Regression tests for created_by ownership protection.

Covers:
- enforce_created_by: strip+inject scoped to frontmatter only
- extract_created_by: read from frontmatter only
- inject_created_by: unchanged append-only behavior
- edit/patch/rollback protection against tampering
- installer: archive import strips fake owner
"""

from deerflow.skills.manager import (
    enforce_created_by,
    extract_created_by,
    inject_created_by,
)

# ── Helpers ──────────────────────────────────────────────────────────────


def _skill_md(frontmatter_extra: str = "", body: str = "") -> str:
    """Build a SKILL.md content string."""
    return f"---\nname: test-skill\ndescription: A test skill\n{frontmatter_extra}---\n{body}"


# ── extract_created_by ───────────────────────────────────────────────────


class TestExtractCreatedBy:
    def test_reads_existing_owner(self):
        content = _skill_md("created_by: alice\n")
        assert extract_created_by(content) == "alice"

    def test_returns_none_when_absent(self):
        content = _skill_md()
        assert extract_created_by(content) is None

    def test_ignores_created_by_in_body(self):
        content = _skill_md() + "\ncreated_by: body-line\n"
        assert extract_created_by(content) is None

    def test_returns_none_for_no_frontmatter(self):
        assert extract_created_by("just plain text") is None

    def test_handles_empty_value(self):
        content = _skill_md("created_by:\n")
        assert extract_created_by(content) is None

    def test_handles_whitespace_value(self):
        content = _skill_md("created_by:   \n")
        assert extract_created_by(content) is None


# ── inject_created_by (unchanged append-only) ───────────────────────────


class TestInjectCreatedBy:
    def test_appends_to_frontmatter(self):
        content = _skill_md()
        result = inject_created_by(content, "alice")
        assert "created_by: alice" in result
        assert result.index("created_by: alice") < result.index("---", 3)

    def test_does_not_strip_existing(self):
        content = _skill_md("created_by: original\n")
        result = inject_created_by(content, "new")
        # Both should be present (append-only, no stripping)
        assert "created_by: original" in result
        assert "created_by: new" in result


# ── enforce_created_by ───────────────────────────────────────────────────


class TestEnforceCreatedBy:
    def test_strips_and_injects(self):
        content = _skill_md("created_by: fake\n")
        result = enforce_created_by(content, "alice")
        assert "created_by: fake" not in result
        assert "created_by: alice" in result

    def test_injects_when_absent(self):
        content = _skill_md()
        result = enforce_created_by(content, "alice")
        assert "created_by: alice" in result

    def test_strips_without_injecting_when_user_id_none(self):
        content = _skill_md("created_by: someone\n")
        result = enforce_created_by(content, None)
        assert "created_by" not in result

    def test_does_not_touch_body_with_created_by_line(self):
        body = "Some docs\n\ncreated_by: should-not-be-touched\n"
        content = _skill_md("created_by: alice\n", body)
        result = enforce_created_by(content, "bob")
        assert "created_by: alice" not in result
        assert "created_by: bob" in result
        # Body line is preserved
        assert "created_by: should-not-be-touched" in result

    def test_preserves_other_frontmatter_fields(self):
        content = _skill_md("version: '1.0'\nlicense: MIT\ncreated_by: old\n")
        result = enforce_created_by(content, "new")
        assert "version: '1.0'" in result
        assert "license: MIT" in result
        assert "created_by: old" not in result
        assert "created_by: new" in result

    def test_no_frontmatter_returns_unchanged(self):
        content = "just plain text without frontmatter"
        result = enforce_created_by(content, "alice")
        assert result == content

    def test_double_enforce_is_idempotent(self):
        content = _skill_md("created_by: fake\n")
        result = enforce_created_by(enforce_created_by(content, "alice"), "alice")
        assert result.count("created_by: alice") == 1


# ── Edit path tampering protection ──────────────────────────────────────


class TestEditTamperingProtection:
    """Verify that edit paths enforce created_by after validation.

    These tests use enforce_created_by + extract_created_by directly
    to verify the protection logic without hitting the full tool/router stack.
    """

    def test_owner_cannot_change_created_by(self):
        original = _skill_md("created_by: alice\n")
        # User tries to change owner in their edit
        edited = _skill_md("created_by: bob\n", "new body")
        result = enforce_created_by(edited, extract_created_by(original))
        assert "created_by: alice" in result
        assert "created_by: bob" not in result

    def test_owner_cannot_clear_created_by(self):
        original = _skill_md("created_by: alice\n")
        # User removes created_by from their edit
        edited = _skill_md(body="new body")
        result = enforce_created_by(edited, extract_created_by(original))
        assert "created_by: alice" in result

    def test_legacy_skill_prevents_injection(self):
        original = _skill_md()  # No created_by
        # User tries to inject created_by
        edited = _skill_md("created_by: attacker\n", "new body")
        result = enforce_created_by(edited, extract_created_by(original))
        assert "created_by" not in result

    def test_rollback_preserves_current_owner(self):
        current = _skill_md("created_by: alice\n", "current version")
        # Historical version has no created_by
        historical = _skill_md(body="old version")
        result = enforce_created_by(historical, extract_created_by(current))
        assert "created_by: alice" in result


# ── Archive import protection ────────────────────────────────────────────


class TestArchiveImportProtection:
    def test_strips_archive_fake_owner(self):
        archive_content = _skill_md("created_by: malicious\n")
        result = enforce_created_by(archive_content, "server-user")
        assert "created_by: malicious" not in result
        assert "created_by: server-user" in result

    def test_no_owner_in_archive_injects_server_owner(self):
        archive_content = _skill_md()
        result = enforce_created_by(archive_content, "server-user")
        assert "created_by: server-user" in result
