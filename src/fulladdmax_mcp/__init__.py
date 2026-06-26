"""FullADDMAX-mcp: multi-agent orchestration MCP server.

Provides four workflows through MCP tools:
    - orchestrator_run: Orchestrator-Workers (planner / workers / synthesizer)
    - parallel_agents_run: bounded parallel agent fan-out (max 10)
    - map_reduce_run: sharded Map-Reduce pipeline
    - swarm_run: lightweight agent handoffs with shared history

Configure your LLM endpoint first via the ``configure_llm`` tool or
``FULLADDMAX_*`` environment variables.
"""

__version__ = "0.2.0"

# Import ``mcp`` from server last so the version attribute above is already
# defined when ``server.py`` references ``fulladdmax_mcp.__version__``.
from .server import mcp  # noqa: E402

__all__ = ["mcp", "__version__"]
