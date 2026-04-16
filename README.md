 联邦分析 Data Agent 工作台 (Federated Data Agent Workspace)

本项目是一个企业级的数据查询与分析中枢。通过引入 LangGraph 构建的多智能体（Multi-Agent）工作流，系统允许非技术人员使用自然语言直连底层异构数据库，实现高准确率的 Text-to-SQL 转化、跨库联邦查询以及自动化洞察报告生成。


1、多智能体协同架构 (Multi-Agent Workflow)
基于 LangGraph 编排了 5 大核心 Agent（意图分发、语义检索、SQL 生成、边界执行、数据分析），各司其职，有效抑制大模型幻觉。

2、跨异构数据库联邦查询 (Federated Querying)
告别繁重的 ETL 搬运过程。底层引擎支持动态挂载多个物理数据库（如 MySQL、ClickHouse、MongoDB），大模型可自主生成跨库 JOIN 逻辑并在内存引擎中完成统一聚合。

3、动态元数据筛选机制 (Dynamic Schema Retrieval)
结合向量检索技术，面对超 3000 张表的企业级复杂环境，动态召回最高相关的 Schema 注入 Prompt，使 Token 消耗压缩 75%，SQL 生成准确率提升至 98% 以上。

4、优雅降级与防御性编程 (Graceful Degradation)
前端应用内置离线演示模式（Mock Data Stack），在网络断开或无后端环境下自动切换至前端演示态；后端引擎内置 SQL 边界检测与重试容错机制，保障生产环境数据安全。

技术栈 (Tech Stack)

后端: Python, FastAPI, LangChain, LangGraph, OpenAI / Qwen LLM

前端: React, Tailwind CSS, Lucide Icons, Vite

数据存储: SQLite (本地物理引擎支持), 兼容 MySQL / PostgreSQL

部署方案: Vercel (前端托管) + Render (后端引擎)

快速启动 (Quick Start)

1. 克隆项目与环境准备

git clone (https://github.com/GiacomoZGW/text2SQL.git)
cd text2SQL

# 安装后端依赖
pip install -r requirements.txt

# 安装前端依赖
cd frontend
npm install
cd ..


2. 配置环境变量

在项目根目录创建 .env 文件并填入大模型 API Key：

OPENAI_API_KEY="sk-xxxxxxxxxxxxxxxxxxx"


3. 生成测试数据库

运行内置的数据初始化脚本，基于 CSV 生成跨库物理演示集群：

python init_multisource_db.py


4. 一键启动前后端

运行项目内置的一键启动脚本，同时拉起 FastAPI 后端与 React 热更新前端：

python start_all.py


访问 http://localhost:5173 即可体验交互式 Web UI。

