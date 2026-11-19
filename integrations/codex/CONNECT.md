# Connect Codex

Install and start the shared vdra core using [INSTALL.md](../../docs/INSTALL.md). Then either use the upstream OpenViking plugin for automatic capture and recall, or add a direct MCP connection for on-demand use.

## Automatic capture and recall

Use Node.js 22 or newer and a Codex version supporting plugin hooks. In Codex CLI:

```text
codex plugin marketplace add volcengine/OpenViking
codex plugin add openviking-memory@openviking
```

Copy `ovcli.conf.example` to `~/.openviking/ovcli.conf` on Linux/WSL or `%USERPROFILE%\.openviking\ovcli.conf` on native Windows. If that file already points to another service, use a separate file and launch Codex with `OPENVIKING_CLI_CONFIG_FILE` set to its absolute path. With credential environment variables in use, select the intended config with `OPENVIKING_CREDENTIAL_SOURCE=cli`. Confirm that the plugin is enabled and review its hook commands in `/hooks`. The recall hook is `UserPromptSubmit`, and the capture hook is `Stop`. Add or merge `plugin_hooks = true` under `[features]` if your Codex version requires it.

Choose the Agent mode before testing. Automatic capture sends session content to the configured OpenViking server. On-demand mode below is sufficient for explicit search and project-card reads.

## On-demand MCP

Merge `config.toml.example` into the applicable Codex config. If you use the upstream automatic plugin, its MCP proxy already exposes the tools and the direct configuration is unnecessary. Reopen Codex and verify the tools with `/mcp`. Use the same loopback host OS for Codex and the memory server, especially when switching between Windows and WSL.

[Upstream OpenViking Codex setup](https://docs.openviking.ai/en/agent-integrations/04-codex) · [OpenAI Docs for MCP](https://developers.openai.com/codex/mcp)
