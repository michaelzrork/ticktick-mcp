"""
The shared MCP server instance every tool module registers against.

mcp 2.x renamed FastMCP to MCPServer and dropped the mcp.server.fastmcp module.
The decorator API (@mcp.tool()) and run() are unchanged.
"""

from mcp.server.mcpserver import MCPServer

# Define the shared MCP instance
mcp = MCPServer("ticktick-server")
