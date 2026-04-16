# 负责模拟向量数据库的召回
def search_schemas(query: str, threshold: float) -> str:
    """
    模拟通过 Milvus 或 Chroma 获取对应高优 Table 的 Schema 信息
    """
    # 模拟动态容错：当 threshold 降低时，召回的关联表变多，给大模型更多上下文
    if threshold < 0.6:
        return """
        Table: user_behavior_log
        Columns: id (int), user_id (int), action (varchar), extra_info (json), created_at (timestamp)

        Table: orders
        Columns: order_id (int), user_id (int), amount (decimal), status (varchar), created_at (timestamp)
        """
    else:
        # 严格匹配时，只给订单表
        return """
        Table: orders
        Columns: order_id (int), user_id (int), amount (decimal), status (varchar), created_at (timestamp)
        """