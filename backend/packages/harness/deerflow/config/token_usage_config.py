from pydantic import BaseModel, Field

# Attention threshold, in cumulative tokens per run.
#
# Derived from this deployment's 869 real runs (measured 2026-08-03):
#
#     median      121,182
#     p95       7,462,994
#     p99      29,634,502
#     max     256,517,593
#     lead_agent mean ≈ 1,100,000
#
# 387 of those runs exceed upstream's 200,000 default and 370 of them
# *succeeded* — that default would flag 45% of healthy work, so it is not
# usable here. p95, rounded to 7,500,000, marks the runs that are genuinely
# unusual for this workload.
#
# This threshold is a **reporting** signal only: crossing it writes a log line
# and lights up a flag in the operations console. It never warns the model,
# never strips tool_calls, and never ends a run. Video jobs legitimately run
# long, and injecting "wrap up your work" would make them cut storyboards short.
DEFAULT_ATTENTION_THRESHOLD_TOKENS = 7_500_000


class TokenUsageConfig(BaseModel):
    """Configuration for token usage tracking."""

    enabled: bool = Field(default=True, description="Enable token usage tracking middleware")
    attention_threshold_tokens: int = Field(
        default=DEFAULT_ATTENTION_THRESHOLD_TOKENS,
        ge=0,
        description="Cumulative per-run tokens above which a run is logged and flagged in the operations console. 0 disables the flag. Recording only — never alters agent behavior.",
    )
