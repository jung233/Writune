"""Boundary for a future official MCP Tasks extension implementation.

The installed SDK's application-level tools remain usable without an extension.
No experimental or removed Tasks API is imported here.
"""
from typing import Callable,Protocol,TYPE_CHECKING
if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP
    from writune.service import Service

class TaskExtensionAdapter(Protocol):
    def install(self,server:'FastMCP',service_factory:Callable[[], 'Service']) -> None:
        """Install an explicitly supplied, SDK-compatible standard extension."""
