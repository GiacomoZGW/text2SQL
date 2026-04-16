import os

from langchain_community.embeddings import DashScopeEmbeddings
from langchain_openai import OpenAIEmbeddings
from langchain_community.vectorstores import Chroma
from langchain_core.documents import Document

# 导入我们的 API 配置
import os
from dotenv import load_dotenv

# 🚀 核心补充：强制加载项目根目录下的 .env 文件
# 如果找到了 .env 文件，它会自动把里面的键值对写入系统的环境变量中
load_dotenv()

def build_schema_index():
    print(f"🛠️ 正在使用 API  构建 Schema 向量索引...")

    # 初始化纯 API 版本的 Embedding 客户端，秒级响应，不占本地内存
    embeddings =DashScopeEmbeddings(
        model="text-embedding-v4"
    )

    # 准备要被向量化的数据库表结构和业务描述
    schemas = [
        Document(
            page_content="Table: user_behavior_log. Columns: id (int), user_id (int), action (varchar), extra_info (json), created_at (timestamp). Description: 记录用户的退款请求、点击、浏览等行为日志。当询问用户行为时查询此表。",
            metadata={"table": "user_behavior_log"}
        ),
        Document(
            page_content="Table: orders. Columns: order_id (int), user_id (int), amount (decimal), status (varchar), created_at (timestamp). Description: 记录用户的购买订单、金额、退款状态、物流状态。当询问销量、订单金额时查询此表。",
            metadata={"table": "orders"}
        )
    ]

    # 将其存入本地轻量级向量数据库 Chroma 中
    db_path = os.path.join(os.path.dirname(__file__), "chroma_db")

    vectorstore = Chroma.from_documents(
        documents=schemas,
        embedding=embeddings,
        persist_directory=db_path
    )
    vectorstore.persist()
    print(f"✅ 向量索引构建完成！数据已保存在 {db_path} 目录下。")


if __name__ == "__main__":
    build_schema_index()