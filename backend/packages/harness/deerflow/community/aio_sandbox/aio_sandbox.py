import base64
import logging
import math
import os
import shlex
import threading
import uuid

from agent_sandbox import Sandbox as AioSandboxClient

from deerflow.sandbox.sandbox import Sandbox
from deerflow.sandbox.search import GrepMatch, path_matches, should_ignore_path, truncate_line

logger = logging.getLogger(__name__)

_ERROR_OBSERVATION_SIGNATURE = "'ErrorObservation' object has no attribute 'exit_code'"

# ── DEF-04 C1（2026-09-23 批 1）：沙箱死亡指纹，源头单点维护 ──────────────
# 命中任一子串（对 str(exc).lower() 匹配）= 执行环境本身已死（容器不存在 /
# 连接拒绝 / daemon 失联），区别于命令正常非零退出（编译报错、grep 空命中等
# 走不到 except 分支、天然豁免）。命中时错误串加 ``[SANDBOX_DEAD]`` 机器可读
# 前缀，SandboxCircuitBreakerMiddleware 只认该标记计数，不解析自然语言措辞
# ——daemon 错误文案变化不会让熔断静默失效。指纹清单与 daemon 措辞共演进。
SANDBOX_DEAD_MARKER = "[SANDBOX_DEAD]"
_SANDBOX_DEAD_SIGNATURES = (
    "no such container",  # docker: container removed under us
    "connection refused",  # sandbox API port unreachable
    "is not running",  # "Container ... is not running" (docker API 500)
    "cannot connect to the docker daemon",  # daemon down
    "daemon is not running",
    "no route to host",  # host gone entirely
)


def is_sandbox_dead_error(exc: BaseException) -> bool:
    """Return whether an exception carries a sandbox-death fingerprint."""
    text = str(exc).lower()
    return any(signature in text for signature in _SANDBOX_DEAD_SIGNATURES)


def tag_sandbox_dead(exc: BaseException) -> str:
    """Return the error string, prefixed with the machine-readable dead marker."""
    detail = str(exc).strip() or exc.__class__.__name__
    return f"Error: {SANDBOX_DEAD_MARKER} {detail}"


def _aggregate_console_output(data) -> str:
    """Collect partial command output from ``data.console`` records.

    The AIO SDK only populates ``data.output`` when the command reaches
    ``completed`` status; timed-out/interrupted commands report partial
    output per console record instead. Join the non-empty record outputs
    so the agent sees what the command produced before it was cut off.
    """
    if not data:
        return ""
    console = getattr(data, "console", None)
    if not console:
        return ""
    parts = [record.output for record in console if getattr(record, "output", None)]
    return "\n".join(parts)


# The sandbox defaults to killing a command after 120s without new output, which
# cuts off long silent jobs such as ffmpeg encoding or Remotion renders.
_NO_CHANGE_TIMEOUT_SECONDS = 600
# B9（§4.4，2026-09-30 上游移植）：双闸常量——no_change_timeout 是地板，
# 墙钟 hard_timeout 是上限；宽限保证死人与墙钟先于 HTTP 层超时触发。
_REQUEST_TIMEOUT_GRACE_SECONDS = 5.0
_DEFAULT_HARD_TIMEOUT = 600.0


def _effective_command_timeout(timeout: float | None) -> float:
    return timeout if timeout is not None else _DEFAULT_HARD_TIMEOUT


def _effective_no_change_timeout(timeout: float) -> int:
    return max(_NO_CHANGE_TIMEOUT_SECONDS, math.ceil(timeout + _REQUEST_TIMEOUT_GRACE_SECONDS))


class AioSandbox(Sandbox):
    """Sandbox implementation using the agent-infra/sandbox Docker container.

    This sandbox connects to a running AIO sandbox container via HTTP API.
    A threading lock serializes shell commands to prevent concurrent requests
    from corrupting the container's single persistent session (see #1433).
    """

    def __init__(self, id: str, base_url: str, home_dir: str | None = None):
        """Initialize the AIO sandbox.

        Args:
            id: Unique identifier for this sandbox instance.
            base_url: URL of the sandbox API (e.g., http://localhost:8080).
            home_dir: Home directory inside the sandbox. If None, will be fetched from the sandbox.
        """
        super().__init__(id)
        self._base_url = base_url
        self._client = AioSandboxClient(base_url=base_url, timeout=600)
        self._home_dir = home_dir
        self._lock = threading.Lock()

    @property
    def base_url(self) -> str:
        return self._base_url

    @property
    def home_dir(self) -> str:
        """Get the home directory inside the sandbox."""
        if self._home_dir is None:
            context = self._client.sandbox.get_context()
            self._home_dir = context.home_dir
        return self._home_dir

    def _execute_command_with_env(self, command: str, timeout: float | None, env: dict[str, str]) -> str:
        """评审二轮修复（H1 消费端）：经 bash.exec 通道按调用注入 env。

        bash.exec 与 shell.exec_command 是并列 API（独立会话空间），支持
        ``env`` 参数——这正是上游 required-secrets 注入链的消费端。超时
        双闸语义与主路径一致（hard=墙钟）；死亡打标复用同一指纹清单。

        返回形状注意（2026-10-13 公司服务器实测修正）：bash.exec 的 data 是
        ``stdout``/``stderr``/``exit_code``——与 shell.exec_command 的
        ``output``/``console`` 不同族。此前按 shell 形状读 ``data.output``，
        路由 404 时代该缺陷被 HTTP 错误遮挡，沙箱基座升 1.11.0 修复路由后
        显形为 ``'BashExecResult' object has no attribute 'output'``。
        """
        with self._lock:
            effective_timeout = _effective_command_timeout(timeout)
            try:
                result = self._client.bash.exec(
                    command=command,
                    env=env,
                    timeout=effective_timeout,
                    hard_timeout=effective_timeout,
                )
                parts: list[str] = []
                if result.data is not None:
                    if result.data.stdout:
                        parts.append(result.data.stdout)
                    if result.data.stderr:
                        parts.append(result.data.stderr)
                return "\n".join(parts)
            except Exception as e:
                logger.error(f"Failed to execute command with env in sandbox: {e}")
                if is_sandbox_dead_error(e):
                    return tag_sandbox_dead(e)
                return f"Error: {e}"

    def execute_command(self, command: str, timeout: float | None = None, env: dict[str, str] | None = None) -> str:
        """Execute a shell command in the sandbox.

        ``timeout`` (T27, 2026-08-13) maps to the SDK's ``no_change_timeout``:
        a per-command cap replacing the previously hardcoded 600s default.

        ``env``（评审二轮修复，2026-09-30）：非空时切换到 SDK 的 ``bash.exec``
        单命令通道按调用注入环境变量——值不进命令串、不驻留容器 env
        （`env` dump 不可见，仅本条命令可见）；为空走既有持久 shell 会话
        路径（行为零变化）。带 env 的执行不经持久会话（无 cwd 续接），
        技能脚本场景无会话依赖。

        Uses a lock to serialize concurrent requests. The AIO sandbox
        container maintains a single persistent shell session that
        corrupts when hit with concurrent exec_command calls (returns
        ``ErrorObservation`` instead of real output). If corruption is
        detected despite the lock (e.g. multiple processes sharing a
        sandbox), the command is retried on a fresh session.

        Args:
            command: The command to execute.

        Returns:
            The output of the command.
        """
        if env:
            return self._execute_command_with_env(command, timeout, env)

        with self._lock:
            effective_timeout = _effective_command_timeout(timeout)
            no_change_timeout = _effective_no_change_timeout(effective_timeout)
            try:
                result = self._client.shell.exec_command(command=command, no_change_timeout=no_change_timeout, hard_timeout=effective_timeout)
                output = result.data.output if result.data else ""

                if output and _ERROR_OBSERVATION_SIGNATURE in output:
                    logger.warning("ErrorObservation detected in sandbox output, retrying with a fresh session")
                    fresh_id = str(uuid.uuid4())
                    # R-05 ② (2026-08-13): exec_command only auto-creates a
                    # session when called with NO id — the recovery session must
                    # be created explicitly before targeting it, or the retry
                    # keeps failing against the corrupted session.
                    self._client.shell.create_session(id=fresh_id)
                    try:
                        result = self._client.shell.exec_command(command=command, id=fresh_id, no_change_timeout=no_change_timeout, hard_timeout=effective_timeout)
                        output = result.data.output if result.data else ""
                    finally:
                        # The ad-hoc recovery session is single-use; leaving it behind
                        # leaks a shell session inside the container on every retry.
                        try:
                            self._client.shell.cleanup_session(fresh_id)
                        except Exception as cleanup_error:
                            logger.warning(f"Failed to cleanup recovery shell session {fresh_id}: {cleanup_error}")

                if not output:
                    # R-05 ① (2026-08-13): ``data.output`` is only populated when
                    # the command completes; a no-change/hard-timeout result
                    # carries partial output in ``data.console``. Surface it with
                    # a marker instead of silently reporting "(no output)".
                    partial = _aggregate_console_output(result.data)
                    if partial:
                        output = f"{partial}\n[command timed out/interrupted — partial output]"
                        logger.warning("Sandbox command returned partial console output (status=%s)", getattr(getattr(result, "data", None), "status", None))

                return output if output else "(no output)"
            except Exception as e:
                logger.error(f"Failed to execute command in sandbox: {e}")
                # C1（DEF-04）：死亡指纹 → 机器可读前缀，熔断中间件只认标记。
                # 正常非零退出不走本分支；agent 可见的错误文本仍含原始内容。
                if is_sandbox_dead_error(e):
                    return tag_sandbox_dead(e)
                return f"Error: {e}"

    def read_file(self, path: str) -> str:
        """Read the content of a file in the sandbox.

        Args:
            path: The absolute path of the file to read.

        Returns:
            The content of the file.
        """
        try:
            result = self._client.file.read_file(file=path)
            return result.data.content if result.data else ""
        except Exception as e:
            logger.error(f"Failed to read file in sandbox: {e}")
            # B10（§4.4）：对齐 execute_command 的死亡打标形态。
            if is_sandbox_dead_error(e):
                return tag_sandbox_dead(e)
            return f"Error: {e}"

    def list_dir(self, path: str, max_depth: int = 2) -> list[str]:
        """List the contents of a directory in the sandbox.

        Args:
            path: The absolute path of the directory to list.
            max_depth: The maximum depth to traverse. Default is 2.

        Returns:
            The contents of the directory.
        """
        with self._lock:
            try:
                result = self._client.shell.exec_command(
                    command=f"find {shlex.quote(path)} -maxdepth {max_depth} -type f -o -type d 2>/dev/null | head -500", no_change_timeout=_NO_CHANGE_TIMEOUT_SECONDS, hard_timeout=_effective_command_timeout(None)
                )
                output = result.data.output if result.data else ""
                if output:
                    return [line.strip() for line in output.strip().split("\n") if line.strip()]
                return []
            except Exception as e:
                logger.error(f"Failed to list directory in sandbox: {e}")
                # B10（§4.4）：容器死亡以单条打标条目浮出——ls 工具 join 后，
                # 熔断中间件从结果文本计数 [SANDBOX_DEAD]，纯文件工具循环可熔断。
                if is_sandbox_dead_error(e):
                    return [tag_sandbox_dead(e)]
                return []

    def write_file(self, path: str, content: str, append: bool = False) -> None:
        """Write content to a file in the sandbox.

        Args:
            path: The absolute path of the file to write to.
            content: The text content to write to the file.
            append: Whether to append the content to the file.
        """
        with self._lock:
            try:
                if append:
                    self._client.file.write_file(file=path, content=content, append=True)
                else:
                    self._client.file.write_file(file=path, content=content)
            except Exception as e:
                logger.error(f"Failed to write file in sandbox: {e}")
                # B10（§4.4）：死亡时换带标记的 RuntimeError，让错误回执可熔断；
                # 非死亡错误原样上抛。
                if is_sandbox_dead_error(e):
                    raise RuntimeError(tag_sandbox_dead(e)) from e
                raise

    def glob(self, path: str, pattern: str, *, include_dirs: bool = False, max_results: int = 200) -> tuple[list[str], bool]:
        if not include_dirs:
            result = self._client.file.find_files(path=path, glob=pattern)
            files = result.data.files if result.data and result.data.files else []
            filtered = [file_path for file_path in files if not should_ignore_path(file_path)]
            truncated = len(filtered) > max_results
            return filtered[:max_results], truncated

        result = self._client.file.list_path(path=path, recursive=True, show_hidden=False)
        entries = result.data.files if result.data and result.data.files else []
        matches: list[str] = []
        root_path = path.rstrip("/") or "/"
        root_prefix = root_path if root_path == "/" else f"{root_path}/"
        for entry in entries:
            if entry.path != root_path and not entry.path.startswith(root_prefix):
                continue
            if should_ignore_path(entry.path):
                continue
            rel_path = entry.path[len(root_path) :].lstrip("/")
            if path_matches(pattern, rel_path):
                matches.append(entry.path)
                if len(matches) >= max_results:
                    return matches, True
        return matches, False

    def grep(
        self,
        path: str,
        pattern: str,
        *,
        glob: str | None = None,
        literal: bool = False,
        case_sensitive: bool = False,
        max_results: int = 100,
    ) -> tuple[list[GrepMatch], bool]:
        import re as _re

        regex_source = _re.escape(pattern) if literal else pattern
        # Validate the pattern locally so an invalid regex raises re.error
        # (caught by grep_tool's except re.error handler) rather than a
        # generic remote API error.
        _re.compile(regex_source, 0 if case_sensitive else _re.IGNORECASE)
        regex = regex_source if case_sensitive else f"(?i){regex_source}"

        if glob is not None:
            find_result = self._client.file.find_files(path=path, glob=glob)
            candidate_paths = find_result.data.files if find_result.data and find_result.data.files else []
        else:
            list_result = self._client.file.list_path(path=path, recursive=True, show_hidden=False)
            entries = list_result.data.files if list_result.data and list_result.data.files else []
            candidate_paths = [entry.path for entry in entries if not entry.is_directory]

        matches: list[GrepMatch] = []
        truncated = False

        for file_path in candidate_paths:
            if should_ignore_path(file_path):
                continue

            search_result = self._client.file.search_in_file(file=file_path, regex=regex)
            data = search_result.data
            if data is None:
                continue

            line_numbers = data.line_numbers or []
            matched_lines = data.matches or []
            for line_number, line in zip(line_numbers, matched_lines):
                matches.append(
                    GrepMatch(
                        path=file_path,
                        line_number=line_number if isinstance(line_number, int) else 0,
                        line=truncate_line(line),
                    )
                )
                if len(matches) >= max_results:
                    truncated = True
                    return matches, truncated

        return matches, truncated

    def update_file(self, path: str, content: bytes) -> None:
        """Update a file with binary content in the sandbox.

        Args:
            path: The absolute path of the file to update.
            content: The binary content to write to the file.
        """
        with self._lock:
            try:
                base64_content = base64.b64encode(content).decode("utf-8")
                self._client.file.write_file(file=path, content=base64_content, encoding="base64")
            except Exception as e:
                logger.error(f"Failed to update file in sandbox: {e}")
                raise

    def upload_file_stream(self, path: str, local_path: str | os.PathLike) -> None:
        """Stream a local file into the sandbox via the SDK's multipart endpoint.

        Uses ``client.file.upload_file(file=IO[bytes])`` to stream the file
        content directly into the sandbox without loading it all into memory
        or base64-encoding it — avoiding the O(N) memory overhead of
        :meth:`update_file` for large files.

        Args:
            path: Virtual path inside the sandbox.
            local_path: Local filesystem path to the source file.
        """
        with self._lock, open(local_path, "rb") as fh:
            self._client.file.upload_file(file=fh, path=path)
