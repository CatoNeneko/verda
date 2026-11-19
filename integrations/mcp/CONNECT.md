# Connect another MCP client

Start vdra using [INSTALL.md](../../docs/INSTALL.md). Add a Streamable HTTP server at `http://127.0.0.1:21933/mcp`; adapt `mcp.json.example` to your client's config format. In OpenViking API-key mode, supply the appropriate user token in the client's credential store.

Verify tool discovery and a read from the intended workspace. Use `search` with `mode="context"` and an optional real session ID for the vdra context policy. `find` is OpenViking's ordinary search. Generic MCP does not run lifecycle hooks. Automatic capture and injection depend on an adapter for your Agent.

[OpenViking MCP protocol and client examples](https://docs.openviking.ai/en/guides/06-mcp-integration)
