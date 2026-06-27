"""Handler packages for the 4 mega tools.

Each sub-module exposes a :data:`HANDLERS` dict that maps operation
names to async callables.  The callables accept business parameters
as keyword arguments and return a Markdown / JSON string for the MCP
client.  Schemas are registered at import time via
:func:`fulladdmax_mcp.dispatcher.register_schema`.
"""

from . import admin, agent, config, knowledge

__all__ = ["admin", "agent", "config", "knowledge"]
