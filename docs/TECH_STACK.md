# 联邦 Text2SQL 项目技术栈与架构说明

> 依据仓库当前代码、依赖与配置整理。本文描述的是已实现的能力；README 中的规划性表述若与代码不一致，以本文件的“当前实现”说明为准。

## 1. 项目定位

这是一个面向自然语言数据查询的本地 Web 工作台。用户在 React 页面提出问题，FastAPI 将请求送入 LangGraph 编排的多 Agent 工作流；工作流生成并校验只读 SQL，随后查询单库 SQLite 或 DuckDB 驱动的多源联邦数据，并把结果交给分析 Agent 生成 Markdown 报告。

核心目标包括：

- Text-to-SQL 与数据分析问答；
- 跨数据源/跨方言的联邦查询；
- 运行时 Schema 检索，降低提示词中无关表结构的比例；
- 只读 SQL 安全控制、重试和请求暂停；
- 会话偏好、对话上下文和可确认的语义记忆；
- 对请求、Agent、LLM 调用和 Token 用量的可观测性。

## 2. 技术栈总览

| 层级 | 当前技术 | 作用 |
| --- | --- | --- |
| 后端语言与运行时 | Python、Uvicorn | API 与 Agent 运行时 |
| Web API | FastAPI、Pydantic、CORS Middleware | HTTP 接口、请求/响应模型、跨域支持 |
| Agent 编排 | LangGraph、LangChain Core | TypedDict 状态图、条件路由、消息对象 |
| LLM 接入 | `langchain-openai` 的 `ChatOpenAI` | 意图识别、SQL 生成/评审、结果分析；当前代码模型名为 `deepseek-v4-flash` |
| 向量嵌入 | DashScope、`DashScopeEmbeddings`、`text-embedding-v4` | Schema 和语义记忆的语义检索 |
| 向量存储 | 内存向量缓存（默认）、Chroma（可选） | 默认规避 Windows 上嵌入式 Chroma 原生运行时问题 |
| SQL 安全与解析 | `mindsdb-sql-parser`、仓库内 MindsDB renderer | AST 解析、只读 SQL 检查、方言转换辅助 |
| 联邦查询 | DuckDB 1.3+ | 内存 DuckDB 连接，挂载 SQLite 演示库或远端 MySQL/PostgreSQL/ClickHouse |
| 关系型数据访问 | SQLite、SQLAlchemy、Pandas | 演示库、记忆/观测库、CSV 初始化 |
| 配置 | YAML、python-dotenv | 数据源、联邦映射、环境变量加载 |
| 前端 | React 19、React DOM、Vite 6 | 单页应用、开发服务器和构建 |
| 样式与图标 | Tailwind CSS 4、`@tailwindcss/vite`、Lucide React | 响应式样式和图标 |
| 测试 | Python `unittest` | 工作流安全、意图、记忆、检索、数据源、暂停和观测测试 |
| 打包/部署辅助 | Node.js、`scripts/copy-dist.mjs`、Vercel 配置目录 | 前端构建、静态产物复制；README 提到 Vercel/Render，但仓库未包含后端部署定义 |

## 3. 总体请求链路

```text
React Chat
  ├─ 正常模式：POST /api/v1/query
  └─ 离线模式：demoMock.js（不访问后端）
          │
          ▼
FastAPI API
  ├─ 解析数据源、偏好、会话上下文和请求 ID
  ├─ 创建观测记录
  └─ 调用 LangGraph app.invoke()
          │
          ▼
LangGraph 多 Agent 工作流
  意图 → 监督路由 → 语义记忆 → 实时 Schema → Schema 检索
       → 双候选 SQL → 只读/AST 校验 → SQL 评审 → 执行 → 分析报告
          │
          ▼
SQLite 单库 或 DuckDB 联邦引擎
          │
          ▼
返回 answer、SQL、执行轨迹、检索信息、Token 与观测指标
```

## 4. 后端与 API

### 4.1 FastAPI 服务

入口为 `api/main.py`，服务默认监听 `127.0.0.1:8000`。服务启用了允许所有来源、方法和请求头的 CORS；适合本地开发，但生产部署应将 `allow_origins` 收紧为实际前端域名。

`POST /api/v1/query` 是主接口。请求包括 `user_id`、`query`、可选的 `data_source_id`、`conversation_id`、澄清请求关联 ID、客户端请求 ID 与兼容字段 `target_db`。接口会：

1. 按“本次指定数据源 → 用户默认数据源 → 旧字段映射”确定可用数据源；
2. 读取用户偏好和最近会话上下文；
3. 创建请求控制与观测记录；
4. 构造 LangGraph 初始状态并同步调用工作流；
5. 返回回答、最终 SQL、查询计划、检索来源、候选 SQL 数、执行轨迹、重试次数和聚合 Token 指标；
6. 在成功查询后写入会话轮次，并尝试保存可复用 SQL 模板。

其他接口包括：

| 路径 | 作用 |
| --- | --- |
| `GET /api/v1/data-sources` | 返回已启用的数据源 |
| `GET/PUT /api/v1/memory/preferences/{user_id}` | 读取/更新默认数据源、输出风格、是否记忆、默认行数等偏好 |
| `DELETE /api/v1/memory/{user_id}` | 清除对话/偏好和该用户的语义记忆 |
| `POST /api/v1/requests/{request_id}/pause` | 标记请求暂停 |
| `GET/POST/DELETE /api/v1/memory/semantic...` | 查询、显式确认写入、删除语义记忆 |
| `GET /api/v1/observability/summary` | 返回时间窗口内请求、Token、路由和 Agent 指标 |
| `GET /api/v1/observability/requests/{request_id}` | 查看某一次请求的执行详情 |

`api/routes.py` 还保留了一个较早的 `/chat/sql` 路由；主前端使用的是 `/api/v1/*` 路由。

### 4.2 启动与本地开发

- `python start_all.py`：先运行 `api/main.py`，等待三秒后运行 `npm run dev`；
- 后端依赖：`pip install -r requirements.txt`；
- 前端依赖：在 `frontend` 下执行 `npm install`；
- `init_multisource_db.py`：从 CSV 初始化多源演示数据；
- 前端开发服务器默认由 Vite 提供，README 约定访问地址为 `http://localhost:5173`。

## 5. Agent 开发细节

### 5.1 状态与模型调用

核心实现在 `agents/workflow.py`。`AgentState` 是贯穿全图的 TypedDict，保存 LangChain 消息、用户与请求标识、会话/语义记忆、意图及实体、数据源/执行模式、Schema、候选 SQL、校验与执行结果、重试次数、相似度阈值和执行轨迹。

所有 LLM 调用经 `_invoke_llm()` 封装：调用前检查暂停标记，调用后记录模型名、提示词字符数、输入/输出/总 Token、耗时与失败信息到可观测性库。当前实例配置为低温度（`temperature=0.1`）、60 秒超时、3 次 SDK 重试。

> 注意：README 与前端页脚中出现 OpenAI/Qwen 的展示文字；当前工作流代码实际以 `ChatOpenAI(model="deepseek-v4-flash")` 创建模型，嵌入则使用 DashScope。最终所连服务还取决于 `.env` 中的兼容 API 配置，文档或页面标签不能替代运行时配置。

### 5.2 节点与路由

| 阶段 | 节点 | 实现重点 |
| --- | --- | --- |
| 意图 | `intent` | 先用关键词规则识别高置信请求，再用 LLM 返回 Pydantic 约束的 JSON；低于 0.7 或分析指标缺失时转澄清 |
| 监督 | `supervisor` | 识别 SQLite/联邦模式，生成查询计划，按意图路由 |
| 非查询分支 | `security`、`clarification`、`help`、`data_source`、`schema_response` | 分别拒绝写操作、补充问题、说明产品能力、列出数据源、回答表结构问题 |
| 长期上下文 | `semantic_memory` | 从用户已确认语义记忆和成功 SQL 模板中检索可复用上下文 |
| 实时元数据 | `schema` | 读取当前单库或联邦连接的 Schema；失败时计入重试 |
| Schema RAG | `retrieval` | 取最相关表结构；首次取 4 张，重试时最多扩展至 6 张 |
| SQL 生成 | `sql` | 对同一问题以两种策略分别生成候选 SQL；提示词要求只返回一条 SQL |
| 防护 | `validator` | 清理 SQL、限制为单条 `SELECT`/`WITH`、限制行数、禁止危险语句，再经 MindsDB AST 解析 |
| 选择 | `reviewer` | 一条有效候选直接选用；多条时由 LLM 只输出候选编号 |
| 执行 | `executor` | 在 SQLite 或 DuckDB 联邦引擎执行并返回最多 50 行 JSON |
| 报告 | `analyst` | 仅基于实际结果输出 Markdown 分析和 1–3 条洞察；失败时返回原因和修正建议 |

图中 `validator` 或 `executor` 失败会回到 `schema → retrieval → sql` 重新生成，最多 `MAX_RETRIES = 2` 次，超限后转交 `analyst` 输出失败说明。

### 5.3 意图与安全策略

支持的意图为：`text_to_sql`、`data_analysis`、`schema_question`、`database_selection`、`operation_help`、`greeting`、`unsafe_operation`、`clarification_required` 和 `out_of_scope`。

系统是**只读查询设计**：规则层识别 `DELETE`、`DROP`、`UPDATE`、`INSERT`、`ALTER`、`CREATE`、授权等中英文写操作；SQL 校验层再次限制语句类型、禁止多语句，并将默认/超大结果集限制为 50 行。SQL 执行前还会做 DuckDB 预检或 SQLite 执行。该策略降低误写风险，但不能替代生产环境的数据库只读账号、网络隔离和审计。

### 5.4 请求暂停

`core_engine/request_control.py` 使用进程内 `Lock` 管理请求 ID 的暂停集合。API 可标记暂停，Agent 在每次 LLM 调用前检查。它适合当前单进程本地运行；若部署为多进程/多副本，应改用 Redis、数据库或任务队列等共享状态。

## 6. 数据与联邦查询

### 6.1 数据源注册

`config/data_sources.yaml` 与 `core_engine/data_source_registry.py` 管理数据源。当前启用：

- `sqlite_local`：SQLite Ecommerce Demo，单库模式；
- `federated_demo`：DuckDB Federated Demo，联邦模式。

MySQL 业务库、PostgreSQL 日志库和 ClickHouse 特征仓默认配置为未启用，连接信息通过环境变量引用，而不是写死在 YAML 中。

### 6.2 DuckDB 联邦层

`core_engine/federation_engine.py` 在内存中创建 DuckDB 连接，并依据 `config/federation.yaml` 挂载来源：

- 配置了 URI 时，使用相应 DuckDB 扩展访问 MySQL、PostgreSQL 或 ClickHouse；
- 未配置 URI 时，用 DuckDB SQLite 扩展挂载本地演示 `.db` 文件；
- 对联邦 SQL 做预检，然后执行并将 `date`、`datetime`、`Decimal` 等结果转换为 JSON 兼容值。

联邦别名是 `db_mysql`、`db_mongo`、`db_ch`。其中 `db_mongo` 在当前配置中实际类型为 `postgres`，名称表达的是“日志源的逻辑别名”，并不是已接入 MongoDB 驱动。

`config/db_mappings.yaml` 还保留虚拟库到方言/主机的映射思路；当前主流程主要依据数据源与联邦配置运行。

### 6.3 Schema 检索

`vector_store/schema_indexer.py` 将实时 Schema 按表切为 LangChain `Document`：

- 默认 `dashscope_memory`：通过 DashScope embedding 生成向量并以 catalog hash 缓存在 API 进程内；
- 失败回退：基于词项重叠的 lexical ranking；
- 可选 `SCHEMA_VECTOR_BACKEND=chroma`：持久 Chroma 集合，但代码明确将其设为可选，以规避本 Windows 环境的运行时稳定性风险；
- catalog 变化会使缓存重建，避免使用旧表结构。

## 7. 记忆与可观测性

### 7.1 记忆

两类记忆都以 SQLite 保存：

- `memory/store.py`：用户偏好、会话轮次和最近对话上下文；
- `memory/semantic_store.py`：字段别名、指标定义、用户确认的知识、成功 SQL 模板与过期策略。

语义记忆写入 API 要求 `confirmed=true`；成功的 Text2SQL/分析请求会尝试保存模板。检索会按用户、查询和数据源范围过滤，作为工作流提示词上下文的一部分。

### 7.2 可观测性

`observability/store.py` 将数据保存在 `observability.db`，记录：

- 请求生命周期、延迟、状态、重试次数、意图置信度、澄清是否解决与 Supervisor 路由；
- 每个 Agent 事件和元数据；
- 每次 LLM 调用的模型、Token、耗时、状态和错误。

汇总接口提供成功率、平均延迟、Token 趋势、按 Agent 调用情况、意图识别质量和澄清后的成功次数，供前端 Token Monitor 展示。

## 8. 前端开发

### 8.1 构建与依赖

前端目录为 `frontend/`，使用 Vite 的 React 插件和 Tailwind v4 Vite 插件；入口为 `src/main.jsx`，根组件为 `src/app.jsx`。`package.json` 的主要命令：

| 命令 | 用途 |
| --- | --- |
| `npm run dev` | 启动 Vite 开发服务器 |
| `npm run build` | 正常生产构建 |
| `npm run build:demo` | 以 demo mode 构建 |
| `npm run preview` | 预览构建结果 |
| `npm run package` / `package:demo` | 构建并执行 `scripts/copy-dist.mjs` 复制产物 |

全局样式 `src/index.css` 使用 `@import "tailwindcss"`。UI 不引入组件库，主要以 Tailwind 原子类与 Lucide 图标组成。

### 8.2 页面与交互

`src/app.jsx` 是轻量的状态式页面切换，而不是 React Router：主页提供项目卡片；Text2SQL 卡片进入 `Chat`；另有静态 RPA 案例展示页面，使用 `public/` 下视频资源。RPA 内容是作品集展示，不属于 Text2SQL 后端调用链。

`src/Chat.jsx` 实现数据问答工作台：

- 会加载数据源和用户偏好，维护当前会话/请求 ID；
- 发送消息时调用查询 API，展示回答、SQL、请求状态与重试；
- 请求处理中可调用暂停 API；
- 支持切换离线演示模式；
- 可进入 Token Monitor 页面。

`src/demoMock.js` 是降级策略：离线模式不发请求，返回与 `/api/v1/query` 结构兼容的模拟回答和 SQL。`src/TokenMonitor.jsx` 用原生 SVG 折线图与指标卡渲染观测汇总，没有额外图表依赖。

## 9. 配置、数据与环境变量

不要将真实凭据提交到仓库；`.env` 仅应保存本地或部署环境的密钥与连接信息。项目代码/配置涉及的变量包括：

| 变量 | 用途 |
| --- | --- |
| LLM 相关变量 | 由 `ChatOpenAI`/dotenv 使用，用于模型服务认证或兼容端点配置 |
| `MYSQL_URI` | MySQL 业务源 URI |
| `POSTGRES_URI` | PostgreSQL 日志源 URI |
| `CLICKHOUSE_URI` | ClickHouse 特征源 URI |
| `SCHEMA_VECTOR_BACKEND` | `dashscope_memory`（默认）或 `chroma` |
| 数据源配置中的 `connection_secret_ref` | 为 SQLite 演示库路径或联邦配置引用预留的密钥/路径名称 |

仓库根目录包含若干 SQLite 演示库和 CSV 文件，例如电商、订单、用户行为与特征数据。这些是本地示例数据，不应视为生产数据管理方案。

## 10. 测试覆盖

测试位于 `tests/`，使用 `unittest`，当前覆盖：

- SQL 只读验证、单语句限制和结果行数截断；
- 规则意图识别与意图评估；
- 数据源注册；
- 请求暂停；
- 普通记忆与语义记忆；
- Schema Indexer 默认后端；
- 可观测性存储；
- LangGraph 工作流基础行为。

建议在改动 Agent 提示词、SQL 规则、数据源配置或记忆 schema 后执行：

```powershell
python -m unittest discover -s tests
```

## 11. 当前边界与后续工程建议

1. **模型配置一致性**：统一 README、前端展示标签、`ChatOpenAI` 模型名与 `.env` 中实际供应商，避免环境切换时误判。
2. **生产级安全**：除了应用层校验，数据库必须使用只读账号；收紧 CORS；对联邦扩展下载、连接 URI 和 SQL 审计设置网络与权限边界。
3. **异步与可扩展性**：当前 `app.invoke()` 和暂停状态是进程内同步实现。长查询可迁移至后台任务/队列，并用 Redis 等共享取消信号。
4. **前端工程化**：页面目前以单个较大的 JSX 组件和状态切换组织；功能增加后可拆分 API client、状态管理、路由、错误边界与组件单测。
5. **联邦数据源命名**：将 `db_mongo` 的逻辑名称和真实 PostgreSQL 类型在 UI/文档中明确区分，避免对 MongoDB 支持范围产生误解。
6. **RAG 评估**：为 Schema 召回、候选 SQL 选择和最终 SQL 正确率建立可复现基准集，并把观测指标与离线评测结果结合。

