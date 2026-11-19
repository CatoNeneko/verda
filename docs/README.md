# Verda 简明安装指南 / Quick Install Guide

[简体中文](#简体中文) · [English](#english)

## 简体中文

**强烈建议让 Agent 安装。** 它可以确认客户端实际运行在原生 Windows 还是 WSL、选择正确的插件和配置路径，并验证召回和采集。也可以按下方步骤手动安装。

把这段发给负责安装的 Agent：

> 根据 [Verda 部署指南](https://github.com/CatoNeneko/verda/blob/v0.1.0a2/docs/INSTALL.md) 安装 v0.1.0a2。先识别客户端、操作系统和现有 OpenViking 配置，再选择对应的 [发行包与 SHA-256 清单](https://github.com/CatoNeneko/verda/releases/tag/v0.1.0a2)。保留原有配置和记忆库，先用隔离工作区验证。列出需要用户确认的钩子或权限，并报告实际通过的 MCP、召回和采集检查。

### 手动安装

1. **下载并校验对应的 ZIP。** 从 [v0.1.0a2 预发行页](https://github.com/CatoNeneko/verda/releases/tag/v0.1.0a2) 下载 `-codex.zip`、`-dsh.zip` 或 `-mcp.zip`，同时下载 `SHA256SUMS`。用 PowerShell 的 `Get-FileHash -Algorithm SHA256 <文件>` 或 WSL 的 `sha256sum <文件>` 计算 ZIP 的哈希。核对清单中同名 ZIP 的哈希，两者一致再解压。
2. **安装并启动核心。** 在客户端运行的操作系统中进入解压目录，用 Python 3.11 或 3.12 执行 `python -m venv .venv`。用虚拟环境的 Python 执行 `python -m pip install ./vdra-0.1.0a2-py3-none-any.whl`。按 [配置步骤](INSTALL.md#中文) 准备 `ov.conf` 和独立的 `state`，再运行 `python -m vdra serve --home ./state --config ./ov.conf`。这里的 `python` 指 Windows 的 `.venv\Scripts\python.exe` 或 WSL 的 `.venv/bin/python`。
3. **接入实际使用的客户端。**

   | 客户端 | 接入步骤 |
   | --- | --- |
   | Codex | 自动召回和采集需要 Node.js 22 或更新版本，以及支持插件钩子的 Codex。执行 `codex plugin marketplace add volcengine/OpenViking` 和 `codex plugin add openviking-memory@openviking`。配置 `ovcli.conf` 后确认 `/hooks` 中的召回和采集钩子。按需检索也可直接配置 MCP。[Codex 详细步骤](../integrations/codex/CONNECT.md) |
   | DSH Desktop | 在桌面版的 **Plugins → Add plugin** 安装并启用 `@openviking/dsh-memory-plugin`，配置服务地址后打开新会话。桌面版使用自己的 profile 和随包运行时。[DSH 详细步骤](../integrations/dsh/CONNECT.md) |
   | DSH Web / CLI | 使用 Node.js 24.9 或更新版本，确认 PATH 上有 pnpm。运行 `dsh plugin --profile web add @openviking/dsh-memory-plugin@0.5.39`。使用 headless CLI 时把 `web` 换成 `headless`。[DSH 详细步骤](../integrations/dsh/CONNECT.md) |
   | 其他 MCP 客户端 | 以 Streamable HTTP 连接默认地址 `http://127.0.0.1:21933/mcp`。自动召回和采集依赖客户端的适配器。[通用 MCP 详细步骤](../integrations/mcp/CONNECT.md) |

4. **开启新会话验证。** 确认能调用 MCP 读到一条带来源的测试事实。启用自动接入的 Codex 或 DSH 再检查事实是否进入召回上下文，以及本轮是否被采集。首次使用可按 [历史导入指引](INSTALL.md#首次导入历史) 导入需要保留的对话。

## English

**We strongly recommend letting an agent install Verda.** It can identify whether your client runs natively on Windows or under WSL, choose the matching plugin and configuration paths, and verify recall and capture. Manual steps are below.

Give the deploying agent this prompt:

> Install Verda v0.1.0a2 using the [Deployment Guide](https://github.com/CatoNeneko/verda/blob/v0.1.0a2/docs/INSTALL.md). Identify the client, operating system, and existing OpenViking configuration first. Download the matching [release bundle and SHA-256 list](https://github.com/CatoNeneko/verda/releases/tag/v0.1.0a2). Preserve existing configuration and memory, and verify against an isolated workspace first. Report any hook or permission approvals needed and which MCP, recall, and capture checks passed.

### Manual installation

1. **Download and verify the matching ZIP.** Download `-codex.zip`, `-dsh.zip`, or `-mcp.zip` and `SHA256SUMS` from the [v0.1.0a2 prerelease](https://github.com/CatoNeneko/verda/releases/tag/v0.1.0a2). Compute the ZIP's hash with `Get-FileHash -Algorithm SHA256 <file>` in PowerShell or `sha256sum <file>` on WSL. Extract the ZIP only if the hash matches its row in `SHA256SUMS`.
2. **Install and start the core.** From the extracted directory on the operating system running the client, create a Python 3.11 or 3.12 environment with `python -m venv .venv`. Run `python -m pip install ./vdra-0.1.0a2-py3-none-any.whl` with the environment's Python. Follow the [configuration steps](INSTALL.md#english) to prepare `ov.conf` and an isolated `state` directory, then run `python -m vdra serve --home ./state --config ./ov.conf`. Use `.venv\Scripts\python.exe` on Windows or `.venv/bin/python` on WSL.
3. **Connect the client you run.**

   | Client | Connection |
   | --- | --- |
   | Codex | Automatic recall and capture require Node.js 22 or newer and a Codex version that supports plugin hooks. Run `codex plugin marketplace add volcengine/OpenViking` and `codex plugin add openviking-memory@openviking`. Configure `ovcli.conf` and check the recall and capture hooks in `/hooks`. Direct MCP is available for on-demand use. [Codex setup](../integrations/codex/CONNECT.md) |
   | DSH Desktop | In **Plugins → Add plugin**, install and enable `@openviking/dsh-memory-plugin`, configure the server address, and open a new chat. Desktop uses its own profile and bundled runtime. [DSH setup](../integrations/dsh/CONNECT.md) |
   | DSH Web / CLI | Use Node.js 24.9 or newer with pnpm on PATH. Run `dsh plugin --profile web add @openviking/dsh-memory-plugin@0.5.39`. For the headless CLI, replace `web` with `headless`. [DSH setup](../integrations/dsh/CONNECT.md) |
   | Other MCP clients | Connect to the default `http://127.0.0.1:21933/mcp` endpoint with Streamable HTTP. Automatic recall and capture depend on a client adapter. [Generic MCP setup](../integrations/mcp/CONNECT.md) |

4. **Verify in a new chat.** Read a sourced test fact through MCP. For automatic Codex or DSH integration, also check that the fact reaches recall context and that the new turn is captured. Follow the [history import steps](INSTALL.md#import-your-history) to bring in past conversations you want to keep.
