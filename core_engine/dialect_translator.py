import traceback
from mindsdb_sql_parser.parser import parse_sql
# 导入 MindsDB 的 SQLAlchemy Renderer 进行方言转换
from core_engine.mindsdb_core.utilities.render.sqlalchemy_render import SqlalchemyRender
from core_engine.database_manager import DatabaseManager


def translate_and_execute(standard_sql: str, target_dialect: str) -> str:
    """
    核心：屏蔽多数据源差异，基于 AST 将标准 SQL 转为底层方言
    """
    try:
        print(f"   -> [解析器] 将标准 SQL 转化为 AST 语法树...")
        # 1. 词法与语法分析：解析为 AST
        ast_tree = parse_sql(standard_sql)

        # 2. 方言转换 (Dialect Translation)
        print(f"   -> [转换器] AST 渲染为 {target_dialect.upper()} 专属方言...")
        renderer = SqlalchemyRender(target_dialect)
        dialect_sql = renderer.get_string(ast_tree, with_failback=True)

        # 3. 交给连接池执行
        return DatabaseManager.execute_query(dialect_sql, target_dialect)

    except Exception as e:
        error_msg = f"ERROR: 方言转换或解析失败 - {str(e)}"
        return error_msg