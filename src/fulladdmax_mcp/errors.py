"""Custom exception hierarchy for FullADDMAX-mcp."""

from __future__ import annotations


class FullADDMAXError(Exception):
    """Base class for all FullADDMAX-mcp errors."""


class LLMError(FullADDMAXError):
    """Raised when the LLM call fails (HTTP error, network, empty response, ...)."""


class LLMTimeoutError(LLMError):
    """Raised when an LLM call times out."""


class EmptyInputError(FullADDMAXError):
    """Raised when a tool receives empty / whitespace-only input."""


class ToolTimeoutError(FullADDMAXError):
    """Raised when an overall tool execution exceeds the user-provided timeout."""


class HandoffError(FullADDMAXError):
    """Raised when a swarm agent produces an invalid handoff payload."""


class ConfigError(FullADDMAXError):
    """Raised when LLM configuration is missing or invalid."""


class RateLimitError(FullADDMAXError):
    """Raised when a token-bucket rate limit is exceeded.

    Surfaces as a normal MCP tool error (``"ERROR: RateLimitError: ..."``).
    Contains retry hints in :attr:`retry_after` (seconds) and the
    limit that was hit.
    """

    def __init__(
        self,
        message: str,
        *,
        retry_after: float = 0.0,
        scope: str = "global",
    ) -> None:
        super().__init__(message)
        self.retry_after = retry_after
        self.scope = scope

    def __str__(self) -> str:
        if self.retry_after > 0:
            return f"{super().__str__()} (retry after {self.retry_after:.1f}s, scope={self.scope})"
        return f"{super().__str__()} (scope={self.scope})"


class UsageStoreError(FullADDMAXError):
    """Raised on problems writing to / reading from the UsageStore."""
