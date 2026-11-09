"""Config for token budget middleware."""

from pydantic import BaseModel, Field, model_validator


class TokenBudgetConfig(BaseModel):
    """Configuration for per-run token budget enforcement.

    Positioning (2026-08-13 D7, thresholds calibrated against the local
    112GB DB's real run distribution):
      * This guard stops 8M+ "runaway monster" runs (e.g. the 256M/12.7M
        historical outliers) — it does NOT and cannot stop a 1.38M-token
        input-explosion accident, which completes in 4 calls and sits inside
        the legitimate range (recent p95 = 2.8M, max = 7.6M). That shape is
        handled by T12 (view_image no longer fuels checkpoint base64) + T1
        (loop detection).
      * REF's 200K default would false-kill more than half of this
        deployment's successful runs (recent p50 = 236K) — do NOT import it.
      * Ships enabled:false; enabling (warn first, then hard) is a separate
        decision after a 1-2 week observation window watching whether the
        success distribution approaches 8M.
    """

    enabled: bool = Field(default=False, description="Whether to enable per-run token budget enforcement.")
    max_tokens: int = Field(default=8_000_000, ge=1000, description="Maximum total tokens (input + output) allowed per run. Calibrated: recent-success max 7.6M × 1.05.")
    max_input_tokens: int | None = Field(default=6_000_000, ge=1, description="Optional separate limit for input tokens only (second line against input-explosion shapes).")
    max_output_tokens: int | None = Field(default=None, ge=1, description="Optional separate limit for output tokens only.")
    warn_threshold: float = Field(default=0.6, ge=0.0, le=1.0, description="Fraction of max_tokens at which a soft warning is injected. 0.6 → warn at 4.8M.")
    hard_stop_threshold: float = Field(default=1.0, ge=0.0, le=1.0, description=("Fraction of max_tokens at which tool calls are stripped and the agent is forced to produce a final answer."))

    @model_validator(mode="after")
    def validate_thresholds(self) -> "TokenBudgetConfig":
        """Ensure hard stop cannot trigger before the warning."""
        if self.hard_stop_threshold < self.warn_threshold:
            raise ValueError("hard_stop_threshold must be >= warn_threshold")
        return self
