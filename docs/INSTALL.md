# Agent 部署指南 / Agent Deployment Guide

[简体中文](#中文) · [English](#english)

## 中文

本指南供负责部署 Verda 的 Agent 执行。开始前识别当前客户端、实际运行客户端的操作系统，以及是否已有 OpenViking 服务。使用 [v0.1.0a1 发行页](https://github.com/CatoNeneko/verda/releases/tag/v0.1.0a1) 的对应 ZIP 和 `SHA256SUMS`。

1. **选包。** Codex 选 `-codex.zip`，DeepSeek Harness 选 `-dsh.zip`，提供 Streamable HTTP MCP 的其他 Agent 选 `-mcp.zip`。先核对 ZIP 的 SHA-256，再解压到独立目录。
2. **建环境。** 原生 Windows 使用 Windows x64 Python 3.11 或 3.12。Linux/WSL 使用同架构的 Linux Python。用 `python -m venv .venv` 建环境，然后用这个环境的 Python 安装包内的 `vdra-0.1.0a1-py3-none-any.whl`。Windows 解释器在 `.venv\Scripts\python.exe`，Linux/WSL 在 `.venv/bin/python`。
3. **配置。** 将 `examples/ov.conf.example` 复制为 `ov.conf`，将 `examples/work-memory.json` 复制到新的 `state/work-memory.json`。设置绝对的 `storage.workspace`、可用的向量提供者、模型和维度。沿用已获授权的提供者和本地凭据入口；缺少可用向量配置时向用户明确这一项。样例绑定 `127.0.0.1:21933`，其 dev 认证模式适合单机一次性试用。使用私人资料时选择 OpenViking 认证，并对齐客户端身份与工作记忆的账户和用户。配置文件使用无 BOM 的 UTF-8。
4. **启动和接入。** 用环境内的解释器执行 `python -m vdra serve --home ./state --config ./ov.conf`。在服务所在系统内检查 `/health`，并用 `state/maintenance-runtime.json` 确认加载版本。按选定包的 `integrations/<agent>/CONNECT.md` 配置插件或 MCP。需要启用自动采集时，按客户端的信任和审批流程操作。
5. **验证。** 确认 Agent 能发现 MCP 工具，并读到独立工作区的一条测试事实。用两段会话测试一条有来源的事实和一个缺失的细节。有明确项目卡后，按 [项目配置](PROJECTS.md) 添加别名和事实 URI。向用户报告实际通过的步骤、服务端口和客户端接入状态。

### 首次导入历史

首次部署后，请先导入用户希望延续的历史对话。选定用户授权的本地会话和时间范围，核对目标服务地址、账户及用户。先选一个小批次验证，再导入剩余历史。

对提供原生适配器的日志（如 Codex），使用同一虚拟环境的 `openviking-server ingest`。让 `OPENVIKING_CONFIG_FILE` 指向此次导入配置的绝对路径。在 `ingest` 中启用导入，设置目标 `server_url`、账户和用户，并只启用所选来源的 `backfill` 模式。用 `list-sources` 核对适配器，用所选来源的 `paths` 指向已确认范围。以 Codex 为例，先执行 `openviking-server ingest backfill --harness codex --dry-run`。确认范围和提取成本后移除 `--dry-run`，需要限定日期时加 `--since YYYY-MM-DD`。提交导入会话可能触发 OpenViking 的记忆提取模型。保留导入游标，以便分批续跑。[OpenViking 日志导入说明](https://docs.openviking.ai/en/agent-integrations/09-log-ingestion)

对当前安装版无原生日志适配器的客户端（如 DSH），优先读取用户授权的导出或原始记录，把重要决定和结果整理成带日期、消息引用的项目卡和主题卡。保留原始记录供查证。导入后开启新会话，确认可以召回一条过去的事实及其来源，然后汇报实际覆盖的历史范围。

### 升级与回滚

停服务后备份独立工作区、配置及 `state`，保留向量模型和维度。升级时在新虚拟环境安装发行 wheel，连接原工作区并验证一条已知事实。同一工作区一次只运行一个服务。回滚时切回前一 wheel，或以同一份配置启动 `openviking-server --config ov.conf --workers 1`。保留 vdra 状态便于重新启用。

## English

This guide is for the agent deploying Verda. Identify the client, the OS it actually runs on, and any existing OpenViking service. Use an Agent bundle and `SHA256SUMS` from the [v0.1.0a1 release](https://github.com/CatoNeneko/verda/releases/tag/v0.1.0a1).

1. **Select the bundle.** Use `-codex.zip` for Codex, `-dsh.zip` for DeepSeek Harness, or `-mcp.zip` for another Streamable HTTP MCP client. Verify its SHA-256 checksum and extract it into a dedicated directory.
2. **Create the environment.** Use native x64 Python 3.11 or 3.12 on Windows and the Linux interpreter on Linux or WSL. Create a venv with `python -m venv .venv` and install the bundled `vdra-0.1.0a1-py3-none-any.whl` with that venv's Python. Find it at `.venv\Scripts\python.exe` on Windows or `.venv/bin/python` on Linux and WSL.
3. **Configure.** Copy `examples/ov.conf.example` to `ov.conf` and `examples/work-memory.json` to a new `state/work-memory.json`. Set an absolute `storage.workspace` and a supported embedding provider, model, and dimension. Use an already authorized provider and local credential source. Ask for provider configuration if none is available. The sample binds to `127.0.0.1:21933` and uses dev authentication for a single-user disposable workspace. For private data, configure OpenViking authentication and match the client and working-memory account and user. Save configuration as UTF-8 without a BOM.
4. **Start and connect.** Using the venv interpreter, run `python -m vdra serve --home ./state --config ./ov.conf`. Check `/health` from the host OS and confirm the revision in `state/maintenance-runtime.json`. Follow the selected bundle's `integrations/<agent>/CONNECT.md`. Follow the client's hook trust and approval flow when enabling automatic capture.
5. **Verify.** Confirm MCP discovery and read a test fact from the isolated workspace. Use two sessions to check one sourced fact and one missing detail. Add aliases and real fact URIs using [project configuration](PROJECTS.md). Report the verified checks, service port, and status of the client connection.

### Import your history

On first use, import the conversations the user wants to carry forward. Confirm the authorized local sessions and date range along with the destination URL, account, and user. Verify a small batch before expanding the import.

For logs with a native adapter, such as Codex, use `openviking-server ingest` from the same venv. Point `OPENVIKING_CONFIG_FILE` to the absolute path of the import config. Enable `ingest`, set the destination `server_url`, account, and user, and enable `backfill` for the selected source. Run `list-sources` to verify the adapter and set its `paths` to the approved scope. For Codex, run `openviking-server ingest backfill --harness codex --dry-run` first. Confirm the scope and extraction cost, then remove `--dry-run`. Use `--since YYYY-MM-DD` to limit the date range. Committing imported sessions can invoke OpenViking's memory extraction model. Preserve the cursor to resume between batches. [OpenViking log ingestion guide](https://docs.openviking.ai/en/agent-integrations/09-log-ingestion)

For a client without a log adapter in the installed version, such as DSH, use the user-approved export or source records to write dated project and topic cards with message references for important decisions and results. Keep the original records available. After import, verify recall of an old fact and its source in a new session, then report the history actually covered.

### Upgrade and rollback

Stop the service and back up the workspace, configuration files, and `state`. Keep the embedding model and dimension fixed. Install the new wheel in a separate venv, reopen the workspace, and verify a known fact. Use one process per workspace. To roll back, start the prior wheel or run `openviking-server --config ov.conf --workers 1` with the same settings. Preserve vdra state for reactivation.
