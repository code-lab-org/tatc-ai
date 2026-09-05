import os

from fastmcp import FastMCP


def _build_auth():
    """OIDC auth against Dex, enabled only when the deploy stack configures it.

    The dev compose file sets none of these, so the dev server stays open.
    """
    issuer_url = os.environ.get("MCP_OIDC_ISSUER_URL")
    if not issuer_url:
        return None

    from fastmcp.server.auth.oidc_proxy import OIDCProxy

    return OIDCProxy(
        config_url=f"{issuer_url}/.well-known/openid-configuration",
        client_id=os.environ["MCP_OIDC_CLIENT_ID"],
        client_secret=os.environ["MCP_OIDC_CLIENT_SECRET"],
        base_url=os.environ["MCP_BASE_URL"],
        # Must match the mcp-server redirectURIs entry in config/dex/start-dex.sh.
        redirect_path="/oauth/callback",
        # Deliberately not setting required_scopes: in fastmcp 2.14.7,
        # OIDCProxy enforces it against Dex's own token, which carries no
        # scope claim (Dex omits it per RFC 6749 §5.1, since the granted
        # scope matches what was requested) - every token gets rejected as
        # a result. The scope actually requested from Dex comes from
        # LibreChat's own /authorize call (scope=openid+profile+email),
        # independent of this setting.
    )


mcp = FastMCP("tatc-ai-mcp-server", auth=_build_auth())


@mcp.tool()
def echo(text: str) -> str:
    """Return the provided text unchanged."""
    return text


if __name__ == "__main__":
    mcp.run(
        transport="streamable-http",
        host="0.0.0.0",
        port=8000,
        # Behind Traefik, uvicorn otherwise only trusts X-Forwarded-* from
        # 127.0.0.1: it would see every request as http on the container's
        # internal address, mismatching the https public URL OIDCProxy uses
        # for token audiences, and rejecting every token as invalid.
        uvicorn_config={"proxy_headers": True, "forwarded_allow_ips": "*"},
    )
