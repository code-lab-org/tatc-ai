from fastmcp import FastMCP

mcp = FastMCP("tatc-ai-mcp-server")


@mcp.tool()
def echo(text: str) -> str:
    """Return the provided text unchanged."""
    return text


if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="0.0.0.0", port=8000)
