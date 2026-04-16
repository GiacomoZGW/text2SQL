import sqlite3
import yaml
from pathlib import Path


class DatabaseManager:
    """
    异构数据库连接池管理与执行器
    （当前已接入 SQLite 真实测试数据库）
    """
    _db_mappings = {}

    @classmethod
    def load_mappings(cls):
        """加载虚拟库名映射配置"""
        config_path = Path(__file__).parent.parent / "config" / "db_mappings.yaml"
        if config_path.exists():
            with open(config_path, "r", encoding="utf-8") as f:
                cls._db_mappings = yaml.safe_load(f)

    @classmethod
    def execute_query(cls, sql: str, db_name: str) -> str:
        """
        真正的执行器：连接本地的 SQLite 测试数据库
        """
        print(f"   -> [DB连接池] 正在 {db_name.upper()} 实例中执行底层 SQL: \n      {sql}")
        try:
            # 找到项目根目录下的 ecommerce_test.db
            db_path = Path(__file__).parent.parent.parent / "ecommerce_test.db"

            if not db_path.exists():
                return "ERROR: 找不到 ecommerce_test.db 测试数据库，请先运行 create_test_db.py 脚本。"

            # 连接数据库
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row  # 使得查询结果可以像字典一样访问
            cursor = conn.cursor()

            cursor.execute(sql)
            rows = cursor.fetchall()

            # 将结果格式化为大模型容易理解的 JSON/Dict 字符串
            result = [dict(row) for row in rows]
            conn.close()

            if not result:
                return "查询执行成功，但结果为空(0 rows)。"

            return str(result)

        except Exception as e:
            # 如果大模型写错了 SQL (比如字段名不对)，这里会捕获异常
            # 并把异常抛回给 Agent，触发容错重试机制！
            return f"ERROR: {str(e)}"


# 初始化时加载映射
DatabaseManager.load_mappings()