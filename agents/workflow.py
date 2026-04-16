
import json
import sqlite3
import os
import operator
from typing import TypedDict, Annotated, List
from langchain_core.messages import BaseMessage, HumanMessage, AIMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, END
import dotenv

dotenv.load_dotenv()

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# ==========================================
# 1. 定义图状态 (Graph State)
# ==========================================
class AgentState(TypedDict):
    messages: Annotated[List[BaseMessage], operator.add]
    user_query: str
    target_db_type: str  # 'federated' 为多源跨库，其他如 'sqlite' 为单库
    relevant_schemas: str
    generated_sql: str
    execution_result: str
    error_count: int
    similarity_threshold: float


# ==========================================
# 2. 核心：动态路由引擎层 (Dynamic Routing Engine)
# ==========================================

def get_database_schema(db_type: str) -> str:
    """
    根据目标数据库类型，动态返回对应的表结构 Schema。
    """
    if db_type == "federated":
        # 多源联邦库 Schema
        return """
        【库A: db_mysql】(核心交易库)
        - 表名: db_mysql.users (用户表) -> 字段: user_id, age, gender, province, city, registration_date, member_level, account_balance, credit_score
        - 表名: db_mysql.products (商品表) -> 字段: product_id, product_name, category, brand, price, sales_count
        - 表名: db_mysql.orders (订单表) -> 字段: order_id, user_id, product_id, quantity, order_date, order_status, payment_method, unit_price, total_amount, discount, actual_payment, delivery_date, receive_date, review_score, review_content

        【库B: db_mongo】(行为日志库)
        - 表名: db_mongo.user_behaviors (用户行为表) -> 字段: behavior_id, user_id, product_id, behavior_type, behavior_time, duration_seconds

        【库C: db_ch】(数仓特征库)
        - 表名: db_ch.user_features (用户画像表) -> 字段: user_id, total_spent, order_count, completed_orders, avg_order_amount, browse_count, consumption_level, member_level_score
        - 表名: db_ch.product_features (商品特征表) -> 字段: product_id, total_revenue, total_sales, conversion_rate, avg_review_score, popularity_score
        """
    else:
        # 默认的单体测试库 (ecommerce_test.db) Schema
        return """
        # Table: orders (订单表)
        # Columns: order_id (TEXT), customer_id (TEXT), order_status (TEXT), order_purchase_timestamp (DATETIME), order_approved_at (DATETIME), order_delivered_carrier_date (DATETIME), order_delivered_customer_date (DATETIME), order_estimated_delivery_date (DATETIME), timeout (TEXT)
        # 
        # Table: user_behavior_log (用户行为日志表)
        # Columns: id (INTEGER), customer_id (TEXT), action (VARCHAR), extra_info (TEXT), created_at (DATETIME)
        Table: orders (订单表)
        Columns: id (INTEGER), user_id (TEXT), user_name (TEXT), product_id (TEXT), product_name (TEXT), category (TEXT), unit_price (REAL), purchase_time (DATETIME), quantity (INTEGER), total_amount (REAL), city (TEXT), gender (TEXT), age (INTEGER)
    
        Table: user_behavior_log (用户行为日志表)
        Columns: id (INTEGER), user_id (TEXT), action (VARCHAR), extra_info (TEXT), created_at (DATETIME)
        """

def execute_real_sql(sql: str, db_type: str) -> str:
    """
    动态执行器：根据路由类型决定是开启联邦跨库计算，还是连单体数据库。
    """
    print(f"\n[执行引擎] 当前路由模式: {db_type} | 正在执行 SQL: \n{sql}")
    try:
        if db_type == "federated":
            # --- 联邦跨库引擎逻辑 ---
            conn = sqlite3.connect(':memory:')
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            db_mysql = os.path.join(BASE_DIR, 'mysql_business.db')
            db_mongo = os.path.join(BASE_DIR, 'mongo_logs.db')
            db_ch = os.path.join(BASE_DIR, 'clickhouse_features.db')

            for db_file in [db_mysql, db_mongo, db_ch]:
                if not os.path.exists(db_file):
                    return f"ERROR: 找不到底层物理数据库 {db_file}，请先运行 init_multisource_db.py。"

            cursor.execute(f"ATTACH DATABASE '{db_mysql}' AS db_mysql")
            cursor.execute(f"ATTACH DATABASE '{db_mongo}' AS db_mongo")
            cursor.execute(f"ATTACH DATABASE '{db_ch}' AS db_ch")
        else:
            # --- 单体数据库查询逻辑 ---
            db_path = os.path.join(BASE_DIR, 'ecommerce_test.db')
            if not os.path.exists(db_path):
                return f"ERROR: 找不到单体数据库文件 {db_path}，请先运行单库初始化的脚本。"
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

        # 清理并执行 SQL
        clean_sql = sql.replace("```sql", "").replace("```", "").strip()
        cursor.execute(clean_sql)
        rows = cursor.fetchall()

        result_list = [dict(row) for row in rows]
        conn.close()

        if not result_list:
            return "执行成功，但未找到匹配的数据 (结果为空)。"

        return json.dumps(result_list[:50], ensure_ascii=False)  # 截断前50条

    except Exception as e:
        error_msg = f"ERROR: {str(e)}"
        print(f"⚠️ [底层执行报错] {error_msg}")
        return error_msg


# 初始化大模型配置
llm = ChatOpenAI(
    model="qwen3.5-flash",
    temperature=0.1,
    timeout=60,
    max_retries=3
)


# ==========================================
# 3. 定义 5 类核心 Agent 节点
# ==========================================

def intent_router_agent(state: AgentState):
    print("👉 [1. IntentRouter] 正在分析意图...")
    return {"user_query": state["messages"][-1].content}


def schema_retriever_agent(state: AgentState):
    threshold = state.get("similarity_threshold", 0.8)
    error_count = state.get("error_count", 0)
    db_type = state.get("target_db_type", "sqlite")

    if error_count > 0:
        threshold = max(0.4, threshold - 0.2)
        print(f"🔄 [2. SchemaRetriever] 触发容错，放宽阈值至 {threshold}...")
    else:
        print(f"🔍 [2. SchemaRetriever] 加载 {db_type} 环境的元数据表...")

    # 动态获取当前环境需要的 Schema
    schema_info = get_database_schema(db_type)
    return {"relevant_schemas": schema_info, "similarity_threshold": threshold}


def sql_generator_agent(state: AgentState):
    db_type = state.get("target_db_type", "sqlite")
    print(f"🧠 [3. SQLGenerator] 正在基于 {db_type} Schema 生成 SQL...")

    # 根据是否是联邦模式，给大模型下达不同的限制指令
    if db_type == "federated":
        instruction = """
        1. 你必须使用 `库名.表名` 的语法来引用表。例如查询用户，必须写 `FROM db_mysql.users`。
        2. 如果用户的查询需要结合业务数据和日志数据，允许使用 JOIN 跨库关联，例如 `db_mysql.orders JOIN db_mongo.user_behaviors ON ...`。
        """
    else:
        instruction = """
        1. 请编写标准的 SQLite SQL 语句查询当前的单体数据库。
        2. 不要加上库名前缀，直接使用表名即可（例如 `FROM orders`）。
        """

    prompt = f"""
    你是企业级 Data Agent，请根据以下表结构，为用户的提问编写标准 SQL。

    表结构: 
    {state['relevant_schemas']}

    用户提问: {state['user_query']}

    【核心指令要求】：
    {instruction}
    3. 只返回纯 SQL 字符串，不要带 markdown 标记，不要输出分析文字。

    之前的报错信息: {state.get('execution_result', '无')}
    如果之前有报错，请修复你的 SQL。
    """
    response = llm.invoke([HumanMessage(content=prompt)])
    clean_sql = response.content.replace("```sql", "").replace("```", "").strip()
    return {"generated_sql": clean_sql}


def boundary_executor_agent(state: AgentState):
    db_type = state.get("target_db_type", "sqlite")
    print(f"⚙️ [4. BoundaryExecutor] 正在底层数据库中抓取数据...")

    sql = state["generated_sql"]
    result = execute_real_sql(sql, db_type)

    if result.startswith("ERROR:"):
        return {
            "execution_result": result,
            "error_count": state.get("error_count", 0) + 1
        }

    print("✅ [边界检测] 数据提取成功！")
    return {"execution_result": result}


def data_analyst_agent(state: AgentState):
    print("📊 [5. DataAnalyst] 正在分析数据并生成洞察报告...")
    prompt = f"""
    根据用户的原始提问和查询的真实结果，返回分析结论。
    用户提问: {state['user_query']}
    执行的SQL: {state['generated_sql']}
    SQL结果: {state['execution_result']}

    要求：
    1. 必须使用提供的 SQL 结果进行准确回答，绝对不能自己捏造数据。
    2. 给出专业、排版整洁的 Markdown 数据总结报告。
    """
    response = llm.invoke([HumanMessage(content=prompt)])
    return {"messages": [AIMessage(content=response.content)]}


# ==========================================
# 4. 定义图边界与执行逻辑
# ==========================================
def should_retry(state: AgentState) -> str:
    result = state.get("execution_result", "")
    error_count = state.get("error_count", 0)
    if result.startswith("ERROR:") and error_count < 3:
        return "retry"
    elif result.startswith("ERROR:"):
        return "fail"
    return "success"


workflow = StateGraph(AgentState)
workflow.add_node("router", intent_router_agent)
workflow.add_node("retriever", schema_retriever_agent)
workflow.add_node("sql_gen", sql_generator_agent)
workflow.add_node("executor", boundary_executor_agent)
workflow.add_node("analyst", data_analyst_agent)

workflow.set_entry_point("router")
workflow.add_edge("router", "retriever")
workflow.add_edge("retriever", "sql_gen")
workflow.add_edge("sql_gen", "executor")

workflow.add_conditional_edges(
    "executor",
    should_retry,
    {
        "retry": "retriever",
        "fail": "analyst",
        "success": "analyst"
    }
)
workflow.add_edge("analyst", END)

app = workflow.compile()