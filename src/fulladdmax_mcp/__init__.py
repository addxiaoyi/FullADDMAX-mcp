"""FullADDMAX-mcp: multi-agent orchestration MCP server.

Provides four workflows through MCP tools:
    - orchestrator_run: Orchestrator-Workers (planner / workers / synthesizer)
    - parallel_agents_run: bounded parallel agent fan-out (max 10)
    - map_reduce_run: sharded Map-Reduce pipeline
    - swarm_run: lightweight agent handoffs with shared history

Configure your LLM endpoint first via the ``configure_llm`` tool or
``FULLADDMAX_*`` environment variables.
"""

from .server import mcp

__version__ = "0.1.0"
__all__ = ["mcp", "__version__"]
