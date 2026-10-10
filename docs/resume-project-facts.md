# 联邦 Text-to-SQL 项目：简历事实分析

> 分析基线：`main` 分支，HEAD `90f5a14`，分析日期 2026-09-15。  
> 本文记录仓库、Git 历史、本轮验证及用户本人确认的事实。用户已确认 Git 作者 `Klein` 为本人；具体模块分工仍未填写。

## 1. 项目事实摘要

### 1.1 项目解决的核心问题

该项目把自然语言数据问题转换为可安全执行的 SQL，并将查询结果组织为分析回答。它重点处理四类工程问题：

1. 自然语言意图不明确或包含写操作时，如何在生成 SQL 前完成分流、澄清或拒绝；
2. 数据库表结构较多时，如何从实时 Schema 中筛出与问题相关的上下文，而不是把完整 Schema 全量塞入 Prompt；
3. LLM 生成的 SQL 可能包含错误表列、错误关联、越权访问或高风险执行计划时，如何在执行前分层拦截；
4. 单库、跨源查询和耗时 Agent 调用如何通过统一 API、任务运行时、记忆与观测机制落地。

### 1.2 主要用户与业务场景

- **代码直接支持的场景**：通过 React 工作台查询电商订单、用户、商品、行为日志和特征数据；可查询单库 SQLite，也可通过 DuckDB 对多个逻辑数据源做联邦分析。
- **合理推断的用户**：需要用自然语言探索业务数据的运营、分析或非 SQL 熟练用户。仓库没有用户调研、客户或上线材料，因此该用户画像不能当作已验证业务成果。
- **不能直接声称的场景**：仓库没有国家、币种、跨境平台、关务、国际物流等模型或接口，当前只能证明“电商数据分析”，不能证明“跨境电商系统”。

### 1.3 样例数据事实

本轮直接读取仓库内 SQLite 文件得到：

| 数据文件 | 表 | 当前行数 | 主要内容 |
| --- | --- | ---: | --- |
| `ecommerce_test.db` | `orders`、`user_behavior_log` | 15,000、100 | 单库订单与行为演示 |
| `mysql_business.db` | `orders`、`products`、`users` | 15,000、2,000、5,000 | 业务库本地替身 |
| `mongo_logs.db` | `user_behaviors` | 30,000 | 日志源本地替身，实际是 SQLite 文件 |
| `clickhouse_features.db` | `product_features`、`user_features` | 2,000、5,000 | 特征源本地替身 |

这些数字可以描述仓库样例数据，但不能外推为生产数据规模或“大数据处理规模”。

### 1.4 本人确认的真实运行与指标

以下事实由用户本人在 2026-09-15 明确确认，即使仓库暂缺完整 benchmark，也保留为 `CONFIRMED_BY_USER`：

- 已真实联调远程数据库并执行跨数据库查询；
- Docker Compose 完整环境真实运行过；
- Redis Streams 真实用于任务恢复；
- Schema 规模为 3,000+ 表；
- Token 消耗降低约 75%；
- Text-to-SQL 准确率为 98%+；
- 约 15,000 条订单可在 3 秒内处理。

当前缺失项：Token 基线、测试方式、模型与统计口径；SQL 测试集规模、准确率定义与判定方式；3 秒指标是否包含 LLM、数据库数量、执行环境及缓存状态。以上不改变事实状态，但均标记为“面试前需要补充测量口径”。

### 1.5 当前核心技术栈

| 层级 | 已实际进入主链路的技术 | 使用方式与边界 |
| --- | --- | --- |
| 后端 | Python 3.11、FastAPI、Pydantic、Uvicorn | 查询、数据源、记忆、暂停、结果轮询、观测、审计和评估 API |
| Agent | LangGraph、LangChain Core、`langchain-openai` | TypedDict 状态图、条件路由、消息与模型调用封装 |
| 模型 | 默认 `deepseek-v4-flash`，OpenAI-compatible API | 意图兜底分类、双候选 SQL、候选选择、结果分析；实际供应商取决于环境配置 |
| Embedding / RAG | DashScope `text-embedding-v4`、关键词与元数据排序 | Schema 和语义记忆检索；embedding 失败时词法降级 |
| SQL | SQLGlot、`mindsdb-sql-parser` | 权限策略、表列与关联审查、AST 解析、只读边界和执行计划检查 |
| 数据访问 | DuckDB、SQLite、SQLAlchemy | SQLite 单库、DuckDB 联邦层、可选 MySQL/PostgreSQL/ClickHouse 直连 |
| 任务与状态 | Redis Streams、PostgreSQL / SQLite | 可选持久任务队列、暂停和 Schema 缓存共享、运行时状态存储 |
| 可观测性 | OpenTelemetry、SQLite / PostgreSQL 指标存储 | 请求、Agent、LLM、Token、延迟、审计和质量反馈 |
| 前端 | React 19、Vite 6、Tailwind CSS 4、Lucide React | 数据问答工作台、异步结果轮询、暂停、离线演示与指标页 |
| 工程化 | Docker、Docker Compose、GitHub Actions、unittest、Playwright | 多阶段镜像、API/Worker/Redis/PostgreSQL 编排与自动化验证 |

以下依赖不能作为默认核心技术直接写入：

- Chroma：代码保留可选后端，但默认是 `dashscope_memory`；只有本人确认实际使用过，才适合突出。
- MySQL/PostgreSQL/ClickHouse：连接器和 DuckDB 远端挂载路径已实现；当前提交配置默认禁用直连并回退到 SQLite 演示库。用户已确认真实联调过远程数据库，但尚未说明具体包含哪几类数据库。
- MongoDB：当前没有 MongoDB 驱动接入；`db_mongo` 只是历史逻辑别名，对应 PostgreSQL 类型或本地 SQLite 日志库。

### 1.6 主要模块及职责

| 模块 | 真实职责 | 简历价值 |
| --- | --- | --- |
| `agents/workflow.py` | LangGraph 状态、意图路由、Schema、检索、双 SQL 候选、校验、评审、执行、分析和错误回流 | 极高 |
| `vector_store/schema_indexer.py` | 实时 Schema 切片、混合排序、缓存、权限前置过滤和 embedding/Chroma 降级 | 极高 |
| `core_engine/sql_reviewer.py` | 表列、JOIN、聚合、租户条件及 EXPLAIN 风险的确定性审查 | 极高 |
| `core_engine/federation_engine.py` | DuckDB 挂载异构源、本地演示回退、Schema 获取、EXPLAIN 和查询执行 | 高 |
| `core_engine/durable_tasks.py`、`task_worker.py` | Redis Streams 入队、任务状态、Worker 消费、挂起任务认领和有界重试 | 极高（Python 后端） |
| `core_engine/request_runtime.py` | 有界进程内队列、线程卸载、超时、协作式暂停和 SSE 状态事件 | 高 |
| `memory/*`、`context_builder.py` | 用户偏好、会话轮次、检查点、语义记忆、SQL 模板脱敏及角色化上下文预算 | 极高（Agent / Memory） |
| `core_engine/access_control.py` | API Key/OIDC、RBAC、表列权限、租户谓词注入和限流 | 高（后端 / 安全） |
| `observability/store.py`、`telemetry.py` | 请求/Agent/LLM 观测、Token 与延迟、审计、质量反馈和 OTLP Trace | 高 |
| `api/main.py` | 统一 HTTP 语义、主请求编排、幂等请求 ID、状态与结果 API | 高 |
| `frontend/src/Chat.jsx`、`TokenMonitor.jsx` | 查询 UI、暂停、异步轮询、离线演示和观测面板 | 中等，取决于目标岗位 |

### 1.7 项目中最复杂的 5 个工程问题及真实方案

#### 问题 1：如何让 Text-to-SQL 流程可控，而不是一次模型调用直出 SQL

真实方案：用 LangGraph 构建 15 个节点的条件状态图。规则先处理高置信问候、帮助、危险操作、数据库选择和常见数据意图，不能确定时再调用 LLM；低于 0.7 的置信度或缺失分析指标会转澄清。查询分支串联语义记忆、实时 Schema、检索、SQL 生成、校验、评审、执行和分析，校验/评审/执行错误最多回流 2 次。

#### 问题 2：如何控制 Schema 与历史上下文的 Prompt 体积

真实方案：从实时数据库元数据构造带版本哈希的业务 Schema Catalog，以 DashScope 向量、关键词和元数据按 0.55/0.30/0.15 权重混合排序；首次召回 4 张表，失败重试时最多扩展到 6 张。向量按 catalog hash 缓存在进程内或 Redis，embedding 失败则回退词法排序。`context_builder.py` 还分别限制会话、语义记忆、Schema 和结果上下文长度。

#### 问题 3：如何降低 LLM SQL 的错误执行和越权风险

真实方案：生成两个独立候选 SQL；先强制单条 `SELECT/WITH`、禁止注释和写/管理语句、限制最多 50 行并做预执行检查，再用 MindsDB parser 做 AST 解析。随后用 SQLGlot 校验表列、JOIN 关系、聚合形态、RBAC 列权限与租户谓词，并结合 `EXPLAIN` 拦截笛卡尔积、超阈值全表扫描或计划成本；多候选通过后再由 Reviewer 选择。

#### 问题 4：如何让阻塞式 Agent 任务具备后端服务语义和失败恢复能力

真实方案：默认把同步 LangGraph 调用放入有界进程内队列，通过线程卸载避免阻塞 FastAPI 事件循环，并提供超时、协作式暂停和 SSE 状态事件。可切换到 Redis Streams 模式：API 返回 202，独立 Worker 消费任务；`HSETNX` 保证同一 request ID 不重复入队，`XAUTOCLAIM` 认领超时 pending 消息，基础设施失败按配置有界重试，任务结果保留 TTL。

#### 问题 5：如何统一单库、远端直连与跨源查询

真实方案：数据源注册表统一描述执行模式。SQLite 使用只读 URI；直连模式通过 SQLAlchemy 维护按数据源复用的连接池，并在连接边界再次检查只读 SQL；联邦模式在内存 DuckDB 中以固定别名挂载 MySQL、PostgreSQL、ClickHouse URI，没有 URI 时挂载仓库内 SQLite 演示库，从同一 SQL 入口读取 Schema、EXPLAIN 和执行结果。

### 1.8 最能体现工程能力的模块

优先级建议：

1. `agents/workflow.py` + `core_engine/sql_reviewer.py`：体现 Agent 编排、状态管理、确定性护栏和失败反馈闭环；
2. `vector_store/schema_indexer.py` + `core_engine/context_builder.py`：体现 RAG、混合检索、缓存降级和 Prompt 工程；
3. `core_engine/durable_tasks.py` + `request_runtime.py`：体现 Python 后端并发、任务状态、幂等与恢复；
4. `core_engine/federation_engine.py` + `database_connectors.py`：体现多源数据访问和只读执行边界；
5. `memory/*` + `observability/*`：体现 Agent Memory、状态持久化与工程可观测性。

### 1.9 代码量较多但不建议写入简历的内容

- `core_engine/mindsdb_core/` 下 43 个工具文件：大部分属于引入的兼容/第三方代码；当前主工作流只直接使用 `mindsdb-sql-parser`，不应把整套 MindsDB 工具代码包装成个人核心成果。
- `frontend/src/app.jsx` 中的作品集和 RPA 展示：与 Text-to-SQL 主链路无关，投 AI / 后端岗位时价值低。
- `api/routes.py`、`database_manager.py`、`dialect_translator.py`：属于较早的兼容入口或旧执行链，主前端和主 API 已不以它们为核心。
- CSV、SQLite 数据文件、Chroma 二进制索引：能证明演示数据存在，但不体现核心编码能力。
- 普通 CRUD 配置接口：可以作为系统完整性证据，不应挤占最终 3～4 条项目 Bullet。

## 2. 核心架构与调用链

```text
React Chat Workspace
  ├─ 同步模式：POST /api/v1/query
  │    └─ FastAPI → bounded RequestRuntime → thread → LangGraph
  └─ 持久模式：POST /api/v1/query → 202
       └─ Redis Stream → independent Worker → LangGraph → result polling

FastAPI boundary
  → API Key / OIDC identity + rate limit
  → data-source and owner authorization
  → user preferences + bounded conversation checkpoint
  → request / audit / trace initialization

LangGraph
  intent (rules first, LLM fallback)
  → supervisor conditional routing
  ├─ help / security / clarification / schema response
  └─ semantic memory
      → live schema
      → versioned Schema Catalog
      → hybrid Schema retrieval (4 → up to 6 tables)
      → two SQL candidates
      → read-only + RBAC + tenant + AST validation
      → deterministic schema / JOIN / aggregate / EXPLAIN review
      → candidate selection
      → SQLite / direct connector / DuckDB federation execution
      → analyst report

Failure loop
  validator / reviewer / executor error
  → refresh schema and expand retrieval
  → regenerate SQL
  → stop after MAX_RETRIES = 2 and return safe failure guidance

Persistence and observability
  → conversation turn + structured checkpoint
  → user-confirmed semantic memory / redacted successful SQL template
  → request, agent, LLM, token, latency, audit and quality metrics
  → SQLite local fallback or PostgreSQL shared state
```

## 3. 最值得写入简历的技术亮点

1. **可恢复的 LangGraph Text-to-SQL 状态图**：不是单轮 Prompt，而是含条件路由、结构化状态、候选生成、错误回流和终止策略的完整链路。
2. **可降级的 Schema RAG**：实时 Schema + 版本化业务元数据 + 混合检索 + catalog hash 缓存 + lexical fallback，并对不同 Agent 设置上下文预算。
3. **确定性 SQL 安全与质量审查**：生成前危险意图拦截，生成后叠加只读、AST、RBAC、租户隔离、Schema 关系与 EXPLAIN 审查。
4. **Redis Streams 持久任务运行时**：API/Worker 解耦、request ID 幂等、pending 任务认领、基础设施失败有界重试与状态轮询。
5. **DuckDB 联邦执行层**：用稳定别名统一远端异构源与本地演示源，支持跨源 SQL 的 Schema、预检与执行。
6. **Agent Memory 与可观测性**：会话检查点、用户级语义记忆、SQL 模板脱敏、Token/延迟/路由/审计记录和 OTLP Trace。

目标岗位侧重点：

| 岗位 | 应优先突出 |
| --- | --- |
| AI 应用 / Agent / RAG | LangGraph 状态图、Schema RAG、Memory、上下文预算、模型调用观测 |
| Python 后端 | FastAPI、Redis Streams、幂等、任务恢复、标准错误语义、PostgreSQL 状态 |
| 数据处理 / 分析 | DuckDB 联邦查询、实时 Schema、SQL Reviewer、结果分析链路 |
| 电商开发 | 订单/用户/商品/行为/特征数据模型；目前只能写“电商”，不能写“跨境” |
| 大数据 | 只能强调多源数据访问与联邦查询，不能声称分布式大数据规模或 Spark/Hadoop 经验 |

## 4. 事实状态表

| 准备使用的技术点或表述 | 状态 | 证据与边界 |
| --- | --- | --- |
| 项目是自然语言到 SQL 的电商数据查询与分析工作台 | `VERIFIED_BY_CODE` | `agents/workflow.py`、`api/main.py`、React 查询入口及仓库电商数据模型 |
| LangGraph 编排意图、记忆、Schema、检索、SQL、评审、执行和分析节点 | `VERIFIED_BY_CODE` | `agents/workflow.py` 中 15 个节点及条件边 |
| 校验/评审/执行失败可回流并最多重试 2 次 | `VERIFIED_BY_CODE` | `MAX_RETRIES = 2` 与三个 route 函数 |
| 每次生成两个 SQL 候选并进行确定性审查和候选选择 | `VERIFIED_BY_CODE` | `sql_agent`、`validator_agent`、`sql_reviewer_agent` |
| Schema RAG 混合向量、关键词和元数据排序，并有词法降级 | `VERIFIED_BY_CODE` | `vector_store/schema_indexer.py`；对应测试通过 |
| 首次召回 4 张表，重试时最多扩展至 6 张 | `VERIFIED_BY_CODE` | `schema_retrieval_agent` 的 `min(6, 4 + error_count)` |
| 角色化 Prompt 上下文有长度与行数预算 | `VERIFIED_BY_CODE` | `core_engine/context_builder.py`；4 项上下文测试通过 |
| SQL 只允许单条 SELECT/WITH，并限制结果最多 50 行 | `VERIFIED_BY_CODE` | `_prepare_read_only_sql`、`execute_real_sql`；工作流测试通过 |
| SQLGlot 实现表列权限、租户谓词注入和 Schema/JOIN/聚合审查 | `VERIFIED_BY_CODE` | `access_control.py`、`sql_reviewer.py`；相关测试通过 |
| DuckDB 可挂载 MySQL、PostgreSQL、ClickHouse URI 或本地 SQLite 演示库 | `VERIFIED_BY_CODE` | `federation_engine.py`、`federation.yaml`；仅证明实现路径，不证明远端生产连接已使用 |
| SQLAlchemy 直连层具有连接池、超时、只读 session 与行数上限 | `VERIFIED_BY_CODE` | `database_connectors.py`；连接器单元测试通过 |
| FastAPI 有界队列、线程卸载、超时、暂停和 SSE 状态事件 | `VERIFIED_BY_CODE` | `request_runtime.py`、API 状态接口；相关测试通过 |
| Redis Streams 支持 request ID 幂等入队、pending 认领和有界重试 | `VERIFIED_BY_CODE` | `durable_tasks.py`；4 项持久任务测试通过 |
| 对话检查点、用户级语义记忆和成功 SQL 模板脱敏 | `VERIFIED_BY_CODE` | `memory/store.py`、`semantic_store.py`；相关测试通过 |
| 请求、Agent、LLM Token/延迟、审计和质量维度被持久化 | `VERIFIED_BY_CODE` | `observability/store.py`、`telemetry.py`；观测测试通过 |
| OIDC/API Key/RBAC/限流机制已实现 | `VERIFIED_BY_CODE` | `access_control.py`、`access_control.yaml`；5 项访问控制测试通过 |
| React 前端支持查询、暂停、异步结果轮询、离线演示和指标页 | `VERIFIED_BY_CODE` | `frontend/src/Chat.jsx`、`TokenMonitor.jsx`；本轮生产构建通过 |
| 当前后端测试共 71 项并全部通过 | `VERIFIED_BY_CODE` | 本轮执行 `USE_TORCH=0; python -m unittest discover -s tests -v`，结果 71/71 |
| 2026-07 的三个提交集中加入联邦、Memory、观测、运行时、安全、持久队列与测试 | `VERIFIED_BY_HISTORY` | `876a7ed`、`eaf1634`、`90f5a14`，提交作者显示为 `Klein` |
| Git 作者 `Klein` 就是本人 | `CONFIRMED_BY_USER` | 用户于本轮明确确认；结合 Git 历史，可将 `Klein` 提交视为本人提交 |
| 本人具体完成哪些核心模块、哪些模块不是主要贡献 | `NEEDS_CONFIRMATION` | 用户提供的模块列表仍为“[填写]”，不能从作者身份自动推导模块分工 |
| 已真实联调远程数据库 | `CONFIRMED_BY_USER` | 用户明确确认；具体数据库类型、版本和环境仍需面试前整理 |
| 已真实执行跨数据库查询 | `CONFIRMED_BY_USER` | 用户明确确认；具体 SQL、参与数据库和结果规模仍需整理 |
| Docker Compose 完整环境真实运行过 | `CONFIRMED_BY_USER` | 用户明确确认；仓库同时存在可解析的 Compose 配置 |
| Redis Streams 真实用于任务恢复 | `CONFIRMED_BY_USER` | 用户明确确认；恢复演练步骤和故障类型仍需整理 |
| 项目已上线并被真实用户或业务团队使用 | `NEEDS_CONFIRMATION` | 仓库只有部署代码与说明，没有环境、访问量或业务使用证据 |
| 面向跨境电商业务 | `NEEDS_CONFIRMATION` | 代码只能证明一般电商订单/商品/行为/特征分析 |
| Schema 规模为 3,000+ 表 | `CONFIRMED_BY_USER` | 用户明确确认；面试前需补充数据源数量、真实/模拟 Schema 构成及统计方式 |
| Token 消耗降低约 75% | `CONFIRMED_BY_USER` | 用户明确确认；面试前需补充基线、测试方式、模型与 Token 统计口径 |
| Text-to-SQL 准确率为 98%+ | `CONFIRMED_BY_USER` | 用户明确确认；面试前需补充测试集规模、准确率定义及判定方式 |
| 约 15,000 条订单在 3 秒内处理 | `CONFIRMED_BY_USER` | 用户明确确认；面试前需补充是否包含 LLM、数据库数量、执行环境与缓存状态 |
| 当前系统接入 MongoDB | `CONTRADICTED` | `db_mongo` 当前配置类型是 PostgreSQL，演示回退是 SQLite，依赖中也无 MongoDB 驱动 |
| 默认使用 Chroma 完成 Schema 检索 | `CONTRADICTED` | 当前默认后端是 `dashscope_memory`；Chroma 仅为可选路径 |
| 离线评估证明 Text-to-SQL 达到 100% 准确率 | `CONTRADICTED` | 本轮 2/2 仅覆盖问候和危险操作规则，没有评估 SQL 正确率 |

`CONFIRMED_BY_USER` 表示用户对事实本身负责确认；它不等同于仓库内已有可复现 benchmark。简历可以保留这些事实，但面试前应补齐测量口径，避免无法解释。

### Git 历史交叉验证

| 提交 | 历史可证明的演进 | 注意事项 |
| --- | --- | --- |
| `876a7ed` | 大幅升级 Agent 工作流，新增数据源注册、DuckDB 联邦、请求控制、Memory、语义记忆、观测和相应测试 | 作者 `Klein` 已由用户确认是本人；具体模块分工仍待填写 |
| `eaf1634` | 新增访问控制、直连数据库适配、有界请求运行时、共享状态、评估、Docker/CI 和更多测试 | 同上 |
| `90f5a14` | 新增 Redis Streams 持久任务、上下文预算、Schema Catalog、SQL Reviewer、统一模型配置、PostgreSQL 状态适配、OTel 与 E2E 配置 | 同上 |

## 5. 需要本人确认的问题

以下问题的答案会决定哪些候选 Bullet 可以安全升级为个人成果或量化成果：

1. 请填写你本人完成的核心模块，以及不是你主要完成的模块；目前不能把 Git 作者身份直接等同于所有模块均独立完成。
2. 远程数据库联调具体包括哪些数据库、版本和部署环境？请准备一个真实跨库 SQL、参与的数据源及返回结果规模。
3. Docker Compose 完整运行是在本机、云服务器还是其他环境？运行了多久，是否包含 API、Worker、Redis、PostgreSQL 四个服务？
4. Token 降低约 75%：基线是什么、测试问题有多少、使用哪个模型、统计 input/total token 还是 Schema 上下文长度？
5. Text-to-SQL 准确率 98%+：测试集规模、问题难度、准确率定义，以及按执行结果、SQL 完全匹配还是人工审核判定？
6. 15,000 条订单 / 3 秒：是否包含 LLM 推理，涉及几个数据库，硬件/容器环境是什么，是否 warm cache，测量次数及 P50/P95 如何？
7. 项目是否服务跨境电商？若是，请补充真实的国家/站点、币种、平台、国际物流、关务或跨时区数据问题；否则继续定位为一般电商数据分析。
8. OIDC、租户隔离和 PostgreSQL 运行时状态是否也完成实际联调？用户本轮只明确确认了完整 Compose 与 Redis Streams 恢复，不应自动扩大到每个安全能力。

### 本轮验证记录与限制

- `USE_TORCH=0; python -m unittest discover -s tests -v`：**71 项测试全部通过**。未设置该环境变量时，本机 Anaconda 的 PyTorch DLL 初始化失败；这是当前机器依赖冲突，不是仓库测试失败。
- `npm run build`：**通过**，Vite 完成生产构建。
- `docker compose config --quiet`：**通过**，Compose 配置可解析。
- `python scripts/run_evaluation.py --offline`：**2/2 通过**，但只覆盖问候与危险写操作规则，不能作为 Text-to-SQL 准确率证据。
- 本轮未调用真实 LLM、未连接远端业务数据库、未启动完整 Docker 服务、未执行 Playwright 浏览器测试，因此不对这些运行效果作结论。
