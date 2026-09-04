import importlib.util
import runpy
import sys
import types
import unittest
from pathlib import Path


SERVER_PATH = Path(__file__).resolve().parents[1] / "src" / "server.py"


class FakeFastMCP:
    last_instance = None

    def __init__(self, name):
        self.name = name
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


class ServerModuleTests(unittest.TestCase):
    def setUp(self):
        self.original_fastmcp = sys.modules.get("fastmcp")
        fake_module = types.ModuleType("fastmcp")
        fake_module.FastMCP = FakeFastMCP
        sys.modules["fastmcp"] = fake_module
        FakeFastMCP.last_instance = None

    def tearDown(self):
        if self.original_fastmcp is None:
            sys.modules.pop("fastmcp", None)
        else:
            sys.modules["fastmcp"] = self.original_fastmcp

    def test_server_registers_echo_tool(self):
        spec = importlib.util.spec_from_file_location("tat_ai_server", SERVER_PATH)
        module = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(module)

        self.assertEqual(module.mcp.name, "tat-ai-mcp-server")
        self.assertIn("echo", module.mcp.registered_tools)
        self.assertEqual(module.echo("hello"), "hello")

    def test_main_runs_streamable_http_server(self):
        runpy.run_path(str(SERVER_PATH), run_name="__main__")

        instance = FakeFastMCP.last_instance
        self.assertIsNotNone(instance)
        self.assertEqual(
            instance.run_kwargs,
            {"transport": "streamable-http", "host": "0.0.0.0", "port": 8000},
        )


if __name__ == "__main__":
    unittest.main()
