import pandas as pd
from sqlalchemy import create_engine
import os


def csv_to_db(csv_file, db_uri, table_name):
    """
    读取 CSV 文件并将其导入到指定的数据库表中（支持 SQLite, MySQL, PostgreSQL 等）
    """
    if not os.path.exists(csv_file):
        print(f"❌ 找不到文件 {csv_file}，请确保它和本脚本在同一目录下。")
        return False

    print(f"📂 正在读取 {csv_file} 并连接数据库 ...")
    try:
        # 使用 'utf-8-sig' 处理可能存在的 BOM 头
        df = pd.read_csv(csv_file, encoding='utf-8-sig')

        # 使用 SQLAlchemy 创建数据库引擎，它能自动识别 db_uri 是 MySQL 还是 SQLite
        engine = create_engine(db_uri)

        # 将 DataFrame 写入数据库
        # if_exists='replace' 表示如果表存在则覆盖，index=False 表示不导入 pandas 的行索引
        df.to_sql(table_name, engine, if_exists='replace', index=False)

        # 统计写入的行数
        count = len(df)
        print(f"✅ 成功将 {count} 条数据写入目标库的 '{table_name}' 表中！")
        return True
    except Exception as e:
        print(f"❌ 导入 {csv_file} 失败: {str(e)}")
        return False


def setup_federated_databases():
    print("🚀 开始构建企业级多源异构数据库...\n")

    # ==========================================
    # 1. 真实 MySQL / 模拟 MySQL (核心业务库)
    # ==========================================
    # 如果你有真实的 MySQL 环境，请将下面的开关改成 True
    # 并在 mysql_uri 中填入你真实的账号、密码、IP 和 数据库名（需提前在 MySQL 中建好该库）
    USE_REAL_MYSQL = False

    if USE_REAL_MYSQL:
        # 真实的 MySQL 连接字符串 (格式: mysql+pymysql://用户名:密码@IP:端口/数据库名)
        mysql_uri = "mysql+pymysql://root:123456@127.0.0.1:3306/business_db"
        print("🌐 [1/3] 正在直连真实的 MySQL 业务库...")
    else:
        # 回退到本地 SQLite 模拟
        mysql_uri = "sqlite:///mysql_business.db"
        print("📦 [1/3] 正在构建本地 SQLite 模拟业务库...")

    csv_to_db('users.csv', mysql_uri, 'users')
    csv_to_db('products.csv', mysql_uri, 'products')
    csv_to_db('orders.csv', mysql_uri, 'orders')
    print("-" * 50)

    # ==========================================
    # 2. 真实 PostgreSQL / 模拟 Mongo (行为日志库)
    # ==========================================
    # 如果你要连真实的 PostgreSQL，只需将 URI 改为:
    # logs_uri = "postgresql+psycopg2://postgres:123456@127.0.0.1:5432/logs_db"
    logs_uri = "sqlite:///mongo_logs.db"
    print(f"📦 [2/3] 正在构建日志库: {logs_uri}")
    csv_to_db('user_behaviors.csv', logs_uri, 'user_behaviors')
    print("-" * 50)

    # ==========================================
    # 3. 真实 ClickHouse / 模拟 ClickHouse (数仓特征库)
    # ==========================================
    # 如果你要连真实的 ClickHouse，只需将 URI 改为:
    # olap_uri = "clickhouse+clickhouse_driver://default:@127.0.0.1/features_db"
    olap_uri = "sqlite:///clickhouse_features.db"
    print(f"📦 [3/3] 正在构建数仓特征库: {olap_uri}")
    csv_to_db('user_features.csv', olap_uri, 'user_features')
    csv_to_db('product_features.csv', olap_uri, 'product_features')
    print("-" * 50)

    print("🎉 所有数据库构建完成！")


if __name__ == "__main__":
    setup_federated_databases()