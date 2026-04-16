import csv
import json
import random
import sqlite3
from datetime import datetime, timedelta


def setup_test_database():
    print("🛠️ 正在初始化本地 SQLite 测试数据库 (ecommerce_test.db)...")
    # 连接到本地 SQLite 数据库（如果不存在会自动创建）
    conn = sqlite3.connect('ecommerce_test.db')
    cursor = conn.cursor()

    # ==========================================
    # 1. 动态匹配最新的 CSV 结构创建 orders (订单表)
    # ==========================================
    cursor.execute('DROP TABLE IF EXISTS orders')
    cursor.execute('''
    CREATE TABLE orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT,
        user_name TEXT,
        product_id TEXT,
        product_name TEXT,
        category TEXT,
        unit_price REAL,
        purchase_time DATETIME,
        quantity INTEGER,
        total_amount REAL,
        city TEXT,
        gender TEXT,
        age INTEGER
    )
    ''')

    # ==========================================
    # 2. 读取中文表头的 CSV 文件并插入数据
    # ==========================================
    print("📂 正在解析并导入 data.csv 文件数据...")
    orders_data = []
    user_ids = set()  # 用 set 去重，提取真实的用户 ID 列表

    # 使用 utf-8-sig 防止 CSV 带有 BOM 头导致读取失败
    with open('data.csv', 'r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            # 记录真实的用户 ID，用于之后生成行为日志
            user_ids.add(row['用户ID'])

            # 按照 CSV 中的中文列名提取数据
            orders_data.append((
                row['用户ID'],
                row['用户姓名'],
                row['商品ID'],
                row['商品名称'],
                row['商品类别'],
                float(row['单价']),
                row['购买时间'],
                int(row['购买数量']),
                float(row['消费金额']),
                row['用户城市'],
                row['用户性别'],
                int(row['用户年龄'])
            ))

    # 批量插入订单数据
    cursor.executemany('''
        INSERT INTO orders (
            user_id, user_name, product_id, product_name, category, 
            unit_price, purchase_time, quantity, total_amount, city, gender, age
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', orders_data)
    print(f"✅ 成功导入 {len(orders_data)} 条真实的订单记录！")


    # 提交事务并关闭连接
    conn.commit()
    conn.close()
    print("🎉 测试数据库 ecommerce_test.db 创建完毕，完美适配新的 CSV 格式！")


if __name__ == "__main__":
    setup_test_database()