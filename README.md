# Verda

[简体中文](#中文) · [English](#english)

## 中文

Verda 是基于 OpenViking 的 Agent 长期记忆系统，适合需要跨会话延续的科研和开发任务。它帮助 Agent 找回当前项目的决定、证据和下一步，并标出缺少依据的问题。

### 特点

- **事实可查证。** 项目卡保存当前决定，主题卡和历史记录保留来源。遇到一次问多项事实的请求，Verda 会分别检查证据并标出缺项。
- **长任务可续接。** 工作记忆保存任务目标、检查点和未决问题，方便在长对话压缩或换会话后继续。
- **自动召回有预算。** Verda 的语义上下文最多 1500 token、5 项，允许空结果。召回组装无需额外调用语言模型。向量检索和可选的会话总结使用 OpenViking 的提供者配置。
- **按 Agent 分发。** Codex、DeepSeek Harness 和通用 MCP 各有接入包，共用一个核心。原生 Windows x64 和 WSL Linux 已通过隔离安装、召回和回滚测试。

### 部署

把 [Agent 部署指南](docs/INSTALL.md#中文) 发给负责部署的 Agent，让它选用当前系统和客户端对应的包。

## English

Verda is a long-term memory layer for agents, built on OpenViking. It helps research and development agents pick up work across sessions with the project's decisions, supporting evidence, and next steps in view.

### What Verda does well

- **Evidence-backed recall.** Project cards hold current decisions. Topic cards and history retain their sources. When a request asks for several facts, Verda tracks which details have evidence and flags gaps.
- **Continuity across long tasks.** Working memory keeps goals, checkpoints, and open questions available after compaction or a session change.
- **Bounded automatic context.** Verda's semantic context is limited to 1,500 tokens and five entries, and can return empty. Context assembly requires no extra language-model call. Embedding and optional session summarization follow the OpenViking provider settings.
- **Agent-specific packages.** Codex, DeepSeek Harness, and generic MCP bundles share the same core. Clean install, recall, and rollback were tested on native Windows x64 and WSL Linux.

### Deploy

Give the [Agent Deployment Guide](docs/INSTALL.md#english) to the deploying agent. It can choose the bundle for the current client and operating system.

---

[Alpha release](https://github.com/CatoNeneko/verda/releases/tag/v0.1.0a1) · [AGPL v3](LICENSE)
