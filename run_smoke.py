"""
Smoke Test - 快速验证核心组件（不需要真实 API）

验证内容：
1. YamlOrchestrator 能正确加载 YAML 配置
2. LLMClient 能正确构建请求（不发送）
3. AgentRunner 初始化流程
4. 打包器能生成 agent.json
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

BASE_DIR = Path(__file__).parent
sys.path.insert(0, str(BASE_DIR))

from agent_compose.llm_client import LLMClient
from agent_compose.agent_runner import AgentRunner
from agent_compose.orchestrator import YamlOrchestrator
from agent_compose.packagers.agent_packager import AgentPackager
from agent_compose.mcp_sse_client import MCPSSEClient
from agent_compose.config_resolver import ConfigResolver
from agent_compose.definition_loader import DefinitionLoader


class TestSmoke(unittest.TestCase):

    def test_1_load_yaml_config(self):
        """测试：YAML 配置加载"""
        print("\n[Smoke Test 1] YAML 配置加载...")
        project_dir = BASE_DIR / "examples" / "kimi_webbridge"
        orch = YamlOrchestrator(project_dir=str(project_dir))

        agents = orch.list_agents()
        print(f"  发现 Agent: {agents}")
        self.assertIn("webbridge_agent", agents)

        agent = orch.get_agent("webbridge_agent")
        self.assertIsNotNone(agent)
        self.assertIn("role", agent)
        self.assertIn("model", agent)
        print(f"  角色: {agent['role']}")
        print(f"  模型: {agent['model']}")
        print("  ✓ YAML 加载通过")

    def test_2_config_resolver_env(self):
        """测试：环境变量解析"""
        print("\n[Smoke Test 2] 环境变量解析...")
        os.environ["TEST_VAR"] = "hello_world"
        resolver = ConfigResolver()
        result = resolver._substitute_env_vars("Value is ${TEST_VAR}")
        self.assertEqual(result, "Value is hello_world")
        print("  ✓ 环境变量解析通过")

    def test_3_llm_client_config(self):
        """测试：LLM 客户端配置"""
        print("\n[Smoke Test 3] LLM 客户端配置...")
        client = LLMClient(
            provider="kimi",
            api_key="sk-test",
            model="mochi-v2-5",
            base_url="https://api.moonshot.cn/v1",
        )
        self.assertEqual(client.provider, "kimi")
        self.assertEqual(client.model, "mochi-v2-5")
        self.assertTrue(client.base_url)
        print(f"  Provider: {client.provider}")
        print(f"  Model: {client.model}")
        print(f"  Base URL: {client.base_url}")
        print("  ✓ LLM 客户端配置通过")

    def test_4_mcp_client(self):
        """测试：MCP 客户端初始化"""
        print("\n[Smoke Test 4] MCP 客户端初始化...")
        client = MCPSSEClient(sse_url="http://127.0.0.1:6001/sse", auth_token="test")
        self.assertEqual(client.sse_url, "http://127.0.0.1:6001/sse")
        self.assertEqual(client.auth_token, "test")
        self.assertFalse(client._connected)
        print("  ✓ MCP 客户端初始化通过")

    def test_5_agent_packager(self):
        """测试：打包器生成 agent.json"""
        print("\n[Smoke Test 5] Agent Packager...")

        project_dir = BASE_DIR / "examples" / "kimi_webbridge"
        orch = YamlOrchestrator(project_dir=str(project_dir))
        agent_config = orch.get_agent("webbridge_agent")
        self.assertIsNotNone(agent_config)

        tmp = tempfile.mkdtemp(prefix="ac_test_pkg_")
        packager = AgentPackager(output_dir=tmp)
        path = packager.package("webbridge_agent", agent_config)

        agent_json_path = Path(path) / "agent.json"
        self.assertTrue(agent_json_path.exists())
        with open(agent_json_path, encoding="utf-8") as f:
            agent_json = json.load(f)

        self.assertEqual(agent_json["type"], "agent")
        self.assertIn("name", agent_json)
        self.assertIn("capabilities", agent_json)
        print(f"  agent.json keys: {list(agent_json.keys())}")
        print(f"  capabilities: {agent_json.get('capabilities', [])}")
        print("  ✓ Agent Packager 通过")

    def test_6_agent_runner_init(self):
        """测试：AgentRunner 初始化流程（无真实连接）"""
        print("\n[Smoke Test 6] Agent Runner 初始化（模拟模式）...")

        project_dir = BASE_DIR / "examples" / "kimi_webbridge"
        runner = AgentRunner(
            project_dir=str(project_dir),
            agent_name="webbridge_agent",
            api_key="sk-test-key",
            webbridge_token="test-token",
        )

        # 不调用真实连接，只测试配置加载
        runner.orchestrator.get_agent("webbridge_agent")
        self.assertIsNotNone(runner.orchestrator)

        print("  ✓ Agent Runner 初始化通过")

    def test_7_tool_schema_format(self):
        """测试：工具 schema 格式"""
        print("\n[Smoke Test 7] 工具 Schema 格式...")
        from agent_compose.agent_runner import _format_mcp_tools_schema

        mcp_tools = [
            {
                "name": "page_navigate",
                "description": "Navigate to a URL",
                "inputSchema": {
                    "type": "object",
                    "properties": {"url": {"type": "string"}},
                    "required": ["url"],
                },
            },
            {
                "name": "page_extract",
                "description": "Extract content from page",
                "inputSchema": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": [],
                },
            },
        ]

        schema = _format_mcp_tools_schema(mcp_tools)
        self.assertEqual(len(schema), 2)
        self.assertEqual(schema[0]["type"], "function")
        self.assertEqual(schema[0]["function"]["name"], "page_navigate")
        self.assertIn("url", schema[0]["function"]["parameters"]["properties"])
        self.assertIn("url", schema[0]["function"]["parameters"]["required"])
        print(f"  工具 Schema 数量: {len(schema)}")
        print(f"  第一个工具: {schema[0]['function']['name']}")
        print("  ✓ 工具 Schema 格式通过")

    def test_8_builtin_tools(self):
        """测试：内置工具"""
        print("\n[Smoke Test 8] 内置工具...")

        project_dir = BASE_DIR / "examples" / "kimi_webbridge"
        runner = AgentRunner(
            project_dir=str(project_dir),
            agent_name="webbridge_agent",
            api_key="sk-test",
        )

        # 测试 calculator
        result = runner._builtin_calculator({"expression": "2 + 2"})
        self.assertIn("4", result["result"])
        print(f"  calculator('2+2'): {result['result']}")

        # 测试 file writer/reader
        tmp_file = tempfile.mktemp(suffix=".txt", prefix="ac_test_")
        result = runner._builtin_file_writer({"path": tmp_file, "content": "hello world"})
        self.assertIn("写入", result["result"])

        result = runner._builtin_file_reader({"path": tmp_file})
        self.assertIn("hello world", result["result"])
        print(f"  file_writer/reader: ✓")

        os.unlink(tmp_file)
        print("  ✓ 内置工具通过")


def main():
    print("=" * 70)
    print(" agent-compose - Smoke Test")
    print("=" * 70)

    loader = unittest.TestLoader()
    suite = loader.loadTestsFromTestCase(TestSmoke)
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    print("\n" + "=" * 70)
    if result.wasSuccessful():
        print("✓ 所有 Smoke Test 通过！")
    else:
        print("✗ 有测试失败，请检查。")
    print("=" * 70)

    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
