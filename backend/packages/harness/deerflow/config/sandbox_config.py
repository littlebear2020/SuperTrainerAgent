from pydantic import BaseModel, ConfigDict, Field


class VolumeMountConfig(BaseModel):
    """Configuration for a volume mount."""

    host_path: str = Field(..., description="Path on the host machine")
    container_path: str = Field(..., description="Path inside the container")
    read_only: bool = Field(default=False, description="Whether the mount is read-only")


class SandboxConfig(BaseModel):
    """Config section for a sandbox.

    Common options:
        use: Class path of the sandbox provider (required)
        allow_host_bash: Enable host-side bash execution for LocalSandboxProvider.
            Dangerous and intended only for fully trusted local workflows.

    AioSandboxProvider specific options:
        image: Docker image to use (default: enterprise-public-cn-beijing.cr.volces.com/vefaas-public/all-in-one-sandbox:latest)
        port: Base port for sandbox containers (default: 8080)
        replicas: Maximum number of concurrent sandbox containers (default: 3). When the limit is reached the least-recently-used sandbox is evicted to make room.
        container_prefix: Prefix for container names (default: deer-flow-sandbox)
        idle_timeout: Idle timeout in seconds before sandbox is released (default: 600 = 10 minutes). Set to 0 to disable.
        mounts: List of volume mounts to share directories with the container
        environment: Environment variables to inject into the container (values starting with $ are resolved from host env)
        mem_limit: Container memory limit, e.g. '6g' (default 3g; env SANDBOX_MEM_LIMIT overrides; empty env disables; restart required)
        shm_size: Container /dev/shm size, e.g. '2g' (default 2g; env SANDBOX_SHM_SIZE overrides; empty env disables; restart required)
    """

    use: str = Field(
        ...,
        description="Class path of the sandbox provider (e.g. deerflow.sandbox.local:LocalSandboxProvider)",
    )
    allow_host_bash: bool = Field(
        default=False,
        description="Allow the bash tool to execute directly on the host when using LocalSandboxProvider. Dangerous; intended only for fully trusted local environments.",
    )
    image: str | None = Field(
        default=None,
        description="Docker image to use for the sandbox container",
    )
    port: int | None = Field(
        default=None,
        description="Base port for sandbox containers",
    )
    replicas: int | None = Field(
        default=None,
        description="Maximum number of concurrent sandbox containers (default: 3). When the limit is reached the least-recently-used sandbox is evicted to make room.",
    )
    container_prefix: str | None = Field(
        default=None,
        description="Prefix for container names",
    )
    idle_timeout: int | None = Field(
        default=None,
        description="Idle timeout in seconds before sandbox is released (default: 600 = 10 minutes). Set to 0 to disable.",
    )
    mounts: list[VolumeMountConfig] = Field(
        default_factory=list,
        description="List of volume mounts to share directories between host and container",
    )
    environment: dict[str, str] = Field(
        default_factory=dict,
        description="Environment variables to inject into the sandbox container. Values starting with $ will be resolved from host environment variables.",
    )
    mem_limit: str | None = Field(
        default=None,
        description=(
            "Container memory limit for LocalContainerBackend, e.g. '3g' / '6g' (also sets --memory-swap to the same "
            "value, disabling swap). None keeps the built-in default (3g). Priority: env SANDBOX_MEM_LIMIT (empty string "
            "= explicitly disabled) > this config value > built-in default. Changing this requires a gateway restart — "
            "the provider loads it once at startup (F2, 2026-09-29)."
        ),
    )
    shm_size: str | None = Field(
        default=None,
        description=(
            "Container /dev/shm size for LocalContainerBackend, e.g. '2g'. Docker's default 64M is too small for Chrome "
            "multi-process rendering (long-video 'Target closed' crashes). None keeps the built-in default (2g, aligned "
            "with remotion-worker). Priority: env SANDBOX_SHM_SIZE (empty string = explicitly disabled) > this config "
            "value > built-in default; restart required to take effect (F1/F2, 2026-09-29)."
        ),
    )

    bash_output_max_chars: int = Field(
        default=20000,
        ge=0,
        description="Maximum characters to keep from bash tool output. Output exceeding this limit is middle-truncated (head + tail), preserving the first and last half. Set to 0 to disable truncation.",
    )
    read_file_output_max_chars: int = Field(
        default=50000,
        ge=0,
        description="Maximum characters to keep from read_file tool output. Output exceeding this limit is head-truncated. Set to 0 to disable truncation.",
    )
    bash_command_timeout: int = Field(
        default=300,
        gt=0,
        description=(
            "Output-silence timeout in seconds for a bash command — the AIO sandbox maps this to "
            "`no_change_timeout`: the command is killed only when it produces NO new output for this long, "
            "so a chatty long-running command survives while a hung one is cut at 300s (D9/T27, 2026-08-13). "
            "Previously hardcoded 600s in both sandbox implementations — a hung command could stall a turn "
            "for 10 minutes. NOT a wall-clock limit: a foreground `sleep 300` with zero output is killed at "
            "the line, which is why polling loops must sleep < this value (C9, 2026-09-29). "
            "The bash tool reads this and passes it to execute_command(timeout=...)."
        ),
    )
    ls_output_max_chars: int = Field(
        default=20000,
        ge=0,
        description="Maximum characters to keep from ls tool output. Output exceeding this limit is head-truncated. Set to 0 to disable truncation.",
    )

    model_config = ConfigDict(extra="allow")
