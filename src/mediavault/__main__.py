"""Entry point: python -m mediavault or the mediavault script.

Runs the MCP server over stdio, the transport every desktop MCP client
(Claude Desktop, Cursor, DSH, ...) speaks for local servers.
"""

from mediavault.server import mcp


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
