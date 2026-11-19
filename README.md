# Verda

[简体中文](#中文) · [English](#english)

## 中文

**Verda 特别适合管理超长对话。** 它是基于 OpenViking 的 Agent 长期记忆系统，面向需要反复核对事实、跨会话延续的科研和开发任务。作者已用这一版本管理大量长对话，在日常工作中实际用起来很顺手。

当一个会话经历多次压缩、实验阶段变化或任务交接，Verda 可以从有来源的项目事实、检查点和未决问题接续。它把**正确性**和**完整性**落实到召回和覆盖检查上。召回的事实有出处，能识别的多项要求逐项核对，缺项明确标记。这样即使记忆拉得很长，Agent 也能分清哪些已经确认、哪些已被取代、哪些还需查证。

### 在 OpenViking 之上增加了什么

OpenViking 提供存储、向量检索、会话和 MCP 接口。Verda 沿用这些能力，在自动召回中加入以下机制。

| 改进 | Verda 的做法 |
| --- | --- |
| **长期记忆与超长会话续接** | 项目主卡保存稳定事实和当前决定，主题卡保留方法、结果和来源。项目绑定、任务目标、带证据的检查点与未决队列帮助 Agent 跨压缩和跨会话接续。 |
| **维基百科式事实链接** | 条目用“支持”“取代”“依赖”等有方向的关系相连。链接可记录证据、日期和有效期。自动召回从相关条目最多沿一跳扩展，并复核项目范围、访问权限、两端内容指纹和关系证据。 |
| **强事实依赖** | 召回按项目、时间、实验阶段和请求中的数值约束筛选，并提取原文中的相关段落。查询历史日期时，按已有证据判断该日是否适用，或者标注日期有效性待核实。 |
| **幻觉抑制** | 将当前决定与已取代方案区分开。弱相关、过期和跨项目的候选会被筛选，缺少可靠材料时允许空召回，减少旧结论混入当前回答的机会。 |
| **防细节编造与漏项** | 对识别出的分项事实查询，按最终返回的证据片段逐项检查覆盖情况。缺项列在 `fact_coverage.missing`，正文预算允许时提示核对原始来源。Agent 可以明确报告哪些实验参数、数值或流程细节尚未查证。 |
| **记忆老化与主卡治理** | 保护重要事实，让临时状态过期，将普通历史降权或归档。项目主卡超出体积预算时，保留待整理的内容并拦住超限更新。 |
| **召回开销可控** | 语义召回最多 **1500 token、5 项**，上下文组装无需额外调用语言模型。向量检索和可选的会话总结沿用 OpenViking 的提供者配置。 |

### 示例

在一个合成测试项目中，Agent 同时核对**数据划分**和**预注册的传感器协议**。Verda 找到记录训练、验证、测试集按 **70% / 15% / 15%** 划分的段落，并报告 `fact_coverage.missing: [2]`。这让 Agent 能回答第一项，同时明确第二项仍需查证。

### 首次使用与部署

**首次使用，请先导入想延续的历史对话。** 新工作区从空白开始。导入获准的本地历史后，旧对话中的事实和决定才会进入可召回的记忆库。

Codex、DeepSeek Harness 和通用 MCP 各有接入包，共用同一个核心。原生 Windows x64 和 WSL Linux 已通过隔离安装、召回和回滚测试。把 [Agent 部署指南](docs/INSTALL.md#中文) 发给负责部署的 Agent，选包、导入历史和验证的步骤都在里面。

## English

**Verda is designed for very long conversations.** It is a long-term memory layer for agents built on OpenViking, aimed at research and development that involves repeated fact-checking and work across sessions. The author has used this version to manage many long conversations and found it useful in everyday work.

When a session goes through repeated compaction, an experiment moves to the next stage, or work changes hands, Verda can resume from sourced project facts, checkpoints, and open questions. It makes **correctness and completeness checkable**. Retrieved facts have sources, and recognized multi-part questions are checked item by item for missing evidence. The agent can distinguish confirmed decisions, superseded plans, and details that still need verification.

### What Verda adds to OpenViking

OpenViking supplies storage, vector retrieval, sessions, and MCP. Verda builds on those capabilities and adds the following mechanisms to automatic recall.

| Improvement | What Verda adds |
| --- | --- |
| **Long-term memory and continuity for very long sessions** | Project cards hold stable facts and current decisions. Topic cards retain methods, results, and sources. Project bindings, goals, evidence-backed checkpoints, and open issues make it easier to resume after compaction or a session change. |
| **Wikipedia-style fact links** | Entries can have typed, directed links such as `supports`, `supersedes`, and `depends_on`, with evidence, dates, and validity windows. Recall follows at most one hop from a relevant entry and verifies scope, access, content hashes for both entries, and evidence for the link. |
| **Evidence-first recall** | Project, date, experiment stage, and numeric constraints guide candidate selection. Verda extracts relevant passages and uses available evidence to check whether a source applied on a requested date, marking that applicability as unverified when needed. |
| **Hallucination suppression** | Current decisions and superseded plans have different eligibility. Relevance, time, and project checks filter candidates. Recall can return empty when reliable evidence is unavailable, reducing the chance that stale claims enter the answer context. |
| **Protection against fabricated details and omissions** | For recognized multi-part fact requests, Verda checks coverage against the passages it actually returns. It reports uncovered items in `fact_coverage.missing` and prompts a source check when the context budget allows. The agent can flag unverified parameters, numbers, and protocol details. |
| **Retention and project-card size** | Important facts are protected, temporary state expires, and routine history can be downranked or archived. Oversized project-card updates are preserved for review and blocked from growing the main card. |
| **Bounded recall cost** | Semantic recall is capped at **1,500 tokens and five entries**. Context assembly requires no extra language-model call. Embedding and optional session summarization use the providers configured in OpenViking. |

### Example

In a synthetic test project, the agent asks for the **data split** and the **preregistered sensor protocol**. Verda retrieves the passage documenting **70% / 15% / 15%** for train, validation, and test. It reports `fact_coverage.missing: [2]`, so the agent can answer the first item and flag the second for verification.

### First use and deployment

**Import the conversation history you want to carry forward.** A new workspace starts empty. Import approved local history to make facts and decisions from earlier sessions available for recall.

Codex, DeepSeek Harness, and generic MCP bundles share one core. Isolated install, recall, and rollback were tested on native Windows x64 and WSL Linux. Give the [Agent Deployment Guide](docs/INSTALL.md#english) to the deploying agent for bundle selection, history import, and verification.

---

[Alpha release](https://github.com/CatoNeneko/verda/releases/tag/v0.1.0a1) · [AGPL v3](LICENSE)
