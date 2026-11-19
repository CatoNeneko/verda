# Connect DeepSeek Harness

Start the vdra core on the OS running DSH using [INSTALL.md](../../docs/INSTALL.md). The DSH plugin calls the same OpenViking API and uses the same server-side recall policy as Codex.

Copy `ovcli.conf.example` to `~/.openviking/ovcli.conf` on Linux or `%USERPROFILE%\.openviking\ovcli.conf` on Windows. If you already use OpenViking, back up the existing connection and choose which endpoint your DSH profile should use. The npm CLI also needs `pnpm` on `PATH` for its plugin commands. With DSH `0.2.2-rc.5`, use Node.js 24.9 or newer and check both `node --version` and `pnpm --version`. Desktop brings its own runtime.

For DSH CLI, use the upstream bundle in your intended profile.

```text
dsh plugin --profile web add @openviking/dsh-memory-plugin@0.5.39
dsh --profile web --dump-config
```

Use `headless` in place of `web` when that is the profile you run. Look for `openviking-memory-runtime` in the corresponding profile. Allow the MCP tool list to finish loading in a persistent session. For a short headless command, automatic recall and capture can complete before the asynchronous MCP tool list is ready. To turn it off, remove the bundle from that profile with `dsh plugin --profile web rm @openviking/dsh-memory-plugin`.

For native Windows DSH Desktop, use **Plugins → Add plugin** and install `@openviking/dsh-memory-plugin`, then enable it and open a new conversation. Desktop manages a separate profile. Its embedded CLI lives under `resources\runtime\cli\bin\dsh.cmd` in the app directory. The npm CLI's `web` profile is separate.

In a fresh DSH conversation, check the available OpenViking tools and recall a fact from your isolated test workspace. The upstream plugin handles capture and injection, including its own default settings. Use a separate profile if you want to restrict that capture to testing.

[Upstream DSH setup and configuration](https://docs.openviking.ai/en/agent-integrations/17-dsh)
