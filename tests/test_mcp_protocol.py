import unittest

from kb import mcp


class McpProtocolTests(unittest.TestCase):
    def test_initialize_echoes_protocol_and_identifies_server(self):
        response = mcp._handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2024-11-05"},
            }
        )

        self.assertEqual(response["id"], 1)
        self.assertEqual(response["result"]["protocolVersion"], "2024-11-05")
        self.assertEqual(response["result"]["serverInfo"]["name"], "kb-mcp")

    def test_tools_list_contains_public_tools(self):
        response = mcp._handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        names = {tool["name"] for tool in response["result"]["tools"]}

        self.assertEqual(names, {"kb_recall", "kb_add", "kb_status"})

    def test_unknown_method_returns_method_not_found(self):
        response = mcp._handle({"jsonrpc": "2.0", "id": 3, "method": "unknown"})

        self.assertEqual(response["error"]["code"], -32601)


if __name__ == "__main__":
    unittest.main()
