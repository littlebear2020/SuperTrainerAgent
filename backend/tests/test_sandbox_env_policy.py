"""Env policy for sandbox subprocesses（P1-S2，2026-09-30 上游移植）。

移植自上游 tests/test_skill_request_scoped_secrets.py 的 TestEnvPolicy 段
（issue #3861 delta 1）——平台密钥清洗策略的纯函数面。
"""

import pytest


class TestEnvPolicy:
    """Platform-secret scrubbing policy for sandbox subprocesses."""

    @pytest.mark.parametrize(
        "name",
        [
            "OPENAI_API_KEY",
            "ANTHROPIC_API_KEY",
            "LANGFUSE_SECRET_KEY",
            "GITHUB_TOKEN",
            "AWS_SECRET_ACCESS_KEY",
            "DB_PASSWORD",
            "MY_SERVICE_CREDENTIAL",
            "api_key",
            "Some_Token_Here",
            # Connection-string credentials (no KEY/SECRET/TOKEN substring) — these
            # routinely embed a password, e.g. postgresql://user:pw@host/db.
            "DATABASE_URL",
            "REDIS_URL",
            "MONGODB_URI",
            "AMQP_URL",
            "SENTRY_DSN",
            "POSTGRES_DSN",
            "CONN_STR",
            "GH_PAT",
            # Password vars for services whose connection strings are already blocked
            # above. These carry no KEY/SECRET/TOKEN/PASSWORD/PASSWD substring, and a
            # blanket ``*PWD*`` / ``*AUTH*`` pattern would strip benign vars (``PWD``,
            # ``OLDPWD``), so they need exact entries.
            "MYSQL_PWD",  # read directly by mysql / libmysqlclient
            "REDISCLI_AUTH",  # read directly by redis-cli
            "REDIS_AUTH",
            # Abbreviated ``_PASS`` password vars: value-bearing plaintext passwords
            # that the full-spelling ``*PASSWORD*`` / ``*PASSWD*`` patterns miss.
            "DB_PASS",
            "SMTP_PASS",
            "MYSQL_PASS",
            "REDIS_PASS",
            "FTP_PASS",
            "MAIL_PASS",
            # Postgres file-based credential sources read by libpq/psql with no flag,
            # the direct analog of MYSQL_PWD/REDISCLI_AUTH above.
            "PGPASSFILE",
            "PGSERVICEFILE",
            # Credential *helpers*: each names a program that dispenses a credential
            # on demand.
            "GIT_ASKPASS",
            "SSH_ASKPASS",
            "SUDO_ASKPASS",
            # ssh-agent socket: a credential pointer like the ASKPASS helpers.
            "SSH_AUTH_SOCK",
        ],
    )
    def test_secret_like_names_are_blocked(self, name):
        from deerflow.sandbox.env_policy import is_blocked_env_name

        assert is_blocked_env_name(name) is True

    @pytest.mark.parametrize(
        "name",
        [
            "PATH",
            "HOME",
            "SHELL",
            "USER",
            "LANG",
            "LC_ALL",
            "PWD",
            "OLDPWD",
            "TMPDIR",
            "VIRTUAL_ENV",
            "PYTHONPATH",
            "DEERFLOW_PLAIN_VAR",
            # Not a blanket *URL* block: a benign service URL a skill may legitimately
            # read is not treated as a credential.
            "NEXT_PUBLIC_BASE_URL",
            "SERVICE_ENDPOINT",
        ],
    )
    def test_benign_names_are_allowed(self, name):
        """Names here must survive the scrub.

        Note what this list does *not* contain: any name carrying a ``PASS``
        substring. That is deliberate, not an oversight — ``*PASS*`` scrubs every
        such name. Over-scrubbing is this module's fail-safe direction; a skill
        that needs a scrubbed name declares it via ``required-secrets``.
        ``PWD``/``OLDPWD`` are the boundary this list does pin: they carry no
        ``PASS`` substring and must never be stripped.
        """
        from deerflow.sandbox.env_policy import is_blocked_env_name

        assert is_blocked_env_name(name) is False

    def test_db_password_vars_do_not_reach_the_subprocess_env(self, monkeypatch):
        """The URL forms are scrubbed; the password vars for the same services too.

        ``mysql`` reads ``MYSQL_PWD`` and ``redis-cli`` reads ``REDISCLI_AUTH`` as
        the password with no further configuration.
        """
        from deerflow.sandbox.env_policy import build_sandbox_env

        monkeypatch.setenv("MYSQL_URL", "mysql://user:pw@host/db")
        monkeypatch.setenv("PWD", "/repo")  # POSIX hosts set PWD themselves; plant it so the survival check runs on Windows too
        monkeypatch.setenv("MYSQL_PWD", "prod-db-password")
        monkeypatch.setenv("REDISCLI_AUTH", "prod-redis-auth")
        env = build_sandbox_env()
        assert "MYSQL_URL" not in env
        assert "MYSQL_PWD" not in env
        assert "REDISCLI_AUTH" not in env
        assert env.get("PWD")  # the working directory must survive the added entries

    def test_injection_still_wins_for_the_newly_blocked_names(self, monkeypatch):
        """``required-secrets`` stays the escape hatch for the names added here.

        The request-scoped value must also override the host's.
        """
        from deerflow.sandbox.env_policy import build_sandbox_env

        monkeypatch.setenv("MYSQL_PWD", "host-value-must-not-leak")
        env = build_sandbox_env(injected={"MYSQL_PWD": "request-scoped-value"})
        assert env["MYSQL_PWD"] == "request-scoped-value"

    def test_build_sandbox_env_scrubs_inherited_and_layers_injected(self, monkeypatch):
        from deerflow.sandbox.env_policy import build_sandbox_env

        monkeypatch.setenv("OPENAI_API_KEY", "platform-key-should-vanish")
        monkeypatch.setenv("HARMLESS_PLAIN", "ok")
        env = build_sandbox_env(injected={"SCOPED_TOKEN": "v"})
        assert "OPENAI_API_KEY" not in env  # platform secret scrubbed
        assert env.get("HARMLESS_PLAIN") == "ok"  # benign preserved
        assert env.get("SCOPED_TOKEN") == "v"  # injected layered on top
        assert env.get("PATH")  # core var preserved

    def test_build_sandbox_env_none_injection_still_scrubs(self, monkeypatch):
        from deerflow.sandbox.env_policy import build_sandbox_env

        monkeypatch.setenv("ANTHROPIC_API_KEY", "leak")
        env = build_sandbox_env()
        assert "ANTHROPIC_API_KEY" not in env

    def test_build_sandbox_env_emergency_off_lever(self, monkeypatch):
        """DEER_FLOW_SANDBOX_ENV_POLICY=off = 本仓紧急杆（R-P1-2 缓解）。

        只在部署事故期使用：恢复上游清洗前"全量继承"行为，让 skill
        临时取回平台密钥。
        """
        from deerflow.sandbox import env_policy

        monkeypatch.setenv("DEER_FLOW_SANDBOX_ENV_POLICY", "off")
        monkeypatch.setenv("OPENAI_API_KEY", "legacy-inherit")
        env = env_policy.build_sandbox_env()
        assert env.get("OPENAI_API_KEY") == "legacy-inherit"
