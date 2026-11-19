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

### 从记忆宫殿到百科网格

西塞罗在《论演说家》里讲过一个故事。诗人西摩尼德斯离开宴席后，厅堂倒塌。据说他凭记住的座位认出了每一位宾客。后来所谓记忆宫殿，就是把待记的信息放在熟悉的位置上，需要时沿着位置找回它。[《论演说家》第二卷 351–354 节](https://www.laits.utexas.edu/memoria/Cicero.html)

Verda 借用的是**用位置帮助回忆**这件事。项目是查找的入口，决策、方法、结果等主题像不同房间，事实卡和原始来源有稳定的 `viking://` 地址。任务检查点记录做到哪里、哪些问题未决。对话压缩后，Agent 可以从这些位置恢复项目和任务，而把原始记录留作查证。

**百科式链接把不同主题串起来。** 例如，结果卡可以链接到采用的方法，再由带时间的取代关系指明哪一版仍有效。这样，同一条方法或证据可以被多条结论引用，修改状态也有可追溯的路径。Verda 自动召回只沿已登记的关系扩展一跳，复核访问权限、两端内容及关系证据，并把适用的片段带进当前任务。

**跨会话共享，按当前主题召回。** 同一账户的会话可以复用记忆，但当前请求和会话绑定的项目决定召回范围。“继续”优先恢复当前任务的检查点。能明确识别当前项目时，自动召回会过滤已识别为其他项目的材料，关联也要重新通过相关性和证据检查。例如，实验会话继续核对数据划分时，另一个项目的客户端发布记录会留在各自的项目里。没有足够相关的内容时，Verda 可以留空。

### Verda 如何分配召回注意力

这里的注意力指**哪些证据拿到上下文位置，以及占用多少预算**。Verda 按以下顺序决定。

1. **先定位要解决的问题。** “继续”优先取有证据的任务快照。查询项目事实时，先看当前项目和匹配段落，再用检索补足缺项。当前问题按有效状态筛选，带日期的历史问题可以检查那一天适用的证据。
2. **按时效处理候选。** 重要或受保护的记忆保留权重。默认情况下，被识别为临时状态的事件 1 天后过期，普通事件按 30 天半衰期降权、90 天后逻辑归档。这些时间可以配置。显式查历史时可找回归档或过期条目，并带上历史标记。原始记录仍可查证。
3. **相关证据优先。** 候选通过来源和事实匹配检查后，排序的基础是 **35% 原生检索分 + 65% 证据匹配分**，再考虑项目、来源类型和内容重复度。分项查询优先选能覆盖此前缺项的材料，最后在 **1500 token、最多 5 项**内只送入相关片段。[召回排序](src/vdra/recall_policy.py) · [保留策略](src/vdra/memory_policy.py)

### 示例

在一个合成测试项目中，Agent 同时核对**数据划分**和**预注册的传感器协议**。Verda 找到记录训练、验证、测试集按 **70% / 15% / 15%** 划分的段落，并报告 `fact_coverage.missing: [2]`。这让 Agent 能回答第一项，同时明确第二项仍需查证。

### CPU 本地向量检索

**检索可以完全在本机 CPU 上运行，无需额外购买外部搜索或 embedding API。** 本机使用 OpenViking 内置的本地向量数据库（`storage.vectordb.backend=local`）。2026 年 11 月 19 日测得活动向量集合占 **29.1 MiB**，其中向量索引约 **10.0 MiB**。这是向量集合的体积，模型权重和原始对话另计。可通过 [OpenViking 本地嵌入配置](https://docs.openviking.ai/en/guides/01-configuration#local-provider-local) 接入 CPU 模型。

轻量部署可选 **BGE-small-zh-v1.5 F16 GGUF**，本机权重文件实测 **45.7 MiB**，输出 512 维向量。本机长对话部署使用 **BGE-M3 FP32**，以两条 CPU 推理线程生成 1024 维向量。历史提炼和可选的模型总结沿用配置的模型服务。

#### 本机实测

Ryzen 7 9800X3D，CPU 两线程。离线对照使用 457 个脱敏来源和 2,268 个段落，四种候选方案都将稠密向量检索与 BM25 关键词检索融合。

| 嵌入模型 | FP32 权重 | 完整证据 Hit@5 | 热查询均值 / p95 | 查询进程内存 |
| --- | ---: | ---: | ---: | ---: |
| [BGE-base-zh-v1.5](https://huggingface.co/BAAI/bge-base-zh-v1.5) | 390 MiB | 69.0%（29/42） | 0.224 / 0.455 秒 | 0.77 GiB |
| [BGE-M3](https://huggingface.co/BAAI/bge-m3) | 2.12 GiB | **81.0%（34/42）** | **0.431 / 1.028 秒** | 1.84 GiB |
| 每次 base + M3 | 2.50 GiB | 76.2%（32/42） | 0.548 / 1.289 秒 | 2.23 GiB |
| base 默认，按需 M3 | 2.50 GiB | 73.8%（31/42） | 0.440 / 1.306 秒 | 2.22 GiB |

Hit@5 按 42 道正例计分，要求在前 5 项内找回完整的指定证据。计时使用每组 36 次热请求，包含查询编码、检索、融合和选段。该基准衡量证据召回和本机查询用时，线上 HTTP 耗时另计。

#### 旧版实测和迁移过程

早期版本实际使用 **BGE-small-zh-v1.5 F16 GGUF** 的 512 维向量，保留的旧向量集合为 **10.8 MiB**。换模型前，用旧版做了 10 条真实召回验收，**5 条找回预期事实，5 条应空的请求正确留空**。这套用例用于验收旧配置的实际行为。上表的 42 道正例则用于同题比较后续候选。

2026 年 10 月 29 日，本机先比较了 base、M3、每次双模型和按需双模型，再选定 M3。它在这批题中找回的完整证据最多，并且只需驻留一个嵌入模型。在 6 道自然长请求里，base 命中 3 道，M3 命中 5 道。切换时用 M3 建了独立的 **1024 维向量集合**，迁入 **2,094 条**记录并核对元数据和权限，保留旧集合用于回滚。两版都使用 OpenViking 的本地向量数据库后端。

### 首次使用与部署

**首次使用，请先导入想延续的历史对话。** 新工作区从空白开始。导入获准的本地历史后，旧对话中的事实和决定才会进入可召回的记忆库。

Codex、DeepSeek Harness 和通用 MCP 各有接入包，共用同一个核心。原生 Windows x64 和 WSL Linux 已通过隔离安装、召回和回滚测试。把 [Agent 部署指南](docs/INSTALL.md#中文) 发给负责部署的 Agent，选包、导入历史和验证的步骤都在里面。

如果 Verda 用起来顺手，帮你解决了长对话里的记忆问题，欢迎给项目点个 ⭐ Star。

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

### From the memory palace to a Wikipedia-style network

Cicero tells a story about the poet Simonides. After Simonides stepped outside a banquet, the hall collapsed. According to the account, he could identify the guests from their places at the table. The memory palace applies the same idea by giving facts familiar places to find them again. [Cicero, *On the Orator*, II.351–354](https://www.laits.utexas.edu/memoria/Cicero.html)

Verda borrows **location as a retrieval cue**. A project is an entry point. Decisions, methods, and results occupy topic rooms. Fact cards and original sources have stable `viking://` addresses. Task checkpoints record progress and open questions. After compaction, the agent can resume from that structure and consult the original record when it needs to verify a detail.

**Wikipedia-style links connect those topics.** A result can point to the method it used, while a dated replacement link records which version applies. Several conclusions can cite the same source, and changes remain traceable. Automatic recall follows registered links at most one hop. It verifies access, both entries' contents, and the evidence for the relationship before using a relevant passage.

**Shared across sessions, scoped to the current topic.** Sessions under the same account can reuse memory, with recall guided by the current request and the session's project binding. A continuation request favors the current task checkpoint. When the current project is confidently identified, automatic recall filters material identified as belonging to other projects, and linked passages have to pass relevance and evidence checks. For example, a session verifying an experiment's data split stays focused while a separate project's client release notes remain available in their own project. If nothing relevant qualifies, Verda can return empty.

### How Verda allocates recall attention

Here, attention means **which evidence gets context space and how much**. Verda allocates it in the following order.

1. **Resolve the task first.** A continuation request favors an evidence-backed work snapshot. A project question starts with matching passages from that project and retrieves more evidence to fill gaps. Current questions use current evidence, and dated historical questions can look for what applied at the requested date.
2. **Apply time and retention rules.** Important or protected memories retain their weight. By default, events recognized as temporary expire after one day. Ordinary events decay with a 30-day half-life and are logically archived after 90 days. These times are configurable. Explicit historical lookup can recover those items with a historical label. The source records stay available for verification.
3. **Rank for evidence and coverage.** After scope and evidence checks, the base score is **35% native retrieval score plus 65% evidence-match score**, with adjustments for project, source, and repetition. Multi-part questions prioritize passages that cover an uncovered item. The final context includes relevant excerpts within **1,500 tokens and five entries**. [Ranking policy](src/vdra/recall_policy.py) · [Retention policy](src/vdra/memory_policy.py)

### Example

In a synthetic test project, the agent asks for the **data split** and the **preregistered sensor protocol**. Verda retrieves the passage documenting **70% / 15% / 15%** for train, validation, and test. It reports `fact_coverage.missing: [2]`, so the agent can answer the first item and flag the second for verification.

### Local vector retrieval on CPU

**Memory search can run entirely on the local CPU, with no paid external search or embedding API required.** The reference machine uses OpenViking's built-in local vector database (`storage.vectordb.backend=local`). On November 19, 2026, the active vector collection occupied **29.1 MiB**, including about **10.0 MiB** of vector index files. Model weights and source transcripts are stored separately. See the [OpenViking local embedding settings](https://docs.openviking.ai/en/guides/01-configuration#local-provider-local).

For a lightweight setup, **BGE-small-zh-v1.5 F16 GGUF** has a measured weight file of **45.7 MiB** and produces 512-dimensional vectors. The author's long-session deployment uses **BGE-M3 FP32**, which produces 1,024-dimensional vectors and runs with two CPU inference threads. History extraction and optional summaries follow the configured model provider.

#### Local benchmark

AMD Ryzen 7 9800X3D, two CPU threads. The offline comparison used 457 redacted sources and 2,268 passages. All four strategies fused dense vector retrieval with BM25 keyword search.

| Embedding model | FP32 weights | Complete-evidence Hit@5 | Warm query mean / p95 | Query process RSS |
| --- | ---: | ---: | ---: | ---: |
| [BGE-base-zh-v1.5](https://huggingface.co/BAAI/bge-base-zh-v1.5) | 390 MiB | 69.0% (29/42) | 0.224 / 0.455 s | 0.77 GiB |
| [BGE-M3](https://huggingface.co/BAAI/bge-m3) | 2.12 GiB | **81.0% (34/42)** | **0.431 / 1.028 s** | 1.84 GiB |
| Always base + M3 | 2.50 GiB | 76.2% (32/42) | 0.548 / 1.289 s | 2.23 GiB |
| Base with selective M3 routing | 2.50 GiB | 73.8% (31/42) | 0.440 / 1.306 s | 2.22 GiB |

Hit@5 measures whether complete reference evidence appeared among the top five results on 42 positive questions. Each latency run used 36 warm requests and included query encoding, retrieval, fusion, and passage selection. The benchmark measures evidence retrieval and local query time. Production HTTP latency is measured separately.

#### Earlier deployment and migration

The earlier deployment used **BGE-small-zh-v1.5 F16 GGUF** with 512-dimensional vectors. Its preserved vector collection occupies **10.8 MiB**. Before switching, it passed 10 live recall checks. **Five recovered the expected fact, and five correctly returned empty.** Those checks verified the behavior of the earlier deployment. The 42 positive questions above compare the later candidates on the same questions.

On October 29, 2026, the author compared base, M3, always running both models, and selective M3 routing before choosing M3. It recovered the most complete evidence on this test set and required just one resident embedding model. On six naturally phrased long requests, base covered three and M3 covered five. The migration built a separate **1,024-dimensional collection**, moved **2,094 records** with metadata and access checks, and kept the old collection for rollback. Both versions use OpenViking's local vector database backend.

### First use and deployment

**Import the conversation history you want to carry forward.** A new workspace starts empty. Import approved local history to make facts and decisions from earlier sessions available for recall.

Codex, DeepSeek Harness, and generic MCP bundles share one core. Isolated install, recall, and rollback were tested on native Windows x64 and WSL Linux. Give the [Agent Deployment Guide](docs/INSTALL.md#english) to the deploying agent for bundle selection, history import, and verification.

If Verda helps you keep track of long conversations and solves a real problem for you, a ⭐ star is appreciated.

---

[Alpha release](https://github.com/CatoNeneko/verda/releases/tag/v0.1.0a1) · [AGPL v3](LICENSE)
