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
        required_scopes=["openid", "profile", "email"],
        # Dex's access token carries no scope claim (it's an opaque-style
        # token; the real JWT is the id_token), so validate that instead of
        # the access_token. Requires fastmcp>=3.0.1.
        verify_id_token=True,
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
