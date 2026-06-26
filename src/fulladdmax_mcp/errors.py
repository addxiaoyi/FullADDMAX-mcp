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
