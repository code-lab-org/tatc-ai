import importlib.util
import os
import runpy
import sys
import types
import unittest
from pathlib import Path
from unittest import mock


SERVER_PATH = Path(__file__).resolve().parents[1] / "src" / "server.py"

OIDC_ENV_VARS = (
    "MCP_OIDC_ISSUER_URL",
    "MCP_OIDC_CLIENT_ID",
    "MCP_OIDC_CLIENT_SECRET",
    "MCP_BASE_URL",
)


class FakeFastMCP:
    last_instance = None

    def __init__(self, name, auth=None):
        self.name = name
        self.auth = auth
        self.registered_tools = []
        self.run_kwargs = None
        FakeFastMCP.last_instance = self

    def tool(self):
        def decorator(func):
            self.registered_tools.append(func.__name__)
            return func

        return decorator

    def run(self, **kwargs):
        self.run_kwargs = kwargs


class FakeOIDCProxy:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class ServerModuleTests(unittest.TestCase):
    def setUp(self):
        self.original_modules = {
            name: sys.modules.get(name)
            for name in (
                "fastmcp",
                "fastmcp.server",
                "fastmcp.server.auth",
                "fastmcp.server.auth.oidc_proxy",
            )
        }

        fake_fastmcp = types.ModuleType("fastmcp")
        fake_fastmcp.FastMCP = FakeFastMCP
        fake_oidc_proxy = types.ModuleType("fastmcp.server.auth.oidc_proxy")
        fake_oidc_proxy.OIDCProxy = FakeOIDCProxy

        sys.modules["fastmcp"] = fake_fastmcp
        sys.modules["fastmcp.server"] = types.ModuleType("fastmcp.server")
        sys.modules["fastmcp.server.auth"] = types.ModuleType("fastmcp.server.auth")
        sys.modules["fastmcp.server.auth.oidc_proxy"] = fake_oidc_proxy

        FakeFastMCP.last_instance = None

        self.env_patcher = mock.patch.dict(os.environ, {}, clear=False)
        self.env_patcher.start()
        for name in OIDC_ENV_VARS:
            os.environ.pop(name, None)

    def tearDown(self):
        self.env_patcher.stop()
        for name, module in self.original_modules.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module

    @staticmethod
    def load_server_module():
        spec = importlib.util.spec_from_file_location("tat_ai_server", SERVER_PATH)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)
        return module

    def test_server_registers_echo_tool(self):
        module = self.load_server_module()

        self.assertEqual(module.mcp.name, "tatc-ai-mcp-server")
        self.assertIn("echo", module.mcp.registered_tools)
        self.assertEqual(module.echo("hello"), "hello")

    def test_no_auth_when_oidc_not_configured(self):
        module = self.load_server_module()

        self.assertIsNone(module.mcp.auth)

    def test_builds_oidc_auth_when_configured(self):
        os.environ["MCP_OIDC_ISSUER_URL"] = "https://auth.example.com"
        os.environ["MCP_OIDC_CLIENT_ID"] = "mcp-server"
        os.environ["MCP_OIDC_CLIENT_SECRET"] = "test-secret"
        os.environ["MCP_BASE_URL"] = "https://mcp.example.com"

        module = self.load_server_module()

        self.assertIsInstance(module.mcp.auth, FakeOIDCProxy)
        self.assertEqual(
            module.mcp.auth.kwargs["config_url"],
            "https://auth.example.com/.well-known/openid-configuration",
        )
        self.assertEqual(module.mcp.auth.kwargs["client_id"], "mcp-server")
        self.assertEqual(module.mcp.auth.kwargs["client_secret"], "test-secret")
        self.assertEqual(module.mcp.auth.kwargs["base_url"], "https://mcp.example.com")
        self.assertEqual(module.mcp.auth.kwargs["redirect_path"], "/oauth/callback")
        self.assertEqual(
            module.mcp.auth.kwargs["required_scopes"], ["openid", "profile", "email"]
        )
        self.assertTrue(module.mcp.auth.kwargs["verify_id_token"])

    def test_main_runs_streamable_http_server(self):
        runpy.run_path(str(SERVER_PATH), run_name="__main__")

        instance = FakeFastMCP.last_instance
        self.assertIsNotNone(instance)
        self.assertEqual(
            instance.run_kwargs,
            {
                "transport": "streamable-http",
                "host": "0.0.0.0",
                "port": 8000,
                "uvicorn_config": {"proxy_headers": True, "forwarded_allow_ips": "*"},
            },
        )


if __name__ == "__main__":
    unittest.main()
