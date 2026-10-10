# 联邦 Text-to-SQL 项目：更新版简历描述

> 事实来源包括 `VERIFIED_BY_CODE`、`VERIFIED_BY_HISTORY` 和 `CONFIRMED_BY_USER`。  
> 用户已确认 Git 作者 `Klein` 为本人；由于“本人完成/非主要完成模块”仍未填写，投递前还需确认具体个人职责边界。

## 1. 项目一句话简介

面向电商多源数据的自然语言查询与分析系统，基于 LangGraph 编排 Schema RAG、SQL 生成/审查、联邦执行和结果分析，并通过 FastAPI、Redis Streams 与 PostgreSQL 构建可恢复的 Agent 服务。

更短版本：

基于 LangGraph、FastAPI 与 DuckDB 构建的联邦 Text-to-SQL 系统，覆盖 3,000+ 表 Schema 检索、SQL 安全审查、跨库执行和任务恢复。

## 2. 核心技术栈

**Python、FastAPI、LangGraph、LangChain、DeepSeek（OpenAI-compatible API）、DashScope Embedding、SQLGlot、DuckDB、SQLAlchemy、MySQL、PostgreSQL、ClickHouse、Redis Streams、SQLite、React、Docker Compose、OpenTelemetry**

边界说明：

- 代码已实现 MySQL、PostgreSQL、ClickHouse 的连接/挂载能力；本人确认真实联调过远程数据库和跨库查询，但具体使用了哪几类数据库及其版本仍待补充。
- Chroma 是可选兼容后端，不列入默认核心技术栈。
- 不写 MongoDB；当前 `db_mongo` 逻辑别名并不代表 MongoDB 接入。
- 不写 Spark、Flink、Kafka、Hadoop；项目的优势是 Agent 工程与多源联邦查询，不是分布式大数据计算。

## 3. 项目母版 Bullet（8 条）

1. 设计 LangGraph 多阶段 Text-to-SQL 状态图，串联规则优先的意图识别、条件路由、语义记忆、实时 Schema、SQL 生成/审查/执行与结果分析，并将校验或执行错误回流后重新生成。

2. 构建面向 3,000+ 表的 Schema RAG，将实时元数据与版本化业务目录融合，以 DashScope 向量、关键词和元数据混合排序动态召回相关表，使 Token 消耗降低约 75%。

3. 建立角色化 Prompt 上下文管理，将会话检查点、近期轮次、用户偏好、语义记忆、Schema 和查询结果按 Agent 分包并限制字符数与结果行数，避免历史数据和无关结构无界进入模型上下文。

4. 实现双候选 SQL 与确定性 Reviewer，叠加单条 `SELECT/WITH`、50 行上限、MindsDB AST、SQLGlot 表列/JOIN/聚合检查、RBAC/租户策略和 `EXPLAIN` 风险审查；本人确认测试中 Text-to-SQL 准确率达到 98%+。

5. 构建 DuckDB 联邦查询层，以稳定别名统一远程数据库与本地 SQLite 演示源的 Schema 获取、执行计划预检、跨源 SQL 执行和 JSON 结果转换，并完成真实跨库查询联调。

6. 将同步 LangGraph 调用封装为 FastAPI 请求运行时，提供有界队列、超时、协作式暂停和 SSE 状态事件；通过 Redis Streams 实现 request ID 幂等入队、独立 Worker、挂起任务认领和真实任务恢复。

7. 设计用户级 Agent Memory 与观测链路，持久化偏好、会话检查点和已确认语义记忆，对成功 SQL 常量进行脱敏复用，并记录请求、Agent、Token、延迟、重试、审计和 OpenTelemetry Trace。

8. 使用 Docker Compose 完整运行 API、Worker、Redis 与 PostgreSQL 服务；本人确认约 15,000 条订单可在 3 秒内处理，并为访问控制、任务恢复、Memory、Schema 检索和 SQL Reviewer 等模块建立 71 项后端测试。

指标状态：第 2、4、8 条中的规模、Token、准确率和查询性能均为 `CONFIRMED_BY_USER`；事实保留，但测量口径尚未补齐。

## 4. 最推荐的 4 条投递 Bullet

- 设计 LangGraph 多阶段 Text-to-SQL 工作流，串联意图识别、语义记忆、实时 Schema 检索、双候选 SQL、校验/评审、联邦执行与结果分析，并通过条件路由实现澄清、拒绝和失败反馈重试。
- 构建面向 3,000+ 表的 Schema RAG，融合实时元数据与版本化业务目录，通过 DashScope 向量、关键词和元数据混合召回相关表，使 Prompt Token 消耗降低约 75%。
- 实现双候选 SQL 与确定性 Reviewer，结合 AST、Schema 关系、RBAC/租户策略和 `EXPLAIN` 审查拦截危险或结构错误 SQL，测试中 Text-to-SQL 准确率达到 98%+。
- 基于 DuckDB 完成远程多库联邦查询联调，测试中约 15,000 条订单数据处理耗时控制在 3 秒内；通过 Redis Streams 实现幂等入队、挂起任务认领与异常恢复。

指标说明：75%、98%+ 与 15,000 条 / 3 秒均由本人确认，已按用户要求进入投递版。98%+ 仅表述为“测试准确率”；3 秒仅表述为订单数据处理性能，不扩展为包含 LLM 推理的端到端响应。测试集、准确率定义、执行环境与缓存状态仍需在面试前补齐。

## 5. 极限压缩版（3 条）

- 基于 LangGraph 编排意图、Schema RAG、双候选 SQL、审查、联邦执行与错误回流，测试中 Text-to-SQL 准确率达到 98%+。
- 面向 3,000+ 表构建 DashScope 向量、关键词与元数据混合召回，仅注入相关 Schema，使 Prompt Token 消耗降低约 75%。
- 以 DuckDB、FastAPI 和 Redis Streams 构建可恢复的多源查询服务，完成真实跨库联调，测试中约 15,000 条订单数据处理耗时控制在 3 秒内。

## 6. 每条母版 Bullet 的面试追问

| 母版 Bullet | 面试官可能追问 | 需要讲清的细节 | 当前风险 |
| --- | --- | --- | --- |
| 1. LangGraph 工作流 | 为什么使用 LangGraph？哪些节点调用 LLM？双候选和重试如何终止？ | `AgentState`、规则优先/LLM 兜底、Supervisor、`MAX_RETRIES=2`、各节点职责 | “多智能体”容易被理解成自治/并行 Agent，建议坚持“多阶段工作流” |
| 2. Schema RAG | 3,000+ 表来自几个库？如何切片、召回和缓存？75% 如何测？ | table-scoped Document、catalog hash、混合排序权重、4～6 表召回、词法降级 | 必须补 Token 基线、模型、测试问题数和统计口径 |
| 3. Context Budget | 各 Agent 分别拿到哪些上下文？为什么按字符而非 Token？ | checkpoint、recent turns、semantic memory、Schema/result budgets、冷/热数据边界 | 不能把字符预算等同于精确 Token 控制 |
| 4. SQL Reviewer | 98% 是什么准确率？两个 parser 如何分工？错误 SQL 如何回流？ | MindsDB AST、SQLGlot、RBAC、租户注入、JOIN/聚合规则、EXPLAIN | 98% 口径不清时追问成本极高 |
| 5. DuckDB 联邦 | 实际连接了哪些数据库？跨库 JOIN 在哪里执行？给一个真实 SQL | DuckDB extension、`ATTACH READ_ONLY`、稳定别名、远端 URI、Schema/EXPLAIN | 需准备真实跨库案例、版本、数据量和部署拓扑；不能提 MongoDB |
| 6. Redis Streams | 如何幂等？Worker 崩溃后怎样恢复？消息是 exactly-once 吗？ | `HSETNX`、consumer group、`XAUTOCLAIM`、TTL、有界重试、202 轮询 | 是 at-least-once 风格，不要声称 exactly-once；暂停为协作式 |
| 7. Memory / Observability | 哪些记忆自动写入？如何隔离用户？记录哪些指标？ | 会话 checkpoint、confirmed semantic memory、SQL 常量脱敏、Token/延迟/审计/Trace | 不要声称 Memory 能自动保证事实正确或完全防止 Prompt Injection |
| 8. 部署与性能 | 3 秒是否包含 LLM？什么硬件和缓存？71 项测试覆盖什么？ | Compose 服务拓扑、状态存储、测试分类、warm/cold cache、测量次数 | 15,000 行纯数据库查询并不天然有竞争力，口径不完整时可能反向减分 |

## 7. 适合放入简历的指标

### 当前即可使用

- **3,000+ 表 Schema 规模**：与 Schema RAG 的技术动作直接相关，能说明为什么需要动态检索；面试前仍要准备表数量的统计方式和数据源构成。
- **Prompt Token 消耗降低约 75%**：与 Schema RAG 直接构成“规模—方案—收益”闭环；需在面试前补充基线、模型、问题集和 Token 统计口径。
- **Text-to-SQL 准确率 98%+**：本人确认的测试结果，可写入项目成果；必须明确这是测试准确率，并准备测试集规模与准确率定义。
- **约 15,000 条订单 / 3 秒**：本人确认的查询处理性能，可用于后端/数据处理版本；不得表述为包含 LLM 推理的端到端响应，除非后续确认。
- **71 项后端测试**：本轮可复现验证通过，口径清晰；适合工程化、Python 后端岗位，但优先级低于核心 Agent/RAG Bullet。

### 已写入但面试前需补齐口径

- **Token 消耗降低约 75%**：与 Schema 召回和上下文预算高度相关，是最值得补齐的效果指标。至少准备：完整 Schema 注入基线、同一问题集、相同模型、统计 input token 还是 total token、样本均值或中位数。
- **Text-to-SQL 准确率 98%+**：补充测试集规模、题型、判定方式和是否包含执行结果；若是人工审核，应说明审核标准。

## 8. 已进入投递版但追问风险较高的指标

- **Text-to-SQL 准确率 98%+**：真实性已由本人确认，但“准确率”可能指 SQL 字符串匹配、可执行率、结果正确率或人工认可率；不同定义差异很大，面试前必须统一口径。
- **约 15,000 条订单 / 3 秒**：缺少是否包含 LLM、数据库数量、硬件、缓存和统计分位数；当前只写“订单数据处理”，不写“端到端响应”或“高性能”。
- **Token 降低约 75%**：已进入投递版，但需要能够说明全量 Schema 基线、所用模型、问题集和 input/total Token 统计方式。

### 指标口径补齐后的替换 Bullet

Token 口径补齐后，可将最终第 2 条替换为：

- 构建面向 3,000+ 表的 Schema RAG，融合实时元数据与版本化业务目录，通过向量、关键词和元数据混合排序动态召回相关表，使同模型、同问题集下的 Prompt Token 消耗降低约 75%。

准确率口径补齐后，可将母版第 4 条压缩为：

- 实现双候选 SQL 与确定性 Reviewer，结合 AST、Schema 关系、RBAC/租户策略和 `EXPLAIN` 审查，在 **[测试集规模]** 个问题上取得 98%+ 的 **[执行结果正确率/人工审核准确率]**。

性能口径补齐且确有区分度时，可使用：

- 在 **[执行环境]**、**[缓存状态]** 下完成 **[数据库数量]** 个数据源约 15,000 条订单的 **[端到端/纯 SQL]** 查询，**[P50/P95]** 延迟控制在 3 秒内。
